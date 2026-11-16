"""Aero-L3: find the photos of an L3 folder and read what each one says (position, RTK status, angles, calibration, orientation).

Pure Python 3.9, no Metashape, no Qt. A "flight" is one folder (DJI makes one folder per flight, named `DJI_<date>_<n>_<mission>`); a folder
called `LV` or `RV` (photos sorted by lens) belongs to the flight of its parent. The photos of the two lenses of one shot share a stem:
`DJI_20300101130012_0003_LV.JPG` and `..._RV.JPG`.
"""

import os
import re
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

from .runtime import Cancelled, Progress
from . import mrk, orient, xmpdji

PHOTO_EXTENSIONS = (".jpg", ".jpeg")
LIDAR_EXTENSIONS = (".ldr", ".ldrt", ".imu", ".rpos", ".rtk", ".rtb", ".rtl", ".clc", ".cli", ".sig", ".dbg")
LENS_FOLDERS = ("LV", "RV")
_STEM = re.compile(r"^(?P<stem>.*?)(?:_(?P<lens>[LR])V)?$", re.IGNORECASE)
RTK_NAMES = {50: "fix", 34: "float", 16: "single", 0: "none"}


class PhotoRecord(object):
    """Everything the tool keeps about one photo. Plain attributes, nothing computed lazily."""

    __slots__ = ("path", "name", "stem", "lens", "flight", "width", "height", "exif_orientation", "make", "model", "product", "drone",
                 "serial", "source", "lat", "lon", "alt", "alt_type", "gps_status", "rtk_flag", "rtk_std", "rel_alt", "gps_time",
                 "local_time", "dewarp_flag", "dewarp_data", "cal_focal", "focal_mm", "focal_35mm", "angles", "size", "lens_conflict",
                 "has_dji_xmp")

    def __init__(self, path):
        self.path = path
        self.name = os.path.basename(path)
        self.stem = ""
        self.lens = ""
        self.flight = ""
        self.width = self.height = 0
        self.exif_orientation = None
        self.make = self.model = self.product = self.drone = self.serial = self.source = ""
        self.lat = self.lon = self.alt = None
        self.alt_type = self.gps_status = ""
        self.rtk_flag = None
        self.rtk_std = None
        self.rel_alt = None
        self.gps_time = None
        self.local_time = ""
        self.dewarp_flag = None
        self.dewarp_data = ""
        self.cal_focal = None
        self.focal_mm = self.focal_35mm = None
        self.angles = {}
        self.size = 0
        self.lens_conflict = False
        self.has_dji_xmp = False

    @property
    def has_position(self):
        return self.lat is not None and self.lon is not None and self.alt is not None and not (self.lat == 0 and self.lon == 0)

    @property
    def rtk_name(self):
        return RTK_NAMES.get(self.rtk_flag, "none" if self.rtk_flag is None else "other")

    @property
    def station_key(self):
        """Key shared by the two lenses of one shot (the same flight, the same stem)."""
        return (self.flight, self.stem)


class FlightInfo(object):
    """The photos of one folder and the files next to them."""

    def __init__(self, key):
        self.key = key
        self.name = os.path.basename(key.rstrip("\\/")) or key
        self.photos = []
        self.mrk_files = []
        self.mrk_shots = []
        self.mrk_ignored = 0           # lines of the MRK files that were not understood, or files that could not be read
        self.lidar_files = 0
        self.report_files = 0

    @property
    def has_mrk(self):
        return bool(self.mrk_files)


class ScanResult(object):
    def __init__(self, root):
        self.root = root
        self.flights = []
        self.records = []
        self.unreadable = []            # [(path, reason)]
        self.foreign = []               # JPEG files without DJI metadata (not photos of the drone)
        self.cancelled = False

    def flight(self, key):
        for flight in self.flights:
            if flight.key == key:
                return flight
        return None


def flight_key(path):
    """Folder that stands for the flight of a photo: its own folder, or the parent of an `LV` / `RV` sorting folder."""
    folder = os.path.dirname(path)
    if os.path.basename(folder).upper() in LENS_FOLDERS:
        folder = os.path.dirname(folder)
    return folder


def split_name(name):
    """(stem, lens or '') of a photo file name: `DJI_..._0003_LV.JPG` -> (`DJI_..._0003`, 'L')."""
    base = os.path.splitext(name)[0]
    m = _STEM.match(base)
    stem = m.group("stem") if m else base
    lens = (m.group("lens") or "").upper() if m else ""
    return stem, lens


def _lens_from_source(source):
    source = (source or "").upper()
    if source.startswith("LEFT"):
        return orient.LEFT
    if source.startswith("RIGHT"):
        return orient.RIGHT
    return ""


def record_from_info(path, info, size=0):
    """The `PhotoRecord` of a file from what `xmpdji.read_jpeg` returned (pure: tests feed it synthetic `JpegInfo`)."""
    rec = PhotoRecord(path)
    xmp, exif = info.xmp, info.exif
    rec.size = size
    rec.width, rec.height = info.width, info.height
    rec.exif_orientation = exif.get("orientation")
    rec.make = xmp.get("tiff:Make") or exif.get("make", "")
    rec.model = xmp.get("tiff:Model") or exif.get("model", "")
    rec.product = xmpdji.text(xmp, "ProductName")
    rec.drone = xmpdji.text(xmp, "DroneModel")
    rec.serial = xmpdji.text(xmp, "CameraSerialNumber")
    rec.source = xmpdji.text(xmp, "ImageSource")
    rec.has_dji_xmp = any(key.startswith("drone-dji:") for key in xmp)
    rec.stem, name_lens = split_name(rec.name)
    xmp_lens = _lens_from_source(rec.source)
    rec.lens = xmp_lens or name_lens
    rec.lens_conflict = bool(xmp_lens and name_lens and xmp_lens != name_lens)
    rec.flight = flight_key(path)
    rec.lat = xmpdji.number(xmp, "GpsLatitude", exif.get("gps_lat"))
    rec.lon = xmpdji.number(xmp, "GpsLongitude", exif.get("gps_lon"))
    rec.alt = xmpdji.number(xmp, "AbsoluteAltitude", exif.get("gps_alt"))
    rec.rel_alt = xmpdji.number(xmp, "RelativeAltitude")
    rec.alt_type = xmpdji.text(xmp, "AltitudeType")
    rec.gps_status = xmpdji.text(xmp, "GpsStatus")
    flag = xmpdji.number(xmp, "RtkFlag")
    rec.rtk_flag = int(flag) if flag is not None else None
    std = [xmpdji.number(xmp, k) for k in ("RtkStdLat", "RtkStdLon", "RtkStdHgt")]
    rec.rtk_std = tuple(std) if all(v is not None for v in std) else None
    rec.gps_time = mrk.parse_gps_stamp(xmpdji.text(xmp, "UTCAtExposure"))
    rec.local_time = exif.get("datetime_original", "") or xmp.get("xmp:CreateDate", "")
    rec.dewarp_flag = xmpdji.number(xmp, "DewarpFlag")
    rec.dewarp_data = xmpdji.text(xmp, "DewarpData")
    rec.cal_focal = xmpdji.number(xmp, "CalibratedFocalLength")
    rec.focal_mm = exif.get("focal_length")
    rec.focal_35mm = exif.get("focal_length_35mm")
    rec.angles = {
        "camera_yaw": xmpdji.number(xmp, "CameraYawDegree"), "camera_pitch": xmpdji.number(xmp, "CameraPitchDegree"),
        "camera_roll": xmpdji.number(xmp, "CameraRollDegree"),
        "gimbal_yaw": xmpdji.number(xmp, "GimbalYawDegree"), "gimbal_pitch": xmpdji.number(xmp, "GimbalPitchDegree"),
        "gimbal_roll": xmpdji.number(xmp, "GimbalRollDegree"),
        "flight_yaw": xmpdji.number(xmp, "FlightYawDegree"), "flight_pitch": xmpdji.number(xmp, "FlightPitchDegree"),
        "flight_roll": xmpdji.number(xmp, "FlightRollDegree"),
    }
    return rec


def read_record(path):
    """`PhotoRecord` of a file on disk. Raises `xmpdji.JpegError` or OSError."""
    info = xmpdji.read_jpeg(path)
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    return record_from_info(path, info, size)


def find_files(root, cancelled=None):
    """(photo paths, {folder: [other file names]}) under `root`, folders visited in name order. `cancelled()` is polled between folders."""
    photos = []
    others = {}
    for folder, dirs, names in os.walk(root):
        if cancelled is not None and cancelled():
            raise Cancelled()
        dirs[:] = sorted(d for d in dirs if not d.startswith("$") and d.lower() != "system volume information")
        for name in sorted(names):
            if name.lower().endswith(PHOTO_EXTENSIONS):
                photos.append(os.path.join(folder, name))
            else:
                others.setdefault(folder, []).append(name)
    return photos, others


def scan_folder(root, progress=None, cancelled=None, workers=6, reader=None):
    """Scan `root` (a flight folder, a mission folder with several flights, or a folder of photos sorted by lens).

    `progress(fraction, text)` and `cancelled()` are plain callbacks (see `Progress`). `reader` replaces `read_record` (tests). Returns a `ScanResult`;
    when cancelled the result holds what was read so far and `cancelled` is True."""
    prog = Progress.of(progress, cancelled)
    reader = reader or read_record
    result = ScanResult(root)
    prog(0.0, "Looking for photos")
    try:
        paths, others = find_files(root, prog.is_cancelled)
    except Cancelled:
        result.cancelled = True
        return result
    flights = OrderedDict()
    total = len(paths)

    def one(path):
        try:
            return path, reader(path), None
        except Exception as exc:                          # any unreadable file is reported, never fatal
            return path, None, "{}: {}".format(type(exc).__name__, exc)

    done = 0
    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
        for path, rec, error in pool.map(one, paths):
            done += 1
            if prog.is_cancelled():
                result.cancelled = True
                pool.shutdown(wait=False, cancel_futures=True)
                break
            if rec is None:
                result.unreadable.append((path, error or ""))
            elif not rec.has_dji_xmp and not rec.make.upper().startswith("DJI"):
                result.foreign.append(path)
            else:
                result.records.append(rec)
                flights.setdefault(rec.flight, FlightInfo(rec.flight)).photos.append(rec)
            if done % 50 == 0 or done == total:
                prog(0.05 + 0.9 * done / float(max(1, total)), "{} of {} photos read".format(done, total))
    for key, flight in flights.items():
        folder_files = others.get(key, [])
        for name in folder_files:
            low = name.lower()
            if low.endswith(".mrk"):
                flight.mrk_files.append(os.path.join(key, name))
            elif low.endswith(LIDAR_EXTENSIONS):
                flight.lidar_files += 1
            elif low.endswith(".rpt"):
                flight.report_files += 1
        for path in flight.mrk_files:
            shots, ignored, error = mrk.read_mrk(path)
            flight.mrk_shots.extend(shots)
            flight.mrk_ignored += ignored + (1 if error else 0)
        flight.photos.sort(key=lambda r: (r.gps_time is None, r.gps_time, r.name))
        result.flights.append(flight)
    prog(1.0, "Scan finished")
    return result
