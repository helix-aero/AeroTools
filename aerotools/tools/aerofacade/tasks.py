"""Aero-Facade UI: a long backend call run on a worker thread, watched by polling. No Qt in this module.

The rule that came out of a Metashape crash: a progress callback is called from the worker thread, so it must only write plain
attributes. The window reads them with a timer (`Task.fraction`, `text`, `items`, `done`) and never the other way round.
"""

import threading
import traceback

from .backend import Cancelled


class Task(object):
    """`func(progress, cancelled)` runs on a daemon thread. `progress(fraction, text)` and `cancelled()` are what the backend gets.

    While it runs the worker may also write `items[key] = (state, text)` (the window shows these per facade). When it ends, exactly
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
            self._thread = threading.Thread(target=self._run, name="aerofacade-" + (self.name or "task"), daemon=True)
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
