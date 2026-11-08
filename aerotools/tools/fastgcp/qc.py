"""Pinning progress and markers to watch (no Qt, no Metashape).

Works on the rows of the panel table: dicts with `label`, `proj` (number of
projections) and `x`, `y`, `z` (errors in metres, or None).

A marker is "pinned" when it has at least MIN_PHOTOS projections: this is the
minimum for a solid triangulation. To watch:
  - `few`      : fewer than MIN_PHOTOS projections;
  - `residual` : total error above the threshold. The threshold is the larger of
                 RESIDUAL_FLOOR_M and RESIDUAL_FACTOR times the low median of the
                 chunk's errors, so that a globally noisy chunk does not flag
                 everything.
"""

import math

MIN_PHOTOS = 3
RESIDUAL_FLOOR_M = 0.03
RESIDUAL_FACTOR = 3.0


def total_error(row):
    """Total error in metres, or None if a component is missing."""
    try:
        values = [row.get(key) for key in ("x", "y", "z")]
        if any(v is None for v in values):
            return None
        total = math.sqrt(sum(float(v) ** 2 for v in values))
    except (TypeError, ValueError):
        return None
    return total if math.isfinite(total) else None


def _low_median(values):
    """Low median: with few markers, a large error must not raise the threshold
    that flags it (the usual median of 2 values would)."""
    values = sorted(values)
    return values[(len(values) - 1) // 2] if values else 0.0


def residual_limit(rows):
    """Total-error threshold (m) above which a marker is flagged."""
    totals = [t for t in (total_error(row) for row in rows) if t is not None]
    return max(RESIDUAL_FLOOR_M, RESIDUAL_FACTOR * _low_median(totals))


def progress(rows, min_photos=MIN_PHOTOS):
    """(pinned markers, total markers)."""
    rows = list(rows)
    done = sum(1 for row in rows if int(row.get("proj") or 0) >= min_photos)
    return done, len(rows)


def is_few(row, min_photos=MIN_PHOTOS):
    return int(row.get("proj") or 0) < min_photos


def watchlist(rows, min_photos=MIN_PHOTOS):
    """Markers to watch.

    Returns {"limit": threshold, "residual": [...], "few": [...]}. Each item
    is {"label", "proj", "total"}; `residual` goes from the largest error to the
    smallest, `few` from the least pinned to the most pinned (ties keep project order).
    """
    rows = list(rows)
    limit = residual_limit(rows)
    residual = []
    few = []
    for row in rows:
        total = total_error(row)
        item = {"label": row.get("label", ""), "proj": int(row.get("proj") or 0),
                "total": total}
        if total is not None and total > limit:
            residual.append(item)
        if is_few(row, min_photos):
            few.append(item)
    residual.sort(key=lambda item: -item["total"])
    few.sort(key=lambda item: item["proj"])
    return {"limit": limit, "residual": residual, "few": few}


# State of a marker in the "Residual check" window, from most to least urgent.
RESIDUAL, FEW, NONE, OK = "residual", "few", "none", "ok"
_ORDER = {RESIDUAL: 0, FEW: 1, NONE: 2, OK: 3}


def status(row, limit, min_photos=MIN_PHOTOS):
    """FEW (not enough photos, including none), NONE (cannot be computed), RESIDUAL or OK."""
    if is_few(row, min_photos):
        return FEW
    total = total_error(row)
    if total is None:
        return NONE
    return RESIDUAL if total > limit else OK


def table(rows, enabled=None, min_photos=MIN_PHOTOS):
    """All markers, most urgent first (the window also shows the ones that are fine).

    Each item: label, proj, x, y, z, total (metres or None), role (True = control, False =
    check, None = unknown), status."""
    rows = list(rows)
    enabled = enabled or {}
    limit = residual_limit(rows)
    out = []
    for row in rows:
        label = row.get("label", "")
        out.append({"label": label, "proj": int(row.get("proj") or 0), "x": row.get("x"), "y": row.get("y"),
                    "z": row.get("z"), "total": total_error(row), "role": enabled.get(label),
                    "status": status(row, limit, min_photos)})

    def key(item):
        kind = item["status"]
        if kind == FEW:
            return (_ORDER[kind], item["proj"], item["label"])
        return (_ORDER[kind], -(item["total"] or 0.0), item["label"])

    out.sort(key=key)
    return out


def group_rms(items):
    """(control, check): each (3D root mean square in metres, count) or None.

    `items` comes from `table`. Only markers whose error can be computed count."""
    sums = {True: [0.0, 0], False: [0.0, 0]}
    for item in items:
        if item["total"] is None or item["role"] not in (True, False) or item["status"] == FEW:
            continue                # a barely pinned marker has a meaningless position: not counted
        sums[item["role"]][0] += item["total"] ** 2
        sums[item["role"]][1] += 1

    def finish(pair):
        return (math.sqrt(pair[0] / pair[1]), pair[1]) if pair[1] else None

    return finish(sums[True]), finish(sums[False])
