import json
import sys
import time
import types

import pytest

from aerotools import config, msapi
from aerotools.tools.fastgcp import aigcp, aigcp_runner

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
STUB_FAIL = "import sys\nsys.stderr.write('boom')\nsys.exit(3)\n"


@pytest.fixture(autouse=True)
def _bind(ms):
    msapi.Metashape = ms


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

    def set_status(self, text):
        self.statuses.append(text)

    def update_active_card(self):
        pass

    def refresh_errors(self):
        pass


def make_state(marker):
    state = types.SimpleNamespace(panel=FakePanel(), active_marker=marker,
                                  aigcp_run=None, aigcp_applied=[])
    state.active_label = lambda: marker.label
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
    items, skipped = aigcp.collect_items(chunk, marker)
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
    items, _ = aigcp.collect_items(chunk, marker)
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

def test_collect_all_skips_markers_without_two_projections(ms, install, confirmed):
    chunk, first, cams = confirmed
    chunk.markers.append(ms.Marker("GCP2", position=ms.Vector(0, 0, 0)))
    items, used, ignored = aigcp.collect_all(chunk)
    assert used == 1 and ignored == 1
    assert items and all(it["label"] == "GCP1" and it["id"].startswith("0:") for it in items)
    assert [it["image_path"] for it in items] == sorted(it["image_path"] for it in items)


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


def test_scope_all_without_confirmed_marker_says_so(install, world):
    _chunk, marker, cams = world
    del marker.projections[cams[1]]           # une seule projection : position non confirmee
    state = make_state(marker)
    assert aigcp.start(state, "damier", "all") is False
    assert "2 projections" in state.panel.statuses[-1]


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
