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


def case(name):
    def wrap(func):
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

@case("wizard: opens, loads the picture of the building, Run is disabled until an outline is drawn")
def _():
    window, _backend = new_wizard()
    show(window)
    loaded = wait_picture(window)
    search = window.screens[S.SEARCH]
    disabled = not search.run_button.isEnabled()
    window.close()
    return loaded and disabled, "loaded={} run_disabled={}".format(loaded, disabled)


def render_all(scale):
    """Every screen of the wizard rendered to a PNG at one font scale (--shots)."""
    tag = "x%.1f" % scale
    window, backend = new_wizard(delay=0.35, dense_cloud=False, fail={"C": "Not enough depth maps cover this wall."})
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
