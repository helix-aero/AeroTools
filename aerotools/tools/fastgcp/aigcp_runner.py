"""Executeur externe de la detection AIGCP (damier ou bombe).

Lance par FastGCP dans un Python a part, jamais dans celui de Metashape :

    python aigcp_runner.py --work work.json --out hits.json

Deux moteurs, choisis d'apres les poids demandes (`weights`, un fichier .pt) :
  - ONNX Runtime, s'il existe un fichier .onnx a cote du .pt et que onnxruntime est installe :
    leger (une dizaine de Mo en tout, sans torch), demarre en une fraction de seconde ;
  - ultralytics (torch), sinon : le moteur d'origine, aussi utilise par l'entrainement.
Les deux donnent les memes detections (a 0,2 px et 0,01 de confiance pres).

`work.json` (ecrit par aigcp.py) :
    {"version": 1, "type": "damier", "weights": "...pt", "conf": 0.25,
     "scales_m": [0.3, 0.5, 0.8], "device": "auto",
     "engine": "onnx" (leger) | "torch" (precis) | "auto",
     "items": [{"id": "...", "image_path": "...", "x_est": 123.4,
                "y_est": 456.7, "px_per_m": 210.0, "rescue": true}, ...],
     "crops_dir": "dossier des vignettes de revue (facultatif)"}
`rescue` (defaut vrai) : permet la recherche elargie si rien n'est trouve au centre ;
faux quand la position attendue est connue a quelques centimetres.

`hits.json` (ecrit ici, en fin de travail) :
    {"version": 1, "seconds": 3.2, "device": "cpu",
     "hits": [{"id": "...", "found": true, "x": 130.1, "y": 450.2,
               "conf": 0.91, "crop_px": 214,
               "crop": {"big": "0_12.jpg", "win": 214, "x0": 23, "y0": 343}}, ...],
     "errors": ["id : message", ...]}

Methode, celle qui a ete mesuree sur le jeu d'apprentissage (modeles\\evaluer.py) :
recadrage carre de 4 x S x px_par_metre pixels (borne a 96-640) autour de la
position estimee du marqueur, ramene a 160 x 160 (gris hors image), modele
YOLO11n, detection la plus proche du centre ponderee par la confiance. S est
inconnue : trois tailles de cible (0,3 / 0,5 / 0,8 m). Si rien n'est trouve,
on cherche dans 4 recadrages decales de 0,4 fois la taille du recadrage. Une
seconde passe recentre le recadrage sur la detection pour affiner la position.

Vitesse : les photos sont decodees en avance par plusieurs fils (le decodage JPEG de
21 Mpixels prend ~100 ms et dominait), et toutes les tuiles des marqueurs d'une meme photo
passent au modele en un seul lot par etape (premiere passe, recherche elargie, affinage)
au lieu d'un appel par marqueur. Le resultat est identique a celui de l'ancienne boucle.
"""

import argparse
import json
import os
import sys
import time
from collections import deque

OUT = 160
CROP_MIN, CROP_MAX = 96, 640
FLOOR_CONF = 0.05
PROXIMITY_PX = 40.0
GRID_OFFSET = 0.4
GRID_STEPS = ((1, 0), (-1, 0), (0, 1), (0, -1))
NMS_IOU = 0.7
MAX_DET = 10
BATCH = 64                    # tuiles par appel du modele
MAX_WORKERS = 6               # fils de decodage


def read_image(path):
    import cv2
    import numpy as np
    return cv2.imdecode(np.fromfile(path, np.uint8),
                        cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)


def crop_size(ppm, scale_m):
    return int(round(min(CROP_MAX, max(CROP_MIN, 4.0 * scale_m * ppm))))


def crop(image, cx, cy, size, out=OUT):
    """Carre de `size` px centre en (cx, cy), gris hors image, ramene a `out` x `out`.

    Renvoie (tuile, x0, y0) ou None si le carre est entierement hors image.
    """
    import cv2
    import numpy as np
    height, width = image.shape[:2]
    x0 = int(round(cx - size / 2.0))
    y0 = int(round(cy - size / 2.0))
    x1, y1 = x0 + size, y0 + size
    if x0 < 0 or y0 < 0 or x1 > width or y1 > height:
        tile = np.full((size, size, 3), 128, np.uint8)
        sx0, sy0 = max(0, x0), max(0, y0)
        sx1, sy1 = min(width, x1), min(height, y1)
        if sx1 <= sx0 or sy1 <= sy0:
            return None
        tile[sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0] = image[sy0:sy1, sx0:sx1]
    else:
        tile = image[y0:y1, x0:x1]
    interpolation = cv2.INTER_AREA if size > out else cv2.INTER_CUBIC
    return cv2.resize(tile, (out, out), interpolation=interpolation), x0, y0


# --------------------------------------------------------------------------
# Moteurs
# --------------------------------------------------------------------------

class Detector(object):
    """Moteur ultralytics (torch) : celui d'origine, aussi utilise par l'entrainement."""

    def __init__(self, weights, device):
        try:
            from ultralytics import YOLO
        except ImportError:
            raise RuntimeError("pas de fichier {} et ultralytics n'est pas installe : relance l'entrainement "
                               "(installeur d'entrainement) ou remets le modele de base".format(
                                   os.path.basename(onnx_path(weights))))
        self.model = YOLO(weights)
        self.device = device

    def predict(self, tiles):
        """Une liste de tableaux (N, 5) = cx, cy, w, h, conf (px de la tuile), par tuile."""
        import numpy as np
        out = []
        for start in range(0, len(tiles), BATCH):
            chunk = tiles[start:start + BATCH]
            for result in self.model.predict(chunk, imgsz=OUT, conf=FLOOR_CONF, iou=NMS_IOU,
                                             max_det=MAX_DET, device=self.device, verbose=False):
                if result.boxes is None or len(result.boxes) == 0:
                    out.append(np.zeros((0, 5), np.float32))
                    continue
                xywh = result.boxes.xywh.cpu().numpy()
                conf = result.boxes.conf.cpu().numpy()[:, None]
                out.append(np.concatenate([xywh, conf], 1))
        return out


def nms(boxes, scores, iou, limit):
    """Suppression des non-maxima (gloutonne, boxes en x1 y1 x2 y2). Renvoie les indices gardes."""
    import numpy as np
    order = np.argsort(-scores)
    keep = []
    while len(order) and len(keep) < limit:
        i = int(order[0])
        keep.append(i)
        if len(order) == 1:
            break
        rest = order[1:]
        x1 = np.maximum(boxes[i, 0], boxes[rest, 0])
        y1 = np.maximum(boxes[i, 1], boxes[rest, 1])
        x2 = np.minimum(boxes[i, 2], boxes[rest, 2])
        y2 = np.minimum(boxes[i, 3], boxes[rest, 3])
        inter = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
        area_i = (boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1])
        area_r = (boxes[rest, 2] - boxes[rest, 0]) * (boxes[rest, 3] - boxes[rest, 1])
        overlap = inter / np.maximum(area_i + area_r - inter, 1e-9)
        order = rest[overlap <= iou]
    return keep


def decode_output(row, floor=FLOOR_CONF, iou=NMS_IOU, limit=MAX_DET):
    """Sortie brute d'un modele YOLO exporte (4 + classes, ancres) -> (N, 5) cx, cy, w, h, conf."""
    import numpy as np
    pred = np.asarray(row, np.float32).T                # (ancres, 4 + classes)
    scores = pred[:, 4:].max(1)
    mask = scores > floor
    if not mask.any():
        return np.zeros((0, 5), np.float32)
    pred, scores = pred[mask], scores[mask]
    xywh = pred[:, :4]
    xyxy = np.stack([xywh[:, 0] - xywh[:, 2] / 2.0, xywh[:, 1] - xywh[:, 3] / 2.0,
                     xywh[:, 0] + xywh[:, 2] / 2.0, xywh[:, 1] + xywh[:, 3] / 2.0], 1)
    keep = nms(xyxy, scores, iou, limit)
    return np.concatenate([xywh[keep], scores[keep][:, None]], 1).astype(np.float32)


class OnnxDetector(object):
    """Moteur ONNX Runtime sur processeur : sans torch ni ultralytics.

    Mesure sur un vrai run (1171 marqueurs, 182 photos de 21 Mpixels) : plus rapide que torch
    sur une RTX 4070 (24 s contre 47 s avec l'ancienne boucle). DirectML (carte graphique) a ete
    essaye : 38 s, plus lent, car ces tuiles de 160 x 160 sont trop petites pour amortir chaque appel.
    """

    def __init__(self, path, threads=None):
        import onnxruntime as ort
        options = ort.SessionOptions()
        options.intra_op_num_threads = int(threads or min(8, os.cpu_count() or 2))
        options.log_severity_level = 3
        self.session = ort.InferenceSession(path, options, providers=["CPUExecutionProvider"])
        self.input = self.session.get_inputs()[0].name
        self.device = "onnx-cpu"

    def predict(self, tiles):
        import numpy as np
        out = []
        for start in range(0, len(tiles), BATCH):
            chunk = np.stack(tiles[start:start + BATCH])[..., ::-1]          # BGR -> RGB
            batch = np.ascontiguousarray(chunk.transpose(0, 3, 1, 2), dtype=np.float32) / 255.0
            raw = self.session.run(None, {self.input: batch})[0]
            out.extend(decode_output(row) for row in raw)
        return out


def onnx_path(weights):
    return os.path.splitext(weights)[0] + ".onnx"


def make_detector(weights, device, engine="auto"):
    """Le moteur demande : "onnx" (ONNX Runtime, leger), "torch" (ultralytics, le precis, carte graphique
    si presente) ou "auto" (ONNX si possible et sur processeur, sinon ultralytics).

    Un moteur choisi expressement n'est jamais remplace en silence : s'il manque, l'erreur le dit."""
    path = onnx_path(weights)
    if engine == "onnx":
        if not os.path.isfile(path):
            raise RuntimeError("le moteur leger demande le fichier {} (absent) : reinstalle le modele ou "
                               "choisis le moteur precis".format(os.path.basename(path)))
        return OnnxDetector(path)
    if engine == "torch":
        return Detector(weights, pick_device(device))
    if device in (None, "auto", "cpu") and os.path.isfile(path):
        try:
            return OnnxDetector(path)
        except Exception as exc:
            sys.stderr.write("aigcp_runner : ONNX indisponible ({!r}), repli sur ultralytics\n".format(exc))
    return Detector(weights, pick_device(device))


# --------------------------------------------------------------------------
# Detection
# --------------------------------------------------------------------------

def best_of(preds, metas, nearest):
    """Meilleure detection parmi des tuiles : (x, y, conf, taille) en px photo, ou None.

    `nearest` : score = confiance x exp(-(d / 40 px)^2 / 2), d = distance au centre
    de la tuile (donc a l'estimation) ; sinon, la plus forte confiance.
    """
    import numpy as np
    best, best_score = None, -1.0
    for det, (x0, y0, size) in zip(preds, metas):
        if not len(det):
            continue
        if nearest:
            dist = np.hypot(det[:, 0] - OUT / 2.0, det[:, 1] - OUT / 2.0)
            score = det[:, 4] * np.exp(-0.5 * (dist / PROXIMITY_PX) ** 2)
        else:
            score = det[:, 4]
        i = int(np.argmax(score))
        if score[i] > best_score:
            best_score = float(score[i])
            best = (float(x0 + det[i, 0] * size / OUT), float(y0 + det[i, 1] * size / OUT),
                    float(det[i, 4]), int(size))
    return best


def batch_best(detector, image, requests):
    """Une etape pour plusieurs marqueurs : toutes les tuiles passent au modele en un seul lot.

    `requests` : liste de (cle, centres [(x, y, taille)], nearest). Renvoie {cle: meilleur ou None}.
    """
    tiles, metas, spans = [], [], []
    for key, centres, nearest in requests:
        begin = len(tiles)
        for (x, y, size) in centres:
            made = crop(image, x, y, size)
            if made is not None:
                tiles.append(made[0])
                metas.append((made[1], made[2], size))
        spans.append((key, begin, len(tiles), nearest))
    results = {key: None for key, _c, _n in requests}
    if not tiles:
        return results
    preds = detector.predict(tiles)
    for key, begin, end, nearest in spans:
        if end > begin:
            results[key] = best_of(preds[begin:end], metas[begin:end], nearest)
    return results


def _row(found, conf):
    if found is None or found[2] < conf:
        return {"found": False, "conf": 0.0 if found is None else round(found[2], 4)}
    return {"found": True, "x": round(found[0], 2), "y": round(found[1], 2),
            "conf": round(found[2], 4), "crop_px": int(found[3])}


def detect_items(detector, image, items, scales, conf):
    """Resultats de tous les marqueurs d'une photo : liste de dicts {found, x, y, conf, crop_px}.

    Meme methode que `detect_item`, mais chaque etape est groupee pour toute la photo.
    """
    count = len(items)
    plans = []
    for item in items:
        ppm = float(item["px_per_m"])
        cx, cy = float(item["x_est"]), float(item["y_est"])
        plans.append((cx, cy, sorted(set(crop_size(ppm, s) for s in scales)), bool(item.get("rescue", True))))

    # 1. premiere passe : les tailles de cible autour de l'estimation
    first = batch_best(detector, image, [
        (i, [(cx, cy, s) for s in sizes], True) for i, (cx, cy, sizes, _r) in enumerate(plans)])
    found = [first[i] for i in range(count)]

    # 2. recherche elargie pour ceux qui n'ont rien de sur
    requests = []
    for i, (cx, cy, sizes, rescue) in enumerate(plans):
        if rescue and (found[i] is None or found[i][2] < conf):
            around = [(cx, cy, s) for s in sizes]
            for s in sizes:
                for dx, dy in GRID_STEPS:
                    around.append((cx + dx * GRID_OFFSET * s, cy + dy * GRID_OFFSET * s, s))
            requests.append((i, around, False))
    if requests:
        wider = batch_best(detector, image, requests)
        for i, _c, _n in requests:
            if wider[i] is not None and (found[i] is None or wider[i][2] > found[i][2]):
                found[i] = wider[i]

    # 3. affinage : on recentre le recadrage sur la detection
    requests = [(i, [(found[i][0], found[i][1], found[i][3])], True)
                for i in range(count) if found[i] is not None and found[i][2] >= conf]
    if requests:
        refined = batch_best(detector, image, requests)
        for i, _c, _n in requests:
            if refined[i] is not None and refined[i][2] >= conf:
                found[i] = refined[i]

    return [_row(found[i], conf) for i in range(count)]


def detect_item(detector, image, item, scales, conf):
    """Resultat d'une photo : dict {found, x, y, conf, crop_px}."""
    return detect_items(detector, image, [item], scales, conf)[0]


BIG = 960


def save_review_crops(image, row, crops_dir, name):
    """Recadrage autour du point trouve, pour le Review panel de FastGCP.

    La fenetre est celle du modele (`crop_px`, au moins 96 px) : l'interface en decoupe
    la moitie centrale a zoom normal, et le curseur de zoom montre plus ou moins de
    contexte. Renvoie {big, win, x0, y0} (nom du fichier dans `crops_dir`, win = cote
    de la fenetre en pixels de la photo, x0 y0 = son coin), ou None.
    """
    import cv2
    side = max(CROP_MIN, int(round(row["crop_px"])))
    made = crop(image, row["x"], row["y"], side, BIG)
    if made is None:
        return None
    ok, buffer = cv2.imencode(".jpg", made[0], [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        return None
    buffer.tofile(os.path.join(crops_dir, name + ".jpg"))
    return {"big": name + ".jpg", "win": side, "x0": made[1], "y0": made[2]}


def pick_device(wanted):
    if wanted and wanted != "auto":
        return wanted
    try:
        import torch
        return 0 if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def write_progress(out_path, done, total):
    """progress.json a cote de hits.json : la barre de progression de FastGCP le relit."""
    path = os.path.join(os.path.dirname(os.path.abspath(out_path)), "progress.json")
    try:
        with open(path + ".tmp", "w", encoding="utf-8") as handle:
            json.dump({"done": done, "total": total}, handle)
        os.replace(path + ".tmp", path)
    except OSError:
        pass            # lecture en cours cote FastGCP : la prochaine ecriture passera


def photo_groups(items):
    """[(chemin, [indices des items])] : les items d'une meme photo se suivent, un seul decodage."""
    groups = []
    for index, item in enumerate(items):
        if groups and groups[-1][0] == item["image_path"]:
            groups[-1][1].append(index)
        else:
            groups.append((item["image_path"], [index]))
    return groups


def safe_name(identifier):
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in str(identifier))


def run(work_path, out_path):
    from concurrent.futures import ThreadPoolExecutor
    started = time.time()
    with open(work_path, "r", encoding="utf-8-sig") as handle:
        work = json.load(handle)
    scales = [float(s) for s in work.get("scales_m") or (0.3, 0.5, 0.8)]
    conf = float(work.get("conf", 0.25))
    detector = make_detector(work["weights"], work.get("device", "auto"), work.get("engine", "auto"))
    items = work.get("items", [])
    crops_dir = work.get("crops_dir")
    if crops_dir:
        os.makedirs(crops_dir, exist_ok=True)
    rows = [None] * len(items)
    errors = []
    write_progress(out_path, 0, len(items))
    last_write, done = time.time(), 0

    groups = photo_groups(items)
    workers = max(2, min(MAX_WORKERS, (os.cpu_count() or 4) - 1))
    try:
        import cv2
        cv2.setNumThreads(1)           # le parallelisme vient des fils de decodage
    except Exception:
        pass
    pending = deque()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        def submit(position):
            path = groups[position][0]
            pending.append((position, pool.submit(_decode, path)))

        for position in range(min(len(groups), workers + 1)):
            submit(position)
        next_position = len(pending)
        while pending:
            position, future = pending.popleft()
            if next_position < len(groups):
                submit(next_position)
                next_position += 1
            image, problem = future.result()
            indices = groups[position][1]
            group_items = [items[i] for i in indices]
            try:
                if image is None:
                    raise ValueError(problem or "image illisible")
                results = detect_items(detector, image, group_items, scales, conf)
                for index, item, row in zip(indices, group_items, results):
                    row["id"] = item["id"]
                    if crops_dir and row["found"]:
                        row["crop"] = save_review_crops(image, row, crops_dir, safe_name(item["id"]))
                    rows[index] = row
            except Exception as exc:
                for item in group_items:
                    errors.append("{} : {!r}".format(item.get("id"), exc))
            done += len(indices)
            if time.time() - last_write >= 0.25:
                write_progress(out_path, done, len(items))
                last_write = time.time()
    write_progress(out_path, len(items), len(items))
    result = {"version": 1, "seconds": round(time.time() - started, 2),
              "device": str(getattr(detector, "device", "cpu")),
              "hits": [row for row in rows if row is not None], "errors": errors}
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False)
    os.replace(tmp, out_path)


def _decode(path):
    """(image, probleme) : ne leve jamais, pour qu'un fichier illisible ne coupe pas la detection."""
    try:
        image = read_image(path)
        return (image, None) if image is not None else (None, "image illisible")
    except Exception as exc:
        return None, "image illisible ({!r})".format(exc)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Detection AIGCP pour FastGCP")
    parser.add_argument("--work", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        run(args.work, args.out)
    except Exception as exc:
        sys.stderr.write("aigcp_runner : {!r}\n".format(exc))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
