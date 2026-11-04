"""Mise a jour d'AeroTools depuis GitHub, sans Qt et sans Metashape.

Principe
--------
Le depot publie un `manifest.json` : pour chaque fichier utile (code, modeles, installeur, docs),
son empreinte SHA-256 et sa taille. L'installation locale a le sien (celui de la derniere version
telechargee). La mise a jour compare les empreintes des fichiers du disque a celles du manifeste
distant : ce qui differe est a telecharger. Le test de la suite (tests/test_manifest.py) echoue si
`manifest.json` n'est plus a jour, donc on ne peut pas l'oublier avant de publier.

Les fichiers texte sont compares apres normalisation des fins de ligne (CRLF -> LF) : le meme fichier
a la meme empreinte sous Windows et sur GitHub. Les fichiers binaires (modeles, polices, images) le
sont tels quels.

Sure par construction
---------------------
  - tout est telecharge et verifie (empreinte + taille) dans un dossier temporaire AVANT de toucher
    a l'installation ;
  - les fichiers remplaces sont d'abord copies dans un dossier de sauvegarde ; si le remplacement
    echoue en route, tout est remis comme avant ;
  - `manifest.json` local est ecrit en dernier : tant qu'il n'a pas change, la version « installee »
    n'a pas change ;
  - une copie de developpement (dossier .git) n'est jamais mise a jour : on n'ecrase pas le travail.

Reglages caches (settings.json, section `update`) :
  channel   branche suivie : "main" (defaut) ou "dev"
  base_url  racine des fichiers (defaut : https://raw.githubusercontent.com/<depot>/<branche>) ;
            sert aux tests et aux miroirs. Accepte aussi file:///...
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
                   ".toml", ".svg", ".html", ".css", ".js", ".csv"}
CRLF_EXTENSIONS = {".bat", ".ps1"}
KEEP_BACKUPS = 3

# Les modules enregistres : un seul pour l'instant, la mise a jour sait en gerer plusieurs.
PLUGINS = [
    {"id": "aerotools", "title": "HELIX AERO TOOLS", "repo": "helix-aero/AeroTools", "root": None},
]


class UpdateError(Exception):
    """Echec lisible : le message s'affiche tel quel (journal technique, en francais)."""


# --------------------------------------------------------------------------
# Modules et reglages
# --------------------------------------------------------------------------

def plugins():
    """Les modules gerables, avec leur racine locale (celle d'AeroTools par defaut)."""
    out = []
    for entry in PLUGINS:
        item = dict(entry)
        item["root"] = item.get("root") or config.ROOT
        out.append(item)
    return out


def channel():
    value = config.get(SECTION, "channel")
    return value if isinstance(value, str) and value.strip() else "main"


def base_url(plugin):
    value = config.get(SECTION, "base_url")
    if isinstance(value, str) and value.strip():
        return value.rstrip("/")
    return "https://raw.githubusercontent.com/{}/{}".format(plugin["repo"], channel())


def file_url(plugin, path):
    return base_url(plugin) + "/" + urllib.parse.quote(path.replace("\\", "/"))


def is_dev_copy(plugin):
    """Un depot git : on ne l'ecrase pas."""
    return os.path.exists(os.path.join(plugin["root"], ".git"))


def is_text(path):
    name = os.path.basename(path)
    return os.path.splitext(name)[1].lower() in TEXT_EXTENSIONS or name == "LICENSE"


# --------------------------------------------------------------------------
# Empreintes et manifeste
# --------------------------------------------------------------------------

def digest_bytes(data, path=""):
    """SHA-256 d'un contenu ; fins de ligne normalisees pour un fichier texte."""
    if is_text(path):
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def digest_file(path, name=None):
    """(sha256, taille normalisee) d'un fichier du disque."""
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
        raise UpdateError("manifeste illisible")
    for name, info in data["files"].items():
        if (not isinstance(info, dict) or not isinstance(info.get("sha256"), str)
                or not isinstance(info.get("size"), int)):
            raise UpdateError("manifeste : entree invalide pour {}".format(name))
        _safe_relative(name)
    return data


def _safe_relative(name):
    """Refuse un chemin qui sortirait du dossier d'installation."""
    clean = name.replace("\\", "/")
    if (not clean or clean.startswith("/") or ".." in clean.split("/") or ":" in clean
            or os.path.isabs(name)):
        raise UpdateError("chemin refuse dans le manifeste : {!r}".format(name))
    return clean


def local_manifest(plugin):
    """Manifeste de l'installation (celui de la derniere version recue), ou None."""
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
        raise UpdateError("manifeste distant injoignable : {!r}".format(exc))
    try:
        return _check_manifest(json.loads(raw.decode("utf-8-sig"))), raw
    except UpdateError:
        raise
    except Exception as exc:
        raise UpdateError("manifeste distant illisible : {!r}".format(exc))


# --------------------------------------------------------------------------
# Comparaison
# --------------------------------------------------------------------------

def _local_path(plugin, name):
    return os.path.join(plugin["root"], *name.split("/"))


def compare(plugin, remote, local=None):
    """Liste des changements pour passer de l'etat du disque au manifeste distant.

    Chaque element : {"path", "action": "add" | "replace" | "delete", "size"}.
    Les suppressions ne concernent que des fichiers que l'ancien manifeste local listait."""
    changes = []
    for name in sorted(remote["files"]):
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
            if os.path.isfile(_local_path(plugin, name)):
                changes.append({"path": name, "action": "delete", "size": 0})
    return changes


def describe_time(value):
    """Date locale lisible d'un horodatage ISO, ou "" ."""
    try:
        return datetime.datetime.fromisoformat(value).strftime("%d/%m/%Y %H:%M")
    except (TypeError, ValueError):
        return ""


def installed_info(plugin):
    """{"version", "built", "updated"} de l'installation locale (vides si inconnus)."""
    local = local_manifest(plugin) or {}
    state = config.get(SECTION, plugin["id"])
    updated = state.get("updated") if isinstance(state, dict) else None
    return {"version": str(local.get("version") or ""), "built": str(local.get("built") or ""),
            "updated": updated or ""}


def check(plugin):
    """Etat d'un module : compare l'installation au depot. Ne leve jamais.

    Renvoie {"id", "title", "state", "changes", "bytes", "remote", "message"} ou state vaut
    "current", "update", "dev" (copie git : pas de mise a jour) ou "error"."""
    result = {"id": plugin["id"], "title": plugin["title"], "state": "error", "changes": [], "bytes": 0,
              "remote": {}, "message": "", "dev": is_dev_copy(plugin)}
    try:
        remote, _raw = remote_manifest(plugin)
        result["remote"] = {"version": str(remote.get("version") or ""), "built": str(remote.get("built") or "")}
        result["changes"] = compare(plugin, remote, local_manifest(plugin))
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
# Application
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
        raise UpdateError("dossier d'installation non modifiable ({}) : {!r}".format(plugin["root"], exc))


def _download(plugin, name, info, target, progress, cancelled):
    """Telecharge un fichier dans `target`, verifie taille et empreinte. Leve UpdateError."""
    url = file_url(plugin, name)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response, open(target, "wb") as handle:
            while True:
                if cancelled():
                    raise UpdateError("annule")
                block = response.read(CHUNK)
                if not block:
                    break
                handle.write(block)
                progress(len(block))
    except UpdateError:
        raise
    except Exception as exc:
        raise UpdateError("telechargement de {} impossible : {!r}".format(name, exc))
    digest, size = digest_file(target, name)
    if digest != info["sha256"] or size != info["size"]:
        raise UpdateError("{} recu abime (empreinte differente) : mise a jour interrompue".format(name))


def _install_file(source, destination, name):
    """Ecrit `source` sur `destination` de facon atomique ; .bat et .ps1 repassent en CRLF."""
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


def apply(plugin, changes=None, progress=None, cancelled=None):
    """Telecharge, verifie puis installe la derniere version. Renvoie {"ok", "updated", "error"}.

    `progress(fait_octets, total_octets, texte)` est appele pendant le telechargement ;
    `cancelled()` rend True quand l'utilisateur annule (avant la phase de remplacement). Ne leve jamais."""
    progress = progress or (lambda done, total, text: None)
    cancelled = cancelled or (lambda: False)
    backup = None
    done_files = []
    try:
        if is_dev_copy(plugin):
            raise UpdateError("copie de developpement (git) : mise a jour refusee")
        remote, raw = remote_manifest(plugin)
        local = local_manifest(plugin)
        changes = compare(plugin, remote, local)
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
            raise UpdateError("annule")
        progress(total, total, "")

        # Remplacement : sauvegarde de chaque fichier avant de le toucher.
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
        # Le manifeste local en dernier : il fait foi de la version installee.
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
        journal.write("Mise a jour {} : {} fichier(s)".format(plugin["id"], len(changes)))
        return {"ok": True, "updated": len(changes), "error": ""}
    except UpdateError as exc:
        _rollback(done_files)
        journal.write("Mise a jour {} : {}".format(plugin["id"], exc))
        return {"ok": False, "updated": 0, "error": str(exc)}
    except Exception as exc:
        _rollback(done_files)
        journal.write("Mise a jour {} : echec {!r}".format(plugin["id"], exc))
        return {"ok": False, "updated": 0, "error": repr(exc)}


def _rollback(done_files):
    """Remet les fichiers deja remplaces dans leur etat d'avant (ou retire ceux qui etaient nouveaux)."""
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
    """Apres une mise a jour : relit les fichiers du disque. Renvoie (nombre de fichiers, liste des differents)."""
    manifest = local_manifest(plugin)
    if manifest is None:
        return 0, ["manifest.json"]
    bad = []
    for name, info in manifest["files"].items():
        path = _local_path(plugin, name)
        try:
            digest, _size = digest_file(path, name)
        except OSError:
            bad.append(name)
            continue
        if digest != info["sha256"]:
            bad.append(name)
    return len(manifest["files"]), bad


def format_size(count):
    """12 345 678 -> « 11.8 MB » (en anglais : unites universelles)."""
    value = float(count)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return "{:.0f} {}".format(value, unit) if unit == "B" else "{:.1f} {}".format(value, unit)
        value /= 1024.0
