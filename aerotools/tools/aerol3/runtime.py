"""Aero-L3 long calls: `Cancelled`, `Progress` (progress and cancel callbacks) and `Task` (a call run on a worker thread, watched by polling).

No Qt in this module. A progress callback is called from the worker thread, so it only writes plain attributes: the window reads them with a
timer (`Task.fraction`, `text`, `items`, `done`) and never the other way round.
"""

import threading
import time
import traceback


class Cancelled(Exception):
    """Raised by a backend (or by `Progress.check`) when the operator cancelled a long call."""


class Progress:
    """Wraps a `progress(fraction, text)` callback and a `cancelled()` callback; both may be None.

    A stage of a bigger job takes a `sub(lo, hi)` view whose fractions are mapped into [lo, hi] of the parent, so each
    function reports 0..1 for its own work. `check()` raises `Cancelled`."""

    def __init__(self, callback=None, cancelled=None, lo=0.0, hi=1.0, throttle=0.0):
        self.callback = callback
        self.cancelled = cancelled
        self.lo = float(lo)
        self.hi = float(hi)
        self.throttle = float(throttle)      # minimum seconds between two callbacks (the last one, 1.0, always goes through)
        self._last = 0.0
        self.fraction = 0.0
        self.text = ""

    def __call__(self, fraction, text=""):
        fraction = min(1.0, max(0.0, float(fraction)))
        self.fraction = self.lo + (self.hi - self.lo) * fraction
        self.text = text or self.text
        if self.callback is not None:
            now = time.time()
            if self.throttle <= 0 or fraction >= 1.0 or now - self._last >= self.throttle:
                self._last = now
                self.callback(self.fraction, self.text)
        self.check()

    def is_cancelled(self):
        return bool(self.cancelled is not None and self.cancelled())

    def check(self):
        """Raise `Cancelled` if the operator asked to stop."""
        if self.is_cancelled():
            raise Cancelled()

    def sub(self, lo, hi):
        """A view of this progress that maps 0..1 onto [lo, hi] of it."""
        span = self.hi - self.lo
        child = Progress(self.callback, self.cancelled, self.lo + span * lo, self.lo + span * hi, self.throttle)
        child._last = self._last
        return child

    def step(self, i, n, text=""):
        """Report 'i of n done' (i = number of completed items)."""
        self(0.0 if n <= 0 else i / float(n), text)

    @classmethod
    def of(cls, progress=None, cancelled=None):
        """Accept a `Progress` as is, or build one from plain callbacks (either may be None)."""
        if isinstance(progress, cls):
            return progress
        return cls(progress, cancelled)


class Task(object):
    """`func(progress, cancelled)` runs on a daemon thread. `progress(fraction, text)` and `cancelled()` are what the backend gets.

    While it runs the worker may also write `items[key] = (state, text)` (the window shows these per flight). When it ends, exactly
    one of `result`, `error` (an exception, `trace` has the traceback) or `was_cancelled` tells how, and `done` becomes True last."""

    def __init__(self, func, name="", items=None, extras=None):
        self.func = func
        self.name = name
        self.fraction = 0.0
        self.text = ""
        self.items = items if items is not None else {}
        self.extras = extras if extras is not None else {}      # anything else the worker hands over (thumbnails...)
        self.result = None
        self.error = None
        self.trace = ""
        self.was_cancelled = False
        self.done = False
        self._cancel = False
        self._thread = None

    # -- called by the worker -------------------------------------------------------------------

    def progress(self, fraction, text=""):
        try:
            self.fraction = min(1.0, max(0.0, float(fraction)))
        except (TypeError, ValueError):
            pass
        if text:
            self.text = str(text)

    def cancelled(self):
        return self._cancel

    def _run(self):
        try:
            self.result = self.func(self.progress, self.cancelled)
            stopped_inside = any(isinstance(v, tuple) and v and v[0] == "cancelled" for v in list(self.items.values()))
            if self._cancel or stopped_inside:                  # also when the job was stopped from outside (Metashape's Cancel button)
                self.was_cancelled = True
        except Cancelled:
            self.was_cancelled = True
        except BaseException as exc:                          # the window must hear of every failure, whatever it is
            self.error = exc
            self.trace = traceback.format_exc()
        finally:
            self.done = True

    # -- called by the window -------------------------------------------------------------------

    def start(self, threaded=True):
        """Start the call. `threaded=False` runs it right here (tests, or a backend that must stay on the main thread)."""
        if threaded:
            self._thread = threading.Thread(target=self._run, name="aerol3-" + (self.name or "task"), daemon=True)
            self._thread.start()
        else:
            self._run()
        return self

    def cancel(self):
        self._cancel = True

    def is_cancel_requested(self):
        return self._cancel

    def wait(self, timeout=None):
        if self._thread is not None:
            self._thread.join(timeout)
        return self.done


def plain_error(error):
    """One plain sentence for an exception coming from a backend (the backend writes plain English messages)."""
    text = str(error).strip()
    if not text:
        text = type(error).__name__
    return text if len(text) < 400 else text[:397] + "..."
