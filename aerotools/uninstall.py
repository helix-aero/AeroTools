"""Remove AeroTools from this computer, without Qt and without Metashape.

What can go, each item on its own:
  menu      the launcher in Metashape's scripts folder (and its .bak): this is what puts the Aero menu back at each start.
            Always removed. Metashape cannot take a menu entry away while it runs, so the menu disappears at the next start.
  code      the code the installer downloaded (%LOCALAPPDATA%\\HELIX\\AeroToolsApp: the core and the tools that were installed)
  settings  the workstation settings and the log (%LOCALAPPDATA%\\HELIX\\AeroTools)
  env       the Python environment of the AI detection (%USERPROFILE%\\AIGCP\\_env, 100 MB to 3 GB)

Never removed: an AeroTools folder that is not the one the installer made (a development copy, a shared folder), the Python
installed on the system, and any Metashape project.

Only the exact folders above are deleted; each one is checked by name before anything is removed.
"""

import os
import re
import shutil
import stat

from . import config

LAUNCHER = "aerotools_launcher.py"
MENU, CODE, SETTINGS, ENV = "menu", "code", "settings", "env"
ORDER = (MENU, CODE, SETTINGS, ENV)
LOCKED = (MENU,)                                        # cannot be unticked: without it there is nothing to uninstall
DEFAULT_ON = (MENU, CODE)                               # ticked when the window opens: both come back with the installer


def scripts_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "Agisoft", "Metashape Pro", "scripts")


def launcher_path():
    return os.path.join(scripts_dir(), LAUNCHER)


def env_dir():
    return os.path.join(os.path.expanduser("~"), "AIGCP", "_env")


_ROOT_LINE = re.compile(r'^[ \t]*_aero_root\s*=\s*r"([^"]*)"(\s*\+\s*"\\\\")?', re.MULTILINE)


def launcher_root():
    """The folder the launcher points to ("" when there is no launcher or it is not one of ours). Read before the launcher is deleted."""
    try:
        with open(launcher_path(), encoding="utf-8", errors="replace") as handle:
            found = _ROOT_LINE.search(handle.read())
    except OSError:
        return ""
    if not found:
        return ""
    return found.group(1) + ("\\" if found.group(2) else "")


def code_folders():
    """The folders of the code the installer made: the default one, and the one the launcher points to when the installer was given
    another -AppDir. A folder under another name (a development copy) is not listed here."""
    folders = [config.app_dir()]
    pointed = launcher_root()
    if pointed:
        pointed = os.path.normpath(pointed)
        if os.path.basename(pointed) == config.APP_DIR_NAME and os.path.normcase(pointed) != os.path.normcase(os.path.normpath(folders[0])):
            folders.append(pointed)
    return folders


def targets():
    """{id: [paths]}: what each item removes (paths that exist or not)."""
    launcher = launcher_path()
    return {MENU: [launcher, launcher + ".bak"], CODE: code_folders(), SETTINGS: [config.settings_dir()], ENV: [env_dir()]}


def folder_size(path):
    """Total size in bytes of a file or folder (0 if missing); unreadable files count for nothing."""
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for folder, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(folder, name))
            except OSError:
                pass
    return total


def items():
    """[{"id", "paths" (the ones that exist), "bytes"}] in display order."""
    found = []
    for item_id, paths in targets().items():
        present = [path for path in paths if os.path.exists(path)]
        found.append({"id": item_id, "paths": present, "bytes": sum(folder_size(path) for path in present)})
    return sorted(found, key=lambda entry: ORDER.index(entry["id"]))


def _safe(item_id, path):
    """A folder is only deleted if its name is the expected one: a wrong environment variable must never reach another folder."""
    expected = {CODE: config.APP_DIR_NAME, SETTINGS: config.APP_NAME, ENV: "_env"}.get(item_id)
    return expected is None or os.path.basename(os.path.normpath(path)) == expected


def _unlock(function, path, _excinfo):
    """rmtree handler: a read-only file (a copy made from a zip, a model) is made writable and deleted again."""
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        function(path)
    except OSError:
        pass


def _remove_path(item_id, path):
    if not _safe(item_id, path):
        raise OSError("unexpected folder: " + path)
    if os.path.islink(path) or _is_junction(path):
        os.rmdir(path) if os.path.isdir(path) else os.remove(path)               # the link goes, never what it points to
    elif os.path.isdir(path):
        shutil.rmtree(path, onerror=_unlock)
    else:
        try:
            os.remove(path)
        except PermissionError:
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
            os.remove(path)
    if os.path.exists(path):
        raise OSError("still there: " + path)


def _is_junction(path):
    """A Windows junction: a folder that is a link (os.path.islink does not see it on Python 3.9)."""
    try:
        return bool(os.lstat(path).st_file_attributes & 0x400) and not os.path.islink(path)       # FILE_ATTRIBUTE_REPARSE_POINT
    except (AttributeError, OSError):
        return False


def remove(ids, progress=None):
    """Removes the items asked for ("menu" is always included). `progress(item_id)` is called before each one.
    Returns {"removed": [ids], "errors": {id: message}}; an item that fails does not stop the others."""
    wanted = [item_id for item_id in ORDER if item_id in ids or item_id in LOCKED]
    removed, errors = [], {}
    pointed = targets()                                    # read first: removing the launcher must not hide where the code is
    if CODE in wanted:
        try:
            from . import bootstrap
            bootstrap.release_files()                      # a file the core holds open (proj.db) would stay behind
        except Exception:
            pass
    for item_id in wanted:
        if progress:
            progress(item_id)
        failed = []
        for path in pointed[item_id]:
            if not os.path.exists(path):
                continue
            try:
                _remove_path(item_id, path)
            except OSError as exc:
                failed.append(str(exc))
        if failed:
            errors[item_id] = "; ".join(failed)
        else:
            removed.append(item_id)
    return {"removed": removed, "errors": errors}
