"""Aero-L3: a `Backend` that creates nothing, for the tests and the screenshots of the wizard. Pure Python 3.9."""

from ... import i18n
from . import geomcheck
from .backend import STEP_ORDER, Backend, Cancelled, Progress, RunResult, StepResult, step_spans

T = i18n.t

ACTIVITY = {"create": "l3.run.adding", "verify": "l3.run.references", "quick_test": "l3.run.matching", "align": "l3.run.aligning",
            "repair": "l3.run.realigning", "optimize": "l3.run.optimizing", "dem": "l3.run.depth_maps", "ortho": "l3.run.ortho"}


class FakeBackend(Backend):
    """Pretends to create the chunk. `flipped` = how many cameras the fake alignment flips (all repaired unless `unrepairable`);
    `fail` = key of a step that fails; `steps_delay` = pauses `pump()` is called with (lets a test see the progress)."""

    threaded = False

    def __init__(self, flipped=0, unrepairable=False, fail=None, chunks=None, pump=None):
        self.flipped = flipped
        self.unrepairable = unrepairable
        self.fail = fail
        self.chunks = list(chunks or [])
        self.pump = pump or (lambda: None)
        self.runs = []

    def project_info(self):
        return {"project": "", "chunks": list(self.chunks), "active": self.chunks[0] if self.chunks else ""}

    def run(self, plan, steps, progress, cancelled):
        prog = Progress.of(progress, cancelled)
        self.runs.append((plan, list(steps)))
        result = RunResult()
        spans = step_spans(steps)
        result.chunk_label = plan.options.chunk_name or "L3"
        try:
            for key, low, high in spans:
                for share in (0.2, 0.6):
                    prog(low + (high - low) * share, T(ACTIVITY[key]))
                    self.pump()
                    prog.check()
                if self.fail == key:
                    result.steps.append(StepResult(key, False, T("l3.run.step_failed", step=T("l3.run.name_" + key), reason="simulated failure")))
                    break
                result.steps.append(self._step(key, plan, result))
                if not result.steps[-1].ok:
                    break
        except Cancelled:
            result.cancelled = True
        return result

    def _step(self, key, plan, result):
        n = plan.photo_count
        if key == "create":
            return StepResult("create", True, T("l3.run.created", name=plan.options.chunk_name or "L3", n=n, sensors=len(plan.sensors)))
        if key == "verify":
            report = geomcheck.GeometryReport()
            report.planned = report.cameras = n
            report.with_position = report.with_rotation = n
            report.rig_angle = 45.0
            for sensor in plan.sensors.values():
                report.sensors.append((sensor.label, sensor.width, sensor.height, sensor.calibration.f))
            result.geometry = report
            return StepResult("verify", True, T("l3.run.verify_ok", n=n, err="0.000"),
                              [T("l3.run.sensor_line", label=s[0], w=s[1], h=s[2], f="%.1f" % s[3]) for s in report.sensors])
        if key == "quick_test":
            test = geomcheck.alignment_report([("t%d" % i, 1.5) for i in range(min(30, n))])
            result.test = test
            return StepResult("quick_test", True, T("l3.run.test_ok", n=test.aligned, total=test.total))
        if key == "align":
            entries = [("c%d" % i, 90.0 if i < self.flipped else 1.6) for i in range(n)]
            result.alignment = geomcheck.alignment_report(entries)
            return StepResult("align", True, T("l3.run.aligned", n=result.alignment.aligned, total=result.alignment.total))
        if key == "repair":
            report = result.alignment
            if not report or not report.flipped:
                return StepResult("repair", True, T("l3.run.repair_none"))
            first = len(report.flipped)
            if self.unrepairable:
                return StepResult("repair", False, T("l3.run.repair_left", n=first, first=first))
            result.alignment = geomcheck.alignment_report([("c%d" % i, 1.6) for i in range(n)])
            result.alignment.repaired = first
            return StepResult("repair", True, T("l3.run.repair_done", n=first))
        if key == "optimize":
            return StepResult("optimize", True, T("l3.run.optimized"))
        if key == "dem":
            return StepResult("dem", True, T("l3.run.dem_done", crs="EPSG:32631"))
        return StepResult("ortho", True, T("l3.run.ortho_done", crs="EPSG:32631"))
