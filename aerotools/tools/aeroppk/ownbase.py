"""Use your own base: recognise its RINEX files, turn what the user typed into the position the solver needs, build a base.

No Qt and no Metashape. The solver wants one point: the PHASE CENTRE of the base antenna (WGS 84 latitude, longitude and
ELLIPSOIDAL height) with no antenna offset on top. Two things can be typed, and they must never be mixed up:

* "Base position": the phase centre itself (latitude, longitude, ellipsoidal height).
* "Marker position": WGS 84 latitude, longitude and ellipsoidal height of the ground point under the antenna, plus the pole height
  (the antenna reference point above the marker) and the phase centre above the antenna reference point. Both heights are needed and
  they add up. The phase centre is the marker
  moved up along the vertical by the sum of the two heights.

The frame of the result is the frame of the coordinates typed here (the tool does not convert it), as for any base.
"""

import datetime
import os
import re

import numpy as np

from . import geo, pipeline, rinex, rinexprep, unpack
from .stations import rgp

OWN_CODE = "BASE"
DEFAULT_POLE = 2.0                         # metres, the pole height proposed (a free number, any pole)
MAX_POLE = 10.0                            # metres: more is a typing mistake
MAX_PC = 1.0                               # metres: phase centre above the antenna base
FAR_KM = 100.0                             # a base this far from the photos is probably mistyped
HEADER_GAP_M = 50.0                        # the typed position this far from the file header's one is worth a question

# Receiver names as written in `REC # / TYPE / VERS` (first match wins): word looked for -> brand shown. Data, not prose.
BRANDS = (("trimble", "Trimble"), ("leica", "Leica"), ("sept", "Septentrio"), ("ublox", "u-blox"), ("u-blox", "u-blox"),
          ("emlid", "Emlid"), ("topcon", "Topcon"), ("javad", "Javad"), ("novatel", "NovAtel"), ("swift", "Swift Navigation"),
          ("chc", "CHC Navigation"), ("stonex", "Stonex"), ("sokkia", "Sokkia"), ("south", "South"), ("tersus", "Tersus"),
          ("unicore", "Unicore"), ("hemisphere", "Hemisphere"), ("ashtech", "Ashtech"), ("spectra", "Spectra Precision"),
          ("geodetic", "Geodetic"), ("septentrio", "Septentrio"))
# Raw receiver logs (not RINEX): extension -> brand. They need a conversion to RINEX first.
RAW_EXTENSIONS = {".ubx": "u-blox", ".sbf": "Septentrio", ".jps": "Javad", ".jvd": "Javad", ".t01": "Trimble", ".t02": "Trimble",
                  ".t04": "Trimble", ".dat": "Trimble", ".rtcm3": "RTCM", ".rtcm": "RTCM", ".bnx": "NovAtel", ".gnss": "NovAtel",
                  ".mdb": "Leica", ".m00": "Leica", ".lb2": "Leica", ".raw": "Raw receiver log", ".bin": "Raw receiver log"}
NAV_KINDS = ("N", "G", "H", "P", "L", "C", "I", "J")


def brand_of(receiver, program=""):
    """Brand of the receiver (or of the program that wrote the file) as a short name, or ''."""
    text = ("%s %s" % (receiver or "", program or "")).lower()
    for word, brand in BRANDS:
        if word in text:
            return brand
    return ""


# ---------------------------------------------------------------------------------------------------------------------
# What was typed
# ---------------------------------------------------------------------------------------------------------------------

_DMS = re.compile(r"^\s*([+-]?\d+(?:[.,]\d+)?)\s*[°d\s]\s*(?:(\d+(?:[.,]\d+)?)\s*['m\s]\s*)?(?:(\d+(?:[.,]\d+)?)\s*[\"s]?\s*)?([NSEWnsew])?\s*$")


def parse_angle(text):
    """`45.3`, `45,3`, `-1.25`, `45 18 35.2 N`, `45°18'35.2"` -> decimal degrees, or None."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        return float(text.replace(",", "."))
    except ValueError:
        pass
    match = _DMS.match(text)
    if not match:
        return None
    deg, minutes, seconds, hemi = match.groups()
    sign = -1.0 if deg.strip().startswith("-") or (hemi or "").upper() in ("S", "W") else 1.0
    value = abs(float(deg.replace(",", "."))) + float((minutes or "0").replace(",", ".")) / 60.0 + \
        float((seconds or "0").replace(",", ".")) / 3600.0
    return sign * value


def parse_number(text):
    """`1234.5`, `1 234,5` -> float, or None."""
    try:
        return float((text or "").strip().replace(" ", "").replace(",", "."))
    except ValueError:
        return None


class Position(object):
    """The base as the solver needs it. `lat`, `lon` degrees, `h` ellipsoidal metres of the PHASE CENTRE."""

    def __init__(self, lat, lon, h, source="position", marker=None, pole=None, pc_offset=None):
        self.lat, self.lon, self.h = float(lat), float(lon), float(h)
        self.source = source                    # "position" (typed as it is) or "marker" (computed from the marker)
        self.marker = marker                    # (lat, lon, h) of the ground point when the position comes from it
        self.pole, self.pc_offset = pole, pc_offset

    @property
    def xyz(self):
        x, y, z = geo.llh_to_ecef(self.lat, self.lon, self.h)
        return float(x), float(y), float(z)


def position_from_phase_centre(lat, lon, h):
    """Typed as the phase centre: nothing to convert."""
    return Position(lat, lon, h, "position")


def position_from_marker(lat, lon, h, pole, pc_offset):
    """Marker latitude, longitude, ellipsoidal height (WGS 84) + pole height + phase centre above the antenna reference point ->
    Position of the phase centre: the marker lifted along the vertical by `pole + pc_offset` metres (the two heights add up)."""
    return Position(float(lat), float(lon), float(h) + float(pole) + float(pc_offset), "marker", (float(lat), float(lon), float(h)),
                    float(pole), float(pc_offset))


def heights_valid(pole, pc_offset):
    """True when the pole height (0 to `MAX_POLE`, 0 excluded) and the phase centre height (0 to `MAX_PC`) are usable numbers."""
    return (pole is not None and pc_offset is not None and 0.0 < pole <= MAX_POLE and 0.0 <= pc_offset <= MAX_PC)


def marker_from_header(xyz):
    """Header position (geocentric metres) -> (lat, lon, h) for display."""
    lat, lon, h = geo.ecef_to_llh(*[float(v) for v in xyz])
    return float(lat), float(lon), float(h)


class Check(object):
    """A finding about the typed position: `key` (text key suffix), `level` ("stop" or "warn"), numbers for the text."""

    def __init__(self, key, level, **fields):
        self.key, self.level, self.fields = key, level, fields

    def __repr__(self):
        return "Check(%s, %s)" % (self.key, self.level)


def check_position(lat, lon, h, site=None, header_xyz=None):
    """Findings about a base position (values already numbers, or None when not typed). `site` = (lat, lon) of the photos."""
    out = []
    if lat is None or lon is None or h is None:
        return [Check("incomplete", "stop")]
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return [Check("range", "stop")]
    if abs(lat) < 1e-9 and abs(lon) < 1e-9:
        return [Check("zero", "stop")]
    if not (-500.0 <= h <= 9000.0):
        out.append(Check("height", "stop"))
    if site is not None:
        km = geo.distance_m(lat, lon, site[0], site[1]) / 1000.0
        if km > FAR_KM:
            out.append(Check("far", "warn", km=km))
    if header_xyz is not None and not out:
        hlat, hlon, hh = marker_from_header(header_xyz)
        gap = geo.baseline_m(lat, lon, h, hlat, hlon, hh)
        if gap > HEADER_GAP_M:
            out.append(Check("header_gap", "warn", m=gap))
    return out


# ---------------------------------------------------------------------------------------------------------------------
# What the files are
# ---------------------------------------------------------------------------------------------------------------------

class Issue(object):
    """Something missing or odd in the base files. `key` selects the texts `aeroppk.own.issue.<key>` and `aeroppk.own.cons.<key>`
    (what is wrong, what it may cause); `level` is "stop" (cannot go on) or "warn"."""

    def __init__(self, key, level, **fields):
        self.key, self.level, self.fields = key, level, fields

    def __repr__(self):
        return "Issue(%s, %s)" % (self.key, self.level)


class Recognition(object):
    def __init__(self):
        self.obs = None                 # path of the (merged) plain RINEX observation file
        self.obs_info = None
        self.navs = []                  # plain RINEX navigation paths
        self.brand = ""
        self.names = {"obs": [], "nav": []}      # file names as given, for display
        self.issues = []

    def stops(self):
        return [i for i in self.issues if i.level == "stop"]

    def ok(self):
        return self.obs is not None and not self.stops()


def _plain(path, work_dir, crx2rnx):
    """Path of a plain RINEX file for `path` (a copy is made only when it has to be unpacked)."""
    name = path.lower()
    if name.endswith((".gz", ".z")) or rinex.is_hatanaka(path):
        return unpack.unpack(path, work_dir, crx2rnx=crx2rnx)
    return path


def _file_kind(path):
    """("obs" | "nav" | "other", version) from the first line of a RINEX file."""
    try:
        with open(path, "rb") as handle:
            first = handle.readline().decode("ascii", "replace")
    except OSError:
        return "other", 0.0
    if first[60:80].strip() != "RINEX VERSION / TYPE":
        return "other", 0.0
    try:
        version = float(first[0:9])
    except ValueError:
        version = 0.0
    kind = first[20:21]
    if kind == "O":
        return "obs", version
    if kind in NAV_KINDS:
        return "nav", version
    return "other", version


def recognise(paths, work_dir, crx2rnx=None, flight=None, rover_systems=None, rover_has_nav=False):
    """Look at the files the user gave (any order, any name, compressed or not) and say what they are and what is missing.

    `flight` = (start, end) GPS datetimes of the flight, `rover_systems` = constellations of the drone's data. Nothing is
    written outside `work_dir`; the user's files are only read. Returns a Recognition with `issues`.
    """
    rec = Recognition()
    os.makedirs(work_dir, exist_ok=True)
    obs_files, other = [], []
    for number, path in enumerate(paths):
        name = os.path.basename(path)
        ext = os.path.splitext(name)[1].lower()
        if ext in RAW_EXTENSIONS and ext not in (".dat",) and not rinex.is_hatanaka(path) and _file_kind(path)[0] == "other":
            rec.issues.append(Issue("raw", "stop", name=name, brand=RAW_EXTENSIONS[ext]))
            rec.brand = rec.brand or (RAW_EXTENSIONS[ext] if RAW_EXTENSIONS[ext] != "Raw receiver log" else "")
            continue
        try:
            plain = _plain(path, os.path.join(work_dir, "src%d" % number), crx2rnx)    # one folder per input: same names never collide
        except Exception as exc:                       # unpack.UnpackError, OSError
            key = "no_decoder" if "crx2rnx" in str(exc) else "unreadable"
            rec.issues.append(Issue(key, "stop", name=name))
            continue
        kind, _version = _file_kind(plain)
        if kind == "obs":
            obs_files.append((plain, name))
        elif kind == "nav":
            rec.navs.append(plain)
            rec.names["nav"].append(name)
        else:
            other.append(name)
    for name in other:
        rec.issues.append(Issue("unknown", "warn", name=name))
    if obs_files:
        try:
            infos = sorted(((rinex.read_obs_info(p), p, n) for p, n in obs_files), key=lambda t: (t[0].first is None, t[0].first or datetime.datetime.min))
            merged = rinexprep.merge_obs([p for _i, p, _n in infos], os.path.join(work_dir, "merged_own_base.obs"))
            rec.obs = merged
            rec.obs_info = rinex.read_obs_info(merged) if len(infos) > 1 else infos[0][0]
            rec.names["obs"] = [n for _i, _p, n in infos]
        except (rinex.RinexError, rinexprep.PrepError, OSError) as exc:
            rec.obs = None
            rec.issues.append(Issue("obs_unusable", "stop", why=str(exc)))
    info = rec.obs_info
    if info is not None:
        rec.brand = brand_of(info.receiver, info.program) or rec.brand
        if info.first is None or info.last is None:
            rec.issues.append(Issue("no_times", "warn"))
        if "G" not in info.systems:
            rec.issues.append(Issue("no_gps", "stop"))
        elif rover_systems and not [s for s in info.systems if s in rover_systems]:
            rec.issues.append(Issue("no_common", "stop"))
        if info.interval and info.interval > 5.0:
            rec.issues.append(Issue("coarse", "warn", s=info.interval))
        if flight is not None and info.first is not None and info.last is not None:
            if info.last < flight[0] or info.first > flight[1]:
                rec.issues.append(Issue("no_overlap", "stop"))
            elif info.first > flight[0] or info.last < flight[1]:
                rec.issues.append(Issue("coverage", "warn"))
        if not info.antenna:
            rec.issues.append(Issue("no_antenna", "warn"))
    elif not any(i.key in ("raw", "no_decoder", "unreadable", "obs_unusable") for i in rec.issues):
        rec.issues.append(Issue("no_obs", "stop"))
    if not rec.navs and not rover_has_nav:
        rec.issues.append(Issue("no_nav", "stop"))
    elif not rec.navs:
        rec.issues.append(Issue("no_nav_base", "warn"))
    return rec


# ---------------------------------------------------------------------------------------------------------------------
# The base for the pipeline
# ---------------------------------------------------------------------------------------------------------------------

def make_station(position, name="Own base"):
    """A station object (same fields as an RGP one) at the typed phase centre, so the rest of the chain needs no special case."""
    station = rgp.Station(OWN_CODE, OWN_CODE, name, position.lat, position.lon, position.h, None, None, "", "", provider="own")
    return station


def make_product(rec):
    """A product object listing the user's files, for the report."""
    names = rec.names["obs"]
    obs = [(rec.obs, ", ".join(names) if names else os.path.basename(rec.obs))]
    nav = [(p, n) for p, n in zip(rec.navs, rec.names["nav"])]
    interval = rec.obs_info.interval if rec.obs_info is not None else None
    return rgp.Product("own", "Files supplied by the user", interval or 0.0, int(rec.obs_info.version) if rec.obs_info else 3, obs, nav)


def make_base(rec, position, site, window):
    """BaseData for `pipeline.compute` from the recognised files and the position. `site` = (lat, lon) of the photos."""
    if not rec.ok():
        raise pipeline.PipelineError("The base files cannot be used.")
    station = make_station(position)
    distance = geo.distance_m(site[0], site[1], position.lat, position.lon) if site else 0.0
    spec = pipeline.BaseSpec(station, make_product(rec), distance)
    base = pipeline.BaseData(spec, rec.obs, list(rec.navs), rec.obs_info, position.xyz)
    base.antenna_delta = (0.0, 0.0, 0.0)               # the position typed is the phase centre: nothing to add
    info = rec.obs_info
    if window is not None and (info.first is None or info.last is None or info.first > window.start or info.last < window.end):
        base.warnings.append("coverage")
    if info.approx_xyz is not None:
        shift = float(np.linalg.norm(np.array(info.approx_xyz) - np.array(position.xyz)))
        if shift > 1.0:
            base.warnings.append("header_position_own")
    if info.interval and info.interval > 5.0:
        base.warnings.append("coarse_sampling")
    return base
