"""Optional recording of the manual pins (AIGCP training set).

Active only if the session option `collect_dataset` is ticked. Each successful
manual pin appends a JSON line to the file:

    <config.settings_dir()>/dataset/pins.jsonl

Format: one JSON object per line, UTF-8, appended at the end of the file. No image
is copied and nothing is cropped: only references to the photos are kept.

Keys of a line (schema 1):
    schema      int    format version (1)
    ts          str    local ISO 8601 date, to the second
    chunk       str    chunk label
    marker      str    marker (GCP) label
    camera      str    photo label
    image_path  str    path of the source image as Metashape knows it
    image_w     int    sensor width in pixels (0 if unknown)
    image_h     int    sensor height in pixels (0 if unknown)
    x, y        float  placed position, in pixels, origin at the top left
                       (after snapping to the target centre, see snap.py)
    x_click, y_click  float  raw click of the operator, before snapping (optional)
    source      str    "manual" for a mouse pin

A training script reads the file line by line (json.loads), ignores
the lines whose `schema` is unknown, and reopens the image through `image_path`.
"""

import datetime
import json
import os

from ... import config, journal

SCHEMA = 1
FILE_NAME = "pins.jsonl"


def dataset_dir():
    return os.path.join(config.settings_dir(), "dataset")


def dataset_path():
    return os.path.join(dataset_dir(), FILE_NAME)


def count():
    """Number of recorded pins (0 if the file does not exist)."""
    try:
        total = 0
        with open(dataset_path(), "rb") as handle:
            for line in handle:
                if line.strip():
                    total += 1
        return total
    except Exception:
        return 0


def record_pin(state, record):
    """Adds a pin to the training set.

    `record`: dict with chunk, marker, camera, image_path, image_w, image_h,
    x, y and possibly source. Returns the dict written (with schema and ts),
    or None if the option is off or if the write fails. Never raises.
    """
    try:
        if not state.option("collect_dataset"):
            return None
        row = {
            "schema": SCHEMA,
            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            "chunk": record.get("chunk", ""),
            "marker": record.get("marker", ""),
            "camera": record.get("camera", ""),
            "image_path": record.get("image_path", ""),
            "image_w": int(record.get("image_w", 0) or 0),
            "image_h": int(record.get("image_h", 0) or 0),
            "x": round(float(record["x"]), 2),
            "y": round(float(record["y"]), 2),
            "source": record.get("source", "manual"),
        }
        # Raw click of the operator, before snapping to the target (optional).
        if record.get("x_click") is not None and record.get("y_click") is not None:
            row["x_click"] = round(float(record["x_click"]), 2)
            row["y_click"] = round(float(record["y_click"]), 2)
        os.makedirs(dataset_dir(), exist_ok=True)
        with open(dataset_path(), "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row
    except Exception as exc:
        try:
            journal.write("training set: write failed ({!r})".format(exc))
        except Exception:
            pass
        return None
