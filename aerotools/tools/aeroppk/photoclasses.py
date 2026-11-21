"""The photos of a flight in three classes, to read the result without the table of every photo. Pure logic, no Qt.

The photos that have a position are sorted by their horizontal standard deviation (the smaller, the better). The best `BEST_SHARE` percent of
them are the class "best", the worst `WORST_SHARE` percent are the class "worst", and all the others are the class "median". The classes are
cut by rank, so the shares are the same whatever the values are and they add up to 100 (largest remainder). Each class is shown by one photo
and by its precision: the middle photo of the class for "median", the smallest standard deviation for "best", the largest for "worst".
Photos without a position are left out and counted apart.
"""

import math

BEST_SHARE = 25                      # percent of the photos in the class "best"
WORST_SHARE = 10                     # percent of the photos in the class "worst"
ORDER = ("median", "worst", "best")  # the order of the rows


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def shares(counts):
    """Whole percents of `counts` that add up to 100 (largest remainder), all zero when there is nothing."""
    total = float(sum(counts))
    if total <= 0:
        return [0] * len(counts)
    exact = [100.0 * c / total for c in counts]
    out = [int(math.floor(v)) for v in exact]
    left = 100 - sum(out)
    for index in sorted(range(len(counts)), key=lambda i: (exact[i] - out[i], counts[i]), reverse=True)[:left]:
        out[index] += 1
    return out


def positioned(rows):
    """[(horizontal sd, vertical sd or None, row)] of the photos that have a position, smallest horizontal sd first."""
    items = []
    for row in rows:
        if not row.get("q"):
            continue
        n, e = _finite(row.get("sdn")), _finite(row.get("sde"))
        if n is None or e is None:
            continue
        items.append((math.hypot(n, e), _finite(row.get("sdu")), row))
    items.sort(key=lambda item: item[0])
    return items


def classify(rows, good_limit_m=0.10):
    """The three classes of a list of photo rows, or None when no photo has a position.

    {"rows": [{"kind", "name", "sd_h", "sd_u", "count", "share"}] in the order median, worst, best (name and sds are None for an empty class;
    sds in metres), "n": photos with a position, "without": photos without, "good_pct": share of the photos with a position whose horizontal
    sd is at most `good_limit_m`}"""
    items = positioned(rows)
    n = len(items)
    if not n:
        return None
    best_n = int(n * BEST_SHARE / 100.0 + 0.5)
    worst_n = int(n * WORST_SHARE / 100.0 + 0.5)
    median_n = n - best_n - worst_n
    if median_n <= 0:                                      # a very small flight: the middle class is never empty
        best_n, worst_n, median_n = 0, 0, n
    groups = {"best": items[:best_n], "median": items[best_n:n - worst_n], "worst": items[n - worst_n:]}
    pick = {"best": lambda g: g[0], "worst": lambda g: g[-1], "median": lambda g: g[len(g) // 2]}
    out = []
    percents = dict(zip(ORDER, shares([len(groups[k]) for k in ORDER])))
    for kind in ORDER:
        group = groups[kind]
        entry = {"kind": kind, "name": None, "sd_h": None, "sd_u": None, "count": len(group), "share": percents[kind]}
        if group:
            sd_h, sd_u, row = pick[kind](group)
            entry.update(name=row.get("name") or "", sd_h=sd_h, sd_u=sd_u)
        out.append(entry)
    good = sum(1 for item in items if item[0] <= good_limit_m + 1e-12)
    return {"rows": out, "n": n, "without": len(rows) - n, "good_pct": 100.0 * good / n}
