"""Executeur externe de la detection AIGCP (damier ou bombe).

Lance par FastGCP dans un Python a part (celui qui a torch et ultralytics),
jamais dans celui de Metashape :

    python aigcp_runner.py --work work.json --out hits.json

`work.json` (ecrit par aigcp.py) :
    {"version": 1, "type": "damier", "weights": "...pt", "conf": 0.25,
     "scales_m": [0.3, 0.5, 0.8], "device": "auto",
     "items": [{"id": "...", "image_path": "...", "x_est": 123.4,
                "y_est": 456.7, "px_per_m": 210.0, "rescue": true}, ...]}
`rescue` (defaut vrai) : permet la recherche elargie si rien n'est trouve au centre ;
faux quand la position attendue est connue a quelques centimetres.

`hits.json` (ecrit ici, en fin de travail) :
    {"version": 1, "seconds": 3.2, "device": "cpu",
     "hits": [{"id": "...", "found": true, "x": 130.1, "y": 450.2,
               "conf": 0.91, "crop_px": 214}, ...],
     "errors": ["id : message", ...]}

Methode, celle qui a ete mesuree sur le jeu d'apprentissage (modeles\\evaluer.py) :
recadrage carre de 4 x S x px_par_metre pixels (borne a 96-640) autour de la
position estimee du marqueur, ramene a 160 x 160 (gris hors image), modele
YOLO11n, detection la plus proche du centre ponderee par la confiance. S est
inconnue : trois tailles de cible (0,3 / 0,5 / 0,8 m). Si rien n'est trouve,
on cherche dans 4 recadrages decales de 0,4 fois la taille du recadrage. Une
seconde passe recentre le recadrage sur la detection pour affiner la position.
"""

import argparse
import json
import math
import os
import sys
import time

OUT = 160
CROP_MIN, CROP_MAX = 96, 640
FLOOR_CONF = 0.05
PROXIMITY_PX = 40.0
GRID_OFFSET = 0.4
GRID_STEPS = ((1, 0), (-1, 0), (0, 1), (0, -1))


def read_image(path):
    import cv2
    import numpy as np
    return cv2.imdecode(np.fromfile(path, np.uint8),
                        cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)


def crop_size(ppm, scale_m):
    return int(round(min(CROP_MAX, max(CROP_MIN, 4.0 * scale_m * ppm))))


def crop(image, cx, cy, size):
    """Carre de `size` px centre en (cx, cy), gris hors image, ramene a 160 x 160.

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
    interpolation = cv2.INTER_AREA if size > OUT else cv2.INTER_CUBIC
    return cv2.resize(tile, (OUT, OUT), interpolation=interpolation), x0, y0


class Detector(object):
    def __init__(self, weights, device):
        from ultralytics import YOLO
        self.model = YOLO(weights)
        self.device = device

    def predict(self, tiles):
        """Une liste de tableaux (N, 5) = cx, cy, w, h, conf (px de la tuile), par tuile."""
        import numpy as np
        out = []
        for result in self.model.predict(tiles, imgsz=OUT, conf=FLOOR_CONF, iou=0.7,
                                         max_det=10, device=self.device, verbose=False):
            if result.boxes is None or len(result.boxes) == 0:
                out.append(np.zeros((0, 5), np.float32))
                continue
            xywh = result.boxes.xywh.cpu().numpy()
            conf = result.boxes.conf.cpu().numpy()[:, None]
            out.append(np.concatenate([xywh, conf], 1))
        return out


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


def detect_item(detector, image, item, scales, conf):
    """Resultat d'une photo : dict {found, x, y, conf, crop_px}."""
    ppm = float(item["px_per_m"])
    cx, cy = float(item["x_est"]), float(item["y_est"])
    sizes = sorted(set(crop_size(ppm, s) for s in scales))

    def run(centres, nearest):
        tiles, metas = [], []
        for (x, y, size) in centres:
            made = crop(image, x, y, size)
            if made is not None:
                tiles.append(made[0])
                metas.append((made[1], made[2], size))
        if not tiles:
            return None
        return best_of(detector.predict(tiles), metas, nearest)

    found = run([(cx, cy, s) for s in sizes], True)
    if (found is None or found[2] < conf) and item.get("rescue", True):
        around = [(cx, cy, s) for s in sizes]
        for s in sizes:
            for dx, dy in GRID_STEPS:
                around.append((cx + dx * GRID_OFFSET * s, cy + dy * GRID_OFFSET * s, s))
        wider = run(around, False)
        if wider is not None and (found is None or wider[2] > found[2]):
            found = wider
    if found is not None and found[2] >= conf:
        refined = run([(found[0], found[1], found[3])], True)
        if refined is not None and refined[2] >= conf:
            found = refined
    if found is None or found[2] < conf:
        return {"found": False, "conf": 0.0 if found is None else round(found[2], 4)}
    return {"found": True, "x": round(found[0], 2), "y": round(found[1], 2),
            "conf": round(found[2], 4), "crop_px": int(found[3])}


def pick_device(wanted):
    if wanted and wanted != "auto":
        return wanted
    try:
        import torch
        return 0 if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def run(work_path, out_path):
    started = time.time()
    with open(work_path, "r", encoding="utf-8") as handle:
        work = json.load(handle)
    scales = [float(s) for s in work.get("scales_m") or (0.3, 0.5, 0.8)]
    conf = float(work.get("conf", 0.25))
    device = pick_device(work.get("device", "auto"))
    detector = Detector(work["weights"], device)
    hits, errors = [], []
    last_path, image = None, None     # les items d'une meme photo se suivent : un seul decodage
    for item in work.get("items", []):
        try:
            if item["image_path"] != last_path:
                last_path, image = item["image_path"], None
                image = read_image(last_path)
            if image is None:
                raise ValueError("image illisible")
            row = detect_item(detector, image, item, scales, conf)
            row["id"] = item["id"]
            hits.append(row)
        except Exception as exc:
            errors.append("{} : {!r}".format(item.get("id"), exc))
    result = {"version": 1, "seconds": round(time.time() - started, 2),
              "device": str(device), "hits": hits, "errors": errors}
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False)
    os.replace(tmp, out_path)


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
