"""Aero-Facade model.py and backend.py: names, colours, margins links, JSON round trips, Progress."""

import json
import math

import pytest

from aerotools.tools.aerofacade import backend as B
from aerotools.tools.aerofacade import model as M


def test_letter_names():
    assert [M.letter_name(i) for i in (0, 1, 25)] == ["A", "B", "Z"]
    assert [M.letter_name(i) for i in (26, 27, 51, 52, 701, 702)] == ["AA", "AB", "AZ", "BA", "ZZ", "AAA"]
    with pytest.raises(ValueError):
        M.letter_name(-1)


def test_letter_index_round_trip():
    for i in list(range(0, 800, 7)) + [25, 26, 701, 702]:
        assert M.letter_index(M.letter_name(i)) == i
    with pytest.raises(ValueError):
        M.letter_index("A1")
    with pytest.raises(ValueError):
        M.letter_index("")


def test_palette_is_distinct_and_repeats():
    assert len(set(M.PALETTE)) == len(M.PALETTE) >= 12
    assert all(len(c) == 7 and c[0] == "#" for c in M.PALETTE)
    assert M.colour_for(0) == M.colour_for(len(M.PALETTE))


def test_compass_names():
    assert [M.compass_name(a) for a in (0, 44, 46, 90, 180, 270, 359, 360, -90)] == ["N", "NE", "NE", "E", "S", "W", "N", "N", "W"]


def test_margins_defaults_and_links():
    m = M.Margins()
    assert (m.left, m.right, m.top, m.bottom, m.depth) == (1.0, 1.0, 1.0, 1.0, 2.0)
    assert m.link_lr and m.link_tb
    m.set_left(3)
    assert m.right == 3
    m.set_bottom(0.5)
    assert m.top == 0.5
    m.set_link_lr(False)
    m.set_left(4)
    assert (m.left, m.right) == (4, 3)
    m.set_right(2)
    assert (m.left, m.right) == (4, 2)
    m.set_link_lr(True)                       # linking again makes the right side equal to the left one
    assert m.right == 4
    m.set_link_tb(False)
    m.set_top(7)
    assert (m.top, m.bottom) == (7, 0.5)


def test_margins_clamp_and_nan():
    m = M.Margins()
    m.set_left(-5)
    assert m.left == 0
    m.set_left(1e9)
    assert m.left == M.Margins.MAX_SIDE
    m.set_left(float("nan"))
    assert m.left == 0
    m.set_depth(0)
    assert m.depth == 0.1
    m.set_depth(1e9)
    assert m.depth == M.Margins.MAX_DEPTH
    m.set_all(2.5)
    assert (m.left, m.right, m.top, m.bottom) == (2.5,) * 4


def test_margins_json_round_trip():
    m = M.Margins(left=2, right=3, top=1.5, bottom=0.5, depth=4, link_lr=False, link_tb=False)
    d = json.loads(json.dumps(m.to_dict()))
    assert M.Margins.from_dict(d) == m
    assert M.Margins.from_dict(None) == M.Margins()
    c = m.copy()
    c.set_left(9)
    assert m.left == 2


def test_margins_for():
    a, b = M.Margins(left=2), M.Margins(left=3)
    f = M.Facade(letter="B")
    assert M.margins_for(a, f) is a
    assert M.margins_for({"A": a, "B": b}, f) is b
    assert M.margins_for({"A": a, "*": b}, f) is b
    assert M.margins_for({}, f) == M.Margins()
    assert M.margins_for(None, f) == M.Margins()


def test_method_keys():
    assert [m.value for m in M.Method] == ["dense_cloud", "depth_maps", "tie_points"]
    assert M.Method.from_key("depth_maps") is M.Method.DEPTH_MAPS
    assert M.Method.from_key(M.Method.TIE_POINTS) is M.Method.TIE_POINTS
    assert M.Method.from_key("DENSE_CLOUD") is M.Method.DENSE_CLOUD
    with pytest.raises(ValueError):
        M.Method.from_key("nope")


def test_availability_round_trip():
    a = M.Availability.from_dict({"tie_points": (True, ""), "depth_maps": (False, "no depth maps"), "dense_cloud": [False, "none"]})
    assert a.ok(M.Method.TIE_POINTS) and not a.ok("depth_maps")
    assert a.reason(M.Method.DEPTH_MAPS) == "no depth maps"
    assert a.any_surface()
    assert M.Availability.from_dict(json.loads(json.dumps(a.to_dict()))) == a
    nothing = M.Availability.from_dict({})
    assert not nothing.any_surface()
    assert nothing.reason("dense_cloud") == "unknown"
    assert M.Availability.from_dict({"tie_points": True}).ok("tie_points")


def _facade():
    return M.Facade.from_frame("A", 0, (10.0, 20.0, 5.0), (1.0, 0.0, 0.0), 66.0, 15.0, points=1000, score=0.8)


def test_facade_frame_from_south_front():
    f = _facade()                              # wall along east, n = (0, -1, 0): faces south
    assert f.n == (0.0, -1.0, 0.0)
    assert f.v == (0.0, 0.0, 1.0)
    assert f.azimuth == pytest.approx(180.0)
    assert f.facing == "S"
    assert f.end == pytest.approx((76.0, 20.0, 5.0))
    assert f.centre == pytest.approx((43.0, 20.0, 12.5))
    assert f.point_at(1, 2, 3) == pytest.approx((11.0, 17.0, 7.0))     # 3 m toward the cameras = 3 m south
    assert f.polyline_xy()[0] == (10.0, 20.0)
    assert f.area == 990.0
    assert f.density == pytest.approx(1000 / 990.0)
    assert f.colour == M.PALETTE[0]


def test_facade_frame_is_right_handed_for_any_direction():
    for ang in range(0, 360, 17):
        u = (math.cos(math.radians(ang)), math.sin(math.radians(ang)), 0.0)
        f = M.Facade.from_frame("A", 0, (0, 0, 0), u, 10, 5)
        n = f.n
        cross = (f.u[1] * f.v[2] - f.u[2] * f.v[1], f.u[2] * f.v[0] - f.u[0] * f.v[2], f.u[0] * f.v[1] - f.u[1] * f.v[0])
        assert cross == pytest.approx(n, abs=1e-9)                      # u x v = n
        assert f.u[0] * n[0] + f.u[1] * n[1] == pytest.approx(0, abs=1e-9)


def test_facade_explicit_normal_flips_azimuth():
    f = M.Facade.from_frame("A", 0, (0, 0, 0), (1, 0, 0), 10, 5, n=(0, 1, 0))     # faces north
    assert f.azimuth == pytest.approx(0.0)
    assert f.facing == "N"


def test_facade_json_round_trip():
    f = _facade()
    f.removed = True
    d = json.loads(json.dumps(f.to_dict()))
    assert d["facing"] == "S"
    g = M.Facade.from_dict(d)
    assert g == f
    rows = M.facades_to_json([f, g])
    assert M.facades_from_json(rows) == [f, g]


def test_assign_names_and_active():
    fs = [M.Facade() for _ in range(28)]
    M.assign_names(fs)
    assert fs[0].letter == "A" and fs[26].letter == "AA" and fs[27].letter == "AB"
    assert fs[1].colour != fs[0].colour
    fs[3].removed = True
    assert len(M.active(fs)) == 27


def test_settings_round_trip():
    s = M.Settings(margins=M.Margins(left=2), method="tie_points", pixel_m=0.05)
    assert M.Settings.from_dict(json.loads(json.dumps(s.to_dict()))) == s
    assert M.Settings.from_dict(None).method == "depth_maps"


def test_backend_methods_raise():
    b = B.Backend()
    for name, args in [("project_info", ()), ("availability", ()), ("ortho_preview", ()), ("detect", ([], None, None)),
                       ("existing_dems", ([], None, None)), ("build_dems", ([], None, None, None, None)),
                       ("build_orthos", ([], None, None, 0.03, None, None)), ("thumbnail", (None,)),
                       ("export", ([], "", "png", {}, None, None))]:
        with pytest.raises(NotImplementedError):
            getattr(b, name)(*args)


def test_progress_maps_ranges_and_cancels():
    seen = []
    flag = {"stop": False}
    p = B.Progress(lambda f, t: seen.append((round(f, 3), t)), lambda: flag["stop"])
    p(0.5, "half")
    sub = p.sub(0.2, 0.6)
    sub(0.5, "mid")
    sub.sub(0.0, 0.5)(1.0, "inner")
    assert seen == [(0.5, "half"), (0.4, "mid"), (0.4, "inner")]
    p.step(1, 4, "one of four")
    assert seen[-1] == (0.25, "one of four")
    flag["stop"] = True
    with pytest.raises(B.Cancelled):
        p(0.9, "x")
    assert p.is_cancelled()
    with pytest.raises(B.Cancelled):
        sub.check()


def test_progress_without_callbacks_and_of():
    p = B.Progress()
    p(2.0, "clamped")
    assert p.fraction == 1.0 and not p.is_cancelled()
    assert B.Progress.of(p) is p
    q = B.Progress.of(lambda f, t: None, lambda: False)
    assert isinstance(q, B.Progress)
    assert B.Progress.of(None).callback is None


def test_progress_throttle_keeps_the_last_report():
    seen = []
    p = B.Progress(lambda f, t: seen.append(f), throttle=3600.0)
    p(0.1)
    p(0.2)
    p(1.0)
    assert seen == [0.1, 1.0]
