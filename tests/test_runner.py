"""Le runner de detection : decodage des sorties ONNX, lots, photos illisibles, de bout en bout (moteur factice)."""

import json
import os
import subprocess

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

from aerotools.tools.fastgcp import aigcp_runner as runner, training


class FakeDetector(object):
    """Detecte une tache claire : renvoie son centre et une confiance qui depend de sa luminosite."""

    device = "fake"

    def __init__(self):
        self.calls = 0
        self.tiles = 0

    def predict(self, tiles):
        self.calls += 1
        self.tiles += len(tiles)
        out = []
        for tile in tiles:
            gray = tile.mean(2)
            ys, xs = np.nonzero(gray > 200)
            if len(xs) < 20:
                out.append(np.zeros((0, 5), np.float32))
                continue
            conf = min(0.99, len(xs) / 400.0)
            out.append(np.array([[xs.mean(), ys.mean(), 12.0, 12.0, conf]], np.float32))
        return out


def photo(path, spots, size=(1200, 900)):
    """Image sombre avec des taches claires de 20 x 20 aux positions `spots`."""
    image = np.full((size[1], size[0], 3), 40, np.uint8)
    for x, y in spots:
        image[y - 10:y + 10, x - 10:x + 10] = 255
    assert cv2.imwrite(path, image)
    return image


def item(name, path, x, y, rescue=True):
    return {"id": name, "image_path": path, "x_est": x, "y_est": y, "px_per_m": 250.0, "rescue": rescue}


def test_nms_keeps_the_best_of_overlapping_boxes():
    boxes = np.array([[0, 0, 10, 10], [0.5, 0.5, 10.5, 10.5], [50, 50, 60, 60]], np.float32)   # IoU des deux premières ≈ 0,82
    scores = np.array([0.6, 0.9, 0.5], np.float32)
    assert runner.nms(boxes, scores, 0.7, 10) == [1, 2]
    assert runner.nms(boxes, scores, 0.7, 1) == [1]                     # max_det


def test_decode_output_applies_the_floor_and_nms():
    # sortie brute d'un modele a une classe : (4 + 1, ancres) ; deux ancres sur le meme objet, une faible
    raw = np.zeros((5, 4), np.float32)
    raw[:, 0] = (80.0, 80.0, 20.0, 20.0, 0.80)
    raw[:, 1] = (82.0, 81.0, 20.0, 20.0, 0.70)
    raw[:, 2] = (30.0, 30.0, 10.0, 10.0, 0.04)                          # sous le seuil de 0,05
    raw[:, 3] = (120.0, 40.0, 10.0, 10.0, 0.30)
    out = runner.decode_output(raw)
    assert out.shape == (2, 5)
    assert out[0].tolist() == pytest.approx([80.0, 80.0, 20.0, 20.0, 0.80])
    assert out[1, 4] == pytest.approx(0.30)
    assert runner.decode_output(np.zeros((5, 4), np.float32)).shape == (0, 5)


def test_photo_groups_follow_the_order_of_the_items():
    items = [{"image_path": "a"}, {"image_path": "a"}, {"image_path": "b"}, {"image_path": "a"}]
    assert runner.photo_groups(items) == [("a", [0, 1]), ("b", [2]), ("a", [3])]


def test_batching_gives_the_same_rows_as_one_marker_at_a_time(tmp_path):
    path = str(tmp_path / "p.jpg")
    image = photo(path, [(300, 300), (700, 500)])
    items = [item("A", path, 305, 296), item("B", path, 702, 503), item("C", path, 900, 800), item("D", path, 100, 100, rescue=False)]
    scales = (0.3, 0.5, 0.8)
    together = runner.detect_items(FakeDetector(), image, items, scales, 0.25)
    alone = [runner.detect_items(FakeDetector(), image, [one], scales, 0.25)[0] for one in items]
    assert together == alone
    assert [row["found"] for row in together] == [True, True, False, False]
    assert together[0]["x"] == pytest.approx(300.0, abs=2.0) and together[0]["y"] == pytest.approx(300.0, abs=2.0)


def test_batching_makes_far_fewer_model_calls(tmp_path):
    path = str(tmp_path / "p.jpg")
    image = photo(path, [(300, 300)])
    items = [item("M%d" % i, path, 300 + i, 300) for i in range(6)]
    batched, one_by_one = FakeDetector(), FakeDetector()
    runner.detect_items(batched, image, items, (0.3, 0.5, 0.8), 0.25)
    for one in items:
        runner.detect_items(one_by_one, image, [one], (0.3, 0.5, 0.8), 0.25)
    assert batched.tiles == one_by_one.tiles                             # meme travail...
    assert batched.calls <= 3 and one_by_one.calls >= 12                 # ...en bien moins d'appels


def run_work(tmp_path, monkeypatch, items, crops=False):
    monkeypatch.setattr(runner, "make_detector", lambda weights, device: FakeDetector())
    work = {"version": 1, "type": "damier", "weights": "unused.pt", "conf": 0.25, "scales_m": [0.3, 0.5, 0.8],
            "device": "auto", "items": items}
    if crops:
        work["crops_dir"] = str(tmp_path / "crops")
    work_path, out_path = str(tmp_path / "work.json"), str(tmp_path / "hits.json")
    with open(work_path, "w", encoding="utf-8") as handle:
        json.dump(work, handle)
    runner.run(work_path, out_path)
    with open(out_path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def test_run_end_to_end_with_review_crops(tmp_path, monkeypatch):
    first, second = str(tmp_path / "1.jpg"), str(tmp_path / "2.jpg")
    photo(first, [(300, 300)])
    photo(second, [(600, 400)])
    items = [item("a/1", first, 302, 298), item("b/1", second, 598, 402), item("a/2", first, 900, 700)]
    result = run_work(tmp_path, monkeypatch, items, crops=True)
    assert result["errors"] == [] and result["device"] == "fake"
    assert [row["id"] for row in result["hits"]] == ["a/1", "b/1", "a/2"]          # l'ordre des items est gardé
    assert [row["found"] for row in result["hits"]] == [True, True, False]
    assert os.path.isfile(str(tmp_path / "crops" / "a_1.jpg"))                      # vignette de revue
    with open(str(tmp_path / "progress.json"), encoding="utf-8") as handle:
        assert json.load(handle) == {"done": 3, "total": 3}


def test_an_unreadable_photo_does_not_stop_the_others(tmp_path, monkeypatch):
    good, broken = str(tmp_path / "good.jpg"), str(tmp_path / "broken.jpg")
    photo(good, [(300, 300)])
    with open(broken, "wb") as handle:
        handle.write(b"ce n'est pas une image")
    items = [item("ok", good, 300, 300), item("bad", broken, 300, 300), item("missing", str(tmp_path / "nope.jpg"), 1, 1),
             item("ok2", good, 301, 299)]
    result = run_work(tmp_path, monkeypatch, items)
    assert [row["id"] for row in result["hits"]] == ["ok", "ok2"]
    assert len(result["errors"]) == 2
    assert any(line.startswith("bad") for line in result["errors"]) and any(line.startswith("missing") for line in result["errors"])


def test_an_empty_work_file_gives_an_empty_result(tmp_path, monkeypatch):
    result = run_work(tmp_path, monkeypatch, [])
    assert result["hits"] == [] and result["errors"] == []


def test_a_marker_outside_the_photo_is_not_found_and_not_an_error(tmp_path, monkeypatch):
    path = str(tmp_path / "p.jpg")
    photo(path, [(300, 300)])
    result = run_work(tmp_path, monkeypatch, [item("far", path, 50000, 50000)])
    assert result["errors"] == [] and result["hits"][0]["found"] is False


def test_the_onnx_file_is_found_next_to_the_weights():
    assert runner.onnx_path(os.path.join("a", "models", "bombe.pt")) == os.path.join("a", "models", "bombe.onnx")


# -- outils d'entrainement ----------------------------------------------------------

def test_missing_tools_reads_the_list_from_the_python(tmp_path, monkeypatch):
    fake = tmp_path / "python.exe"
    fake.write_text("")

    def answer(code, out):
        return lambda *args, **kwargs: subprocess.CompletedProcess(args, code, stdout=out, stderr=b"")

    monkeypatch.setattr(subprocess, "run", answer(0, b"\n"))
    assert training.missing_tools(str(fake)) == []
    monkeypatch.setattr(subprocess, "run", answer(1, b"torch,onnx\n"))
    assert training.missing_tools(str(fake)) == ["torch", "onnx"]
    assert training.missing_tools(str(tmp_path / "absent.exe")) == ["python"]

    def broken(*args, **kwargs):
        raise OSError("boom")
    monkeypatch.setattr(subprocess, "run", broken)
    assert training.missing_tools(str(fake)) == ["?"]
