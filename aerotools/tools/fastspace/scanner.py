"""Finds the Metashape projects of a drive and measures what they weigh (no Qt, no Metashape).

A Metashape project is a small `name.psx` (a few bytes) and a `name.files` folder that holds
everything else:

    name.files/<chunk>/<image>/dense_cloud[.N]/      dense cloud            (often 1 GB or more)
                               depth_maps[.N]/       depth maps             (often 2 to 3 GB)
                               point_cloud[.N]/      tie points
                               elevation[.N]/ orthomosaic[.N]/ tiled_model[.N]/ model[.N]/ ...
    name.files/lock                                  present while the project is open

What is heavy AND can be recomputed: the dense cloud, the depth maps and the orthophotos (the rectified images
`images*.zip` stored in the `orthomosaic` folder: this is the biggest part, the mosaic itself
weighs almost nothing). This is what lightening can remove, at the user's choice; the rest (alignment, markers, DEM,
mosaic) stays intact.

Deleting a project only touches the `.psx` and its `.files`. Everything next to it (exported orthomosaic or DEM,
clouds, reports...) stays in place: `measure_kept` counts it so that it can be reported before deleting. However,
a DEM or orthomosaic built in Metashape and never exported lives in the `.files` and goes away with it:
`Project.at_risk` flags this when no exported file is found next to it.

A `.files` folder with no `.psx` next to it is an orphan: a project whose `.psx` was deleted
alone. It is reported too (it is wasted space).
"""

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

DENSE = "dense_cloud"
DEPTH = "depth_maps"
ORTHO = "orthomosaic"
ORTHO_PHOTOS = "ortho_photos"                              # virtual component: the images*.zip of the orthomosaic folder
LIGHT_PARTS = (DENSE, DEPTH)                               # whole folders that can be removed
ITEMS = ("dense", "depth", "ortho")                        # what lightening can remove
DEFAULT_ITEMS = ("dense", "depth")                         # ticked by default (the orthophotos: optional)
ITEM_PART = {"dense": DENSE, "depth": DEPTH, "ortho": ORTHO_PHOTOS}
ORTHO_IMAGES = re.compile(r"^images\d+\.zip$", re.IGNORECASE)
SKIP_DIRS = {"$recycle.bin", "system volume information", "windows", "program files", "program files (x86)",
             "programdata", "recovery", "$windows.~bt", "node_modules", ".git"}
PART_NAME = re.compile(r"^(?P<name>[A-Za-z_]+?)(\.\d+)?$")
WORKERS = 8

FREE, OPEN, STALE, UNKNOWN = "free", "open", "stale", "unknown"   # UNKNOWN: the lock file could not be examined (treated as open)
INTERNAL_PRODUCTS = ("elevation", "orthomosaic")           # DEM and orthomosaic built in Metashape
EXPORT_EXTS = {".tif", ".tiff", ".ecw", ".jp2", ".img", ".asc", ".las", ".laz", ".dxf", ".dwg", ".xyz", ".e57",
               ".ply", ".obj"}
KEPT_CAP = 5000                                            # at most this many entries read per project folder
KEPT_SHOWN = 6
_KEPT_CACHE = {}


class Kept(object):
    """What stays next to a project that is deleted: files and folders of the same folder (one level of subfolders)."""

    def __init__(self):
        self.files = 0
        self.dirs = 0
        self.size = 0
        self.exports = False                               # an exported DEM / orthomosaic / cloud file is there
        self.partial = False                               # folder too large, counting stopped
        self.biggest = []                                  # [(bytes, relative path)]


class Project(object):
    """A project found on the disk, with the useful sizes."""

    def __init__(self, path, orphan=False):
        self.path = path                                   # .psx (or, for an orphan, the .files folder)
        self.orphan = orphan
        self.files_dir = path if orphan else os.path.splitext(path)[0] + ".files"
        self.name = os.path.basename(path)
        self.psx_size = 0
        self.files_size = 0
        self.parts = {}                                    # component -> bytes
        self.modified = 0.0
        self.lock = FREE
        self.lock_owner = ""                               # "machine/user" written in the lock file
        self.error = ""
        self.kept = Kept()

    @property
    def dense(self):
        return self.parts.get(DENSE, 0)

    @property
    def depth(self):
        return self.parts.get(DEPTH, 0)

    @property
    def ortho_photos(self):
        return self.parts.get(ORTHO_PHOTOS, 0)

    def size_of(self, item):
        """Bytes of one of the ITEMS (dense, depth, ortho)."""
        return self.parts.get(ITEM_PART[item], 0)

    def removable(self, items=DEFAULT_ITEMS):
        """What lightening frees for the chosen ITEMS."""
        return sum(self.size_of(item) for item in items)

    @property
    def total(self):
        return self.psx_size + self.files_size

    @property
    def in_use(self):
        """Open in a Metashape, or impossible to tell (the lock file could not be examined): never treated as free."""
        return self.lock in (OPEN, UNKNOWN)

    @property
    def internal_products(self):
        """DEM + orthomosaic built in Metashape: they live in the .files."""
        return sum(self.parts.get(name, 0) for name in INTERNAL_PRODUCTS)

    @property
    def at_risk(self):
        """Deleting this project loses a DEM or orthomosaic that is not found exported anywhere next to it."""
        return not self.orphan and self.internal_products > 0 and not self.kept.exports

    def folders(self, count=3):
        """The `count` folders that precede the project, from the farthest to the nearest."""
        parts = [p for p in os.path.dirname(self.path).replace("/", "\\").split("\\") if p]
        if parts and parts[0].endswith(":"):
            parts[0] = parts[0] + "\\"
        return parts[-count:]

    def display_folder(self, count=3, sep=" › "):
        return sep.join(self.folders(count))


# --------------------------------------------------------------------------
# Measurement
# --------------------------------------------------------------------------

def tree_size(path):
    """Logical size, in bytes, of all the files under `path` (0 if unreadable)."""
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


def ortho_sizes(path):
    """(bytes of the images*.zip orthophotos, bytes of the whole folder) of an `orthomosaic[.N]` folder."""
    photos = total = 0
    stack = [path]
    while stack:
        current = stack.pop()
        for entry in _scan(current):
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(entry.path)
                    continue
                size = entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue
            total += size
            if current == path and ORTHO_IMAGES.match(entry.name):
                photos += size
    return photos, total


def part_of(folder_name):
    """Name of the component of a frame folder: "depth_maps.1" -> "depth_maps"."""
    match = PART_NAME.match(folder_name)
    return match.group("name") if match else folder_name


def measure_files(files_dir):
    """({component: bytes}, bytes outside components, total) of a .files folder."""
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
                    name = part_of(item.name)
                    if name == ORTHO:
                        photos, size = ortho_sizes(item.path)
                        parts[ORTHO_PHOTOS] = parts.get(ORTHO_PHOTOS, 0) + photos
                        size -= photos
                        total += photos
                    else:
                        size = tree_size(item.path)
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
    """Files left next to the projects in `folder` (the .psx and .files of the projects are not counted)."""
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
    """(files kept, bytes kept, projects at risk) if `projects` are deleted."""
    kepts = {id(p.kept): p.kept for p in projects if not p.orphan}
    files = sum(kept.files for kept in kepts.values())
    size = sum(kept.size for kept in kepts.values())
    return files, size, [p for p in projects if p.at_risk]


def lock_state(files_dir):
    """FREE (no lock), OPEN (open in a Metashape), STALE (lock left behind after a crash), UNKNOWN (the lock file could not be
    examined: network error, path too long...: handled like OPEN, never like FREE).

    Metashape keeps its `lock` file open as long as the project is open: it cannot then be opened
    for writing. A `lock` that can be opened is a leftover of a dead session. Only "the file is not there" means FREE."""
    lock = os.path.join(files_dir, "lock")
    try:
        os.stat(lock)
    except (FileNotFoundError, NotADirectoryError):
        return FREE
    except OSError:
        return UNKNOWN
    try:
        with open(lock, "r+b"):
            pass
        return STALE
    except PermissionError:
        return OPEN
    except FileNotFoundError:                    # removed between the two calls
        return FREE
    except OSError:
        return UNKNOWN


def lock_owner(files_dir):
    """What Metashape wrote in `lock` (computer/user), or "" if unreadable."""
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
# Search
# --------------------------------------------------------------------------

def find(root, cancelled=None, on_found=None):
    """Paths of the .psx files and of the orphan .files folders under `root`. Returns (psx, orphans)."""
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
                        files_dirs.append(entry.path)       # we do not descend into a project
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
    """All the projects under `root`, measured. `progress(done, total, text)` is called as the scan goes."""
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
# Display
# --------------------------------------------------------------------------

def gb(count):
    """Bytes -> "12.34 GB" (or MB under 0.1 GB)."""
    if count >= 100 * 1024 * 1024:
        return "{:.2f} GB".format(count / 1073741824.0)
    return "{:.0f} MB".format(count / 1048576.0)
