import types

import pytest

from aerotools import journal, msapi
from aerotools.tools.fastgcp import history, marking, photos
from aerotools.tools.fastgcp import session as session_mod


@pytest.fixture(autouse=True)
def _clean(ms, local_appdata):
    msapi.Metashape = ms
    session_mod.reset()
    history.clear()
    photos.cancel_advance()
    saved = (msapi.cursor_over_photo_view, msapi.image_coords_at)
    yield
    msapi.cursor_over_photo_view, msapi.image_coords_at = saved
    history.clear()
    session_mod.reset()


class FakeView(object):
    def __init__(self, camera=None):
        self.camera = camera
        self.opened = []

    def open(self, camera):
        self.opened.append(camera)
        self.camera = camera


class FakePanel(object):
    def __init__(self):
        self.calls = []

    def set_active_marker_by_label(self, label, from_sync=False):
        self.calls.append(("active", label))

    def update_active_card(self):
        self.calls.append(("card",))

    def refresh_errors(self):
        self.calls.append(("errors",))

    def set_status(self, text):
        self.calls.append(("status", text))


@pytest.fixture
def world(ms):
    cam_a = ms.Camera(1, ms.Vector(0, 0, -10))
    cam_b = ms.Camera(2, ms.Vector(5, 0, -10))
    marker = ms.Marker("GCP1", position=ms.Vector(0, 0, 0))
    chunk = ms.Chunk([cam_a, cam_b], [marker])
    ms.app.document.chunk = chunk
    view = FakeView(cam_a)
    ms.app.photo_view = view
    state = session_mod.Session()
    state.active_marker = marker
    state.panel = FakePanel()
    return types.SimpleNamespace(ms=ms, a=cam_a, b=cam_b, marker=marker, chunk=chunk,
                                 view=view, state=state)


def _place(world, x, y, camera=None):
    """Pose via marking.place_at avec une vue photo factice."""
    world.view.camera = camera or world.a
    msapi.cursor_over_photo_view = lambda pos: True
    msapi.image_coords_at = lambda pos: ((x, y), None)
    return marking.place_at(world.state, None)


# --------------------------------------------------------------------------
# La pile
# --------------------------------------------------------------------------

def test_lifo_and_empty_pop():
    assert history.pop() is None
    first = history.push("m1", "c1", None)
    second = history.push("m2", "c2", (1, 2, True))
    assert history.size() == 2 and history.can_undo()
    assert history.peek() is second
    assert history.pop() is second
    assert history.pop() is first
    assert history.pop() is None and not history.can_undo()


def test_depth_is_capped():
    for index in range(history.MAX_DEPTH + 25):
        history.push("m{}".format(index), "c", None)
    assert history.size() == history.MAX_DEPTH
    assert history.pop().marker == "m{}".format(history.MAX_DEPTH + 24)


def test_unusable_entries_are_ignored_without_raising():
    assert history.push(None, "cam", None) is None
    assert history.push("marker", None, None) is None
    assert history.size() == 0
    entry = history.push("marker", "cam", "pas un tuple")
    assert entry.previous is None
    entry = history.push("marker", "cam", ("1.5", 2, 0))
    assert entry.previous == (1.5, 2.0, False)


def test_discard_only_removes_the_top_entry():
    first = history.push("m1", "c", None)
    second = history.push("m2", "c", None)
    history.discard(first)
    assert history.size() == 2
    history.discard(second)
    assert history.size() == 1
    history.discard(None)
    assert history.size() == 1


def test_listeners_are_told_and_a_broken_one_is_dropped():
    seen = []

    def good():
        seen.append(history.size())

    def bad():
        raise RuntimeError("widget detruit")

    history.subscribe(good)
    history.subscribe(good)
    history.subscribe(bad)
    history.push("m", "c", None)
    history.push("m", "c", None)
    history.pop()
    history.clear()
    history.unsubscribe(good)
    assert seen == [1, 2, 1, 0]
    history.notify()
    assert seen == [1, 2, 1, 0]


def test_entry_validity_detects_dead_references():
    class Dead(object):
        @property
        def label(self):
            raise RuntimeError("objet detruit")

    alive = types.SimpleNamespace(label="x", key=1)
    assert history.Entry(alive, alive, None).is_valid()
    assert not history.Entry(Dead(), alive, None).is_valid()


def test_forget_chunk_clears_history():
    history.push("m", "c", None)
    session_mod.Session().forget_chunk()
    assert not history.can_undo()


# --------------------------------------------------------------------------
# msapi.get_projection / remove_projection
# --------------------------------------------------------------------------

def test_get_projection_reads_a_copy_or_none(world):
    assert msapi.get_projection(world.marker, world.a) is None
    msapi.set_projection(world.marker, world.a, 10.0, 20.0, pinned=False)
    assert msapi.get_projection(world.marker, world.a) == (10.0, 20.0, False)
    assert msapi.get_projection(None, world.a) is None


def test_remove_projection(world):
    msapi.set_projection(world.marker, world.a, 1.0, 2.0)
    assert msapi.remove_projection(world.marker, world.a) == (True, "")
    assert world.a not in world.marker.projections
    ok, message = msapi.remove_projection(world.marker, world.a)
    assert ok is False and message


# --------------------------------------------------------------------------
# Pose puis annulation
# --------------------------------------------------------------------------

def test_place_records_history_before_writing(world):
    assert _place(world, 100.0, 200.0) is True
    entry = history.peek()
    assert entry.marker is world.marker and entry.camera is world.a
    assert entry.previous is None
    assert entry.label == "GCP1" and entry.camera_name == "IMG_0001"


def test_undo_removes_a_first_projection_and_reopens_the_photo(world):
    _place(world, 100.0, 200.0, world.a)
    world.view.camera = world.b
    world.state.active_marker = None
    assert marking.undo_last(world.state) is True
    assert world.a not in world.marker.projections
    assert world.view.opened == [world.a]
    assert world.state.active_marker is world.marker
    assert ("active", "GCP1") in world.state.panel.calls
    assert ("errors",) in world.state.panel.calls
    assert not history.can_undo()


def test_undo_restores_the_previous_position_of_a_moved_point(world):
    msapi.set_projection(world.marker, world.a, 11.0, 22.0, pinned=False)
    _place(world, 300.0, 400.0, world.a)
    assert msapi.get_projection(world.marker, world.a) == (300.0, 400.0, True)
    marking.undo_last(world.state)
    assert msapi.get_projection(world.marker, world.a) == (11.0, 22.0, False)


def test_successive_undos_walk_back_in_order(world):
    _place(world, 1.0, 2.0, world.a)
    _place(world, 3.0, 4.0, world.b)
    _place(world, 5.0, 6.0, world.a)
    marking.undo_last(world.state)
    assert msapi.get_projection(world.marker, world.a) == (1.0, 2.0, True)
    marking.undo_last(world.state)
    assert world.b not in world.marker.projections
    marking.undo_last(world.state)
    assert world.a not in world.marker.projections
    assert marking.undo_last(world.state) is False


def test_a_refused_write_leaves_no_history(world, monkeypatch):
    monkeypatch.setattr(msapi, "set_projection", lambda *a, **k: (False, "refus"))
    assert _place(world, 1.0, 2.0) is False
    assert not history.can_undo()


def test_undo_does_nothing_while_the_tool_is_paused(world):
    _place(world, 1.0, 2.0)
    world.state.enabled = False
    assert marking.undo_last(world.state) is False
    assert history.can_undo()
    assert msapi.get_projection(world.marker, world.a) == (1.0, 2.0, True)


def test_undo_with_an_empty_history_says_so(world):
    assert marking.undo_last(world.state) is False
    assert ("status", "Rien à annuler") in world.state.panel.calls
    assert any("rien a annuler" in line for line in journal.lines())


def test_history_from_another_chunk_is_dropped(world):
    _place(world, 1.0, 2.0)
    world.chunk.key = 2
    assert marking.undo_last(world.state) is False
    assert not history.can_undo()
    assert msapi.get_projection(world.marker, world.a) == (1.0, 2.0, True)


def test_dead_entries_are_skipped_without_raising(world):
    _place(world, 1.0, 2.0, world.a)

    class Dead(object):
        @property
        def label(self):
            raise RuntimeError("objet detruit")

    history.push(Dead(), world.a, None, chunk_key=world.chunk.key)
    outsider = types.SimpleNamespace(key=99, label="IMG_0099")
    history.push(world.marker, outsider, None, chunk_key=world.chunk.key)
    assert marking.undo_last(world.state) is True
    assert world.a not in world.marker.projections


def test_undo_never_raises_when_metashape_does(world, monkeypatch):
    _place(world, 1.0, 2.0)

    def boom(*args, **kwargs):
        raise ValueError("api cassee")

    monkeypatch.setattr(msapi, "camera_in_chunk", boom)
    assert marking.undo_last(world.state) is False


def test_cancel_advance_makes_a_pending_advance_stale(world, monkeypatch):
    calls = []
    monkeypatch.setattr(msapi, "current_camera", lambda: world.a)
    monkeypatch.setattr(msapi, "chunk", lambda: calls.append("chunk"))
    photos._ADVANCE_TOKEN[0] += 1
    token = photos._ADVANCE_TOKEN[0]
    photos._advance(world.state, world.marker, 1, token)
    assert "chunk" in calls
    del calls[:]
    photos.cancel_advance()
    photos._advance(world.state, world.marker, 1, token)
    assert calls == []


# --------------------------------------------------------------------------
# Retablir
# --------------------------------------------------------------------------

def test_redo_puts_back_what_undo_took_away(world):
    _place(world, 100.0, 200.0, world.a)
    marking.undo_last(world.state)
    assert world.a not in world.marker.projections and history.can_redo()
    assert marking.redo_last(world.state) is True
    assert msapi.get_projection(world.marker, world.a) == (100.0, 200.0, True)
    assert history.can_undo() and not history.can_redo()
    marking.undo_last(world.state)                      # on peut defaire ce qu'on vient de retablir
    assert world.a not in world.marker.projections


def test_redo_walks_forward_in_order(world):
    _place(world, 1.0, 2.0, world.a)
    _place(world, 3.0, 4.0, world.a)
    marking.undo_last(world.state)
    marking.undo_last(world.state)
    marking.redo_last(world.state)
    assert msapi.get_projection(world.marker, world.a) == (1.0, 2.0, True)
    marking.redo_last(world.state)
    assert msapi.get_projection(world.marker, world.a) == (3.0, 4.0, True)
    assert marking.redo_last(world.state) is False


def test_a_new_pin_clears_the_redo_stack(world):
    _place(world, 1.0, 2.0, world.a)
    marking.undo_last(world.state)
    assert history.can_redo()
    _place(world, 9.0, 9.0, world.b)
    assert not history.can_redo()
    assert marking.redo_last(world.state) is False


def test_redo_with_nothing_to_redo_says_so(world):
    assert marking.redo_last(world.state) is False
    assert any(c[0] == "status" for c in world.state.panel.calls)
