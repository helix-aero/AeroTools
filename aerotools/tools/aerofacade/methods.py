"""Aero-Facade: how the surface (the DEM) under each façade is built. Ranking, availability rules, explanations, warnings, time hints.

Pure Python, no Qt, no Metashape. Texts are translation KEYS (`facade.method.*` in aerotools/lang/en.py and fr.py); the functions
return keys and fields, and `text(key, **fields)` renders them with `aerotools.i18n.t`.

Order decided in the spec (best quality first), with the reasons shown to the operator as small grey text:

1. Dense cloud  - the most complete and cleanest surface. Best quality, and the fastest to use when it already exists. It is never
                  built by the tool: if the chunk has none the option is greyed with the reason.
2. Depth maps   - very good detail, quality close to the dense cloud, available in most projects, quick because only the area of the
                  façade is processed. The usual default when there is no dense cloud.
3. Tie points   - always there but sparse: holes and a rough surface. Offered with a warning ("quick look only").
"""

import math
from dataclasses import dataclass, field

from . import geometry as G
from .model import Availability, Margins, Method

ORDER = (Method.DENSE_CLOUD, Method.DEPTH_MAPS, Method.TIE_POINTS)
LONG_SECONDS = 600.0          # an estimate whose upper bound passes ten minutes gets the "this can take a long time" notice

# English fallback reasons (the backend's own reason is shown when it gives one)
DEFAULT_REASON = {
    Method.DENSE_CLOUD: "There is no dense cloud in this chunk. Building one takes a long time, so Aero-Facade does not offer it.",
    Method.DEPTH_MAPS: "There are no depth maps in this chunk.",
    Method.TIE_POINTS: "There are no tie points in this chunk.",
}


def as_availability(availability):
    """Accept an `Availability` or the dict returned by Backend.availability()."""
    return availability if isinstance(availability, Availability) else Availability.from_dict(availability)


_KEYS = {
    Method.DENSE_CLOUD: {"title": "facade.method.dense_cloud.title", "explain": "facade.method.dense_cloud.explain",
                         "quality": "facade.method.dense_cloud.quality", "reason": "facade.method.dense_cloud.missing", "warning": None},
    Method.DEPTH_MAPS: {"title": "facade.method.depth_maps.title", "explain": "facade.method.depth_maps.explain",
                        "quality": "facade.method.depth_maps.quality", "reason": "facade.method.depth_maps.missing", "warning": None},
    Method.TIE_POINTS: {"title": "facade.method.tie_points.title", "explain": "facade.method.tie_points.explain",
                        "quality": "facade.method.tie_points.quality", "reason": "facade.method.tie_points.missing",
                        "warning": "facade.method.tie_points.warning"},
}

# Keys of the other notices of screen 4 and of the margins screen (the wizard reads them from here or writes its own keys under facade.ui.)
METHOD_KEYS = {"title": "facade.method.title", "recommended": "facade.method.recommended", "unavailable": "facade.method.unavailable",
               "none_available": "facade.method.none_available", "long_notice": "facade.method.long_notice",
               "time_rough": "facade.method.time_rough"}
MARGIN_KEYS = {
    "title": "facade.margin.title", "left": "facade.margin.left", "right": "facade.margin.right", "top": "facade.margin.top",
    "bottom": "facade.margin.bottom", "depth": "facade.margin.depth", "link_on": "facade.margin.link_on",
    "link_off": "facade.margin.link_off", "depth_hint": "facade.margin.depth_hint", "apply_all": "facade.margin.apply_all",
    "apply_selected": "facade.margin.apply_selected", "pixel": "facade.margin.pixel", "pixel_hint": "facade.margin.pixel_hint",
    "size_info": "facade.margin.size_info", "pixel_heavy": "facade.margin.pixel_heavy", "pixel_too_big": "facade.margin.pixel_too_big",
    "schematic_caption": "facade.margin.schematic_caption", "front_view": "facade.margin.front_view", "top_view": "facade.margin.top_view",
}
DETECT_KEYS = {
    "running": "facade.detect.running", "found": "facade.detect.found", "none": "facade.detect.none",
    "few_points": "facade.detect.few_points", "cancelled": "facade.detect.cancelled", "failed": "facade.detect.failed",
    "size": "facade.detect.size", "facing": "facade.detect.facing",
}
DIRECTION_KEYS = {"N": "facade.detect.dir.N", "NE": "facade.detect.dir.NE", "E": "facade.detect.dir.E", "SE": "facade.detect.dir.SE",
                  "S": "facade.detect.dir.S", "SW": "facade.detect.dir.SW", "W": "facade.detect.dir.W", "NW": "facade.detect.dir.NW"}
QUALITY_KEYS = {"high": "facade.detect.quality.high", "medium": "facade.detect.quality.medium", "low": "facade.detect.quality.low"}
STAGE_KEYS = {"read": "facade.detect.stage.read", "select": "facade.detect.stage.select", "thin": "facade.detect.stage.thin",
              "search": "facade.detect.stage.search", "name": "facade.detect.stage.name", "done": "facade.detect.stage.done"}


def method_keys(method):
    """Translation keys of one method: title, one-line explanation, quality label, unavailable reason, and the warning (or None)."""
    return dict(_KEYS[Method.from_key(method)])


def direction_key(facade_or_name):
    """Key of the compass word of a façade's facing direction ('S' -> facade.detect.dir.S)."""
    name = facade_or_name if isinstance(facade_or_name, str) else facade_or_name.facing
    return DIRECTION_KEYS[name]


def quality_level(score):
    """'high' | 'medium' | 'low' from a façade's 0..1 quality score."""
    return "high" if score >= 0.75 else "medium" if score >= 0.45 else "low"


def facade_summary(facade, translate=None):
    """{'size': '66.2 m long, 21.8 m high', 'facing': 'facing south', 'quality': 'Reliable'} in the current language."""
    tr = translate or text
    return {
        "size": tr(DETECT_KEYS["size"], length="%.1f" % facade.length, height="%.1f" % facade.height),
        "facing": tr(DETECT_KEYS["facing"], direction=tr(direction_key(facade))),
        "quality": tr(QUALITY_KEYS[quality_level(facade.score)]),
    }


def stage_text(stage, translate=None, **fields):
    """Progress line of a detection stage ('read', 'select', 'thin', 'search', 'name', 'done') in the current language."""
    return (translate or text)(STAGE_KEYS[stage], **fields)


def rank_methods(availability):
    """[(Method, available, reason, default)] in quality order. `reason` is '' when available, else the backend's reason (or an English
    default); exactly one entry (the best available) has default=True, none when nothing is available."""
    av = as_availability(availability)
    best = choose_default(av)
    out = []
    for m in ORDER:
        ok, why = av.get(m)
        out.append((m, bool(ok), "" if ok else (why if why and why != "unknown" else DEFAULT_REASON[m]), m is best))
    return out


def choose_default(availability):
    """The method preselected on screen 4: the best available one in quality order, or None if none can be used."""
    av = as_availability(availability)
    for m in ORDER:
        if av.ok(m):
            return m
    return None


def is_available(availability, method):
    return as_availability(availability).ok(method)


@dataclass
class TimeHint:
    """What screen 4 shows under a method: `key` + `params` for the one-line hint, `rough` when the numbers are first estimates
    rather than measurements, `long` when the gentle 'this can take a long time' notice applies (key `facade.method.long_notice`)."""
    key: str = "facade.method.time_unknown"
    params: dict = field(default_factory=dict)
    rough: bool = False
    long: bool = False
    low_s: float = 0.0
    high_s: float = 0.0


def time_hint(method, facades, margins, existing=()):
    """Time hint of a method for the façades that are not removed (letters in `existing` already have a DEM and cost nothing)."""
    todo = [f for f in facades if not f.removed and f.letter not in existing]
    n = len(todo)
    if n == 0:
        return TimeHint("facade.method.time_none", {"n": 0}, False, False, 0.0, 0.0)
    est = G.estimate_batch_dems(method, todo, margins)
    if not est.known:
        return TimeHint("facade.method.time_unknown", {"n": n})
    rough = est.basis != "measured"
    long = est.high > LONG_SECONDS
    if est.high < 60.0:
        return TimeHint("facade.method.time_under", {"n": n}, rough, long, est.low, est.high)
    lo = max(1, int(round(est.low / 60.0)))
    hi = max(lo, int(math.ceil(est.high / 60.0)))
    if lo == hi:
        return TimeHint("facade.method.time_about", {"n": n, "low": lo}, rough, long, est.low, est.high)
    return TimeHint("facade.method.time_range", {"n": n, "low": lo, "high": hi}, rough, long, est.low, est.high)


def warnings_for(method, facades=None, margins=None, existing=()):
    """List of (key, fields) of the notices to show for the chosen method: the tie-point warning and the long-time notice."""
    out = []
    m = Method.from_key(method)
    wk = method_keys(m)["warning"]
    if wk:
        out.append((wk, {}))
    if facades:
        h = time_hint(m, facades, margins or Margins(), existing)
        if h.long:
            out.append(("facade.method.long_notice", {"low": h.params.get("low", 0), "high": h.params.get("high", 0)}))
    return out


def describe(availability, facades=None, margins=None, existing=(), translate=None):
    """Everything screen 4 needs, rendered: a list of dicts (method, key, available, default, title, explain, quality, reason, warning,
    time) in quality order. `translate(key, **fields)` defaults to `aerotools.i18n.t`."""
    tr = translate or text
    rows = []
    for m, ok, reason, default in rank_methods(availability):
        k = method_keys(m)
        hint = time_hint(m, facades, margins or Margins(), existing) if (facades is not None and ok) else None
        rows.append({
            "method": m, "key": m.value, "available": ok, "default": default,
            "title": tr(k["title"]), "explain": tr(k["explain"]), "quality": tr(k["quality"]),
            "reason": tr(k["reason"]) if not ok else "", "detail": reason, "warning": tr(k["warning"]) if k["warning"] else "",
            "time": (tr(hint.key, **hint.params) + (" " + tr("facade.method.time_rough") if hint.rough else "")) if hint else "",
            "long": bool(hint and hint.long),
        })
    return rows


def text(key, **fields):
    """Render a translation key with the current language (aerotools.i18n)."""
    from aerotools import i18n
    return i18n.t(key, **fields)
