"""Aero-Facade: lengthening and shortening a wall by hand (the handles at its ends): the pure geometry of an end move, the extent kept on the
facade (detected one remembered, reset), the state rules (builds invalidated, signatures), and the pure helpers of the picture's handles."""
import json

import pytest

from aerotools.tools.aerofacade import geometry as G
from aerotools.tools.aerofacade import msfacade
from aerotools.tools.aerofacade import state as S
from aerotools.tools.aerofacade import widgets as W
from aerotools.tools.aerofacade.model import Facade, Margins, Method


def wall(letter="A", length=30.0):
    """A 30 m wall from (10, 20) along (0.6, 0.8), foot at 5 m, 8 m high, facing (0.8, -0.6)."""
    return Facade.from_frame(letter, 0, (10.0, 20.0, 5.0), (0.6, 0.8), length, 8.0, n=(0.8, -0.6))


def end_point(extent, f):
    x0, y0, _z0, length = extent
    return (x0 + length * f.ux, y0 + length * f.uy)


# -- projection of the mouse on the wall's line ------------------------------------------------------------

def test_a_point_is_projected_on_the_wall_axis():
    f = wall()
    assert G.axis_position(f, 10.0, 20.0) == pytest.approx(0.0)
    assert G.axis_position(f, 10.0 + 7 * 0.6, 20.0 + 7 * 0.8) == pytest.approx(7.0)
    off = (10.0 + 12 * 0.6 + 5 * 0.8, 20.0 + 12 * 0.8 - 5 * 0.6)                   # 12 m along, 5 m to the side
    assert G.axis_position(f, *off) == pytest.approx(12.0)
    assert G.axis_position(f, 10.0 - 0.6 * 4, 20.0 - 0.8 * 4) == pytest.approx(-4.0)


# -- moving the right end -----------------------------------------------------------------------------------

def test_the_right_end_follows_the_mouse_along_the_line_and_nothing_else_changes():
    f = wall()
    x0, y0, z0, length = G.moved_end(f, "end", 10.0 + 41 * 0.6 + 3 * 0.8, 20.0 + 41 * 0.8 - 3 * 0.6)     # 41 m along, 3 m off the line
    assert (x0, y0, z0) == (f.x0, f.y0, f.z0)                                                           # the start does not move
    assert length == pytest.approx(41.0)
    ex, ey = end_point((x0, y0, z0, length), f)
    assert (ex - 10.0) * 0.8 - (ey - 20.0) * 0.6 == pytest.approx(0.0, abs=1e-9)                       # still on the same line


def test_the_right_end_can_be_shortened_but_not_below_one_metre_nor_past_the_start():
    f = wall()
    assert G.moved_end(f, "end", 10.0 + 0.6 * 12, 20.0 + 0.8 * 12)[3] == pytest.approx(12.0)
    assert G.moved_end(f, "end", 10.0 + 0.6 * 0.3, 20.0 + 0.8 * 0.3)[3] == G.MIN_WALL_LENGTH == 1.0
    assert G.moved_end(f, "end", 10.0 - 5 * 0.6, 20.0 - 5 * 0.8)[3] == 1.0                              # dragged behind the start
    assert G.moved_end(f, "end", 10.0 + 0.6 * 9, 20.0 + 0.8 * 9, min_length=4.0)[3] == pytest.approx(9.0)
    assert G.moved_end(f, "end", 10.0 + 0.6 * 2, 20.0 + 0.8 * 2, min_length=4.0)[3] == 4.0


def test_the_maximum_length_and_the_picture_limit_clamp_the_move():
    f = wall()
    assert G.moved_end(f, "end", 10.0 + 0.6 * 900, 20.0 + 0.8 * 900)[3] == G.MAX_WALL_LENGTH
    box = (-100.0, -100.0, 40.0, 100.0)                                           # the picture stops at x = 40
    extent = G.moved_end(f, "end", 10.0 + 0.6 * 80, 20.0 + 0.8 * 80, box=box)
    assert extent[3] == pytest.approx(50.0)                                       # x = 10 + 0.6 s = 40  ->  s = 50
    assert end_point(extent, f)[0] == pytest.approx(40.0)
    start = G.moved_end(f, "start", 10.0 - 0.6 * 90, 20.0 - 0.8 * 90, box=(-5.0, -100.0, 100.0, 100.0))
    assert start[0] == pytest.approx(-5.0) and start[3] == pytest.approx(30.0 + 25.0)         # x = 10 + 0.6 s = -5  ->  s = -25
    assert G.axis_range_in_box(f, (100.0, 0.0, 200.0, 10.0)) == (0.0, 0.0)             # a line that misses the picture leaves no room


# -- moving the left end ------------------------------------------------------------------------------------

def test_the_left_end_moves_the_origin_along_the_line_and_keeps_the_right_end_where_it_is():
    f = wall()
    before = end_point((f.x0, f.y0, f.z0, f.length), f)
    extent = G.moved_end(f, "start", 10.0 + 0.6 * 10 + 2 * 0.8, 20.0 + 0.8 * 10 - 2 * 0.6)       # 10 m along, 2 m off the line
    x0, y0, z0, length = extent
    assert length == pytest.approx(20.0) and z0 == f.z0
    assert (x0, y0) == pytest.approx((10.0 + 6.0, 20.0 + 8.0))
    assert end_point(extent, f) == pytest.approx(before)                          # the right end did not move
    longer = G.moved_end(f, "start", 10.0 - 0.6 * 6, 20.0 - 0.8 * 6)               # pulled outwards: lengthened
    assert longer[3] == pytest.approx(36.0) and end_point(longer, f) == pytest.approx(before)


def test_the_left_end_keeps_at_least_the_minimum_length_and_never_passes_the_right_end():
    f = wall()
    before = end_point((f.x0, f.y0, f.z0, f.length), f)
    for target in (29.7, 30.0, 45.0):
        extent = G.moved_end(f, "start", 10.0 + 0.6 * target, 20.0 + 0.8 * target)
        assert extent[3] == 1.0 and end_point(extent, f) == pytest.approx(before)
    assert G.moved_end(f, "start", 10.0 - 0.6 * 900, 20.0 - 0.8 * 900)[3] == G.MAX_WALL_LENGTH


def test_an_unknown_end_is_refused():
    with pytest.raises(ValueError):
        G.moved_end(wall(), "middle", 0.0, 0.0)


# -- the extent kept on the facade ---------------------------------------------------------------------------

def test_the_detected_extent_is_remembered_the_first_time_and_reset_brings_it_back():
    f = wall()
    assert not f.edited and f.detected is None and f.reset_extent() is False
    f.set_extent(16.0, 28.0, 5.0, 20.0)
    assert f.edited and f.detected == (10.0, 20.0, 5.0, 30.0) and f.length == 20.0
    f.set_extent(14.0, 25.0, 5.0, 22.0)                                           # a second edit keeps the FIRST detected extent
    assert f.detected == (10.0, 20.0, 5.0, 30.0)
    assert f.reset_extent() is True
    assert (f.x0, f.y0, f.z0, f.length) == (10.0, 20.0, 5.0, 30.0) and not f.edited


def test_an_edit_back_to_the_detected_extent_is_no_edit():
    f = wall()
    f.set_extent(16.0, 28.0, 5.0, 20.0)
    f.set_extent(10.0, 20.0, 5.0, 30.0)
    assert not f.edited and f.detected is None


def test_the_edit_survives_a_json_round_trip_and_the_wall_keeps_its_line_and_height():
    f = wall()
    height, azimuth, u = f.height, f.azimuth, f.u
    f.set_extent(16.0, 28.0, 5.0, 20.0)
    back = Facade.from_dict(json.loads(json.dumps(f.to_dict())))
    assert back.detected == (10.0, 20.0, 5.0, 30.0) and back.length == 20.0 and back.edited
    assert (back.height, back.azimuth, back.u) == (height, azimuth, u)
    assert Facade.from_dict(json.loads(json.dumps(wall().to_dict()))).detected is None


def test_the_trace_on_the_picture_follows_the_edited_extent():
    f = wall()
    f.set_extent(*G.moved_end(f, "end", 10.0 + 0.6 * 12, 20.0 + 0.8 * 12))
    (ax, ay), (bx, by) = f.polyline_xy()
    assert (ax, ay) == (10.0, 20.0) and (bx, by) == pytest.approx((10.0 + 7.2, 20.0 + 9.6))


# -- state: what an edit invalidates -------------------------------------------------------------------------

def built_state():
    st = S.WizardState()
    st.set_facades([wall("A"), Facade.from_frame("B", 1, (50.0, 0.0, 0.0), (1.0, 0.0), 12.0, 6.0)])
    st.method = "tie_points"
    st.dem_state = {"A": (True, ""), "B": (True, "")}
    st.ortho_state = {"A": (True, ""), "B": (True, "")}
    st.ortho_key = st.signature()
    st.picture_choice = {"A"}
    return st


def test_editing_a_wall_invalidates_the_builds_and_changes_both_signatures():
    st = built_state()
    sig, dem_sig = st.signature(), st.dem_signature()
    assert st.set_wall_extent("A", 16.0, 28.0, 5.0, 20.0) is True
    assert st.dem_state == {} and st.ortho_state == {} and st.ortho_key is None and st.picture_choice is None
    assert st.signature() != sig and st.dem_signature() != dem_sig
    assert st.facade("A").length == 20.0 and st.facade("B").length == 12.0
    assert st.set_wall_extent("Z", 0.0, 0.0, 0.0, 5.0) is False


def test_resetting_walls_invalidates_only_when_something_changed():
    st = built_state()
    assert st.reset_walls(["A", "B"]) == [] and st.ortho_state != {}               # nothing was edited: the builds stay
    st.set_wall_extent("A", 16.0, 28.0, 5.0, 20.0)
    st.dem_state = {"A": (True, "")}
    assert st.reset_walls(["B"]) == [] and st.dem_state != {}
    assert st.reset_walls(["A", "B"]) == ["A"] and st.dem_state == {} and st.facade("A").length == 30.0


def test_the_signature_of_a_work_chunk_depends_on_the_extent_of_the_wall():
    import numpy as np
    f = wall()
    base = msfacade.signature(f, Margins(), Method.TIE_POINTS, np.eye(4))
    f.set_extent(f.x0, f.y0, f.z0, 25.0)
    assert msfacade.signature(f, Margins(), Method.TIE_POINTS, np.eye(4)) != base
    f.reset_extent()
    assert msfacade.signature(f, Margins(), Method.TIE_POINTS, np.eye(4)) == base


def test_an_edited_wall_changes_the_picture_and_the_region_that_are_built():
    f = wall()
    m = Margins()
    before = G.picture_size_m(f, m)
    box = G.bounding_region(f, m)
    f.set_extent(*G.moved_end(f, "end", 10.0 + 0.6 * 20, 20.0 + 0.8 * 20))
    assert G.picture_size_m(f, m)[0] == pytest.approx(before[0] - 10.0)
    assert G.bounding_region(f, m)["size"][0] == pytest.approx(box["size"][0] - 10.0)
    assert any("%.4f" % 20.0 in line or "20.00" in line for line in G.georef_lines(f, m, 0.03))       # the georeferencing text says 20 m


# -- helpers of the picture ---------------------------------------------------------------------------------

def test_the_nearest_handle_within_reach_is_grabbed_and_a_selected_wall_wins_a_near_tie():
    handles = [("A", "start", (100.0, 100.0)), ("A", "end", (300.0, 100.0)), ("B", "start", (104.0, 100.0))]
    assert W.handle_at(handles, (101.0, 101.0)) == ("A", "start")
    assert W.handle_at(handles, (103.0, 100.0)) == ("B", "start")
    assert W.handle_at(handles, (102.0, 100.0)) == ("A", "start")                        # 2 px against 2 px: the first one
    assert W.handle_at(handles, (102.0, 100.0), prefer=("B",)) == ("B", "start")        # ... unless the other wall is the selected one
    assert W.handle_at(handles, (200.0, 100.0)) is None
    assert W.handle_at(handles, (300.0 + W.HANDLE_HIT_PX - 1, 100.0)) == ("A", "end")
    assert W.handle_at(handles, (300.0 + W.HANDLE_HIT_PX + 1, 100.0)) is None
    assert W.HANDLE_HIT_PX > W.HANDLE_R                                                 # easier to grab than to see


@pytest.mark.parametrize("dx,dy,name", [(10, 0, "horizontal"), (-10, 1, "horizontal"), (0, 10, "vertical"), (1, -10, "vertical"),
                                        (10, -10, "diag_up"), (-10, 10, "diag_up"), (10, 10, "diag_down"), (-10, -10, "diag_down")])
def test_the_resize_cursor_follows_the_direction_of_the_wall_on_the_screen(dx, dy, name):
    assert W.resize_cursor(dx, dy) == name
