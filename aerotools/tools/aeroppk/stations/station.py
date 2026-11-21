"""A permanent GNSS station, whatever network it belongs to, and the distance search shared by every network.

`lat`, `lon`, `h` (degrees, ellipsoidal metres) are the position in the frame of the network at the reference epoch of its
coordinates; they are what the map and the distances use. Networks whose frame moves with the plates (IGS, EPN, Geoscience
Australia) also give a velocity: `xyz_at` then returns the position at the date of the flight, which is what the solver needs.
Networks with a fixed frame (RGP, NOAA CORS) have no velocity and `xyz_at` returns the published position.
"""

import datetime

from .. import geo


def decimal_year(when):
    """A date, datetime or decimal year -> decimal year (UTC calendar, good to a day, far below what a velocity needs)."""
    if isinstance(when, (int, float)):
        return float(when)
    if isinstance(when, datetime.datetime):
        day = when.date()
        fraction = (when.hour * 3600 + when.minute * 60 + when.second) / 86400.0
    else:
        day, fraction = when, 0.0
    year_start = datetime.date(day.year, 1, 1)
    length = (datetime.date(day.year + 1, 1, 1) - year_start).days
    return day.year + ((day - year_start).days + fraction) / float(length)


class Station(object):
    """A permanent station. `provider` is the id of its network (see `catalog.py`)."""

    def __init__(self, code, long_id, name, lat, lon, h, start, end, stability, department, provider="rgp", frame=None,
                 xyz=None, vel=None, epoch=None, grade="precise", tag="R"):
        self.code = code
        self.long_id = long_id
        self.name = name
        self.lat, self.lon, self.h = lat, lon, h
        self.start = start               # date put in service or None
        self.end = end                   # date taken out of service or None
        self.stability = stability
        self.department = department
        self.provider = provider
        self.frame = frame
        self.xyz_ref = tuple(xyz) if xyz else None      # geocentric metres at `epoch`, when the source gives them
        self.vel = tuple(vel) if vel else None          # metres per year, geocentric
        self.epoch = epoch                              # decimal year of `xyz_ref`
        self.grade = grade                              # "precise" (coordinate solution) or "log" (site log, decimetre level)
        self.tag = tag                                  # data source letter in long RINEX 3 names: R (receiver), S (stream)
        self.usable = True
        self.reason = ""
        self.distance_m = None           # filled by `nearby`
        self.bearing = None

    @property
    def xyz(self):
        """Geocentric position at the reference epoch of the published coordinates."""
        if self.xyz_ref is not None:
            return self.xyz_ref
        x, y, z = geo.llh_to_ecef(self.lat, self.lon, self.h)
        return float(x), float(y), float(z)

    def xyz_at(self, when):
        """Geocentric position on date `when` (date, datetime or decimal year): the reference position moved by the velocity."""
        x, y, z = self.xyz
        if self.vel is None or self.epoch is None or when is None:
            return x, y, z
        dt = decimal_year(when) - self.epoch
        return x + self.vel[0] * dt, y + self.vel[1] * dt, z + self.vel[2] * dt

    def active_on(self, day):
        if isinstance(day, datetime.datetime):
            day = day.date()
        if self.start and day < self.start:
            return False
        if self.end and day > self.end:
            return False
        return True

    def to_dict(self):
        out = {"code": self.code, "long_id": self.long_id, "name": self.name, "lat": self.lat, "lon": self.lon, "h": self.h,
               "start": self.start.isoformat() if self.start else "", "end": self.end.isoformat() if self.end else "",
               "stability": self.stability, "department": self.department}
        if self.xyz_ref is not None:
            out["xyz"] = [round(v, 4) for v in self.xyz_ref]
        if self.vel is not None:
            out["vel"] = [round(v, 5) for v in self.vel]
        if self.epoch is not None:
            out["epoch"] = self.epoch
        if self.grade != "precise":
            out["grade"] = self.grade
        if self.tag != "R":
            out["tag"] = self.tag
        return out


def parse_date(text):
    text = (text or "").strip()
    try:
        return datetime.date.fromisoformat(text) if text else None
    except ValueError:
        return None


def from_dict(item, provider, frame=None):
    """A station from one entry of a snapshot file."""
    return Station(item["code"], item["long_id"], item["name"], float(item["lat"]), float(item["lon"]), float(item["h"]),
                   parse_date(item.get("start")), parse_date(item.get("end")), item.get("stability", ""), item.get("department", ""),
                   provider=provider, frame=frame, xyz=item.get("xyz"), vel=item.get("vel"), epoch=item.get("epoch"),
                   grade=item.get("grade", "precise"), tag=item.get("tag", "R"))


def nearby(stations, lat, lon, radius_km, day=None, usable_only=True):
    """Stations within `radius_km` of (lat, lon) that were in service on `day`, nearest first, with distance."""
    found = []
    for station in stations:
        if usable_only and not station.usable:
            continue
        if day is not None and not station.active_on(day):
            continue
        distance = geo.distance_m(lat, lon, station.lat, station.lon)
        if distance <= radius_km * 1000.0:
            station.distance_m = distance
            found.append(station)
    found.sort(key=lambda s: s.distance_m)
    return found
