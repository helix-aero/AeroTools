"""Aero-L3: the geometry checks made after the cameras are created and after the alignment. Pure Python 3.9, no Metashape.

Why: on the reference project the alignment sometimes put whole strips of cameras in a mirrored or flipped pose (optical axis more than 40
degrees off the vertical instead of 22.5) because nothing constrained the rotations. Every camera has a starting orientation (see `orient`);
after the alignment each camera that got a pose is compared with it. The gap between the adjusted poses of the reference project and the
starting orientations was 3.3 degrees at most, a flip is 40 degrees or more: the limit is `FLIP_DEG`.
"""

import statistics

from . import orient

FLIP_DEG = 20.0
ROUND_TRIP_DEG = 0.05              # the angles written to Metashape must give back the orientation to this precision


class GeometryReport(object):
    """What was read back from the project right after the cameras were created."""

    def __init__(self):
        self.planned = 0                 # photos the plan wanted
        self.cameras = 0                 # cameras found in the chunk
        self.missing = []                # planned file names that are not in the chunk
        self.unknown = 0                 # cameras of the chunk that the plan does not know
        self.with_position = 0
        self.with_rotation = 0
        self.max_rotation_error = 0.0    # degrees, between what was planned and what Metashape holds
        self.bad_rotation = []           # names whose round trip error is above `ROUND_TRIP_DEG`
        self.bad_orientation = []        # names whose camera.orientation is not 1
        self.sensors = []                # [(label, width, height, focal pixels, p1, p2)]
        self.rig_angle = None            # degrees between the two lenses as stored in the project
        self.problems = []               # plain English lines, empty when everything is as planned

    @property
    def ok(self):
        return not self.problems


class AlignmentReport(object):
    """The cameras of a chunk compared with their starting orientation after an alignment."""

    def __init__(self):
        self.total = 0
        self.aligned = 0
        self.compared = 0                # aligned cameras that have a starting orientation
        self.flipped = []                # [(name, angle)] worst first
        self.median = None
        self.maximum = None
        self.repaired = 0
        self.remaining = 0
        self.lens_median = {}            # {'L': median angle, 'R': ...} in degrees

    @property
    def clean(self):
        return not self.flipped


def compare_rotations(nominal, estimated):
    """Angle in degrees between two camera-to-east-north-up rotations."""
    return orient.angle_between(nominal, estimated)


def alignment_report(entries, limit=FLIP_DEG, lens_of=None):
    """`entries` = [(name, angle in degrees or None)]: None means the camera has no pose (not aligned) or no starting orientation.
    Counting: `total` all, `aligned` those with an angle, flipped = angle above `limit`. `lens_of` = {name: 'L' | 'R'} adds the median per lens."""
    report = AlignmentReport()
    report.total = len(entries)
    angles = [a for _n, a in entries if a is not None]
    report.aligned = report.compared = len(angles)
    report.flipped = sorted(((n, a) for n, a in entries if a is not None and a > limit), key=lambda e: -e[1])
    if angles:
        report.median = statistics.median(angles)
        report.maximum = max(angles)
    report.remaining = len(report.flipped)
    if lens_of:
        for lens in sorted(set(lens_of.values())):
            values = [a for n, a in entries if a is not None and lens_of.get(n) == lens]
            if values:
                report.lens_median[lens] = statistics.median(values)
    return report


def tilt_summary(rotations):
    """(median, minimum, maximum) tilt from the nadir in degrees of a list of rotations, or None for an empty list."""
    tilts = [orient.tilt_from_nadir(r) for r in rotations]
    if not tilts:
        return None
    return statistics.median(tilts), min(tilts), max(tilts)
