"""Update AeroTools from GitHub, without Qt and without Metashape.

Principle
---------
The repository publishes a `manifest.json`: for each useful file (code, models, installer, docs),
its SHA-256 digest and its size. The local installation has its own (the one of the last version
downloaded). The update compares the digests of the files on disk with those of the remote
manifest: what differs has to be downloaded. The test suite (tests/test_updater.py) fails if
`manifest.json` is out of date, so it cannot be forgotten before publishing.

Text files are compared after line-ending normalization (CRLF -> LF): the same file
has the same digest on Windows and on GitHub. Binary files (models, fonts, images) are compared
as they are.

Safe by construction
--------------------
  - everything is downloaded and verified (digest + size) in a temporary folder BEFORE touching
    the installation;
  - replaced files are first copied to a backup folder; if the replacement
    fails midway, everything is put back as it was;
  - the local `manifest.json` is written last: as long as it has not changed, the "installed"
    version has not changed;
  - a development copy (.git folder) is never updated: work is not overwritten.

Hidden settings (settings.json, section `update`):
  channel   followed branch: "main" (default) or "dev"
  base_url  root of the files (default: https://raw.githubusercontent.com/<repo>/<branch>);
            used by tests and mirrors. Also accepts file:///...
"""

import datetime
import hashlib
import json
import os
import shutil
import time
import urllib.parse
import urllib.request

from . import config, journal

SECTION = "update"
MANIFEST = "manifest.json"
SCHEMA = 1
TIMEOUT = 30
CHUNK = 1 << 16
USER_AGENT = "AeroTools-updater"
TEXT_EXTENSIONS = {".py", ".md", ".ps1", ".bat", ".tpl", ".json", ".txt", ".yml", ".yaml", ".cfg",
                   ".toml", ".svg", ".html", ".css", ".js", ".csv", ".ini", ".geojson"}
CRLF_EXTENSIONS = {".bat", ".ps1"}
KEEP_BACKUPS = 3

# The registered modules: just one for now, the updater can handle several.
PLUGINS = [
    {"id": "aerotools", "title": "HELIX AERO TOOLS", "repo": "helix-aero/AeroTools", "root": None},
]


class UpdateError(Exception):
    """Readable failure: the message is shown as is (technical journal)."""


# --------------------------------------------------------------------------
# Modules and settings
# --------------------------------------------------------------------------

def plugins():
    """The manageable modules, with their local root (AeroTools' own by default)."""
    out = []
    for entry in PLUGINS:
        item = dict(entry)
        item["root"] = item.get("root") or config.ROOT
        out.append(item)
    return out


def channel():
    value = config.get(SECTION, "channel")
    return value if isinstance(value, str) and value.strip() else "main"


def set_channel(value):
    """Remember the followed branch (workstation setting). Returns True if the write succeeded."""
    return config.put(SECTION, "channel", value)


def base_url(plugin):
    value = config.get(SECTION, "base_url")
    if isinstance(value, str) and value.strip():
        return value.rstrip("/")
    return "https://raw.githubusercontent.com/{}/{}".format(plugin["repo"], channel())


def file_url(plugin, path):
    return base_url(plugin) + "/" + urllib.parse.quote(path.replace("\\", "/"))


def is_dev_copy(plugin):
    """A git repository: it is not overwritten."""
    return os.path.exists(os.path.join(plugin["root"], ".git"))


def is_text(path):
    name = os.path.basename(path)
    return os.path.splitext(name)[1].lower() in TEXT_EXTENSIONS or name == "LICENSE"


# --------------------------------------------------------------------------
# Digests and manifest
# --------------------------------------------------------------------------

def digest_bytes(data, path=""):
    """SHA-256 of a content; line endings normalized for a text file."""
    if is_text(path):
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def digest_file(path, name=None):
    """(sha256, normalized size) of a file on disk."""
    with open(path, "rb") as handle:
        data = handle.read()
    if is_text(name or path):
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest(), len(data)


def read_manifest(path):
    with open(path, "r", encoding="utf-8-sig") as handle:
        return _check_manifest(json.load(handle))


def _check_manifest(data):
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        raise UpdateError("unreadable manifest")
    for name, info in data["files"].items():
        if (not isinstance(info, dict) or not isinstance(info.get("sha256"), str)
                or not isinstance(info.get("size"), int)):
            raise UpdateError("manifest: invalid entry for {}".format(name))
        _safe_relative(name)
    return data


def _safe_relative(name):
    """Refuse a path that would leave the installation folder."""
    clean = name.replace("\\", "/")
    if (not clean or clean.startswith("/") or ".." in clean.split("/") or ":" in clean
            or os.path.isabs(name)):
        raise UpdateError("path refused in the manifest: {!r}".format(name))
    return clean


def local_manifest(plugin):
    """Manifest of the installation (the one of the last version received), or None."""
    path = os.path.join(plugin["root"], MANIFEST)
    try:
        return read_manifest(path)
    except Exception:
        return None


def fetch(url, timeout=TIMEOUT):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def remote_manifest(plugin):
    try:
        raw = fetch(file_url(plugin, MANIFEST))
    except Exception as exc:
        raise UpdateError("remote manifest unreachable: {!r}".format(exc))
    try:
        return _check_manifest(json.loads(raw.decode("utf-8-sig"))), raw
    except UpdateError:
        raise
    except Exception as exc:
        raise UpdateError("remote manifest unreadable: {!r}".format(exc))


# --------------------------------------------------------------------------
# Comparison
# --------------------------------------------------------------------------

def _local_path(plugin, name):
    return os.path.join(plugin["root"], *name.split("/"))


def compare(plugin, remote, local=None, packs=None):
    """List of changes to go from the state of the disk to the remote manifest.

    Each element: {"path", "action": "add" | "replace" | "delete", "size"}.
    Deletions only concern files that the old local manifest listed. `packs` (a set of pack ids, "core" included) restricts the
    comparison to the files of those packs: the others are not installed here, so nothing is missing or out of date."""
    changes = []
    for name in sorted(remote["files"]):
        if packs is not None and config.pack_of(name) not in packs:
            continue
        info = remote["files"][name]
        path = _local_path(plugin, name)
        if not os.path.isfile(path):
            changes.append({"path": name, "action": "add", "size": info["size"]})
            continue
        try:
            digest, _size = digest_file(path, name)
        except OSError:
            digest = None
        if digest != info["sha256"]:
            changes.append({"path": name, "action": "replace", "size": info["size"]})
    if local:
        for name in sorted(set(local["files"]) - set(remote["files"])):
            if packs is not None and config.pack_of(name) not in packs:
                continue
            if os.path.isfile(_local_path(plugin, name)):
                changes.append({"path": name, "action": "delete", "size": 0})
    return changes


def describe_time(value):
    """Readable local date of an ISO timestamp, or ""."""
    try:
        return datetime.datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return ""


def installed_info(plugin):
    """{"version", "built", "updated"} of the local installation (empty if unknown)."""
    local = local_manifest(plugin) or {}
    state = config.get(SECTION, plugin["id"])
    updated = state.get("updated") if isinstance(state, dict) else None
    return {"version": str(local.get("version") or ""), "built": str(local.get("built") or ""),
            "updated": updated or ""}


def check(plugin):
    """State of a module: compares the installation with the repository. Never raises.

    Returns {"id", "title", "state", "changes", "bytes", "remote", "message"} where state is
    "current", "update", "dev" (git copy: no update) or "error"."""
    result = {"id": plugin["id"], "title": plugin["title"], "state": "error", "changes": [], "bytes": 0,
              "remote": {}, "message": "", "dev": is_dev_copy(plugin)}
    try:
        remote, _raw = remote_manifest(plugin)
        result["remote"] = {"version": str(remote.get("version") or ""), "built": str(remote.get("built") or "")}
        result["changes"] = compare(plugin, remote, local_manifest(plugin), installed_packs(plugin))
        result["bytes"] = sum(c["size"] for c in result["changes"] if c["action"] != "delete")
        if result["dev"]:
            result["state"] = "dev"
        else:
            result["state"] = "update" if result["changes"] else "current"
    except UpdateError as exc:
        result["message"] = str(exc)
    except Exception as exc:
        result["message"] = repr(exc)
    return result


# --------------------------------------------------------------------------
# Packs: the tools that are installed on this computer
# --------------------------------------------------------------------------

def is_pack_installed(plugin, pack_id):
    """True when the marker file of the pack is on disk. A development copy (git) has everything."""
    entry = config.pack(pack_id)
    if entry is None:
        return False
    return is_dev_copy(plugin) or os.path.isfile(_local_path(plugin, entry["marker"]))


def tool_installed(tool_id):
    """True if a tool of the Aero menu is installed in this AeroTools (the first registered module)."""
    return is_pack_installed(plugins()[0], tool_id)


def installed_packs(plugin):
    """Set of the installed pack ids, "core" included."""
    return {config.CORE} | {entry["id"] for entry in config.PACKS if is_pack_installed(plugin, entry["id"])}


def pack_files(manifest, pack_id):
    """{path: info} of the files of a pack in a manifest."""
    return {name: info for name, info in manifest["files"].items() if config.pack_of(name) == pack_id}


def pack_status(plugin, remote=None):
    """[{"id", "installed", "bytes" (what it weighs, its requirements not counted), "needs" (requirements not installed yet),
    "update" (files to download to bring the installed pack up to date)}] for every pack, in the order of config.PACKS.
    `remote` is the remote manifest; without it sizes and updates are unknown (0)."""
    have = installed_packs(plugin)
    local = local_manifest(plugin)
    found = []
    for entry in config.PACKS:
        pack_id = entry["id"]
        files = pack_files(remote, pack_id) if remote else {}
        update = 0
        if remote and pack_id in have:
            update = len(compare(plugin, remote, local, {pack_id}))
        found.append({"id": pack_id, "installed": pack_id in have, "bytes": sum(i["size"] for i in files.values()),
                      "needs": [p for p in config.pack_closure([pack_id]) if p != pack_id and p not in have],
                      "update": update})
    return found


def install_pack(plugin, pack_id, progress=None, cancelled=None):
    """Download and install a pack with the packs it requires. Returns {"ok", "updated", "error", "packs" (the ones added)}.
    Nothing is left half-installed: the files are verified before the first one is replaced, and put back if it fails."""
    entry = config.pack(pack_id)
    if entry is None:
        return {"ok": False, "updated": 0, "error": "unknown tool: {}".format(pack_id), "packs": []}
    if is_dev_copy(plugin):
        return {"ok": False, "updated": 0, "error": "development copy (git): everything is already there", "packs": []}
    have = installed_packs(plugin)
    wanted = config.pack_closure([pack_id])
    result = apply(plugin, progress=progress, cancelled=cancelled, packs=have | set(wanted))
    result["packs"] = [p for p in wanted if p not in have] if result["ok"] else []
    if result["ok"] and not is_pack_installed(plugin, pack_id):
        result.update(ok=False, error="the files were received but {} is not complete".format(pack_id), packs=[])
    if result["ok"]:
        journal.write("Install {}: {}".format(pack_id, ", ".join(result["packs"]) or "already there"))
    return result


def remove_pack(plugin, pack_id):
    """Delete the files of an installed pack (the ones the manifest lists, then the empty folders). Returns {"ok", "error"}.
    Refused while another installed pack needs it. Settings, models trained by the user and Metashape projects are never touched."""
    entry = config.pack(pack_id)
    if entry is None:
        return {"ok": False, "error": "unknown tool: {}".format(pack_id)}
    if is_dev_copy(plugin):
        return {"ok": False, "error": "development copy (git): nothing is removed"}
    have = installed_packs(plugin)
    users = [p for p in config.dependents(pack_id) if p in have]
    if users:
        return {"ok": False, "error": "needed by: {}".format(", ".join(users)), "needed_by": users}
    manifest = local_manifest(plugin) or {"files": {}}
    errors = []
    for name in sorted(pack_files(manifest, pack_id)):
        path = _local_path(plugin, name)
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError as exc:
            errors.append("{}: {!r}".format(name, exc))
    for prefix in entry["paths"]:
        _remove_empty_folders(_local_path(plugin, prefix.rstrip("/")))
    if errors:
        journal.write("Remove {}: {}".format(pack_id, "; ".join(errors)))
        return {"ok": False, "error": "; ".join(errors[:3])}
    journal.write("Remove {}".format(pack_id))
    return {"ok": True, "error": ""}


def _remove_empty_folders(folder):
    """Delete the __pycache__ folders and then every empty folder, from the bottom up, starting at `folder` itself."""
    if not os.path.isdir(folder):
        return
    for base, dirs, _names in os.walk(folder, topdown=False):
        for name in dirs:
            if name == "__pycache__":
                shutil.rmtree(os.path.join(base, name), ignore_errors=True)
        try:
            os.rmdir(base)
        except OSError:
            pass


# --------------------------------------------------------------------------
# Applying
# --------------------------------------------------------------------------

def _stage_dir(plugin):
    base = os.path.join(config.settings_dir(), "updates", plugin["id"])
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path, suffix = os.path.join(base, stamp), 0
    while os.path.exists(path):
        suffix += 1
        path = os.path.join(base, "{}_{}".format(stamp, suffix))
    os.makedirs(path)
    return path


def _prune(plugin):
    base = os.path.join(config.settings_dir(), "updates", plugin["id"])
    try:
        names = sorted(os.listdir(base))
        for name in names[:-KEEP_BACKUPS]:
            shutil.rmtree(os.path.join(base, name), ignore_errors=True)
    except OSError:
        pass


def _check_writable(plugin):
    probe = os.path.join(plugin["root"], ".aerotools_write_test")
    try:
        with open(probe, "w") as handle:
            handle.write("x")
        os.remove(probe)
    except OSError as exc:
        raise UpdateError("installation folder not writable ({}): {!r}".format(plugin["root"], exc))


def _download(plugin, name, info, target, progress, cancelled):
    """Download a file into `target`, verify size and digest. Raises UpdateError."""
    url = file_url(plugin, name)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response, open(target, "wb") as handle:
            while True:
                if cancelled():
                    raise UpdateError("cancelled")
                block = response.read(CHUNK)
                if not block:
                    break
                handle.write(block)
                progress(len(block))
    except UpdateError:
        raise
    except Exception as exc:
        raise UpdateError("download of {} failed: {!r}".format(name, exc))
    digest, size = digest_file(target, name)
    if digest != info["sha256"] or size != info["size"]:
        raise UpdateError("{} received damaged (different digest): update interrupted".format(name))


def _install_file(source, destination, name):
    """Write `source` over `destination` atomically; .bat and .ps1 go back to CRLF."""
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    temp = destination + ".aeroupd"
    if os.path.splitext(name)[1].lower() in CRLF_EXTENSIONS:
        with open(source, "rb") as handle:
            data = handle.read().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        with open(temp, "wb") as handle:
            handle.write(data)
    else:
        shutil.copyfile(source, temp)
    os.replace(temp, destination)


def apply(plugin, changes=None, progress=None, cancelled=None, packs=None):
    """Download, verify, then install the latest version of the installed packs (or of `packs`, to install new ones).
    Returns {"ok", "updated", "error"}.

    `progress(done_bytes, total_bytes, text)` is called during the download;
    `cancelled()` returns True when the user cancels (before the replacement phase). Never raises."""
    progress = progress or (lambda done, total, text: None)
    cancelled = cancelled or (lambda: False)
    backup = None
    done_files = []
    try:
        if is_dev_copy(plugin):
            raise UpdateError("development copy (git): update refused")
        remote, raw = remote_manifest(plugin)
        local = local_manifest(plugin)
        changes = compare(plugin, remote, local, packs if packs is not None else installed_packs(plugin))
        if not changes:
            _write_state(plugin, remote)
            return {"ok": True, "updated": 0, "error": ""}
        _check_writable(plugin)
        stage = _stage_dir(plugin)
        backup = os.path.join(stage, "old")
        incoming = os.path.join(stage, "new")
        wanted = [c for c in changes if c["action"] != "delete"]
        total = sum(c["size"] for c in wanted) or 1
        state = {"done": 0}
        current = {"name": ""}

        def advance(count):
            state["done"] += count
            progress(state["done"], total, current["name"])

        for change in wanted:
            current["name"] = change["path"]
            _download(plugin, change["path"], remote["files"][change["path"]],
                      os.path.join(incoming, *change["path"].split("/")), advance, cancelled)
        if cancelled():
            raise UpdateError("cancelled")
        progress(total, total, "")

        # Replacement: back up each file before touching it.
        for change in changes:
            name = change["path"]
            destination = _local_path(plugin, name)
            saved = None
            if os.path.isfile(destination):
                saved = os.path.join(backup, *name.split("/"))
                os.makedirs(os.path.dirname(saved), exist_ok=True)
                shutil.copy2(destination, saved)
            done_files.append((destination, saved))
            if change["action"] == "delete":
                os.remove(destination)
            else:
                _install_file(os.path.join(incoming, *name.split("/")), destination, name)
        # The local manifest last: it is the authority on the installed version.
        manifest_path = os.path.join(plugin["root"], MANIFEST)
        saved = None
        if os.path.isfile(manifest_path):
            saved = os.path.join(backup, MANIFEST)
            os.makedirs(backup, exist_ok=True)
            shutil.copy2(manifest_path, saved)
        done_files.append((manifest_path, saved))
        with open(manifest_path + ".aeroupd", "wb") as handle:
            handle.write(raw)
        os.replace(manifest_path + ".aeroupd", manifest_path)
        _write_state(plugin, remote)
        _prune(plugin)
        journal.write("Update {}: {} file(s)".format(plugin["id"], len(changes)))
        return {"ok": True, "updated": len(changes), "error": ""}
    except UpdateError as exc:
        _rollback(done_files)
        journal.write("Update {}: {}".format(plugin["id"], exc))
        return {"ok": False, "updated": 0, "error": str(exc)}
    except Exception as exc:
        _rollback(done_files)
        journal.write("Update {}: failed {!r}".format(plugin["id"], exc))
        return {"ok": False, "updated": 0, "error": repr(exc)}


def _rollback(done_files):
    """Put the files already replaced back in their previous state (or remove the ones that were new)."""
    for destination, saved in reversed(done_files):
        try:
            if saved and os.path.isfile(saved):
                os.makedirs(os.path.dirname(destination), exist_ok=True)
                shutil.copy2(saved, destination)
            elif os.path.isfile(destination):
                os.remove(destination)
        except OSError:
            pass


def _write_state(plugin, remote):
    config.put(SECTION, plugin["id"], {
        "updated": datetime.datetime.now().isoformat(timespec="seconds"),
        "version": str(remote.get("version") or ""), "built": str(remote.get("built") or "")})


def verify(plugin):
    """After an update: re-read the files on disk. Returns (number of files, list of the differing ones)."""
    manifest = local_manifest(plugin)
    if manifest is None:
        return 0, ["manifest.json"]
    bad = []
    packs = installed_packs(plugin)
    files = {name: info for name, info in manifest["files"].items() if config.pack_of(name) in packs}
    for name, info in files.items():
        path = _local_path(plugin, name)
        try:
            digest, _size = digest_file(path, name)
        except OSError:
            bad.append(name)
            continue
        if digest != info["sha256"]:
            bad.append(name)
    return len(files), bad


def format_size(count):
    """12345678 -> "11.8 MB"."""
    value = float(count)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return "{:.0f} {}".format(value, unit) if unit == "B" else "{:.1f} {}".format(value, unit)
        value /= 1024.0
