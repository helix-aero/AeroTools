"""Aero-L3 Metashape adapter, with a small fake Metashape (no Metashape, no Qt, no display).

The fake keeps what the adapter writes (sensors, calibrations, references, orientation) and aligns cameras by giving them the pose their
reference angles ask for, except for the cameras a test marks as flipped.
"""

import datetime
import math
import os
import sys
import types

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import aerol3_synth as synth  # noqa: E402
from aerotools.tools.aerol3 import checks, geo, geomcheck, msl3, orient, plan, scan  # noqa: E402
from aerotools.tools.aerol3.backend import Cancelled  # noqa: E402


# --------------------------------------------------------------------------------------------------
# Fake Metashape
# --------------------------------------------------------------------------------------------------

class Vector(object):
    def __init__(self, values=(0, 0, 0)):
        self.v = [float(x) for x in values]

    def __getitem__(self, i):
        return self.v[i]

    def __iter__(self):
        return iter(self.v)

    def __len__(self):
        return len(self.v)


class Matrix(object):
    def __init__(self, rows):
        self.a = np.array(rows, dtype=float)

    def __getitem__(self, ij):
        return self.a[ij]

    def __mul__(self, other):
        return Matrix(self.a @ other.a)

    def mulp(self, p):
        q = self.a @ np.array(list(p) + [1.0])
        return Vector(q[:3])


def ecef_to_geodetic(p):
    x, y, z = p
    lon = math.atan2(y, x)
    r = math.hypot(x, y)
    lat = math.atan2(z, r * (1 - geo.E2))
    for _ in range(6):
        n = geo.A / math.sqrt(1 - geo.E2 * math.sin(lat) ** 2)
        lat = math.atan2(z + geo.E2 * n * math.sin(lat), r)
    return math.degrees(lat), math.degrees(lon)


class CoordinateSystem(object):
    def __init__(self, code=""):
        self.code = code

    def localframe(self, p):
        lat, lon = ecef_to_geodetic(list(p))
        e, n, u = geo.enu_axes(lat, lon)
        m = np.eye(4)
        m[0, :3], m[1, :3], m[2, :3] = e, n, u
        return Matrix(m)


class Calibration(object):
    def __init__(self):
        self.width = self.height = 0
        self.f = self.cx = self.cy = self.k1 = self.k2 = self.k3 = self.p1 = self.p2 = 0.0


class Sensor(object):
    def __init__(self, key, master=None):
        self.key, self.label, self.master = key, "sensor%d" % key, master or self
        self.width, self.height = 6144, 4096
        self.user_calib = None
        self.calibration = None
        self.fixed_calibration = False
        self.pixel_size = None
        self.focal_length = 0.0
        self.rotation = Matrix(np.eye(3))


class Ref(object):
    def __init__(self):
        self.location = None
        self.location_accuracy = None
        self.accuracy = None
        self.rotation = None
        self.rotation_accuracy = None
        self.rotation_enabled = False
        self.enabled = False


class Photo(object):
    def __init__(self, path):
        self.path = path


class Camera(object):
    def __init__(self, key, path, sensor):
        self.key, self.photo, self.sensor = key, Photo(path), sensor
        self.label = os.path.splitext(os.path.basename(path))[0]
        self.reference = Ref()
        self.orientation = 1
        self.transform = None
        self.center = None
        self.enabled = True


class Chunk(object):
    def __init__(self, doc, key):
        self.doc, self.key, self.label = doc, key, "Chunk %d" % key
        self.crs = None
        self.cameras, self.sensors = [], []
        self.transform = types.SimpleNamespace(matrix=Matrix(np.eye(4)))
        self.calls = []
        self.flip = set()               # labels the fake alignment puts in a flipped pose
        self.stubborn = set()           # labels that stay flipped even when aligned again
        self.fail = None
        self.camera_location_accuracy = None
        self.camera_rotation_accuracy = None
        self.rejects_flags = False
        self.cancel_in_add = None
        self.merge_flat = False

    def _progress(self, progress):
        if progress:
            for pct in (0, 40, 100):
                progress(pct)

    def addPhotos(self, filenames=None, filegroups=None, layout=None, progress=None, **flags):
        if self.rejects_flags and flags:
            raise TypeError("unexpected keyword")
        self.calls.append(("addPhotos", dict(filenames=list(filenames), filegroups=list(filegroups or []), layout=layout, **flags)))
        if self.fail == "addPhotos":
            raise RuntimeError("disk error")
        self._progress(progress)
        if self.cancel_in_add:
            self.cancel_in_add()
            self._progress(progress)
        if layout == "multiplane":
            master = Sensor(len(self.sensors))
            slave = Sensor(len(self.sensors) + 1, master)
            self.sensors += [master, slave]
            i = 0
            for size in filegroups:
                for j in range(size):
                    self.cameras.append(Camera(len(self.cameras), filenames[i], master if j == 0 else slave))
                    i += 1
        else:
            if self.merge_flat and self.sensors:
                sensor = self.sensors[0]                 # Metashape may reuse a sensor that looks the same
            else:
                sensor = Sensor(len(self.sensors))
                self.sensors.append(sensor)
            for path in filenames:
                self.cameras.append(Camera(len(self.cameras), path, sensor))

    def addSensor(self):
        sensor = Sensor(len(self.sensors))
        self.sensors.append(sensor)
        return sensor

    def copy(self):
        other = Chunk(self.doc, self.key + 100 + len(self.doc.chunks))
        other.crs = self.crs
        other.sensors = self.sensors
        for c in self.cameras:
            n = Camera(c.key, c.photo.path, c.sensor)
            n.reference = c.reference
            n.orientation = c.orientation
            other.cameras.append(n)
        other.flip, other.stubborn = set(self.flip), set(self.stubborn)
        self.doc.chunks.append(other)
        self.calls.append(("copy",))
        return other

    def matchPhotos(self, progress=None, **kwargs):
        self.calls.append(("matchPhotos", kwargs))
        if self.fail == "matchPhotos":
            raise RuntimeError("out of memory")
        self._progress(progress)

    def _pose(self, cam, flipped):
        ref = cam.reference
        gl = np.array(msl3_utils_ypr2mat(ref.rotation).a)
        ms = np.array([[gl[i][0], -gl[i][1], -gl[i][2]] for i in range(3)])      # camera -> ENU in Metashape's camera frame
        if flipped:
            ms = ms @ np.array([[-1, 0, 0], [0, 1, 0], [0, 0, -1.0]])               # turned half a turn about the picture's vertical axis
        lon, lat, alt = ref.location
        e, n, u = geo.enu_axes(lat, lon)
        b = np.vstack([e, n, u])                                                    # ECEF -> ENU
        t = np.eye(4)
        t[:3, :3] = b.T @ ms
        t[:3, 3] = geo.to_ecef(lat, lon, alt)
        cam.transform = Matrix(t)
        cam.center = Vector(t[:3, 3])

    def alignCameras(self, cameras=None, reset_alignment=True, progress=None, **kwargs):
        self.calls.append(("alignCameras", dict(cameras=[c.label for c in cameras] if cameras else None, reset_alignment=reset_alignment, **kwargs)))
        self._progress(progress)
        full = cameras is None
        for cam in (cameras if cameras is not None else self.cameras):
            if cam.enabled and cam.reference.rotation is not None:
                # a full alignment flips the cameras of `flip`; aligning chosen cameras again only fails for the `stubborn` ones
                self._pose(cam, (cam.label in self.flip) if full else (cam.label in self.stubborn))

    def optimizeCameras(self, progress=None, **kwargs):
        self.calls.append(("optimizeCameras", kwargs))
        self._progress(progress)

    def buildDepthMaps(self, progress=None, **kwargs):
        self.calls.append(("buildDepthMaps", kwargs))
        self._progress(progress)

    def buildDem(self, progress=None, **kwargs):
        self.calls.append(("buildDem", kwargs))
        self._progress(progress)

    def buildOrthomosaic(self, progress=None, **kwargs):
        self.calls.append(("buildOrthomosaic", kwargs))
        self._progress(progress)


def _mat2ypr(m):
    a = np.array([[m[i, j] for j in range(3)] for i in range(3)])
    pitch = math.asin(max(-1, min(1, a[2][1])))
    roll = math.atan2(-a[2][0], a[2][2])
    yaw = math.atan2(a[0][1], a[1][1])
    return Vector([math.degrees(yaw), math.degrees(pitch), math.degrees(roll)])


def msl3_utils_ypr2mat(ypr):
    y, p, r = [math.radians(float(x)) for x in ypr]
    rz = np.array([[math.cos(y), math.sin(y), 0], [-math.sin(y), math.cos(y), 0], [0, 0, 1.0]])
    rx = np.array([[1, 0, 0], [0, math.cos(p), -math.sin(p)], [0, math.sin(p), math.cos(p)]])
    ry = np.array([[math.cos(r), 0, math.sin(r)], [0, 1, 0], [-math.sin(r), 0, math.cos(r)]])
    return Matrix(rz @ rx @ ry)


class Doc(object):
    def __init__(self):
        self.chunks = []
        self.path = ""
        self.chunk = None
        self.saved = False

    def addChunk(self):
        c = Chunk(self, len(self.chunks) + 1)
        self.chunks.append(c)
        self.chunk = c
        return c

    def remove(self, chunk):
        self.chunks.remove(chunk)

    def save(self, *a, **k):
        self.saved = True


def make_fake(doc):
    mod = types.ModuleType("Metashape")
    mod.Vector, mod.Matrix, mod.CoordinateSystem, mod.Calibration = Vector, Matrix, CoordinateSystem, Calibration
    mod.MultiplaneLayout, mod.FlatLayout = "multiplane", "flat"
    mod.ReferencePreselectionSource = "source"
    mod.ElevationData, mod.DepthMapsData, mod.MosaicBlending, mod.MildFiltering = "dem", "depth", "mosaic", "mild"
    mod.OrthoProjection = type("OrthoProjection", (), {"crs": None})
    mod.utils = types.SimpleNamespace(mat2ypr=_mat2ypr, ypr2mat=msl3_utils_ypr2mat)
    mod.app = types.SimpleNamespace(document=doc)
    return mod


@pytest.fixture
def world(tmp_path, monkeypatch):
    doc = Doc()
    mod = make_fake(doc)
    monkeypatch.setattr(msl3, "Metashape", mod)
    synth.write_flight(tmp_path, "DJI_203001011200_009_TESTSITE-FLIGHT01", shots=8, heading=120.0, group="B")
    synth.write_flight(tmp_path, "DJI_203001011230_011_TESTSITE-FLIGHT02", shots=8, heading=300.0, group="A", lat=48.8005, start=synth.GPS0 + datetime.timedelta(minutes=30))
    result = scan.scan_folder(str(tmp_path))
    analysis = checks.analyse(result.flights, plan.Options(chunk_name="L3_TESTSITE_300101"))
    backend = msl3.MetashapeL3Backend()
    return types.SimpleNamespace(doc=doc, mod=mod, plan=analysis.plan, backend=backend, result=result, tmp=tmp_path)


def run(world, steps=(), cancelled=None, progress=None):
    return world.backend.run(world.plan, list(steps), progress, cancelled)


# --------------------------------------------------------------------------------------------------
# Rotation glue
# --------------------------------------------------------------------------------------------------

def test_yaw_pitch_roll_round_trip_gives_the_same_orientation_back(world):
    for heading, group, lens in ((120.0, "B", "L"), (300.0, "A", "R"), (10.0, "B", "R")):
        tags = synth.photo_tags(lens, heading, group, 48.8, 2.3, 200.0, synth.GPS0)
        angles = {"camera_yaw": float(tags["CameraYawDegree"]), "camera_pitch": float(tags["CameraPitchDegree"]), "camera_roll": float(tags["CameraRollDegree"])}
        r = orient.nominal_from_camera_angles(angles)
        back = msl3.ypr_to_rotation(msl3.rotation_to_ypr(r))
        assert orient.angle_between(r, back) < 1e-6
    assert msl3.to_gl(msl3.to_gl(orient.IDENTITY)) == orient.IDENTITY


def test_the_flip_of_the_axes_matches_what_metashape_takes():
    # a nadir camera with north at the top: Metashape camera frame diag(1, -1, -1) -> OpenGL-like frame = identity
    nadir = orient.dji_matrix(0.0, -90.0, 0.0)
    flat = [v for row in msl3.to_gl(nadir) for v in row]
    assert flat == pytest.approx([v for row in orient.IDENTITY for v in row], abs=1e-12)


# --------------------------------------------------------------------------------------------------
# Creation
# --------------------------------------------------------------------------------------------------

def test_the_chunk_is_created_with_the_multi_camera_layout_and_the_tools_own_values(world):
    result = run(world)
    assert result.ok and result.chunk_label == "L3_TESTSITE_300101"
    chunk = world.doc.chunks[0]
    assert chunk.label == "L3_TESTSITE_300101" and chunk.crs.code == "EPSG::4326"
    call = chunk.calls[0][1]
    assert call["layout"] == "multiplane" and call["filegroups"] == [2] * 16 and len(call["filenames"]) == 32
    assert call["filenames"][0].endswith("_LV.JPG") and call["filenames"][1].endswith("_RV.JPG")
    # Metashape's own reading of the DJI tags is switched off: the tool reads them itself
    assert [call[k] for k in ("load_reference", "load_xmp_calibration", "load_xmp_orientation", "load_xmp_accuracy", "load_xmp_antenna")] == [False] * 5
    assert len(chunk.cameras) == 32 and len(chunk.sensors) == 2
    assert not world.doc.saved


def test_sensors_get_their_own_calibration_and_the_slave_its_rotation(world):
    run(world)
    chunk = world.doc.chunks[0]
    master, slave = chunk.sensors
    assert master.label.startswith("L3 left") and slave.label.startswith("L3 right")
    cal = master.user_calib
    assert (cal.f, cal.cx, cal.cy, cal.k1, cal.k2, cal.k3) == (5000.0, 10.0, 20.0, 0.1, -0.4, 0.5)
    assert cal.p1 == pytest.approx(-2.2e-5) and cal.p2 == pytest.approx(-1.1e-5) and (cal.width, cal.height) == (6144, 4096)
    assert slave.user_calib.f == 5010.0 and slave.user_calib.cx == -12.0
    assert master.fixed_calibration is False and master.pixel_size[0] == pytest.approx(0.0030238560267857144)
    rot = np.array(slave.rotation.a)
    assert rot == pytest.approx(np.array(orient.rig_rotation()), abs=1e-12)
    assert master.rotation.a == pytest.approx(np.eye(3))


def test_the_calibration_can_be_left_fixed(world):
    world.plan.options.calibration_fixed = True
    run(world)
    assert all(s.fixed_calibration for s in world.doc.chunks[0].sensors)


def test_every_camera_gets_position_accuracy_angles_and_no_rotation(world):
    run(world)
    chunk = world.doc.chunks[0]
    specs = {msl3.norm_path(p): s for p, s in world.plan.cameras.items()}
    for cam in chunk.cameras:
        spec = specs[msl3.norm_path(cam.photo.path)]
        ref = cam.reference
        assert list(ref.location) == [spec.lon, spec.lat, spec.alt]                  # longitude first
        assert list(ref.location_accuracy) == [0.05] * 3 and ref.enabled and ref.rotation_enabled
        assert list(ref.rotation_accuracy) == [10.0] * 3
        assert orient.angle_between(spec.rotation, msl3.ypr_to_rotation(ref.rotation)) < 1e-6
        assert cam.orientation == 1
    assert list(chunk.camera_location_accuracy) == [0.05] * 3


def test_a_height_shift_and_accuracies_reach_the_references(world):
    options = plan.Options(chunk_name="X", z_shift=0.27, accuracy_fixed=0.03)
    world.plan = checks.analyse(world.result.flights, options).plan
    run(world)
    cam = world.doc.chunks[0].cameras[0]
    assert cam.reference.location[2] == pytest.approx(200.0 + 0.27) and list(cam.reference.location_accuracy) == [0.03] * 3


def test_creation_is_checked_by_reading_it_back(world):
    result = run(world)
    verify = result.step("verify")
    assert verify.ok and result.geometry.ok
    assert result.geometry.with_rotation == 32 and result.geometry.max_rotation_error < 1e-6
    assert result.geometry.rig_angle == pytest.approx(45.0, abs=1e-6)
    assert [s[0][:7] for s in result.geometry.sensors] == ["L3 left", "L3 righ"]
    assert "32 cameras" in verify.text


def test_a_camera_left_turned_or_wrong_is_caught_by_the_check(world):
    result = run(world)
    chunk = world.doc.chunks[0]
    chunk.cameras[3].orientation = 6
    chunk.cameras[4].reference.rotation = Vector([10.0, 20.0, 30.0])
    del chunk.cameras[5]
    step = world.backend._verify(chunk, world.plan, None, types.SimpleNamespace())
    report = world.backend.read_back(chunk, world.plan)
    assert len(report.bad_orientation) == 1 and len(report.bad_rotation) == 1 and len(report.missing) == 1 and report.max_rotation_error > 1.0
    assert not step.ok and step.text == "Cameras differ from the plan" and len(step.lines) >= 3


def test_a_chunk_name_that_exists_gets_a_number(world):
    run(world)
    again = run(world)
    assert again.chunk_label == "L3_TESTSITE_300101 (2)"
    assert [c.label for c in world.doc.chunks] == ["L3_TESTSITE_300101", "L3_TESTSITE_300101 (2)"]


def test_photos_without_partner_go_on_a_sensor_of_their_own(world):
    right_only = [r for r in world.result.records if not (r.lens == "L" and r.stem.endswith("_0004"))]
    world.plan = plan.build_plan(right_only, plan.Options(chunk_name="X", include_unpaired=True))
    result = run(world)
    chunk = world.doc.chunks[0]
    assert result.ok and [c[1]["layout"] for c in chunk.calls if c[0] == "addPhotos"] == ["multiplane", "flat"]
    assert len(chunk.sensors) == 3
    lone = [c for c in chunk.cameras if c.photo.path.endswith("_0004_RV.JPG")]
    assert len(lone) == 2 and all(c.sensor is chunk.sensors[2] for c in lone) and lone[0].sensor.user_calib.cx == -12.0
    assert chunk.sensors[2].label.endswith("(single photos)") and chunk.sensors[2].rotation.a[0][2] == 0.0       # no rig rotation on a lone sensor
    assert chunk.sensors[1].rotation.a[0][2] == pytest.approx(math.sin(math.radians(45.0)))


def test_lone_photos_never_share_the_sensor_of_the_pairs(world, monkeypatch):
    """If Metashape put the lone photos on an existing sensor (same EXIF), they are moved to a new one."""
    original = Chunk.__init__

    def init(self, doc, key):
        original(self, doc, key)
        self.merge_flat = True
    monkeypatch.setattr(Chunk, "__init__", init)
    lone = [r for r in world.result.records if not (r.lens == "L" and r.stem.endswith("_0004"))]
    world.plan = plan.build_plan(lone, plan.Options(chunk_name="X", include_unpaired=True))
    run(world)
    chunk = world.doc.chunks[0]
    camera = [c for c in chunk.cameras if c.photo.path.endswith("_0004_RV.JPG")][0]
    assert camera.sensor.key not in (chunk.sensors[0].key, chunk.sensors[1].key) and camera.sensor.user_calib.cx == -12.0
    assert chunk.sensors[0].user_calib.cx == 10.0          # the left lens sensor kept its own calibration


def test_an_older_api_without_the_import_flags_still_works(world, monkeypatch):
    original = Chunk.__init__

    def init(self, doc, key):
        original(self, doc, key)
        self.rejects_flags = True
    monkeypatch.setattr(Chunk, "__init__", init)
    result = run(world)
    assert result.ok
    assert [c[0] for c in world.doc.chunks[0].calls].count("addPhotos") == 1


def test_a_failure_while_adding_removes_the_half_made_chunk(world, monkeypatch):
    original = Chunk.__init__

    def init(self, doc, key):
        original(self, doc, key)
        self.fail = "addPhotos"
    monkeypatch.setattr(Chunk, "__init__", init)
    result = run(world)
    assert not result.ok and "disk error" in result.steps[0].text and world.doc.chunks == []
    assert len(result.steps) == 1


def test_cancelling_during_the_creation_stops_everything_and_leaves_no_chunk(world, monkeypatch):
    flag = {"stop": False}
    original = Chunk.__init__

    def init(self, doc, key):
        original(self, doc, key)
        self.cancel_in_add = lambda: flag.update(stop=True)
    monkeypatch.setattr(Chunk, "__init__", init)
    result = run(world, steps=["align", "repair"], cancelled=lambda: flag["stop"])
    assert result.cancelled and not result.ok and world.doc.chunks == []


# --------------------------------------------------------------------------------------------------
# Alignment and repair
# --------------------------------------------------------------------------------------------------

def set_flags(monkeypatch, flip=(), stubborn=(), fail=None):
    """Every chunk created from now on flips these cameras in a full alignment (and keeps `stubborn` ones flipped when re-aligned)."""
    original = Chunk.__init__

    def init(self, doc, key):
        original(self, doc, key)
        self.flip, self.stubborn, self.fail = set(flip), set(stubborn), fail
    monkeypatch.setattr(Chunk, "__init__", init)


def test_alignment_then_check_finds_nothing_wrong_on_a_good_alignment(world):
    result = run(world, steps=["align", "repair"])
    assert result.ok
    assert result.alignment.aligned == 32 and result.alignment.clean and result.alignment.median < 0.5
    assert result.step("repair").text == "None"
    chunk = world.doc.chunks[0]
    kinds = [c[0] for c in chunk.calls]
    assert kinds.count("matchPhotos") == 1 and kinds.count("alignCameras") == 1
    match = [c[1] for c in chunk.calls if c[0] == "matchPhotos"][0]
    assert match["downscale"] == 1 and match["generic_preselection"] and match["reference_preselection"] and match["keypoint_limit"] == 60000
    assert match["tiepoint_limit"] == 10000 and match["filter_stationary_points"] and match["reference_preselection_mode"] == "source"
    assert [c[1] for c in chunk.calls if c[0] == "alignCameras"][0]["adaptive_fitting"] is True


def test_flipped_cameras_are_found_reset_and_aligned_again(world, monkeypatch):
    names = {os.path.splitext(os.path.basename(p))[0] for g in world.plan.groups for p in g}
    victims = sorted(n for n in names if n.endswith("0006_LV") or n.endswith("0006_RV") or n.endswith("0008_LV"))
    assert len(victims) == 6
    set_flags(monkeypatch, flip=victims)
    result = run(world, steps=["align", "repair"])
    assert result.alignment.repaired == 6 and result.alignment.clean
    assert result.step("align").ok and result.step("repair").ok and "6 re-aligned" in result.step("repair").text
    chunk = world.doc.chunks[0]
    realign = [c[1] for c in chunk.calls if c[0] == "alignCameras"][1]
    assert sorted(realign["cameras"]) == [n for n in sorted(names) if n.rsplit("_", 1)[0] in {v.rsplit("_", 1)[0] for v in victims}]      # both lenses of each shot
    assert realign["reset_alignment"] is False
    assert all(c.transform is not None for c in chunk.cameras)


def test_the_first_alignment_report_shows_the_flipped_cameras(world, monkeypatch):
    victims = [os.path.splitext(os.path.basename(world.plan.groups[2][0]))[0]]
    set_flags(monkeypatch, flip=victims)
    result = run(world, steps=["align"])
    assert result.alignment.remaining == 1 and result.alignment.flipped[0][0] == victims[0] and result.alignment.flipped[0][1] > 150.0
    assert result.step("align").ok


def test_cameras_that_stay_flipped_after_repair_are_reported_not_hidden(world, monkeypatch):
    victims = [os.path.splitext(os.path.basename(world.plan.groups[1][1]))[0]]
    set_flags(monkeypatch, flip=victims, stubborn=victims)
    result = run(world, steps=["align", "repair", "optimize"])
    repair = result.step("repair")
    assert not repair.ok and "still flipped" in repair.text and result.step("optimize") is None
    assert result.alignment.remaining == 1 and not result.ok
    assert [c[1]["cameras"] is not None for c in world.doc.chunks[0].calls if c[0] == "alignCameras"].count(True) == 2           # two repair passes


def test_a_failure_in_the_alignment_is_explained_and_stops_the_batch(world, monkeypatch):
    set_flags(monkeypatch, fail="matchPhotos")
    result = run(world, steps=["align", "repair", "optimize"])
    assert result.step("create").ok and not result.step("align").ok and "out of memory" in result.step("align").text.lower()
    assert result.step("repair") is None and result.step("optimize") is None


def test_optimize_dem_and_ortho_call_metashape_with_the_planned_settings(world):
    result = run(world, steps=["align", "optimize", "dem", "ortho"])
    assert result.ok
    chunk = world.doc.chunks[0]
    names = [c[0] for c in chunk.calls]
    assert names.index("optimizeCameras") < names.index("buildDepthMaps") < names.index("buildDem") < names.index("buildOrthomosaic")
    opt = [c[1] for c in chunk.calls if c[0] == "optimizeCameras"][0]
    assert all(opt["fit_" + k] for k in ("f", "cx", "cy", "k1", "k2", "k3", "p1", "p2"))
    dem = [c[1] for c in chunk.calls if c[0] == "buildDem"][0]
    assert dem["projection"].crs.code == "EPSG::32631" and "EPSG:32631" in result.step("dem").text           # UTM zone of 5 degrees east
    ortho = [c[1] for c in chunk.calls if c[0] == "buildOrthomosaic"][0]
    assert ortho["refine_seamlines"] is False and ortho["blending_mode"] == "mosaic"


def test_a_projection_can_be_chosen(world):
    world.plan.options.ortho_epsg = "2154"
    result = run(world, steps=["align", "dem"])
    assert result.step("dem").ok and "EPSG:2154" in result.step("dem").text


def test_the_quick_test_runs_on_a_copy_that_is_deleted(world):
    world.plan.options.quick_test_photos = 12
    result = run(world, steps=["quick_test"])
    assert result.ok and result.test.aligned == 12 and result.test.clean
    assert [c.label for c in world.doc.chunks] == ["L3_TESTSITE_300101"]          # the copy is gone
    chunk = world.doc.chunks[0]
    assert ("copy",) in chunk.calls and [c[0] for c in chunk.calls].count("matchPhotos") == 0       # the original was not aligned
    assert "12 / 12 photos" in result.step("quick_test").text
    assert result.test.lens_median["L"] < 1.0 and result.test.lens_median["R"] < 1.0


def test_the_quick_test_tells_a_swapped_rig_rotation(world, monkeypatch):
    """If the rig rotation had the wrong sign the right lens would come out far from its starting orientation: the test would say so."""
    labels = [os.path.splitext(os.path.basename(g[1]))[0] for g in world.plan.groups]
    set_flags(monkeypatch, flip=labels, stubborn=labels)
    world.plan.options.quick_test_photos = 12
    result = run(world, steps=["quick_test"])
    assert not result.ok and not result.test.clean
    assert result.test.lens_median["R"] > 90.0 and result.test.lens_median["L"] < 1.0
    assert "flipped" in result.step("quick_test").text and len(world.doc.chunks) == 1


def test_cancel_between_steps_keeps_what_was_done(world):
    def cancelled():
        chunk = world.doc.chunks[0] if world.doc.chunks else None
        return bool(chunk is not None and any(c[0] == "alignCameras" for c in chunk.calls))
    result = run(world, steps=["align", "optimize"], cancelled=cancelled)
    assert result.cancelled and result.step("create").ok and result.step("optimize") is None
    assert len(world.doc.chunks) == 1


def test_progress_goes_up_and_ends_at_one(world):
    seen = []
    run(world, steps=["align"], progress=lambda f, t: seen.append((f, t)))
    fractions = [f for f, _t in seen]
    assert fractions == sorted(fractions) and fractions[-1] == pytest.approx(1.0) and any("Matching" in t or "Adding" in t for _f, t in seen)


def test_metashape_is_only_imported_by_the_adapter():
    import pathlib
    package = pathlib.Path(msl3.__file__).parent
    offenders = [p.name for p in package.glob("*.py") if p.name != "msl3.py" and "import Metashape" in p.read_text(encoding="utf-8")]
    assert offenders == []
