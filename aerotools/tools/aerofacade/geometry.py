"""Aero-Facade geometry: polygons, plane frames, bounding regions, margins schematic, sizes and time estimates.

Pure numpy / math, no Qt, no Metashape. All coordinates are metres in the chunk's local east/north/up frame unless stated.
(Step 2 delivers the polygon helpers; the rest of the module follows in step 3.)
"""

import numpy as np


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
