"""Qt self-test of the AeroPPK map widget (mapview.py): renders PNG files and drives it with simulated mouse events.

Run it with Metashape's own Python, without starting Metashape (no pytest needed):
    "C:\\Program Files\\Agisoft\\Metashape_v22\\python\\python.exe" -B tests_qt\\run_qt_aeroppk_map.py

It also runs with any Python that has PySide6 or PySide2. The Qt platform is "offscreen" and the network is never used: the
tile fetcher is replaced by one that draws checkerboard tiles with their z/x/y written on them.

Environment variables:
  AEROTOOLS_MAP_OUT   folder for the PNG files (otherwise a temporary folder, printed at the end)

Output: one PASS / FAIL line per check, non-zero exit code on the first FAIL.
"""

import os
import shutil
import sys
import tempfile
import threading
import time
import traceback

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="aerotools_qt_map_")
OUT = os.environ.get("AEROTOOLS_MAP_OUT") or tempfile.mkdtemp(prefix="aerotools_map_")
os.makedirs(OUT, exist_ok=True)


def _prepare_qt_environment():
    """Metashape DLLs when running inside its Python, offscreen platform when available."""
    metashape_dir = os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))
    has_qt = os.path.exists(os.path.join(metashape_dir, "Qt5Core.dll"))
    offscreen = True
    if has_qt:
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(metashape_dir)
        os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(metashape_dir, "plugins"))
        offscreen = os.path.exists(os.path.join(metashape_dir, "plugins", "platforms", "qoffscreen.dll"))
    if offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"

    if os.environ.get("QT_QPA_PLATFORM") == "offscreen" and os.path.isdir(r"C:\Windows\Fonts"):
        os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")      # (the offscreen platform finds no font by itself: text would have no size)

_prepare_qt_environment()

from aerotools import i18n  # noqa: E402
from aerotools.qt import BINDING, QtCore, QtGui, QtWidgets  # noqa: E402
from aerotools.tools.aeroppk import mapmath, mapview  # noqa: E402
from aerotools.tools.aeroppk.stations import net  # noqa: E402

FAILURES = []
SITE = (45.30, 5.15)
STATIONS = [
    {"code": "STNA", "name": "Station A", "lat": 45.2999, "lon": 5.1510, "distance_m": 600.0, "selected": False, "state": "ok",
     "tooltip": ["Receiver: Trimble NetR9", "Data: 1 s, GPS + GLONASS"]},
    {"code": "STNB", "name": "Station B", "lat": 45.0470, "lon": 5.4040, "distance_m": 21000.0, "selected": False,
     "state": "warn", "tooltip": ["Data: 30 s only"]},
    {"code": "STND", "name": "Station C", "lat": 45.5850, "lon": 5.4500, "distance_m": 33000.0, "selected": False, "state": "na"},
]
PHOTOS = [(SITE[0] + dy * 0.0009, SITE[1] + dx * 0.0012) for dy in range(-6, 7) for dx in range(-9, 10)]


def check(name, condition, detail=""):
    print("{} {}{}".format("PASS" if condition else "FAIL", name, "  [{}]".format(detail) if detail else ""))
    if not condition:
        FAILURES.append(name)
    return bool(condition)


# --------------------------------------------------------------------------
# Fake tiles: checkerboard with z/x/y written on it
# --------------------------------------------------------------------------

DRAW_LOCK = threading.Lock()


def tile_png(z, x, y):
    with DRAW_LOCK:
        image = QtGui.QImage(256, 256, QtGui.QImage.Format_RGB32)
        image.fill(QtGui.QColor("#cfe3c4" if (x + y) % 2 else "#e9dfc7"))
        painter = QtGui.QPainter(image)
        painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0, 70), 1))
        painter.drawRect(0, 0, 255, 255)
        painter.setPen(QtGui.QColor("#555555"))
        font = QtGui.QFont("Arial")
        font.setPixelSize(22)
        painter.setFont(font)
        painter.drawText(QtCore.QRectF(0, 0, 256, 256), QtCore.Qt.AlignCenter, "%d/%d/%d" % (z, x, y))
        painter.end()
        data = QtCore.QByteArray()
        buffer = QtCore.QBuffer(data)
        buffer.open(QtCore.QIODevice.WriteOnly)
        image.save(buffer, "PNG")
        buffer.close()
        return bytes(data.data())


class FakeServer(object):
    """Records every request; counts how many run at the same time."""

    def __init__(self, delay=0.02, fail=False):
        self.urls = []
        self.delay = delay
        self.fail = fail
        self.lock = threading.Lock()
        self.running = 0
        self.peak = 0

    def __call__(self, url):
        with self.lock:
            self.urls.append(url)
            self.running += 1
            self.peak = max(self.peak, self.running)
        try:
            time.sleep(self.delay)
            if self.fail:
                raise IOError("offline")
            parts = url.rsplit("/", 3)
            z, x, y = int(parts[1]), int(parts[2]), int(parts[3].split(".")[0])
            return tile_png(z, x, y)
        finally:
            with self.lock:
                self.running -= 1

    def keys(self):
        out = []
        for url in self.urls:
            parts = url.rsplit("/", 3)
            out.append((int(parts[1]), int(parts[2]), int(parts[3].split(".")[0])))
        return out


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def pump(milliseconds=0, until=None, timeout=8.0):
    """Process Qt events for `milliseconds`, or until `until()` is true (at most `timeout` seconds)."""
    end = time.time() + (timeout if until else milliseconds / 1000.0)
    while time.time() < end:
        QtWidgets.QApplication.processEvents()
        if until and until():
            return True
        time.sleep(0.01)
    return False if until else True


def send_mouse(widget, kind, x, y, button, buttons):
    event = QtGui.QMouseEvent(kind, QtCore.QPointF(x, y), button, buttons, QtCore.Qt.NoModifier)
    QtWidgets.QApplication.sendEvent(widget, event)


def click(widget, x, y):
    send_mouse(widget, QtCore.QEvent.MouseButtonPress, x, y, QtCore.Qt.LeftButton, QtCore.Qt.LeftButton)
    send_mouse(widget, QtCore.QEvent.MouseButtonRelease, x, y, QtCore.Qt.LeftButton, QtCore.Qt.NoButton)


def drag(widget, x0, y0, x1, y1):
    send_mouse(widget, QtCore.QEvent.MouseButtonPress, x0, y0, QtCore.Qt.LeftButton, QtCore.Qt.LeftButton)
    for i in range(1, 6):
        send_mouse(widget, QtCore.QEvent.MouseMove, x0 + (x1 - x0) * i / 5.0, y0 + (y1 - y0) * i / 5.0,
                   QtCore.Qt.NoButton, QtCore.Qt.LeftButton)
    send_mouse(widget, QtCore.QEvent.MouseButtonRelease, x1, y1, QtCore.Qt.LeftButton, QtCore.Qt.NoButton)


def wheel(widget, x, y, notches):
    pos = QtCore.QPointF(x, y)
    event = QtGui.QWheelEvent(pos, pos, QtCore.QPoint(0, 0), QtCore.QPoint(0, int(120 * notches)), QtCore.Qt.NoButton,
                              QtCore.Qt.NoModifier, QtCore.Qt.NoScrollPhase, False)
    QtWidgets.QApplication.sendEvent(widget, event)


def make_widget(width, height, server, cache_dir, tiles=True, stations=True, radii=(10, 20, 30)):
    widget = mapview.MapView(fetch=server, cache_dir=cache_dir)
    widget.resize(width, height)
    widget.set_tiles_enabled(tiles)
    widget.set_site(SITE[0], SITE[1], PHOTOS)
    if stations:
        widget.set_stations([dict(s) for s in STATIONS])
    widget.set_radii_km(list(radii))
    return widget


def settle(widget, server=None, expect_tiles=True):
    """Paint, wait for the background tiles, paint again."""
    widget.grab()
    if expect_tiles and server is not None:
        pump(until=lambda: widget._fetcher._queue == [] and not widget._fetcher._inflight and server.urls, timeout=8)
        pump(150)
    widget.grab()


def save(widget, name):
    path = os.path.join(OUT, name)
    image = widget.grab().toImage()
    image.save(path)
    return path, image


def colors_in(image, rect):
    """Distinct (coarse) colours of an image region: tells whether something is drawn there."""
    seen = set()
    for x in range(int(rect[0]), int(rect[2]), 2):
        for y in range(int(rect[1]), int(rect[3]), 2):
            if 0 <= x < image.width() and 0 <= y < image.height():
                c = QtGui.QColor(image.pixel(x, y))
                seen.add((c.red() // 24, c.green() // 24, c.blue() // 24))
    return seen


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------

def main():
    QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    print("Qt binding: {}  platform: {}  output: {}".format(BINDING, os.environ.get("QT_QPA_PLATFORM", "native"), OUT))
    cache = tempfile.mkdtemp(prefix="aerotools_map_cache_")

    # --- 1. the full map at three sizes -----------------------------------------------------------------------------
    server = FakeServer()
    widget = make_widget(900, 640, server, cache)
    settle(widget, server)
    path, image = save(widget, "map_900x640_tiles.png")
    check("renders 900x640 with tiles", os.path.getsize(path) > 20000 and image.width() == 900)
    check("tiles were fetched", len(server.urls) >= 4, "%d requests" % len(server.urls))
    check("only 2 simultaneous connections at most", server.peak <= 2, "peak %d" % server.peak)
    z_levels = {k[0] for k in server.keys()}
    check("tile zoom stays within 3..18", z_levels and min(z_levels) >= 3 and max(z_levels) <= 18, str(sorted(z_levels)))
    check("every request is a distinct, standard OSM tile URL",
          len(set(server.urls)) == len(server.urls)
          and all(u.startswith("https://tile.openstreetmap.org/") and u.endswith(".png") for u in server.urls))
    check("no bulk prefetch: at most the tiles of one view",
          len(server.urls) <= mapmath.MAX_VIEW_TILES + 4, "%d requests" % len(server.urls))
    check("tile files are in the disk cache",
          all(os.path.isfile(mapmath.tile_path(cache, *k)) for k in set(server.keys())))
    check("attribution is shown with tiles", widget._attr_rect.width() > 100 and widget._attr_rect.bottom() >= 620)
    check("attribution text is the OSM one", mapview.ATTRIBUTION == "\u00a9 OpenStreetMap contributors")
    lat, lon, _z = widget.view()
    check("auto fit centres on the site", abs(lat - SITE[0]) < 0.1 and abs(lon - SITE[1]) < 0.15, "%.3f %.3f" % (lat, lon))
    sx, sy = widget.project(*SITE)
    check("site is inside the view", 0 < sx < 900 and 0 < sy < 640)
    # the 30 km ring fits: its west and east points are visible
    ring = mapmath.ring_points(SITE[0], SITE[1], 30000)
    pts = [widget.project(la, lo) for la, lo in ring]
    check("the 30 km circle fits in the view", min(p[0] for p in pts) >= 0 and max(p[0] for p in pts) <= 900
          and min(p[1] for p in pts) >= 0 and max(p[1] for p in pts) <= 640)
    pts = {r: [widget.project(la, lo) for la, lo in mapmath.ring_points(SITE[0], SITE[1], r * 1000.0)] for r in (10, 20, 30)}
    widths = {r: max(p[0] for p in v) - min(p[0] for p in v) for r, v in pts.items()}
    check("circles are in 10:20:30 proportion", abs(widths[20] / widths[10] - 2) < 0.02 and abs(widths[30] / widths[10] - 3) < 0.02)

    # the label layout (quadratic placement) is computed once per view, not at every hover repaint
    widget.grab()
    layout = widget._layout_cache
    sx1, sy1 = widget.station_position("STNA")
    send_mouse(widget, QtCore.QEvent.MouseMove, sx1, sy1, QtCore.Qt.NoButton, QtCore.Qt.NoButton)
    widget.grab()
    check("hovering a marker repaints without placing the labels again", widget._hover == ("station", "STNA") and widget._layout_cache is layout)
    send_mouse(widget, QtCore.QEvent.MouseMove, 5, 5, QtCore.Qt.NoButton, QtCore.Qt.NoButton)
    widget.grab()
    check("moving off a marker keeps the layout", widget._hover is None and widget._layout_cache is layout)
    click(widget, sx1, sy1)
    widget.grab()
    check("picking a station places the labels again", widget._layout_cache is not layout and "STNA" in widget.selected_codes())
    click(widget, sx1, sy1)                                                       # leave the selection as it was
    layout = widget._layout_cache
    lat0, lon0, zoom0 = widget.view()
    widget.set_view(lat0, lon0, zoom0 + 1)
    widget.grab()
    check("zooming places the labels again", widget._layout_cache is not layout)
    widget.set_view(lat0, lon0, zoom0)
    widget.grab()

    small = make_widget(480, 360, server, cache)
    settle(small, server)
    path, image = save(small, "map_480x360_tiles.png")
    check("renders 480x360", os.path.getsize(path) > 8000)
    sx0, sy0 = small.station_position("STND")
    check("farthest station visible at 480x360", 0 < sx0 < 480 and 0 < sy0 < 360)
    small.shutdown()

    big = make_widget(1920, 1080, server, cache)
    settle(big, server)
    path, image = save(big, "map_1920x1080_tiles.png")
    check("renders 1920x1080", os.path.getsize(path) > 30000)
    check("big window stays within the tile cap", len({k for k in server.keys()}) <= 3 * mapmath.MAX_VIEW_TILES)
    big.shutdown()

    # --- 2. station click, signal, order numbers ---------------------------------------------------------------------
    got = []
    widget.stationClicked.connect(got.append)
    x, y = widget.station_position("STNB")
    click(widget, x, y)
    check("click on a station emits stationClicked(code)", got == ["STNB"], str(got))
    check("click selects the station", widget.selected_codes() == ["STNB"])
    click(widget, x, y)
    check("second click unselects it", widget.selected_codes() == [] and got == ["STNB", "STNB"])
    click(widget, *widget.station_position("STND"))
    click(widget, *widget.station_position("STNA"))
    check("selection order is kept", widget.selected_codes() == ["STND", "STNA"], str(widget.selected_codes()))
    path, image = save(widget, "map_900x640_selected.png")
    check("click far from any station does nothing", (lambda n: (click(widget, 30, 600), len(got) == n)[1])(len(got)))
    check("an incoming selection list is honoured", (lambda: (
        widget.set_stations([dict(s, selected=(s["code"] in ("STNB", "STNA"))) for s in STATIONS]),
        widget.selected_codes() == ["STNB", "STNA"] or widget.selected_codes() == ["STNA", "STNB"])[1])())
    widget.set_stations([dict(s, selected=(s["code"] == "STNA"), order=1) for s in STATIONS])

    # --- 3. tooltip ---------------------------------------------------------------------------------------------------
    tip = widget.tooltip_at(*widget.station_position("STNB"))
    check("tooltip has name, distance and the given lines",
          "STNB" in tip and "Station B" in tip and "21 km" in tip and "30 s only" in tip, tip)
    check("tooltip of an empty place is empty", widget.tooltip_at(5, 300) == "")
    check("tooltip of a button", "Zoom in" in widget.tooltip_at(*(lambda r: (r.center().x(), r.center().y()))(
        widget._button_rects()["zoom_in"])))

    # --- 4. wheel zoom around the cursor ----------------------------------------------------------------------------
    widget.fit()
    widget.grab()
    z0 = widget.zoom()
    anchor = (700.0, 200.0)
    before = widget.unproject(*anchor)
    wheel(widget, anchor[0], anchor[1], 1)
    check("wheel up zooms in", widget.zoom() > z0, "%.2f -> %.2f" % (z0, widget.zoom()))
    after = widget.unproject(*anchor)
    check("the point under the cursor stays put", abs(after[0] - before[0]) < 1e-7 and abs(after[1] - before[1]) < 1e-7)
    wheel(widget, anchor[0], anchor[1], -2)
    check("wheel down zooms out", widget.zoom() < z0 + 1e-9)
    for _ in range(60):
        wheel(widget, 400, 300, 1)
    check("zoom is limited to 18", widget.zoom() == 18.0)
    for _ in range(80):
        wheel(widget, 400, 300, -1)
    check("zoom is limited to 3", widget.zoom() == 3.0)

    # --- 4b. zoomed in: photo dots and their hull; and a crowd of stations at a low zoom -------------------------------
    widget.set_view(SITE[0], SITE[1], 13.0)
    widget.grab()
    px, py = widget.project(*PHOTOS[0])
    path, image = save(widget, "map_900x640_zoom13.png")
    blue = [QtGui.QColor(image.pixel(int(px) + dx, int(py) + dy)) for dx in (-1, 0, 1) for dy in (-1, 0, 1)]
    check("photo dots are drawn in blue", any(c.blue() > c.red() + 60 for c in blue), str([c.name() for c in blue][:3]))
    check("photo dots are decimated to at most 500", len(widget._footprint) <= 500 and len(widget._hull) >= 3)
    import random
    rnd = random.Random(7)
    crowd = []
    for i in range(70):
        la, lo = 41.4 + rnd.random() * 1.3, 8.6 + rnd.random() * 1.1
        crowd.append({"code": "S%03d" % i, "name": "Station %d" % i, "lat": la, "lon": lo,
                      "distance_m": 1000.0 * i, "selected": i in (3, 11, 40), "state": rnd.choice(["ok", "ok", "warn", "na"])})
    widget.set_stations(crowd)
    widget.set_view(45.0, 5.1, 8.0)
    path, image = save(widget, "map_900x640_crowd.png")
    check("70 stations render and keep the selection order", widget.selected_codes() == ["S003", "S011", "S040"],
          str(widget.selected_codes()))
    widget.set_stations([dict(s) for s in STATIONS])

    # --- 5. pan and fit --------------------------------------------------------------------------------------------
    widget.fit()
    widget.grab()
    lat0, lon0, _z = widget.view()
    n = len(got)
    x, y = widget.station_position("STNA")
    drag(widget, x, y, x + 120, y + 60)
    lat1, lon1, _z = widget.view()
    check("drag pans the map", lon1 < lon0 and lat1 > lat0, "%.4f,%.4f -> %.4f,%.4f" % (lat0, lon0, lat1, lon1))
    check("a drag that starts on a station does not toggle it", len(got) == n)
    fit_rect = widget._button_rects()["fit"]
    click(widget, fit_rect.center().x(), fit_rect.center().y())
    lat2, lon2, z2 = widget.view()
    check("fit button brings the view back", abs(lat2 - lat0) < 1e-9 and abs(lon2 - lon0) < 1e-9)
    zin = widget._button_rects()["zoom_in"]
    click(widget, zin.center().x(), zin.center().y())
    check("+ button zooms in", widget.zoom() > z2)
    zout = widget._button_rects()["zoom_out"]
    click(widget, zout.center().x(), zout.center().y())
    click(widget, zout.center().x(), zout.center().y())
    check("- button zooms out", widget.zoom() < z2)
    send_mouse(widget, QtCore.QEvent.MouseButtonDblClick, 100, 500, QtCore.Qt.LeftButton, QtCore.Qt.LeftButton)
    check("double click on empty map fits", abs(widget.zoom() - z2) < 1e-9)
    check("fit includes selected stations", all(0 < widget.station_position(c)[0] < 900
                                               for c in widget.selected_codes()))

    # --- 6. cache: second map re-uses the disk, an old tile is fetched again, offline falls back to the cache -----------------
    again = FakeServer()
    twin = make_widget(900, 640, again, cache)
    settle(twin, again, expect_tiles=False)
    pump(400)
    check("cached tiles are re-used without network", len(again.urls) == 0, "%d requests" % len(again.urls))
    twin.shutdown()
    some = sorted(set(server.keys()))[0]
    old = mapmath.tile_path(cache, *some)
    stamp = time.time() - 8 * 86400
    os.utime(old, (stamp, stamp))
    loaded = []
    fetcher = mapview.TileFetcher(FakeServer(), cache)
    fetcher.tileLoaded.connect(lambda z, x, y, data: loaded.append((z, x, y, data)))
    fetcher.request([some])
    pump(until=lambda: loaded, timeout=5)
    check("an 8 day old tile is downloaded again", fetcher._fetch.urls and loaded and loaded[0][3])
    check("and the cache file is renewed", time.time() - os.path.getmtime(old) < 60)
    os.utime(old, (stamp, stamp))
    offline = []
    fetcher2 = mapview.TileFetcher(FakeServer(fail=True), cache)
    fetcher2.tileLoaded.connect(lambda z, x, y, data: offline.append(data))
    fetcher2.request([some])
    pump(until=lambda: offline, timeout=5)
    check("offline, an old cached tile is better than nothing", offline and offline[0])
    fetcher.stop()
    fetcher2.stop()

    # --- 7. fetch failures: placeholder, no crash, pause after 4 failures ---------------------------------------------------
    bad = FakeServer(fail=True, delay=0.0)
    broken = make_widget(900, 640, bad, tempfile.mkdtemp(prefix="aerotools_map_nocache_"))
    broken.grab()
    pump(until=lambda: broken._offline, timeout=8)
    path, image = save(broken, "map_900x640_offline.png")
    check("failing fetches do not crash and are flagged", broken._offline and os.path.getsize(path) > 8000)
    check("after 4 failures in a row the map stops asking", broken._fetcher.paused() and len(bad.urls) <= 12,
          "%d requests" % len(bad.urls))
    check("a failed tile is not asked again at once", (lambda n: (broken.grab(), pump(200), len(bad.urls) == n)[2])(len(bad.urls)))
    broken.shutdown()

    # --- 8. no network at all -------------------------------------------------------------------------------------------
    quiet = FakeServer()
    plain = make_widget(900, 640, quiet, cache, tiles=False)
    plain.set_stations([dict(s, selected=(s["code"] == "STNB"), order=1) for s in STATIONS])
    plain.grab()
    pump(300)
    path, image = save(plain, "map_900x640_plain.png")
    check("tiles disabled: renders a plain map", os.path.getsize(path) > 8000)
    check("tiles disabled: no request at all", len(quiet.urls) == 0)
    check("tiles disabled: no OSM attribution (nothing from OSM is shown)", plain._attr_rect.isEmpty())
    plain.set_tiles_enabled(True)
    plain.grab()
    pump(until=lambda: len(plain._tiles) > 0, timeout=5)        # served from the disk cache: no request needed
    check("tiles can be switched back on", len(plain._tiles) > 0 and plain.tiles_enabled())
    plain.shutdown()

    # --- 9. identifying User-Agent on the real download path (urlopen is faked) ---------------------------------------
    seen = {}

    class _Answer(object):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, n=-1):
            return tile_png(5, 1, 1)

    def fake_urlopen(request, timeout=None):
        seen["ua"] = request.get_header("User-agent")
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        return _Answer()

    real = mapview.urllib.request.urlopen
    mapview.urllib.request.urlopen = fake_urlopen
    try:
        data = mapview.default_fetch("https://tile.openstreetmap.org/5/1/1.png")
    finally:
        mapview.urllib.request.urlopen = real
    check("default download sends the application User-Agent", seen.get("ua") == net.USER_AGENT and "AeroPPK" in seen["ua"],
          str(seen.get("ua")))
    check("default download has a timeout", seen.get("timeout") and seen["timeout"] <= 30)
    check("error pages are never taken for tiles", not mapview.looks_like_image(b"<html>429 Too Many Requests</html>")
          and mapview.looks_like_image(data))

    # --- 10. misc ---------------------------------------------------------------------------------------------------------
    empty = mapview.MapView(fetch=FakeServer())
    empty.resize(480, 360)
    empty.grab()
    check("an empty map renders without data", empty.grab().width() == 480)
    empty.set_stations([{"code": "X", "lat": "bad", "lon": 1}, {"lat": 1}])
    empty.set_site(None, None, [(float("nan"), 1.0)])
    empty.grab()
    check("bad input is ignored", empty.stations() == [])
    empty.shutdown()
    widget.shutdown()

    shutil.rmtree(cache, ignore_errors=True)
    print()
    print("PNG files in {}".format(OUT))
    print("RESULT: {}".format("FAIL ({})".format(", ".join(FAILURES)) if FAILURES else "all checks passed"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception:
        traceback.print_exc()
        print("FAIL unexpected exception")
        code = 1
    sys.stdout.flush()
    os._exit(code)
