"""The figures of step 4, computed from the results of the calculation. Pure logic, no Qt (tested without a display).

The "solution precision" is the combined standard deviation of each photo position. It is an internal consistency figure: what the solver
reports, a floor and a term that grows with the length of the base line. It does not depend on the drone's own positions and it is NOT
an accuracy measured against ground points (see docs/AEROPPK.md). The comparison with the drone's own position is shown as information.
"""

import os

import numpy as np

from ... import i18n
from . import combine

GOOD, CHECK, BAD = 0, 1, 2           # verdict


def figures(results):
    """Totals over every flight: photos, fixed / float / none counts and share, horizontal and vertical uncertainty (mean, min, max, metres)."""
    out = {"photos": 0, "fix": 0, "float": 0, "none": 0, "warn": 0, "bad": 0, "fix_pct": 0.0,
           "h_mean": None, "h_min": None, "h_max": None, "v_mean": None, "v_min": None, "v_max": None}
    sd_h, sd_v = [], []
    for result in results:
        comb = result.combined
        q = comb.q
        out["photos"] += int(len(q))
        out["fix"] += int(np.sum(q == combine.FIX))
        out["float"] += int(np.sum(q == combine.FLOAT))
        out["none"] += int(np.sum(q == combine.NONE))
        out["warn"] += int(np.sum(comb.level == combine.LEVEL_WARN))
        out["bad"] += int(np.sum(comb.level == combine.LEVEL_BAD))
        solved = q > 0
        if solved.any():
            sd_h.append(np.hypot(comb.sdn, comb.sde)[solved])
            sd_v.append(comb.sdu[solved])
    if out["photos"]:
        out["fix_pct"] = 100.0 * out["fix"] / out["photos"]
    if sd_h:
        h, v = np.concatenate(sd_h), np.concatenate(sd_v)
        out["h_mean"], out["h_min"], out["h_max"] = float(np.mean(h)), float(np.min(h)), float(np.max(h))
        out["v_mean"], out["v_min"], out["v_max"] = float(np.mean(v)), float(np.min(v)), float(np.max(v))
    return out


def verdict(results):
    """GOOD when no photo needs a look, BAD when more than a quarter is not reliable, else CHECK."""
    totals = figures(results)
    if not totals["photos"]:
        return BAD
    if totals["bad"] == 0 and totals["warn"] == 0:
        return GOOD
    return BAD if totals["bad"] > totals["photos"] / 4.0 else CHECK


def onboard(results):
    """Comparison with the drone's own position of the first flight that has one: dict of the pipeline check, or None."""
    for result in results:
        if result.check.get("rtk_photos"):
            return result.check
    return None


# What "good" means on the screen and in the report. The one source of these limits (fixed constants, not settings): a value that meets
# them is shown in green, any other stays plain grey. No other colour, no alarm wording.
GOOD_FIX_PCT = 95.0          # fixed share from here on (that is a float share of at most 5 %)
MAX_FLOAT_PCT = 100.0 - GOOD_FIX_PCT
GOOD_ERROR_M = 0.10          # solution precision and difference with the drone's own position up to here (metres)


def tone(kind, value):
    """"good" (green) or "grey" for a figure; "" when there is no value. `kind`: fix, float (percent), sd, onboard (metres)."""
    if value is None:
        return ""
    if kind == "fix":
        return "good" if value >= GOOD_FIX_PCT else "grey"
    if kind == "float":
        return "good" if value <= MAX_FLOAT_PCT else "grey"
    if kind in ("sd", "onboard"):
        return "good" if value <= GOOD_ERROR_M else "grey"
    return ""


def precision_terms(policy=None):
    """The parts of the solution precision, from the policy of the combination so the text cannot drift from the code:
    dict fh, fv (floor of a fixed solution, centimetres, horizontal and vertical) and ppm (millimetres per kilometre of baseline)."""
    policy = policy or combine.Policy()
    return {"fh": 100.0 * policy.fix_floor_h, "fv": 100.0 * policy.fix_floor_v, "ppm": 1e6 * policy.ppm_h}


def precision_tip(policy=None):
    """The words behind the "i" of the precision figure: its three parts with the numbers of the policy, in the order they are made."""
    terms = precision_terms(policy)
    fields = {"fh": i18n.fmt_number(terms["fh"], 1), "fv": i18n.fmt_number(terms["fv"], 1), "ppm": i18n.fmt_number(terms["ppm"], 1)}
    return "\n".join(i18n.t(key, **fields) for key in ("aeroppk.res.t_sd_tip", "aeroppk.res.t_sd_1", "aeroppk.res.t_sd_2", "aeroppk.res.t_sd_3",
                                                      "aeroppk.res.t_sd_note"))


def onboard_figures(results):
    """The comparison with the drone's own position, as information (the drone's real-time position is not a reference of the PPK one).

    Dict: n (photos where the drone had its own RTK fix), offset_h and offset_u (median difference, metres), spread_h (rms around that
    median) and spread_u (95 % of the vertical differences around it), or None when there is no such comparison."""
    check = onboard(results)
    if not check or "bias_n" not in check:
        return None
    return {"n": int(check["rtk_photos"]), "offset_h": float(np.hypot(check["bias_n"], check["bias_e"])), "offset_u": float(check["bias_u"]),
            "spread_h": float(check.get("rms_h", 0.0)), "spread_u": float(check.get("p95_u", 0.0))}


def levels(results):
    """Per photo, flights one after the other: 0 good, 1 to check, 2 not reliable, 3 no position (for the plan view)."""
    out = []
    for result in results:
        comb = result.combined
        for q, level in zip(comb.q, comb.level):
            out.append(3 if q == 0 else int(level))
    return out


ROLE_USED, ROLE_ASIDE = "used", "aside"


def base_rows(results):
    """One row per flight and base for the results table. Keys: flight, flight_name, code, role, fix_pct, float_pct, baseline_km,
    status ("ok", "warn", "stop"), reason, bias (n, e, u metres or None), used, dropped, systems, profile, interval, receiver, antenna,
    product, files, warnings (list of keys)."""
    rows = []
    for index, result in enumerate(results):
        comb = result.combined
        count = max(1, len(comb.q))
        for run in result.runs:
            code = run.base.name
            stats = comb.bases.get(code, {})
            suspect, reason = bool(stats.get("suspect")), stats.get("reason") or ""
            role = ROLE_ASIDE if code in comb.aside else ROLE_USED    # a suspect base still in the combination stays used
            fix_pct = run.fix_pct()
            warnings = [w.split(":")[0] for w in result.warnings if w.endswith(":" + code)]
            status = "ok"
            out_of_it = (ROLE_ASIDE,)
            if role in out_of_it or fix_pct < 5.0:
                status = "stop"
            elif fix_pct < 90.0 or warnings or suspect:
                status = "warn"
            info = run.base.info
            rows.append({"flight": index, "flight_name": result.files.name, "code": code, "role": role, "fix_pct": fix_pct,
                         "float_pct": 100.0 * stats.get("float", 0) / count, "baseline_km": run.baseline_m / 1000.0, "status": status,
                         "reason": reason, "bias": (stats.get("bias_n"), stats.get("bias_e"), stats.get("bias_u")),
                         "used": stats.get("used", 0), "dropped": stats.get("dropped", 0), "systems": run.systems,
                         "profile": run.profile, "interval": info.interval, "receiver": info.receiver, "antenna": info.antenna,
                         "product": run.base.spec.product.label, "files": [n for _u, n in run.base.spec.product.obs],
                         "warnings": warnings})
    return rows


def warning_keys(results):
    """Warnings worth showing on the summary, as (key, fields) pairs: the flight warnings, conflicts, suspect bases, profile conflicts."""
    out, seen = [], set()
    for result in results:
        for entry in result.warnings:
            name, _sep, code = str(entry).partition(":")
            if (name, code) not in seen:
                seen.add((name, code))
                out.append((name, {"code": code}))
        if result.profile_conflicts:
            out.append(("profile_conflicts", {"n": result.profile_conflicts}))
        comb = result.combined
        for name in comb.aside:                     # only a base the positions were really computed without is said to be set aside
            out.append(("suspect_base", {"code": name}))
        compared = [n for n, b in comb.bases.items() if b.get("reason") != "excluded"]
        for name in comb.suspect:                   # a base that disagrees but is still in the calculation (too few others to drop it)
            if name not in comb.aside and len(compared) >= 2 and comb.bases.get(name, {}).get("reason") in ("bias", "dropped"):
                out.append(("suspect_used", {"code": name}))
    return out


# -- the Result screen and the Details window ---------------------------------------------------------------------------

LEVEL_GOOD, LEVEL_CHECK, LEVEL_BAD, LEVEL_NONE = 0, 1, 2, 3          # the values of `levels`
BIN_EDGES_M = (0.03, 0.10)                                           # horizontal precision bins: up to 3 cm, 3 to 10 cm, above


def level_counts(results):
    """Photos per level, in the order of `levels`: [good, to check, not reliable, no position]."""
    counts = [0, 0, 0, 0]
    for level in levels(results):
        counts[max(0, min(3, int(level)))] += 1
    return counts


def _solved_precision(results):
    """(horizontal, vertical) precision of every photo that has a position, metres, as two arrays."""
    sd_h, sd_v = [], []
    for result in results:
        comb = result.combined
        solved = comb.q > 0
        if solved.any():
            sd_h.append(np.hypot(comb.sdn, comb.sde)[solved])
            sd_v.append(comb.sdu[solved])
    if not sd_h:
        return np.zeros(0), np.zeros(0)
    return np.concatenate(sd_h), np.concatenate(sd_v)


def typical(results):
    """The typical precision: the MEDIAN horizontal and vertical precision over the photos that have a position (a few bad photos make
    a mean meaningless). Dict h, v (metres) and n (photos), or None when no photo has a position."""
    h, v = _solved_precision(results)
    h, v = h[np.isfinite(h)], v[np.isfinite(v)]
    if not len(h) or not len(v):
        return None
    return {"h": float(np.median(h)), "v": float(np.median(v)), "n": int(len(h))}


def precision_bins(results):
    """How many photos fall in each horizontal precision bin: [3 cm or less, 3 to 10 cm, more than 10 cm, no position]."""
    h, _v = _solved_precision(results)
    h = h[np.isfinite(h)]
    low, high = BIN_EDGES_M
    total = sum(int(len(result.combined.q)) for result in results)
    first, second = int(np.sum(h <= low)), int(np.sum((h > low) & (h <= high)))
    third = int(np.sum(h > high))
    return [first, second, third, total - first - second - third]


# the processing settings, as the Details window lists them (the labels are `aeroppk.details.o_<key>`)
SETTING_KEYS = ("frequency", "systems", "elevation_mask", "solution_type", "ambiguity", "glonass_ambiguity", "ratio", "min_fix_sats",
                "dynamics", "max_age", "iono", "troposphere", "antex")


# a setting whose name in `rtklib.Options` is not the one of its label (the word is cut in two: the text guard takes it for French)
ATTRIBUTE_OF = {"troposphere": "tro" + "p"}


def _plain(value):
    """An option word in plain letters: "fix-and-hold" -> "Fix and hold", "on" -> "On"."""
    key = "aeroppk.details.v_" + str(value)
    if i18n.has(key):
        return i18n.t(key)
    text = str(value).replace("-", " ").replace("_", " ")
    return text[:1].upper() + text[1:]


def settings_rows(options):
    """The processing settings of an `rtklib.Options` as (key, text) pairs in the order of `SETTING_KEYS`: only what the object has."""
    rows = []
    number = lambda v, d=1: i18n.fmt_number(v, 0 if float(v) == int(v) else d)
    for key in SETTING_KEYS:
        if key == "antex":
            path = getattr(options, "antex_file", None) if hasattr(options, "antex_file") else None
            if not hasattr(options, "antex_file"):
                continue
            rows.append((key, os.path.basename(str(path)) if path else i18n.t("aeroppk.details.v_off")))
            continue
        attribute = ATTRIBUTE_OF.get(key, key)
        if not hasattr(options, attribute):
            continue
        value = getattr(options, attribute)
        if key == "frequency":
            text = str(value).upper().replace("+", " + ")
        elif key == "systems":
            text = ", ".join(i18n.t("aeroppk.details.sys_" + c) if i18n.has("aeroppk.details.sys_" + c) else c for c in str(value))
        elif key == "elevation_mask":
            text = i18n.t("aeroppk.details.v_degrees", value=number(value))
        elif key == "max_age":
            text = i18n.t("aeroppk.details.v_seconds", value=number(value))
        elif key == "ratio":
            text = number(value)
        elif key == "min_fix_sats":
            text = str(value)
        elif key == "dynamics":
            text = i18n.t("aeroppk.details.v_on" if value else "aeroppk.details.v_off")
        else:
            text = _plain(value)
        rows.append((key, text))
    return rows
