"""Hook point of the automatic target detection (AIGCP).

This module contains no AI: it defines the types through which a detector
plugs in. The AIGCP detector (checker, spray) is in aigcp.py; the panel
drives it directly ("AI detection" button), asynchronously.

AIGCP file contract (see aigcp.py and aigcp_runner.py):
  - FastGCP writes a `work.json`: model weights, confidence threshold, and a
    list of items {id, image_path, x_est, y_est, px_per_m}.
  - A separate executable, in an external Python environment, reads this file
    and writes `hits.json`: a list of {id, found, x, y, conf, crop_px}.
  - torch, ultralytics and CUDA are NEVER imported in the Metashape
    Python: all the computation goes through this executable and these two files.
  - A projection placed by the AI is placed with `pinned=False`, a manual
    projection with `pinned=True` (msapi.set_projection).

A detector plugs in like this:
    from aerotools.tools.fastgcp import detection
    detection.register(MyDetector())
"""

import collections

from ... import journal

# A pin proposal made by a detector.
#   marker_label : label of the marker concerned
#   camera_key   : key (camera.key) of the photo
#   x, y         : sensor pixels
#   confidence   : 0.0 to 1.0
#   source       : name of the detector that produced it
Proposal = collections.namedtuple(
    "Proposal", ["marker_label", "camera_key", "x", "y", "confidence", "source"]
)


class Detector(object):
    """Base of a target detector. Does nothing by default."""

    name = "detector"

    def status(self):
        """(ok, message): is the detector ready to serve?"""
        return True, ""

    def propose(self, chunk, marker, cameras):
        """List of `Proposal` for this marker on these photos (empty here)."""
        return []

    def on_pin_placed(self, record):
        """Called after each manual pin. `record`: dict, see dataset.py."""
        return None


class NullDetector(Detector):
    """Default detector: none, as long as AIGCP is not installed."""

    name = "none"

    def status(self):
        return False, "No detector installed (AIGCP coming)"


_NULL = NullDetector()
_active = _NULL


def register(detector):
    """Installs `detector` as the current detector. `None` goes back to the NullDetector."""
    global _active
    _active = detector if detector is not None else _NULL
    return _active


def active():
    """Current detector (NullDetector if none is registered)."""
    return _active


def notify_pin(record):
    """Notifies the current detector of a manual pin. Never raises."""
    try:
        active().on_pin_placed(record)
    except Exception as exc:
        journal.write("detector: {!r}".format(exc))
