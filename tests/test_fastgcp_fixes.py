"""Aero-GCP: chunk identity, Adjust apply, camera table, batched writes, review feedback, duplicate labels, caps."""

import math
import random
import types

import pytest

from aerotools import msapi
from aerotools.workers import Worker
from aerotools.tools.fastgcp import aigcp, consensus, panel, report, review, session, tool
from test_aigcp import FakePanel, make_state, wait_done, install, ring  # noqa: F401  (fixtures)


@pytest.fixture(autouse=True)
def _bind(ms):
    msapi.Metashape = ms
    msapi._PROPOSALS.clear()
    msapi.invalidate_camera_tables()
    yield
    msapi.invalidate_camera_tables()


def scene(ms, count=40, seed=3):
    """Cameras scattered over a block (some not aligned, some disabled) and a marker near the middle."""
    rng = random.Random(seed)
    V, C = ms.Vector, ms.Camera
    cams = []
    for key in range(1, count + 1):
        cams.append(C(key, V(rng.uniform(-300, 300), rng.uniform(-300, 300), -rng.uniform(30, 80)), sign=1,
                      aligned=rng.random() > 0.1, enabled=rng.random() > 0.1))
    marker = ms.Marker("M", position=V(10.0, 20.0, 0.0))
    chunk = ms.Chunk(cams, [marker])
    ms.app.document.chunk = chunk
    return chunk, marker, cams


def reference_cameras_seeing(chk, marker, limit_m=msapi.MAX_FILTER_DIST_M):
    """The scan as it was written before the camera table (one API read per camera and per marker)."""
    point = msapi.marker_point(chk, marker)
    limit = limit_m / (chk.transform.scale or 1.0)
    found = []
    for cam in chk.cameras:
        if cam.transform is None or not cam.enabled:
            continue
        distance = (cam.center - point).norm()
        if distance > limit:
            continue
        local = cam.transform.inv().mulp(point)
        if local.z <= 0:
            continue
        uv = cam.project(point)
        if uv is None:
            continue
        if 0 <= uv.x < cam.sensor.width and 0 <= uv.y < cam.sensor.height:
            found.append((distance, cam.key))
    return [key for _d, key in sorted(found)]


# -- camera table ---------------------------------------------------------------------------------------------------------

def test_camera_table_gives_the_same_photos_as_the_old_scan(ms):
    for seed in range(5):
        chunk, marker, _cams = scene(ms, 60, seed)
        msapi.invalidate_camera_tables()
        cameras, _diag = msapi.cameras_seeing_marker(chunk, marker)
        assert [c.key for c in cameras] == reference_cameras_seeing(chunk, marker)


def test_the_cameras_are_read_once_for_all_the_markers(ms):
    reads = {"center": 0}

    class Counted(ms.Camera):
        @property
        def center(self):
            reads["center"] += 1
            return self._center

        @center.setter
        def center(self, value):
            self._center = value

    V = ms.Vector
    cams = [Counted(k, V(k * 2.0, 0, -40)) for k in range(1, 31)]
    markers = [ms.Marker("M{}".format(i), position=V(i * 3.0, 0, 0)) for i in range(10)]
    chunk = ms.Chunk(cams, markers)
    ms.app.document.chunk = chunk
    for marker in markers:
        msapi.cameras_seeing_marker(chunk, marker)
    first_pass = reads["center"]
    assert first_pass <= len(cams) * 3                      # the table (1 read each) + the near ones, not 10 full scans
    assert first_pass < 10 * len(cams) / 2


def test_changing_the_project_or_the_chunk_rebuilds_the_table(ms, monkeypatch):
    chunk, marker, cams = scene(ms, 10)
    first = msapi.camera_table(chunk)[0]
    assert msapi.camera_table(chunk)[0] is first            # same chunk: cached
    other = ms.Chunk(list(cams[:5]), [])
    assert msapi.camera_table(other)[0] is not first        # another count / other cameras: rebuilt
    session.Session().forget_chunk()
    assert msapi.camera_table(chunk)[0] is not first        # a chunk change forgets the tables


def test_camera_in_chunk_uses_the_cached_keys_and_still_sees_new_photos(ms):
    chunk, _marker, cams = scene(ms, 10)
    assert msapi.camera_in_chunk(chunk, cams[3])
    stranger = ms.Camera(999, ms.Vector(0, 0, 0))
    assert not msapi.camera_in_chunk(chunk, stranger)
    chunk.cameras.append(stranger)                          # added after the table was read
    assert msapi.camera_in_chunk(chunk, stranger)


def test_a_tab_named_like_a_photo_is_found_without_scanning_the_cameras(ms):
    reads = {"label": 0}

    class Counted(ms.Camera):
        @property
        def label(self):
            reads["label"] += 1
            return "IMG_{:04d}".format(self.key)

        @label.setter
        def label(self, value):
            pass

    cams = [Counted(k, ms.Vector(k, 0, -40)) for k in range(1, 51)]
    chunk = ms.Chunk(cams, [])
    assert msapi._title_is_photo("IMG_0007.JPG", chunk) is True
    assert msapi._title_is_photo("Model", chunk) is False
    after_first = reads["label"]
    for _ in range(200):                                    # clicks in the 3D view
        msapi._title_is_photo("Model", chunk)
        msapi._title_is_photo("IMG_0010", chunk)
    assert reads["label"] == after_first


# -- chunk identity (P1-7) ------------------------------------------------------------------------------------------------

def test_two_projects_with_the_same_chunk_key_are_told_apart(ms):
    chunk = ms.Chunk([], [])
    ms.app.document.chunk = chunk
    ms.app.document.path = "C:/a/one.psx"
    first = msapi.chunk_identity(chunk)
    ms.app.document.path = "C:/b/two.psx"
    assert msapi.chunk_key(chunk) == chunk.key and msapi.chunk_identity(chunk) != first


class _TickPanel(FakePanel):
    def set_chunk_name(self, name):
        pass

    def refresh_markers(self):
        self.calls.append("refresh_markers")


def test_opening_another_project_resets_the_chunk_state(ms):
    chunk = ms.Chunk([], [])
    ms.app.document.chunk = chunk
    ms.app.document.path = "C:/a/one.psx"
    seen = []
    state = types.SimpleNamespace(panel=_TickPanel(), last_chunk_key=msapi.chunk_identity(chunk), last_selected_label="point 1",
                                  aigcp_run=None, forget_chunk=lambda: seen.append("forget"),
                                  close_chunk_windows=lambda: seen.append("close"))
    tool._tick(state)
    assert seen == []                                       # same project, same chunk: nothing happens
    ms.app.document.path = "C:/b/two.psx"                   # File > Open: another project, chunk key unchanged
    tool._tick(state)
    assert seen == ["forget", "close"] and state.last_chunk_key == msapi.chunk_identity(chunk)


# -- Adjust (P1-6) --------------------------------------------------------------------------------------------------------

def _points(ms, names, enabled):
    out = []
    for name in names:
        marker = ms.Marker(name)
        marker.reference.enabled = enabled.get(name, True)
        out.append({"label": name, "marker": marker, "n": 5})
    return out


def test_adjust_apply_refuses_a_result_from_another_chunk(ms, monkeypatch):
    chunk = ms.Chunk([], [])
    ms.app.document.chunk = chunk
    points = _points(ms, ["A", "B"], {})
    monkeypatch.setattr(msapi, "marker_frame_points", lambda chk: (points, ""))
    stale = ("C:/old/one.psx", chunk.key)
    ms.app.document.path = "C:/new/two.psx"
    count, error = panel.FastGcpPanel._adjust_apply(types.SimpleNamespace(), ["A"], stale, {"A", "B"})
    assert count == 0 and error
    assert all(p["marker"].reference.enabled for p in points)           # nothing was unticked


def test_adjust_apply_leaves_the_markers_outside_the_analysis_alone(ms, monkeypatch):
    chunk = ms.Chunk([], [])
    ms.app.document.chunk = chunk
    points = _points(ms, ["A", "B", "FEW", "NEW"], {})
    monkeypatch.setattr(msapi, "marker_frame_points", lambda chk: (points, ""))
    context = msapi.chunk_identity(chunk)
    count, error = panel.FastGcpPanel._adjust_apply(types.SimpleNamespace(), ["A"], context, {"A", "B"})
    assert error == "" and count == 1
    state = {p["label"]: p["marker"].reference.enabled for p in points}
    assert state == {"A": True, "B": False, "FEW": True, "NEW": True}    # only analysed markers change


# -- Worker ---------------------------------------------------------------------------------------------------------------

def test_worker_runs_a_job_with_itself_and_reports_errors():
    ok = Worker(lambda worker: {"seen": worker.cancel})
    ok.start()
    ok.join()
    assert ok.finished and ok.result == {"seen": False}
    bad = Worker(lambda worker: 1 / 0)
    bad.start()
    bad.join()
    assert bad.finished and "ZeroDivisionError" in bad.result["error"]
    ok.abandon()
    assert ok.abandoned and ok.cancel


# -- consensus: duplicate labels, no point (items 21, 22) -------------------------------------------------------------------

def test_two_markers_with_the_same_label_are_judged_separately(ms, install, monkeypatch):
    V, C = ms.Vector, ms.Camera
    cams = []
    for k in range(6):
        angle = 2 * math.pi * k / 6
        cam = C(k + 1, V(30 * math.cos(angle), 30 * math.sin(angle), -40))
        cam.sensor.calibration = types.SimpleNamespace(f=1000.0)
        cam.photo = types.SimpleNamespace(path="r_{}.jpg".format(k + 1))
        cams.append(cam)
    first = ms.Marker("GCP1", position=V(0, 0, 0))
    second = ms.Marker("GCP1", position=V(15, 0, 0))              # merged project: same label, another point
    first.key, second.key = 1, 2
    chunk = ms.Chunk(cams, [first, second])
    ms.app.document.chunk = chunk

    def pixel_ray(camera, x, y):                                  # the hit's pixel back to a ray (cameras look along +Z)
        c = camera.center
        return (c.x, c.y, c.z), ((x - 2000.0) / 1000.0, (y - 1500.0) / 1000.0, 1.0)

    monkeypatch.setattr(msapi, "pixel_ray", pixel_ray)
    state = make_state(first)
    assert aigcp.start(state, "checker", "all") is True
    wait_done(state)
    assert len(state.aigcp_applied) == 12                          # grouped by label, one of the two points would be thrown away
    assert len([c for c in cams if msapi.get_projection(first, c)]) == 6
    assert len([c for c in cams if msapi.get_projection(second, c)]) == 6


def test_no_point_with_enough_rays_is_not_a_free_pass(ms, monkeypatch):
    parallel = [((float(k), 0.0, -40.0), (0.0, 0.0, 1.0)) for k in range(5)]
    monkeypatch.setattr(msapi, "pixel_ray", lambda camera, x, y: parallel[camera])
    monkeypatch.setattr(msapi, "chunk_scale", lambda chk: 1.0)
    group = [({"camera": k}, {"x": 0.0, "y": 0.0}) for k in range(5)]
    kept, dropped = aigcp._consistent(None, group)
    assert kept == [] and len(dropped) == 5
    # fewer than MIN_RAYS usable rays: still "cannot be judged", everything is kept
    kept, dropped = aigcp._consistent(None, group[:consensus.MIN_RAYS - 1])
    assert len(kept) == consensus.MIN_RAYS - 1 and dropped == []


# -- cap in "All markers" (23) and the runner process (24) ------------------------------------------------------------------

def test_the_photo_limit_is_shared_and_the_skipped_markers_are_named(ms, install, monkeypatch):
    V, C = ms.Vector, ms.Camera
    cams = []
    for k in range(6):
        cam = C(k + 1, V(k * 2.0, 0, -40))
        cam.sensor.calibration = types.SimpleNamespace(f=1000.0)
        cam.photo = types.SimpleNamespace(path="c_{}.jpg".format(k + 1))
        cams.append(cam)
    markers = [ms.Marker(name, position=V(3.0, 0, 0)) for name in ("A", "B", "C")]
    chunk = ms.Chunk(cams, markers)
    ms.app.document.chunk = chunk
    monkeypatch.setattr(aigcp, "MAX_PHOTOS_ALL", 5)
    monkeypatch.setattr(aigcp, "MAX_PHOTOS", 3)
    capped = []
    items, used, _ignored = aigcp.collect_all(chunk, capped=capped)
    assert len(items) == 5 and used == 2 and capped == ["C"]
    per_marker = {}
    for item in items:
        per_marker[item["label"]] = per_marker.get(item["label"], 0) + 1
    assert per_marker == {"A": 3, "B": 2}                          # the first marker did not take every slot


def test_a_runner_that_cannot_start_leaves_no_open_log(install, tmp_path, monkeypatch):
    job = aigcp.Job("checker", {"items": []}, str(tmp_path), python="/no/such/python")

    def boom(*args, **kwargs):
        raise FileNotFoundError("python")

    monkeypatch.setattr(aigcp.subprocess, "Popen", boom)
    with pytest.raises(FileNotFoundError):
        job.start()
    assert job._log is None


# -- writes are batched (19) and the review cost is linear (18) ------------------------------------------------------------

def _entries(ms, count):
    V, C = ms.Vector, ms.Camera
    marker = ms.Marker("GCP1", position=V(0, 0, 0))
    marker.key = 1
    cams = [C(k + 1, V(k, 0, -40)) for k in range(count)]
    for cam in cams:
        cam.photo = types.SimpleNamespace(path="x.jpg")
    chunk = ms.Chunk(cams, [marker])
    ms.app.document.chunk = chunk
    entries, applied = [], []
    for cam in cams:
        msapi.set_projection(marker, cam, 10.0, 11.0, pinned=False)
        msapi.mark_proposal(marker, cam)
        applied.append((marker, cam, None))
        entries.append({"marker": marker, "label": "GCP1", "camera": cam, "camera_label": cam.label, "image_path": "x.jpg",
                        "ai_x": 10.0, "ai_y": 11.0, "x": 10.0, "y": 11.0, "conf": 0.9, "crop_px": 100.0, "px_per_m": 50.0,
                        "previous": None, "big": None, "win": 0.0, "x0": 0.0, "y0": 0.0, "state": review.KEPT})
    return marker, cams, entries, applied


def test_accepting_many_proposals_refreshes_metashape_once(ms, monkeypatch):
    marker, cams, entries, applied = _entries(ms, 40)
    refreshes = []
    monkeypatch.setattr(msapi, "refresh", lambda: refreshes.append(1))
    monkeypatch.setattr(review, "record", lambda *args, **kwargs: None)
    state = types.SimpleNamespace(aigcp_applied=applied)
    assert review.accept_kept(state, review.Review("checker", "run", entries)) == 40
    assert len(refreshes) == 1
    assert state.aigcp_applied == []
    assert all(msapi.get_projection(marker, cam)[2] for cam in cams)


def test_accepting_proposals_reads_each_marker_and_photo_about_once(ms, monkeypatch):
    marker, cams, entries, applied = _entries(ms, 60)
    monkeypatch.setattr(review, "record", lambda *args, **kwargs: None)
    calls = {"label": 0, "key": 0}
    real_label, real_key = msapi.marker_label, msapi.camera_key

    def label(m):
        calls["label"] += 1
        return real_label(m)

    def key(c):
        calls["key"] += 1
        return real_key(c)

    monkeypatch.setattr(msapi, "marker_label", label)
    monkeypatch.setattr(msapi, "camera_key", key)
    state = types.SimpleNamespace(aigcp_applied=applied)
    review.accept_kept(state, review.Review("checker", "run", entries))
    assert calls["label"] < 60 * 6 and calls["key"] < 60 * 12       # was 60 x 60 x 2 on each


def test_set_projection_can_skip_the_refresh(ms, monkeypatch):
    marker, cams, _entries_, _applied = _entries(ms, 2)
    refreshes = []
    monkeypatch.setattr(msapi, "refresh", lambda: refreshes.append(1))
    msapi.set_projection(marker, cams[0], 1.0, 2.0, update=False)
    assert refreshes == []
    msapi.set_projection(marker, cams[0], 1.0, 2.0)
    assert refreshes == [1]


def test_placing_the_proposals_refreshes_metashape_once(ms, install, ring, monkeypatch):
    chunk, marker, cams = ring
    refreshes = []
    monkeypatch.setattr(msapi, "refresh", lambda: refreshes.append(1))
    monkeypatch.setattr(msapi, "pixel_ray", lambda camera, x, y: ((camera.center.x, camera.center.y, camera.center.z),
                                                                 (-camera.center.x, -camera.center.y, -camera.center.z)))
    state = make_state(marker)
    aigcp.start(state, "checker", "marker")
    wait_done(state)
    assert len(state.aigcp_applied) == 6
    assert len(refreshes) == 1


# -- review feedback (25) ---------------------------------------------------------------------------------------------------

def test_removing_a_proposal_the_operator_replaced_is_not_a_false_detection(ms, monkeypatch):
    marker, cams, entries, applied = _entries(ms, 2)
    state = types.SimpleNamespace(aigcp_applied=applied)
    given = review.Review("checker", "run", entries)
    sent = []
    monkeypatch.setattr(review, "record", lambda rev, entry, action, x=None, y=None: sent.append(action))
    msapi.set_projection(marker, cams[0], 50.0, 60.0, pinned=True)         # the operator pinned this photo by hand
    assert review.remove(state, given, entries[0]) is True
    assert entries[0]["state"] == review.REMOVED and sent == []              # the spot was kept: no "rejected"
    assert msapi.get_projection(marker, cams[0]) == (50.0, 60.0, True)       # and their pin is untouched
    assert review.remove(state, given, entries[1]) is True                   # an untouched proposal is a rejection
    assert sent == ["rejected"]


# -- poll signature (20) ----------------------------------------------------------------------------------------------------

def test_the_table_signature_changes_with_the_markers_and_costs_no_error(ms, monkeypatch):
    marker, cams, _entries_, _applied = _entries(ms, 3)
    chunk = ms.app.document.chunk
    monkeypatch.setattr(msapi, "marker_errors", lambda *a: pytest.fail("an error was computed for the signature"))
    first = msapi.markers_signature(chunk)
    assert msapi.markers_signature(chunk) == first
    msapi.remove_projection(marker, cams[0])
    second = msapi.markers_signature(chunk)
    assert second != first
    marker.position = ms.Vector(1.0, 2.0, 3.0)
    assert msapi.markers_signature(chunk) != second
    chunk.markers.append(ms.Marker("NEW"))
    assert msapi.markers_signature(chunk) != second
    assert msapi.markers_signature(None) is None


def test_poll_rows_rebuilds_only_when_something_changed(ms, monkeypatch):
    marker, cams, _entries_, _applied = _entries(ms, 3)
    monkeypatch.setattr(msapi, "marker_errors", lambda *a: pytest.fail("an error was computed by the poll"))
    rebuilt = []
    scrollbar = types.SimpleNamespace(value=lambda: 0, setValue=lambda v: None)
    fake = types.SimpleNamespace(_state=object(), _signature=None, poll_cost=0.0,
                                 grid=types.SimpleNamespace(verticalScrollBar=lambda: scrollbar),
                                 refresh_markers=lambda: rebuilt.append(1))
    poll = panel.FastGcpPanel.poll_rows
    poll(fake)
    poll(fake)
    assert rebuilt == []                                                   # first read, then unchanged
    msapi.remove_projection(marker, cams[0])
    poll(fake)
    assert rebuilt == [1]


# -- report ---------------------------------------------------------------------------------------------------------------

def test_the_report_has_no_dead_fallback_left():
    assert not hasattr(report, "_fallback_errors")


# -- review: restore and refine --------------------------------------------------------------------------------------------

def test_restore_puts_a_removed_proposal_back_and_refuses_a_pinned_photo(ms, monkeypatch):
    marker, cams, entries, applied = _entries(ms, 2)
    monkeypatch.setattr(review, "record", lambda *args, **kwargs: None)
    state = types.SimpleNamespace(aigcp_applied=[])
    given = review.Review("checker", "run", entries)
    assert review.remove(state, given, entries[0])
    assert msapi.get_projection(marker, cams[0]) is None
    assert review.restore(state, given, entries[0]) is True
    assert entries[0]["state"] == review.KEPT and msapi.get_projection(marker, cams[0]) == (10.0, 11.0, False)
    assert msapi.is_proposal(marker, cams[0]) and len(state.aigcp_applied) == 1
    assert review.remove(state, given, entries[1])
    msapi.set_projection(marker, cams[1], 5.0, 6.0, pinned=True)             # the operator pins that photo meanwhile
    assert review.restore(state, given, entries[1]) is False


def test_refine_replaces_the_proposal_by_a_pin_and_can_be_undone(ms, monkeypatch):
    from aerotools.tools.fastgcp import history
    marker, cams, entries, applied = _entries(ms, 1)
    monkeypatch.setattr(review, "record", lambda *args, **kwargs: None)
    history.clear()
    state = types.SimpleNamespace(aigcp_applied=applied)
    assert review.refine(state, review.Review("checker", "run", entries), entries[0], 12.5, 13.5) is True
    assert entries[0]["state"] == review.REFINED and msapi.get_projection(marker, cams[0]) == (12.5, 13.5, True)
    assert state.aigcp_applied == [] and not msapi.is_proposal(marker, cams[0])
    assert history.can_undo()
    history.clear()
