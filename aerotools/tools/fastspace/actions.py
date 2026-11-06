"""What Fast Space can do with a project: lighten it (soft version) or delete it (hard version).

Two possible fates for what is removed:
  RECYCLE    Windows recycle bin: everything can be restored as long as it is not emptied (local disk only;
             a Windows network drive has no recycle bin);
  PERMANENT  permanent deletion. The interface then asks to type DELETE.

Lightening (`lighten`) removes, at the user's choice, the dense cloud, the depth maps and the orthophotos (the rectified
images of the orthomosaic, `Orthomosaic.removeOrthophotos()`), keeping the alignment, the markers, the DEMs and the
mosaics. So that the project stays consistent (Metashape keeps the list of its
dense clouds in the project file), the Metashape API is used, which deletes its own
files on save (measured on a copy: 1967 MB -> 1087 MB, project reopened without error).
Lightening is therefore always PERMANENT: no recycle bin is possible. Tried: sending the
folders to the recycle bin BEFORE, then letting the API clean up, fails (`chunk.remove` reads the dense cloud again and
raises "Can't open file dense_cloud.zip", the project keeps phantom dense clouds).

  1. read-only: the chunks, cameras, markers are noted;
  2. the API removes what was chosen in each chunk and saves;
  3. the folders and files that would remain are deleted;
  4. check: the project reopens, same cameras and markers, nothing left of what was chosen.

Deleting the project (`delete_project`) removes the .psx and its .files folder, and nothing else: the files
exported next to it (orthomosaic, DEM, clouds, reports) are not touched.

Never on an open project (`lock` file held by a Metashape) nor on the one that is open here.
Each action is written to a CSV journal (`<settings>\\fastspace\\log.csv`).
"""

import csv
import ctypes
import datetime
import gc
import os
import shutil
import stat
import subprocess
import time

from ... import config, i18n, journal
from . import scanner

RECYCLE = "recycle"
PERMANENT = "permanent"
CONFIRM_WORD = "DELETE"
LOG_NAME = "log.csv"
RETRIES = 5


class SpaceError(Exception):
    """Readable failure: the message is shown as it is."""


# --------------------------------------------------------------------------
# Safeguards
# --------------------------------------------------------------------------

def confirmed(text):
    """Has the confirmation word been typed? (ignoring case and spaces)"""
    return str(text).strip().lower() == CONFIRM_WORD.lower()


def current_document_path():
    """Path of the project open in this Metashape ("" if none)."""
    try:
        import Metashape
        return Metashape.app.document.path or ""
    except Exception:
        return ""


def same_path(a, b):
    return bool(a) and bool(b) and os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def refusal(project):
    """Reason why this project is left alone ("" if it can be touched)."""
    if project.in_use:
        return "open in a Metashape (lock file held)"
    if not project.orphan and same_path(project.path, current_document_path()):
        return "open in this Metashape"
    return ""


def delete_refusal(project):
    """Absolute reason not to delete ("" if it can be): the project open here is never deleted.

    A lock held by another Metashape can be overridden ("Confirm delete" screen): it is `refusal` that reports it."""
    if not project.orphan and same_path(project.path, current_document_path()):
        return "open in this Metashape"
    return ""


def reveal(path):
    """Open Explorer on the project (file selected) or, failing that, on its folder.

    Returns True if Explorer was launched."""
    path = os.path.normpath(path)
    try:
        if os.name == "nt" and os.path.exists(path):
            subprocess.Popen('explorer /select,"{}"'.format(path))
            return True
        folder = path if os.path.isdir(path) else os.path.dirname(path)
        if os.name == "nt" and os.path.isdir(folder):
            os.startfile(folder)
            return True
    except OSError:
        pass
    return False


def is_network(path):
    if path.startswith("\\\\"):
        return True
    try:
        drive = os.path.splitdrive(os.path.abspath(path))[0] + "\\"
        return ctypes.windll.kernel32.GetDriveTypeW(drive) == 4          # DRIVE_REMOTE
    except Exception:
        return False


def recycle_available(path):
    """Does the recycle bin exist for this path? (Windows, local disk)"""
    return os.name == "nt" and not is_network(path)


# --------------------------------------------------------------------------
# Recycle bin and deletion
# --------------------------------------------------------------------------

def recycle(paths):
    """Send files or folders to the Windows recycle bin. Returns (ok, message).

    If an item exceeds the size of the bin, Windows asks the question (its confirmation is not
    overridden): a refusal gives ok=False and nothing is deleted."""
    paths = [os.path.abspath(p) for p in paths if os.path.exists(p)]
    if not paths:
        return True, ""
    if os.name != "nt":
        return False, "recycle bin unavailable outside Windows"
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", ctypes.c_void_p),
                    ("pTo", ctypes.c_void_p), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    FO_DELETE, FOF_ALLOWUNDO, FOF_NOERRORUI = 3, 0x0040, 0x0400
    buffer = ctypes.create_unicode_buffer("\0".join(paths) + "\0\0")
    operation = SHFILEOPSTRUCTW()
    operation.wFunc = FO_DELETE
    operation.pFrom = ctypes.addressof(buffer)
    operation.fFlags = FOF_ALLOWUNDO | FOF_NOERRORUI
    code = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
    if code != 0 or operation.fAnyOperationsAborted:
        return False, "recycle bin refused (code {})".format(code)
    leftover = [p for p in paths if os.path.exists(p)]
    if leftover:
        return False, "not everything went to the recycle bin: {}".format(leftover[0])
    return True, ""


def _unlock(function, path, _info):
    os.chmod(path, stat.S_IWRITE)
    function(path)


def remove_path(path):
    """Permanently delete a file or a folder (raises OSError if impossible)."""
    if os.path.isdir(path) and not os.path.islink(path):
        for attempt in range(RETRIES):
            try:
                shutil.rmtree(path, onerror=_unlock)
                return
            except OSError:
                if attempt == RETRIES - 1:
                    raise
                time.sleep(0.5)
    elif os.path.exists(path):
        try:
            os.remove(path)
        except PermissionError:
            os.chmod(path, stat.S_IWRITE)
            os.remove(path)


# --------------------------------------------------------------------------
# Journal
# --------------------------------------------------------------------------

def log_path():
    return os.path.join(config.settings_dir(), "fastspace", LOG_NAME)


def log_action(action, mode, project, ok, message="", size=0):
    """Append a line to the CSV journal. Never raises."""
    try:
        path = log_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        new = not os.path.exists(path)
        with open(path, "a", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, delimiter=";")
            if new:
                writer.writerow(["date", "action", "mode", "project", "bytes", "ok", "message"])
            writer.writerow([datetime.datetime.now().isoformat(timespec="seconds"), action, mode, project.path,
                             int(size), "yes" if ok else "no", message])
    except Exception as exc:
        journal.write("Fast Space: journal not written: {!r}".format(exc))


# --------------------------------------------------------------------------
# Lightening
# --------------------------------------------------------------------------

def removable_paths(project, items=scanner.DEFAULT_ITEMS):
    """Folders (dense_cloud*, depth_maps*) and files (orthomosaic*/images*.zip) of the project that belong to `items`."""
    found = []
    try:
        chunks = list(os.scandir(project.files_dir))
    except OSError:
        return found
    for chunk in chunks:
        if not chunk.is_dir(follow_symlinks=False):
            continue
        for frame in scanner._scan(chunk.path):
            if not frame.is_dir(follow_symlinks=False):
                continue
            for item in scanner._scan(frame.path):
                if not item.is_dir(follow_symlinks=False):
                    continue
                part = scanner.part_of(item.name)
                if part == scanner.DENSE and "dense" in items or part == scanner.DEPTH and "depth" in items:
                    found.append(item.path)
                elif part == scanner.ORTHO and "ortho" in items:
                    found.extend(entry.path for entry in scanner._scan(item.path)
                                 if entry.is_file(follow_symlinks=False) and scanner.ORTHO_IMAGES.match(entry.name))
    return found


def _open(path, read_only):
    import Metashape
    document = Metashape.Document()
    document.open(path, read_only=read_only)
    return document


def _dense_list(chunk):
    for name in ("point_clouds", "dense_clouds"):
        items = getattr(chunk, name, None)
        if items is not None:
            return name, list(items)
    return None, []


def _depth_list(chunk):
    items = getattr(chunk, "depth_maps_sets", None)
    if items is not None:
        return list(items)
    single = getattr(chunk, "depth_maps", None)
    return [single] if single is not None else []


def snapshot(document):
    """State of a project for the check: one row per chunk."""
    rows = []
    for chunk in document.chunks:
        rows.append({"label": chunk.label, "cameras": len(chunk.cameras), "markers": len(chunk.markers),
                     "dense": len(_dense_list(chunk)[1]), "depth": len(_depth_list(chunk))})
    return rows


def _orthomosaics(chunk):
    items = getattr(chunk, "orthomosaics", None)
    if items is not None:
        return list(items)
    single = getattr(chunk, "orthomosaic", None)
    return [single] if single is not None else []


def strip_chunk(chunk, items=scanner.DEFAULT_ITEMS):
    """Remove from a chunk what `items` asks for (dense, depth, ortho). Returns what was removed."""
    removed = []
    if "dense" in items:
        _name, dense = _dense_list(chunk)
        if dense:
            chunk.remove(dense)
            removed.append("dense")
    if "depth" in items:
        sets = getattr(chunk, "depth_maps_sets", None)
        if sets:
            chunk.remove(list(sets))
            removed.append("depth")
        elif sets is None and getattr(chunk, "depth_maps", None) is not None:
            chunk.depth_maps = None
            removed.append("depth")
    if "ortho" in items:
        for mosaic in _orthomosaics(chunk):
            mosaic.removeOrthophotos()
            removed.append("ortho")
    return removed


def check_snapshots(before, after, items=scanner.DEFAULT_ITEMS):
    """None if the project is as before (except what `items` removes), otherwise the text of the problem."""
    if len(before) != len(after):
        return "different number of chunks ({} -> {})".format(len(before), len(after))
    for old, new in zip(before, after):
        for key in ("cameras", "markers"):
            if old[key] != new[key]:
                return "chunk {}: {} different ({} -> {})".format(old["label"], key, old[key], new[key])
        if ("dense" in items and new["dense"]) or ("depth" in items and new["depth"]):
            return "chunk {}: dense cloud or depth maps remain".format(old["label"])
    return None


def lighten(project, mode, progress=None, items=scanner.DEFAULT_ITEMS):
    """Lighten a project: removes what `items` asks for (dense, depth, ortho). Returns {ok, freed, in_bin, error, steps}.

    To be run in Metashape's main thread (the API cannot work elsewhere).
    `progress(text)` announces each step."""
    items = tuple(item for item in scanner.ITEMS if item in items)
    progress = progress or (lambda text: None)
    result = {"ok": False, "freed": 0, "in_bin": 0, "error": "", "steps": []}

    def step(text):
        result["steps"].append(text)
        progress(text)

    reason = refusal(project)
    if reason:
        result["error"] = reason
        return result
    if project.orphan:
        result["error"] = "no .psx: nothing to lighten (delete the folder)"
        return result
    if mode != PERMANENT:
        result["error"] = "lightening goes through Metashape, which deletes its own files: no recycle bin"
        return result
    if not items:
        result["error"] = "nothing to remove: no box ticked"
        return result
    before = project.removable(items)
    try:
        if project.lock == scanner.STALE:
            # A lock left by a dead session (often another PC) forces Metashape to open read-only.
            step("stale lock")
            try:
                os.remove(os.path.join(project.files_dir, "lock"))
            except OSError as exc:
                raise SpaceError(i18n.t("space.err_lock_remove", error=exc))
            project.lock = scanner.FREE
        step("reading")
        document = _open(project.path, True)
        snap_before = snapshot(document)
        document = None
        gc.collect()
        step("cleaning the project")
        document = _open(project.path, False)
        for chunk in document.chunks:
            strip_chunk(chunk, items)
        document.save()
        document = None
        gc.collect()
        left = removable_paths(project, items)
        if left:
            step("remaining folders")
            for path in left:
                remove_path(path)
        step("checking")
        document = _open(project.path, True)
        problem = check_snapshots(snap_before, snapshot(document), items)
        document = None
        gc.collect()
        if problem:
            raise SpaceError("check: " + problem)
        after = scanner.measure_project(project.path)
        result["freed"] = max(0, before - after.removable(items))
        result["ok"] = True
        project.parts, project.files_size = after.parts, after.files_size
    except Exception as exc:
        text = str(exc) if isinstance(exc, SpaceError) else repr(exc)
        if "read-only mode" in text:
            text = i18n.t("space.err_read_only", owner=project.lock_owner or scanner.lock_owner(project.files_dir) or "?")
        result["error"] = text
    return result


# --------------------------------------------------------------------------
# Deleting the project
# --------------------------------------------------------------------------

def delete_project(project, mode, force=False):
    """Delete the project (.psx and .files only), or the orphan folder. Returns {ok, freed, in_bin, error}.

    `force`: the user confirmed despite a lock held by another Metashape; only the project
    open in this Metashape is still refused."""
    result = {"ok": False, "freed": 0, "in_bin": 0, "error": ""}
    reason = delete_refusal(project) if force else refusal(project)
    if reason:
        result["error"] = reason
        return result
    size = project.total
    targets = [project.files_dir] if project.orphan else [project.path, project.files_dir]
    try:
        for target in targets:
            if len(os.path.abspath(target)) < 8:
                raise SpaceError("path too short to be a project: " + target)
        if mode == RECYCLE:
            ok, message = recycle(targets)
            if not ok:
                raise SpaceError(message)
            result["in_bin"] = size
        else:
            for target in targets:                          # the .psx first: a leftover = a visible orphan
                remove_path(target)
            result["freed"] = size
        result["ok"] = True
    except Exception as exc:
        result["error"] = str(exc) if isinstance(exc, SpaceError) else repr(exc)
    return result
