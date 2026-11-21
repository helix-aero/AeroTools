"""The PPK positions of one flight computed from a base station, with an error estimate per photo.

Pure numpy, no Qt, no Metashape, no RTKLIB: the input is the antenna position of every photo and its quality.

Method (all of it is a design choice, documented in docs/AEROPPK.md; the numbers are in `Policy`):

1. The solution of the base gets, for every photo, an error estimate per axis (north, east, up) made of
   * what the solver reports (its standard deviation), which is optimistic,
   * a floor, so a fixed solution is never trusted below ~1 cm,
   * a distance term that grows with the base line length (about 1.2 ppm horizontally, twice that vertically),
   * a large floor and inflation when the ambiguities are not fixed (float).
   Solutions that are neither fixed nor float (dgps, single) are not used.
"""

import numpy as np

from . import geo

FIX, FLOAT, NONE = 1, 2, 0

# Flags, kept as short stable strings (shown in the report, tested).
F_NO_SOLUTION = "no_solution"
F_FLOAT = "float"
LEVEL_OK, LEVEL_WARN, LEVEL_BAD = 0, 1, 2


class Policy(object):
    def __init__(self, **kwargs):
        self.fix_floor_h = 0.008          # metres, horizontal sigma floor of a fixed solution
        self.fix_floor_v = 0.016
        self.ppm_h = 1.2e-6               # metres per metre of base line (about 1.2 cm per 10 km)
        self.ppm_v = 2.4e-6
        self.float_floor_h = 0.10
        self.float_floor_v = 0.20
        self.float_inflate = 3.0
        self.outlier_k = 3.0
        self.outlier_min_h = 0.03         # a solution is only dropped when it is also at least this far off
        self.outlier_min_v = 0.05
        self.conflict_k = 3.0
        self.conflict_min_h = 0.04
        self.conflict_min_v = 0.08
        self.spread_warn = 0.05           # metres between used solutions (3D)
        self.bad_sd = 0.15                # metres: combined sigma above which a photo is "bad"
        self.suspect_bias = 0.05          # metres of median 3D bias against the consensus
        self.suspect_dropped = 0.25       # share of photos where the base was dropped
        for key, value in kwargs.items():
            if not hasattr(self, key):
                raise TypeError("unknown policy field %s" % key)
            setattr(self, key, value)


class Solution(object):
    """The PPK antenna positions of every photo for one base. Arrays have one entry per photo (NaN / q = 0 when missing)."""

    def __init__(self, name, baseline_m, lat, lon, h, q, sdn, sde, sdu, ratio=None, ns=None):
        self.name = name
        self.baseline_m = float(baseline_m)
        self.lat, self.lon, self.h = (np.asarray(a, float) for a in (lat, lon, h))
        self.q = np.asarray(q, int)
        self.sdn, self.sde, self.sdu = (np.asarray(a, float) for a in (sdn, sde, sdu))
        count = len(self.lat)
        self.ratio = np.full(count, np.nan) if ratio is None else np.asarray(ratio, float)
        self.ns = np.zeros(count, int) if ns is None else np.asarray(ns, int)

    def __len__(self):
        return len(self.lat)


class Combined(object):
    def __init__(self, count):
        self.lat = np.full(count, np.nan)
        self.lon = np.full(count, np.nan)
        self.h = np.full(count, np.nan)
        self.q = np.zeros(count, int)
        self.sdn = np.full(count, np.nan)
        self.sde = np.full(count, np.nan)
        self.sdu = np.full(count, np.nan)
        self.used = np.zeros(count, int)          # number of solutions in the mean
        self.spread = np.full(count, np.nan)      # largest 3D distance between two used solutions (m)
        self.level = np.full(count, LEVEL_BAD, int)
        self.flags = [[] for _ in range(count)]
        self.used_names = [[] for _ in range(count)]
        self.bases = {}                           # name -> dict of statistics (see `_base_stats`)
        self.suspect = []                         # names of bases excluded as aberrant
        self.aside = []                           # suspect bases the combination was really redone without
        self.policy = None


def _sigmas(sol, policy):
    """(sn, se, su) arrays of the error estimate of one solution; NaN where the solution cannot be used."""
    q = sol.q
    usable = (q == FIX) | (q == FLOAT)
    ppm_h, ppm_v = policy.ppm_h * sol.baseline_m, policy.ppm_v * sol.baseline_m
    fix = q == FIX
    sn = np.where(fix, np.sqrt(np.nan_to_num(sol.sdn) ** 2 + max(policy.fix_floor_h, 0) ** 2 + ppm_h ** 2),
                  np.sqrt((policy.float_inflate * np.nan_to_num(sol.sdn)) ** 2 + policy.float_floor_h ** 2 + ppm_h ** 2))
    se = np.where(fix, np.sqrt(np.nan_to_num(sol.sde) ** 2 + policy.fix_floor_h ** 2 + ppm_h ** 2),
                  np.sqrt((policy.float_inflate * np.nan_to_num(sol.sde)) ** 2 + policy.float_floor_h ** 2 + ppm_h ** 2))
    su = np.where(fix, np.sqrt(np.nan_to_num(sol.sdu) ** 2 + policy.fix_floor_v ** 2 + ppm_v ** 2),
                  np.sqrt((policy.float_inflate * np.nan_to_num(sol.sdu)) ** 2 + policy.float_floor_v ** 2 + ppm_v ** 2))
    for arr in (sn, se, su):
        arr[~usable] = np.nan
    return sn, se, su


def _ned_offsets(sols, i, idx):
    """Position of each solution in `idx` at photo `i` as north / east / down metres from solution idx[0]."""
    base = sols[idx[0]]
    x0, y0, z0 = geo.llh_to_ecef(base.lat[i], base.lon[i], base.h[i])
    out = []
    for j in idx:
        s = sols[j]
        x, y, z = geo.llh_to_ecef(s.lat[i], s.lon[i], s.h[i])
        out.append(geo.ecef_delta_to_ned(base.lat[i], base.lon[i], float(x - x0), float(y - y0), float(z - z0)))
    return np.array(out, float)


def _offsets(sols, sig):
    """North / east / down offset of every solution from the reference of each photo (the first solution usable for it), at once.

    Returns (offsets (n solutions, photos, 3), reference index per photo, reference x, y, z per photo, usable (n solutions, photos))."""
    count = len(sols[0])
    usable = np.array([np.isfinite(s[0]) for s in sig])                         # (n, photos)
    ref = np.argmax(usable, axis=0)
    cols = np.arange(count)
    ecef = [geo.llh_to_ecef(s.lat, s.lon, s.h) for s in sols]
    stack = np.array([[c[0], c[1], c[2]] for c in ecef])                         # (n, 3, photos)
    lat = np.array([s.lat for s in sols])[ref, cols]
    lon = np.array([s.lon for s in sols])[ref, cols]
    ref_xyz = stack[ref, :, cols].T                                              # (3, photos)
    offsets = np.empty((len(sols), count, 3))
    with np.errstate(invalid="ignore"):
        for j in range(len(sols)):
            n, e, d = geo.ecef_delta_to_ned_array(lat, lon, stack[j, 0] - ref_xyz[0], stack[j, 1] - ref_xyz[1], stack[j, 2] - ref_xyz[2])
            offsets[j, :, 0], offsets[j, :, 1], offsets[j, :, 2] = n, e, d
    return offsets, ref, ref_xyz, usable


def _combine_event(sols, sig, i, policy, offsets=None):
    """One photo. Returns None when nothing usable, else a dict with position offset, sigma, used list, flags.

    `offsets` = the first result of `_offsets` (computed here for this photo alone when not given)."""
    idx = [j for j in range(len(sols)) if np.isfinite(sig[j][0][i])]
    if not idx:
        return None
    flags = []
    d = np.array([offsets[j, i] for j in idx]) if offsets is not None else _ned_offsets(sols, i, idx)          # (k, 3) north, east, down
    s = np.array([[sig[j][0][i], sig[j][1][i], sig[j][2][i]] for j in idx])
    dropped = []
    keep = list(range(len(idx)))
    w = 1.0 / s[keep] ** 2
    mean = (w * d[keep]).sum(axis=0) / w.sum(axis=0)
    used = [idx[k] for k in keep]
    conflict = False
    w = 1.0 / s[keep] ** 2
    sigma = np.sqrt(1.0 / w.sum(axis=0))
    spread = 0.0
    return {"mean": mean, "sigma": sigma, "used": used, "dropped": dropped, "spread": spread, "flags": flags,
            "ref": idx[0], "conflict": conflict}


def combine(
        solutions,
        policy=None,
):
    """The solution of the base (a list of one Solution) -> Combined."""
    policy = policy or Policy()
    count = len(solutions[0]) if solutions else 0
    sols = list(solutions)
    out = Combined(count)
    out.policy = policy
    if not sols:
        out.flags = [[F_NO_SOLUTION] for _ in range(count)]
        return out
    sig = [_sigmas(s, policy) for s in sols]
    stats_dropped = {s.name: 0 for s in sols}
    stats_used = {s.name: 0 for s in sols}
    offsets, _ref, ref_xyz, usable = _offsets(sols, sig)
    rows, means = [], []
    for i in range(count):
        res = _combine_event(sols, sig, i, policy, offsets)
        if res is None:
            out.flags[i] = [F_NO_SOLUTION]
            out.level[i] = LEVEL_BAD
            continue
        rows.append(i)
        means.append(res["mean"])
        out.sdn[i], out.sde[i], out.sdu[i] = (float(v) for v in res["sigma"])
        out.used[i] = len(res["used"])
        out.spread[i] = res["spread"]
        out.used_names[i] = [sols[j].name for j in res["used"]]
        kinds = [sols[j].q[i] for j in res["used"]]
        out.q[i] = FIX if all(k == FIX for k in kinds) else FLOAT
        flags = list(res["flags"])
        if out.q[i] != FIX:
            flags.append(F_FLOAT)
        for j in res["dropped"]:
            stats_dropped[sols[j].name] += 1
        for j in res["used"]:
            stats_used[sols[j].name] += 1
        out.flags[i] = flags
        sd_h = float(np.hypot(out.sdn[i], out.sde[i]))
        bad = out.q[i] != FIX and max(sd_h, out.sdu[i]) > policy.bad_sd
        warn = bool(flags) or sd_h > policy.bad_sd / 2.0
        out.level[i] = LEVEL_BAD if bad else (LEVEL_WARN if warn else LEVEL_OK)
    resid = dict((s.name, np.zeros((0, 4))) for s in sols)
    if rows:
        rows = np.array(rows)
        mean = np.array(means, float).reshape(-1, 3)
        ref_lat = np.array([s.lat for s in sols])[_ref[rows], rows]
        ref_lon = np.array([s.lon for s in sols])[_ref[rows], rows]
        dx, dy, dz = geo.ned_to_ecef_delta_array(ref_lat, ref_lon, mean[:, 0], mean[:, 1], mean[:, 2])
        cx, cy, cz = ref_xyz[0][rows] + dx, ref_xyz[1][rows] + dy, ref_xyz[2][rows] + dz         # the combined position, geocentric
        out.lat[rows], out.lon[rows], out.h[rows] = geo.ecef_to_llh(cx, cy, cz)
        # residuals of every solution against the consensus (for the base statistics)
        for j, s in enumerate(sols):
            use = rows[usable[j][rows]]
            if not len(use):
                continue
            where = np.searchsorted(rows, use)
            xs, ys, zs = geo.llh_to_ecef(s.lat[use], s.lon[use], s.h[use])
            n, e, d = geo.ecef_delta_to_ned_array(out.lat[use], out.lon[use], xs - cx[where], ys - cy[where], zs - cz[where])
            resid[s.name] = np.column_stack([n, e, -d, s.q[use] == FIX])
    for j, s in enumerate(sols):
        out.bases[s.name] = _base_stats(s, resid[s.name], stats_used[s.name], stats_dropped[s.name], count, policy)
    out.suspect = [n for n, b in out.bases.items() if b["suspect"]]
    return out


def _base_stats(sol, residuals, used, dropped, count, policy):
    arr = np.array(residuals, float).reshape(-1, 4)
    stats = {"name": sol.name, "baseline_m": sol.baseline_m, "photos": count, "solved": int(len(arr)),
             "fix": int(np.sum(sol.q == FIX)), "float": int(np.sum(sol.q == FLOAT)), "used": int(used), "dropped": int(dropped),
             "bias_n": None, "bias_e": None, "bias_u": None, "rms_h": None, "rms_u": None, "suspect": False, "reason": ""}
    if len(arr):
        fixed = arr[arr[:, 3] > 0.5] if (arr[:, 3] > 0.5).any() else arr
        stats["bias_n"], stats["bias_e"], stats["bias_u"] = (float(np.median(fixed[:, k])) for k in range(3))
        stats["rms_h"] = float(np.sqrt(np.mean(fixed[:, 0] ** 2 + fixed[:, 1] ** 2)))
        stats["rms_u"] = float(np.sqrt(np.mean(fixed[:, 2] ** 2)))
        bias3 = float(np.sqrt(stats["bias_n"] ** 2 + stats["bias_e"] ** 2 + stats["bias_u"] ** 2))
        share = dropped / float(max(1, len(arr)))
        stats["bias_3d"] = bias3
        limit = policy.suspect_bias + 4.0 * policy.ppm_h * sol.baseline_m
        if stats["fix"] < 0.05 * count:
            stats["suspect"], stats["reason"] = True, "no_fix"         # unusable: the ambiguities were never fixed
        elif bias3 > limit:
            stats["suspect"], stats["reason"] = True, "bias"
        elif share > policy.suspect_dropped:
            stats["suspect"], stats["reason"] = True, "dropped"
    elif count:
        stats["suspect"], stats["reason"] = True, "no_fix"
    return stats


def summary(combined):
    """Global figures for the report and the window."""
    count = len(combined.q)
    solved = combined.q > 0
    out = {"photos": count, "solved": int(solved.sum()), "fix": int((combined.q == FIX).sum()),
           "float": int((combined.q == FLOAT).sum()), "none": int((combined.q == NONE).sum()),
           "fix_pct": 100.0 * float((combined.q == FIX).sum()) / count if count else 0.0,
           "ok": int((combined.level == LEVEL_OK).sum()), "warn": int((combined.level == LEVEL_WARN).sum()),
           "bad": int((combined.level == LEVEL_BAD).sum()), "suspect_bases": list(combined.suspect)}
    if solved.any():
        sd_h = np.hypot(combined.sdn, combined.sde)[solved]
        out["sd_h_median"], out["sd_h_max"] = float(np.median(sd_h)), float(np.max(sd_h))
        out["sd_u_median"], out["sd_u_max"] = float(np.median(combined.sdu[solved])), float(np.max(combined.sdu[solved]))
    return out
