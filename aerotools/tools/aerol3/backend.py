"""Aero-L3 `Backend` protocol: what the wizard needs from the outside world. Pure Python 3.9, no Qt, no Metashape.

Two implementations: `fake_backend.FakeBackend` (tests and screenshots) and `msl3.MetashapeL3Backend` (the Metashape adapter, the only module of
the tool that imports `Metashape`). `Progress` and `Cancelled` are the ones of Aero-Facade (same meaning: `progress(fraction, text)` from 0 to 1,
`cancelled()` polled between steps and inside Metashape's callbacks).
"""

from ..aerofacade.backend import Cancelled, Progress  # noqa: F401  (re-exported)

STEP_ORDER = ("create", "verify", "quick_test", "align", "repair", "optimize", "dem", "ortho")


class StepResult(object):
    """The outcome of one step of a run."""

    def __init__(self, key, ok, text, lines=None, skipped=False):
        self.key = key                     # one of STEP_ORDER
        self.ok = ok
        self.text = text                   # one plain English sentence
        self.lines = list(lines or [])     # more plain English lines (details, warnings)
        self.skipped = skipped

    def __repr__(self):
        return "StepResult({}, ok={}, {!r})".format(self.key, self.ok, self.text)


class RunResult(object):
    """What `Backend.run` returns."""

    def __init__(self):
        self.chunk_label = ""
        self.steps = []                    # [StepResult]
        self.geometry = None               # geomcheck.GeometryReport after the creation
        self.test = None                   # geomcheck.AlignmentReport of the quick test alignment
        self.alignment = None              # geomcheck.AlignmentReport after the alignment (and the repair)
        self.cancelled = False

    @property
    def ok(self):
        return not self.cancelled and all(s.ok for s in self.steps)

    def step(self, key):
        for s in self.steps:
            if s.key == key:
                return s
        return None


class Backend(object):
    threaded = False                       # True: `run` may be called from a worker thread

    def project_info(self):
        """{"project": path of the .psx or '', "chunks": [labels], "active": label or ''}."""
        raise NotImplementedError

    def run(self, plan, steps, progress, cancelled):
        """Create the chunk of `plan` and carry out the ticked `steps` (a list of keys of STEP_ORDER; `create` and `verify` always run).
        Returns a `RunResult`. A cancel stops the whole batch: the steps done so far stay, the result has `cancelled` True."""
        raise NotImplementedError
