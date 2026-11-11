"""Aero-L3: the Metashape adapter. The ONLY module of the tool that imports `Metashape`; it implements `backend.Backend`.

What it does with a `plan.Plan` (see docs/AERO_L3.md):

1. `create`: a new chunk named after the site and the date, in WGS 84 (or ETRS89) with ellipsoidal heights; the photos are added with the
   multi-camera layout, two photos (left lens first) per group, so Metashape makes a master sensor (left lens) and a slave sensor (right
   lens). The tool's own reading of the XMP replaces Metashape's: `load_reference`, `load_xmp_calibration`, `load_xmp_orientation`,
   `load_xmp_accuracy` and `load_xmp_antenna` are all False. Then every sensor gets its calibration (from `DewarpData`, adjustable), the slave
   its rotation (45 degrees about Y), and every camera its position and accuracy, its starting orientation (yaw, pitch, roll written with
   `Metashape.utils.mat2ypr`) and `orientation = 1` (the picture is never turned).
2. `verify`: reads everything back and compares it with the plan (the angles must give the planned orientation again).
3. `quick_test`: aligns a few dozen photos in a COPY of the chunk, compares the poses with the starting orientations, deletes the copy.
4. `align`, `repair` (cameras whose pose is far from their starting orientation are reset and aligned again), `optimize`, `dem`, `ortho`.

The project is never saved. All the computing runs on the main thread (Metashape shows its own progress); between two Metashape calls `pump()`
lets Qt work. A cancel (the flag, or Metashape's own Cancel button) stops the whole batch.
"""

import math
import os
import time

import Metashape

from ... import i18n, journal
from . import geomcheck, orient, plan as plan_module
from .backend import STEP_ORDER, STEP_WEIGHTS, Backend, Cancelled, Progress, RunResult, StepResult

TAG = "AeroL3"
T = i18n.t

ALIGN_DOWNSCALE = 1                    # highest accuracy: what worked on test data
KEYPOINT_LIMIT = 60000
TIEPOINT_LIMIT = 10000
REPAIR_PASSES = 2


def log(message):
    journal.write("Aero-L3: " + message, tag=TAG)


# --------------------------------------------------------------------------------------------------
# Conversions
# --------------------------------------------------------------------------------------------------

def norm_path(path):
    return os.path.normcase(os.path.normpath(path))


def to_gl(rotation):
    """Camera frame of Metashape (x right, y down, z forward) -> OpenGL-like camera frame (x right, y up, z back): what `utils.mat2ypr` takes."""
    return tuple((row[0], -row[1], -row[2]) for row in rotation)


def ms_matrix(rotation):
    return Metashape.Matrix([[float(rotation[i][j]) for j in range(3)] for i in range(3)])


def rotation_to_ypr(rotation):
    """(yaw, pitch, roll) in degrees for Metashape's camera reference, from a camera -> east-north-up rotation."""
    ypr = Metashape.utils.mat2ypr(ms_matrix(to_gl(rotation)))
    return (float(ypr[0]), float(ypr[1]), float(ypr[2]))


def ypr_to_rotation(ypr):
    """Inverse of `rotation_to_ypr`: the camera -> east-north-up rotation of a Metashape yaw, pitch, roll."""
    m = Metashape.utils.ypr2mat(Metashape.Vector([float(ypr[0]), float(ypr[1]), float(ypr[2])]))
    gl = tuple(tuple(float(m[i, j]) for j in range(3)) for i in range(3))
    return to_gl(gl)                                   # the flip of the y and z columns is its own inverse


def estimated_rotation(chunk, camera):
    """Camera -> east-north-up rotation of an aligned camera, or None (not aligned, or the chunk has no georeference)."""
    try:
        if camera.transform is None or chunk.transform.matrix is None or chunk.crs is None:
            return None
        chunk_to_ecef = chunk.transform.matrix
        frame = chunk.crs.localframe(chunk_to_ecef.mulp(camera.center))
        m = frame * chunk_to_ecef * camera.transform
        block = [[float(m[i, j]) for j in range(3)] for i in range(3)]
    except Exception:
        return None
    scale = math.sqrt(sum(block[i][0] ** 2 for i in range(3)))
    if scale <= 0:
        return None
    return tuple(tuple(block[i][j] / scale for j in range(3)) for i in range(3))


def explain(exc):
    """Plain-English reason for a failure."""
    text = str(exc) or exc.__class__.__name__
    low = text.lower()
    if "out of memory" in low or "bad_alloc" in low:
        return T("l3.run.err_memory")
    if "gpu" in low or "opencl" in low:
        return T("l3.run.err_gpu", text=text)
    return text


def finished(prog, text):
    """Last report of a step that is DONE: a cancel that arrives now must not throw the finished work away (it is noticed at the next step)."""
    try:
        prog(1.0, text)
    except Cancelled:
        pass


class _Meter(object):
    """Turns Metashape's 0..100 callbacks into `Progress` fractions; raises KeyboardInterrupt on cancel (what makes Metashape stop a job cleanly)."""

    def __init__(self, prog, lo, hi, text):
        self.prog, self.lo, self.hi, self.text = prog, lo, hi, text
        self._last = 0.0

    def __call__(self, pct):
        if self.prog.is_cancelled():
            raise KeyboardInterrupt("cancelled")
        now = time.time()
        if now - self._last < 0.2 and pct < 99.9:
            return
        self._last = now
        frac = self.lo + (self.hi - self.lo) * min(100.0, max(0.0, float(pct))) / 100.0
        self.prog.fraction, self.prog.text = self.prog.lo + (self.prog.hi - self.prog.lo) * frac, self.text
        if self.prog.callback is not None:
            self.prog.callback(self.prog.fraction, self.text)


def unique_label(doc, wanted):
    labels = {c.label for c in doc.chunks}
    if wanted not in labels:
        return wanted
    i = 2
    while "{} ({})".format(wanted, i) in labels:
        i += 1
    return "{} ({})".format(wanted, i)


class MetashapeL3Backend(Backend):
    """Creates the chunk of a plan in the open Metashape project and carries out the ticked steps."""

    threaded = False

    def __init__(self, pump=None):
        self.doc = Metashape.app.document
        self.pump = pump or (lambda: None)

    # ---- small things -------------------------------------------------------------------------------
    def project_info(self):
        active = self.doc.chunk
        return {"project": self.doc.path or "", "chunks": [c.label for c in self.doc.chunks], "active": active.label if active else ""}

    def _call(self, prog, lo, hi, text, func, *args, **kwargs):
        """One Metashape processing call with its progress mapped to [lo, hi] of `prog`; a cancel becomes `Cancelled`."""
        self.pump()
        prog.check()
        kwargs["progress"] = _Meter(prog, lo, hi, text)
        try:
            return func(*args, **kwargs)
        except KeyboardInterrupt:
            raise Cancelled()

    def _chunk_of(self, label):
        for chunk in self.doc.chunks:
            if chunk.label == label:
                return chunk
        raise RuntimeError(T("l3.run.err_no_chunk", name=label))

    def _specs(self, plan):
        return {norm_path(p): spec for p, spec in plan.cameras.items()}

    # ---- the run ------------------------------------------------------------------------------------
    def run(self, plan, steps, progress, cancelled):
        prog = Progress.of(progress, cancelled)
        wanted = [k for k in STEP_ORDER if k in ("create", "verify") or k in steps]
        total = sum(STEP_WEIGHTS[k] for k in wanted)
        result = RunResult()
        done = 0.0
        label = None
        try:
            for key in wanted:
                lo, hi = done / total, (done + STEP_WEIGHTS[key]) / total
                done += STEP_WEIGHTS[key]
                sub = prog.sub(lo, hi)
                sub.check()
                self.pump()
                step = None
                try:
                    if key == "create":
                        label, step = self._create(plan, sub, result)
                        result.chunk_label = label
                    elif label is None:
                        step = StepResult(key, False, T("l3.run.skipped_no_chunk"), skipped=True)
                    else:
                        chunk = self._chunk_of(label)
                        step = getattr(self, "_" + key)(chunk, plan, sub, result)
                except Cancelled:
                    raise
                except Exception as exc:
                    log("{} failed: {!r}".format(key, exc))
                    step = StepResult(key, False, T("l3.run.step_failed", step=T("l3.run.name_" + key), reason=explain(exc)))
                result.steps.append(step)
                if not step.ok:
                    break
                finished(sub, T("l3.run.step_done", step=T("l3.run.name_" + key)))
        except Cancelled:
            result.cancelled = True
        return result

    # ---- create -------------------------------------------------------------------------------------
    def _create(self, plan, prog, result):
        options = plan.options
        lines = []
        label = unique_label(self.doc, options.chunk_name or "L3")
        chunk = self.doc.addChunk()
        chunk.label = label
        try:
            return self._fill(chunk, label, plan, prog, lines)
        except BaseException:                                   # a cancel or a failure leaves no half-made chunk behind
            try:
                self.doc.remove(chunk)
            except Exception:
                pass
            raise

    def _fill(self, chunk, label, plan, prog, lines):
        options = plan.options
        chunk.crs = Metashape.CoordinateSystem(options.crs)
        groups, singles = plan.groups, plan.singles
        rig_keys = set()
        if groups:
            self._add_photos(chunk, prog, 0.0, 0.7, T("l3.run.adding"), [p for g in groups for p in g], [len(g) for g in groups],
                             Metashape.MultiplaneLayout)
            rig_keys = {camera.sensor.key for camera in chunk.cameras}
        if singles:
            self._add_singles(chunk, plan, prog, lines, rig_keys)
        self.pump()
        specs = self._specs(plan)
        sensors = self._configure_sensors(chunk, plan, specs, lines, rig_keys)
        positions, rotations, forced = self._configure_cameras(chunk, plan, specs, lines, prog)
        for attr, value in (("camera_location_accuracy", options.accuracy_fixed), ("camera_rotation_accuracy", options.rotation_accuracy)):
            try:
                setattr(chunk, attr, Metashape.Vector([value, value, value]))
            except Exception:
                pass
        added = len(chunk.cameras)
        text = T("l3.run.created", name=label, n=added, sensors=len(sensors))
        sub_ok = added > 0
        if not sub_ok:
            text = T("l3.run.nothing_added")
        step = StepResult("create", sub_ok, text, lines)
        finished(prog, text)
        return label, step

    def _add_singles(self, chunk, plan, prog, lines, rig_keys):
        """Photos without their partner: one `addPhotos` call per lens, each on a sensor of its own (the sensor of the pair layout would hold
        the wrong calibration; Metashape could also put the two kinds on one sensor, since their EXIF is the same)."""
        for lens in (orient.LEFT, orient.RIGHT):
            paths = [p for p in plan.singles if plan.cameras[p].lens == lens]
            if not paths:
                continue
            self._add_photos(chunk, prog, 0.7, 0.8, T("l3.run.adding_single"), paths, [], Metashape.FlatLayout)
            wanted = {norm_path(p) for p in paths}
            added = [c for c in chunk.cameras if norm_path(c.photo.path) in wanted]
            if added and added[0].sensor.key in rig_keys:
                try:
                    sensor = chunk.addSensor()
                    for camera in added:
                        camera.sensor = sensor
                except Exception as exc:
                    lines.append(T("l3.run.single_sensor_failed", reason=explain(exc)))

    def _add_photos(self, chunk, prog, lo, hi, text, filenames, filegroups, layout):
        meter = _Meter(prog, lo, hi, text)
        self.pump()
        prog.check()
        base = dict(filenames=filenames, filegroups=filegroups, layout=layout, progress=meter)
        flags = dict(load_reference=False, load_xmp_calibration=False, load_xmp_orientation=False, load_xmp_accuracy=False, load_xmp_antenna=False)
        try:
            try:
                chunk.addPhotos(**dict(base, **flags))
            except TypeError:                                   # an older API without these names: the defaults (angles not read) are close
                chunk.addPhotos(**base)
        except KeyboardInterrupt:
            raise Cancelled()

    def _configure_sensors(self, chunk, plan, specs, lines, rig_keys):
        """Gives every sensor its calibration (and the slave its rotation); returns the list of sensors. Problems are written to `lines`."""
        found = []
        seen = set()
        for camera in chunk.cameras:
            spec = specs.get(norm_path(camera.photo.path))
            if spec is not None and camera.sensor.key not in seen:
                seen.add(camera.sensor.key)
                found.append((camera.sensor, spec.lens))
        masters = {}
        for sensor, lens in found:
            sensor_spec = plan.sensors.get(lens)
            if sensor_spec is None:
                continue
            is_slave = self._is_slave(sensor)
            sensor.label = sensor_spec.label if sensor.key in rig_keys else sensor_spec.label + " (single photos)"
            cal = Metashape.Calibration()
            c = sensor_spec.calibration
            try:
                cal.width, cal.height = c.width, c.height
                cal.f, cal.cx, cal.cy = c.f, c.cx, c.cy
                cal.k1, cal.k2, cal.k3, cal.p1, cal.p2 = c.k1, c.k2, c.k3, c.p1, c.p2
                sensor.user_calib = cal
            except Exception as exc:
                lines.append(T("l3.run.sensor_calibration_failed", name=sensor_spec.label, reason=explain(exc)))
            try:
                sensor.fixed_calibration = bool(plan.options.calibration_fixed)
            except Exception:
                pass
            if sensor_spec.pixel_mm:
                try:
                    sensor.pixel_size = Metashape.Vector([sensor_spec.pixel_mm, sensor_spec.pixel_mm])
                    sensor.focal_length = c.f * sensor_spec.pixel_mm
                except Exception:
                    pass
            if is_slave and sensor_spec.rotation is not None:
                try:
                    sensor.rotation = ms_matrix(sensor_spec.rotation)
                except Exception as exc:
                    lines.append(T("l3.run.rig_rotation_failed", reason=explain(exc)))
            if lens == orient.LEFT and not is_slave:
                masters[sensor.key] = sensor
        if plan.groups:
            slaves = [s for s, _l in found if self._is_slave(s)]
            if not slaves or any(getattr(s.master, "key", None) not in masters for s in slaves):
                lines.append(T("l3.run.rig_unexpected"))
        return [s for s, _l in found]

    @staticmethod
    def _is_slave(sensor):
        master = getattr(sensor, "master", None)
        return master is not None and master.key != sensor.key

    def _configure_cameras(self, chunk, plan, specs, lines, prog):
        """Reference position, accuracy, starting orientation and `orientation = 1` of every camera. Returns (n positions, n rotations, n forced)."""
        positions = rotations = forced = 0
        failures = {"orientation": 0}
        cameras = list(chunk.cameras)
        for i, camera in enumerate(cameras):
            spec = specs.get(norm_path(camera.photo.path))
            if spec is None:
                continue
            ref = camera.reference
            if spec.accuracy is not None:
                ref.location = Metashape.Vector([spec.lon, spec.lat, spec.alt])
                try:
                    ref.location_accuracy = Metashape.Vector(list(spec.accuracy))
                except Exception:
                    ref.accuracy = Metashape.Vector(list(spec.accuracy))
                ref.enabled = True
                positions += 1
            if spec.rotation is not None:
                ref.rotation = Metashape.Vector(list(rotation_to_ypr(spec.rotation)))
                a = spec.rotation_accuracy
                ref.rotation_accuracy = Metashape.Vector([a, a, a])
                try:
                    ref.rotation_enabled = True
                except Exception:
                    pass
                rotations += 1
            try:
                camera.orientation = 1
                forced += 1
            except Exception:
                failures["orientation"] += 1
            if i % 200 == 0:
                prog(0.8 + 0.2 * i / max(1, len(cameras)), T("l3.run.references"))
        if failures["orientation"]:
            lines.append(T("l3.run.orientation_not_set", n=failures["orientation"]))
        return positions, rotations, forced

    # ---- verify -------------------------------------------------------------------------------------
    def read_back(self, chunk, plan):
        """A `GeometryReport`: what the chunk holds against what the plan wanted."""
        report = geomcheck.GeometryReport()
        specs = self._specs(plan)
        report.planned = len(specs)
        found = set()
        for camera in chunk.cameras:
            key = norm_path(camera.photo.path)
            spec = specs.get(key)
            if spec is None:
                report.unknown += 1
                continue
            found.add(key)
            ref = camera.reference
            if spec.accuracy is not None and ref.location is not None and ref.enabled:
                report.with_position += 1
            if spec.rotation is not None:
                try:
                    back = ypr_to_rotation(ref.rotation)
                    err = orient.angle_between(spec.rotation, back)
                    report.with_rotation += 1
                    report.max_rotation_error = max(report.max_rotation_error, err)
                    if err > geomcheck.ROUND_TRIP_DEG:
                        report.bad_rotation.append(camera.label)
                except Exception:
                    report.bad_rotation.append(camera.label)
            try:
                if int(camera.orientation) != 1:
                    report.bad_orientation.append(camera.label)
            except Exception:
                pass
        report.cameras = len(found) + report.unknown
        report.missing = [spec.name for key, spec in specs.items() if key not in found]
        seen = set()
        for camera in chunk.cameras:
            sensor = camera.sensor
            if sensor.key in seen:
                continue
            seen.add(sensor.key)
            cal = sensor.user_calib or sensor.calibration
            report.sensors.append((sensor.label, sensor.width, sensor.height, float(cal.f) if cal else 0.0))
        try:
            lens_sensor = {}
            for camera in chunk.cameras:
                spec = specs.get(norm_path(camera.photo.path))
                if spec is not None and spec.lens not in lens_sensor:
                    lens_sensor[spec.lens] = camera.sensor
            slave = lens_sensor.get(orient.RIGHT)
            if slave is not None and plan.groups:
                m = slave.rotation
                rot = tuple(tuple(float(m[i, j]) for j in range(3)) for i in range(3))
                report.rig_angle = orient.angle_between(orient.IDENTITY, rot)
        except Exception:
            report.rig_angle = None
        return report

    def _verify(self, chunk, plan, prog, result):
        report = self.read_back(chunk, plan)
        result.geometry = report
        lines = []
        if report.missing:
            report.problems.append(T("l3.run.missing_photos", n=len(report.missing)))
            lines.append(", ".join(report.missing[:5]))
        if report.bad_rotation:
            report.problems.append(T("l3.run.bad_rotation", n=len(report.bad_rotation)))
        if report.bad_orientation:
            report.problems.append(T("l3.run.bad_orientation", n=len(report.bad_orientation)))
        if report.rig_angle is not None and plan.groups and abs(report.rig_angle - orient.RIG_ANGLE) > 1.0:
            report.problems.append(T("l3.run.bad_rig", angle=i18n.fmt_number(report.rig_angle, 1)))
        lines = report.problems + lines
        if report.problems:
            return StepResult("verify", False, T("l3.run.verify_failed"), lines)
        text = T("l3.run.verify_ok", n=report.with_rotation, err=i18n.fmt_number(report.max_rotation_error, 3))
        return StepResult("verify", True, text, [T("l3.run.sensor_line", label=s[0], w=s[1], h=s[2], f=i18n.fmt_number(s[3], 1)) for s in report.sensors])

    # ---- alignment ----------------------------------------------------------------------------------
    def _match_and_align(self, chunk, prog, lo, hi, downscale, keypoints, tiepoints, cameras=None):
        mid = lo + (hi - lo) * 0.7
        kwargs = dict(downscale=downscale, generic_preselection=True, reference_preselection=True, keypoint_limit=keypoints,
                      tiepoint_limit=tiepoints, filter_stationary_points=True)
        if hasattr(Metashape, "ReferencePreselectionSource"):
            kwargs["reference_preselection_mode"] = Metashape.ReferencePreselectionSource
        if cameras is not None:
            kwargs["cameras"] = cameras
        self._call(prog, lo, mid, T("l3.run.matching"), chunk.matchPhotos, **kwargs)
        align = dict(adaptive_fitting=True)
        if cameras is not None:
            align["cameras"] = cameras
        self._call(prog, mid, hi, T("l3.run.aligning"), chunk.alignCameras, **align)

    def alignment_entries(self, chunk, plan):
        """([(camera label, angle between its pose and its starting orientation or None)], {label: lens}) for every camera of the plan."""
        specs = self._specs(plan)
        entries, lens_of = [], {}
        for camera in chunk.cameras:
            spec = specs.get(norm_path(camera.photo.path))
            if spec is None:
                continue
            lens_of[camera.label] = spec.lens
            rotation = estimated_rotation(chunk, camera)
            if rotation is None or spec.rotation is None:
                entries.append((camera.label, None))
            else:
                entries.append((camera.label, geomcheck.compare_rotations(spec.rotation, rotation)))
        return entries, lens_of

    def _report(self, chunk, plan):
        entries, lens_of = self.alignment_entries(chunk, plan)
        report = geomcheck.alignment_report(entries, lens_of=lens_of)
        report.total = len(entries)
        return report

    def _align_lines(self, report):
        lines = []
        if report.median is not None:
            lines.append(T("l3.run.align_gap", median=i18n.fmt_number(report.median, 1), maximum=i18n.fmt_number(report.maximum, 1)))
        return lines

    def _quick_test(self, chunk, plan, prog, result):
        paths = {norm_path(p) for p in plan_module.pick_test_stations(plan, plan.options.quick_test_photos)}
        if not paths:
            return StepResult("quick_test", True, T("l3.run.test_none"), skipped=True)
        work = chunk.copy()
        work.label = chunk.label + " (L3 test copy)"
        try:
            for camera in work.cameras:
                camera.enabled = norm_path(camera.photo.path) in paths
            self._match_and_align(work, prog, 0.0, 0.95, 2, 40000, 4000, cameras=[c for c in work.cameras if c.enabled])
            report = self._report(work, plan)
            report.total = len(paths)
        finally:
            try:
                self.doc.remove(work)
            except Exception:
                pass
        result.test = report
        lines = self._align_lines(report)
        for lens, name in ((orient.LEFT, "l3.run.lens_left"), (orient.RIGHT, "l3.run.lens_right")):
            if lens in report.lens_median:
                lines.append(T("l3.run.test_lens", lens=T(name), median=i18n.fmt_number(report.lens_median[lens], 1)))
        if report.aligned == 0:
            return StepResult("quick_test", False, T("l3.run.test_nothing", n=report.total), lines)
        if report.flipped:
            return StepResult("quick_test", False, T("l3.run.test_flipped", n=len(report.flipped), total=report.aligned), lines)
        return StepResult("quick_test", True, T("l3.run.test_ok", n=report.aligned, total=report.total), lines)

    def _align(self, chunk, plan, prog, result):
        self._match_and_align(chunk, prog, 0.0, 1.0, ALIGN_DOWNSCALE, KEYPOINT_LIMIT, TIEPOINT_LIMIT)
        report = self._report(chunk, plan)
        result.alignment = report
        text = T("l3.run.aligned", n=report.aligned, total=report.total)
        ok = report.aligned > 0
        return StepResult("align", ok, text if ok else T("l3.run.align_none"), self._align_lines(report))

    def _repair(self, chunk, plan, prog, result):
        report = result.alignment or self._report(chunk, plan)
        first = len(report.flipped)
        passes = 0
        while report.flipped and passes < REPAIR_PASSES:
            passes += 1
            names = {n for n, _a in report.flipped}
            stations = set()
            for camera in chunk.cameras:
                if camera.label in names:
                    stations.add(camera.label.rsplit("_", 1)[0])
            targets = [c for c in chunk.cameras if c.label.rsplit("_", 1)[0] in stations]
            for camera in targets:
                camera.transform = None
            self._call(prog, (passes - 1) / float(REPAIR_PASSES), passes / float(REPAIR_PASSES), T("l3.run.realigning"), chunk.alignCameras,
                       cameras=targets, reset_alignment=False, adaptive_fitting=True)
            report = self._report(chunk, plan)
        report.repaired = max(0, first - len(report.flipped))
        result.alignment = report
        if first == 0:
            return StepResult("repair", True, T("l3.run.repair_none"), self._align_lines(report))
        if report.flipped:
            return StepResult("repair", False, T("l3.run.repair_left", n=len(report.flipped), first=first),
                              [n for n, _a in report.flipped[:5]] + self._align_lines(report))
        return StepResult("repair", True, T("l3.run.repair_done", n=first), self._align_lines(report))

    def _optimize(self, chunk, plan, prog, result):
        self._call(prog, 0.0, 1.0, T("l3.run.optimizing"), chunk.optimizeCameras, fit_f=True, fit_cx=True, fit_cy=True, fit_k1=True, fit_k2=True,
                   fit_k3=True, fit_p1=True, fit_p2=True, adaptive_fitting=False)
        return StepResult("optimize", True, T("l3.run.optimized"))

    # ---- DEM and orthomosaic (optional; heavy) --------------------------------------------------------
    def _projection(self, chunk, plan):
        epsg = plan.options.ortho_epsg
        if not epsg:
            lon = None
            for camera in chunk.cameras:
                if camera.reference.location is not None:
                    lon = float(camera.reference.location[0])
                    break
            lat = None
            for camera in chunk.cameras:
                if camera.reference.location is not None:
                    lat = float(camera.reference.location[1])
                    break
            if lon is None or lat is None:
                raise RuntimeError(T("l3.run.err_no_position"))
            zone = int((lon + 180.0) // 6.0) + 1
            epsg = "EPSG::{}".format((32600 if lat >= 0 else 32700) + zone)
        elif not str(epsg).upper().startswith("EPSG"):
            epsg = "EPSG::{}".format(epsg)
        proj = Metashape.OrthoProjection()
        proj.crs = Metashape.CoordinateSystem(epsg)
        return proj, epsg

    def _dem(self, chunk, plan, prog, result):
        proj, epsg = self._projection(chunk, plan)
        self._call(prog, 0.0, 0.7, T("l3.run.depth_maps"), chunk.buildDepthMaps, downscale=4, filter_mode=Metashape.MildFiltering)
        self._call(prog, 0.7, 1.0, T("l3.run.dem"), chunk.buildDem, source_data=Metashape.DepthMapsData, projection=proj)
        return StepResult("dem", True, T("l3.run.dem_done", crs=str(epsg).replace("::", ":")))

    def _ortho(self, chunk, plan, prog, result):
        proj, epsg = self._projection(chunk, plan)
        kwargs = dict(surface_data=Metashape.ElevationData, blending_mode=Metashape.MosaicBlending, fill_holes=True, refine_seamlines=False,
                      projection=proj)
        self._call(prog, 0.0, 1.0, T("l3.run.ortho"), chunk.buildOrthomosaic, **kwargs)
        return StepResult("ortho", True, T("l3.run.ortho_done", crs=str(epsg).replace("::", ":")))
