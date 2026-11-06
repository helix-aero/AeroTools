"""Pinning diagnostic: which pins, which photos and which markers are problematic.

Method, for each marker that has at least 3 manual pins (green dot):
  1. take the ray of each pin (camera centre -> pinned pixel);
  2. for a given pin, triangulate the marker's 3D position with ALL THE OTHER
     rays ("cross-validation": the pin being judged does not influence its own verdict);
  3. project that point onto the judged photo and measure the gap, in pixels, with the pin.
A gap of a few pixels at most is normal; a large gap means an offset pin or a
misaligned photo. With only 2 pins there is nothing to cross-check against: the marker is
flagged "to complete", not judged.

The classic gap "pin - reprojection of Metashape's position" (fit_px) is also computed,
so it can be compared with the "Error (pix)" figure of Metashape's References pane.

A very offset pin pulls the position computed without it, and makes its neighbours look faulty. So we
"peel": judge everyone, set aside the worst one if it exceeds PEEL_PX, then start again
without it, until nothing exceeds the limit any more (or only 3 pins remain, which cannot be told
apart any further). Only the real culprit is then flagged.

Flagging threshold for a pin: max(FLOOR_PX, FACTOR x median of the project's gaps): a globally
noisy project does not flag everything. A photo is suspect when its median (over >= 3 markers)
is clearly above the project's.

`analyze` touches neither Metashape nor Qt; `collect` reads the project.
"""

import math

from ... import msapi
from . import consensus, qc

MIN_PINNED = 3
FLOOR_PX = 3.0
PEEL_PX = 3.0
FACTOR = 4.0
PHOTO_FLOOR_PX = 1.5
PHOTO_FACTOR = 2.0
PHOTO_MIN_MARKERS = 3


def median(values):
    values = sorted(values)
    if not values:
        return 0.0
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else 0.5 * (values[middle - 1] + values[middle])


def rms(values):
    values = list(values)
    return math.sqrt(sum(v * v for v in values) / len(values)) if values else 0.0


def leave_one_out_points(rays):
    """For each ray, the point triangulated with all the others (None if impossible).

    `rays`: list of (origin, direction) or None. At least 2 other usable rays are needed, not
    all parallel."""
    out = []
    for index in range(len(rays)):
        others = [ray for i, ray in enumerate(rays) if i != index and ray is not None]
        cleaned = [r for r in consensus._clean(others) if r is not None]
        out.append(consensus.triangulate(cleaned) if len(cleaned) >= 2 else None)
    return out


def cross_check(rays, gap_of, peel_px=PEEL_PX):
    """Gap (px) of each pin from the position triangulated WITHOUT it, peeling the worst one at each round.

    `rays`: (origin, direction) per pin; `gap_of(i, point)`: gap in pixels between pin i
    and the projection of `point` on its photo (or None). Returns a list (None if it cannot be judged)."""
    result = [None] * len(rays)
    active = [i for i, ray in enumerate(rays) if ray is not None]
    while len(active) >= MIN_PINNED:
        points = leave_one_out_points([rays[i] for i in active])
        gaps = [gap_of(i, point) if point is not None else None for i, point in zip(active, points)]
        judged = [(gap, i) for gap, i in zip(gaps, active) if gap is not None]
        if not judged:
            break
        worst, culprit = max(judged)
        if worst <= peel_px or len(active) == MIN_PINNED:
            for gap, i in zip(gaps, active):
                result[i] = gap
            break
        result[culprit] = worst
        active.remove(culprit)
    return result


# --------------------------------------------------------------------------
# Reading the project
# --------------------------------------------------------------------------

def collect(chk):
    """The manual pins of each marker of the chunk, with their gaps in pixels.

    Returns a list of {"label", "marker", "total_m"?, "pinned": n, "proposals": n,
    "observations": [{"camera", "camera_label", "x", "y", "fit_px", "loo_px"}]}. Never raises."""
    out = []
    for marker in msapi.markers(chk):
        label = msapi.marker_label(marker)
        if not label:
            continue
        try:
            out.append(_collect_marker(chk, marker, label))
        except Exception:
            continue
    return out


def _collect_marker(chk, marker, label):
    try:
        position = marker.position
    except Exception:
        position = None
    observations, rays, proposals = [], [], 0
    for camera in msapi.projection_cameras(marker):
        projection = msapi.get_projection(marker, camera)
        if projection is None:
            continue
        if not projection[2]:
            if msapi.is_proposal(marker, camera):
                proposals += 1
            continue
        fit = None
        if position is not None:
            fit = _gap(projection, msapi.project_point(camera, position))
        observations.append({"camera": camera, "camera_label": msapi.camera_label(camera),
                             "x": projection[0], "y": projection[1], "fit_px": fit, "loo_px": None})
        rays.append(msapi.pixel_ray(camera, projection[0], projection[1]))
    if len(observations) >= MIN_PINNED:
        def gap_of(index, point):
            obs = observations[index]
            return _gap((obs["x"], obs["y"]), msapi.project_point(obs["camera"], point))

        for obs, gap in zip(observations, cross_check(rays, gap_of)):
            obs["loo_px"] = gap
    return {"label": label, "marker": marker, "pinned": len(observations), "proposals": proposals,
            "observations": observations}


def _gap(a, b):
    if a is None or b is None:
        return None
    return math.hypot(a[0] - b[0], a[1] - b[1])


# --------------------------------------------------------------------------
# Analysis
# --------------------------------------------------------------------------

def analyze(markers, rows=None):
    """Diagnostic result (dict) from `collect`; `rows`: rows of the panel table
    (for the total 3D error per marker, see qc.watchlist).

    Keys: summary, limit_px, projections (sorted worst to best), photos, markers."""
    loo = [o["loo_px"] for m in markers for o in m["observations"] if o["loo_px"] is not None]
    fit = [o["fit_px"] for m in markers for o in m["observations"] if o["fit_px"] is not None]
    typical = median(loo)
    limit = max(FLOOR_PX, FACTOR * typical)
    projections = []
    by_camera = {}
    for marker in markers:
        for obs in marker["observations"]:
            if obs["loo_px"] is None:
                continue
            by_camera.setdefault(obs["camera_label"], {"camera": obs["camera"], "values": [], "markers": set()})
            by_camera[obs["camera_label"]]["values"].append(obs["loo_px"])
            by_camera[obs["camera_label"]]["markers"].add(marker["label"])
            if obs["loo_px"] > limit:
                projections.append({"label": marker["label"], "marker": marker["marker"],
                                    "camera": obs["camera"], "camera_label": obs["camera_label"],
                                    "px": obs["loo_px"], "fit_px": obs["fit_px"]})
    projections.sort(key=lambda item: -item["px"])
    photos = []
    for name, data in by_camera.items():
        if len(data["markers"]) < PHOTO_MIN_MARKERS:
            continue
        mid = median(data["values"])
        if mid > max(PHOTO_FLOOR_PX, PHOTO_FACTOR * typical):
            photos.append({"camera": data["camera"], "camera_label": name, "px": mid,
                           "n": len(data["markers"])})
    photos.sort(key=lambda item: -item["px"])

    flagged = []
    for marker in markers:
        if marker["pinned"] < MIN_PINNED:
            flagged.append({"label": marker["label"], "marker": marker["marker"],
                            "kind": "few" if marker["pinned"] < 2 else "unchecked", "n": marker["pinned"]})
        if marker["proposals"]:
            flagged.append({"label": marker["label"], "marker": marker["marker"],
                            "kind": "proposals", "n": marker["proposals"]})
    if rows:
        result = qc.watchlist(rows)
        for item in result["residual"]:
            flagged.append({"label": item["label"], "marker": None, "kind": "residual",
                            "n": item["proj"], "total": item["total"], "limit": result["limit"]})
    order = {"few": 0, "residual": 1, "unchecked": 2, "proposals": 3}
    flagged.sort(key=lambda item: (order[item["kind"]], item["label"]))

    summary = {
        "markers": len(markers),
        "checked": sum(1 for m in markers if m["pinned"] >= MIN_PINNED),
        "observations": sum(m["pinned"] for m in markers),
        "photos": len({o["camera_label"] for m in markers for o in m["observations"]}),
        "rms_px": rms(fit), "median_px": typical, "loo_rms_px": rms(loo),
        "below_1px": (sum(1 for v in loo if v <= 1.0) / float(len(loo))) if loo else None,
    }
    return {"summary": summary, "limit_px": limit, "projections": projections, "photos": photos,
            "markers": flagged}


def is_clean(result):
    return not (result["projections"] or result["photos"] or result["markers"])
