"""Automatic zoom of the photo view: msapi.zoom_to_point and photos.zoom_on_marker.

Test doubles local to this file: a fake photo view (camera, center, scale,
width, height, open) and a scheduler that replaces QTimer.singleShot.
"""

import types

import pytest

from aerotools import journal, msapi
from aerotools.tools.fastgcp import marking, photos, session as session_mod


# --------------------------------------------------------------------------
# Test doubles
# --------------------------------------------------------------------------

class FakeView(object):
    """Same surface as Metashape.app.photo_view, with variants."""

    def __init__(self, ms, width=1600, height=900, camera=None, defer_open=False,
                 only_three=False, drift_on_scale=False):
        self.ms = ms
        self.camera = camera
        self.width = width
        self.height = height
        self._scale = 0.4
        self._center = ms.Vector(2000.0, 1500.0)
        self.defer_open = defer_open
        self.only_three = only_three
        self.drift_on_scale = drift_on_scale
        self.opened = []
        self.pending = None
        self.center_sets = []
        self.scale_sets = []

    def open(self, camera):
        self.opened.append(camera)
        if self.defer_open:
            self.pending = camera
        else:
            self.camera = camera

    def finish_open(self):
        if self.pending is not None:
            self.camera, self.pending = self.pending, None

    @property
    def center(self):
        return self._center

    @center.setter
    def center(self, value):
        size = getattr(value, "n", 3)
        self.center_sets.append(size)
        if self.only_three and size != 3:
            raise TypeError("3-component Vector expected")
        self._center = value

    @property
    def scale(self):
        return self._scale

    @scale.setter
    def scale(self, value):
        self.scale_sets.append(value)
        self._scale = value
        if self.drift_on_scale:
            self._center = self.ms.Vector(0.0, 0.0)


class Sched(object):
    """Replaces msapi._schedule: callbacks run on demand."""

    def __init__(self):
        self.queue = []

    def __call__(self, delay_ms, function):
        self.queue.append((delay_ms, function))

    def run_all(self):
        while self.queue:
            _delay, function = self.queue.pop(0)
            function()

    def run_one(self):
        _delay, function = self.queue.pop(0)
        function()


@pytest.fixture(autouse=True)
def _bind(ms, monkeypatch):
    msapi.Metashape = ms
    msapi._ZOOM_LOGGED[0] = False

    class V(ms.Vector):
        def __init__(self, *args):
            values = list(args[0]) if len(args) == 1 else list(args)
            self.n = len(values)
            super(V, self).__init__(*values)

    ms.Vector = V
    sched = Sched()
    monkeypatch.setattr(msapi, "_schedule", sched)
    return sched


@pytest.fixture
def sched(_bind):
    return _bind


@pytest.fixture
def world(ms):
    """A camera at (0,0,-10) looking towards +Z, a marker at (0,0,0)."""
    cam = ms.Camera(1, ms.Vector(0, 0, -10))
    other = ms.Camera(2, ms.Vector(0, 0, -20))
    marker = ms.Marker("GCP1", position=ms.Vector(0, 0, 0))
    chunk = ms.Chunk([cam, other], [marker])
    ms.app.document.chunk = chunk
    view = FakeView(ms, camera=cam)
    ms.app.photo_view = view
    return chunk, marker, cam, other, view


def _zoom_lines():
    return [line for line in journal.lines() if "zoom: center=" in line]


def _state(**extra):
    state = types.SimpleNamespace(enabled=True, zoomed=[], zoom_px=400.0,
                                  active_marker=None, panel=None,
                                  last_cameras=[], last_filter_key=None,
                                  last_diagnostic={}, last_photo_count=0)
    for key, value in extra.items():
        setattr(state, key, value)
    return state


# --------------------------------------------------------------------------
# Scale
# --------------------------------------------------------------------------

def test_scale_is_view_width_over_window(world):
    _chunk, _marker, cam, _other, view = world
    assert msapi.zoom_scale(cam, 400) == pytest.approx(4.0)     # 1600 / 400
    assert msapi.zoom_scale(cam, 800) == pytest.approx(2.0)
    assert msapi.zoom_scale(cam, None) == pytest.approx(4.0)    # default 400
    assert msapi.zoom_scale(cam, "anything") == pytest.approx(4.0)


def test_scale_is_bounded_by_fit_and_max(world):
    _chunk, _marker, cam, _other, view = world
    view.width, view.height = 8000, 6000
    assert msapi.zoom_scale(cam, 400) == msapi.ZOOM_MAX_SCALE   # 20 -> 16
    view.width, view.height = 1000, 500
    fit = min(1000 / 4000.0, 500 / 3000.0)
    assert msapi.zoom_scale(cam, 100000) == pytest.approx(fit)  # not less than the fit scale


def test_scale_falls_back_when_view_width_unknown(world):
    _chunk, _marker, cam, _other, view = world
    view.width = None
    assert msapi.zoom_scale(cam, 400) == msapi.ZOOM_FALLBACK_SCALE == 4.0
    view.width = 0
    assert msapi.zoom_scale(cam, 400) == 4.0


# --------------------------------------------------------------------------
# Anchor point
# --------------------------------------------------------------------------

def test_anchor_uses_existing_projection(ms, world):
    chunk, marker, cam, _other, _view = world
    marker.projections[cam] = ms.Marker.Projection(ms.Vector(1200.0, 800.0), True)
    anchor, how = msapi.marker_anchor(chunk, marker, cam)
    assert anchor == (1200.0, 800.0) and how == "placed"


def test_anchor_falls_back_to_estimated_position(world):
    chunk, marker, cam, _other, _view = world
    anchor, how = msapi.marker_anchor(chunk, marker, cam)
    assert how == "estimated"
    assert anchor == pytest.approx((2000.0, 1500.0))


def test_anchor_none_when_outside_image(ms, world):
    chunk, marker, cam, _other, _view = world
    marker.position = ms.Vector(50, 0, 0)       # x = 2000 + 1000 * 50 / 10 = 7000
    anchor, how = msapi.marker_anchor(chunk, marker, cam)
    assert anchor is None and "outside the image" in how
    marker.projections[cam] = ms.Marker.Projection(ms.Vector(-5.0, 10.0), True)
    anchor, how = msapi.marker_anchor(chunk, marker, cam)
    assert anchor is None and "outside the image" in how


def test_anchor_none_without_data(ms, world):
    chunk, marker, cam, _other, _view = world
    behind = ms.Camera(3, ms.Vector(0, 0, 10))              # looks towards +Z, point behind
    assert msapi.marker_anchor(chunk, marker, behind)[0] is None
    unaligned = ms.Camera(4, ms.Vector(0, 0, -5), aligned=False)
    assert msapi.marker_anchor(chunk, marker, unaligned)[0] is None
    marker.position = None
    assert msapi.marker_anchor(chunk, marker, cam)[0] is None


# --------------------------------------------------------------------------
# zoom_to_point
# --------------------------------------------------------------------------

def test_zoom_sets_center_then_scale_and_logs_once(world, sched):
    _chunk, _marker, cam, _other, view = world
    done = []
    assert msapi.zoom_to_point(cam, 1200.0, 800.0, window_px=400, on_done=done.append)
    assert view.center_sets == []                       # deferred
    assert sched.queue[0][0] == 60
    sched.run_all()
    assert (view.center.x, view.center.y) == (1200.0, 800.0)
    assert view.center.n == 2
    assert view.scale == pytest.approx(4.0)
    assert done == [True]
    assert len(_zoom_lines()) == 1
    assert "read=" in _zoom_lines()[0]
    msapi.zoom_to_point(cam, 100.0, 100.0)
    sched.run_all()
    assert len(_zoom_lines()) == 1                      # only one detailed line


def test_zoom_explicit_scale_is_used_as_is(world, sched):
    _chunk, _marker, cam, _other, view = world
    msapi.zoom_to_point(cam, 500.0, 400.0, scale=10.0)
    sched.run_all()
    assert view.scale == 10.0


def test_zoom_tries_three_components_when_two_refused(ms, world, sched):
    _chunk, _marker, cam, _other, view = world
    view.only_three = True
    msapi.zoom_to_point(cam, 700.0, 600.0)
    sched.run_all()
    assert view.center_sets == [2, 3]
    assert (view.center.x, view.center.y) == (700.0, 600.0)


def test_zoom_reapplies_center_when_scale_moves_it(world, sched):
    _chunk, _marker, cam, _other, view = world
    view.drift_on_scale = True
    msapi.zoom_to_point(cam, 700.0, 600.0)
    sched.run_all()
    assert view.center_sets == [2, 2]       # a second set after the scale


def test_zoom_waits_for_the_photo_then_retries_once(world, sched):
    _chunk, _marker, cam, other, view = world
    view.defer_open = True
    view.open(other)                        # opening not finished yet
    done = []
    msapi.zoom_to_point(other, 900.0, 700.0, on_done=done.append)
    sched.run_one()                         # 1st attempt: the view still shows cam
    assert view.center_sets == [] and done == []
    assert sched.queue[0][0] == 150
    view.finish_open()
    sched.run_all()                         # 2nd attempt: right photo
    assert (view.center.x, view.center.y) == (900.0, 700.0)
    assert done == [True]


def test_zoom_gives_up_when_photo_never_opens(world, sched):
    _chunk, _marker, cam, other, view = world
    done = []
    msapi.zoom_to_point(other, 900.0, 700.0, on_done=done.append)   # the view stays on cam
    sched.run_all()
    assert view.center_sets == [] and view.scale_sets == []
    assert done == [False]
    assert sched.queue == []                # no third try


def test_zoom_without_view_or_with_bad_input_never_raises(ms, world, sched):
    _chunk, _marker, cam, _other, _view = world
    assert msapi.zoom_to_point(None, 1.0, 1.0) is False
    assert msapi.zoom_to_point(cam, float("nan"), 1.0) is False
    assert msapi.zoom_to_point(cam, 1.0, 1.0, scale=-2.0) is False
    ms.app.photo_view = None
    assert msapi.zoom_to_point(cam, 1.0, 1.0) is False
    assert sched.queue == []


def test_zoom_guard_cancels_at_apply_time(world, sched):
    _chunk, _marker, cam, _other, view = world
    flag = [True]
    done = []
    msapi.zoom_to_point(cam, 700.0, 600.0, guard=lambda: flag[0], on_done=done.append)
    flag[0] = False                          # paused in the meantime
    sched.run_all()
    assert view.center_sets == [] and done == [False]


def test_zoom_survives_unreadable_view(ms, world, sched):
    _chunk, _marker, cam, _other, view = world

    class Broken(FakeView):
        @property
        def scale(self):
            raise RuntimeError("unreadable")

        @scale.setter
        def scale(self, value):
            pass

    broken = Broken(ms, camera=cam)
    ms.app.photo_view = broken
    done = []
    assert msapi.zoom_to_point(cam, 700.0, 600.0, on_done=done.append)
    sched.run_all()                          # does not raise
    assert done and done[0] in (True, False)


# --------------------------------------------------------------------------
# photos.zoom_on_marker
# --------------------------------------------------------------------------

def test_zoom_on_marker_centers_on_estimated_point(world, sched):
    _chunk, marker, cam, _other, view = world
    state = _state()
    assert photos.zoom_on_marker(state, marker) is True
    sched.run_all()
    assert (view.center.x, view.center.y) == pytest.approx((2000.0, 1500.0))
    assert view.scale == pytest.approx(4.0)
    assert (1, "GCP1") in state.zoomed


def test_zoom_on_marker_uses_configured_window(world, sched):
    _chunk, marker, cam, _other, view = world
    photos.zoom_on_marker(_state(zoom_px=200.0), marker)
    sched.run_all()
    assert view.scale == pytest.approx(8.0)


def test_zoom_on_marker_prefers_pinned_projection(ms, world, sched):
    _chunk, marker, cam, _other, view = world
    marker.projections[cam] = ms.Marker.Projection(ms.Vector(1234.0, 876.0), True)
    photos.zoom_on_marker(_state(), marker)
    sched.run_all()
    assert (view.center.x, view.center.y) == (1234.0, 876.0)


def test_zoom_on_marker_does_not_override_manual_zoom(world, sched):
    _chunk, marker, cam, _other, view = world
    state = _state()
    photos.zoom_on_marker(state, marker)
    sched.run_all()
    view.scale = 9.0                                   # the operator zooms by hand
    sets = len(view.scale_sets)
    assert photos.zoom_on_marker(state, marker) is False
    sched.run_all()
    assert view.scale == 9.0 and len(view.scale_sets) == sets
    assert photos.zoom_on_marker(state, marker, force=True) is True
    sched.run_all()
    assert view.scale == 9.0 and len(view.scale_sets) == sets   # recentered, scale kept


def test_zoom_on_marker_new_marker_on_same_photo_zooms(ms, world, sched):
    chunk, marker, cam, _other, view = world
    second = ms.Marker("GCP2", position=ms.Vector(1, 0, 0))
    chunk.markers.append(second)
    state = _state()
    photos.zoom_on_marker(state, marker)
    sched.run_all()
    assert photos.zoom_on_marker(state, second) is True
    sched.run_all()
    assert view.center.x == pytest.approx(2000.0 + 1000.0 * 1 / 10.0)


def test_zoom_on_marker_paused_does_nothing(world, sched):
    _chunk, marker, cam, _other, view = world
    state = _state(enabled=False)
    assert photos.zoom_on_marker(state, marker) is False
    assert sched.queue == [] and view.center_sets == [] and state.zoomed == []


def test_zoom_on_marker_paused_between_schedule_and_apply(world, sched):
    _chunk, marker, cam, _other, view = world
    state = _state()
    photos.zoom_on_marker(state, marker)
    state.enabled = False
    sched.run_all()
    assert view.center_sets == []
    assert state.zoomed == []                # not remembered: nothing was done


def test_zoom_on_marker_out_of_image_leaves_view_untouched(ms, world, sched):
    _chunk, marker, cam, _other, view = world
    marker.position = ms.Vector(50, 0, 0)
    before = (view.center.x, view.center.y, view.scale)
    assert photos.zoom_on_marker(_state(), marker) is False
    sched.run_all()
    assert (view.center.x, view.center.y, view.scale) == before
    assert any("no zoom on" in line for line in journal.lines()[-3:])


def test_zoom_on_marker_without_open_photo_or_view(ms, world, sched):
    _chunk, marker, cam, _other, view = world
    view.camera = None
    assert photos.zoom_on_marker(_state(), marker) is False
    ms.app.photo_view = None
    assert photos.zoom_on_marker(_state(), marker) is False
    assert sched.queue == []


def test_zoom_on_marker_forgets_pair_when_zoom_fails(world, sched):
    _chunk, marker, cam, other, view = world
    state = _state()
    assert photos.zoom_on_marker(state, marker, other) is True   # the view stays on cam
    sched.run_all()
    assert state.zoomed == []                # abandoned: no zoom remembered


def test_zoom_memory_is_bounded(ms, world, sched):
    chunk, marker, cam, _other, view = world
    state = _state()
    for index in range(photos.ZOOM_MEMORY + 20):
        state.zoomed.append((1000 + index, "X"))
    photos.zoom_on_marker(state, marker)
    sched.run_all()
    assert len(state.zoomed) == photos.ZOOM_MEMORY
    assert state.zoomed[-1] == (1, "GCP1")


# --------------------------------------------------------------------------
# Integration: next photo, marker choice, undo
# --------------------------------------------------------------------------

def test_advance_opens_next_photo_then_zooms(ms, world, sched):
    chunk, marker, cam, other, view = world
    state = _state(active_marker=marker)
    marker.projections[cam] = ms.Marker.Projection(ms.Vector(2000.0, 1500.0), True)
    photos._advance(state, marker, cam.key)
    assert view.opened == [other]
    sched.run_all()
    # photo `other` at (0,0,-20): the point (0,0,0) also projects to the center
    assert (view.center.x, view.center.y) == pytest.approx((2000.0, 1500.0))
    assert view.scale == pytest.approx(4.0)
    assert (2, "GCP1") in state.zoomed


def test_advance_zoom_waits_for_async_open(ms, world, sched):
    chunk, marker, cam, other, view = world
    view.defer_open = True
    state = _state(active_marker=marker)
    marker.projections[cam] = ms.Marker.Projection(ms.Vector(2000.0, 1500.0), True)
    photos._advance(state, marker, cam.key)
    sched.run_one()                          # the photo is not there yet
    assert view.scale_sets == []
    view.finish_open()
    sched.run_all()
    assert view.scale == pytest.approx(4.0)


def test_advance_paused_neither_opens_nor_zooms(ms, world, sched):
    chunk, marker, cam, other, view = world
    state = _state(active_marker=marker, enabled=False)
    photos._advance(state, marker, cam.key)
    assert view.opened == [] and sched.queue == []


def test_filter_for_marker_zooms_the_open_photo(ms, world, sched):
    chunk, marker, cam, other, view = world
    state = _state()
    photos.filter_for_marker(state, marker)
    sched.run_all()
    assert view.scale == pytest.approx(4.0)
    assert (1, "GCP1") in state.zoomed
    view.scale = 9.0                                       # the operator zoomed by hand
    sets = len(view.scale_sets)
    photos.filter_for_marker(state, marker, force=True)   # click again on the same marker
    sched.run_all()
    assert len(view.scale_sets) == sets                    # the operator's zoom is kept
    assert view.scale == 9.0


def test_undo_reopens_photo_and_zooms(ms, world, sched):
    chunk, marker, cam, other, view = world
    state = _state(active_marker=marker)
    view.camera = other                      # we are on the next photo
    marker.projections[cam] = ms.Marker.Projection(ms.Vector(1500.0, 1000.0), True)
    marking.history.clear()
    marking.history.push(marker, cam, (1500.0, 1000.0, True),
                         chunk_key=chunk.key, label="GCP1", camera_name=cam.label)
    assert marking.undo_last(state) is True
    assert view.opened == [cam]
    sched.run_all()
    assert (view.center.x, view.center.y) == (1500.0, 1000.0)
    assert view.scale == pytest.approx(4.0)


def test_undo_keeps_zoom_when_photo_already_on_screen(ms, world, sched):
    chunk, marker, cam, other, view = world
    state = _state(active_marker=marker)
    state.zoomed.append((cam.key, "GCP1"))   # already zoomed for this marker
    marker.projections[cam] = ms.Marker.Projection(ms.Vector(1500.0, 1000.0), True)
    marking.history.clear()
    marking.history.push(marker, cam, (1500.0, 1000.0, True),
                         chunk_key=chunk.key, label="GCP1", camera_name=cam.label)
    view.scale = 7.0                         # manual zoom on the open photo
    assert marking.undo_last(state) is True
    sched.run_all()
    assert view.scale == 7.0


# --------------------------------------------------------------------------
# image_coords_at stays correct after a zoom
# --------------------------------------------------------------------------

class _Point(object):
    def __init__(self, x, y):
        self._x, self._y = x, y

    def x(self):
        return self._x

    def y(self):
        return self._y


class _Widget(object):
    """Widget the size of the view: mapFromGlobal(pos) = pos (tuple)."""

    def __init__(self, width, height):
        self._w, self._h = width, height

    def mapFromGlobal(self, pos):
        return _Point(pos[0], pos[1])

    def width(self):
        return self._w

    def height(self):
        return self._h


def test_image_coords_follow_center_and_scale_after_zoom(world, sched, monkeypatch):
    _chunk, marker, cam, _other, view = world
    monkeypatch.setattr(msapi, "photo_view_widgets",
                        lambda: [_Widget(view.width, view.height)])
    # Before zoom: scale 0.4, center (2000, 1500). The middle of the view = the center.
    coords, _ = msapi.image_coords_at((800, 450))
    assert coords == pytest.approx((2000.0, 1500.0))

    msapi.zoom_to_point(cam, 1200.0, 800.0)          # scale 4.0
    sched.run_all()
    middle, _ = msapi.image_coords_at((800, 450))
    assert middle == pytest.approx((1200.0, 800.0))   # click in the middle = anchor
    # 100 screen px to the right and 40 down = 25 px and 10 px of image.
    shifted, _ = msapi.image_coords_at((900, 490))
    assert shifted == pytest.approx((1225.0, 810.0))
    # The left edge of the view shows 800 / 4 = 200 image px to the left of the center.
    edge, _ = msapi.image_coords_at((0, 450))
    assert edge == pytest.approx((1000.0, 800.0))


def test_image_coords_reject_unreadable_scale(world, monkeypatch):
    _chunk, _marker, cam, _other, view = world
    monkeypatch.setattr(msapi, "photo_view_widgets",
                        lambda: [_Widget(view.width, view.height)])
    view._scale = 0.0
    coords, reason = msapi.image_coords_at((800, 450))
    assert coords is None and "scale" in reason


# --------------------------------------------------------------------------
# Hidden setting
# --------------------------------------------------------------------------

def test_session_reads_hidden_zoom_setting(local_appdata):
    import json
    import os

    from aerotools import config

    session_mod.reset()
    assert session_mod.Session().zoom_px == 400.0
    os.makedirs(config.settings_dir(), exist_ok=True)
    with open(config.settings_path(), "w", encoding="utf-8") as handle:
        json.dump({"fastgcp": {"zoom_window_px": 250}}, handle)
    assert session_mod.Session().zoom_px == 250.0
    with open(config.settings_path(), "w", encoding="utf-8") as handle:
        json.dump({"fastgcp": {"zoom_window_px": "a lot", "zoom_px": 600}}, handle)
    assert session_mod.Session().zoom_px == 600.0           # alias
    with open(config.settings_path(), "w", encoding="utf-8") as handle:
        json.dump({"fastgcp": {"zoom_window_px": 3}}, handle)
    assert session_mod.Session().zoom_px == 400.0           # out of bounds: default
    with open(config.settings_path(), "w", encoding="utf-8") as handle:
        json.dump({"fastgcp": {"zoom_fenetre_px": 300}}, handle)
    assert session_mod.Session().zoom_px == 300.0           # key written by earlier versions
    session_mod.reset()


def test_session_options_unchanged_by_zoom_setting(local_appdata):
    session_mod.reset()
    state = session_mod.Session()
    assert state.options == session_mod.DEFAULTS
    state.zoomed.append((1, "A"))
    state.forget_chunk()
    assert state.zoomed == []
    session_mod.reset()


def test_zoom_keeps_the_operator_scale_and_only_recenters(world, sched):
    _chunk, _marker, cam, _other, view = world
    view._scale = 9.0                      # well above the fit scale (0.3)
    msapi.zoom_to_point(cam, 1200.0, 800.0, window_px=400)
    sched.run_all()
    assert (view.center.x, view.center.y) == (1200.0, 800.0)
    assert view.scale_sets == [] and view.scale == 9.0
    assert "operator scale kept" in _zoom_lines()[-1]


def test_zoom_applies_its_scale_on_a_view_that_is_still_fitted(world, sched):
    _chunk, _marker, cam, _other, view = world
    view._scale = 0.3                      # whole photo in the window
    msapi.zoom_to_point(cam, 1200.0, 800.0, window_px=400)
    sched.run_all()
    assert view.scale == pytest.approx(4.0)


def test_zoom_explicit_scale_ignores_the_operator_scale(world, sched):
    _chunk, _marker, cam, _other, view = world
    view._scale = 9.0
    msapi.zoom_to_point(cam, 500.0, 400.0, scale=10.0)
    sched.run_all()
    assert view.scale == 10.0
