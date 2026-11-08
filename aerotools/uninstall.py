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
import shutil

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


def targets():
    """{id: [paths]}: what each item removes (paths that exist or not)."""
    launcher = launcher_path()
    return {MENU: [launcher, launcher + ".bak"], CODE: [config.app_dir()], SETTINGS: [config.settings_dir()], ENV: [env_dir()]}


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


def _remove_path(item_id, path):
    if not _safe(item_id, path):
        raise OSError("unexpected folder: " + path)
    if os.path.isdir(path):
        shutil.rmtree(path)
    else:
        os.remove(path)
    if os.path.exists(path):
        raise OSError("still there: " + path)


def remove(ids, progress=None):
    """Removes the items asked for ("menu" is always included). `progress(item_id)` is called before each one.
    Returns {"removed": [ids], "errors": {id: message}}; an item that fails does not stop the others."""
    wanted = [item_id for item_id in ORDER if item_id in ids or item_id in LOCKED]
    removed, errors = [], {}
    for item_id in wanted:
        if progress:
            progress(item_id)
        failed = []
        for path in targets()[item_id]:
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
