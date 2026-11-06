"""Aero-Facade geometry: polygons, plane frames, bounding regions, margins schematic, sizes and time estimates.

Pure numpy / math, no Qt, no Metashape. All coordinates are metres in the chunk's local east/north/up frame unless stated.

Plane frame of a facade (see model.Facade): `u` along the wall to the viewer's right, `v` up, `n` toward the cameras; a point of the
plane frame is (s along, t up, d toward the cameras) measured from the bottom-left corner of the wall.
"""

import math
from dataclasses import dataclass

import numpy as np

from .model import Margins, Method, margins_for


# --------------------------------------------------------------------------
# Polygons
# --------------------------------------------------------------------------

def _poly(polygon):
    a = np.asarray(polygon, dtype=np.float64).reshape(-1, 2)
    if len(a) > 1 and np.array_equal(a[0], a[-1]):          # a closed ring: drop the repeated last point
        a = a[:-1]
    return a


def polygon_area(polygon):
    """Absolute area of a simple polygon (shoelace), m2. Fewer than 3 points: 0."""
    a = _poly(polygon)
    if len(a) < 3:
        return 0.0
    x, y = a[:, 0], a[:, 1]
    return float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2.0)


def polygon_bbox(polygon):
    """(xmin, ymin, xmax, ymax) of the polygon."""
    a = _poly(polygon)
    if len(a) == 0:
        raise ValueError("empty polygon")
    return (float(a[:, 0].min()), float(a[:, 1].min()), float(a[:, 0].max()), float(a[:, 1].max()))


def polygon_is_valid(polygon, min_area=1.0):
    """At least 3 distinct points, a non-trivial area, and no two edges crossing (a bow-tie is refused)."""
    a = _poly(polygon)
    if len(a) < 3 or polygon_area(a) < min_area:
        return False
    return not _self_intersects(a)


def _ccw(p, q, r):
    return (r[1] - p[1]) * (q[0] - p[0]) - (q[1] - p[1]) * (r[0] - p[0])


def _segments_cross(p1, p2, p3, p4):
    d1, d2 = _ccw(p3, p4, p1), _ccw(p3, p4, p2)
    d3, d4 = _ccw(p1, p2, p3), _ccw(p1, p2, p4)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)) and d1 != 0 and d2 != 0 and d3 != 0 and d4 != 0


def _self_intersects(a):
    n = len(a)
    for i in range(n):
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue                                    # first and last edges share a vertex
            if _segments_cross(a[i], a[(i + 1) % n], a[j], a[(j + 1) % n]):
                return True
    return False


def point_in_polygon(xy, polygon):
    """Boolean mask: which of the (N, 2) points lie inside the polygon (even-odd rule, vectorised over the points)."""
    xy = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
    a = _poly(polygon)
    inside = np.zeros(len(xy), dtype=bool)
    if len(a) < 3 or len(xy) == 0:
        return inside
    x, y = xy[:, 0], xy[:, 1]
    b = np.roll(a, -1, axis=0)
    for (x0, y0), (x1, y1) in zip(a, b):
        if y0 == y1:
            continue
        crosses = (y0 > y) != (y1 > y)
        xint = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
        inside ^= crosses & (x < xint)
    return inside


def filter_points(points_xyz, polygon):
    """The points (N, 3) whose plan-view position lies inside the polygon, all heights kept. Returns (points, index array)."""
    P = np.asarray(points_xyz, dtype=np.float64).reshape(-1, 3)
    if len(P) == 0:
        return P, np.zeros(0, dtype=np.int64)
    x0, y0, x1, y1 = polygon_bbox(polygon)
    near = np.flatnonzero((P[:, 0] >= x0) & (P[:, 0] <= x1) & (P[:, 1] >= y0) & (P[:, 1] <= y1))   # cheap bbox pre-filter
    keep = near[point_in_polygon(P[near, :2], polygon)]
    return P[keep], keep


# --------------------------------------------------------------------------
# Plane frame
# --------------------------------------------------------------------------

def plane_axes(facade):
    """(u, v, n) of the facade as three numpy unit vectors."""
    return np.array(facade.u, float), np.array(facade.v, float), np.array(facade.n, float)


def rotation_matrix(facade):
    """3 x 3 nested lists whose COLUMNS are u, v, n (ENU components): plane coordinates -> ENU directions.
    This is what the adapter turns into the rotation of a Metashape region (chunk axes) or into the inverse of an OrthoProjection matrix."""
    u, v, n = plane_axes(facade)
    return [[float(u[i]), float(v[i]), float(n[i])] for i in range(3)]


def enu_to_plane(facade, points):
    """(N, 3) ENU points -> (N, 3) plane coordinates (s along, t up, d toward the cameras)."""
    P = np.asarray(points, dtype=np.float64).reshape(-1, 3) - np.array(facade.origin)
    u, v, n = plane_axes(facade)
    return np.column_stack([P @ u, P @ v, P @ n])


def plane_to_enu(facade, stn):
    """(N, 3) plane coordinates (s, t, d) -> (N, 3) ENU points."""
    A = np.asarray(stn, dtype=np.float64).reshape(-1, 3)
    u, v, n = plane_axes(facade)
    return np.array(facade.origin) + A[:, :1] * u + A[:, 1:2] * v + A[:, 2:3] * n


def distance_to_plane(facade, points):
    """Signed distance (m) of ENU points to the facade plane, positive toward the cameras."""
    return enu_to_plane(facade, points)[:, 2]


# --------------------------------------------------------------------------
# Generated bounding region around a facade
# --------------------------------------------------------------------------

def bounding_region(facade, margins):
    """The box that limits every computation for one facade: the wall rectangle grown by the margins, `depth` metres in front of
    and behind the plane. Returns a dict, in local ENU metres:

      centre   (x, y, z) of the box
      size     (sx, sy, sz) in metres along u, v, n  (= length + left + right, height + top + bottom, 2 * depth)
      rot      3 x 3 nested lists, columns u, v, n (see rotation_matrix); box axes in ENU
      box2d    (xmin, ymin, xmax, ymax) of the picture area in plane coordinates (s, t): what a 2D region takes
      depth    half thickness (m)
      origin, u, v, n   the plane frame (origin = bottom-left corner of the wall)
      corners  the 8 corners in ENU (bottom face first), for drawing and for point tests
    The adapter converts centre / size / rot into Metashape types (chunk units are not metres: multiply by chunk.transform.scale)."""
    m = margins_for(margins, facade)
    L, H = facade.length, facade.height
    s0, s1 = -m.left, L + m.right
    t0, t1 = -m.bottom, H + m.top
    sc, tc = 0.5 * (s0 + s1), 0.5 * (t0 + t1)
    centre = plane_to_enu(facade, [[sc, tc, 0.0]])[0]
    corners = plane_to_enu(facade, [[s, t, d] for d in (-m.depth, m.depth) for (s, t) in ((s0, t0), (s1, t0), (s1, t1), (s0, t1))])
    return {
        "centre": tuple(float(c) for c in centre),
        "size": (float(s1 - s0), float(t1 - t0), float(2.0 * m.depth)),
        "rot": rotation_matrix(facade),
        "box2d": (float(s0), float(t0), float(s1), float(t1)),
        "depth": float(m.depth),
        "origin": tuple(facade.origin), "u": tuple(facade.u), "v": tuple(facade.v), "n": tuple(facade.n),
        "corners": [tuple(float(c) for c in row) for row in corners],
    }


def points_in_region(points, region):
    """Boolean mask of the ENU points (N, 3) that lie inside a `bounding_region` box."""
    P = np.asarray(points, dtype=np.float64).reshape(-1, 3) - np.array(region["centre"])
    R = np.array(region["rot"], dtype=np.float64)
    loc = P @ R                                           # columns of R are the axes: local = P . axis
    half = 0.5 * np.array(region["size"], dtype=np.float64)
    return np.all(np.abs(loc) <= half, axis=1)


def region_footprint(facade, margins):
    """Plan-view outline [(x, y) x 4] of the bounding region (what the UI can draw around the wall on the orthomosaic)."""
    r = bounding_region(facade, margins)
    c = r["corners"]
    return [(c[i][0], c[i][1]) for i in (0, 1, 5, 4)]


# --------------------------------------------------------------------------
# Margins schematic (data for the drawing of screen 3)
# --------------------------------------------------------------------------

def margin_schematic(length, height, margins, width_px=240, height_px=150, pad=14):
    """Geometry of the little front view of screen 3, in pixels (y down), scaled to fit a `width_px` x `height_px` box.

    Returns dict(scale, wall=(x, y, w, h), area=(x, y, w, h), total_m=(w, h), labels) where `wall` is the facade rectangle, `area` the
    picture area (wall + margins) and `labels` the metre values to print beside each side. The area is centred in the box. A zero
    margin gives an area edge on the wall edge."""
    m = margins if isinstance(margins, Margins) else Margins()
    length, height = max(float(length), 0.1), max(float(height), 0.1)
    tw, th = length + m.left + m.right, height + m.top + m.bottom
    scale = min((width_px - 2 * pad) / tw, (height_px - 2 * pad) / th)
    ax = (width_px - tw * scale) / 2.0
    ay = (height_px - th * scale) / 2.0
    area = (ax, ay, tw * scale, th * scale)
    wall = (ax + m.left * scale, ay + m.top * scale, length * scale, height * scale)
    return {"scale": scale, "wall": wall, "area": area, "total_m": (tw, th),
            "labels": {"left": m.left, "right": m.right, "top": m.top, "bottom": m.bottom}, "box": (width_px, height_px)}


def depth_schematic(margins, wall_m=0.4, width_px=240, height_px=70, pad=10):
    """Top view of the wall and the kept depth: dict(wall=(x, y, w, h), front=(…), back=(…), scale) in pixels; the cameras are in front
    (below in the drawing). `wall_m` is the drawn wall thickness in metres."""
    m = margins if isinstance(margins, Margins) else Margins()
    total = 2.0 * m.depth + wall_m
    scale = min((height_px - 2 * pad) / total, (width_px - 2 * pad) / 6.0)
    cx = width_px / 2.0
    top = (height_px - total * scale) / 2.0
    back = (cx - 3.0 * scale, top, 6.0 * scale, m.depth * scale)
    wall = (cx - 3.0 * scale, top + m.depth * scale, 6.0 * scale, wall_m * scale)
    front = (cx - 3.0 * scale, top + (m.depth + wall_m) * scale, 6.0 * scale, m.depth * scale)
    return {"back": back, "wall": wall, "front": front, "scale": scale, "box": (width_px, height_px)}


# --------------------------------------------------------------------------
# Pixel size, picture size, file size
# --------------------------------------------------------------------------

PIXEL_SIZES = (0.005, 0.01, 0.02, 0.03, 0.05, 0.10)      # metres, offered by the UI (default 0.03)
DEFAULT_PIXEL_M = 0.03
HEAVY_MPIX = 60.0                                          # above this a picture is heavy to build and to open
TOO_BIG_MPIX = 400.0                                       # above this the tool refuses (a TIFF would be over 1 GB)


def picture_size_m(facade, margins):
    """(width, height) in metres of the picture area (wall + margins)."""
    m = margins_for(margins, facade)
    return facade.length + m.left + m.right, facade.height + m.top + m.bottom


def image_size_px(facade, margins, pixel_m):
    """(width, height) in pixels of the orthophoto at `pixel_m` metres per pixel."""
    if pixel_m <= 0:
        raise ValueError("pixel size must be positive")
    w, h = picture_size_m(facade, margins)
    return max(1, int(math.ceil(w / pixel_m - 1e-9))), max(1, int(math.ceil(h / pixel_m - 1e-9)))


def megapixels(facade, margins, pixel_m):
    w, h = image_size_px(facade, margins, pixel_m)
    return w * h / 1e6


def file_size_estimate_mb(width, height, fmt):
    """Rough size on disk of a picture: tif is uncompressed RGB, png about 1.8 bytes per pixel on photographs, jpg about 0.35.
    An estimate (+-50 %), shown as 'about'."""
    per_px = {"tif": 3.0, "tiff": 3.0, "png": 1.8, "jpg": 0.35, "jpeg": 0.35}.get(str(fmt).lower().lstrip("."), 3.0)
    return width * height * per_px / 1e6


def pixel_size_check(facade, margins, pixel_m):
    """('ok' | 'heavy' | 'too_big', megapixels): the UI turns it into a hint ('smaller pixels are heavier')."""
    mp = megapixels(facade, margins, pixel_m)
    if mp > TOO_BIG_MPIX:
        return "too_big", mp
    if mp > HEAVY_MPIX:
        return "heavy", mp
    return "ok", mp


def finest_allowed_pixel(facade, margins, max_mpix=TOO_BIG_MPIX):
    """Smallest pixel size (m, rounded up to the millimetre) that keeps the picture under `max_mpix`."""
    w, h = picture_size_m(facade, margins)
    return math.ceil(math.sqrt(w * h / (max_mpix * 1e6)) * 1000.0) / 1000.0


def georef_lines(facade, margins, pixel_m):
    """Lines of the georeferencing text file written beside each picture (so it can be placed and measured)."""
    m = margins_for(margins, facade)
    w, h = image_size_px(facade, m, pixel_m)
    return [
        "Aero-Facade georeferencing",
        "facade: %s" % facade.letter,
        "frame: local east/north/up metres of the chunk",
        "image size px: %d x %d" % (w, h),
        "pixel size m: %.5f" % pixel_m,
        "picture top-left corner in the plane (s along, t up) m: %.3f %.3f" % (-m.left, facade.height + m.top),
        "plane origin (bottom-left corner of the wall) E N U: %.4f %.4f %.4f" % facade.origin,
        "u (along the wall, to the right) E N U: %.6f %.6f %.6f" % facade.u,
        "v (up) E N U: %.6f %.6f %.6f" % facade.v,
        "n (toward the cameras) E N U: %.6f %.6f %.6f" % facade.n,
        "wall length m: %.3f" % facade.length,
        "wall height m: %.3f" % facade.height,
        "margins m (left right top bottom depth): %.2f %.2f %.2f %.2f %.2f" % (m.left, m.right, m.top, m.bottom, m.depth),
    ]


# --------------------------------------------------------------------------
# Time estimates (honest ranges)
# --------------------------------------------------------------------------

@dataclass
class TimeEstimate:
    low: float = 0.0                 # seconds
    high: float = 0.0
    basis: str = "unknown"           # 'measured' | 'estimated' | 'unknown'

    def __add__(self, other):
        order = {"unknown": 0, "estimated": 1, "measured": 2}
        basis = self.basis if order[self.basis] <= order[other.basis] else other.basis
        return TimeEstimate(self.low + other.low, self.high + other.high, basis)

    @property
    def known(self):
        return self.basis != "unknown"


# seconds = fixed + per_m2 * (area of the picture region in m2), as (low, high) for the DEM step of ONE facade.
# 'measured': Metashape 2.2.1 on CARRO / Citadelle (docs/AERO_FACADE_STUDY.md: DEM from tie points 2.2 s for 26 m x 14.7 m).
# The other two are first estimates until the adapter measures them; update THESE numbers (one place) when it does.
DEM_TIME = {
    Method.TIE_POINTS.value: dict(fixed=(1.0, 3.0), per_m2=(0.001, 0.006), basis="measured"),
    Method.DENSE_CLOUD.value: dict(fixed=(3.0, 8.0), per_m2=(0.005, 0.02), basis="estimated"),
    Method.DEPTH_MAPS.value: dict(fixed=(10.0, 30.0), per_m2=(0.03, 0.15), basis="estimated"),
}
ORTHO_TIME = dict(fixed=(1.5, 4.0), per_mpix=(4.0, 14.0), basis="measured")     # 4.4 s for 0.43 Mpix in the study


def estimate_dem_time(method, area_m2):
    """TimeEstimate (seconds) of the DEM of one facade whose picture region covers `area_m2`."""
    spec = DEM_TIME.get(Method.from_key(method).value)
    if spec is None:
        return TimeEstimate()
    a = max(0.0, float(area_m2))
    return TimeEstimate(spec["fixed"][0] + spec["per_m2"][0] * a, spec["fixed"][1] + spec["per_m2"][1] * a, spec["basis"])


def estimate_ortho_time(mpix):
    """TimeEstimate (seconds) of the orthophoto of a picture of `mpix` megapixels."""
    return TimeEstimate(ORTHO_TIME["fixed"][0] + ORTHO_TIME["per_mpix"][0] * mpix, ORTHO_TIME["fixed"][1] + ORTHO_TIME["per_mpix"][1] * mpix,
                        ORTHO_TIME["basis"])


def estimate_batch_dems(method, facades, margins, skip=()):
    """TimeEstimate for the DEMs of all not-removed facades (letters in `skip` already exist and cost nothing)."""
    total = TimeEstimate(0.0, 0.0, "measured")
    n = 0
    for f in facades:
        if f.removed or f.letter in skip:
            continue
        w, h = picture_size_m(f, margins)
        total = total + estimate_dem_time(method, w * h)
        n += 1
    if n == 0:
        return TimeEstimate(0.0, 0.0, "measured")
    return total


def estimate_batch_orthos(facades, margins, pixel_m):
    total = TimeEstimate(0.0, 0.0, "measured")
    for f in facades:
        if not f.removed:
            total = total + estimate_ortho_time(megapixels(f, margins, pixel_m))
    return total


def suggest_default_margins(facade=None):
    """Margins to preselect: 1 m on the four sides and 2 m depth (the spec defaults), adapted to the facade: half a metre for a very
    short wall, and more depth for a leaning wall (a vertical plane through its middle is up to H x tan(tilt) / 2 away from the top and
    the foot). Rounded to 0.5 m."""
    m = Margins()
    if facade is None:
        return m
    if facade.length < 8.0:
        m.set_left(0.5)
    if facade.height < 6.0:
        m.set_top(0.5)
    depth = 2.0 + 0.5 * facade.height * math.tan(math.radians(min(max(facade.tilt, 0.0), 30.0)))
    m.set_depth(min(Margins.MAX_DEPTH, round(depth * 2.0) / 2.0))
    return m
