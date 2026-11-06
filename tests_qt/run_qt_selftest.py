"""FastGCP Qt self-test with real mouse events, outside Metashape.

Launch (no pytest required):
    python -B tests_qt\\run_qt_selftest.py

Works with the Python embedded in Metashape (PySide2) as well as with a Python
equipped with PySide6. A fake `Metashape` module is injected before AeroTools is
imported. The events are real QMouseEvents sent by QApplication.sendEvent: they
go through the real ClickFilter installed by `marking.install_filter`.

Output: one PASS / FAIL line per case, non-zero exit code on the slightest FAIL.
Environment variable AEROTOOLS_SELFTEST_VERBOSE=1: keeps the journal.
"""

import json
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
sys.path.insert(0, HERE)
import _isolation  # noqa: E402
GUARD = _isolation.Guard("aerotools_qt_")                      # settings of the machine stay untouched


# --------------------------------------------------------------------------
# Qt environment
# --------------------------------------------------------------------------

def _prepare_qt_environment():
    """Metashape DLLs when running in its Python, offscreen platform if possible."""
    metashape_dir = os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))
    has_qt = os.path.exists(os.path.join(metashape_dir, "Qt5Core.dll"))
    offscreen = True
    if has_qt:
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(metashape_dir)
        os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(metashape_dir, "plugins"))
        # The Qt of Metashape does not ship the "offscreen" plugin.
        offscreen = os.path.exists(
            os.path.join(metashape_dir, "plugins", "platforms", "qoffscreen.dll")
        )
    if offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    return "offscreen" if offscreen else "native"


PLATFORM = _prepare_qt_environment()


# --------------------------------------------------------------------------
# Fake Metashape
# --------------------------------------------------------------------------

class Vector(object):
    def __init__(self, *args):
        values = list(args[0]) if len(args) == 1 else list(args)
        values = (values + [0.0, 0.0, 0.0])[:3]
        self.x, self.y, self.z = [float(v) for v in values]

    def __sub__(self, other):
        return Vector(self.x - other.x, self.y - other.y, self.z - other.z)

    def __getitem__(self, index):
        return (self.x, self.y, self.z)[index]

    def norm(self):
        return (self.x ** 2 + self.y ** 2 + self.z ** 2) ** 0.5


class Projection(object):
    def __init__(self, coord=None, pinned=False):
        self.coord = coord
        self.pinned = pinned


class _Reference(object):
    def __init__(self):
        self.location = None


class Marker(object):
    Projection = Projection

    def __init__(self, label, position=None):
        self.label = label
        self.position = position
        self.projections = {}
        self.reference = _Reference()
        self.selected = False


class _Sensor(object):
    def __init__(self, width, height):
        self.width, self.height = width, height


class _CamTransform(object):
    def __init__(self, origin):
        self.origin = origin

    def inv(self):
        return self

    def mulp(self, point):
        return Vector(point.x - self.origin.x, point.y - self.origin.y,
                      point.z - self.origin.z)


class _Photo(object):
    path = "Y:/fake/photo.jpg"


class Camera(object):
    FOCAL = 1000.0

    def __init__(self, key, center):
        self.key = key
        self.label = "IMG_{:04d}".format(key)
        self.center = center
        self.enabled = True
        self.sensor = _Sensor(4000, 3000)
        self.transform = _CamTransform(center)
        self.photo = _Photo()

    def project(self, point):
        local = self.transform.mulp(point)
        if local.z == 0:
            return None
        return Vector(self.sensor.width / 2.0 + self.FOCAL * local.x / local.z,
                      self.sensor.height / 2.0 + self.FOCAL * local.y / local.z, 0)


class _ChunkTransform(object):
    scale = 1.0
    matrix = None


class Chunk(object):
    def __init__(self, cameras, markers):
        self.cameras = list(cameras)
        self.markers = list(markers)
        self.transform = _ChunkTransform()
        self.crs = None
        self.key = 1
        self.label = "Chunk test"


class PhotoView(object):
    """Fake photo view: same attributes as Metashape.app.photo_view."""

    def __init__(self, width, height):
        self.camera = None
        self.width = width
        self.height = height
        self.scale = 0.5
        self.center = Vector(2000.0, 1500.0)
        self.opened = []
        self.on_open = None

    def open(self, camera):
        self.opened.append(camera)
        self.camera = camera
        if self.on_open is not None:
            self.on_open(camera)


class PhotosPane(object):
    def __init__(self):
        self.filter = None

    def setFilter(self, cameras):
        self.filter = list(cameras)

    def resetFilter(self):
        self.filter = None


class FakeApp(object):
    version = "2.2.1-selftest"

    def __init__(self):
        self.document = types.SimpleNamespace(chunk=None)
        self.photo_view = None
        self.photos_pane = PhotosPane()
        self.menu_items = []

    def update(self):
        pass

    def addMenuItem(self, label, callback, shortcut=None):
        self.menu_items.append((label, callback, shortcut))

    def messageBox(self, text):
        print(text)


FAKE = types.ModuleType("Metashape")
FAKE.Vector = Vector
FAKE.Marker = Marker
FAKE.Camera = Camera
FAKE.Chunk = Chunk
FAKE.app = FakeApp()
sys.modules["Metashape"] = FAKE


# --------------------------------------------------------------------------
# AeroTools imports (after the fake Metashape)
# --------------------------------------------------------------------------

from aerotools import config, journal, msapi, qt  # noqa: E402
GUARD.pin(config)
from aerotools.qt import QtCore, QtGui, QtWidgets  # noqa: E402
from aerotools.tools.fastgcp import (  # noqa: E402
    history, marking, photos, session as session_mod, tool as tool_mod)
from aerotools.tools.fastgcp import panel as panel_mod  # noqa: E402

try:  # absent from the Qt embedded in Metashape (no Qt5Test.dll)
    if qt.BINDING == "PySide2":
        from PySide2 import QtTest  # noqa: E402
    else:
        from PySide6 import QtTest  # noqa: E402
except Exception:  # pragma: no cover
    QtTest = None

if not os.environ.get("AEROTOOLS_SELFTEST_VERBOSE"):
    journal.print = lambda *args, **kwargs: None  # the journal does not pollute the output

Qt = QtCore.Qt
PHOTO_W, PHOTO_H = 400, 300
CLICK_POS = QtCore.QPoint(100, 75)
FAILURES = []
TIMESTAMP = [1000]
EXC = []


def _excepthook(kind, value, trace):
    EXC.append(value)


sys.excepthook = _excepthook


# --------------------------------------------------------------------------
# Qt scene
# --------------------------------------------------------------------------

class Canvas(QtWidgets.QWidget):
    """Widget the size of the photo view: counts the presses that reach it."""

    def __init__(self, parent=None):
        super(Canvas, self).__init__(parent)
        self.presses = 0

    def mousePressEvent(self, event):
        self.presses += 1
        event.ignore()


class FakePanel(object):
    def __init__(self):
        self.calls = 0

    def update_active_card(self):
        self.calls += 1

    def refresh_errors(self):
        pass


APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
WINDOW = QtWidgets.QMainWindow()
TABS = QtWidgets.QTabWidget()
WINDOW.setCentralWidget(TABS)
MODEL_PAGE = Canvas()
PHOTO_PAGE = QtWidgets.QWidget()
CANVAS = Canvas(PHOTO_PAGE)
CANVAS.setGeometry(10, 10, PHOTO_W, PHOTO_H)
TABS.addTab(MODEL_PAGE, "Model")
TABS.addTab(PHOTO_PAGE, "IMG_0000")
DOCK = QtWidgets.QDockWidget("Reference")
DOCK_BODY = Canvas()
DOCK.setWidget(DOCK_BODY)
WINDOW.addDockWidget(Qt.RightDockWidgetArea, DOCK)
WINDOW.resize(900, 520)
WINDOW.setWindowFlags(WINDOW.windowFlags() | Qt.WindowStaysOnTopHint)
WINDOW.move(20, 20)
WINDOW.show()
WINDOW.raise_()
TABS.setCurrentIndex(1)


def pump(milliseconds):
    end = time.time() + milliseconds / 1000.0
    while time.time() < end:
        APP.processEvents()
        time.sleep(0.005)


def _wait_exposed(timeout_s=5.0):
    """Wait until the window is shown: without this widgetAt() returns None."""
    end = time.time() + timeout_s
    while time.time() < end:
        pump(50)
        if QtWidgets.QApplication.widgetAt(CANVAS.mapToGlobal(CLICK_POS)) is not None:
            return True
    return False


EXPOSED = _wait_exposed()


def send_mouse(widget, pos, button=Qt.LeftButton, modifiers=Qt.NoModifier,
               kind=QtCore.QEvent.MouseButtonPress):
    """Send a real QMouseEvent to `widget`. Returns the event."""
    local = QtCore.QPointF(pos)
    global_pos = QtCore.QPointF(widget.mapToGlobal(pos))
    buttons = button if kind != QtCore.QEvent.MouseButtonRelease else Qt.NoButton
    event = QtGui.QMouseEvent(kind, local, global_pos, button, buttons, modifiers)
    TIMESTAMP[0] += 10
    event.setTimestamp(TIMESTAMP[0])
    QtWidgets.QApplication.sendEvent(widget, event)
    return event


def click(widget, pos=CLICK_POS, button=Qt.LeftButton, modifiers=Qt.NoModifier):
    """Click on `widget`. Returns True if the ClickFilter consumed the press."""
    before = widget.presses
    send_mouse(widget, pos, button, modifiers)
    return widget.presses == before


# --------------------------------------------------------------------------
# Scenario
# --------------------------------------------------------------------------

SCENARIOS = []


class Scenario(object):
    def __init__(self, camera_count=5):
        self.cameras = [Camera(i, Vector(float(i), 0.0, 0.0)) for i in range(camera_count)]
        self.marker = Marker("GCP1", position=Vector(0.0, 0.0, 10.0))
        self.chunk = Chunk(self.cameras, [self.marker])
        FAKE.app.document.chunk = self.chunk
        self.view = PhotoView(PHOTO_W, PHOTO_H)
        self.view.on_open = self._on_open
        FAKE.app.photo_view = self.view
        FAKE.app.photos_pane.resetFilter()
        self.open_photo(self.cameras[0])
        history.clear()
        self.state = session_mod.Session()
        self.panel = FakePanel()
        self.state.panel = self.panel
        self.state.active_marker = self.marker
        self.state.enabled = True
        marking.install_filter(self.state)
        marking.PLAIN_CLICK_PLACES = True
        SCENARIOS.append(self)
        pump(20)

    def _on_open(self, camera):
        TABS.setTabText(1, camera.label)

    def open_photo(self, camera):
        self.view.camera = camera
        TABS.setTabText(1, camera.label)
        TABS.setCurrentIndex(1)

    def close(self):
        self.state.enabled = False  # neutralises the pending moves to the next photo
        marking.remove_filter(self.state)
        marking.PLAIN_CLICK_PLACES = True
        pump(20)

    def projection(self, camera=None):
        return self.marker.projections.get(camera or self.cameras[0])


def log_lines():
    return journal.lines()


def case(name):
    """Decorator: runs the case, prints PASS / FAIL, remembers the failure."""
    def wrap(func):
        try:
            result = func()
            ok, detail = result if isinstance(result, tuple) else (bool(result), "")
        except Exception as exc:
            ok, detail = False, "exception {!r}\n{}".format(exc, traceback.format_exc())
        print("{} {}{}".format("PASS" if ok else "FAIL", name,
                               "" if ok or not detail else "  -> " + str(detail)))
        if not ok:
            FAILURES.append(name)
        return func
    return wrap


def expect(condition, detail=""):
    return bool(condition), detail


def _expected_xy(pos=CLICK_POS):
    # img = center + (local - taille / 2) / scale
    return (2000.0 + (pos.x() - PHOTO_W / 2.0) / 0.5,
            1500.0 + (pos.y() - PHOTO_H / 2.0) / 0.5)


# --------------------------------------------------------------------------
# Cases: Qt modifiers
# --------------------------------------------------------------------------

@case("qt.modifiers: none / Shift / Ctrl / Alt / Ctrl+Shift via real events")
def _():
    seen = {}

    class Probe(QtWidgets.QWidget):
        def mousePressEvent(self, event):
            seen["value"] = qt.modifiers(event)

    probe = Probe()
    probe.resize(50, 50)
    probe.show()
    expected = (
        (Qt.NoModifier, (False, False, False)),
        (Qt.ShiftModifier, (False, True, False)),
        (Qt.ControlModifier, (True, False, False)),
        (Qt.AltModifier, (False, False, True)),
        (Qt.ControlModifier | Qt.ShiftModifier, (True, True, False)),
    )
    for modifiers, want in expected:
        seen.clear()
        send_mouse(probe, QtCore.QPoint(5, 5), modifiers=modifiers)
        if seen.get("value") != want:
            probe.close()
            return False, "modifiers={!r} expected {!r} got {!r}".format(
                modifiers, want, seen.get("value"))
    probe.close()
    return True, ""


# --------------------------------------------------------------------------
# Cases: placement
# --------------------------------------------------------------------------

@case("Shift + left click: places the projection at the right coordinates, event consumed")
def _():
    sc = Scenario()
    consumed = click(CANVAS, modifiers=Qt.ShiftModifier)
    proj = sc.projection()
    sc.close()
    if proj is None:
        return False, "no projection placed"
    x, y = _expected_xy()
    ok = (consumed and abs(proj.coord.x - x) < 1e-6 and abs(proj.coord.y - y) < 1e-6
          and proj.pinned is True)
    return ok, "consumed={} coord=({}, {}) expected=({}, {}) pinned={}".format(
        consumed, proj.coord.x, proj.coord.y, x, y, proj.pinned)


@case("plain click (no modifier): also places the projection, event consumed")
def _():
    sc = Scenario()
    consumed = click(CANVAS)
    proj = sc.projection()
    sc.close()
    return expect(proj is not None and consumed,
                  "projection={} consumed={}".format(proj, consumed))


@case("PLAIN_CLICK_PLACES = False: a plain click places nothing, Shift + click does")
def _():
    sc = Scenario()
    marking.PLAIN_CLICK_PLACES = False
    consumed_plain = click(CANVAS)
    plain_placed = sc.projection() is not None
    consumed_shift = click(CANVAS, modifiers=Qt.ShiftModifier)
    shift_placed = sc.projection() is not None
    sc.close()
    return expect(not plain_placed and not consumed_plain and shift_placed and consumed_shift,
                  "plain_placed={} shift_placed={}".format(plain_placed, shift_placed))


@case("Ctrl + click, Alt + click, Ctrl+Shift + click: nothing, event not consumed")
def _():
    sc = Scenario()
    results = []
    for modifiers in (Qt.ControlModifier, Qt.AltModifier,
                      Qt.ControlModifier | Qt.ShiftModifier,
                      Qt.ShiftModifier | Qt.AltModifier):
        consumed = click(CANVAS, modifiers=modifiers)
        results.append((consumed, sc.projection() is not None))
    sc.close()
    return expect(all(c is False and not placed for c, placed in results), repr(results))


@case("right click and middle click (even with Shift): nothing")
def _():
    sc = Scenario()
    results = []
    for button in (Qt.RightButton, Qt.MiddleButton):
        for modifiers in (Qt.NoModifier, Qt.ShiftModifier):
            consumed = click(CANVAS, button=button, modifiers=modifiers)
            results.append((consumed, sc.projection() is not None))
    sc.close()
    return expect(all(c is False and not placed for c, placed in results), repr(results))


@case("mouse release and move: never intercepted")
def _():
    sc = Scenario()
    for kind in (QtCore.QEvent.MouseButtonRelease, QtCore.QEvent.MouseMove):
        send_mouse(CANVAS, CLICK_POS, modifiers=Qt.ShiftModifier, kind=kind)
    sc.close()
    return expect(sc.projection() is None)


@case("a second placement on the same photo replaces the first")
def _():
    sc = Scenario()
    click(CANVAS, QtCore.QPoint(100, 75))
    first = (sc.projection().coord.x, sc.projection().coord.y)
    sc.open_photo(sc.cameras[0])
    click(CANVAS, QtCore.QPoint(300, 200), modifiers=Qt.ShiftModifier)
    proj = sc.projection()
    sc.close()
    want = _expected_xy(QtCore.QPoint(300, 200))
    ok = (len(sc.marker.projections) == 1 and (proj.coord.x, proj.coord.y) == want
          and first != want)
    return ok, "projections={} coord=({}, {}) expected={}".format(
        len(sc.marker.projections), proj.coord.x, proj.coord.y, want)


@case("click outside the photo view (panel, Model tab): nothing and silent journal")
def _():
    sc = Scenario()
    before = len(log_lines())
    results = [
        click(DOCK_BODY, QtCore.QPoint(10, 10), modifiers=Qt.ShiftModifier),
        click(DOCK_BODY, QtCore.QPoint(10, 10)),
    ]
    TABS.setCurrentIndex(0)
    pump(20)
    results.append(click(MODEL_PAGE, QtCore.QPoint(30, 30), modifiers=Qt.ShiftModifier))
    results.append(click(MODEL_PAGE, QtCore.QPoint(30, 30)))
    TABS.setCurrentIndex(1)
    pump(20)
    added = log_lines()[before:]
    placed = sc.projection() is not None
    sc.close()
    return expect(not placed and all(r is False for r in results) and not added,
                  "placed={} results={} journal={!r}".format(placed, results, added))


@case("no current marker: nothing, not consumed, a single journal line")
def _():
    sc = Scenario()
    sc.state.active_marker = None
    before = len(log_lines())
    consumed = click(CANVAS)
    added = log_lines()[before:]
    sc.close()
    return expect(consumed is False and sc.projection() is None and len(added) == 1
                  and "no current marker" in added[0],
                  "consumed={} journal={!r}".format(consumed, added))


@case("tool inactive (state.enabled = False): nothing, silent, Metashape normal")
def _():
    sc = Scenario()
    sc.state.enabled = False
    before = len(log_lines())
    r1 = click(CANVAS)
    r2 = click(CANVAS, modifiers=Qt.ShiftModifier)
    added = log_lines()[before:]
    sc.close()
    return expect(r1 is False and r2 is False and sc.projection() is None and not added,
                  "journal={!r}".format(added))


@case("pixel outside the image: readable refusal (one line), not consumed")
def _():
    sc = Scenario()
    sc.view.scale = 1.0
    sc.view.center = Vector(0.0, 0.0)
    before = len(log_lines())
    consumed = click(CANVAS, QtCore.QPoint(5, 5), modifiers=Qt.ShiftModifier)
    added = log_lines()[before:]
    sc.close()
    return expect(consumed is False and sc.projection() is None and len(added) == 1
                  and "outside the image" in added[0],
                  "consumed={} journal={!r}".format(consumed, added))


@case("photo outside the chunk: readable refusal, not consumed")
def _():
    sc = Scenario()
    outsider = Camera(99, Vector(0.0, 0.0, 0.0))
    sc.view.camera = outsider
    TABS.setTabText(1, "IMG_0099")
    before = len(log_lines())
    consumed = click(CANVAS, modifiers=Qt.ShiftModifier)
    added = log_lines()[before:]
    sc.close()
    return expect(consumed is False and sc.marker.projections.get(outsider) is None
                  and len(added) == 1 and "chunk" in added[0],
                  "consumed={} journal={!r}".format(consumed, added))


@case("broken photo view (attributes that raise): no exception, not consumed")
def _():
    sc = Scenario()

    class Broken(object):
        @property
        def camera(self):
            raise RuntimeError("view destroyed")

        def __getattr__(self, name):
            raise RuntimeError("view destroyed")

    FAKE.app.photo_view = Broken()
    del EXC[:]
    consumed = click(CANVAS, modifiers=Qt.ShiftModifier)
    consumed_plain = click(CANVAS)
    FAKE.app.photo_view = sc.view
    sc.close()
    return expect(consumed is False and consumed_plain is False and not EXC
                  and sc.projection() is None, "exceptions={!r}".format(EXC))


@case("filter removed: no more interception")
def _():
    sc = Scenario()
    marking.remove_filter(sc.state)
    consumed = click(CANVAS, modifiers=Qt.ShiftModifier)
    sc.close()
    return expect(consumed is False and sc.projection() is None)


# --------------------------------------------------------------------------
# Cases: move to the next photo
# --------------------------------------------------------------------------

@case("successful placement: next photo opened after ADVANCE_DELAY_MS, list stored")
def _():
    sc = Scenario()
    click(CANVAS)
    opened_early = len(sc.view.opened)
    pump(photos.ADVANCE_DELAY_MS + 250)
    names = [c.label for c in getattr(sc.state, "last_cameras", [])]
    sc.close()
    ok = (photos.ADVANCE_DELAY_MS == 300 and opened_early == 0
          and sc.view.camera is sc.cameras[1]
          and sc.state.last_photo_count == 5 and names[:1] == ["IMG_0000"]
          and sc.panel.calls >= 1)
    return ok, "opened={} camera={} last_cameras={} count={} panel={}".format(
        len(sc.view.opened), sc.view.camera.label, names, sc.state.last_photo_count,
        sc.panel.calls)


@case("next photo: skips the photos already marked, stops at the last one")
def _():
    sc = Scenario()
    sc.marker.projections[sc.cameras[2]] = Projection(Vector([10.0, 10.0]), True)
    visited = []
    for _step in range(6):
        before = sc.view.camera
        click(CANVAS)
        pump(photos.ADVANCE_DELAY_MS + 250)
        visited.append(sc.view.camera.label)
        if sc.view.camera is before:
            break
    sc.close()
    # 0 -> 1 -> 3 (2 already has a projection) -> 4 -> stays on 4
    ok = visited[:3] == ["IMG_0001", "IMG_0003", "IMG_0004"] and visited[-1] == "IMG_0004"
    return ok, "path={}".format(visited)


@case("no photo left to mark: journal 'marked on all photos', stay")
def _():
    sc = Scenario(camera_count=2)
    sc.marker.projections[sc.cameras[1]] = Projection(Vector([10.0, 10.0]), True)
    before = len(log_lines())
    click(CANVAS)
    pump(photos.ADVANCE_DELAY_MS + 250)
    added = [line for line in log_lines()[before:] if "on all photos" in line]
    sc.close()
    return expect(sc.view.camera is sc.cameras[0] and not sc.view.opened and len(added) == 1,
                  "opened={} journal={!r}".format(len(sc.view.opened), added))


@case("the user changes photo before the delay: no automatic jump")
def _():
    sc = Scenario()
    click(CANVAS)
    sc.open_photo(sc.cameras[3])
    pump(photos.ADVANCE_DELAY_MS + 250)
    sc.close()
    return expect(sc.view.camera is sc.cameras[3] and not sc.view.opened)


@case("msapi.open_photo: (ok, message) without raising")
def _():
    sc = Scenario()
    ok_result = msapi.open_photo(sc.cameras[2])

    class Refusing(object):
        camera = None

        def open(self, camera):
            raise RuntimeError("refused")

    FAKE.app.photo_view = Refusing()
    bad_result = msapi.open_photo(sc.cameras[2])
    FAKE.app.photo_view = None
    none_result = msapi.open_photo(sc.cameras[2])
    FAKE.app.photo_view = sc.view
    sc.close()
    return expect(ok_result[0] is True and bad_result[0] is False and none_result[0] is False,
                  "{!r} {!r} {!r}".format(ok_result, bad_result, none_result))


# --------------------------------------------------------------------------
# Cases: session
# --------------------------------------------------------------------------

@case("session: filter and sync always on, after placement = API, writes ignored")
def _():
    folder = os.path.join(os.environ["LOCALAPPDATA"], "HELIX", "AeroTools")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "settings.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"fastgcp": {"auto_filter": False, "sync_selection": False,
                               "after_place": "stay", "collect_dataset": True}}, handle)
    state = session_mod.Session()
    state.set_option("auto_filter", False)
    state.set_option("after_place", session_mod.AFTER_STAY)
    again = session_mod.Session()
    ok = all((s.option("auto_filter") is True and s.option("sync_selection") is True
              and s.option("after_place") == session_mod.AFTER_API
              and s.option("collect_dataset") is True) for s in (state, again))
    os.remove(path)
    return expect(ok, repr(again.options))



# --------------------------------------------------------------------------
# Cases: undo, table, export (real panel in the window)
# --------------------------------------------------------------------------

def press_ctrl_z(state):
    """Real Ctrl+Z (Qt shortcut table) if QtTest is there, otherwise the signal."""
    if QtTest is not None:
        # Application shortcuts only fire if a window is active.
        WINDOW.activateWindow()
        pump(30)
        QtTest.QTest.keyClick(CANVAS, Qt.Key_Z, Qt.ControlModifier)
        pump(30)
        return "keyboard"
    shortcut = state.undo_shortcut
    if shortcut is not None and shortcut.isEnabled():
        shortcut.activated.emit()
    pump(30)
    return "signal"


class PanelScenario(Scenario):
    """Scenario with the real panel in a dock and the real Ctrl+Z shortcut."""

    def __init__(self, camera_count=5):
        super(PanelScenario, self).__init__(camera_count)
        extra = []
        for label, ref, err_x, err_z, shots in (
            ("GCP10", (10.0, 0.0, 5.0), -0.010, 0.050, 3),
            ("GCP2", (20.0, 0.0, 5.0), 0.020, -0.004, 0),
        ):
            marker = Marker(label, position=Vector(ref[0] + err_x, ref[1], ref[2] + err_z))
            marker.reference.location = Vector(*ref)
            for index in range(shots):
                marker.projections[self.cameras[index]] = Projection(Vector([5.0, 5.0]), True)
            extra.append(marker)
        # GCP1: a 3 cm error in X, projections on a single photo.
        self.marker.position = Vector(0.030, 0.0, 10.0)
        self.marker.reference.location = Vector(0.0, 0.0, 10.0)
        self.marker.projections[self.cameras[3]] = Projection(Vector([7.0, 7.0]), True)
        self.chunk.markers.extend(extra)
        history.clear()
        self.real = panel_mod.FastGcpPanel(self.state, WINDOW)
        self.state.panel = self.real
        WINDOW.addDockWidget(Qt.RightDockWidgetArea, self.real)
        self.real.show()
        self.real.refresh_markers()
        tool_mod._install_undo_shortcut(self.state, WINDOW)
        pump(120)

    def order(self):
        grid = self.real.grid
        return [grid.topLevelItem(i).text(0) for i in range(grid.topLevelItemCount())]

    def cell(self, label, column):
        item = self.real._item_for(label)
        return item.text(column) if item is not None else None

    def click_header(self, column):
        header = self.real.grid.header()
        x = header.sectionViewportPosition(column) + 12
        pos = QtCore.QPoint(x, header.height() // 2)
        viewport = header.viewport()
        send_mouse(viewport, pos)
        send_mouse(viewport, pos, kind=QtCore.QEvent.MouseButtonRelease)
        pump(40)

    def close(self):
        super(PanelScenario, self).close()
        listener = getattr(self.state, "undo_listener", None)
        if listener is not None:
            history.unsubscribe(listener)
        shortcut = getattr(self.state, "undo_shortcut", None)
        if shortcut is not None:
            shortcut.setEnabled(False)
            shortcut.deleteLater()
        self.state.undo_shortcut = None
        self.real.detach()
        WINDOW.removeDockWidget(self.real)
        self.real.setParent(None)
        self.real.deleteLater()
        history.clear()
        pump(40)


@case("Ctrl+Z undoes the marking: projection removed, photo reopened, marker active, no jump")
def _():
    sc = PanelScenario()
    click(CANVAS)
    pump(40)
    placed = sc.projection() is not None
    enabled_before = sc.real.undo_button.isEnabled()
    shortcut_before = sc.state.undo_shortcut.isEnabled()
    sc.state.active_marker = None
    mode = press_ctrl_z(sc.state)
    gone = sc.projection() is None
    camera_back = sc.view.camera is sc.cameras[0]
    active_back = sc.state.active_marker is sc.marker
    pump(photos.ADVANCE_DELAY_MS + 250)
    stayed = sc.view.camera is sc.cameras[0]
    logged = any("Undone: GCP1" in line for line in log_lines())
    disabled_after = (not sc.real.undo_button.isEnabled()
                      and not sc.state.undo_shortcut.isEnabled())
    sc.close()
    ok = (placed and enabled_before and shortcut_before and gone and camera_back
          and active_back and stayed and logged and disabled_after)
    return ok, ("mode={} placed={} button={} shortcut={} removed={} photo={} active={} stayed={} "
                "journal={} greyed={}").format(mode, placed, enabled_before, shortcut_before,
                                               gone, camera_back, active_back, stayed, logged,
                                               disabled_after)


@case("Ctrl+Z on a moved point: the old position comes back (marked or not)")
def _():
    sc = PanelScenario()
    sc.marker.projections[sc.cameras[0]] = Projection(Vector([111.0, 222.0]), False)
    click(CANVAS, QtCore.QPoint(300, 200))
    moved = sc.projection().coord.x != 111.0
    press_ctrl_z(sc.state)
    back = sc.projection()
    sc.close()
    ok = moved and back is not None and (back.coord.x, back.coord.y, back.pinned) == (111.0, 222.0, False)
    return ok, "moved={} back={}".format(moved, back and (back.coord.x, back.coord.y, back.pinned))


@case("Undo button: greyed at start, active after a marking, click = undo, greyed again")
def _():
    sc = PanelScenario()
    button = sc.real.undo_button
    start = button.isEnabled()
    click(CANVAS)
    pump(40)
    after_pose = button.isEnabled()
    button.click()
    pump(40)
    undone = sc.projection() is None
    after_undo = button.isEnabled()
    # Tool paused: button and shortcut grey out, the history is kept.
    click(CANVAS)
    pump(40)
    sc.real.toggle.click()
    pump(30)
    paused = (not button.isEnabled()) and (not sc.state.undo_shortcut.isEnabled())
    sc.real.toggle.click()
    pump(30)
    resumed = button.isEnabled() and sc.state.undo_shortcut.isEnabled()
    sc.close()
    ok = (not start) and after_pose and undone and (not after_undo) and paused and resumed
    return ok, "start={} placed={} undone={} after={} paused={} resumed={}".format(
        start, after_pose, undone, after_undo, paused, resumed)


@case("Ctrl+Z in the Search field: undoes the text, undoes no marking")
def _():
    if QtTest is None:
        return True, "skipped: no QtTest in this Python (checked under PySide6)"
    sc = PanelScenario()
    click(CANVAS)
    pump(40)
    WINDOW.activateWindow()
    sc.real.search.setFocus()
    pump(60)
    focus_ok = APP.focusWidget() is sc.real.search
    if not focus_ok:
        sc.close()
        return True, "skipped: the platform does not give keyboard focus"
    QtTest.QTest.keyClicks(sc.real.search, "GCP")
    pump(30)
    typed = sc.real.search.text()
    QtTest.QTest.keyClick(sc.real.search, Qt.Key_Z, Qt.ControlModifier)
    pump(60)
    text_after = sc.real.search.text()
    kept = sc.projection() is not None and history.can_undo()
    sc.close()
    ok = typed == "GCP" and text_after != "GCP" and kept
    return ok, "typed={!r} after={!r} marking_kept={}".format(typed, text_after, kept)


@case("Ctrl+Z with nothing to undo: the shortcut is inactive (Metashape keeps the key)")
def _():
    sc = PanelScenario()
    inactive = not sc.state.undo_shortcut.isEnabled()
    sc.state.enabled = False
    history.push(sc.marker, sc.cameras[0], None, chunk_key=sc.chunk.key)
    paused_inactive = not sc.state.undo_shortcut.isEnabled()
    sc.close()
    return expect(inactive and paused_inactive,
                  "empty_inactive={} paused_inactive={}".format(inactive, paused_inactive))


@case("table: 5 columns, errors in metres, dash without reference, minimum width 420")
def _():
    sc = PanelScenario()
    sc.marker.reference.location = None
    sc.real.refresh_markers()
    grid = sc.real.grid
    heads = [grid.headerItem().text(i) for i in range(grid.columnCount())]
    row10 = [sc.cell("GCP10", c) for c in range(5)]
    row1 = [sc.cell("GCP1", c) for c in range(5)]
    width_ok = sc.real.minimumSizeHint().width() >= 420 or sc.real.widget().minimumWidth() >= 420
    sc.close()
    ok = (heads == ["ID", "Error X", "Error Y", "Error Z", "Proj."]
          and row10 == ["GCP10", "-0.010", "0.000", "0.050", "3"]
          and row1 == ["GCP1", "\u2013", "\u2013", "\u2013", "1"] and width_ok)
    return ok, "headers={} GCP10={} GCP1={} width={}".format(heads, row10, row1, width_ok)


@case("click on a header: ascending, descending, project order; ID in natural sort")
def _():
    sc = PanelScenario()
    seen = {"project": sc.order()}
    sc.click_header(1)
    seen["x_asc"] = sc.order()
    sc.click_header(1)
    seen["x_desc"] = sc.order()
    sc.click_header(1)
    seen["x_back"] = sc.order()
    sc.click_header(0)
    seen["id_asc"] = sc.order()
    sc.click_header(4)
    seen["proj_asc"] = sc.order()
    indicator = sc.real.grid.header().sortIndicatorSection()
    sc.click_header(4)
    sc.click_header(4)
    seen["indicator_cleared"] = sc.real.grid.header().sortIndicatorSection() == -1
    sc.close()
    ok = (seen["project"] == ["GCP1", "GCP10", "GCP2"]
          and seen["x_asc"] == ["GCP10", "GCP2", "GCP1"]
          and seen["x_desc"] == ["GCP1", "GCP2", "GCP10"]
          and seen["x_back"] == ["GCP1", "GCP10", "GCP2"]
          and seen["id_asc"] == ["GCP1", "GCP2", "GCP10"]
          and seen["proj_asc"] == ["GCP2", "GCP1", "GCP10"]
          and indicator == 4 and seen["indicator_cleared"])
    return ok, repr(seen)


@case("table: search, synchronised selection, active marker and dot still in place")
def _():
    sc = PanelScenario()
    panel = sc.real
    panel.search.setText("gcp1")
    pump(30)
    searched = sc.order()
    panel.search.setText("")
    pump(30)
    # Clicked row = current marker + photo filter.
    item = panel._item_for("GCP10")
    panel.grid.setCurrentItem(item)
    panel._on_item_clicked(item, 0)
    pump(40)
    current = sc.state.active_label()
    filtered = FAKE.app.photos_pane.filter is not None
    selected = panel.grid.currentItem() is item
    # Reference selection: same marker, without filtering.
    panel.set_active_marker_by_label("GCP2", from_sync=True)
    synced = sc.state.active_label() == "GCP2" and panel.grid.currentItem().text(0) == "GCP2"
    card = panel.active_value._full          # the displayed text may be elided at this width
    pointed_dot = panel._item_for("GCP10").icon(0).isNull() is False
    counters = panel.count_pointed._number.text()
    sc.close()
    ok = (searched == ["GCP1", "GCP10"] and current == "GCP10" and filtered and selected
          and synced and card == "GCP2" and pointed_dot and counters == "0")
    return ok, ("search={} current={} filter={} selected={} sync={} card={} "
                "dot={} counter={}").format(searched, current, filtered, selected, synced,
                                            card, pointed_dot, counters)


@case("errors refreshed after a marking and after its undo")
def _():
    sc = PanelScenario()
    sc.state.active_marker = sc.marker
    sc.real.update_active_card()
    before = sc.cell("GCP1", 4)
    click(CANVAS)
    pump(40)
    after_pose = sc.cell("GCP1", 4)
    press_ctrl_z(sc.state)
    after_undo = sc.cell("GCP1", 4)
    sc.close()
    return expect(before == "1" and after_pose == "2" and after_undo == "1",
                  "before={} placed={} undo={}".format(before, after_pose, after_undo))


class _StubReport(object):
    """Fake report.py: remembers the calls, or raises."""

    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def export_pdf(self, state, parent):
        self.calls.append(("pdf", state, parent))
        if self.fail:
            raise RuntimeError("simulated failure")

    def export_csv(self, state, parent):
        self.calls.append(("csv", state, parent))


def _with_report(stub, action):
    """Run `action()` with `report` replaced by `stub` (None: import impossible)."""
    package = sys.modules["aerotools.tools.fastgcp"]
    name = "aerotools.tools.fastgcp.report"
    had_attr = hasattr(package, "report")
    old_attr = getattr(package, "report", None)
    had_module = name in sys.modules
    old_module = sys.modules.get(name)
    sys.modules[name] = stub
    if stub is not None:
        package.report = stub
    elif had_attr:
        del package.report
    try:
        return action()
    finally:
        if had_module:
            sys.modules[name] = old_module
        else:
            sys.modules.pop(name, None)
        if had_attr:
            package.report = old_attr
        elif hasattr(package, "report"):
            del package.report


@case("Export button: present, PDF report / CSV table menu, calls report through a lazy import")
def _():
    sc = PanelScenario()
    panel = sc.real
    texts = [action.text() for action in panel.export_menu.actions()]
    label = panel.export_button.text()
    has_icon = not panel.export_button.icon().isNull()
    before_gear = (panel.export_button.geometry().right() < panel.more_button.geometry().left())
    stub = _StubReport()
    _with_report(stub, lambda: [action.trigger() for action in panel.export_menu.actions()])
    called = [(kind, state is sc.state, parent is panel) for kind, state, parent in stub.calls]
    panel.export_button.click()
    pump(60)
    menu_open = panel.export_menu.isVisible()
    panel.export_menu.hide()
    sc.close()
    ok = (texts == ["PDF report", "CSV table"] and label == "Export" and has_icon
          and before_gear and called == [("pdf", True, True), ("csv", True, True)] and menu_open)
    return ok, "menu={} text={!r} icon={} left_of_gear={} calls={} open={}".format(
        texts, label, has_icon, before_gear, called, menu_open)


@case("export without report.py or with an error: status message + journal, no exception")
def _():
    sc = PanelScenario()
    panel = sc.real
    del EXC[:]
    before = len(log_lines())
    _with_report(None, lambda: panel.export_menu.actions()[0].trigger())
    missing_status = panel.status._full
    missing_log = [line for line in log_lines()[before:] if "unavailable" in line]
    before = len(log_lines())
    stub = _StubReport(fail=True)
    _with_report(stub, lambda: panel.export_menu.actions()[0].trigger())
    failed_status = panel.status._full
    failed_log = [line for line in log_lines()[before:] if "failed" in line]
    sc.close()
    ok = (missing_status == "Export unavailable" and len(missing_log) == 1
          and failed_status == "Export failed" and len(failed_log) == 1 and not EXC)
    return ok, "status={!r}/{!r} journal={}/{} exceptions={!r}".format(
        missing_status, failed_status, missing_log, failed_log, EXC)


# --------------------------------------------------------------------------
# Cases: panel texts
# --------------------------------------------------------------------------

def _shown(widget):
    """Full text of a widget with an elided text (or its displayed text)."""
    return getattr(widget, "_full", None) or widget.text()


def visible_texts(panel):
    """Visible texts of the panel."""
    grid = panel.grid
    return {
        "headers": [grid.headerItem().text(i) for i in range(grid.columnCount())],
        "export": panel.export_button.text(),
        "menu": [action.text() for action in panel.export_menu.actions()],
        "state": _shown(panel.state_text),
        "card": _shown(panel._eyebrow_active),
        "search": panel.search.placeholderText(),
        "log": _shown(panel.log_button),
        "check": _shown(panel._checks["collect_dataset"]),
        "tooltips": [panel.count_pointed.toolTip(), panel.count_photos.toolTip()],
    }


EXPECTED = {
    "headers": ["ID", "Error X", "Error Y", "Error Z", "Proj."],
    "export": "Export",
    "menu": ["PDF report", "CSV table"],
    "state": "Tool active",
    "card": "ACTIVE MARKER",
    "search": "Search",
    "log": "Log",
    "check": "Save marker picks",
    "tooltips": ["Photos where this marker is placed", "Photos that see this marker"],
}


def overflowing(panel):
    """Visible widgets of the panel that go past the right edge of the body (overflow)."""
    scroll = panel.widget().findChild(QtWidgets.QScrollArea)
    body = scroll.widget()
    bad = []
    if body.width() > scroll.viewport().width() + 1:
        bad.append(("body", body.width(), scroll.viewport().width()))
    for child in body.findChildren(QtWidgets.QWidget):
        if not child.isVisibleTo(body) or child.width() <= 0:
            continue
        right = child.mapTo(body, QtCore.QPoint(child.width(), 0)).x()
        if right > body.width() + 1:
            bad.append((child.objectName() or type(child).__name__, right, body.width()))
    return bad


@case("panel texts: catalog texts shown, state text follows pause / play, nothing overflows")
def _():
    sc = PanelScenario()
    panel = sc.real
    texts = visible_texts(panel)
    status = panel.status._full
    panel.toggle.click()
    pump(30)
    paused = _shown(panel.state_text)
    panel.toggle.click()
    pump(30)
    resumed = _shown(panel.state_text)
    overflow = overflowing(panel)
    sc.close()
    ok = (texts == EXPECTED and status == "1/3 marked" and paused == "Tool inactive" and resumed == "Tool active"
          and not overflow and not EXC)
    return ok, "texts={} status={!r} paused={!r} resumed={!r} overflow={} exceptions={!r}".format(
        texts if texts != EXPECTED else "ok", status, paused, resumed, overflow, EXC)


def main():
    APP.processEvents()
    WINDOW.close()
    print("-" * 60)
    print("Qt binding: {} ; platform: {} ; Python {}".format(
        qt.BINDING, PLATFORM, sys.version.split()[0]))
    problem = GUARD.check()
    if problem:
        FAILURES.append("real settings: " + problem)
    if FAILURES:
        print("{} failure(s): {}".format(len(FAILURES), " | ".join(FAILURES)))
        return 1
    print("all cases pass")
    return 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
