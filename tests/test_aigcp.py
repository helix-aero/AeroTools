import json
import math
import os
import sys
import time
import types

import pytest

from aerotools import config, i18n, msapi
from aerotools.tools.fastgcp import aigcp, aigcp_runner, history, review

STUB_OK = '''
import argparse, json
parser = argparse.ArgumentParser()
parser.add_argument("--work"); parser.add_argument("--out")
args = parser.parse_args()
work = json.load(open(args.work))
hits = [{"id": it["id"], "found": True, "x": it["x_est"] + 1.0, "y": it["y_est"] + 1.0,
         "conf": 0.9, "crop_px": 200} for it in work["items"]]
json.dump({"version": 1, "hits": hits, "errors": []}, open(args.out, "w"))
'''
STUB_FAR = STUB_OK.replace('it["x_est"] + 1.0', 'it["x_est"] + 40.0')
STUB_SLOW = STUB_OK.replace(
    "hits = [", "import os, time\n"
    "json.dump({'done': 2, 'total': 5}, open(os.path.join(os.path.dirname(args.out), 'progress.json'), 'w'))\n"
    "time.sleep(1.5)\nhits = [", 1)
STUB_CROPS = STUB_OK.replace(
    '"conf": 0.9, "crop_px": 200}',
    '"conf": 0.9, "crop_px": 200, "crop": {"big": "b_" + it["id"].replace(":", "_") + ".jpg", "win": 200, '
    '"x0": it["x_est"] + 1.0 - 100, "y0": it["y_est"] + 1.0 - 100}}')
STUB_FAIL = "import sys\nsys.stderr.write('boom')\nsys.exit(3)\n"


@pytest.fixture(autouse=True)
def _bind(ms):
    msapi.Metashape = ms
    msapi._PROPOSALS.clear()


@pytest.fixture
def world(ms):
    V, C = ms.Vector, ms.Camera
    cams = [C(1, V(0, 0, -10)), C(2, V(0, 0, -20)), C(3, V(0, 0, -40)), C(4, V(0, 0, -30))]
    for cam in cams:
        cam.sensor.calibration = types.SimpleNamespace(f=1000.0)
        cam.photo = types.SimpleNamespace(path="img_{}.jpg".format(cam.key))
    cams[3].photo = types.SimpleNamespace(path="")
    pinned = ms.Marker.Projection(V(2000, 1500, 0), True)
    proposal = ms.Marker.Projection(V(1990, 1490, 0), False)
    marker = ms.Marker("GCP1", position=V(0, 0, 0), projections={cams[0]: pinned, cams[1]: proposal})
    chunk = ms.Chunk(cams, [marker])
    ms.app.document.chunk = chunk
    return chunk, marker, cams


@pytest.fixture
def install(tmp_path, local_appdata, monkeypatch):
    """Poids, Python externe (celui des tests) et runner factice."""
    models = tmp_path / "models"
    models.mkdir()
    for kind in aigcp.TYPES:
        (models / (kind + ".pt")).write_bytes(b"poids")
    config.put(aigcp.SECTION, "models_dir", str(models))
    config.put(aigcp.SECTION, "python", sys.executable)
    runner = tmp_path / "stub_runner.py"
    runner.write_text(STUB_OK)
    monkeypatch.setattr(aigcp, "RUNNER", str(runner))
    return runner


class FakePanel(object):
    def __init__(self):
        self.statuses = []
        self.calls = []

    def show_ai_setup(self, text):
        self.setups = getattr(self, "setups", []) + [text]

    def open_review(self):
        self.calls.append("open_review")

    def close_review(self):
        self.calls.append("close_review")

    def set_status(self, text):
        self.statuses.append(text)

    def update_active_card(self):
        pass

    def refresh_errors(self):
        pass

    def refresh_rows(self, labels=None):
        self.refreshed = labels


def make_state(marker):
    state = types.SimpleNamespace(panel=FakePanel(), active_marker=marker,
                                  aigcp_run=None, aigcp_applied=[])
    state.active_label = lambda: marker.label
    # Les tests historiques repassent sur les points bleus et respectent le vert.
    aigcp.set_choice("ignore", "green")
    return state


def wait_done(state, seconds=20.0):
    end = time.time() + seconds
    while state.aigcp_run is not None and time.time() < end:
        aigcp.poll(state)
        time.sleep(0.05)
    assert state.aigcp_run is None


# -- msapi : projections posees, echelle, estimation -------------------------

def test_only_marked_proposals_are_left_out_of_the_count(world):
    _chunk, marker, cams = world
    # Point bleu calcule par Metashape (pas une proposition) : il compte, comme avant.
    assert msapi.projection_count(marker) == 2
    msapi.mark_proposal(marker, cams[1])
    assert msapi.projection_count(marker) == 1
    assert len(msapi.projection_cameras(marker)) == 2
    assert [c.key for c in msapi.projection_cameras(marker, without_proposals=True)] == [1]


def test_validated_proposal_is_no_longer_one(ms, world):
    _chunk, marker, cams = world
    msapi.mark_proposal(marker, cams[1])
    msapi.set_projection(marker, cams[1], 5.0, 6.0, pinned=True)
    assert msapi.is_proposal(marker, cams[1]) is False
    assert msapi.projection_count(marker) == 2


def test_proposal_memory_is_per_chunk(ms, world):
    chunk, marker, cams = world
    msapi.mark_proposal(marker, cams[1])
    chunk.key = 77
    assert msapi.is_proposal(marker, cams[1]) is False


def test_ground_scale_is_focal_over_distance(world):
    chunk, marker, cams = world
    assert msapi.marker_ground_scale(chunk, marker, cams[0]) == pytest.approx(100.0)
    chunk.transform.scale = 2.0
    assert msapi.marker_ground_scale(chunk, marker, cams[0]) == pytest.approx(50.0)


def test_ground_scale_none_without_calibration(world):
    chunk, marker, cams = world
    del cams[0].sensor.calibration
    assert msapi.marker_ground_scale(chunk, marker, cams[0]) is None


def test_estimate_ignores_existing_projection(world):
    chunk, marker, cams = world
    assert msapi.marker_estimate(chunk, marker, cams[0]) == (2000.0, 1500.0)
    assert msapi.marker_estimate(chunk, marker, cams[1]) == (2000.0, 1500.0)


def test_estimate_none_when_point_is_behind(ms, world):
    chunk, marker, _cams = world
    behind = ms.Camera(9, ms.Vector(0, 0, 10))
    assert msapi.marker_estimate(chunk, marker, behind) is None


# -- installation ------------------------------------------------------------

def test_check_reports_missing_python(local_appdata, tmp_path):
    config.put(aigcp.SECTION, "python", str(tmp_path / "absent.exe"))
    ok, message = aigcp.check("damier")
    assert not ok and "absent.exe" in message


def test_check_reports_missing_weights(local_appdata, tmp_path):
    config.put(aigcp.SECTION, "python", sys.executable)
    config.put(aigcp.SECTION, "models_dir", str(tmp_path))
    ok, message = aigcp.check("bombe")
    assert not ok and "bombe" in message


def test_check_ok_and_unknown_type(install):
    assert aigcp.check("damier") == (True, "")
    assert aigcp.check("bombe") == (True, "")
    assert aigcp.check("rond")[0] is False


def test_conf_defaults_and_override(local_appdata):
    assert aigcp.conf_for("damier") == 0.25
    assert aigcp.conf_for("bombe") == 0.30
    config.put(aigcp.SECTION, "conf_bombe", 0.5)
    assert aigcp.conf_for("bombe") == 0.5
    config.put(aigcp.SECTION, "conf_bombe", 7)
    assert aigcp.conf_for("bombe") == 0.30


def test_child_env_drops_metashape_python_variables(monkeypatch):
    monkeypatch.setenv("PYTHONHOME", "x")
    monkeypatch.setenv("PYTHONPATH", "y")
    env = aigcp.child_env()
    assert "PYTHONHOME" not in env and "PYTHONPATH" not in env


# -- contenu du travail ------------------------------------------------------

def test_collect_items_skips_pinned_and_unusable_photos(install, world):
    chunk, marker, cams = world
    items, skipped = aigcp.collect_items(chunk, marker, skip_blue=False)
    ids = [it["id"] for it in items]
    assert "0:1" not in ids                 # pointee a la main
    assert "0:2" in ids and "0:3" in ids    # proposition IA : a retester ; photo jamais pointee
    assert "0:4" not in ids and skipped == 1  # pas de chemin d'image
    by_id = {it["id"]: it for it in items}
    assert by_id["0:3"]["px_per_m"] == pytest.approx(25.0)
    assert (by_id["0:3"]["x_est"], by_id["0:3"]["y_est"]) == (2000.0, 1500.0)
    assert by_id["0:3"]["label"] == "GCP1" and by_id["0:3"]["marker"] is marker


def test_build_work_has_weights_conf_and_items(install, world):
    chunk, marker, _cams = world
    items, _ = aigcp.collect_items(chunk, marker, skip_blue=False)
    work = aigcp.build_work("damier", items)
    assert work["weights"].endswith("damier.pt") and work["conf"] == 0.25
    assert work["scales_m"] == [0.3, 0.5, 0.8]
    assert {"id", "image_path", "x_est", "y_est", "px_per_m", "rescue"} == set(work["items"][0])
    json.dumps(work)


def test_accept_rules():
    item = {"x_est": 100.0, "y_est": 100.0}
    good = {"found": True, "x": 110.0, "y": 100.0, "conf": 0.8, "crop_px": 200}
    assert aigcp.accept(good, item, 0.25)
    assert not aigcp.accept(dict(good, found=False), item, 0.25)
    assert not aigcp.accept(dict(good, conf=0.2), item, 0.25)
    assert not aigcp.accept(dict(good, x=400.0), item, 0.25)     # trop loin de l'estimation
    assert not aigcp.accept({"found": True}, item, 0.25)


# -- calcul en arriere-plan --------------------------------------------------

def test_job_returns_hits(install, tmp_path):
    work = {"items": [{"id": "7", "x_est": 10.0, "y_est": 20.0}]}
    job = aigcp.Job("damier", work, str(tmp_path)).start()
    hits, errors = job.wait()
    assert errors == [] and hits[0]["id"] == "7" and hits[0]["x"] == 11.0


def test_job_failure_carries_runner_output(install, tmp_path):
    install.write_text(STUB_FAIL)
    job = aigcp.Job("damier", {"items": []}, str(tmp_path)).start()
    with pytest.raises(RuntimeError) as info:
        job.wait()
    assert "boom" in str(info.value)


def test_start_poll_places_unpinned_proposals_only(install, world):
    chunk, marker, cams = world
    state = make_state(marker)
    assert aigcp.start(state, "damier") is True
    assert aigcp.busy(state)
    wait_done(state)
    first = msapi.get_projection(marker, cams[0])
    assert first == (2000.0, 1500.0, True)                       # pointage manuel intact
    for cam in (cams[1], cams[2]):
        x, y, pinned = msapi.get_projection(marker, cam)
        assert (x, y, pinned) == (2001.0, 1501.0, False)
    assert msapi.get_projection(marker, cams[3]) is None          # photo sans chemin
    assert msapi.projection_count(marker) == 1
    assert msapi.is_proposal(marker, cams[1]) and msapi.is_proposal(marker, cams[2])
    assert "2 proposition(s)" in state.panel.statuses[-1]
    assert len(state.aigcp_applied) == 2


def test_second_start_while_running_is_refused(install, world):
    _chunk, marker, _cams = world
    state = make_state(marker)
    assert aigcp.start(state, "damier") is True
    assert aigcp.start(state, "bombe") is False
    wait_done(state)


def test_start_without_install_says_why(world, local_appdata, tmp_path):
    _chunk, marker, _cams = world
    config.put(aigcp.SECTION, "python", str(tmp_path / "absent.exe"))
    state = make_state(marker)
    assert aigcp.start(state, "damier") is False
    assert "absent.exe" in state.panel.statuses[-1]


def test_start_without_marker(install, world):
    state = make_state(None)
    state.active_label = lambda: None
    assert aigcp.start(state, "damier") is False
    assert "marqueur" in state.panel.statuses[-1]


def test_runner_failure_is_reported_not_raised(install, world):
    install.write_text(STUB_FAIL)
    _chunk, marker, _cams = world
    state = make_state(marker)
    aigcp.start(state, "damier")
    wait_done(state)
    assert "echoue" in state.panel.statuses[-1] or "échoué" in state.panel.statuses[-1]
    assert state.aigcp_applied == []


def test_pin_made_while_running_is_never_overwritten(install, world, ms):
    _chunk, marker, cams = world
    state = make_state(marker)
    aigcp.start(state, "damier")
    msapi.set_projection(marker, cams[2], 5.0, 6.0, pinned=True)   # l'operateur pointe la photo 3
    wait_done(state)
    assert msapi.get_projection(marker, cams[2]) == (5.0, 6.0, True)


def test_chunk_change_discards_results(install, world, ms):
    chunk, marker, cams = world
    state = make_state(marker)
    aigcp.start(state, "damier")
    ms.app.document.chunk = ms.Chunk()
    ms.app.document.chunk.key = 99
    wait_done(state)
    assert msapi.get_projection(marker, cams[2]) is None
    assert state.aigcp_applied == []


def test_clear_removes_only_unvalidated_proposals(install, world):
    chunk, marker, cams = world
    state = make_state(marker)
    aigcp.start(state, "damier")
    wait_done(state)
    msapi.set_projection(marker, cams[2], 7.0, 8.0, pinned=True)   # validee par l'operateur
    assert aigcp.clear(state) == 1
    assert msapi.get_projection(marker, cams[1]) == (1990.0, 1490.0, False)   # point bleu d'avant rendu
    assert msapi.get_projection(marker, cams[2]) == (7.0, 8.0, True)
    assert msapi.get_projection(marker, cams[0]) == (2000.0, 1500.0, True)
    assert state.aigcp_applied == []


def test_clear_gives_back_the_previous_blue_point(install, world):
    chunk, marker, cams = world
    state = make_state(marker)
    aigcp.start(state, "damier")
    wait_done(state)
    assert msapi.get_projection(marker, cams[1]) == (2001.0, 1501.0, False)
    aigcp.clear(state)
    assert msapi.get_projection(marker, cams[1]) == (1990.0, 1490.0, False)
    assert msapi.is_proposal(marker, cams[1]) is False


def test_cancel_stops_a_running_job(install, world):
    _chunk, marker, _cams = world
    state = make_state(marker)
    aigcp.start(state, "damier")
    aigcp.cancel(state)
    assert state.aigcp_run is None


def test_detector_contract(install, world):
    chunk, marker, cams = world
    detector = aigcp.AigcpDetector("bombe")
    assert detector.status() == (True, "")
    proposals = detector.propose(chunk, marker, [cams[2]])
    assert len(proposals) == 1
    assert proposals[0].camera_key == 3 and proposals[0].source == "aigcp-bombe"
    assert (proposals[0].x, proposals[0].y) == (2001.0, 1501.0)


# -- executeur : parties sans numpy ni torch ---------------------------------

def test_crop_size_follows_target_scale_and_is_bounded():
    assert aigcp_runner.crop_size(200.0, 0.5) == 400
    assert aigcp_runner.crop_size(10.0, 0.3) == aigcp_runner.CROP_MIN
    assert aigcp_runner.crop_size(5000.0, 0.8) == aigcp_runner.CROP_MAX


# -- garde-fou geometrique ---------------------------------------------------

@pytest.fixture
def confirmed(ms, world):
    """Marqueur a deux pointages manuels qui retombent sur la reprojection de son point 3D."""
    chunk, marker, cams = world
    marker.projections[cams[0]] = ms.Marker.Projection(ms.Vector(2001.0, 1500.0, 0), True)
    marker.projections[cams[3]] = ms.Marker.Projection(ms.Vector(2000.0, 1500.0, 0), True)
    msapi.mark_proposal(marker, cams[1])      # reste d'une detection precedente : ne compte pas
    return chunk, marker, cams


def test_spread_unknown_with_a_single_projection(ms, world):
    chunk, _marker, cams = world
    single = ms.Marker("SOLO", position=ms.Vector(0, 0, 0),
                       projections={cams[0]: ms.Marker.Projection(ms.Vector(2000.0, 1500.0, 0), True)})
    assert msapi.marker_spread_m(chunk, single) is None
    items, _ = aigcp.collect_items(chunk, single)
    assert items and all(it["gate_m"] is None and it["rescue"] is True for it in items)


def test_projections_computed_by_metashape_count_for_the_spread(ms, world):
    chunk, _marker, cams = world
    blue = ms.Marker("BLEU", position=ms.Vector(0, 0, 0), projections={
        cams[0]: ms.Marker.Projection(ms.Vector(2000.0, 1500.0, 0), False),
        cams[1]: ms.Marker.Projection(ms.Vector(2000.0, 1500.0, 0), False)})
    assert msapi.marker_spread_m(chunk, blue) == pytest.approx(0.0)


def test_ai_proposals_never_count_for_the_spread(ms, world):
    chunk, marker, cams = world
    solo = ms.Marker("SOLO", position=ms.Vector(0, 0, 0), projections={
        cams[0]: ms.Marker.Projection(ms.Vector(2000.0, 1500.0, 0), True),
        cams[1]: ms.Marker.Projection(ms.Vector(1500.0, 1000.0, 0), False)})
    chunk.markers.append(solo)
    msapi.mark_proposal(solo, cams[1])
    assert msapi.marker_spread_m(chunk, solo) is None


def test_spread_is_the_worst_pin_gap_in_metres(confirmed):
    chunk, marker, _cams = confirmed
    assert msapi.marker_spread_m(chunk, marker) == pytest.approx(0.01)   # 1 px a 100 px/m


def test_gate_has_a_floor_and_cuts_the_wide_search(install, confirmed):
    chunk, marker, _cams = confirmed
    items, _ = aigcp.collect_items(chunk, marker)
    assert items and all(it["gate_m"] == pytest.approx(0.30) and it["rescue"] is False for it in items)


def test_gate_grows_with_loose_pins(ms, install, confirmed):
    chunk, marker, cams = confirmed
    marker.projections[cams[3]] = ms.Marker.Projection(ms.Vector(2000.0, 1600.0, 0), True)
    # cam 4 : 33,3 px/m, 100 px d'ecart = 3 m -> garde-fou 5 x 3 m
    assert aigcp.marker_gate_m(chunk, marker) == pytest.approx(15.0, rel=0.05)


def test_hit_far_from_the_expected_position_is_rejected_by_the_gate():
    item = {"x_est": 100.0, "y_est": 100.0, "px_per_m": 25.0, "gate_m": 0.30}
    near = {"found": True, "x": 103.0, "y": 100.0, "conf": 0.9, "crop_px": 400}
    far = {"found": True, "x": 140.0, "y": 100.0, "conf": 0.9, "crop_px": 400}
    assert aigcp.check_hit(near, item, 0.25) == (True, "")
    assert aigcp.check_hit(far, item, 0.25) == (False, "gate")
    assert aigcp.check_hit(far, dict(item, gate_m=None), 0.25) == (True, "")
    assert aigcp.check_hit(dict(far, conf=0.1), item, 0.25) == (False, "conf")
    assert aigcp.check_hit(dict(far, found=False), item, 0.25) == (False, "absent")
    assert aigcp.check_hit(dict(far, x=900.0), dict(item, gate_m=None), 0.25) == (False, "far")


def test_gated_detections_are_not_placed_and_are_counted(install, confirmed):
    install.write_text(STUB_FAR)
    chunk, marker, cams = confirmed
    state = make_state(marker)
    aigcp.start(state, "damier", "marker")
    wait_done(state)
    assert msapi.get_projection(marker, cams[2]) is None
    assert state.aigcp_applied == []
    assert "garde-fou" in state.panel.statuses[-1]


# -- portee : tous les marqueurs ---------------------------------------------

def test_collect_all_skips_only_markers_without_a_3d_position(ms, install, confirmed):
    chunk, first, cams = confirmed
    chunk.markers.append(ms.Marker("NOPOS"))                     # ni position ni coordonnees
    chunk.markers.append(ms.Marker("GCP2", position=ms.Vector(0, 0, 0)))   # aucun pointage, mais une position
    items, used, ignored = aigcp.collect_all(chunk)
    assert used == 2 and ignored == 1
    labels = {it["label"] for it in items}
    assert labels == {"GCP1", "GCP2"}
    assert [it["image_path"] for it in items] == sorted(it["image_path"] for it in items)
    unpinned = [it for it in items if it["label"] == "GCP2"]
    assert unpinned and all(it["gate_m"] is None and it["rescue"] is True for it in unpinned)


def test_scope_all_places_proposals_for_every_confirmed_marker(ms, install, confirmed):
    chunk, first, cams = confirmed
    second = ms.Marker("GCP2", position=ms.Vector(0, 0, 0), projections={
        cams[0]: ms.Marker.Projection(ms.Vector(2000.0, 1500.0, 0), True),
        cams[3]: ms.Marker.Projection(ms.Vector(2000.0, 1500.0, 0), True)})
    chunk.markers.append(second)
    state = make_state(first)
    assert aigcp.start(state, "damier", "all") is True
    wait_done(state)
    assert msapi.get_projection(first, cams[2])[2] is False
    assert msapi.get_projection(second, cams[2])[2] is False
    assert "2 marqueur(s)" in state.panel.statuses[-1]
    assert len(state.aigcp_applied) == 4


def test_scope_all_without_any_marker_position_says_so(install, ms):
    marker = ms.Marker("SANS")
    ms.app.document.chunk = ms.Chunk([], [marker])
    state = make_state(marker)
    assert aigcp.start(state, "damier", "all") is False
    assert "position 3D" in state.panel.statuses[-1]


def _three_markers(ms, first, cams):
    """Le marqueur confirme + GCP2 et GCP3 (memes pointages) : de quoi choisir parmi plusieurs."""
    others = []
    for name in ("GCP2", "GCP3"):
        marker = ms.Marker(name, position=ms.Vector(0, 0, 0), projections={
            cams[0]: ms.Marker.Projection(ms.Vector(2000.0, 1500.0, 0), True),
            cams[3]: ms.Marker.Projection(ms.Vector(2000.0, 1500.0, 0), True)})
        others.append(marker)
    return others


def test_collect_all_can_be_limited_to_some_labels(ms, install, confirmed):
    chunk, first, cams = confirmed
    for marker in _three_markers(ms, first, cams):
        chunk.markers.append(marker)
    items, used, _ignored = aigcp.collect_all(chunk, labels=["GCP1", "GCP3"])
    assert used == 2 and {it["label"] for it in items} == {"GCP1", "GCP3"}


def test_several_selected_markers_run_together_in_marker_scope(ms, install, confirmed):
    chunk, first, cams = confirmed
    second, third = _three_markers(ms, first, cams)
    chunk.markers.append(second)
    chunk.markers.append(third)
    state = make_state(first)
    assert aigcp.start(state, "damier", "marker", labels=["GCP1", "GCP3"]) is True
    wait_done(state)
    assert msapi.get_projection(first, cams[2]) is not None
    assert msapi.get_projection(third, cams[2]) is not None
    assert msapi.get_projection(second, cams[2]) is None          # non selectionne : intact
    assert "2 marqueur(s)" in state.panel.statuses[-1]


def test_one_selected_marker_keeps_the_active_marker_behaviour(ms, install, confirmed):
    chunk, first, cams = confirmed
    second, _third = _three_markers(ms, first, cams)
    chunk.markers.append(second)
    state = make_state(first)
    assert aigcp.start(state, "damier", "marker", labels=["GCP2"]) is True      # un seul : on garde l'actif
    wait_done(state)
    assert msapi.get_projection(first, cams[2]) is not None
    assert msapi.get_projection(second, cams[2]) is None


# -- progression, suppression, rafraichissement ------------------------------

def test_accept_kept_reports_its_progress(reviewed):
    state, rev, _marker, _cams = reviewed
    calls = []
    done = review.accept_kept(state, rev, lambda d, t: calls.append((d, t)))
    assert done == 6 and calls == [(i, 6) for i in range(6)]


def test_remove_all_projections_empties_the_marker_but_keeps_it(ms, confirmed):
    chunk, marker, cams = confirmed
    before = len(msapi.projection_cameras(marker))
    assert before >= 3                       # deux pointages manuels et une proposition
    assert msapi.remove_all_projections(marker) == before
    assert msapi.projection_cameras(marker) == [] and marker in chunk.markers


def test_remove_marker_takes_it_out_of_the_chunk(ms, confirmed):
    chunk, marker, cams = confirmed
    other = ms.Marker("AUTRE")
    chunk.markers.append(other)
    assert msapi.remove_marker(chunk, marker) == (True, "")
    assert chunk.markers == [other]


def test_forget_markers_drops_their_proposals_log_and_review(reviewed):
    state, rev, marker, _cams = reviewed
    state.aigcp_log = list(rev.entries)
    aigcp.forget_markers(state, ["AUTRE"])
    assert len(state.aigcp_applied) == 6 and len(state.aigcp_log) == 6 and state.aigcp_review is rev
    aigcp.forget_markers(state, ["RING"])
    assert state.aigcp_applied == [] and state.aigcp_log == []
    assert state.aigcp_review is None and "close_review" in state.panel.calls


# -- choix memorises ---------------------------------------------------------

def test_choices_default_and_persist(local_appdata):
    assert aigcp.selected_type() == "damier" and aigcp.selected_scope() == "marker"
    aigcp.set_choice("type", "bombe")
    aigcp.set_choice("scope", "all")
    assert aigcp.selected_type() == "bombe" and aigcp.selected_scope() == "all"
    aigcp.set_choice("type", "rond")
    aigcp.set_choice("scope", "nulle part")
    assert aigcp.selected_type() == "bombe" and aigcp.selected_scope() == "all"


def test_start_without_arguments_uses_the_saved_choices(install, confirmed):
    chunk, marker, cams = confirmed
    aigcp.set_choice("type", "bombe")
    state = make_state(marker)
    assert aigcp.start(state) is True
    assert state.aigcp_run.kind == "bombe" and state.aigcp_run.scope == "marker"
    wait_done(state)


# -- photos deja marquees : cases vert / bleu --------------------------------

def test_ignore_choice_defaults_to_both_and_maps_to_the_flags(local_appdata):
    assert aigcp.selected_ignore() == "both" and aigcp.skip_flags() == (True, True)
    for choice, flags in (("green", (True, False)), ("blue", (False, True)),
                          ("none", (False, False)), ("both", (True, True))):
        aigcp.set_choice("ignore", choice)
        assert aigcp.skip_flags() == flags
    aigcp.set_choice("ignore", "tout")
    assert aigcp.selected_ignore() == "both"


def test_blue_points_of_metashape_are_ignored_unless_unchecked(install, world):
    chunk, marker, cams = world            # cams[1] : point bleu de Metashape, pas une proposition
    ids = lambda **flags: [it["id"] for it in aigcp.collect_items(chunk, marker, **flags)[0]]
    assert "0:2" not in ids()
    assert "0:2" in ids(skip_blue=False)


def test_previous_ai_proposals_are_always_retried(install, world):
    chunk, marker, cams = world
    msapi.mark_proposal(marker, cams[1])
    items, _ = aigcp.collect_items(chunk, marker)
    assert "0:2" in [it["id"] for it in items]


def test_green_points_are_ignored_unless_unchecked(install, world):
    chunk, marker, cams = world            # cams[0] : pointage manuel
    ids = lambda **flags: [it["id"] for it in aigcp.collect_items(chunk, marker, **flags)[0]]
    assert "0:1" not in ids()
    assert "0:1" in ids(skip_green=False)


def test_unchecking_green_lets_the_ai_go_over_a_manual_pin_and_clear_restores_it(install, world):
    chunk, marker, cams = world
    state = make_state(marker)
    aigcp.set_choice("ignore", "none")
    aigcp.start(state, "damier", "marker")
    wait_done(state)
    assert msapi.get_projection(marker, cams[0]) == (2001.0, 1501.0, False)
    assert msapi.is_proposal(marker, cams[0])
    aigcp.clear(state)
    assert msapi.get_projection(marker, cams[0]) == (2000.0, 1500.0, True)
    assert msapi.projection_count(marker) == 2


def test_blue_points_stay_untouched_when_checked(install, world):
    chunk, marker, cams = world
    state = make_state(marker)
    aigcp.set_choice("ignore", "both")
    aigcp.start(state, "damier", "marker")
    wait_done(state)
    assert msapi.get_projection(marker, cams[1]) == (1990.0, 1490.0, False)   # intact
    assert msapi.get_projection(marker, cams[2])[2] is False                   # photo vierge : proposition


# -- coherence entre photos ----------------------------------------------------

@pytest.fixture
def ring(ms):
    """Six photos en couronne autour d'un marqueur sans aucune projection."""
    V, C = ms.Vector, ms.Camera
    cams = []
    for k in range(6):
        angle = 2 * math.pi * k / 6
        cam = C(k + 1, V(30 * math.cos(angle), 30 * math.sin(angle), -40))
        cam.sensor.calibration = types.SimpleNamespace(f=1000.0)
        cam.photo = types.SimpleNamespace(path="r_{}.jpg".format(k + 1))
        cams.append(cam)
    marker = ms.Marker("RING", position=V(0, 0, 0))
    chunk = ms.Chunk(cams, [marker])
    ms.app.document.chunk = chunk
    return chunk, marker, cams


def rays_to(monkeypatch, shifts):
    """Les detections visent (0, 0, 0) sauf pour les photos de `shifts` : {cle: cible}."""
    def fake(camera, x, y):
        c = camera.center
        t = shifts.get(camera.key, (0.0, 0.0, 0.0))
        return (c.x, c.y, c.z), (t[0] - c.x, t[1] - c.y, t[2] - c.z)
    monkeypatch.setattr(msapi, "pixel_ray", fake)


def test_detections_off_the_common_point_are_dropped(install, ring, monkeypatch):
    chunk, marker, cams = ring
    rays_to(monkeypatch, {2: (6.0, 0.0, 0.0), 5: (0.0, 7.0, 0.0)})
    state = make_state(marker)
    aigcp.start(state, "damier", "marker")
    wait_done(state)
    assert [msapi.get_projection(marker, c) is not None for c in cams] == [True, False, True, True, False, True]
    assert len(state.aigcp_applied) == 4
    assert "2 écartée" in state.panel.statuses[-1] or "2 ecartee" in state.panel.statuses[-1]


def test_no_common_point_rejects_every_detection(install, ring, monkeypatch):
    chunk, marker, cams = ring
    rays_to(monkeypatch, {1: (6.0, 0.0, 0.0), 2: (0.0, 6.0, 0.0), 3: (-6.0, 0.0, 0.0),
                          4: (0.0, -6.0, 0.0), 5: (0.0, 0.0, 6.0), 6: (3.0, 3.0, 3.0)})
    state = make_state(marker)
    aigcp.start(state, "damier", "marker")
    wait_done(state)
    assert all(msapi.get_projection(marker, c) is None for c in cams)
    assert state.aigcp_applied == []
    assert "garde-fou" in state.panel.statuses[-1]


def test_two_detections_cannot_be_judged_and_are_kept(install, ring, monkeypatch):
    chunk, marker, cams = ring
    chunk.cameras = cams[:2]
    rays_to(monkeypatch, {2: (6.0, 0.0, 0.0)})
    state = make_state(marker)
    aigcp.start(state, "damier", "marker")
    wait_done(state)
    assert len(state.aigcp_applied) == 2


def test_missing_rays_never_block_the_detections(install, ring, monkeypatch):
    chunk, marker, cams = ring
    monkeypatch.setattr(msapi, "pixel_ray", lambda camera, x, y: None)
    state = make_state(marker)
    aigcp.start(state, "damier", "marker")
    wait_done(state)
    assert len(state.aigcp_applied) == 6


def test_consensus_tolerance_is_a_setting(install, ring, monkeypatch):
    chunk, marker, cams = ring
    config.put(aigcp.SECTION, "consensus_m", 50.0)
    rays_to(monkeypatch, {2: (6.0, 0.0, 0.0)})
    state = make_state(marker)
    aigcp.start(state, "damier", "marker")
    wait_done(state)
    assert len(state.aigcp_applied) == 6


def test_pixel_ray_goes_from_the_camera_to_the_pixel(ms):
    camera = types.SimpleNamespace(
        center=ms.Vector(1.0, 2.0, 3.0),
        sensor=types.SimpleNamespace(calibration=types.SimpleNamespace(
            unproject=lambda point: ms.Vector((point.x - 2000.0) / 1000.0, (point.y - 1500.0) / 1000.0, 1.0))),
        transform=types.SimpleNamespace(mulv=lambda v: v))
    origin, direction = msapi.pixel_ray(camera, 3000.0, 1500.0)
    assert origin == (1.0, 2.0, 3.0) and direction == (1.0, 0.0, 1.0)
    assert msapi.pixel_ray(ms.Camera(1, ms.Vector(0, 0, 0)), 10.0, 10.0) is None


# -- progression -----------------------------------------------------------------

def test_job_progress_is_none_until_the_runner_reports(install, tmp_path):
    job = aigcp.Job("damier", {"items": []}, str(tmp_path))
    assert job.progress() is None
    (tmp_path / "progress.json").write_text(json.dumps({"done": 3, "total": 9}))
    assert job.progress() == (3, 9)
    (tmp_path / "progress.json").write_text("pas du json")
    assert job.progress() is None


def test_progress_text_goes_in_the_bar_in_english_while_running(install, world):
    install.write_text(STUB_SLOW)
    _chunk, marker, _cams = world
    state = make_state(marker)
    aigcp.start(state, "damier", "marker")
    assert aigcp.progress_of(state) is None            # le runner n'a encore rien dit
    assert aigcp.bar_text(state) == "AI (Checker) · starting…"
    end = time.time() + 15
    while aigcp.progress_of(state) is None and time.time() < end:
        aigcp.poll(state)
        time.sleep(0.05)
    assert aigcp.progress_of(state) == (2, 5)
    assert aigcp.bar_text(state) == "AI (Checker) · 2/5 photos"
    assert not any("2/5" in text for text in state.panel.statuses)    # plus de doublon en bas du panneau
    wait_done(state)
    assert aigcp.progress_of(state) is None
    assert aigcp.bar_text(state) == ""


def test_abort_stops_the_run_and_says_so(install, world):
    install.write_text(STUB_SLOW)
    _chunk, marker, _cams = world
    state = make_state(marker)
    assert aigcp.abort(state) is False                  # rien ne tourne
    aigcp.start(state, "damier", "marker")
    assert aigcp.busy(state)
    assert aigcp.abort(state) is True
    assert not aigcp.busy(state)
    assert i18n.t("aigcp.cancelled") in state.panel.statuses
    assert aigcp.kind_label("bombe") == "Spray" and aigcp.kind_label("damier") == "Checker"


# -- revue des pointes de l'IA -------------------------------------------------------

def feedback_rows():
    path = review.feedback_path()
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


@pytest.fixture
def reviewed(install, ring):
    """Une detection terminee sur la couronne : six propositions a revoir."""
    install.write_text(STUB_CROPS)
    chunk, marker, cams = ring
    state = make_state(marker)
    aigcp.start(state, "damier", "marker")
    wait_done(state)
    return state, state.aigcp_review, marker, cams


def test_a_finished_detection_opens_the_review(reviewed):
    state, rev, _marker, _cams = reviewed
    assert len(rev.entries) == 6 and rev.counts()[review.KEPT] == 6
    assert rev.labels() == ["RING"] and len(rev.entries_for("RING")) == 6
    assert state.panel.calls[-1] == "open_review"
    entry = rev.entries[0]
    assert entry["big"].endswith(".jpg") and review.has_crop(entry)
    assert os.path.basename(os.path.dirname(entry["big"])) == "crops"


def test_thumbnail_and_photo_coordinates_agree():
    entry = {"win": 100.0, "x0": 950.0, "y0": 1450.0, "ai_x": 1000.0, "ai_y": 1500.0}
    assert review.to_view(entry, 1000.0, 1500.0, 320) == pytest.approx((160.0, 160.0))
    u, v = review.to_view(entry, 1010.0, 1490.0, 640)
    assert review.to_source(entry, u, v, 640) == pytest.approx((1010.0, 1490.0))


def test_zoom_shows_less_or_more_of_the_photo_around_the_point():
    entry = {"win": 200.0, "x0": 900.0, "y0": 1400.0, "ai_x": 1000.0, "ai_y": 1500.0}
    assert review.view_rect(entry, 1.0)[2] == pytest.approx(100.0)       # moitie de la fenetre
    assert review.view_rect(entry, 0.5)[2] == pytest.approx(200.0)       # toute la fenetre
    assert review.view_rect(entry, 4.0)[2] == pytest.approx(25.0)
    for zoom in (0.5, 1.0, 2.0, 4.0):
        assert review.to_view(entry, 1000.0, 1500.0, 300, zoom) == pytest.approx((150.0, 150.0))
        u, v = review.to_view(entry, 1012.0, 1491.0, 300, zoom)
        assert review.to_source(entry, u, v, 300, zoom) == pytest.approx((1012.0, 1491.0))
    # Le meme carre, dans l'image recadree enregistree de 960 px pour une fenetre de 200 px.
    left, top, side = review.source_rect(entry, 1.0, 960)
    assert (left, top, side) == pytest.approx((240.0, 240.0, 480.0))
    assert review.source_rect(entry, 0.5, 960) == pytest.approx((0.0, 0.0, 960.0))


def test_an_entry_without_thumbnail_cannot_be_refined(install, ring):
    chunk, marker, cams = ring
    state = make_state(marker)
    aigcp.start(state, "damier", "marker")           # le runner factice n'ecrit aucune vignette
    wait_done(state)
    assert state.aigcp_review.entries and not review.has_crop(state.aigcp_review.entries[0])


def test_remove_gives_back_the_previous_point_and_logs_a_negative(reviewed):
    state, rev, marker, cams = reviewed
    entry = rev.entries[2]
    assert review.remove(state, rev, entry) is True
    assert entry["state"] == review.REMOVED
    assert msapi.get_projection(marker, entry["camera"]) is None
    assert msapi.is_proposal(marker, entry["camera"]) is False
    assert all(t[1] is not entry["camera"] for t in state.aigcp_applied)
    assert entry in state.aigcp_log and entry["state"] == review.REMOVED
    assert review.remove(state, rev, entry) is False                 # deja retire
    row = feedback_rows()[-1]
    assert row["action"] == "rejected" and row["marker"] == "RING" and row["type"] == "damier"
    assert row["ai_x"] == pytest.approx(entry["ai_x"], abs=0.01) and row["run"] == rev.run_id
    assert row["image_path"] == entry["image_path"] and "x" not in row


def test_restore_puts_the_proposal_back(reviewed):
    state, rev, marker, cams = reviewed
    entry = rev.entries[0]
    review.remove(state, rev, entry)
    assert review.restore(state, rev, entry) is True
    assert entry["state"] == review.KEPT and msapi.is_proposal(marker, entry["camera"])
    assert msapi.get_projection(marker, entry["camera"])[:2] == pytest.approx((entry["ai_x"], entry["ai_y"]))
    assert feedback_rows()[-1]["action"] == "restored"


def test_restore_refuses_when_the_operator_pinned_the_photo(reviewed):
    state, rev, marker, cams = reviewed
    entry = rev.entries[0]
    review.remove(state, rev, entry)
    msapi.set_projection(marker, entry["camera"], 5.0, 6.0, pinned=True)
    assert review.restore(state, rev, entry) is False
    assert msapi.get_projection(marker, entry["camera"]) == (5.0, 6.0, True)


def test_remove_leaves_a_photo_the_operator_pinned_in_the_meantime(reviewed):
    state, rev, marker, cams = reviewed
    entry = rev.entries[1]
    msapi.set_projection(marker, entry["camera"], 7.0, 8.0, pinned=True)
    review.remove(state, rev, entry)
    assert msapi.get_projection(marker, entry["camera"]) == (7.0, 8.0, True)


def test_refine_pins_the_corrected_point_and_keeps_the_correction(reviewed):
    state, rev, marker, cams = reviewed
    entry = rev.entries[3]
    assert review.refine(state, rev, entry, 1234.5, 987.25) is True
    assert msapi.get_projection(marker, entry["camera"]) == (1234.5, 987.25, True)
    assert entry["state"] == review.REFINED and not msapi.is_proposal(marker, entry["camera"])
    assert history.can_undo()
    row = feedback_rows()[-1]
    assert row["action"] == "refined" and (row["x"], row["y"]) == (1234.5, 987.25)
    assert row["ai_x"] == pytest.approx(entry["ai_x"], abs=0.01)
    msapi.remove_projection(marker, entry["camera"])
    history.clear()


def test_a_removed_proposal_can_still_be_refined(reviewed):
    state, rev, marker, cams = reviewed
    entry = rev.entries[4]
    review.remove(state, rev, entry)
    assert review.refine(state, rev, entry, 10.0, 20.0) is True
    assert msapi.get_projection(marker, entry["camera"]) == (10.0, 20.0, True)
    history.clear()


def test_a_refined_or_validated_entry_can_be_refined_again(reviewed):
    state, rev, marker, cams = reviewed
    entry = rev.entries[2]
    assert review.refine(state, rev, entry, 10.0, 20.0) is True
    assert review.refine(state, rev, entry, 11.0, 21.0) is True            # deja affinee : on la reprend
    assert entry["state"] == review.REFINED and (entry["x"], entry["y"]) == (11.0, 21.0)
    assert msapi.get_projection(marker, entry["camera"]) == (11.0, 21.0, True)
    other = rev.entries[0]
    review.accept_kept(state, rev)
    assert other["state"] == review.ACCEPTED
    assert review.refine(state, rev, other, 5.0, 6.0) is True              # deja validee
    assert other["state"] == review.REFINED
    assert feedback_rows()[-1]["action"] == "refined"
    history.clear()


def test_a_validated_point_can_be_excluded_and_restored(reviewed):
    state, rev, marker, cams = reviewed
    entry = rev.entries[1]
    review.accept_kept(state, rev)
    assert entry["state"] == review.ACCEPTED and msapi.get_projection(marker, entry["camera"])[2] is True
    assert review.remove(state, rev, entry) is True                        # apres validation
    assert entry["state"] == review.REMOVED
    assert msapi.get_projection(marker, entry["camera"]) is None           # retour a l'etat d'avant la detection
    assert feedback_rows()[-1]["action"] == "rejected"
    assert review.restore(state, rev, entry) is True
    assert entry["state"] == review.ACCEPTED                               # remis tel que valide, pas en proposition
    assert msapi.get_projection(marker, entry["camera"])[:2] == pytest.approx((entry["ai_x"], entry["ai_y"]))
    assert msapi.get_projection(marker, entry["camera"])[2] is True
    assert feedback_rows()[-1]["action"] == "accepted"


def test_a_refined_point_can_be_excluded_and_comes_back_where_it_was_refined(reviewed):
    state, rev, marker, cams = reviewed
    entry = rev.entries[3]
    review.refine(state, rev, entry, 1234.5, 987.25)
    assert review.remove(state, rev, entry) is True
    assert msapi.get_projection(marker, entry["camera"]) is None
    assert review.restore(state, rev, entry) is True
    assert entry["state"] == review.REFINED
    assert msapi.get_projection(marker, entry["camera"]) == (1234.5, 987.25, True)
    history.clear()


def test_validate_kept_pins_only_the_kept_ones(reviewed):
    state, rev, marker, cams = reviewed
    review.remove(state, rev, rev.entries[0])
    assert review.accept_kept(state, rev) == 5
    assert rev.counts() == {review.KEPT: 0, review.REMOVED: 1, review.REFINED: 0, review.ACCEPTED: 5}
    assert msapi.get_projection(marker, rev.entries[0]["camera"]) is None
    for entry in rev.entries[1:]:
        assert msapi.get_projection(marker, entry["camera"])[2] is True
    accepted = [r for r in feedback_rows() if r["action"] == "accepted"]
    assert len(accepted) == 5 and accepted[0]["x"] == pytest.approx(accepted[0]["ai_x"], abs=0.01)
    assert state.aigcp_applied == []


def test_clear_closes_and_forgets_the_review(reviewed):
    state, rev, marker, cams = reviewed
    aigcp.clear(state)
    assert state.aigcp_review is None and state.panel.calls[-1] == "close_review"


def test_a_detection_with_nothing_kept_leaves_no_review(install, ring, monkeypatch):
    chunk, marker, cams = ring
    monkeypatch.setattr(msapi, "pixel_ray", lambda camera, x, y: (
        (camera.center.x, camera.center.y, camera.center.z),
        (camera.key * 3.0 - camera.center.x, camera.key * 1.0 - camera.center.y, 7.0 * camera.key - camera.center.z)))
    state = make_state(marker)
    aigcp.start(state, "damier", "marker")
    wait_done(state)
    assert state.aigcp_review is None


# -- ouverture automatique du Review panel et journal cumule -----------------------------

def test_the_review_does_not_open_when_the_setting_is_off(install, ring):
    chunk, marker, cams = ring
    state = make_state(marker)
    state.option = lambda key: key != "ai_auto_review"
    aigcp.start(state, "damier", "marker")
    wait_done(state)
    assert state.aigcp_review is not None and "open_review" not in state.panel.calls


def test_the_review_opens_by_default(install, ring):
    chunk, marker, cams = ring
    state = make_state(marker)
    aigcp.start(state, "damier", "marker")
    wait_done(state)
    assert state.panel.calls.count("open_review") == 1


def test_the_log_accumulates_detections_and_forgets_cleared_proposals(install, ring):
    chunk, marker, cams = ring
    state = make_state(marker)
    aigcp.start(state, "damier", "marker")
    wait_done(state)
    first = list(state.aigcp_review.entries)
    assert len(state.aigcp_log) == 6
    review.refine(state, state.aigcp_review, first[0], 10.0, 20.0)
    aigcp.start(state, "damier", "marker")           # nouvelle detection : 5 propositions, la 1re est pointee
    wait_done(state)
    assert len(state.aigcp_log) == 6
    states = [e["state"] for e in state.aigcp_log]
    assert states.count(review.REFINED) == 1
    aigcp.clear(state)
    assert all(e["state"] != review.KEPT for e in state.aigcp_log)
    history.clear()


def test_a_missing_ai_setup_is_explained_in_a_box(install, world, monkeypatch):
    _chunk, marker, _cams = world
    state = make_state(marker)
    monkeypatch.setattr(aigcp, "python_path", lambda: os.path.join(os.sep, "absent", "python.exe"))
    assert aigcp.start(state, "damier", "marker") is False
    assert state.panel.setups and "CLICK HERE TO INSTALL.bat" in state.panel.setups[0]
    assert "python.exe" in state.panel.setups[0]
