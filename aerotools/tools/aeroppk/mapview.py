"""The AeroPPK map: a small slippy map drawn with QPainter only (QtWebEngine does not exist in Metashape's Python).

What it shows
-------------
  - OpenStreetMap raster tiles as the background (optional, see `set_tiles_enabled`), or a plain background with a graticule.
  - The site (the mean position of the photos) and the photo footprint (small dots and their convex hull).
  - GNSS base stations: labelled triangles, green / amber / grey, clickable (they toggle their selection).
  - Concentric distance circles around the site, drawn as true geodesic circles.

Network rules (OpenStreetMap tile usage policy, https://operations.osmfoundation.org/policies/tiles/)
  - Only the tiles of the current view are requested, never a bulk prefetch, zoom limited to 3..18.
  - At most 2 connections at the same time, in background threads: the GUI thread never waits for the network.
  - A User-Agent that names the application, no cache busting, tiles kept on disk and re-used for 7 days.
  - The attribution "(c) OpenStreetMap contributors" is always visible while the tiles are on.
  After 4 failures in a row the map stops asking for 30 seconds and shows a plain background (graticule) instead.

The tile fetcher is replaceable (`set_fetcher(callable)`, callable(url) -> bytes) so tests never need the network.
Every visible text goes through `aerotools.i18n` (keys `aeroppk.map.*`, see aerotools/lang/en.py).
"""

import collections
import html
import math
import os
import threading
import time
import urllib.request

from ... import i18n
from ...qt import QtCore, QtGui, QtWidgets
from ...uikit import metrics as ui_metrics, tokens
from . import mapmath
from .geo import distance_m
from .stations import net

ATTRIBUTION = "© OpenStreetMap contributors"
COPYRIGHT_URL = "https://www.openstreetmap.org/copyright"
CACHE_DAYS = 7
WORKERS = 2
FETCH_TIMEOUT = 10
FAIL_RETRY_S = 60
FAIL_PAUSE_AFTER = 4
FAIL_PAUSE_S = 30
MEMORY_TILES = 256
MAX_DOTS = 500
RING_POINTS = 96
WHEEL_ZOOM_STEP = 0.5
BUTTON_SIZE = 28
BUTTON_GAP = 4
EDGE = 8
CLICK_RADIUS = 13
DRAG_START = 4
FIT_MARGIN = 44
FIT_MAX_ZOOM = 17.0
MIN_SITE_BOX_M = 1500.0
DEFAULT_VIEW = (46.5, 2.5, 5.0)

def state_colors():
    return {"ok": tokens.SUCCESS, "warn": tokens.WARNING, "na": tokens.TEXT_MUTED}      # read when painting (skin switch)
STATE_ORDER = {"ok": 0, "warn": 1, "na": 2}


def _alpha(value, alpha):
    color = QtGui.QColor(value)
    color.setAlpha(alpha)
    return color


def palettes():
    """Every colour of the map, from the kit tokens when it is drawn (so a skin switch reaches it). With map tiles the ground is the
    tile's own (light): its ink is the overlay ink and its halos the overlay white, whatever the skin; without tiles the map is on the
    window's own ground."""
    t = tokens
    return {
        "tiles": {
            "background": t.mix(t.ON_OVERLAY, t.OVERLAY, 0.88), "failed": t.mix(t.ON_OVERLAY, t.OVERLAY, 0.8),
            "grid": _alpha(t.OVERLAY, 22), "grid_text": t.mix(t.OVERLAY, t.ON_OVERLAY, 0.55),
            "ring": t.OVERLAY, "ring_halo": _alpha(t.ON_OVERLAY, 215), "pill": _alpha(t.ON_OVERLAY, 225),
            "text": t.OVERLAY, "halo": _alpha(t.ON_OVERLAY, 235),
        },
        "plain": {
            "background": t.BG, "failed": t.BG, "grid": _alpha(t.TEXT_STRONG, 30), "grid_text": t.TEXT_DISABLED,
            "ring": t.TEXT_MUTED, "ring_halo": _alpha(t.BG, 160), "pill": _alpha(t.BG, 225),
            "text": t.TEXT, "halo": _alpha(t.BG, 225),
        },
    }


def _t(key, **fields):
    return i18n.t(key, **fields)


def _color(value, alpha=None):
    color = QtGui.QColor(value)
    if alpha is not None:
        color.setAlpha(alpha)
    return color


def _event_xy(event):
    """Local (x, y) of a mouse or wheel event, Qt5 and Qt6."""
    getter = getattr(event, "position", None)
    if getter is not None:
        try:
            point = getter()
            return point.x(), point.y()
        except Exception:
            pass
    point = event.pos()
    return point.x(), point.y()


def _global_point(event):
    getter = getattr(event, "globalPos", None)
    if getter is not None:
        try:
            return getter()
        except Exception:
            pass
    getter = getattr(event, "globalPosition", None)
    if getter is not None:
        try:
            return getter().toPoint()
        except Exception:
            pass
    return QtGui.QCursor.pos()


def _text_width(metrics, text):
    getter = getattr(metrics, "horizontalAdvance", None)
    return float(getter(text) if getter is not None else metrics.width(text))


def looks_like_image(data):
    """True for PNG or JPEG bytes (a server error page must never be saved as a tile)."""
    return bool(data) and (data[:8] == b"\x89PNG\r\n\x1a\n" or data[:3] == b"\xff\xd8\xff")


def default_fetch(url):
    """Download one tile with the application's User-Agent. Raises on any failure."""
    request = urllib.request.Request(url, headers={"User-Agent": net.USER_AGENT})
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT) as answer:
        return answer.read(4 * 1024 * 1024)


# --------------------------------------------------------------------------
# Tile fetcher: 2 background threads, disk cache, results sent to the GUI thread by a signal
# --------------------------------------------------------------------------

class TileFetcher(QtCore.QObject):
    """Loads tiles in background threads. `tileLoaded(z, x, y, data)` is emitted in the GUI thread (data is None on failure).

    `request(keys)` replaces the waiting list with `keys` (tiles that are no longer in view are forgotten, tiles being
    downloaded are left alone). Nothing is ever fetched ahead of time.
    """

    tileLoaded = QtCore.Signal(int, int, int, object)

    def __init__(self, fetch=None, cache_dir=None, parent=None):
        super(TileFetcher, self).__init__(parent)
        self._fetch = fetch
        self._cache_dir = cache_dir
        self._cond = threading.Condition()
        self._queue = []
        self._inflight = set()
        self._threads = []
        self._stopped = False
        self._consecutive = 0
        self._pause_until = 0.0

    # -- settings ----------------------------------------------------------
    def set_fetch(self, fetch):
        self._fetch = fetch

    def set_cache_dir(self, cache_dir):
        self._cache_dir = cache_dir

    def paused(self):
        return time.time() < self._pause_until

    def pause_remaining(self):
        return max(0.0, self._pause_until - time.time())

    # -- requests ----------------------------------------------------------
    def request(self, keys):
        """Ask for the tiles `keys` ((z, x, y) tuples), nearest first. Replaces anything still waiting."""
        with self._cond:
            if self._stopped:
                return
            if self.paused():
                self._queue = []
                return
            self._queue = [k for k in keys if k not in self._inflight]
            if self._queue:
                self._start_threads()
                self._cond.notify_all()

    def stop(self):
        with self._cond:
            self._stopped = True
            self._queue = []
            self._cond.notify_all()

    def _start_threads(self):
        while len(self._threads) < WORKERS:
            thread = threading.Thread(target=self._work, name="aeroppk-tiles-%d" % len(self._threads))
            thread.daemon = True
            self._threads.append(thread)
            thread.start()

    # -- worker ------------------------------------------------------------
    def _work(self):
        while True:
            with self._cond:
                while not self._queue and not self._stopped:
                    self._cond.wait()
                if self._stopped:
                    return
                key = self._queue.pop(0)
                self._inflight.add(key)
                cache_dir, fetch = self._cache_dir, self._fetch
            try:
                data = self._load(key, cache_dir, fetch)
            except Exception:
                data = None
            with self._cond:
                self._inflight.discard(key)
                if self._stopped:
                    return
                if data is None:
                    self._consecutive += 1
                    if self._consecutive >= FAIL_PAUSE_AFTER:
                        self._pause_until = time.time() + FAIL_PAUSE_S
                        self._queue = []
                        self._consecutive = 0
                else:
                    self._consecutive = 0
            try:
                self.tileLoaded.emit(key[0], key[1], key[2], data)
            except RuntimeError:
                return                                          # the widget is gone

    @staticmethod
    def _load(key, cache_dir, fetch):
        z, x, y = key
        path = mapmath.tile_path(cache_dir, z, x, y) if cache_dir else None
        stale = None
        if path and os.path.isfile(path):
            try:
                mtime = os.path.getmtime(path)
                with open(path, "rb") as handle:
                    cached = handle.read()
            except OSError:
                cached = None
            if looks_like_image(cached):
                if mapmath.cache_is_fresh(mtime, time.time(), CACHE_DAYS * 24 * 3600):
                    return cached
                stale = cached
        try:
            data = (fetch or default_fetch)(mapmath.tile_url(z, x, y))
        except Exception:
            data = None
        if looks_like_image(data):
            if path:
                tmp = "%s.%d.part" % (path, threading.get_ident())
                try:
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(tmp, "wb") as handle:
                        handle.write(data)
                    os.replace(tmp, path)
                except OSError:
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
            return bytes(data)
        return stale                                            # offline: an old tile is better than none


# --------------------------------------------------------------------------
# The widget
# --------------------------------------------------------------------------

class MapView(QtWidgets.QWidget):
    """Slippy map with the site, the photo footprint, base stations and distance circles."""

    stationClicked = QtCore.Signal(str)

    def __init__(self, parent=None, fetch=None, cache_dir=None):
        super(MapView, self).__init__(parent)
        self.setMinimumSize(240, 180)
        self.setMouseTracking(True)
        self.setAttribute(QtCore.Qt.WA_OpaquePaintEvent, True)
        self.setCursor(QtCore.Qt.OpenHandCursor)
        self._lat, self._lon, self._zoom = DEFAULT_VIEW
        self._cx = self._cy = 0.0
        self._site = None
        self._footprint = []
        self._hull = []
        self._stations = []
        self._order = []
        self._radii = []
        self._rings = {}
        self._tiles_enabled = True
        self._auto_fit = True
        self._fit_pending = True
        self._tiles = collections.OrderedDict()
        self._failed = {}
        self._offline = False
        self._epoch = 0
        self._base = None
        self._base_key = None
        self._layout_cache = None
        self._hover = None
        self._press = None
        self._dragging = False
        self._buttons = {}
        self._attr_rect = QtCore.QRectF()
        self._fetcher = TileFetcher(fetch, cache_dir, self)
        self._fetcher.tileLoaded.connect(self._on_tile)
        self._retry = QtCore.QTimer(self)
        self._retry.setSingleShot(True)
        self._retry.timeout.connect(self._retry_tiles)
        self._apply_view()

    # ---------------------------------------------------------------- public: settings
    def set_cache_dir(self, path):
        """Folder where tiles are kept between sessions (re-used for 7 days)."""
        self._fetcher.set_cache_dir(path or None)

    def set_fetcher(self, fetch):
        """`fetch(url) -> bytes` used instead of the network (tests). None puts the real download back."""
        self._fetcher.set_fetch(fetch)

    def set_tiles_enabled(self, enabled):
        """False = no network at all: plain background with a graticule."""
        enabled = bool(enabled)
        if enabled == self._tiles_enabled:
            return
        self._tiles_enabled = enabled
        if not enabled:
            self._fetcher.request([])
            self._offline = False
        self._invalidate()

    def tiles_enabled(self):
        return self._tiles_enabled

    def shutdown(self):
        """Stop the download threads (called when the widget closes)."""
        self._fetcher.stop()

    # ---------------------------------------------------------------- public: data
    def set_site(self, lat, lon, footprint=None):
        """The site (mean photo position, `lat`/`lon` may be None to clear) and `footprint`, a list of (lat, lon) photos."""
        if lat is None or lon is None:
            self._site = None
        else:
            self._site = (float(lat), float(lon))
        points = []
        for point in (footprint or []):
            try:
                la, lo = float(point[0]), float(point[1])
            except (TypeError, ValueError, IndexError):
                continue
            if math.isfinite(la) and math.isfinite(lo):
                points.append((la, lo))
        self._hull = mapmath.convex_hull(points) if points else []
        self._footprint = mapmath.decimate(points, MAX_DOTS)
        self._rebuild_rings()
        self._request_fit()

    def set_stations(self, stations):
        """List of dicts: code, name, lat, lon, distance_m, selected, state ("ok" / "warn" / "na"), optional tooltip lines, optional
        net (short name) and net_color (the fill of the marker: the network; the state is then a dot)."""
        first = not self._stations
        clean = []
        for item in (stations or []):
            try:
                lat, lon = float(item["lat"]), float(item["lon"])
            except (KeyError, TypeError, ValueError):
                continue
            if not (math.isfinite(lat) and math.isfinite(lon)):
                continue
            distance = item.get("distance_m")
            if distance is None and self._site is not None:
                distance = distance_m(self._site[0], self._site[1], lat, lon)
            lines = item.get("tooltip") or []
            if isinstance(lines, str):
                lines = [lines]
            state = item.get("state") if item.get("state") in ("ok", "warn", "na") else "na"
            clean.append({"code": str(item.get("code", "")), "name": str(item.get("name", "") or ""), "lat": lat, "lon": lon,
                          "distance_m": None if distance is None else float(distance), "selected": bool(item.get("selected")),
                          "state": state, "tooltip": [str(line) for line in lines], "order": item.get("order"),
                          "net": str(item.get("net") or ""), "net_color": str(item.get("net_color") or "")})
        self._stations = clean
        chosen = [s for s in clean if s["selected"]]
        explicit = sorted((s for s in chosen if isinstance(s["order"], int)), key=lambda s: s["order"])
        known = [c for c in self._order if any(s["code"] == c for s in chosen)]
        order = [s["code"] for s in explicit]
        order += [c for c in known if c not in order]
        order += [s["code"] for s in chosen if s["code"] not in order]
        self._order = order
        if first and clean:
            self._request_fit()
        self.update()

    def set_radii_km(self, radii):
        """Distance circles around the site, in kilometres (for example [10, 20, 30])."""
        values = []
        for radius in (radii or []):
            try:
                value = float(radius)
            except (TypeError, ValueError):
                continue
            if value > 0 and math.isfinite(value) and value not in values:
                values.append(value)
        self._radii = sorted(values)
        self._rebuild_rings()
        self._request_fit()

    def selected_codes(self):
        """Codes of the selected stations, in selection order."""
        return [c for c in self._order if any(s["code"] == c and s["selected"] for s in self._stations)]

    def stations(self):
        return [dict(s) for s in self._stations]

    # ---------------------------------------------------------------- public: view
    def view(self):
        """(center lat, center lon, zoom)."""
        return self._lat, self._lon, self._zoom

    def zoom(self):
        return self._zoom

    def set_view(self, lat, lon, zoom):
        self._set_view(lat, lon, zoom)
        self._auto_fit = False

    def zoom_in(self):
        self.zoom_by(1.0)

    def zoom_out(self):
        self.zoom_by(-1.0)

    def zoom_by(self, steps, anchor=None):
        """Zoom by `steps` notches (a mouse wheel notch is 1.0 = half a zoom level), around `anchor` (x, y) or the middle."""
        w, h = max(1, self.width()), max(1, self.height())
        ax, ay = anchor if anchor is not None else (w / 2.0, h / 2.0)
        new_zoom = max(float(mapmath.MIN_ZOOM), min(float(mapmath.MAX_ZOOM), self._zoom + steps * WHEEL_ZOOM_STEP))
        if new_zoom == self._zoom:
            return
        lon, lat = mapmath.world_to_lonlat(self._cx + (ax - w / 2.0), self._cy + (ay - h / 2.0), self._zoom)
        nx, ny = mapmath.lonlat_to_world(lon, lat, new_zoom)
        new_lon, new_lat = mapmath.world_to_lonlat(nx - (ax - w / 2.0), ny - (ay - h / 2.0), new_zoom)
        self._set_view(new_lat, new_lon, new_zoom)
        self._auto_fit = False

    def fit(self):
        """Show the site, the distance circles, the selected stations and the photos."""
        self._auto_fit = True
        self._fit_pending = False
        box = self._fit_bounds()
        if box is None:
            self._set_view(*DEFAULT_VIEW)
            return
        lat, lon, zoom = mapmath.fit_view(box, self.width(), self.height(), FIT_MARGIN, max_zoom=FIT_MAX_ZOOM)
        self._set_view(lat, lon, zoom)

    def project(self, lat, lon):
        """Screen position (x, y) in pixels of a geographic position, for the current view."""
        x, y = mapmath.lonlat_to_world(lon, lat, self._zoom)
        return x - self._cx + self.width() / 2.0, y - self._cy + self.height() / 2.0

    def unproject(self, x, y):
        lon, lat = mapmath.world_to_lonlat(self._cx + x - self.width() / 2.0, self._cy + y - self.height() / 2.0, self._zoom)
        return lat, lon

    def station_position(self, code):
        """Screen position (x, y) of a station, or None."""
        for s in self._stations:
            if s["code"] == code:
                return self.project(s["lat"], s["lon"])
        return None

    def tooltip_at(self, x, y):
        """Tooltip text (HTML) that the mouse would show at (x, y), or an empty string."""
        return self._tooltip_for(self._hit(x, y))

    def sizeHint(self):
        return QtCore.QSize(640, 440)

    # ---------------------------------------------------------------- view internals
    def _apply_view(self):
        self._lat = max(-mapmath.MAX_LAT, min(mapmath.MAX_LAT, float(self._lat)))
        self._lon = max(-180.0, min(180.0, float(self._lon)))
        self._zoom = max(float(mapmath.MIN_ZOOM), min(float(mapmath.MAX_ZOOM), float(self._zoom)))
        self._cx, self._cy = mapmath.lonlat_to_world(self._lon, self._lat, self._zoom)

    def _set_view(self, lat, lon, zoom):
        self._lat, self._lon, self._zoom = lat, lon, zoom
        self._apply_view()
        self.update()

    def _request_fit(self):
        """Fit when the window has its real size (the data often arrives before the first paint)."""
        if self._auto_fit:
            self._fit_pending = True
            self.update()

    def _fit_bounds(self):
        boxes = []
        if self._site is not None:
            boxes.append(mapmath.bounds_of([self._site]))
            boxes.append(mapmath.ring_bounds(self._site[0], self._site[1], MIN_SITE_BOX_M))
            if self._radii:
                boxes.append(mapmath.ring_bounds(self._site[0], self._site[1], self._radii[-1] * 1000.0))
        chosen = [(s["lat"], s["lon"]) for s in self._stations if s["selected"]]
        boxes.append(mapmath.bounds_of(chosen))
        boxes.append(mapmath.bounds_of(self._footprint))
        return mapmath.merge_bounds(*boxes)

    def _rebuild_rings(self):
        self._rings = {}
        if self._site is None:
            return
        for radius in self._radii:
            self._rings[radius] = mapmath.ring_points(self._site[0], self._site[1], radius * 1000.0, RING_POINTS)

    def _invalidate(self):
        self._base_key = None
        self.update()

    # ---------------------------------------------------------------- tiles
    def _on_tile(self, z, x, y, data):
        key = (z, x, y)
        image = None
        if data:
            image = QtGui.QImage()
            if not image.loadFromData(data):
                image = None
        if image is None:
            self._failed[key] = time.time()
            self._offline = True
            if self._fetcher.paused() and not self._retry.isActive():
                self._retry.start(int(self._fetcher.pause_remaining() * 1000) + 500)
        else:
            self._tiles[key] = image
            self._tiles.move_to_end(key)
            while len(self._tiles) > MEMORY_TILES:
                self._tiles.popitem(last=False)
            self._failed.pop(key, None)
            self._offline = False
        self._epoch += 1
        self.update()

    def _retry_tiles(self):
        self._failed.clear()
        self._offline = False
        self._invalidate()

    def _tile_image(self, key):
        image = self._tiles.get(key)
        if image is not None:
            self._tiles.move_to_end(key)
        return image

    def _draw_tiles(self, painter, width, height):
        """Draw the tiles of the view. Missing ones show a larger tile from a lower zoom when we have it. Returns the keys to ask."""
        wanted = []
        now = time.time()
        for z, x, y, left, top, size in mapmath.view_tiles(self._lat, self._lon, self._zoom, width, height):
            key = (z, x, y)
            target = QtCore.QRectF(left, top, size + 0.6, size + 0.6)      # a hair of overlap hides seams between tiles
            image = self._tile_image(key)
            if image is not None:
                painter.drawImage(target, image)
                continue
            failed = now - self._failed.get(key, -1e9) < FAIL_RETRY_S
            if failed:
                painter.fillRect(target, _color(palettes()["tiles"]["failed"]))
            else:
                wanted.append(key)
            for level in range(1, 4):
                if z - level < mapmath.MIN_ZOOM:
                    break
                parent = self._tile_image((z - level, x >> level, y >> level))
                if parent is not None:
                    span = 256 >> level
                    source = QtCore.QRectF((x & ((1 << level) - 1)) * span, (y & ((1 << level) - 1)) * span, span, span)
                    painter.drawImage(target, parent, source)
                    break
        return wanted

    # ---------------------------------------------------------------- base layer (cached)
    def _base_pixmap(self):
        width, height = self.width(), self.height()
        ratio = self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else float(self.devicePixelRatio())
        key = (width, height, ratio, round(self._zoom, 5), round(self._cx, 2), round(self._cy, 2),
               self._tiles_enabled, self._epoch)
        if key == self._base_key and self._base is not None:
            return self._base
        pixmap = QtGui.QPixmap(max(1, int(width * ratio)), max(1, int(height * ratio)))
        pixmap.setDevicePixelRatio(ratio)
        palette = palettes()["tiles" if self._tiles_enabled else "plain"]
        pixmap.fill(_color(palette["background"]))
        painter = QtGui.QPainter(pixmap)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        self._draw_graticule(painter, width, height, palette)
        wanted = []
        if self._tiles_enabled:
            wanted = self._draw_tiles(painter, width, height)
        painter.end()
        self._base, self._base_key = pixmap, key
        if self._tiles_enabled:
            self._fetcher.request(wanted)
        return pixmap

    def _draw_graticule(self, painter, width, height, palette):
        lon0, lat0 = mapmath.world_to_lonlat(self._cx - width / 2.0, self._cy - height / 2.0, self._zoom)
        lon1, lat1 = mapmath.world_to_lonlat(self._cx + width / 2.0, self._cy + height / 2.0, self._zoom)
        step = mapmath.nice_step(max(1e-9, lon1 - lon0), max(1.0, width / 110.0))
        decimals = max(0, int(math.ceil(-math.log10(step) - 1e-9))) if step < 1 else 0
        font = self._font("caption")
        metrics = QtGui.QFontMetricsF(font)
        pen = QtGui.QPen(palette["grid"])
        pen.setWidthF(1.0)
        painter.setPen(pen)
        painter.setFont(font)
        labels = []
        k = int(math.floor(lon0 / step))
        while k * step <= lon1 and k < 100000:
            lon = k * step
            x, _y = mapmath.lonlat_to_world(lon, 0, self._zoom)
            sx = x - self._cx + width / 2.0
            painter.drawLine(QtCore.QPointF(sx, 0), QtCore.QPointF(sx, height))
            labels.append((sx + 3, height - 4 - EDGE * 1.5 - metrics.descent(), "%.*f" % (decimals, lon)))
            k += 1
        k = int(math.floor(min(lat0, lat1) / step))
        while k * step <= max(lat0, lat1) and k < 100000:
            lat = k * step
            _x, y = mapmath.lonlat_to_world(0, lat, self._zoom)
            sy = y - self._cy + height / 2.0
            painter.drawLine(QtCore.QPointF(0, sy), QtCore.QPointF(width, sy))
            labels.append((4, sy - 3, "%.*f" % (decimals, lat)))
            k += 1
        painter.setPen(_color(palette["grid_text"]))
        for x, y, text in labels:
            painter.drawText(QtCore.QPointF(x, y), text)

    # ---------------------------------------------------------------- overlay
    def _button_rects(self):
        rects = {}
        x = self.width() - EDGE - BUTTON_SIZE
        for index, name in enumerate(("zoom_in", "zoom_out", "fit")):
            rects[name] = QtCore.QRectF(x, EDGE + index * (BUTTON_SIZE + BUTTON_GAP), BUTTON_SIZE, BUTTON_SIZE)
        return rects

    @staticmethod
    def _font(role, bold=False):
        """A font of the kit's type scale ('caption', 'small'): the map has no size of its own."""
        return ui_metrics.font(role, strong=bold)

    @staticmethod
    def _free(rect, placed, bounds):
        if not bounds.contains(rect):
            return False
        for other in placed:
            if rect.intersects(other):
                return False
        return True

    def _text_path(self, rect, text, font):
        path = QtGui.QPainterPath()
        metrics = QtGui.QFontMetricsF(font)
        path.addText(rect.left(), rect.top() + metrics.ascent(), font, text)
        return path

    def _draw_halo_text(self, painter, rect, text, font, fg, halo, width=3.0, path=None):
        path = path if path is not None else self._text_path(rect, text, font)
        painter.strokePath(path, QtGui.QPen(halo, width, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin))
        painter.fillPath(path, fg)

    # ---------------------------------------------------------------- label layout (cached)
    def _layout(self, width, height):
        """Where every ring label, the site label and the station labels go, with their text paths.

        The placement tests each label against the ones already placed (quadratic in the number of stations), so it is done once per
        view, set of stations, selection and size, not at every repaint: moving the mouse over a marker only redraws."""
        key = (width, height, self._tiles_enabled, round(self._zoom, 5), round(self._cx, 2), round(self._cy, 2),
               tuple(s["selected"] for s in self._stations), tuple(self._order), tuple(self._radii), self._site)
        cached = self._layout_cache
        if cached is not None and cached["key"] == key and cached["stations"] is self._stations and cached["rings"] is self._rings:
            return cached
        bounds = QtCore.QRectF(2, 2, width - 4, height - 4)
        buttons = self._button_rects()
        placed = [r.adjusted(-4, -4, 4, 4) for r in buttons.values()]
        attribution = QtCore.QRectF()
        if self._tiles_enabled:
            text_w = _text_width(QtGui.QFontMetricsF(self._font("caption")), ATTRIBUTION)
            attribution = QtCore.QRectF(width - text_w - 10, height - 18, text_w + 10, 18)
            placed.append(attribution)
        rings = []
        for radius in self._radii:
            points = self._rings.get(radius)
            if not points:
                continue
            screen = [self.project(la, lo) for la, lo in points]
            rings.append((radius, screen, QtGui.QPolygonF([QtCore.QPointF(x, y) for x, y in screen])))
        site_xy = None
        if self._site is not None:
            site_xy = self.project(*self._site)
            placed.append(QtCore.QRectF(site_xy[0] - 12, site_xy[1] - 12, 24, 24))
        visible = []
        for s in self._stations:
            sx, sy = self.project(s["lat"], s["lon"])
            if -30 <= sx <= width + 30 and -30 <= sy <= height + 30:
                visible.append((s, sx, sy))
        for s, sx, sy in visible:
            radius = 17 if s["selected"] else 11
            placed.append(QtCore.QRectF(sx - radius, sy - radius, 2 * radius, 2 * radius))
        ring_labels = self._place_ring_labels([(radius, screen) for radius, screen, _polygon in rings], placed, bounds)
        ordered = sorted(visible, key=lambda v: (v[0]["selected"], -STATE_ORDER[v[0]["state"]]))
        # the site label, then the station labels: selected stations first so that they win the free space
        site_label = None
        if site_xy is not None:
            text = _t("aeroppk.map.site")
            font = self._font("small", True)
            metrics = QtGui.QFontMetricsF(font)
            rect = QtCore.QRectF(0, 0, _text_width(metrics, text), metrics.height())
            offsets = []
            for gap in (14, 28):                                   # then farther away when a station sits on the site
                offsets += [(gap, -rect.height() / 2.0), (-gap - rect.width(), -rect.height() / 2.0),
                            (-rect.width() / 2.0, -gap - rect.height()), (-rect.width() / 2.0, gap)]
            for dx, dy in offsets:
                rect.moveTo(site_xy[0] + dx, site_xy[1] + dy)
                if self._free(rect.adjusted(-2, -1, 2, 1), placed, bounds):
                    placed.append(rect.adjusted(-2, -1, 2, 1))
                    site_label = (QtCore.QRectF(rect), text, font, self._text_path(rect, text, font))
                    break
        order_index = dict((code, i) for i, code in enumerate(self._order))
        label_order = sorted(visible, key=lambda v: (0, order_index.get(v[0]["code"], 0)) if v[0]["selected"]
                             else (1, STATE_ORDER[v[0]["state"]], v[0]["distance_m"] or 0.0))
        labels = []
        for s, sx, sy in label_order:
            text = s["code"]
            if not text:
                continue
            font = self._font("small", bool(s["selected"]))
            metrics = QtGui.QFontMetricsF(font)
            rect = QtCore.QRectF(0, 0, _text_width(metrics, text), metrics.height())
            radius = 17 if s["selected"] else 11
            spot = None
            for dx, dy in ((radius + 2, -rect.height() / 2.0), (-radius - 2 - rect.width(), -rect.height() / 2.0),
                           (-rect.width() / 2.0, -radius - 2 - rect.height()), (-rect.width() / 2.0, radius + 2)):
                rect.moveTo(sx + dx, sy + dy)
                if self._free(rect.adjusted(-2, -1, 2, 1), placed, bounds):
                    placed.append(rect.adjusted(-2, -1, 2, 1))
                    spot = QtCore.QRectF(rect)
                    break
            if spot is not None:
                labels.append((s, text, font, spot, self._text_path(spot, text, font), True))
            else:                                                  # no room: shown anyway only for a chosen or pointed station
                rect.moveTo(sx + radius + 2, sy - rect.height() / 2.0)
                labels.append((s, text, font, QtCore.QRectF(rect), None, False))
        self._layout_cache = {"key": key, "stations": self._stations, "rings": self._rings, "buttons": buttons,
                              "attribution": attribution, "ring_shapes": rings, "ring_labels": ring_labels, "site_xy": site_xy,
                              "site_label": site_label, "ordered": ordered, "labels": labels}
        return self._layout_cache

    def paintEvent(self, event):
        if self._fit_pending and self.width() >= 60 and self.height() >= 60:
            self.fit()
        painter = QtGui.QPainter(self)
        painter.drawPixmap(0, 0, self._base_pixmap())
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        palette = palettes()["tiles" if self._tiles_enabled else "plain"]
        width, height = self.width(), self.height()
        layout = self._layout(width, height)
        buttons = layout["buttons"]
        self._buttons = buttons
        small = self._font("caption")
        small_metrics = QtGui.QFontMetricsF(small)
        self._attr_rect = layout["attribution"]

        self._draw_footprint(painter)
        self._draw_rings(painter, palette, layout["ring_shapes"])
        self._draw_ring_labels(painter, palette, layout["ring_labels"])
        site_xy = layout["site_xy"]
        for s, sx, sy in layout["ordered"]:
            self._draw_station(painter, s, sx, sy, hover=self._hover == ("station", s["code"]))
        if site_xy is not None:
            self._draw_site(painter, site_xy[0], site_xy[1])
        fg, halo = _color(palette["text"]), palette["halo"]
        if layout["site_label"] is not None:
            rect, text, font, path = layout["site_label"]
            site_text = _color(tokens.DANGER_SOLID) if self._tiles_enabled else _color(tokens.DANGER)
            self._draw_halo_text(painter, rect, text, font, site_text, halo, path=path)
        hovered = self._hover[1] if self._hover and self._hover[0] == "station" else None
        for s, text, font, rect, path, fitted in layout["labels"]:
            if fitted:
                self._draw_halo_text(painter, rect, text, font, fg, halo, path=path)
            elif s["selected"] or s["code"] == hovered:           # never hide the label of a chosen or pointed station
                self._draw_halo_text(painter, rect, text, font, fg, halo)
        self._draw_controls(painter, palette, small, small_metrics, buttons)
        painter.end()

    def _draw_footprint(self, painter):
        if len(self._hull) >= 3:
            polygon = QtGui.QPolygonF([QtCore.QPointF(*self.project(la, lo)) for la, lo in self._hull])
            pen = QtGui.QPen(_color(tokens.INFO), 1.6)
            pen.setJoinStyle(QtCore.Qt.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(_color(tokens.INFO, 38))
            painter.drawPolygon(polygon)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(_color(tokens.INFO, 215))
        for la, lo in self._footprint:
            x, y = self.project(la, lo)
            painter.drawEllipse(QtCore.QPointF(x, y), 2.2, 2.2)

    def _draw_rings(self, painter, palette, rings):
        """Draw the dashed circles (`rings` = the shapes of the layout)."""
        for _radius, _screen, polygon in rings:
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.setPen(QtGui.QPen(palette["ring_halo"], 4.0, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin))
            painter.drawPolygon(polygon)
            pen = QtGui.QPen(_color(palette["ring"]), 1.7)
            pen.setDashPattern([4.0, 3.0])
            pen.setJoinStyle(QtCore.Qt.RoundJoin)
            painter.setPen(pen)
            painter.drawPolygon(polygon)

    def _place_ring_labels(self, rings, placed, bounds):
        """[(rect, text)] of the labels of the circles that found room (`placed` grows)."""
        font = self._font("caption", True)
        metrics = QtGui.QFontMetricsF(font)
        preferred = [12, 36, 84, 60, 0, 24, 48, 72] + list(range(2, RING_POINTS, 4))
        found = []
        for radius, screen in rings:
            text = _t("aeroppk.map.ring", km=mapmath.format_radius_km(radius))
            rect = QtCore.QRectF(0, 0, _text_width(metrics, text) + 8, metrics.height() + 2)
            for index in preferred:
                x, y = screen[index % len(screen)]
                rect.moveCenter(QtCore.QPointF(x, y))
                if self._free(rect, placed, bounds):
                    placed.append(QtCore.QRectF(rect))
                    found.append((QtCore.QRectF(rect), text))
                    break
        return found

    def _draw_ring_labels(self, painter, palette, labels):
        font = self._font("caption", True)
        for rect, text in labels:
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(palette["pill"])
            painter.drawRoundedRect(rect, ui_metrics.px(tokens.RADIUS), ui_metrics.px(tokens.RADIUS))
            painter.setFont(font)
            painter.setPen(_color(palette["ring"]).darker(115) if self._tiles_enabled else _color(palette["ring"]))
            painter.drawText(rect, QtCore.Qt.AlignCenter, text)

    def _draw_station(self, painter, station, x, y, hover=False):
        color = _color(station.get("net_color") or state_colors()[station["state"]])        # the colour of the network when there is one
        scale = 1.18 if hover else 1.0
        triangle = QtGui.QPolygonF([QtCore.QPointF(x, y - 8.5 * scale), QtCore.QPointF(x - 7.5 * scale, y + 5.5 * scale),
                                    QtCore.QPointF(x + 7.5 * scale, y + 5.5 * scale)])
        if station["selected"]:
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.setPen(QtGui.QPen(_color(tokens.ON_OVERLAY, 235), 6.0))
            painter.drawEllipse(QtCore.QPointF(x, y), 13.5, 13.5)
            painter.setPen(QtGui.QPen(_color(tokens.OVERLAY), 3.0))
            painter.drawEllipse(QtCore.QPointF(x, y), 13.5, 13.5)
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.setPen(QtGui.QPen(_color(tokens.ON_OVERLAY, 240), 4.2, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin))
        painter.drawPolygon(triangle)
        painter.setBrush(color)
        painter.setPen(QtGui.QPen(_color(tokens.OVERLAY), 1.3, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin))
        painter.drawPolygon(triangle)
        if station.get("net_color") and station["state"] != "na":                         # then the state of the data is a small dot
            painter.setBrush(_color(state_colors()[station["state"]]))
            painter.setPen(QtGui.QPen(_color(tokens.OVERLAY), 1.1))
            painter.drawEllipse(QtCore.QPointF(x + 7.5 * scale, y + 5.5 * scale), 3.6, 3.6)
        if station["selected"] and station["code"] in self._order:
            number = str(self._order.index(station["code"]) + 1)
            centre = QtCore.QPointF(x + 13.0, y - 13.0)
            painter.setBrush(_color(tokens.OVERLAY))
            painter.setPen(QtGui.QPen(_color(tokens.ON_OVERLAY), 1.6))
            painter.drawEllipse(centre, 8.0, 8.0)
            painter.setPen(_color(tokens.ON_OVERLAY))
            painter.setFont(self._font("caption", True))
            painter.drawText(QtCore.QRectF(centre.x() - 8, centre.y() - 8, 16, 16), QtCore.Qt.AlignCenter, number)

    def _draw_site(self, painter, x, y):
        color = _color(tokens.DANGER)
        centre = QtCore.QPointF(x, y)
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.setPen(QtGui.QPen(_color(tokens.ON_OVERLAY, 240), 5.0, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
        painter.drawEllipse(centre, 7.5, 7.5)
        for dx, dy in ((-12, 0), (12, 0), (0, -12), (0, 12)):
            painter.drawLine(QtCore.QPointF(x + dx * 0.55, y + dy * 0.55), QtCore.QPointF(x + dx, y + dy))
        painter.setPen(QtGui.QPen(color, 2.2, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
        painter.drawEllipse(centre, 7.5, 7.5)
        for dx, dy in ((-12, 0), (12, 0), (0, -12), (0, 12)):
            painter.drawLine(QtCore.QPointF(x + dx * 0.55, y + dy * 0.55), QtCore.QPointF(x + dx, y + dy))
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(centre, 2.6, 2.6)

    def _draw_controls(self, painter, palette, small, small_metrics, buttons):
        hover = self._hover[1] if self._hover and self._hover[0] == "button" else None
        for name, rect in buttons.items():
            on = name == hover
            painter.setBrush(_color(tokens.ON_ACCENT if on else tokens.BG, 235))
            painter.setPen(QtGui.QPen(_color(tokens.ACCENT if on else tokens.BORDER_STRONG), 1.2))
            painter.drawRoundedRect(rect, ui_metrics.px(tokens.RADIUS), ui_metrics.px(tokens.RADIUS))
            painter.setPen(QtGui.QPen(_color(tokens.TEXT_STRONG), 1.8, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
            cx, cy = rect.center().x(), rect.center().y()
            if name in ("zoom_in", "zoom_out"):
                painter.drawLine(QtCore.QPointF(cx - 5.5, cy), QtCore.QPointF(cx + 5.5, cy))
                if name == "zoom_in":
                    painter.drawLine(QtCore.QPointF(cx, cy - 5.5), QtCore.QPointF(cx, cy + 5.5))
            else:                                                   # four corner brackets
                for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                    corner = QtCore.QPointF(cx + sx * 6.0, cy + sy * 6.0)
                    painter.drawLine(corner, QtCore.QPointF(corner.x() - sx * 4.0, corner.y()))
                    painter.drawLine(corner, QtCore.QPointF(corner.x(), corner.y() - sy * 4.0))
        painter.setFont(self._font("caption"))
        if self._tiles_enabled:
            rect = self._attr_rect
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(_color(tokens.ON_OVERLAY, 215))
            painter.drawRect(rect)
            painter.setPen(_color(tokens.LOGO_0 if self._hover == ("attribution",) else tokens.OVERLAY))
            painter.drawText(rect, QtCore.Qt.AlignCenter, ATTRIBUTION)
        if self._offline and self._tiles_enabled:
            text = _t("aeroppk.map.offline")
            room = self.width() - 3 * EDGE - BUTTON_SIZE
            shown = small_metrics.elidedText(text, QtCore.Qt.ElideRight, max(40, room - 16))
            rect = QtCore.QRectF(EDGE, EDGE, _text_width(small_metrics, shown) + 16, 22)
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(_color(tokens.BG, 225))
            painter.drawRoundedRect(rect, ui_metrics.px(tokens.RADIUS), ui_metrics.px(tokens.RADIUS))
            painter.setFont(small)
            painter.setPen(_color(tokens.WARNING))
            painter.drawText(rect, QtCore.Qt.AlignCenter, shown)

    # ---------------------------------------------------------------- mouse
    def _hit(self, x, y):
        """What is under (x, y): ("button", name), ("station", code), ("site",), ("attribution",) or None."""
        point = QtCore.QPointF(x, y)
        for name, rect in self._button_rects().items():
            if rect.contains(point):
                return ("button", name)
        if self._tiles_enabled and self._attr_rect.contains(point):
            return ("attribution",)
        best, best_d = None, CLICK_RADIUS * CLICK_RADIUS
        for s in self._stations:
            sx, sy = self.project(s["lat"], s["lon"])
            d = (sx - x) ** 2 + (sy - y) ** 2
            if d <= best_d:
                best, best_d = s["code"], d
        if best is not None:
            return ("station", best)
        if self._site is not None:
            sx, sy = self.project(*self._site)
            if (sx - x) ** 2 + (sy - y) ** 2 <= 12 * 12:
                return ("site",)
        return None

    def _tooltip_for(self, hit):
        if not hit:
            return ""
        kind = hit[0]
        if kind == "button":
            return html.escape(_t("aeroppk.map.%s" % hit[1]))
        if kind == "attribution":
            return html.escape(_t("aeroppk.map.copyright_tip"))
        if kind == "site":
            return html.escape(_t("aeroppk.map.site_tip"))
        station = next((s for s in self._stations if s["code"] == hit[1]), None)
        if station is None:
            return ""
        title = station["code"] + (" - " + station["name"] if station["name"] else "")
        lines = ["<b>%s</b>" % html.escape(title)]
        if station["distance_m"] is not None:
            lines.append(html.escape(_t("aeroppk.map.tip_distance", km=mapmath.format_km(station["distance_m"]))))
        lines.extend(html.escape(line) for line in station["tooltip"])
        if station["selected"] and station["code"] in self._order:
            lines.append(html.escape(_t("aeroppk.map.tip_order", n=self._order.index(station["code"]) + 1)))
        lines.append(html.escape(_t("aeroppk.map.tip_unpick" if station["selected"] else "aeroppk.map.tip_pick")))
        return "<br>".join(lines)

    def mousePressEvent(self, event):
        if event.button() != QtCore.Qt.LeftButton:
            return super(MapView, self).mousePressEvent(event)
        x, y = _event_xy(event)
        self._press = (x, y, self._lat, self._lon)
        self._dragging = False
        event.accept()

    def mouseMoveEvent(self, event):
        x, y = _event_xy(event)
        if self._press is not None and (event.buttons() & QtCore.Qt.LeftButton):
            px, py, lat, lon = self._press
            if not self._dragging and (x - px) ** 2 + (y - py) ** 2 >= DRAG_START * DRAG_START:
                self._dragging = True
                self.setCursor(QtCore.Qt.ClosedHandCursor)
                QtWidgets.QToolTip.hideText()
            if self._dragging:
                cx, cy = mapmath.lonlat_to_world(lon, lat, self._zoom)
                new_lon, new_lat = mapmath.world_to_lonlat(cx - (x - px), cy - (y - py), self._zoom)
                self._set_view(new_lat, new_lon, self._zoom)
                self._auto_fit = False
            return
        hit = self._hit(x, y)
        if hit != self._hover:
            self._hover = hit
            self.update()
            self.setCursor(QtCore.Qt.PointingHandCursor if hit else QtCore.Qt.OpenHandCursor)
            if not hit:
                QtWidgets.QToolTip.hideText()
        if hit:
            QtWidgets.QToolTip.showText(_global_point(event), self._tooltip_for(hit), self)

    def mouseReleaseEvent(self, event):
        if event.button() != QtCore.Qt.LeftButton or self._press is None:
            return super(MapView, self).mouseReleaseEvent(event)
        dragged = self._dragging
        self._press, self._dragging = None, False
        self.setCursor(QtCore.Qt.OpenHandCursor)
        if dragged:
            return
        x, y = _event_xy(event)
        hit = self._hit(x, y)
        if not hit:
            return
        if hit[0] == "button":
            {"zoom_in": self.zoom_in, "zoom_out": self.zoom_out, "fit": self.fit}[hit[1]]()
        elif hit[0] == "attribution":
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(COPYRIGHT_URL))
        elif hit[0] == "station":
            self.toggle_station(hit[1])

    def mouseDoubleClickEvent(self, event):
        if event.button() != QtCore.Qt.LeftButton:
            return super(MapView, self).mouseDoubleClickEvent(event)
        x, y = _event_xy(event)
        if not self._hit(x, y):
            self.fit()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta:
            self.zoom_by(delta / 120.0, _event_xy(event))
        event.accept()

    def leaveEvent(self, event):
        if self._hover is not None:
            self._hover = None
            self.update()
        QtWidgets.QToolTip.hideText()
        super(MapView, self).leaveEvent(event)

    def toggle_station(self, code):
        """Select or unselect a station (what a click does) and emit `stationClicked(code)`."""
        found = False
        for s in self._stations:
            if s["code"] == code:
                s["selected"] = not s["selected"]
                found = True
                if s["selected"]:
                    self._order = [c for c in self._order if c != code] + [code]
                else:
                    self._order = [c for c in self._order if c != code]
        if found:
            self.update()
            self.stationClicked.emit(code)

    # ---------------------------------------------------------------- misc events
    def resizeEvent(self, event):
        super(MapView, self).resizeEvent(event)
        if self._auto_fit and self.width() >= 60 and self.height() >= 60:
            self._fit_pending = True

    def closeEvent(self, event):
        self.shutdown()
        super(MapView, self).closeEvent(event)
