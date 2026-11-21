"""The satellite of the Base step of the Aero-PPK window (orbits that exist for the flight day), without network and without Metashape.

    "C:\\Program Files\\Agisoft\\Metashape_v22\\python\\python.exe" -B tests_qt\\run_qt_aeroppk_orbits.py [out folder]

A synthetic flight is put in the window and the Base step is opened on it. The servers are a fake opener that holds the file names the
real servers hold for the day (the real selection code runs): a flight of today gives white, of yesterday blue, of three weeks ago green;
a flight of two days in a row gives the lower level; no connection gives white and "Broadcast orbits only."; a slow answer for a flight
that was replaced meanwhile is ignored. Screenshots of the three states (dark theme) go to the out folder. Settings go to a temporary
LOCALAPPDATA. Prints PASS / FAIL lines.
"""

import datetime
import os
import sys
import tempfile
import threading
import time
import types
import urllib.error

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import _isolation  # noqa: E402

GUARD = _isolation.Guard("aeroppk_orbits_")
OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="aeroppk_orbits_out_")
os.makedirs(OUT, exist_ok=True)


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

from aerotools import config, theme  # noqa: E402
from aerotools.qt import QtCore, QtGui, QtWidgets  # noqa: E402

GUARD.pin(config)
from aerotools.tools.aeroppk import basket as B, dialog, gnsstime, state as S, widgets  # noqa: E402
from aerotools.uikit import tokens  # noqa: E402
from aerotools.tools.aeroppk.drones.base import PhotoEvent  # noqa: E402
from aerotools.tools.aeroppk.stations import orbits  # noqa: E402

FAILURES = []
SITE = (47.4, 8.5)
REAL_LEVEL_OF_DAYS = orbits.level_of_days


def check(name, condition, detail=""):
    print("{} {}{}".format("PASS" if condition else "FAIL", name, "  [{}]".format(detail) if detail else ""))
    if not condition:
        FAILURES.append(name)


def pump(app, seconds=0.3):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def pump_until(app, condition, seconds=5.0):
    end = time.time() + seconds
    while time.time() < end and not condition():
        app.processEvents()
        time.sleep(0.02)
    return condition()


def synthetic_flight(day, hour=10, minutes=0, spacing=2.0):
    """Thirty photos along a line around SITE, `spacing` seconds apart from `day` `hour`:`minutes` UTC, with the drone's own positions."""
    start = gnsstime.to_seconds(gnsstime.utc_to_gps(datetime.datetime(day.year, day.month, day.day, hour, minutes, 0)))
    events = []
    for i in range(30):
        event = PhotoEvent(i + 1, start + spacing * i)
        event.onboard = (SITE[0] + 0.0004 * (i % 6), SITE[1] + 0.0006 * (i // 6), 420.0)
        event.onboard_q = 50
        event.photo_name = "IMG_%04d.JPG" % (i + 1)
        events.append(event)
    files = types.SimpleNamespace(name="FLIGHT_DEMO_001", folder="demo", photos=["IMG_%04d.JPG" % (i + 1) for i in range(30)], obs=None, nav=None)
    return {"reader": None, "files": files, "events": events, "needs": [], "warnings": []}


class Servers(object):
    """What the open servers hold: the files of the levels in `have` for the days in `days`; an optional gate holds the answers back."""

    def __init__(self):
        self.have = {}
        self.error = None
        self.gate = None
        self.calls = 0

    def publish(self, level, day, source=None):
        for name, url in orbits.candidates(level, day):
            if source is None or name == source:
                self.have[url] = (100000, "application/x-gzip")
                return

    def __call__(self, request, timeout=None):
        self.calls += 1
        if self.error is not None:
            raise self.error
        if self.gate is not None:
            self.gate.wait(10)
        url = request.full_url
        if url in self.have:
            size, kind = self.have[url]
            return _Response(size, kind)
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)


class _Response(object):
    def __init__(self, size, kind):
        self.headers = {"Content-Length": str(size), "Content-Type": kind}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def main():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    theme.load_fonts(QtGui)
    servers = Servers()
    orbits.level_of_days = lambda days, **kw: REAL_LEVEL_OF_DAYS(days, opener=servers)       # the real selection, the fake servers
    dlg = dialog.PpkDialog()
    dlg.show()
    pump(app)
    base = dlg.screens[S.BASE]
    known = base.net
    known.ask = lambda station: None
    known.map.set_tiles_enabled(False)
    mark = known.orbit_mark
    today = datetime.datetime.now(datetime.timezone.utc).date()
    yesterday, old = today - datetime.timedelta(days=1), today - datetime.timedelta(days=21)
    check("no flight yet: the satellite is hidden", not mark.isVisible())

    def open_flights(flights):
        orbits.clear_cache()
        dlg.state.flights = flights
        if dlg.current_step() == S.BASE:
            known.start()
        else:
            dlg.go(S.BASE, force=True)
        pump_until(app, lambda: mark.toolTip() and "Looking" not in mark.toolTip())
        pump(app, 0.3)

    shots = {}

    def snap(key):
        """The basket panel as it is in the window (cropped from a grab of the window), and the mark drawn large on the dark ground."""
        panel = mark.parentWidget()
        corner = panel.mapTo(dlg, QtCore.QPoint(0, 0))
        crop = dlg.grab().copy(QtCore.QRect(corner, QtCore.QSize(panel.width(), min(panel.height(), 150))))
        crop.save(os.path.join(OUT, "orbit_%s.png" % key))
        big = widgets.OrbitMark()
        big.set_state(mark.level(), "x")
        big.resize(150, 150)
        big.setAutoFillBackground(True)
        palette = big.palette()
        palette.setColor(QtGui.QPalette.Window, QtGui.QColor(tokens.BG))
        big.setPalette(palette)
        shots[key] = (crop, big.grab())

    # ---- today: the day is not over, only the broadcast orbits
    open_flights([synthetic_flight(today, 0, 5)])
    check("flight of today: white", mark.level() == "broadcast" and mark.isVisible(), mark.level())
    check("flight of today: the tooltip says broadcast and when the others come", "Broadcast orbits only" in mark.toolTip() and "rapid" in mark.toolTip(), mark.toolTip())
    check("a flight of today asks no server", servers.calls == 0, str(servers.calls))
    snap("white")
    # ---- yesterday: rapid
    servers.publish("rapid", yesterday, "ESA")
    open_flights([synthetic_flight(yesterday)])
    check("flight of yesterday: blue", mark.level() == "rapid", mark.level())
    check("blue tooltip: type, latency, source, and that the solver does not use it",
          "Rapid" in mark.toolTip() and "1 day" in mark.toolTip() and "ESA" in mark.toolTip() and "uses the broadcast orbits" in mark.toolTip(), mark.toolTip())
    snap("blue")
    # ---- three weeks ago: final
    servers.publish("final", old, "CODE")
    open_flights([synthetic_flight(old)])
    check("flight of three weeks ago: green", mark.level() == "final", mark.level())
    check("green tooltip: type, latency, source", "Final" in mark.toolTip() and "2 weeks" in mark.toolTip() and "CODE" in mark.toolTip(), mark.toolTip())
    snap("green")
    # ---- the flight changes: the icon follows
    open_flights([synthetic_flight(yesterday)])
    check("the flight changes: the icon follows (green to blue)", mark.level() == "rapid", mark.level())
    # ---- a flight across midnight: the lower level of its two days
    first, second = today - datetime.timedelta(days=5), today - datetime.timedelta(days=4)
    servers.publish("final", first, "ESA")
    servers.publish("rapid", second, "GFZ")
    open_flights([synthetic_flight(first, 23, 40, spacing=90.0)])
    check("the flight days are the two dates", known.orbit_days == (first, second), str(known.orbit_days))
    check("a flight across midnight takes the lower level of its two days", mark.level() == "rapid", mark.level())
    open_flights([synthetic_flight(first, 10, 0)])
    check("the same first day alone is final", mark.level() == "final", mark.level())
    # ---- offline: silent, white
    servers.error = urllib.error.URLError("no connection")
    open_flights([synthetic_flight(old)])
    check("offline: white", mark.level() == "broadcast", mark.level())
    check("offline: the tooltip is just 'Broadcast orbits only.'", mark.toolTip().replace("<p>", "").replace("</p>", "") == "Broadcast orbits only.", mark.toolTip())
    check("offline: the Base step still works", known.isVisible() and dlg.state.flights)
    servers.error = None
    # ---- a slow answer for a flight that was replaced is ignored
    orbits.clear_cache()
    servers.gate = threading.Event()
    dlg.state.flights = [synthetic_flight(old)]
    known.start()
    pump(app, 0.3)
    check("while the servers answer, the tooltip says so", "Looking" in mark.toolTip(), mark.toolTip())
    slow = known.orbit_days
    servers.publish("rapid", yesterday, "ESA")
    dlg.state.flights = [synthetic_flight(yesterday)]
    known.orbit_days = None
    servers.gate.set()
    known.start()
    pump_until(app, lambda: mark.level() == "rapid", 5.0)
    pump(app, 0.5)
    check("a late answer of the replaced flight does not overwrite the current one", mark.level() == "rapid" and known.orbit_days != slow, mark.level())
    servers.gate = None
    # ---- the pictures: the three states side by side, enlarged
    width = sum(shots[k][0].width() for k in ("white", "blue", "green")) + 40
    height = shots["white"][0].height() + shots["white"][1].height() + 40
    sheet = QtGui.QPixmap(width, height)
    sheet.fill(QtGui.QColor(theme.WINDOW))
    painter = QtGui.QPainter(sheet)
    x = 10
    for key in ("white", "blue", "green"):
        crop, big = shots[key]
        painter.drawPixmap(x, 10, crop)
        painter.drawPixmap(x + (crop.width() - big.width()) // 2, 20 + crop.height(), big)
        x += crop.width() + 10
    painter.end()
    sheet.save(os.path.join(OUT, "orbit_states.png"))
    print("out:", OUT)
    orbits.level_of_days = REAL_LEVEL_OF_DAYS
    dlg.close()
    pump(app, 0.3)
    problem = GUARD.check()
    check("real settings untouched", problem is None, str(problem))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
