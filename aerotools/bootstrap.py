"""Adds the "Aero" menu to Metashape and loads the tools on demand.

Permanent menu mechanism
------------------------
At startup, Metashape runs every .py file found in
`%LOCALAPPDATA%\\Agisoft\\Metashape Pro\\scripts\\`. A launcher of about ten lines is installed there
(see install/install_aerotools.bat); it adds the AeroTools root of the server to sys.path and calls
`install_menu()`. The Aero menu therefore comes back each time Metashape opens, with nothing else to run.

Late loading
------------
`install_menu()` only registers menu entries: no tool and no Qt widget is built at startup. The code of
a tool is imported at the first click on its entry. The menu callbacks all go through
`launch(tool_id)`, so reloading the code does not require registering the menu again, which the
Metashape API cannot undo.

Robustness
----------
An unreachable server or a broken tool must never stop Metashape from starting: everything is under
try/except and only writes to the console.
"""

import builtins
import importlib
import os
import sys

_MENU_FLAG = "_AEROTOOLS_MENU_INSTALLED"
_LAST_TOOL_FLAG = "_AEROTOOLS_LAST_TOOL"
_ROOT_FLAG = "_AEROTOOLS_ROOT"

MENU = "Aero"


# --------------------------------------------------------------------------
# Repository root
# --------------------------------------------------------------------------

def root():
    """Folder that contains the "aerotools" package."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    setattr(builtins, _ROOT_FLAG, here)
    return here


def ensure_on_path():
    here = root()
    if here not in sys.path:
        sys.path.insert(0, here)
    return here


# --------------------------------------------------------------------------
# Menu
# --------------------------------------------------------------------------

def install_menu(force=False):
    """Registers the entries of the Aero menu. Idempotent.

    The flag lives on `builtins` and not in a module: it therefore survives the
    purge of sys.modules done by `reload_all()`, which avoids piling up
    duplicate menu entries at each reload.
    """
    if getattr(builtins, _MENU_FLAG, False) and not force:
        return False
    ensure_on_path()
    try:
        from . import config, i18n, msapi
    except Exception as exc:
        print("[AeroTools] cannot load: {!r}".format(exc))
        return False

    # The menu is added once per Metashape session (the Metashape API cannot remove or rename an entry).
    added = 0
    for entry in config.TOOLS:
        name = entry["menu"]
        if not entry.get("ready", False):
            name = "{} {}".format(name, i18n.t("menu.soon"))
        label = "{}/{}".format(MENU, name)
        callback = _make_launcher(entry["id"])
        if msapi.add_menu_item(label, callback, entry.get("shortcut")):
            added += 1

    # Three blocks split by a line: the tools, maintenance (updates, reload), information.
    msapi.add_menu_separator(MENU)
    msapi.add_menu_item("{}/{}".format(MENU, i18n.t("menu.update")), update_plugins)
    msapi.add_menu_item("{}/{}".format(MENU, i18n.t("menu.reload")), reload_all)
    msapi.add_menu_separator(MENU)
    msapi.add_menu_item("{}/{}".format(MENU, i18n.t("menu.about")), about)

    setattr(builtins, _MENU_FLAG, True)
    print(
        "[AeroTools] menu \"{}\" installed ({} tool(s)) - root {}".format(
            MENU, added, root()
        )
    )
    return True


def _make_launcher(tool_id):
    def _run():
        launch(tool_id)

    _run.__name__ = "aero_launch_" + tool_id
    return _run


# --------------------------------------------------------------------------
# Loading the tools
# --------------------------------------------------------------------------

def launch(tool_id, reload=False):
    """Loads a tool and installs it. Returns True if the tool started."""
    ensure_on_path()
    from . import config, i18n, journal, msapi

    entry = config.tool(tool_id)
    if entry is None:
        msapi.message_box(i18n.t("boot.unknown_tool", tool=tool_id))
        return False

    if not entry.get("ready", False):
        msapi.message_box(i18n.t("boot.not_ready", title=entry["title"]))
        return False

    if reload:
        purge_modules()

    module_name = "aerotools.tools.{}.tool".format(tool_id)
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        journal.write("failed to load {}: {!r}".format(tool_id, exc))
        msapi.message_box(
            i18n.t("boot.load_failed", tool=tool_id, error=repr(exc))
        )
        return False

    try:
        module.install()
    except Exception as exc:
        journal.write("failed to start {}: {!r}".format(tool_id, exc))
        msapi.message_box(
            i18n.t("boot.start_failed", tool=tool_id, error=repr(exc))
        )
        return False

    if entry.get("reopen", False):
        setattr(builtins, _LAST_TOOL_FLAG, tool_id)
    return True


def purge_modules():
    """Forgets every aerotools module to re-read the code from the server.

    The open tools are closed cleanly before the purge: without this,
    their Qt event filters and timers would keep running in the old code.
    """
    for name in sorted(sys.modules):
        if not name.startswith("aerotools"):
            continue
        module = sys.modules.get(name)
        closer = getattr(module, "uninstall", None)
        if callable(closer) and name.endswith(".tool"):
            try:
                closer()
            except Exception:
                pass
    for name in [n for n in sys.modules if n.startswith("aerotools")]:
        del sys.modules[name]


def reload_all():
    """Re-reads the code from the server and reopens the last panel used (not the one-off windows)."""
    last = getattr(builtins, _LAST_TOOL_FLAG, None)
    here = root()
    purge_modules()
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        module = importlib.import_module("aerotools.bootstrap")
    except Exception as exc:
        print("[AeroTools] cannot reload: {!r}".format(exc))
        return False
    print("[AeroTools] code reloaded from {}".format(here))
    if last and module.reopens(last):
        return module.launch(last)
    return True


def reopens(tool_id):
    """True if "Reload AeroTools" must reopen this tool (a panel, not a one-off window).

    The test is redone here, at reload time: a flag left by an old version reopens nothing."""
    from . import config
    entry = config.tool(tool_id)
    return bool(entry and entry.get("reopen", False))


# --------------------------------------------------------------------------
# Updates
# --------------------------------------------------------------------------

def update_plugins():
    """Aero > Updates menu: opens the window, reloads the code if something changed."""
    ensure_on_path()
    from . import i18n, journal, msapi
    try:
        from . import update_dialog
        update_dialog.open_and_reload(msapi.main_window())
    except Exception as exc:
        journal.write("update window failed: {!r}".format(exc))
        msapi.message_box(i18n.t("boot.update_failed", error=repr(exc)))


# --------------------------------------------------------------------------
# About
# --------------------------------------------------------------------------

def about():
    ensure_on_path()
    from . import __version__, config, i18n, msapi

    soon = i18n.t("menu.soon")
    tools = "\n".join(
        "    {}  {}{}".format(
            entry["title"],
            i18n.t(entry["subtitle"]),
            "" if entry.get("ready") else "  " + soon,
        )
        for entry in config.TOOLS
    )
    msapi.message_box(
        i18n.t(
            "boot.about",
            version=__version__,
            vendor=config.VENDOR,
            tools=tools,
            root=root(),
            settings=config.settings_path(),
        )
    )
