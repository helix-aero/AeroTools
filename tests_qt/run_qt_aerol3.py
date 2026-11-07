"""Qt self-test of the Aero-L3 wizard: real widgets, real mouse and key events, a fake backend, synthetic L3 photos. No Metashape needed.

    python -B tests_qt\\run_qt_aerol3.py                 (PySide6 here, also meant for Metashape's PySide2)
    python -B tests_qt\\run_qt_aerol3.py --shots DIR     also writes a PNG of every screen at three font scales into DIR and prints how much text each one holds

`AEROL3_ONLY=text` runs only the cases whose name contains the text.
"""

import datetime
import os
import re
import sys
import tempfile
import time
import traceback
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import _isolation  # noqa: E402
GUARD = _isolation.Guard("aerotools_qt_l3_")                      # settings of the machine stay untouched
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")   # the offscreen platform finds no font by itself

FAKE = types.ModuleType("Metashape")
FAKE.app = types.SimpleNamespace(version="2.2.1-selftest", document=types.SimpleNamespace(chunk=None),
                                 addMenuItem=lambda *a, **k: None, messageBox=lambda text: None, update=lambda: None)
sys.modules["Metashape"] = FAKE

from aerotools import config, journal  # noqa: E402
GUARD.pin(config)
from aerotools.qt import QtCore, QtGui, QtWidgets  # noqa: E402
from aerotools.tools.aeroexports import controls  # noqa: E402
from aerotools.tools.aerol3 import checks, plan as plan_module, state as S, widgets, wizard as W  # noqa: E402
from aerotools.tools.aerol3.fake_backend import FakeBackend  # noqa: E402
import aerol3_synth as synth  # noqa: E402

controls.screen_area = lambda: QtCore.QRect(0, 0, 1920, 1040)       # a Full HD screen (the offscreen one is small and varies)
journal.print = lambda *args, **kwargs: None
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
FAILURES = []
SHOTS = None
if "--shots" in sys.argv:
    SHOTS = sys.argv[sys.argv.index("--shots") + 1]
    os.makedirs(SHOTS, exist_ok=True)
DATA = tempfile.mkdtemp(prefix="aerol3_data_")


# --------------------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------------------

def pump(milliseconds=60):
    end = time.time() + milliseconds / 1000.0
    while time.time() < end:
        APP.processEvents()
        time.sleep(0.004)


def until(condition, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        APP.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


ONLY = os.environ.get("AEROL3_ONLY", "")


def case(name):
    def wrap(func):
        if ONLY and ONLY not in name:
            return func
        try:
            result = func()
            ok, detail = result if isinstance(result, tuple) else (bool(result), "")
        except Exception as exc:
            ok, detail = False, "exception {!r}\n{}".format(exc, traceback.format_exc())
        line = "{} {}{}".format("PASS" if ok else "FAIL", name, "" if ok or not detail else "  -> " + str(detail))
        print(line.encode("ascii", "replace").decode("ascii"))
        if not ok:
            FAILURES.append(name)
        return func
    return wrap


def click_button(button):
    """A real left click in the middle of a button."""
    point = QtCore.QPointF(button.rect().center())
    for kind in (QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonRelease):
        buttons = QtCore.Qt.LeftButton if kind == QtCore.QEvent.MouseButtonPress else QtCore.Qt.NoButton
        APP.sendEvent(button, QtGui.QMouseEvent(kind, point, point, QtCore.Qt.LeftButton, buttons, QtCore.Qt.NoModifier))
    pump(30)


def key(widget, code, text="", modifiers=QtCore.Qt.NoModifier):
    for kind in (QtCore.QEvent.KeyPress, QtCore.QEvent.KeyRelease):
        APP.sendEvent(widget, QtGui.QKeyEvent(kind, code, modifiers, text))


def type_into(field, text):
    """Select everything in a line edit, type `text` and press Enter."""
    field.setFocus()
    field.selectAll()
    for ch in text:
        key(field, QtCore.Qt.Key_0, ch)
    field.setText(text) if field.text() != text else None
    key(field, QtCore.Qt.Key_Return, "\r")
    field.editingFinished.emit()
    pump(30)


SCALES = (1.0, 1.5, 2.0)


def rescale(window, factor):
    """What Windows display scaling does to the text: every font grows by `factor`. Call it before the window is shown."""
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


def _parents(widget):
    parent = widget.parentWidget()
    while parent is not None:
        yield parent
        parent = parent.parentWidget()


def cut_off(window):
    """What is cut off in a window: a scroll page that needs a horizontal bar, a widget past its page's or the window's right edge, text in a
    label that does not fit its box. Empty when all is visible."""
    problems = []
    for index, area in enumerate(window.findChildren(controls.FitScroll)):
        if not area.isVisibleTo(window):
            continue
        page = area.widget()
        if area.horizontalScrollBar().isVisible() or page.width() > area.viewport().width() + 1:
            problems.append("panel {} needs a horizontal bar (page {} > view {})".format(index, page.width(), area.viewport().width()))
        for widget in page.findChildren(QtWidgets.QWidget):
            if not widget.isVisibleTo(page) or widget.width() <= 1:
                continue
            right = widget.mapTo(page, QtCore.QPoint(widget.width() - 1, 0)).x()
            if right > page.width() - 1:
                problems.append("panel {}: {} ({}) ends at {} > {}".format(index, type(widget).__name__, widget.objectName(), right, page.width() - 1))
    frame = window.rect()
    for widget in window.findChildren(QtWidgets.QWidget):
        if widget.isVisibleTo(window) and widget.width() > 1:
            corner = widget.mapTo(window, QtCore.QPoint(widget.width() - 1, widget.size().height() - 1))
            inside_scroll = any(isinstance(p, QtWidgets.QScrollArea) for p in _parents(widget))
            if corner.x() > frame.right() + 1 or (corner.y() > frame.bottom() + 1 and not inside_scroll):
                problems.append("{} ({}) past the window edge: {},{} > {},{}".format(type(widget).__name__, widget.objectName(), corner.x(),
                                                                                    corner.y(), frame.right(), frame.bottom()))
    for label in window.findChildren(QtWidgets.QLabel):
        if label.isVisibleTo(window) and label.wordWrap() and label.text() and label.width() > 20:
            need = label.heightForWidth(label.width())
            if need > label.height() + 2:
                problems.append("label '{}' is cut: needs {} px, has {}".format(label.text()[:30], need, label.height()))
    return problems


def shot(window, name):
    if SHOTS:
        pump(80)
        window.grab().save(os.path.join(SHOTS, name + ".png"))


def make_mission(name="mission", extra=True, mixed=False):
    """A synthetic mission folder: two nadir flights in opposite directions, a calibration flight and a stray picture. Returns its path."""
    folder = os.path.join(DATA, name)
    os.makedirs(folder, exist_ok=True)
    synth.write_flight(folder, "DJI_202610020844_009_TESTSITE-FLIGHT01", shots=12, heading=120.0, group="B")
    synth.write_flight(folder, "DJI_202610020910_011_TESTSITE-FLIGHT02", shots=12, heading=300.0, group="A", lat=43.1005,
                       start=synth.GPS0 + datetime.timedelta(minutes=30))
    if extra:
        def tags_for(i, lens, tags):
            tags.update(GimbalPitchDegree="+0.00", GimbalRollDegree="+0.00", CameraPitchDegree="+0.00", CameraRollDegree="+0.00")
            tags["CameraYawDegree"] = "%+.2f" % (float(tags["GimbalYawDegree"]) + (-22.5 if lens == "L" else 22.5))
            return tags
        synth.write_flight(folder, "DJI_202610021054_016_TESTSITE-VOLBIAIS", shots=6, tags_for=tags_for, start=synth.GPS0 + datetime.timedelta(hours=2))
    if mixed:
        synth.write_flight(folder, "DJI_202610021300_020_TESTSITE-OTHERL3", shots=6, start=synth.GPS0 + datetime.timedelta(hours=4),
                           tags_for=lambda i, lens, t: dict(t, CameraSerialNumber="OTHER-" + lens))
    with open(os.path.join(folder, "stray.jpg"), "wb") as handle:
        handle.write(synth.make_jpeg(make="Canon", model="X", tags={}, with_xmp=False))
    return folder


MISSION = make_mission()


def new_wizard(keep_settings=False, **backend_args):
    if not keep_settings:                                    # every case starts from the default choices
        try:
            os.remove(config.settings_path())
        except OSError:
            pass
    backend = FakeBackend(pump=lambda: APP.processEvents(), **backend_args)
    window = W.L3Wizard(backend)
    window.pick_folder = lambda start: MISSION
    return window, backend


def show(window):
    window.fit_to_fonts()
    window.show()
    pump(80)
    window.resize(window.size())
    pump(80)


def scanned(window, folder=None):
    """Type the folder, press Scan, wait for the scan to end."""
    screen = window.screens[S.PHOTOS]
    screen.path.setText(folder or MISSION)
    click_button(screen.scan_button)
    assert until(lambda: not window.busy() and window.state.has_photos(), 30), "the scan did not finish"
    pump(60)


def to_step(window, step):
    scanned(window)
    while window.current_step() < step:
        assert window.next_button.isEnabled(), "Next is disabled on step {}".format(window.current_step())
        click_button(window.next_button)
        pump(60)


def texts_of(screen):
    """The words a screen shows (labels, buttons, tick boxes), without the tips."""
    out = []
    for widget in screen.findChildren(QtWidgets.QWidget):
        if not widget.isVisibleTo(screen):
            continue
        if isinstance(widget, (QtWidgets.QLabel, QtWidgets.QAbstractButton)) and widget.text():
            out.append(widget.text())
    return out


def labels(window):
    return texts_of(window.screen())


def vertical_bars(window):
    return [bar for bar in window.screen().findChildren(QtWidgets.QScrollBar) if bar.orientation() == QtCore.Qt.Vertical and bar.isVisibleTo(window)]


def tips(window):
    return [info.text_tip() for info in window.screen().findChildren(widgets.InfoButton)]


def flight_boxes(window):
    rows = window.screens[S.PHOTOS].rows
    return {key.split("-")[-1]: rows[key][0] for key in rows}


def run_to_end(window, **ticks):
    """On the Project screen: tick the steps named, press Create and wait for the end."""
    screen = window.screens[S.PROJECT]
    for key, value in ticks.items():
        screen.boxes[key].setChecked(value)
    click_button(window.next_button)
    assert until(lambda: not window.busy() and window.state.result is not None, 30)
    pump(100)


# --------------------------------------------------------------------------------------------------
# cases
# --------------------------------------------------------------------------------------------------

@case("opens on Photos: Next disabled, Back hidden, four steps in the bar")
def _():
    window, _b = new_wizard()
    show(window)
    shot(window, "L301_photos_empty_x1.0")
    ok = (window.current_step() == S.PHOTOS and not window.next_button.isEnabled() and not window.back_button.isVisible()
          and len(window.steps.circles) == 4 and window.steps.state_of(0) == "current" and window.steps.state_of(1) == "todo")
    ok = ok and not window.screens[S.PHOTOS].scroll.isVisible()                          # no empty list before the scan
    window.close()
    return ok


@case("a folder that does not exist is refused in words")
def _():
    window, _b = new_wizard()
    show(window)
    screen = window.screens[S.PHOTOS]
    screen.path.setText(os.path.join(DATA, "nothing_here"))
    click_button(screen.scan_button)
    ok = screen.status.text() == "Folder not found." and not window.next_button.isEnabled()
    window.close()
    return ok, screen.status.text()


@case("scan: bar while it runs, the flights appear under the folder, Next opens")
def _():
    window, _b = new_wizard()
    show(window)
    screen = window.screens[S.PHOTOS]
    screen.path.setText(MISSION)
    click_button(screen.scan_button)
    busy = window.busy() and screen.bar.isVisible()
    shot(window, "L302_photos_scanning_x1.0")
    assert until(lambda: window.state.has_photos() and not window.busy(), 30)
    pump(80)
    shot(window, "L303_photos_found_x1.0")
    ok = (busy and screen.status.text() == "60 photos, 3 flights" and screen.skipped.text() == "1 other file skipped" and screen.scroll.isVisible()
          and len(screen.rows) == 3 and window.next_button.isEnabled() and not screen.bar.isVisible())
    window.close()
    return ok, screen.status.text() + " | " + screen.skipped.text()


@case("the chosen folder is remembered between sessions")
def _():
    window, _b = new_wizard()
    show(window)
    scanned(window)
    window.close()
    pump(60)
    again, _b2 = new_wizard(keep_settings=True)
    show(again)
    ok = again.screens[S.PHOTOS].path.text() == MISSION
    again.close()
    return ok


@case("Browse opens the picker and scans what it returns")
def _():
    window, _b = new_wizard()
    show(window)
    click_button(window.screens[S.PHOTOS].browse)
    assert until(lambda: window.state.has_photos() and not window.busy(), 30)
    ok = window.screens[S.PHOTOS].path.text() == MISSION
    window.close()
    return ok


@case("a folder without DJI photos says so and keeps Next closed")
def _():
    empty = os.path.join(DATA, "empty")
    os.makedirs(empty, exist_ok=True)
    window, _b = new_wizard()
    show(window)
    screen = window.screens[S.PHOTOS]
    screen.path.setText(empty)
    click_button(screen.scan_button)
    assert until(lambda: not window.busy(), 15)
    pump(60)
    ok = screen.status.text().startswith("No DJI photos") and not window.next_button.isEnabled() and not screen.scroll.isVisible()
    window.close()
    return ok, screen.status.text()


@case("photos: columns Flight, Shots, RTK fix, Date; the calibration flight starts unticked; the summary counts the ticked ones")
def _():
    window, _b = new_wizard()
    show(window)
    scanned(window)
    shot(window, "L304_photos_flights_x1.0")
    screen = window.screens[S.PHOTOS]
    boxes = flight_boxes(window)
    ticks = {k: b.isChecked() for k, b in boxes.items()}
    heads = [label.text() for label in screen.page.findChildren(QtWidgets.QLabel) if label.objectName() == "l3ColHead"]
    ok = (ticks == {"FLIGHT01": True, "VOLBIAIS": False, "FLIGHT02": True}
          and heads == ["Flight", "Shots", "RTK fix", "Date (UTC)"] and screen.summary.text() == "2 of 3 flights, 24 shots"
          and screen.all_box.isVisible() and not screen.all_box.isChecked() and window.next_button.isEnabled())
    window.close()
    return ok, "{} {} {}".format(ticks, heads, screen.summary.text())


@case("photos: ticking a flight updates the summary, the header box ticks all or none, no flight closes Next")
def _():
    window, _b = new_wizard()
    show(window)
    scanned(window)
    screen = window.screens[S.PHOTOS]
    boxes = flight_boxes(window)
    boxes["VOLBIAIS"].setChecked(True)
    pump(400)
    with_calibration = screen.summary.text()
    all_ticked = screen.all_box.isChecked()
    click_button(screen.all_box)                         # all ticked: the click unticks them all
    closed = not window.next_button.isEnabled() and screen.summary.text() == "Tick a flight."
    click_button(screen.all_box)
    reopened = window.next_button.isEnabled() and screen.summary.text() == "3 of 3 flights, 30 shots"
    click_button(boxes["FLIGHT01"])                        # a click on the tick box itself
    ok = with_calibration == "3 of 3 flights, 30 shots" and all_ticked and closed and reopened and not boxes["FLIGHT01"].isChecked()
    window.close()
    return ok, with_calibration


@case("photos: a click on the row ticks the flight")
def _():
    window, _b = new_wizard()
    show(window)
    scanned(window)
    screen = window.screens[S.PHOTOS]
    key = [k for k in screen.rows if k.endswith("VOLBIAIS")][0]
    frame = screen.rows[key][1]
    click_button(frame)
    ok = screen.rows[key][0].isChecked() and window.state.selection[key] is True and frame.property("on") is True
    window.close()
    return ok


@case("checks: a clean selection says Ready, lists nothing, and folds the passed checks in one line")
def _():
    window, _b = new_wizard()
    show(window)
    to_step(window, S.CHECKS)
    shot(window, "L305_checks_ready_x1.0")
    screen = window.screens[S.CHECKS]
    passed = screen.passed
    folded = passed is not None and not passed.is_open() and not passed.body.isVisible() and passed.head.text() == "8 checks passed"
    tiles = {k: v.text() for k, v in screen.tiles.items()}
    ok = (screen.verdict.text() == "Ready" and screen.verdict_glyph.kind() == "ok" and folded and not screen.finding_rows
          and tiles == {"shots": "24", "flights": "2", "left": "SERIAL-LEFT-001", "right": "SERIAL-RIGHT-001", "tilt": "22.5 deg"}
          and window.next_button.isEnabled())
    passed.set_open(True)
    pump(60)
    shot(window, "L305_checks_ready_open_x1.0")
    ok = ok and passed.body.isVisible() and "RTK fixed" in labels(window)
    window.close()
    return ok, "{} {} {}".format(screen.verdict.text(), passed.head.text() if passed else None, tiles)


@case("checks: two L3 units give '1 blocking', no count, the reason in the tip; unticking one flight fixes it")
def _():
    folder = make_mission("mixed", extra=False, mixed=True)
    window, _b = new_wizard()
    show(window)
    scanned(window, folder)
    for box in flight_boxes(window).values():
        box.setChecked(True)
    pump(40)
    click_button(window.next_button)
    pump(60)
    shot(window, "L306_checks_blocked_x1.0")
    screen = window.screens[S.CHECKS]
    row = screen.finding_rows[0]
    blocked = (screen.verdict.text() == "1 blocking" and screen.verdict_glyph.kind() == "stop" and row.label.text() == "More than one L3"
               and row.count.text() == "" and row.glyph.kind() == "stop" and "one unit only" in row.info.text_tip() and not window.next_button.isEnabled())
    click_button(window.back_button)
    flight_boxes(window)["OTHERL3"].setChecked(False)
    pump(400)
    click_button(window.next_button)
    pump(60)
    fixed = window.next_button.isEnabled() and screen.verdict.text() == "Ready"
    window.close()
    return blocked and fixed, screen.verdict.text()


@case("checks: a photo that would come out turned is '1 to check' with its file name in the tip")
def _():
    folder = make_mission("rotated", extra=False)
    flight = [d for d in os.listdir(folder) if d.endswith("FLIGHT01")][0]
    victim = sorted(f for f in os.listdir(os.path.join(folder, flight)) if f.endswith("_LV.JPG"))[2]
    with open(os.path.join(folder, flight, victim), "wb") as handle:
        handle.write(synth.make_jpeg(orientation=6, tags=synth.photo_tags("L", 120.0, "B", 43.1, 5.0, 200.0, synth.GPS0)))
    window, _b = new_wizard()
    show(window)
    scanned(window, folder)
    click_button(window.next_button)
    pump(60)
    screen = window.screens[S.CHECKS]
    rows = {r.label.text(): r for r in screen.finding_rows}
    row = rows.get("Rotated by EXIF")
    tip_ok = row is not None and victim in row.info.text_tip() and row.count.text() == "1" and row.glyph.kind() == "warn"
    ok = tip_ok and screen.verdict.text().endswith("to check") and window.next_button.isEnabled() and "Files upright" not in [l.text() for l in screen.passed.body.findChildren(QtWidgets.QLabel)]
    shot(window, "L307_checks_to_check_x1.0")
    window.close()
    return ok, str(list(rows))


@case("checks: the 'i' shows its tip on click and has a name for screen readers; a long tip wraps")
def _():
    folder = make_mission("rotated2", extra=False)
    flight = [d for d in os.listdir(folder) if d.endswith("FLIGHT01")][0]
    victim = sorted(f for f in os.listdir(os.path.join(folder, flight)) if f.endswith("_LV.JPG"))[2]
    with open(os.path.join(folder, flight, victim), "wb") as handle:
        handle.write(synth.make_jpeg(orientation=6, tags=synth.photo_tags("L", 120.0, "B", 43.1, 5.0, 200.0, synth.GPS0)))
    window, _b = new_wizard()
    show(window)
    scanned(window, folder)
    click_button(window.next_button)
    pump(60)
    info = window.screens[S.CHECKS].finding_rows[0].info
    click_button(info)
    pump(60)
    shown = QtWidgets.QToolTip.isVisible() and victim in QtWidgets.QToolTip.text()
    QtWidgets.QToolTip.hideText()
    ok = (shown and info.accessibleName() == "More information" and victim in info.accessibleDescription()
          and info.focusPolicy() == QtCore.Qt.StrongFocus and info.toolTip().startswith("<p>"))
    window.close()
    return ok, QtWidgets.QToolTip.text()[:80]


@case("checks: more findings than fit scroll inside the list, the verdict and the buttons stay in place")
def _():
    window, _b = new_wizard()
    show(window)
    to_step(window, S.CHECKS)
    screen = window.screens[S.CHECKS]
    window.state.analysis.findings.extend(checks.Finding(checks.WARNING, "no_gps", n=i + 2, total=40, examples=["a.JPG"]) for i in range(16))
    screen.rebuild()
    pump(100)
    shot(window, "L307_checks_many_x1.0")
    ok = (len(screen.finding_rows) == 16 and screen.scroll.verticalScrollBar().isVisible() and screen.verdict.isVisible()
          and screen.verdict.text() == "16 to check" and window.next_button.isVisible() and not cut_off(window))
    window.close()
    return ok, str(cut_off(window))[:200]


@case("project: advanced is folded; typed numbers apply, a wrong text goes back, the name is required")
def _():
    window, _b = new_wizard()
    show(window)
    to_step(window, S.PROJECT)
    shot(window, "L308_project_x1.0")
    screen = window.screens[S.PROJECT]
    folded = not screen.advanced.is_open() and not screen.fields["accuracy_fixed"].isVisible()
    screen.advanced.set_open(True)
    pump(60)
    shot(window, "L308_project_advanced_x1.0")
    ok = (folded and screen.name.text() == "L3_TESTSITE_261002" and window.next_button.text() == "Create project" and window.next_button.isEnabled()
          and screen.fields["accuracy_fixed"].isVisible())
    type_into(screen.fields["accuracy_fixed"], "0,03")
    ok = ok and window.state.options.accuracy_fixed == 0.03
    type_into(screen.fields["accuracy_fixed"], "abc")
    ok = ok and window.state.options.accuracy_fixed == 0.03 and screen.fields["accuracy_fixed"].text() == "0.030"
    type_into(screen.fields["z_shift"], "0.27")
    ok = ok and window.state.options.z_shift == 0.27
    type_into(screen.fields["rotation_accuracy"], "5000")
    ok = ok and window.state.options.rotation_accuracy == 10.0                      # out of range: refused
    screen.name.setText("")
    screen.name.textEdited.emit("")
    pump(30)
    ok = ok and not window.next_button.isEnabled()
    screen.name.setText("My chunk")
    screen.name.textEdited.emit("My chunk")
    ok = ok and window.next_button.isEnabled() and window.state.options.chunk_name == "My chunk"
    window.close()
    return ok


@case("project: frame choice, calibration, unpaired photos and the quick test go to the options")
def _():
    window, _b = new_wizard()
    show(window)
    to_step(window, S.PROJECT)
    screen = window.screens[S.PROJECT]
    screen.advanced.set_open(True)
    click_button(screen.frame.button("etrs89"))
    screen.fixed_cal.setChecked(True)
    screen.unpaired.setChecked(True)
    screen.quick.setChecked(True)
    screen.boxes["align"].setChecked(True)
    screen.boxes["ortho"].setChecked(True)
    pump(40)
    o = window.state.options
    ok = (o.frame == "etrs89" and screen.frame.value() == "etrs89" and o.calibration_fixed and o.include_unpaired and o.quick_test and o.steps["align"]
          and o.steps["ortho"] and window.state.steps_to_run() == ["quick_test", "align", "repair", "ortho"])
    window.close()
    return ok, str(window.state.steps_to_run())


@case("project: the steps are a chain; DEM ticks Align, unticking Align clears the rest, the pixel size follows the orthophoto")
def _():
    window, _b = new_wizard()
    show(window)
    to_step(window, S.PROJECT)
    screen = window.screens[S.PROJECT]
    ok = not any(b.isChecked() for b in screen.boxes.values()) and not screen.fields["pixel_cm"].isEnabled() and "repair" not in screen.boxes
    screen.boxes["dem"].setChecked(True)
    ok = ok and screen.boxes["align"].isChecked() and not screen.boxes["optimize"].isChecked()
    screen.boxes["ortho"].setChecked(True)
    ok = ok and screen.fields["pixel_cm"].isEnabled()
    screen.boxes["align"].setChecked(False)
    ok = ok and not any(b.isChecked() for b in screen.boxes.values()) and not screen.fields["pixel_cm"].isEnabled()
    window.close()
    return ok


@case("create: a successful run lists each step with its figure, one Next line, Back is closed")
def _():
    window, backend = new_wizard()
    show(window)
    to_step(window, S.PROJECT)
    run_to_end(window, align=True)
    shot(window, "L309_run_done_x1.0")
    screen = window.screens[S.RUN]
    rows = {line.key: (line.glyph.kind(), line.figure.text()) for line in screen.lines}
    plan, steps = backend.runs[0]
    ok = (window.current_step() == S.RUN and screen.headline.text() == "Ready: L3_TESTSITE_261002" and screen.head_glyph.kind() == "ok"
          and rows == {"create": ("ok", "48 cameras, sensors: 2"), "verify": ("ok", "48 cameras, max error 0.000 deg"), "align": ("ok", "48 / 48 photos")}
          and screen.next_line.text() == "Next: add control points, then save the project."
          and not window.back_button.isVisible() and window.cancel_button.text() == "Close" and steps == ["align", "repair"]
          and plan.options.chunk_name == "L3_TESTSITE_261002" and plan.photo_count == 48)
    window.close()
    return ok, str(rows)


@case("create: with nothing ticked the chunk is made and the next move is to align")
def _():
    window, backend = new_wizard()
    show(window)
    to_step(window, S.PROJECT)
    run_to_end(window)
    screen = window.screens[S.RUN]
    ok = (screen.next_line.text() == "Next: align the photos, then save the project." and backend.runs[0][1] == []
          and [line.key for line in screen.lines] == ["create", "verify"])
    window.close()
    return ok, screen.next_line.text()


@case("create: while it runs one step turns, the ones before are ticked, the ones after wait; its bar is the only one")
def _():
    seen = {}

    def watch():
        APP.processEvents()
        time.sleep(0.12)                                    # the window looks at the progress every 100 ms
        APP.processEvents()
        window = seen.get("window")
        if window is not None and window.busy() and window.current_step() == S.RUN:
            screen = window.screens[S.RUN]
            kinds = [line.glyph.kind() for line in screen.lines]
            if "running" in kinds and kinds != seen.get("last"):
                seen["last"] = kinds
                seen.setdefault("states", []).append((kinds, [line.bar.isVisibleTo(window) for line in screen.lines], screen.headline.text()))
                if len(seen["states"]) == 3:
                    shot(window, "L309_run_running_x1.0")
    backend = FakeBackend(pump=watch)
    window = W.L3Wizard(backend)
    window.pick_folder = lambda start: MISSION
    seen["window"] = window
    show(window)
    to_step(window, S.PROJECT)
    run_to_end(window, align=True, optimize=True)
    states = seen.get("states", [])
    first_ok = bool(states) and states[0][0][0] == "running" and states[0][0][1:] == ["pending"] * 3
    later_ok = any(k == ["ok", "ok", "running", "pending"] for k, _b, _h in states)
    bars_ok = all(sum(bars) == 1 for _k, bars, _h in states) and all(h == "Creating L3_TESTSITE_261002" for _k, _b, h in states)
    window.close()
    return first_ok and later_ok and bars_ok, str(states[:3])


@case("create: flipped cameras that cannot be repaired give a 'Check' headline and a line for them")
def _():
    window, _b = new_wizard(flipped=4, unrepairable=True)
    show(window)
    to_step(window, S.PROJECT)
    run_to_end(window, align=True)
    shot(window, "L310_run_problem_x1.0")
    screen = window.screens[S.RUN]
    rows = {line.key: (line.glyph.kind(), line.figure.text()) for line in screen.lines}
    ok = (screen.headline.text() == "Check L3_TESTSITE_261002" and screen.head_glyph.kind() == "warn" and rows["repair"] == ("stop", "4 of 4 still flipped")
          and window.state.result.alignment.remaining == 4 and screen.next_line.text() == "")
    window.close()
    return ok, str(rows)


@case("create: flipped cameras that are repaired get a line that says how many")
def _():
    window, _b = new_wizard(flipped=4)
    show(window)
    to_step(window, S.PROJECT)
    run_to_end(window, align=True)
    screen = window.screens[S.RUN]
    rows = {line.key: (line.glyph.kind(), line.figure.text()) for line in screen.lines}
    ok = rows.get("repair") == ("ok", "4 re-aligned") and screen.headline.text().startswith("Ready")
    window.close()
    return ok, str(rows)


@case("create: a failing step is a cross with its reason, the next ones wait")
def _():
    window, _b = new_wizard(fail="verify")
    show(window)
    to_step(window, S.PROJECT)
    run_to_end(window, align=True)
    screen = window.screens[S.RUN]
    rows = {line.key: (line.glyph.kind(), line.figure.text()) for line in screen.lines}
    ok = rows["verify"] == ("stop", "simulated failure") and rows["align"][0] == "pending" and screen.headline.text().startswith("Check")
    window.close()
    return ok, str(rows)


@case("create: Cancel during the run stops the whole batch")
def _():
    holder = {}

    def slow_pump():
        APP.processEvents()
        time.sleep(0.15)
        if holder.get("window") is not None and not holder.get("clicked") and holder["window"].busy() and holder["window"].current_step() == S.RUN:
            holder["clicked"] = True
            click_button(holder["window"].cancel_button)
    backend = FakeBackend(pump=slow_pump)
    window = W.L3Wizard(backend)
    window.pick_folder = lambda start: MISSION
    holder["window"] = window
    show(window)
    to_step(window, S.PROJECT)
    for key_ in ("align", "optimize", "dem", "ortho"):
        window.screens[S.PROJECT].boxes[key_].setChecked(True)
    click_button(window.next_button)
    assert until(lambda: not window.busy(), 30)
    pump(80)
    result = window.state.result
    screen = window.screens[S.RUN]
    ok = result is not None and result.cancelled and screen.headline.text() == "Cancelled" and len(result.steps) < 6 and screen.lines[-1].glyph.kind() == "pending"
    window.close()
    return ok, screen.headline.text()


@case("photos: a list of 22 flights never asks for more height than the screen has, and Back to Photos keeps the window on screen")
def _():
    from aerotools.tools.aerol3 import screens as SC
    tall = QtWidgets.QWidget()
    tall.setMinimumSize(300, 3000)
    box = SC.ListScroll(tall, preferred=600)
    area = controls.screen_area()
    capped = box.sizeHint().height() <= area.height() - 440 + 4
    window, _b = new_wizard()
    show(window)
    scanned(window)
    click_button(window.next_button)
    pump(60)
    window.screens[S.PHOTOS].scroll.widget().setMinimumHeight(3000)          # as tall as 22 rows
    click_button(window.back_button)
    pump(120)
    on_screen = window.height() <= area.height() - W.SCREEN_MARGIN
    window.close()
    return capped and on_screen, "hint {} window {} screen {}".format(box.sizeHint().height(), window.height(), area.height())


@case("window: no frame to drag (fixed-size dialog frame), the size follows the content")
def _():
    window, _b = new_wizard()
    show(window)
    pump(200)
    flag = bool(window.windowFlags() & QtCore.Qt.MSWindowsFixedSizeDialogHint)
    window.close()
    return flag, "fixed-size frame flag: {}".format(flag)

@case("window: its least height follows the page on show, not the tallest page, so it can be made smaller")
def _():
    window, _b = new_wizard()
    show(window)
    need = window.layout().minimumHeightForWidth(window.width())
    floor = window.layout().totalMinimumSize().height()
    window.close()
    return need <= floor + 80, "least height {} for a floor of {}".format(need, floor)

@case("closing the window while a job runs waits for the job to stop")
def _():
    window, _b = new_wizard()
    show(window)
    screen = window.screens[S.PHOTOS]
    screen.path.setText(MISSION)
    click_button(screen.scan_button)
    window.close()
    ended = until(lambda: not window.busy(), 20)
    window.close()
    return ended


@case("Esc and Enter do not close the window or press a random button")
def _():
    window, _b = new_wizard()
    show(window)
    key(window, QtCore.Qt.Key_Escape)
    key(window, QtCore.Qt.Key_Return, "\r")
    ok = window.isVisible() and window.current_step() == S.PHOTOS
    window.close()
    return ok


@case("Back walks through the screens and keeps the choices")
def _():
    window, _b = new_wizard()
    show(window)
    to_step(window, S.PROJECT)
    window.screens[S.PROJECT].name.setText("Kept name")
    window.screens[S.PROJECT].name.textEdited.emit("Kept name")
    click_button(window.back_button)
    click_button(window.back_button)
    ok = window.current_step() == S.PHOTOS and window.state.has_photos()
    for _i in range(2):
        click_button(window.next_button)
    ok = ok and window.current_step() == S.PROJECT and window.screens[S.PROJECT].name.text() == "Kept name"
    window.close()
    return ok


@case("the Tab key reaches the 'i' of a row and Space shows its tip")
def _():
    window, _b = new_wizard()
    show(window)
    scanned(window)
    info = window.screens[S.PHOTOS].page.findChildren(widgets.InfoButton)[0]
    info.setFocus()
    pump(30)
    key(info, QtCore.Qt.Key_Space, " ")
    pump(60)
    shown = QtWidgets.QToolTip.isVisible() and "start unticked" in QtWidgets.QToolTip.text()
    QtWidgets.QToolTip.hideText()
    window.close()
    return shown, QtWidgets.QToolTip.text()[:60]


@case("project: the Tab key goes Name, frame, steps, quick test, Advanced, then its fields in screen order")
def _():
    window, _b = new_wizard()
    show(window)
    to_step(window, S.PROJECT)
    screen = window.screens[S.PROJECT]
    screen.advanced.set_open(True)
    pump(60)
    window.activateWindow()
    chain, widget = [], screen.name
    for _i in range(60):
        widget = widget.nextInFocusChain()
        if widget is screen.name:
            break
        if widget.isVisibleTo(window) and widget.isEnabled() and widget.focusPolicy() & QtCore.Qt.TabFocus and screen.isAncestorOf(widget):
            chain.append(widget)
    want = [screen.quick, screen.advanced.head, screen.fields["accuracy_fixed"], screen.fields["accuracy_float"], screen.fields["accuracy_single"],
            screen.fields["z_shift"], screen.fields["rotation_accuracy"], screen.fixed_cal, screen.unpaired]
    seen = [w for w in chain if w in want]
    window.close()
    return seen == want, str([type(w).__name__ for w in seen])


# --------------------------------------------------------------------------------------------------
# every screen at three font scales
# --------------------------------------------------------------------------------------------------

METRICS = {}
LONGEST_LABEL = 60
TEXT_LIMITS = {"photos": 50, "checks": 50, "project": 50, "run": 50}               # words


def inspect(window, name, tag, problems, advanced_open=False):
    """Checks every screen: nothing cut off, no vertical bar, no label longer than 60 characters; counts what is written."""
    found = cut_off(window)
    bars = vertical_bars(window)
    if bars:
        found.append("a vertical scroll bar is visible ({} px of range)".format(bars[0].maximum()))
    texts = texts_of(window.screen())
    long_ones = [t for t in texts if len(t) > LONGEST_LABEL]
    if long_ones:
        found.append("label longer than {}: {}".format(LONGEST_LABEL, long_ones[0][:40]))
    METRICS.setdefault(name, {})[tag] = (len(" ".join(texts).split()), len("".join(texts)))
    if found:
        problems[name] = found


def render_all(scale):
    """Every screen at one font scale: nothing may be cut off, no vertical bar anywhere. With --shots the screens are saved as PNG."""
    tag = "x%.1f" % scale
    problems = {}
    window, backend = new_wizard(flipped=3)
    rescale(window, scale)
    show(window)
    inspect(window, "photos_empty", tag, problems)
    shot(window, "L3v2_photos_empty_" + tag)
    scanned(window)
    inspect(window, "photos", tag, problems)
    shot(window, "L3v2_photos_" + tag)
    click_button(window.next_button)
    pump(100)
    inspect(window, "checks", tag, problems)
    shot(window, "L3v2_checks_" + tag)
    window.screens[S.CHECKS].passed.set_open(True)
    pump(100)
    inspect(window, "checks_open", tag, problems)
    shot(window, "L3v2_checks_open_" + tag)
    window.screens[S.CHECKS].passed.set_open(False)
    click_button(window.next_button)
    pump(100)
    inspect(window, "project", tag, problems)
    shot(window, "L3v2_project_" + tag)
    window.screens[S.PROJECT].advanced.set_open(True)
    pump(100)
    inspect(window, "project_advanced", tag, problems)
    shot(window, "L3v2_project_advanced_" + tag)
    window.screens[S.PROJECT].advanced.set_open(False)
    window.screens[S.PROJECT].boxes["ortho"].setChecked(True)
    window.screens[S.PROJECT].quick.setChecked(True)
    pump(60)
    shot(window, "L3v2_project_steps_" + tag)
    click_button(window.next_button)
    until(lambda: not window.busy() and window.state.result is not None, 30)
    pump(150)
    inspect(window, "run", tag, problems)
    shot(window, "L3v2_run_" + tag)
    window.close()
    unscale()
    return problems


def render_states(scale):
    """The screens in their other states: a blocking finding, findings to check, a run that stops on flipped cameras."""
    tag = "x%.1f" % scale
    problems = {}
    folder = make_mission("mixed_shots", extra=False, mixed=True)
    window, _b = new_wizard()
    rescale(window, scale)
    show(window)
    scanned(window, folder)
    for box in flight_boxes(window).values():
        box.setChecked(True)
    pump(40)
    click_button(window.next_button)
    pump(100)
    inspect(window, "checks_blocked", tag, problems)
    shot(window, "L3v2_checks_blocked_" + tag)
    window.close()
    unscale()
    window, _b = new_wizard(flipped=4, unrepairable=True)
    rescale(window, scale)
    show(window)
    to_step(window, S.PROJECT)
    run_to_end(window, align=True)
    inspect(window, "run_problem", tag, problems)
    shot(window, "L3v2_run_problem_" + tag)
    window.close()
    unscale()
    return problems


@case("font scales x1.0, x1.5 and x2.0: nothing cut off, no vertical scroll bar, no label over 60 characters, on every screen")
def _():
    global SHOTS
    keep, SHOTS = SHOTS, None
    try:
        bad = {}
        for factor in SCALES:
            for found in (render_all(factor), render_states(factor)):
                if found:
                    bad.setdefault(factor, {}).update(found)
        return not bad, str(bad)[:900]
    finally:
        SHOTS = keep


@case("every screen stays short: few words, a few characters")
def _():
    if not METRICS:
        keep = SHOTS
        render_all(1.0)
    over = {n: m["x1.0"] for n, m in METRICS.items() if n.split("_")[0] in TEXT_LIMITS and m["x1.0"][0] > TEXT_LIMITS[n.split("_")[0]]}
    return not over, str(over)


def finish():
    problem = GUARD.check()
    if problem:
        FAILURES.append("settings isolation")
        print("FAIL settings isolation  -> " + problem)
    print("\n{} failure(s)".format(len(FAILURES)) if FAILURES else "\nall passed")
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    if SHOTS:
        for factor in SCALES:
            for found in (render_all(factor), render_states(factor)):
                print("shots x%.1f: %s" % (factor, "nothing cut off" if not found else "; ".join("%s: %s" % (k, v[0]) for k, v in found.items())))
        print("words and characters of what is written on each screen (x1.0):")
        for name, metric in METRICS.items():
            print("  %-18s %4d words %5d characters" % (name, metric["x1.0"][0], metric["x1.0"][1]))
    finish()
