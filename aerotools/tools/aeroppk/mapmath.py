"""Map maths for the AeroPPK map: Web Mercator, tiles, zoom fitting, distance circles. Pure Python, no Qt.

Conventions
-----------
  - Positions are (lat, lon) in degrees, except the functions named `lonlat_*` and `*_lonlat` which use the (lon, lat)
    order of the tile world (x then y).
  - "World pixels" are the pixels of the whole Web Mercator world at a zoom level `z`: the world is `256 * 2**z` pixels
    wide and high, x grows to the east, y grows to the south. `z` may be fractional (smooth zoom).
  - Bounds are (min_lat, min_lon, max_lat, max_lon). Bounds that cross the antimeridian are not supported (the tool
    works on one survey site at a time).
  - Tiles are the OpenStreetMap "slippy map" tiles: (z, x, y) with integer z, x, y.
"""

import math
import os

from .geo import destination

TILE_SIZE = 256
MIN_ZOOM = 3
MAX_ZOOM = 18
MAX_LAT = 85.0511287798066
MAX_VIEW_TILES = 30
TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"

# Distance circles (km): the first steps that make sense for a base station search.
RADIUS_STEPS_KM = (10, 20, 30, 50, 100, 200, 300, 500, 1000, 2000)
MAX_RINGS = 6


# --------------------------------------------------------------------------
# Web Mercator
# --------------------------------------------------------------------------

def world_size(z):
    """Width (and height) in pixels of the whole world at zoom `z` (fractional allowed)."""
    return TILE_SIZE * (2.0 ** z)


def clamp_lat(lat):
    return max(-MAX_LAT, min(MAX_LAT, lat))


def lonlat_to_world(lon, lat, z):
    """(lon, lat) in degrees -> (x, y) world pixels at zoom `z`. Latitudes beyond the Mercator limit are clamped."""
    size = world_size(z)
    x = (lon + 180.0) / 360.0 * size
    s = math.sin(math.radians(clamp_lat(lat)))
    y = (0.5 - math.log((1.0 + s) / (1.0 - s)) / (4.0 * math.pi)) * size
    return x, y


def world_to_lonlat(x, y, z):
    """(x, y) world pixels at zoom `z` -> (lon, lat) in degrees. Inverse of `lonlat_to_world`."""
    size = world_size(z)
    lon = x / size * 360.0 - 180.0
    n = math.pi - 2.0 * math.pi * y / size
    lat = math.degrees(math.atan(math.sinh(n)))
    return lon, lat


def latlon_to_world(lat, lon, z):
    """Same as `lonlat_to_world` with the (lat, lon) order used everywhere else in AeroPPK."""
    return lonlat_to_world(lon, lat, z)


def world_to_latlon(x, y, z):
    lon, lat = world_to_lonlat(x, y, z)
    return lat, lon


def lonlat_to_tile(lon, lat, z):
    """Integer tile (x, y) containing (lon, lat) at the integer zoom `z`."""
    z = int(z)
    x, y = lonlat_to_world(lon, lat, z)
    count = 2 ** z
    return min(count - 1, max(0, int(math.floor(x / TILE_SIZE)))), min(count - 1, max(0, int(math.floor(y / TILE_SIZE))))


def tile_to_lonlat(x, y, z):
    """(lon, lat) of the north-west corner of tile (x, y) at zoom `z`."""
    return world_to_lonlat(x * TILE_SIZE, y * TILE_SIZE, z)


def meters_per_pixel(lat, z):
    """Ground size of one screen pixel at latitude `lat` and zoom `z` (spherical Earth)."""
    return 2.0 * math.pi * 6378137.0 * math.cos(math.radians(clamp_lat(lat))) / world_size(z)


# --------------------------------------------------------------------------
# Bounds and fitting
# --------------------------------------------------------------------------

def bounds_of(points):
    """(min_lat, min_lon, max_lat, max_lon) of a list of (lat, lon), or None when the list is empty."""
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    if not lats:
        return None
    return min(lats), min(lons), max(lats), max(lons)


def merge_bounds(*boxes):
    """Smallest bounds containing every non-None box, or None."""
    boxes = [b for b in boxes if b is not None]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def fit_zoom(bounds, width_px, height_px, margin_px=0, min_zoom=MIN_ZOOM, max_zoom=MAX_ZOOM):
    """Largest (fractional) zoom at which `bounds` fit in a `width_px` x `height_px` view with `margin_px` all around."""
    min_lat, min_lon, max_lat, max_lon = bounds
    avail_w = max(1.0, float(width_px) - 2.0 * margin_px)
    avail_h = max(1.0, float(height_px) - 2.0 * margin_px)
    x0, y1 = lonlat_to_world(min_lon, min_lat, 0)
    x1, y0 = lonlat_to_world(max_lon, max_lat, 0)
    span_x = max(x1 - x0, 1e-12)
    span_y = max(y1 - y0, 1e-12)
    # The spans above are in world pixels at zoom 0 (256 px for the whole world): at zoom z they are 2**z times bigger.
    zoom = min(math.log(avail_w / span_x, 2.0), math.log(avail_h / span_y, 2.0))
    return max(float(min_zoom), min(float(max_zoom), zoom))


def center_of(bounds):
    """(lat, lon) of the middle of `bounds` in Mercator space (so the box is centered on the screen)."""
    min_lat, min_lon, max_lat, max_lon = bounds
    x0, y0 = lonlat_to_world(min_lon, max_lat, 0)
    x1, y1 = lonlat_to_world(max_lon, min_lat, 0)
    lon, lat = world_to_lonlat((x0 + x1) / 2.0, (y0 + y1) / 2.0, 0)
    return lat, lon


def fit_view(bounds, width_px, height_px, margin_px=0, min_zoom=MIN_ZOOM, max_zoom=MAX_ZOOM):
    """(center_lat, center_lon, zoom) that shows all of `bounds`."""
    lat, lon = center_of(bounds)
    return lat, lon, fit_zoom(bounds, width_px, height_px, margin_px, min_zoom, max_zoom)


# --------------------------------------------------------------------------
# Distance circles
# --------------------------------------------------------------------------

def ring_points(lat, lon, radius_m, n=96):
    """`n` (lat, lon) points of the geodesic circle of `radius_m` metres around (lat, lon), starting north, clockwise.

    The circle is a true circle on the ground: on a Mercator map it is drawn slightly stretched north-south, as it must be.
    """
    n = max(3, int(n))
    return [destination(lat, lon, 360.0 * i / n, radius_m) for i in range(n)]


def ring_bounds(lat, lon, radius_m):
    """Bounds of a geodesic circle (sampled every 5 degrees, accurate to well under a pixel at any zoom we use)."""
    return bounds_of(ring_points(lat, lon, radius_m, 72))


def nice_radii(max_distance_m):
    """Default distance circles in km: 10, 20, 30, 50, 100, 200 ... up to the first one that reaches `max_distance_m`.

    No distance (None, 0 or negative) gives 10, 20, 30. At most MAX_RINGS circles are kept: the largest ones, because the
    small ones are lost in the middle of the map when the farthest station is hundreds of kilometres away.
    """
    if not max_distance_m or max_distance_m <= 0:
        return [10, 20, 30]
    max_km = max_distance_m / 1000.0
    chosen = []
    for step in RADIUS_STEPS_KM:
        chosen.append(step)
        if step >= max_km:
            break
    else:
        # beyond the last step: add thousands until the farthest station is covered
        step = RADIUS_STEPS_KM[-1]
        while step < max_km:
            step += 1000
            chosen.append(step)
    return chosen[-MAX_RINGS:]


def format_km(distance_m):
    """Short kilometre figure without unit: 0.6, 2.5, 21, 330."""
    km = distance_m / 1000.0
    if km < 10:
        text = "%.1f" % km
        return text
    return "%.0f" % km


def format_radius_km(radius_km):
    """Kilometre figure of a ring label: 10, 2.5."""
    if abs(radius_km - round(radius_km)) < 1e-9:
        return "%d" % round(radius_km)
    return ("%.2f" % radius_km).rstrip("0").rstrip(".")


# --------------------------------------------------------------------------
# Points
# --------------------------------------------------------------------------

def decimate(points, max_points=500):
    """At most `max_points` points, evenly spread over the list (the first one is always kept)."""
    points = list(points)
    if max_points <= 0:
        return []
    if len(points) <= max_points:
        return points
    step = len(points) / float(max_points)
    return [points[int(i * step)] for i in range(max_points)]


def convex_hull(points):
    """Convex hull (Andrew's monotone chain) of (lat, lon) points, counter-clockwise in (lon, lat) axes, no repeated end."""
    unique = sorted(set((float(p[0]), float(p[1])) for p in points))
    if len(unique) <= 2:
        return unique

    def cross(o, a, b):
        return (a[1] - o[1]) * (b[0] - o[0]) - (a[0] - o[0]) * (b[1] - o[1])

    lower = []
    for p in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def mean_position(points):
    """Mean (lat, lon) of a list of positions (fine for a survey site, not meant for points all around the globe)."""
    points = list(points)
    if not points:
        return None
    return sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points)


def nice_step(span, max_lines=8):
    """Graticule step (degrees) giving at most `max_lines` lines over `span` degrees."""
    for step in (0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 30, 45, 90):
        if span / step <= max_lines:
            return step
    return 90


# --------------------------------------------------------------------------
# Tiles
# --------------------------------------------------------------------------

def tile_path(cache_dir, z, x, y):
    """Where tile (z, x, y) lives in the disk cache: <cache_dir>/<z>/<x>/<y>.png (the OSM server layout)."""
    return os.path.join(cache_dir, str(int(z)), str(int(x)), "%d.png" % int(y))


def tile_url(z, x, y, template=TILE_URL):
    return template.format(z=int(z), x=int(x), y=int(y))


def tile_zoom_for(zoom, min_zoom=MIN_ZOOM, max_zoom=MAX_ZOOM):
    """Integer zoom of the tiles drawn for a (fractional) view zoom: the nearest one, so tiles are scaled by 0.7 to 1.4."""
    return max(min_zoom, min(max_zoom, int(math.floor(zoom + 0.5))))


def view_tiles(center_lat, center_lon, zoom, width_px, height_px, max_tiles=MAX_VIEW_TILES,
               min_zoom=MIN_ZOOM, max_zoom=MAX_ZOOM):
    """Tiles that cover a view, nearest to the middle first: list of (z, x, y, left, top, size).

    `left` and `top` are the screen position (pixels, float) of the tile's top-left corner and `size` its size on screen.
    `x` is wrapped around the antimeridian; rows beyond the poles are skipped. When more than `max_tiles` tiles would be
    needed (big window, high zoom) the tile level is lowered one step at a time (blurrier but cheaper) until the count
    fits: the view is never partly left empty and the number of requests stays small.
    """
    zoom = max(float(min_zoom), min(float(max_zoom), float(zoom)))
    cx, cy = lonlat_to_world(center_lon, center_lat, zoom)
    left_world = cx - width_px / 2.0
    top_world = cy - height_px / 2.0
    tz = tile_zoom_for(zoom, min_zoom, max_zoom)
    while True:
        scale = 2.0 ** (zoom - tz)
        size = TILE_SIZE * scale
        count = 2 ** tz
        first_x = int(math.floor(left_world / size))
        last_x = int(math.floor((left_world + width_px) / size))
        first_y = max(0, int(math.floor(top_world / size)))
        last_y = min(count - 1, int(math.floor((top_world + height_px) / size)))
        total = (last_x - first_x + 1) * max(0, last_y - first_y + 1)
        if total <= max_tiles or tz <= min_zoom:
            break
        tz -= 1
    tiles = []
    mid_x, mid_y = width_px / 2.0, height_px / 2.0
    for ty in range(first_y, last_y + 1):
        for tx in range(first_x, last_x + 1):
            left = tx * size - left_world
            top = ty * size - top_world
            tiles.append((tz, tx % count, ty, left, top, size))
    tiles.sort(key=lambda t: (t[3] + t[5] / 2.0 - mid_x) ** 2 + (t[4] + t[5] / 2.0 - mid_y) ** 2)
    return tiles[:max_tiles] if tz <= min_zoom else tiles


def cache_is_fresh(mtime, now, max_age_s=7 * 24 * 3600):
    """True when a cached tile written at `mtime` may still be used at `now` (OSM asks for at least 7 days)."""
    return (now - mtime) <= max_age_s
