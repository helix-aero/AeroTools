import types

from aerotools.tools.fastgcp import photos


def _cams(*keys):
    return [types.SimpleNamespace(key=key) for key in keys]


def test_next_unpinned_skips_pinned_photos_after_current():
    cams = _cams(0, 1, 2, 3)
    cam, remaining = photos.next_unpinned(cams, 0, {2})
    assert cam.key == 1 and remaining is True
    cam, _ = photos.next_unpinned(cams, 1, {2})
    assert cam.key == 3


def test_next_unpinned_none_left_after_current():
    cams = _cams(0, 1, 2)
    cam, remaining = photos.next_unpinned(cams, 2, {2})
    assert cam is None and remaining is True  # la 0 et la 1 restent avant
    cam, remaining = photos.next_unpinned(cams, 2, {0, 1, 2})
    assert cam is None and remaining is False


def test_next_unpinned_starts_from_top_when_current_not_listed():
    cams = _cams(5, 6)
    cam, _ = photos.next_unpinned(cams, 99, {5})
    assert cam.key == 6
    cam, _ = photos.next_unpinned(cams, 99, set())
    assert cam.key == 5


def test_advance_delay_is_300_ms():
    assert photos.ADVANCE_DELAY_MS == 300


def test_same_marker_compares_labels_not_objects(monkeypatch):
    """La synchro Reference recree l'objet marqueur : l'identite ne suffit pas."""
    from aerotools import msapi

    monkeypatch.setattr(msapi, "marker_label", lambda m: m.label)
    old = types.SimpleNamespace(label="PS_2")
    new = types.SimpleNamespace(label="PS_2")
    other = types.SimpleNamespace(label="PS_3")
    state = types.SimpleNamespace(active_marker=new)
    assert photos._same_marker(state, old) is True
    state.active_marker = other
    assert photos._same_marker(state, old) is False
    state.active_marker = None
    assert photos._same_marker(state, old) is False
