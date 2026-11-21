"""RGP (Reseau GNSS Permanent, IGN France): station catalogue, file names, availability, download.

Public data, anonymous access, Licence Ouverte (Etalab). Server: https://rgpdata.ign.fr/pub (checked 06/11/2026).

What was measured on 06/11/2026 (not assumed):
  * coordinates: https://rgp.ign.fr/STATIONS/coordRGP.php?t=csv, 782 stations, RGF93 v2b (the legal French frame, an ETRS89
    realisation). Angles are written DDD.MMSSsssss (degrees, minutes, seconds glued together), not decimal degrees.
    The RINEX header position of a station equals these coordinates (CRTE: 4664928.549 751615.790 4270552.169).
  * RINEX 3 at 1 Hz (`data_v3/.../data_1/..._15M_01S_MO.crx.gz`, 15 minute files) exists only for the last days
    (found for 7 days ago, gone at 26 days).
  * RINEX 2 at 1 Hz (`data/.../data_1/ssssDDDh.YYd.Z`, one file per hour) exists for years (2022 to 2026 found).
  * RINEX 3 at 30 s, hourly and daily, exists for every day tested (2025, 2026); RINEX 2 at 30 s is daily only (`...0.YYd.Z`).
  * the station name in RINEX 3 file names is the 9 character code of the csv (CRTE00FRA), not always `00FRA`.
"""

import csv
import datetime
import io
import json
import os
import urllib.request

from .... import config
from .. import geo, gnsstime
from . import net, provider as _provider, station as _station
from .provider import Product, utc_slots as _utc_slots

ID = "rgp"
NAME = "RGP (IGN)"
LICENSE = "Licence Ouverte Etalab 2.0 - data: RGP / IGN (https://rgp.ign.fr)"
SERVER = "https://rgpdata.ign.fr/pub"
COORD_URL = "https://rgp.ign.fr/STATIONS/coordRGP.php?t=csv"
SNAPSHOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rgp_stations.json")
FRAME = "ETRS89"                     # RGF93 v2b: what the base coordinates are expressed in
FRAME_LABEL = "RGF93 v2b (ETRS89)"
CATALOG_MAX_AGE_DAYS = 30
NAV_SYSTEMS = "GREC"                 # letters of the RINEX 3 navigation files RGP publishes per constellation


class Station(_station.Station):
    """An RGP station. `lat`, `lon`, `h` in degrees and ellipsoidal metres, all in `frame` (RGF93 v2b for RGP).

    Overseas stations (other frames: RGAF09, RGFG95, RGR92...) are kept in the list but marked unusable."""

    def __init__(self, code, long_id, name, lat, lon, h, start, end, stability, department, provider=ID, frame=FRAME, **more):
        super(Station, self).__init__(code, long_id, name, lat, lon, h, start, end, stability, department, provider=provider,
                                      frame=frame, **more)
        if frame != FRAME:
            self.usable, self.reason = False, "coordinates in a local frame"


_date = _station.parse_date


def _frame_for(department):
    """Overseas stations are in other frames (RGAF09, RGFG95, RGR92...): not usable together with ETRS89 without more work."""
    digits = "".join(c for c in department if c.isdigit())
    return "local" if digits[:2] in ("97", "98") else FRAME


def station_from_dict(item):
    return Station(item["code"], item["long_id"], item["name"], float(item["lat"]), float(item["lon"]), float(item["h"]),
                   _date(item.get("start")), _date(item.get("end")), item.get("stability", ""), item.get("department", ""),
                   frame=_frame_for(item.get("department", "")))


def parse_csv(text):
    """`coordRGP.php?t=csv` -> list of Station. Raises ValueError when it does not look like the file."""
    text = text.lstrip("﻿")
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    rows = []
    for row in reader:
        try:
            code = row["acronyme"].strip()
            long_id = row["acronyme_long"].strip() or (code + "00FRA")
            lat = geo.packed_dms_to_deg(row["latitude"])
            lon = geo.packed_dms_to_deg(row["longitude"])
            height = float(row["hauteur"])
        except (KeyError, ValueError, AttributeError):
            continue
        rows.append(Station(code, long_id, row.get("nom", "").strip(), lat, lon, height, _date(row.get("mise_en_service")),
                            _date(row.get("hors_service")), row.get("groupe_de_stabilite", "").strip(),
                            row.get("departement", "").strip(), frame=_frame_for(row.get("departement", ""))))
    if len(rows) < 100:
        raise ValueError("unexpected station file")
    return rows


def cache_dir():
    return os.path.join(config.settings_dir(), "AeroPPK")


def catalog_cache_path():
    return os.path.join(cache_dir(), "rgp_stations.json")


def load_snapshot():
    with open(SNAPSHOT, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    return [station_from_dict(item) for item in data["stations"]], data.get("date", "")


def load_catalog():
    """(stations, source text). The cached copy when it is recent, else the one shipped with the tool."""
    path = catalog_cache_path()
    try:
        age = datetime.datetime.now() - datetime.datetime.fromtimestamp(os.path.getmtime(path))
        if age < datetime.timedelta(days=CATALOG_MAX_AGE_DAYS):
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return [station_from_dict(i) for i in data["stations"]], data.get("date", "")
    except (OSError, ValueError, KeyError):
        pass
    return load_snapshot()


def refresh_catalog(opener=None):
    """Download the station list from the RGP and cache it. Returns (stations, date) or raises net.NetError."""
    request = urllib.request.Request(COORD_URL, headers={"User-Agent": net.USER_AGENT})
    try:
        with (opener or urllib.request.urlopen)(request, timeout=net.TIMEOUT) as answer:
            text = answer.read().decode("utf-8", "replace")
        stations = parse_csv(text)
    except ValueError as exc:
        raise net.NetError(str(exc))
    except OSError as exc:
        raise net.NetError("cannot reach the RGP: %s" % exc)
    today = datetime.date.today().isoformat()
    os.makedirs(cache_dir(), exist_ok=True)
    with open(catalog_cache_path(), "w", encoding="utf-8") as handle:
        json.dump({"date": today, "source": COORD_URL, "stations": [s.to_dict() for s in stations]}, handle, ensure_ascii=False)
    return stations, today


nearby = _station.nearby


# ---------------------------------------------------------------------------------------------------------------------
# File names
# ---------------------------------------------------------------------------------------------------------------------

def _v3_names(station, slot, duration, interval_text):
    stamp = "%04d%03d%02d%02d" % (slot.year, gnsstime.day_of_year(slot), slot.hour, slot.minute)
    root = "%s_R_%s_%s" % (station.long_id, stamp, duration)
    return root + "_%s_MO.crx.gz" % interval_text, [(root + "_%sN.rnx.gz" % s) for s in NAV_SYSTEMS]


def _dir(slot, version, folder):
    base = "data_v3" if version == 3 else "data"
    return "%s/%s/%04d/%03d/%s" % (SERVER, base, slot.year, gnsstime.day_of_year(slot), folder)


def products(station, start_gps, end_gps, margin_s=60.0):
    """Every file set that could cover the flight, best first. Nothing is tested on the network here (see `probe`).

    The window is padded by `margin_s` seconds on both sides (the solver needs base data before the first photo to converge).
    """
    start_utc = gnsstime.gps_to_utc(start_gps - datetime.timedelta(seconds=margin_s))
    end_utc = gnsstime.gps_to_utc(end_gps + datetime.timedelta(seconds=margin_s))
    code = station.code.lower()
    out = []

    def v3(slots, folder, duration, interval_text, key, label, interval):
        obs, nav = [], []
        for slot in slots:
            obs_name, nav_names = _v3_names(station, slot, duration, interval_text)
            base = _dir(slot, 3, folder)
            obs.append(("%s/%s" % (base, obs_name), obs_name))
            nav.extend(("%s/%s" % (base, n), n) for n in nav_names)
        return Product(key, label, interval, 3, obs, nav)

    def v2(slots, folder, letter_of, key, label, interval):
        obs, nav = [], []
        for slot in slots:
            yy = slot.year % 100
            stem = "%s%03d%s" % (code, gnsstime.day_of_year(slot), letter_of(slot))
            base = _dir(slot, 2, folder)
            obs.append(("%s/%s.%02dd.Z" % (base, stem, yy), "%s.%02dd.Z" % (stem, yy)))
            for kind in "ng":
                nav.append(("%s/%s.%02d%s.Z" % (base, stem, yy, kind), "%s.%02d%s.Z" % (stem, yy, kind)))
        return Product(key, label, interval, 2, obs, nav)

    hour = lambda slot: chr(ord("a") + slot.hour)
    day = lambda slot: "0"
    out.append(v3(_utc_slots(start_utc, end_utc, 15), "data_1", "15M", "01S", "v3_1hz", "1 s, RINEX 3, 15 min files", 1.0))
    out.append(v2(_utc_slots(start_utc, end_utc, 60), "data_1", hour, "v2_1hz", "1 s, RINEX 2, hourly files", 1.0))
    out.append(v3(_utc_slots(start_utc, end_utc, 60), "data_30", "01H", "30S", "v3_30s_hour", "30 s, RINEX 3, hourly files", 30.0))
    days = _utc_slots(start_utc.replace(hour=0, minute=0), end_utc, 1440)
    out.append(v3(days, "data_30", "01D", "30S", "v3_30s_day", "30 s, RINEX 3, daily files", 30.0))
    out.append(v2(days, "data_30", day, "v2_30s_day", "30 s, RINEX 2, daily files", 30.0))
    return out


def probe(station, start_gps, end_gps, wanted_interval=None, opener=None, cancelled=None):
    """Which products really exist on the server for this window.

    Returns a list of Product with `obs` (every file must exist), `nav` (only the existing ones; GPS ephemerides are required)
    and `sizes`. Ordered best first; with `wanted_interval` (seconds) products coarser than it are left out.
    """
    return _provider.probe_products(products(station, start_gps, end_gps), wanted_interval, opener, cancelled)


def fetch(station, product, folder=None, progress=None, cancelled=None, opener=None):
    """Download the files of a product that are not already cached. Returns ([obs paths], [nav paths]) of downloaded archives."""
    return _provider.fetch_product(ID, station, product, folder, progress, cancelled, opener)


class RgpProvider(_provider.Provider):
    id = ID
    name = NAME
    short = "RGP"
    color = "#8b5cf6"
    licence = LICENSE
    licence_short = "Licence Ouverte Etalab 2.0"
    site = SERVER
    frame_group = "ETRS89"
    frame_label = FRAME_LABEL
    frame_short = "RGF93 v2b"
    snapshot = "rgp_stations.json"
    snapshot_source = COORD_URL

    def station_from_dict(self, item):
        return station_from_dict(item)

    def load_snapshot(self):
        return load_snapshot()

    def load_catalog(self):
        return load_catalog()

    def products(self, station, start_gps, end_gps, margin_s=60.0):
        return products(station, start_gps, end_gps, margin_s)

    def probe(self, station, start_gps, end_gps, wanted_interval=None, opener=None, cancelled=None):
        return probe(station, start_gps, end_gps, wanted_interval, opener, cancelled)

    def fetch(self, station, product, folder=None, progress=None, cancelled=None, opener=None):
        return fetch(station, product, folder, progress, cancelled, opener)


PROVIDER = RgpProvider()
