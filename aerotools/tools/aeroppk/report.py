"""PDF report and CSV export of an Aero-PPK calculation.

The writer only reads the dictionary described in `reportdata.py` (see `reportdata.sample()`): it never touches the
Metashape project or the calculation objects. Three layers, so that the first two can be unit tested without Qt:

  pure helpers   formatting, quality / flag names, verdict, pagination plan, histogram bins, map geometry
  csv            `csv_rows(data)` and `write_csv(data, path)`
  painting       `build_pdf(data, path)` paints A4 pages at 300 dpi with QPdfWriter and QPainter, `render_pages(data)`
                 paints the same pages on QImage (used by the Qt self test to look at the result)

The PDF is a plain, airy document in the manner of the reports of the survey software: white pages, thin rules, sections that open with
one sentence, light grey tables, small neutral type. Green stands for what is good, every other value is grey: there is no orange and no
red anywhere. The logo is at the top left of the first page and in the header of the others. It draws with the helpers of
`aerotools.pdfkit`. Everything is vector (maps and charts included, no tiles, no network).

Texts: every visible text goes through `_t(key)`, the catalog of the application (`aerotools/lang/en.py`, keys
`aeroppk.report.*`); a key that is not in the catalog is shown as the key itself.
"""

import csv
import datetime
import math
import os

from ... import config, i18n, pdfkit
from ...pdfkit import A4_H, A4_W, DOT, EM_DASH, FAINT, GREEN, GREEN_DEEP, GRID, INK, INK_2, MARGIN, MINUS, MUTED, PAPER, PT_TO_DEVICE
from ...qt import QtCore, QtGui, QtWidgets
from . import gnsstime, photoclasses, summary

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

WHITE = "#ffffff"
GREY_DARK = "#4a4d54"
GREY_MID = "#8c8e96"
GREY_LIGHT = "#c4c5c9"
RULE = "#c9c9cc"                     # thin lines
HEAD_FILL = "#e9e9eb"                # header row of a table
BAND_FILL = "#e3e3e6"                # flight band of the photo table
ZEBRA = "#f4f4f5"                    # every other row
GOOD_TEXT = GREEN_DEEP               # a good figure written in green
LOGO_FILE = "helix-aero-AERO-degrade-512px.png"
PAGE_TOP = 98.0                      # first line of the body under the header of a page
FONT_SCALE = 1.18                    # every text of the report is drawn this much bigger than the size written in the code (see _PpkStyle)
TABLE_HEAD_H = 28.0                  # header row of the photo table

ROW_H = 16.0                         # photo table row
FLIGHT_ROW_H = 26.0                  # flight band inside the photo table
LEVEL_WARN, LEVEL_BAD = 1, 2
EARTH_RADIUS = 6371008.8
GROSS_LIMIT_M = 20.0

QUALITY_COLORS = {"fix": GREEN, "float": GREY_MID, "none": GREY_LIGHT}
LEVEL_COLORS = {"ok": GREEN, "warn": GREY_MID, "bad": GREY_LIGHT}
SYSTEM_NAMES = {"G": "GPS", "R": "GLONASS", "E": "Galileo", "C": "BeiDou", "J": "QZSS", "S": "SBAS", "I": "NavIC"}
FLAG_ORDER = ("no_solution", "float")
OPTION_KEYS = ("frequency", "systems", "elevation_mask", "ambiguity", "ratio", "dynamics", "solution_type", "iono", "tropo")
CSV_KEYS = ("flight", "photo", "time", "lat", "lon", "h", "sd_n", "sd_e", "sd_u", "quality", "level", "bases", "spread",
            "flags", "dn", "de", "du")

LAST_OVERFLOWS = []                  # content that did not fit a page during the last build (for the self test)


# --------------------------------------------------------------------------
# Texts
# --------------------------------------------------------------------------

def _has(key):
    try:
        return bool(i18n.has(key))
    except Exception:
        return False


def _t(key, **fields):
    """Text of `key` from the application catalog (aerotools/lang/en.py), or the key itself. Never raises."""
    try:
        return i18n.t(key, **fields)
    except Exception:
        return str(key)


def _text_or(keys, default, **fields):
    """First existing key of `keys`, else `default`."""
    for key in keys:
        if _has(key):
            return _t(key, **fields)
    return default


# --------------------------------------------------------------------------
# Formats (pure)
# --------------------------------------------------------------------------

def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) or math.isinf(number) else number


def fmt_num(value, decimals=3):
    """Number as the application formats it, an em dash when unknown."""
    return i18n.fmt_number(value, decimals) or EM_DASH


def fmt_signed(value, decimals=1):
    """Number with an explicit sign and a typographic minus."""
    number = _finite(value)
    if number is None:
        return EM_DASH
    text = fmt_num(number, decimals)
    if text.startswith("-"):
        return MINUS + text[1:]
    if number > 0 and float(text.replace(",", ".")) != 0.0:
        return "+" + text
    return text


def fmt_cm(value_m, decimals=1):
    """Metres in, centimetres out (no unit)."""
    number = _finite(value_m)
    return EM_DASH if number is None else fmt_num(number * 100.0, decimals)


def fmt_signed_cm(value_m, decimals=1):
    number = _finite(value_m)
    return EM_DASH if number is None else fmt_signed(number * 100.0, decimals)


def fmt_pct(value, decimals=1):
    number = _finite(value)
    return EM_DASH if number is None else fmt_num(number, decimals) + " %"


def fmt_km(value_km):
    number = _finite(value_km)
    if number is None:
        return EM_DASH
    return fmt_num(number, 1 if number >= 10 else 2) + " km"


def fmt_length_m(meters):
    """'250 m' or '5 km' for a scale bar or a ring label."""
    if meters >= 1000.0:
        return fmt_num(meters / 1000.0, 0 if meters % 1000.0 == 0 else 1) + " km"
    return fmt_num(meters, 0) + " m"


def fmt_time(value):
    """HH:MM:SS of a datetime, or a dash."""
    if isinstance(value, datetime.datetime):
        return value.strftime("%H:%M:%S")
    return EM_DASH


def fmt_datetime(value):
    """Date in the language format and HH:MM, or a dash."""
    if isinstance(value, datetime.datetime):
        return "{} {}".format(i18n.fmt_date(value), value.strftime("%H:%M"))
    if isinstance(value, datetime.date):
        return i18n.fmt_date(value)
    return EM_DASH


def fmt_duration(seconds):
    number = _finite(seconds)
    if number is None:
        return EM_DASH
    total = int(round(number))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return "{} h {:02d} min".format(hours, minutes)
    if minutes:
        return "{} min {:02d} s".format(minutes, secs)
    return "{} s".format(secs)


def systems_names(letters):
    """'GRE' -> 'GPS, GLONASS, Galileo'."""
    names = []
    for letter in str(letters or ""):
        name = SYSTEM_NAMES.get(letter.upper(), letter.upper())
        if name not in names:
            names.append(name)
    return ", ".join(names)


def quality_key(q):
    """RTKLIB / combination status: 1 fixed, 2 float, anything else no usable solution."""
    return {1: "fix", 2: "float"}.get(q, "none")


def level_key(level):
    return {0: "ok", 1: "warn"}.get(level, "bad")


def quality_label(q):
    return _t("aeroppk.report.q_" + quality_key(q))


def level_label(level):
    return _t("aeroppk.report.lv_" + level_key(level))


def flag_label(flag, short=False):
    """Name of a combination flag (`float`, `spread`, ...). Unknown flags are shown as they are."""
    key = "aeroppk.report.flag_{}_{}".format("short" if short else "long", flag)
    return _text_or((key,), str(flag).replace("_", " "))


def option_label(key):
    return _text_or(("aeroppk.report.opt_" + str(key),), str(key).replace("_", " ").capitalize())


def warning_level(entry):
    level = str(entry[0]) if entry else "warning"
    return level if level in ("info", "warning", "error") else "warning"


def warning_text(entry):
    """Text of a warning `(level, key, fields)`. An unknown key is shown as it is."""
    key = str(entry[1]) if len(entry) > 1 else ""
    fields = dict(entry[2]) if len(entry) > 2 and entry[2] else {}
    name = key.rsplit(".", 1)[-1]
    return _text_or((key, "aeroppk.report.warn_" + name), key, **fields)


def need_texts(key):
    """(name, why) of a checklist line. `aeroppk.need.<key>` first, then our own fallback, then the key."""
    name = _text_or(("aeroppk.need." + key, "aeroppk.report.need_" + key), key)
    why = _text_or(("aeroppk.why." + key, "aeroppk.report.why_" + key), "")
    return name, why


def safe_name(text, default="Project"):
    return pdfkit.safe_name(text, default)


def suggested_filename(data, ext=".pdf", today=None):
    """`YYYY-MM-DD_PPK_report_<project>.pdf`, `..._positions_...csv`."""
    generated = data.get("generated")
    day = today or (generated.date() if isinstance(generated, datetime.datetime) else generated) or datetime.date.today()
    kind = _t("aeroppk.report.file_table" if str(ext).lower() == ".csv" else "aeroppk.report.file_report")
    return "{}_{}_{}{}".format(day.strftime("%Y-%m-%d"), kind, safe_name(data.get("title")), ext)


# --------------------------------------------------------------------------
# Summary values (pure)
# --------------------------------------------------------------------------

def _rows(flight):
    return flight.get("photos_rows") or []


def flight_counts(flight):
    """{"photos", "fix", "float", "none", "ok", "warn", "bad"} of a flight (quality dictionary, else counted on the rows)."""
    quality = flight.get("quality") or {}
    rows = _rows(flight)
    out = {"photos": quality.get("photos", len(rows))}
    out["fix"] = quality.get("fix", sum(1 for r in rows if quality_key(r.get("q")) == "fix"))
    out["float"] = quality.get("float", sum(1 for r in rows if quality_key(r.get("q")) == "float"))
    out["none"] = quality.get("none", sum(1 for r in rows if quality_key(r.get("q")) == "none"))
    out["ok"] = quality.get("ok", sum(1 for r in rows if r.get("level") == 0))
    out["warn"] = quality.get("warn", sum(1 for r in rows if r.get("level") == LEVEL_WARN))
    out["bad"] = quality.get("bad", sum(1 for r in rows if r.get("level") == LEVEL_BAD))
    return out


def totals(data):
    """Totals of the report: `data["totals"]` completed, when keys are missing, from the flights."""
    given = data.get("totals") or {}
    out = {}
    flights = [flight_counts(f) for f in data.get("flights") or []]
    for key in ("photos", "fix", "float", "none", "warn", "bad", "ok"):
        out[key] = given[key] if key in given else sum(c[key] for c in flights)
    out["fix_pct"] = given["fix_pct"] if "fix_pct" in given else _pct(out["fix"], out["photos"])
    return out


def _pct(part, whole):
    return 100.0 * part / whole if whole else 0.0


def collect_warnings(data):
    """Every warning of the report as [{"level", "text", "flight"}], errors first. The same text is listed once: with no
    flight name when it comes from the report itself or from every flight, else with the names of its flights."""
    merged, order = {}, []
    all_flights = [f.get("name") or "" for f in data.get("flights") or []]
    entries = [(None, w) for w in data.get("warnings") or []]
    for name in all_flights:
        flight = next(f for f in data.get("flights") or [] if (f.get("name") or "") == name)
        entries.extend((name, w) for w in flight.get("warnings") or [])
    for name, entry in entries:
        mark = (warning_level(entry), warning_text(entry))
        if mark not in merged:
            merged[mark] = {"names": [], "global": False}
            order.append(mark)
        if name is None:
            merged[mark]["global"] = True
        elif name not in merged[mark]["names"]:
            merged[mark]["names"].append(name)
    out = []
    for mark in order:
        names = merged[mark]["names"]
        everywhere = len(all_flights) > 1 and len(names) == len(set(all_flights))
        flight = None if merged[mark]["global"] or not names or everywhere else ", ".join(names)
        out.append({"level": mark[0], "text": mark[1], "flight": flight})
    rank = {"error": 0, "warning": 1, "info": 2}
    out.sort(key=lambda w: rank[w["level"]])
    return out


def base_codes(data):
    """Distinct base station codes over all flights, in order of appearance."""
    codes = []
    for flight in data.get("flights") or []:
        for base in flight.get("bases") or []:
            code = base.get("code")
            if code and code not in codes:
                codes.append(code)
    return codes


def suspect_codes(data):
    codes = []
    for flight in data.get("flights") or []:
        for base in flight.get("bases") or []:
            if base.get("suspect") and base.get("code") not in codes:
                codes.append(base.get("code"))
    return codes


def verdict(data):
    """Overall verdict of the report: ("ok" | "check" | "stop", number of photos to check).

    stop: an error warning, a gross error against the drone's own position, no photo, a blocking file missing,
    more than 10 % of photos not reliable, or fewer than half of the photos fixed.
    check: any warning, suspect base, or photo highlighted in the table. ok: nothing of the above.
    """
    tot = totals(data)
    to_check = tot["warn"] + tot["bad"]
    warnings = collect_warnings(data)
    severe = any(w["level"] == "error" for w in warnings)
    for flight in data.get("flights") or []:
        if (flight.get("onboard") or {}).get("gross_error"):
            severe = True
        if any(f.get("level") == "blocking" and not f.get("present") for f in flight.get("files") or []):
            severe = True
    if tot["photos"] <= 0 or severe or tot["bad"] > 0.10 * tot["photos"] or tot["fix_pct"] < 50.0:
        return "stop", to_check
    if to_check or suspect_codes(data) or any(w["level"] == "warning" for w in warnings):
        return "check", to_check
    return "ok", 0


def sd_h(row):
    """Combined horizontal standard deviation (m) of a photo row, or None."""
    n, e = _finite(row.get("sdn")), _finite(row.get("sde"))
    return None if n is None or e is None else math.hypot(n, e)


# --------------------------------------------------------------------------
# Histograms (pure)
# --------------------------------------------------------------------------

def _quantile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return None
    position = fraction * (len(ordered) - 1)
    low = int(math.floor(position))
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def robust_top(values, divisions=4):
    """Upper bound of a histogram axis that starts at 0 (or is centred on 0 for differences): the largest absolute value,
    or four times the 75th percentile when a few values are far away (those go to an extra bar at the end).
    Returns (top, step) of a nice axis, None without values."""
    good = [abs(v) for v in values if _finite(v) is not None]
    if not good:
        return None
    peak = max(good)
    cap = 4.0 * _quantile(good, 0.75)
    return pdfkit.nice_top(min(peak, cap) if cap > 0 else peak, divisions)


def bin_counts(values, low, high, n_bins):
    """Counts of `values` in `n_bins` equal bins of [low, high]. Returns (counts, below, above): the values outside the range
    are counted apart, not put in the first or the last bin."""
    counts = [0] * n_bins
    below = above = 0
    width = (high - low) / float(n_bins)
    for value in values:
        number = _finite(value)
        if number is None:
            continue
        if number < low:
            below += 1
        elif number > high:
            above += 1
        else:
            counts[min(int(math.floor((number - low) / width)) if width > 0 else 0, n_bins - 1)] += 1
    return counts, below, above


def histogram(values, signed=False):
    """Everything a histogram needs, or None without values:
    {"edges", "counts", "below", "above", "n", "low", "high", "step", "k"}.

    Unsigned values (standard deviations, spreads) start at 0, signed ones (differences) are centred on 0. Bars are
    `step / k` wide, the axis is labelled every `step`. Values beyond the axis are counted in `below` / `above`."""
    good = [float(v) for v in values if _finite(v) is not None]
    if not good:
        return None
    top, step = robust_top(good, 3 if signed else 4)
    low, high = (-top, top) if signed else (0.0, top)
    span = high - low
    k = 1
    for candidate in (1, 2, 4, 5, 10):
        k = candidate
        if span / (step / candidate) >= (14 if signed else 14):
            break
    n_bins = int(round(span / (step / k)))
    counts, below, above = bin_counts(good, low, high, n_bins)
    width = span / float(n_bins)
    return {"edges": [low + i * width for i in range(n_bins + 1)], "counts": counts, "below": below, "above": above,
            "n": len(good), "low": low, "high": high, "step": step, "k": k}


# --------------------------------------------------------------------------
# Pagination plan (pure)
# --------------------------------------------------------------------------

def table_capacity(height=A4_H):
    return height - 46.0 - PAGE_TOP - TABLE_HEAD_H - 20.0


def table_plan(counts, height=A4_H):
    """Spread the photo rows of every flight on pages. `counts`: photos per flight.

    Returns a list of pages; a page is a list of ("flight", i, continued), ("rows", i, start, stop) or ("empty", i).
    A flight band is never left alone at the bottom of a page: at least three rows follow it."""
    capacity = table_capacity(height)
    pages, page, used = [], [], 0.0
    for index, n in enumerate(counts):
        start, continued = 0, False
        while True:
            need = FLIGHT_ROW_H + ROW_H * (min(n - start, 3) if n > start else 1)
            if page and used + need > capacity:
                pages.append(page)
                page, used = [], 0.0
            page.append(("flight", index, continued))
            used += FLIGHT_ROW_H
            if n == 0:
                page.append(("empty", index))
                used += ROW_H
                break
            take = min(int((capacity - used) // ROW_H), n - start)
            page.append(("rows", index, start, start + take))
            used += take * ROW_H
            start += take
            if start >= n:
                break
            pages.append(page)
            page, used = [], 0.0
            continued = True
    if page:
        pages.append(page)
    return pages


def photo_table_pages(photos, height=A4_H):
    """About how many pages the table of `photos` photos takes: the number of photos over the rows of a page (the flight bands add a few)."""
    per_page = max(1, int((table_capacity(height) - FLIGHT_ROW_H) // ROW_H))          # every page starts with a flight band
    return int(math.ceil(max(int(photos), 0) / float(per_page)))


def page_plan(data, height=A4_H):
    """Every page of the PDF, in order, as tuples: ("cover",), then for each flight ("imported", i), ("stations", i), ("solution", i),
    ("maps", i), ("quality", i), then the photo table ("table", rows, only when `data["table"]` is not False), ("method", 1), ("method", 2) and ("method", 3) which ends with the disclaimer."""
    flights = data.get("flights") or []
    plan = [("cover",)]
    for index in range(len(flights)):
        for kind in ("imported", "stations", "solution", "maps", "quality"):
            plan.append((kind, index))
    if data.get("table", True):                                  # the owner may leave the table of every photo out (data["table"] False)
        for page in table_plan([len(_rows(f)) for f in flights], height):
            plan.append(("table", page))
    plan.append(("method", 1))
    plan.append(("method", 2))
    plan.append(("method", 3))
    return plan


def page_count(data):
    """Number of pages `build_pdf` writes."""
    return len(page_plan(data))


# --------------------------------------------------------------------------
# Map geometry (pure)
# --------------------------------------------------------------------------

def local_xy(lat, lon, lat0, lon0):
    """East and north metres of (lat, lon) from (lat0, lon0): flat earth, good for a few tens of kilometres."""
    north = math.radians(lat - lat0) * EARTH_RADIUS
    east = math.radians(lon - lon0) * EARTH_RADIUS * math.cos(math.radians(lat0))
    return east, north


def nice_length(meters):
    """Largest 1, 2 or 5 x 10^n not above `meters`."""
    if meters <= 0:
        return 1.0
    magnitude = 10 ** math.floor(math.log10(meters))
    best = magnitude
    for factor in (1, 2, 5, 10):
        if factor * magnitude <= meters * 1.0000001:
            best = factor * magnitude
    return best


def ring_step(half_extent_m):
    """Spacing of the distance rings: a nice length giving two to four rings inside the map."""
    return nice_length(max(half_extent_m, 1.0) / 3.0)


class MapView(object):
    """Linear map from metres (east, north) to points on the page, north up, same scale both ways."""

    def __init__(self, rect, bbox):
        x, y, w, h = rect
        self.rect = rect
        width = max(bbox[2] - bbox[0], 1e-6)
        height = max(bbox[3] - bbox[1], 1e-6)
        self.k = min(w / width, h / height)                     # points per metre
        cx, cy = (bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0
        self.ox = x + w / 2.0 - cx * self.k
        self.oy = y + h / 2.0 + cy * self.k

    def pt(self, east, north):
        return self.ox + east * self.k, self.oy - north * self.k

    def inside(self, east, north, margin=0.0):
        x, y, w, h = self.rect
        px, py = self.pt(east, north)
        return x + margin <= px <= x + w - margin and y + margin <= py <= y + h - margin

    def edge_point(self, east, north, margin=12.0):
        """Where the line from the middle of the map to (east, north) leaves the rectangle (inset by `margin`),
        and the angle of that direction in radians (page axes: x right, y down)."""
        x, y, w, h = self.rect
        cx, cy = x + w / 2.0, y + h / 2.0
        px, py = self.pt(east, north)
        dx, dy = px - cx, py - cy
        if abs(dx) < 1e-9 and abs(dy) < 1e-9:
            return cx, cy, 0.0
        half_w, half_h = w / 2.0 - margin, h / 2.0 - margin
        scale = min(half_w / abs(dx) if dx else float("inf"), half_h / abs(dy) if dy else float("inf"))
        return cx + dx * scale, cy + dy * scale, math.atan2(dy, dx)


def flight_geometry(flight):
    """Positions of a flight and its bases in metres around the flight centre, for the maps.

    {"origin": (lat, lon) or None, "track": [(e, n, q)], "photos": [(e, n, level)], "bases": [(e, n, base)]}"""
    track = []
    for point in flight.get("track") or []:
        lat, lon = _finite(point[0]), _finite(point[1])
        if lat is not None and lon is not None:
            track.append((lat, lon, point[2] if len(point) > 2 else 0))
    photos = []
    for row in _rows(flight):
        lat, lon = _finite(row.get("lat")), _finite(row.get("lon"))
        if lat is not None and lon is not None:
            photos.append((lat, lon, row.get("level", 0), row.get("q", 0)))
    if len(track) < 2:
        track = [(p[0], p[1], p[3]) for p in photos]
    origin = flight.get("center")
    if origin and _finite(origin[0]) is not None and _finite(origin[1]) is not None:
        origin = (float(origin[0]), float(origin[1]))
    elif track:
        origin = (sum(p[0] for p in track) / len(track), sum(p[1] for p in track) / len(track))
    else:
        origin = None
    out = {"origin": origin, "track": [], "photos": [], "bases": []}
    if origin is None:
        return out
    for lat, lon, q in track:
        east, north = local_xy(lat, lon, origin[0], origin[1])
        out["track"].append((east, north, q))
    for lat, lon, level, _q in photos:
        east, north = local_xy(lat, lon, origin[0], origin[1])
        out["photos"].append((east, north, level))
    for base in flight.get("bases") or []:
        lat, lon = _finite(base.get("lat")), _finite(base.get("lon"))
        if lat is not None and lon is not None:
            east, north = local_xy(lat, lon, origin[0], origin[1])
            out["bases"].append((east, north, base))
    return out


def track_segments(track):
    """Cut a track [(e, n, q)] into runs of the same quality: [(quality key, [(e, n), ...])]. Runs share their end points
    so the line stays continuous."""
    runs = []
    for index, (east, north, q) in enumerate(track):
        key = quality_key(q)
        if runs and runs[-1][0] == key:
            runs[-1][1].append((east, north))
        else:
            start = [(track[index - 1][0], track[index - 1][1])] if index else []
            runs.append((key, start + [(east, north)]))
    return runs


# --------------------------------------------------------------------------
# CSV
# --------------------------------------------------------------------------

def _csv_num(value, decimals):
    number = _finite(value)
    return "" if number is None else i18n.fmt_number(number, decimals)


def _csv_time(value):
    if isinstance(value, datetime.datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S") + i18n.decimal_separator() + "{:03d}".format(value.microsecond // 1000)
    return ""


def csv_header():
    return [_t("aeroppk.report.csv_" + key) for key in CSV_KEYS]


def csv_rows(data):
    """CSV lines (lists of text), header included: one row per photo over all flights."""
    rows = [csv_header()]
    for flight in data.get("flights") or []:
        for row in _rows(flight):
            rows.append([
                flight.get("name") or "", row.get("name") or "", _csv_time(row.get("time_utc")),
                _csv_num(row.get("lat"), 9), _csv_num(row.get("lon"), 9), _csv_num(row.get("h"), 4),
                _csv_num(row.get("sdn"), 4), _csv_num(row.get("sde"), 4), _csv_num(row.get("sdu"), 4),
                quality_key(row.get("q")), level_key(row.get("level", 0)), "+".join(row.get("used") or []),
                _csv_num(row.get("spread"), 4), "|".join(row.get("flags") or []),
                _csv_num(row.get("dn"), 4), _csv_num(row.get("de"), 4), _csv_num(row.get("du"), 4)])
    return rows


def write_csv(data, path):
    """Write the CSV, UTF-8 with BOM (Excel), with the delimiter and decimal separator of the application formats."""
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle, delimiter=i18n.csv_delimiter(), lineterminator="\r\n")
        for row in csv_rows(data):
            writer.writerow(row)
    return path

# --------------------------------------------------------------------------
# Drawing tools
# --------------------------------------------------------------------------

def _qcolor(color, alpha=1.0):
    return pdfkit.qcolor(color, alpha)


class _PpkStyle(pdfkit.Style):
    """The fonts of the report: the sizes written in the code are multiplied by `scale` (the report is read on paper and on screen, small type
    was too small). The shared kit is left alone: only this report asks for the bigger type."""

    def __init__(self):
        pdfkit.Style.__init__(self)
        self.scale = FONT_SCALE

    def body_font(self, px, *args, **kwargs):
        return pdfkit.Style.body_font(self, px * self.scale, *args, **kwargs)

    def display_font(self, px, weight):
        return pdfkit.Style.display_font(self, px * self.scale, weight)


class _PpkCanvas(pdfkit.Canvas):
    """The canvas of `aerotools.pdfkit` plus what the maps and charts need, and a record of content that overflows a page.
    A box is a thin grey frame here, never a rounded card: the report is plain on purpose."""

    def __init__(self, painter, style):
        pdfkit.Canvas.__init__(self, painter, style)
        self.overflows = []

    def card(self, x, y, w, h, radius=0.0):
        self.rect(x, y, w, h, fill=None, line=RULE, line_w=0.5)

    def guard(self, y, limit, what):
        if y > limit + 0.01:
            self.overflows.append((what, round(y, 1), round(limit, 1)))

    def polyline(self, points, color, width=1.0, alpha=1.0, dash=None):
        if len(points) < 2:
            return
        path = QtGui.QPainterPath()
        path.moveTo(QtCore.QPointF(points[0][0], points[0][1]))
        for px, py in points[1:]:
            path.lineTo(QtCore.QPointF(px, py))
        pen = QtGui.QPen(_qcolor(color, alpha), width)
        pen.setJoinStyle(QtCore.Qt.RoundJoin)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        if dash:
            pen.setDashPattern([float(d) for d in dash])
        self.p.setPen(pen)
        self.p.setBrush(QtCore.Qt.NoBrush)
        self.p.drawPath(path)

    def polygon(self, points, fill=None, line=None, line_w=0.6, alpha=1.0):
        self.p.setPen(QtGui.QPen(_qcolor(line), line_w) if line else QtCore.Qt.NoPen)
        self.p.setBrush(_qcolor(fill, alpha) if fill else QtCore.Qt.NoBrush)
        self.p.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(px, py) for px, py in points]))

    def triangle(self, cx, cy, r, fill, line="#ffffff", line_w=0.8, angle=None):
        """Triangle of radius r, pointing up (or toward `angle`, page axes)."""
        base = -math.pi / 2.0 if angle is None else angle
        points = [(cx + r * math.cos(base + k * 2.0 * math.pi / 3.0), cy + r * math.sin(base + k * 2.0 * math.pi / 3.0))
                  for k in range(3)]
        self.polygon(points, fill=fill, line=line, line_w=line_w)

    def clip(self, x, y, w, h):
        self.p.save()
        self.p.setClipRect(QtCore.QRectF(x, y, w, h))

    def unclip(self):
        self.p.restore()

    def icon(self, cx, cy, kind, color, r=6.0):
        """Round mark: ok (white tick on the colour), dash (white bar)."""
        self.circle(cx, cy, r, fill=color)
        white = "#ffffff"
        if kind == "ok":
            self.polyline([(cx - r * 0.48, cy + r * 0.02), (cx - r * 0.1, cy + r * 0.42), (cx + r * 0.52, cy - r * 0.38)], white, 1.3)
        else:
            self.line(cx - r * 0.45, cy, cx + r * 0.45, cy, white, 1.4)

    def tick(self, cx, cy, ok=True, r=4.6):
        """The mark of a list: a green tick, or a grey bar for what is not there."""
        self.icon(cx, cy, "ok" if ok else "dash", GREEN if ok else GREY_LIGHT, r)


_LOGO = []


def _logo():
    """The AERO logo (the same file as the panel of Aero-GCP), or None when the file is not there."""
    if not _LOGO:
        image = QtGui.QImage(os.path.join(config.ROOT, "assets", "logo", LOGO_FILE))
        _LOGO.append(None if image.isNull() else image)
    return _LOGO[0]


def _draw_logo(cv, x, y, height):
    """The logo with its top left corner at (x, y). Returns its width."""
    image = _logo()
    if image is None:
        return 0.0
    width = height * image.width() / float(image.height())
    cv.image(image, x, y, width, height)
    return width


def _fmt_date(value):
    return i18n.fmt_date(value)


def _footer(cv, W, H, page, total):
    y = H - 34.0
    cv.line(MARGIN, y, W - MARGIN, y, RULE, 0.5)
    cv.text(MARGIN, y + 14.0, "Aero-PPK", 7.2, MUTED)
    cv.text(W - MARGIN, y + 14.0, _t("aeroppk.report.footer_page", page=page, total=total), 7.2, MUTED, align="r")


def _page_bg(cv, W, H):
    cv.rect(0, 0, W, H, fill=WHITE)


def _page_head(cv, W, data, title, subtitle=None):
    _draw_logo(cv, MARGIN, 22.0, 12.0)
    right = "{}  {}  {}".format(_t("aeroppk.report.title"), DOT, data.get("title") or "")
    cv.text(W - MARGIN, 32.0, cv.fit(right, 7.4, 330), 7.4, MUTED, align="r")
    cv.line(MARGIN, 42.0, W - MARGIN, 42.0, RULE, 0.5)
    cv.text(MARGIN, 66.0, title, 13.5, INK, "display", 2)
    if subtitle:
        cv.text(MARGIN, 82.0, cv.fit(subtitle, 7.8, W - 2 * MARGIN), 7.8, MUTED)


def _limit(H):
    return H - 46.0


def _para(cv, x, y, w, text, px=8.0, color=INK_2, weight=1, spacing=1.38, max_lines=60):
    """Write a paragraph whose top is `y`. Returns its height."""
    lines = cv.wrap(text, px, w, "body", weight, max_lines)
    size = px * cv.st.scale
    for i, line in enumerate(lines):
        cv.text(x, y + size + i * size * spacing, line, px, color, "body", weight)
    return len(lines) * size * spacing + (size * 0.3 if lines else 0.0)


def _para_h(cv, w, text, px=8.0, weight=1, spacing=1.38, max_lines=60):
    lines = cv.wrap(text, px, w, "body", weight, max_lines)
    size = px * cv.st.scale
    return len(lines) * size * spacing + (size * 0.3 if lines else 0.0)


def _section(cv, x, y, w, title, right=None, sub=None):
    """A heading with a thin rule under it and, when given, one short sentence that says what the section is. Returns the height it takes."""
    cv.text(x, y + 11.0, title, 9.6, INK, "display", 2)
    if right:
        cv.text(x + w, y + 11.0, cv.fit(right, 7.2, w * 0.6), 7.2, MUTED, align="r")
    cv.line(x, y + 16.0, x + w, y + 16.0, RULE, 0.5)
    if sub:
        return 25.0 + _para(cv, x, y + 19.0, w, sub, 7.4, MUTED) + 2.0
    return 25.0


def _kv(cv, x, y, w, rows, columns=2, label_w=74.0):
    """Label and value pairs in `columns` columns, filled column by column. A value is a text or (text, colour). Returns the height."""
    per = int(math.ceil(len(rows) / float(columns))) or 1
    col_w = w / float(columns)
    for i, (label, value) in enumerate(rows):
        col, row = divmod(i, per)
        px = x + col * col_w
        color = INK
        if isinstance(value, tuple):
            value, color = value
        by = y + 8.5 + row * 12.5
        cv.text(px, by, cv.fit(label, 7.4, label_w - 4.0), 7.4, MUTED)
        cv.text(px + label_w, by, cv.fit(value, 7.8, col_w - label_w - 12.0), 7.8, color, "body", 1)
    return per * 12.5 + 4.0


def _table(cv, x, y, w, cols, rows, row_h=15.5, head_h=19.0, px=7.4):
    """A plain table: grey header row, thin lines. `cols`: [(key, header, width (0: takes what is left), align)]. A row is a dict key -> cell; a
    cell is a text, (text, colour) or (text, colour, bold), and a dict {"tick": True or False, "text": ...} puts a mark before its text.
    Returns the height."""
    flex = w - sum(c[2] for c in cols)
    left, spots = x, []
    for key, head, width, align in cols:
        width = width or flex
        spots.append((key, head, left, left + width, align))
        left += width
    height = head_h + max(len(rows), 1) * row_h
    cv.rect(x, y, w, head_h, fill=HEAD_FILL)
    for _key, head, a, b, align in spots:
        cv.text(b - 4.0 if align == "r" else ((a + b) / 2.0 if align == "c" else a + 4.0), y + head_h - 6.0,
                cv.fit(head, px - 0.4, b - a - 6.0, "body", 2), px - 0.4, GREY_DARK, "body", 2, align=align)
    for i, row in enumerate(rows):
        ry = y + head_h + i * row_h
        if i % 2 == 0:
            cv.rect(x, ry, w, row_h, fill=ZEBRA)
        for key, _head, a, b, align in spots:
            cell = row.get(key, "")
            color, bold, mark = INK_2, False, None
            if isinstance(cell, dict) and "pill" in cell:
                _pill(cv, (a + b) / 2.0 - cv.width(cell["pill"], 6.6, "body", 2) / 2.0 - 7.0, ry + row_h / 2.0, cell["pill"], cell.get("good", True))
                continue
            if isinstance(cell, dict):
                mark, cell = cell.get("tick"), cell.get("text", "")
            if isinstance(cell, tuple):
                color = cell[1] if len(cell) > 1 else color
                bold = bool(cell[2]) if len(cell) > 2 else False
                cell = cell[0]
            tx = a + 4.0
            if mark is not None:
                cv.tick(a + 9.0, ry + row_h / 2.0, bool(mark), 3.8)
                tx = a + 18.0
            text = cv.fit(str(cell), px, b - tx - 4.0, "body", 2 if bold else 1)
            cv.text(b - 4.0 if align == "r" else ((a + b) / 2.0 if align == "c" else tx), ry + row_h - 4.6, text, px, color, "body", 2 if bold else 1,
                    align=align)
    cv.rect(x, y, w, height, fill=None, line=RULE, line_w=0.5)
    return height


def _flight_label(flight, index, count, with_name=True):
    parts = [flight.get("name") or ""] if with_name else []
    if flight.get("drone"):
        parts.append(flight["drone"])
    start, end = flight.get("start_utc"), flight.get("end_utc")
    if isinstance(start, datetime.datetime) and isinstance(end, datetime.datetime):
        parts.append(_t("aeroppk.report.flight_window", start=fmt_datetime(start), end=fmt_time(end)))
    parts.append(_t("aeroppk.report.n_photos", n=flight_counts(flight)["photos"]))
    text = "  {}  ".format(DOT).join(p for p in parts if p)
    if count > 1:
        text = "{}  {}  {}".format(_t("aeroppk.report.flight_n", i=index + 1, n=count), DOT, text)
    return text


def _good_text(kind, value):
    """Colour of a figure of the report: green when it is good, plain grey otherwise (see `summary.tone`)."""
    return GOOD_TEXT if summary.tone(kind, value) == "good" else GREY_DARK


# --------------------------------------------------------------------------
# Page 1: general information
# --------------------------------------------------------------------------

def _own_base(data):
    """True when the base files and position were supplied by the user (no public network)."""
    return (data.get("source") or {}).get("kind") == "own"


def _pill(cv, x, y, text, good=True, align="l", px=6.6):
    """A small rounded label: green with white text for what is good or present, grey for the rest. `y` is the middle line."""
    width = cv.width(text, px, "body", 2) + 14.0
    left = x - width if align == "r" else x
    cv.rect(left, y - 6.2, width, 12.4, fill=GREEN if good else GREY_LIGHT, radius=6.2)
    cv.text(left + 7.0, y + px * cv.st.scale * 0.34, text, px, "#ffffff" if good else GREY_DARK, "body", 2)
    return width


def _info_table(cv, x, y, w, rows, label_w=130.0, row_h=16.5, px=7.6):
    """Label and value rows, as in a form: the label in bold on the left, the value centred, light grey on every other row.
    A value is a text, (text, colour) or {"pill": text, "good": bool}. Returns the height."""
    for i, (label, value) in enumerate(rows):
        ry = y + i * row_h
        if i % 2 == 0:
            cv.rect(x, ry, w, row_h, fill=ZEBRA)
        cv.text(x + label_w / 2.0, ry + row_h - 4.8, cv.fit(label, px, label_w - 8.0, "body", 2), px, GREY_DARK, "body", 2, align="c")
        cx = x + label_w + (w - label_w) / 2.0
        if isinstance(value, dict):
            _pill(cv, cx - 16.0, ry + row_h / 2.0, value["pill"], value.get("good", True))
            continue
        color = INK_2
        if isinstance(value, tuple):
            value, color = value
        cv.text(cx, ry + row_h - 4.8, cv.fit(str(value), px, w - label_w - 12.0), px, color, "body", 1, align="c")
    height = max(len(rows), 1) * row_h
    cv.rect(x, y, w, height, fill=None, line=RULE, line_w=0.5)
    cv.line(x + label_w, y, x + label_w, y + height, RULE, 0.4)
    return height


def _all_points(data):
    """[(east, north, q)] of the exposures of every flight around the first flight's centre, for the small map of the cover."""
    flights = data.get("flights") or []
    origin = None
    for flight in flights:
        center = flight.get("center")
        if center and _finite(center[0]) is not None and _finite(center[1]) is not None:
            origin = (float(center[0]), float(center[1]))
            break
    out = []
    if origin is None:
        return out
    for flight in flights:
        for row in _rows(flight):
            lat, lon = _finite(row.get("lat")), _finite(row.get("lon"))
            if lat is not None and lon is not None:
                east, north = local_xy(lat, lon, origin[0], origin[1])
                out.append((east, north, row.get("q", 0)))
    return out


def _thumb_map(cv, x, y, w, h, points):
    """A small map of the exposures coloured by solution quality, north up, with the scale. No background map (nothing is downloaded)."""
    cv.rect(x, y, w, h, fill=ZEBRA, line=RULE, line_w=0.5)
    if not points:
        pdfkit.empty_note(cv, x, y, w, h, _t("aeroppk.report.no_data"))
        return
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    cx0, cy0 = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
    half_w = max((max(xs) - min(xs)) / 2.0, 15.0) * 1.12
    half_h = max((max(ys) - min(ys)) / 2.0, 15.0) * 1.12
    rect = (x, y, w, h)
    view = MapView((x + 8.0, y + 8.0, w - 16.0, h - 16.0), (cx0 - half_w, cy0 - half_h, cx0 + half_w, cy0 + half_h))
    cv.clip(x, y, w, h)
    for east, north, q in points:
        px, py = view.pt(east, north)
        cv.circle(px, py, 1.7, fill=_quality_color(quality_key(q)))
    cv.unclip()
    _draw_compass_and_scale(cv, view, rect)


def _status_pill(cv, W, y, data):
    code, _n = verdict(data)
    _pill(cv, W - MARGIN, y, _t("aeroppk.report.status_" + code), code == "ok", "r", 7.0)


def _general_rows(data):
    flights = data.get("flights") or []
    tool = data.get("tool") or {}
    starts = [f["start_utc"] for f in flights if isinstance(f.get("start_utc"), datetime.datetime)]
    ends = [f["end_utc"] for f in flights if isinstance(f.get("end_utc"), datetime.datetime)]
    devices = []
    for flight in flights:
        if flight.get("drone") and flight["drone"] not in devices:
            devices.append(flight["drone"])
    centers = [f["center"] for f in flights if f.get("center")]
    place = EM_DASH
    if centers and _finite(centers[0][0]) is not None and _finite(centers[0][1]) is not None:
        place = "{}, {}".format(fmt_num(centers[0][0], 5), fmt_num(centers[0][1], 5))
    rows = [(_t("aeroppk.report.band_project"), data.get("title") or EM_DASH),
            (_t("aeroppk.report.set_device"), ", ".join(devices) or EM_DASH),
            (_t("aeroppk.report.set_correction"), _t("aeroppk.report.set_ppk")),
            (_t("aeroppk.report.set_start"), fmt_datetime(min(starts)) if starts else EM_DASH),
            (_t("aeroppk.report.set_end"), fmt_datetime(max(ends)) if ends else EM_DASH),
            (_t("aeroppk.report.set_location"), place),
            (_t("aeroppk.report.kpi_photos"), str(totals(data)["photos"])), (_t("aeroppk.report.col_flights"), str(len(flights))),
            (_t("aeroppk.report.band_frame"), (data.get("frame") or {}).get("label") or EM_DASH),
            (_t("aeroppk.report.band_tool"), "{} {}".format(tool.get("name") or "Aero-PPK", tool.get("version") or "").strip())]
    return rows


def _summary_table(cv, x, y, w, data):
    cols = [("flight", _t("aeroppk.report.col_flight"), 0.0, "l"), ("photos", _t("aeroppk.report.col_photos"), 44.0, "r"),
            ("fix", _t("aeroppk.report.col_fix"), 50.0, "r"), ("float", _t("aeroppk.report.col_float"), 50.0, "r"),
            ("none", _t("aeroppk.report.col_none_long"), 62.0, "r"), ("sd", _t("aeroppk.report.col_precision"), 74.0, "r"),
            ("base", _t("aeroppk.report.col_base"), 96.0, "l")]
    rows = []
    flights = data.get("flights") or []
    for flight in flights:
        c = flight_counts(flight)
        n = c["photos"]
        fix, flt, none = _pct(c["fix"], n), _pct(c["float"], n), _pct(c["none"], n)
        sd = (flight.get("quality") or {}).get("sd_h_median")
        rows.append({"flight": flight.get("name") or EM_DASH, "photos": str(n), "fix": (fmt_pct(fix), _good_text("fix", fix)),
                     "float": (fmt_pct(flt), _good_text("float", flt)), "none": (fmt_pct(none), GOOD_TEXT if not c["none"] else GREY_DARK),
                     "sd": (fmt_cm(sd) + " cm" if sd is not None else EM_DASH, _good_text("sd", sd)),
                     "base": ", ".join(b.get("code") or "" for b in (flight.get("bases") or [])[:3]) or EM_DASH})
    if len(flights) > 1:
        tot = totals(data)
        n = tot["photos"]
        fix, flt, none = _pct(tot["fix"], n), _pct(tot["float"], n), _pct(tot["none"], n)
        rows.append({"flight": (_t("aeroppk.report.col_total"), INK, True), "photos": (str(n), INK, True), "fix": (fmt_pct(fix), _good_text("fix", fix), True),
                     "float": (fmt_pct(flt), _good_text("float", flt), True), "none": (fmt_pct(none), GOOD_TEXT if not tot["none"] else GREY_DARK, True)})
    return _table(cv, x, y, w, cols, rows)


def _notes_block(cv, x, y, w, data, max_rows):
    """The notes of the calculation, plain: a grey dot and the sentence. Returns the height (0 when there is none)."""
    items = collect_warnings(data)
    if not items:
        return 0.0
    height = _section(cv, x, y, w, _t("aeroppk.report.notes_title"))
    shown = 0
    for item in items:
        text = item["text"] if not item["flight"] else "{}: {}".format(item["flight"], item["text"])
        lines = cv.wrap(text, 7.8, w - 16.0, max_lines=3)
        if shown + len(lines) > max_rows:
            cv.text(x + 12.0, y + height + 9.0, _t("aeroppk.report.warnings_more", n=len(items) - items.index(item)), 7.4, MUTED)
            height += 14.0
            break
        cv.circle(x + 4.0, y + height + 6.0, 2.0, fill=GREY_MID)
        for k, line in enumerate(lines):
            cv.text(x + 12.0, y + height + 9.0 + k * 12.4, line, 7.8, INK_2)
        height += len(lines) * 12.4 + 3.0
        shown += len(lines)
    return height


def _page_cover(cv, W, H, data, number, total):
    _page_bg(cv, W, H)
    inner = W - 2 * MARGIN
    _draw_logo(cv, MARGIN, 26.0, 26.0)
    cv.text(W / 2.0, 46.0, cv.fit(data.get("title") or "", 15.0, inner - 260.0, "display", 2), 15.0, INK, "display", 2, align="c")
    _status_pill(cv, W, 34.0, data)
    cv.text(W - MARGIN, 50.0, _t("aeroppk.report.generated", date=_fmt_date(data.get("generated"))), 7.4, MUTED, align="r")
    cv.text(MARGIN, 90.0, _t("aeroppk.report.title"), 20, INK, "display", 2)
    y = 106.0
    y += _verdict_block(cv, MARGIN, y, inner, data) + 12.0
    y += _section(cv, MARGIN, y, inner, _t("aeroppk.report.general_title"))
    rows = _general_rows(data)
    table_w = inner * 0.58
    height = _info_table(cv, MARGIN, y, table_w, rows, 112.0)
    thumb_x = MARGIN + table_w + 12.0
    _thumb_map(cv, thumb_x, y, W - MARGIN - thumb_x, max(height, 130.0), _all_points(data))
    y += max(height, 130.0) + 6.0
    _map_legend(cv, thumb_x, y + 4.0, W - MARGIN - thumb_x)
    y += 14.0
    y += _section(cv, MARGIN, y, inner, _t("aeroppk.report.summary_title"), None, _t("aeroppk.report.summary_sub"))
    y += _summary_table(cv, MARGIN, y, inner, data) + 12.0
    notes_rows = max(2, int((_limit(H) - y - 40.0) // 13.0))
    y += _notes_block(cv, MARGIN, y, inner, data, notes_rows)
    cv.guard(y, _limit(H) + 8.0, "cover")
    _footer(cv, W, H, number, total)


def _verdict_block(cv, x, y, w, data):
    """The sentence a client reads first. Green with a tick when the data are good, plain grey otherwise. Returns the height."""
    code, n_check = verdict(data)
    n = totals(data)["photos"]
    good = code == "ok"
    cv.tick(x + 8.0, y + 10.0, good, 7.5)
    cv.text(x + 24.0, y + 15.0, _t("aeroppk.report.verdict_" + code), 12.5, GOOD_TEXT if good else INK, "display", 2)
    key = "aeroppk.report.verdict_sub_" + code + ("_csv" if code == "check" and data.get("table", True) is False else "")
    used = _para(cv, x + 24.0, y + 22.0, w - 24.0, _t(key, n=n_check, photos=n), 8.2, INK_2)
    return 30.0 + used


# --------------------------------------------------------------------------
# Flight pages: imported data, processing settings, geodesy
# --------------------------------------------------------------------------

def _engine_summary(data):
    engine = data.get("engine") or {}
    name = " ".join(p for p in (engine.get("name"), engine.get("version")) if p)
    return name or EM_DASH


def _imported_rows(flight):
    """The summary of what was imported for one flight: label and value (a text or a pill)."""
    c = flight_counts(flight)
    start, end = flight.get("start_utc"), flight.get("end_utc")
    rows = [(_t("aeroppk.report.set_begin"), fmt_datetime(start) if isinstance(start, datetime.datetime) else EM_DASH),
            (_t("aeroppk.report.set_end"), fmt_datetime(end) if isinstance(end, datetime.datetime) else EM_DASH),
            (_t("aeroppk.report.set_duration"), fmt_duration(flight.get("duration_s"))),
            (_t("aeroppk.report.kpi_photos"), str(c["photos"])), (_t("aeroppk.report.set_exposures"), str(flight.get("events", EM_DASH)))]
    solved = c["fix"] + c["float"]
    ratio = _pct(solved, c["photos"])
    rows.append((_t("aeroppk.report.set_coverage"), "{} %".format(fmt_num(ratio, 0))))
    rows.append((_t("aeroppk.report.files_col_status"), {"pill": _t("aeroppk.report.pill_valid" if ratio >= 95.0 else "aeroppk.report.pill_partial"), "good": ratio >= 95.0}))
    return rows


def _files_table(cv, x, y, w, flight):
    needs = flight.get("files") or []
    if not needs:
        cv.text(x, y + 9.0, _t("aeroppk.report.files_none"), 7.8, MUTED)
        return 14.0
    cols = [("name", _t("aeroppk.report.files_col_file"), 0.0, "l"), ("status", _t("aeroppk.report.files_col_status"), 70.0, "c"),
            ("detail", _t("aeroppk.report.files_col_detail"), 190.0, "l")]
    rows = []
    for need in needs:
        present = bool(need.get("present"))
        name, _why = need_texts(str(need.get("key") or ""))
        rows.append({"name": (name, INK, False), "status": {"pill": _t("aeroppk.report.file_present" if present else "aeroppk.report.file_absent"), "good": present},
                     "detail": (need.get("detail") or "", GREY_DARK)})
    return _table(cv, x, y, w, cols, rows, row_h=18.0)


def _settings_rows(data):
    engine = data.get("engine") or {}
    rows = [(_t("aeroppk.report.set_engine"), _engine_summary(data))]
    for key, value in engine.get("options") or []:
        rows.append((option_label(key), str(value)))
    return rows


def _leap_offset(data):
    """GPS - UTC on the day of the (first) flight; the day of the report when no flight date is known."""
    for flight in data.get("flights") or []:
        start = flight.get("start_utc")
        if isinstance(start, (datetime.date, datetime.datetime)):
            return gnsstime.leap_seconds(start)
    generated = data.get("generated")
    return gnsstime.leap_seconds(generated if isinstance(generated, (datetime.date, datetime.datetime)) else datetime.date.today())


def _geodesy_rows(data, flight):
    own = _own_base(data)
    offset = _leap_offset(data)
    return [(_t("aeroppk.report.band_frame"), (data.get("frame") or {}).get("label") or EM_DASH),
            (_t("aeroppk.report.set_base_position"), _t("aeroppk.report.set_pos_own" if own else "aeroppk.report.set_pos_network")),
            (_t("aeroppk.report.set_heights"), _t("aeroppk.report.set_heights_ellipsoidal")),
            (_t("aeroppk.report.set_time"), _t("aeroppk.report.set_time_value", offset=offset)),
            (_t("aeroppk.report.set_orbits"), _t("aeroppk.report.set_orbits_broadcast"))]


def _page_imported(cv, W, H, data, index, number, total):
    flights = data.get("flights") or []
    flight = flights[index]
    _page_bg(cv, W, H)
    _page_head(cv, W, data, flight.get("name") or EM_DASH, _flight_label(flight, index, len(flights), False))
    inner = W - 2 * MARGIN
    y = PAGE_TOP
    y += _section(cv, MARGIN, y, inner, _t("aeroppk.report.imported_title"), None, _t("aeroppk.report.imported_sub"))
    y += _info_table(cv, MARGIN, y, inner, _imported_rows(flight), 150.0) + 8.0
    cv.text(MARGIN, y + 9.0, _t("aeroppk.report.files_title"), 7.6, INK, "body", 2)
    y += 14.0
    y += _files_table(cv, MARGIN, y, inner, flight) + 12.0
    y += _section(cv, MARGIN, y, inner, _t("aeroppk.report.settings_title"), None, _t("aeroppk.report.settings_sub"))
    y += _info_table(cv, MARGIN, y, inner, _settings_rows(data), 150.0) + 12.0
    y += _section(cv, MARGIN, y, inner, _t("aeroppk.report.geodesy_title"), None, _t("aeroppk.report.geodesy_sub"))
    y += _info_table(cv, MARGIN, y, inner, _geodesy_rows(data, flight), 150.0)
    cv.guard(y, _limit(H), "imported data page")
    _footer(cv, W, H, number, total)


# --------------------------------------------------------------------------
# Flight pages: base stations
# --------------------------------------------------------------------------

def _stations_table(cv, x, y, w, data, flight):
    bases = flight.get("bases") or []
    cols = [("code", _t("aeroppk.report.col_code"), 40.0, "l"), ("name", _t("aeroppk.report.col_name"), 0.0, "l"),
            ("net", _t("aeroppk.report.col_network"), 56.0, "l"), ("dist", _t("aeroppk.report.col_distance"), 54.0, "r"),
            ("base", _t("aeroppk.report.col_baseline"), 54.0, "r"), ("step", _t("aeroppk.report.col_sampling"), 54.0, "r"),
            ("fix", _t("aeroppk.report.col_fix_short"), 44.0, "r")]
    rows = []
    for base in bases:
        fix = base.get("fix_pct")
        rows.append({"code": (base.get("code") or EM_DASH, INK, True), "name": base.get("name") or EM_DASH, "net": base.get("network") or EM_DASH,
                     "dist": fmt_km(base.get("distance_km")), "base": fmt_km(base.get("baseline_km")),
                     "step": "{} s".format(fmt_num(base.get("interval_s"), 0 if (base.get("interval_s") or 0) >= 1 else 2)),
                     "fix": (fmt_pct(fix, 0), _good_text("fix", fix))})
    if not rows:
        cv.text(x, y + 9.0, _t("aeroppk.report.src_no_bases"), 7.8, MUTED)
        return 14.0
    return _table(cv, x, y, w, cols, rows)


def _station_map(cv, x, y, w, h, flight, geom, base):
    """The site and one base: a line between them, the distance, the base as a triangle. No background map."""
    east_north = None
    for east, north, other in geom["bases"]:
        if other is base:
            east_north = (east, north)
    points = [(0.0, 0.0)] + ([east_north] if east_north else [])
    side = min(w, h)

    def draw(view):
        sx, sy = view.pt(0.0, 0.0)
        cv.circle(sx, sy, 3.2, fill=GREEN, line="#ffffff", line_w=0.8)
        if east_north:
            bx, by = view.pt(*east_north)
            cv.line(sx, sy, bx, by, GREY_MID, 0.7, dash=(3, 2.5))
            cv.triangle(bx, by, 5.0, INK)
            cv.text(bx + 7.0, by + 3.0, cv.fit(base.get("code") or "", 7.2, 60.0), 7.2, INK, "body", 2)
        cv.text(sx + 6.0, sy - 5.0, _t("aeroppk.report.map_flight"), 6.8, GREY_DARK, "body", 2)

    _map_box(cv, x, y, w, side, None, points, draw)


def _station_block(cv, x, y, w, data, flight, geom, base):
    """One base: its map on the left, its facts and the position that was used on the right. Returns the height."""
    map_w = 170.0
    height = 200.0
    _station_map(cv, x, y, map_w, height, flight, geom, base)
    rx = x + map_w + 12.0
    rw = w - map_w - 12.0
    info = [(_t("aeroppk.report.col_name"), base.get("name") or EM_DASH), (_t("aeroppk.report.col_network"), base.get("network") or EM_DASH),
            (_t("aeroppk.report.base_receiver"), base.get("receiver") or EM_DASH), (_t("aeroppk.report.base_antenna"), base.get("antenna") or EM_DASH),
            (_t("aeroppk.report.col_systems"), systems_names(base.get("systems_used") or base.get("systems")) or EM_DASH),
            (_t("aeroppk.report.col_sampling"), "{} s".format(fmt_num(base.get("interval_s"), 0 if (base.get("interval_s") or 0) >= 1 else 2))
             if base.get("interval_s") else EM_DASH),
            (_t("aeroppk.report.col_distance"), fmt_km(base.get("distance_km"))), (_t("aeroppk.report.col_baseline"), fmt_km(base.get("baseline_km")))]
    h1 = _info_table(cv, rx, y, rw, info, 80.0, 15.5, 7.2)
    position = [(_t("aeroppk.report.base_lat"), fmt_num(base.get("lat"), 7)), (_t("aeroppk.report.base_lon"), fmt_num(base.get("lon"), 7)),
                (_t("aeroppk.report.base_h"), "{} m".format(fmt_num(base.get("h"), 3)))]
    cv.text(rx, y + h1 + 12.0, _t("aeroppk.report.base_position"), 7.2, INK, "body", 2)
    _info_table(cv, rx, y + h1 + 16.0, rw, position, 80.0, 15.5, 7.2)
    return height + 6.0


def _page_stations(cv, W, H, data, index, number, total):
    flights = data.get("flights") or []
    flight = flights[index]
    _page_bg(cv, W, H)
    _page_head(cv, W, data, flight.get("name") or EM_DASH, _flight_label(flight, index, len(flights), False))
    inner = W - 2 * MARGIN
    y = PAGE_TOP
    y += _section(cv, MARGIN, y, inner, _t("aeroppk.report.stations_title"), None, _t("aeroppk.report.stations_sub"))
    y += _stations_table(cv, MARGIN, y, inner, data, flight) + 6.0
    src = data.get("source") or {}
    if _own_base(data):
        y += _para(cv, MARGIN, y, inner, _t("aeroppk.report.src_line_own"), 7.6, MUTED) + 8.0
    else:
        y += _para(cv, MARGIN, y, inner, _t("aeroppk.report.src_line", name=src.get("name") or EM_DASH, licence=src.get("licence") or EM_DASH), 7.6, MUTED) + 8.0
    geom = flight_geometry(flight)
    shown = 0
    for base in flight.get("bases") or []:
        if y + 250.0 > _limit(H):
            break
        y += _section(cv, MARGIN, y, inner, _t("aeroppk.report.base_title", code=base.get("code") or EM_DASH)) + 4.0
        y += _station_block(cv, MARGIN, y, inner, data, flight, geom, base)
        files = _base_files_text(base, 6)
        y += _para(cv, MARGIN, y, inner, _t("aeroppk.report.base_files", files=files), 7.2, MUTED) + 8.0
        shown += 1
    left = len(flight.get("bases") or []) - shown
    if left > 0:
        cv.text(MARGIN, min(y + 8.0, _limit(H)), _t("aeroppk.report.bases_more", n=left), 7.4, MUTED)
        y += 12.0
    cv.guard(y, _limit(H), "stations page")
    _footer(cv, W, H, number, total)


def _base_files_text(base, limit=4):
    files = list(base.get("files") or [])
    if not files:
        return EM_DASH
    text = ", ".join(files[:limit])
    if len(files) > limit:
        text += " " + _t("aeroppk.report.more_files", n=len(files) - limit)
    return text


# --------------------------------------------------------------------------
# Flight pages: the processed solution
# --------------------------------------------------------------------------

def _stats(values):
    """(average, std, rms, min, max) of a list of numbers, or None when it is empty."""
    values = [float(v) for v in values if v is not None]
    if not values:
        return None
    n = float(len(values))
    mean = sum(values) / n
    std = math.sqrt(sum((v - mean) ** 2 for v in values) / n)
    rms = math.sqrt(sum(v * v for v in values) / n)
    return mean, std, rms, min(values), max(values)


def _accuracy_table(cv, x, y, w, flight):
    """The precision of the positions: average, standard deviation, rms, smallest and largest, horizontal and vertical, in centimetres."""
    rows_data = _rows(flight)
    horizontal = _stats([sd_h(r) for r in rows_data if sd_h(r) is not None])
    vertical = _stats([_finite(r.get("sdu")) for r in rows_data if _finite(r.get("sdu")) is not None])
    cols = [("axis", "", 0.0, "l")] + [(k, _t("aeroppk.report.stat_" + k), 56.0, "c") for k in ("avg", "std", "rms", "min", "max")]
    rows = []
    for label, stats in ((_t("aeroppk.report.axis_h"), horizontal), (_t("aeroppk.report.axis_v"), vertical)):
        row = {"axis": (label, INK, True)}
        for k, key in enumerate(("avg", "std", "rms", "min", "max")):
            row[key] = (fmt_num(stats[k] * 100.0, 1), _good_text("sd", stats[k])) if stats else EM_DASH
        rows.append(row)
    return _table(cv, x, y, w, cols, rows, row_h=16.5)


def _pie(cv, cx, cy, r, parts):
    """A pie chart; `parts`: [(count, colour)], the first slice starts at the top and the slices turn clockwise."""
    total = float(sum(c for c, _col in parts))
    if total <= 0:
        cv.circle(cx, cy, r, fill=GRID)
        return
    start = 90.0
    rect = QtCore.QRectF(cx - r, cy - r, 2 * r, 2 * r)
    for count, color in parts:
        if count <= 0:
            continue
        span = -360.0 * count / total
        cv.p.setPen(QtGui.QPen(_qcolor("#ffffff"), 0.8))
        cv.p.setBrush(_qcolor(color))
        if count >= total - 1e-9:
            cv.p.drawEllipse(rect)
        else:
            cv.p.drawPie(rect, int(round(start * 16)), int(round(span * 16)))
        start += span


def _solution_summary_rows(data, flight):
    c = flight_counts(flight)
    start, end = flight.get("start_utc"), flight.get("end_utc")
    return [(_t("aeroppk.report.sol_mode"), _t("aeroppk.report.set_ppk")),
            (_t("aeroppk.report.sol_first"), fmt_datetime(start) if isinstance(start, datetime.datetime) else EM_DASH),
            (_t("aeroppk.report.sol_last"), fmt_datetime(end) if isinstance(end, datetime.datetime) else EM_DASH),
            (_t("aeroppk.report.kpi_photos"), str(c["photos"])), (_t("aeroppk.report.set_orbits"), _t("aeroppk.report.set_orbits_broadcast")),
            (_t("aeroppk.report.sol_bases"), str(len(flight.get("bases") or [])))]


def _solution_types(cv, x, y, w, flight):
    """The pie of the solution types with its legend. Returns the height."""
    c = flight_counts(flight)
    n = c["photos"]
    parts = [(c["fix"], GREEN), (c["float"], GREY_MID), (c["none"], GREY_LIGHT)]
    r = 56.0
    _pie(cv, x + r + 6.0, y + r + 4.0, r, parts)
    lx = x + 2 * r + 34.0
    for k, key in enumerate(("fix", "float", "none")):
        ly = y + 30.0 + k * 24.0
        cv.rect(lx, ly - 7.5, 9.0, 9.0, fill=parts[k][1], radius=2.0)
        label = "{}  {} ({})".format(_t("aeroppk.report.q_" + key), c[key], fmt_pct(_pct(c[key], n)))
        cv.text(lx + 15.0, ly, label, 8.0, INK_2)
    return 2 * r + 14.0


def _classes_block(cv, x, y, w, flight):
    """The photos in three classes, median then worst then best: the photo that shows the class, its precision, the share of the photos it
    stands for (see `photoclasses`). A precision of 10 cm or better is green. Returns the height."""
    cv.text(x, y + 9.0, _t("aeroppk.report.classes_title"), 7.8, INK, "body", 2)
    height = 14.0
    info = photoclasses.classify(_rows(flight), summary.GOOD_ERROR_M)
    if info is None:
        cv.text(x, y + height + 9.0, _t("aeroppk.report.no_data"), 7.8, MUTED)
        return height + 16.0
    cols = [("kind", _t("aeroppk.report.col_class"), 56.0, "l"), ("name", _t("aeroppk.report.table_col_photo"), 0.0, "l"),
            ("h", _t("aeroppk.report.axis_h"), 92.0, "r"), ("v", _t("aeroppk.report.axis_v"), 92.0, "r"),
            ("share", _t("aeroppk.report.col_share"), 52.0, "r")]
    rows = []
    for entry in info["rows"]:
        sd_h_m, sd_u_m = entry["sd_h"], entry["sd_u"]
        rows.append({"kind": (_t("aeroppk.report.class_" + entry["kind"]), INK, True), "name": entry["name"] or EM_DASH,
                     "h": (fmt_num(sd_h_m * 100.0, 1), _good_text("sd", sd_h_m)) if sd_h_m is not None else EM_DASH,
                     "v": (fmt_num(sd_u_m * 100.0, 1), _good_text("sd", sd_u_m)) if sd_u_m is not None else EM_DASH,
                     "share": (fmt_pct(entry["share"], 0), GREY_DARK)})
    height += _table(cv, x, y + height, w, cols, rows) + 4.0
    note = _t("aeroppk.report.classes_note", best=photoclasses.BEST_SHARE, worst=photoclasses.WORST_SHARE,
              median=100 - photoclasses.BEST_SHARE - photoclasses.WORST_SHARE)
    height += _para(cv, x, y + height, w, note, 7.2, MUTED)
    good = summary.tone("fix", info["good_pct"]) == "good"
    cv.text(x, y + height + 9.0, _t("aeroppk.report.classes_good", pct=fmt_pct(info["good_pct"], 1)), 7.6, GOOD_TEXT if good else GREY_DARK, "body", 2)
    height += 13.0
    if info["without"]:
        height += _para(cv, x, y + height, w, _t("aeroppk.report.classes_without", n=info["without"]), 7.2, MUTED)
    return height + 4.0


def _page_solution(cv, W, H, data, index, number, total):
    flights = data.get("flights") or []
    flight = flights[index]
    _page_bg(cv, W, H)
    _page_head(cv, W, data, flight.get("name") or EM_DASH, _flight_label(flight, index, len(flights), False))
    inner = W - 2 * MARGIN
    y = PAGE_TOP
    y += _section(cv, MARGIN, y, inner, _t("aeroppk.report.solution_page_title"), None, _t("aeroppk.report.solution_page_sub"))
    y += _info_table(cv, MARGIN, y, inner, _solution_summary_rows(data, flight), 150.0) + 12.0
    cv.text(MARGIN, y + 9.0, _t("aeroppk.report.solution_title"), 7.8, INK, "body", 2)
    y += 14.0
    pie_h = _solution_types(cv, MARGIN, y, inner * 0.5, flight)
    cv.text(MARGIN + 2 * 56.0 + 34.0, y + 108.0, _t("aeroppk.report.solution_sub"), 7.0, MUTED)
    y += pie_h + 8.0
    cv.text(MARGIN, y + 9.0, _t("aeroppk.report.accuracy_title"), 7.8, INK, "body", 2)
    y += 14.0
    y += _accuracy_table(cv, MARGIN, y, inner, flight) + 4.0
    terms = summary.precision_terms()
    y += _para(cv, MARGIN, y, inner, _t("aeroppk.report.accuracy_note", fh=fmt_num(terms["fh"], 1), fv=fmt_num(terms["fv"], 1),
                                        ppm=fmt_num(terms["ppm"], 1)), 7.2, MUTED) + 8.0
    y += _classes_block(cv, MARGIN, y, inner, flight) + 6.0
    y += _section(cv, MARGIN, y, inner, _t("aeroppk.report.along_title"), _t("aeroppk.report.along_sub"))
    y += _along_flight(cv, MARGIN, y, inner, flight)
    cv.guard(y, _limit(H), "solution page")
    _footer(cv, W, H, number, total)


# --------------------------------------------------------------------------
# Flight pages: maps and height
# --------------------------------------------------------------------------

def _draw_compass_and_scale(cv, view, rect):
    """North arrow (top left) and scale bar (bottom left) inside a map rectangle."""
    x, y, w, h = rect
    ax, ay = x + 14.0, y + 24.0
    cv.polygon([(ax, ay - 12.0), (ax - 4.5, ay + 2.0), (ax, ay - 1.5), (ax + 4.5, ay + 2.0)], fill=INK_2, line=None)
    cv.text(ax, ay + 12.0, "N", 7.2, INK_2, "body", 2, align="c")
    target = nice_length(min(w * 0.32, 90.0) / view.k)
    length = target * view.k
    sx, sy = x + 10.0, y + h - 12.0
    cv.line(sx, sy, sx + length, sy, INK_2, 1.0)
    cv.line(sx, sy - 3.0, sx, sy + 3.0, INK_2, 1.0)
    cv.line(sx + length, sy - 3.0, sx + length, sy + 3.0, INK_2, 1.0)
    cv.text(sx + length / 2.0, sy - 5.0, fmt_length_m(target), 6.8, INK_2, align="c")


def _quality_color(key):
    return {"fix": GREEN, "float": GREY_MID, "none": GREY_LIGHT}[key]


def _trigger_points(flight, geom):
    """[(east, north, q)] of the exposures, in the metres of `geom`."""
    origin = geom["origin"]
    if origin is None:
        return []
    out = []
    for row in _rows(flight):
        lat, lon = _finite(row.get("lat")), _finite(row.get("lon"))
        if lat is not None and lon is not None:
            east, north = local_xy(lat, lon, origin[0], origin[1])
            out.append((east, north, row.get("q", 0)))
    return out


def _map_box(cv, x, y, w, h, title, points, draw):
    """A square map of `points` [(east, north)]: a thin frame, the north arrow and the scale; `draw(view)` paints the content."""
    used = _section(cv, x, y, w, title) if title else 0.0
    side = min(w, h - used)
    rect = (x + (w - side) / 2.0, y + used, side, side)
    px, py, pw, ph = rect
    cv.rect(px, py, pw, ph, fill=None, line=RULE, line_w=0.5)
    if not points:
        pdfkit.empty_note(cv, px, py, pw, ph, _t("aeroppk.report.no_data"))
        return used + side
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    cx0, cy0 = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
    half_w = max((max(xs) - min(xs)) / 2.0, 15.0) * 1.15
    half_h = max((max(ys) - min(ys)) / 2.0, 15.0) * 1.15
    view = MapView((px + 8.0, py + 8.0, pw - 16.0, ph - 16.0), (cx0 - half_w, cy0 - half_h, cx0 + half_w, cy0 + half_h))
    cv.clip(px, py, pw, ph)
    draw(view)
    cv.unclip()
    _draw_compass_and_scale(cv, view, rect)
    return used + side


def _distribution_table(cv, x, y, w, counts, total):
    """Fixed / float / none: count and share. A good share is green."""
    cols = [("q", _t("aeroppk.report.col_quality"), 0.0, "l"), ("n", _t("aeroppk.report.col_count"), 56.0, "r"), ("pct", _t("aeroppk.report.col_share"), 56.0, "r")]
    rows = []
    for key, tone_kind in (("fix", "fix"), ("float", "float"), ("none", None)):
        share = _pct(counts[key], total)
        good = GOOD_TEXT if (tone_kind and _good_text(tone_kind, share) == GOOD_TEXT) or (tone_kind is None and not counts[key]) else GREY_DARK
        rows.append({"q": (_t("aeroppk.report.q_" + key), INK), "n": (str(counts[key]), good), "pct": (fmt_pct(share), good)})
    return _table(cv, x, y, w, cols, rows)


def _map_legend(cv, x, y, w):
    cx = x
    for key in ("fix", "float", "none"):
        cv.circle(cx + 3.0, y - 2.6, 2.6, fill=_quality_color(key))
        cx += 9.0
        cx += cv.text(cx, y, _t("aeroppk.report.q_" + key), 7.0, MUTED) + 10.0


def _nice_ticks(low, high, count=4):
    """(first, step, last) of a value axis with about `count` intervals."""
    span = max(high - low, 1e-6)
    step = nice_length(span / float(count))
    for factor in (1, 2, 2.5, 5, 10):
        if step * factor * count >= span:
            step *= factor
            break
    first = math.floor(low / step) * step
    last = math.ceil(high / step) * step
    return first, step, last


def _height_plot(cv, x, y, w, h, flight):
    """The ellipsoidal height of the camera at each exposure, in the order of the photos. Fixed positions are green, the others grey."""
    rows = [r for r in _rows(flight) if _finite(r.get("h")) is not None]
    if len(rows) < 2:
        pdfkit.empty_note(cv, x, y, w, h, _t("aeroppk.report.no_data"))
        return
    values = [float(r["h"]) for r in rows]
    first, step, last = _nice_ticks(min(values), max(values))
    left, right, top, base = x + 44.0, x + w - 8.0, y + 8.0, y + h - 20.0
    unit = (base - top) / max(last - first, 1e-6)
    ticks = int(round((last - first) / step))
    for i in range(ticks + 1):
        value = first + i * step
        gy = base - (value - first) * unit
        cv.line(left, gy, right, gy, GRID if i else RULE, 0.4)
        cv.text(left - 4.0, gy + 2.8, fmt_num(value, pdfkit.tick_decimals(step)), 6.4, MUTED, align="r")
    n = len(rows)
    points = [(left + (right - left) * i / float(n - 1), base - (v - first) * unit) for i, v in enumerate(values)]
    cv.polyline(points, GREY_LIGHT, 0.6)
    for (px, py), row in zip(points, rows):
        cv.circle(px, py, 1.2, fill=_quality_color(quality_key(row.get("q"))))
    cv.text(left, base + 13.0, "1", 6.4, MUTED)
    cv.text(right, base + 13.0, str(n), 6.4, MUTED, align="r")
    cv.text((left + right) / 2.0, base + 13.0, _t("aeroppk.report.axis_photo"), 6.4, MUTED, align="c")
    cv.text(x, top + 3.0, _t("aeroppk.report.unit_m"), 6.4, MUTED)


def _page_maps(cv, W, H, data, index, number, total):
    flights = data.get("flights") or []
    flight = flights[index]
    _page_bg(cv, W, H)
    _page_head(cv, W, data, flight.get("name") or EM_DASH, _flight_label(flight, index, len(flights), False))
    inner = W - 2 * MARGIN
    gap = 16.0
    half = (inner - gap) / 2.0
    y = PAGE_TOP
    y += _section(cv, MARGIN, y, inner, _t("aeroppk.report.maps_title"), None, _t("aeroppk.report.maps_sub"))
    geom = flight_geometry(flight)
    triggers = _trigger_points(flight, geom)
    c = flight_counts(flight)
    # trigger map and path map side by side
    trig_h = _map_box(cv, MARGIN, y, half, half + 22.0, _t("aeroppk.report.map_triggers"), [(e, n) for e, n, _q in triggers],
                      lambda view: [cv.circle(view.pt(e, n)[0], view.pt(e, n)[1], 2.2, fill=_quality_color(quality_key(q))) for e, n, q in triggers])

    def draw_path(view):
        for key, points in track_segments(geom["track"]):
            cv.polyline([view.pt(e, n) for e, n in points], _quality_color(key), 1.6)

    path_h = _map_box(cv, MARGIN + half + gap, y, half, half + 22.0, _t("aeroppk.report.map_path"), [(e, n) for e, n, _q in geom["track"]], draw_path)
    y += max(trig_h, path_h) + 8.0
    _map_legend(cv, MARGIN, y + 7.0, inner)
    y += 20.0
    path_counts = {"fix": 0, "float": 0, "none": 0}
    for _e, _n, q in geom["track"]:
        path_counts[quality_key(q)] += 1
    tab_h = _distribution_table(cv, MARGIN, y, half, c, c["photos"])
    _distribution_table(cv, MARGIN + half + gap, y, half, path_counts, max(1, sum(path_counts.values())))
    y += tab_h + 4.0
    cv.text(MARGIN, y + 9.0, _t("aeroppk.report.dist_triggers"), 6.8, MUTED)
    cv.text(MARGIN + half + gap, y + 9.0, _t("aeroppk.report.dist_path"), 6.8, MUTED)
    y += 16.0
    y += _section(cv, MARGIN, y, inner, _t("aeroppk.report.height_title"), _t("aeroppk.report.height_sub"))
    plot_h = max(90.0, min(140.0, _limit(H) - y - 4.0))
    _height_plot(cv, MARGIN, y, inner, plot_h, flight)
    y += plot_h
    cv.guard(y, _limit(H), "maps page")
    _footer(cv, W, H, number, total)


# --------------------------------------------------------------------------
# Flight pages: quality
# --------------------------------------------------------------------------

def _hist_slots(hist):
    """Bars of a histogram() result, with an extra bar at each end for the values beyond the axis."""
    slots = []
    if hist["below"]:
        slots.append(("below", hist["below"]))
    slots.extend(("bin", c) for c in hist["counts"])
    if hist["above"]:
        slots.append(("above", hist["above"]))
    return slots


def _hist_bars(cv, left, right, top_y, base, hist, color, scale_top, counts_on=True, good_limit=None):
    """Bars, tick marks and tick labels between x = left..right and y = top_y..base (bars use `scale_top` as full height).
    `good_limit`: bars up to this value of the axis are green, the others grey; without it every bar has `color`.
    Returns the function that turns a value of the axis into an x position."""
    slots = _hist_slots(hist)
    shift = 1 if hist["below"] else 0
    slot = (right - left) / float(len(slots))
    unit = (base - top_y) / float(scale_top or 1)
    peak = max(c for _kind, c in slots) or 1
    bar_w = slot * 0.8
    last_label = -1e9
    for i, (kind, count) in enumerate(slots):
        if not count:
            continue
        cx = left + slot * (i + 0.5)
        bh = count * unit
        fill = color
        if good_limit is not None:
            high = hist["edges"][i - shift + 1] if kind == "bin" else (hist["edges"][0] if kind == "below" else hist["edges"][-1])
            fill = GREEN if (kind != "above" and high <= good_limit + 1e-9) else GREY_MID
        cv.rect(cx - bar_w / 2.0, base - bh, bar_w, bh, fill=fill, radius=min(1.0, bar_w / 3.0))
        if counts_on and (slot >= 13.0 or count == peak or kind != "bin") and cx - last_label >= 13.0:
            cv.text(cx, base - bh - 2.5, str(count), 6.2, MUTED, align="c")
            last_label = cx
    n, k = len(hist["counts"]), hist["k"]
    stride = k * max(1, int(math.ceil(32.0 / (slot * k))))
    for i in range(0, n + 1, stride):
        ex = left + slot * (i + shift)
        text = pdfkit.typo(fmt_num(hist["edges"][i], pdfkit.tick_decimals(abs(hist["step"]))))
        if i == 0 and hist["below"]:
            text = "≤" + text
        elif i == n and hist["above"]:
            text = "≥" + text
        cv.line(ex, base, ex, base + 2.5, FAINT, 0.5)
        cv.text(ex, base + 12.0, text, 6.2, FAINT, align="c")
    per_value = slot * n / float(hist["high"] - hist["low"])
    return lambda value: left + slot * shift + (value - hist["low"]) * per_value


def _hist_panel(cv, x, y, w, h, title, hist, notes=(), unit_text="", good_limit=None):
    """A framed bar chart of a histogram() result with `notes` (text lines) at the bottom."""
    cv.card(x, y, w, h)
    cv.text(x + 10.0, y + 18.0, title, 8.2, INK, "display", 2)
    if hist is None:
        pdfkit.empty_note(cv, x, y + 24.0, w, h - 24.0, _t("aeroppk.report.no_data"))
        return
    left, right = x + 34.0, x + w - 12.0
    top_y, base = y + 40.0, y + h - 28.0 - 12.0 * max(1, len(notes))
    peak = max(max(hist["counts"]), hist["below"], hist["above"]) or 1
    top, ystep = pdfkit.nice_top(float(peak), 3)
    top = max(1.0, top)
    ystep = max(1.0, round(ystep))
    unit = (base - top_y) / top
    for i in range(int(top // ystep) + 1):
        gy = base - i * ystep * unit
        cv.line(left - 3, gy, right, gy, GRID if i else RULE, 0.4)
        cv.text(left - 6, gy + 2.6, str(int(i * ystep)), 6.0, FAINT, align="r")
    _hist_bars(cv, left, right, top_y, base, hist, GREEN, top, good_limit=good_limit)
    if unit_text:
        cv.text(right, base + 25.0, unit_text, 6.4, FAINT, align="r")
    for k, note in enumerate(notes):
        cv.text(x + 10.0, y + h - 8.0 - (len(notes) - 1 - k) * 12.0, cv.fit(note, 7.0, w - 20.0), 7.0, MUTED)


def _precision_panels(cv, x, y, w, h, flight):
    rows = _rows(flight)
    quality = flight.get("quality") or {}
    gap = 14.0
    cw = (w - gap) / 2.0
    sd_hv = [v * 100.0 for v in (sd_h(r) for r in rows) if v is not None]
    sd_uv = [v * 100.0 for v in (_finite(r.get("sdu")) for r in rows) if v is not None]

    def median(values, given):
        return given if given is not None else (_quantile([v / 100.0 for v in values], 0.5) if values else None)

    limit_cm = summary.GOOD_ERROR_M * 100.0
    for k, (title, values, given, max_key) in enumerate(((_t("aeroppk.report.hist_sdh"), sd_hv, quality.get("sd_h_median"), "sd_h_max"),
                                                         (_t("aeroppk.report.hist_sdu"), sd_uv, quality.get("sd_u_median"), "sd_u_max"))):
        biggest = quality.get(max_key) if quality.get(max_key) is not None else (max(values) / 100.0 if values else None)
        _hist_panel(cv, x + k * (cw + gap), y, cw, h, title, histogram(values),
                    [_t("aeroppk.report.hist_notes", median=fmt_cm(median(values, given)), max=fmt_cm(biggest))],
                    unit_text=_t("aeroppk.report.unit_cm"), good_limit=limit_cm)


def _along_flight(cv, x, y, w, flight):
    """One thin bar per photo, in the order of the photos: green when its position is fixed, grey when it is not. Returns the height."""
    rows = _rows(flight)
    h = 16.0
    if not rows:
        pdfkit.empty_note(cv, x, y, w, h, _t("aeroppk.report.no_data"))
        return h + 20.0
    cv.rect(x, y, w, h, fill=GRID)
    step = w / float(len(rows))
    for i, row in enumerate(rows):
        cv.rect(x + i * step, y, max(step, 0.5) + 0.2, h, fill=_quality_color(quality_key(row.get("q"))))
    cv.rect(x, y, w, h, fill=None, line=RULE, line_w=0.5)
    cv.text(x, y + h + 11.0, "1", 6.4, MUTED)
    cv.text(x + w, y + h + 11.0, str(len(rows)), 6.4, MUTED, align="r")
    cv.text(x + w / 2.0, y + h + 11.0, _t("aeroppk.report.axis_photo"), 6.4, MUTED, align="c")
    _map_legend(cv, x, y + h + 24.0, w)
    return h + 32.0


def onboard_rows(flight):
    """Photos used for the comparison with the drone's own position: those where it had an RTK fix (status 50), or all
    photos with a difference when the rows carry no drone status. Returns [(dn, de, du)] in metres."""
    rows = [r for r in _rows(flight) if all(_finite(r.get(k)) is not None for k in ("dn", "de", "du"))]
    fixed = [r for r in rows if r.get("onboard_q") == 50]
    chosen = fixed if fixed else ([r for r in rows if r.get("onboard_q") is None] or [])
    return [(r["dn"], r["de"], r["du"]) for r in chosen]


def _bare_hist(cv, x, y, w, h, hist, color, mark=None):
    """A histogram without frame or y axis (three of them side by side in the comparison with the drone)."""
    if hist is None:
        pdfkit.empty_note(cv, x, y, w, h, _t("aeroppk.report.no_data"))
        return
    left, right, base, top_y = x + 4.0, x + w, y + h - 14.0, y + 8.0
    cv.line(left, base, right, base, RULE, 0.5)
    peak = max(max(hist["counts"]), hist["below"], hist["above"]) or 1
    x_of = _hist_bars(cv, left, right, top_y, base, hist, color, peak, counts_on=False)
    cv.line(x_of(0.0), top_y - 3.0, x_of(0.0), base, INK_2, 0.6)
    if mark is not None:
        mx = min(max(x_of(mark), left), right)
        cv.line(mx, top_y - 3.0, mx, base, GREY_DARK, 0.8, dash=(3, 2.2))


def _onboard_block(cv, x, y, w, flight):
    """The comparison with the drone's own position, as information: three histograms and a small table. Returns the height."""
    onboard = flight.get("onboard") or {}
    rtk = onboard.get("rtk_photos") or 0
    if rtk <= 0:
        cv.text(x, y + 11.0, _t("aeroppk.report.onboard_na"), 8.0, INK_2, "body", 2)
        used = _para(cv, x, y + 17.0, w, _t("aeroppk.report.onboard_na_why"), 7.8, MUTED)
        return 19.0 + used
    height = _para(cv, x, y, w, _t("aeroppk.report.onboard_sub", n=rtk) + " " + _t("aeroppk.report.onboard_note"), 7.6, MUTED) + 4.0
    deltas = onboard_rows(flight)
    gap = 14.0
    cw = (w - 2 * gap) / 3.0
    names = (("n", _t("aeroppk.report.axis_n")), ("e", _t("aeroppk.report.axis_e")), ("u", _t("aeroppk.report.axis_u")))
    for k, (axis, label) in enumerate(names):
        cx = x + k * (cw + gap)
        cv.text(cx, y + height + 10.0, label, 7.8, INK, "body", 2)
        bias = onboard.get("bias_" + axis)
        _bare_hist(cv, cx, y + height + 14.0, cw, 72.0, histogram([d[k] * 100.0 for d in deltas], signed=True), GREY_MID,
                   None if bias is None else bias * 100.0)
    height += 92.0
    cols = [("axis", _t("aeroppk.report.col_axis"), 0.0, "l"), ("offset", _t("aeroppk.report.col_offset"), 90.0, "r"),
            ("spread", _t("aeroppk.report.col_spread"), 110.0, "r")]
    rows = []
    for axis, label in names:
        bias, p95 = onboard.get("bias_" + axis), onboard.get("p95_" + axis)
        rows.append({"axis": label,
                     "offset": (fmt_signed_cm(bias), _good_text("onboard", abs(bias)) if bias is not None else GREY_DARK),
                     "spread": (fmt_cm(p95), _good_text("onboard", p95) if p95 is not None else GREY_DARK)})
    height += _table(cv, x, y + height, w, cols, rows)
    return height


def _page_quality(cv, W, H, data, index, number, total):
    flights = data.get("flights") or []
    flight = flights[index]
    _page_bg(cv, W, H)
    _page_head(cv, W, data, flight.get("name") or EM_DASH, _flight_label(flight, index, len(flights), False))
    inner = W - 2 * MARGIN
    y = PAGE_TOP
    y += _section(cv, MARGIN, y, inner, _t("aeroppk.report.precision_title"), None, _t("aeroppk.report.precision_sub"))
    _precision_panels(cv, MARGIN, y, inner, 176.0, flight)
    y += 176.0 + 14.0
    y += _section(cv, MARGIN, y, inner, _t("aeroppk.report.onboard_title"), None, _t("aeroppk.report.onboard_page_sub"))
    y += _onboard_block(cv, MARGIN, y, inner, flight)
    cv.guard(y, _limit(H), "quality page")
    _footer(cv, W, H, number, total)


# --------------------------------------------------------------------------
# Part 2: the photo table
# --------------------------------------------------------------------------

def _table_columns(x, w):
    """(key, left, right, align) of the photo table columns. Left aligned columns keep 5 points of air on their left."""
    pad = 6.0
    widths = [("index", 22.0, "r"), ("name", 106.0, "l"), ("time", 40.0, "l"), ("lat", 48.0, "r"), ("lon", 48.0, "r"),
              ("h", 40.0, "r"), ("sn", 22.0, "r"), ("se", 22.0, "r"), ("su", 22.0, "r"), ("q", 36.0, "l"), ("bases", 40.0, "l"),
              ("flags", 0.0, "l")]
    flex = w - 2 * pad - sum(c[1] for c in widths)
    left, out = x + pad, []
    for key, width, align in widths:
        width = width or flex
        out.append((key, left + (5.0 if align == "l" and key != "index" else 0.0), left + width, align))
        left += width
    return out


def _table_head(cv, x, y, w):
    cv.rect(x, y, w, TABLE_HEAD_H, fill=HEAD_FILL)
    names = {"index": "table_col_index", "name": "table_col_photo", "time": "table_col_time", "lat": "table_col_lat",
             "lon": "table_col_lon", "h": "table_col_h", "sn": "table_col_sn", "se": "table_col_se", "su": "table_col_su",
             "q": "table_col_q", "bases": "table_col_bases", "flags": "table_col_flags"}
    for key, left, right, align in _table_columns(x, w):
        text = _t("aeroppk.report." + names[key])
        if align == "r":
            cv.text(right - 1.0, y + 18.0, text, 6.6, GREY_DARK, "body", 2, align="r")
        else:
            cv.text(left, y + 18.0, text, 6.6, GREY_DARK, "body", 2)


def _table_row(cv, x, y, w, row, odd):
    cols = {key: (left, right, align) for key, left, right, align in _table_columns(x, w)}
    if not odd:
        cv.rect(x, y, w, ROW_H, fill=ZEBRA)
    base = y + ROW_H - 4.8
    px = 6.9

    def put(key, text, weight=1, col=INK_2):
        left, right, align = cols[key]
        text = cv.fit(str(text), px, right - left - 2.0)
        if align == "r":
            cv.text(right - 1.0, base, text, px, col, "body", weight, align="r")
        else:
            cv.text(left, base, text, px, col, "body", weight)

    q = quality_key(row.get("q"))
    put("index", row.get("index", ""), 1, FAINT)
    put("name", os.path.splitext(row.get("name") or "")[0] or EM_DASH)
    put("time", fmt_time(row.get("time_utc")))
    put("lat", fmt_num(row.get("lat"), 7))
    put("lon", fmt_num(row.get("lon"), 7))
    put("h", fmt_num(row.get("h"), 3))
    put("sn", fmt_cm(row.get("sdn")))
    put("se", fmt_cm(row.get("sde")))
    put("su", fmt_cm(row.get("sdu")))
    put("q", quality_label(row.get("q")), 1, GOOD_TEXT if q == "fix" else GREY_DARK)
    put("bases", "+".join(row.get("used") or []) or EM_DASH)
    put("flags", ", ".join(flag_label(f, True) for f in row.get("flags") or []), 2 if row.get("flags") else 1, GREY_DARK)


def _page_table(cv, W, H, data, entries, number, total, first_of_table):
    flights = data.get("flights") or []
    _page_bg(cv, W, H)
    sub = _t("aeroppk.report.table_sub", n=sum(len(_rows(f)) for f in flights)) if first_of_table else _t("aeroppk.report.table_sub_next")
    _page_head(cv, W, data, _t("aeroppk.report.table_title"), sub)
    inner = W - 2 * MARGIN
    _table_head(cv, MARGIN, PAGE_TOP, inner)
    y = PAGE_TOP + TABLE_HEAD_H
    for entry in entries:
        if entry[0] == "flight":
            flight = flights[entry[1]]
            cv.rect(MARGIN, y, inner, FLIGHT_ROW_H, fill=BAND_FILL)
            text = _flight_label(flight, entry[1], len(flights))
            if entry[2]:
                text += "  " + _t("aeroppk.report.table_cont")
            cv.text(MARGIN + 6.0, y + 17.0, cv.fit(text, 7.6, inner - 12.0, "body", 2), 7.6, INK, "body", 2)
            y += FLIGHT_ROW_H
        elif entry[0] == "empty":
            cv.text(MARGIN + 6.0, y + 11.0, _t("aeroppk.report.table_empty"), 7.4, MUTED)
            y += ROW_H
        else:
            rows = _rows(flights[entry[1]])[entry[2]:entry[3]]
            for i, row in enumerate(rows):
                _table_row(cv, MARGIN, y, inner, row, (entry[2] + i) % 2 == 1)
                y += ROW_H
    cv.line(MARGIN, y, W - MARGIN, y, RULE, 0.5)
    cv.rect(MARGIN, PAGE_TOP, inner, y - PAGE_TOP, fill=None, line=RULE, line_w=0.5)
    cv.guard(y + 8.0, _limit(H), "photo table")
    cv.text(MARGIN, y + 14.0, _t("aeroppk.report.table_legend"), 7.0, MUTED)
    _footer(cv, W, H, number, total)


# --------------------------------------------------------------------------
# Method, limits, files written, disclaimer
# --------------------------------------------------------------------------

def _text_card(cv, x, y, w, title, blocks, sub=None):
    """A section and its blocks: ("p", text), ("b", [bullets]), ("kv", [(label, value)]), ("flags", [flag names]).
    Plain text under a title and a thin rule. Returns its height."""
    text_w = w
    height = _section(cv, x, y, w, title, None, sub)
    for kind, content in blocks:
        if kind == "p":
            height += _para(cv, x, y + height, text_w, content, 8.0) + 4.0
        elif kind == "b":
            for item in content:
                cv.circle(x + 3.0, y + height + 6.6, 1.7, fill=GREY_MID)
                height += _para(cv, x + 12.0, y + height, text_w - 12.0, item, 8.0) + 1.5
            height += 3.0
        elif kind == "kv":
            half = (text_w - 12.0) / 2.0
            for i, (label, value) in enumerate(content):
                col, row = i % 2, i // 2
                kx = x + col * (half + 12.0)
                cv.text(kx, y + height + 10.0 + row * 14.0, label, 7.8, MUTED)
                cv.text(kx + half, y + height + 10.0 + row * 14.0, cv.fit(value, 7.8, half * 0.55), 7.8, INK, "body", 2, align="r")
            height += ((len(content) + 1) // 2) * 14.0 + 4.0
        elif kind == "flags":
            for flag in content:
                cv.text(x, y + height + 9.0, flag_label(flag, True), 7.4, INK, "body", 2)
                height += max(_para(cv, x + 88.0, y + height, text_w - 88.0, _t("aeroppk.report.flag_long_" + flag), 7.8), 13.0) + 3.0
            height += 2.0
    return height


def _exports_block(cv, x, y, w, data):
    """The files that were written with this report, and a word about the table of every photo when it was left out. Returns the height
    (0 when there is nothing to say)."""
    exports = data.get("exports") or []
    left_out = data.get("table", True) is False
    if not exports and not left_out:
        return 0.0
    height = _section(cv, x, y, w, _t("aeroppk.report.exports_title"), None, _t("aeroppk.report.exports_sub"))
    if exports:
        cols = [("kind", _t("aeroppk.report.exports_kind"), 130.0, "l"), ("name", _t("aeroppk.report.exports_name"), 0.0, "l")]
        rows = [{"kind": (_t("aeroppk.out." + kind), INK, True), "name": (name, GREY_DARK)} for kind, name in exports]
        height += _table(cv, x, y + height, w, cols, rows, row_h=16.5) + 6.0
    if left_out:
        text = _t("aeroppk.report.exports_no_table")
        if any(kind == "csv" for kind, _name in exports):
            text += " " + _t("aeroppk.report.exports_csv_all")
        height += _para(cv, x, y + height, w, text, 7.8, INK_2) + 4.0
    return height + 10.0


def _page_method(cv, W, H, data, part, number, total):
    _page_bg(cv, W, H)
    _page_head(cv, W, data, _t("aeroppk.report.method_title"), _t("aeroppk.report.method_sub_{}".format(part)))
    inner = W - 2 * MARGIN
    engine = data.get("engine") or {}
    src = data.get("source") or {}
    frame = (data.get("frame") or {}).get("label") or EM_DASH
    y = PAGE_TOP
    gap = 10.0
    if part == 1:
        y += _text_card(cv, MARGIN, y, inner, _t("aeroppk.report.frame_title"), [("p", _t("aeroppk.report.frame_note", frame=frame))]) + gap
        offset = _leap_offset(data)
        y += _text_card(cv, MARGIN, y, inner, _t("aeroppk.report.time_title"), [("p", _t("aeroppk.report.time_note", offset=offset))]) + gap
        name = " ".join(p for p in (engine.get("name"), engine.get("version")) if p) or EM_DASH
        options = [(option_label(k), str(v)) for k, v in engine.get("options") or []]
        blocks = [("p", _t("aeroppk.report.engine_note", engine=name, licence=engine.get("licence") or EM_DASH))]
        if options:
            blocks.append(("kv", options))
        y += _text_card(cv, MARGIN, y, inner, _t("aeroppk.report.engine_title"), blocks) + gap
        y += _text_card(cv, MARGIN, y, inner, _t("aeroppk.report.classes_method_title"),
                        [("p", _t("aeroppk.report.classes_method", best=photoclasses.BEST_SHARE, worst=photoclasses.WORST_SHARE,
                                  median=100 - photoclasses.BEST_SHARE - photoclasses.WORST_SHARE))]) + gap
    elif part == 2:
        used_flags = [f for f in FLAG_ORDER]
        y += _text_card(cv, MARGIN, y, inner, _t("aeroppk.report.flags_title"),
                        [("p", _t("aeroppk.report.flags_intro" + ("_csv" if data.get("table", True) is False else ""))), ("flags", used_flags)]) + gap
        y += _text_card(cv, MARGIN, y, inner, _t("aeroppk.report.limits_title"),
                        [("b", [_t("aeroppk.report.limit_{}".format(k)) for k in (1, 2, 3, 4, 5, 6)])]) + gap
    else:
        y += _exports_block(cv, MARGIN, y, inner, data)
        if _own_base(data):
            attribution = [("p", _t("aeroppk.report.attrib_own"))]
        else:
            networks = src.get("networks") or []
            if len(networks) > 1:                                  # one credit per network, in full
                attribution = [("p", _t("aeroppk.report.attrib_source", name=n.get("name") or EM_DASH, licence=n.get("licence") or EM_DASH,
                                        url=n.get("url") or EM_DASH)) for n in networks]
            else:
                attribution = [("p", _t("aeroppk.report.attrib_source", name=src.get("name") or EM_DASH, licence=src.get("licence") or EM_DASH,
                                        url=src.get("url") or EM_DASH))]
            ids = [n.get("id") for n in networks] or (["rgp"] if "RGP" in str(src.get("name") or "").upper() else [])
            for network_id in ids:
                key = "aeroppk.report.attrib_" + str(network_id)
                if _t(key) != key:
                    attribution.append(("p", _t(key)))
        attribution.append(("p", _t("aeroppk.report.attrib_tool", tool=(data.get("tool") or {}).get("name") or "Aero-PPK",
                                    version=(data.get("tool") or {}).get("version") or "")))
        y += _text_card(cv, MARGIN, y, inner, _t("aeroppk.report.attrib_title"), attribution) + gap
        disclaimer = [("p", _t("aeroppk.report.disclaimer_own" if _own_base(data) else "aeroppk.report.disclaimer_public")),
                      ("p", _t("aeroppk.report.disclaimer_tool"))]
        y += _text_card(cv, MARGIN, y, inner, _t("aeroppk.report.disclaimer_title"), disclaimer)
    cv.guard(y - gap, _limit(H), "method page {}".format(part))
    _footer(cv, W, H, number, total)


# --------------------------------------------------------------------------
# Pages and PDF
# --------------------------------------------------------------------------

def _draw_page(cv, W, H, data, entry, number, total, first_table=False):
    kind = entry[0]
    if kind == "cover":
        _page_cover(cv, W, H, data, number, total)
    elif kind == "imported":
        _page_imported(cv, W, H, data, entry[1], number, total)
    elif kind == "stations":
        _page_stations(cv, W, H, data, entry[1], number, total)
    elif kind == "solution":
        _page_solution(cv, W, H, data, entry[1], number, total)
    elif kind == "maps":
        _page_maps(cv, W, H, data, entry[1], number, total)
    elif kind == "quality":
        _page_quality(cv, W, H, data, entry[1], number, total)
    elif kind == "table":
        _page_table(cv, W, H, data, entry[1], number, total, first_table)
    else:
        _page_method(cv, W, H, data, entry[1], number, total)


def _first_table_index(plan):
    for index, entry in enumerate(plan):
        if entry[0] == "table":
            return index
    return -1


def _require_qt():
    if QtWidgets.QApplication.instance() is None:
        raise RuntimeError(i18n.t("err.no_qt"))


def _new_writer(path, title):
    return pdfkit.new_writer(path, title, "AeroTools Aero-PPK")


def build_pdf(data, path):
    """Paint the report into `path` and return the number of pages. Raises when the file cannot be written."""
    _require_qt()
    plan = page_plan(data)
    total = len(plan)
    first_table = _first_table_index(plan)
    title = _t("aeroppk.report.doc_title", project=data.get("title") or "").strip()
    tmp = path + ".part"
    writer = _new_writer(tmp, title)
    painter = QtGui.QPainter(writer)
    if not painter.isActive():
        pdfkit.remove(tmp)
        raise IOError(i18n.t("err.cannot_write", path=path))
    del LAST_OVERFLOWS[:]
    try:
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        painter.scale(PT_TO_DEVICE, PT_TO_DEVICE)
        W = writer.width() / PT_TO_DEVICE
        H = writer.height() / PT_TO_DEVICE
        cv = _PpkCanvas(painter, _PpkStyle())
        for index, entry in enumerate(plan):
            if index:
                writer.newPage()
            _draw_page(cv, W, H, data, entry, index + 1, total, index == first_table)
        LAST_OVERFLOWS.extend(cv.overflows)
    except Exception:
        painter.end()
        pdfkit.remove(tmp)
        raise
    painter.end()
    try:
        os.replace(tmp, path)
    except OSError:
        pdfkit.remove(tmp)
        raise
    return total


def render_pages(data, scale=1.6, only=None):
    """Paint the pages of the report on QImage (same drawing code as the PDF): a list of (page number, QImage).
    `only` is a set of page numbers (1 based). Used to look at the layout, and by the self test."""
    _require_qt()
    plan = page_plan(data)
    total = len(plan)
    first_table = _first_table_index(plan)
    images, overflows = [], []
    for index, entry in enumerate(plan):
        number = index + 1
        if only is not None and number not in only:
            continue
        image = QtGui.QImage(int(A4_W * scale), int(A4_H * scale), QtGui.QImage.Format_RGB32)
        image.fill(QtGui.QColor(WHITE))
        painter = QtGui.QPainter(image)
        try:
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            painter.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
            painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
            painter.scale(scale, scale)
            cv = _PpkCanvas(painter, _PpkStyle())
            _draw_page(cv, A4_W, A4_H, data, entry, number, total, index == first_table)
            overflows.extend(cv.overflows)
        finally:
            painter.end()
        images.append((number, image))
    LAST_OVERFLOWS[:] = overflows
    return images
