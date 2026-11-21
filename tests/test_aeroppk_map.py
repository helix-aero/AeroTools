"""AeroPPK map maths (mapmath.py): Web Mercator, zoom fitting, distance circles, tiles. Pure, no Qt, no network."""

import ast
import math
import os
import string

import pytest

from aerotools.tools.aeroppk import geo, mapmath

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = (45.30, 5.15)


# ------------------------------------------------------------------ Web Mercator

def test_world_size_doubles_with_each_zoom():
    assert mapmath.world_size(0) == 256
    assert mapmath.world_size(3) == 2048
    assert mapmath.world_size(2.5) == pytest.approx(256 * 2 ** 2.5)


def test_known_world_pixels():
    # Greenwich / equator is the middle of the world, (-180, ~85) its north-west corner.
    assert mapmath.lonlat_to_world(0, 0, 1) == pytest.approx((256.0, 256.0))
    x, y = mapmath.lonlat_to_world(-180, mapmath.MAX_LAT, 2)
    assert x == pytest.approx(0.0) and y == pytest.approx(0.0, abs=1e-6)
    x, y = mapmath.lonlat_to_world(180, -mapmath.MAX_LAT, 0)
    assert x == pytest.approx(256.0) and y == pytest.approx(256.0, abs=1e-6)


@pytest.mark.parametrize("lat,lon,z", [(45.3, 5.15, 12), (-33.9, 151.2, 7.5), (0.0, 0.0, 3), (64.1, -21.9, 17.25),
                                       (-60, 179.9, 5)])
def test_world_round_trip(lat, lon, z):
    x, y = mapmath.lonlat_to_world(lon, lat, z)
    lon2, lat2 = mapmath.world_to_lonlat(x, y, z)
    assert lon2 == pytest.approx(lon, abs=1e-9)
    assert lat2 == pytest.approx(lat, abs=1e-9)
    la, lo = mapmath.world_to_latlon(*mapmath.latlon_to_world(lat, lon, z), z)
    assert (la, lo) == pytest.approx((lat, lon), abs=1e-9)


def test_latitude_beyond_mercator_limit_is_clamped():
    x, y = mapmath.lonlat_to_world(0, 89.9, 4)
    assert math.isfinite(y) and y == pytest.approx(0.0, abs=1e-3)


def test_tile_numbers_match_the_osm_wiki_examples():
    # https://wiki.openstreetmap.org/wiki/Slippy_map_tilenames : Greenwich at zoom 10 is tile 512/340 (lat 51.4779).
    assert mapmath.lonlat_to_tile(0.0, 51.4779, 10) == (512, 340)
    assert mapmath.lonlat_to_tile(5.15, 45.30, 0) == (0, 0)
    tx, ty = mapmath.lonlat_to_tile(SITE[1], SITE[0], 12)
    lon, lat = mapmath.tile_to_lonlat(tx, ty, 12)
    lon2, lat2 = mapmath.tile_to_lonlat(tx + 1, ty + 1, 12)
    assert lon <= SITE[1] < lon2 and lat2 < SITE[0] <= lat


def test_tile_index_is_clamped_at_the_edges():
    assert mapmath.lonlat_to_tile(180.0, 0.0, 3)[0] == 7
    assert mapmath.lonlat_to_tile(-180.0, 90.0, 3) == (0, 0)


def test_meters_per_pixel_halves_with_zoom_and_shrinks_with_latitude():
    assert mapmath.meters_per_pixel(0, 0) == pytest.approx(156543.03, rel=1e-4)
    assert mapmath.meters_per_pixel(0, 5) == pytest.approx(mapmath.meters_per_pixel(0, 4) / 2)
    assert mapmath.meters_per_pixel(60, 8) == pytest.approx(mapmath.meters_per_pixel(0, 8) / 2, rel=1e-3)


# ------------------------------------------------------------------ bounds and fitting

def test_bounds_of_and_merge():
    assert mapmath.bounds_of([]) is None
    assert mapmath.bounds_of([(1, 2), (3, -4), (2, 9)]) == (1, -4, 3, 9)
    assert mapmath.merge_bounds(None, (0, 0, 1, 1), (-1, 0.5, 0.5, 3)) == (-1, 0, 1, 3)
    assert mapmath.merge_bounds(None, None) is None


def test_fit_zoom_is_the_biggest_zoom_that_fits():
    box = (45.0, 4.9, 45.6, 5.4)
    for width, height in ((480, 360), (1000, 700), (1920, 1080)):
        margin = 30
        z = mapmath.fit_zoom(box, width, height, margin)
        x0, y1 = mapmath.lonlat_to_world(box[1], box[0], z)
        x1, y0 = mapmath.lonlat_to_world(box[3], box[2], z)
        assert (x1 - x0) <= width - 2 * margin + 1e-6
        assert (y1 - y0) <= height - 2 * margin + 1e-6
        # and it is tight on at least one axis
        assert max((x1 - x0) / (width - 2 * margin), (y1 - y0) / (height - 2 * margin)) == pytest.approx(1.0, abs=1e-6)


def test_fit_zoom_grows_with_a_bigger_view_and_shrinks_with_margin():
    box = (45.0, 4.9, 45.6, 5.4)
    assert mapmath.fit_zoom(box, 1000, 700, 0) > mapmath.fit_zoom(box, 480, 360, 0)
    assert mapmath.fit_zoom(box, 800, 600, 80) < mapmath.fit_zoom(box, 800, 600, 0)


def test_fit_zoom_is_clamped():
    assert mapmath.fit_zoom((45.0, 5.0, 45.0, 5.0), 800, 600, 10) == mapmath.MAX_ZOOM
    assert mapmath.fit_zoom((-80, -179, 80, 179), 300, 200, 10) == mapmath.MIN_ZOOM
    assert mapmath.fit_zoom((45.0, 5.0, 45.0, 5.0), 800, 600, 10, max_zoom=15) == 15
    assert mapmath.fit_zoom((45.0, 4.9, 45.6, 5.4), 100, 100, 200) >= mapmath.MIN_ZOOM      # margin bigger than the view


def test_fit_view_centers_the_box():
    box = (45.0, 4.9, 45.6, 5.4)
    lat, lon, z = mapmath.fit_view(box, 800, 600, 20)
    x, y = mapmath.lonlat_to_world(lon, lat, z)
    x0, y1 = mapmath.lonlat_to_world(box[1], box[0], z)
    x1, y0 = mapmath.lonlat_to_world(box[3], box[2], z)
    assert x == pytest.approx((x0 + x1) / 2)
    assert y == pytest.approx((y0 + y1) / 2)


# ------------------------------------------------------------------ distance circles

@pytest.mark.parametrize("radius", [100.0, 10000.0, 30000.0, 500000.0])
def test_ring_points_are_on_the_circle(radius):
    ring = mapmath.ring_points(SITE[0], SITE[1], radius)
    assert len(ring) == 96
    for lat, lon in ring:
        assert geo.distance_m(SITE[0], SITE[1], lat, lon) == pytest.approx(radius, rel=1e-6)


def test_ring_points_start_north_and_go_clockwise():
    ring = mapmath.ring_points(SITE[0], SITE[1], 20000, n=4)
    north, east, south, west = ring
    assert north[0] > SITE[0] and north[1] == pytest.approx(SITE[1])
    assert east[1] > SITE[1] and south[0] < SITE[0] and west[1] < SITE[1]


def test_ring_looks_round_on_a_mercator_screen_and_has_the_right_size():
    # Web Mercator is conformal: a small ground circle stays round on the screen.
    z = 12
    ring = mapmath.ring_points(SITE[0], SITE[1], 5000, 72)
    xs, ys = zip(*[mapmath.lonlat_to_world(lon, lat, z) for lat, lon in ring])
    assert (max(xs) - min(xs)) / (max(ys) - min(ys)) == pytest.approx(1.0, abs=0.01)
    expected = 5000 / mapmath.meters_per_pixel(SITE[0], z)
    assert (max(xs) - min(xs)) / 2 == pytest.approx(expected, rel=0.01)


def test_ring_points_minimum_count_and_wrap_around_the_antimeridian():
    assert len(mapmath.ring_points(0, 0, 1000, n=1)) == 3
    ring = mapmath.ring_points(0.0, 179.99, 50000, 8)
    assert all(-180.0 <= lon <= 180.0 for _lat, lon in ring)


def test_ring_bounds_contain_the_circle():
    box = mapmath.ring_bounds(SITE[0], SITE[1], 30000)
    assert box[0] < SITE[0] - 0.25 and box[2] > SITE[0] + 0.25
    assert box[1] < SITE[1] < box[3]


def test_nice_radii_default_and_growth():
    assert mapmath.nice_radii(None) == [10, 20, 30]
    assert mapmath.nice_radii(0) == [10, 20, 30]
    assert mapmath.nice_radii(600) == [10]
    assert mapmath.nice_radii(9999) == [10]
    assert mapmath.nice_radii(33000) == [10, 20, 30, 50]
    assert mapmath.nice_radii(30000) == [10, 20, 30]
    assert mapmath.nice_radii(75000) == [10, 20, 30, 50, 100]


def test_nice_radii_always_reach_the_farthest_station_and_stay_few():
    for far in (5000, 12000, 33000, 99000, 180000, 450000, 1200000, 2500000, 7000000):
        radii = mapmath.nice_radii(far)
        assert radii[-1] * 1000 >= far
        assert len(radii) <= mapmath.MAX_RINGS
        assert radii == sorted(radii) and len(set(radii)) == len(radii)


def test_format_km():
    assert mapmath.format_km(600) == "0.6"
    assert mapmath.format_km(21340) == "21"
    assert mapmath.format_km(330000) == "330"
    assert mapmath.format_radius_km(10) == "10"
    assert mapmath.format_radius_km(2.5) == "2.5"
    assert mapmath.format_radius_km(0.25) == "0.25"


# ------------------------------------------------------------------ points

def test_decimate_keeps_at_most_n_evenly():
    pts = [(float(i), 0.0) for i in range(5000)]
    out = mapmath.decimate(pts, 500)
    assert len(out) == 500
    assert out[0] == pts[0]
    gaps = [b[0] - a[0] for a, b in zip(out, out[1:])]
    assert max(gaps) - min(gaps) <= 1.0
    assert mapmath.decimate(pts[:10], 500) == pts[:10]
    assert mapmath.decimate(pts, 0) == []


def test_convex_hull():
    square = [(0, 0), (0, 4), (4, 4), (4, 0)]
    inside = [(1, 1), (2, 2), (3, 1), (2, 0)]          # (2, 0) lies on an edge: not a corner
    hull = mapmath.convex_hull(square + inside)
    assert sorted(hull) == sorted((float(a), float(b)) for a, b in square)
    assert mapmath.convex_hull([]) == []
    assert mapmath.convex_hull([(1, 2)]) == [(1.0, 2.0)]
    assert len(mapmath.convex_hull([(0, 0), (1, 1), (2, 2)])) == 2          # collinear points: the two ends


def test_mean_position():
    assert mapmath.mean_position([]) is None
    assert mapmath.mean_position([(42.0, 9.0), (43.0, 10.0)]) == (42.5, 9.5)


def test_nice_step():
    assert mapmath.nice_step(0.5, 6) == 0.1
    assert mapmath.nice_step(0.5, 3) == 0.2
    assert mapmath.nice_step(360, 4) == 90


# ------------------------------------------------------------------ tiles

def test_tile_path_layout():
    path = mapmath.tile_path(os.path.join("cache", "osm"), 12, 2144, 1520)
    assert path == os.path.join("cache", "osm", "12", "2144", "1520.png")
    assert mapmath.tile_path("c", "7", "1", "2") == os.path.join("c", "7", "1", "2.png")


def test_tile_url_is_the_osm_standard_one():
    assert mapmath.tile_url(5, 17, 11) == "https://tile.openstreetmap.org/5/17/11.png"
    assert mapmath.TILE_URL.startswith("https://tile.openstreetmap.org/")


def test_tile_zoom_is_the_nearest_and_limited():
    assert mapmath.tile_zoom_for(9.4) == 9
    assert mapmath.tile_zoom_for(9.5) == 10
    assert mapmath.tile_zoom_for(1.0) == mapmath.MIN_ZOOM
    assert mapmath.tile_zoom_for(30.0) == mapmath.MAX_ZOOM


def test_cache_freshness_is_seven_days():
    now = 1000000000.0
    assert mapmath.cache_is_fresh(now - 6 * 86400, now)
    assert not mapmath.cache_is_fresh(now - 8 * 86400, now)
    assert mapmath.cache_is_fresh(now, now)


def _check_cover(tiles, width, height):
    """Sample points of the view must each be inside one of the tiles (no hole)."""
    assert tiles
    for px in (0, width // 3, width // 2, width - 1):
        for py in (0, height // 3, height // 2, height - 1):
            assert any(l - 1e-6 <= px < l + s + 1e-6 and t - 1e-6 <= py < t + s + 1e-6
                       for _z, _x, _y, l, t, s in tiles), (px, py)


@pytest.mark.parametrize("width,height", [(480, 360), (1000, 700), (800, 600)])
def test_view_tiles_cover_the_view_with_few_tiles(width, height):
    tiles = mapmath.view_tiles(SITE[0], SITE[1], 12.0, width, height)
    assert len(tiles) <= mapmath.MAX_VIEW_TILES
    assert {t[0] for t in tiles} == {12}
    _check_cover(tiles, width, height)


def test_view_tiles_first_tile_is_the_middle_one():
    tiles = mapmath.view_tiles(SITE[0], SITE[1], 12.0, 800, 600)
    tx, ty = mapmath.lonlat_to_tile(SITE[1], SITE[0], 12)
    assert tiles[0][:3] == (12, tx, ty)
    _z, _x, _y, left, top, size = tiles[0]
    assert left <= 400 < left + size and top <= 300 < top + size


def test_view_tiles_are_capped_by_lowering_the_tile_level():
    capped = mapmath.view_tiles(SITE[0], SITE[1], 14.0, 3000, 2000, max_tiles=30)
    assert len(capped) <= 30
    assert capped[0][0] < 14
    _check_cover(capped, 3000, 2000)
    wide = mapmath.view_tiles(SITE[0], SITE[1], 14.0, 3000, 2000, max_tiles=1000)
    assert wide[0][0] == 14 and len(wide) > 30


def test_view_tiles_fractional_zoom_scales_tiles():
    tiles = mapmath.view_tiles(SITE[0], SITE[1], 12.5, 800, 600)
    assert {t[0] for t in tiles} == {13}                         # nearest integer level
    assert all(t[5] == pytest.approx(256 * 2 ** -0.5) for t in tiles)
    tiles = mapmath.view_tiles(SITE[0], SITE[1], 12.4, 800, 600)
    assert {t[0] for t in tiles} == {12} and tiles[0][5] == pytest.approx(256 * 2 ** 0.4)


def test_view_tiles_never_leave_the_tile_grid():
    # Near the antimeridian x wraps around, near the poles rows beyond the map are dropped.
    tiles = mapmath.view_tiles(0.0, 179.99, 5.0, 1000, 700)
    assert all(0 <= x < 32 for _z, x, _y, _l, _t, _s in tiles)
    assert {0, 31} <= {x for _z, x, _y, _l, _t, _s in tiles}
    polar = mapmath.view_tiles(84.9, 0.0, 4.0, 1000, 700)
    assert all(0 <= y < 16 for _z, _x, y, _l, _t, _s in polar)


def test_view_tiles_zoom_is_limited_to_the_osm_range():
    assert {t[0] for t in mapmath.view_tiles(42, 9, 1.0, 400, 300)} == {mapmath.MIN_ZOOM}
    assert {t[0] for t in mapmath.view_tiles(42, 9, 25.0, 400, 300, max_tiles=100)} == {mapmath.MAX_ZOOM}
    assert mapmath.MIN_ZOOM == 3 and mapmath.MAX_ZOOM == 18


def test_view_tile_screen_positions_are_consistent_with_world_pixels():
    z = 11.0
    tiles = mapmath.view_tiles(SITE[0], SITE[1], z, 640, 480)
    cx, cy = mapmath.lonlat_to_world(SITE[1], SITE[0], z)
    for _z, x, y, left, top, size in tiles:
        assert left == pytest.approx(x * 256 - (cx - 320), abs=1e-6)
        assert top == pytest.approx(y * 256 - (cy - 240), abs=1e-6)
        assert size == 256


# ------------------------------------------------------------------ files

def test_no_qt_in_mapmath_and_python39_syntax():
    path = os.path.join(ROOT, "aerotools", "tools", "aeroppk", "mapmath.py")
    with open(path, "rb") as handle:
        source = handle.read()
    tree = ast.parse(source, filename=path, feature_version=(3, 9))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert not imported & {"PySide2", "PySide6", "PyQt5", "PyQt6", "qt"}


def _fields(text):
    return sorted(n for _l, n, _s, _c in string.Formatter().parse(text) if n is not None)


def _map_texts():
    from aerotools import i18n

    return {k: v for k, v in i18n.texts().items() if k.startswith("aeroppk.map.")}


def test_map_texts_follow_the_catalog_style_rules():
    texts = _map_texts()
    assert texts
    for key, text in texts.items():
        assert ";" not in text, key
        assert text == text.strip(), key


def test_map_view_uses_only_declared_text_keys():
    """Every aeroppk.map.* key written in mapview.py exists in the catalog (and so do the ones built from a button name)."""
    texts_map = _map_texts()

    path = os.path.join(ROOT, "aerotools", "tools", "aeroppk", "mapview.py")
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), path)
    used = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and n.value.startswith("aeroppk.map.") and "%" not in n.value}
    assert used and used <= set(texts_map), used - set(texts_map)
    for button in ("zoom_in", "zoom_out", "fit"):
        assert "aeroppk.map." + button in texts_map
