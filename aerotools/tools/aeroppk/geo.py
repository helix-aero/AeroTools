"""Small geodesy helpers (no PROJ): ellipsoidal <-> geocentric, local north-east-down, distances.

Geographic positions are degrees and metres of ellipsoidal height. The ellipsoid is GRS80; WGS84 differs by 0.1 mm in
semi-minor axis, far below anything GNSS can resolve. Frame changes (ETRS89 / RGF93 <-> ITRF) are NOT done here, they
go through PROJ (see frames.py).
"""

import math

import numpy as np

A = 6378137.0
F = 1.0 / 298.257222101
E2 = F * (2.0 - F)
EARTH_R = 6371008.8


def llh_to_ecef(lat, lon, h):
    """Arrays or scalars (deg, deg, m) -> (x, y, z) in metres."""
    lat, lon, h = np.asarray(lat, float), np.asarray(lon, float), np.asarray(h, float)
    phi, lam = np.radians(lat), np.radians(lon)
    sin_p, cos_p = np.sin(phi), np.cos(phi)
    n = A / np.sqrt(1.0 - E2 * sin_p * sin_p)
    x = (n + h) * cos_p * np.cos(lam)
    y = (n + h) * cos_p * np.sin(lam)
    z = (n * (1.0 - E2) + h) * sin_p
    return x, y, z


def ecef_to_llh(x, y, z):
    """Geocentric metres -> (lat deg, lon deg, h m). Iterative, converged to well under a micrometre."""
    x, y, z = np.asarray(x, float), np.asarray(y, float), np.asarray(z, float)
    lon = np.arctan2(y, x)
    p = np.hypot(x, y)
    lat = np.arctan2(z, p * (1.0 - E2))
    h = np.zeros_like(lat)
    for _ in range(8):
        sin_p = np.sin(lat)
        n = A / np.sqrt(1.0 - E2 * sin_p * sin_p)
        h = p / np.cos(lat) - n
        lat = np.arctan2(z, p * (1.0 - E2 * n / (n + h)))
    return np.degrees(lat), np.degrees(lon), h


def ned_to_ecef_delta(lat, lon, north, east, down):
    """Local north / east / down displacement (m) at (lat, lon) -> geocentric displacement (dx, dy, dz)."""
    phi, lam = math.radians(lat), math.radians(lon)
    sp, cp, sl, cl = math.sin(phi), math.cos(phi), math.sin(lam), math.cos(lam)
    dx = -sp * cl * north - sl * east - cp * cl * down
    dy = -sp * sl * north + cl * east - cp * sl * down
    dz = cp * north - sp * down
    return dx, dy, dz


def ned_to_ecef_delta_array(lat, lon, north, east, down):
    """Like `ned_to_ecef_delta` with one (lat, lon) per displacement (arrays of the same length)."""
    phi, lam = np.radians(np.asarray(lat, float)), np.radians(np.asarray(lon, float))
    sp, cp, sl, cl = np.sin(phi), np.cos(phi), np.sin(lam), np.cos(lam)
    dx = -sp * cl * north - sl * east - cp * cl * down
    dy = -sp * sl * north + cl * east - cp * sl * down
    dz = cp * north - sp * down
    return dx, dy, dz


def ecef_delta_to_ned(lat, lon, dx, dy, dz):
    """Geocentric displacement -> local (north, east, down) at (lat, lon). Arrays allowed for dx, dy, dz."""
    phi, lam = math.radians(lat), math.radians(lon)
    sp, cp, sl, cl = math.sin(phi), math.cos(phi), math.sin(lam), math.cos(lam)
    north = -sp * cl * dx - sp * sl * dy + cp * dz
    east = -sl * dx + cl * dy
    down = -cp * cl * dx - cp * sl * dy - sp * dz
    return north, east, down


def llh_difference_ned(lat0, lon0, h0, lat, lon, h):
    """Position (lat, lon, h) minus reference (lat0, lon0, h0) as (north, east, down) metres at the reference."""
    x0, y0, z0 = llh_to_ecef(lat0, lon0, h0)
    x, y, z = llh_to_ecef(lat, lon, h)
    return ecef_delta_to_ned(float(np.mean(lat0)), float(np.mean(lon0)), x - x0, y - y0, z - z0)


def llh_difference_ned_array(lat0, lon0, h0, lat, lon, h):
    """Like `llh_difference_ned`, with the north / east / down taken at each reference point (arrays of the same length)."""
    x0, y0, z0 = llh_to_ecef(lat0, lon0, h0)
    x, y, z = llh_to_ecef(lat, lon, h)
    return ecef_delta_to_ned_array(lat0, lon0, x - x0, y - y0, z - z0)


def distance_m(lat1, lon1, lat2, lon2):
    """Great circle distance in metres (haversine, spherical Earth: fine to 0.3 % for ranking base stations)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2.0) ** 2
    return 2.0 * EARTH_R * math.asin(min(1.0, math.sqrt(a)))


def baseline_m(lat1, lon1, h1, lat2, lon2, h2):
    """3D straight line distance between two geodetic points (metres)."""
    x1, y1, z1 = llh_to_ecef(lat1, lon1, h1)
    x2, y2, z2 = llh_to_ecef(lat2, lon2, h2)
    return float(np.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2 + (z2 - z1) ** 2))


def packed_dms_to_deg(value):
    """`42.175592367` meaning 42 deg 17 min 55.92367 s (the RGP csv notation) -> decimal degrees.

    Trap: the RGP coordinate file writes angles as DDD.MMSSsssss, NOT as decimal degrees. Read as decimal degrees, the Corte
    station lands 13 km south of its true position.
    """
    sign = -1.0 if str(value).strip().startswith("-") else 1.0
    text = "%.9f" % abs(float(value))
    whole, frac = text.split(".")
    minutes = int(frac[0:2])
    seconds = float(frac[2:4] + "." + frac[4:])
    return sign * (int(whole) + minutes / 60.0 + seconds / 3600.0)


def destination(lat, lon, bearing_deg, dist_m):
    """Point reached from (lat, lon) going `dist_m` along `bearing_deg` (spherical). Used to draw distance circles."""
    d = dist_m / EARTH_R
    b = math.radians(bearing_deg)
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540.0) % 360.0 - 180.0


def ecef_delta_to_ned_array(lat, lon, dx, dy, dz):
    """Like `ecef_delta_to_ned` with one (lat, lon) per displacement (arrays of the same length)."""
    phi, lam = np.radians(np.asarray(lat, float)), np.radians(np.asarray(lon, float))
    sp, cp, sl, cl = np.sin(phi), np.cos(phi), np.sin(lam), np.cos(lam)
    north = -sp * cl * dx - sp * sl * dy + cp * dz
    east = -sl * dx + cl * dy
    down = -cp * cl * dx - cp * sl * dy - sp * dz
    return north, east, down


def utm_zone(lon):
    """UTM zone number (1 to 60) of a longitude in degrees."""
    return int((float(lon) + 180.0) // 6.0) % 60 + 1


def to_utm(lat, lon, zone=None):
    """(easting, northing, zone, north) of arrays or scalars of latitude / longitude (degrees) on the GRS80 ellipsoid, UTM projection
    (Kruger series, millimetre level inside the zone). `zone` default: the zone of the mean longitude; `north` False below the equator
    (the northing then carries the false northing of 10 000 km)."""
    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    zone = zone or utm_zone(float(np.nanmean(lon)) if lon.size else 0.0)
    lon0 = math.radians(6.0 * zone - 183.0)
    n = F / (2.0 - F)
    a_hat = A / (1.0 + n) * (1.0 + n * n / 4.0 + n ** 4 / 64.0)
    alpha = (n / 2.0 - 2.0 * n * n / 3.0 + 5.0 * n ** 3 / 16.0, 13.0 * n * n / 48.0 - 3.0 * n ** 3 / 5.0, 61.0 * n ** 3 / 240.0)
    phi, dlam = np.radians(lat), np.radians(lon) - lon0
    q = 2.0 * math.sqrt(n) / (1.0 + n)
    t = np.sinh(np.arctanh(np.sin(phi)) - q * np.arctanh(q * np.sin(phi)))
    xi0 = np.arctan2(t, np.cos(dlam))
    eta0 = np.arctanh(np.sin(dlam) / np.sqrt(1.0 + t * t))
    xi, eta = xi0.copy(), eta0.copy()
    for j, a_j in enumerate(alpha, start=1):
        xi = xi + a_j * np.sin(2.0 * j * xi0) * np.cosh(2.0 * j * eta0)
        eta = eta + a_j * np.cos(2.0 * j * xi0) * np.sinh(2.0 * j * eta0)
    north = bool(np.nanmean(lat) >= 0.0) if lat.size else True
    return 500000.0 + 0.9996 * a_hat * eta, (0.0 if north else 10000000.0) + 0.9996 * a_hat * xi, zone, north
