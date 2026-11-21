"""Aero-PPK wizard: the rules behind each step, tested without Qt (basket, own base, device, outputs, strategy, summary, time axis)."""
import datetime
import gzip
import struct
import types

import numpy as np
import pytest

from aerotools.tools.aeroppk import (antex, basket, combine, devices, drones, flights, geo, outputs, ownbase, pipeline, reportdata,
                                     state, summary, timeline)
from aerotools.tools.aeroppk.drones.base import PhotoEvent
from aerotools.tools.aeroppk.stations import rgp

import test_aeroppk_core as core


# ---------------------------------------------------------------------------------------------------------------------
# basket
# ---------------------------------------------------------------------------------------------------------------------

def station(code, km):
    s = rgp.Station(code, code + "00FRA", "Station " + code, 42.0, 9.0, 100.0, None, None, "", "")
    s.distance_m = km * 1000.0
    return s


def test_the_downloaded_base_is_used_and_next_needs_it():
    b = basket.Basket(limit=1)
    b.add(station("NEAR", 2))
    assert b.can_continue() and [i.code for i in b.todo()] == ["NEAR"]     # Compute downloads what is still to download
    b.get("NEAR").state, b.get("NEAR").base = basket.DONE, object()
    assert [i.code for i in b.used()] == ["NEAR"] and b.can_continue()
    b.get("NEAR").state = basket.RUNNING
    assert b.busy() and not b.can_continue()                               # Next waits for the running download
    b.clear_results()
    assert not b.done() and len(b.todo()) == 1 and b.can_continue()
    b.get("NEAR").state, b.get("NEAR").error = basket.ERROR, "no data"
    assert b.planned() == [] and [i.code for i in b.todo()] == ["NEAR"] and b.can_continue()    # a failed row is tried again
    assert not basket.Basket(limit=1).can_continue()                       # an empty basket has nothing to compute


def test_a_click_on_the_chosen_station_takes_it_out():
    b = basket.Basket(limit=1)
    a = station("AAAA", 5)
    assert b.add(a) and not b.add(a)                                       # twice is refused
    assert b.toggle(a) is False and b.codes() == [] and not b.remove("AAAA")


def test_the_basket_holds_one_station():
    b = basket.Basket(limit=1)
    for i in range(12):
        b.add(station("S%03d" % i, i + 1))
    assert len(b) == 1 and b.limit == 1 and b.codes() == ["S011"]


def test_single_base_basket_holds_one_station_and_a_new_choice_replaces_it():
    b = basket.Basket(limit=1)
    assert b.add(station("AAAA", 5)) and b.codes() == ["AAAA"]
    assert b.add(station("BBBB", 9)) and b.codes() == ["BBBB"]          # the choice replaces the old one, it is not refused
    assert not b.add(station("BBBB", 9))
    assert b.toggle(station("BBBB", 9)) is False and b.codes() == []     # a click on the chosen station takes it out


def test_search_radius_slider_and_margin_and_rate_text():
    assert [basket.radius_from_slider(i) for i in range(9)] == [5, 10, 20, 30, 50, 100, 150, 200, 300]
    assert basket.radius_from_slider(-3) == 5 and basket.radius_from_slider(99) == 300
    assert basket.slider_from_radius(120) == 5 and basket.slider_from_radius(50) == 4 and basket.slider_from_radius(20) == 2
    assert basket.DEFAULT_RADIUS == 50
    assert basket.margin_seconds(1) == 3600 and basket.margin_seconds(2) == 7200 and basket.margin_seconds(5) == 3600
    assert basket.rate_text(1.0) == "1 Hz" and basket.rate_text(0.2) == "5 Hz" and basket.rate_text(30.0) == "1/30 Hz" and basket.rate_text(None) == ""


# ---------------------------------------------------------------------------------------------------------------------
# own base: what was typed
# ---------------------------------------------------------------------------------------------------------------------

def test_angles_are_read_as_decimals_or_degrees_minutes_seconds():
    assert ownbase.parse_angle("45.5") == 45.5 and ownbase.parse_angle("-1,25") == -1.25
    assert ownbase.parse_angle("45 30 0") == pytest.approx(45.5)
    assert ownbase.parse_angle("45 18 35.2 S") == pytest.approx(-(45 + 18 / 60.0 + 35.2 / 3600.0))
    assert ownbase.parse_angle("12 W") == -12.0
    assert ownbase.parse_angle("") is None and ownbase.parse_angle("abc") is None
    assert ownbase.parse_number("1 234,5") == 1234.5 and ownbase.parse_number("x") is None


def test_marker_to_phase_centre_lifts_the_point_along_the_vertical():
    pos = ownbase.position_from_marker(45.0, 5.0, 300.0, 2.0, 0.134)
    assert pos.source == "marker" and pos.pole == 2.0 and pos.pc_offset == 0.134 and pos.marker == (45.0, 5.0, 300.0)
    assert pos.lat == 45.0 and pos.lon == 5.0
    assert pos.h == pytest.approx(302.134, abs=1e-9)                      # pole and phase centre heights add up
    x, y, z = geo.llh_to_ecef(45.0, 5.0, 300.0)
    px, py, pz = pos.xyz
    assert np.sqrt((px - x) ** 2 + (py - y) ** 2 + (pz - z) ** 2) == pytest.approx(2.134, abs=1e-3)


def test_the_phase_centre_typed_is_used_as_it_is_and_never_lifted():
    pos = ownbase.position_from_phase_centre(42.5, 9.2, 120.0)
    assert (pos.lat, pos.lon, pos.h, pos.source) == (42.5, 9.2, 120.0, "position")


def test_the_pole_height_is_any_number_and_both_heights_add_to_the_marker_height():
    low = ownbase.position_from_marker(42.0, 9.0, 50.0, 1.6, 0.1)
    assert ownbase.position_from_marker(42.0, 9.0, 50.0, 2.0, 0.1).h - low.h == pytest.approx(0.4, abs=1e-9)
    assert ownbase.position_from_marker(42.0, 9.0, 50.0, 1.234, 0.0).h == pytest.approx(51.234, abs=1e-9)
    assert ownbase.DEFAULT_POLE == 2.0


def test_pole_and_phase_centre_heights_are_checked():
    assert ownbase.heights_valid(2.0, 0.134) and ownbase.heights_valid(0.5, 0.0) and ownbase.heights_valid(3.7, 1.0)
    assert not ownbase.heights_valid(0.0, 0.1) and not ownbase.heights_valid(-1.0, 0.1) and not ownbase.heights_valid(25.0, 0.1)
    assert not ownbase.heights_valid(2.0, 1.5) and not ownbase.heights_valid(None, 0.1) and not ownbase.heights_valid(2.0, None)


def test_position_checks_catch_the_usual_mistakes():
    keys = lambda *a, **k: [c.key for c in ownbase.check_position(*a, **k)]
    assert keys(None, 9.0, 10.0) == ["incomplete"]
    assert keys(95.0, 9.0, 10.0) == ["range"] and keys(0.0, 0.0, 10.0) == ["zero"]
    assert "height" in keys(42.0, 9.0, 20000.0)
    assert keys(42.0, 9.0, 60.0, site=(42.01, 9.01)) == []
    assert keys(42.0, 9.0, 60.0, site=(48.0, 2.0)) == ["far"]                           # swapped / wrong sign errors land far away
    header = geo.llh_to_ecef(42.0, 9.0, 60.0)
    assert keys(42.0, 9.0, 60.0, header_xyz=header) == []
    assert keys(42.01, 9.0, 60.0, header_xyz=header) == ["header_gap"]
    stops = [c for c in ownbase.check_position(95.0, 9.0, 10.0)]
    assert stops[0].level == "stop"


# ---------------------------------------------------------------------------------------------------------------------
# own base: what the files are
# ---------------------------------------------------------------------------------------------------------------------

OBS = ("     3.05           OBSERVATION DATA    M: Mixed            RINEX VERSION / TYPE\n"
       "GENERIC PROGRAM     TEST                20301001 000000 UTC PGM / RUN BY / DATE\n"
       "STNA                                                        MARKER NAME\n"
       "0000000             TRIMBLE ALLOY       5.37                REC # / TYPE / VERS\n"
       "0000                TRM55971.00     NONE                    ANT # / TYPE\n"
       "  4664928.5490   751615.7900  4270552.1690                  APPROX POSITION XYZ\n"
       "        1.8000        0.0000        0.0000                  ANTENNA: DELTA H/E/N\n"
       "G    8 C1C L1C D1C S1C C2W L2W D2W S2W                      SYS / # / OBS TYPES\n"
       "     1.000                                                  INTERVAL\n"
       "  2030    01     1    10    00    0.0000000     GPS         TIME OF FIRST OBS\n"
       "  2030    01     1    13    00    0.0000000     GPS         TIME OF LAST OBS\n"
       "                                                            END OF HEADER\n")
NAV = ("     3.04           N: GNSS NAV DATA    M: Mixed            RINEX VERSION / TYPE\n"
       "                                                            END OF HEADER\n")
FLIGHT = (datetime.datetime(2030, 1, 1, 10, 30), datetime.datetime(2030, 1, 1, 10, 40))


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text)
    return str(path)


def test_files_are_recognised_whatever_their_order_and_names(tmp_path):
    nav = write(tmp_path, "z_orbits.dat", NAV)
    obs = write(tmp_path, "a_base.xyz", OBS)
    rec = ownbase.recognise([nav, obs], str(tmp_path / "work"), None, FLIGHT, ["G"], False)
    assert rec.ok() and rec.obs == obs and rec.navs == [nav]
    assert rec.brand == "Trimble" and rec.obs_info.antenna.startswith("TRM55971.00")
    assert rec.names == {"obs": ["a_base.xyz"], "nav": ["z_orbits.dat"]}
    assert rec.issues == []


def test_a_compressed_observation_file_is_unpacked_and_the_source_is_kept(tmp_path):
    packed = tmp_path / "base.rnx.gz"
    with gzip.open(str(packed), "wb") as handle:
        handle.write(OBS.encode("ascii"))
    nav = write(tmp_path, "base.nav", NAV)
    rec = ownbase.recognise([str(packed), nav], str(tmp_path / "work"), None, FLIGHT, ["G"], False)
    assert rec.ok() and rec.obs.startswith(str(tmp_path / "work")) and packed.exists()


def test_missing_files_say_what_is_missing(tmp_path):
    obs = write(tmp_path, "base.obs", OBS)
    rec = ownbase.recognise([obs], str(tmp_path / "w"), None, FLIGHT, ["G"], rover_has_nav=False)
    assert [(i.key, i.level) for i in rec.issues] == [("no_nav", "stop")] and not rec.ok()
    rec = ownbase.recognise([obs], str(tmp_path / "w"), None, FLIGHT, ["G"], rover_has_nav=True)
    assert [(i.key, i.level) for i in rec.issues] == [("no_nav_base", "warn")] and rec.ok()
    rec = ownbase.recognise([], str(tmp_path / "w"), None, FLIGHT, ["G"], True)
    assert rec.issues[0].key == "no_obs" and not rec.ok()
    nav = write(tmp_path, "only.nav", NAV)
    assert ownbase.recognise([nav], str(tmp_path / "w"), None, FLIGHT, ["G"], True).issues[0].key == "no_obs"


def test_raw_receiver_files_and_unknown_files_are_named(tmp_path):
    raw = tmp_path / "log.ubx"
    raw.write_bytes(b"\xb5b\x01\x02")
    other = write(tmp_path, "notes.txt", "hello")
    rec = ownbase.recognise([str(raw), other], str(tmp_path / "w"), None, FLIGHT, ["G"], True)
    keys = [i.key for i in rec.issues]
    assert "raw" in keys and "unknown" in keys and "no_obs" not in keys
    raw_issue = next(i for i in rec.issues if i.key == "raw")
    assert raw_issue.fields["brand"] == "u-blox" and raw_issue.level == "stop"


def test_coverage_and_sampling_and_systems_are_checked(tmp_path):
    obs = write(tmp_path, "base.obs", OBS)
    nav = write(tmp_path, "base.nav", NAV)
    late = (datetime.datetime(2030, 1, 1, 12, 50), datetime.datetime(2030, 1, 1, 13, 20))
    rec = ownbase.recognise([obs, nav], str(tmp_path / "w"), None, late, ["G"], False)
    assert "coverage" in [i.key for i in rec.issues] and rec.ok()                      # part of the flight is covered
    far = (datetime.datetime(2030, 1, 2, 10, 0), datetime.datetime(2030, 1, 2, 10, 10))
    rec = ownbase.recognise([obs, nav], str(tmp_path / "w"), None, far, ["G"], False)
    assert "no_overlap" in [i.key for i in rec.issues] and not rec.ok()
    rec = ownbase.recognise([obs, nav], str(tmp_path / "w"), None, FLIGHT, ["R", "E"], False)
    assert "no_common" in [i.key for i in rec.issues] and not rec.ok()
    slow = write(tmp_path, "slow.obs", OBS.replace("     1.000       ", "    30.000       "))
    rec = ownbase.recognise([slow, nav], str(tmp_path / "w"), None, FLIGHT, ["G"], False)
    assert [i.key for i in rec.issues] == ["coarse"] and rec.ok()


def test_brands_come_from_the_receiver_name_or_the_program():
    assert ownbase.brand_of("LEICA GR50") == "Leica" and ownbase.brand_of("SEPT POLARX5") == "Septentrio"
    assert ownbase.brand_of("", "u-blox GNSS") == "u-blox" and ownbase.brand_of("Emlid Reach RS2+") == "Emlid"
    assert ownbase.brand_of("XYZ-1") == ""


def test_a_base_made_from_the_files_carries_no_antenna_offset_and_the_typed_phase_centre(tmp_path):
    obs, nav = write(tmp_path, "b.obs", OBS), write(tmp_path, "b.nav", NAV)
    rec = ownbase.recognise([obs, nav], str(tmp_path / "w"), None, FLIGHT, ["G"], False)
    pos = ownbase.position_from_phase_centre(42.0, 9.0, 60.0)
    base = ownbase.make_base(rec, pos, (42.01, 9.0), pipeline.Window(*FLIGHT))
    assert base.antenna_delta == (0.0, 0.0, 0.0) and base.xyz == pos.xyz and base.name == "BASE"
    assert base.spec.distance_m == pytest.approx(geo.distance_m(42.01, 9.0, 42.0, 9.0))
    assert "header_position_own" in base.warnings                      # the header position (France) is far from the typed one
    assert base.spec.product.obs[0][0] == obs and base.spec.product.nav[0][0] == nav
    with pytest.raises(pipeline.PipelineError):
        ownbase.make_base(ownbase.Recognition(), pos, None, None)


# ---------------------------------------------------------------------------------------------------------------------
# device
# ---------------------------------------------------------------------------------------------------------------------

def jpeg_with(make, model, big_endian=False):
    """A JPEG header with an EXIF block holding two text tags. Texts of 4 bytes or less sit inside the entry, longer ones after the table."""
    order = ">" if big_endian else "<"
    tail, entries = b"", b""
    base = 8 + 2 + 2 * 12 + 4
    for tag, text in ((0x010F, make), (0x0110, model)):
        raw = text.encode() + b"\x00"
        if len(raw) <= 4:
            entries += struct.pack(order + "HHI", tag, 2, len(raw)) + raw.ljust(4, b"\x00")
        else:
            entries += struct.pack(order + "HHII", tag, 2, len(raw), base + len(tail))
            tail += raw
    tiff = (b"MM" if big_endian else b"II") + struct.pack(order + "HI", 42, 8) + struct.pack(order + "H", 2) + entries
    tiff += struct.pack(order + "I", 0) + tail
    payload = b"Exif\x00\x00" + tiff
    return b"\xff\xd8" + b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload + b"\xff\xd9"


def test_make_and_model_are_read_from_the_exif_header():
    assert devices.parse_make_model(jpeg_with("DJI", "M3E")) == ("DJI", "M3E")
    assert devices.parse_make_model(jpeg_with("DJI", "M3E", True)) == ("DJI", "M3E")
    assert devices.parse_make_model(jpeg_with("Hasselblad", "L2D-20c")) == ("Hasselblad", "L2D-20c")
    assert devices.parse_make_model(b"not a jpeg") == ("", "") and devices.parse_make_model(b"\xff\xd8\xff\xd9") == ("", "")


def test_the_device_is_detected_from_the_photos_or_the_names(tmp_path):
    photo = tmp_path / "x.jpg"
    photo.write_bytes(jpeg_with("DJI", "M3E"))
    other = tmp_path / "y.jpg"
    other.write_bytes(jpeg_with("ACME", "CAM1"))
    assert devices.detect([str(photo)]) == "dji_m3e"
    assert devices.detect([str(other)]) == ""
    assert devices.detect([str(other)], core.flight_folder(tmp_path)) == "dji_m3e"      # file names of the folder as a second chance


def test_choices_list_the_readers_then_other_and_the_antenna_of_the_m3e_is_known():
    names = [c.id for c in devices.choices()]
    assert names == [r.id for r in drones.READERS] + [devices.OTHER]
    assert devices.choice_by_id("dji_m3e").antenna_known and not devices.choice_by_id(devices.OTHER).antenna_known
    assert devices.choice_by_id("nonsense").id == devices.OTHER


def test_manual_antenna_height_is_vertical_only_and_sane():
    assert devices.parse_antenna_z("0.085") == 0.085 and devices.parse_antenna_z("8,5") is None      # 8.5 m is a typing mistake
    assert devices.parse_antenna_z("") is None and devices.parse_antenna_z("-0.1") is None
    ev = PhotoEvent(1, 100.0)
    ev.offset_ned = (0.1, -0.2, 0.3)
    out = devices.with_antenna_height([ev], 0.05)
    assert out[0].offset_ned == (0.1, -0.2, pytest.approx(0.35)) and ev.offset_ned == (0.1, -0.2, 0.3)       # a copy: the original is kept


# ---------------------------------------------------------------------------------------------------------------------
# flights and the wizard state
# ---------------------------------------------------------------------------------------------------------------------

def test_flights_are_found_and_a_file_given_by_hand_completes_the_checklist(tmp_path):
    folder = core.flight_folder(tmp_path, with_obs=False)
    found, unread = flights.scan_folders([folder])
    assert len(found) == 1 and not unread
    flight = found[0]
    assert any(n.key == "rover_obs" and not n.present for n in flight["needs"])
    obs = tmp_path / "elsewhere.obs"
    obs.write_text(core.V3)
    flight["files"].obs = str(obs)
    flight = flights.refresh(flight["reader"], flight["files"])
    assert all(n.present for n in flight["needs"] if n.level == "blocking")
    info = flights.describe(flight)
    assert info["events"] == 3 and info["duration"] > 0 and info["start"] < info["end"]


def test_a_folder_of_photos_no_reader_knows_is_listed_as_unread(tmp_path):
    folder = tmp_path / "holiday"
    folder.mkdir()
    for i in range(3):
        (folder / ("IMG_%d.jpg" % i)).write_bytes(b"x")
    found, unread = flights.scan_folders([str(folder)])
    assert found == [] and unread == [(str(folder), 3)]


def make_state(tmp_path, with_obs=True):
    st = state.WizardState()
    st.flights, _ = flights.scan_folders([core.flight_folder(tmp_path, with_obs)])
    st.device_id = "dji_m3e"
    return st


def test_the_drone_step_needs_a_flight_and_a_known_or_typed_antenna_the_files_step_needs_the_files(tmp_path):
    st = state.WizardState()
    assert not st.can_next(state.DRONE) and not st.can_next(state.FILES)
    st = make_state(tmp_path, with_obs=False)
    assert st.blocking_gaps() and st.can_next(state.DRONE) and not st.can_next(state.FILES)      # a gap is the Files step's business
    folder = tmp_path / "second"
    folder.mkdir()
    st = make_state(folder)
    assert st.antenna_state() == "known" and st.can_next(state.DRONE) and st.can_next(state.FILES)
    st.device_id = devices.OTHER
    assert st.antenna_state() == "none" and not st.can_next(state.DRONE) and not st.can_next(state.FILES)
    assert st.unsupported()


def test_an_unknown_antenna_needs_a_typed_height(tmp_path, monkeypatch):
    st = make_state(tmp_path)
    monkeypatch.setattr(drones.READERS[0], "antenna_known", False)
    assert st.antenna_state() == "needed" and not st.can_next(state.DRONE) and not st.can_next(state.FILES)
    st.antenna_z = devices.parse_antenna_z("0.1")
    assert st.antenna_state() == "manual" and st.can_next(state.DRONE) and st.can_next(state.FILES)


def test_the_unsupported_state_is_for_photos_no_reader_knows_and_the_supported_names_come_from_the_readers(tmp_path):
    st = state.WizardState()
    assert not st.unsupported()                                                   # nothing read yet: the empty state, not this one
    st.unread = [("D:/holiday", 3)]
    st.device_id = devices.OTHER
    assert st.unsupported() and not st.can_next(state.DRONE)
    assert state.supported_names() == [r.name for r in drones.READERS] and state.supported_names()
    st = make_state(tmp_path)
    assert not st.unsupported()


def two_flights(tmp_path):
    """A state with two flights read (two copies of the synthetic flight), the second one with a gap when `with_obs` is False."""
    st = state.WizardState()
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()
    st.flights, _ = flights.scan_folders([core.flight_folder(first, True), core.flight_folder(second, False)])
    st.device_id = "dji_m3e"
    return st


def test_a_flight_that_is_left_out_is_not_in_the_flights_that_count_but_stays_in_the_list(tmp_path):
    st = two_flights(tmp_path)
    assert len(st.all_flights) == 2 and len(st.flights) == 2 and st.skipped == set()
    folder = st.all_flights[0]["files"].folder
    assert st.skip_flight(folder, True) and st.is_skipped(st.all_flights[0])
    assert len(st.all_flights) == 2 and [f["files"].folder for f in st.flights] == [st.all_flights[1]["files"].folder]
    assert st.skip_flight(folder, False) and len(st.flights) == 2


def test_the_blocking_gap_of_a_flight_that_is_left_out_does_not_block(tmp_path):
    st = two_flights(tmp_path)
    assert st.blocking_gaps() and not st.can_next(state.FILES)
    gap_folder = st.all_flights[1]["files"].folder
    assert st.skip_flight(gap_folder, True)
    assert st.blocking_gaps() == [] and st.can_next(state.FILES)
    assert st.skip_flight(gap_folder, False) and st.blocking_gaps()


def test_the_last_flight_cannot_be_left_out_and_an_unknown_folder_is_ignored(tmp_path):
    st = two_flights(tmp_path)
    a, b = (f["files"].folder for f in st.all_flights)
    assert st.skip_flight(a, True)
    assert not st.skip_flight(b, True) and len(st.flights) == 1                   # one flight at least counts
    assert not st.skip_flight("nowhere", True) and not st.skip_flight(a, True)   # unknown, or already out: nothing changes
    assert not st.skip_flight(b, False)                                           # already in


def test_setting_the_flights_again_forgets_what_was_left_out(tmp_path):
    st = two_flights(tmp_path)
    st.skip_flight(st.all_flights[0]["files"].folder, True)
    st.flights = list(st.all_flights)
    assert st.skipped == set() and len(st.flights) == 2


def test_nothing_is_ready_without_an_included_flight_and_the_other_steps_follow_the_spec(tmp_path):
    st = state.WizardState()
    assert st.flights == [] and not st.can_next(state.FILES)
    assert st.can_next(state.MODE)                                                 # always
    assert not st.can_next(state.RESULTS) and not st.can_next(state.EXPORT)
    st.results = [object()]
    assert st.can_next(state.RESULTS) and not st.can_next(state.EXPORT)            # the Export screen has its own rule
    assert state.STEPS == (state.DRONE, state.FILES, state.MODE, state.BASE, state.RESULTS, state.EXPORT) == tuple(range(6))


def test_the_base_step_depends_on_the_mode_and_the_files_step_on_the_flights(tmp_path):
    st = make_state(tmp_path)
    assert not st.can_next(state.BASE)
    item_station = station("AAAA", 3)
    st.basket.add(item_station)
    assert st.can_next(state.BASE)                                                # a station to download is enough: Compute fetches it
    st.basket.items[0].state = basket.RUNNING
    assert not st.can_next(state.BASE)                                            # not while the download runs
    st.basket.items[0].state, st.basket.items[0].base = basket.DONE, object()
    assert st.can_next(state.BASE) and st.can_next(state.FILES)
    st.set_mode(state.MODE_OWN)                                                   # the other mode counts now
    assert not st.can_next(state.BASE)
    st.own = types.SimpleNamespace(ok=lambda: True)
    assert not st.can_next(state.BASE)                                            # files fine, position still missing
    st.own_position = ownbase.position_from_phase_centre(42.0, 9.0, 60.0)
    assert st.can_next(state.BASE) and not st.can_next(state.RESULTS)


# ---------------------------------------------------------------------------------------------------------------------
# outputs
# ---------------------------------------------------------------------------------------------------------------------

def test_output_options_default_and_the_exif_rewrite_never_comes_back_from_a_saved_file():
    o = outputs.OutputOptions()
    assert o.kinds() == ["pdf", "csv"] and o.exif is False
    saved = {"pdf": False, "csv": True, "kml": True, "txt": False, "exif": True}
    loaded = outputs.OutputOptions.from_dict(saved)
    assert loaded.kinds() == ["csv", "kml"] and loaded.exif is False
    assert outputs.OutputOptions.from_dict(None).kinds() == ["pdf", "csv"]


def test_the_folder_is_outputs_ppk_next_to_the_project_or_the_photos(tmp_path):
    import os
    psx = str(tmp_path / "proj" / "a.psx")
    assert outputs.output_folder(psx) == str(tmp_path / "proj" / "Outputs" / "PPK")
    assert outputs.output_folder("", str(tmp_path / "photos" / "FLIGHT_DEMO_001")) == str(tmp_path / "photos" / "Outputs" / "PPK")
    assert outputs.output_folder().endswith("Outputs" + os.sep + "PPK")


def test_a_file_that_exists_is_never_overwritten(tmp_path):
    first = outputs.unique_path(str(tmp_path), "r.pdf")
    open(first, "w").close()
    second = outputs.unique_path(str(tmp_path), "r.pdf")
    open(second, "w").close()
    assert first.endswith("r.pdf") and second.endswith("r_v2.pdf") and outputs.unique_path(str(tmp_path), "r.pdf").endswith("r_v3.pdf")


def test_kml_and_text_have_one_line_per_photo_and_say_the_frame():
    data = reportdata.sample()
    kml = outputs.kml_text(data)
    assert kml.count("<Placemark>") == 12 and "RGF93" in kml and kml.startswith("<?xml")
    assert "<coordinates>5.150000000,45.300000000," in kml
    lines = outputs.txt_text(data).splitlines()
    assert lines[0].startswith("#") and "RGF93" in lines[0] and lines[1].startswith("# name")
    assert len([l for l in lines if l and not l.startswith("#")]) == 12
    first = lines[2].split("\t")
    assert first[0] == "IMG_0001.JPG" and float(first[1]) == pytest.approx(45.30) and first[6] == "fixed"
    assert lines[2 + 5].split("\t")[6] == "float"                                  # the float photo of the sample


def test_export_writes_only_the_ticked_files_and_uses_the_given_writers(tmp_path):
    data = reportdata.sample()
    calls = []
    written = outputs.export(data, outputs.OutputOptions(pdf=True, csv=False, kml=True, txt=False), str(tmp_path / "Outputs" / "PPK"),
                             build_pdf=lambda d, p: calls.append(p) or open(p, "w").close(), today=datetime.date(2030, 1, 1))
    names = sorted(__import__("os").path.basename(p) for p in written)
    assert names == ["2030-01-01_PPK_positions_Demo_project.kml", "2030-01-01_PPK_report_Demo_project.pdf"] and len(calls) == 1
    again = outputs.export(data, outputs.OutputOptions(pdf=False, csv=False, kml=True, txt=False), str(tmp_path / "Outputs" / "PPK"),
                           today=datetime.date(2030, 1, 1))
    assert again[0].endswith("_v2.kml")


# ---------------------------------------------------------------------------------------------------------------------
# strategy, recombination, figures of the results
# ---------------------------------------------------------------------------------------------------------------------

from aeroppk_synth import N, fake_result  # noqa: E402


def test_figures_verdict_and_table_row_of_one_base():
    result = fake_result(biases=((0.0, 0.0),), km=(3.0,))
    pipeline.recombine(result)
    fig = summary.figures([result])
    assert fig["photos"] == N and fig["fix_pct"] == 100.0 and fig["none"] == 0
    assert summary.verdict([result]) == summary.GOOD and summary.verdict([]) == summary.BAD
    rows = summary.base_rows([result])
    assert [(r["code"], r["role"]) for r in rows] == [("B1", "used")] and rows[0]["status"] == "ok"
    assert summary.levels([result]) == [int(v) for v in result.combined.level]


# ---------------------------------------------------------------------------------------------------------------------
# time axis
# ---------------------------------------------------------------------------------------------------------------------

def test_time_axis_rows_cover_or_not_and_planned_rows_show_the_window():
    flight = (datetime.datetime(2030, 1, 1, 10, 30), datetime.datetime(2030, 1, 1, 10, 40))
    rows = timeline.build_rows(flight, 1, [("A", datetime.datetime(2030, 1, 1, 9, 0), datetime.datetime(2030, 1, 1, 12, 0)),
                                           ("B", datetime.datetime(2030, 1, 1, 10, 35), datetime.datetime(2030, 1, 1, 12, 0)),
                                           ("C", None, None)], "Flight")
    assert [r.kind for r in rows] == ["flight", "base", "base", "planned"]
    assert rows[1].ok and not rows[2].ok                                                # B starts after the flight started
    assert rows[3].start == flight[0] - datetime.timedelta(hours=1) and rows[3].end == flight[1] + datetime.timedelta(hours=1)
    start, end = timeline.axis_range(rows, 1)
    assert start == datetime.datetime(2030, 1, 1, 9, 0) and end == datetime.datetime(2030, 1, 1, 12, 0)
    ticks = timeline.ticks(start, end)
    assert ticks[0] == start and ticks[-1] == end and len(ticks) == 4
    assert len(timeline.ticks(start, start + datetime.timedelta(hours=40), 8)) <= 9
    assert timeline.fraction(start, start, end) == 0.0 and timeline.fraction(end + datetime.timedelta(hours=9), start, end) == 1.0


# ---------------------------------------------------------------------------------------------------------------------
# antenna calibration file
# ---------------------------------------------------------------------------------------------------------------------

def atx_block(model, radome, serial, g01_up, g02_up):
    def line(text, label):
        return text.ljust(60) + label + "\n"
    return (line("", "START OF ANTENNA") + line(model.ljust(16) + radome.ljust(4) + serial.ljust(20), "TYPE / SERIAL NO")
            + line("   G01", "START OF FREQUENCY") + line("%10.2f%10.2f%10.2f" % (0.1, 0.2, g01_up), "NORTH / EAST / UP")
            + line("   G01", "END OF FREQUENCY") + line("   G02", "START OF FREQUENCY")
            + line("%10.2f%10.2f%10.2f" % (0.0, 0.0, g02_up), "NORTH / EAST / UP") + line("   G02", "END OF FREQUENCY")
            + line("", "END OF ANTENNA"))


def test_the_phase_centre_height_comes_from_the_l1_offset_of_the_antenna_file(tmp_path):
    path = tmp_path / "test.atx"
    path.write_text(atx_block("TRM55971.00", "NONE", "", 59.11, 80.0) + atx_block("LEIAR25.R3", "LEIT", "", 100.0, 120.0)
                    + atx_block("BLOCK IIA", "", "G01", 1000.0, 1000.0))
    assert antex.phase_centre_up(str(path), "TRM55971.00     NONE") == pytest.approx(0.05911)
    assert antex.phase_centre_up(str(path), "LEIAR25.R3      LEIT") == pytest.approx(0.1)
    assert antex.phase_centre_up(str(path), "LEIAR25.R3      NONE") is None            # that model exists only with its radome
    assert antex.phase_centre_up(str(path), "UNKNOWN") is None and antex.phase_centre_up(str(tmp_path / "none.atx"), "X") is None


def test_a_lone_base_that_never_fixes_is_used_and_not_said_to_disagree():
    result = fake_result()
    pipeline.recombine(result)
    result.combined.bases["B1"].update(suspect=True, reason="no_fix")
    result.combined.suspect = ["B1"]
    rows = [r for r in summary.base_rows([result]) if r["code"] == "B1"]
    assert rows[0]["role"] == "used"
    keys = [k for k, _f in summary.warning_keys([result])]
    assert "suspect_base" not in keys and "suspect_used" not in keys
