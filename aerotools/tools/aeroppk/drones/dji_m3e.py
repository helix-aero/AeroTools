"""DJI Mavic 3 Enterprise (M3E) flight folder reader.

A flight folder (a sub-folder of DCIM, `DJI_YYYYMMDDHHMM_NNN_<mission>`) holds the photos and, next to them:

  <folder>_Timestamp.MRK   exposure events: one text line per photo (GPS week + second of week, antenna to camera offsets,
                           the drone's own real time position and its RTK status)
  <folder>_PPKOBS.obs      RINEX 3 observations of the drone's own GNSS receiver (5 Hz on the files we measured)
  <folder>_PPKNAV.nav      RINEX 3 navigation (broadcast ephemerides), often with very few satellites
  <folder>_PPKRAW.bin      raw receiver log (RTCM 3), only needed to rebuild the two RINEX files

Line format of the MRK (checked on 30 real flights, 2023 and later):

  1   379192.119164   [2292]    2,N    49,E    81,V   45.00058025,Lat   5.00700540,Lon   967.321,Ellh   1.55, 1.07, 2.51   16,Q

The time is GPS time, not UTC: events fall inside the RINEX observation window as written in GPS time, and the first
photo of the sample above was taken 2 s after its file name time (local time minus the time zone minus 18 s of leap seconds),
which only works with GPS time. N, E, V are millimetres, the vector from the antenna to the camera centre, V positive down
(the camera hangs below the antenna: V > 0 and a lower ellipsoidal height).
"""

import os
import re

from .... import i18n
from .. import gnsstime
from .base import FlightFiles, Need, PhotoEvent

READER_ID = "dji_m3e"
NAME = "DJI Mavic 3 Enterprise (or same files)"

# A mission planner may append a waypoint tag after the camera letter: DJI_<time>_<n>_V_point12.JPG
PHOTO = re.compile(r"^DJI_(\d{14})_(\d{4})_([A-Z])(?:_[A-Za-z0-9-]+)*\.(jpg|jpeg|dng)$", re.IGNORECASE)
MRK_LINE = re.compile(
    r"^\s*(\d+)\s+(-?[\d.]+)\s+\[(-?\d+)\]\s+(-?\d+),N\s+(-?\d+),E\s+(-?\d+),V\s+(-?[\d.]+),Lat\s+(-?[\d.]+),Lon\s+"
    r"(-?[\d.]+),Ellh\s+([\d.]+),\s*([\d.]+),\s*([\d.]+)\s+(\d+),Q\s*$")

Q_NONE, Q_SINGLE, Q_FLOAT, Q_FIX = 0, 16, 34, 50
Q_NAMES = {0: "none", 16: "single", 34: "float", 50: "fix"}


def _list(folder):
    try:
        return sorted(os.listdir(folder))
    except OSError:
        return []


def _find(names, suffixes):
    for suffix in suffixes:
        for name in names:
            if name.lower().endswith(suffix.lower()):
                return name
    return None


class DjiM3E(object):
    id = READER_ID
    name = NAME
    antenna_known = True            # the events file carries the antenna -> camera vector of every photo
    exif_models = ("M3E",)          # EXIF model of the photos

    def accepts(self, folder):
        names = _list(folder)
        if any(n.lower().endswith("_timestamp.mrk") for n in names):
            return True
        return any(PHOTO.match(n) for n in names)

    def scan(self, folder):
        files = FlightFiles(folder, READER_ID)
        names = _list(folder)
        best = {}
        for name in names:
            match = PHOTO.match(name)
            if match:
                index = int(match.group(2))
                # several cameras (_V visible, _T thermal...): keep the visible one for the position of the exposure
                if index not in best or match.group(3).upper() == "V":
                    best[index] = name
        files.photos = [os.path.join(folder, best[i]) for i in sorted(best)]
        for attr, suffixes in (("events_file", ("_timestamp.mrk",)), ("obs", ("_ppkobs.obs",)), ("nav", ("_ppknav.nav",)),
                               ("raw", ("_ppkraw.bin",))):
            found = _find(names, suffixes)
            setattr(files, attr, os.path.join(folder, found) if found else None)
        if files.events_file is None:
            found = [n for n in names if n.lower().endswith(".mrk")]
            if len(found) == 1:
                files.events_file = os.path.join(folder, found[0])
        return files

    def needs(self, files):
        photo_count = len(files.photos)
        out = [
            Need("photos", photo_count > 0, Need.BLOCKING, str(photo_count) if photo_count else ""),
            Need("events", files.events_file is not None, Need.BLOCKING,
                 os.path.basename(files.events_file) if files.events_file else "", files.events_file),
            Need("rover_obs", files.obs is not None, Need.BLOCKING,
                 os.path.basename(files.obs) if files.obs else "", files.obs),
            Need("rover_nav", files.nav is not None, Need.ADVISED,
                 os.path.basename(files.nav) if files.nav else "", files.nav),
            Need("raw", files.raw is not None, Need.OPTIONAL, os.path.basename(files.raw) if files.raw else "", files.raw),
        ]
        return out

    def read_events(self, files):
        events, warnings = [], []
        if not files.events_file:
            return events, ["no events file"]
        photo_by_index = {}
        for path in files.photos:
            match = PHOTO.match(os.path.basename(path))
            if match:
                photo_by_index[int(match.group(2))] = path
        try:
            with open(files.events_file, "r", encoding="utf-8", errors="replace") as handle:
                lines = handle.read().splitlines()
        except OSError as exc:
            return events, ["cannot read the events file: %s" % exc]
        bad = 0
        no_time = []
        for line in lines:
            if not line.strip():
                continue
            match = MRK_LINE.match(line)
            if not match:
                bad += 1
                continue
            index, tow, week = int(match.group(1)), float(match.group(2)), int(match.group(3))
            if tow < 0.0 or week <= 0:
                # the receiver wrote a placeholder time (seen: -259200.000000 [-522]) for an exposure it could not time
                path = photo_by_index.get(index)
                no_time.append((index, os.path.basename(path) if path else ""))
                continue
            event = PhotoEvent(index, gnsstime.WEEK * week + tow, photo_by_index.get(index))
            event.offset_ned = (int(match.group(4)) / 1000.0, int(match.group(5)) / 1000.0, int(match.group(6)) / 1000.0)
            event.onboard = (float(match.group(7)), float(match.group(8)), float(match.group(9)))
            event.onboard_sd = (float(match.group(10)), float(match.group(11)), float(match.group(12)))
            event.onboard_q = int(match.group(13))
            event.photo_name = os.path.basename(event.photo) if event.photo else ""
            events.append(event)
        if bad:
            warnings.append("%d %s of the events file could not be read" % (bad, i18n.plural(bad, "line", "lines")))
        files.extra["no_time"] = no_time
        if no_time:
            warnings.append("%d %s no valid time in the events file: %s" % (
                len(no_time), i18n.plural(len(no_time), "exposure has", "exposures have"), ", ".join(name or "#%d" % index for index, name in no_time[:5])))
        return events, warnings
