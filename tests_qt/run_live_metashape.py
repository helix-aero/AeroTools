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

def check_skin_switch(tool_id):
    """The UI kit windows follow a live skin change: every open kit root gets the style sheet of the new skin (nothing is saved)."""
    from aerotools.qt import QtWidgets
    from aerotools.uikit import style, tokens
    first = tokens.active_skin()
    try:
        for skin in ("light", "medium", first):
            style.switch_skin(skin, save=False)
            process_events(0.1)
            sheet = style.stylesheet()
            roots = [w for w in QtWidgets.QApplication.instance().allWidgets() if w.objectName() == "aeroPanel"]
            stale = [type(w).__name__ for w in roots if w.styleSheet() != sheet]
            check("skin {} restyles the windows of {}".format(skin, tool_id), not stale, stale)
    finally:
        style.switch_skin(first, save=False)


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
                check_skin_switch(tool_id)
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
# 2. Aero-Exports on the chunk of the open project
# --------------------------------------------------------------------------------------------------------------------------------

def exportable_chunk():
    from aerotools import msapi
    best = None
    for chunk in Metashape.app.document.chunks:
        try:
            info = msapi.raster_info(chunk)
        except Exception:
            continue
        if info.get("ortho") and info.get("dem"):
            return chunk, info
        if best is None and (info.get("ortho") or info.get("dem")):
            best = (chunk, info)
    return best if best else (None, None)


@guarded
def check_exports():
    section("Aero-Exports")
    from aerotools import config, msapi, updater
    if not updater.tool_installed("aeroexports"):
        P("SKIP", "aeroexports", "not in this tree")
        return
    from aerotools import projtool
    from aerotools.tools.aeroexports import crs as crs_module, external, pipeline, plan
    chunk, info = exportable_chunk()
    if chunk is None:
        P("SKIP", "aeroexports", "no chunk with an orthomosaic or a DEM in the open project")
        return
    P("INFO", "chunk", "{} (ortho {}, dem {})".format(chunk.label, info.get("ortho"), info.get("dem")))
    wanted = {kind: 0.5 for kind in plan.KINDS if info.get(kind)}
    tools = {"gm": external.find_global_mapper(""), "acad": external.find_autocad(""), "gdal": external.find_gdal("")}
    P("INFO", "external programs", ", ".join("{}={}".format(k, bool(v)) for k, v in sorted(tools.items())))
    day = datetime.date(2026, 11, 9)
    target = msapi.target_crs(3942)                          # RGF93 v1 / CC42: the project is in degrees, the window asks for a projected system
    check("projected target system available", target is not None)
    heights = "ellipsoidal heights (the height grids are downloaded by the window, not tested here)"
    P("INFO", "export in", "EPSG:3942, " + heights)

    def context(folder, preset, export=None):
        return pipeline.Context(chunk, info, folder, "LIVE", day, preset, export or msapi.export_raster, tools=dict(tools),
                                crs=target, tile_m=None, cad={}, snap=msapi.snapped_box, transform=msapi.transform_box,
                                vertical=None, proj=projtool.Proj(geoid_dir=crs_module.own_dir()), epsg=3942, clip_boundary=True)

    folder = os.path.join(TMP, "Outputs")
    for preset in ("quick", "jpeg"):
        out = os.path.join(folder, preset)
        jobs = plan.build_jobs("LIVE", day, wanted, preset, out)
        started = time.time()
        try:
            result = pipeline.run(context(out, preset), jobs, lambda fraction, text: process_events(0.0))
        except Exception as exc:
            P("FAIL", "export " + preset, repr(exc))
            _out.write(traceback.format_exc() + "\n")
            continue
        names = tree_files(out)
        P("INFO", "export {} files".format(preset), "{} in {:.0f} s".format(", ".join(names), time.time() - started))
        check("export {}: one file per job".format(preset), all(os.path.isfile(job.path) and os.path.getsize(job.path) > 0 for job in jobs))
        check("export {}: no work folder left".format(preset), not any(".aeroexports_tmp" in name for name in names))
    if tools["gdal"] or tools["gm"]:
        out = os.path.join(folder, "jp2")
        jobs = plan.build_jobs("LIVE", day, {k: v for k, v in wanted.items() if k == "ortho"} or wanted, "jp2", out)
        try:
            pipeline.run(context(out, "jp2"), jobs, lambda fraction, text: process_events(0.0))
            check("export jp2", all(os.path.isfile(job.path) and os.path.getsize(job.path) > 0 for job in jobs), ", ".join(tree_files(out)))
        except Exception as exc:
            P("FAIL", "export jp2", repr(exc))
    else:
        P("SKIP", "export jp2", "no GDAL or Global Mapper")
    # A failing export must leave the earlier delivery as it was.
    out = os.path.join(folder, "quick")
    jobs = plan.build_jobs("LIVE", day, wanted, "quick", out, version=1)
    before = {name: os.path.getsize(os.path.join(out, name)) for name in tree_files(out)}

    def failing(*_args, **_kwargs):
        raise RuntimeError("simulated failure of the export")
    try:
        pipeline.run(context(out, "quick", export=failing), jobs, lambda fraction, text: None)
        P("FAIL", "failing export", "no error raised")
    except Exception:
        after = {name: os.path.getsize(os.path.join(out, name)) for name in tree_files(out)}
        check("failing export leaves the earlier files untouched", after == before, "{} -> {}".format(sorted(before), sorted(after)))


# --------------------------------------------------------------------------------------------------------------------------------
# 3. Aero-Space on a throw-away project
# --------------------------------------------------------------------------------------------------------------------------------

@guarded
def check_space():
    section("Aero-Space")
    from aerotools import updater
    if not updater.tool_installed("fastspace"):
        P("SKIP", "aerospace", "not in this tree")
        return
    from aerotools.tools.fastspace import actions, scanner
    folder = os.path.join(TMP, "space")
    os.makedirs(folder)
    path = os.path.join(folder, "SPACE_TEST.psx")
    document = Metashape.Document()
    document.save(path)
    chunk = document.addChunk()
    chunk.label = "test"
    document.save()
    files_dir = os.path.join(folder, "SPACE_TEST.files")
    leftover = os.path.join(files_dir, "0", "0", "depth_maps")
    os.makedirs(leftover)
    with open(os.path.join(leftover, "depth.bin"), "wb") as handle:
        handle.write(b"0" * 200000)
    document = None
    gc.collect()
    project = scanner.measure_project(path)
    P("INFO", "scan", "state lock {}, removable {} bytes".format(project.lock, project.removable(scanner.DEFAULT_ITEMS)))
    check("scan finds the project", project is not None and not project.orphan)

    # An open project is refused: Metashape holds the lock while the document is open.
    opened = Metashape.Document()
    opened.open(path)
    state = scanner.lock_state(files_dir)
    check("lock seen while the project is open", state in (scanner.OPEN,), state)
    refused = actions.lighten(scanner.measure_project(path), actions.PERMANENT)
    check("lighten refuses an open project", not refused["ok"] and bool(refused["error"]), refused["error"][:100])
    gone = actions.delete_project(scanner.measure_project(path), actions.PERMANENT)
    check("delete refuses an open project", not gone["ok"] and os.path.isfile(path), gone["error"][:100])
    opened = None
    gc.collect()
    time.sleep(0.5)

    state = scanner.lock_state(files_dir)
    check("lock released after closing", state != scanner.OPEN, state)
    project = scanner.measure_project(path)
    result = actions.lighten(project, actions.PERMANENT, progress=lambda text: process_events(0.0))
    P("INFO", "lighten", "ok {} freed {} backup {} error {}".format(result["ok"], result["freed"], result["backup"], result["error"][:120]))
    check("lighten ok", result["ok"], result["error"])
    check("leftover folder removed", not os.path.exists(leftover))
    backup = result.get("backup") or ""
    # Only a lightening that goes through the Metashape API (and saves) makes a safety copy; leftover folders alone are swept
    # without opening the project for writing, so no copy is made (the project is not touched).
    check("safety copy written when the API saved, none when only leftovers were swept",
          (not backup) or (os.path.isfile(os.path.join(backup, "RESTORE.txt"))
                           and os.path.isfile(os.path.join(backup, os.path.basename(path) + ".bak"))), backup or "(no copy needed)")
    reopened = Metashape.Document()
    reopened.open(path, read_only=True)
    check("chunk kept by the lightening", len(reopened.chunks) == 1 and reopened.chunks[0].label == "test")
    reopened = None
    gc.collect()


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
    check_exports()
    check_space()
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
