"""Aero-PPK core: time, geodesy, RINEX headers, unpacking, drone reader, RTKLIB files, RGP names, backup, camera positions.

Everything here runs without Metashape, without network and without RTKLIB (the engine itself is measured in
docs/AEROPPK.md with real flights). Data used: a 4.7 KB GLONASS navigation file of a public RGP station (`.Z`) and three
synthetic lines in the format of a Mavic 3 Enterprise `.MRK`.
"""
import datetime
import json
import os
import types

import numpy as np
import pytest

from aerotools.tools.aeroppk import (backup, frames, geo, gnsstime, pipeline, rinex, rtklib, stationfilter, unpack)
from aerotools.tools.aeroppk.drones import dji_m3e, reader_for
from aerotools.tools.aeroppk.stations import inspect as head, rgp

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")


# -- time ----------------------------------------------------------------------------------------------------------

def test_gps_week_tow_round_trip_and_known_date():
    t = gnsstime.from_week_tow(2292, 379192.119164)               # a photo time on 14/12/2023
    assert (t.year, t.month, t.day, t.hour, t.minute, t.second) == (2023, 12, 14, 9, 19, 52)
    week, tow = gnsstime.to_week_tow(t)
    assert week == 2292 and tow == pytest.approx(379192.119164, abs=1e-6)


def test_leap_seconds_gps_minus_utc_is_18_since_2017_and_16_in_2013():
    assert gnsstime.leap_seconds(datetime.date(2026, 11, 6)) == 18
    assert gnsstime.leap_seconds(datetime.date(2013, 5, 1)) == 16
    t = datetime.datetime(2023, 12, 14, 9, 19, 52)
    assert gnsstime.gps_to_utc(t) == datetime.datetime(2023, 12, 14, 9, 19, 34)
    assert gnsstime.utc_to_gps(gnsstime.gps_to_utc(t)) == t


def test_parse_rinex_time_both_layouts():
    assert gnsstime.parse_rinex_time("  2023    12    14     9    17   30.0000000") == datetime.datetime(2023, 12, 14, 9, 17, 30)
    assert gnsstime.parse_rinex_time(" 23 12 14  9 17 30.5000000") == datetime.datetime(2023, 12, 14, 9, 17, 30, 500000)


# -- geodesy -------------------------------------------------------------------------------------------------------

def test_the_rgp_csv_angle_notation_is_not_decimal_degrees():
    assert geo.packed_dms_to_deg("42.175592367") == pytest.approx(42 + 17 / 60 + 55.92367 / 3600)
    assert geo.packed_dms_to_deg("-61.291664012") == pytest.approx(-(61 + 29 / 60 + 16.64012 / 3600))


def test_llh_ecef_round_trip_and_ned_rotation():
    x, y, z = geo.llh_to_ecef(42.2988677, 9.1528654, 499.466)
    assert (float(x), float(y), float(z)) == pytest.approx((4664928.549, 751615.790, 4270552.169), abs=0.002)   # Corte, RGP value
    lat, lon, h = geo.ecef_to_llh(x, y, z)
    assert (float(lat), float(lon), float(h)) == pytest.approx((42.2988677, 9.1528654, 499.466), abs=1e-8)
    d = geo.ned_to_ecef_delta(45.3, 5.15, 1.0, 2.0, 3.0)
    assert geo.ecef_delta_to_ned(45.3, 5.15, *d) == pytest.approx((1.0, 2.0, 3.0))


def test_distance_and_destination_agree():
    lat, lon = geo.destination(45.3, 5.15, 90.0, 10000.0)
    assert geo.distance_m(45.3, 5.15, lat, lon) == pytest.approx(10000.0, abs=1.0)


# -- RINEX headers -------------------------------------------------------------------------------------------------

V3 = ("     3.05           OBSERVATION DATA    M: Mixed            RINEX VERSION / TYPE\n"
      "G    8 C1C L1C D1C S1C C2W L2W D2W S2W                      SYS / # / OBS TYPES\n"
      "E    8 C1C L1C D1C S1C C7Q L7Q D7Q S7Q                      SYS / # / OBS TYPES\n"
      "  2023    12    14     9    17   30.0000000     GPS         TIME OF FIRST OBS\n"
      "  2023    12    14     9    25   43.4000000     GPS         TIME OF LAST OBS\n"
      "                                                            END OF HEADER\n")


def test_rinex3_header(tmp_path):
    path = tmp_path / "rover.obs"
    path.write_text(V3)
    info = rinex.read_obs_info(str(path))
    assert info.version == 3.05 and info.systems == ["G", "E"] and info.system_names() == ["GPS", "Galileo"]
    assert info.first == datetime.datetime(2023, 12, 14, 9, 17, 30) and info.duration() == pytest.approx(493.4)
    assert info.approx_xyz is None                     # DJI writes 0 0 0: no position


def test_rinex2_constellations_come_from_continuation_lines_too(tmp_path):
    header = ("     2.11           OBSERVATION DATA    M (MIXED)           RINEX VERSION / TYPE\n"
              "  4664928.5490   751615.7900  4270552.1690                  APPROX POSITION XYZ\n"
              "     1.0000                                                 INTERVAL\n"
              "                                                            END OF HEADER\n")
    body = (" 23 12 14  5  0  0.0000000  0 14G02G03G04G06G09G17G19G28G31R04R05R06\n"
            "                                R14R15E07E08\n"
            " 130019268.490 6 101313708.46745  24741846.180\n")
    path = tmp_path / "base.23o"
    path.write_text(header + body)
    info = rinex.read_obs_info(str(path))
    assert info.systems == ["G", "R", "E"] and info.interval == 1.0
    assert info.approx_xyz == (4664928.549, 751615.79, 4270552.169)


def test_not_rinex_is_refused_with_a_readable_error(tmp_path):
    path = tmp_path / "x.obs"
    path.write_text("hello\n")
    with pytest.raises(rinex.RinexError):
        rinex.read_obs_header(str(path))


# -- unpacking -----------------------------------------------------------------------------------------------------

def test_unix_compress_decoder_on_a_real_rgp_navigation_file():
    with open(os.path.join(DATA, "crte348j.23g.Z"), "rb") as handle:
        text = unpack.lzw_decompress(handle.read())
    lines = text.decode("ascii").splitlines()
    assert lines[0].split()[0].startswith("2.") and "RINEX VERSION / TYPE" in lines[0]
    assert any(line.endswith("END OF HEADER") for line in lines[:30])
    assert len(text) == 13103                         # same size as when unpacked in the benchmark run


def test_lzw_refuses_garbage():
    with pytest.raises(unpack.UnpackError):
        unpack.lzw_decompress(b"not compressed")


def test_unpack_a_z_file_into_a_new_folder_and_leave_the_source(tmp_path):
    out = unpack.unpack(os.path.join(DATA, "crte348j.23g.Z"), str(tmp_path / "out"))
    assert out.endswith("crte348j.23g") and rinex.read_nav_systems(out) == ["R"]
    assert os.path.isfile(os.path.join(DATA, "crte348j.23g.Z"))


def test_hatanaka_output_names():
    assert unpack.hatanaka_output_name("crte350i.23d") == "crte350i.23o"
    assert unpack.hatanaka_output_name("CRTE00FRA_R_20263000000_15M_01S_MO.crx") == "CRTE00FRA_R_20263000000_15M_01S_MO.rnx"


# -- DJI Mavic 3 Enterprise ----------------------------------------------------------------------------------------

MRK = ("1\t470091.021653\t[2420]\t     7,N\t   -33,E\t    88,V\t45.00000927,Lat\t5.00003899,Lon\t95.679,Ellh\t0.011931, 0.009100, 0.022696\t50,Q\n"
       "2\t470091.727536\t[2420]\t     8,N\t   -20,E\t    93,V\t45.00001193,Lat\t5.00003190,Lon\t95.669,Ellh\t0.011955, 0.009114, 0.022720\t50,Q\n"
       "3\t379194.667785\t[2292]\t     0,N\t    25,E\t    92,V\t45.10058231,Lat\t5.10706577,Lon\t963.080,Ellh\t1.552346, 1.074891, 2.514946\t16,Q\n")


def flight_folder(base, with_obs=True):
    folder = base / "DJI_203001011200_001_TESTSITE"
    folder.mkdir()
    (folder / "DJI_203001011200_001_TESTSITE_Timestamp.MRK").write_text(MRK)
    for i in (1, 2, 3):
        (folder / ("DJI_20300101103%d00_%04d_V.JPG" % (i, i))).write_bytes(b"x")
    (folder / "DJI_203001011200_001_TESTSITE_PPKNAV.nav").write_text("x")
    (folder / "DJI_203001011200_001_TESTSITE_PPKRAW.bin").write_text("x")
    if with_obs:
        (folder / "DJI_203001011200_001_TESTSITE_PPKOBS.obs").write_text(V3)
    return str(folder)


def test_m3e_reader_finds_files_and_reads_events(tmp_path):
    folder = flight_folder(tmp_path)
    reader = reader_for(folder)
    assert reader is not None and reader.id == "dji_m3e"
    files = reader.scan(folder)
    assert len(files.photos) == 3 and files.obs.endswith("_PPKOBS.obs") and files.events_file.endswith("_Timestamp.MRK")
    events, warnings = reader.read_events(files)
    assert warnings == [] and [e.index for e in events] == [1, 2, 3]
    assert events[0].time == pytest.approx(2420 * 604800 + 470091.021653)
    assert events[0].offset_ned == pytest.approx((0.007, -0.033, 0.088))
    assert events[0].onboard_q == 50 and events[2].onboard_q == 16 and events[0].photo.endswith("0001_V.JPG")


def test_m3e_reader_accepts_photo_names_with_a_waypoint_tag(tmp_path):
    folder = flight_folder(tmp_path)
    for name in os.listdir(folder):
        if name.endswith("_V.JPG"):
            os.rename(os.path.join(folder, name), os.path.join(folder, name[:-4] + "_point%d.JPG" % int(name[-10:-6])))
    files = reader_for(folder).scan(folder)
    assert len(files.photos) == 3 and files.photos[0].endswith("0001_V_point1.JPG")
    events, _ = dji_m3e.DjiM3E().read_events(files)
    assert events[0].photo.endswith("0001_V_point1.JPG") and events[0].photo_name.endswith("_point1.JPG")


def test_m3e_checklist_blocks_only_when_a_needed_file_is_missing(tmp_path):
    reader = dji_m3e.DjiM3E()
    good = tmp_path / "good"
    good.mkdir()
    bad = tmp_path / "bad"
    bad.mkdir()
    ok = reader.needs(reader.scan(flight_folder(good)))
    assert not any(n.level == "blocking" and not n.present for n in ok)
    needs = reader.needs(reader.scan(flight_folder(bad, with_obs=False)))
    assert [n.key for n in needs if n.level == "blocking" and not n.present] == ["rover_obs"]
    assert [n.key for n in needs if n.level == "optional" and n.present] == ["raw"]


def test_a_folder_of_something_else_is_not_a_flight(tmp_path):
    (tmp_path / "notes.txt").write_text("x")
    assert reader_for(str(tmp_path)) is None


def test_mrk_lever_arm_sign_camera_is_below_the_antenna():
    # measured on 280 RTK fixed photos: camera = antenna + offset (north, east, DOWN): V > 0 means a lower height
    x, y, z = geo.llh_to_ecef(45.3, 5.15, 500.0)
    d = geo.ned_to_ecef_delta(45.3, 5.15, 0.0, 0.0, 0.088)
    assert float(geo.ecef_to_llh(x + d[0], y + d[1], z + d[2])[2]) == pytest.approx(499.912, abs=1e-3)


# -- RTKLIB files --------------------------------------------------------------------------------------------------

def test_rtklib_config_spells_out_the_important_keys():
    text = rtklib.config_text(rtklib.Options(base_xyz=(4664928.549, 751615.79, 4270552.169), systems="GRE", dynamics=False))
    lines = {l.split("=")[0].strip(): l.split("=", 1)[1] for l in text.splitlines()}
    assert lines["pos1-navsys"] == "13" and lines["pos1-dynamics"] == "off" and lines["ant2-postype"] == "xyz"
    assert lines["ant2-pos1"] == "4664928.5490" and lines["out-timeform"] == "tow" and lines["out-timesys"] == "gpst"
    with pytest.raises(rtklib.RtklibError):
        rtklib.config_text(rtklib.Options())


def test_pos_file_parse_and_interpolation_keeps_the_worst_quality(tmp_path):
    rows = ["% program   : RTKLIB ver.EX 2.5.1",
            "2292 379192.000   45.000000000    5.000000000    965.0000   1   9   0.0100   0.0100   0.0200   0.0   0.0   0.0   0.00   30.0",
            "2292 379192.200   45.000000000    5.000002000    965.0100   2   8   0.0200   0.0300   0.0400   0.0   0.0   0.0   0.00    2.0",
            "2292 379194.000   45.000000000    5.000100000    965.0000   1   9   0.0100   0.0100   0.0200   0.0   0.0   0.0   0.00   30.0"]
    path = tmp_path / "x.pos"
    path.write_text("\n".join(rows) + "\n")
    pos, header = rtklib.parse_pos(str(path))
    assert len(pos) == 3 and header[0].startswith("% program") and pos.quality_counts() == {1: 2, 2: 1}
    times = np.array([2292 * 604800 + 379192.1, 2292 * 604800 + 379193.0])
    out = rtklib.interpolate(pos, times, max_gap=0.5)
    assert out["q"][0] == 2 and out["lon"][0] == pytest.approx(5.000001, abs=1e-9)     # between a fix and a float: float
    assert out["q"][1] == 0 and np.isnan(out["lat"][1])                                   # inside a hole: no invented position


def test_programs_are_found_in_the_shipped_folder():
    found = rtklib.find_programs()
    assert found["rnx2rtkp"] and os.path.isfile(found["rnx2rtkp"]) and found["crx2rnx"]
    assert os.path.isfile(os.path.join(rtklib.VENDOR, "LICENSE.txt"))


# -- RGP -----------------------------------------------------------------------------------------------------------

def test_shipped_station_list_is_correct_and_complete_enough():
    stations, date = rgp.load_snapshot()
    by = {s.code: s for s in stations}
    assert len(stations) > 700 and date
    crte = by["CRTE"]
    assert crte.long_id == "CRTE00FRA" and crte.xyz == pytest.approx((4664928.549, 751615.790, 4270552.169), abs=0.002)
    assert crte.frame == "ETRS89" and not by["ABMF"].usable                  # overseas: another frame, not offered


def test_nearby_filters_by_distance_service_dates_and_sorts():
    stations, _ = rgp.load_snapshot()
    by = {s.code: s for s in stations}
    near = rgp.nearby(stations, by['AJAC'].lat, by['AJAC'].lon, 25, datetime.date(2026, 5, 29))
    assert near[0].distance_m < near[-1].distance_m and {s.code for s in near} >= {"AJAC", "CAMP"}
    old = rgp.nearby(stations, by['CRTE'].lat, by['CRTE'].lon, 5, datetime.date(2020, 1, 1))
    assert "CRTE" not in {s.code for s in old}                                  # Corte started in 2022


def test_file_names_of_the_two_rinex_generations():
    stations, _ = rgp.load_snapshot()
    crte = [s for s in stations if s.code == "CRTE"][0]
    a = gnsstime.from_week_tow(2292, 379183.8)
    b = gnsstime.from_week_tow(2292, 379543.4)
    prods = {p.key: p for p in rgp.products(crte, a, b)}
    assert [n for _u, n in prods["v2_1hz"].obs] == ["crte348j.23d.Z"]
    assert prods["v2_1hz"].nav[0][0] == "https://rgpdata.ign.fr/pub/data/2023/348/data_1/crte348j.23n.Z"
    # the window is 09:18:25 to 09:26:07 UTC (GPS time minus 18 s, 60 s of margin): one 15 minute slot, the one of 09:15
    assert [n for _u, n in prods["v3_1hz"].obs] == ["CRTE00FRA_R_20233480915_15M_01S_MO.crx.gz"]
    assert prods["v3_30s_day"].obs[0][1] == "CRTE00FRA_R_20233480000_01D_30S_MO.crx.gz"
    assert prods["v2_30s_day"].obs[0][1] == "crte3480.23d.Z"


def test_probe_uses_the_server_answers_and_orders_best_first(monkeypatch):
    stations, _ = rgp.load_snapshot()
    camp = [s for s in stations if s.code == "CAMP"][0]
    a = gnsstime.from_week_tow(2420, 470033.0)

    def fake_exists(url, timeout=30, opener=None):
        return ("data/2026/149/data_1/camp149k.26" in url or "data_30/camp1490.26" in url), 1234
    monkeypatch.setattr(rgp.net, "exists", fake_exists)
    found = rgp.probe(camp, a, a + datetime.timedelta(minutes=8))
    assert [p.key for p in found] == ["v2_1hz", "v2_30s_day"]
    assert rgp.probe(camp, a, a + datetime.timedelta(minutes=8), wanted_interval=1.0)[0].key == "v2_1hz"


def test_inspect_reads_systems_from_a_compact_rinex2_first_epoch():
    text = ("     2.11           OBSERVATION DATA    M (MIXED)           RINEX VERSION / TYPE\n"
            "                    " + "LEICA GR50".ljust(20) + "4.51".ljust(20) + "REC # / TYPE / VERS\n"
            "     1.0000                                                 INTERVAL\n"
            "                                                            END OF HEADER\n"
            "&23 12 16  8  0  0.0000000  0 29G04G05G06R01R02E04E05S23\n")
    info = head.parse_head(text)
    assert info["systems"] == ["G", "R", "E", "S"] and info["interval"] == 1.0 and info["receiver"] == "LEICA GR50"


# -- choosing stations ---------------------------------------------------------------------------------------------

class FakeOffer(object):
    def __init__(self, code, km, interval, systems, ok=True):
        self.station = types.SimpleNamespace(code=code, distance_m=km * 1000.0)
        self.interval, self.systems, self._ok = interval, systems, ok

    def available(self):
        return self._ok


def test_filters_and_automatic_choice():
    offers = [FakeOffer("A", 3, 30.0, ["G", "R"]), FakeOffer("B", 9, 1.0, ["G"]), FakeOffer("C", 20, 1.0, ["G", "R", "E"]),
              FakeOffer("D", 5, 1.0, ["G"], ok=False), FakeOffer("E", 40, 1.0, ["G"])]
    assert stationfilter.matches(offers[1], True, "") and not stationfilter.matches(offers[0], True, "")
    assert not stationfilter.matches(offers[1], False, "R") and stationfilter.matches(offers[2], False, "RE")
    assert not stationfilter.matches(offers[3], False, "")                            # no data never matches
    assert stationfilter.auto_pick(offers, 3) == ["B", "C", "E"]                      # 1 s first, nearest first, never D
    assert stationfilter.auto_pick(offers, 1) == ["B"]


# -- frames (PROJ) -------------------------------------------------------------------------------------------------

def test_etrs89_to_itrf_shift_measured_with_the_embedded_proj():
    corte = (4664928.549, 751615.790, 4270552.169)
    delta, dist = frames.shift_m(corte, 2023.96)
    assert dist == pytest.approx(0.903, abs=0.005) and delta == pytest.approx((-0.475, 0.656, 0.401), abs=0.005)
    assert frames.shift_m(corte, 2026.85)[1] > dist                                    # the plate keeps moving
    back = frames.convert_xyz(frames.convert_xyz(corte, 2023.96, "ETRS89", "ITRF2014"), 2023.96, "ITRF2014", "ETRS89")
    assert back == pytest.approx(corte, abs=1e-3)
    with pytest.raises(frames.FrameError):
        frames.convert_xyz(corte, 2023.96, "ETRS89", "MARS2000")


# -- backup --------------------------------------------------------------------------------------------------------

def test_backup_files_are_named_per_project_and_chunk_and_found_oldest_first(tmp_path):
    p1 = backup.new_path(str(tmp_path), "C:/x/Site A.psx", "Chunk 1", datetime.datetime(2026, 1, 2, 3, 4, 5))
    p2 = backup.new_path(str(tmp_path), "C:/x/Site A.psx", "Chunk 1", datetime.datetime(2026, 1, 3, 3, 4, 5))
    entries = [{"path": "a.jpg", "label": "a", "location": [1.0, 2.0, 3.0], "accuracy": [10, 10, 10], "enabled": True}]
    backup.save(p2, entries, "WGS 84")
    backup.save(p1, entries, "WGS 84", "first")
    assert backup.find(str(tmp_path), "C:/x/Site A.psx", "Chunk 1") == [p1, p2]
    assert backup.find(str(tmp_path), "C:/x/Site A.psx", "Other") == []
    assert backup.load(p1)["entries"] == entries
    with open(str(tmp_path / "bad.json"), "w") as handle:
        json.dump({"nothing": 1}, handle)
    with pytest.raises(ValueError):
        backup.load(str(tmp_path / "bad.json"))


# -- camera positions in Metashape (fake API) ----------------------------------------------------------------------

class FakeRef(object):
    def __init__(self):
        self.location, self.location_accuracy, self.location_enabled = None, None, False


class FakeCamera(object):
    def __init__(self, path, loc=None):
        self.photo = types.SimpleNamespace(path=path)
        self.label = os.path.basename(path)
        self.reference = FakeRef()
        self.reference.location = loc
        self.reference.location_accuracy = (10.0, 10.0, 10.0)
        self.reference.location_enabled = True


def fake_metashape(monkeypatch, ms):
    from aerotools import msapi
    ms.CoordinateSystem = type("CS", (object,), {"__init__": lambda self, t: setattr(self, "t", t),
                                                  "transform": staticmethod(lambda point, a, b: (point[0] + 1000.0, point[1], point[2]))})
    ms.Vector = lambda v: tuple(v)
    monkeypatch.setattr(msapi, "Metashape", ms)
    return msapi


def test_positions_are_replaced_and_can_be_put_back(ms, monkeypatch):
    msapi = fake_metashape(monkeypatch, ms)
    chunk = types.SimpleNamespace(cameras=[FakeCamera("D:/f/a.JPG", (8.0, 41.0, 100.0)), FakeCamera("D:/f/b.JPG", None)], crs=object())
    before = msapi.camera_reference_entries(chunk)
    assert before[0]["location"] == [8.0, 41.0, 100.0] and before[1]["location"] is None
    items = [{"path": "d:\\F\\A.jpg", "lat": 45.0, "lon": 5.0, "h": 95.0, "sd_h": 0.02, "sd_v": 0.03},
             {"path": "D:/f/missing.JPG", "lat": 45.0, "lon": 5.0, "h": 95.0, "sd_h": 0.02, "sd_v": 0.03}]
    out = msapi.set_camera_positions(chunk, items)
    assert out["done"] == 1 and out["missing"] == ["D:/f/missing.JPG"] and out["failed"] == []
    ref = chunk.cameras[0].reference
    assert ref.location == (1005.0, 45.0, 95.0) and ref.location_accuracy == (0.02, 0.02, 0.03) and ref.location_enabled
    assert msapi.restore_camera_references(chunk, before) == 2
    assert chunk.cameras[0].reference.location == (8.0, 41.0, 100.0)


# -- pipeline helpers ----------------------------------------------------------------------------------------------

def test_camera_position_adds_the_lever_arm_in_north_east_down():
    event = types.SimpleNamespace(offset_ned=(0.0, 0.0, 0.1))
    comb = types.SimpleNamespace(lat=np.array([45.3]), lon=np.array([5.15]), h=np.array([500.0]))
    cam = pipeline.camera_positions([event], comb)
    assert cam["h"][0] == pytest.approx(499.9, abs=1e-4) and cam["lat"][0] == pytest.approx(45.3, abs=1e-9)


def test_onboard_comparison_ignores_non_rtk_photos_and_catches_gross_errors():
    def ev(q, lat):
        return types.SimpleNamespace(onboard=(lat, 5.15, 500.0), onboard_q=q)
    events = [ev(50, 45.3), ev(50, 45.3), ev(50, 45.3), ev(16, 45.3)]
    cam = {"lat": np.full(4, 45.3 + 1e-6), "lon": np.full(4, 5.15), "h": np.full(4, 500.0)}
    check = pipeline.compare_onboard(events, cam)
    assert check["rtk_photos"] == 3 and check["bias_n"] == pytest.approx(0.111, abs=0.005) and not check["gross_error"]
    far = {"lat": np.full(4, 45.31), "lon": np.full(4, 5.15), "h": np.full(4, 500.0)}
    assert pipeline.compare_onboard(events, far)["gross_error"]


def test_flight_window_is_the_rover_span_widened_to_the_events(tmp_path):
    folder = flight_folder(tmp_path)
    reader = dji_m3e.DjiM3E()
    files = reader.scan(folder)
    events, _ = reader.read_events(files)
    window = pipeline.flight_window(files, events[2:], margin=60)       # event 3 is in 2023, inside the header span
    assert window.start == datetime.datetime(2023, 12, 14, 9, 16, 30)   # header start 09:17:30 minus the 60 s margin
    assert window.end >= gnsstime.from_seconds(events[2].time)
