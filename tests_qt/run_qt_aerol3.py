"""Qt self-test of the Aero-L3 wizard: real widgets, real mouse and key events, a fake backend, synthetic L3 photos. No Metashape needed.

    python -B tests_qt\\run_qt_aerol3.py                 (PySide6 here, also meant for Metashape's PySide2)
    python -B tests_qt\\run_qt_aerol3.py --shots DIR     also writes a PNG of every screen at three font scales into DIR

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
from aerotools.tools.aerol3 import checks, plan as plan_module, state as S, wizard as W  # noqa: E402
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
    screen = window.screens[S.FOLDER]
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


def labels(window):
    return [label.text() for label in window.screen().findChildren(QtWidgets.QLabel)]


# --------------------------------------------------------------------------------------------------
# cases
# --------------------------------------------------------------------------------------------------

@case("opens on the folder step: Next disabled, Back hidden, five steps in the bar")
def _():
    window, _b = new_wizard()
    show(window)
    shot(window, "L301_folder_empty_x1.0")
    ok = (window.current_step() == S.FOLDER and not window.next_button.isEnabled() and not window.back_button.isVisible()
          and len(window.steps.circles) == 5 and window.steps.state_of(0) == "current" and window.steps.state_of(1) == "todo")
    window.close()
    return ok


@case("a folder that does not exist is refused in words")
def _():
    window, _b = new_wizard()
    show(window)
    screen = window.screens[S.FOLDER]
    screen.path.setText(os.path.join(DATA, "nothing_here"))
    click_button(screen.scan_button)
    ok = "does not exist" in screen.status.text() and not window.next_button.isEnabled()
    window.close()
    return ok, screen.status.text()


@case("scan: progress bar while it runs, a summary when it ends, Next opens")
def _():
    window, _b = new_wizard()
    show(window)
    screen = window.screens[S.FOLDER]
    screen.path.setText(MISSION)
    click_button(screen.scan_button)
    busy = window.busy()
    shot(window, "L302_folder_scanning_x1.0")
    assert until(lambda: window.state.has_photos() and not window.busy(), 30)
    pump(80)
    shot(window, "L303_folder_found_x1.0")
    text = screen.status.text()
    ok = busy and "photos found in 3 flight" in text and window.next_button.isEnabled() and "1 JPEG" in screen.detail.text()
    window.close()
    return ok, text + " | " + screen.detail.text()


@case("the chosen folder is remembered between sessions")
def _():
    window, _b = new_wizard()
    show(window)
    scanned(window)
    window.close()
    pump(60)
    again, _b2 = new_wizard(keep_settings=True)
    show(again)
    ok = again.screens[S.FOLDER].path.text() == MISSION
    again.close()
    return ok


@case("Choose a folder opens the picker and scans what it returns")
def _():
    window, _b = new_wizard()
    show(window)
    click_button(window.screens[S.FOLDER].browse)
    assert until(lambda: window.state.has_photos() and not window.busy(), 30)
    ok = window.screens[S.FOLDER].path.text() == MISSION
    window.close()
    return ok


@case("a folder without DJI photos says so and keeps Next closed")
def _():
    empty = os.path.join(DATA, "empty")
    os.makedirs(empty, exist_ok=True)
    window, _b = new_wizard()
    show(window)
    screen = window.screens[S.FOLDER]
    screen.path.setText(empty)
    click_button(screen.scan_button)
    assert until(lambda: not window.busy(), 15)
    pump(60)
    ok = "No DJI photo" in screen.status.text() and not window.next_button.isEnabled()
    window.close()
    return ok, screen.status.text()


@case("flights: table, tiles, calibration flight starts unticked")
def _():
    window, _b = new_wizard()
    show(window)
    to_step(window, S.FLIGHTS)
    shot(window, "L304_flights_x1.0")
    screen = window.screens[S.FLIGHTS]
    table = screen.table
    ticks = {table.item(r, 0).text().split("_")[-1]: table.item(r, 0).checkState() == QtCore.Qt.Checked for r in range(table.rowCount())}
    tiles = {k: v[0].text() for k, v in screen.tiles.items()}
    ok = (table.rowCount() == 3 and ticks == {"TESTSITE-FLIGHT01": True, "TESTSITE-VOLBIAIS": False, "TESTSITE-FLIGHT02": True}
          and tiles["photos"] == "48" and tiles["flights"] == "2" and tiles["pairs"] == "24" and tiles["rtk"] == "100 %"
          and window.next_button.isEnabled())
    window.close()
    return ok, "{} {}".format(ticks, tiles)


@case("flights: ticking a flight updates the numbers, unticking all closes Next")
def _():
    window, _b = new_wizard()
    show(window)
    to_step(window, S.FLIGHTS)
    screen = window.screens[S.FLIGHTS]
    item = [screen.table.item(r, 0) for r in range(3) if screen.table.item(r, 0).text().endswith("VOLBIAIS")][0]
    item.setCheckState(QtCore.Qt.Checked)
    pump(40)
    with_calibration = screen.tiles["photos"][0].text()
    click_button(screen.none_button)
    closed = not window.next_button.isEnabled() and screen.tiles["photos"][0].text() == "-"
    click_button(screen.all_button)
    reopened = window.next_button.isEnabled()
    window.close()
    return with_calibration == "60" and closed and reopened, with_calibration


@case("checks: a clean selection says all passed, with reassurance about flips")
def _():
    window, _b = new_wizard()
    show(window)
    to_step(window, S.CHECKS)
    shot(window, "L305_checks_x1.0")
    texts = " ".join(labels(window))
    ok = ("No blocking problem" in texts or "All checks passed" in texts) and "no picture is flipped or turned" in texts and window.next_button.isEnabled()
    ok = ok and "What will be created" in texts and "Left lens (LV)" in texts and "focal 5000.0 px" in texts
    window.close()
    return ok, texts[:300]


@case("checks: two different L3 units block the project and say why; unticking one flight fixes it")
def _():
    folder = make_mission("mixed", extra=False, mixed=True)
    window, _b = new_wizard()
    show(window)
    scanned(window, folder)
    click_button(window.next_button)
    screen = window.screens[S.FLIGHTS]
    for r in range(screen.table.rowCount()):
        screen.table.item(r, 0).setCheckState(QtCore.Qt.Checked)
    pump(40)
    click_button(window.next_button)
    pump(60)
    shot(window, "L306_checks_blocked_x1.0")
    texts = " ".join(labels(window))
    blocked = "more than one L3" in texts and "must be fixed" in texts and not window.next_button.isEnabled()
    click_button(window.back_button)
    for r in range(screen.table.rowCount()):
        if screen.table.item(r, 0).text().endswith("OTHERL3"):
            screen.table.item(r, 0).setCheckState(QtCore.Qt.Unchecked)
    pump(40)
    click_button(window.next_button)
    pump(60)
    fixed = window.next_button.isEnabled()
    window.close()
    return blocked and fixed, texts[:200]


@case("checks: a photo that would come out turned is shown with its file name")
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
    click_button(window.next_button)
    pump(60)
    texts = " ".join(labels(window))
    ok = "EXIF rotation other than 1" in texts and victim in texts and window.next_button.isEnabled()
    shot(window, "L307_checks_rotated_x1.0")
    window.close()
    return ok, texts[:300]


@case("project settings: typed numbers apply, a wrong text goes back, the name is required")
def _():
    window, _b = new_wizard()
    show(window)
    to_step(window, S.OPTIONS)
    shot(window, "L308_options_x1.0")
    screen = window.screens[S.OPTIONS]
    ok = screen.name.text() == "L3_TESTSITE_261002" and window.next_button.text() == "Create the project" and window.next_button.isEnabled()
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


@case("project settings: frame, calibration, partner photos, steps and the quick test go to the options")
def _():
    window, _b = new_wizard()
    show(window)
    to_step(window, S.OPTIONS)
    screen = window.screens[S.OPTIONS]
    screen.frame.setCurrentIndex(1)
    screen.fixed_cal.setChecked(True)
    screen.unpaired.setChecked(True)
    screen.quick.setChecked(True)
    screen.boxes["align"].setChecked(True)
    screen.boxes["ortho"].setChecked(True)
    pump(40)
    o = window.state.options
    ok = (o.frame == "etrs89" and o.calibration_fixed and o.include_unpaired and o.quick_test and o.steps["align"] and o.steps["ortho"]
          and window.state.steps_to_run() == ["quick_test", "align", "repair", "ortho"])
    window.close()
    return ok, str(window.state.steps_to_run())


@case("create: a successful run shows each step and what to do next, Back is closed")
def _():
    window, backend = new_wizard()
    show(window)
    to_step(window, S.OPTIONS)
    window.screens[S.OPTIONS].boxes["align"].setChecked(True)
    click_button(window.next_button)
    assert until(lambda: not window.busy() and window.state.result is not None, 30)
    pump(100)
    shot(window, "L309_run_done_x1.0")
    texts = " ".join(labels(window))
    plan, steps = backend.runs[0]
    ok = (window.current_step() == S.RUN and "is ready" in texts and "Workflow > Align Photos" not in texts and "What to do next" in texts
          and not window.back_button.isVisible() and window.cancel_button.text() == "Close" and steps == ["align", "repair"]
          and plan.options.chunk_name == "L3_TESTSITE_261002" and plan.photo_count == 48)
    window.close()
    return ok, texts[:400]


@case("create: with nothing ticked the project is made and the next move is Align Photos")
def _():
    window, backend = new_wizard()
    show(window)
    to_step(window, S.OPTIONS)
    for box in window.screens[S.OPTIONS].boxes.values():
        box.setChecked(False)
    click_button(window.next_button)
    assert until(lambda: not window.busy() and window.state.result is not None, 30)
    pump(80)
    texts = " ".join(labels(window))
    ok = "Workflow > Align Photos" in texts and backend.runs[0][1] == [] and "has not been saved" in texts
    window.close()
    return ok, texts[:300]


@case("create: flipped cameras that cannot be repaired turn the headline to a problem")
def _():
    window, _b = new_wizard(flipped=4, unrepairable=True)
    show(window)
    to_step(window, S.OPTIONS)
    window.screens[S.OPTIONS].boxes["align"].setChecked(True)
    click_button(window.next_button)
    assert until(lambda: not window.busy() and window.state.result is not None, 30)
    pump(80)
    shot(window, "L310_run_problem_x1.0")
    texts = " ".join(labels(window))
    ok = "needs your attention" in texts and "still flipped" in texts and window.state.result.alignment.remaining == 4
    window.close()
    return ok, texts[-500:]


@case("create: flipped cameras that are repaired are reported as repaired")
def _():
    window, _b = new_wizard(flipped=4)
    show(window)
    to_step(window, S.OPTIONS)
    window.screens[S.OPTIONS].boxes["align"].setChecked(True)
    click_button(window.next_button)
    assert until(lambda: not window.busy() and window.state.result is not None, 30)
    texts = " ".join(labels(window))
    ok = "4 flipped cameras were reset and aligned again" in texts and "is ready" in texts
    window.close()
    return ok, texts[:300]


@case("create: a failing step is shown with its reason")
def _():
    window, _b = new_wizard(fail="verify")
    show(window)
    to_step(window, S.OPTIONS)
    click_button(window.next_button)
    assert until(lambda: not window.busy() and window.state.result is not None, 30)
    texts = " ".join(labels(window))
    ok = "simulated failure" in texts and "needs your attention" in texts
    window.close()
    return ok, texts[:300]


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
    to_step(window, S.OPTIONS)
    for key_ in ("align", "optimize", "dem", "ortho"):
        window.screens[S.OPTIONS].boxes[key_].setChecked(True)
    click_button(window.next_button)
    assert until(lambda: not window.busy(), 30)
    pump(80)
    texts = " ".join(labels(window))
    result = window.state.result
    ok = result is not None and result.cancelled and "Cancelled" in texts and len(result.steps) < 6
    window.close()
    return ok, texts[:300]


@case("closing the window while a job runs waits for the job to stop")
def _():
    window, _b = new_wizard()
    show(window)
    screen = window.screens[S.FOLDER]
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
    ok = window.isVisible() and window.current_step() == S.FOLDER
    window.close()
    return ok


@case("Back walks through the screens and keeps the choices")
def _():
    window, _b = new_wizard()
    show(window)
    to_step(window, S.OPTIONS)
    window.screens[S.OPTIONS].name.setText("Kept name")
    window.screens[S.OPTIONS].name.textEdited.emit("Kept name")
    click_button(window.back_button)
    click_button(window.back_button)
    click_button(window.back_button)
    ok = window.current_step() == S.FOLDER and window.state.has_photos()
    for _i in range(3):
        click_button(window.next_button)
    ok = ok and window.current_step() == S.OPTIONS and window.screens[S.OPTIONS].name.text() == "Kept name"
    window.close()
    return ok


def render_all(scale):
    """Every screen at one font scale: nothing may be cut off. With --shots the screens are saved as PNG."""
    tag = "x%.1f" % scale
    problems = {}
    window, backend = new_wizard(flipped=3)
    rescale(window, scale)
    show(window)
    problems["folder"] = cut_off(window)
    shot(window, "L3_folder_" + tag)
    scanned(window)
    shot(window, "L3_folder_found_" + tag)
    problems["folder_found"] = cut_off(window)
    click_button(window.next_button)
    pump(100)
    problems["flights"] = cut_off(window)
    shot(window, "L3_flights_" + tag)
    click_button(window.next_button)
    pump(100)
    problems["checks"] = cut_off(window)
    shot(window, "L3_checks_" + tag)
    click_button(window.next_button)
    pump(100)
    problems["options"] = cut_off(window)
    shot(window, "L3_options_" + tag)
    window.screens[S.OPTIONS].boxes["align"].setChecked(True)
    window.screens[S.OPTIONS].quick.setChecked(True)
    click_button(window.next_button)
    until(lambda: not window.busy() and window.state.result is not None, 30)
    pump(150)
    problems["run"] = cut_off(window)
    shot(window, "L3_run_" + tag)
    window.close()
    unscale()
    return {k: v for k, v in problems.items() if v}


@case("font scales x1.0, x1.5 and x2.0: no widget is cut off on any of the screens")
def _():
    global SHOTS
    keep, SHOTS = SHOTS, None
    try:
        bad = {}
        for factor in SCALES:
            found = render_all(factor)
            if found:
                bad[factor] = found
        return not bad, str(bad)[:800]
    finally:
        SHOTS = keep


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
            found = render_all(factor)
            print("shots x%.1f: %s" % (factor, "no cut-off widget" if not found else
                                          "; ".join("%s: %d (%s)" % (k, len(v), v[0]) for k, v in found.items())))
    finish()
