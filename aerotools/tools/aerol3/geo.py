"""Aero-L3: the little geodesy the tool needs (WGS 84 ellipsoid, local east / north / up). Pure Python, no Metashape, no Qt."""

import math

A = 6378137.0
F = 1.0 / 298.257223563
E2 = F * (2.0 - F)


def to_ecef(lat, lon, height):
    """Geocentric (x, y, z) in metres of a geodetic position (degrees, degrees, ellipsoidal height in metres)."""
    la, lo = math.radians(lat), math.radians(lon)
    n = A / math.sqrt(1.0 - E2 * math.sin(la) ** 2)
    return ((n + height) * math.cos(la) * math.cos(lo), (n + height) * math.cos(la) * math.sin(lo), (n * (1.0 - E2) + height) * math.sin(la))


def enu_axes(lat, lon):
    """The east, north and up unit vectors (in geocentric coordinates) at a place."""
    la, lo = math.radians(lat), math.radians(lon)
    east = (-math.sin(lo), math.cos(lo), 0.0)
    north = (-math.sin(la) * math.cos(lo), -math.sin(la) * math.sin(lo), math.cos(la))
    up = (math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la))
    return east, north, up


def enu_of(origin, point):
    """(east, north, up) in metres of `point` seen from `origin`; both are (lat, lon, height)."""
    o = to_ecef(*origin)
    p = to_ecef(*point)
    d = (p[0] - o[0], p[1] - o[1], p[2] - o[2])
    east, north, up = enu_axes(origin[0], origin[1])
    return (sum(a * b for a, b in zip(d, east)), sum(a * b for a, b in zip(d, north)), sum(a * b for a, b in zip(d, up)))


def distance(a, b):
    """3D distance in metres between two (lat, lon, height) positions."""
    e, n, u = enu_of(a, b)
    return math.sqrt(e * e + n * n + u * u)


def azimuth(east, north):
    """Compass bearing in degrees [0, 360) of a horizontal vector."""
    return (math.degrees(math.atan2(east, north)) + 360.0) % 360.0


def angle_difference(a, b):
    """Smallest signed difference a - b of two angles in degrees, in [-180, 180)."""
    return (a - b + 180.0) % 360.0 - 180.0


def meridian_radii(lat):
    """(radius of curvature in the meridian, in the prime vertical) at a latitude, in metres."""
    s = math.sin(math.radians(lat))
    w = math.sqrt(1.0 - E2 * s * s)
    return A * (1.0 - E2) / (w ** 3), A / w
