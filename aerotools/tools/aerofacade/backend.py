"""Aero-Facade `Backend` protocol (frozen: see docs/AERO_FACADE_SPEC.md) plus `Cancelled` and `Progress`.

The wizard talks only to a `Backend`. Two implementations exist: `fake_backend.FakeBackend` (synthetic building, tests and
screenshots) and `msfacade.MetashapeBackend` (the Metashape adapter). Pure Python 3.9, no Qt, no Metashape.

Conventions common to every method:

* `progress(fraction, text)`: fraction in [0, 1], text is a short plain-English line. Called from the worker, never touch a Qt widget in it.
* `cancelled()`: returns True when the operator pressed Cancel. A backend checks it between steps and raises `Cancelled` (or returns
  what is done so far, as documented per method).
* `facades`: list of `model.Facade`; removed ones (`removed=True`) must be skipped by the caller, the backend ignores them anyway.
* `margins`: a `model.Margins` (the same for all facades) or a dict `letter -> Margins`; use `model.margins_for(margins, facade)`.
* `method`: a `model.Method` (or its key string).
* Coordinates: local east/north/up metres of the chunk for `Facade` frames; the polygon and the orthomosaic preview extent are in
  `project_info()["crs"]` metres. A backend that has two frames converts inside; the UI never converts.
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


class Backend:
    """What the wizard needs from the outside world. Every method raises NotImplementedError here."""

    def project_info(self):
        """{"chunk": label, "site": str, "date": "YYMMDD", "outputs": folder, "crs": str}  (crs: name of the plan-view frame, metres)."""
        raise NotImplementedError

    def is_current(self):
        """True while the chunk this backend works on is still the active chunk of the project (a window that was left open is rebuilt otherwise)."""
        return True

    def availability(self):
        """{"tie_points": (ok, reason), "depth_maps": (ok, reason), "dense_cloud": (ok, reason)}; reason is plain English, '' when ok.
        `model.Availability.from_dict` wraps it."""
        raise NotImplementedError

    def ortho_preview(self, max_px=1600):
        """(RGB uint8 array H x W x 3, extent (left, bottom, right, top) in `crs` metres) of the chunk's orthomosaic, longest side
        <= max_px. Raises if the chunk has no orthomosaic."""
        raise NotImplementedError

    def detect(self, polygon, progress, cancelled):
        """Find the facades inside `polygon` ([(x, y)], preview crs metres) from the tie points. Runs off the UI thread.
        Returns list[Facade], named A, B, C ... in `model.assign_names` order. Raises `Cancelled` if cancelled."""
        raise NotImplementedError

    def existing_dems(self, facades, margins, method):
        """{letter: True/False}: is the DEM of this facade already in the chunk for these margins and this method (assets we tagged)?"""
        raise NotImplementedError

    def build_dems(self, facades, margins, method, progress, cancelled):
        """Build the DEMs one after the other. Returns {letter: (ok, message)}; a failure of one facade never stops the others.
        On cancel, returns what was done (the others are simply absent)."""
        raise NotImplementedError

    def build_orthos(self, facades, margins, method, pixel_m, progress, cancelled):
        """Build the orthophoto of each facade at `pixel_m` metres per pixel. Returns {letter: (ok, message)}."""
        raise NotImplementedError

    def thumbnail(self, facade):
        """Small RGB uint8 array (about 240 px wide) of the built orthophoto of `facade`."""
        raise NotImplementedError

    def height_context(self):
        """Where the heights of the drawing sheet can be read: {"epsg": EPSG code of the projected (or geographic) CRS or None when the chunk
        has no usable coordinate system, "lonlat": (longitude, latitude) of the site or None, "crs_name": text}. The export screen offers the
        height systems of that CRS at that place (`aeroexports.vertical.compatible_verticals`). Default: nothing known (relative heights only)."""
        return {"epsg": None, "lonlat": None, "crs_name": ""}

    def export(self, facades, folder, fmt, options, progress, cancelled):
        """Write one file per facade into `folder` (created if needed), named `<site>_<date>_facade_<letter>.<ext>`.
        `fmt`: 'png' | 'jpg' | 'tif' | 'jp2' | 'ecw'; `options`: dict {'georef_txt': bool, 'dxf': bool (3D DXF of each picture),
        'pictures': bool (default True: the loose pictures; False only counts with a sheet), 'tools': {'gm', 'acad', 'gdal'} paths (default: detected),
        'sheet': {'dxf': bool, 'dwg': bool, 'z': bool, 'vertical': id | None, 'titles': 'fr' | 'en'}} (see `sheetout.normalise` and the drawing sheet
        section of docs/AERO_FACADE_SPEC.md). Returns [(path, ok, message)], the sheet's files included."""
        raise NotImplementedError
