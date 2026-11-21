"""Networks that publish RINEX 3 files under the long IGS names (`SSSSMRCCC_R_YYYYDDDHHMM_01D_30S_MO.crx.gz`).

IGS, EPN and Geoscience Australia all do, each with its own folder layout and its own mix of daily, hourly and 15 minute files.
`Rinex3Provider` builds the candidate files from a few folder functions; the server decides which ones exist (`probe`).

The long name carries a source letter that is not the same for every station: `R` (receiver files) or `S` (files cut from a
real-time stream). The snapshot remembers the letter seen on the day it was made; the other letter is tried as well.
"""

from .. import gnsstime
from . import provider as _provider


def long_name(long_id, tag, slot, duration, interval_text, kind, ext):
    """`ABMF00GLP_R_20263050000_01D_30S_MO.crx.gz`. `kind` is MO for observations, MN or GN... for navigation (no interval then)."""
    stamp = "%04d%03d%02d%02d" % (slot.year, gnsstime.day_of_year(slot), slot.hour, slot.minute)
    middle = "_".join([part for part in (stamp, duration, interval_text) if part])
    return "%s_%s_%s_%s.%s" % (long_id, tag, middle, kind, ext)


class Template(object):
    """One way a network publishes observation files: files of `minutes` minutes in the folder `directory(slot)`."""

    def __init__(self, key, label, interval, minutes, directory, duration, interval_text, ext="crx.gz"):
        self.key, self.label, self.interval = key, label, interval
        self.minutes = minutes                     # 15, 60 or 1440
        self.directory = directory                 # slot (UTC datetime) -> folder URL
        self.duration, self.interval_text = duration, interval_text
        self.ext = ext


class Rinex3Provider(_provider.Provider):
    """A network with long RINEX 3 names. Subclass or instantiate and set the class attributes of `Provider` plus these:

    templates      Template list, best first
    nav_dir        slot -> folder URL of the daily navigation files of a station (None when the network has none)
    nav_global     slot -> (url, file name) of a worldwide broadcast file of the day, the fallback (None when not used)
    """

    templates = ()
    nav_dir = None
    nav_global = None
    nav_station = ("MN",)                         # kinds of the station navigation files, first complete set that exists wins
    nav_split = ("GN", "RN", "EN", "CN")
    nav_ext = "rnx.gz"

    def _tags(self, station):
        tags = [station.tag or "R"]
        tags += [t for t in ("R", "S") if t not in tags]
        return tags

    def _nav_groups(self, station, days, tag):
        groups = []
        if self.nav_dir is not None:
            for kinds in (self.nav_station, self.nav_split):
                group = []
                for day in days:
                    name_for = lambda kind: long_name(station.long_id, tag, day, "01D", "", kind, self.nav_ext)
                    group.extend(("%s/%s" % (self.nav_dir(day), name_for(kind)), name_for(kind)) for kind in kinds)
                groups.append(group)
        if self.nav_global is not None:
            groups.append([self.nav_global(day) for day in days])
        return groups

    def products(self, station, start_gps, end_gps, margin_s=60.0):
        """Every file set that could cover the flight, best first. Nothing is tested on the network here (see `probe`)."""
        start_utc, end_utc = _provider.window_utc(start_gps, end_gps, margin_s)
        days = _provider.utc_slots(start_utc.replace(hour=0, minute=0), end_utc, 1440)
        out = []
        for template in self.templates:
            slots = _provider.utc_slots(start_utc, end_utc, template.minutes)
            if template.minutes >= 1440:
                slots = days
            for tag in self._tags(station):
                obs = []
                for slot in slots:
                    name = long_name(station.long_id, tag, slot, template.duration, template.interval_text, "MO", template.ext)
                    obs.append(("%s/%s" % (template.directory(slot), name), name))
                groups = self._nav_groups(station, days, tag)
                out.append(_provider.Product(template.key, template.label, template.interval, 3, obs, [], nav_groups=groups))
        return out


class DailyRinex2Provider(_provider.Provider):
    """A network with short RINEX 2 names, one file per day (NOAA CORS): the sampling is only known from the file header."""

    daily_dir = None                              # slot (UTC datetime at 00:00) -> folder URL
    nav_url = None                                # (slot, letter) -> (url, file name), letter n (GPS) or g (GLONASS)

    def products(self, station, start_gps, end_gps, margin_s=60.0):
        start_utc, end_utc = _provider.window_utc(start_gps, end_gps, margin_s)
        days = _provider.utc_slots(start_utc.replace(hour=0, minute=0), end_utc, 1440)
        code = station.code.lower()
        obs, nav = [], []
        for day in days:
            yy = day.year % 100
            doy = gnsstime.day_of_year(day)
            name = "%s%03d0.%02dd.gz" % (code, doy, yy)
            obs.append(("%s/%s/%s" % (self.daily_dir(day), code, name), name))
            nav.extend(self.nav_url(day, letter) for letter in "ng")
        return [_provider.Product("daily_r2", "daily files, RINEX 2", None, 2, obs, nav)]
