"""External runner of the AIGCP detection (checker or spray).

Launched by FastGCP in a separate Python, never in the Metashape one:

    python aigcp_runner.py --work work.json --out hits.json

Two engines, chosen from the requested weights (`weights`, a .pt file):
  - ONNX Runtime, if there is a .onnx file next to the .pt and onnxruntime is installed:
    light (about ten MB in all, no torch), starts in a fraction of a second;
  - ultralytics (torch) otherwise: the original engine, also used by the training.
Both give the same detections (within 0.2 px and 0.01 of confidence).

`work.json` (written by aigcp.py):
    {"version": 1, "type": "checker", "weights": "...pt", "conf": 0.25,
     "scales_m": [0.3, 0.5, 0.8], "device": "auto",
     "engine": "onnx" (light) | "torch" (precise) | "auto",
     "items": [{"id": "...", "image_path": "...", "x_est": 123.4,
                "y_est": 456.7, "px_per_m": 210.0, "rescue": true}, ...],
     "crops_dir": "folder of the review thumbnails (optional)"}
`rescue` (default true): allows the wider search when nothing is found at the centre;
false when the expected position is known to within a few centimetres.

`hits.json` (written here, at the end of the work):
    {"version": 1, "seconds": 3.2, "device": "cpu",
     "hits": [{"id": "...", "found": true, "x": 130.1, "y": 450.2,
               "conf": 0.91, "crop_px": 214,
               "crop": {"big": "0_12.jpg", "win": 214, "x0": 23, "y0": 343}}, ...],
     "errors": ["id: message", ...]}

Method, the one measured on the training set (its evaluation script):
square crop of 4 x S x px_per_metre pixels (bounded to 96-640) around the
estimated position of the marker, resized to 160 x 160 (grey outside the image), YOLO11n
model, detection closest to the centre weighted by the confidence. S is
unknown: three target sizes (0.3 / 0.5 / 0.8 m). If nothing is found,
4 crops shifted by 0.4 times the crop size are searched. A second
pass recentres the crop on the detection to refine the position.

Speed: the photos are decoded ahead of time by several threads (the JPEG decoding of
21 Mpixels takes ~100 ms and used to dominate), and all the tiles of the markers of one photo
go through the model in a single batch per step (first pass, wider search, refinement)
instead of one call per marker. The result is identical to that of the former loop.
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
BATCH = 64                    # tiles per model call
MAX_WORKERS = 6               # decoding threads


def read_image(path):
    import cv2
    import numpy as np
    return cv2.imdecode(np.fromfile(path, np.uint8),
                        cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)


def crop_size(ppm, scale_m):
    return int(round(min(CROP_MAX, max(CROP_MIN, 4.0 * scale_m * ppm))))


def crop(image, cx, cy, size, out=OUT):
    """Square of `size` px centred on (cx, cy), grey outside the image, resized to `out` x `out`.

    Returns (tile, x0, y0) or None if the square is entirely outside the image.
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
# Engines
# --------------------------------------------------------------------------

class Detector(object):
    """ultralytics (torch) engine: the original one, also used by the training."""

    def __init__(self, weights, device):
        try:
            from ultralytics import YOLO
        except ImportError:
            raise RuntimeError("no file {} and ultralytics is not installed: run the training again "
                               "(training installer) or restore the base model".format(
                                   os.path.basename(onnx_path(weights))))
        self.model = YOLO(weights)
        self.device = device

    def predict(self, tiles):
        """A list of arrays (N, 5) = cx, cy, w, h, conf (tile px), one per tile."""
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
    """Non-maximum suppression (greedy, boxes as x1 y1 x2 y2). Returns the kept indices."""
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
    """Raw output of an exported YOLO model (4 + classes, anchors) -> (N, 5) cx, cy, w, h, conf."""
    import numpy as np
    pred = np.asarray(row, np.float32).T                # (anchors, 4 + classes)
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
    """ONNX Runtime engine on the CPU: no torch or ultralytics.

    Measured on a real run (1171 markers, 182 photos of 21 Mpixels): faster than torch
    on an RTX 4070 (24 s against 47 s with the former loop). DirectML (graphics card) was
    tried: 38 s, slower, because these 160 x 160 tiles are too small to amortise each call.
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
    """The requested engine: "onnx" (ONNX Runtime, light), "torch" (ultralytics, the precise one, graphics card
    if present) or "auto" (ONNX if possible and on the CPU, otherwise ultralytics).

    An explicitly chosen engine is never silently replaced: if it is missing, the error says so."""
    path = onnx_path(weights)
    if engine == "onnx":
        if not os.path.isfile(path):
            raise RuntimeError("the light engine needs the file {} (missing): reinstall the model or "
                               "choose the precise engine".format(os.path.basename(path)))
        return OnnxDetector(path)
    if engine == "torch":
        return Detector(weights, pick_device(device))
    if device in (None, "auto", "cpu") and os.path.isfile(path):
        try:
            return OnnxDetector(path)
        except Exception as exc:
            sys.stderr.write("aigcp_runner: ONNX unavailable ({!r}), falling back to ultralytics\n".format(exc))
    return Detector(weights, pick_device(device))


# --------------------------------------------------------------------------
# Detection
# --------------------------------------------------------------------------

def best_of(preds, metas, nearest):
    """Best detection among tiles: (x, y, conf, size) in photo px, or None.

    `nearest`: score = confidence x exp(-(d / 40 px)^2 / 2), d = distance to the centre
    of the tile (hence to the estimate); otherwise, the highest confidence.
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
    """One step for several markers: all the tiles go through the model in a single batch.

    `requests`: list of (key, centres [(x, y, size)], nearest). Returns {key: best or None}.
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
    """Results of all the markers of a photo: list of dicts {found, x, y, conf, crop_px}.

    Same method as `detect_item`, but each step is grouped for the whole photo.
    """
    count = len(items)
    plans = []
    for item in items:
        ppm = float(item["px_per_m"])
        cx, cy = float(item["x_est"]), float(item["y_est"])
        plans.append((cx, cy, sorted(set(crop_size(ppm, s) for s in scales)), bool(item.get("rescue", True))))

    # 1. first pass: the target sizes around the estimate
    first = batch_best(detector, image, [
        (i, [(cx, cy, s) for s in sizes], True) for i, (cx, cy, sizes, _r) in enumerate(plans)])
    found = [first[i] for i in range(count)]

    # 2. wider search for those with nothing reliable
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

    # 3. refinement: the crop is recentred on the detection
    requests = [(i, [(found[i][0], found[i][1], found[i][3])], True)
                for i in range(count) if found[i] is not None and found[i][2] >= conf]
    if requests:
        refined = batch_best(detector, image, requests)
        for i, _c, _n in requests:
            if refined[i] is not None and refined[i][2] >= conf:
                found[i] = refined[i]

    return [_row(found[i], conf) for i in range(count)]


def detect_item(detector, image, item, scales, conf):
    """Result for one photo: dict {found, x, y, conf, crop_px}."""
    return detect_items(detector, image, [item], scales, conf)[0]


BIG = 960


def save_review_crops(image, row, crops_dir, name):
    """Crop around the point found, for the FastGCP Review panel.

    The window is the model one (`crop_px`, at least 96 px): the interface cuts the
    central half out of it at normal zoom, and the zoom slider shows more or less
    context. Returns {big, win, x0, y0} (file name in `crops_dir`, win = side
    of the window in photo pixels, x0 y0 = its corner), or None.
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
    """progress.json next to hits.json: the FastGCP progress bar re-reads it."""
    path = os.path.join(os.path.dirname(os.path.abspath(out_path)), "progress.json")
    try:
        with open(path + ".tmp", "w", encoding="utf-8") as handle:
            json.dump({"done": done, "total": total}, handle)
        os.replace(path + ".tmp", path)
    except OSError:
        pass            # being read on the FastGCP side: the next write will go through


def photo_groups(items):
    """[(path, [item indices])]: the items of one photo follow each other, a single decoding."""
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
        cv2.setNumThreads(1)           # the parallelism comes from the decoding threads
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
                    raise ValueError(problem or "unreadable image")
                results = detect_items(detector, image, group_items, scales, conf)
                for index, item, row in zip(indices, group_items, results):
                    row["id"] = item["id"]
                    if crops_dir and row["found"]:
                        row["crop"] = save_review_crops(image, row, crops_dir, safe_name(item["id"]))
                    rows[index] = row
            except Exception as exc:
                for item in group_items:
                    errors.append("{}: {!r}".format(item.get("id"), exc))
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
    """(image, problem): never raises, so that an unreadable file does not stop the detection."""
    try:
        image = read_image(path)
        return (image, None) if image is not None else (None, "unreadable image")
    except Exception as exc:
        return None, "unreadable image ({!r})".format(exc)


def main(argv=None):
    parser = argparse.ArgumentParser(description="AIGCP detection for FastGCP")
    parser.add_argument("--work", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        run(args.work, args.out)
    except Exception as exc:
        sys.stderr.write("aigcp_runner: {!r}\n".format(exc))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
