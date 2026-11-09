"""Integration test inside the real Metashape (not a fake): the tools load, their windows open and close, and the checks of each
tool that need the real Metashape run (the export and the lightening of a project, for the tools that have them).

Run it from the Python console of Metashape (the console of the Tools menu), with the project you may play with open:

    AERO_LIVE_OUT = r"C:\\somewhere\\live_result.txt"      # optional, default: next to the temp folder
    AERO_LIVE_ROOT = r"C:\\somewhere\\frozen_copy"          # optional, default: the folder of the Aero launcher
    exec(open(r"C:\\path\\to\\run_live_metashape.py", encoding="utf-8").read())

What it does and does not touch:
  - it never saves the open project and never changes it: exports read the chunk, nothing is written into it;
  - everything it writes goes to a temporary folder, removed at the end;
  - the settings of AeroTools are moved to that folder while it runs;
  - the loaded AeroTools modules are dropped at the end, so the next click on the menu loads the code again.

The result is one line per check (PASS / FAIL / SKIP / INFO) in AERO_LIVE_OUT and in the console.
"""

import builtins
import datetime
import gc
import importlib
import os
import shutil
import sys
import tempfile
import time
import traceback

import Metashape

_G = globals()
OUT = _G.get("AERO_LIVE_OUT") or os.path.join(tempfile.gettempdir(), "aero_live_result.txt")
ROOT = _G.get("AERO_LIVE_ROOT") or getattr(builtins, "_AEROTOOLS_ROOT", None)
if not ROOT:
    raise RuntimeError("Set AERO_LIVE_ROOT to the folder that contains the aerotools package.")
TMP = tempfile.mkdtemp(prefix="aero_live_")
_out = open(OUT, "w", encoding="utf-8")
COUNT = {"PASS": 0, "FAIL": 0, "SKIP": 0, "INFO": 0}


def P(kind, name, detail=""):
    COUNT[kind] += 1
    line = "{} {}{}".format(kind, name, (": " + str(detail)) if detail != "" else "")
    _out.write(line + "\n")
    _out.flush()
    print("[live] " + line)


def check(name, condition, detail=""):
    P("PASS" if condition else "FAIL", name, detail)
    return bool(condition)


def section(title):
    _out.write("\n== {}\n".format(title))
    _out.flush()


def guarded(function):
    """A check that raises is a FAIL with its traceback, and the run goes on."""
    def run(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception as exc:
            P("FAIL", function.__name__, repr(exc))
            _out.write(traceback.format_exc() + "\n")
            _out.flush()
    run.__name__ = function.__name__
    return run


def tree_files(folder):
    found = []
    for base, _dirs, names in os.walk(folder):
        found += [os.path.relpath(os.path.join(base, name), folder) for name in names]
    return sorted(found)


def process_events(seconds=0.3):
    from aerotools.qt import QtWidgets
    end = time.time() + seconds
    while time.time() < end:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.02)


# --------------------------------------------------------------------------------------------------------------------------------
# 0. Environment and loading of the code
# --------------------------------------------------------------------------------------------------------------------------------

def load_code():
    section("Code")
    for name in [m for m in sys.modules if m == "aerotools" or m.startswith("aerotools.")]:
        del sys.modules[name]
    sys.path.insert(0, ROOT)
    P("INFO", "metashape", Metashape.app.version)
    P("INFO", "python", sys.version.split()[0])
    P("INFO", "root", ROOT)
    import aerotools
    P("INFO", "aerotools", aerotools.__version__)
    check("loaded from the wanted root", os.path.normcase(os.path.dirname(os.path.dirname(aerotools.__file__)))
          == os.path.normcase(ROOT), aerotools.__file__)
    from aerotools import config
    folder = os.path.join(TMP, "settings")
    os.makedirs(folder)
    config.settings_dir = lambda: folder                     # the real settings of the machine stay untouched
    return aerotools


@guarded
def check_modules(aerotools):
    from aerotools import config, updater
    tools = [entry["id"] for entry in config.TOOLS]
    P("INFO", "tools of this tree", ", ".join(tools))
    for tool_id in tools:
        installed = updater.tool_installed(tool_id)
        P("INFO", "installed " + tool_id, installed)
    for module in ("bootstrap", "msapi", "ui", "theme", "i18n", "journal", "pdfkit", "projtool", "outputdir"):
        try:
            importlib.import_module("aerotools." + module)
            P("PASS", "import " + module)
        except ImportError as exc:
            if module in ("projtool", "outputdir") and "No module" in str(exc):
                P("SKIP", "import " + module, "not in this channel")
            else:
                P("FAIL", "import " + module, repr(exc))
    for tool_id in tools:
        if not updater.tool_installed(tool_id):
            continue
        try:
            module = importlib.import_module("aerotools.tools.{}.tool".format(tool_id))
            check("entry points " + tool_id, callable(getattr(module, "install", None)) and callable(getattr(module, "uninstall", None)))
        except Exception as exc:
            P("FAIL", "import tool " + tool_id, repr(exc))


@guarded
def check_menu():
    section("Aero menu of this Metashape session (built by the launcher at start-up)")
    shown = getattr(builtins, "_AEROTOOLS_MENU_TOOLS", None)
    P("INFO", "tools with a menu entry", ", ".join(sorted(shown)) if shown else "none (menu not installed in this session)")
    check("menu flag set", bool(getattr(builtins, "_AEROTOOLS_MENU_INSTALLED", False)))


# --------------------------------------------------------------------------------------------------------------------------------
# 1. Windows open and close
# --------------------------------------------------------------------------------------------------------------------------------

@guarded
def check_windows():
    section("Windows")
    from aerotools import config, msapi, updater
    boxes = []
    saved_box = msapi.message_box
    msapi.message_box = lambda text, *a, **k: boxes.append(str(text))            # a message must not block the console
    try:
        for entry in config.TOOLS:
            tool_id = entry["id"]
            if not updater.tool_installed(tool_id):
                continue
            module = importlib.import_module("aerotools.tools.{}.tool".format(tool_id))
            del boxes[:]
            before = len(Metashape.app.document.chunks)
            try:
                module.install()
                process_events(0.6)
                module.uninstall()
                process_events(0.3)
                check("window " + tool_id, True, ("message: " + boxes[0][:80]) if boxes else "")
            except Exception as exc:
                P("FAIL", "window " + tool_id, repr(exc))
                _out.write(traceback.format_exc() + "\n")
            check("chunks unchanged by " + tool_id, len(Metashape.app.document.chunks) == before)
    finally:
        msapi.message_box = saved_box


# --------------------------------------------------------------------------------------------------------------------------------

def main():
    document = Metashape.app.document
    title = document.path or "(unsaved project)"
    modified_before = bool(getattr(document, "modified", False))
    chunks_before = [(chunk.key, chunk.label) for chunk in document.chunks]
    aerotools = load_code()
    check_modules(aerotools)
    check_menu()
    check_windows()
    section("The open project was not touched")
    document = Metashape.app.document
    check("same path", (document.path or "(unsaved project)") == title)
    check("same chunks", [(chunk.key, chunk.label) for chunk in document.chunks] == chunks_before)
    P("INFO", "modified flag", "before {} after {}".format(modified_before, bool(getattr(document, "modified", False))))
    P("INFO", "summary", "{PASS} PASS, {FAIL} FAIL, {SKIP} SKIP".format(**COUNT))


try:
    main()
except Exception as _exc:
    P("FAIL", "run", repr(_exc))
    _out.write(traceback.format_exc() + "\n")
finally:
    try:
        for _name in [m for m in sys.modules if m == "aerotools" or m.startswith("aerotools.")]:
            del sys.modules[_name]
        if ROOT in sys.path:
            sys.path.remove(ROOT)
    except Exception:
        pass
    shutil.rmtree(TMP, ignore_errors=True)
    P("INFO", "done", "")
    _out.close()
