"""Aero-L3 `Backend` protocol: what the wizard needs from the outside world. Pure Python 3.9, no Qt, no Metashape.

Two implementations: `fake_backend.FakeBackend` (tests and screenshots) and `msl3.MetashapeL3Backend` (the Metashape adapter, the only module of
the tool that imports `Metashape`). `Progress` and `Cancelled` come from `runtime.py` (`progress(fraction, text)` from 0 to 1,
`cancelled()` polled between steps and inside Metashape's callbacks).
"""

from .runtime import Cancelled, Progress  # noqa: F401  (re-exported)

STEP_ORDER = ("create", "verify", "quick_test", "align", "repair", "optimize", "dem", "ortho")
STEP_WEIGHTS = {"create": 0.20, "verify": 0.02, "quick_test": 0.12, "align": 0.38, "repair": 0.10, "optimize": 0.08, "dem": 0.06, "ortho": 0.04}


def step_spans(steps):
    """[(key, low, high)]: the share of the whole progress bar each step of a run takes (`create` and `verify` always run)."""
    wanted = [k for k in STEP_ORDER if k in ("create", "verify") or k in steps]
    total = sum(STEP_WEIGHTS[k] for k in wanted)
    spans, done = [], 0.0
    for key in wanted:
        spans.append((key, done / total, (done + STEP_WEIGHTS[key]) / total))
        done += STEP_WEIGHTS[key]
    return spans


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
