"""Stored solutions of the Aero-PPK window: a snapshot of the results at a given moment and the comparison of two of them. Pure logic, no Qt.

A solution is kept in memory only, for the life of the window (the wizard holds them in a list). Nothing is written to disk. The photo
positions are the ones the export would write (the camera positions of the pipeline), keyed by the path of the photo."""

import datetime

import numpy as np

from ... import i18n
from . import summary

EARTH_RADIUS_M = 6371008.8                 # mean radius: the differences compared here are centimetres, a sphere is plenty
OVER_M = 0.05                              # "differs": more than 5 cm
SAME_M = 1e-4                              # two solutions closer than this everywhere are the same one


class Solution(object):
    """The name, how it was made and the figures of one set of results, plus the per-photo arrays (keyed by photo, in `keys` order)."""

    def __init__(self, name, mode, bases, moment, figures, keys, lat, lon, h, level, quality):
        self.name, self.mode, self.bases, self.moment = name, mode, list(bases), moment
        self.figures = figures
        self.keys = list(keys)
        self.index = dict((key, i) for i, key in enumerate(self.keys))
        self.lat, self.lon, self.h = lat, lon, h
        self.level, self.quality = level, quality

    def reliable_pct(self):
        photos = self.figures.get("photos", 0)
        return 100.0 * self.figures["levels"][0] / photos if photos else 0.0


def base_codes(results):
    """The codes of the bases that count in the results (the ones the user took out are left out), each once, in order."""
    codes = []
    for result in results:
        left = set(getattr(result, "excluded", None) or ())
        for run in result.runs:
            if run.base.name not in left and run.base.name not in codes:
                codes.append(run.base.name)
    return codes


def mode_text(mode, count=1):
    """The mode in words: "single base", "several bases" or "own base"."""
    key = {"own": "own", "multi": "several"}.get(mode, "single")
    if key == "several" and count < 2:
        key = "single"
    return i18n.t("aeroppk.sol.mode_" + key)


def snapshot(state, name=None, now=None):
    """The results of `state` as a `Solution`. Without a name: "CRTE, single base, 14:02"."""
    results = list(state.results)
    moment = now or datetime.datetime.now()
    codes = base_codes(results)
    mode = getattr(state, "mode", "single")
    if not name:
        shown = ", ".join(codes[:3]) + ("..." if len(codes) > 3 else "")
        name = i18n.t("aeroppk.sol.name", bases=shown or "-", mode=mode_text(mode, len(codes)), time=moment.strftime("%H:%M"))
    keys, lat, lon, h = [], [], [], []
    level, quality = [], []
    for result in results:
        comb, cam = result.combined, result.cam
        flight = getattr(getattr(result, "files", None), "name", "")
        for i, event in enumerate(result.events):
            photo = getattr(event, "photo", None)
            keys.append(photo or "%s#%d" % (flight, i))
            lat.append(float(cam["lat"][i]))
            lon.append(float(cam["lon"][i]))
            h.append(float(cam["h"][i]))
            level.append(3 if comb.q[i] == 0 else int(comb.level[i]))
            quality.append(int(comb.q[i]))
    typ = summary.typical(results)
    totals = summary.figures(results)
    figures = {"photos": totals["photos"], "levels": summary.level_counts(results), "fix_pct": totals["fix_pct"],
               "h_median": typ["h"] if typ else None, "v_median": typ["v"] if typ else None}
    return Solution(name, mode, codes, moment, figures, keys, np.array(lat, float), np.array(lon, float), np.array(h, float),
                    np.array(level, int), np.array(quality, int))


def compare(a, b):
    """The difference between two solutions over the photos they have in common AND both give a position for.

    Dict: n (photos compared), common (photos in both), h_median, h_p95, h_max, v_median, v_p95, v_max (metres, None when n is 0), over
    (photos that differ by more than 5 cm, horizontally or vertically)."""
    common = [key for key in a.keys if key in b.index]
    ia = np.array([a.index[key] for key in common], int)
    ib = np.array([b.index[key] for key in common], int)
    out = {"common": len(common), "n": 0, "over": 0, "h_median": None, "h_p95": None, "h_max": None,
           "v_median": None, "v_p95": None, "v_max": None}
    if not len(common):
        return out
    ok = (a.quality[ia] > 0) & (b.quality[ib] > 0) & np.isfinite(a.lat[ia]) & np.isfinite(b.lat[ib])
    ia, ib = ia[ok], ib[ok]
    if not len(ia):
        return out
    lat0 = np.radians(a.lat[ia])
    north = np.radians(b.lat[ib] - a.lat[ia]) * EARTH_RADIUS_M
    east = np.radians(b.lon[ib] - a.lon[ia]) * EARTH_RADIUS_M * np.cos(lat0)
    horizontal = np.hypot(north, east)
    vertical = np.abs(b.h[ib] - a.h[ia])
    out["n"] = int(len(ia))
    out["over"] = int(np.sum((horizontal > OVER_M) | (vertical > OVER_M)))
    for prefix, values in (("h", horizontal), ("v", vertical)):
        out[prefix + "_median"] = float(np.median(values))
        out[prefix + "_p95"] = float(np.percentile(values, 95))
        out[prefix + "_max"] = float(np.max(values))
    return out


def same(a, b):
    """True when `b` is the same solution as `a`: the same photos, the same levels, positions closer than a tenth of a millimetre."""
    if a.keys != b.keys or not np.array_equal(a.level, b.level):
        return False
    diff = compare(a, b)
    if diff["n"] != int(np.sum(a.quality > 0)):
        return False
    return diff["n"] == 0 or (diff["h_max"] < SAME_M and diff["v_max"] < SAME_M)


def agree_within(diff):
    """The distance, metres, within which 95 % of the compared photos agree (the larger of the horizontal and vertical ones), or None."""
    if not diff["n"]:
        return None
    return max(diff["h_p95"], diff["v_p95"])
