"""Aero-PPK window: can the mouse reach every control? A hit-test of every step, without network and without Metashape.

    "C:\\Program Files\\Agisoft\\Metashape_v22\\python\\python.exe" -B tests_qt\\run_qt_aeroppk_hittest.py [out folder]

For every visible, enabled, interactive widget of every step (buttons, combos, sliders, edits, tables, the map, the basket rows, the time
axis, the segmented controls, the "i" buttons) the script takes the centre of the widget, asks Qt which widget is really under that screen
point (`QWidget.childAt` of the window, the hit-test Qt applies to a click; `QApplication.widgetAt` also depends on the other applications' windows on the desktop), and checks that it is the widget itself or one of its
children. It also checks that the point is not clipped by a parent, that no other interactive widget covers the widget, that no modal
window or popup is left open, that the busy flags are down when nothing runs, and that a real mouse click (sent to the widget
found under the point) is received by the widget. The same is done in the modes of the Base step (single base, own base),
with 0 and 1 base and the networks on automatic and all ticked. The six steps are swept: Drone, Files and Mode, the Base step, the Result step
(then the Details window in its three views) and the Export step; the fullest states of the first steps (a drone no reader knows, the Files
step with missing files, a flight left out and a folder that was not read) are swept too. It also checks what makes a click miss although
every rectangle is right: the window must not change size under a click, must stay on the screen, needs no more than its design size (a
laptop screen leaves 680 px of height), no control may be squeezed under its least size, and no text may be cut ("..." in a label, a button
narrower than its text). Also checked: the cards of the Mode step and the list of single base mode. A synthetic job (two flights of a
Mavic 3 Enterprise, made-up results, a fake Metashape document: see the helpers below) and made-up servers (`run_qt_aeroppk_fakes.py`) are
used: nothing is downloaded. The helpers are shared by the other aeroppk scripts and by the screenshot script. Settings go to a temporary LOCALAPPDATA. Prints PASS / FAIL lines, one per scenario, and
the details of each failure.
"""

import datetime
import os
import sys
import tempfile
import time
import types

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import _isolation  # noqa: E402

GUARD = _isolation.Guard("aeroppk_hittest_")
OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="aeroppk_hittest_out_")
os.makedirs(OUT, exist_ok=True)
FILES_DIR = tempfile.mkdtemp(prefix="aeroppk_hittest_files_")


def _prepare_qt_environment():
    """Metashape DLLs when running inside its Python; the offscreen platform only when its plugin exists (else Qt would hang)."""
    metashape_dir = os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))
    offscreen = True
    if os.path.exists(os.path.join(metashape_dir, "Qt5Core.dll")):
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(metashape_dir)
        os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(metashape_dir, "plugins"))
        offscreen = os.path.exists(os.path.join(metashape_dir, "plugins", "platforms", "qoffscreen.dll"))
    if offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"

    if os.environ.get("QT_QPA_PLATFORM") == "offscreen" and os.path.isdir(r"C:\Windows\Fonts"):
        os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")      # (the offscreen platform finds no font by itself: text would have no size)

_prepare_qt_environment()
meta = types.ModuleType("Metashape")
meta.app = types.SimpleNamespace(document=None, update=lambda: None)
meta.Vector = lambda *a: a
sys.modules["Metashape"] = meta

from aerotools import config, theme, uikit  # noqa: E402
from aerotools.qt import QtCore, QtGui, QtWidgets  # noqa: E402

GUARD.pin(config)
from aerotools.tools.aeroppk import basket as B, dialog, gnsstime, runner, screen_base, state as S  # noqa: E402
from aerotools.tools.aeroppk import devices, drones, mapview, outputs, pipeline, report, reportdata, summary, ui_views, widgets  # noqa: E402
from aerotools.tools.aeroppk import screen_export  # noqa: E402
from aerotools.tools.aeroppk.drones.base import FlightFiles  # noqa: E402
from aerotools.tools.aeroppk.stations import catalog  # noqa: E402
from aerotools.uikit import metrics, shell, tokens  # noqa: E402
from aerotools.tools.aeroppk.drones.base import PhotoEvent  # noqa: E402
from aerotools.tools.aeroppk.stations import orbits  # noqa: E402
import aeroppk_synth  # noqa: E402
import run_qt_aeroppk_fakes as _fakes  # noqa: E402

FAILURES = []
SITE = (47.4, 8.5)                                                       # French, German and IGS stations around
DAY = datetime.date(2026, 11, 4)
BUTTON_KINDS = (QtWidgets.QAbstractButton, QtWidgets.QComboBox, QtWidgets.QAbstractSlider, QtWidgets.QLineEdit,
                QtWidgets.QAbstractItemView, mapview.MapView, ui_views.TimelineView, screen_base.BasketRow, uikit.Segmented)
SKIP_CLICK = ("rowCross",)                                               # object names of widgets that are only hit-tested (the cross of a row takes the base out)


def check(name, condition, detail=""):
    print("{} {}{}".format("PASS" if condition else "FAIL", name, "  [{}]".format(detail) if detail else ""))
    if not condition:
        FAILURES.append(name)


def pump(app, seconds=0.2):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def shot(app, widget, name, seconds=0.3):
    pump(app, seconds)
    path = os.path.join(OUT, name + ".png")
    widget.grab().save(path)
    print("shot", path)


def synthetic_flight():
    start = gnsstime.to_seconds(gnsstime.utc_to_gps(datetime.datetime(2026, 11, 4, 10, 0, 0)))
    events = []
    for i in range(30):
        event = PhotoEvent(i + 1, start + 2.0 * i)
        event.onboard = (SITE[0] + 0.0004 * (i % 6), SITE[1] + 0.0006 * (i // 6), 420.0)
        event.onboard_q = 50
        event.photo_name = "IMG_%04d.JPG" % (i + 1)
        events.append(event)
    files = types.SimpleNamespace(name="FLIGHT_DEMO_001", folder="demo", photos=["IMG_%04d.JPG" % (i + 1) for i in range(30)], obs=None, nav=None)
    return {"reader": None, "files": files, "events": events, "needs": [], "warnings": []}


# ---------------------------------------------------------------------------------------------------------------------
# A realistic job: two flights of a Mavic 3 Enterprise, their results, a Metashape document (all made up, nothing on disk)
# ---------------------------------------------------------------------------------------------------------------------

FLIGHT_ROOT = "C:/Flights/July"                                          # (nothing is read from there: the files are only named)
FLIGHTS = (("DJI_202607031333_001_site", 225, datetime.datetime(2026, 7, 3, 13, 33, 12)),
           ("DJI_202607031412_002_site", 247, datetime.datetime(2026, 7, 3, 14, 12, 40)))
CHUNK_NAME = "Flight July (472)"


def named_flight(name, photos, start, missing=(), step=1.4):
    """A flight in the format the Mavic 3 Enterprise reader gives (its files, its trigger events, its checklist). `missing` names the files
    that are not there: events_file, obs, nav."""
    reader = drones.READERS[0]
    folder = "%s/%s" % (FLIGHT_ROOT, name)
    files = FlightFiles(folder, reader.id)
    stamp = name.split("_")[1]
    files.photos = ["%s/DJI_%s%02d_%04d_V.JPG" % (folder, stamp, i % 60, i + 1) for i in range(photos)]
    files.events_file = None if "events_file" in missing else "%s/%s_Timestamp.MRK" % (folder, name)
    files.obs = None if "obs" in missing else "%s/%s_PPKOBS.obs" % (folder, name)
    files.nav = None if "nav" in missing else "%s/%s_PPKNAV.nav" % (folder, name)
    first = gnsstime.to_seconds(gnsstime.utc_to_gps(start))
    events = []
    for i in range(photos):
        event = PhotoEvent(i + 1, first + step * i, files.photos[i])
        event.onboard = (42.3 + 0.00002 * (i % 40) + 0.0003 * (i // 40), 9.1 + 0.00003 * (i % 40), 300.0)
        event.onboard_q = 50
        event.photo_name = os.path.basename(files.photos[i])
        events.append(event)
    return {"reader": reader, "files": files, "events": events, "needs": reader.needs(files), "warnings": []}


def realistic_flights(missing=None):
    """The two flights of the job; `missing` = {index: (file attributes that are not there)}."""
    missing = missing or {}
    return [named_flight(name, photos, start, missing.get(i, ())) for i, (name, photos, start) in enumerate(FLIGHTS)]


MORE_STATIONS = ("CRTE", "ATST", "BAST", "LUCI", "TOLA", "OSAN", "PIAA", "TUDA")


def stations(codes=("CRTE", "ATST", "BAST")):
    """Real station objects of the catalog (stations around the site of the flights, which the made-up servers of `run_qt_aeroppk_fakes.py` know)."""
    found = dict((s.code, s) for s in catalog.load())
    return [found[c] for c in codes if c in found]


# what each verdict is made of, per flight: the shares of the photos that are fixed and precise, fixed but less precise (to check), float (not
# reliable: the policy never trusts a float position alone), and without a position
KINDS = {"good": ((1.0, 0.0, 0.0, 0.0), (1.0, 0.0, 0.0, 0.0)),
         "check": ((1.0, 0.0, 0.0, 0.0), (0.78, 0.12, 0.05, 0.05)),
         "bad": ((0.40, 0.15, 0.20, 0.25), (0.20, 0.20, 0.25, 0.35))}
SD_OF = (0.014, 0.058, 0.05, 0.014)                                      # the precision (metres) of each of the four kinds of photo


def serpentine(n, flight=0, per_row=25):
    """The positions (lat, lon arrays) of `n` photos taken along parallel lines, back and forth, like a mapping flight."""
    import numpy as np
    index = np.arange(n)
    row, col = index // per_row, index % per_row
    col = np.where(row % 2 == 0, col, per_row - 1 - col)
    return 42.3 + 0.0016 * flight + 0.00012 * row, 9.1 + 0.00005 * col


def scenario_results(flights, kind="good", codes=("CRTE",), km=(12.6, 24.0, 31.5), north=0.0):
    """One FlightResult per flight, with the photos of the flight, solved from the bases `codes` (a made-up solution: no solver runs; `north`
    moves the whole solution, metres, to make a second one that differs)."""
    found = stations(codes)
    results = []
    for flight, shares in zip(flights, KINDS[kind]):
        n = len(flight["files"].photos)
        cuts = [int(round(n * sum(shares[:k + 1]))) for k in range(4)]
        cuts[3] = n
        kinds = [0] * cuts[0] + [1] * (cuts[1] - cuts[0]) + [2] * (cuts[2] - cuts[1]) + [3] * (cuts[3] - cuts[2])
        q = [{0: 1, 1: 1, 2: 2, 3: 0}[k] for k in kinds]
        sd = [SD_OF[k] for k in kinds]
        result = aeroppk_synth.planned_result([{"sd": sd, "q": q, "north": north} for _c in codes], n=n, km=list(km[:len(codes)]))
        result.files, result.events = flight["files"], result.events[:n]
        lat, lon = serpentine(n, flights.index(flight))
        for event, photo, a, b in zip(result.events, flight["files"].photos, lat, lon):
            event.photo, event.onboard = photo, (a, b, 300.0)
        for run in result.runs:                                          # the solution follows the same lines (`north` moves it)
            run.interp["lat"], run.interp["lon"] = lat + north / 111000.0, lon
        for run, code, station in zip(result.runs, codes, found):
            run.base.name = code
            run.base.spec.station = station
        pipeline.recombine(result)
        results.append(result)
    return results


def with_stations(result, rename=True):
    """The runs of a made-up result tied to real stations (the Details window and the export tell their frame); `rename`: and named after them."""
    for run, station in zip(result.runs, stations(MORE_STATIONS)):
        if rename:
            run.base.name = station.code
        run.base.spec.station = station
    return result


class FakeCamera(object):
    def __init__(self, path):
        self.photo = types.SimpleNamespace(path=path)
        self.label = os.path.basename(path)
        self.reference = types.SimpleNamespace(location=None, location_accuracy=None, location_enabled=True)


class FakeChunk(object):
    def __init__(self, key, label, paths=(), crs="WGS 84 (EPSG::4326)"):
        self.key, self.label, self.cameras = key, label, [FakeCamera(p) for p in paths]
        self.crs = types.SimpleNamespace(name=crs) if crs else None


class FakeDoc(object):
    def __init__(self, chunks, path=""):
        self.chunks, self.path = list(chunks), path
        self.chunk = self.chunks[0] if self.chunks else None


def install_document(flights, path=None, label=CHUNK_NAME):
    """A fake Metashape document (saved in a temporary folder: the backup of the positions is written next to it) with one chunk holding the
    photos of `flights`, in WGS 84. Returns the chunk."""
    path = path or os.path.join(FILES_DIR, "July.psx")
    chunk = FakeChunk(1, label, [p for f in flights for p in f["files"].photos])
    meta.app.document = FakeDoc([chunk], path=path)
    return chunk


def fake_writers():
    """The writers of the report and of the table of a test (a small file each: the PDF needs no solver) and a Metashape that takes the
    positions without converting them. Returns the list the positions written into the chunk are appended to."""
    written = []

    def write(data, path):
        with open(path, "w") as handle:
            handle.write("x")
        return 1
    report.build_pdf, report.write_csv = write, write

    def set_positions(chk, items, source=None):
        written.extend(items)
        return {"done": len(items), "missing": [], "failed": []}
    screen_export.msapi.set_camera_positions = set_positions
    screen_export.msapi.refresh = lambda: None
    screen_export.msapi.restore_camera_references = lambda chk, entries: len(entries)
    return written


# ---------------------------------------------------------------------------------------------------------------------
# What a click does to a widget
# ---------------------------------------------------------------------------------------------------------------------

class Recorder(QtCore.QObject):
    """Application-wide filter: which widgets received a mouse press."""

    def __init__(self):
        super(Recorder, self).__init__()
        self.presses = []

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.MouseButtonPress and isinstance(obj, QtWidgets.QWidget):
            self.presses.append(obj)
        return False


def mouse_click(widget, pos):
    """A left click (press, release) delivered to `widget` at `pos` (its own coordinates), as the window system would deliver it. (QtTest is
    not shipped with Metashape.)"""
    local = QtCore.QPointF(pos)
    glob = QtCore.QPointF(widget.mapToGlobal(pos))
    for kind, buttons in ((QtCore.QEvent.MouseButtonPress, QtCore.Qt.LeftButton), (QtCore.QEvent.MouseButtonRelease, QtCore.Qt.NoButton)):
        event = QtGui.QMouseEvent(kind, local, glob, QtCore.Qt.LeftButton, buttons, QtCore.Qt.NoModifier)
        QtWidgets.QApplication.sendEvent(widget, event)


def describe(widget):
    text = ""
    for attr in ("text", "toolTip"):
        try:
            value = getattr(widget, attr)()
        except Exception:
            value = ""
        if isinstance(value, str) and value.strip():
            text = value.strip().splitlines()[0][:40]
            break
    return "%s#%s '%s'" % (type(widget).__name__, widget.objectName(), text)


def interactive(root):
    """Visible, enabled, interactive widgets under `root`, in tree order (the widgets of pages that are not shown are not visible)."""
    out = []
    for widget in root.findChildren(QtWidgets.QWidget):
        if not widget.isVisible() or not widget.isEnabled():
            continue
        if isinstance(widget, BUTTON_KINDS) or type(widget).__name__ in ("InfoButton", "OrbitMark", "Glyph") and widget.toolTip():
            if isinstance(widget, QtWidgets.QLineEdit) and widget.parent() is not None and isinstance(widget.parent(), QtWidgets.QComboBox):
                continue
            if isinstance(widget, QtWidgets.QAbstractButton) and isinstance(widget.parent(), QtWidgets.QAbstractScrollArea):
                continue                                            # the corner button of a table
            where = clipped(widget)
            if where is not None and where.objectName() == "qt_scrollarea_viewport":
                continue                                            # scrolled out of a list (the basket, the time axis): it is reached by scrolling
            out.append(widget)
    return out


def widget_at(window, point):
    """The widget Qt would deliver a click at the global `point` to, inside `window` (its own hit-test, `QWidget.childAt`). The operating
    system's answer (`QApplication.widgetAt`) also depends on which other applications have a window over this one, so it is not used."""
    popup = QtWidgets.QApplication.activePopupWidget()
    if popup is not None and popup.geometry().contains(point):
        return popup.childAt(popup.mapFromGlobal(point)) or popup
    local = window.mapFromGlobal(point)
    if not window.rect().contains(local):
        return None
    return window.childAt(local) or window


def clipped(widget):
    """None when the centre of the widget is inside every ancestor's rectangle, else the ancestor that clips it."""
    point = widget.rect().center()
    node = widget
    while node.parentWidget() is not None and not node.isWindow():
        parent = node.parentWidget()
        point = node.mapTo(parent, point)
        if not parent.rect().contains(point):
            return parent
        node = parent
    return None


def covering(widget, others):
    """Other interactive widgets whose rectangle (on screen) overlaps the centre of `widget` (not its ancestors or children)."""
    centre = widget.mapToGlobal(widget.rect().center())
    out = []
    for other in others:
        if other is widget or widget.isAncestorOf(other) or other.isAncestorOf(widget):
            continue
        rect = QtCore.QRect(other.mapToGlobal(QtCore.QPoint(0, 0)), other.size())
        parent = other.parentWidget()
        while parent is not None:                                        # a row scrolled half out of a list is drawn (and hit) only inside the list
            if parent.objectName() == "qt_scrollarea_viewport":
                rect = rect.intersected(QtCore.QRect(parent.mapToGlobal(QtCore.QPoint(0, 0)), parent.size()))
                break
            parent = parent.parentWidget()
        if rect.contains(centre):
            out.append(other)
    return out


def hit_problems(window, widget, others):
    """The problems of one widget (list of sentences); [] when a click on its centre lands on it."""
    problems = []
    if widget.width() < 6 or widget.height() < 6:
        problems.append("size %dx%d" % (widget.width(), widget.height()))
        return problems
    where = clipped(widget)
    if where is not None:
        problems.append("centre clipped by %s" % describe(where))
    point = widget.mapToGlobal(widget.rect().center())
    found = widget_at(window, point)
    if found is None:
        problems.append("nothing under the centre")
    elif found is not widget and not widget.isAncestorOf(found):
        problems.append("covered by %s" % describe(found))
    for other in covering(widget, others):
        problems.append("overlapped by %s" % describe(other))
    return problems


def click_problems(app, rec, window, widget):
    """A real click (events sent to the widget) at the centre, sent to the widget that Qt finds under the point; True when `widget` got the press."""
    point = widget.mapToGlobal(widget.rect().center())
    found = widget_at(window, point)
    if found is None:
        return ["no widget to click"]
    rec.presses = []
    QtCore.QTimer.singleShot(250, lambda: close_popups(app))              # a drop-down opens a menu that blocks until it is closed
    mouse_click(found, found.mapFromGlobal(point))
    try:                                                                 # (before the event loop runs: a click may rebuild the page)
        received = any(p is widget or widget.isAncestorOf(p) for p in rec.presses)
        went_to = ", ".join(describe(p) for p in rec.presses[:3])
    except RuntimeError:
        received, went_to = True, ""
    pump(app, 0.05)
    if not received:
        return ["the press went to %s" % went_to if went_to else "no press received"]
    return []


def close_popups(app):
    for _ in range(5):
        popup = QtWidgets.QApplication.activePopupWidget()
        if popup is None:
            break
        popup.close()
        pump(app, 0.05)


def state_problems(dlg):
    """What would make the window unreachable by itself: a modal window, a popup, a busy flag."""
    out = []
    modal = QtWidgets.QApplication.activeModalWidget()
    if modal is not None:
        out.append("modal window open: %s" % describe(modal))
    popup = QtWidgets.QApplication.activePopupWidget()
    if popup is not None:
        out.append("popup open: %s" % describe(popup))
    if dlg.busy():
        out.append("window busy")
    if dlg.state.basket.busy():
        out.append("a basket row is still 'running'")
    if not dlg.isEnabled():
        out.append("window disabled")
    return out


DESIGN = dialog.DESIGN                                                   # the size the window is designed for (design px: scaled on the screen)


def fit_problems(dlg):
    """What makes a click miss although every rectangle looks right: the page needs more room than the window has (Qt then grows the
    window past the bottom of the screen, or squeezes and overlaps the widgets), a widget is squeezed under its own least size, a widget
    sticks out of the window."""
    out = []
    need = dlg.layout().minimumSize()
    design = (metrics.px(DESIGN[0]), metrics.px(DESIGN[1]))
    if need.height() > min(dlg.height(), design[1]) + 1:
        out.append("the page needs %d px of height, the window has %d (design %d)" % (need.height(), dlg.height(), design[1]))
    if need.width() > min(dlg.width(), design[0]) + 1:
        out.append("the page needs %d px of width, the window has %d (design %d)" % (need.width(), dlg.width(), design[0]))
    area = shell.screen_area(dlg)
    frame = dlg.frameGeometry()
    if not (area.left() <= frame.left() and area.top() <= frame.top() and frame.right() <= area.right() and frame.bottom() <= area.bottom()):
        out.append("the window leaves the usable area of the screen: %s on %s" % (frame.getRect(), area.getRect()))
    for widget in interactive(dlg):
        hint = widget.minimumSizeHint()
        if (hint.isValid() and widget.height() < hint.height() - 2 and widget.minimumHeight() != widget.maximumHeight()
                and not isinstance(widget, (mapview.MapView, ui_views.TimelineView, widgets.NetworkFilter, QtWidgets.QHeaderView))):
            out.append("%s squeezed to %d px (needs %d)" % (describe(widget), widget.height(), hint.height()))
        top = widget.mapTo(dlg, QtCore.QPoint(0, widget.height() - 1)).y()
        if top > dlg.height():
            out.append("%s is below the window" % describe(widget))
    return out


def cut_problems(root):
    """Texts the window cuts: a label that shows "..." instead of its words (an ElidedLabel that had to elide), a plain label wider than its
    box, a button whose text does not fit. Hidden parts and the cells of tables are not looked at (a cell elides on purpose)."""
    out = []
    for widget in root.findChildren(QtWidgets.QWidget):
        if not widget.isVisible() or widget.width() < 2:
            continue
        if isinstance(widget, uikit.ElidedLabel):
            if widget._mode == QtCore.Qt.ElideMiddle:
                continue                                         # a path: cut in the middle on purpose, its tip has it all
            if isinstance(widget.parent(), screen_base.BasketRow) and widget is widget.parent().sub:
                continue                                         # the facts line of a base (private build, several bases): the tail may be cut, the tip has it all
            if widget.text() != widget.fullText():
                out.append("%s is cut: '%s'" % (describe(widget), widget.fullText()[:60]))
        elif isinstance(widget, QtWidgets.QLabel) and widget.text() and not widget.wordWrap() and not widget.pixmap():
            if widget.textFormat() != QtCore.Qt.RichText and widget.fontMetrics().horizontalAdvance(widget.text()) > widget.width() + 2:
                out.append("%s is wider than its box: '%s'" % (describe(widget), widget.text()[:60]))
        elif isinstance(widget, QtWidgets.QAbstractButton) and widget.text() and not isinstance(widget, (uikit.Segmented,)):
            if widget.sizeHint().width() > widget.width() + 1:
                out.append("%s is narrower than its text (%d of %d px)" % (describe(widget), widget.width(), widget.sizeHint().width()))
        if isinstance(widget, QtWidgets.QComboBox) and widget.currentText():
            option = QtWidgets.QStyleOptionComboBox()
            widget.initStyleOption(option)
            room = widget.style().subControlRect(QtWidgets.QStyle.CC_ComboBox, option, QtWidgets.QStyle.SC_ComboBoxEditField, widget).width()    # where the text goes
            wide = widget.fontMetrics().horizontalAdvance(widget.currentText())
            if wide > room:
                out.append("%s cuts its text (%d px of %d): '%s'" % (describe(widget), wide, room, widget.currentText()[:60]))
        if isinstance(widget, uikit.DataTable):
            out.extend(table_cut_problems(widget))
    return out


LIVE_FONT = 1.12                                                         # the Qt 5 of Metashape draws its text about this much wider than the fonts of the harness


def table_cut_problems(data_table):
    """A table whose column of a fixed width cuts its header or one of its cells (a column that takes the rest of the width elides on purpose:
    the name of a station, a flight)."""
    out = []
    table = data_table.table
    header = table.horizontalHeader()
    model = table.model()
    pad = 2 * metrics.px(tokens.S)
    for index, column in enumerate(data_table.columns):
        if header.isSectionHidden(index) or column.kind in ("check", "status"):
            continue
        section = header.sectionSize(index)
        if header.sectionSizeFromContents(index).width() > section + 1:
            out.append("%s: the header '%s' is cut (%d px of %d)" % (describe(data_table), column.title, section, header.sectionSizeFromContents(index).width()))
        mode = header.sectionResizeMode(index)
        if mode == QtWidgets.QHeaderView.Stretch:
            continue
        fm = table.fontMetrics()
        slack = LIVE_FONT if mode == QtWidgets.QHeaderView.Fixed else 1.0          # (a width written in the code is tried with the wider text of the real Qt 5)
        for row in range(model.rowCount()):
            text = model.data(model.index(row, index), QtCore.Qt.DisplayRole)
            wide = int(fm.horizontalAdvance(str(text)) * slack) + pad if text else 0
            if wide > section + 1:
                out.append("%s: '%s' is cut in column '%s' (%d px of %d)" % (describe(data_table), str(text)[:40], column.title, section, wide))
                break
    return out


# ---------------------------------------------------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------------------------------------------------

def make_dialog(app, size=None, flights=None):
    """The window with the two flights of the job read (`flights` for others), the Mavic 3 Enterprise picked, nothing that asks or opens."""
    dlg = dialog.PpkDialog()
    dlg.ask_report_options = lambda photos: True                      # the question of the PDF export is modal: a click on Export must not wait for an answer
    dlg.reject = lambda: None                                            # the Cancel button must not close the harness window
    dlg.pick_folder = lambda start: ""
    dlg.pick_file = lambda title, filt, start: ""
    dlg.pick_files = lambda title, filt, start: []
    dlg.confirm = lambda text, ok=None, danger=False: False
    dlg.output_folder = lambda: FILES_DIR                                 # the Export step writes here (a click on Export is a real one)
    dlg.open_folder = lambda folder: None
    dlg.screens[S.EXPORT].report_data = reportdata.sample                 # (the made-up bases have no station to describe)
    dlg.show()
    pump(app, 0.3)
    dlg.state.flights = flights if flights is not None else realistic_flights()
    dlg.state.device_id = drones.READERS[0].id
    for step in (S.DRONE, S.FILES):
        dlg.screens[step].refresh()
    base = dlg.screens[S.BASE]
    base.download = lambda go_on=False: None                             # (run_qt_aeroppk_steps.py puts the real one back to test Compute)
    base.net.map.set_tiles_enabled(False)
    if size:
        dlg.resize(*size)
        pump(app, 0.3)
    return dlg


def settle(app, dlg, seconds=6.0):
    """Let the (fake) servers answer: until there is nothing left to ask."""
    net = dlg.screens[S.BASE].net
    end = time.time() + seconds
    while time.time() < end and (getattr(net, "searching", lambda: False)() or net._asking):
        pump(app, 0.1)
    pump(app, 0.2)


def fill_basket(dlg, app, mode, count, all_networks=False, done=True):
    """Put the Base step in `mode` with `count` bases in the basket (single: a click on the list or the map; own: nothing)."""
    base = dlg.screens[S.BASE]
    net = base.net
    dlg.go(S.BASE, force=True)
    base.set_mode(mode)
    pump(app, 0.2)
    if mode == S.MODE_OWN:
        return []
    net.slider.setValue(B.slider_from_radius(200))
    if all_networks:
        for action in net.netfilter._actions.values():
            if not action.isChecked():
                action.trigger()
    settle(app, dlg)
    if mode == S.MODE_SINGLE:
        if count and not dlg.state.basket.items:                         # (Aero-PPK may have picked the nearest station by itself)
            net.map.toggle_station(net.candidates[0].code)               # a click on a marker of the map
            pump(app, 0.2)
        elif not count and dlg.state.basket.items:
            net.remove(dlg.state.basket.items[0].code)
            pump(app, 0.2)
    else:
        for item in list(dlg.state.basket.items)[count:]:
            net.remove(item.code)
        pump(app, 0.2)
    infos = [{"rate": 1.0, "systems": "GRE", "receiver": "LEICA GR50", "antenna": "LEIAR25.R3 LEIT", "product": "1 s, RINEX 3", "first": None, "last": None},
             {"rate": 1.0, "systems": "GREC", "receiver": "JAVAD TRE_3S DELTA", "antenna": "TRM59800.00 NONE", "product": "1 s, RINEX 3", "first": None, "last": None},
             {"rate": 30.0, "systems": "GR", "receiver": "SEPT POLARX5", "antenna": "TRM59800.00 SCIT", "product": "30 s, RINEX 3", "first": None, "last": None}]
    for index, item in enumerate(dlg.state.basket.items):
        item.info = infos[index % 3]
        if done:
            item.state, item.base = B.DONE, types.SimpleNamespace()
    base.basket_changed()
    if dlg.state.basket.items:
        net.select(dlg.state.basket.items[len(dlg.state.basket.items) // 2].code)
    pump(app, 0.3)
    return list(dlg.state.basket.codes())


def sweep(app, rec, dlg, label, shot_name=None):
    """Hit-test every interactive widget of the window as it is now; click each one for real, then restore the step."""
    step = dlg.current_step()
    first_mode = dlg.state.mode
    base = dlg.screens[S.BASE]
    modes = list(dlg.screens[S.MODE].cards.values())                     # (the mode cards have their own test: a click changes the mode)
    others = interactive(dlg)
    bad = []
    for problem in state_problems(dlg):
        bad.append(("window", problem))
    for problem in fit_problems(dlg):
        bad.append(("fit", problem))
    first_size = getattr(dlg, "_harness_size", None)
    if first_size is None:
        dlg._harness_size = first_size = dlg.size()
    if dlg.size() != first_size:                                         # one size for every step (a page that asks for more room made the window grow under Qt 5)
        bad.append(("fit", "the window is %dx%d on this step, it was %dx%d on the first one" % (dlg.width(), dlg.height(), first_size.width(), first_size.height())))
    for problem in cut_problems(dlg):
        bad.append(("cut", problem))
    for widget in others:
        for problem in hit_problems(dlg, widget, others):
            bad.append((describe(widget), problem))
    if shot_name:
        shot(app, dlg, shot_name)
    clicks = 0
    for widget in list(others):
        try:
            if not widget.isVisible() or not widget.isEnabled() or widget.objectName() in SKIP_CLICK or widget in modes:
                continue
            if widget is dlg.next_button and step == S.BASE:
                continue                                                 # (Compute starts the real calculation: it has its own test, in run_qt_aeroppk_steps.py)
            size_before = dlg.size()
            had_banner = dlg.banner.isVisible()
            for problem in click_problems(app, rec, dlg, widget):
                bad.append((describe(widget), "click: " + problem))
            if dlg.banner.isVisible() and not had_banner:                # a message may need a few lines more (the shell makes room for it)
                dlg.clear_banner()
            elif dlg.size() != size_before:                              # a click must not make the window grow or shrink
                bad.append((describe(widget), "click: the window went from %dx%d to %dx%d" % (
                    size_before.width(), size_before.height(), dlg.width(), dlg.height())))
            clicks += 1
        except RuntimeError:                                             # the click rebuilt the page (basket rows): the widget is gone
            continue
        finally:
            close_popups(app)
            if dlg.current_step() != step:
                dlg.go(step, force=True)
                pump(app, 0.1)
    for problem in state_problems(dlg):
        bad.append(("window after the clicks", problem))
    if dlg.state.mode != first_mode:
        bad.append(("window after the clicks", "the mode changed from %s to %s" % (first_mode, dlg.state.mode)))
    check("%s: %d widgets hit, %d clicked" % (label, len(others), clicks), not bad,
          "; ".join("%s -> %s" % item for item in bad[:6]) + (" ..." if len(bad) > 6 else ""))
    return bad


def sweep_basket_rows(app, rec, dlg, label):
    """The rows of the basket are rebuilt by a click: click them one by one, each found again after the previous click."""
    net = dlg.screens[S.BASE].net
    codes = dlg.state.basket.codes()
    bad = []
    for code in codes:
        row = next((net.rows_box.itemAt(i).widget() for i in range(net.rows_box.count())
                    if net.rows_box.itemAt(i).widget().code == code), None)
        if row is None:
            bad.append((code, "no row"))
            continue
        net.rows_area.ensureWidgetVisible(row)
        pump(app, 0.05)
        others = interactive(dlg)
        for problem in hit_problems(dlg, row, others):
            bad.append((describe(row), problem))
        # a click on the left part of the row (the name), where no button sits
        point = row.mapToGlobal(QtCore.QPoint(20, row.height() // 2))
        found = widget_at(dlg, point)
        rec.presses = []
        mouse_click(found, found.mapFromGlobal(point))
        pump(app, 0.1)
        if net.selected != code:
            bad.append((describe(row), "click did not select the row (selected=%r)" % net.selected))
    check("%s: %d basket rows selectable by a click" % (label, len(codes)), not bad, "; ".join("%s -> %s" % item for item in bad[:4]))


def click_map_station(app, rec, dlg, code):
    """A real click on the marker of a station on the map; True when the basket changed."""
    net = dlg.screens[S.BASE].net
    before = list(dlg.state.basket.codes())
    x, y = net.map.station_position(code)
    point = net.map.mapToGlobal(QtCore.QPoint(int(x), int(y)))
    found = widget_at(dlg, point)
    if found is not net.map:
        return False, "covered by %s" % (describe(found) if found is not None else "nothing")
    mouse_click(net.map, QtCore.QPoint(int(x), int(y)))
    pump(app, 0.1)
    return dlg.state.basket.codes() != before, ""


def check_choice(app, rec, dlg):
    """The stations of the single base mode: the list of the nearest stations with data (what the made-up servers say: `_fakes.fake_survey`)."""
    base = dlg.screens[S.BASE]
    net = base.net
    dlg.go(S.BASE, force=True)
    base.set_mode(S.MODE_SINGLE)
    for code in list(dlg.state.basket.codes()):                          # (the clicks of the sweeps left a station in the basket)
        dlg.state.basket.remove(code)
    net.selected, net.manual, net.dropped = "", set(), set()
    net.slider.setValue(B.slider_from_radius(100))
    settle(app, dlg)
    table = net.station_list.table
    codes = list(net._list_codes)
    enabled = [bool(table.item(r, 0).flags() & QtCore.Qt.ItemIsSelectable) for r in range(table.rowCount())]
    offers = [_fakes.fake_survey(c, None, 1) for c in net.candidates]
    wanted = [o.station.code for o in offers if o.available() and o.interval <= 1.0][:screen_base.LIST_ROWS]      # ("1 s data only" is ticked)
    usable = [c for c, e in zip(codes, enabled) if e]
    check("single base: the list shows the nearest stations with 1 s data, at most %d" % screen_base.LIST_ROWS,
          len(codes) >= 2 and usable == wanted, "%s, wanted %s" % (usable, wanted))
    check("single base: the stations with nothing that day come after, greyed and out of reach of a click",
          enabled[:len(usable)] == [True] * len(usable) and all("no data that day" in table.item(r, 1).text() and not enabled[r]
                                                                for r in range(len(usable), table.rowCount())),
          str([(c, e, table.item(r, 1).text()) for r, (c, e) in enumerate(zip(codes, enabled))]))
    rect = table.visualRect(table.model().index(1, 0))
    point = table.viewport().mapToGlobal(rect.center())
    found = widget_at(dlg, point)
    mouse_click(found, found.mapFromGlobal(point))
    pump(app, 0.3)
    check("single base: a click on a row of the list puts that station alone in the basket", dlg.state.basket.codes() == [codes[1]],
          str(dlg.state.basket.codes()))
    base.set_mode(S.MODE_SINGLE)


def check_modes(app, rec, dlg, label):
    """The cards of the Mode step, clicked for real: the mode changes, the Base step shows the page of the mode, and every page can be reached."""
    base = dlg.screens[S.BASE]
    mode_screen = dlg.screens[S.MODE]
    modes = (S.MODE_OWN, S.MODE_SINGLE)
    for mode in modes:
        dlg.go(S.MODE, force=True)
        pump(app, 0.2)
        card = mode_screen.cards[mode]
        centre = card.mapToGlobal(card.rect().center())
        found = widget_at(dlg, centre)
        ok = found is card
        if ok:
            mouse_click(found, found.mapFromGlobal(centre))
            pump(app, 0.3)
        check("%s: a click on the '%s' card changes the mode" % (label, mode), ok and dlg.state.mode == mode,
              "covered by %s" % describe(found) if not ok else "mode is %s" % dlg.state.mode)
        check("%s: the other cards are not picked" % label, [m for m, c in mode_screen.cards.items() if c.isChecked()] == [mode])
        dlg.go(S.BASE, force=True)
        settle(app, dlg)
        check("%s: the Base step shows the page of '%s'" % (label, mode), base.stack.currentIndex() == (1 if mode == S.MODE_OWN else 0),
              str(base.stack.currentIndex()))
        sweep(app, rec, dlg, "%s / %s mode" % (label, mode), "hit_mode_%s" % mode)


def show_results(app, dlg, results, recombine=False):
    """Put results on the Result step as if the calculation had ended."""
    if recombine:
        for result in results:
            pipeline.recombine(result)
    dlg.state.results = results
    res = dlg.screens[S.RESULTS]
    res.mode, res.errors = "done", []
    dlg.go(S.RESULTS, force=True)
    res._render()
    res._show_mode()
    pump(app, 0.3)
    return res


def sweep_window(app, window, label):
    """Hit-test the controls of a window of its own (Details, Compare): the centre of each control is reached by the mouse, and the window
    needs no more than the screen gives."""
    pump(app, 0.3)
    others = interactive(window)
    bad = []
    for widget in others:
        for problem in hit_problems(window, widget, others):
            bad.append((describe(widget), problem))
    area = shell.screen_area(window)
    frame = window.frameGeometry()
    if not (area.left() <= frame.left() and area.top() <= frame.top() and frame.right() <= area.right() and frame.bottom() <= area.bottom()):
        bad.append(("window", "leaves the usable area of the screen: %s on %s" % (frame.getRect(), area.getRect())))
    need = window.layout().minimumSize()
    if need.height() > window.height() + 1 or need.width() > window.width() + 1:
        bad.append(("window", "needs %dx%d, has %dx%d" % (need.width(), need.height(), window.width(), window.height())))
    for problem in cut_problems(window):
        bad.append(("cut", problem))
    check("%s: %d widgets hit" % (label, len(others)), not bad, "; ".join("%s -> %s" % item for item in bad[:6]))
    return bad


def sweep_crowded(app, rec, dlg, label):
    """The two fullest states of the first steps must fit the window and be reachable: a drone no reader knows (its card and its two buttons),
    and the Files step with missing files, a folder that was not read, a flight left out and a warning of the chunk."""
    state = dlg.state
    saved = (state.all_flights, state.unread, state.device_id, set(state.skipped))
    state.flights, state.unread, state.device_id = [], [("C:/Flights/July/Unknown_drone_photos", 472)], devices.OTHER
    dlg.go(S.DRONE, force=True)
    dlg.screens[S.DRONE].refresh()
    pump(app, 0.3)
    check("%s / Drone, unsupported: the two buttons and the card are shown" % label,
          dlg.screens[S.DRONE].help_row.isVisible() and dlg.screens[S.DRONE].empty.isVisible())
    sweep(app, rec, dlg, "%s / Drone step, unsupported drone" % label, "hit_drone_unsupported")
    flights = realistic_flights({0: ("obs", "nav"), 1: ("events_file",)})
    state.flights, state.unread, state.device_id = flights, [("C:/Flights/July/Misc_photos", 18), ("C:/Flights/July/Other_photos", 4)], drones.READERS[0].id
    state.skip_flight(flights[1]["files"].folder, True)
    dlg.go(S.FILES, force=True)
    files = dlg.screens[S.FILES]
    files.refresh()
    pump(app, 0.3)
    sweep(app, rec, dlg, "%s / Files step, missing files, a flight left out, folders not read" % label, "hit_files_crowded")
    state.flights, state.unread, state.device_id = saved[0], saved[1], saved[2]
    state.skipped = saved[3]
    dlg.screens[S.DRONE].refresh()
    files.refresh()


def sweep_details(app, rec, dlg, res, label):
    """The Details window, opened by its button, in each of its three views; then closed."""
    res.details_button.click()
    pump(app, 0.4)
    check("%s: the Details button opens the window" % label, res.details is not None and res.details.isVisible())
    if res.details is None:
        return
    for key in ("summary", "plan", "settings"):
        res.details.rail.set_value(key)
        res.details._on_view(key)
        pump(app, 0.2)
        sweep_window(app, res.details, "%s / Details, view %s" % (label, key))
    # at its least width the window cuts nothing either (a long line wraps, the sentence of the verdict wraps, a column is as wide as its text)
    window = res.details
    window.resize(window.layout().minimumSize().width(), window.height())
    for key in ("summary", "plan", "settings"):
        window.rail.set_value(key)
        window._on_view(key)
        pump(app, 0.2)
        check("%s / Details at its least width, view %s: nothing is cut" % (label, key), not cut_problems(window),
              "; ".join(cut_problems(window)[:4]))
    window.close()
    close_popups(app)


def sweep_compare(app, dlg, res, label):
    """The Compare window with a stored solution and the current one; then closed and emptied."""
    res.store()
    dlg.clear_banner()
    res.open_compare()
    pump(app, 0.4)
    check("%s: the Compare button opens the window" % label, res.compare_window is not None and res.compare_window.isVisible())
    if res.compare_window is None:
        return
    sweep_window(app, res.compare_window, "%s / Compare" % label)
    res.compare_window.close()
    del dlg.stored[:]
    close_popups(app)


def check_chunk_combo(app, dlg, label):
    """The drop-down of the header with a long chunk name: the name is not cut."""
    natural = [p for p in cut_problems(dlg) if "cuts its text" in p]       # (the name as the window made it, before anything forces a new measure)
    check("%s: the chunk drop-down does not cut the name of the chunk" % label, not natural, "; ".join(natural[:3]))
    rows = dlg._chunk_rows()
    row = dict(rows[0] if rows else {"key": None, "label": "", "photos": 0}, text="PPK_REWORK_TEST_ABCDEFGHI (472)")
    dlg._chunk_rows = lambda: [row]
    try:
        dlg._fill_chunks(force=True)
        pump(app, 0.2)
        problems = [p for p in cut_problems(dlg) if "cuts its text" in p]
        check("%s: the chunk drop-down does not cut a long name" % label, not problems, "; ".join(problems[:3]))
    finally:
        del dlg._chunk_rows
        dlg._fill_chunks(force=True)


def check_station_list(dlg, label):
    """The list of stations of the Base step with its longest texts (a station with nothing that day, files that cannot be used, a far one)."""
    base = dlg.screens[S.BASE].net
    if base.station_list is None or not base.station_list.isVisible():
        return
    from aerotools.tools.aeroppk.ui_common import tr
    base.station_list.set_rows([["AJAC  Ajaccio Sanguinaires", tr("aeroppk.base.no_data_short"), "1234.5 km"],
                                ["CAMP  Campo dell'Oro", tr("aeroppk.base.unusable_short"), "4.1 km"]])
    QtWidgets.QApplication.instance().processEvents()
    problems = table_cut_problems(base.station_list)
    check("%s / Base step: the list of stations cuts none of its texts" % label, not problems, "; ".join(problems[:4]))
    base._list_key = None
    base.refresh_list()


def main():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    theme.load_fonts(QtGui)
    rec = Recorder()
    app.installEventFilter(rec)
    area = app.primaryScreen().availableGeometry()
    print("screen", area.width(), area.height())
    # a test must not reach a server
    runner.fetch_base = _fakes.fake_fetch
    runner.survey_one = _fakes.fake_survey
    orbits.level_of_days = lambda days: orbits.Answer(sure=False)
    real_area = shell.screen_area
    # the screens the window is tried on: a full HD one, and a 1366 x 768 laptop with the taskbar at the bottom (the offscreen screen of the
    # harness is 800 x 600 and is no screen the window is meant for)
    fake_writers()                                                       # (a click on Export writes small files and hands the positions to nobody)
    scenarios = [("full HD", [(0, 0, 1920, 1040)]), ("laptop 1366x768", [(0, 0, 1366, 728)])]
    cases = ((S.MODE_SINGLE, 0, False), (S.MODE_SINGLE, 1, False), (S.MODE_OWN, 0, False))
    laptop_cases = ((S.MODE_SINGLE, 1, False), (S.MODE_OWN, 0, False))
    for size_label, fake_areas in scenarios:
        size = None
        shell.screen_area = lambda widget=None, areas=fake_areas: QtCore.QRect(*areas[0])
        for mode, count, all_networks in cases:
            if size_label.startswith("laptop") and (mode, count, all_networks) not in laptop_cases:
                continue                                                 # (the laptop only needs the fullest cases)
            label = "%s, %s mode, %d base(s)%s" % (size_label, mode, count, ", every network ticked" if all_networks else "")
            dlg = make_dialog(app, size)
            try:
                print("window", dlg.width(), dlg.height())
                fill_basket(dlg, app, mode, count, all_networks)
                tag = "%s_%s_%d%s" % ("fullhd" if size_label == "full HD" else "laptop", mode, count, "_all" if all_networks else "")
                if mode != S.MODE_OWN:
                    if count:
                        sweep_basket_rows(app, rec, dlg, label)
                    # the map itself: a real click on a marker takes a station in and out of the basket
                    net = dlg.screens[S.BASE].net
                    dlg.go(S.BASE, force=True)
                    if mode == S.MODE_SINGLE and not count and dlg.state.basket.items:       # (entering the step picks a station by itself)
                        net.remove(dlg.state.basket.items[0].code)
                        pump(app, 0.2)
                    free = next((s for s in net.stations if s.code not in dlg.state.basket.codes()), None)
                    if mode == S.MODE_SINGLE and count:
                        target = dlg.state.basket.codes()[0]                      # (a click on another one would replace it)
                    else:
                        target = free.code if free is not None and len(dlg.state.basket) < dlg.state.basket.limit else None
                    if target is not None:
                        changed, why = click_map_station(app, rec, dlg, target)
                        check("%s: a click on a marker of the map changes the basket" % label, changed, why)
                        if changed:
                            changed, why = click_map_station(app, rec, dlg, target)
                            check("%s: a second click puts it back as it was" % label, changed, why)
                    else:
                        check("%s: a station is free to click on the map" % label, count == 8, "none free")
                sweep(app, rec, dlg, label + " / Base step", "hit_base_" + tag)
                if mode == S.MODE_SINGLE:
                    check_station_list(dlg, label)
                    check_chunk_combo(app, dlg, label)
                if count == 3 and all_networks and size_label == "full HD":
                    check_modes(app, rec, dlg, label)
                    check_choice(app, rec, dlg)
                # the other steps
                if count or mode == S.MODE_OWN:
                    for step, name in ((S.DRONE, "Drone"), (S.FILES, "Files"), (S.MODE, "Mode")):
                        try:
                            dlg.go(step, force=True)
                            pump(app, 0.2)
                            sweep(app, rec, dlg, "%s / %s step" % (label, name), "hit_%s_%s" % (name.lower(), tag))
                        except Exception as exc:
                            check("%s / %s step opens" % (label, name), False, "%s: %s" % (type(exc).__name__, exc))
                    if count >= 3:
                        sweep_crowded(app, rec, dlg, label)
                        res = show_results(app, dlg, [with_stations(aeroppk_synth.fake_result())], True)
                        sweep(app, rec, dlg, "%s / Result step" % label, "hit_results_%s" % tag)
                        sweep_details(app, rec, dlg, res, label)
                        sweep_compare(app, dlg, res, label)
                        dlg.go(S.EXPORT, force=True)
                        pump(app, 0.3)
                        sweep(app, rec, dlg, "%s / Export step" % label, "hit_export_%s" % tag)
            finally:
                close_popups(app)
                dlg.reject = lambda: None
                dlg.chunk_timer.stop()
                QtWidgets.QWidget.hide(dlg)
                dlg.deleteLater()
                pump(app, 0.1)
    shell.screen_area = real_area
    problem = GUARD.check()
    check("real settings untouched", problem is None, str(problem))
    print("out:", OUT)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
