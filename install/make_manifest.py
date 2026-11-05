"""Ecrit manifest.json : la liste des fichiers publies, avec empreinte et taille.

A lancer avant chaque publication (la suite de tests le rappelle si on l'oublie) :

    python install/make_manifest.py

La mise a jour dans Metashape (aerotools/updater.py) compare les fichiers du disque a ce manifeste.
Seuls les fichiers utiles a l'utilisateur sont listes : code, modeles, installeur, ressources, docs.
"""

import datetime
import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from aerotools import updater  # noqa: E402

FOLDERS = ("aerotools", "install", "models", "assets")
DOC_FOLDER = "docs"
DOC_EXTENSIONS = (".md", ".py")
TOP_FILES = ("README.md", "README.fr.md", "LICENSE", "CLICK HERE TO INSTALL.bat")
MODELS = {"damier.pt", "damier.onnx", "bombe.pt", "bombe.onnx"}
SKIP_FOLDERS = {"__pycache__", ".git", ".pytest_cache"}
SKIP_SUFFIXES = (".pyc", ".pyo", ".bak", ".tmp", ".aeroupd", ".log")
SKIP_NAMES = {"manifest.json", "Thumbs.db", ".DS_Store"}


def wanted(relative):
    """Ce fichier (chemin relatif en /) est-il publie ?"""
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
    """Chemins relatifs (avec /) de tous les fichiers publies, tries."""
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
    """Contenu du manifeste (dict)."""
    files = {}
    for relative in listing(root):
        digest, size = updater.digest_file(os.path.join(root, *relative.split("/")), relative)
        files[relative] = {"sha256": digest, "size": size}
    return {"schema": updater.SCHEMA, "name": "HELIX AERO TOOLS", "version": version(root),
            "built": datetime.datetime.now().isoformat(timespec="seconds"), "files": files}


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
    print("manifest.json : {} fichiers, {:.1f} Mo, version {}".format(
        len(result["files"]), total / 1048576.0, result["version"]))
