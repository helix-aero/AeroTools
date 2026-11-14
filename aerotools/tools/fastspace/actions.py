"""What Fast Space can do with a project: lighten it (soft version) or delete it (hard version).

Two possible fates for what is removed:
  RECYCLE    Windows recycle bin: everything can be restored as long as it is not emptied (local disk only;
             a Windows network drive has no recycle bin);
  PERMANENT  permanent deletion. The interface then asks to type DELETE.

Lightening (`lighten`) removes, at the user's choice, the dense cloud, the depth maps and the orthophotos (the rectified
images of the orthomosaic, `Orthomosaic.removeOrthophotos()`), keeping the alignment, the markers, the DEMs and the
mosaics. So that the project stays consistent (Metashape keeps the list of its
dense clouds in the project file), the Metashape API is used, which deletes its own
files on save (measured on a copy: about 2 GB -> about 1 GB, project reopened without error).
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
import re
import shutil
import stat
import subprocess
import time

from ... import config, i18n, journal, msapi
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
        return msapi.document_path() or ""
    except Exception:
        return ""


def same_path(a, b):
    return bool(a) and bool(b) and os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def recheck_lock(project):
    """Read the lock again, now. The state noted by the scan may be hours old (30 projects on a network disk, another PC opens one
    meanwhile). Returns the state found; the project takes it when it says "busy", or when the lock is gone."""
    state = scanner.lock_state(project.files_dir)
    if state in (scanner.OPEN, scanner.UNKNOWN) or (state == scanner.FREE and project.lock != scanner.FREE):
        project.lock = state
        if state != scanner.FREE and not project.lock_owner:
            project.lock_owner = scanner.lock_owner(project.files_dir)
    return state


def refusal(project, here=None):
    """Reason why this project is left alone ("" if it can be touched). `here`: path of the project open in this Metashape,
    read by the caller on the UI thread (read here when None)."""
    if project.lock == scanner.UNKNOWN:
        return "the lock file cannot be read (network or disk error): treated as open"
    if project.in_use:
        return "open in a Metashape (lock file held)"
    if not project.orphan and same_path(project.path, current_document_path() if here is None else here):
        return "open in this Metashape"
    return ""


def delete_refusal(project, here=None):
    """Absolute reason not to delete ("" if it can be): the project open here is never deleted.

    A lock held by another Metashape can be overridden ("Confirm delete" screen): it is `refusal` that reports it.
    `here`: see `refusal` (the delete worker thread must not ask Metashape itself)."""
    if not project.orphan and same_path(project.path, current_document_path() if here is None else here):
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

NOT_IN_BIN = "not in the recycle bin"        # message prefix: the items are gone, but not (verifiably) to the bin


def _shfileop(paths, hwnd=0):
    """SHFileOperationW(FO_DELETE, undo). Returns (code, aborted, mapped): `mapped` is {old path: new path} of the items that
    Windows moved (the new path is inside the bin), or None when Windows gave no name mappings."""
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", ctypes.c_void_p),
                    ("pTo", ctypes.c_void_p), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    class SHNAMEMAPPINGW(ctypes.Structure):
        _fields_ = [("pszOldPath", wintypes.LPWSTR), ("pszNewPath", wintypes.LPWSTR),
                    ("cchOldPath", ctypes.c_int), ("cchNewPath", ctypes.c_int)]

    class HANDLETOMAPPINGS(ctypes.Structure):
        _fields_ = [("uNumberOfMappings", wintypes.UINT), ("lpSHNameMapping", ctypes.POINTER(SHNAMEMAPPINGW))]

    FO_DELETE, FOF_WANTMAPPINGHANDLE, FOF_ALLOWUNDO, FOF_NOERRORUI, FOF_WANTNUKEWARNING = 3, 0x0020, 0x0040, 0x0400, 0x4000
    buffer = ctypes.create_unicode_buffer("\0".join(paths) + "\0\0")
    operation = SHFILEOPSTRUCTW()
    operation.hwnd = hwnd or None
    operation.wFunc = FO_DELETE
    operation.pFrom = ctypes.addressof(buffer)
    operation.fFlags = FOF_ALLOWUNDO | FOF_NOERRORUI | FOF_WANTNUKEWARNING | FOF_WANTMAPPINGHANDLE
    code = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
    mapped = None
    if operation.hNameMappings:
        mapped = {}
        try:
            table = ctypes.cast(operation.hNameMappings, ctypes.POINTER(HANDLETOMAPPINGS)).contents
            for index in range(table.uNumberOfMappings):
                entry = table.lpSHNameMapping[index]
                mapped[entry.pszOldPath] = entry.pszNewPath
        except Exception:
            mapped = None
        finally:
            try:
                ctypes.windll.shell32.SHFreeNameMappings(ctypes.c_void_p(operation.hNameMappings))
            except Exception:
                pass
    return code, bool(operation.fAnyOperationsAborted), mapped


def _in_bin(path):
    lowered = path.lower().replace("/", "\\")
    return "\\$recycle.bin\\" in lowered or "\\recycler\\" in lowered


def recycle(paths, hwnd=0):
    """Send files or folders to the Windows recycle bin. Returns (ok, message).

    (True, "")             every item went to the bin, confirmed by the name mappings Windows returned;
    (True, NOT_IN_BIN...)  the items are gone but it is not confirmed (or it is known) that they are NOT in the bin: Windows asked
                           "delete permanently?" (item bigger than the bin, bin off for the drive) and the answer was yes. The caller
                           must report a permanent deletion, never "in the bin";
    (False, message)       nothing was deleted (refused or failed)."""
    paths = [os.path.abspath(p) for p in paths if os.path.exists(p)]
    if not paths:
        return True, ""
    if os.name != "nt":
        return False, "recycle bin unavailable outside Windows"
    code, aborted, mapped = _shfileop(paths, hwnd)
    if code != 0 or aborted:
        return False, "recycle bin refused (code {})".format(code)
    leftover = [p for p in paths if os.path.exists(p)]
    if leftover:
        return False, "not everything went to the recycle bin: {}".format(leftover[0])
    if mapped is None:
        return True, NOT_IN_BIN + ": Windows gave no proof that it went there"
    done = {os.path.normcase(os.path.abspath(old)): new for old, new in mapped.items()}
    missing = [p for p in paths if not _in_bin(done.get(os.path.normcase(p), "") or "")]
    if missing:
        return True, NOT_IN_BIN + ": deleted permanently ({})".format(os.path.basename(missing[0]))
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
        journal.write("Aero-Space: journal not written: {!r}".format(exc))


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
    return msapi.open_document(path, read_only)


def _meta_keys(cloud):
    """Lower-case keys of the cloud's meta (what Metashape wrote when it was built or imported); [] if unreadable."""
    try:
        meta = cloud.meta
        return [str(key).lower() for key in meta.keys()]
    except Exception:
        return []


def is_rebuildable(cloud):
    """True only when the cloud is PROVABLY one Metashape built from the photos (its meta carries a `build...` parameter) and not
    one that was imported (LAS/LAZ/PLY...: it cannot be rebuilt). When in doubt: False, and the cloud is kept."""
    keys = _meta_keys(cloud)
    if any("import" in key for key in keys):
        return False
    return any(key.startswith("build") for key in keys)


def split_clouds(chunk):
    """(clouds that can be removed, clouds that are kept) of a chunk."""
    build, keep = [], []
    for cloud in _dense_list(chunk)[1]:
        (build if is_rebuildable(cloud) else keep).append(cloud)
    return build, keep


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
    """Remove from a chunk what `items` asks for (dense, depth, ortho). Returns what was removed. Point clouds that are not provably
    rebuildable (imported ones) are never removed."""
    removed = []
    if "dense" in items:
        build, _keep = split_clouds(chunk)
        if build:
            chunk.remove(build)
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


def check_snapshots(before, after, items=scanner.DEFAULT_ITEMS, kept_dense=None):
    """None if the project is as before (except what `items` removes), otherwise the text of the problem.
    `kept_dense`: per chunk, the number of clouds that had to stay (imported): they must still be there."""
    if len(before) != len(after):
        return "different number of chunks ({} -> {})".format(len(before), len(after))
    for index, (old, new) in enumerate(zip(before, after)):
        for key in ("cameras", "markers"):
            if old[key] != new[key]:
                return "chunk {}: {} different ({} -> {})".format(old["label"], key, old[key], new[key])
        keep = kept_dense[index] if kept_dense and index < len(kept_dense) else 0
        if "dense" in items and new["dense"] < keep:
            return "chunk {}: a point cloud that had to be kept is gone".format(old["label"])
        if ("dense" in items and new["dense"] > keep) or ("depth" in items and new["depth"]):
            return "chunk {}: dense cloud or depth maps remain".format(old["label"])
    return None


BACKUP_MARK = "_AeroSpace_backup_"
BACKUP_FILES = "description_files"
BACKUP_FILE_MAX = 100 * 1024 * 1024          # a "description file" bigger than this is not one: refuse to guess
BACKUP_TOTAL_MAX = 400 * 1024 * 1024
BACKUP_EXTS = (".zip", ".xml")
BACKUP_NOTE = """Safety copy made by Aero-Space before it lightened the project.

What is here: the project file ({psx}.bak) and, in the folder {files}, the small description files of the project
with their sub-folders.
To go back (only if the project no longer opens, or opens wrongly): close Metashape, copy the contents of {files}
into the .files folder of the project (same sub-folders, replacing the files of the same name), and rename {psx}.bak
to {psx}.
The dense clouds, depth maps and orthophotos that were removed are not in this copy (that was the aim): they can be
rebuilt in Metashape from the photos.
This folder is never deleted by Aero-Space: remove it yourself once the lightened project works.
"""


def _listed(folder):
    try:
        return sorted(os.scandir(folder), key=lambda entry: entry.name)
    except OSError:
        return []


def _metadata_files(files_dir):
    """The small description files that Metashape rewrites when it saves: the zips and xml directly in the .files folder, in each
    chunk folder (numbered) and in each frame folder (numbered), not in the folders of the dense cloud, depth maps, tie points..."""
    folders = [files_dir]
    chunks = [entry.path for entry in _listed(files_dir) if entry.is_dir(follow_symlinks=False) and entry.name.isdigit()]
    frames = [entry.path for chunk in chunks for entry in _listed(chunk)
              if entry.is_dir(follow_symlinks=False) and entry.name.isdigit()]
    folders += chunks + frames
    return [entry.path for folder in folders for entry in _listed(folder)
            if entry.is_file(follow_symlinks=False) and entry.name.lower().endswith(BACKUP_EXTS)]


def safety_copy(project, now=None):
    """Copy the .psx and its small description files before Metashape is allowed to write the project. Returns the folder, which sits
    next to the project (a backup sits beside its project, never inside it) and is never deleted by us. Raises SpaceError if the copy cannot
    be made: the project is then left as it is."""
    now = now or datetime.datetime.now()
    stem = os.path.splitext(os.path.basename(project.path))[0]
    root = os.path.join(os.path.dirname(project.path), "{}{}{}".format(stem, BACKUP_MARK, now.strftime("%Y%m%d_%H%M%S")))
    created = False
    try:
        sources = _metadata_files(project.files_dir)
        sizes = [os.path.getsize(path) for path in sources]
        total = os.path.getsize(project.path) + sum(sizes)
        if total > BACKUP_TOTAL_MAX or any(size > BACKUP_FILE_MAX for size in sizes):
            raise SpaceError("the description files are unexpectedly large ({:.0f} MB)".format(total / 1048576.0))
        os.makedirs(root)
        created = True
        shutil.copy2(project.path, os.path.join(root, os.path.basename(project.path) + ".bak"))
        for path in sources:                                # not under a name ending in ".files": the scan would list it as an orphan
            target = os.path.join(root, BACKUP_FILES, os.path.relpath(path, project.files_dir))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(path, target)
        with open(os.path.join(root, "RESTORE.txt"), "w", encoding="utf-8") as handle:
            handle.write(BACKUP_NOTE.format(psx=os.path.basename(project.path), files=BACKUP_FILES))
    except (OSError, SpaceError) as exc:
        if created:                                         # only a folder made by this call: never an earlier copy
            shutil.rmtree(root, ignore_errors=True)
        raise SpaceError(i18n.t("space.err_backup", error=exc))
    return root


def project_version(path):
    """(major, minor) written in the header of the .psx (`<document version="2.1.0">`), or None."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(2048).decode("utf-8", "replace")
    except OSError:
        return None
    match = re.search(r'<document[^>]*\sversion="(\d+)\.(\d+)', head)
    return (int(match.group(1)), int(match.group(2))) if match else None


def running_version():
    match = re.match(r"\s*(\d+)\.(\d+)", msapi.app_version() or "")
    return (int(match.group(1)), int(match.group(2))) if match else None


def upgrade_note(project):
    """Text if saving this project here would write it in a newer format than the one it has ("" otherwise or if unknown)."""
    written, running = project_version(project.path), running_version()
    if written and running and running > written:
        return "written by Metashape {}.{}, this is {}.{}".format(written[0], written[1], running[0], running[1])
    return ""


def lighten(project, mode, progress=None, items=scanner.DEFAULT_ITEMS, allow_upgrade=False):
    """Lighten a project: removes what `items` asks for (dense, depth, ortho). Returns {ok, freed, in_bin, error, steps}.

    To be run in Metashape's main thread (the API cannot work elsewhere).
    `progress(text)` announces each step."""
    items = tuple(item for item in scanner.ITEMS if item in items)
    progress = progress or (lambda text: None)
    result = {"ok": False, "freed": 0, "in_bin": 0, "error": "", "steps": [], "backup": "", "kept_clouds": 0, "nothing": False}

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
    seen_busy = project.in_use
    state = recheck_lock(project)
    if state in (scanner.OPEN, scanner.UNKNOWN):
        result["error"] = refusal(project) + ("" if seen_busy else " (since the scan)")
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
        api_work, mosaics, kept_clouds, kept_dense = 0, False, 0, []
        for chunk in document.chunks:
            build, keep = split_clouds(chunk) if "dense" in items else ([], [])
            api_work, kept_clouds = api_work + len(build), kept_clouds + len(keep)
            kept_dense.append(len(keep))
            if "depth" in items:
                api_work += len(_depth_list(chunk))
            mosaics = mosaics or bool(_orthomosaics(chunk))
        document = None
        gc.collect()
        result["kept_clouds"] = kept_clouds
        # A folder of a kept (imported) cloud is never swept as a "leftover".
        sweep = tuple(item for item in items if not (item == "dense" and kept_clouds))
        need_api = api_work > 0 or (mosaics and "ortho" in items and bool(removable_paths(project, ("ortho",))))
        left = removable_paths(project, sweep)
        if not need_api and not left:
            result["ok"] = result["nothing"] = True         # nothing to remove: no copy, no save, the project is not touched
            step("nothing to remove")
            return result
        if need_api:
            note = "" if allow_upgrade else upgrade_note(project)
            if note:
                raise SpaceError(i18n.t("space.err_upgrade", note=note))
            step("safety copy")
            result["backup"] = safety_copy(project)
            journal.write("Aero-Space: safety copy of {} in {}".format(project.name, result["backup"]))
            step("cleaning the project")
            document = _open(project.path, False)
            for chunk in document.chunks:
                strip_chunk(chunk, items)
            document.save()
            document = None
            gc.collect()
            left = removable_paths(project, sweep)
        if left:
            step("remaining folders")
            for path in left:
                remove_path(path)
        step("checking")
        document = _open(project.path, True)
        problem = check_snapshots(snap_before, snapshot(document), items, kept_dense)
        document = None
        gc.collect()
        if problem:
            raise SpaceError("check: " + problem)
        after = scanner.measure_project(project.path)
        result["freed"] = max(0, before - after.removable(items))
        if kept_clouds:
            journal.write("Aero-Space: {} kept {} point cloud(s) not built by Metashape".format(project.name, kept_clouds))
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

def delete_project(project, mode, force=False, here=None, hwnd=0):
    """Delete the project (.psx and .files only), or the orphan folder. Returns {ok, freed, in_bin, error, note}.

    The lock is read again here, not taken from the scan. A project that is open now, or that the scan saw open and the user did not
    confirm, is refused.
    `force`: the user confirmed THIS project despite a lock held by another Metashape (the "Confirm delete" screen); it only counts
    for a project that the scan saw open. The project open in this Metashape is always refused.
    `here`: path of the project open in this Metashape, read on the UI thread by the caller (this runs in a worker thread).
    `hwnd`: window that owns the Windows prompt (recycle mode).
    `note` is set when the deletion worked but not the way that was asked (recycle mode: the items did not go to the bin)."""
    result = {"ok": False, "freed": 0, "in_bin": 0, "error": "", "note": ""}
    reason = delete_refusal(project, here)
    if not reason:
        seen_busy = project.in_use
        state = recheck_lock(project)
        busy_now = state in (scanner.OPEN, scanner.UNKNOWN)
        if (seen_busy or busy_now) and not (force and seen_busy):
            reason = refusal(project, here) if project.in_use else "open in a Metashape (lock file held)"
            if not seen_busy:
                reason += " (opened since the scan)"
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
            ok, message = recycle(targets, hwnd=hwnd) if hwnd else recycle(targets)
            if not ok:
                raise SpaceError(message)
            if message:                                     # gone, but not provably into the bin: never say "recoverable"
                result["freed"] = size
                result["note"] = message
            else:
                result["in_bin"] = size
        elif mode == PERMANENT:
            for target in targets:                          # the .psx first: a leftover = a visible orphan
                remove_path(target)
            result["freed"] = size
        else:
            raise SpaceError("unknown deletion mode: {!r}".format(mode))
        result["ok"] = True
    except Exception as exc:
        result["error"] = str(exc) if isinstance(exc, SpaceError) else repr(exc)
    return result
