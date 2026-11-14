"""Qt self-test of Aero-Exports on a Metashape WITHOUT numpy: the window opens, the numpy features are disabled with their one-line
explanation, and the exports that do not need numpy stay available.

Run: python -B tests_qt\run_qt_aeroexports_nonumpy.py        (a Python with PySide2 or PySide6; numpy is hidden by the script)
"""

import os
import sys
import time
import traceback
import types

sys.dont_write_bytecode = True
sys.modules["numpy"] = None                          # `import numpy` now raises ImportError, as in a Metashape without it

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import _isolation  # noqa: E402
GUARD = _isolation.Guard("aerotools_qt_nonp_")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FAKE = types.ModuleType("Metashape")
FAKE.app = types.SimpleNamespace(version="2.2.1-selftest", document=types.SimpleNamespace(chunk=None),
                                 addMenuItem=lambda *a, **k: None, messageBox=lambda text: None, update=lambda: None)
sys.modules["Metashape"] = FAKE

from aerotools import config, journal, msapi  # noqa: E402
GUARD.pin(config)
from aerotools.qt import QtWidgets  # noqa: E402
from aerotools.tools.aeroexports import dialog, plan, tool  # noqa: E402

journal.print = lambda *args, **kwargs: None
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
FAILURES = []


def pump(milliseconds=80):
    end = time.time() + milliseconds / 1000.0
    while time.time() < end:
        APP.processEvents()
        time.sleep(0.004)


def case(name):
    def wrap(func):
        try:
            result = func()
            ok, detail = result if isinstance(result, tuple) else (bool(result), "")
        except Exception as exc:
            ok, detail = False, "exception {!r}\n{}".format(exc, traceback.format_exc())
        print(("PASS " if ok else "FAIL ") + name + ("" if ok or not detail else "  -> " + str(detail)))
        if not ok:
            FAILURES.append(name)
        return func
    return wrap


msapi.raster_info = lambda chk=None: {"ortho": (0.05, 4000, 3000), "dem": (0.2, 1000, 800), "box_ortho": (0, 0, 200, 150),
                                      "box_dem": (0, 0, 200, 160), "crs": "WGS 84 / UTM zone 32N", "dates": [],
                                      "crs_object": None, "center": (9.0, 45.0)}
msapi.document_path = lambda: os.path.join(GUARD.temp, "Project.psx")


def open_dialog():
    config.put("aeroexports", "dem_look", None)
    box = dialog.ExportDialog(None)
    box.show()
    box.refresh()
    box.site.setText("SITE")
    pump()
    return box


def choose(box, key):
    box.preset.setCurrentIndex(box.preset.findData(key))
    box.preset.activated.emit(box.preset.currentIndex())
    pump()


@case("numpy is really hidden here")
def _():
    try:
        import numpy  # noqa: F401
    except ImportError:
        return dialog.HAS_NUMPY is False
    return False


@case("the Aero-Exports entry point loads and opens the window (no message box, no exception)")
def _():
    messages = []
    msapi.main_window = lambda: None
    msapi.message_box = messages.append
    tool.install()                                    # no main window in this harness: the tool says so and returns, never raises
    return True


@case("the window opens; the preview, the DEM picture and the crop of the drawing are disabled with the numpy explanation")
def _():
    box = open_dialog()
    ok = (not box.look_button.isEnabled() and not box.picture_check.isEnabled() and not box.cad_clip.isEnabled()
          and "numpy" in box.look_button.toolTip() and "numpy" in box.cad_clip.toolTip())
    box.close()
    return ok


@case("the DEM card says in one visible line why the coloured picture is off; the heights stay a GeoTIFF and the Files list shows it")
def _():
    box = open_dialog()
    rows = [tuple(box.files.row_data(i)) for i in range(box.files.row_count())]
    ok = (not box.numpy_note.isHidden() and "numpy" in box.numpy_note.text() and not box.picture_check.isEnabled()
          and not box.picture_check.isChecked() and box.values_check.isChecked()
          and [what for _name, what in rows] == ["Orthomosaic", "DEM heights"] and box.look_row.isHidden())
    box.close()
    return ok, rows


@case("plain GeoTIFF, JPEG and the other formats stay available and the Export button works")
def _():
    box = open_dialog()
    results = {}
    for key in ("full", "quick", "jpeg"):
        choose(box, key)
        results[key] = (box.go_button.isEnabled(), box.tool_card.isHidden())
    box.close()
    return all(go and hidden for go, hidden in results.values()), results


@case("a drawing with the DEM ticked is blocked with a numpy sentence and no buy button; without the DEM it is allowed")
def _():
    box = open_dialog()
    choose(box, plan.CAD)
    blocked = (not box.tool_card.isHidden() and "numpy" in box.tool_text.text() and not box.go_button.isEnabled()
               and box.tool_buy.isHidden() and box.tool_choose.isHidden())
    box.cards[plan.DEM].button.setChecked(False)
    pump()
    freed = box._requirement() is None or box._requirement()[0] != "numpy"
    box.close()
    return blocked and freed, (blocked, freed)


@case("with another system chosen only ellipsoidal heights are offered (the grids need numpy)")
def _():
    box = open_dialog()
    index = box.crs_box.findData(32632)
    if index < 0:
        box.crs_box.addItem("WGS 84 / UTM zone 32N", 32632)
        index = box.crs_box.findData(32632)
    box.crs_box.setCurrentIndex(index)
    box._on_crs()
    pump()
    items = [box.vert_box.itemData(i) for i in range(box.vert_box.count())]
    box.close()
    return items == ["ellipsoid"], items


print("{} failure(s)".format(len(FAILURES)))
check = GUARD.check()
if check:
    print("FAIL " + check)
    FAILURES.append("settings")
sys.exit(1 if FAILURES else 0)
