"""Qt self-test of Aero-Exports (DEM look window, pixel field, DEM card), without Metashape.

Run (no pytest needed), with a Python that has PySide2 or PySide6 and numpy:
    python -B tests_qt\\run_qt_aeroexports.py

A fake `Metashape` module is injected before AeroTools is imported. Output: one PASS / FAIL line per case, a non-zero exit
code on the first failure. numpy is required for the preview window; without it the cases that need it are skipped.
"""

import os
import shutil
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
sys.path.insert(0, HERE)
import _isolation  # noqa: E402
GUARD = _isolation.Guard("aerotools_qt_fx_")                      # settings of the machine stay untouched
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FAKE = types.ModuleType("Metashape")
FAKE.app = types.SimpleNamespace(version="2.2.1-selftest", document=types.SimpleNamespace(chunk=None),
                                 addMenuItem=lambda *a, **k: None, messageBox=lambda text: None, update=lambda: None)
sys.modules["Metashape"] = FAKE

from aerotools import config, journal, qt  # noqa: E402
GUARD.pin(config)
from aerotools.qt import QtCore, QtGui, QtWidgets  # noqa: E402
from aerotools import ui  # noqa: E402
from aerotools.tools.aeroexports import look as looks  # noqa: E402

try:
    import numpy as np  # noqa: E402
    from aerotools.tools.aeroexports import preview, previewwin  # noqa: E402
except ImportError:                                                  # pragma: no cover
    np = None

journal.print = lambda *args, **kwargs: None
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
# Large fonts / display scaling: nothing may be cut off
# --------------------------------------------------------------------------------------------------

SCALES = (1.0, 1.25, 1.5, 2.0)
SCREEN = QtCore.QRect(0, 0, 1920, 1040)                # a Full HD screen; the offscreen one is smaller and varies


def rescale(window, factor):
    """What Windows display scaling does to the text: every font (the default one and the sizes written in the style sheets)
    grows by `factor`; sizes in pixels stay. Call it before the window is shown, so its minimum size is measured with the big fonts."""
    import re
    if factor == 1.0:
        return
    base = getattr(rescale, "base", None) or APP.font().pointSizeF()
    rescale.base = base
    font = QtGui.QFont(APP.font())
    font.setPointSizeF(base * factor)
    APP.setFont(font)
    pattern = re.compile(r"(\d+(?:\.\d+)?)pt")
    for widget in [window] + window.findChildren(QtWidgets.QWidget):
        sheet = widget.styleSheet()
        if "pt" in sheet:
            widget.setStyleSheet(pattern.sub(lambda m: "{:g}pt".format(float(m.group(1)) * factor), sheet))


def unscale():
    base = getattr(rescale, "base", None)
    if base:
        font = QtGui.QFont(APP.font())
        font.setPointSizeF(base)
        APP.setFont(font)


def cut_off(window):
    """Descriptions of what is cut off in a window: a widget past the right edge of its panel, a panel that needs a horizontal bar,
    or anything past the window's own edge. Empty when all is visible."""
    from aerotools import controls
    problems = []
    for index, area in enumerate(window.findChildren(controls.FitScroll)):
        page = area.widget()
        if area.horizontalScrollBar().isVisible() or page.width() > area.viewport().width():
            problems.append("panel {} needs a horizontal bar (page {} > view {})".format(index, page.width(), area.viewport().width()))
        for widget in page.findChildren(QtWidgets.QWidget):
            if not widget.isVisibleTo(page) or widget.width() <= 1:
                continue
            right = widget.mapTo(page, QtCore.QPoint(widget.width() - 1, 0)).x()
            if right > page.width() - 1:
                problems.append("panel {}: {} ({}) ends at {} > {}".format(index, type(widget).__name__, widget.objectName(), right,
                                                                           page.width() - 1))
    frame = window.rect()
    for widget in window.findChildren(QtWidgets.QWidget):
        if widget.isVisibleTo(window) and widget.width() > 1:
            corner = widget.mapTo(window, QtCore.QPoint(widget.width() - 1, widget.size().height() - 1))
            if corner.x() > frame.right() + 1:
                problems.append("{} ({}) past the window edge: {} > {}".format(type(widget).__name__, widget.objectName(), corner.x(),
                                                                              frame.right()))
    return problems


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
    from aerotools.tools.aeroexports import dialog
    from aerotools import msapi
    config.put("aeroexports", "dem_look", None)
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
    from aerotools.tools.aeroexports import plan
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
    from aerotools.tools.aeroexports import plan
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


# --------------------------------------------------------------------------------------------------
# The export window: two panels, and the coordinate-system trap (a raster in degrees)
# --------------------------------------------------------------------------------------------------

class FakeCrs(object):
    """What the window reads of a Metashape coordinate system: a name, a WKT and an authority."""

    def __init__(self, name, wkt, authority):
        self.name, self.wkt, self.authority = name, wkt, authority

    def __str__(self):
        return self.name


WGS84 = FakeCrs("WGS 84", 'GEOGCS["WGS 84",DATUM["WGS_1984"]]', "EPSG::4326")
LAMBERT93 = FakeCrs("RGF93 v1 / Lambert-93", 'PROJCS["RGF93 v1 / Lambert-93",GEOGCS["RGF93 v1"]]', "EPSG::2154")


def open_trap_dialog(ortho_in_degrees=True, dem_crs=LAMBERT93, preset="full", with_dem=True, scale=1.0, size=(1080, 740)):
    """The window on a chunk with an ortho in degrees (WGS 84) and a DEM in metres (here Lambert-93, which is not what the
    place alone would suggest, so a test can tell the DEM's own system from the best one for the place)."""
    from aerotools.tools.aeroexports import dialog
    from aerotools import msapi
    path = os.path.join(tempfile.mkdtemp(prefix="aerotools_qt_proj_"), "SITE.psx")
    open(path, "w").write("x")
    config.put("aeroexports", "dem_look", None)
    config.put("aeroexports", "preset", preset)
    msapi.document_path = lambda: path
    msapi.chunk = lambda: object()
    dialog.crs.check_geoid_presence = lambda choice: {"status": "ok", "spec": {}}
    dialog.crs.zone_grids = lambda *args: []
    msapi.target_crs = lambda epsg: object()
    msapi.raster_info = lambda chk=None: {
        "ortho": (0.0105, 8150, 8770), "dem": (0.042, 2000, 2200) if with_dem else None, "box_ortho": (2.35, 48.85, 2.36, 48.86),
        "box_dem": (650005.0, 6860005.0, 650094.0, 6860103.0) if with_dem else None, "crs": "WGS 84",
        "dates": ["2030:01:14 10:00:00"], "crs_object": WGS84, "crs_ortho": WGS84 if ortho_in_degrees else dem_crs,
        "crs_dem": dem_crs if with_dem else None, "geographic_ortho": ortho_in_degrees, "geographic_dem": False,
        "center": (2.355, 48.855)}
    box = dialog.ExportDialog(None)
    rescale(box, scale)
    if size:
        box.resize(*size)
    box.show()
    box.refresh()
    pump(120)
    return box


@case("export window: two panels side by side (what and where on the left, format and button on the right), nothing to scroll")
def _():
    box = open_trap_dialog(preset="cad")
    box.site.setText("SITE")
    pump(80)
    def x_of(widget):
        return widget.mapTo(box, QtCore.QPoint(0, 0)).x()
    left = max(x_of(box.cards["ortho"]), x_of(box.crs_box))
    right = min(x_of(box.preset), x_of(box.names), x_of(box.go_button))
    layout_ok = x_of(box.cards["dem"]) == x_of(box.cards["ortho"]) < x_of(box.preset) and left < right
    areas = box.findChildren(QtWidgets.QScrollArea)
    overflow = [a.verticalScrollBar().maximum() for a in areas]
    buttons_in_view = box.go_button.isVisible() and box.go_button.mapTo(box, QtCore.QPoint(0, 0)).y() + box.go_button.height() <= box.height()
    box.close()
    return layout_ok and len(areas) == 2 and not any(overflow) and buttons_in_view, \
        "left={} right={} overflow={} buttons={}".format(left, right, overflow, buttons_in_view)


@case("export window: ortho in degrees next to a DEM in metres: the DEM's own projected system is preselected, with a notice")
def _():
    box = open_trap_dialog()
    chosen = box.crs_box.currentData()
    notice = box.crs_notice.text()
    shown = box.crs_notice.isVisible()
    each = box.crs.text()
    vertical_ready = box.vert_box.count() >= 1
    box.close()
    return chosen == 2154 and shown and "degrees" in notice and "Lambert-93" in notice and "WGS 84" in each and "Lambert-93" in each \
        and vertical_ready, "chosen={} shown={} notice={!r} crs={!r}".format(chosen, shown, notice, each)


@case("export window: without a DEM in metres, the best system for the place is preselected")
def _():
    box = open_trap_dialog(with_dem=False)
    chosen = box.crs_box.currentData()
    box.close()
    return chosen == 3948, "chosen={}".format(chosen)


@case("export window: a system the user picked is kept, and picking 'Same as the project' brings the warning back")
def _():
    box = open_trap_dialog()
    box.crs_box.setCurrentIndex(box.crs_box.findData(3948))
    box.crs_box.activated.emit(box.crs_box.currentIndex())
    pump(60)
    box.refresh()
    pump(60)
    kept = box.crs_box.currentData() == 3948 and not box.crs_notice.isVisible()
    box.crs_box.setCurrentIndex(0)
    box.crs_box.activated.emit(0)
    pump(60)
    box.refresh()
    pump(60)
    same = box.crs_box.currentData() == "same" and box.crs_notice.isVisible() and "projected system" in box.crs_notice.text()
    box.close()
    return kept and same, "kept={} same={}".format(kept, same)


@case("export window: unticking the ortho gives the project's system back (the DEM alone keeps its native grid)")
def _():
    box = open_trap_dialog()
    box.cards["ortho"].button.setChecked(False)
    pump(80)
    back = box.crs_box.currentData() == "same" and not box.crs_notice.isVisible()
    box.cards["ortho"].button.setChecked(True)
    pump(80)
    again = box.crs_box.currentData() == 2154 and box.crs_notice.isVisible()
    box.close()
    return back and again, "back={} again={}".format(back, again)


@case("export window: no notice and no preselection when nothing is in degrees")
def _():
    box = open_trap_dialog(ortho_in_degrees=False)
    ok = box.crs_box.currentData() == "same" and not box.crs_notice.isVisible()
    box.close()
    return ok, "chosen={}".format(box.crs_box.currentData())


@case("export window: a drawing from a raster in degrees with 'Same as the project' is refused in words, before any file")
def _():
    from aerotools.tools.aeroexports import dialog
    box = open_trap_dialog(preset="cad")
    box.site.setText("SITE")
    box.crs_box.setCurrentIndex(0)
    box.crs_box.activated.emit(0)
    pump(60)
    ran = []
    real = dialog.pipeline.run
    dialog.pipeline.run = lambda *args, **kwargs: ran.append(1)
    try:
        box._on_export()
    finally:
        dialog.pipeline.run = real
    text = box.status.text()
    box.close()
    return not ran and "degrees" in text and "metres" in text, "ran={} status={!r}".format(ran, text)


@case("export window: a failure names the file that failed, not the first of the list")
def _():
    from aerotools.tools.aeroexports import dialog
    box = open_trap_dialog()
    box.site.setText("SITE")
    box.cards["ortho"].button.setChecked(True)
    pump(60)
    names = [job.name for job in box._jobs]

    def fail(ctx, jobs, progress):
        ctx.failed = jobs[-1].name
        raise dialog.pipeline.Failure("Empty raster")

    real = dialog.pipeline.run
    dialog.pipeline.run = fail
    try:
        box._on_export()
    finally:
        dialog.pipeline.run = real
    text = box.status.text()
    box.close()
    return len(names) == 2 and names[-1] in text and names[0] not in text and "Empty raster" in text, \
        "names={} status={!r}".format(names, text)


@case("large fonts: the export window keeps every widget inside its panel at x1, x1.25, x1.5 and x2 (nothing cut off)")
def _():
    from aerotools import controls
    from aerotools.tools.aeroexports import plan
    controls.screen_area = lambda: SCREEN
    results = {}
    try:
        for factor in SCALES:
            box = open_trap_dialog(preset="cad", scale=factor)
            box.site.setText("A_RATHER_LONG_SITE_NAME")
            box._set_advanced(True)
            box._finish("SITE_A_300112_14mm_dtm.tif: " + "X" * 90, "error")        # a long unbreakable text must wrap, not widen
            pump(150)
            results[factor] = (cut_off(box), box.minimumSize().width(), box.size().width())
            box.close()
            unscale()
    finally:
        unscale()
        controls.screen_area = lambda: QtCore.QRect(0, 0, 1086, 800)
    bad = {f: r[0] for f, r in results.items() if r[0]}
    return not bad, "cut off: {} ; min/actual widths {}".format(bad, {f: r[1:] for f, r in results.items()})


@case("large fonts: the look window keeps every widget inside its panel at x1, x1.25, x1.5 and x2 (nothing cut off)", needs_numpy=True)
def _():
    from aerotools import controls
    controls.screen_area = lambda: SCREEN
    results = {}
    try:
        for factor in SCALES:
            window = previewwin.PreviewWindow(None, small_preview(), looks.DEFAULT, note="A note " * 12)
            rescale(window, factor)
            window.show()
            pump(150)
            results[factor] = (cut_off(window), window.minimumSize().width(), window.size().width())
            window.close()
            unscale()
    finally:
        unscale()
        controls.screen_area = lambda: QtCore.QRect(0, 0, 1086, 800)
    bad = {f: r[0] for f, r in results.items() if r[0]}
    return not bad, "cut off: {} ; min/actual widths {}".format(bad, {f: r[1:] for f, r in results.items()})


@case("small screen: a window wider than the screen shrinks to it and scrolls (visible bar) instead of leaving the screen")
def _():
    from aerotools import controls
    controls.screen_area = lambda: QtCore.QRect(0, 0, 1024, 700)
    try:
        box = open_trap_dialog(preset="cad", scale=2.0, size=None)
        pump(150)
        fits = box.minimumSize().width() <= 1024 - 70 and box.size().width() <= 1024
        bars = [a.horizontalScrollBar().isVisible() or a.verticalScrollBar().isVisible() for a in box.findChildren(controls.FitScroll)]
        box.close()
    finally:
        unscale()
        controls.screen_area = lambda: QtCore.QRect(0, 0, 1086, 800)
    return fits and any(bars), "fits={} bars={}".format(fits, bars)


# --------------------------------------------------------------------------------------------------
# Always the right chunk: the window pins the chunk it shows; a chunk changed in Metashape is noticed, never exported by surprise
# --------------------------------------------------------------------------------------------------

def fake_raster(key, width, height, resolution):
    return types.SimpleNamespace(key=key, label=str(key), width=width, height=height, resolution=resolution)


def fake_shape(kind):
    return types.SimpleNamespace(boundary_type="Metashape.Shape.BoundaryType." + kind)


class FakeChunk(object):
    """A chunk as the window reads it: key, label, default DEM and ortho, the others, shapes."""

    def __init__(self, key, label, dem, ortho, others=0, shapes=()):
        self.key, self.label, self.elevation, self.orthomosaic = key, label, dem, ortho
        self.elevations = [dem] + [fake_raster(90 + i, 10, 10, 1.0) for i in range(others)]
        self.orthomosaics = [ortho]
        self.shapes = list(shapes)


JULY = FakeChunk(1, "July", fake_raster(3, 5200, 6400, 0.0125), fake_raster(0, 2019, 2395, 0.05), others=3,
                 shapes=[fake_shape("OuterBoundary")])
MAY = FakeChunk(2, "May", fake_raster(4, 172, 165, 0.5), fake_raster(7, 6000, 5000, 0.0079), others=2,
                shapes=[fake_shape("NoBoundary")] * 5)
CHUNK_INFO = {
    "July": {"ortho": (0.05, 2019, 2395), "dem": (0.0125, 5200, 6400), "box_ortho": (100.0, 200.0, 280.0, 350.0),
             "box_dem": (100.0, 200.0, 280.0, 350.0), "crs": "RGF93 v1 / Lambert-93", "dates": ["2030:07:10 09:00:00"],
             "crs_object": None, "center": None},
    "May": {"ortho": (0.0079, 6000, 5000), "dem": (0.5, 172, 165), "box_ortho": (900.0, 800.0, 1050.0, 925.0),
            "box_dem": (900.0, 800.0, 1040.0, 920.0), "crs": "WGS 84 / UTM zone 32N", "dates": ["2030:05:12 09:00:00"],
            "crs_object": None, "center": None},
}
ACTIVE = {"chunk": JULY}


def fake_info(chk=None):
    """msapi.raster_info on the fake chunks: boxes and systems from CHUNK_INFO, default rasters and shapes read for real."""
    from aerotools import msapi
    info = dict(CHUNK_INFO["May" if chk is MAY else "July"])
    info.update(dem_default=msapi.default_raster(chk, "elevation"), ortho_default=msapi.default_raster(chk, "orthomosaic"),
                n_dems=msapi.raster_count(chk, "elevations"), n_orthos=msapi.raster_count(chk, "orthomosaics"),
                boundaries=msapi.boundary_count(chk))
    return info


def open_chunk_dialog(label=None, scale=1.0, size=(1080, 740)):
    """The window with two chunks of one project (different boxes, systems, dates); July is active."""
    from aerotools.tools.aeroexports import dialog
    from aerotools import msapi
    path = os.path.join(tempfile.mkdtemp(prefix="aerotools_qt_chunk_"), "SITE.psx")
    open(path, "w").write("x")
    config.put("aeroexports", "dem_look", None)
    config.put("aeroexports", "preset", "full")
    ACTIVE["chunk"] = JULY
    JULY.label = label or "July"
    MAY.elevation = MAY.elevations[0]
    config.put("aeroexports", "clip_to_boundary_shapes", None)
    msapi.document_path = lambda: path
    msapi.chunk = lambda: ACTIVE["chunk"]
    msapi.raster_info = fake_info
    dialog.crs.check_geoid_presence = lambda choice: {"status": "ok", "spec": {}}
    dialog.crs.zone_grids = lambda *args: []
    box = dialog.ExportDialog(None)
    rescale(box, scale)
    if size:
        box.resize(*size)
    box.show()
    box.refresh()
    box.site.setText("SITE")
    pump(100)
    return box


def fake_export(calls, during=None):
    def export(chk, kind, spec, path, pixel=None, box=None, big=False, progress=None, crs=None, clip_to_boundary=True):
        calls.append({"chunk": chk, "kind": kind, "path": path, "pixel": pixel, "box": box, "clip": clip_to_boundary})
        if during is not None and len(calls) == 1:
            during()
        with open(path, "wb") as handle:
            handle.write(b"x")
        return True, ""
    return export


@case("chunk: the window names its chunk, and a chunk changed before Export is shown, not exported by surprise")
def _():
    from aerotools import msapi
    box = open_chunk_dialog()
    header = box.chunk_title.text()
    names_july = [job.name for job in box._jobs]
    box._preview, box._preview_key = object(), box._preview_signature()           # a preview made on July
    calls = []
    msapi.export_raster = fake_export(calls)
    ACTIVE["chunk"] = MAY                                                          # the user switched chunk in Metashape
    box._on_export()
    notice = box.status.text()
    names_may = [job.name for job in box._jobs]
    first = (list(calls), box._chunk is MAY, box.chunk_title.text(), notice, box._preview, box._preview_model_cached())
    box._on_export()                                                               # now the user presses Export on what he sees
    box.close()
    ok = (header == "Chunk: July" and first[0] == [] and first[1] and first[2] == "Chunk: May"
          and "active chunk changed" in first[3] and "May" in first[3] and first[4] is None and first[5] is None
          and all("300710" in n for n in names_july) and all("300512" in n for n in names_may)
          and len(calls) == 2 and all(c["chunk"] is MAY for c in calls))
    return ok, "header={!r} first={} names={} / {} calls={}".format(header, first, names_july, names_may, [c["path"] for c in calls])


@case("chunk: a chunk switched while the export runs does not change what is exported (the pinned chunk is used to the end)")
def _():
    from aerotools import msapi
    box = open_chunk_dialog()
    calls = []
    msapi.export_raster = fake_export(calls, during=lambda: ACTIVE.update(chunk=MAY))
    box._on_export()
    done = box.status.text()
    title_after = box.chunk_title.text()
    all_july = len(calls) == 2 and all(c["chunk"] is JULY for c in calls)
    box._follow_chunk()                                                            # back in front: now it shows May
    title_then = box.chunk_title.text()
    box.close()
    return all_july and title_after == "Chunk: July" and title_then == "Chunk: May", \
        "calls={} done={!r} after={!r} then={!r}".format([(c["chunk"].label, c["kind"]) for c in calls], done, title_after, title_then)


@case("chunk: the preview never works on another chunk than the one shown")
def _():
    box = open_chunk_dialog()
    ACTIVE["chunk"] = MAY
    model = box._preview_model()
    ok = model is None and box._chunk is MAY and "May" in box.status.text() and "Chunk: May" == box.chunk_title.text()
    box.close()
    return ok, "status={!r}".format(box.status.text())


@case("chunk: the signature of the preview changes with the chunk")
def _():
    box = open_chunk_dialog()
    first = box._preview_signature()
    ACTIVE["chunk"] = MAY
    box._follow_chunk()
    second = box._preview_signature()
    box.close()
    return first != second, "{} / {}".format(first, second)


@case("large fonts: the chunk line stays inside its panel at x1, x1.25, x1.5 and x2, a long name is cut with the full name as tooltip")
def _():
    from aerotools import controls
    controls.screen_area = lambda: SCREEN
    long_name = "Chunk of the July flight over the harbour and the old town with the second battery " * 3
    results = {}
    try:
        for factor in SCALES:
            box = open_chunk_dialog(label=long_name, scale=factor)
            pump(150)
            title = box.chunk_title
            results[factor] = (cut_off(box), title.text().endswith("…"), title.toolTip() == "Chunk: " + long_name,
                               title.isVisible() and title.height() > 0)
            box.close()
            unscale()
    finally:
        unscale()
        controls.screen_area = lambda: QtCore.QRect(0, 0, 1086, 800)
    bad = {f: r for f, r in results.items() if r[0] or not all(r[1:])}
    return not bad, "problems: {}".format(bad)


@case("boundary: the tick box shows only for a chunk with boundary shapes, is off by default, and is saved")
def _():
    box = open_chunk_dialog()
    shown_july = not box.boundary_row.isHidden()
    default_off = not box.boundary_check.isChecked()
    note_off = box.boundary_note.text()
    box.boundary_check.setChecked(True)
    saved_on = config.get("aeroexports", "clip_to_boundary_shapes")
    note_on = box.boundary_note.text()
    ACTIVE["chunk"] = MAY                                   # 5 shapes, none of them a boundary: nothing to clip
    box._follow_chunk()
    hidden_may = box.boundary_row.isHidden()
    clip_may = box._boundary_clip()
    ACTIVE["chunk"] = JULY
    box._follow_chunk()
    still_on = box.boundary_check.isChecked()
    box.close()
    return (shown_july and default_off and "1 boundary shape" in note_on and saved_on is True and "whole area" in note_off
            and hidden_may and clip_may is True and still_on), \
        "july={} off={} note={!r} saved={} off_note={!r} may_hidden={} may_clip={} remembered={}".format(
            shown_july, default_off, note_on, saved_on, note_off, hidden_may, clip_may, still_on)


@case("boundary: the setting reaches the preview export, every raster export and the journal lines")
def _():
    from aerotools import journal, msapi
    box = open_chunk_dialog()
    calls, lines = [], []
    msapi.export_raster = fake_export(calls)
    real_write = journal.write
    journal.write = lines.append
    try:
        box.boundary_check.setChecked(False)
        box._preview_model()                                # a small export (the fake writes no real TIFF, so the preview fails: the call is what counts)
        preview_flags = [c["clip"] for c in calls]
        del calls[:]
        box._on_export()
        export_flags = [c["clip"] for c in calls]
        del calls[:]
        shutil.rmtree(box._folder(), ignore_errors=True)          # else the window asks overwrite / new version
        box.boundary_check.setChecked(True)
        box._on_export()
        on_flags = [c["clip"] for c in calls]
    finally:
        journal.write = real_write
    text = " | ".join(lines)
    box.close()
    return (preview_flags == [False] and export_flags == [False, False] and on_flags == [True, True]
            and "chunk 'July'" in text and "DEM key 3" in text and "5200x6400" in text and "clip_to_boundary=False" in text
            and "clip_to_boundary=True" in text and "ortho key 0" in text), \
        "preview={} export={} on={} lines={}".format(preview_flags, export_flags, on_flags, text[:600])


@case("raster: each card says exactly which raster is exported, how many there are, and warns about a coarse DEM")
def _():
    box = open_chunk_dialog()
    july_dem = box.raster_details["dem"].text()
    july_ortho = box.raster_details["ortho"].text()
    july_warn = box.raster_warnings["dem"].isHidden()
    ACTIVE["chunk"] = MAY
    box._follow_chunk()
    may_dem = box.raster_details["dem"].text()
    may_warn = box.raster_warnings["dem"].text()
    may_visible = not box.raster_warnings["dem"].isHidden()
    box.close()
    return ("Default DEM: 5200 x 6400 px, 1.25 cm/px." in july_dem and "1 of 4 DEMs" in july_dem and "Workspace" in july_dem
            and "Default orthomosaic: 2019 x 2395 px, 5 cm/px." in july_ortho and "double-click" not in july_ortho and july_warn
            and "Default DEM: 172 x 165 px, 50 cm/px." in may_dem and "1 of 3 DEMs" in may_dem
            and may_visible and "coarse (50 cm/px)" in may_warn), \
        "{!r} / {!r} / {} / {!r} / {!r}".format(july_dem, july_ortho, july_warn, may_dem, may_warn)


@case("auto refresh: the real timer follows a chunk switched in Metashape, and a default DEM changed in the Workspace, by itself")
def _():
    box = open_chunk_dialog()
    running = box._watch.isActive() and box._watch.interval() <= 500
    ACTIVE["chunk"] = MAY                                   # no click in the window, no activation event
    pump(1000)
    title = box.chunk_title.text()
    notice = box.status.text()
    MAY.elevation = fake_raster(9, 4000, 3000, 0.015)       # double-click on another DEM in the Workspace pane
    pump(1000)
    dem_notice = box.status.text()
    dem_text = box.raster_details["dem"].text()
    MAY.elevation = MAY.elevations[0]                       # back to the coarse one for the other cases
    box.close()
    return (running and title == "Chunk: May" and "active chunk changed" in notice and "May" in notice
            and dem_notice == "The default DEM changed" and "4000 x 3000 px" in dem_text), \
        "running={} title={!r} notice={!r} dem_notice={!r} dem={!r}".format(running, title, notice, dem_notice, dem_text)


@case("auto refresh: nothing happens during an export or while a window of ours is open on top, and the timer never raises")
def _():
    from aerotools import msapi
    box = open_chunk_dialog()
    ACTIVE["chunk"] = MAY
    box._busy = True
    box._watch_tick()
    kept_busy = box._chunk is JULY
    box._busy = False
    box._dialog_open = 1
    box._watch_tick()
    kept_open = box._chunk is JULY
    box._dialog_open = 0
    real = msapi.raster_signature
    msapi.raster_signature = lambda chk: 1 / 0
    try:
        box._watch_tick()                                   # a failing read of Metashape is logged, not raised
    finally:
        msapi.raster_signature = real
    box._watch_tick()
    followed = box._chunk is MAY
    box.close()
    return kept_busy and kept_open and followed, "busy={} open={} followed={}".format(kept_busy, kept_open, followed)


@case("large fonts: the raster lines, the coarse-DEM warning and the boundary tick box stay inside their panel at x1 to x2")
def _():
    from aerotools import controls
    controls.screen_area = lambda: SCREEN
    results = {}
    try:
        for factor in SCALES:
            box = open_chunk_dialog(scale=factor)
            pump(150)
            july = (cut_off(box), not box.boundary_row.isHidden(), bool(box.raster_details["dem"].text()))
            ACTIVE["chunk"] = MAY
            box._follow_chunk()
            pump(150)
            may = (cut_off(box), not box.raster_warnings["dem"].isHidden())
            results[factor] = (july, may)
            box.close()
            unscale()
    finally:
        unscale()
        controls.screen_area = lambda: QtCore.QRect(0, 0, 1086, 800)
    bad = {f: r for f, r in results.items() if r[0][0] or r[1][0] or not (r[0][1] and r[0][2] and r[1][1])}
    return not bad, "problems: {}".format(bad)


@case("resize: the GCP diagnostic and the busy window (and the disk space window) declare the least height their wrapped text needs")
def _():
    from aerotools.tools.fastgcp import diagnostic_dialog, review_dialog
    from aerotools.tools.fastspace import dialog as space_dialog
    result = {"summary": {"observations": 10, "markers": 3, "checked": 3, "photos": 20, "rms_px": 0.5, "median_px": 0.4,
                          "below_1px": 0.9}, "projections": [], "photos": [], "markers": [], "limit_px": 1.0}
    makers = {
        "space": space_dialog.SpaceDialog,
        "diagnostic": lambda: diagnostic_dialog.DiagnosticDialog(lambda: result, lambda item: None),
        "busy": lambda: review_dialog.BusyDialog("Applying the changes to the markers"),
    }
    short = {}
    for name, make in makers.items():
        dialog = make()
        dialog.show()
        pump(200)
        layout = dialog.layout()
        need = layout.minimumHeightForWidth(dialog.width()) if layout.hasHeightForWidth() else layout.minimumSize().height()
        if dialog.minimumHeight() < need:
            short[name] = (dialog.minimumHeight(), need)
        dialog.resize(dialog.width(), 40)                      # as far down as a drag can go
        pump(100)
        if dialog.height() < need:
            short[name + " (shrunk)"] = (dialog.height(), need)
        dialog.close()
    return not short, "least height below what the text needs: {}".format(short)


@case("uninstall window: the menu is ticked and locked, settings and AI environment are optional, nothing is clipped, the result says what is left")
def _():
    from aerotools import uninstall, uninstall_dialog
    base = tempfile.mkdtemp(prefix="aerotools_uninstall_")
    saved = {name: os.environ.get(name) for name in ("LOCALAPPDATA", "USERPROFILE", "HOME")}
    try:
        os.environ.update(LOCALAPPDATA=os.path.join(base, "local"), USERPROFILE=os.path.join(base, "profile"),
                          HOME=os.path.join(base, "profile"))
        for path in (os.path.dirname(uninstall.launcher_path()), os.path.join(uninstall.env_dir(), "Scripts")):
            os.makedirs(path)
        open(uninstall.launcher_path(), "w").write("# launcher")
        open(os.path.join(uninstall.env_dir(), "Scripts", "python.exe"), "wb").write(b"x" * 2048)
        dialog = uninstall_dialog.UninstallDialog()
        dialog.show()
        pump(200)
        boxes = dialog.boxes
        problems = []
        if not (boxes["menu"].isChecked() and not boxes["menu"].isEnabled()):
            problems.append("menu box should be ticked and locked")
        if boxes["settings"].isChecked() or boxes["env"].isChecked() or not boxes["env"].isEnabled():
            problems.append("settings and environment should start unticked but available")
        if dialog.width() != uninstall_dialog.WIDTH:
            problems.append("width {}".format(dialog.width()))
        boxes["env"].setChecked(True)
        dialog._on_go()
        for _ in range(100):
            pump(50)
            if dialog.completed:
                break
        pump(200)
        layout = dialog.layout()
        need = layout.minimumHeightForWidth(dialog.width())
        if dialog.height() < need or dialog.minimumHeight() < need:
            problems.append("window {} / least {} below the {} the text needs".format(dialog.height(), dialog.minimumHeight(), need))
        if os.path.exists(uninstall.launcher_path()) or os.path.exists(uninstall.env_dir()):
            problems.append("launcher or environment still there")
        if "restart Metashape" not in dialog.status.text():
            problems.append("result text: " + dialog.status.text())
        if dialog.go_button.isVisible():
            problems.append("Uninstall button should be gone")
        dialog.close()
        return not problems, "; ".join(problems)
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        shutil.rmtree(base, ignore_errors=True)


@case("engine options window: starts on Light without retraining, retraining needs an engine")
def _():
    from aerotools import plugins_dialog
    problems = []
    options = plugins_dialog.EngineOptions("Aero-GCP")
    if options.choice() != ("light", False):
        problems.append("engine options should start on Light, no retraining: {}".format(options.choice()))
    options.radios["none"].setChecked(True)
    pump(50)
    if options.training.isEnabled() or options.choice() != (None, False):
        problems.append("without an engine the retraining box must be off")
    options.radios["both"].setChecked(True)
    options.training.setChecked(True)
    if options.choice() != ("both", True):
        problems.append("engine options choice: {}".format(options.choice()))
    return not problems, "; ".join(problems)


def main():
    pump(30)
    print("-" * 60)
    print("Qt binding: {} ; Python {} ; platform {}".format(qt.BINDING, sys.version.split()[0],
                                                           os.environ.get("QT_QPA_PLATFORM")))
    problem = GUARD.check()
    print("{} the real settings are untouched".format("FAIL" if problem else "PASS") + ("  -> " + problem if problem else ""))
    if problem:
        FAILURES.append("real settings untouched")
    if FAILURES:
        print("{} failure(s): {}".format(len(FAILURES), " | ".join(FAILURES)))
        return 1
    print("all cases pass" + (" ({} skipped)".format(len(SKIPPED)) if SKIPPED else ""))
    return 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
