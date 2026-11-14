"""TaskRunner: one way to run a long call, with progress, cancel and a single error path.

Two modes, because the Metashape API only works on the UI thread:
  threaded     `func` runs on a worker thread (pure Python: files, numbers). Progress reaches the window through queued Qt signals,
               so nothing polls and no widget is touched off the UI thread.
  cooperative  `func` runs right here on the UI thread; each `progress(...)` call lets Qt process events (rate-limited), which keeps the
               window alive and delivers a click on Cancel. Use it for anything that calls Metashape. `start()` returns when it ended.

`func(progress)` gets a `Progress`: call it as `progress(fraction, text)`, `progress.step(i, n, text)`, and `progress.check()` (raises
`Cancelled` when the operator asked to stop) as often as the work allows. A sub-stage uses `progress.sub(lo, hi)`.
Outcome signals (all on the UI thread): `finished(result)`, `failed(exc)`, `cancelled()`, then always `ended()`.
"""

import threading
import time
import traceback

from .. import i18n, journal
from ..qt import QtCore, QtWidgets


class Cancelled(Exception):
    """Raised by `Progress.check()` when the operator cancelled."""


def plain_error(exc):
    """A one-line message for an exception (never empty)."""
    text = str(exc).strip().splitlines()
    return text[0] if text and text[0] else type(exc).__name__


class Progress(object):
    """The object a task reports to. Safe to call from a worker thread."""

    def __init__(self, report=None, is_cancelled=None, lo=0.0, hi=1.0, tick=None):
        self._report, self._is_cancelled, self._tick = report, is_cancelled, tick
        self.lo, self.hi = float(lo), float(hi)
        self.fraction = 0.0
        self.text = ""

    def __call__(self, fraction=None, text=""):
        if fraction is not None:
            self.fraction = self.lo + (self.hi - self.lo) * min(1.0, max(0.0, float(fraction)))
        if text:
            self.text = str(text)
        if self._report is not None:
            self._report(self.fraction, self.text)
        self.check()

    def step(self, done, total, text=""):
        """'done of total finished'."""
        self(0.0 if total <= 0 else done / float(total), text)

    def sub(self, lo, hi):
        """A view of this progress that maps 0..1 onto [lo, hi] of it."""
        span = self.hi - self.lo
        return Progress(self._report, self._is_cancelled, self.lo + span * lo, self.lo + span * hi, self._tick)

    def is_cancelled(self):
        return bool(self._is_cancelled is not None and self._is_cancelled())

    def check(self):
        """Let the window breathe (cooperative mode) and raise `Cancelled` if the operator asked to stop."""
        if self._tick is not None:
            self._tick()
        if self.is_cancelled():
            raise Cancelled()


class TaskRunner(QtCore.QObject):
    """Runs one call at a time. See the module docstring."""
    progressed = QtCore.Signal(float, str)
    finished = QtCore.Signal(object)
    failed = QtCore.Signal(object)
    cancelled = QtCore.Signal()
    ended = QtCore.Signal()
    _worker_done = QtCore.Signal()
    _worker_progress = QtCore.Signal(float, str)

    PUMP_SECONDS = 0.04
    REPORT_SECONDS = 0.03

    def __init__(self, parent=None, mode="threaded", pump=None):
        super(TaskRunner, self).__init__(parent)
        self.mode = mode
        self._pump = pump or QtWidgets.QApplication.processEvents
        self._running = False
        self._cancel = False
        self._thread = None
        self._last_pump = 0.0
        self._last_report = 0.0
        self.name = ""
        self.result = None
        self.error = None
        self.trace = ""
        self.was_cancelled = False
        self.last_fraction = 0.0
        self.last_text = ""
        self._worker_done.connect(self._finish)
        self._worker_progress.connect(self._on_worker_progress)

    # -- state ------------------------------------------------------------------------------------

    def is_running(self):
        return self._running

    def is_cancel_requested(self):
        return self._cancel

    def cancel(self):
        """Ask the running task to stop; it stops at its next `progress.check()`."""
        self._cancel = True

    def wait(self, timeout=None):
        """Block until a threaded task ended (tests); the end signals still need the event loop."""
        if self._thread is not None:
            self._thread.join(timeout)
        return not self._running

    # -- start ------------------------------------------------------------------------------------

    def start(self, func, name="", mode=None):
        """Start `func(progress)`. False when a task is already running."""
        if self._running:
            return False
        self.mode = mode or self.mode
        self.name = name
        self._running, self._cancel = True, False
        self.result, self.error, self.trace, self.was_cancelled = None, None, "", False
        self.last_fraction, self.last_text = 0.0, ""
        cooperative = self.mode == "cooperative"
        progress = Progress(self._report_cooperative if cooperative else self._report_threaded, self.is_cancel_requested,
                            tick=self._tick if cooperative else None)
        if cooperative:
            self._run(func, progress)
            self._finish()
        else:
            self._thread = threading.Thread(target=self._run_and_signal, args=(func, progress), name="aero-" + (name or "task"), daemon=True)
            self._thread.start()
        return True

    def _run_and_signal(self, func, progress):
        self._run(func, progress)
        self._worker_done.emit()

    def _run(self, func, progress):
        try:
            self.result = func(progress)
            if self._cancel:
                self.was_cancelled = True
        except Cancelled:
            self.was_cancelled = True
        except BaseException as exc:                  # the window must hear of every failure
            self.error = exc
            self.trace = traceback.format_exc()

    # -- reports ----------------------------------------------------------------------------------

    def _report_threaded(self, fraction, text):
        self.last_fraction, self.last_text = fraction, text
        now = time.time()
        if fraction >= 1.0 or now - self._last_report >= self.REPORT_SECONDS:
            self._last_report = now
            self._worker_progress.emit(fraction, text)

    def _report_cooperative(self, fraction, text):
        self.last_fraction, self.last_text = fraction, text
        self.progressed.emit(fraction, text)

    def _tick(self):
        now = time.time()
        if now - self._last_pump >= self.PUMP_SECONDS:
            self._last_pump = now
            self._pump()

    def _on_worker_progress(self, fraction, text):
        self.progressed.emit(fraction, text)

    # -- end --------------------------------------------------------------------------------------

    def _finish(self):
        if not self._running:
            return
        self._running = False
        self._thread = None
        if self.error is not None:
            try:
                journal.write("Task {!r} failed: {}".format(self.name, self.trace.strip().splitlines()[-1] if self.trace else self.error))
            except Exception:
                pass
            self.failed.emit(self.error)
        elif self.was_cancelled:
            self.cancelled.emit()
        else:
            self.progressed.emit(1.0, self.last_text)
            self.finished.emit(self.result)
        self.ended.emit()

    # -- convenience ------------------------------------------------------------------------------

    def bind_row(self, row):
        """Show the progress in a `ProgressRow` and let its Cancel button cancel this task."""
        self.progressed.connect(row.set_progress)
        row.cancelRequested.connect(self.cancel)
        return row


def describe(exc, trace=""):
    """(message, details) of a failure for a `Banner`: one plain line, and the last lines of the traceback."""
    lines = [line for line in str(trace).strip().splitlines() if line.strip()]
    return plain_error(exc), "\n".join(lines[-8:])
