"""RTKLIB-EX (demo5) as a sub-process: find the programs, write the configuration, run `rnx2rtkp`, read the `.pos` file.

RTKLIB-EX is BSD 2-clause licensed (see aerotools/vendor/rtklib/LICENSE.txt). It runs outside Metashape, as a separate
process, so a crash or a bad file never takes the application down. Nothing here needs Qt or Metashape.
"""

import datetime
import os
import re
import subprocess
import threading

import numpy as np

from . import geo, gnsstime

VENDOR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "vendor", "rtklib"))
NO_WINDOW = 0x08000000
EXE = ".exe" if os.name == "nt" else ""
PROGRAMS = ("rnx2rtkp", "convbin", "crx2rnx")

# Solution quality codes written by RTKLIB in the `.pos` file.
Q_FIX, Q_FLOAT, Q_SBAS, Q_DGPS, Q_SINGLE, Q_PPP = 1, 2, 3, 4, 5, 6
Q_NAMES = {1: "fix", 2: "float", 3: "sbas", 4: "dgps", 5: "single", 6: "ppp"}

NAVSYS_BITS = {"G": 1, "S": 2, "R": 4, "E": 8, "J": 16, "C": 32, "I": 64}


class RtklibError(Exception):
    """A failure with a sentence the user can read."""


def program_path(name, folder=None):
    folder = folder or VENDOR
    path = os.path.join(folder, name + EXE)
    return path if os.path.isfile(path) else None


def find_programs(extra_folder=None):
    """{name: path or None} for rnx2rtkp, convbin and crx2rnx.

    Order: the copy shipped with AeroPPK, a folder the user chose (setting), then the PATH. Never a hard coded folder.
    """
    found = {}
    for name in PROGRAMS:
        path = program_path(name)
        if path is None and extra_folder:
            path = program_path(name, extra_folder)
        if path is None:
            for folder in os.environ.get("PATH", "").split(os.pathsep):
                candidate = os.path.join(folder.strip('"'), name + EXE)
                if folder and os.path.isfile(candidate):
                    path = candidate
                    break
        found[name] = path
    return found


def navsys_mask(letters):
    return sum(NAVSYS_BITS.get(letter, 0) for letter in letters)


class Options(object):
    """What we tell RTKLIB. The defaults are the result of the benchmark in docs/AEROPPK.md, not guesses."""

    def __init__(self, **kwargs):
        self.frequency = "l1+l2"          # l1, l1+l2, l1+l2+l5
        self.systems = "GREC"             # constellations to use (letters of rinex.SYSTEMS)
        self.elevation_mask = 15.0
        self.dynamics = False             # vehicle dynamics filter: measured worse on drone data (see docs)
        self.solution_type = "combined"   # forward, backward, combined
        self.ambiguity = "fix-and-hold"   # off, continuous, instantaneous, fix-and-hold
        self.glonass_ambiguity = "fix-and-hold"   # off, on, autocal, fix-and-hold
        self.ratio = 3.0                  # validation threshold for the integer fix
        self.min_fix_sats = 4
        self.max_age = 30.0               # seconds, maximum base data age
        self.iono = "brdc"                # off, brdc, dual-freq, est-stec
        self.trop = "saas"                # off, saas, est-ztd
        self.base_xyz = None              # base position, geocentric metres (required)
        self.base_antenna_delta = (0.0, 0.0, 0.0)   # e, n, u of the antenna reference point over the marker (m)
        self.rover_pos = "single"
        self.antex_file = None            # ANTEX file with the calibration of the base antenna (optional, see antex.py)
        self.antex_extra = []             # more ANTEX files, consulted for an antenna the first one does not have (ngs20.atx)
        self.base_antenna = ""            # `ANT # / TYPE` of the base as written in the ANTEX file (model + radome)
        self.satellite_pcv = False        # also apply the satellite antenna model of the same file (experiment, off)
        self.extra = {}                   # raw RTKLIB keys, to override anything (advanced)
        for key, value in kwargs.items():
            if not hasattr(self, key):
                raise TypeError("unknown option %s" % key)
            setattr(self, key, value)

    def copy(self, **kwargs):
        new = Options()
        new.__dict__.update(self.__dict__)
        new.extra = dict(self.extra)
        for key, value in kwargs.items():
            setattr(new, key, value)
        return new

    def to_dict(self):
        data = dict(self.__dict__)
        data["extra"] = dict(self.extra)
        return data


_FREQ = {"l1": "l1", "l1+l2": "l1+l2", "l1+l2+l5": "l1+l2+l5"}


def config_text(options):
    """The RTKLIB `.conf` for `rnx2rtkp`, every key spelled out (nothing left to RTKLIB's defaults)."""
    if options.base_xyz is None:
        raise RtklibError("The base position is missing.")
    x, y, z = options.base_xyz
    de, dn, du = options.base_antenna_delta
    use_pcv = bool(options.antex_file and options.base_antenna)
    keys = [
        ("pos1-posmode", "kinematic"), ("pos1-frequency", _FREQ.get(options.frequency, options.frequency)),
        ("pos1-soltype", options.solution_type), ("pos1-elmask", "%g" % options.elevation_mask),
        ("pos1-snrmask_r", "off"), ("pos1-snrmask_b", "off"),
        ("pos1-snrmask_L1", ",".join(["35"] * 9)), ("pos1-snrmask_L2", ",".join(["35"] * 9)),
        ("pos1-snrmask_L5", ",".join(["35"] * 9)),
        ("pos1-dynamics", "on" if options.dynamics else "off"), ("pos1-tidecorr", "off"),
        ("pos1-ionoopt", options.iono), ("pos1-tropopt", options.trop), ("pos1-sateph", "brdc"),
        ("pos1-posopt1", "on" if use_pcv and options.satellite_pcv else "off"),
        ("pos1-posopt2", "on" if use_pcv else "off"), ("pos1-posopt3", "off"), ("pos1-posopt4", "off"),
        ("pos1-posopt5", "off"), ("pos1-posopt6", "off"), ("pos1-exclsats", ""),
        ("pos1-navsys", str(navsys_mask(options.systems))),
        ("pos2-armode", options.ambiguity), ("pos2-gloarmode", options.glonass_ambiguity), ("pos2-bdsarmode", "on"),
        ("pos2-arfilter", "on"), ("pos2-arthres", "%g" % options.ratio), ("pos2-arthresmin", "%g" % options.ratio),
        ("pos2-arthresmax", "%g" % options.ratio), ("pos2-arthres1", "0.1"), ("pos2-arthres2", "0"),
        ("pos2-arthres3", "1e-09"), ("pos2-arthres4", "1e-05"), ("pos2-varholdamb", "0.1"), ("pos2-gainholdamb", "0.01"),
        ("pos2-arlockcnt", "5"), ("pos2-minfixsats", str(options.min_fix_sats)), ("pos2-minholdsats", "5"),
        ("pos2-mindropsats", "10"), ("pos2-arelmask", "25"), ("pos2-arminfix", "20"), ("pos2-armaxiter", "1"),
        ("pos2-elmaskhold", "15"), ("pos2-aroutcnt", "20"), ("pos2-maxage", "%g" % options.max_age),
        ("pos2-syncsol", "off"), ("pos2-slipthres", "0.05"), ("pos2-dopthres", "0"), ("pos2-rejionno", "2"),
        ("pos2-rejcode", "10"), ("pos2-niter", "1"), ("pos2-baselen", "0"), ("pos2-basesig", "0"),
        ("out-solformat", "llh"), ("out-outhead", "on"), ("out-outopt", "on"), ("out-outvel", "off"),
        ("out-timesys", "gpst"), ("out-timeform", "tow"), ("out-timendec", "3"), ("out-degform", "deg"),
        ("out-fieldsep", ""), ("out-outsingle", "off"), ("out-maxsolstd", "0"), ("out-height", "ellipsoidal"),
        ("out-geoid", "internal"), ("out-solstatic", "all"), ("out-nmeaintv1", "0"), ("out-nmeaintv2", "0"),
        ("out-outstat", "off"),
        ("stats-eratio1", "300"), ("stats-eratio2", "300"), ("stats-eratio5", "300"), ("stats-errphase", "0.005"),
        ("stats-errphaseel", "0.005"), ("stats-errphasebl", "0"), ("stats-errdoppler", "1"), ("stats-snrmax", "52"),
        ("stats-errsnr", "0"), ("stats-errrcv", "0"), ("stats-stdbias", "30"), ("stats-stdiono", "0.03"),
        ("stats-stdtrop", "0.3"), ("stats-prnaccelh", "3"), ("stats-prnaccelv", "1"), ("stats-prnbias", "0.0001"),
        ("stats-prniono", "0.001"), ("stats-prntrop", "0.0001"), ("stats-prnpos", "0"), ("stats-clkstab", "5e-12"),
        ("ant1-postype", "single"), ("ant1-pos1", "0"), ("ant1-pos2", "0"), ("ant1-pos3", "0"), ("ant1-anttype", ""),
        ("ant1-antdele", "0"), ("ant1-antdeln", "0"), ("ant1-antdelu", "0"),
        ("ant2-postype", "xyz"), ("ant2-pos1", "%.4f" % x), ("ant2-pos2", "%.4f" % y), ("ant2-pos3", "%.4f" % z),
        ("ant2-anttype", options.base_antenna if use_pcv else ""), ("ant2-antdele", "%.4f" % de), ("ant2-antdeln", "%.4f" % dn), ("ant2-antdelu", "%.4f" % du),
        ("ant2-maxaveep", "1"), ("ant2-initrst", "on"),
        ("misc-timeinterp", "on"), ("misc-sbasatsel", "0"), ("misc-rnxopt1", ""), ("misc-rnxopt2", ""), ("misc-pppopt", ""),
        ("file-satantfile", options.antex_file if use_pcv and options.satellite_pcv else ""),
        ("file-rcvantfile", options.antex_file if use_pcv else ""), ("file-staposfile", ""), ("file-geoidfile", ""),
        ("file-ionofile", ""), ("file-dcbfile", ""), ("file-eopfile", ""),
    ]
    table = dict(keys)
    names = [k for k, _ in keys] + [k for k in options.extra if k not in table]
    table.update(options.extra)
    return "".join("%-18s =%s\n" % (name, table[name]) for name in names)


class PosEpoch(object):
    """Columns of a `.pos` file in numpy arrays: time (GPS seconds), position, quality, satellites, sigmas, ratio."""

    FIELDS = ("t", "lat", "lon", "h", "q", "ns", "sdn", "sde", "sdu", "age", "ratio")

    def __init__(self, rows):
        arr = np.asarray(rows, dtype=float).reshape(-1, len(self.FIELDS))
        for index, name in enumerate(self.FIELDS):
            setattr(self, name, arr[:, index])
        order = np.argsort(self.t, kind="stable")
        if not np.all(order == np.arange(len(order))):
            for name in self.FIELDS:
                setattr(self, name, getattr(self, name)[order])

    def __len__(self):
        return len(self.t)

    def quality_counts(self):
        counts = {}
        for q in self.q.astype(int):
            counts[q] = counts.get(q, 0) + 1
        return counts

    def fix_fraction(self):
        return float(np.mean(self.q == Q_FIX)) if len(self) else 0.0


def parse_pos(path):
    """Read a `.pos` file written with `out-timeform=tow` and `out-solformat=llh`. Returns (PosEpoch, header_lines)."""
    rows, header = [], []
    try:
        with open(path, "r", encoding="ascii", errors="replace") as handle:
            for line in handle:
                if line.startswith("%"):
                    header.append(line.rstrip("\n"))
                    continue
                words = line.split()
                if len(words) < 15:
                    continue
                week, tow = int(words[0]), float(words[1])
                rows.append([gnsstime.WEEK * week + tow,
                             float(words[2]), float(words[3]), float(words[4]), int(words[5]), int(words[6]),
                             float(words[7]), float(words[8]), float(words[9]), float(words[13]), float(words[14])])
    except OSError as exc:
        raise RtklibError("Cannot read the solver output: %s" % exc)
    return PosEpoch(rows), header


_PROGRESS = re.compile(rb"processing\s*:\s*(\d{4})/(\d\d)/(\d\d)\s+(\d\d):(\d\d):(\d\d)")


def run_rnx2rtkp(rover_obs, base_obs, nav_files, options, out_path, start=None, end=None, exe=None, progress=None,
                 cancelled=None, timeout=3600, work_dir=None):
    """Run RTKLIB on one rover file, one base file and any number of navigation files. Returns the log text.

    `start` / `end` are GPS datetimes limiting the processing window. `progress(fraction)` is called while it runs;
    `cancelled()` returning True stops the process. On failure raises RtklibError with the last lines of the log.
    """
    exe = exe or program_path("rnx2rtkp")
    if not exe:
        raise RtklibError("RTKLIB (rnx2rtkp) was not found.")
    work_dir = work_dir or os.path.dirname(os.path.abspath(out_path))
    os.makedirs(work_dir, exist_ok=True)
    conf = os.path.join(work_dir, os.path.splitext(os.path.basename(out_path))[0] + ".conf")
    with open(conf, "w", encoding="mbcs" if os.name == "nt" else "utf-8") as handle:       # the ANTEX path may hold accents
        handle.write(config_text(options))
    if os.path.exists(out_path):
        os.remove(out_path)
    args = [exe, "-k", conf, "-o", out_path]
    if start is not None:
        args += ["-ts", start.strftime("%Y/%m/%d"), start.strftime("%H:%M:%S")]
    if end is not None:
        args += ["-te", end.strftime("%Y/%m/%d"), end.strftime("%H:%M:%S")]
    args += [rover_obs, base_obs] + list(nav_files)
    flags = NO_WINDOW if os.name == "nt" else 0
    try:
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=flags)   # stdout is never read: it must not fill up
    except OSError as exc:
        raise RtklibError("RTKLIB does not start: %s" % exc)
    log = []
    span = (end - start).total_seconds() if (start is not None and end is not None) else 0.0

    def pump():
        buffer = b""
        while True:
            chunk = proc.stderr.read(256)
            if not chunk:
                break
            buffer = (buffer + chunk)[-2000:]
            match = None
            for match in _PROGRESS.finditer(buffer):
                pass
            if match is not None and progress is not None and span > 0:
                stamp = datetime.datetime(*[int(g) for g in match.groups()])
                try:
                    progress(max(0.0, min(1.0, (stamp - start).total_seconds() / span)))
                except Exception:
                    pass
            if b"error" in chunk.lower() or b"warning" in chunk.lower():
                log.append(chunk.decode("ascii", "replace"))

    thread = threading.Thread(target=pump, daemon=True)
    thread.start()
    waited = 0.0
    while True:
        try:
            proc.wait(timeout=0.25)
            break
        except subprocess.TimeoutExpired:
            waited += 0.25
            if (cancelled is not None and cancelled()) or waited > timeout:
                proc.kill()
                proc.wait()
                thread.join(2)
                raise RtklibError("Calculation stopped." if waited <= timeout else "RTKLIB took too long.")
    thread.join(5)
    text = "".join(log)
    if proc.returncode != 0 or not os.path.isfile(out_path):
        raise RtklibError(("RTKLIB failed: " + text.strip()[-300:]).strip())
    return text


def interpolate(pos, times, max_gap=None):
    """Position of the trajectory at `times` (GPS seconds).

    Linear interpolation of geocentric coordinates between the two epochs around each time. Quality is the WORST of the two
    epochs (a fixed epoch next to a float one is float), sigmas the larger. Times with no epoch within `max_gap` seconds
    on each side (a hole in the trajectory) get q = 0 and NaN. Returns a dict of arrays.
    """
    times = np.asarray(times, float)
    count = len(times)
    out = {"lat": np.full(count, np.nan), "lon": np.full(count, np.nan), "h": np.full(count, np.nan),
           "q": np.zeros(count, int), "ns": np.zeros(count, int), "sdn": np.full(count, np.nan),
           "sde": np.full(count, np.nan), "sdu": np.full(count, np.nan), "ratio": np.full(count, np.nan),
           "gap": np.full(count, np.nan)}
    if len(pos) < 2:
        return out
    if max_gap is None:
        step = float(np.median(np.diff(pos.t)))
        max_gap = max(1.0, 3.0 * step)
    x, y, z = geo.llh_to_ecef(pos.lat, pos.lon, pos.h)
    index = np.searchsorted(pos.t, times)
    index = np.where((index == 0) & (times == pos.t[0]), 1, index)          # an event at the very first epoch is interpolated too
    inside = (index > 0) & (index < len(pos))
    k = np.nonzero(inside)[0]
    j = index[k]
    i = j - 1
    t0, t1, tk = pos.t[i], pos.t[j], times[k]
    near = ~((tk - t0 > max_gap) | (t1 - tk > max_gap))                      # no epoch within `max_gap` on a side: a hole
    k, i, j, t0, t1, tk = k[near], i[near], j[near], t0[near], t1[near], tk[near]
    span = t1 - t0
    w = np.where(span == 0.0, 0.0, (tk - t0) / np.where(span == 0.0, 1.0, span))
    la, lo, hh = geo.ecef_to_llh(x[i] + w * (x[j] - x[i]), y[i] + w * (y[j] - y[i]), z[i] + w * (z[j] - z[i]))
    out["lat"][k], out["lon"][k], out["h"][k] = la, lo, hh
    out["q"][k] = np.maximum(pos.q[i], pos.q[j]).astype(int)
    out["ns"][k] = np.minimum(pos.ns[i], pos.ns[j]).astype(int)
    out["sdn"][k] = np.maximum(pos.sdn[i], pos.sdn[j])
    out["sde"][k] = np.maximum(pos.sde[i], pos.sde[j])
    out["sdu"][k] = np.maximum(pos.sdu[i], pos.sdu[j])
    out["ratio"][k] = np.minimum(pos.ratio[i], pos.ratio[j])
    out["gap"][k] = np.maximum(tk - t0, t1 - tk)
    return out
