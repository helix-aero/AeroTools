"""Light RINEX reader: what AeroPPK needs to know about a file before handing it to the solver.

Only headers (and, for the last epoch, the tail of the file) are read, so a 200 MB daily file costs milliseconds.
RINEX 2.xx and 3.xx observation and navigation files are understood. Compact RINEX (Hatanaka) is detected but must be
unpacked first (see unpack.py).
"""

import datetime
import os

from . import gnsstime

SYSTEMS = {"G": "GPS", "R": "GLONASS", "E": "Galileo", "C": "BeiDou", "J": "QZSS", "S": "SBAS", "I": "NavIC"}
ORDER = "GRECJSI"
# RINEX header label (a fixed word of the file format, not prose; see ALLOWED_RECOGNITION_LISTS in tests/test_no_hardcoded_text.py).
RECEIVER_LABEL = "REC # / TYPE / VERS"


class RinexError(Exception):
    """A file that is not usable RINEX, with a sentence a user can read."""


class ObsInfo(object):
    """Header facts of an observation file."""

    def __init__(self):
        self.path = ""
        self.version = 0.0
        self.kind = ""                     # "O" observation, "N" navigation
        self.system = ""                   # file system letter (M mixed, G, R, ...)
        self.marker = ""
        self.program = ""                  # PGM / RUN BY / DATE: the program that wrote the file
        self.receiver = ""
        self.antenna = ""
        self.approx_xyz = None             # RINEX header position (m) or None when absent / zero
        self.antenna_hen = (0.0, 0.0, 0.0)  # ANTENNA: DELTA H/E/N
        self.interval = None               # seconds, from the header
        self.first = None                  # GPS time (datetime)
        self.last = None
        self.systems = []                  # letters present in the observation codes ("G", "R", ...)
        self.obs_types = {}                # system letter -> list of observation codes
        self.time_system = "GPS"
        self.compact = False
        self.leap_seconds = None
        self.comments = []

    def system_names(self):
        return [SYSTEMS.get(s, s) for s in self.systems]

    def duration(self):
        if self.first is None or self.last is None:
            return None
        return (self.last - self.first).total_seconds()


def is_hatanaka(path):
    try:
        with open(path, "rb") as handle:
            head = handle.read(80)
    except OSError:
        return False
    return b"COMPACT RINEX FORMAT" in head


def _read_header_lines(path, limit=4000):
    lines = []
    try:
        with open(path, "rb") as handle:
            for raw in handle:
                line = raw.decode("ascii", "replace").rstrip("\r\n")
                lines.append(line)
                if line[60:73] == "END OF HEADER" or len(lines) >= limit:
                    break
    except OSError as exc:
        raise RinexError("Cannot read %s: %s" % (os.path.basename(path), exc))
    return lines


def _float(text, default=None):
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


BDT_BEHIND_GPS = 14                      # seconds: BeiDou time = GPS time - 14 s


def to_gps_time(when, time_system):
    """A time written in the time system of a RINEX file (`GLO`, `UTC`, `BDT`; GPS, Galileo, QZSS and IRNSS time are GPS time for our purpose)
    as GPS time. The files of some receivers (Trimble / teqc in GLONASS mode, some Javad) are written in UTC."""
    if when is None:
        return None
    system = (time_system or "GPS").strip().upper()
    if system in ("GLO", "UTC"):
        return when + datetime.timedelta(seconds=gnsstime.leap_seconds(when))
    if system == "BDT":
        return when + datetime.timedelta(seconds=BDT_BEHIND_GPS)
    return when


def read_obs_header(path):
    """Header of an observation file -> ObsInfo. Raises RinexError for something that is not RINEX observations."""
    info = ObsInfo()
    info.path = path
    if is_hatanaka(path):
        info.compact = True
    lines = _read_header_lines(path)
    if not lines or "RINEX VERSION / TYPE" not in (lines[0][60:] if len(lines[0]) > 60 else "") and not info.compact:
        raise RinexError("%s is not a RINEX file." % os.path.basename(path))
    pending = None
    for line in lines:
        label = line[60:].strip()
        body = line[:60]
        if label == "RINEX VERSION / TYPE":
            info.version = _float(body[0:9], 0.0)
            info.kind = body[20:21]
            info.system = body[40:41].strip()
        elif label == "MARKER NAME":
            info.marker = body.strip()
        elif label == "PGM / RUN BY / DATE":
            info.program = body[0:20].strip()
        elif label == RECEIVER_LABEL:
            info.receiver = body[20:40].strip()
        elif label == "ANT # / TYPE":
            info.antenna = body[20:40].strip()
        elif label == "APPROX POSITION XYZ":
            values = [_float(body[i * 14:(i + 1) * 14]) for i in range(3)]
            if None not in values and any(abs(v) > 1.0 for v in values):
                info.approx_xyz = tuple(values)
        elif label == "ANTENNA: DELTA H/E/N":
            values = [_float(body[i * 14:(i + 1) * 14], 0.0) for i in range(3)]
            info.antenna_hen = tuple(values)
        elif label == "INTERVAL":
            info.interval = _float(body[0:10])
        elif label == "TIME OF FIRST OBS":
            info.first = gnsstime.parse_rinex_time(body[0:43])
            info.time_system = (body[48:51] or "GPS").strip() or "GPS"
        elif label == "TIME OF LAST OBS":
            info.last = gnsstime.parse_rinex_time(body[0:43])
        elif label == "LEAP SECONDS":
            try:
                info.leap_seconds = int(body[0:6])
            except ValueError:
                pass
        elif label == "COMMENT":
            info.comments.append(body.strip())
        elif label == "SYS / # / OBS TYPES":
            if body[0:1].strip():
                pending = body[0:1]
                info.obs_types[pending] = body[7:].split()
            elif pending:
                info.obs_types[pending].extend(body[7:].split())
        elif label == "# / TYPES OF OBSERV":
            # RINEX 2: one list for everything, the constellations come from the epochs (see scan_epochs)
            if body[0:6].strip():
                info.obs_types.setdefault("*", [])
            info.obs_types.setdefault("*", []).extend(body[6:].split())
    if info.version >= 3.0:
        info.systems = [s for s in ORDER if s in info.obs_types]
    info.first = to_gps_time(info.first, info.time_system)               # the times of the file are in its own time system
    info.last = to_gps_time(info.last, info.time_system)
    return info


def _epoch_time_v3(line):
    return gnsstime.parse_rinex_time(line[2:].split()[0] + " " + " ".join(line[2:].split()[1:6]))


def last_epoch(path, info=None):
    """GPS time of the last observation epoch (reads the tail only). None if it cannot be found."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            handle.seek(max(0, size - 400000))
            tail = handle.read().decode("ascii", "replace").splitlines()
    except OSError:
        return None
    version = info.version if info is not None else 3.0
    for line in reversed(tail):
        try:
            if version >= 3.0:
                if line.startswith(">"):
                    return _epoch_time_v3(line)
            elif len(line) > 28 and line[0:1] == " " and line[28:29] in "0123456":
                words = line[:26].split()
                if len(words) >= 6 and words[0].isdigit() and 0 < int(words[1]) <= 12:
                    return gnsstime.parse_rinex_time(line[:26])
        except (ValueError, IndexError):
            continue
    return None


def scan_systems_v2(path, max_epochs=40):
    """RINEX 2 observation files list satellites in the epoch lines (12 per line, the rest on continuation lines that start
    with 32 blanks): read the first epochs to find the constellations."""
    found = set()
    count = 0
    try:
        with open(path, "rb") as handle:
            in_header = True
            for raw in handle:
                line = raw.decode("ascii", "replace")
                if in_header:
                    in_header = line[60:73] != "END OF HEADER"
                    continue
                text = None
                if len(line) > 32 and line[28:29] in "0123456" and line[0:1] == " " and line[30:32].strip().isdigit():
                    count += 1
                    if count > max_epochs:
                        break
                    text = line[32:68]
                elif line.startswith(" " * 32) and len(line) > 35 and line[32:33] in ORDER and line[33:35].strip().isdigit():
                    text = line[32:68]
                if text:
                    for index in range(0, len(text), 3):
                        if text[index:index + 1] in ORDER:
                            found.add(text[index:index + 1])
    except OSError:
        pass
    return [s for s in ORDER if s in found]


def read_obs_info(path):
    """Header plus the facts that need a look at the data (last epoch, constellations of a RINEX 2 file)."""
    info = read_obs_header(path)
    if info.kind and info.kind != "O":
        raise RinexError("%s is not an observation file." % os.path.basename(path))
    if info.compact:
        return info
    if info.version < 3.0:
        info.systems = scan_systems_v2(path)
        if not info.systems and info.system:
            info.systems = [info.system] if info.system in ORDER else ["G"]
    if info.last is None:
        info.last = to_gps_time(last_epoch(path, info), info.time_system)
    return info


def read_nav_systems(path):
    """Constellation letters found in a navigation file (header type for RINEX 2, record letters for RINEX 3)."""
    lines = _read_header_lines(path)
    if not lines:
        return []
    version = _float(lines[0][0:9], 0.0)
    kind = lines[0][20:21]
    system = lines[0][40:41].strip()
    if kind not in ("N", "G", "H", "P"):
        return []
    if version < 3.0:
        if kind == "G":
            return ["R"]
        if kind in ("N", ""):
            return ["G"]
        return []
    if system and system != "M":
        return [system]
    found = set()
    try:
        with open(path, "rb") as handle:
            for raw in handle:
                line = raw.decode("ascii", "replace")
                if line[:1] in ORDER and line[1:3].strip().isdigit():
                    found.add(line[:1])
                if len(found) >= 7:
                    break
    except OSError:
        pass
    return [s for s in ORDER if s in found]


def covers(info, start, end, margin=0.0):
    """True when the observation file covers [start, end] (GPS datetimes) with `margin` seconds to spare."""
    if info.first is None or info.last is None:
        return False
    pad = datetime.timedelta(seconds=margin)
    return info.first <= start - pad and info.last >= end + pad
