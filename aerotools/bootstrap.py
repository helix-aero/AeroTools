"""Adds the "Aero" menu to Metashape and loads the tools on demand.

Permanent menu mechanism
------------------------
At startup, Metashape runs every .py file found in
`%LOCALAPPDATA%\\Agisoft\\Metashape Pro\\scripts\\`. A launcher of about ten lines is installed there
(see install/install_aerotools.bat); it adds the AeroTools root folder to sys.path and calls
`install_menu()`. The Aero menu therefore comes back each time Metashape opens, with nothing else to run.

Late loading
------------
`install_menu()` only registers menu entries: no tool and no Qt widget is built at startup. The code of
a tool is imported at the first click on its entry. The menu callbacks all go through
`launch(tool_id)`, so reloading the code does not require registering the menu again, which the
Metashape API cannot undo.

Robustness
----------
A missing folder or a broken tool must never stop Metashape from starting: everything is under
try/except and only writes to the console.
"""

import builtins
import importlib
import os
import sys

_MENU_FLAG = "_AEROTOOLS_MENU_INSTALLED"
_LAST_TOOL_FLAG = "_AEROTOOLS_LAST_TOOL"
_ROOT_FLAG = "_AEROTOOLS_ROOT"
_TOOLS_FLAG = "_AEROTOOLS_MENU_TOOLS"           # ids of the tools that already have their entry in the menu of this session
_RETRY_FLAG = "_AEROTOOLS_MENU_RETRIES"         # how many times the menu layout was retried because the main window was not there yet
ARRANGE_RETRIES = 5
ARRANGE_RETRY_MS = 1000

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

    try:
        msapi.probe_capabilities()
    except Exception as exc:
        print("[AeroTools] capability probe failed: {!r}".format(exc))

    # An update that was cut short (Metashape closed in the middle of it) is undone before anything reads the installation.
    try:
        from . import updater
        for plugin in updater.plugins():
            updater.recover(plugin)
    except Exception as exc:
        print("[AeroTools] update recovery failed: {!r}".format(exc))

    # The menu is added once per Metashape session (the Metashape API cannot remove or rename an entry). Only the tools that are
    # installed on this computer have an entry; "Install plugins" adds the others as they come.
    setattr(builtins, _TOOLS_FLAG, set())
    added = sum(1 for entry in config.TOOLS if add_tool_menu(entry["id"], arrange=False))

    # Three blocks split by a line: the tools, maintenance (install, updates, reload, uninstall), information.
    if added:
        msapi.add_menu_separator(MENU)
    msapi.add_menu_item("{}/{}".format(MENU, i18n.t("menu.install")), install_plugins)
    msapi.add_menu_item("{}/{}".format(MENU, i18n.t("menu.update")), update_plugins)
    msapi.add_menu_item("{}/{}".format(MENU, i18n.t("menu.reload")), reload_all)
    msapi.add_menu_item("{}/{}".format(MENU, i18n.t("menu.uninstall")), open_uninstall)
    msapi.add_menu_separator(MENU)
    msapi.add_menu_item("{}/{}".format(MENU, i18n.t("menu.about")), about)

    arrange_menu()
    setattr(builtins, _MENU_FLAG, True)
    print(
        "[AeroTools] menu \"{}\" installed ({} {}) - root {}".format(
            MENU, added, i18n.plural(added, "tool", "tools"), root()
        )
    )
    return True


def add_tool_menu(tool_id, arrange=True):
    """Adds the menu entry of an installed tool, once per session. Returns True if it was added now. A tool installed while Metashape
    runs gets its entry at the end of the menu until the next start, when it takes its place. `arrange=False` leaves the ordering to the
    caller (install_menu adds every entry first and arranges the menu once)."""
    from . import config, i18n, msapi, updater
    entry = config.tool(tool_id)
    shown = getattr(builtins, _TOOLS_FLAG, None)
    if shown is None:
        shown = set()
        setattr(builtins, _TOOLS_FLAG, shown)
    if entry is None or tool_id in shown or not updater.tool_installed(tool_id):
        return False
    name = entry["menu"]
    if not entry.get("ready", False):
        name = "{} {}".format(name, i18n.t("menu.soon"))
    if not msapi.add_menu_item("{}/{}".format(MENU, name), _make_launcher(tool_id), entry.get("shortcut")):
        return False
    shown.add(tool_id)
    if arrange:
        arrange_menu()
    return True


def arrange_menu():
    """Tools first (in the order of the registry), then maintenance, then information: also for a tool installed during the session."""
    from . import config, i18n, msapi
    tools = [e["menu"] + (" " + i18n.t("menu.soon") if not e.get("ready", False) else "") for e in config.TOOLS]
    maintenance = [i18n.t(key) for key in ("menu.install", "menu.update", "menu.reload", "menu.uninstall")]
    done = msapi.arrange_menu(MENU, [tools, maintenance, [i18n.t("menu.about")]])
    logo = msapi.add_logo_button(MENU, os.path.join(root(), "assets", "logo", "helix-aero-AERO-blanc-512px.png"), i18n.t("menu.logo_tip"))
    if not (done and logo):
        _retry_arrange()
    return done


def _retry_arrange():
    """The main window may not exist yet when the launcher runs: try again a few times, one second apart, instead of never."""
    tries = getattr(builtins, _RETRY_FLAG, 0)
    if tries >= ARRANGE_RETRIES:
        return False
    setattr(builtins, _RETRY_FLAG, tries + 1)
    try:
        from .qt import QtCore
        QtCore.QTimer.singleShot(ARRANGE_RETRY_MS, _retry_now)
        return True
    except Exception:
        return False


def _retry_now():
    try:
        arrange_menu()
    except Exception as exc:
        print("[AeroTools] menu layout retry failed: {!r}".format(exc))


def _make_launcher(tool_id):
    def _run():
        launch(tool_id)

    _run.__name__ = "aero_launch_" + tool_id
    return _run


# --------------------------------------------------------------------------
# Loading the tools
# --------------------------------------------------------------------------

def _missing_text(missing):
    return ", ".join(missing)


def _module_named_in(exc):
    """The third-party package an ImportError is about ("numpy"), or "" for anything else (a bug of ours must stay visible)."""
    name = getattr(exc, "name", None) or ""
    if isinstance(exc, ImportError) and name and not name.startswith("aerotools"):
        return name.split(".")[0]
    return ""


def launch(tool_id):
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

    from . import updater
    if not updater.tool_installed(tool_id):
        msapi.message_box(i18n.t("boot.not_installed", title=entry["title"]))
        return False

    missing = config.missing_modules(tool_id)
    if missing:                                    # one clear line, before anything of the tool is imported
        journal.write("{} not started: missing Python {} {}".format(
            tool_id, i18n.plural(len(missing), "package", "packages"), _missing_text(missing)))
        msapi.message_box(i18n.t("boot.missing_module", title=entry["title"], modules=_missing_text(missing)))
        return False

    module_name = "aerotools.tools.{}.tool".format(tool_id)
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        journal.write("failed to load {}: {!r}".format(tool_id, exc))
        if _module_named_in(exc):
            msapi.message_box(i18n.t("boot.missing_module", title=entry["title"], modules=_module_named_in(exc)))
        else:
            msapi.message_box(i18n.t("boot.load_failed", tool=tool_id, error=repr(exc)))
        return False

    try:
        module.install()
    except Exception as exc:
        journal.write("failed to start {}: {!r}".format(tool_id, exc))
        if _module_named_in(exc):
            msapi.message_box(i18n.t("boot.missing_module", title=entry["title"], modules=_module_named_in(exc)))
        else:
            msapi.message_box(i18n.t("boot.start_failed", tool=tool_id, error=repr(exc)))
        return False

    if entry.get("reopen", False):
        setattr(builtins, _LAST_TOOL_FLAG, tool_id)
    return True


def forget_tool(tool_id):
    """Closes a tool's windows and forgets its modules: used when the tool is removed while Metashape runs."""
    prefix = "aerotools.tools.{}".format(tool_id)
    closer = getattr(sys.modules.get(prefix + ".tool"), "uninstall", None)
    if callable(closer):
        try:
            closer()
        except Exception:
            pass
    for name in [n for n in sys.modules if n == prefix or n.startswith(prefix + ".")]:
        del sys.modules[name]


def purge_modules():
    """Forgets every aerotools module to re-read the code from disk.

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
    release_files()
    for name in [n for n in sys.modules if n.startswith("aerotools")]:
        del sys.modules[name]


def release_files():
    """Let go of the files the core keeps open (the read-only proj.db connection of projtool): on Windows an update or an uninstall
    could not replace them otherwise. Called before the files change (updates, plugin install, uninstall) and before a purge."""
    closer = getattr(sys.modules.get("aerotools.projtool"), "close_db", None)
    if callable(closer):
        try:
            closer()
        except Exception:
            pass


def reload_all():
    """Re-reads the code from disk and reopens the last panel used (not the one-off windows)."""
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


def forget_last(tool_id):
    """A panel closed by the user is not reopened by "Reload AeroTools"."""
    if getattr(builtins, _LAST_TOOL_FLAG, None) == tool_id:
        setattr(builtins, _LAST_TOOL_FLAG, None)


def reopens(tool_id):
    """True if "Reload AeroTools" must reopen this tool (a panel, not a one-off window).

    The test is redone here, at reload time: a flag left by an old version reopens nothing."""
    from . import config
    entry = config.tool(tool_id)
    return bool(entry and entry.get("reopen", False))


# --------------------------------------------------------------------------
# Install plugins
# --------------------------------------------------------------------------

def install_plugins():
    """Aero > Install plugins menu: the window that lists the tools and installs or removes them."""
    ensure_on_path()
    from . import i18n, journal, msapi
    try:
        from . import plugins_dialog
        plugins_dialog.show(msapi.main_window())
    except Exception as exc:
        journal.write("plugins window failed: {!r}".format(exc))
        msapi.message_box(i18n.t("boot.plugins_failed", error=repr(exc)))


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
# Uninstall
# --------------------------------------------------------------------------

def open_uninstall():
    """Aero > Uninstall Aero menu: opens the window that removes the launcher (and, if ticked, the settings and the AI environment)."""
    ensure_on_path()
    from . import i18n, journal, msapi
    try:
        from . import uninstall_dialog
        uninstall_dialog.show(msapi.main_window())
    except Exception as exc:
        journal.write("uninstall window failed: {!r}".format(exc))
        msapi.message_box(i18n.t("boot.uninstall_failed", error=repr(exc)))


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
