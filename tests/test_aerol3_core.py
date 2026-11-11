"""Aero-L3 core: header reader, MRK, calibration, angle conventions, scan, plan, checks. No Metashape, no Qt."""

import datetime
import io
import math
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import aerol3_synth as synth  # noqa: E402
from aerotools.tools.aerol3 import calib, checks, geo, geomcheck, mrk, orient, plan, scan, xmpdji  # noqa: E402


def flights_of(result):
    return result.flights


# --------------------------------------------------------------------------------------------------
# JPEG header
# --------------------------------------------------------------------------------------------------

def test_header_reader_gets_size_exif_and_xmp():
    tags = synth.photo_tags("L", 120.0, "B", 48.8, 2.3, 200.0, synth.GPS0)
    data = synth.make_jpeg(tags=tags, lat=48.8, lon=2.3, alt=200.0, padding=3000)
    info = xmpdji.parse_jpeg_stream(io.BytesIO(data))
    assert (info.width, info.height) == (6144, 4096)
    assert info.exif["make"] == "DJI" and info.exif["model"] == "L3" and info.exif["orientation"] == 1
    assert info.exif["focal_length"] == pytest.approx(14.45) and info.exif["focal_length_35mm"] == 28
    assert info.exif["gps_lat"] == pytest.approx(48.8, abs=1e-6) and info.exif["gps_lon"] == pytest.approx(2.3, abs=1e-6)
    assert info.exif["gps_alt"] == pytest.approx(200.0)
    assert info.exif["datetime_original"] == "2030:01:01 12:05:53"
    assert info.xmp["drone-dji:ImageSource"] == "LEFT_CAMERA"
    assert xmpdji.number(info.xmp, "GpsLatitude") == pytest.approx(48.8)
    assert xmpdji.number(info.xmp, "Missing", 7) == 7


def test_header_reader_big_endian_exif_and_southern_western_hemisphere():
    data = synth.make_jpeg(tags={}, lat=-33.5, lon=-70.25, alt=12.0, order=">", orientation=6)
    info = xmpdji.parse_jpeg_stream(io.BytesIO(data))
    assert info.exif["orientation"] == 6
    assert info.exif["gps_lat"] == pytest.approx(-33.5, abs=1e-6) and info.exif["gps_lon"] == pytest.approx(-70.25, abs=1e-6)


def test_header_reader_xmp_as_elements():
    packet = b'<x:xmpmeta><rdf:Description><drone-dji:GimbalYawDegree>+12.5</drone-dji:GimbalYawDegree></rdf:Description></x:xmpmeta>'
    assert xmpdji.number(xmpdji.parse_xmp(packet), "GimbalYawDegree") == 12.5


@pytest.mark.parametrize("data", [b"", b"not a jpeg", b"\xff\xd8", b"\xff\xd8\xff\xe1\x00"])
def test_header_reader_refuses_broken_files(data):
    with pytest.raises(xmpdji.JpegError):
        xmpdji.parse_jpeg_stream(io.BytesIO(data))


def test_header_reader_survives_a_damaged_exif_block():
    data = bytearray(synth.make_jpeg(tags={"ImageSource": "LEFT_CAMERA"}))
    i = data.find(b"Exif\x00\x00") + 6
    data[i + 4:i + 8] = b"\xff\xff\xff\xff"                 # IFD0 offset points outside
    info = xmpdji.parse_jpeg_stream(io.BytesIO(bytes(data)))
    assert info.exif == {} and info.xmp["drone-dji:ImageSource"] == "LEFT_CAMERA"


# --------------------------------------------------------------------------------------------------
# MRK and time
# --------------------------------------------------------------------------------------------------

SAMPLE_LINE = "0003_LV\t212772.442935\t[2608]\t   107,N\t   713,E\t   217,V\t48.83811012,Lat\t2.31879060,Lon\t199.878,Ellh\t0.001221, 0.001338, 0.002954\t34,Q"


def test_mrk_line_is_parsed():
    shot = mrk.parse_line(SAMPLE_LINE)
    assert shot.stem == "0003" and shot.lens == "L" and shot.week == 2608
    assert (shot.n_mm, shot.e_mm, shot.v_mm) == (107, 713, 217)
    assert shot.lat == pytest.approx(48.83811012) and shot.height == pytest.approx(199.878) and shot.quality == 34
    assert mrk.parse_line("garbage") is None


def test_mrk_time_is_gps_time_and_matches_the_utc_stamp_of_the_xmp():
    moment = mrk.gps_datetime(2608, 212772.442935)
    assert moment == datetime.datetime(2030, 1, 1, 11, 6, 12, 442935)
    assert mrk.parse_gps_stamp("2030-01-01T11:06:12.442935") == moment          # the XMP says UTC, it is GPS time
    assert mrk.gps_to_utc(moment) == datetime.datetime(2030, 1, 1, 11, 5, 54, 442935)
    assert mrk.parse_gps_stamp("nonsense") is None


def test_mrk_file_and_mean_offsets(tmp_path):
    path = tmp_path / "x_Timestamp.MRK"
    path.write_text(SAMPLE_LINE + "\n" + SAMPLE_LINE.replace("217,V", "  ", 1).replace("0003_LV", "0003_RV") + "\nheader line\n")
    shots = mrk.parse_mrk(str(path))
    assert len(shots) == 1
    assert mrk.mean_offsets(shots) == (107.0, 713.0, 217.0)
    assert mrk.parse_mrk(str(tmp_path / "missing.MRK")) == [] and mrk.mean_offsets([]) is None


# --------------------------------------------------------------------------------------------------
# Calibration
# --------------------------------------------------------------------------------------------------

def test_dewarp_data_to_metashape_calibration_swaps_the_tangential_terms():
    c = calib.calibration_for(6144, 4096, 0, synth.LV_DEWARP, 4986.19)
    assert (c.f, c.cx, c.cy) == (5000.0, 10.0, 20.0)
    assert (c.k1, c.k2, c.k3) == (0.1, -0.4, 0.5)
    # DJI writes ...,k2,p1,p2,k3 = -1.1e-5, -2.2e-5: Metashape's p1 is the second of them
    assert c.p1 == pytest.approx(-2.2e-5) and c.p2 == pytest.approx(-1.1e-5)
    assert c.source == "dewarp" and c.date == "2025-01-01"
    d = calib.calibration_for(6144, 4096, None, synth.RV_DEWARP)
    assert d.p1 == pytest.approx(4.4e-5) and d.p2 == pytest.approx(-3.3e-5) and d.cx == -12.0
    assert not calib.same_calibration(c, d)


def test_a_picture_already_corrected_is_a_pinhole():
    c = calib.calibration_for(6144, 4096, 1, synth.LV_DEWARP, 4986.197266)
    assert (c.f, c.cx, c.cy, c.k1, c.p1) == (4986.197266, 0.0, 0.0, 0.0, 0.0) and c.source == "ideal"
    with pytest.raises(calib.CalibrationError):
        calib.calibration_for(6144, 4096, 1, "", None)


@pytest.mark.parametrize("text", ["", None, "no semicolon", "2025;1,2,3", "2025;a,b,c,d,e,f,g,h,i", "2025;0,0,0,0,0,0,0,0,0"])
def test_a_damaged_dewarp_string_is_refused(text):
    with pytest.raises(calib.CalibrationError):
        calib.calibration_for(6144, 4096, 0, text)


def test_pixel_size_follows_the_35mm_equivalent_like_metashape():
    assert calib.pixel_size_mm(6144, 14.45, 28) == pytest.approx(0.0030238560267857144)
    assert calib.pixel_size_mm(6144, None, 28) is None and calib.pixel_size_mm(0, 14.45, 28) is None


# --------------------------------------------------------------------------------------------------
# Angles: the rules that keep the pictures from coming out flipped or turned
# --------------------------------------------------------------------------------------------------

def nominal(lens, heading, group):
    tags = synth.photo_tags(lens, heading, group, 48.8, 2.3, 200.0, synth.GPS0)
    angles = {"camera_yaw": float(tags["CameraYawDegree"]), "camera_pitch": float(tags["CameraPitchDegree"]), "camera_roll": float(tags["CameraRollDegree"]),
              "gimbal_yaw": float(tags["GimbalYawDegree"]), "gimbal_pitch": float(tags["GimbalPitchDegree"]), "gimbal_roll": float(tags["GimbalRollDegree"])}
    return angles, orient.nominal_orientation(angles, lens)[0]


def test_nadir_camera_with_north_at_the_top_of_the_picture():
    r = orient.dji_matrix(0.0, -90.0, 0.0)
    for row, want in zip(r, ((1, 0, 0), (0, -1, 0), (0, 0, -1))):
        assert row == pytest.approx(want, abs=1e-12)
    assert orient.tilt_from_nadir(r) == pytest.approx(0.0, abs=1e-9)
    assert orient.up_azimuth(r) == pytest.approx(0.0, abs=1e-9) or orient.up_azimuth(r) == pytest.approx(360.0)


@pytest.mark.parametrize("heading", [0.0, 37.0, 90.0, 120.0, 180.0, 255.0, 300.0, 359.0])
@pytest.mark.parametrize("group", ["A", "B"])
def test_the_picture_always_points_along_the_flight_and_each_lens_keeps_its_side(heading, group):
    """Independent of the angle formulas: the top of the picture follows the heading, left lens looks left, right lens right, 22.5 degrees off nadir."""
    for lens, side in (("L", -1), ("R", 1)):
        _angles, r = nominal(lens, heading, group)
        assert r is not None
        assert abs(geo.angle_difference(orient.up_azimuth(r), heading)) < 1e-6
        assert orient.tilt_from_nadir(r) == pytest.approx(22.5, abs=1e-6)
        assert orient.side_of_travel(r, heading) == side
        right = orient.image_right(r)                      # the right of the picture is the right of the flight
        assert geo.angle_difference(orient.horizontal_azimuth(right), heading + 90.0) == pytest.approx(0.0, abs=1e-6)


@pytest.mark.parametrize("heading", [10.0, 200.0])
def test_the_two_lenses_are_45_degrees_apart_around_the_picture_vertical_axis(heading):
    for group in ("A", "B"):
        _a, left = nominal("L", heading, group)
        _b, right = nominal("R", heading, group)
        assert orient.angle_between(left, right) == pytest.approx(45.0, abs=1e-9)
        rel = orient.mul(orient.transpose(left), right)
        for row, want in zip(rel, orient.rig_rotation()):
            assert row == pytest.approx(want, abs=1e-9)


@pytest.mark.parametrize("group", ["A", "B"])
def test_gimbal_angles_plus_the_lens_tilt_give_the_camera_angles(group):
    angles, camera = nominal("L", 77.0, group)
    only_gimbal = {k: v for k, v in angles.items() if k.startswith("gimbal")}
    fallback, source = orient.nominal_orientation(only_gimbal, "L")
    assert source == "gimbal" and orient.angle_between(camera, fallback) < 1e-6


def test_raw_gimbal_angles_are_not_the_camera_orientation():
    """Reading the gimbal angles as the camera (what a generic importer does) loses the 22.5 degree tilt of each lens."""
    angles, camera = nominal("L", 77.0, "B")
    raw = orient.dji_matrix(angles["gimbal_yaw"], angles["gimbal_pitch"], angles["gimbal_roll"])
    assert orient.angle_between(raw, camera) == pytest.approx(22.5, abs=1e-6)
    assert orient.tilt_from_nadir(raw) == pytest.approx(0.0, abs=1e-6)


def test_a_wrong_sign_would_flip_the_picture():
    """Guards the convention: the same angles with the roll sign changed turn the picture round."""
    _a, good = nominal("L", 120.0, "B")
    bad = orient.dji_matrix(29.8, -67.5, -90.0)
    assert abs(geo.angle_difference(orient.up_azimuth(good), orient.up_azimuth(bad))) > 90.0


def test_missing_angles_give_no_orientation():
    assert orient.nominal_orientation({}, "L") == (None, "")
    assert orient.nominal_orientation({"gimbal_yaw": 1.0, "gimbal_pitch": -90.0, "gimbal_roll": None}, "L") == (None, "")
    assert orient.nominal_orientation({"gimbal_yaw": 1.0, "gimbal_pitch": -90.0, "gimbal_roll": 180.0}, "") == (None, "")


def test_rotation_helpers_are_consistent():
    r = orient.mul(orient.rot_z(30), orient.mul(orient.rot_y(-20), orient.rot_x(70)))
    assert orient.angle_between(r, r) == pytest.approx(0.0, abs=1e-6)
    assert orient.mul(r, orient.transpose(r))[0][0] == pytest.approx(1.0)
    assert orient.apply(orient.rot_z(90), (1, 0, 0)) == pytest.approx((0, 1, 0), abs=1e-12)
    assert orient.as_matrix([[1, 0, 0], [0, 1, 0], [0, 0, 1]]) == orient.IDENTITY


# --------------------------------------------------------------------------------------------------
# Geodesy
# --------------------------------------------------------------------------------------------------

def test_geodesy_helpers():
    a, b = (48.8, 2.3, 100.0), (48.8, 2.3, 130.0)
    assert geo.distance(a, b) == pytest.approx(30.0, abs=1e-6)
    e, n, u = geo.enu_of(a, (48.8009, 2.3, 100.0))
    assert n == pytest.approx(100.0, abs=0.3) and abs(e) < 0.1
    assert geo.azimuth(1, 1) == pytest.approx(45.0) and geo.azimuth(-1, 0) == pytest.approx(270.0)
    assert geo.angle_difference(10, 350) == pytest.approx(20.0) and geo.angle_difference(350, 10) == pytest.approx(-20.0)


# --------------------------------------------------------------------------------------------------
# Scan
# --------------------------------------------------------------------------------------------------

@pytest.fixture
def mission(tmp_path):
    """A mission folder: two nadir flights (opposite directions), one calibration flight of one shot, a stray picture and a broken file."""
    synth.write_flight(tmp_path, "DJI_203001011200_009_TESTSITE-FLIGHT01", shots=8, heading=120.0, group="B")
    synth.write_flight(tmp_path, "DJI_203001011230_011_TESTSITE-FLIGHT02", shots=8, heading=300.0, group="A", lat=48.8005, start=synth.GPS0 + datetime.timedelta(minutes=30))
    synth.write_flight(tmp_path, "DJI_203001011200_010_TESTSITE-SIDE", shots=1, heading=300.0, group="A", with_mrk=False, with_lidar=False)
    (tmp_path / "stray.jpg").write_bytes(synth.make_jpeg(make="Canon", model="X", tags={}, with_xmp=False))
    (tmp_path / "broken.jpg").write_bytes(b"\xff\xd8garbage")
    (tmp_path / "notes.txt").write_text("hello")
    return tmp_path


def test_scan_finds_flights_photos_and_companion_files(mission):
    result = scan.scan_folder(str(mission))
    assert [f.name for f in result.flights] == ["DJI_203001011200_009_TESTSITE-FLIGHT01", "DJI_203001011200_010_TESTSITE-SIDE", "DJI_203001011230_011_TESTSITE-FLIGHT02"]
    sizes = {f.name: len(f.photos) for f in result.flights}
    assert sizes["DJI_203001011200_009_TESTSITE-FLIGHT01"] == 16 and sizes["DJI_203001011200_010_TESTSITE-SIDE"] == 2
    assert len(result.records) == 34
    assert len(result.foreign) == 1 and len(result.unreadable) == 1 and "broken" in result.unreadable[0][0]
    first = result.flights[0]
    assert first.has_mrk and len(first.mrk_shots) == 16 and first.lidar_files == 3
    assert not result.flights[1].has_mrk
    rec = first.photos[0]
    assert rec.lens == "L" and rec.rtk_name == "fix" and rec.width == 6144 and rec.serial == synth.SERIAL_L
    assert rec.gps_time == synth.GPS0 and rec.has_position and rec.stem.endswith("_0003")
    assert rec.angles["camera_roll"] == 90.0 and rec.dewarp_data == synth.LV_DEWARP


def test_photos_are_sorted_by_time_inside_a_flight(mission):
    flight = scan.scan_folder(str(mission)).flights[0]
    times = [r.gps_time for r in flight.photos]
    assert times == sorted(times)
    assert [r.lens for r in flight.photos[:4]] == ["L", "R", "L", "R"]


def test_lens_sorting_folders_belong_to_the_flight_of_their_parent(tmp_path):
    synth.write_flight(tmp_path, "DJI_203001011200_009_TESTSITE-FLIGHT01", shots=5, lens_folders=True)
    result = scan.scan_folder(str(tmp_path))
    assert len(result.flights) == 1 and len(result.flights[0].photos) == 10
    assert result.flights[0].has_mrk
    assert scan.flight_key(os.path.join("a", "flight", "LV", "x_LV.JPG")) == os.path.join("a", "flight")


def test_scan_reports_progress_and_can_be_cancelled(mission):
    seen = []
    scan.scan_folder(str(mission), progress=lambda f, t: seen.append(f))
    assert seen[0] == 0.0 and seen[-1] == 1.0 and seen == sorted(seen)
    calls = {"n": 0}

    def cancelled():
        calls["n"] += 1
        return calls["n"] > 3

    partial = scan.scan_folder(str(mission), cancelled=cancelled)
    assert partial.cancelled is True


def test_scan_of_an_empty_or_unreadable_folder(tmp_path):
    result = scan.scan_folder(str(tmp_path))
    assert result.records == [] and result.flights == []
    result = scan.scan_folder(str(tmp_path / "does_not_exist"))
    assert result.records == []


def test_a_reader_that_fails_is_reported_not_raised(mission):
    def reader(path):
        raise RuntimeError("disk error")
    result = scan.scan_folder(str(mission), reader=reader)
    assert len(result.unreadable) == 36 and "disk error" in result.unreadable[0][1]


# --------------------------------------------------------------------------------------------------
# Plan
# --------------------------------------------------------------------------------------------------

def scanned(tmp_path, **kwargs):
    synth.write_flight(tmp_path, **kwargs)
    return scan.scan_folder(str(tmp_path))


def test_plan_pairs_the_lenses_left_first(tmp_path):
    result = scanned(tmp_path, shots=6)
    p = plan.build_plan(result.records)
    assert len(p.groups) == 6 and p.photo_count == 12 and not p.singles
    for left, right in p.groups:
        assert left.endswith("_LV.JPG") and right.endswith("_RV.JPG") and left[:-7] == right[:-7]
    assert set(p.sensors) == {"L", "R"} and p.sensors["R"].rotation is not None and p.sensors["L"].rotation is None
    assert p.sensors["L"].calibration.cx == 10.0 and p.sensors["R"].calibration.cx == -12.0
    assert p.sensors["L"].pixel_mm == pytest.approx(0.0030238560267857144)
    assert p.size == (6144, 4096) and p.excluded == []


def test_plan_references_and_starting_orientation(tmp_path):
    result = scanned(tmp_path, shots=4, heading=120.0, group="B")
    options = plan.Options(z_shift=0.25, accuracy_fixed=0.04)
    p = plan.build_plan(result.records, options)
    spec = p.cameras[p.groups[0][0]]
    assert spec.accuracy == (0.04, 0.04, 0.04)
    assert spec.alt == pytest.approx(200.0 + 0.25)
    assert spec.rotation_source == "camera" and spec.rotation_accuracy == 10.0
    assert abs(geo.angle_difference(orient.up_azimuth(spec.rotation), 120.0)) < 1e-6
    assert orient.tilt_from_nadir(spec.rotation) == pytest.approx(22.5, abs=1e-6)


def test_accuracy_follows_the_rtk_status(tmp_path):
    def tags_for(i, lens, tags):
        tags["RtkFlag"] = {0: "50", 1: "34", 2: "16", 3: "0"}[i]
        return tags
    result = scanned(tmp_path, shots=4, tags_for=tags_for)
    p = plan.build_plan(result.records, plan.Options(accuracy_fixed=0.05, accuracy_float=0.25, accuracy_single=5.0))
    values = [p.cameras[g[0]].accuracy[0] for g in p.groups]
    assert values == [0.05, 0.25, 5.0, 5.0]


def test_photos_without_position_get_no_reference(tmp_path):
    def tags_for(i, lens, tags):
        if i == 1:
            tags["GpsLatitude"] = tags["GpsLongitude"] = "+0.000000000"
        return tags
    result = scanned(tmp_path, shots=3, tags_for=tags_for)
    p = plan.build_plan(result.records)
    assert p.cameras[p.groups[1][0]].accuracy is None and p.cameras[p.groups[0][0]].accuracy is not None


def test_unpaired_photos_are_left_out_unless_asked(tmp_path):
    result = scanned(tmp_path, shots=4)
    right_only = [r for r in result.records if not (r.lens == "R" and r.stem.endswith("_0004"))]
    p = plan.build_plan(right_only)
    assert len(p.groups) == 3 and p.photo_count == 6
    assert [rec.name for rec, why in p.excluded if why == "unpaired"] == [r.name for r in right_only if r.stem.endswith("_0004")]
    q = plan.build_plan(right_only, plan.Options(include_unpaired=True))
    assert len(q.groups) == 3 and len(q.singles) == 1 and q.photo_count == 7


def test_a_lone_right_photo_never_goes_in_the_pair_list(tmp_path):
    """Metashape's multi-camera layout would put a lone photo on the master (left lens) sensor with the wrong calibration."""
    result = scanned(tmp_path, shots=3)
    only_right = [r for r in result.records if not (r.lens == "L" and r.stem.endswith("_0003"))]
    q = plan.build_plan(only_right, plan.Options(include_unpaired=True))
    assert all(len(g) == 2 and g[0].endswith("_LV.JPG") for g in q.groups)
    assert q.singles and q.singles[0].endswith("_0003_RV.JPG")


def test_wrong_size_rotated_and_duplicate_photos_are_excluded_with_a_reason(tmp_path):
    def files_for(i, lens, kwargs):
        if i == 1 and lens == "L":
            kwargs["orientation"] = 6                    # EXIF says: turn it
        if i == 2 and lens == "R":
            kwargs["width"], kwargs["height"] = 4096, 6144       # the picture itself was turned
        return kwargs
    result = scanned(tmp_path, shots=5, files_for=files_for)
    p = plan.build_plan(result.records + [result.records[0]])
    reasons = {rec.name: why for rec, why in p.excluded}
    assert sorted(reasons.values()).count("orientation") == 1 and sorted(reasons.values()).count("size") == 1
    assert "duplicate" in reasons.values()
    assert len(p.groups) == 3                                     # the two shots touched by a bad photo lose their pair... and the survivors are unpaired
    assert p.excluded_counts()["unpaired"] == 2


def test_two_different_l3_units_are_not_mixed(tmp_path):
    synth.write_flight(tmp_path, "DJI_203001011200_009_A", shots=3)
    other = tmp_path / "other"
    other.mkdir()
    synth.write_flight(other, "DJI_203001011300_011_B", shots=3, start=synth.GPS0 + datetime.timedelta(hours=1),
                       tags_for=lambda i, lens, t: dict(t, CameraSerialNumber="OTHER-" + lens))
    result = scan.scan_folder(str(tmp_path))
    analysis = checks.analyse(result.flights)
    assert analysis.has("mixed_units") and not analysis.ok
    assert analysis.plan.excluded_counts()["other_unit"] + analysis.plan.excluded_counts()["unpaired"] >= 3 and analysis.plan.excluded_counts()["other_unit"] in (6, 6)


def test_photos_that_are_not_from_an_l3_are_left_out(tmp_path):
    result = scanned(tmp_path, shots=3, tags_for=lambda i, lens, t: dict(t, ProductName="X9"), files_for=lambda i, lens, k: dict(k, model="X9"))
    analysis = checks.analyse(result.flights)
    assert analysis.has("not_l3") and not analysis.ok and analysis.has("nothing_to_do")


def test_options_round_trip_and_bad_data():
    options = plan.Options(accuracy_fixed=0.03, frame="etrs89", steps={"align": True, "repair": True, "optimize": False, "dem": False, "ortho": False})
    again = plan.Options.from_dict(options.as_dict())
    assert again.as_dict() == options.as_dict() and again.crs == "EPSG::4258"
    bad = plan.Options.from_dict({"accuracy_fixed": "x", "frame": "mars", "steps": {"align": 1, "nonsense": True}, "include_unpaired": 3, "quick_test_photos": 12.7})
    assert bad.accuracy_fixed == 0.05 and bad.frame == "wgs84" and bad.include_unpaired is False and bad.quick_test_photos == 12
    assert bad.steps["align"] is True and "nonsense" not in bad.steps
    assert plan.Options.from_dict("junk").as_dict() == plan.Options().as_dict()
    with pytest.raises(TypeError):
        plan.Options(nope=1)


def test_quick_test_picks_consecutive_complete_stations_from_the_middle(tmp_path):
    result = scanned(tmp_path, shots=30)
    p = plan.build_plan(result.records)
    chosen = plan.pick_test_stations(p, 12)
    assert len(chosen) == 12
    stems = sorted({os.path.basename(c)[:-7] for c in chosen})
    all_stems = sorted({os.path.basename(g[0])[:-7] for g in p.groups})
    start = all_stems.index(stems[0])
    assert stems == all_stems[start:start + 6] and 5 <= start <= 19
    assert plan.pick_test_stations(plan.build_plan([]), 12) == []


# --------------------------------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------------------------------

def analysed(tmp_path, options=None, **kwargs):
    synth.write_flight(tmp_path, **kwargs)
    result = scan.scan_folder(str(tmp_path))
    return checks.analyse(result.flights, options), result


def test_a_clean_flight_has_no_warning(tmp_path):
    analysis, _ = analysed(tmp_path, shots=10, heading=120.0, group="B")
    assert analysis.ok
    assert [f.code for f in analysis.findings if f.level != checks.INFO] == []
    assert analysis.passed == [key for key, _codes in checks.TOPICS]               # every topic passed: nothing to show but the count
    s = analysis.summary
    assert s.photos == 20 and s.pairs == 10 and s.lenses == ["L", "R"] and s.rtk["fix"] == 20
    assert s.site == "TESTSITE" and s.date == "300101" and checks.default_chunk_name(s) == "L3_TESTSITE_300101"
    assert s.first_utc == datetime.datetime(2030, 1, 1, 11, 5, 54, 442935)          # GPS - 18 s
    assert s.tilt_min == pytest.approx(22.5, abs=1e-6) and s.mean_v_mm == pytest.approx(217.0)


@pytest.mark.parametrize("heading,group", [(120.0, "B"), (300.0, "A"), (10.0, "B"), (200.0, "A")])
def test_both_flight_directions_pass_the_flip_checks(tmp_path, heading, group):
    analysis, _ = analysed(tmp_path, shots=10, heading=heading, group=group)
    assert not analysis.has("against_track") and not analysis.has("lens_side") and not analysis.has("rig_angle")


def test_a_picture_that_would_come_out_turned_round_is_found(tmp_path):
    def tags_for(i, lens, tags):
        if i in (4, 5):        # camera angles of the opposite flight direction on a shot of this flight: the top of the picture points backwards
            other = synth.photo_tags(lens, 120.0 + 180.0, "A", 48.8, 2.3, 200.0, synth.GPS0)
            for k in ("CameraYawDegree", "CameraPitchDegree", "CameraRollDegree"):
                tags[k] = other[k]
        return tags
    analysis, _ = analysed(tmp_path, shots=10, heading=120.0, group="B", tags_for=tags_for)
    found = analysis.by_code("against_track")
    assert found and found[0].n >= 2 and found[0].level == checks.WARNING


def test_lenses_that_look_to_the_wrong_side_are_found(tmp_path):
    def tags_for(i, lens, tags):
        other = synth.photo_tags("R" if lens == "L" else "L", 120.0, "B", 48.8, 2.3, 200.0, synth.GPS0)
        for k in ("CameraYawDegree", "CameraPitchDegree", "CameraRollDegree"):
            tags[k] = other[k]
        return tags
    analysis, _ = analysed(tmp_path, shots=10, heading=120.0, group="B", tags_for=tags_for)
    assert analysis.has("lens_side")
    assert analysis.has("rig_angle") is False          # the two lenses are still 45 degrees apart: only the sides are swapped


def test_a_wrong_rig_angle_and_a_pair_time_gap_are_found(tmp_path):
    def tags_for(i, lens, tags):
        if lens == "R" and i == 3:
            tags["CameraPitchDegree"] = "-100.00"
            tags["UTCAtExposure"] = "2030-01-01T11:06:15.000000"
        return tags
    analysis, _ = analysed(tmp_path, shots=10, tags_for=tags_for)
    assert analysis.has("rig_angle") and analysis.has("pair_gap")


def test_missing_camera_angles_are_replaced_by_the_gimbal_angles(tmp_path):
    analysis, _ = analysed(tmp_path, shots=6, tags_for=lambda i, lens, t: {k: v for k, v in t.items() if not k.startswith("Camera") or k == "CameraSerialNumber"})
    assert not analysis.has("angles_missing")
    assert analysis.by_code("orientation_ready")[0].fields["source"] == "mixed"
    assert not analysis.has("against_track") and not analysis.has("lens_side")      # the fallback gives the same geometry


def test_photos_with_no_angles_at_all_are_reported(tmp_path):
    analysis, _ = analysed(tmp_path, shots=6, tags_for=lambda i, lens, t: {k: v for k, v in t.items() if "Degree" not in k or k.startswith("Flight")})
    assert analysis.has("angles_missing") and analysis.by_code("angles_missing")[0].n == 12


def test_camera_and_gimbal_angles_that_disagree_are_reported(tmp_path):
    analysis, _ = analysed(tmp_path, shots=6, tags_for=lambda i, lens, t: dict(t, GimbalYawDegree="-10.00") if i == 2 else t)
    assert analysis.has("angles_inconsistent") and analysis.by_code("angles_inconsistent")[0].n == 2


def test_rtk_float_barometric_and_no_gps_are_reported(tmp_path):
    def tags_for(i, lens, tags):
        if i == 1:
            tags["RtkFlag"] = "34"
        if i == 2:
            tags["AltitudeType"] = "BaroAlt"
        if i == 3:
            tags["GpsStatus"] = "GPS"
        return tags
    analysis, _ = analysed(tmp_path, shots=6, tags_for=tags_for)
    rtk = analysis.by_code("rtk_not_fixed")[0]
    assert rtk.n == 2 and rtk.fields["flt"] == 2 and rtk.fields["fix"] == 10
    assert analysis.has("altitude_type") and analysis.has("gps_not_rtk") and analysis.ok
    (tmp_path / "x").mkdir()
    nogps, _ = analysed(tmp_path / "x", shots=3, tags_for=lambda i, lens, t: dict(t, GpsLatitude="+0.0", GpsLongitude="+0.0"))
    assert nogps.has("no_gps") and nogps.by_code("no_gps")[0].level == checks.ERROR


def test_a_position_jump_is_found(tmp_path):
    def tags_for(i, lens, tags):
        if i == 4:
            tags["GpsLatitude"] = "%+.9f" % 48.9
        return tags
    analysis, _ = analysed(tmp_path, shots=8, tags_for=tags_for)
    assert analysis.has("position_jump")


def test_mrk_mismatch_and_missing_mrk(tmp_path):
    analysis, _ = analysed(tmp_path, shots=4, with_mrk=False)
    assert analysis.has("mrk_missing")
    (tmp_path / "m").mkdir()
    analysis, _ = analysed(tmp_path / "m", shots=4, tags_for=lambda i, lens, t: dict(t, GpsLongitude="%+.9f" % 2.31) if i == 1 else t)
    assert analysis.has("mrk_mismatch")


def test_oblique_calibration_flights_start_unticked(tmp_path):
    def tags_for(i, lens, tags):
        tags.update(GimbalPitchDegree="+0.00", GimbalRollDegree="+0.00", CameraPitchDegree="+0.00", CameraRollDegree="+0.00")
        tags["CameraYawDegree"] = "%+.2f" % (float(tags["GimbalYawDegree"]) + (-22.5 if lens == "L" else 22.5))
        return tags
    synth.write_flight(tmp_path, "DJI_203001011200_009_TESTSITE-MAP", shots=8)
    synth.write_flight(tmp_path, "DJI_203001011254_016_TESTSITE-BIASED", shots=8, tags_for=tags_for, start=synth.GPS0 + datetime.timedelta(hours=1))
    synth.write_flight(tmp_path, "DJI_203001011300_017_TESTSITE-TINY", shots=2, start=synth.GPS0 + datetime.timedelta(hours=2))
    result = scan.scan_folder(str(tmp_path))
    selection = checks.default_selection(result.flights)
    names = {f.name.split("_")[-1]: selection[f.key] for f in result.flights}
    assert names == {"TESTSITE-MAP": True, "TESTSITE-BIASED": False, "TESTSITE-TINY": False}
    analysis = checks.analyse(result.flights)
    assert analysis.has("oblique") and analysis.has("tiny_flights")
    assert analysis.summary.tilt_max > 80.0
    row = checks.flight_row(result.flights[1])
    assert row["pairs"] == 8 and row["tilt"] > 80.0 and row["mrk"] and row["fix"] == 1.0


def test_default_selection_never_leaves_nothing_ticked(tmp_path):
    synth.write_flight(tmp_path, "DJI_203001011200_009_TESTSITE-TINY", shots=2)
    result = scan.scan_folder(str(tmp_path))
    assert checks.default_selection(result.flights) == {result.flights[0].key: True}


def test_nothing_ticked_gives_a_blocking_message():
    analysis = checks.analyse([])
    assert analysis.has("no_photos") and not analysis.ok
    assert checks.analyse([]).by_code("no_photos")[0].short() == "No photo ticked" and checks.analyse([]).passed == []


def test_unpaired_and_unusual_files_produce_messages(tmp_path):
    def files_for(i, lens, kwargs):
        if i == 1 and lens == "L":
            kwargs["orientation"] = 8
        return kwargs
    analysis, _ = analysed(tmp_path, shots=5, files_for=files_for)
    assert analysis.has("orientation") and analysis.has("unpaired") and analysis.ok
    assert "files" not in analysis.passed and "pairs" not in analysis.passed          # a topic with a finding is not passed
    finding = analysis.by_code("orientation")[0]
    assert finding.short() == "Rotated by EXIF" and "left out" in finding.detail().lower()
    assert finding.tip().splitlines()[-1].endswith("_LV.JPG") and finding.count_text() == "1"


def test_every_finding_has_a_text(tmp_path):
    from aerotools import i18n
    from aerotools.lang import en
    codes = {"no_photos", "not_l3", "mixed_units", "size", "orientation", "lens", "calibration", "duplicate_names", "unpaired", "unpaired_included", "lens_conflict",
             "dewarped", "nothing_to_do", "no_gps", "rtk_not_fixed", "gps_not_rtk", "altitude_type", "orientation_ready", "angles_missing",
             "angles_inconsistent", "oblique", "rig_angle", "pair_gap", "against_track", "lens_side", "position_jump", "mrk_missing", "mrk_mismatch",
             "tiny_flights"}
    for code in codes:
        short, detail = en.TEXTS["l3.check." + code + ".short"], en.TEXTS["l3.check." + code + ".detail"]
        assert len(short.split()) <= 6 and detail.count(". ") <= 1 and len(detail) <= 120, code
    for key, topic_codes in checks.TOPICS:
        assert "l3.pass." + key in en.TEXTS and set(topic_codes) <= codes
    source = open(os.path.join(os.path.dirname(HERE), "aerotools", "tools", "aerol3", "checks.py"), encoding="utf-8").read()
    import re
    used = set(re.findall(r'_add\(findings, (?:ERROR|WARNING|INFO|ERROR if [^"]*?else WARNING), "(\w+)"', source))
    assert used <= codes


# --------------------------------------------------------------------------------------------------
# Geometry check after alignment
# --------------------------------------------------------------------------------------------------

def test_flipped_cameras_are_told_from_good_ones():
    entries = [("a", 1.2), ("b", 2.0), ("c", 179.0), ("d", None), ("e", 41.0), ("f", 3.3)]
    report = geomcheck.alignment_report(entries, lens_of={"a": "L", "b": "R", "c": "L", "e": "R", "f": "L", "d": "L"})
    assert report.total == 6 and report.aligned == 5
    assert [n for n, _a in report.flipped] == ["c", "e"] and not report.clean
    assert report.maximum == 179.0 and report.lens_median["L"] == pytest.approx(3.3) and report.lens_median["R"] == pytest.approx(21.5)
    clean = geomcheck.alignment_report([("a", 1.0), ("b", 3.3)])
    assert clean.clean and geomcheck.alignment_report([]).median is None
    assert geomcheck.FLIP_DEG > 3.3 * 3 and geomcheck.FLIP_DEG < 40.0                # between the real noise (3.3) and a real flip (40 and more)
    assert geomcheck.tilt_summary([orient.dji_matrix(0, -90, 0)]) == pytest.approx((0.0, 0.0, 0.0), abs=1e-6)
    assert geomcheck.tilt_summary([]) is None
