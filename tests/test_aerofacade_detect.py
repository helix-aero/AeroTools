"""Aero-Facade prototype: wall detection on a synthetic scene (terrain, buildings, roofs, trees, outliers, a fence)."""

import math
import os
import sys

import pytest

np = pytest.importorskip("numpy")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aerofacade_synth as S  # noqa: E402
from aerotools.tools.aerofacade import detect as D  # noqa: E402


def _covered(walls, g, atol=6.0, dtol=0.3):
    """True if some detected wall lies on the ground-truth wall `g` (direction, distance of its middle, overlap)."""
    gx, gy = g["x1"] - g["x0"], g["y1"] - g["y0"]
    ang = math.degrees(math.atan2(gy, gx))
    mid = np.array([(g["x0"] + g["x1"]) / 2, (g["y0"] + g["y1"]) / 2])
    for w in walls:
        da = abs(((w.azimuth - ang + 90.0) % 180.0) - 90.0)
        n = np.array([w.nx, w.ny])
        if da <= atol and abs(mid @ n - w.d) <= dtol and w.length >= 0.5 * math.hypot(gx, gy):
            return True
    return False


@pytest.fixture(scope="module")
def scene():
    return S.make_scene(seed=1, visible_range=(1.0, 1.0), wall_density=2.0, curved=False)


def test_plumb_walls_found_and_fence_ignored(scene):
    P, L, gt = scene
    walls = D.detect(P, "grid_hough")
    straight = [g for g in gt if g["kind"] == "wall"]
    found = sum(_covered(walls, g) for g in straight)
    assert found >= 0.8 * len(straight)
    # the 1.2 m fence is below the 2 m wall height: never reported
    for w in walls:
        labels = np.bincount(L[w.idx], minlength=6)
        assert labels.argmax() != S.FENCE


def test_wall_geometry_is_centimetric(scene):
    P, L, gt = scene
    for w in D.detect(P, "grid_hough"):
        assert w.height >= 2.0 and w.length >= 2.0
        assert w.rms < 0.1
        assert w.tilt < 3.0


def test_works_in_projected_coordinates_and_any_rotation():
    # the same scene rotated and moved to Lambert-like coordinates must give the same number of walls
    a = S.make_scene(seed=2, visible_range=(1.0, 1.0), curved=False, rotation_deg=0.0, offset=(0.0, 0.0))
    b = S.make_scene(seed=2, visible_range=(1.0, 1.0), curved=False, rotation_deg=37.0, offset=(2175800.0, 1206700.0))
    na = len(D.detect(a[0], "grid_hough"))
    nb = len(D.detect(b[0], "grid_hough"))
    assert abs(na - nb) <= 2


def test_leaning_wall_needs_the_plane_pass():
    P, L, gt = S.make_scene(seed=1, visible_range=(1.0, 1.0), lean_deg=8.0, curved=False)
    walls = D.detect(P, "combined")
    leaning = [w for w in walls if w.tilt >= 3.0]
    assert leaning, "a wall leaning 8 degrees should come out of the second (plane) pass"


def test_empty_and_tiny_inputs():
    assert D.detect(np.zeros((0, 3)), "grid_hough") == []
    assert D.detect(np.random.RandomState(0).uniform(0, 5, (10, 3)), "grid_hough") == []
