"""One background thread for the windows that compute outside the Qt thread (Aero-GCP, Aero-Space).

The job gets the worker as its only argument: it reads `worker.cancel` to stop early and writes `worker.progress`.
Never call the Metashape API from a job: read what it needs beforehand, in the main thread.
"""

import threading


class Worker(threading.Thread):
    """Run `job(worker)` outside the Qt thread; the window polls `finished` and `result`."""

    def __init__(self, job):
        super(Worker, self).__init__(daemon=True)
        self.job = job
        self.progress = (0, 0, "")
        self.result = None
        self.finished = False
        self.cancel = False                  # asked to stop (the job decides when it looks)
        self.abandoned = False               # the window that wanted the result is gone: nobody will read it

    def run(self):
        try:
            self.result = self.job(self)
        except Exception as exc:
            self.result = {"error": repr(exc)}
        self.finished = True

    def abandon(self):
        self.abandoned = self.cancel = True


def advance(progress, done, total, text=""):
    """Report `done of total` to a kit `Progress` from a callback that must not raise (the updater and the installer ask `cancelled()`
    themselves and put the original files back): the stop request is read by them, not thrown through their code."""
    from .uikit.tasks import Cancelled
    try:
        progress.step(done, total, text)
    except Cancelled:
        pass


def tick(progress):
    """Let a cooperative task's window breathe from a callback that must not raise (a pipeline that polls `cancelled()` itself and
    returns its own "stopped" result): pumps Qt, rate-limited, like `progress.check()` but without throwing `Cancelled`."""
    from .uikit.tasks import Cancelled
    try:
        progress.check()
    except Cancelled:
        pass
