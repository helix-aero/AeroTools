"""Review of the picks proposed by the AI (no Qt).

After a detection, each proposal placed in the project becomes a review entry:
a thumbnail cropped around the point, which the operator can keep, remove
or refine. The window is in review_dialog.py; here, the state and what is done with it.

Each decision is applied to the project right away and recorded, as an
event, in a file meant for future retraining:

    <training.storage_dir()>/dataset/aigcp_feedback.jsonl
    (by default the workstation's settings folder; nothing is written if the
    "use my manual refinements" box in the settings is unticked)

One line per event (UTF-8, appended at the end of the file). A training script
keeps the LAST event of each (run, marker, photo). Keys (schema 1):
    schema, ts, run          format version, local date, detection run
    type                     "checker" or "spray" (the two models are never mixed)
    chunk, marker, camera    where
    image_path, image_w, image_h   the source photo (no image is copied)
    ai_x, ai_y, conf         what the AI had proposed (photo px, origin at top left)
    crop_px, px_per_m        size of the model window and ground scale
    action                   "rejected" : false detection (negative example)
                             "restored" : the operator took back their removal
                             "refined"  : position corrected by hand (x, y)
                             "accepted" : proposal validated as is (x, y = ai_x, ai_y)
    x, y                     final position when it exists (refined, accepted)
    source                   "aigcp_review"
"""

import datetime
import json
import os

from ... import config, journal, msapi
from . import history, training

SCHEMA = 1
FILE_NAME = "aigcp_feedback.jsonl"
KEPT = "kept"
REMOVED = "removed"
REFINED = "refined"
ACCEPTED = "accepted"


def feedback_path():
    return training.feedback_path()


class Review(object):
    """The proposals of a detection: state of each entry and grouping by marker."""

    def __init__(self, kind, run_id, entries):
        self.kind = kind
        self.run_id = run_id
        self.entries = list(entries)

    def labels(self):
        """Marker labels, most doubtful first: the weakest detection of each one
        decides (ties keep detection order)."""
        weakest, order = {}, []
        for entry in self.entries:
            label = entry["label"]
            if label not in weakest:
                order.append(label)
                weakest[label] = entry["conf"]
            else:
                weakest[label] = min(weakest[label], entry["conf"])
        return sorted(order, key=lambda label: weakest[label])

    def weakest(self, label):
        """Lowest confidence among the detections of this marker (1.0 if it has none)."""
        return min([entry["conf"] for entry in self.entries if entry["label"] == label] or [1.0])

    def entries_for(self, label):
        """Detections of a marker, least certain first."""
        return sorted((entry for entry in self.entries if entry["label"] == label), key=lambda entry: entry["conf"])

    def counts(self):
        out = {KEPT: 0, REMOVED: 0, REFINED: 0, ACCEPTED: 0}
        for entry in self.entries:
            out[entry["state"]] += 1
        return out


def make_entry(item, hit, previous, crops_dir):
    """Review entry for a placed proposal. `hit["crop"]` comes from the runner (may be missing)."""
    crop = hit.get("crop") or {}

    def path(key):
        name = crop.get(key)
        return os.path.join(crops_dir, name) if name else None

    return {
        "marker": item["marker"], "label": item["label"],
        "camera": item["camera"], "camera_label": msapi.camera_label(item["camera"]),
        "image_path": item["image_path"],
        "ai_x": float(hit["x"]), "ai_y": float(hit["y"]),
        "x": float(hit["x"]), "y": float(hit["y"]),
        "conf": float(hit["conf"]), "crop_px": float(hit.get("crop_px") or 0),
        "px_per_m": float(item["px_per_m"]), "previous": previous,
        "big": path("big"),
        "win": float(crop.get("win") or 0), "x0": float(crop.get("x0") or 0),
        "y0": float(crop.get("y0") or 0),
        "state": KEPT,
    }


# --------------------------------------------------------------------------
# Conversion between the thumbnail and the photo
# --------------------------------------------------------------------------

def view_rect(entry, zoom=1.0):
    """Square (left, top, side), in photo position, shown at this zoom around the AI point.

    At zoom 1 it is half the window recorded by the runner; zoom 0.5 shows
    the whole window, zoom 4 an eighth of its side.
    """
    side = entry["win"] / 2.0 / zoom
    return entry["ai_x"] - side / 2.0, entry["ai_y"] - side / 2.0, side


def to_view(entry, x, y, size, zoom=1.0):
    """Photo position (x, y), in pixels of a square thumbnail of `size` px at the given zoom."""
    left, top, side = view_rect(entry, zoom)
    scale = size / side
    return (x - left) * scale, (y - top) * scale


def to_source(entry, u, v, size, zoom=1.0):
    """Pixel (u, v) of a square thumbnail of `size` px at the given zoom, as a photo position."""
    left, top, side = view_rect(entry, zoom)
    scale = side / float(size)
    return left + u * scale, top + v * scale


def source_rect(entry, zoom, image_width):
    """The square of view_rect, in pixels of the saved cropped image (width `image_width`)."""
    left, top, side = view_rect(entry, zoom)
    scale = image_width / entry["win"]
    return (left - entry["x0"]) * scale, (top - entry["y0"]) * scale, side * scale


def has_crop(entry):
    return bool(entry.get("big") and entry["win"] > 0)


# --------------------------------------------------------------------------
# Feedback for training
# --------------------------------------------------------------------------

def record(review, entry, action, x=None, y=None):
    """Appends an event to the feedback file. Never raises.

    Does nothing (returns None) when the operator has unticked the collection."""
    try:
        if not training.collect_enabled():
            return None
        width, height = msapi.camera_size(entry["camera"])
        row = {
            "schema": SCHEMA,
            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            "run": review.run_id, "type": review.kind,
            "chunk": msapi.chunk_label(msapi.chunk()),
            "marker": entry["label"], "camera": entry["camera_label"],
            "image_path": entry["image_path"], "image_w": width, "image_h": height,
            "ai_x": round(entry["ai_x"], 2), "ai_y": round(entry["ai_y"], 2),
            "conf": round(entry["conf"], 4),
            "crop_px": round(entry["crop_px"], 1), "px_per_m": round(entry["px_per_m"], 3),
            "action": action, "source": "aigcp_review",
        }
        if x is not None and y is not None:
            row["x"], row["y"] = round(float(x), 2), round(float(y), 2)
        os.makedirs(os.path.dirname(feedback_path()), exist_ok=True)
        with open(feedback_path(), "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row
    except Exception as exc:
        try:
            journal.write("AI review: feedback not saved ({!r})".format(exc))
        except Exception:
            pass
        return None


# --------------------------------------------------------------------------
# Operator decisions
# --------------------------------------------------------------------------

def _forget(state, entry):
    """Removes the entry from the proposals that "Clear" must undo."""
    _forget_many(state, [entry])


def _forget_many(state, entries):
    """Removes these entries from the proposals that "Clear" must undo, in one pass: the Metashape reads are one per entry and one per
    applied proposal, not one per pair (accepting 800 proposals used to be about 640 000 reads)."""
    applied = getattr(state, "aigcp_applied", None) or []
    if not entries or not applied:
        return
    mine = {(entry["label"], msapi.camera_key(entry["camera"])) for entry in entries}
    state.aigcp_applied = [t for t in applied if (msapi.marker_label(t[0]), msapi.camera_key(t[1])) not in mine]


def _own(entry):
    """True if the photo's projection is still the AI proposal (not pinned)."""
    current = msapi.get_projection(entry["marker"], entry["camera"])
    return current is not None and not current[2]


def remove(state, review, entry):
    """Removes a proposal from the project, or an already validated or refined point: the previous point comes back.

    Returns True if done. A validated or refined point is a manual pin: it is always
    undone (the operator asks for it), whereas an unvalidated proposal is only removed
    if the photo was not pinned in the meantime. `restore` puts the entry back in its previous state.
    """
    was = entry["state"]
    if was not in (KEPT, ACCEPTED, REFINED):
        return False
    marker, camera = entry["marker"], entry["camera"]
    own = _own(entry) if was == KEPT else True
    msapi.unmark_proposal(marker, camera)
    if own:
        previous = entry["previous"]
        if previous is not None:
            msapi.set_projection(marker, camera, previous[0], previous[1], previous[2])
        else:
            msapi.remove_projection(marker, camera)
    _forget(state, entry)
    entry["was"] = was
    entry["state"] = REMOVED
    if own:
        record(review, entry, "rejected")
    # else: the operator had already pinned the photo by hand, so the spot was kept: it is not a false detection and the
    # training feedback must not say so.
    return True


def restore(state, review, entry):
    """Puts a removed entry back in its previous state: proposal, validated point or refined point.

    Refuses if the operator pinned the photo in the meantime.
    """
    if entry["state"] != REMOVED:
        return False
    marker, camera = entry["marker"], entry["camera"]
    current = msapi.get_projection(marker, camera)
    if current is not None and current[2]:
        return False
    was = entry.get("was", KEPT)
    if was in (ACCEPTED, REFINED):
        done, _message = msapi.set_projection(marker, camera, entry["x"], entry["y"], pinned=True)
        if not done:
            return False
        entry["state"] = was
        record(review, entry, was, entry["x"], entry["y"])
        return True
    done, _message = msapi.set_projection(marker, camera, entry["ai_x"], entry["ai_y"], pinned=False)
    if not done:
        return False
    msapi.mark_proposal(marker, camera)
    entry["previous"] = current
    state.aigcp_applied = list(getattr(state, "aigcp_applied", None) or []) + [(marker, camera, current)]
    entry["state"] = KEPT
    record(review, entry, "restored")
    return True


def refine(state, review, entry, x, y):
    """Replaces the proposal with a manual pin at (x, y). Can be undone with Ctrl+Z.

    Works on any entry, including one already refined or validated: it can be taken up again.
    """
    marker, camera = entry["marker"], entry["camera"]
    before = msapi.get_projection(marker, camera)
    chunk = msapi.chunk()
    undo = history.push(marker, camera, before, chunk_key=msapi.chunk_key(chunk),
                        label=entry["label"], camera_name=entry["camera_label"])
    done, message = msapi.set_projection(marker, camera, x, y, pinned=True)
    if not done:
        history.discard(undo)
        journal.write("AI review: {} on {}: {}".format(entry["label"], entry["camera_label"], message))
        return False
    msapi.unmark_proposal(marker, camera)
    _forget(state, entry)
    entry["x"], entry["y"] = float(x), float(y)
    entry["state"] = REFINED
    record(review, entry, "refined", x, y)
    return True


def accept_kept(state, review, progress=None):
    """Sets every kept proposal as a manual pin. Returns their number.

    `progress(done, total)`, optional, is called before each proposal: the interface
    updates its bar there (placing several hundred points takes time).
    """
    done_count = 0
    pending = [entry for entry in review.entries if entry["state"] == KEPT]
    accepted = []
    try:
        for index, entry in enumerate(pending):
            if progress is not None:
                progress(index, len(pending))
            marker, camera = entry["marker"], entry["camera"]
            if not _own(entry):
                continue          # the operator has already pinned this photo
            done, _message = msapi.set_projection(marker, camera, entry["ai_x"], entry["ai_y"], pinned=True, update=False)
            if not done:
                continue
            msapi.unmark_proposal(marker, camera)
            entry["state"] = ACCEPTED
            accepted.append(entry)
            record(review, entry, "accepted", entry["ai_x"], entry["ai_y"])
            done_count += 1
    finally:
        _forget_many(state, accepted)
        msapi.refresh()                                  # once for the whole series
    return done_count
