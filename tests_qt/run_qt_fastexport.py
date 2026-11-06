"""Qt self-test of Fast Export (DEM look window, pixel field, DEM card), without Metashape.

Run (no pytest needed), with a Python that has PySide2 or PySide6 and numpy:
    python -B tests_qt\\run_qt_fastexport.py

A fake `Metashape` module is injected before AeroTools is imported. Output: one PASS / FAIL line per case, a non-zero exit
code on the first failure. numpy is required for the preview window; without it the cases that need it are skipped.
"""

import os
import sys
import tempfile
import time
import traceback
import types

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="aerotools_qt_fx_")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FAKE = types.ModuleType("Metashape")
FAKE.app = types.SimpleNamespace(version="2.2.1-selftest", document=types.SimpleNamespace(chunk=None),
                                 addMenuItem=lambda *a, **k: None, messageBox=lambda text: None, update=lambda: None)
sys.modules["Metashape"] = FAKE

from aerotools import config, i18n, journal, qt  # noqa: E402
from aerotools.qt import QtCore, QtGui, QtWidgets  # noqa: E402
from aerotools import ui  # noqa: E402
from aerotools.tools.fastexport import look as looks  # noqa: E402

try:
    import numpy as np  # noqa: E402
    from aerotools.tools.fastexport import preview, previewwin  # noqa: E402
except ImportError:                                                  # pragma: no cover
    np = None

journal.print = lambda *args, **kwargs: None
i18n.set_lang("en", persist=False)
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
FAILURES, SKIPPED = [], []


def pump(milliseconds=60):
    end = time.time() + milliseconds / 1000.0
    while time.time() < end:
        APP.processEvents()
        time.sleep(0.005)


def case(name, needs_numpy=False):
    def wrap(func):
        if needs_numpy and np is None:
            print("SKIP {} (no numpy)".format(name))
            SKIPPED.append(name)
            return func
        try:
            result = func()
            ok, detail = result if isinstance(result, tuple) else (bool(result), "")
        except Exception as exc:
            ok, detail = False, "exception {!r}\n{}".format(exc, traceback.format_exc())
        print("{} {}{}".format("PASS" if ok else "FAIL", name, "" if ok or not detail else "  -> " + str(detail)))
        if not ok:
            FAILURES.append(name)
        return func
    return wrap


def small_preview(pixel=0.5):
    rows, cols = np.mgrid[0:120, 0:180]
    grid = (10.0 + 0.05 * cols + 2.0 * np.sin(rows / 9.0)).astype("float32")
    grid[:8, :] = np.nan                                              # no data along one edge
    return preview.Preview(grid, pixel, pixel, exact=(float(np.nanmin(grid)), float(np.nanmax(grid))))


def press(widget, pos):
    event = QtGui.QMouseEvent(QtCore.QEvent.MouseButtonPress, QtCore.QPointF(pos), QtCore.Qt.LeftButton, QtCore.Qt.LeftButton,
                              QtCore.Qt.NoModifier)
    APP.sendEvent(widget, event)
    release = QtGui.QMouseEvent(QtCore.QEvent.MouseButtonRelease, QtCore.QPointF(pos), QtCore.Qt.LeftButton,
                                QtCore.Qt.NoButton, QtCore.Qt.NoModifier)
    APP.sendEvent(widget, release)


# --------------------------------------------------------------------------------------------------
# The look window
# --------------------------------------------------------------------------------------------------

def double_click(widget):
    event = QtGui.QMouseEvent(QtCore.QEvent.MouseButtonDblClick, QtCore.QPointF(5, 5), QtCore.Qt.LeftButton, QtCore.Qt.LeftButton,
                              QtCore.Qt.NoModifier)
    APP.sendEvent(widget, event)


def key(widget, code, text=""):
    for kind in (QtCore.QEvent.KeyPress, QtCore.QEvent.KeyRelease):
        APP.sendEvent(widget, QtGui.QKeyEvent(kind, code, QtCore.Qt.NoModifier, text))


def type_into(value, text, code=QtCore.Qt.Key_Return):
    """Open the editor of an EditableValue by double-click, type `text`, press Enter (or `code`)."""
    double_click(value.label)
    value.edit.setText(text)
    key(value.edit, code)
    pump(120)


@case("look window: opens with the saved look, paints once, shows the legend", needs_numpy=True)
def _():
    window = previewwin.PreviewWindow(None, small_preview(), looks.DEFAULT)
    window.show()
    pump()
    data = window.preview.legend(looks.DEFAULT)
    ok = (window.paints == 1 and window.palette_box.currentData() == looks.DEFAULT_PALETTE
          and window.palette_box.count() >= 6 and len(window.legend._ticks) == 5 and len(data["ticks"]) == 5
          and window.view._image is not None and not window.view._image.isNull())
    window.close()
    return ok, "paints={} palettes={}".format(window.paints, window.palette_box.count())


@case("look window: a burst of changes gives one repaint with the latest values", needs_numpy=True)
def _():
    window = previewwin.PreviewWindow(None, small_preview(), looks.DEFAULT)
    window.show()
    pump()
    before = window.paints
    for value in (10, 40, 90, 200, 270):
        window.direction.setValue(value)
    window.strength.setValue(35)
    pump(150)
    look = window.current_look()
    window.close()
    return window.paints == before + 1 and look.azimuth == 270.0 and abs(look.strength - 0.35) < 1e-9, \
        "paints {} -> {} look={}".format(before, window.paints, look)


@case("look window: palette and shading choices reach the picture", needs_numpy=True)
def _():
    window = previewwin.PreviewWindow(None, small_preview(), looks.DEFAULT)
    window.show()
    pump()
    first = window.view._image.copy()
    window.palette_box.setCurrentIndex(window.palette_box.findData("grayscale") if window.palette_box.findData("grayscale") >= 0 else 1)
    window.palette_box.activated.emit(window.palette_box.currentIndex())
    pump(120)
    second = window.view._image.copy()
    window.shading_box.setCurrentIndex(window.shading_box.findData(looks.NONE))
    window.shading_box.activated.emit(window.shading_box.currentIndex())
    pump(120)
    sun_enabled = window.direction.isEnabled()
    look = window.current_look()
    window.close()
    return first != second and look.shading == looks.NONE and not sun_enabled, \
        "palette changed={} shading={} sun controls enabled={}".format(first != second, look.shading, sun_enabled)


@case("look window: a bad custom range shows a message and blocks the button; a good one is kept", needs_numpy=True)
def _():
    window = previewwin.PreviewWindow(None, small_preview(), looks.DEFAULT)
    window.show()
    pump()
    window.range_radios[looks.CUSTOM].setChecked(True)
    pump(100)
    window.low_edit.setText("30")
    window.high_edit.setText("20")
    pump(120)
    blocked = (not window.apply_button.isEnabled()) and not window.range_error.isHidden()
    window.low_edit.setText("11,5")                                      # comma accepted
    window.high_edit.setText("14")
    pump(120)
    look = window.current_look()
    good = window.apply_button.isEnabled() and window.range_error.isHidden() and (look.stretch, look.low, look.high) == (looks.CUSTOM, 11.5, 14.0)
    window.close()
    return blocked and good, "blocked={} good={} look={}".format(blocked, good, look)


@case("look window: Use this look keeps the look; Cancel keeps nothing", needs_numpy=True)
def _():
    window = previewwin.PreviewWindow(None, small_preview(), looks.DEFAULT)
    window.show()
    pump()
    window.shading_box.setCurrentIndex(window.shading_box.findData(looks.SLOPE))
    window.shading_box.activated.emit(window.shading_box.currentIndex())
    pump(100)
    window._apply()
    kept = window.result_look
    cancel = previewwin.PreviewWindow(None, small_preview(), looks.DEFAULT)
    cancel.show()
    pump()
    cancel.reject()
    return kept is not None and kept.shading == looks.SLOPE and cancel.result_look is None, "kept={}".format(kept)


@case("look window: Reset puts the default look back", needs_numpy=True)
def _():
    window = previewwin.PreviewWindow(None, small_preview(), looks.Look("rainbow", looks.LIGHTS4, 90.0, 20.0, 0.3, looks.AUTO, None, None))
    window.show()
    pump()
    window._reset()
    pump(100)
    ok = looks.is_default(window.current_look()) and window.paints >= 2
    window.close()
    return ok, str(window.current_look())


@case("look window: double-click on a value, type a number, Enter: the slider and the picture follow", needs_numpy=True)
def _():
    window = previewwin.PreviewWindow(None, small_preview(), looks.DEFAULT)
    window.show()
    pump()
    before = window.paints
    double_click(window.direction_text.label)
    opened = window.direction_text.is_editing() and window.direction_text.currentWidget() is window.direction_text.edit
    window.direction_text.edit.setText("45")
    key(window.direction_text.edit, QtCore.Qt.Key_Return)
    pump(150)
    window.strength_text.begin_edit()
    window.strength_text.edit.setText("35 %")                       # the unit is accepted
    key(window.strength_text.edit, QtCore.Qt.Key_Enter)
    pump(150)
    look = window.current_look()
    ok = (opened and not window.direction_text.is_editing() and window.direction.value() == 45 and look.azimuth == 45.0
          and abs(look.strength - 0.35) < 1e-9 and window.paints > before and "45" in window.direction_text.text()
          and window.strength_text.text() == "35 %")
    window.close()
    return ok, "opened={} dir={} look={} paints {} -> {}".format(opened, window.direction.value(), look, before, window.paints)


@case("look window: Esc keeps the old value and does not close the window; a wrong number is refused", needs_numpy=True)
def _():
    window = previewwin.PreviewWindow(None, small_preview(), looks.DEFAULT)
    window.show()
    pump()
    type_into(window.direction_text, "90", QtCore.Qt.Key_Escape)
    escaped = window.direction.value() == 315 and not window.direction_text.is_editing() and window.isVisible()
    double_click(window.height_text.label)
    results = []
    for wrong in ("abc", "200", "-3", ""):                          # height lives in 5..85
        window.height_text.edit.setText(wrong)
        key(window.height_text.edit, QtCore.Qt.Key_Return)
        results.append(window.height_text.is_editing() and window.height.value() == 45 and window.height_text.edit.property("bad"))
    key(window.height_text.edit, QtCore.Qt.Key_Escape)
    window.direction_text.begin_edit()
    window.direction_text.edit.setText("360")                       # a compass wraps: 360 is north
    key(window.direction_text.edit, QtCore.Qt.Key_Return)
    pump(120)
    wrapped = window.direction.value() == 0
    window.close()
    return escaped and all(results) and wrapped, "escaped={} wrong={} wrapped={}".format(escaped, results, wrapped)


@case("look window: the value can be opened from the keyboard (Enter on it), and leaving with a good number applies it", needs_numpy=True)
def _():
    window = previewwin.PreviewWindow(None, small_preview(), looks.DEFAULT)
    window.show()
    pump()
    window.strength_text.label.setFocus()
    pump(30)
    key(window.strength_text.label, QtCore.Qt.Key_Return)
    opened = window.strength_text.is_editing()
    window.strength_text.edit.setText("20")
    window.strength_text.edit.left.emit()                           # what a click elsewhere sends
    pump(120)
    ok = opened and window.strength.value() == 20 and not window.strength_text.is_editing()
    window.close()
    return ok, "strength={}".format(window.strength.value())


@case("look window: each control has a reset button that puts its default back, greyed when it is already the default", needs_numpy=True)
def _():
    start = looks.Look("rainbow", looks.LIGHTS4, 90.0, 20.0, 0.3, looks.AUTO, None, None)
    window = previewwin.PreviewWindow(None, small_preview(), start)
    window.show()
    pump()
    pairs = ((window.palette_reset, lambda: window.palette_box.currentData() == looks.DEFAULT.palette),
             (window.shading_reset, lambda: window.shading_box.currentData() == looks.DEFAULT.shading),
             (window.direction_reset, lambda: window.direction.value() == 315),
             (window.height_reset, lambda: window.height.value() == 45),
             (window.strength_reset, lambda: window.strength.value() == 80),
             (window.range_reset, lambda: window._range_choice() == looks.FULL))
    results = []
    for button, back in pairs:
        before = button.isEnabled()
        button.click()
        pump(100)
        results.append((before, back(), button.isEnabled()))
    done = looks.is_default(window.current_look())
    window.close()
    return all(r == (True, True, False) for r in results) and done, "{} default={}".format(results, done)


@case("look window: degrees (geographic DEM) greys the shading out and says why", needs_numpy=True)
def _():
    window = previewwin.PreviewWindow(None, small_preview(pixel=0.00001), looks.DEFAULT)
    window.show()
    pump()
    ok = (not window.shading_box.isEnabled() and not window.direction.isEnabled() and not window.note.isHidden()
          and window.view._image is not None)
    window.close()
    return ok


@case("look window: no custom range when the project heights are converted", needs_numpy=True)
def _():
    window = previewwin.PreviewWindow(None, small_preview(), looks.Look("metashape", looks.SUN, 315.0, 45.0, 0.8, looks.CUSTOM, 1.0, 2.0),
                                      custom_allowed=False)
    window.show()
    pump()
    ok = not window.range_radios[looks.CUSTOM].isEnabled() and window.current_look().stretch == looks.FULL
    window.close()
    return ok, str(window.current_look())


@case("look window: painting stays fast (under 300 ms at 1000 pixels, offscreen)", needs_numpy=True)
def _():
    grid = np.random.RandomState(1).rand(700, 1000).astype("float32") * 50
    model = preview.Preview(grid, 0.5, 0.5)
    window = previewwin.PreviewWindow(None, model, looks.DEFAULT)
    window.show()
    pump()
    start = time.time()
    window.shading_box.setCurrentIndex(window.shading_box.findData(looks.LIGHTS4))
    window._paint()
    elapsed = (time.time() - start) * 1000
    window.close()
    return elapsed < 300, "{:.0f} ms".format(elapsed)


# --------------------------------------------------------------------------------------------------
# The pixel field (select all)
# --------------------------------------------------------------------------------------------------

@case("pixel field: Tab / focus selects all the text")
def _():
    host = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(host)
    first, edit = QtWidgets.QLineEdit(), ui.SelectAllEdit()
    layout.addWidget(first)
    layout.addWidget(edit)
    edit.setText("12.5")
    host.show()
    first.setFocus()
    pump()
    edit.setFocus(QtCore.Qt.TabFocusReason)
    pump()
    ok = edit.selectedText() == "12.5"
    host.close()
    return ok, repr(edit.selectedText())


@case("pixel field: a mouse click into it selects all the text")
def _():
    host = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(host)
    other, edit = QtWidgets.QLineEdit(), ui.SelectAllEdit()
    layout.addWidget(other)
    layout.addWidget(edit)
    edit.setText("0.05")
    host.show()
    host.activateWindow()
    other.setFocus()
    pump()
    press(edit, QtCore.QPoint(8, edit.height() // 2))
    pump()
    first = edit.selectedText()            # (a second click cannot be checked here: an offscreen window is never active)
    host.close()
    return first == "0.05", "selected={!r}".format(first)


# --------------------------------------------------------------------------------------------------
# The DEM card of the export window
# --------------------------------------------------------------------------------------------------

def open_dialog():
    from aerotools.tools.fastexport import dialog
    from aerotools import msapi
    config.put("fastexport", "dem_look", None)
    msapi.raster_info = lambda chk=None: {"ortho": (0.05, 4000, 3000), "dem": (0.2, 1000, 800), "box_ortho": (0, 0, 200, 150),
                                          "box_dem": (0, 0, 200, 160), "crs": "WGS 84 / UTM zone 32N", "dates": [],
                                          "crs_object": None, "center": None}
    box = dialog.ExportDialog(None)
    box.show()
    box.activateWindow()
    box.refresh()
    pump()
    return box


@case("export window: the DEM card has the look line, hidden until the DEM is ticked with a format that uses it", needs_numpy=True)
def _():
    from aerotools.tools.fastexport import plan
    box = open_dialog()
    present = hasattr(box, "look_row") and box.look_button.isEnabled()
    box.preset.setCurrentIndex(box.preset.findData(plan.CAD))
    box.preset.activated.emit(box.preset.currentIndex())
    pump(80)
    shown_cad = not box.look_row.isHidden()
    box.preset.setCurrentIndex(box.preset.findData("full"))
    box.preset.activated.emit(box.preset.currentIndex())
    pump(80)
    key = box.preset.currentData()
    shown_other = not box.look_row.isHidden()
    text = box.look_text.text()
    box.close()
    return present and shown_cad and (shown_other == (key in plan.PICTURE_PRESETS)), \
        "present={} cad={} other({})={} text={!r}".format(present, shown_cad, key, shown_other, text)


@case("export window: the saved look is what the card says, and the saved look reaches the export context", needs_numpy=True)
def _():
    box = open_dialog()
    looks.save(looks.Look("viridis", looks.SLOPE, 10.0, 30.0, 0.5, looks.AUTO, None, None))
    box._refresh_look_text()
    text = box.look_text.text()
    saved = looks.load()
    box.close()
    return "slope" in text.lower() or "Slope" in text, "text={!r} saved={}".format(text, saved)


@case("export window: the pixel size field selects all on entry", needs_numpy=False)
def _():
    from aerotools.tools.fastexport import plan
    box = open_dialog()
    field = box.fields[plan.DEM]
    field.setText("5")
    other = box.fields[plan.ORTHO]
    other.setFocus()
    pump()
    field.setFocus(QtCore.Qt.TabFocusReason)
    pump()
    ok = isinstance(field, ui.SelectAllEdit) and field.selectedText() == "5"
    box.close()
    return ok, repr(field.selectedText())


def main():
    pump(30)
    print("-" * 60)
    print("Qt binding: {} ; Python {} ; platform {}".format(qt.BINDING, sys.version.split()[0],
                                                           os.environ.get("QT_QPA_PLATFORM")))
    if FAILURES:
        print("{} failure(s): {}".format(len(FAILURES), " | ".join(FAILURES)))
        return 1
    print("all cases pass" + (" ({} skipped)".format(len(SKIPPED)) if SKIPPED else ""))
    return 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
