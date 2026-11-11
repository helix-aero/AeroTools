"""Aero-L3: the `*_Timestamp.MRK` file DJI writes next to the photos of a flight. Pure Python 3.9.

One line per photo, tab separated::

    0003_LV  216012.500000  [2608]  107,N  713,E  217,V  48.85412345,Lat  2.34912345,Lon  95.412,Ellh  0.001221, 0.001338, 0.002954  34,Q

photo label, GPS second of week, [GPS week], the N / E / V offsets in millimetres (lever arm between the GNSS antenna and the camera), latitude,
longitude and ellipsoidal height of the camera, the standard deviations (lat, lon, height, metres) and the status (16 single, 34 float,
50 RTK fixed). **The time is GPS time, not UTC** (verified: `UTCAtExposure` of the XMP is the same GPS time, 18 s ahead of UTC).
"""

import datetime
import re
from collections import namedtuple

GPS_EPOCH = datetime.datetime(1980, 1, 6)
LEAP_SECONDS = 18                         # GPS - UTC since 2017-01-01

MrkShot = namedtuple("MrkShot", "label stem lens tow week n_mm e_mm v_mm lat lon height std_lat std_lon std_height quality")

_LINE = re.compile(
    r"^\s*(?P<label>\d+(?:_[LR]V)?)\s+(?P<tow>[\d.]+)\s+\[(?P<week>\d+)\]\s+"
    r"(?P<n>-?\d+),N\s+(?P<e>-?\d+),E\s+(?P<v>-?\d+),V\s+"
    r"(?P<lat>-?[\d.]+),Lat\s+(?P<lon>-?[\d.]+),Lon\s+(?P<h>-?[\d.]+),Ellh\s+"
    r"(?P<sy>[\d.]+),\s*(?P<sx>[\d.]+),\s*(?P<sz>[\d.]+)\s+(?P<q>\d+),Q")


def parse_line(line):
    """One `MrkShot`, or None for a line that is not a shot."""
    m = _LINE.match(line)
    if not m:
        return None
    label = m.group("label")
    stem, _, lens = label.partition("_")
    return MrkShot(label, stem, lens[:1] if lens else "", float(m.group("tow")), int(m.group("week")), int(m.group("n")), int(m.group("e")),
                   int(m.group("v")), float(m.group("lat")), float(m.group("lon")), float(m.group("h")), float(m.group("sy")), float(m.group("sx")),
                   float(m.group("sz")), int(m.group("q")))


def parse_mrk(path):
    """The shots of a MRK file (a list, empty when the file cannot be read or holds no shot)."""
    shots = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                shot = parse_line(line)
                if shot is not None:
                    shots.append(shot)
    except OSError:
        return []
    return shots


def gps_datetime(week, tow):
    """GPS time of a (week, second of week) as a naive datetime."""
    return GPS_EPOCH + datetime.timedelta(weeks=week, seconds=tow)


def gps_to_utc(moment):
    return moment - datetime.timedelta(seconds=LEAP_SECONDS)


def parse_gps_stamp(text):
    """The `UTCAtExposure` string of the XMP (`2030-01-01T12:00:12.500000`) as a naive datetime; it is GPS time in spite of its name. None if unreadable."""
    if not text:
        return None
    text = text.strip().rstrip("Z")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def mean_offsets(shots):
    """(mean N, mean E, mean V) of the lever arm offsets in millimetres, or None for an empty list."""
    if not shots:
        return None
    n = float(len(shots))
    return (sum(s.n_mm for s in shots) / n, sum(s.e_mm for s in shots) / n, sum(s.v_mm for s in shots) / n)
