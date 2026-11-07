"""Write manifest.json: the list of published files, with hash and size.

Run before each publication (the test suite reminds you if you forget):

    python install/make_manifest.py

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
DOC_EXTENSIONS = (".md", ".py")
TOP_FILES = ("README.md", "LICENSE", "CLICK HERE TO INSTALL.bat")
MODELS = {"checker.pt", "checker.onnx", "spray.pt", "spray.onnx"}
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


def build(root=ROOT):
    """Manifest content (dict)."""
    files = {}
    for relative in listing(root):
        digest, size = updater.digest_file(os.path.join(root, *relative.split("/")), relative)
        files[relative] = {"sha256": digest, "size": size}
    # The packs are listed too: the installer (install_core.ps1, plain PowerShell) copies the files that belong to none of them.
    packs = [{"id": entry["id"], "paths": entry["paths"], "requires": entry["requires"]} for entry in config.PACKS]
    return {"schema": updater.SCHEMA, "name": "HELIX AERO TOOLS", "version": version(root),
            "built": datetime.datetime.now().isoformat(timespec="seconds"), "packs": packs, "files": files}


def write(root=ROOT):
    manifest = build(root)
    path = os.path.join(root, updater.MANIFEST)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, indent=1, sort_keys=True, ensure_ascii=False)
        handle.write("\n")
    return manifest


if __name__ == "__main__":
    result = write()
    total = sum(info["size"] for info in result["files"].values())
    print("manifest.json: {} files, {:.1f} MB, version {}".format(
        len(result["files"]), total / 1048576.0, result["version"]))
