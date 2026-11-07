"""Aero-L3: the checks made BEFORE anything is created in Metashape, and the summary shown to the operator. Pure Python 3.9.

`analyse(flights, options)` takes the flights the operator ticked and returns an `Analysis`: the `Plan`, the list of `Finding`s (each with a
level, a code that maps to a text in `lang/en.py` and a count) and a `Summary`. A finding of level `error` blocks the creation; a `warning` is
shown and does not; `info` is good to know.
"""

import math
from collections import Counter, OrderedDict

from ... import i18n
from . import geo, mrk, orient, plan as plan_module

ERROR, WARNING, INFO = "error", "warning", "info"
LEVEL_ORDER = {ERROR: 0, WARNING: 1, INFO: 2}

OBLIQUE_DEG = 35.0                # tilt from the nadir above which a shot counts as oblique
MAX_SPEED_MS = 35.0               # faster than this between two shots: a position jump (the M400 flies at 23 m/s at most)
PAIR_GAP_S = 0.05                 # seconds between the two lenses of one shot (2.7 ms measured)
RIG_TOLERANCE_DEG = 1.0
ANGLE_CROSS_CHECK_DEG = 1.0       # camera angles against gimbal angles
AGAINST_TRACK_DEG = 120.0         # top of the picture further than this from the direction of travel
MRK_POSITION_M = 0.05
TINY_FLIGHT_PAIRS = 3
EXAMPLES = 5
TIP_EXAMPLES = 3
NO_COUNT = ("no_photos", "mixed_units", "nothing_to_do")

# What each topic checks: it counts as passed when none of its codes shows up as a warning or an error.
TOPICS = (
    ("photos", ("not_l3", "mixed_units", "lens", "calibration", "lens_conflict")),
    ("files", ("size", "orientation", "duplicate_names")),
    ("pairs", ("unpaired", "pair_gap", "rig_angle")),
    ("positions", ("no_gps", "position_jump")),
    ("rtk", ("rtk_not_fixed", "gps_not_rtk", "altitude_type")),
    ("angles", ("angles_missing", "angles_inconsistent")),
    ("direction", ("against_track", "lens_side")),
    ("mrk", ("mrk_missing", "mrk_mismatch")),
)


class Finding(object):
    def __init__(self, level, code, n=0, total=0, examples=None, **fields):
        self.level = level
        self.code = code
        self.n = n
        self.total = total
        self.examples = list(examples or [])[:EXAMPLES]
        self.fields = fields

    def short(self):
        """The label of the row (a few words)."""
        return i18n.t("l3.check." + self.code + ".short", n=self.n, total=self.total, **self.fields)

    def detail(self):
        """One sentence that explains the finding."""
        return i18n.t("l3.check." + self.code + ".detail", n=self.n, total=self.total, **self.fields)

    def examples_text(self):
        """The first few file names concerned, as one line ('' when there are none)."""
        if not self.examples:
            return ""
        shown = self.examples[:TIP_EXAMPLES]
        return i18n.t("l3.check.examples", names=", ".join(shown) + (", ..." if self.n > len(shown) else ""))

    def tip(self):
        """What the small "i" of the row shows: the sentence and the file names."""
        extra = self.examples_text()
        return self.detail() + (chr(10) + extra if extra else "")

    def count_text(self):
        return "" if self.code in NO_COUNT or not self.n else "{:,}".format(int(self.n)).replace(",", " ")

    def __repr__(self):
        return "Finding({}, {}, n={})".format(self.level, self.code, self.n)


class Summary(object):
    def __init__(self):
        self.photos = 0
        self.flights = 0
        self.stations = 0
        self.pairs = 0
        self.lenses = []                  # ['L', 'R']
        self.serials = {}                 # {'L': serial, 'R': serial}
        self.first_utc = None
        self.last_utc = None
        self.rtk = OrderedDict([("fix", 0), ("float", 0), ("single", 0), ("none", 0)])
        self.mrk_flights = 0
        self.lidar_flights = 0
        self.mean_v_mm = None
        self.tilt_min = None
        self.tilt_max = None
        self.site = ""
        self.date = ""                    # YYMMDD of the first photo, local date of the EXIF


class Analysis(object):
    def __init__(self, plan, findings, summary):
        self.plan = plan
        self.findings = findings
        self.summary = summary

    @property
    def passed(self):
        """Topics (keys of `TOPICS`) in which nothing was found. Empty when there is no photo to check."""
        if not self.plan.paths():
            return []
        bad = {f.code for f in self.findings if f.level != INFO}
        return [key for key, codes in TOPICS if not bad & set(codes)]

    @property
    def blocking(self):
        return [f for f in self.findings if f.level == ERROR]

    @property
    def ok(self):
        return not self.blocking

    def by_code(self, code):
        return [f for f in self.findings if f.code == code]

    def has(self, code):
        return any(f.code == code for f in self.findings)


def _utc(record):
    return mrk.gps_to_utc(record.gps_time) if record.gps_time is not None else None


def local_date_code(record):
    """YYMMDD from the EXIF local date of a photo (`2026:10:02 08:49:53`), '' if unknown."""
    text = (record.local_time or "").replace("-", ":").strip()
    parts = text[:10].split(":")
    if len(parts) == 3 and all(p.isdigit() for p in parts) and len(parts[0]) == 4:
        return parts[0][2:] + parts[1] + parts[2]
    return ""


def site_name(flights):
    """A short site name from the folders: the mission part of the DJI folder name (`DJI_202610020844_009_MYSITE-FLIGHT01` -> `MYSITE`)."""
    for flight in flights:
        parts = flight.name.split("_")
        if len(parts) >= 4 and parts[0].upper() == "DJI" and parts[1].isdigit():
            return parts[3].split("-")[0]
    for flight in flights:
        return flight.name
    return ""


def default_chunk_name(summary):
    pieces = ["L3"]
    if summary.site:
        pieces.append(summary.site)
    if summary.date:
        pieces.append(summary.date)
    return "_".join(pieces)


def stations_in_time_order(flight_photos):
    """[(station key, left record, right record)] of a flight, ordered by time (the left photo stands for the station)."""
    seen = OrderedDict()
    for rec in flight_photos:
        entry = seen.setdefault(rec.station_key, [None, None])
        entry[0 if rec.lens == orient.LEFT else 1] = rec
    out = []
    for key, (left, right) in seen.items():
        out.append((key, left, right))
    out.sort(key=lambda e: ((e[1] or e[2]).gps_time is None, (e[1] or e[2]).gps_time, (e[1] or e[2]).name))
    return out


def headings(items):
    """Direction of travel in degrees at each station (None where it cannot be told: first and last stations, turns, gaps).

    `items` = [(record with a position and a time)] in time order. The heading is the mean of the bearings to the previous and to the next
    station when those two agree to 20 degrees and neither gap lasts more than 10 s."""
    out = [None] * len(items)
    for i in range(1, len(items) - 1):
        a, b, c = items[i - 1], items[i], items[i + 1]
        if not (a.has_position and b.has_position and c.has_position and a.gps_time and b.gps_time and c.gps_time):
            continue
        dt1 = (b.gps_time - a.gps_time).total_seconds()
        dt2 = (c.gps_time - b.gps_time).total_seconds()
        if not (0 < dt1 <= 10 and 0 < dt2 <= 10):
            continue
        e1, n1, _ = geo.enu_of((a.lat, a.lon, a.alt), (b.lat, b.lon, b.alt))
        e2, n2, _ = geo.enu_of((b.lat, b.lon, b.alt), (c.lat, c.lon, c.alt))
        if math.hypot(e1, n1) < 1.0 or math.hypot(e2, n2) < 1.0:
            continue
        h1, h2 = geo.azimuth(e1, n1), geo.azimuth(e2, n2)
        if abs(geo.angle_difference(h1, h2)) > 20.0:
            continue
        out[i] = (h1 + geo.angle_difference(h2, h1) / 2.0) % 360.0
    return out


def _add(findings, level, code, n=0, total=0, examples=None, **fields):
    findings.append(Finding(level, code, n, total, examples, **fields))


def analyse(flights, options=None):
    """Checks for the photos of the given flights (`scan.FlightInfo` objects). Returns an `Analysis`."""
    options = options or plan_module.Options()
    records = [rec for flight in flights for rec in flight.photos]
    plan = plan_module.build_plan(records, options)
    findings = []
    summary = Summary()
    summary.photos = len(records)
    summary.flights = len(flights)
    summary.site = site_name(flights)
    summary.stations = len(plan.stations)
    summary.pairs = len(plan.groups)

    if not records:
        _add(findings, ERROR, "no_photos")
        return Analysis(plan, findings, summary)

    # -- what the photos are -------------------------------------------------------------------------------------------------
    counts = plan.excluded_counts()
    by_reason = {}
    for rec, reason in plan.excluded:
        by_reason.setdefault(reason, []).append(rec.name)
    not_l3 = by_reason.get("not_l3", [])
    if not_l3:
        _add(findings, ERROR if len(not_l3) == len(records) else WARNING, "not_l3", len(not_l3), len(records), not_l3)
    signatures = Counter(plan_module.unit_signature(r) for r in records if r.lens in (orient.LEFT, orient.RIGHT) and plan_module.is_l3(r))
    per_lens = Counter()
    for sig in signatures:
        per_lens[sig[0]] += 1
    if any(n > 1 for n in per_lens.values()):
        _add(findings, ERROR, "mixed_units", sum(n for n in per_lens.values()), len(records), [sig[1] or "?" for sig in signatures])
    if by_reason.get("size"):
        _add(findings, WARNING, "size", len(by_reason["size"]), len(records), by_reason["size"], size="{} x {}".format(*plan.size))
    if by_reason.get("orientation"):
        _add(findings, WARNING, "orientation", len(by_reason["orientation"]), len(records), by_reason["orientation"])
    if by_reason.get("lens"):
        _add(findings, WARNING, "lens", len(by_reason["lens"]), len(records), by_reason["lens"])
    if by_reason.get("calibration"):
        _add(findings, WARNING, "calibration", len(by_reason["calibration"]), len(records), by_reason["calibration"])
    if by_reason.get("duplicate"):
        _add(findings, WARNING, "duplicate_names", len(by_reason["duplicate"]), len(records), by_reason["duplicate"])
    if by_reason.get("unpaired"):
        _add(findings, WARNING, "unpaired", len(by_reason["unpaired"]), len(records), by_reason["unpaired"])
    elif options.include_unpaired and any(not s.paired for s in plan.stations):
        _add(findings, INFO, "unpaired_included", sum(1 for s in plan.stations if not s.paired), len(plan.stations))
    conflicts = [r.name for r in records if r.lens_conflict]
    if conflicts:
        _add(findings, WARNING, "lens_conflict", len(conflicts), len(records), conflicts)
    if any(r.dewarp_flag == 1 for r in records):
        _add(findings, INFO, "dewarped", sum(1 for r in records if r.dewarp_flag == 1), len(records))

    if not plan.paths():
        _add(findings, ERROR, "nothing_to_do")

    # -- positions and RTK -----------------------------------------------------------------------------------------------------
    usable = [plan.cameras[p] for p in plan.cameras]
    kept = [r for flight in flights for r in flight.photos if r.path in plan.cameras]
    for rec in kept:
        summary.rtk[rec.rtk_name if rec.rtk_name in summary.rtk else "none"] += 1
    no_gps = [r.name for r in kept if not r.has_position]
    if no_gps:
        _add(findings, ERROR if len(no_gps) == len(kept) else WARNING, "no_gps", len(no_gps), len(kept), no_gps)
    if kept:
        not_fixed = [r.name for r in kept if r.has_position and r.rtk_flag != 50]
        if not_fixed:
            _add(findings, WARNING, "rtk_not_fixed", len(not_fixed), len(kept), not_fixed, fix=summary.rtk["fix"], flt=summary.rtk["float"],
                 single=summary.rtk["single"] + summary.rtk["none"])
        not_rtk = [r.name for r in kept if r.has_position and (r.gps_status or "").upper() not in ("RTK", "")]
        if not_rtk:
            _add(findings, WARNING, "gps_not_rtk", len(not_rtk), len(kept), not_rtk)
        baro = [r.name for r in kept if r.alt_type and r.alt_type != "RtkAlt"]
        if baro:
            _add(findings, WARNING, "altitude_type", len(baro), len(kept), baro)

    # -- time span, serials, tilt --------------------------------------------------------------------------------------------
    times = [_utc(r) for r in kept if r.gps_time is not None]
    if times:
        summary.first_utc, summary.last_utc = min(times), max(times)
    summary.lenses = sorted({r.lens for r in kept}, key=lambda l: l != orient.LEFT)
    for r in kept:
        summary.serials.setdefault(r.lens, r.serial)
    for r in kept:
        code = local_date_code(r)
        if code:
            summary.date = code
            break

    # -- orientation: angles, rig, track ----------------------------------------------------------------------------------
    no_angles = []
    cross = []
    tilts = []
    oblique_names = []
    for rec in kept:
        spec = plan.cameras[rec.path]
        if spec.rotation is None:
            no_angles.append(rec.name)
            continue
        tilt = orient.tilt_from_nadir(spec.rotation)
        tilts.append(tilt)
        if tilt > OBLIQUE_DEG:
            oblique_names.append(rec.name)
        other = orient.nominal_from_gimbal_angles(rec.angles, rec.lens) if spec.rotation_source == "camera" else None
        if other is not None and orient.angle_between(other, spec.rotation) > ANGLE_CROSS_CHECK_DEG:
            cross.append(rec.name)
    ready = len(kept) - len(no_angles)
    if ready and not all(plan.cameras[r.path].rotation_source == "camera" for r in kept if plan.cameras[r.path].rotation is not None):
        _add(findings, INFO, "orientation_ready", ready, len(kept), source="mixed")
    if no_angles:
        _add(findings, WARNING, "angles_missing", len(no_angles), len(kept), no_angles)
    if cross:
        _add(findings, WARNING, "angles_inconsistent", len(cross), len(kept), cross)
    if tilts:
        summary.tilt_min, summary.tilt_max = min(tilts), max(tilts)
    if oblique_names:
        _add(findings, INFO, "oblique", len(oblique_names), len(kept), oblique_names)

    rig_bad = []
    gap_bad = []
    for station in plan.stations:
        if not station.paired:
            continue
        a, b = plan.cameras.get(station.left.path), plan.cameras.get(station.right.path)
        if a is not None and b is not None and a.rotation is not None and b.rotation is not None:
            if abs(orient.angle_between(a.rotation, b.rotation) - orient.RIG_ANGLE) > RIG_TOLERANCE_DEG:
                rig_bad.append(station.key[1])
        if station.left.gps_time is not None and station.right.gps_time is not None:
            if abs((station.right.gps_time - station.left.gps_time).total_seconds()) > PAIR_GAP_S:
                gap_bad.append(station.key[1])
    if rig_bad:
        _add(findings, WARNING, "rig_angle", len(rig_bad), len(plan.groups), rig_bad)
    if gap_bad:
        _add(findings, WARNING, "pair_gap", len(gap_bad), len(plan.groups), gap_bad)

    against, wrong_side, jumps, jump_names, straight = [], [], 0, [], 0
    for flight in flights:
        photos = [r for r in flight.photos if r.path in plan.cameras]
        stations = stations_in_time_order(photos)
        lefts = [(l or r) for _k, l, r in stations]
        heads = headings(lefts)
        for (key, left, right), heading in zip(stations, heads):
            for rec in (left, right):
                if rec is None:
                    continue
                spec = plan.cameras.get(rec.path)
                if spec is None or spec.rotation is None or heading is None:
                    continue
                straight += 1
                up = orient.up_azimuth(spec.rotation)
                if up is not None and abs(geo.angle_difference(up, heading)) > AGAINST_TRACK_DEG:
                    against.append(rec.name)
                if rec.lens in (orient.LEFT, orient.RIGHT) and orient.tilt_from_nadir(spec.rotation) > 5.0:
                    expected = -1 if rec.lens == orient.LEFT else 1
                    if orient.side_of_travel(spec.rotation, heading) == -expected and abs(geo.angle_difference(up, heading)) < 90.0:
                        wrong_side.append(rec.name)
        for prev, cur in zip(lefts, lefts[1:]):
            if prev.has_position and cur.has_position and prev.gps_time and cur.gps_time:
                dt = (cur.gps_time - prev.gps_time).total_seconds()
                if dt > 0 and geo.distance((prev.lat, prev.lon, prev.alt), (cur.lat, cur.lon, cur.alt)) / dt > MAX_SPEED_MS:
                    jumps += 1
                    jump_names.append(cur.name)
    if against:
        _add(findings, WARNING, "against_track", len(against), straight, against)
    if wrong_side:
        _add(findings, WARNING, "lens_side", len(wrong_side), straight, wrong_side)
    if jumps:
        _add(findings, WARNING, "position_jump", jumps, len(kept), jump_names)

    # -- companion files --------------------------------------------------------------------------------------------------------
    no_mrk = [f.name for f in flights if not f.has_mrk]
    if no_mrk:
        _add(findings, WARNING, "mrk_missing", len(no_mrk), len(flights), no_mrk)
    summary.mrk_flights = len(flights) - len(no_mrk)
    summary.lidar_flights = sum(1 for f in flights if f.lidar_files)
    shots = [s for f in flights for s in f.mrk_shots]
    summary.mean_v_mm = mrk.mean_offsets(shots)[2] if shots else None
    mismatch = []
    for flight in flights:
        index = {(s.stem, s.lens): s for s in flight.mrk_shots}
        for rec in flight.photos:
            if rec.path not in plan.cameras or not rec.has_position:
                continue
            tail = rec.stem.rsplit("_", 1)[-1]
            shot = index.get((tail, rec.lens))
            if shot is not None and geo.distance((rec.lat, rec.lon, rec.alt), (shot.lat, shot.lon, shot.height)) > MRK_POSITION_M:
                mismatch.append(rec.name)
    if mismatch:
        _add(findings, WARNING, "mrk_mismatch", len(mismatch), len(kept), mismatch)
    tiny = [f.name for f in flights if sum(1 for r in f.photos if r.lens == orient.LEFT) <= TINY_FLIGHT_PAIRS]
    if tiny:
        _add(findings, INFO, "tiny_flights", len(tiny), len(flights), tiny)

    findings.sort(key=lambda f: LEVEL_ORDER[f.level])
    return Analysis(plan, findings, summary)


def default_selection(flights):
    """{flight key: bool}: which flights to tick at first. Flights of a few shots and flights looking mostly obliquely are left unticked."""
    selection = {}
    for flight in flights:
        lefts = [r for r in flight.photos if r.lens == orient.LEFT]
        tilts = []
        for rec in flight.photos:
            matrix, _src = orient.nominal_orientation(rec.angles, rec.lens)
            if matrix is not None:
                tilts.append(orient.tilt_from_nadir(matrix))
        oblique = bool(tilts) and sorted(tilts)[len(tilts) // 2] > OBLIQUE_DEG
        selection[flight.key] = len(lefts) > TINY_FLIGHT_PAIRS and not oblique
    if flights and not any(selection.values()):
        for flight in flights:
            selection[flight.key] = True              # never offer an empty choice: the operator decides
    return selection


def flight_row(flight):
    """What the flight list shows about one flight: a dict with name, photos, pairs, fix (share of RTK fixed photos, 0 to 1), tilt (median tilt
    from the nadir in degrees or None), mrk, lidar and the UTC time of the first photo."""
    stems = {}
    for rec in flight.photos:
        stems.setdefault(rec.stem, set()).add(rec.lens)
    pairs = sum(1 for lenses in stems.values() if orient.LEFT in lenses and orient.RIGHT in lenses)
    fixed = sum(1 for r in flight.photos if r.rtk_flag == 50)
    tilts = []
    for rec in flight.photos:
        matrix, _src = orient.nominal_orientation(rec.angles, rec.lens)
        if matrix is not None:
            tilts.append(orient.tilt_from_nadir(matrix))
    tilts.sort()
    times = [_utc(r) for r in flight.photos if r.gps_time is not None]
    return {
        "name": flight.name, "photos": len(flight.photos), "pairs": pairs,
        "fix": fixed / float(len(flight.photos)) if flight.photos else 0.0,
        "tilt": tilts[len(tilts) // 2] if tilts else None,
        "mrk": flight.has_mrk, "lidar": flight.lidar_files, "start": min(times) if times else None,
    }
