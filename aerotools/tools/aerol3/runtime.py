"""Aero-L3 long calls: `Cancelled` and `Progress` (progress and cancel callbacks) for the backends and the scan.

No Qt in this module. The window runs a call through the kit (`ToolWindow.run`) and hands the backend plain `progress(fraction, text)` and
`cancelled()` callbacks; a progress callback may be called from a worker thread, so it only reports and never touches a widget.
"""

import time


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
