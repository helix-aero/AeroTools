"""Cherche les projets Metashape d'un disque et mesure ce qu'ils pesent (sans Qt, sans Metashape).

Un projet Metashape, c'est un petit `nom.psx` (quelques octets) et un dossier `nom.files` qui contient
tout le reste :

    nom.files/<chunk>/<image>/dense_cloud[.N]/       nuage dense            (souvent 1 Go ou plus)
                             depth_maps[.N]/         cartes de profondeur   (souvent 2 a 3 Go)
                             point_cloud[.N]/        points de liaison
                             elevation[.N]/ orthomosaic[.N]/ tiled_model[.N]/ model[.N]/ ...
    nom.files/lock                                   present quand le projet est ouvert

Ce qui est lourd ET se recalcule : le nuage dense et les cartes de profondeur. C'est ce que
l'allegement retire ; le reste (alignement, marqueurs, MNT, ortho) reste intact.

Supprimer un projet ne touche qu'au `.psx` et a son `.files`. Tout ce qui est a cote (ortho ou MNT exportes,
nuages, rapports...) reste en place : `measure_kept` le compte pour pouvoir le dire avant de supprimer. En revanche
un MNT ou une ortho construits dans Metashape et jamais exportes vivent dans le `.files` et partent avec lui :
`Project.at_risk` le signale quand aucun fichier exporte n'est trouve a cote.

Un dossier `.files` sans `.psx` a cote est un orphelin : un projet dont on a supprime le `.psx`
seul. Il est signale aussi (c'est de la place perdue).
"""

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

DENSE = "dense_cloud"
DEPTH = "depth_maps"
LIGHT_PARTS = (DENSE, DEPTH)
SKIP_DIRS = {"$recycle.bin", "system volume information", "windows", "program files", "program files (x86)",
             "programdata", "recovery", "$windows.~bt", "node_modules", ".git"}
PART_NAME = re.compile(r"^(?P<name>[A-Za-z_]+?)(\.\d+)?$")
WORKERS = 8

FREE, OPEN, STALE = "free", "open", "stale"
INTERNAL_PRODUCTS = ("elevation", "orthomosaic")           # MNT et ortho construits dans Metashape
EXPORT_EXTS = {".tif", ".tiff", ".ecw", ".jp2", ".img", ".asc", ".las", ".laz", ".dxf", ".dwg", ".xyz", ".e57",
               ".ply", ".obj"}
KEPT_CAP = 5000                                            # entrees lues au plus par dossier de projet
KEPT_SHOWN = 6
_KEPT_CACHE = {}


class Kept(object):
    """Ce qui reste a cote d'un projet qu'on supprime : fichiers et dossiers du meme dossier (un niveau de sous-dossiers)."""

    def __init__(self):
        self.files = 0
        self.dirs = 0
        self.size = 0
        self.exports = False                               # un fichier de type MNT / ortho / nuage exporte est la
        self.partial = False                               # dossier trop grand, comptage arrete
        self.biggest = []                                  # [(octets, chemin relatif)]


class Project(object):
    """Un projet trouve sur le disque, avec les tailles utiles."""

    def __init__(self, path, orphan=False):
        self.path = path                                   # .psx (ou, pour un orphelin, le dossier .files)
        self.orphan = orphan
        self.files_dir = path if orphan else os.path.splitext(path)[0] + ".files"
        self.name = os.path.basename(path)
        self.psx_size = 0
        self.files_size = 0
        self.parts = {}                                    # composant -> octets
        self.modified = 0.0
        self.lock = FREE
        self.lock_owner = ""                               # « machine/utilisateur » ecrit dans le fichier lock
        self.error = ""
        self.kept = Kept()

    @property
    def dense(self):
        return self.parts.get(DENSE, 0)

    @property
    def depth(self):
        return self.parts.get(DEPTH, 0)

    @property
    def light(self):
        """Ce que l'allegement libere : nuage dense + cartes de profondeur."""
        return self.dense + self.depth

    @property
    def total(self):
        return self.psx_size + self.files_size

    @property
    def in_use(self):
        return self.lock == OPEN

    @property
    def internal_products(self):
        """MNT + ortho construits dans Metashape : ils vivent dans le .files."""
        return sum(self.parts.get(name, 0) for name in INTERNAL_PRODUCTS)

    @property
    def at_risk(self):
        """Supprimer ce projet perd un MNT ou une ortho qu'on ne retrouve exporte nulle part a cote."""
        return not self.orphan and self.internal_products > 0 and not self.kept.exports

    def folders(self, count=3):
        """Les `count` dossiers qui precedent le projet, du plus loin au plus proche."""
        parts = [p for p in os.path.dirname(self.path).replace("/", "\\").split("\\") if p]
        if parts and parts[0].endswith(":"):
            parts[0] = parts[0] + "\\"
        return parts[-count:]

    def display_folder(self, count=3, sep=" › "):
        return sep.join(self.folders(count))


# --------------------------------------------------------------------------
# Mesure
# --------------------------------------------------------------------------

def tree_size(path):
    """Taille logique, en octets, de tous les fichiers sous `path` (0 si illisible)."""
    total = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def part_of(folder_name):
    """Nom du composant d'un dossier de frame : « depth_maps.1 » -> « depth_maps »."""
    match = PART_NAME.match(folder_name)
    return match.group("name") if match else folder_name


def measure_files(files_dir):
    """({composant: octets}, octets hors composants, total) d'un dossier .files."""
    parts, other, total = {}, 0, 0
    try:
        chunks = list(os.scandir(files_dir))
    except OSError:
        return parts, other, total
    for chunk in chunks:
        if not chunk.is_dir(follow_symlinks=False):
            size = _file_size(chunk)
            other += size
            total += size
            continue
        for frame in _scan(chunk.path):
            if not frame.is_dir(follow_symlinks=False):
                size = _file_size(frame)
                other += size
                total += size
                continue
            for item in _scan(frame.path):
                if item.is_dir(follow_symlinks=False):
                    size = tree_size(item.path)
                    name = part_of(item.name)
                    parts[name] = parts.get(name, 0) + size
                else:
                    size = _file_size(item)
                    other += size
                total += size
    return parts, other, total


def _scan(path):
    try:
        return list(os.scandir(path))
    except OSError:
        return []


def _file_size(entry):
    try:
        return entry.stat(follow_symlinks=False).st_size
    except OSError:
        return 0


def _is_project_part(name):
    lowered = name.lower()
    return lowered.endswith((".psx", ".files")) or lowered in SKIP_DIRS


def measure_kept(folder):
    """Fichiers laisses a cote des projets dans `folder` (les .psx et .files des projets n'en font pas partie)."""
    key = os.path.normcase(folder)
    cached = _KEPT_CACHE.get(key)
    if cached is not None:
        return cached
    kept = Kept()
    found = []
    seen = 0
    stack = [(folder, "", 0)]
    while stack and not kept.partial:
        current, prefix, depth = stack.pop()
        for entry in _scan(current):
            seen += 1
            if seen > KEPT_CAP:
                kept.partial = True
                break
            name = entry.name
            if _is_project_part(name):
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    kept.dirs += 1
                    if depth == 0:
                        stack.append((entry.path, prefix + name + "/", 1))
                    continue
                size = entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue
            kept.files += 1
            kept.size += size
            found.append((size, prefix + name))
            if os.path.splitext(name)[1].lower() in EXPORT_EXTS:
                kept.exports = True
    found.sort(reverse=True)
    kept.biggest = found[:KEPT_SHOWN]
    _KEPT_CACHE[key] = kept
    return kept


def delete_impact(projects):
    """(fichiers gardes, octets gardes, projets a risque) si on supprime `projects`."""
    kepts = {id(p.kept): p.kept for p in projects if not p.orphan}
    files = sum(kept.files for kept in kepts.values())
    size = sum(kept.size for kept in kepts.values())
    return files, size, [p for p in projects if p.at_risk]


def lock_state(files_dir):
    """FREE (pas de verrou), OPEN (ouvert dans un Metashape), STALE (verrou oublie apres un plantage).

    Metashape garde son fichier `lock` ouvert tant que le projet l'est : on ne peut alors pas l'ouvrir
    en ecriture. Un `lock` qu'on arrive a ouvrir est un reste d'une session morte."""
    lock = os.path.join(files_dir, "lock")
    if not os.path.exists(lock):
        return FREE
    try:
        with open(lock, "r+b"):
            pass
        return STALE
    except PermissionError:
        return OPEN
    except OSError:
        return FREE


def lock_owner(files_dir):
    """Ce que Metashape a ecrit dans `lock` (ordinateur/utilisateur), ou "" si illisible."""
    try:
        with open(os.path.join(files_dir, "lock"), "rb") as handle:
            return handle.read(120).decode("utf-8", "replace").strip().splitlines()[0][:80]
    except (OSError, IndexError):
        return ""


def measure_project(psx_path):
    project = Project(psx_path)
    try:
        project.psx_size = os.path.getsize(psx_path)
        times = [os.path.getmtime(psx_path)]
        parts, other, total = measure_files(project.files_dir)
        project.parts = parts
        project.files_size = total
        for name in ("project.zip",):
            candidate = os.path.join(project.files_dir, name)
            if os.path.exists(candidate):
                times.append(os.path.getmtime(candidate))
        project.modified = max(times)
        project.lock = lock_state(project.files_dir)
        if project.lock != FREE:
            project.lock_owner = lock_owner(project.files_dir)
        project.kept = measure_kept(os.path.dirname(psx_path))
    except OSError as exc:
        project.error = repr(exc)
    return project


def measure_orphan(files_dir):
    project = Project(files_dir, orphan=True)
    try:
        parts, other, total = measure_files(files_dir)
        project.parts, project.files_size = parts, total
        project.modified = os.path.getmtime(files_dir)
        project.lock = lock_state(files_dir)
        if project.lock != FREE:
            project.lock_owner = lock_owner(files_dir)
    except OSError as exc:
        project.error = repr(exc)
    return project


# --------------------------------------------------------------------------
# Recherche
# --------------------------------------------------------------------------

def find(root, cancelled=None, on_found=None):
    """Chemins des .psx et des dossiers .files orphelins sous `root`. Renvoie (psx, orphelins)."""
    cancelled = cancelled or (lambda: False)
    psx, files_dirs = [], []
    stack = [root]
    while stack and not cancelled():
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            try:
                name = entry.name
                if entry.is_dir(follow_symlinks=False):
                    lowered = name.lower()
                    if lowered.endswith(".files"):
                        files_dirs.append(entry.path)       # on ne descend pas dans un projet
                    elif lowered not in SKIP_DIRS:
                        stack.append(entry.path)
                elif name.lower().endswith(".psx"):
                    psx.append(entry.path)
                    if on_found:
                        on_found(entry.path)
            except OSError:
                continue
    known = {os.path.normcase(os.path.splitext(p)[0] + ".files") for p in psx}
    orphans = [d for d in files_dirs
               if os.path.normcase(d) not in known and os.path.exists(os.path.join(d, "project.zip"))]
    return psx, orphans


def scan(root, progress=None, cancelled=None):
    """Tous les projets sous `root`, mesures. `progress(fait, total, texte)` est appele au fil de l'eau."""
    cancelled = cancelled or (lambda: False)
    progress = progress or (lambda done, total, text: None)
    started = time.time()
    _KEPT_CACHE.clear()
    psx, orphans = find(root, cancelled, on_found=lambda path: progress(0, 0, path))
    jobs = [("psx", p) for p in psx] + [("orphan", d) for d in orphans]
    results = []
    total = len(jobs)

    def work(job):
        if cancelled():
            return None
        kind, path = job
        return measure_project(path) if kind == "psx" else measure_orphan(path)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for done, project in enumerate(pool.map(work, jobs), 1):
            if project is not None:
                results.append(project)
            progress(done, total, project.name if project else "")
    results.sort(key=lambda item: -item.total)
    return {"projects": results, "seconds": time.time() - started, "cancelled": cancelled()}


# --------------------------------------------------------------------------
# Affichage
# --------------------------------------------------------------------------

def gb(count):
    """Octets -> « 12.34 GB » (ou Mo sous 0,1 Go)."""
    if count >= 100 * 1024 * 1024:
        return "{:.2f} GB".format(count / 1073741824.0)
    return "{:.0f} MB".format(count / 1048576.0)
