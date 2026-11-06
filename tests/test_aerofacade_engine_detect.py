"""Aero-Facade detect.py (engine version): occupancy test, suppression, regularisation, orientation, facades, CARRO regression."""

import math
import os

import pytest

np = pytest.importorskip("numpy")

from aerotools.tools.aerofacade import detect as D  # noqa: E402
from aerotools.tools.aerofacade import fake_backend as FB  # noqa: E402
from aerotools.tools.aerofacade import geometry as G  # noqa: E402
from aerotools.tools.aerofacade.backend import Cancelled  # noqa: E402


def _wall(x0, y0, x1, y1, n=1000, z0=0.0, z1=10.0, contrast=5.0, idx=None):
    d = math.hypot(x1 - x0, y1 - y0)
    ux, uy = (x1 - x0) / d, (y1 - y0) / d
    nx, ny = uy, -ux
    return D.Wall(x0, y0, x1, y1, z0, z1, nx, ny, nx * x0 + ny * y0, 0.0, n, 0.05, contrast, 50, idx)


def _slab(rng, p0, p1, z0, z1, density=6.0, noise=0.04):
    """Points on a vertical rectangle."""
    p0, p1 = np.array(p0, float), np.array(p1, float)
    length = float(np.hypot(*(p1 - p0)))
    m = rng.poisson(length * (z1 - z0) * density)
    t = rng.uniform(0, length, m)
    u = (p1 - p0) / length
    nrm = np.array([u[1], -u[0]])
    xy = p0 + t[:, None] * u + rng.normal(0, noise, m)[:, None] * nrm
    return np.column_stack([xy, rng.uniform(z0, z1, m)])


# ---------------------------------------------------------------------------------------------------------------------

def test_z_extent_widens_the_trimmed_range_and_ignores_outliers():
    rng = np.random.RandomState(0)
    z = np.concatenate([rng.uniform(5.0, 15.0, 20000), [-40.0, 90.0]])
    lo, hi = D._z_extent(z)
    assert lo == pytest.approx(5.0, abs=0.3) and hi == pytest.approx(15.0, abs=0.3)


def test_occupancy_keeps_walls_and_drops_roofs_and_ground():
    rng = np.random.RandomState(1)
    ground = np.column_stack([rng.uniform(0, 30, (6000, 2)), rng.normal(0, 0.03, 6000)])
    roof = np.column_stack([rng.uniform(5, 25, (6000, 2)), rng.normal(12, 0.05, 6000)])
    wall = _slab(rng, (5, 5), (25, 5), 0, 12)
    P = np.vstack([ground, roof, wall])
    kept = D.occupancy_cells(P, D.Params())
    labels = np.concatenate([np.zeros(len(ground)), np.ones(len(roof)), np.full(len(wall), 2)])[kept]
    assert (labels == 2).sum() > 0.9 * len(wall)
    assert (labels == 0).sum() < 0.05 * len(ground)
    assert (labels == 1).sum() < 0.2 * len(roof)          # only the roof edge cells above the wall column
    assert len(D.occupancy_cells(np.zeros((0, 3)), D.Params())) == 0


def test_a_flat_roof_is_never_a_wall_whatever_its_density():
    rng = np.random.RandomState(2)
    roof = np.column_stack([rng.uniform(0, 30, (200000, 2)), rng.normal(12, 0.03, 200000)])
    ground = np.column_stack([rng.uniform(-10, 40, (20000, 2)), rng.normal(0, 0.03, 20000)])
    assert D.detect(np.vstack([roof, ground]), "auto") == []


def test_suppress_duplicates_relief_and_setbacks():
    p = D.Params()
    main = _wall(0, 0, 60, 0, n=50000)
    dup = _wall(5, 0.3, 55, 0.3, n=4000)                  # the same wall seen again 0.3 m off
    balcony = _wall(10, -1.0, 14, -1.0, n=800)             # a short piece 1 m in front: relief
    layer = _wall(0, -1.2, 58, -1.2, n=3000)               # a long weak layer 1.2 m in front: relief (under a third of the points)
    setback = _wall(20, -3.0, 40, -3.0, n=15000)           # 3 m behind the line: a real setback
    far = _wall(0, 30, 50, 30, n=20000)                    # another wall
    kept = D.suppress([dup, balcony, layer, setback, far, main], p)
    assert main in kept and setback in kept and far in kept
    assert dup not in kept and balcony not in kept and layer not in kept
    # perpendicular walls never suppress each other
    gable = _wall(0, 0, 0, -15, n=10000)
    assert gable in D.suppress([main, gable], p)


def test_aligned_with_axes_removes_slanted_debris_but_keeps_other_buildings():
    p = D.Params()
    main = _wall(0, 0, 60, 0, n=50000)
    gable = _wall(60, 0, 60, -12, n=10000)
    debris = _wall(10, -1, 24, -6, n=2500, contrast=3.0)      # slanted, dwarfed, close to the main wall
    other = _wall(150, 100, 160, 108, n=3000, contrast=3.0)     # another building far away with its own direction
    kept = D.aligned_with_axes([main, gable, debris, other], p)
    assert debris not in kept
    assert main in kept and gable in kept and other in kept
    strong = _wall(10, -1, 24, -6, n=2500, contrast=20.0)
    assert strong in D.aligned_with_axes([main, strong], p)     # but very clear evidence keeps a wall


def test_regularize_snaps_gables_perpendicular_and_leaves_skewed_wings():
    rng = np.random.RandomState(3)
    front = _slab(rng, (0, 0), (60, 0), 0, 10, 8.0)
    gable = _slab(rng, (60, 0), (60, -12), 0, 10, 8.0)           # truly perpendicular
    wing = _slab(rng, (0, 0), (-8, -8), 0, 10, 8.0)              # 45 degrees: a real skewed wing
    P = np.vstack([front, gable, wing])
    nf, ng = len(front), len(gable)
    w_front = _wall(0, 0, 60, 0, n=50000, idx=np.arange(nf))
    w_gable = _wall(60.0, 0.0, 60.4, -12.0, n=9000, idx=np.arange(nf, nf + ng))   # fitted 2 degrees off the perpendicular
    w_wing = _wall(0, 0, -8, -8, n=9000, idx=np.arange(nf + ng, len(P)))
    p = D.Params()
    before = w_gable.azimuth
    reg = D.regularize(P, [w_front, w_gable, w_wing], p)
    assert abs(before - 90.0) > 1.5
    assert reg[1].azimuth == pytest.approx(90.0, abs=0.05)             # now exactly perpendicular to the front
    assert reg[1].x0 == pytest.approx(60.0, abs=0.1) and reg[1].x1 == pytest.approx(60.0, abs=0.1)
    assert reg[1].length == pytest.approx(12.0, abs=0.3)
    assert reg[2].azimuth == pytest.approx(45.0, abs=1.0) or reg[2].azimuth == pytest.approx(135.0, abs=1.0)   # the wing is left alone
    # a wall that really is 3 degrees off keeps its own direction (its points would leave the band)
    skew = _slab(rng, (60, 0), (60.6, -12), 0, 10, 8.0)
    P2 = np.vstack([front, skew])
    w_skew = _wall(60.0, 0.0, 60.6, -12.0, n=9000, idx=np.arange(nf, len(P2)))
    reg2 = D.regularize(P2, [_wall(0, 0, 60, 0, n=50000, idx=np.arange(nf)), w_skew], p)
    assert reg2[1].azimuth == pytest.approx(math.degrees(math.atan2(-12.0, 0.6)) % 180.0, abs=0.1)


def test_outside_normal_uses_the_ground_in_front_not_the_roof():
    rng = np.random.RandomState(4)
    wall = _slab(rng, (0, 0), (30, 0), 0, 12)                      # faces south (-y): the ground is there
    ground = np.column_stack([rng.uniform(0, 30, (3000, 1)), rng.uniform(-8, -0.7, (3000, 1)), rng.normal(-0.1, 0.04, (3000, 1))])
    roof = np.column_stack([rng.uniform(0, 30, (6000, 1)), rng.uniform(0.7, 10, (6000, 1)), rng.normal(12, 0.05, (6000, 1))])
    P = np.vstack([wall, ground, roof])
    w = _wall(0, 0, 30, 0, z0=0.0, z1=12.0)
    nx, ny = D.outside_normal(P, w)
    assert ny < -0.99
    flipped = _wall(30, 0, 0, 0, z0=0.0, z1=12.0)
    assert D.outside_normal(P, flipped)[1] < -0.99
    # no decisive data: the cameras vote
    sparse = np.vstack([wall])
    cams = np.array([[15.0, 40.0, 30.0]] * 5)
    assert D.outside_normal(sparse, w, cams)[1] > 0.99


def test_walls_to_facades_builds_a_right_handed_frame_facing_outward():
    rng = np.random.RandomState(5)
    wall = _slab(rng, (0, 0), (30, 0), 0, 12)
    ground = np.column_stack([rng.uniform(0, 30, 3000), rng.uniform(-8, -0.7, 3000), rng.normal(0, 0.04, 3000)])
    P = np.vstack([wall, ground])
    w = _wall(0, 0, 30, 0, n=len(wall), z0=0.0, z1=12.0)
    f = D.walls_to_facades([w], P)[0]
    assert f.letter == "A" and f.facing == "S" and f.azimuth == pytest.approx(180.0)
    assert f.u == pytest.approx((1.0, 0.0, 0.0)) and f.n == pytest.approx((0.0, -1.0, 0.0)) and f.v == pytest.approx((0.0, 0.0, 1.0))
    assert f.origin == pytest.approx((0.0, 0.0, 0.0)) and f.length == pytest.approx(30.0) and f.height == pytest.approx(12.0)
    assert 0.0 <= f.score <= 1.0
    # the same wall given end to end the other way round gives the same frame
    w2 = _wall(30, 0, 0, 0, n=len(wall), z0=0.0, z1=12.0)
    f2 = D.walls_to_facades([w2], P)[0]
    assert f2.origin == pytest.approx(f.origin) and f2.u == pytest.approx(f.u)


def test_walls_are_named_by_decreasing_area():
    small = _wall(0, 0, 10, 0, z1=5)
    big = _wall(0, 20, 60, 20, z1=20)
    P = np.vstack([np.array([[0, 0, 0.0], [10, 0, 5.0], [0, 20, 0.0], [60, 20, 20.0]]), np.array([[5, -5, 0.0]] * 30), np.array([[5, 15, 0.0]] * 30)])
    fs = D.walls_to_facades([small, big], P)
    assert [f.length for f in fs] == pytest.approx([60.0, 10.0]) and [f.letter for f in fs] == ["A", "B"]


def test_detect_in_polygon_filters_progress_and_cancel():
    fb = FB.FakeBackend(delay=0)
    around = [(-5, -20), (75, -20), (75, 22), (-5, 22)]
    seen = []
    fs = D.detect_in_polygon(fb.tie_points, around, lambda f, t: seen.append(f), None, cameras=fb.cameras)
    assert len(fs) == 6 and seen == sorted(seen) and seen[-1] == 1.0
    assert D.detect_in_polygon(fb.tie_points, [(500, 500), (510, 500), (510, 510)]) == []
    with pytest.raises(Cancelled):
        D.detect_in_polygon(fb.tie_points, around, None, lambda: True)


def test_synthetic_building_geometry_is_centimetric():
    """Detected façades against the truth of the FakeBackend: plane offset, direction, length and height."""
    fb = FB.FakeBackend(delay=0)
    fs = D.detect_in_polygon(fb.tie_points, [(-5, -20), (75, -20), (75, 22), (-5, 22)], cameras=fb.cameras)
    truth = [t for t in fb.truth if t["kind"] == "wall" and not t["name"].startswith("H")]
    matched = 0
    for t in truth:
        a = np.array([t["x0"], t["y0"]])
        b = np.array([t["x1"], t["y1"]])
        n = np.array([t["nx"], t["ny"]])
        best = None
        ta, tb = sorted((float(a @ np.array([-n[1], n[0]])), float(b @ np.array([-n[1], n[0]]))))
        for f in fs:
            if np.dot(f.n[:2], n) < 0.99:
                continue
            off = abs(np.dot(np.array(f.origin[:2]) - a, n))
            uf = np.array(f.u[:2])
            fa, fb_ = sorted((float(np.array(f.origin[:2]) @ np.array([-n[1], n[0]])), float(np.array(f.end[:2]) @ np.array([-n[1], n[0]]))))
            if off < 0.3 and min(tb, fb_) - max(ta, fa) > 0.5 * (tb - ta):
                best = f
        if best is not None:
            matched += 1
            if not t["name"].startswith("N"):                      # the north plane is one façade over three heights
                assert abs(best.height - t["height"]) < 0.2 * t["height"] + 1.0
    assert matched >= 6                                        # S1 S2 S3, the north plane, W, E
    # the north plane is one façade covering N1 N2 N3
    north = [f for f in fs if f.facing == "N"][0]
    assert north.length == pytest.approx(66.0, abs=1.5)
    # the set-back section is its own façade, 2.5 m behind the front
    south = sorted((f for f in fs if f.facing == "S"), key=lambda f: f.origin[0])
    assert len(south) == 3
    d = abs(G.enu_to_plane(south[0], np.array([south[1].origin]))[0, 2])
    assert d == pytest.approx(2.5, abs=0.2)


def test_detector_on_a_clutter_free_wall_is_fast_and_accurate():
    rng = np.random.RandomState(6)
    wall = _slab(rng, (0, 0), (40, 0), 0, 9, 4.0)
    ground = np.column_stack([rng.uniform(-10, 50, (30000, 2)) * [1, 0.5] - [0, 3], rng.normal(-0.2, 0.04, 30000)])
    walls = D.detect(np.vstack([wall, ground]), "auto")
    assert len(walls) == 1
    w = walls[0]
    assert w.length == pytest.approx(40.0, abs=0.6) and w.height == pytest.approx(9.0, abs=0.5)
    assert abs(w.y0) < 0.05 and abs(w.y1) < 0.05 and w.rms < 0.06


CARRO = "C:/temp/tie_enu.npy"


@pytest.mark.skipif(not os.path.exists(CARRO), reason="the CARRO tie points are not on this machine")
def test_carro_four_facades_with_the_plans_dimensions():
    P = np.load(CARRO)
    fs = D.detect_in_polygon(P, [(-45, -16), (38, -16), (38, 14), (-45, 14)])
    assert len(fs) == 4
    by = {f.facing: f for f in fs}
    assert set(by) == {"N", "S", "E", "W"}
    assert by["N"].length == pytest.approx(66.46, abs=1.5)        # plans: 66.46 m
    assert by["S"].length == pytest.approx(65.96, abs=1.5)        # plans: 65.96 m
    assert abs(((by["N"].azimuth - by["S"].azimuth) % 360.0) - 180.0) < 1.0
    gap = abs(G.enu_to_plane(by["S"], np.array([by["N"].origin]))[0, 2])
    assert gap == pytest.approx(10.5, abs=0.3)                    # north and south planes 10.5 m apart (the roof plan is 11.8 m deep)
    for f in fs:
        assert f.tilt == 0.0 and f.rms < 0.1 and f.height > 15.0
