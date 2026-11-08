"""Fine-tuning of the AIGCP models on the operator feedback.

Launched by training.py in a separate Python (the one with torch and ultralytics),
never in the Metashape one:

    python -I aigcp_train.py --work train.json --out result.json

`train.json` (written by training.py):
    {"version": 1, "types": ["spray"], "events": "events.jsonl",
     "base_weights": {"spray": "...\\spray.pt"}, "out_dir": "...", "work_dir": "...",
     "epochs": 30, "device": "auto", "min_positives": 30, "replay_dir": null}
`events.jsonl`: the latest review events (see review.py), one per line:
refined / accepted = correct point (x, y), rejected = false detection at (ai_x, ai_y).

Method (that of the original training set): square crop of
crop_px (96-640 px) around the point, resized to 160 x 160, one class, 0.25 x 0.25 box
(the target is ~40 px). Each positive gives one centred crop and two crops shifted by
+-28 px with a scale jitter of x0.85 to x1.2; each rejected one gives an unlabelled
crop (hard negative); a few random backgrounds from the same photos.
At most 25 examples per target (refinements first) so that a heavily
pinned marker does not dominate. Train / val split PER PHYSICAL TARGET (chunk + marker):
a target is never on both sides. Training starts from the base weights, frozen backbone,
low learning rate, few epochs. The two types are never mixed.

`result.json` (written at the end of the work, `progress.json` next to it during the work):
    {"ok": true, "message": "", "seconds": 123.0, "device": "cuda:0 (...)",
     "types": {"spray": {"ok": true, "samples": {...}, "epochs": 30,
                         "metrics": {"base": {map50, precision, recall},
                                     "new": {map50, precision, recall}},
                         "warning": null}}}
A type without enough readable positives: {"ok": false, "skipped": "text"}.
The weights go to <out_dir>/<type>.pt (last state, not the "best" one: the
validation is too small to choose an epoch).
"""

import argparse
import hashlib
import importlib.util
import json
import math
import os
import random
import shutil
import sys
import time

OUT = 160
CROP_MIN, CROP_MAX = 96, 640
BOX = 0.25
SHIFT_PX = 28.0
JITTER = (0.85, 1.2)
VAL_FRACTION = 0.15
BG_PER_POSITIVE = 0.25
MAX_PER_TARGET = 25
REPLAY_SHARE = 0.3
WARN_MARGIN = 0.05
SEED = 0


def load_runner():
    """aigcp_runner.py (same folder): under `python -I` the script folder is not on sys.path."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aigcp_runner.py")
    spec = importlib.util.spec_from_file_location("aigcp_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


class Progress(object):
    def __init__(self, out_path, kinds_total):
        self.path = os.path.join(os.path.dirname(os.path.abspath(out_path)), "progress.json")
        self.state = {"kinds_total": kinds_total, "kind_index": 0, "type": "", "stage": "prepare",
                      "epoch": 0, "epochs": 1}

    def update(self, **fields):
        self.state.update(fields)
        try:
            write_json(self.path, self.state)
        except OSError:
            pass            # being read on the FastGCP side: the next write will go through


# --------------------------------------------------------------------------
# Events -> examples
# --------------------------------------------------------------------------

def read_events(path, kind):
    """(positives, negatives) of the type: lists of usable event dicts."""
    positives, negatives = [], []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("type") != kind or not row.get("image_path"):
                continue
            action = row.get("action")
            if action in ("refined", "accepted") and "x" in row and "y" in row:
                positives.append(row)
            elif action == "rejected":
                negatives.append(row)
    return positives, negatives


def target_of(row):
    return "{}|{}".format(row.get("chunk"), row.get("marker"))


def side_of(row):
    return int(min(CROP_MAX, max(CROP_MIN, round(float(row.get("crop_px") or CROP_MIN)))))


def cap_per_target(rows, rng):
    """At most MAX_PER_TARGET rows per target; refinements come first."""
    groups = {}
    for row in rows:
        groups.setdefault(target_of(row), []).append(row)
    kept = []
    for key in sorted(groups):
        group = groups[key]
        rng.shuffle(group)
        group.sort(key=lambda r: 0 if r.get("action") == "refined" else 1)
        kept.extend(group[:MAX_PER_TARGET])
    return kept


def split_targets(targets, rng_seed=SEED):
    """Set of validation targets: ~15 %, at least one if there are 5 or more."""
    ordered = sorted(targets, key=lambda t: hashlib.sha1("{}{}".format(rng_seed, t).encode("utf-8")).hexdigest())
    if len(ordered) < 5:
        return set()
    return set(ordered[:max(1, int(math.ceil(VAL_FRACTION * len(ordered))))])


def tile(runner, image, cx, cy, size, target=None, box_size=None):
    """(160 x 160 tile, YOLO label line or None); None if the target falls outside the frame."""
    made = runner.crop(image, cx, cy, int(size))
    if made is None:
        return None
    tile_img, x0, y0 = made
    if target is None:
        return tile_img, None
    u = (target[0] - x0) / float(size)
    v = (target[1] - y0) / float(size)
    if not (0.08 < u < 0.92 and 0.08 < v < 0.92):
        return None
    side = box_size / float(size)
    return tile_img, "0 {:.6f} {:.6f} {:.6f} {:.6f}".format(u, v, side, side)


def save_sample(cv2, np, folder, name, image_array, label):
    ok, buffer = cv2.imencode(".jpg", image_array, [cv2.IMWRITE_JPEG_QUALITY, 95])
    if not ok:
        return False
    buffer.tofile(os.path.join(folder, "images", name + ".jpg"))
    with open(os.path.join(folder, "labels", name + ".txt"), "w", encoding="utf-8") as handle:
        handle.write((label + "\n") if label else "")
    return True


def build_dataset(runner, kind, events_path, work_dir, replay_dir=None):
    """Writes the YOLO set of the type into work_dir/<kind>. Returns (yaml, statistics)."""
    import cv2
    import numpy as np
    rng = random.Random(SEED)
    positives, negatives = read_events(events_path, kind)
    positives = cap_per_target(positives, rng)
    negatives = cap_per_target(negatives, rng)
    rows = positives + negatives
    targets = set(target_of(r) for r in rows)
    val_targets = split_targets(targets)
    root = os.path.join(work_dir, kind)
    shutil.rmtree(root, ignore_errors=True)
    for split in ("train", "val"):
        for sub in ("images", "labels"):
            os.makedirs(os.path.join(root, split, sub))
    stats = {"positives": 0, "negatives": 0, "targets": len(targets), "targets_val": len(val_targets),
             "train": 0, "val": 0, "unreadable": 0, "val_is_train": False, "replay": 0}
    by_image = {}
    for row in rows:
        by_image.setdefault(row["image_path"], []).append(row)
    counter = 0
    for path in sorted(by_image):
        try:
            image = runner.read_image(path)
        except Exception:
            image = None
        if image is None:
            stats["unreadable"] += len(by_image[path])
            continue
        photo_rows = by_image[path]
        height, width = image.shape[:2]
        for row in photo_rows:
            split = "val" if target_of(row) in val_targets else "train"
            folder = os.path.join(root, split)
            size = side_of(row)
            made = []
            if row.get("action") == "rejected":
                variants = [(float(row["ai_x"]), float(row["ai_y"]), size)]
                if split == "train":
                    variants.append((float(row["ai_x"]) + rng.uniform(-SHIFT_PX, SHIFT_PX) * size / OUT,
                                     float(row["ai_y"]) + rng.uniform(-SHIFT_PX, SHIFT_PX) * size / OUT,
                                     size))
                for cx, cy, s in variants:
                    out = tile(runner, image, cx, cy, s)
                    if out is not None:
                        made.append((out[0], None))
                        stats["negatives"] += 1
            else:
                x, y = float(row["x"]), float(row["y"])
                variants = [(x, y, size)]
                if split == "train":
                    for _ in range(2):
                        s2 = int(min(CROP_MAX, max(CROP_MIN, round(size * rng.uniform(*JITTER)))))
                        variants.append((x + rng.uniform(-SHIFT_PX, SHIFT_PX) * s2 / OUT,
                                         y + rng.uniform(-SHIFT_PX, SHIFT_PX) * s2 / OUT, s2))
                for cx, cy, s in variants:
                    out = tile(runner, image, cx, cy, s, target=(x, y), box_size=BOX * size)
                    if out is not None:
                        made.append(out)
                        stats["positives"] += 1
            for tile_img, label in made:
                counter += 1
                if save_sample(cv2, np, folder, "{}_{:05d}".format(kind, counter), tile_img, label):
                    stats[split] += 1
        # random backgrounds: far from any known point of this photo
        sizes = sorted(side_of(r) for r in photo_rows)
        median = sizes[len(sizes) // 2]
        known = [(float(r.get("x", r.get("ai_x"))), float(r.get("y", r.get("ai_y")))) for r in photo_rows]
        wanted = int(math.ceil(BG_PER_POSITIVE * len(photo_rows)))
        split = "val" if target_of(photo_rows[0]) in val_targets else "train"
        for _ in range(wanted):
            for _try in range(40):
                cx = rng.uniform(median / 2.0, width - median / 2.0) if width > median else None
                cy = rng.uniform(median / 2.0, height - median / 2.0) if height > median else None
                if cx is None or cy is None:
                    break
                if all(math.hypot(cx - kx, cy - ky) > 1.5 * median for kx, ky in known):
                    out = tile(runner, image, cx, cy, median)
                    if out is not None:
                        counter += 1
                        if save_sample(cv2, np, os.path.join(root, split), "{}_{:05d}".format(kind, counter),
                                       out[0], None):
                            stats[split] += 1
                            stats["negatives"] += 1
                    break
    if replay_dir and stats["train"]:
        stats["replay"] = add_replay(replay_dir, os.path.join(root, "train"),
                                     int(round(stats["train"] * REPLAY_SHARE / (1.0 - REPLAY_SHARE))), rng)
    if stats["val"] == 0:
        # too few targets to reserve a validation set: measure on the train set (not independent)
        for sub in ("images", "labels"):
            for name in os.listdir(os.path.join(root, "train", sub)):
                shutil.copy2(os.path.join(root, "train", sub, name), os.path.join(root, "val", sub, name))
        stats["val"] = stats["train"]
        stats["val_is_train"] = True
    yaml = os.path.join(root, "data.yaml").replace("\\", "/")
    with open(yaml, "w", encoding="utf-8") as handle:
        handle.write("path: {}\ntrain: train/images\nval: val/images\nnames:\n  0: gcp_{}\n".format(
            root.replace("\\", "/"), kind))
    return yaml, stats


def add_replay(source, train_folder, count, rng):
    """Copies `count` examples of the original set (source/train/{images,labels}) into the train set."""
    images = os.path.join(source, "train", "images")
    labels = os.path.join(source, "train", "labels")
    try:
        names = sorted(os.listdir(images))
    except OSError:
        return 0
    rng.shuffle(names)
    done = 0
    for name in names[:count]:
        stem = os.path.splitext(name)[0]
        shutil.copy2(os.path.join(images, name), os.path.join(train_folder, "images", "rp_" + name))
        label = os.path.join(labels, stem + ".txt")
        target = os.path.join(train_folder, "labels", "rp_" + stem + ".txt")
        if os.path.isfile(label):
            shutil.copy2(label, target)
        else:
            open(target, "w").close()
        done += 1
    return done


# --------------------------------------------------------------------------
# Measurement and training
# --------------------------------------------------------------------------

def evaluate(YOLO, weights, yaml, device):
    """mAP50, precision, recall of the model on the val split of the set."""
    model = YOLO(weights)
    result = model.val(data=yaml, imgsz=OUT, batch=32, device=device, plots=False, verbose=False,
                       workers=0, split="val")
    return {"map50": round(float(result.box.map50), 4), "precision": round(float(result.box.mp), 4),
            "recall": round(float(result.box.mr), 4)}


def train_kind(runner, YOLO, kind, work, device, progress, index):
    started = time.time()
    epochs = int(work.get("epochs") or 30)
    base = work["base_weights"][kind]
    progress.update(type=kind, kind_index=index, stage="prepare", epoch=0, epochs=epochs)
    yaml, stats = build_dataset(runner, kind, work["events"], work["work_dir"], work.get("replay_dir"))
    need = int(work.get("min_positives") or 1)
    own_positives = stats["positives"]
    if own_positives < need:
        return {"ok": False, "skipped": "only {} readable positive examples (need {})".format(
            own_positives, need), "samples": stats}
    progress.update(stage="base eval")
    before = evaluate(YOLO, base, yaml, device)
    batch = max(4, min(32, stats["train"] // 4))
    model = YOLO(base)

    def on_epoch(trainer):
        progress.update(stage="train", epoch=int(trainer.epoch) + 1, epochs=int(trainer.epochs))

    model.add_callback("on_fit_epoch_end", on_epoch)
    progress.update(stage="train", epoch=0, epochs=epochs)
    runs = os.path.join(work["work_dir"], "runs")
    model.train(
        data=yaml, epochs=epochs, imgsz=OUT, batch=batch, device=device, workers=0 if os.name == "nt" else 2,
        project=runs, name=kind, exist_ok=True, patience=10, plots=False, amp=False, cache=False,
        seed=SEED, deterministic=False, verbose=False, freeze=10, optimizer="SGD", lr0=0.001, lrf=0.1,
        warmup_epochs=1, close_mosaic=min(5, max(0, epochs // 5)),
        mosaic=0.3, scale=0.3, translate=0.1, degrees=0.0, shear=0.0, perspective=0.0,
        fliplr=0.5, flipud=0.5, hsv_h=0.01, hsv_s=0.5, hsv_v=0.4, erasing=0.0, mixup=0.0)
    last = os.path.join(runs, kind, "weights", "last.pt")
    if not os.path.isfile(last):
        raise RuntimeError("no weights produced for " + kind)
    os.makedirs(work["out_dir"], exist_ok=True)
    shutil.copy2(last, os.path.join(work["out_dir"], kind + ".pt"))
    progress.update(stage="eval")
    after = evaluate(YOLO, os.path.join(work["out_dir"], kind + ".pt"), yaml, device)
    # The detection engine (ONNX Runtime, no torch) reads this file: it is exported next to the weights.
    onnx_error = None
    try:
        YOLO(os.path.join(work["out_dir"], kind + ".pt")).export(
            format="onnx", imgsz=OUT, dynamic=True, simplify=True, opset=17)
    except Exception as exc:
        onnx_error = repr(exc)
    warning = None
    if after["map50"] < before["map50"] - WARN_MARGIN:
        warning = "{}: the new model scores lower than the base on your validation set ({:.3f} vs {:.3f})".format(
            kind, after["map50"], before["map50"])
    return {"ok": True, "samples": stats, "epochs": epochs, "batch": batch, "onnx_error": onnx_error,
            "metrics": {"base": before, "new": after}, "warning": warning,
            "val_independent": not stats["val_is_train"], "seconds": round(time.time() - started, 1)}


def run(work_path, out_path):
    started = time.time()
    with open(work_path, "r", encoding="utf-8") as handle:
        work = json.load(handle)
    os.environ.setdefault("YOLO_OFFLINE", "1")
    kinds = list(work["types"])
    progress = Progress(out_path, len(kinds))
    runner = load_runner()
    from ultralytics import YOLO
    device = runner.pick_device(work.get("device", "auto"))
    label = "cpu"
    if device != "cpu":
        try:
            import torch
            label = "cuda:{} ({})".format(device, torch.cuda.get_device_name(device))
        except Exception:
            label = "cuda:{}".format(device)
    result = {"ok": False, "message": "", "device": label, "types": {}}
    try:
        for index, kind in enumerate(kinds):
            try:
                result["types"][kind] = train_kind(runner, YOLO, kind, work, device, progress, index)
            except Exception as exc:
                result["types"][kind] = {"ok": False, "skipped": "failed: {!r}".format(exc)}
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass
        done = [k for k, v in result["types"].items() if v.get("ok")]
        result["ok"] = bool(done)
        if not done:
            result["message"] = "; ".join("{}: {}".format(k, v.get("skipped")) for k, v in result["types"].items())
    finally:
        result["seconds"] = round(time.time() - started, 1)
        shutil.rmtree(work.get("work_dir") or "", ignore_errors=True)
        write_json(out_path, result)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Fine-tuning of the AIGCP models for FastGCP")
    parser.add_argument("--work", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        run(args.work, args.out)
    except Exception as exc:
        sys.stderr.write("aigcp_train: {!r}\n".format(exc))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
