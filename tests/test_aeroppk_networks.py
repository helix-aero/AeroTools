"""Aero-PPK base station networks (RGP, EPN, IGS, NOAA CORS, Geoscience Australia): catalogue, file names, probe, frames, merge of RINEX pieces.

No network: the readers of the coordinate files are tested on small excerpts of the real files (tests/data/aeroppk_nets, cut from the
files the shipped snapshots were made from on 08/11/2026), the file names and the probe on a fake server, the networks on the shipped
snapshots. The live check of every network is tests_qt/run_qt_aeroppk_networks_live.py (not run here).
"""
import datetime
import os
import types

import numpy as np
import pytest

from aerotools.tools.aeroppk import geo, gnsstime, pipeline, rinex, rinexprep, unpack
from aerotools.tools.aeroppk.stations import catalog, epn, ga, igs, inspect as head, net, noaa, provider, rgp, sources, station, survey

import test_aeroppk_wizard_logic as wl

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data", "aeroppk_nets")


def lines_of(name):
    with open(os.path.join(DATA, name), "r", encoding="utf-8") as handle:
        return handle.read().splitlines()


def text_of(name):
    with open(os.path.join(DATA, name), "r", encoding="utf-8") as handle:
        return handle.read()


def snapshot(provider_id):
    return dict((s.code, s) for s in catalog.get(provider_id).load_catalog()[0])


def st(code, long_id, provider_id, lat, lon, h=100.0, tag="R", **more):
    s = station.Station(code, long_id, code, lat, lon, h, None, None, "", "", provider=provider_id, tag=tag, **more)
    s.distance_m = 1000.0
    return s


def gps(year, month, day, hour, minute=0):
    return gnsstime.utc_to_gps(datetime.datetime(year, month, day, hour, minute))


# ---------------------------------------------------------------------------------------------------------------------
# Stations and their motion
# ---------------------------------------------------------------------------------------------------------------------

def test_decimal_year_of_dates_and_datetimes():
    assert station.decimal_year(2026.5) == 2026.5
    assert station.decimal_year(datetime.date(2026, 1, 1)) == pytest.approx(2026.0)
    assert station.decimal_year(datetime.datetime(2026, 7, 2, 12)) == pytest.approx(2026.5, abs=0.002)
    assert station.decimal_year(datetime.date(2024, 12, 31)) == pytest.approx(2024.9973, abs=1e-4)      # a leap year has 366 days


def test_a_station_with_a_velocity_is_moved_to_the_date_and_one_without_stays():
    moving = station.Station("MOVE", "MOVE00XXX", "m", 10.0, 20.0, 5.0, None, None, "", "", xyz=(1000.0, 2000.0, 3000.0),
                             vel=(0.01, -0.02, 0.03), epoch=2020.0)
    assert moving.xyz_at(2026.0) == pytest.approx((1000.06, 1999.88, 3000.18))
    assert moving.xyz_at(None) == (1000.0, 2000.0, 3000.0)
    fixed = station.Station("FIXE", "FIXE00XXX", "f", 42.0, 9.0, 100.0, None, None, "", "")
    assert fixed.xyz_at(2030.0) == fixed.xyz and fixed.xyz == pytest.approx(tuple(float(v) for v in geo.llh_to_ecef(42.0, 9.0, 100.0)))


def test_the_snapshot_entry_keeps_position_velocity_epoch_and_grade_and_the_old_entries_still_load():
    s = station.Station("ABCD", "ABCD00XXX", "n", 1.0, 2.0, 3.0, None, None, "", "AUS", xyz=(1.0, 2.0, 3.0), vel=(0.1, 0.2, 0.3),
                        epoch=2020.0, tag="S")
    back = station.from_dict(s.to_dict(), "ga", "IGS20")
    assert back.xyz_ref == (1.0, 2.0, 3.0) and back.vel == (0.1, 0.2, 0.3) and back.epoch == 2020.0 and back.tag == "S"
    old = station.from_dict({"code": "OLD", "long_id": "OLD00FRA", "name": "o", "lat": 1, "lon": 2, "h": 3}, "rgp", "ETRS89")
    assert old.xyz_ref is None and old.vel is None and old.tag == "R" and old.grade == "precise"
    assert "xyz" not in old.to_dict() and "tag" not in old.to_dict()


def test_rgp_stations_are_still_rgp_stations():
    stations, _ = rgp.load_snapshot()
    by = dict((s.code, s) for s in stations)
    assert isinstance(by["CRTE"], rgp.Station) and by["CRTE"].provider == "rgp" and by["CRTE"].xyz_at(2030.0) == by["CRTE"].xyz
    assert not by["ABMF"].usable                                       # overseas: another frame


# ---------------------------------------------------------------------------------------------------------------------
# Readers of the coordinate files (excerpts of the real ones)
# ---------------------------------------------------------------------------------------------------------------------

def test_sinex_epochs():
    assert sources.snx_epoch("20:001:00000") == pytest.approx(2020.0)
    assert sources.snx_epoch("26:217:43185") == pytest.approx(2026.593, abs=0.001)
    assert sources.snx_epoch("94:047:69180") == pytest.approx(1994.128, abs=0.001)
    assert sources.snx_epoch("00:000:00000") is None and sources.snx_epoch("garbage") is None


def test_igs_solution_with_the_open_position_solution_of_each_station():
    estimates = sources.parse_sinex_estimates(lines_of("igc20_excerpt.ssc"))
    open_soln = sources.parse_open_solutions(lines_of("soln_igc20_excerpt.snx"))
    assert open_soln == {"ABMF": 4, "ALGO": 5}                          # the solution that has no end date
    algo = estimates[("ALGO", 5)]
    assert algo["epoch"] == pytest.approx(2020.0) and algo["pos"] == pytest.approx((918129.134, -4346071.2, 4561977.8), abs=2.0)
    assert algo["vel"] is not None and abs(algo["vel"][0]) < 0.05       # metres per year
    assert ("ALGO", 1) in estimates and estimates[("ALGO", 1)]["pos"] != algo["pos"]       # the earlier solutions exist and differ
    names = sources.parse_sinex_sites(lines_of("igc20_excerpt.ssc"))
    assert names["ALGO"]["name"].startswith("Algonquin") and names["ALGO"]["domes"] == "40104M002"


def test_epn_solutions_keep_the_last_one_if_current_and_take_the_short_file_without_velocity():
    solutions = sources.parse_epn_ssc(lines_of("epn_etrf2000_excerpt.ssc"))
    assert [i["soln"] for i in solutions["BRUX"]] == [1, 2, 3, 4]
    last = solutions["BRUX"][3]
    assert last["epoch"] == pytest.approx(2020.0) and last["vel"] == pytest.approx((-0.00017, -0.00043, -0.00025))
    assert last["pos"] == pytest.approx((4027881.84155, 306998.26152, 4919498.64628))
    now = sources.latest_epn_solutions(solutions, 2026.28, 0.5)
    assert now["BRUX"]["soln"] == 4
    assert sources.latest_epn_solutions(solutions, 2030.0, 0.5) == {}           # a station whose last solution ended long ago is not current
    short = sources.latest_epn_solutions(sources.parse_epn_ssc(lines_of("epn_etrf2000_short_excerpt.ssc")), 2026.28, 0.5)
    assert short["IGNP"]["vel"] is None and short["IGNP"]["pos"][0] == pytest.approx(4201788.81237)
    assert sources.parse_epn_names(lines_of("euref_names_excerpt.snx")) == {"ACOR": "A Coruna", "BRUX": "Brussels"}


def test_noaa_coordinate_page_gives_the_nad83_2011_position_of_the_antenna_reference_point():
    item = sources.parse_noaa_coord(text_of("noaa_ab02_coord.txt"))
    assert item["code"] == "ab02" and item["region"] == "ALASKA" and item["epoch"] == 2010.0
    assert item["xyz"] == (-3776807.243, -744084.917, 5068727.604)        # NAD 83 (2011), not the ITRF2020 block that comes first
    assert item["lat"] == pytest.approx(52.9706, abs=1e-4) and item["lon"] == pytest.approx(-168.8547, abs=1e-4)
    assert item["h"] == pytest.approx(191.993, abs=0.001)
    assert sources.parse_noaa_coord("not a coordinate page") is None
    listing = "rinex/2026/308/ab02/ab023080.26d.gz 1\nrinex/2026/308/ab02/ab023080.26o.gz 2\nrinex/2026/308/zzzz/zzzz3080.26d.gz 3\nrinex/2026/308/ab03/ab04.26d.gz"
    assert sources.parse_noaa_stations(listing, 2026, 308) == ["ab02", "zzzz"]


def test_the_mirror_listing_gives_nine_character_names_and_the_source_letter():
    found = sources.parse_listing(text_of("bkg_listing_excerpt.html"), r"_01D_30S_MO\.crx\.gz")
    assert found == {"ABMF00GLP": "R", "ALIC00AUS": "R"}
    assert sources.parse_listing("AAER00FRA_S_20263080000_01D_30S_MO.crx.gz", r"_01D_30S_MO\.crx\.gz") == {"AAER00FRA": "S"}


def test_ga_weekly_solution_estimates_and_state_codes():
    lines = lines_of("aus_week_excerpt.snx")
    estimates = sources.parse_sinex_estimates(lines)
    assert set(estimates) == {("3WKB", 1), ("4GLE", 1), ("ALIC", 1)} and estimates[("ALIC", 1)]["epoch"] == pytest.approx(2026.593, abs=0.001)
    assert estimates[("ALIC", 1)]["vel"] is None
    assert sources.parse_sinex_sites(lines)["4GLE"]["long_id"] == "4GLE00AUS"


def test_velocity_of_stations_known_by_two_positions_is_checked_against_the_neighbours():
    """A rigid plate moving 6 cm/yr north-east, a few stations with a mm of noise, one that jumped by 20 cm (antenna change)."""
    rng = np.random.RandomState(3)
    first, second = {}, {}
    for k in range(12):
        lat, lon = -25.0 + 0.4 * k, 135.0 + 0.5 * k
        x, y, z = (float(v) for v in geo.llh_to_ecef(lat, lon, 300.0))
        east, north = 0.035, 0.05
        v = sources.enu_to_geocentric((x, y, z), (east, north, 0.0))
        span = 2.0
        noise = rng.normal(0.0, 0.003, 3)
        first["S%02d" % k] = {"pos": (x, y, z), "epoch": 2024.0}
        second["S%02d" % k] = {"pos": tuple(c + vc * span + n for c, vc, n in zip((x, y, z), v, noise)), "epoch": 2026.0}
    jump = tuple(c + 0.2 for c in second["S05"]["pos"])
    second["S05"] = {"pos": jump, "epoch": 2026.0}
    second["NEW"] = {"pos": second["S03"]["pos"], "epoch": 2026.0}                       # a station that has no earlier position
    out = sources.velocities_from_two_solutions(first, second)
    for code in ("S00", "S05", "S11", "NEW"):
        east, north, up = sources.local_enu_velocity(second[code]["pos"], out[code])
        assert east == pytest.approx(0.035, abs=0.006) and north == pytest.approx(0.05, abs=0.006) and abs(up) < 0.01, code


# ---------------------------------------------------------------------------------------------------------------------
# The shipped snapshots
# ---------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("provider_id, minimum", [("rgp", 700), ("epn", 200), ("igs", 150), ("noaa", 1500), ("ga", 500)])
def test_shipped_snapshots_are_complete_enough_and_say_where_they_come_from(provider_id, minimum):
    p = catalog.get(provider_id)
    stations, date = p.load_snapshot()
    assert len(stations) > minimum and date.startswith("2026-")
    with open(p.snapshot_path(), "r", encoding="utf-8") as handle:
        import json
        data = json.load(handle)
    assert data["source"].startswith("http") and data["license"] == p.licence
    if provider_id != "rgp":
        assert data["frame"] == p.frame_label
        assert all(s.long_id and len(s.long_id) == 9 and s.tag in "RS" for s in stations)
        assert all(-90.0 <= s.lat <= 90.0 and -180.0 <= s.lon <= 180.0 and -500.0 < s.h < 6000.0 for s in stations)
        assert len(set(s.code for s in stations)) == len(stations)


def test_snapshot_positions_agree_with_the_geographic_ones_and_velocities_are_plate_motions():
    for provider_id in ("epn", "igs", "ga"):
        for s in list(catalog.get(provider_id).load_catalog()[0])[:60]:
            lat, lon, h = geo.ecef_to_llh(*s.xyz)
            assert (float(lat), float(lon)) == pytest.approx((s.lat, s.lon), abs=1e-6) and float(h) == pytest.approx(s.h, abs=1e-3)
            assert s.vel is None or float(np.linalg.norm(s.vel)) < 0.15, (provider_id, s.code)       # metres a year
    assert all(s.vel is None and s.epoch == 2010.0 for s in catalog.get("noaa").load_catalog()[0])    # NAD 83 (2011): fixed, epoch 2010.0
    ga_stations = catalog.get("ga").load_catalog()[0]
    assert all(s.vel is not None and 0.04 < float(np.hypot(*sources.local_enu_velocity(s.xyz, s.vel)[:2])) < 0.08 for s in ga_stations)
    algo = snapshot("igs")["ALGO"]
    assert algo.xyz_at(2026.8) != algo.xyz and float(np.linalg.norm(np.array(algo.xyz_at(2026.8)) - np.array(algo.xyz))) > 0.1


def test_the_same_station_in_two_networks_agrees_to_a_few_centimetres():
    """Two independent solutions of the same stations in the same frame group give the same position (checked at the date)."""
    day = datetime.date(2026, 11, 4)
    both = [(a, b) for a in snapshot("ga").values() for b in [snapshot("igs").get(a.code)] if b is not None]
    assert len(both) >= 10
    for a, b in both:
        assert float(np.linalg.norm(np.array(a.xyz_at(day)) - np.array(b.xyz_at(day)))) < 0.05, a.code
    for a, b in [(s, snapshot("epn").get(c)) for c, s in snapshot("rgp").items() if s.usable]:
        if b is not None:                                                  # RGF93 v2b against ETRF2000: the same family
            assert float(np.linalg.norm(np.array(a.xyz_at(day)) - np.array(b.xyz_at(day)))) < 0.05, a.code


# ---------------------------------------------------------------------------------------------------------------------
# File names, probe, download
# ---------------------------------------------------------------------------------------------------------------------

def test_igs_names_and_folders_for_a_flight_at_09_10_utc():
    s = st("ABMF", "ABMF00GLP", "igs", 16.0, -61.5, tag="R")
    found = dict((p.key + p.obs[0][1][10], p) for p in igs.PROVIDER.products(s, gps(2026, 11, 4, 9, 10), gps(2026, 11, 4, 9, 20)))
    fine, daily = found["hr_1sR"], found["day_30sR"]
    assert fine.obs[0][0] == "https://igs.bkg.bund.de/root_ftp/IGS/highrate/2026/308/j/ABMF00GLP_R_20263080900_15M_01S_MO.crx.gz"
    assert fine.interval == 1.0 and [n for _u, n in fine.obs] == ["ABMF00GLP_R_20263080900_15M_01S_MO.crx.gz",
                                                                  "ABMF00GLP_R_20263080915_15M_01S_MO.crx.gz"]
    assert daily.obs[0][0] == "https://igs.bkg.bund.de/root_ftp/IGS/obs/2026/308/ABMF00GLP_R_20263080000_01D_30S_MO.crx.gz"
    assert daily.nav_groups[-1] == [("https://igs.bkg.bund.de/root_ftp/IGS/BRDC/2026/308/BRDC00IGS_R_20263080000_01D_MN.rnx.gz",
                                     "BRDC00IGS_R_20263080000_01D_MN.rnx.gz")]
    assert daily.nav_groups[0][0][1] == "ABMF00GLP_R_20263080000_01D_MN.rnx.gz"
    assert [n for _u, n in daily.nav_groups[1]] == ["ABMF00GLP_R_20263080000_01D_%sN.rnx.gz" % k for k in "GREC"]


def test_the_other_source_letter_is_tried_too_and_the_snapshot_letter_comes_first():
    s = st("AUBG", "AUBG00DEU", "epn", 48.4, 10.9, tag="S")
    letters = [p.obs[0][1][10] for p in epn.PROVIDER.products(s, gps(2026, 11, 4, 9), gps(2026, 11, 4, 9, 5))]
    assert letters == ["S", "R", "S", "R"]                                        # per product: the snapshot letter, then the other one
    assert epn.PROVIDER.products(s, gps(2026, 11, 4, 9), gps(2026, 11, 4, 9, 5))[0].obs[0][0].startswith(
        "https://igs.bkg.bund.de/root_ftp/EUREF/highrate/2026/308/i/AUBG00DEU_S_")        # the 15 minute slot of 08:45 is in the folder of hour 8


def test_a_window_over_midnight_asks_for_two_days_and_the_files_of_every_slot():
    s = st("AUBG", "AUBG00DEU", "epn", 48.4, 10.9)
    day = [p for p in epn.PROVIDER.products(s, gps(2026, 11, 4, 23, 50), gps(2026, 11, 5, 0, 20)) if p.key == "day_30s"][0]
    assert [n[:23] for _u, n in day.obs] == ["AUBG00DEU_R_20263080000", "AUBG00DEU_R_20263090000"]
    fine = [p for p in epn.PROVIDER.products(s, gps(2026, 11, 4, 23, 50), gps(2026, 11, 5, 0, 20)) if p.key == "hr_1s"][0]
    names = [n for _u, n in fine.obs]
    assert names[0] == "AUBG00DEU_R_20263082345_15M_01S_MO.crx.gz" and "AUBG00DEU_R_20263090000_15M_01S_MO.crx.gz" in names
    assert fine.obs[0][0].endswith("/highrate/2026/308/x/" + names[0]) and fine.obs[-1][0].count("/2026/309/a/") == 1     # hour letters x (23) and a (0)


def test_geoscience_australia_has_hourly_and_highrate_folders_by_hour_number():
    s = st("ALIC", "ALIC00AUS", "ga", -23.7, 133.9)
    prods = dict((p.key, p) for p in ga.PROVIDER.products(s, gps(2026, 11, 4, 5, 20), gps(2026, 11, 4, 5, 40)) if p.obs[0][1][10] == "R")
    assert prods["hour_30s"].obs[0][0] == "https://ga-gnss-data-rinex-v1.s3.amazonaws.com/public/hourly/2026/308/05/ALIC00AUS_R_20263080500_01H_30S_MO.crx.gz"
    assert prods["hr_1s"].obs[0][0].endswith("/highrate/2026/308/05/ALIC00AUS_R_20263080515_15M_01S_MO.crx.gz".replace("0515", "0515"))
    assert prods["day_30s"].obs[0][0].endswith("/daily/2026/308/ALIC00AUS_R_20263080000_01D_30S_MO.crx.gz")
    assert [p.key for p in ga.PROVIDER.products(s, gps(2026, 11, 4, 5, 20), gps(2026, 11, 4, 5, 40))][:3] == ["hr_1s", "hr_1s", "hour_30s"]


def test_noaa_has_one_rinex_2_file_a_day_with_gps_and_glonass_navigation():
    s = st("ab02", "AB0200USA", "noaa", 52.97, -168.85)
    prods = noaa.PROVIDER.products(s, gps(2026, 11, 4, 9), gps(2026, 11, 4, 9, 30))
    assert len(prods) == 1 and prods[0].interval is None and prods[0].version == 2
    assert prods[0].obs == [("https://noaa-cors-pds.s3.amazonaws.com/rinex/2026/308/ab02/ab023080.26d.gz", "ab023080.26d.gz")]
    assert [n for _u, n in prods[0].nav] == ["brdc3080.26n.gz", "brdc3080.26g.gz"]
    assert prods[0].nav[0][0] == "https://noaa-cors-pds.s3.amazonaws.com/rinex/2026/308/brdc3080.26n.gz"


def fake_server(present):
    def exists(url, timeout=30, opener=None):
        return (url in present), 100
    return exists


def test_probe_finds_the_first_existing_product_and_its_first_complete_navigation_group(monkeypatch):
    s = st("AUBG", "AUBG00DEU", "epn", 48.4, 10.9, tag="R")                         # the letter of the snapshot is wrong: the stream one exists
    products = epn.PROVIDER.products(s, gps(2026, 11, 4, 9, 10), gps(2026, 11, 4, 9, 20))
    present = set(u for p in products if p.key == "hr_1s" and p.obs[0][1][10] == "S" for u, _n in p.obs)
    present.add(igs.broadcast(datetime.datetime(2026, 11, 4))[0])
    monkeypatch.setattr(net, "exists", fake_server(present))
    found = epn.PROVIDER.probe(s, gps(2026, 11, 4, 9, 10), gps(2026, 11, 4, 9, 20))
    assert [(p.key, p.obs[0][1][10]) for p in found] == [("hr_1s", "S")]
    assert found[0].nav == [igs.broadcast(datetime.datetime(2026, 11, 4))] and found[0].nav_groups is None
    assert all(url in found[0].sizes for url, _n in found[0].obs)
    assert epn.PROVIDER.probe(s, gps(2026, 11, 4, 9, 10), gps(2026, 11, 4, 9, 20), wanted_interval=0.5) == []        # nothing finer than 1 s


def test_probe_needs_navigation_and_every_observation_file(monkeypatch):
    s = st("ALIC", "ALIC00AUS", "ga", -23.7, 133.9)
    window = (gps(2026, 11, 4, 5, 50), gps(2026, 11, 4, 6, 10))                      # two hours, two hourly files
    products = ga.PROVIDER.products(s, *window)
    hourly = [p for p in products if p.key == "hour_30s" and p.obs[0][1][10] == "R"][0]
    assert len(hourly.obs) == 2
    monkeypatch.setattr(net, "exists", fake_server(set([hourly.obs[0][0]]) | set(u for u, _n in products[-1].nav_groups[0])))
    assert [p.key for p in ga.PROVIDER.probe(s, *window)] == []                       # the second hourly file is missing, no navigation yet
    monkeypatch.setattr(net, "exists", fake_server(set(u for u, _n in hourly.obs) | set([igs.broadcast(datetime.datetime(2026, 11, 4))[0]])))
    assert [p.key for p in ga.PROVIDER.probe(s, *window)] == ["hour_30s"]


def test_fetch_downloads_what_is_missing_into_a_folder_per_network_and_station(tmp_path, monkeypatch):
    s = st("ALIC", "ALIC00AUS", "ga", -23.7, 133.9)
    product = provider.Product("day_30s", "x", 30.0, 3, [("http://x/a.crx.gz", "a.crx.gz")], [("http://x/n.rnx.gz", "n.rnx.gz")])
    calls = []

    def download(url, target, **kwargs):
        calls.append(url)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(b"\x1f\x8bdata")                              # a real archive starts with the gzip magic
    monkeypatch.setattr(net, "download", download)
    obs, nav = ga.PROVIDER.fetch(s, product, str(tmp_path / "cache"))
    assert obs == [str(tmp_path / "cache" / "a.crx.gz")] and nav == [str(tmp_path / "cache" / "n.rnx.gz")] and len(calls) == 2
    ga.PROVIDER.fetch(s, product, str(tmp_path / "cache"))
    assert len(calls) == 2                                                          # the cached files are kept


def test_the_survey_asks_the_provider_of_the_station_and_takes_the_rate_from_the_header_when_the_network_does_not_say(monkeypatch):
    s = st("ab02", "AB0200USA", "noaa", 52.97, -168.85)
    seen = []
    monkeypatch.setattr(net, "exists", lambda url, timeout=30, opener=None: (seen.append(url) or True, 10))
    header = ("     2.11           OBSERVATION DATA    M (MIXED)           RINEX VERSION / TYPE\n"
              "                    " + "SEPT POLARX5".ljust(20) + "5.5".ljust(20) + "REC # / TYPE / VERS\n"
              "     5.0000                                                 INTERVAL\n"
              "                                                            END OF HEADER\n")
    monkeypatch.setattr(head, "inspect_product", lambda product, opener=None: head.parse_head(header))
    window = pipeline.Window(gps(2026, 11, 4, 9), gps(2026, 11, 4, 9, 30))
    offer = survey.survey_station(s, window)
    assert offer.available() and offer.interval == 5.0 and offer.best.key == "daily_r2" and seen[0].startswith("https://noaa-cors-pds")


def test_a_compact_rinex_newer_than_the_decoder_is_not_offered(monkeypatch):
    s = st("MAL2", "MAL200KEN", "igs", -3.0, 40.2)
    monkeypatch.setattr(net, "exists", lambda url, timeout=30, opener=None: (True, 10))
    text = "3.1                 COMPACT RINEX FORMAT                    CRINEX VERS   / TYPE\n     4.02           OBSERVATION DATA    M"
    monkeypatch.setattr(head, "inspect_product", lambda product, opener=None: head.parse_head(text))
    offer = survey.survey_station(s, pipeline.Window(gps(2026, 11, 4, 9), gps(2026, 11, 4, 9, 30)))
    assert not offer.available() and offer.problem == "crinex"
    assert head.problem({"crinex": 3.0}) == "" and head.problem({"crinex": None}) == ""
    assert unpack.crinex_version("3.1                 COMPACT RINEX FORMAT") == 3.1 and unpack.crinex_version("garbage") is None


def test_unpack_refuses_a_compact_rinex_the_decoder_cannot_read_with_a_sentence(tmp_path):
    path = tmp_path / "x.crx"
    path.write_bytes(b"3.1                 COMPACT RINEX FORMAT                    CRINEX VERS   / TYPE\n")
    fake_exe = tmp_path / "crx2rnx.exe"
    fake_exe.write_bytes(b"")
    with pytest.raises(unpack.UnpackError) as error:
        unpack.unpack(str(path), str(tmp_path / "out"), crx2rnx=str(fake_exe))
    assert "3.1" in str(error.value) and "newer" in str(error.value)


# ---------------------------------------------------------------------------------------------------------------------
# The catalogue: merge, filter, frames
# ---------------------------------------------------------------------------------------------------------------------

def test_a_station_offered_by_two_networks_is_listed_once_from_the_first_one():
    a = st("BRUX", "BRUX00BEL", "epn", 50.798, 4.358)
    b = st("BRUX", "BRUX00BEL", "igs", 50.7981, 4.3581)               # the same station, 10 m of rounding apart
    c = st("TEST", "TEST00XXX", "igs", 10.0, 10.0)
    merged = catalog.merge([[a], [b, c]])
    assert [(s.code, s.provider) for s in merged] == [("BRUX", "epn"), ("TEST", "igs")]


def test_two_different_stations_with_the_same_code_both_stay_and_keep_unique_codes():
    a = st("ABCD", "ABCD00FRA", "rgp", 45.0, 5.0)
    b = st("ABCD", "ABCD00USA", "noaa", 40.0, -100.0)
    merged = catalog.merge([[a], [b]])
    assert [s.code for s in merged] == ["ABCD", "ABCD~noaa"] and len(set(s.code for s in merged)) == 2


def test_the_merged_catalogue_has_unique_codes_and_a_station_in_two_networks_once():
    merged = catalog.load()
    assert len(set(s.code for s in merged)) == len(merged) and len(merged) > 3000
    assert [s.provider for s in merged if s.code == "ALIC"] == ["ga"]            # GA before IGS
    assert len(catalog.load(["epn"])) == len(catalog.get("epn").load_catalog()[0])
    assert all(s.provider == "noaa" for s in catalog.load(["noaa"]))


def test_networks_that_cover_the_site_are_found_from_their_stations():
    day = datetime.date(2026, 11, 4)
    assert catalog.covering(-33.9, 151.2, 100, day)[0] == "ga" and "noaa" not in catalog.covering(-33.9, 151.2, 100, day)
    assert "noaa" in catalog.covering(39.0, -105.0, 100, day) and "ga" not in catalog.covering(39.0, -105.0, 100, day)
    assert "epn" in catalog.covering(48.1, 11.6, 100, day)
    corte = snapshot("rgp")["CRTE"]
    around = catalog.covering(corte.lat, corte.lon, 50, datetime.date(2026, 5, 29))
    assert around[0] == "rgp" and "noaa" not in around and "ga" not in around
    assert catalog.covering(0.0, -140.0, 50, day) == []                          # the middle of an ocean: nothing


def test_the_network_filter_defaults_to_the_networks_around_and_obeys_the_user():
    from aerotools.tools.aeroppk import stationfilter
    counts = {"rgp": 12, "epn": 3, "noaa": 0, "ga": 0, "igs": 1}
    assert stationfilter.enabled_networks(None, counts) == ["rgp", "epn", "igs"]
    assert stationfilter.enabled_networks(["noaa", "epn", "nowhere"], counts) == ["epn", "noaa"]
    assert stationfilter.enabled_networks([], counts) == []
    assert stationfilter.enabled_networks(None, {"rgp": 0, "epn": 0}) == ["rgp", "epn"]       # none covers: nothing is hidden


def test_frames_are_mixed_only_between_different_groups_and_never_with_an_own_base():
    rg = st("A", "A00FRA", "rgp", 45.0, 5.0)
    ep = st("B", "B00DEU", "epn", 48.0, 10.0)
    ig = st("C", "C00DEU", "igs", 49.0, 8.0)
    au = st("D", "D00AUS", "ga", -34.0, 151.0)
    own = st("OWN", "OWN", "own", 45.0, 5.0)
    assert not catalog.frames_mixed([rg, ep])                                    # RGF93 v2b and ETRF2000 are both ETRS89
    assert catalog.frames_mixed([ep, ig]) and catalog.frames_mixed([rg, ig])
    assert not catalog.frames_mixed([ig, au])                                    # IGS and GA are both IGS20
    assert not catalog.frames_mixed([own, ep]) and not catalog.frames_mixed([])
    assert catalog.frames_text([rg, ep, ig, au, own]) == "RGF93 v2b (ETRS89) + ETRF2000 + IGS20 (ITRF2020)"
    assert catalog.frames_text([ep, ig], short=True) == "ETRF2000 + IGS20"
    assert [i["short"] for i in catalog.networks_info([rg, ep, rg, own])] == ["RGP", "EPN"]


def test_the_report_source_names_one_network_in_full_and_several_by_short_name():
    one = catalog.source_of([st("A", "A00FRA", "rgp", 45.0, 5.0)])
    assert one["name"] == rgp.NAME and one["licence"] == rgp.LICENSE and one["url"] == rgp.SERVER
    many = catalog.source_of([st("B", "B00DEU", "epn", 48.0, 10.0), st("C", "C00USA", "noaa", 39.0, -105.0)])
    assert many["name"] == "EPN, CORS" and "EPN: CC BY 4.0" in many["licence"] and [n["id"] for n in many["networks"]] == ["epn", "noaa"]
    assert catalog.source_of([])["name"] == rgp.NAME


def test_every_network_has_what_the_window_and_the_report_need():
    for p in catalog.PROVIDERS:
        assert p.id and p.name and p.short and p.licence and p.licence_short and p.site.startswith("https://") and p.frame_label, p.id
        assert p.color.startswith("#") and len(p.color) == 7 and p.snapshot.endswith("_stations.json") and p.frame_group
        assert os.path.isfile(p.snapshot_path()), p.id
    assert len(set(p.id for p in catalog.PROVIDERS)) == len(set(p.short for p in catalog.PROVIDERS)) == len(catalog.PROVIDERS)
    assert len(set(p.color for p in catalog.PROVIDERS)) == len(catalog.PROVIDERS)


# ---------------------------------------------------------------------------------------------------------------------
# Results of bases in different frames
# ---------------------------------------------------------------------------------------------------------------------

def result_with_networks(providers):
    result = wl.fake_result()
    for run, pid in zip(result.runs, providers):
        run.base.spec.station = st(run.base.name, run.base.name + "00XXX", pid, 42.0, 9.0)
    return result


# ---------------------------------------------------------------------------------------------------------------------
# The basket
# ---------------------------------------------------------------------------------------------------------------------


# ---------------------------------------------------------------------------------------------------------------------
# Merging RINEX 3 pieces whose receiver was set up again
# ---------------------------------------------------------------------------------------------------------------------

def test_obs_types_of_a_header_are_read_and_written_back_the_same():
    header, _ = rinexprep._split_v3(os.path.join(DATA, "aubg_0845_excerpt.rnx"))
    types_ = rinexprep.types_v3(header)
    assert list(types_) == ["C", "E", "G", "R"] and types_["G"][:4] == ["C1C", "L1C", "D1C", "S1C"] and len(types_["G"]) == 24
    written = rinexprep._types_lines(types_)
    original = [l for l in header if l[60:].strip() == rinexprep.OBS_TYPES_LABEL]
    assert [l.rstrip() for l in written] == [l.rstrip() for l in original]


def sat_values(line, codes):
    """{code: text of its 16 character field} of one satellite line of a RINEX 3 body."""
    return dict((code, line[3 + 16 * i:3 + 16 * (i + 1)].strip()) for i, code in enumerate(codes))


def test_pieces_with_the_codes_in_another_order_are_rewritten_on_the_first_order(tmp_path):
    first, second = os.path.join(DATA, "aubg_0845_excerpt.rnx"), os.path.join(DATA, "aubg_0900_excerpt.rnx")
    h1, b1 = rinexprep._split_v3(first)
    h2, b2 = rinexprep._split_v3(second)
    target, source = rinexprep.types_v3(h1)["G"], rinexprep.types_v3(h2)["G"]
    assert target != source and sorted(target) == sorted(source)                                # the same codes, another order
    out = rinexprep.merge_obs([first, second], str(tmp_path / "merged.rnx"))
    header, body = rinexprep._split_v3(out)
    assert rinexprep.types_v3(header) == rinexprep.types_v3(h1)                                 # the header of the first piece is kept
    assert [l for l in body if l.startswith(b">")] == [l for l in b1 if l.startswith(b">")] + [l for l in b2 if l.startswith(b">")]
    first_part = body[:len(b1)]
    assert first_part == b1                                                                     # the first piece is not touched
    old = [l for l in b2 if l.startswith(b"G")]
    new = [l for l in body[len(b1):] if l.startswith(b"G")]
    assert len(old) == len(new) >= 2
    checked = 0
    for before, after in zip(old, new):
        assert before[:3] == after[:3]
        was, now = sat_values(before, source), sat_values(after, target)
        for code in target:
            assert was[code] == now[code], code                                                 # each value stays on its satellite and code
            checked += bool(now[code])
    assert checked > 20
    assert rinex.read_obs_info(out).systems == ["G", "R", "E", "C"]


def test_pieces_that_add_a_code_get_the_union_in_the_header_and_blanks_before(tmp_path):
    first = os.path.join(DATA, "aubg_0845_excerpt.rnx")
    h2, b2 = rinexprep._split_v3(os.path.join(DATA, "aubg_0900_excerpt.rnx"))
    # the second piece also records L9X after the last GPS code (an invented code, only to see the union): one more column in the header
    # and a value at the end of each GPS line
    types_ = rinexprep.types_v3(h2)
    types_["G"] = types_["G"] + ["L9X"]
    plus = str(tmp_path / "plus.rnx")
    with open(plus, "wb") as handle:
        handle.writelines(rinexprep._with_types(h2, types_))
        for line in b2:
            handle.write(line.rstrip().ljust(3 + 16 * 24) + b"       123.456   " + bytes((10,)) if line.startswith(b"G") else line)
    out = rinexprep.merge_obs([first, plus], str(tmp_path / "merged.rnx"))
    header, body = rinexprep._split_v3(out)
    union = rinexprep.types_v3(header)["G"]
    assert len(union) == 25 and union[-1] == "L9X" and union[:24] == rinexprep.types_v3(rinexprep._split_v3(first)[0])["G"]
    assert [l for l in header if l[60:].strip() == rinexprep.OBS_TYPES_LABEL and l.startswith(b"G ")][0][:6] == b"G   25"
    gps_lines = [l for l in body if l.startswith(b"G")]
    assert gps_lines[0][3 + 16 * 24:3 + 16 * 25].strip() == b""                                 # the first piece has no L9X: blank
    assert gps_lines[-1][3 + 16 * 24:3 + 16 * 25].strip() == b"123.456"                          # the second one keeps it


def test_pieces_with_the_same_codes_are_just_joined_and_rinex_2_pieces_must_agree(tmp_path):
    first = os.path.join(DATA, "aubg_0845_excerpt.rnx")
    out = rinexprep.merge_obs([first, first], str(tmp_path / "twice.rnx"))
    with open(first, "rb") as one, open(out, "rb") as two:
        a, b = one.read(), two.read()
    header_len = len(b"".join(rinexprep._split_v3(first)[0]))
    assert b == a + a[header_len:]
    with pytest.raises(rinexprep.PrepError):
        rinexprep.merge_obs([], str(tmp_path / "none.rnx"))
