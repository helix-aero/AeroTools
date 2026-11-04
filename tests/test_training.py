import json
import os
import sys
import time

import pytest

from aerotools import config, msapi
from aerotools.tools.fastgcp import aigcp, review, training

STUB_TRAIN = '''
import argparse, json, os, time
parser = argparse.ArgumentParser()
parser.add_argument("--work"); parser.add_argument("--out")
args = parser.parse_args()
work = json.load(open(args.work))
base = os.path.dirname(args.out)
json.dump({"kinds_total": len(work["types"]), "kind_index": 0, "type": work["types"][0],
           "stage": "train", "epoch": 3, "epochs": 6}, open(os.path.join(base, "progress.json"), "w"))
%SLEEP%
os.makedirs(work["out_dir"], exist_ok=True)
types = {}
for kind in work["types"]:
    open(os.path.join(work["out_dir"], kind + ".pt"), "wb").write(b"neuf " + kind.encode())
    types[kind] = {"ok": True, "metrics": {"base": {"map50": 0.5}, "new": {"map50": 0.6}}, "warning": None}
json.dump({"ok": True, "message": "", "device": "cpu", "seconds": 1.0, "types": types}, open(args.out, "w"))
'''
STUB_SLOW = STUB_TRAIN.replace("%SLEEP%", "time.sleep(60)")
STUB_QUICK = STUB_TRAIN.replace("%SLEEP%", "")
STUB_FAIL = "import sys\nsys.stderr.write('boom')\nsys.exit(3)\n"


@pytest.fixture
def base(tmp_path, local_appdata):
    """Poids de base factices et reglages de poste jetables."""
    models = tmp_path / "base_models"
    models.mkdir()
    for kind in training.TYPES:
        (models / (kind + ".pt")).write_bytes(b"base " + kind.encode())
    config.put(aigcp.SECTION, "models_dir", str(models))
    return models


def event(action, marker="GCP1", camera="cam1", kind="bombe", run="r1", **extra):
    row = {"schema": 1, "run": run, "type": kind, "chunk": "C", "marker": marker, "camera": camera,
           "image_path": "x.jpg", "ai_x": 10.0, "ai_y": 20.0, "action": action}
    row.update(extra)
    return row


def write_feedback(rows):
    path = training.feedback_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write((row if isinstance(row, str) else json.dumps(row)) + "\n")


def positives(count, kind="bombe"):
    return [event("accepted", marker="{}{}".format(kind, i), camera="c{}".format(i), kind=kind, x=1.0, y=2.0)
            for i in range(count)]


# -- retours -----------------------------------------------------------------

def test_feedback_counts_keep_the_last_event_and_ignore_restored(base):
    write_feedback([
        event("rejected", camera="a"),
        event("restored", camera="a"),                      # annule le rejet : aucun exemple
        event("rejected", camera="b"),
        event("accepted", camera="c", x=1.0, y=2.0),
        event("refined", camera="c", x=3.0, y=4.0),         # le dernier gagne
        event("accepted", camera="d", kind="damier", x=1.0, y=2.0),
        "pas du json",
        json.dumps(dict(event("accepted", camera="e"), schema=2)),
    ])
    counts = training.feedback_counts()
    assert counts["bombe"] == {"samples": 2, "positives": 1, "negatives": 1,
                               "refined": 1, "accepted": 0, "rejected": 1}
    assert counts["damier"]["positives"] == 1 and counts["damier"]["samples"] == 1


def test_feedback_counts_without_file_are_zero(base):
    counts = training.feedback_counts()
    assert counts["bombe"]["samples"] == 0 and counts["damier"]["samples"] == 0


def test_feedback_lives_in_the_chosen_storage_dir(base, tmp_path):
    assert training.feedback_path() == os.path.join(config.settings_dir(), "dataset", "aigcp_feedback.jsonl")
    elsewhere = tmp_path / "donnees"
    training.set_storage_dir(str(elsewhere))
    assert training.feedback_path() == os.path.join(str(elsewhere), "dataset", "aigcp_feedback.jsonl")
    assert review.feedback_path() == training.feedback_path()
    training.set_storage_dir("")
    assert training.storage_dir() == config.settings_dir()


def test_storage_summary_counts_what_the_folder_holds(base, tmp_path):
    root = tmp_path / "stock"
    assert training.storage_summary(str(root)) == {"annotations": 0, "models": 0, "detections": 0, "trainings": 0}
    (root / "dataset").mkdir(parents=True)
    feedback = [json.dumps(event("accepted")), "", "{}"]
    (root / "dataset" / training.FEEDBACK_FILE).write_text(chr(10).join(feedback) + chr(10), encoding="utf-8")
    (root / "models" / "local-1.1").mkdir(parents=True)
    (root / "models" / "counter.json").write_text("{}", encoding="utf-8")        # un fichier : pas un modele
    (root / "models" / "autre").mkdir()
    (root / "aigcp" / "20261104_1").mkdir(parents=True)
    (root / "aigcp" / "20261104_2").mkdir()
    summary = training.storage_summary(str(root))
    assert summary == {"annotations": 2, "models": 1, "detections": 2, "trainings": 0}


def test_copy_storage_copies_data_and_models_without_erasing_anything(base, tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    (old / "dataset").mkdir(parents=True)
    (old / "dataset" / training.FEEDBACK_FILE).write_text("a", encoding="utf-8")
    (old / "models" / "local-1.1").mkdir(parents=True)
    (old / "models" / "local-1.1" / "bombe.pt").write_bytes(b"w")
    (old / "aigcp").mkdir()
    new.mkdir()
    assert sorted(training.copy_storage(str(old), str(new))) == ["dataset", "models"]
    assert (new / "dataset" / training.FEEDBACK_FILE).read_text(encoding="utf-8") == "a"
    assert (new / "models" / "local-1.1" / "bombe.pt").read_bytes() == b"w"
    assert not (new / "aigcp").exists()                                           # les passes de detection restent
    assert (old / "dataset" / training.FEEDBACK_FILE).exists()                    # l'ancien dossier est intact
    assert training.copy_storage(str(old), str(new)) == []                        # jamais d'ecrasement
    assert training.copy_storage(str(old), str(old)) == []


def _fake_entry():
    return {"camera": "cam1", "camera_label": "cam1", "label": "GCP1", "image_path": "x.jpg",
            "ai_x": 1.0, "ai_y": 2.0, "conf": 0.9, "crop_px": 100.0, "px_per_m": 50.0}


def test_review_record_respects_the_collect_switch(base, monkeypatch):
    monkeypatch.setattr(msapi, "camera_size", lambda camera: (100, 100))
    monkeypatch.setattr(msapi, "chunk", lambda: None)
    monkeypatch.setattr(msapi, "chunk_label", lambda chunk: "C")
    rev = review.Review("bombe", "r1", [])
    assert training.collect_enabled() is True
    assert review.record(rev, _fake_entry(), "rejected") is not None
    assert len(open(training.feedback_path(), encoding="utf-8").read().splitlines()) == 1
    training.set_collect(False)
    assert training.collect_enabled() is False
    assert review.record(rev, _fake_entry(), "rejected") is None
    assert len(open(training.feedback_path(), encoding="utf-8").read().splitlines()) == 1
    training.set_collect(True)
    assert review.record(rev, _fake_entry(), "rejected") is not None


# -- registre des modeles ----------------------------------------------------

def test_base_model_is_first_and_cannot_be_deleted(base):
    models = training.list_models()
    assert [m["id"] for m in models] == [training.BASE_ID]
    assert models[0]["name"] == "Aero V1.0" and models[0]["base"] is True
    assert models[0]["types"] == ["damier", "bombe"] or sorted(models[0]["types"]) == ["bombe", "damier"]
    assert training.delete_model(training.BASE_ID) is False
    assert os.path.isfile(str(base / "bombe.pt"))
    assert training.get_model(training.BASE_ID) is not None


def _make_local(tmp_path, name, kinds):
    folder = tmp_path / "w_{}".format(name)
    folder.mkdir()
    paths = {}
    for kind in kinds:
        path = folder / (kind + ".pt")
        path.write_bytes(b"local " + kind.encode())
        paths[kind] = str(path)
    return training.register_model(paths, metrics={k: {"epochs": 5} for k in kinds})


def test_local_models_are_numbered_and_numbers_are_never_reused(base, tmp_path):
    first = _make_local(tmp_path, "a", ["bombe"])
    second = _make_local(tmp_path, "b", ["bombe", "damier"])
    assert (first["name"], second["name"]) == ("Local 1.1", "Local 1.2")
    assert [m["name"] for m in training.list_models()] == ["Aero V1.0", "Local 1.1", "Local 1.2"]
    assert training.delete_model(second["id"]) is True
    third = _make_local(tmp_path, "c", ["bombe"])
    assert third["name"] == "Local 1.3"                       # 1.2 ne revient pas
    assert [m["name"] for m in training.list_models()] == ["Aero V1.0", "Local 1.1", "Local 1.3"]
    assert training.get_model(third["id"])["created"]


def test_weights_follow_the_active_model_with_a_fallback_to_the_base(base, tmp_path):
    assert aigcp.weights_path("bombe") == str(base / "bombe.pt")
    local = _make_local(tmp_path, "a", ["bombe"])
    assert training.set_active_model(local["id"]) is True
    assert training.active_model() == local["id"]
    assert aigcp.weights_path("bombe") == os.path.join(local["path"], "bombe.pt")
    assert aigcp.weights_path("damier") == str(base / "damier.pt")      # type absent du modele local
    assert training.set_active_model("local-1.99") is False
    training.revert_to_base()
    assert aigcp.weights_path("bombe") == str(base / "bombe.pt")


def test_deleting_the_active_model_goes_back_to_the_base(base, tmp_path):
    local = _make_local(tmp_path, "a", ["bombe"])
    training.set_active_model(local["id"])
    assert training.delete_model(local["id"]) is True
    assert training.active_model() == training.BASE_ID
    assert aigcp.weights_path("bombe") == str(base / "bombe.pt")
    assert training.delete_model("n-importe-quoi") is False


def test_a_vanished_active_model_falls_back_to_the_base(base):
    config.put(aigcp.SECTION, "model", "local-1.7")
    assert training.active_model() == training.BASE_ID


# -- entrainement ------------------------------------------------------------

def _stub(tmp_path, source):
    path = tmp_path / "stub_train.py"
    path.write_text(source)
    return str(path)


def _finish(job, seconds=20.0):
    end = time.time() + seconds
    while time.time() < end:
        result = job.poll()
        if result is not None:
            return result
        time.sleep(0.05)
    job.cancel()
    raise AssertionError("l'entrainement factice n'a pas fini")


def test_a_type_needs_enough_positives_to_be_trained(base, tmp_path):
    write_feedback(positives(training.MIN_POSITIVES - 1))
    assert training.eligible_kinds() == []
    result = training.Training(python=sys.executable, script=_stub(tmp_path, STUB_QUICK)).start().poll()
    assert result["ok"] is False and result["code"] == "no_data"
    assert result["model_id"] is None
    write_feedback(positives(training.MIN_POSITIVES) + positives(2, kind="damier"))
    assert training.eligible_kinds() == ["bombe"]            # le damier n'en a pas assez, le type est ignore


def test_negatives_alone_never_make_a_type_eligible(base):
    write_feedback([event("rejected", camera="c{}".format(i)) for i in range(50)])
    assert training.eligible_kinds() == []


def test_training_registers_a_model_and_cleans_up(base, tmp_path):
    write_feedback(positives(5))
    job = training.Training(min_positives=5, python=sys.executable, script=_stub(tmp_path, STUB_QUICK))
    job.start()
    result = _finish(job)
    assert result["ok"] is True, result
    assert result["name"] == "Local 1.1" and result["kinds_trained"] == ["bombe"]
    assert result["metrics"]["bombe"]["metrics"]["new"]["map50"] == 0.6
    model = training.get_model(result["model_id"])
    assert model["types"] == ["bombe"]
    assert open(os.path.join(model["path"], "bombe.pt"), "rb").read() == b"neuf bombe"
    assert open(str(base / "bombe.pt"), "rb").read() == b"base bombe"            # la base n'a pas bouge
    assert not os.path.exists(job.run_dir)
    assert training.is_training() is False
    assert training.active_model() == training.BASE_ID                           # on n'active rien tout seul


def test_progress_is_read_from_the_runner(base, tmp_path):
    write_feedback(positives(5))
    job = training.Training(min_positives=5, python=sys.executable, script=_stub(tmp_path, STUB_SLOW)).start()
    try:
        end = time.time() + 15
        while job.progress()[0] is None and time.time() < end:
            time.sleep(0.05)
        fraction, text = job.progress()
        assert abs(fraction - 0.5) < 1e-6 and text == "bombe epoch 3/6"
        assert training.is_training() is True
    finally:
        job.cancel()


def test_cancel_stops_the_run_and_leaves_no_model(base, tmp_path):
    write_feedback(positives(5))
    job = training.Training(min_positives=5, python=sys.executable, script=_stub(tmp_path, STUB_SLOW)).start()
    run_dir = job.run_dir
    assert job.running() is True
    job.cancel()
    result = job.poll()
    assert result["ok"] is False and result["code"] == "cancelled"
    assert job.running() is False
    assert not os.path.exists(run_dir)
    assert [m["id"] for m in training.list_models()] == [training.BASE_ID]
    assert training.is_training() is False


def test_a_second_training_is_refused_while_one_runs(base, tmp_path):
    write_feedback(positives(5))
    first = training.Training(min_positives=5, python=sys.executable, script=_stub(tmp_path, STUB_SLOW)).start()
    try:
        second = training.Training(min_positives=5, python=sys.executable,
                                   script=_stub(tmp_path, STUB_QUICK)).start()
        assert second.poll()["code"] == "busy"
        assert training.is_training() is True                # le verrou du premier est intact
    finally:
        first.cancel()
    assert training.is_training() is False


def test_a_crashing_trainer_gives_a_clear_failure(base, tmp_path):
    write_feedback(positives(5))
    job = training.Training(min_positives=5, python=sys.executable, script=_stub(tmp_path, STUB_FAIL)).start()
    result = _finish(job)
    assert result["ok"] is False and result["code"] == "failed" and "boom" in result["message"]
    assert [m["id"] for m in training.list_models()] == [training.BASE_ID]
    assert not os.path.exists(os.path.join(job.run_dir, "out"))
    assert training.is_training() is False


def test_missing_python_is_reported(base, tmp_path):
    write_feedback(positives(5))
    job = training.Training(min_positives=5, python=str(tmp_path / "pas_de_python.exe"),
                            script=_stub(tmp_path, STUB_QUICK)).start()
    assert job.poll()["code"] == "no_python"


# -- partie sans torch du script d'entrainement ------------------------------

def test_trainer_helpers_cap_and_split_by_target():
    from aerotools.tools.fastgcp import aigcp_train as tr
    import random
    rows = [dict(event("accepted", marker="A", camera="c{}".format(i), x=1.0, y=1.0)) for i in range(40)]
    rows += [dict(event("refined", marker="A", camera="r{}".format(i), x=1.0, y=1.0)) for i in range(3)]
    capped = tr.cap_per_target(rows, random.Random(0))
    assert len(capped) == tr.MAX_PER_TARGET
    assert sum(1 for r in capped if r["action"] == "refined") == 3          # les affinages d'abord
    assert tr.split_targets({"a", "b", "c", "d"}) == set()                   # moins de 5 cibles : pas de val
    val = tr.split_targets({"t{}".format(i) for i in range(17)})
    assert len(val) == 3 and val <= {"t{}".format(i) for i in range(17)}
    assert tr.split_targets({"t{}".format(i) for i in range(17)}) == val     # deterministe
    assert tr.side_of({"crop_px": 20}) == tr.CROP_MIN and tr.side_of({"crop_px": 9000}) == tr.CROP_MAX
