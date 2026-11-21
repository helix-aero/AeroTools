"""Backup of the camera positions before AeroPPK replaces them, so the original positions can always be put back.

Pure Python (no Metashape): `msapi.camera_reference_entries` produces the entries, `msapi.restore_camera_references` uses them.
The backup is a small JSON file next to the Metashape project (or in the application folder while the project is unsaved).
Nothing is ever deleted: a second application makes a second file, the restore uses the oldest one that is still marked "original".
A backup is of one chunk: its name carries the chunk label and the chunk key (the key never changes, two chunks may share a label).
Backups made before the key was in the name are known by the label only (`find(..., legacy=True)`).
"""

import datetime
import json
import os
import re

FORMAT = 1


def safe(text):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text or "chunk")).strip("_") or "chunk"


def folder_for(project_path, fallback):
    return os.path.dirname(project_path) if project_path else fallback


def _stem(project_path):
    return safe(os.path.splitext(os.path.basename(project_path))[0] if project_path else "project")


def _prefix(project_path, chunk_name, chunk_key=None):
    marker = "" if chunk_key is None else "_k%s" % safe(chunk_key)
    return "%s_%s%s_AeroPPK_backup_" % (_stem(project_path), safe(chunk_name), marker)


def new_path(folder, project_path, chunk_name, now=None, chunk_key=None):
    now = now or datetime.datetime.now()
    return os.path.join(folder, "%s%s.json" % (_prefix(project_path, chunk_name, chunk_key), now.strftime("%Y%m%d_%H%M%S")))


def save(path, entries, crs_name, note="", chunk="", chunk_key=None):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump({"format": FORMAT, "written": datetime.datetime.now().isoformat(timespec="seconds"), "crs": crs_name,
                   "note": note, "chunk": chunk, "chunk_key": chunk_key, "entries": entries}, handle, indent=1, ensure_ascii=False)
    os.replace(tmp, path)
    return path


def load(path):
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or "entries" not in data:
        raise ValueError("not an AeroPPK backup")
    return data


def _stamp(name):
    return name[-20:-5]


def find(folder, project_path, chunk_name, chunk_key=None, legacy=True):
    """Backups of this project and chunk, oldest first (the oldest holds the true original positions).

    With a `chunk_key`: the files of that key, and (`legacy`) the older ones named by the label only. Without: the label only."""
    prefixes = [_prefix(project_path, chunk_name)]
    if chunk_key is not None:
        prefixes = [_prefix(project_path, chunk_name, chunk_key)] + (prefixes if legacy else [])
    try:
        names = [n for n in os.listdir(folder) if n.endswith(".json") and any(n.startswith(p) for p in prefixes)]
    except OSError:
        return []
    return [os.path.join(folder, n) for n in sorted(names, key=lambda n: (_stamp(n), n))]
