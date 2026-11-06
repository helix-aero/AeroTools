"""Aero-Facade geometry.py."""

import pytest

np = pytest.importorskip("numpy")

from aerotools.tools.aerofacade import geometry as G  # noqa: E402

SQUARE = [(0, 0), (10, 0), (10, 10), (0, 10)]
L_SHAPE = [(0, 0), (10, 0), (10, 4), (4, 4), (4, 10), (0, 10)]


def test_polygon_area_and_bbox():
    assert G.polygon_area(SQUARE) == pytest.approx(100.0)
    assert G.polygon_area(SQUARE[::-1]) == pytest.approx(100.0)             # orientation does not matter
    assert G.polygon_area(L_SHAPE) == pytest.approx(64.0)
    assert G.polygon_area(SQUARE + [SQUARE[0]]) == pytest.approx(100.0)     # explicit closing point
    assert G.polygon_area([(0, 0), (1, 1)]) == 0.0
    assert G.polygon_bbox(L_SHAPE) == (0.0, 0.0, 10.0, 10.0)
    with pytest.raises(ValueError):
        G.polygon_bbox([])


def test_point_in_polygon_square_and_concave():
    pts = np.array([[5, 5], [11, 5], [-1, 5], [5, 11], [5, -1], [2, 2], [8, 8], [2, 8]], float)
    assert G.point_in_polygon(pts, SQUARE).tolist() == [True, False, False, False, False, True, True, True]
    assert G.point_in_polygon(pts, L_SHAPE).tolist() == [False, False, False, False, False, True, False, True]
    assert G.point_in_polygon(np.zeros((0, 2)), SQUARE).shape == (0,)
    assert not G.point_in_polygon(pts, [(0, 0), (1, 1)]).any()


def test_point_in_polygon_is_fast_on_a_million_points():
    import time
    pts = np.random.RandomState(0).uniform(-5, 15, (1000000, 2))
    t = time.time()
    m = G.point_in_polygon(pts, L_SHAPE)
    assert time.time() - t < 3.0
    assert m.mean() == pytest.approx(64.0 / 400.0, abs=0.01)


def test_filter_points_keeps_all_heights():
    P = np.array([[5, 5, -3], [5, 5, 80], [20, 5, 0], [1, 1, 1]], float)
    Q, idx = G.filter_points(P, SQUARE)
    assert idx.tolist() == [0, 1, 3] and Q.shape == (3, 3)
    Q, idx = G.filter_points(np.zeros((0, 3)), SQUARE)
    assert len(Q) == 0 and len(idx) == 0


def test_polygon_validity():
    assert G.polygon_is_valid(SQUARE)
    assert G.polygon_is_valid(L_SHAPE)
    assert not G.polygon_is_valid([(0, 0), (1, 0)])
    assert not G.polygon_is_valid([(0, 0), (10, 10), (10, 0), (0, 10)])      # bow-tie
    assert not G.polygon_is_valid([(0, 0), (0.1, 0), (0.1, 0.1)])             # tiny
    assert G.polygon_is_valid([(0, 0), (0.1, 0), (0.1, 0.1)], min_area=0.0)
