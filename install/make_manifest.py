"""Write manifest.json: the list of published files, with hash and size.

Run before each publication (the test suite reminds you if you forget):

    python install/make_manifest.py            write manifest.json
    python install/make_manifest.py --check    change nothing, exit 1 and list the differences when it is stale

The output is deterministic: running it twice on the same tree gives the same bytes (`built` only moves when the files, the packs or
the version change; set SOURCE_DATE_EPOCH to pin it).

The update inside Metashape (aerotools/updater.py) compares the files on disk with this manifest.
Only the files useful to the user are listed: code, models, installer, assets, docs.
"""

import datetime
import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from aerotools import config, updater  # noqa: E402

FOLDERS = ("aerotools", "install", "models", "assets")
DOC_FOLDER = "docs"
DOC_EXTENSIONS = (".md",)           # no script of docs/ (debug helpers) travels to the users
TOP_FILES = ("README.md", "LICENSE", "THIRD_PARTY.md", "CLICK HERE TO INSTALL.bat")
MODELS = {"checker.pt", "checker.onnx", "spray.pt", "spray.onnx", "NOTICE.txt"}
SKIP_FOLDERS = {"__pycache__", ".git", ".pytest_cache"}
SKIP_SUFFIXES = (".pyc", ".pyo", ".bak", ".tmp", ".aeroupd", ".log")
SKIP_NAMES = {"manifest.json", "Thumbs.db", ".DS_Store"}


def wanted(relative):
    """Is this file (relative path with /) published?"""
    parts = relative.split("/")
    name = parts[-1]
    if name in SKIP_NAMES or name.endswith(SKIP_SUFFIXES) or SKIP_FOLDERS.intersection(parts):
        return False
    if parts[0] == "models":
        return len(parts) == 2 and name in MODELS
    if parts[0] == DOC_FOLDER:
        return len(parts) == 2 and name.endswith(DOC_EXTENSIONS)
    return True


def listing(root=ROOT):
    """Relative paths (with /) of all published files, sorted."""
    found = []
    for folder in FOLDERS + (DOC_FOLDER,):
        for base, dirs, names in os.walk(os.path.join(root, folder)):
            dirs[:] = [d for d in dirs if d not in SKIP_FOLDERS]
            for name in names:
                path = os.path.join(base, name)
                relative = os.path.relpath(path, root).replace(os.sep, "/")
                if wanted(relative):
                    found.append(relative)
    for name in TOP_FILES:
        if os.path.isfile(os.path.join(root, name)):
            found.append(name)
    return sorted(found)


def version(root=ROOT):
    with open(os.path.join(root, "aerotools", "__init__.py"), encoding="utf-8") as handle:
        match = re.search(r'__version__\s*=\s*"([^"]+)"', handle.read())
    return match.group(1) if match else "0"


def _built_now():
    epoch = os.environ.get("SOURCE_DATE_EPOCH", "")
    if epoch.isdigit():
        return datetime.datetime.fromtimestamp(int(epoch)).isoformat(timespec="seconds")
    return datetime.datetime.now().isoformat(timespec="seconds")


def build(root=ROOT, built=None):
    """Manifest content (dict). `built` defaults to now."""
    files = {}
    for relative in listing(root):
        digest, size = updater.digest_file(os.path.join(root, *relative.split("/")), relative)
        files[relative] = {"sha256": digest, "size": size}
    # The packs are listed too: the installer (install_core.ps1, plain PowerShell) copies the files that belong to none of them.
    packs = [{"id": entry["id"], "paths": entry["paths"], "requires": entry["requires"]} for entry in config.PACKS]
    return {"schema": updater.SCHEMA, "name": "HELIX AERO TOOLS", "version": version(root),
            "built": built or _built_now(), "packs": packs, "files": files}


def _previous(root):
    try:
        with open(os.path.join(root, updater.MANIFEST), encoding="utf-8-sig") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _content(manifest):
    return {key: value for key, value in manifest.items() if key != "built"}


def stale(root=ROOT):
    """What differs between manifest.json on disk and the tree: a list of readable lines (empty when it is up to date)."""
    previous = _previous(root)
    if previous is None:
        return ["manifest.json is missing or unreadable"]
    now = build(root, built=str(previous.get("built") or ""))
    problems = []
    old_files, new_files = previous.get("files") or {}, now["files"]
    for name in sorted(set(old_files) | set(new_files)):
        if name not in new_files:
            problems.append("listed but not on disk (or not published any more): " + name)
        elif name not in old_files:
            problems.append("on disk but not listed: " + name)
        elif old_files[name] != new_files[name]:
            problems.append("changed since the manifest was written: " + name)
    for key in ("version", "packs", "schema", "name"):
        if previous.get(key) != now[key]:
            problems.append("{} differs".format(key))
    return problems


def write(root=ROOT):
    previous = _previous(root)
    manifest = build(root, built=str(previous.get("built") or "") if previous else None)
    if previous and _content(previous) != _content(manifest):
        manifest["built"] = _built_now()                 # something changed: a new build; otherwise keep the date (identical bytes)
    path = os.path.join(root, updater.MANIFEST)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, indent=1, sort_keys=True, ensure_ascii=False)
        handle.write("\n")
    return manifest


if __name__ == "__main__":
    if "--check" in sys.argv[1:]:
        differences = stale()
        for line in differences:
            print(line)
        print("manifest.json is up to date" if not differences else "manifest.json is stale: run python install/make_manifest.py")
        sys.exit(1 if differences else 0)
    result = write()
    total = sum(info["size"] for info in result["files"].values())
    print("manifest.json: {} files, {:.1f} MB, version {}".format(
        len(result["files"]), total / 1048576.0, result["version"]))
