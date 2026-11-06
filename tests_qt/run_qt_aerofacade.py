"""Qt self-test of Aero-Facade (the wizard window, its widgets and screens), without Metashape.

Run (no pytest needed), with a Python that has PySide2 or PySide6 and numpy:
    python -B tests_qt\\run_qt_aerofacade.py [--shots FOLDER]

The `FakeBackend` (synthetic building) stands in for Metashape. Settings are isolated (`_isolation`). One PASS / FAIL line per case, a
non-zero exit code when one fails. With `--shots FOLDER` every screen is also rendered to a PNG at the font scales x1.0, x1.5 and x2.0.
"""

import os
import re
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
GUARD = _isolation.Guard("aerotools_qt_fc_")                      # settings of the machine stay untouched
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")   # the offscreen platform finds no font by itself

FAKE = types.ModuleType("Metashape")
FAKE.app = types.SimpleNamespace(version="2.2.1-selftest", document=types.SimpleNamespace(chunk=None),
                                 addMenuItem=lambda *a, **k: None, messageBox=lambda text: None, update=lambda: None)
sys.modules["Metashape"] = FAKE

from aerotools import config, i18n, journal  # noqa: E402
GUARD.pin(config)
from aerotools.qt import QtCore, QtGui, QtWidgets  # noqa: E402
from aerotools.tools.aerofacade import state as S  # noqa: E402
from aerotools.tools.aerofacade import widgets, wizard as W  # noqa: E402
from aerotools.tools.aerofacade.fake_backend import FakeBackend  # noqa: E402
from aerotools.tools.aerofacade.model import Margins  # noqa: E402

from aerotools.tools.aeroexports import controls  # noqa: E402
controls.screen_area = lambda: QtCore.QRect(0, 0, 1920, 1040)       # a Full HD screen (the offscreen one is small and varies)

journal.print = lambda *args, **kwargs: None
i18n.set_lang("en", persist=False)
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
FAILURES = []
SHOTS = None
if "--shots" in sys.argv:
    SHOTS = sys.argv[sys.argv.index("--shots") + 1]
    os.makedirs(SHOTS, exist_ok=True)
OUTPUTS = tempfile.mkdtemp(prefix="aerofacade_out_")


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


ONLY = os.environ.get("AEROFACADE_ONLY", "")                      # run only the cases whose name contains this text


def case(name):
    def wrap(func):
        if ONLY and ONLY not in name:
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


def mouse(widget, kind, pos, button=QtCore.Qt.LeftButton, buttons=None, modifiers=QtCore.Qt.NoModifier):
    point = QtCore.QPointF(pos)
    if buttons is None:
        buttons = button if kind in (QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonDblClick) else QtCore.Qt.NoButton
    APP.sendEvent(widget, QtGui.QMouseEvent(kind, point, point, button, buttons, modifiers))


def click(widget, pos, button=QtCore.Qt.LeftButton, modifiers=QtCore.Qt.NoModifier):
    mouse(widget, QtCore.QEvent.MouseButtonPress, pos, button, modifiers=modifiers)
    mouse(widget, QtCore.QEvent.MouseButtonRelease, pos, button, modifiers=modifiers)


def double_click(widget, pos):
    """What Qt sends for a double click: press, release, double-click, release."""
    mouse(widget, QtCore.QEvent.MouseButtonPress, pos)
    mouse(widget, QtCore.QEvent.MouseButtonRelease, pos)
    mouse(widget, QtCore.QEvent.MouseButtonDblClick, pos)
    mouse(widget, QtCore.QEvent.MouseButtonRelease, pos)


def drag(widget, start, end, button=QtCore.Qt.RightButton, steps=6):
    mouse(widget, QtCore.QEvent.MouseButtonPress, start, button)
    for index in range(1, steps + 1):
        point = QtCore.QPointF(start.x() + (end.x() - start.x()) * index / float(steps),
                               start.y() + (end.y() - start.y()) * index / float(steps))
        mouse(widget, QtCore.QEvent.MouseMove, point, QtCore.Qt.NoButton, buttons=button)
    mouse(widget, QtCore.QEvent.MouseButtonRelease, end, button)


def key(widget, code, text="", modifiers=QtCore.Qt.NoModifier):
    for kind in (QtCore.QEvent.KeyPress, QtCore.QEvent.KeyRelease):
        APP.sendEvent(widget, QtGui.QKeyEvent(kind, code, modifiers, text))


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
    """What is cut off in a window: a scroll page that needs a horizontal bar, a widget past its page's or the window's right edge,
    text in a label that does not fit its box. Empty when all is visible."""
    from aerotools.tools.aeroexports import controls
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
                problems.append("panel {}: {} ({}) ends at {} > {}".format(index, type(widget).__name__, widget.objectName(), right,
                                                                           page.width() - 1))
    frame = window.rect()
    for widget in window.findChildren(QtWidgets.QWidget):
        if widget.isVisibleTo(window) and widget.width() > 1:
            corner = widget.mapTo(window, QtCore.QPoint(widget.width() - 1, widget.size().height() - 1))
            inside_scroll = any(isinstance(p, QtWidgets.QScrollArea) for p in _parents(widget))
            if corner.x() > frame.right() + 1 or (corner.y() > frame.bottom() + 1 and not inside_scroll):
                problems.append("{} ({}) past the window edge: {},{} > {},{}".format(type(widget).__name__, widget.objectName(),
                                                                                    corner.x(), corner.y(), frame.right(), frame.bottom()))
    for label in window.findChildren(QtWidgets.QLabel):
        if label.isVisibleTo(window) and label.wordWrap() and label.text() and label.width() > 20:
            need = label.heightForWidth(label.width())
            if need > label.height() + 2:
                problems.append("label '{}' is cut: needs {} px, has {}".format(label.text()[:30], need, label.height()))
    return problems


def new_wizard(**backend_args):
    backend_args.setdefault("delay", 0)
    backend_args.setdefault("outputs", OUTPUTS)
    backend = FakeBackend(**backend_args)
    window = W.FacadeWizard(backend)
    return window, backend


def show(window):
    window.fit_to_fonts()
    window.show()
    pump(80)
    window.resize(window.size())
    pump(80)


def wait_picture(window):
    return until(lambda: window.canvas.has_picture(), 10)


def building_polygon(window, margin=0.0):
    """A rectangle around the synthetic building, in metres."""
    return [(-12.0 - margin, -14.0 - margin), (82.0 + margin, -14.0 - margin), (82.0 + margin, 30.0 + margin),
            (-12.0 - margin, 30.0 + margin)]


def draw_polygon(window, world, close="double"):
    canvas = window.canvas
    frame = canvas.frame()
    pts = [canvas.scene_to_view(*frame.to_pixel(x, y)) for x, y in world]
    viewport = canvas.viewport()
    for index, point in enumerate(pts):
        if index == len(pts) - 1 and close == "double":
            double_click(viewport, QtCore.QPointF(point))
        else:
            click(viewport, QtCore.QPointF(point))
    if close == "first":
        click(viewport, QtCore.QPointF(pts[0]))


def run_detection(window):
    draw_polygon(window, building_polygon(window))
    search = window.screens[S.SEARCH]
    search.start_detection()
    ok = until(lambda: window.current_step() == S.FACADES, 30)
    pump(50)
    return ok


def shot(window, name):
    if SHOTS:
        pump(60)
        window.grab().save(os.path.join(SHOTS, name + ".png"))


def prepare_to(window, step):
    """Drive the wizard to `step` with the default choices (tests and screenshots)."""
    assert wait_picture(window)
    if step >= S.FACADES:
        assert run_detection(window)
    if step >= S.MARGINS:
        window.next()
    if step >= S.METHOD:
        window.next()
    if step >= S.BUILD:
        window.next()
    if step >= S.EXPORT:
        window.ask = lambda title, text, buttons: "continue"
        window.screens[S.BUILD].start_build()
        assert until(lambda: window.current_step() == S.EXPORT, 30)


# --------------------------------------------------------------------------------------------------
# cases
# --------------------------------------------------------------------------------------------------

def facade_letters(window):
    return [f.letter for f in window.state.active()]


@case("wizard: opens, loads the picture of the building, Run is disabled until an outline is drawn")
def _():
    window, _backend = new_wizard()
    show(window)
    loaded = wait_picture(window)
    search = window.screens[S.SEARCH]
    disabled = not search.run_button.isEnabled()
    drawing = window.canvas.is_drawing()
    window.close()
    return loaded and disabled and drawing, "loaded={} run_disabled={} drawing={}".format(loaded, disabled, drawing)


@case("picture: a chunk without an orthomosaic says why, nothing can be drawn, Run stays disabled")
def _():
    window, _backend = new_wizard(ortho=False)
    show(window)
    until(lambda: not window.busy(), 10)
    pump(100)
    text = window.canvas._empty
    ok = (not window.canvas.has_picture() and "orthomosaic" in text and not window.canvas.is_drawing()
          and not window.screens[S.SEARCH].run_button.isEnabled())
    window.close()
    return ok, text


@case("polygon: clicks place corners, a double click closes, the outline is reported in metres and Run is enabled")
def _():
    window, _backend = new_wizard()
    show(window)
    wait_picture(window)
    canvas, search = window.canvas, window.screens[S.SEARCH]
    seen = []
    canvas.polygon_changed.connect(seen.append)
    world = building_polygon(window)
    draw_polygon(window, world[:3], close="none")
    three = canvas.corner_count() == 3 and not canvas.is_closed() and not search.run_button.isEnabled()
    draw_polygon(window, world[3:], close="double")
    closed = canvas.is_closed() and canvas.corner_count() == 4 and len(seen) == 1
    got = seen[0] if seen else []
    near = len(got) == 4 and all(abs(a[0] - b[0]) < 0.6 and abs(a[1] - b[1]) < 0.6 for a, b in zip(got, world))
    run = search.run_button.isEnabled()
    window.close()
    return three and closed and near and run, "three={} closed={} near={} run={} got={}".format(three, closed, near, run, got)


@case("polygon: a click on the first corner closes it too")
def _():
    window, _backend = new_wizard()
    show(window)
    wait_picture(window)
    draw_polygon(window, building_polygon(window), close="first")
    ok = window.canvas.is_closed() and window.canvas.corner_count() == 4 and window.screens[S.SEARCH].run_button.isEnabled()
    window.close()
    return ok


@case("polygon: Esc cancels the outline being drawn, Delete clears a closed one and it can be drawn again")
def _():
    window, _backend = new_wizard()
    show(window)
    wait_picture(window)
    canvas, search = window.canvas, window.screens[S.SEARCH]
    draw_polygon(window, building_polygon(window)[:2], close="none")
    key(canvas, QtCore.Qt.Key_Escape)
    esc = canvas.corner_count() == 0 and canvas.is_drawing()
    draw_polygon(window, building_polygon(window), close="double")
    closed = canvas.is_closed() and search.run_button.isEnabled()
    key(canvas, QtCore.Qt.Key_Escape)                                   # Esc leaves a closed outline alone
    still = canvas.is_closed()
    key(canvas, QtCore.Qt.Key_Delete)
    cleared = (not canvas.is_closed() and canvas.corner_count() == 0 and canvas.is_drawing() and not search.run_button.isEnabled()
               and window.state.polygon is None)
    draw_polygon(window, building_polygon(window, margin=5.0), close="double")
    again = canvas.is_closed() and search.run_button.isEnabled()
    window.close()
    return esc and closed and still and cleared and again, "esc={} closed={} still={} cleared={} again={}".format(esc, closed, still, cleared, again)


@case("polygon: an outline that crosses itself is refused (Run stays disabled) with a plain message")
def _():
    window, _backend = new_wizard()
    show(window)
    wait_picture(window)
    bow = [(-10.0, -10.0), (60.0, 20.0), (60.0, -10.0), (-10.0, 20.0)]
    draw_polygon(window, bow, close="double")
    search = window.screens[S.SEARCH]
    ok = (window.canvas.is_closed() and window.canvas.polygon_problem() == "crossing" and not search.run_button.isEnabled()
          and "crosses" in search.status.text())
    window.close()
    return ok, search.status.text()


@case("canvas: wheel zoom, right-drag pan, zoom buttons and fit")
def _():
    window, _backend = new_wizard()
    show(window)
    wait_picture(window)
    canvas = window.canvas
    base = canvas.scale_now()
    viewport = canvas.viewport()
    centre = QtCore.QPointF(viewport.width() / 2.0, viewport.height() / 2.0)
    wheel = QtGui.QWheelEvent(centre, canvas.mapToGlobal(centre.toPoint()), QtCore.QPoint(0, 0), QtCore.QPoint(0, 240),
                              QtCore.Qt.NoButton, QtCore.Qt.NoModifier, QtCore.Qt.NoScrollPhase, False)
    APP.sendEvent(viewport, wheel)
    zoomed = canvas.scale_now() > base * 1.3
    before = (canvas.horizontalScrollBar().value(), canvas.verticalScrollBar().value())
    drag(viewport, QtCore.QPointF(300, 300), QtCore.QPointF(200, 240), QtCore.Qt.RightButton)
    after = (canvas.horizontalScrollBar().value(), canvas.verticalScrollBar().value())
    panned = after != before
    drag(viewport, QtCore.QPointF(300, 300), QtCore.QPointF(320, 310), QtCore.Qt.MiddleButton)
    no_point = canvas.corner_count() == 0                                # a drag is not a click: no corner was placed
    canvas.zoom_by(1.25)
    canvas.zoom_by(1 / 1.25)
    canvas.fit_all()
    fitted = abs(canvas.scale_now() - base) < 1e-6
    window.close()
    return zoomed and panned and no_point and fitted, "zoomed={} panned={} no_point={} fitted={}".format(zoomed, panned, no_point, fitted)


@case("detection: Run finds the walls off the UI thread, goes to screen 2, each wall has a letter, colour and size in the list")
def _():
    window, backend = new_wizard(delay=0.05)
    show(window)
    wait_picture(window)
    draw_polygon(window, building_polygon(window))
    search = window.screens[S.SEARCH]
    search.start_detection()
    pump(120)
    running = window.busy() and search.busy() and not window.back_button.isEnabled() and not window.next_button.isEnabled()
    arrived = until(lambda: window.current_step() == S.FACADES, 30)
    screen = window.screens[S.FACADES]
    rows = screen.list.list.count()
    letters = facade_letters(window)
    marks = window.canvas.letters()
    ok = (running and arrived and rows == len(letters) >= 3 and marks == sorted(letters)
          and ("detect", 4) in backend.calls and window.next_button.isEnabled())
    window.close()
    return ok, "running={} arrived={} rows={} letters={}".format(running, arrived, rows, letters)


@case("detection: Cancel stops it, the outline stays, a message says so and Run can be pressed again")
def _():
    window, _backend = new_wizard(delay=0.6)
    show(window)
    wait_picture(window)
    draw_polygon(window, building_polygon(window))
    search = window.screens[S.SEARCH]
    search.start_detection()
    pump(200)
    search.cancel_job()
    done = window.wait_idle(20000)
    pump(100)
    ok = (done and window.current_step() == S.SEARCH and window.canvas.is_closed() and search.run_button.isEnabled()
          and "cancelled" in search.error.text().lower() and not window.state.facades)
    window.close()
    return ok, search.error.text()


@case("detection: a detector that fails shows the reason and leaves the window usable")
def _():
    window, backend = new_wizard()
    show(window)
    wait_picture(window)

    def boom(polygon, progress, cancelled):
        raise RuntimeError("Only 40 tie points are in this area.")
    backend.detect = boom
    draw_polygon(window, building_polygon(window))
    window.screens[S.SEARCH].start_detection()
    window.wait_idle(20000)
    pump(100)
    search = window.screens[S.SEARCH]
    ok = window.current_step() == S.SEARCH and "40 tie points" in search.error.text() and search.run_button.isEnabled()
    window.close()
    return ok, search.error.text()


@case("detection: an outline with no wall in it says so in plain words")
def _():
    window, _backend = new_wizard()
    show(window)
    wait_picture(window)
    draw_polygon(window, [(-30.0, -40.0), (-20.0, -40.0), (-20.0, -30.0), (-30.0, -30.0)])
    window.screens[S.SEARCH].start_detection()
    window.wait_idle(20000)
    pump(100)
    search = window.screens[S.SEARCH]
    ok = window.current_step() == S.SEARCH and "No wall" in search.error.text()
    window.close()
    return ok, search.error.text()


@case("facades: click, ctrl-click and shift-click on the picture select; the list follows; Delete removes; Undo brings back")
def _():
    window, _backend = new_wizard()
    show(window)
    prepare_to(window, S.FACADES)
    canvas, screen = window.canvas, window.screens[S.FACADES]
    letters = facade_letters(window)
    first, second = letters[0], letters[1]
    viewport = canvas.viewport()

    def at(letter):
        return QtCore.QPointF(canvas.facade_view_point(letter))
    click(viewport, at(first))
    one = screen.list.selected_letters() == [first] and canvas.selected() == [first]
    click(viewport, at(second), modifiers=QtCore.Qt.ControlModifier)
    two = sorted(screen.list.selected_letters()) == sorted([first, second]) and sorted(canvas.selected()) == sorted([first, second])
    click(viewport, at(second), modifiers=QtCore.Qt.ControlModifier)
    toggled = screen.list.selected_letters() == [first]
    click(viewport, at(letters[2]), modifiers=QtCore.Qt.ShiftModifier)
    shift = sorted(screen.list.selected_letters()) == sorted([first, letters[2]])
    click(viewport, QtCore.QPointF(5, 5))                                   # empty ground: selection cleared
    cleared = screen.list.selected_letters() == []
    click(viewport, at(first))
    key(canvas, QtCore.Qt.Key_Delete)
    removed = first not in facade_letters(window) and first not in canvas.letters() and screen.list.list.count() == len(letters) - 1
    key(canvas, QtCore.Qt.Key_Z, "z", QtCore.Qt.ControlModifier)
    back = first in facade_letters(window) and first in canvas.letters() and screen.list.list.count() == len(letters)
    window.close()
    return one and two and toggled and shift and cleared and removed and back, "one={} two={} toggled={} shift={} cleared={} removed={} back={}".format(
        one, two, toggled, shift, cleared, removed, back)


@case("facades: Remove button and Undo; removed walls are not processed; Next needs at least one wall")
def _():
    window, backend = new_wizard()
    show(window)
    prepare_to(window, S.FACADES)
    screen = window.screens[S.FACADES]
    lst = screen.list
    letters = facade_letters(window)
    lst.select(letters[:2])
    lst.selection_changed.emit(lst.selected_letters())
    lst.remove_button.click()
    pump(30)
    gone = facade_letters(window) == letters[2:] and all(f.removed for f in window.state.facades if f.letter in letters[:2])
    undo_on = lst.undo_button.isEnabled()
    lst.undo()
    restored = facade_letters(window) == letters
    for letter in letters:                                                  # remove everything: Next is disabled, the note shows
        lst.select([letter])
        lst.remove_selected()
    empty = not facade_letters(window) and not window.next_button.isEnabled() and not screen.empty_note.isHidden()
    lst.undo()
    one_back = len(facade_letters(window)) == 1 and window.next_button.isEnabled()
    window.close()
    return gone and undo_on and restored and empty and one_back, "gone={} undo_on={} restored={} empty={} one_back={}".format(
        gone, undo_on, restored, empty, one_back)


@case("facades: selecting a wall in the list zooms the picture on it")
def _():
    window, _backend = new_wizard()
    show(window)
    prepare_to(window, S.FACADES)
    canvas, lst = window.canvas, window.screens[S.FACADES].list
    base = canvas.scale_now()
    lst.select([facade_letters(window)[1]])
    lst.selection_changed.emit(lst.selected_letters())
    ok = canvas.scale_now() > base * 1.4
    window.close()
    return ok, "{} -> {}".format(base, canvas.scale_now())

@case("margins: left and right are linked by default, the badge unlinks them, linking again copies the left value")
def _():
    window, _backend = new_wizard()
    show(window)
    prepare_to(window, S.MARGINS)
    editor = window.screens[S.MARGINS].editor
    f = editor.fields
    f["left"].setText("2,5")
    f["left"].commit()
    linked = f["right"].value() == 2.5 and f["lr"].isChecked()
    f["lr"].click()
    unlinked = not f["lr"].isChecked()
    f["right"].setText("4")
    f["right"].commit()
    free = f["left"].value() == 2.5 and f["right"].value() == 4.0
    f["lr"].click()
    relinked = f["right"].value() == 2.5 and f["lr"].isChecked()
    f["top"].setText("0.5")
    f["top"].commit()
    tb = f["bottom"].value() == 0.5
    window.close()
    return linked and unlinked and free and relinked and tb, "linked={} unlinked={} free={} relinked={} tb={}".format(
        linked, unlinked, free, relinked, tb)


@case("margins: a wrong number is refused with a red frame and the old value stays; arrow keys step by a tenth")
def _():
    window, _backend = new_wizard()
    show(window)
    prepare_to(window, S.MARGINS)
    field = window.screens[S.MARGINS].editor.fields["left"]
    field.setText("abc")
    refused = not field.commit() and "border" in field.styleSheet()
    field.set_value(field.value())
    field.setText("999")
    too_big = not field.commit()
    field.set_value(1.0)
    key(field, QtCore.Qt.Key_Up)
    up = abs(field.value() - 1.1) < 1e-6
    key(field, QtCore.Qt.Key_Down)
    key(field, QtCore.Qt.Key_Down)
    down = abs(field.value() - 0.9) < 1e-6
    window.close()
    return refused and too_big and up and down, "refused={} too_big={} up={} down={}".format(refused, too_big, up, down)


@case("margins: 'apply to all' changes every wall, turned off the numbers apply to the walls picked in the list only")
def _():
    window, _backend = new_wizard()
    show(window)
    prepare_to(window, S.MARGINS)
    screen = window.screens[S.MARGINS]
    st = window.state
    letters = facade_letters(window)
    screen.editor.fields["left"].setText("3")
    screen.editor.fields["left"].commit()
    everyone = all(st.margins_of(l).left == 3.0 and st.margins_of(l).right == 3.0 for l in letters)
    screen.editor.apply_all.setChecked(False)
    pump(30)
    screen.list.select([letters[1]])
    screen.list.selection_changed.emit(screen.list.selected_letters())
    screen.editor.fields["top"].setText("2")
    screen.editor.fields["top"].commit()
    only = st.margins_of(letters[1]).top == 2.0 and all(st.margins_of(l).top == 1.0 for l in letters if l != letters[1])
    screen.list.select([letters[0]])
    screen.list.selection_changed.emit(screen.list.selected_letters())
    shows_other = screen.editor.fields["top"].value() == 1.0
    screen.editor.pixel.setText("5")
    screen.editor.pixel.commit()
    pixel = abs(st.pixel_cm - 5.0) < 1e-9
    window.close()
    return everyone and only and shows_other and pixel, "everyone={} only={} shows_other={} pixel={}".format(
        everyone, only, shows_other, pixel)


@case("method: the best available is ticked, an unavailable one is greyed with its reason and cannot be picked")
def _():
    window, _backend = new_wizard(dense_cloud=False)
    show(window)
    prepare_to(window, S.METHOD)
    picker = window.screens[S.METHOD].picker
    best = picker.selected() == "depth_maps"
    dense = picker._cards["dense_cloud"]
    greyed = not picker.is_enabled("dense_cloud") and not dense.button.isEnabled()
    picker.select("dense_cloud")
    stays = picker.selected() == "depth_maps"
    picker.select("tie_points")
    tie = picker.selected() == "tie_points" and window.state.method == "tie_points"
    texts = " ".join(label.text() for label in dense.findChildren(QtWidgets.QLabel))
    reason = "dense cloud" in texts.lower()
    window.close()
    return best and greyed and stays and tie and reason, "best={} greyed={} stays={} tie={} reason={}".format(best, greyed, stays, tie, reason)


@case("method: with every method available the dense cloud is the default; with none the screen says so and Next is off")
def _():
    window, _backend = new_wizard()
    show(window)
    prepare_to(window, S.METHOD)
    dense = window.screens[S.METHOD].picker.selected() == "dense_cloud"
    window.close()
    window, _backend = new_wizard(tie_points=False, depth_maps=False, dense_cloud=False)
    show(window)
    prepare_to(window, S.METHOD)
    none = (window.screens[S.METHOD].picker.selected() == "" and not window.next_button.isEnabled()
            and not window.screens[S.METHOD].none_note.isHidden())
    window.close()
    return dense and none, "dense={} none={}".format(dense, none)


@case("wizard: full run from the outline to the files (all six screens), files written next to their georeferencing text")
def _():
    out = tempfile.mkdtemp(prefix="aerofacade_full_")
    window, backend = new_wizard()
    show(window)
    prepare_to(window, S.EXPORT)
    ex = window.screens[S.EXPORT]
    arrived = until(lambda: ex.stack.currentIndex() == 1, 30)
    letters = facade_letters(window)
    thumbs = ex.thumbs.letters() == letters and all(not ex.thumbs.list.item(r).icon().isNull() for r in range(ex.thumbs.count()))
    ex.folder.setText(out)
    ex.start_export()
    window.wait_idle(30000)
    pump(100)
    written = sorted(n for n in os.listdir(out) if n.endswith(".png"))
    georef = sorted(n for n in os.listdir(out) if n.endswith(".txt"))
    expect = ["CARRO_261106_facade_%s.png" % l for l in letters]
    done = ex.stack.currentIndex() == 2 and not ex.open_button.isHidden()
    steps = [window.steps.state_of(i) for i in range(6)]
    window.close()
    return (arrived and thumbs and written == expect and len(georef) == len(letters) and done and steps[5] == "current",
            "arrived={} thumbs={} written={} done={} steps={}".format(arrived, thumbs, written, done, steps))


@case("export: only the ticked pictures are written, in the chosen format; the DXF option adds a drawing")
def _():
    folder = tempfile.mkdtemp(prefix="aerofacade_sel_")
    window, backend = new_wizard()
    show(window)
    prepare_to(window, S.EXPORT)
    ex = window.screens[S.EXPORT]
    until(lambda: ex.stack.currentIndex() == 1, 30)
    letters = facade_letters(window)
    ex.thumbs.set_checked(letters[:2])
    ex.folder.setText(folder)
    for radio in ex.formats.buttons():
        if radio.property("value") == "jpg":
            radio.setChecked(True)
    ex.dxf.setChecked(True)
    ex.start_export()
    window.wait_idle(30000)
    names = sorted(os.listdir(folder))
    pics = [n for n in names if n.endswith(".jpg") or n.endswith(".png")]
    ok = len(pics) == 2 and ex.format() == "jpg" and len([n for n in names if n.endswith(".dxf")]) == 2
    window.close()
    return ok, str(names)


@case("export: Export is off while nothing is ticked or the folder is empty")
def _():
    window, _backend = new_wizard()
    show(window)
    prepare_to(window, S.EXPORT)
    ex = window.screens[S.EXPORT]
    until(lambda: ex.stack.currentIndex() == 1, 30)
    ex.folder.setText(OUTPUTS)
    on = ex.export_button.isEnabled()
    ex.thumbs.set_all(False)
    off1 = not ex.export_button.isEnabled()
    ex.thumbs.set_all(True)
    ex.folder.setText("")
    off2 = not ex.export_button.isEnabled()
    window.close()
    return on and off1 and off2, "on={} off1={} off2={}".format(on, off1, off2)


@case("build: when every surface already exists screen 5 is skipped and Back from the end goes to the method")
def _():
    window, backend = new_wizard(existing="ABCDEFGHIJ")
    show(window)
    prepare_to(window, S.METHOD)
    window.next()
    pump(100)
    skipped = window.current_step() == S.EXPORT and window.state.skipped_build
    built = "build_dems" not in [c[0] for c in backend.calls]
    ex = window.screens[S.EXPORT]
    until(lambda: ex.stack.currentIndex() == 1, 30)
    window.back()
    back = window.current_step() == S.METHOD
    window.close()
    return skipped and built and back, "skipped={} built={} back={}".format(skipped, built, back)


@case("build: one wall fails, the others go on, the row says why, no success choice, Try again redoes only that one")
def _():
    window, backend = new_wizard(fail={"B": "Not enough depth maps cover this wall."})
    show(window)
    prepare_to(window, S.BUILD)
    asked = []
    window.ask = lambda *a: asked.append(a) or None
    screen = window.screens[S.BUILD]
    screen.start_build()
    window.wait_idle(60000)
    pump(100)
    states = screen.batch.states()
    failed = states.get("B") == "failed" and all(v == "done" for k, v in states.items() if k != "B")
    note = "Not enough depth maps" in screen.batch._rows["B"].status.text()
    no_dialog = not asked
    next_on = window.next_button.isEnabled()
    again = not screen.build_button.isHidden()
    backend.fail.clear()
    screen.start_build()
    window.wait_idle(60000)
    calls = [c for c in backend.calls if c[0] == "build_dems"]
    only_b = calls[-1][1] == ["B"]
    everything = all(v == "done" for v in screen.batch.states().values()) and len(asked) == 1
    window.close()
    return failed and note and no_dialog and next_on and again and only_b and everything, (
        "failed={} note={} no_dialog={} next_on={} again={} only_b={} everything={} states={}".format(
            failed, note, no_dialog, next_on, again, only_b, everything, states))


@case("build: the banner shows while it runs, Cancel stops after the current wall, the rest says cancelled, Build resumes")
def _():
    window, backend = new_wizard(delay=0)
    show(window)
    prepare_to(window, S.BUILD)
    backend.delay = 0.25
    window.ask = lambda *a: None
    screen = window.screens[S.BUILD]
    screen.start_build()
    pump(300)
    banner = not screen.batch.banner.isHidden() and screen.batch.is_running() and not window.back_button.isEnabled()
    screen.batch.cancel_button.click()
    window.wait_idle(30000)
    pump(100)
    states = screen.batch.states()
    some_cancelled = "cancelled" in states.values() and not screen.batch.banner.isVisible()
    screen.start_build()
    window.wait_idle(60000)
    pump(100)
    resumed = all(v == "done" for v in screen.batch.states().values())
    window.close()
    return banner and some_cancelled and resumed, "banner={} some_cancelled={} resumed={} states={}".format(banner, some_cancelled, resumed, states)


@case("build: success shows the choice, 'Continue' goes to the pictures, 'Exit' closes the window")
def _():
    window, backend = new_wizard()
    show(window)
    prepare_to(window, S.BUILD)
    seen = []
    window.ask = lambda title, text, buttons: seen.append([b[0] for b in buttons]) or "continue"
    window.screens[S.BUILD].start_build()
    window.wait_idle(60000)
    pump(100)
    go_on = window.current_step() == S.EXPORT and seen == [["exit", "continue"]]
    window.close()
    window2, _b = new_wizard()
    show(window2)
    prepare_to(window2, S.BUILD)
    window2.ask = lambda *a: "exit"
    window2.screens[S.BUILD].start_build()
    window2.wait_idle(60000)
    pump(100)
    closed = not window2.isVisible()
    return go_on and closed, "go_on={} closed={}".format(go_on, closed)


@case("orthophotos: a failed picture is listed with its reason and does not block the others")
def _():
    window, backend = new_wizard(fail_ortho={"D": "The cameras do not see this wall."})
    show(window)
    prepare_to(window, S.BUILD)
    window.ask = lambda *a: "continue"
    window.screens[S.BUILD].start_build()
    window.wait_idle(60000)
    until(lambda: not window.busy(), 60)
    pump(200)
    ex = window.screens[S.EXPORT]
    states = ex.batch.states()
    failed = states.get("D") == "failed" and ex.stack.currentIndex() == 0
    keep = not ex.keep_button.isHidden()
    reason = "cameras do not see" in ex.batch._rows["D"].status.text()
    ex._show_choose()
    only_ok = "D" not in ex.thumbs.letters() and len(ex.thumbs.letters()) == len(states) - 1
    window.close()
    return failed and keep and reason and only_ok, "failed={} keep={} reason={} only_ok={} states={}".format(failed, keep, reason, only_ok, states)


@case("orthophotos: Cancel while they are built stops the batch and offers to try again")
def _():
    window, backend = new_wizard(delay=0)
    show(window)
    prepare_to(window, S.BUILD)
    window.ask = lambda *a: (setattr(backend, "delay", 0.5), "continue")[1]      # slow only the pictures, not the surfaces
    window.screens[S.BUILD].start_build()
    until(lambda: window.current_step() == S.EXPORT, 120)
    ex = window.screens[S.EXPORT]
    pump(500)
    running = ex.busy() and ex.stack.currentIndex() == 0 and not ex.batch.banner.isHidden()
    ex.batch.cancel_button.click()
    window.wait_idle(60000)
    pump(100)
    states = ex.batch.states()
    ok = running and "cancelled" in states.values() and not ex.batch.retry_button.isHidden()
    window.close()
    return ok, "running={} states={}".format(running, states)


@case("export: Cancel during the export stops it and says so")
def _():
    window, backend = new_wizard(delay=0)
    show(window)
    prepare_to(window, S.EXPORT)
    ex = window.screens[S.EXPORT]
    until(lambda: ex.stack.currentIndex() == 1, 120)
    backend.delay = 0.6
    ex.folder.setText(tempfile.mkdtemp(prefix="aerofacade_c_"))
    ex.start_export()
    pump(400)
    running = ex.busy() and not ex.export_box.isHidden() and not ex.export_button.isVisible()
    ex.cancel_job()
    window.wait_idle(30000)
    pump(100)
    ok = running and ex.stack.currentIndex() == 2 and "cancelled" in ex.done_bad.text().lower()
    window.close()
    return ok, "running={} page={} bad={}".format(running, ex.stack.currentIndex(), ex.done_bad.text())


@case("export: a picture that cannot be written is listed with its reason, the others are written")
def _():
    out = tempfile.mkdtemp(prefix="aerofacade_f_")
    window, backend = new_wizard(fail_export={"A": "The disk is full."})
    show(window)
    prepare_to(window, S.EXPORT)
    ex = window.screens[S.EXPORT]
    until(lambda: ex.stack.currentIndex() == 1, 60)
    ex.folder.setText(out)
    ex.start_export()
    window.wait_idle(30000)
    pump(100)
    ok = "disk is full" in ex.done_bad.text() and "picture(s) saved" in ex.done_title.text() and len(os.listdir(out)) >= 2
    window.close()
    return ok, "bad={} title={}".format(ex.done_bad.text(), ex.done_title.text())

@case("wizard: Back and Next move between the screens, Back from a screen keeps its choices")
def _():
    window, _backend = new_wizard()
    show(window)
    prepare_to(window, S.MARGINS)
    window.screens[S.MARGINS].editor.fields["left"].setText("3")
    window.screens[S.MARGINS].editor.fields["left"].commit()
    window.back()
    on_facades = window.current_step() == S.FACADES
    window.back()
    on_search = window.current_step() == S.SEARCH and window.canvas.is_closed() and window.next_button.isEnabled()
    window.next()
    window.next()
    kept = window.screens[S.MARGINS].editor.fields["left"].value() == 3.0
    window.close()
    return on_facades and on_search and kept, "facades={} search={} kept={}".format(on_facades, on_search, kept)


@case("wizard: changing the outline after the detection makes Next wait for a new Run")
def _():
    window, _backend = new_wizard()
    show(window)
    prepare_to(window, S.FACADES)
    window.back()
    window.canvas.clear_polygon()
    draw_polygon(window, building_polygon(window, margin=3.0))
    stale = not window.next_button.isEnabled()
    window.screens[S.SEARCH].start_detection()
    until(lambda: window.current_step() == S.FACADES, 30)
    current = window.state.detection_is_current()
    window.close()
    return stale and current, "stale={} current={}".format(stale, current)


@case("wizard: Esc does not close the window; closing during a job cancels it first")
def _():
    window, _backend = new_wizard(delay=0.4)
    show(window)
    wait_picture(window)
    key(window, QtCore.Qt.Key_Escape)
    still = window.isVisible()
    draw_polygon(window, building_polygon(window))
    window.screens[S.SEARCH].start_detection()
    pump(200)
    window.close()
    pump(100)
    until(lambda: not window.isVisible(), 30)                              # the job is told to stop, the window closes when it has
    gone = not window.isVisible() and not window.busy()
    return still and gone, "still={} gone={}".format(still, gone)


@case("wizard: the last margins, method, pixel size and export options are remembered in the 'aerofacade' settings")
def _():
    window, _backend = new_wizard()
    show(window)
    prepare_to(window, S.MARGINS)
    ed = window.screens[S.MARGINS].editor
    ed.fields["left"].setText("1.5")
    ed.fields["left"].commit()
    ed.fields["top"].setText("0.5")
    ed.fields["top"].commit()
    ed.pixel.setText("2")
    ed.pixel.commit()
    window.next()
    window.screens[S.METHOD].picker.select("tie_points")
    window.next()
    window.close()
    saved = config.section("aerofacade")
    stored = saved.get("margins", {}).get("left") == 1.5 and saved.get("method") == "tie_points" and saved.get("pixel_cm") == 2.0
    window2, _b = new_wizard()
    show(window2)
    prepare_to(window2, S.MARGINS)
    ed2 = window2.screens[S.MARGINS].editor
    loaded = (ed2.fields["left"].value() == 1.5 and ed2.fields["top"].value() == 0.5 and abs(ed2.pixel_cm() - 2.0) < 1e-9
              and window2.state.method == "tie_points")
    window2.close()
    config.save_settings({})
    return stored and loaded, "stored={} loaded={} saved={}".format(stored, loaded, saved)


@case("backend that runs on the main thread (the Metashape adapter): the window follows progress through the timer and finishes")
def _():
    class MainThreadBackend(FakeBackend):
        threaded = False

        def _wait(self, prog, f0, f1, text, ticks=4):
            for i in range(ticks):
                prog(f0 + (f1 - f0) * i / float(ticks), text)
                APP.processEvents()                                          # the adapter's pump()
                time.sleep(0.01)
            prog(f1, text)

    backend = MainThreadBackend(delay=0.05, outputs=OUTPUTS)
    window = W.FacadeWizard(backend)
    show(window)
    wait_picture(window)
    updates = []
    search = window.screens[S.SEARCH]
    draw_polygon(window, building_polygon(window))
    original = window.run_job

    def spy(func, on_progress, on_done, name="", **kw):
        return original(func, (lambda task: (updates.append(round(task.fraction, 2)), on_progress(task))) if on_progress else None,
                        on_done, name, **kw)
    window.run_job = spy
    search.start_detection()
    arrived = window.current_step() == S.FACADES
    window.next()
    window.next()
    window.next()
    window.ask = lambda *a: "continue"
    window.screens[S.BUILD].start_build()
    pump(100)
    ok = window.current_step() == S.EXPORT and window.screens[S.EXPORT].stack.currentIndex() == 1
    many = len(set(updates)) >= 3                                          # the timer ran while the backend held the main thread
    window.close()
    return arrived and many and ok, "arrived={} updates={} export_page={}".format(arrived, sorted(set(updates))[:6], ok)


@case("French: the whole wizard is in French, no text key shows through")
def _():
    i18n.set_lang("fr", persist=False)
    try:
        window, _backend = new_wizard()
        show(window)
        prepare_to(window, S.EXPORT)
        until(lambda: window.screens[S.EXPORT].stack.currentIndex() == 1, 30)
        leaks, words = [], 0
        for index in range(6):
            for label in window.screens[index].findChildren(QtWidgets.QLabel):
                if "facade." in label.text():
                    leaks.append(label.text())
                words += len(label.text().split())
        for widget in window.findChildren(QtWidgets.QAbstractButton):
            if "facade." in widget.text():
                leaks.append(widget.text())
        suivant = window.back_button.text() == "Retour"
        window.close()
        return not leaks and suivant and words > 100, "leaks={} suivant={} words={}".format(leaks[:3], suivant, words)
    finally:
        i18n.set_lang("en", persist=False)


def render_all(scale):
    """Every screen of the wizard rendered to a PNG at one font scale (--shots)."""
    tag = "x%.1f" % scale
    window, backend = new_wizard(delay=0.35 if SHOTS else 0.04, dense_cloud=False, fail={"C": "Not enough depth maps cover this wall."})
    rescale(window, scale)
    show(window)
    wait_picture(window)
    window.resize(window.size())
    pump(150)
    shot(window, "UI01_search_empty_" + tag)
    problems = {"search_empty": cut_off(window)}
    draw_polygon(window, building_polygon(window)[:3], close="none")
    pump(60)
    shot(window, "UI02_search_drawing_" + tag)
    draw_polygon(window, building_polygon(window)[3:], close="double")
    pump(100)
    shot(window, "UI03_search_outline_ready_" + tag)
    window.screens[S.SEARCH].start_detection()
    pump(500)
    shot(window, "UI04_search_running_" + tag)
    until(lambda: window.current_step() == S.FACADES, 30)
    pump(150)
    shot(window, "UI05_facades_" + tag)
    problems["facades"] = cut_off(window)
    screen = window.screens[S.FACADES]
    screen.list.select(["A", "B"])
    window.canvas.set_selected(["A", "B"])
    window.canvas.zoom_to_facades(["A", "B"])
    pump(100)
    shot(window, "UI06_facades_selected_" + tag)
    window.canvas.fit_all()
    window.next()
    pump(200)
    shot(window, "UI07_margins_" + tag)
    problems["margins"] = cut_off(window)
    window.screens[S.MARGINS].editor.apply_all.setChecked(False)
    pump(100)
    shot(window, "UI08_margins_pick_" + tag)
    window.screens[S.MARGINS].editor.apply_all.setChecked(True)
    window.next()
    pump(200)
    shot(window, "UI09_method_" + tag)
    problems["method"] = cut_off(window)
    window.next()
    pump(200)
    shot(window, "UI10_build_ready_" + tag)
    problems["build"] = cut_off(window)
    window.ask = lambda title, text, buttons: None
    window.screens[S.BUILD].start_build()
    pump(900)
    shot(window, "UI11_build_running_" + tag)
    until(lambda: not window.busy(), 60)
    pump(200)
    shot(window, "UI12_build_failed_row_" + tag)
    problems["build_done"] = cut_off(window)
    window.screens[S.BUILD]._retry(["C"])
    until(lambda: not window.busy(), 60)
    window.next()
    pump(400)
    shot(window, "UI13_orthos_building_" + tag)
    until(lambda: window.screens[S.EXPORT].stack.currentIndex() != 0 or not window.busy(), 60)
    pump(300)
    if window.screens[S.EXPORT].stack.currentIndex() == 0:
        shot(window, "UI14_orthos_some_failed_" + tag)
        window.screens[S.EXPORT]._show_choose()
        pump(200)
    shot(window, "UI15_export_choose_" + tag)
    problems["export_choose"] = cut_off(window)
    window.screens[S.EXPORT].start_export()
    until(lambda: window.screens[S.EXPORT].stack.currentIndex() == 2, 60)
    pump(200)
    shot(window, "UI16_export_done_" + tag)
    problems["export_done"] = cut_off(window)
    window.close()
    unscale()
    return dict((k, v) for k, v in problems.items() if v)


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
        return not bad, str(bad)[:600]
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
