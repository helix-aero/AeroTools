"""Aero-Facade geometry.py: plane frame, bounding region, margins schematic, picture sizes, time estimates."""

import math

import pytest

np = pytest.importorskip("numpy")

from aerotools.tools.aerofacade import geometry as G  # noqa: E402
from aerotools.tools.aerofacade.model import Facade, Margins, Method  # noqa: E402


def _facade(az_deg=0.0, length=20.0, height=10.0, origin=(100.0, 200.0, 5.0), **kw):
    u = (math.cos(math.radians(az_deg)), math.sin(math.radians(az_deg)), 0.0)
    return Facade.from_frame("A", 0, origin, u, length, height, **kw)


def test_plane_round_trip_and_distance():
    f = _facade(33.0)
    rng = np.random.RandomState(1)
    stn = rng.uniform(-5, 25, (50, 3))
    P = G.plane_to_enu(f, stn)
    assert np.allclose(G.enu_to_plane(f, P), stn)
    assert np.allclose(G.distance_to_plane(f, P), stn[:, 2])
    p = np.array(f.point_at(5, 4, 3.0))
    assert G.distance_to_plane(f, p[None, :])[0] == pytest.approx(3.0)


def test_rotation_matrix_columns_are_the_axes():
    f = _facade(33.0)
    R = np.array(G.rotation_matrix(f))
    assert np.allclose(R.T @ R, np.eye(3), atol=1e-12)
    assert np.allclose(R[:, 0], f.u) and np.allclose(R[:, 1], f.v) and np.allclose(R[:, 2], f.n)
    assert np.linalg.det(R) == pytest.approx(1.0)                      # a proper rotation (no mirror)


def test_bounding_region_size_centre_and_box():
    f = _facade(0.0)                                                    # along east, faces south
    r = G.bounding_region(f, Margins(left=1, right=3, top=2, bottom=0.5, depth=2.5, link_lr=False, link_tb=False))
    assert r["size"] == pytest.approx((24.0, 12.5, 5.0))
    assert r["box2d"] == pytest.approx((-1.0, -0.5, 23.0, 12.0))
    # centre: s = 11 (20/2 + (3-1)/2), t = 5.75 (10/2 + (2-0.5)/2), no offset in depth
    assert r["centre"] == pytest.approx((111.0, 200.0, 10.75))
    assert len(r["corners"]) == 8
    assert r["depth"] == 2.5
    xs = [c[0] for c in r["corners"]]
    assert min(xs) == pytest.approx(99.0) and max(xs) == pytest.approx(123.0)
    ys = [c[1] for c in r["corners"]]
    assert min(ys) == pytest.approx(197.5) and max(ys) == pytest.approx(202.5)    # 2.5 m in front (south) and behind (north)


def test_bounding_region_follows_the_wall_direction():
    for az in (0, 33, 90, 200, 317):
        f = _facade(az)
        r = G.bounding_region(f, Margins())
        assert G.points_in_region([r["centre"]], r)[0]
        assert G.points_in_region([f.centre], r)[0]
        assert not G.points_in_region([f.point_at(10, 5, 3.0)], r)[0]         # 3 m in front: beyond the 2 m depth
        assert G.points_in_region([f.point_at(10, 5, 1.9)], r)[0]
        assert G.points_in_region([f.point_at(10, 5, -1.9)], r)[0]
        assert not G.points_in_region([f.point_at(-1.2, 5, 0)], r)[0]         # beyond the left margin (1 m)
        assert G.points_in_region([f.point_at(-0.9, 5, 0)], r)[0]
        assert not G.points_in_region([f.point_at(10, 11.2, 0)], r)[0]        # above the top margin
        corners = np.array(r["corners"])
        mid = np.array(r["centre"])
        assert G.points_in_region(mid + 0.999 * (corners - mid), r).all()


def test_bounding_region_per_facade_margins_dict():
    f = _facade()
    r = G.bounding_region(f, {"A": Margins(left=4, right=4, depth=1)})
    assert r["size"][0] == pytest.approx(28.0) and r["size"][2] == pytest.approx(2.0)
    assert G.bounding_region(f, {"B": Margins(left=4)})["size"][0] == pytest.approx(22.0)   # no entry: the defaults


def test_region_footprint_is_a_rectangle_in_plan():
    f = _facade(20.0)
    fp = np.array(G.region_footprint(f, Margins()))
    assert fp.shape == (4, 2)
    assert G.polygon_area(fp) == pytest.approx(22.0 * 4.0)               # (length + 2) x (2 x depth)


def test_margin_schematic_fits_the_box_and_keeps_proportions():
    m = Margins(left=2, right=2, top=1, bottom=1)
    s = G.margin_schematic(20, 10, m, 240, 150, pad=10)
    ax, ay, aw, ah = s["area"]
    wx, wy, ww, wh = s["wall"]
    assert ax >= 10 - 1e-9 and ay >= 10 - 1e-9 and ax + aw <= 230 + 1e-9 and ay + ah <= 140 + 1e-9
    assert ww / wh == pytest.approx(2.0)
    assert (wx - ax) / s["scale"] == pytest.approx(2.0) and (wy - ay) / s["scale"] == pytest.approx(1.0)
    assert s["total_m"] == (24.0, 12.0)
    assert (ax + aw / 2) == pytest.approx(120.0) and (ay + ah / 2) == pytest.approx(75.0)    # centred
    zero = G.margin_schematic(20, 10, Margins(left=0, right=0, top=0, bottom=0))
    assert zero["wall"] == pytest.approx(zero["area"])
    tall = G.margin_schematic(2, 30, m)
    assert tall["area"][3] <= 150 - 2 * 14 + 1e-9                                      # a tall thin wall is limited by the height


def test_depth_schematic_orders_back_wall_front():
    d = G.depth_schematic(Margins(depth=2.0))
    assert d["back"][1] < d["wall"][1] < d["front"][1]
    assert d["back"][3] == pytest.approx(d["front"][3])
    assert d["wall"][1] == pytest.approx(d["back"][1] + d["back"][3])
    deeper = G.depth_schematic(Margins(depth=4.0))
    assert deeper["scale"] < d["scale"]                                               # more depth, smaller drawing


def test_image_size_and_pixel_checks():
    f = _facade(length=66.0, height=26.0)
    m = Margins()
    assert G.picture_size_m(f, m) == (68.0, 28.0)
    assert G.image_size_px(f, m, 0.03) == (2267, 934)
    assert G.image_size_px(f, m, 0.1) == (680, 280)
    with pytest.raises(ValueError):
        G.image_size_px(f, m, 0)
    assert G.megapixels(f, m, 0.03) == pytest.approx(2267 * 934 / 1e6)
    assert G.pixel_size_check(f, m, 0.03)[0] == "ok"
    assert G.pixel_size_check(f, m, 0.005)[0] == "heavy"          # 76 Mpix
    assert G.pixel_size_check(f, m, 0.002)[0] == "too_big"
    px = G.finest_allowed_pixel(f, m)
    assert G.megapixels(f, m, px) <= G.TOO_BIG_MPIX + 1e-6
    assert G.megapixels(f, m, px - 0.001) > G.TOO_BIG_MPIX
    assert G.DEFAULT_PIXEL_M in G.PIXEL_SIZES


def test_file_size_estimates_order():
    t, p, j = (G.file_size_estimate_mb(2000, 1000, k) for k in ("tif", "png", "jpg"))
    assert t == pytest.approx(6.0) and t > p > j > 0
    assert G.file_size_estimate_mb(10, 10, "weird") == pytest.approx(300 / 1e6)


def test_georef_lines():
    f = _facade(length=10, height=5)
    text = "\n".join(G.georef_lines(f, Margins(), 0.05))
    assert "pixel size m: 0.05000" in text and "image size px: 240 x 140" in text
    assert "wall length m: 10.000" in text


def test_time_estimates_are_ordered_and_scale_with_area():
    a = 600.0
    tie, dense, depth = (G.estimate_dem_time(m, a) for m in (Method.TIE_POINTS, Method.DENSE_CLOUD, Method.DEPTH_MAPS))
    for e in (tie, dense, depth):
        assert 0 < e.low < e.high
    assert tie.high < dense.high < depth.high
    assert tie.basis == "measured" and depth.basis == "estimated"
    assert G.estimate_dem_time("depth_maps", 2000).high > G.estimate_dem_time("depth_maps", 200).high
    # the measured case of the study: 2.2 s for a 26 m x 14.7 m region (28 x 16.7 with margins)
    study = G.estimate_dem_time("tie_points", 28 * 16.7)
    assert study.low <= 2.2 <= study.high
    # the orthophoto: 4.4 s for 873 x 489 px
    o = G.estimate_ortho_time(873 * 489 / 1e6)
    assert o.low <= 4.4 <= o.high


def test_batch_estimates_skip_removed_and_existing():
    fs = [_facade(length=20), _facade(length=40), _facade(length=10)]
    for i, f in enumerate(fs):
        f.letter = "ABC"[i]
    fs[2].removed = True
    m = Margins()
    full = G.estimate_batch_dems("tie_points", fs, m)
    skip = G.estimate_batch_dems("tie_points", fs, m, skip=("A",))
    assert skip.high < full.high
    assert G.estimate_batch_dems("tie_points", fs, m, skip=("A", "B")).high == 0.0
    assert G.estimate_batch_dems("tie_points", [], m).high == 0.0
    assert G.estimate_batch_orthos(fs, m, 0.03).low > 0
    s = G.TimeEstimate(1, 2, "measured") + G.TimeEstimate(3, 4, "estimated")
    assert (s.low, s.high, s.basis) == (4, 6, "estimated")
    assert not G.TimeEstimate().known


def test_suggest_default_margins():
    m = G.suggest_default_margins()
    assert (m.left, m.right, m.top, m.bottom, m.depth) == (1, 1, 1, 1, 2)
    short = G.suggest_default_margins(_facade(length=5.0, height=4.0))
    assert short.left == short.right == 0.5 and short.top == short.bottom == 0.5
    lean = G.suggest_default_margins(_facade(length=20, height=20, tilt=8.0))
    assert lean.depth > 2.0 and lean.depth * 2 == round(lean.depth * 2)
    assert G.suggest_default_margins(_facade(length=20, height=20, tilt=89.0)).depth <= Margins.MAX_DEPTH
