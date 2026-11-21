"""The contract between AeroPPK and a drone: what files exist, what the photo events are.

A drone reader is the only drone specific piece of AeroPPK. To support a new model, write a class with the same methods as
`DroneReader` in a new module of this folder, register it in `__init__.py`, and add tests with a few real file samples.
Everything after the reader (stations, solver, combination, Metashape, report) only sees `PhotoEvent` and `FlightFiles`.
"""

import os


class PhotoEvent(object):
    """One exposure.

    `time` is GPS time in seconds since the GPS epoch (see gnsstime.to_seconds), never UTC.
    `offset_ned` is the vector from the GNSS antenna phase centre to the camera centre in local north / east / down metres
    (lever arm). `onboard` is what the drone itself computed (real time position, ellipsoidal height) and is used only for
    comparison and sanity checks, never as the PPK result.
    """

    def __init__(self, index, time, photo=None):
        self.index = index
        self.time = time
        self.photo = photo                 # absolute path of the image, or None when it is missing
        self.offset_ned = (0.0, 0.0, 0.0)
        self.onboard = None                # (lat, lon, h) of the drone's own solution
        self.onboard_q = None              # drone status code (DJI: 0 none, 16 single, 34 float, 50 fix)
        self.onboard_sd = None             # (n, e, u) metres
        self.photo_name = ""


class Need(object):
    """A line of the checklist shown before any calculation: a file or a fact, present or not, and why it matters."""

    BLOCKING, ADVISED, OPTIONAL = "blocking", "advised", "optional"

    def __init__(self, key, present, level, detail="", path=None):
        self.key = key                     # text key suffix: aeroppk.need.<key> and aeroppk.why.<key>
        self.present = bool(present)
        self.level = level
        self.detail = detail               # ready to display extra information (count, name, ...)
        self.path = path


class FlightFiles(object):
    """What was found for one flight folder."""

    def __init__(self, folder, reader_id):
        self.folder = folder
        self.reader_id = reader_id
        self.name = os.path.basename(folder.rstrip("/\\"))
        self.photos = []                   # absolute paths
        self.events_file = None            # timestamp / event file
        self.obs = None                    # rover observation file (RINEX)
        self.nav = None                    # rover navigation file (RINEX)
        self.raw = None                    # raw receiver log, optional
        self.extra = {}

    def has_blocking_gap(self, needs):
        return any(n.level == Need.BLOCKING and not n.present for n in needs)


class DroneReader(object):
    """Interface of a drone reader (documentation only: duck typing, nothing to inherit)."""

    id = ""            # short technical id, used in settings and reports
    name = ""          # shown to the user, e.g. "DJI Mavic 3 Enterprise"
    antenna_known = False   # True when `read_events` gives the true antenna -> camera vector; False = the user may type the height
    exif_models = ()   # EXIF model strings of the photos of this drone (used to detect it)

    def accepts(self, folder):
        """True when `folder` looks like a flight of this drone (a fast test on file names, no image decoding)."""
        raise NotImplementedError

    def scan(self, folder):
        """List the files of the flight -> FlightFiles."""
        raise NotImplementedError

    def needs(self, files):
        """The checklist -> list of Need."""
        raise NotImplementedError

    def read_events(self, files):
        """Exposure events -> (list of PhotoEvent, list of warning texts)."""
        raise NotImplementedError
