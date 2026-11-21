"""Work that runs in a background thread for the Aero-PPK window: the job object, the fetch of a base, the whole calculation.

No Qt and no Metashape here (the window polls a `Job` with a timer; the Metashape API is only touched from the main thread).
"""

import datetime
import os
import threading

from . import combine, devices, ionosphere, pipeline
from .stations import catalog, rgp, survey


class Job(object):
    """One background task. `fn(post, cancelled)` runs in a thread; the window reads `messages` and `state` from its timer."""

    def __init__(self, fn, tag=None):
        self.tag = tag
        self.messages = []
        self.state = "running"      # running, done, failed, stopped
        self.result = None
        self.error = ""
        self._stop = threading.Event()
        self._fn = fn
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            self.result = self._fn(self.post, self._stop.is_set)
            self.state = "stopped" if self._stop.is_set() else "done"
        except pipeline.Cancelled:
            self.state = "stopped"
        except pipeline.PipelineError as exc:
            self.error = str(exc)
            self.state = "failed"
        except Exception as exc:                                  # pragma: no cover - shown to the user
            self.error = "%s: %s" % (type(exc).__name__, exc)
            self.state = "failed"

    def post(self, kind, **fields):
        with self._lock:
            self.messages.append((kind, fields))

    def drain(self):
        with self._lock:
            out, self.messages = self.messages, []
        return out

    def stop(self):
        self._stop.set()

    def wait(self, timeout=None):
        self._thread.join(timeout)


# ---------------------------------------------------------------------------------------------------------------------
# Windows of time
# ---------------------------------------------------------------------------------------------------------------------

def union_window(flights, margin_s=60.0):
    """The time span of all the flights (a `pipeline.Window`), padded by `margin_s`. None when no flight has a usable span."""
    windows = []
    for flight in flights:
        try:
            windows.append(pipeline.flight_window(flight["files"], flight["events"], margin_s))
        except pipeline.PipelineError:
            continue
    if not windows:
        return None
    return pipeline.Window(min(w.start for w in windows), max(w.end for w in windows))


def widened(window, hours):
    """The window with `hours` more before and after: what is asked of the base station servers."""
    pad = datetime.timedelta(hours=float(hours))
    return pipeline.Window(window.start - pad, window.end + pad)


# ---------------------------------------------------------------------------------------------------------------------
# One base of the basket
# ---------------------------------------------------------------------------------------------------------------------

def survey_one(station, flight_window, hours, post=None, cancelled=None, opener=None):
    """What a station offers for the window (a few small requests): the `survey.Offer`."""
    return survey.survey_station(station, widened(flight_window, hours), opener=opener, cancelled=cancelled)


def info_of(base, offer=None):
    """The facts shown under a basket row, from a downloaded base (and the survey when it is all there is)."""
    if base is not None:
        info = base.info
        return {"rate": info.interval or base.spec.product.interval, "systems": "".join(info.systems), "receiver": info.receiver,
                "antenna": info.antenna, "product": base.spec.product.label, "first": info.first, "last": info.last}
    if offer is not None and offer.available():
        return {"rate": offer.interval, "systems": "".join(offer.systems), "receiver": offer.info.get("receiver", ""),
                "antenna": offer.info.get("antenna", ""), "product": offer.best.label, "first": None, "last": None}
    return {}


FINE_S = 5.0                     # a product sampled at this rate or finer is "1 Hz class": nothing coarser is worth a second look


def _coarse(product):
    return product.interval is not None and product.interval > FINE_S


def find_products(provider, station, flight_window, hours, cancelled=None, opener=None):
    """The products to try for a station, best first: those of the wide window (flights plus `hours` before and after), unless the
    narrow window (the flights alone, plus the margin) has a finer one.

    A 1 Hz file of the flight itself is often there when the wide window is not complete yet (the last hour of a flight of yesterday is not
    published), while a 30 s daily file covers the whole wide window: the finer sampling wins. Raises net.NetError."""
    products = provider.probe(station, *_ends(widened(flight_window, hours)), cancelled=cancelled, opener=opener)
    if hours and (not products or _coarse(products[0])) and not (cancelled is not None and cancelled()):
        narrow = provider.probe(station, flight_window.start, flight_window.end, cancelled=cancelled, opener=opener)
        if narrow and (not products or (narrow[0].interval is not None and products[0].interval is not None
                                        and narrow[0].interval < products[0].interval)):
            products = narrow
    return products


def fetch_base(station, flight_window, hours, programs, work_root, post=None, cancelled=None, opener=None, products=None):
    """Find, download and unpack the files of one station for the flights. Returns a `pipeline.BaseData`.

    The files asked for cover the flights plus `hours` before and after. When the server does not have all of that yet (a
    flight of today), the narrow window is tried before giving up. `products` = what a survey of the same window already found (best
    first): the server is not asked again, unless the best of them is coarser than 5 s (the narrow window may have a finer one).
    Raises `pipeline.PipelineError` with a sentence for the user."""
    from .stations import net
    provider = catalog.provider_of(station) or rgp.PROVIDER
    try:
        if not products or _coarse(products[0]):
            products = find_products(provider, station, flight_window, hours, cancelled=cancelled, opener=opener)
    except net.NetError as exc:
        raise pipeline.PipelineError("Cannot reach the server: %s" % exc)
    if cancelled is not None and cancelled():
        raise pipeline.Cancelled()
    if not products:
        raise pipeline.PipelineError("No data for this station on that day.")
    distance = station.distance_m
    if distance is None:
        distance = 0.0
    spec = pipeline.BaseSpec(station, products[0], distance)
    return pipeline.prepare_base(spec, flight_window, os.path.join(work_root, "bases"), programs,
                                 progress=(lambda step, f: post("fraction", value=f)) if post else None, cancelled=cancelled,
                                 opener=opener)


def _ends(window):
    return window.start, window.end


# ---------------------------------------------------------------------------------------------------------------------
# The calculation
# ---------------------------------------------------------------------------------------------------------------------

def calculate(
        flights,
        bases,
        site,
        programs,
        options,
        post,
        cancelled,
        work_root=None,
):
    """Run the PPK calculation of every flight with the prepared `bases`. Returns {"results": [FlightResult], "errors": [...]}.

    Messages posted: "flight" (k, n, name), "stage" (name, code), "fraction" (value 0..1), "run" (flight index, base code, q list)
    when a base is finished."""
    results, errors = [], []
    work_root = work_root or os.path.join(rgp.cache_dir(), "work")
    for k, flight in enumerate(flights):
        files, events = flight["files"], flight["events"]
        if flight.get("antenna_z"):
            events = devices.with_antenna_height(events, flight["antenna_z"])
        post("flight", k=k + 1, n=len(flights), name=files.name)
        try:
            window = pipeline.flight_window(files, events, 60.0)
            count = [0]

            def on_run(run, k=k):
                count[0] += 1
                post("run", flight=k, code=run.base.name, q=[int(v) for v in run.interp["q"]])

            def progress(step, f, k=k):
                post("fraction", value=(k + f * 0.95) / len(flights))
                post("stage", name="solve", code=bases[min(len(bases) - 1, int(f * len(bases)))].name if bases else "")
            result = pipeline.compute(files, events, window, bases, options, programs, os.path.join(work_root, files.name, "solve"),
                                      progress=progress, cancelled=cancelled, on_run=on_run,
                                      ion_fetch=ionosphere.fetch_broadcast,
                                      )
            post("stage", name="combine", code="")
            results.append(result)
        except pipeline.PipelineError as exc:
            errors.append("error:%s:%s" % (files.name, exc))
    post("fraction", value=1.0)
    return {"results": results, "errors": errors}
