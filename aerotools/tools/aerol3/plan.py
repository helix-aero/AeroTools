"""Aero-L3: from the scanned photos to a plan the Metashape adapter can carry out. Pure Python 3.9, no Metashape, no Qt.

A `Plan` holds what is to be created: the two sensors (left and right lens, each with its own initial calibration), the stations (one per
shot: the left photo first, then the right one: Metashape's multi-camera layout makes the first photo of each group the master sensor), and
for every photo its reference (position, accuracy) and its starting orientation. Photos that cannot be used are listed with the reason.
"""

from collections import Counter, OrderedDict

from . import calib, orient

FRAMES = OrderedDict([("wgs84", "EPSG::4326"), ("etrs89", "EPSG::4258")])
STEP_KEYS = ("align", "repair", "optimize", "dem", "ortho")
EXPECTED_MODEL = "L3"

# reasons a photo is left out (text keys: l3.reason.<code>)
REASONS = ("not_l3", "lens", "size", "orientation", "calibration", "duplicate", "other_unit", "unpaired")


class Options(object):
    """What the operator can set (screen 4). Every value has a safe default; `as_dict` / `from_dict` keep them between sessions."""

    DEFAULTS = OrderedDict([
        ("chunk_name", ""),
        ("accuracy_fixed", 0.05),          # m, RTK fixed (the value used on test data)
        ("accuracy_float", 0.25),          # m, RTK float
        ("accuracy_single", 5.0),          # m, single point or unknown status
        ("rotation_accuracy", 10.0),       # degrees; the real gap to the adjusted poses was 3.3 degrees at most
        ("z_shift", 0.0),                  # m added to the height of every camera (0 unless a check on GCPs says otherwise)
        ("frame", "wgs84"),
        ("include_unpaired", False),
        ("calibration_fixed", False),
        ("quick_test", False),
        ("quick_test_photos", 30),
        ("pixel_cm", 5.0),
        ("ortho_epsg", ""),                # projected system of the DEM and the ortho, '' = the UTM zone of the site (WGS 84)
        ("steps", {"align": False, "repair": True, "optimize": False, "dem": False, "ortho": False}),
    ])

    def __init__(self, **values):
        for key, default in self.DEFAULTS.items():
            setattr(self, key, dict(default) if isinstance(default, dict) else default)
        for key, value in values.items():
            if key not in self.DEFAULTS:
                raise TypeError("unknown option " + key)
            setattr(self, key, value)

    def as_dict(self):
        return OrderedDict((key, dict(getattr(self, key)) if isinstance(getattr(self, key), dict) else getattr(self, key)) for key in self.DEFAULTS)

    @classmethod
    def from_dict(cls, data):
        """Options from a saved dict; wrong types and unknown keys are ignored (a damaged settings file never breaks the tool)."""
        options = cls()
        if not isinstance(data, dict):
            return options
        for key, default in cls.DEFAULTS.items():
            if key not in data:
                continue
            value = data[key]
            if isinstance(default, bool):
                if isinstance(value, bool):
                    setattr(options, key, value)
            elif isinstance(default, (int, float)):
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    setattr(options, key, type(default)(value))
            elif isinstance(default, dict):
                if isinstance(value, dict):
                    merged = dict(default)
                    merged.update({k: bool(v) for k, v in value.items() if k in default})
                    setattr(options, key, merged)
            elif isinstance(default, str):
                if isinstance(value, str) and (key != "frame" or value in FRAMES):
                    setattr(options, key, value)
        return options

    @property
    def crs(self):
        return FRAMES.get(self.frame, FRAMES["wgs84"])


class SensorSpec(object):
    """One lens as a Metashape sensor."""

    def __init__(self, lens, label, calibration, pixel_mm, serial, rotation):
        self.lens = lens
        self.label = label
        self.calibration = calibration
        self.width = calibration.width
        self.height = calibration.height
        self.pixel_mm = pixel_mm
        self.serial = serial
        self.rotation = rotation               # relative to the master sensor (None for the master)


class CameraSpec(object):
    """The reference and the starting orientation of one photo."""

    def __init__(self, record):
        self.path = record.path
        self.name = record.name
        self.lens = record.lens
        self.lat, self.lon, self.alt = record.lat, record.lon, record.alt
        self.accuracy = None                   # (x, y, z) in metres, None = no position reference for this photo
        self.rotation = None                   # camera frame -> east-north-up (see `orient`), None = no orientation reference
        self.rotation_source = ""
        self.rotation_accuracy = None          # degrees
        self.rtk = record.rtk_name


class Station(object):
    """The two photos of one shot (either may be missing)."""

    def __init__(self, key, left=None, right=None):
        self.key = key
        self.left = left
        self.right = right

    @property
    def paired(self):
        return self.left is not None and self.right is not None

    @property
    def records(self):
        return [r for r in (self.left, self.right) if r is not None]


class Plan(object):
    def __init__(self, options):
        self.options = options
        self.sensors = {}                      # {'L': SensorSpec, 'R': SensorSpec}
        self.stations = []
        self.groups = []                       # [[left path, right path], ...] for addPhotos (multi-camera layout)
        self.singles = []                      # photos added alone on their own sensor (only with options.include_unpaired)
        self.cameras = OrderedDict()           # {path: CameraSpec}
        self.excluded = []                     # [(PhotoRecord, reason code)]
        self.size = (0, 0)
        self.unit = None

    @property
    def photo_count(self):
        return sum(len(g) for g in self.groups) + len(self.singles)

    def excluded_counts(self):
        return Counter(reason for _rec, reason in self.excluded)

    def paths(self):
        """Every path to give to `addPhotos`, groups first, in order."""
        out = []
        for group in self.groups:
            out.extend(group)
        out.extend(self.singles)
        return out


def is_l3(record):
    """True for a photo of a Zenmuse L3: the DJI product name when the XMP gives one, else the EXIF model."""
    name = (record.product or record.model or "").upper()
    return name == EXPECTED_MODEL


def unit_signature(record):
    """What identifies the physical lens and its calibration: two photos of one lens must agree on it."""
    return (record.lens, record.serial, record.dewarp_data, int(record.dewarp_flag or 0))


def accuracy_for(record, options):
    """(x, y, z) accuracy in metres of the position of a photo, or None when it has no usable position."""
    if not record.has_position:
        return None
    if record.rtk_flag == 50:
        value = options.accuracy_fixed
    elif record.rtk_flag == 34:
        value = options.accuracy_float
    else:
        value = options.accuracy_single
    return (value, value, value)


def build_plan(records, options=None):
    """The `Plan` for these photos (already filtered to the flights the operator ticked)."""
    options = options or Options()
    plan = Plan(options)
    kept = []
    seen = set()
    sizes = Counter((r.width, r.height) for r in records)
    plan.size = sizes.most_common(1)[0][0] if sizes else (0, 0)
    units = Counter(unit_signature(r) for r in records if r.lens in (orient.LEFT, orient.RIGHT))
    by_lens = {}
    for sig, count in units.most_common():
        by_lens.setdefault(sig[0], sig)                 # most common signature of each lens
    plan.unit = by_lens
    for rec in records:
        reason = None
        if not is_l3(rec):
            reason = "not_l3"
        elif rec.lens not in (orient.LEFT, orient.RIGHT):
            reason = "lens"
        elif (rec.width, rec.height) != plan.size:
            reason = "size"
        elif rec.exif_orientation not in (None, 1):
            reason = "orientation"
        elif rec.name in seen:
            reason = "duplicate"
        elif unit_signature(rec) != by_lens.get(rec.lens):
            reason = "other_unit"
        else:
            try:
                calib.calibration_for(rec.width, rec.height, rec.dewarp_flag, rec.dewarp_data, rec.cal_focal)
            except calib.CalibrationError:
                reason = "calibration"
        if reason:
            plan.excluded.append((rec, reason))
            continue
        seen.add(rec.name)
        kept.append(rec)

    stations = OrderedDict()
    for rec in kept:
        station = stations.setdefault(rec.station_key, Station(rec.station_key))
        if rec.lens == orient.LEFT:
            station.left = rec
        else:
            station.right = rec
    for station in stations.values():
        if station.paired:
            plan.groups.append([station.left.path, station.right.path])
        elif options.include_unpaired:
            plan.singles.extend(r.path for r in station.records)
        else:
            for rec in station.records:
                plan.excluded.append((rec, "unpaired"))
            continue
        plan.stations.append(station)

    for lens in (orient.LEFT, orient.RIGHT):
        first = next((r for r in kept if r.lens == lens), None)
        if first is None:
            continue
        calibration = calib.calibration_for(first.width, first.height, first.dewarp_flag, first.dewarp_data, first.cal_focal)
        pixel = calib.pixel_size_mm(first.width, first.focal_mm, first.focal_35mm)
        label = "L3 left lens (LV)" if lens == orient.LEFT else "L3 right lens (RV)"
        plan.sensors[lens] = SensorSpec(lens, label, calibration, pixel, first.serial, None if lens == orient.LEFT else orient.rig_rotation())

    used = set(plan.paths())
    for rec in kept:
        if rec.path not in used:
            continue
        spec = CameraSpec(rec)
        spec.accuracy = accuracy_for(rec, options)
        if spec.accuracy is not None:
            spec.alt = rec.alt + options.z_shift
        matrix, source = orient.nominal_orientation(rec.angles, rec.lens)
        if matrix is not None:
            spec.rotation, spec.rotation_source, spec.rotation_accuracy = matrix, source, options.rotation_accuracy
        plan.cameras[rec.path] = spec
    return plan


def pick_test_stations(plan, photos=30):
    """Paths of about `photos` photos for a quick test alignment: a run of consecutive complete stations from the middle of the flight that
    has the most of them (consecutive shots overlap, so the test can align). Empty when there is no complete station."""
    by_flight = OrderedDict()
    for station in plan.stations:
        if station.paired:
            by_flight.setdefault(station.key[0], []).append(station)
    if not by_flight:
        return []
    stations = max(by_flight.values(), key=len)
    count = max(4, (int(photos) + 1) // 2)
    count = min(count, len(stations))
    start = max(0, (len(stations) - count) // 2)
    chosen = stations[start:start + count]
    return [rec.path for station in chosen for rec in (station.left, station.right)]
