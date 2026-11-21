"""Readers of the public files the station snapshots are made from. No network here (`build_catalogs.py` downloads, these parse).

  * SINEX / SSC (IGS, EPN, Geoscience Australia): station positions and velocities, `SOLUTION/ESTIMATE` block;
  * the NOAA CORS coordinate pages (`ssss_20.coord.txt`);
  * the directory pages of the open mirrors (to learn the nine character station names and the source letter);
  * site logs (IGS format) are not used: only coordinate solutions are, because a site log position is good to a decimetre.

Every reader returns plain dictionaries; geocentric metres, metres per year, decimal years.
"""

import re

import numpy as np

from .. import geo

SNX_EPOCH = re.compile(r"^(\d\d):(\d\d\d):(\d+)$")


def snx_epoch(text):
    """`20:001:00000` (year, day of year, seconds of the day) -> decimal year. None for `00:000:00000` (open end)."""
    match = SNX_EPOCH.match((text or "").strip())
    if not match:
        return None
    yy, doy, sec = int(match.group(1)), int(match.group(2)), int(match.group(3))
    if doy == 0:
        return None
    year = 1900 + yy if yy >= 50 else 2000 + yy
    days = 366 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 365
    return year + (doy - 1 + sec / 86400.0) / days


def read_block(lines, name):
    """The lines of a `+NAME` ... `-NAME` block of a SINEX file (comments removed)."""
    out, inside = [], False
    for line in lines:
        if line.startswith("+" + name):
            inside = True
        elif line.startswith("-" + name):
            break
        elif inside and not line.startswith("*"):
            out.append(line.rstrip("\n"))
    return out


def parse_sinex_estimates(lines):
    """`SOLUTION/ESTIMATE` -> {(code, soln): {"pos": (x, y, z), "vel": (vx, vy, vz) or None, "epoch": decimal year}}."""
    found = {}
    for line in read_block(lines, "SOLUTION/ESTIMATE"):
        parts = line.split()
        if len(parts) < 9 or parts[1] not in ("STAX", "STAY", "STAZ", "VELX", "VELY", "VELZ"):
            continue
        try:
            value = float(parts[-2])
        except ValueError:
            continue
        code, soln, epoch = parts[2].upper(), int(parts[4]), snx_epoch(parts[5])
        entry = found.setdefault((code, soln), {"p": [None] * 3, "v": [None] * 3, "epoch": epoch})
        axis = "XYZ".index(parts[1][3])
        entry["p" if parts[1].startswith("STA") else "v"][axis] = value
    out = {}
    for key, entry in found.items():
        if None in entry["p"]:
            continue
        vel = tuple(entry["v"]) if None not in entry["v"] else None
        out[key] = {"pos": tuple(entry["p"]), "vel": vel, "epoch": entry["epoch"]}
    return out


def parse_sinex_sites(lines):
    """`SITE/ID` -> {code: {"domes", "name", "long_id"}}. The long id (nine characters) is there for the Geoscience Australia files only."""
    out = {}
    for line in read_block(lines, "SITE/ID"):
        parts = line.split()
        if len(parts) < 3:
            continue
        code = parts[0].upper()
        domes = parts[2]
        name = line[21:43].strip() if len(line) > 53 else " ".join(parts[3:])
        match = re.search(r"\b([A-Z0-9]{4}\d\d[A-Z]{3})\b", line)
        out[code] = {"domes": domes, "name": name, "long_id": match.group(1) if match else ""}
    return out


def parse_open_solutions(lines):
    """`SOLUTION/DISCONTINUITY` of an IGS `soln_*.snx` file -> {code: soln number of the position solution that has no end date}."""
    last = {}
    for line in read_block(lines, "SOLUTION/DISCONTINUITY"):
        parts = line.split()
        if len(parts) < 7 or parts[6] != "P":                  # the last letter tells a position (P) from a velocity (V) discontinuity
            continue
        try:
            soln = int(parts[2])
        except ValueError:
            continue
        end = parts[5]
        if end.startswith("00:000"):
            last[parts[0].upper()] = soln
    return last


EPN_LINE = re.compile(r"^(\S+)\s+(\S+)\s+\S+\s+(\S+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+[\d.]+\s+[\d.]+\s+[\d.]+\s+(\d+)\s+"
                      r"(\d\d:\d\d\d:\d+)\s+(\d\d:\d\d\d:\d+)\s+(\d\d:\d\d\d:\d+)\s*$")
EPN_VEL = re.compile(r"^(\S+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+[\d.]+\s+[\d.]+\s+[\d.]+\s*$")


def parse_epn_ssc(lines):
    """EPN position and velocity file (SSC) -> {code: list of solutions, each {"soln", "pos", "vel", "start", "end", "epoch", "domes"}}."""
    out, current = {}, None
    for line in lines:
        match = EPN_LINE.match(line.rstrip("\n"))
        if match:
            domes, code = match.group(1), match.group(3).upper()
            current = {"soln": int(match.group(7)), "pos": tuple(float(match.group(i)) for i in (4, 5, 6)), "vel": None,
                       "start": snx_epoch(match.group(8)), "end": snx_epoch(match.group(9)), "epoch": snx_epoch(match.group(10)),
                       "domes": domes}
            out.setdefault(code, []).append(current)
            continue
        vel = EPN_VEL.match(line.rstrip("\n"))
        if vel and current is not None and current["vel"] is None:
            current["vel"] = tuple(float(vel.group(i)) for i in (2, 3, 4))
    return out


def latest_epn_solutions(solutions, reference_year, max_gap_years=0.25):
    """The current solution of each EPN station: the last one, if it ends within `max_gap_years` of the end of the file.

    The solution has no velocity for the recent stations of the `short` file (then `vel` is None: the position is used as it is)."""
    out = {}
    for code, items in solutions.items():
        if not items:
            continue
        last = max(items, key=lambda i: i["soln"])
        if last["end"] is not None and last["end"] >= reference_year - max_gap_years:
            out[code] = last
    return out


def parse_epn_names(lines):
    """The header SINEX of the EPN (`euref_20.snx`, `SITE/ID`) -> {code: name}. Names there are cut at 22 characters."""
    out = {}
    for line in read_block(lines, "SITE/ID"):
        parts = line.split()
        if len(parts) >= 5:
            out[parts[0].upper()] = line[21:43].strip()
    return out


def parse_listing(text, kind_regex):
    """A directory page of an open mirror -> {nine character id: source letter} of the files matching `kind_regex`.

    `kind_regex` is the end of the file name, for example `_01D_30S_MO\\.crx\\.gz`. The page may be HTML (links) or plain text."""
    pattern = re.compile(r"\b([A-Z0-9]{4}\d\d[A-Z]{3})_([RSU])_\d{11}" + kind_regex)
    out = {}
    for match in pattern.finditer(text):
        out.setdefault(match.group(1), match.group(2))
    return out


def to_geographic(xyz):
    lat, lon, h = geo.ecef_to_llh(*xyz)
    return float(lat), float(lon), float(h)


NOAA_NAME = re.compile(r"^\s*(.+?)\s*\((\w{4})\)\s*,\s*(.*)$")
NOAA_NUMBER = re.compile(r"([XYZ])\s*=\s*(-?\d+\.\d+)\s*m")
NOAA_HEIGHT = re.compile(r"ellipsoid height\s*=\s*(-?\d+\.\d+)\s*m")


def parse_noaa_coord(text):
    """A NOAA CORS coordinate page -> dict(code, name, region, xyz, lat, lon, h, epoch) in NAD 83 (2011) epoch 2010.0, or None.

    The first NAD 83 (2011) position of the page is the one of the antenna reference point, which is the point the files refer to."""
    lines = text.splitlines()
    name = region = code = ""
    for line in lines[:6]:
        match = NOAA_NAME.match(line)
        if match:
            name, code, region = match.group(1).strip(), match.group(2).lower(), match.group(3).strip()
            break
    if not code:
        return None
    start = next((i for i, line in enumerate(lines) if "NAD_83 (2011) POSITION" in line and "EPOCH 2010.0" in line), None)
    if start is None:
        return None
    xyz, height = {}, None
    for line in lines[start + 1:start + 8]:
        for axis, value in NOAA_NUMBER.findall(line):
            xyz[axis] = float(value)
        match = NOAA_HEIGHT.search(line)
        if match:
            height = float(match.group(1))
    if len(xyz) != 3 or height is None:
        return None
    position = (xyz["X"], xyz["Y"], xyz["Z"])
    lat, lon, h = to_geographic(position)
    if abs(h - height) > 0.05:
        return None                                  # the page and its own numbers disagree: do not trust it
    return {"code": code, "name": name.replace("_", " ").strip(), "region": region, "xyz": position, "lat": lat, "lon": lon, "h": h,
            "epoch": 2010.0}


def parse_noaa_stations(files_list_text, year, doy):
    """The `YYYY.DDD.files.list` of the NOAA bucket -> sorted station codes that have a daily Hatanaka file that day."""
    pattern = re.compile(r"rinex/%04d/%03d/(?P<c>\w{4})/(?P=c)%03d0\.%02dd\.gz" % (year, doy, doy, year % 100))
    return sorted(set(m.group("c") for m in pattern.finditer(files_list_text)))


def local_enu_velocity(xyz, vel):
    """Geocentric velocity at a position -> (east, north, up) in the same unit."""
    lat, lon, _h = to_geographic(xyz)
    phi, lam = np.radians(lat), np.radians(lon)
    e = np.array([-np.sin(lam), np.cos(lam), 0.0])
    n = np.array([-np.sin(phi) * np.cos(lam), -np.sin(phi) * np.sin(lam), np.cos(phi)])
    u = np.array([np.cos(phi) * np.cos(lam), np.cos(phi) * np.sin(lam), np.sin(phi)])
    v = np.asarray(vel, float)
    return float(e @ v), float(n @ v), float(u @ v)


def enu_to_geocentric(xyz, enu):
    lat, lon, _h = to_geographic(xyz)
    phi, lam = np.radians(lat), np.radians(lon)
    e = np.array([-np.sin(lam), np.cos(lam), 0.0])
    n = np.array([-np.sin(phi) * np.cos(lam), -np.sin(phi) * np.sin(lam), np.cos(phi)])
    u = np.array([np.cos(phi) * np.cos(lam), np.cos(phi) * np.sin(lam), np.sin(phi)])
    v = enu[0] * e + enu[1] * n + enu[2] * u
    return tuple(float(c) for c in v)


def velocities_from_two_solutions(first, second, neighbours=8, horizontal_tolerance=0.012):
    """Velocities of stations known only by two positions at two epochs (Geoscience Australia weekly solutions).

    `first` and `second` map code -> {"pos", "epoch"}. The raw velocity is the difference over the time span; it is kept when its
    horizontal part is within `horizontal_tolerance` m/yr of the median of the nearest stations (a station that moved because of an
    antenna change would stand out), otherwise, and for the vertical part always (too noisy over two years), the median of the
    nearest stations is used: the Australian plate is rigid to a few mm/yr, which is far below the metre these velocities guard against.
    Returns {code: (vx, vy, vz)}."""
    rows = []
    for code, item in second.items():
        other = first.get(code)
        raw = None
        if other is not None and item["epoch"] - other["epoch"] > 0.5:
            span = item["epoch"] - other["epoch"]
            raw = tuple((a - b) / span for a, b in zip(item["pos"], other["pos"]))
        rows.append((code, item["pos"], raw))
    pos = np.array([r[1] for r in rows])
    enu_raw = [local_enu_velocity(p, r) if r is not None else None for (_c, p, r) in rows]
    have = [i for i, v in enumerate(enu_raw) if v is not None and np.hypot(v[0], v[1]) < 0.15]
    out = {}
    for i, (code, p, _raw) in enumerate(rows):
        dist = np.linalg.norm(pos[have] - pos[i], axis=1) if have else np.array([])
        order = [have[j] for j in np.argsort(dist)[:neighbours + 1] if have[j] != i][:neighbours]
        if not order:
            continue
        local = np.median(np.array([enu_raw[j] for j in order]), axis=0)
        mine = enu_raw[i]
        east, north = (mine[0], mine[1]) if mine is not None and np.hypot(mine[0] - local[0], mine[1] - local[1]) <= horizontal_tolerance \
            else (local[0], local[1])
        out[code] = enu_to_geocentric(p, (float(east), float(north), float(local[2])))
    return out
