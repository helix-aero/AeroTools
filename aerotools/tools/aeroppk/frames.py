"""Reference frames, done by the embedded PROJ (never by hand): ETRS89 / RGF93 <-> ITRF at a given date.

Why it matters. A rover position computed from a base inherits the frame of the base coordinates. RGP coordinates are RGF93
v2b, an ETRS89 realisation: fixed to the European plate, so it slides away from ITRF / WGS 84 (the frame of the satellite
orbits) by about a metre over the years. Two things follow:
  * AeroPPK gives the photo positions in the frame of the bases (RGF93 for RGP) and says so; a project in RGF93 / Lambert 93
    gets coordinates that agree with its GCPs. This is NOT "WGS 84 of the day of the flight".
  * a base whose coordinates are given in ITRF (a PPP result, an IGS station) must be moved to the frame of the others at the
    date of the observations before being used: `convert_xyz` does it with PROJ and the epoch.
Measured with the embedded PROJ 9.8.1 on the Corte station on 16/12/2023 (decimal year 2023.96): ETRS89 -> ITRF2014 moves
the point by (-0.475, +0.656, +0.401) m geocentric, 0.90 m in 3D (tests/test_aeroppk_frames.py).
"""

import numpy as np

from ... import projtool

# Geocentric (X, Y, Z) coordinate systems, EPSG codes. Time dependent operations take the epoch as 4th coordinate.
FRAMES = {"ETRS89": 4936, "ITRF2014": 7789, "ITRF2020": 9988}


class FrameError(Exception):
    pass


def convert_xyz(xyz, epoch, source, target, proj=None):
    """Geocentric position (x, y, z metres) from frame `source` to frame `target` at decimal year `epoch`."""
    if source == target:
        return tuple(float(v) for v in xyz)
    if source not in FRAMES or target not in FRAMES:
        raise FrameError("unknown reference frame: %s -> %s" % (source, target))
    proj = proj or projtool.Proj()
    if not proj.available():
        raise FrameError("PROJ is not available")
    rows = np.array([[xyz[0], xyz[1], xyz[2], epoch]], float)
    try:
        out = proj._columns("cs2cs", ["-d", "9", "EPSG:%d" % FRAMES[source], "EPSG:%d" % FRAMES[target]], rows, 4)
    except projtool.ProjError as exc:
        raise FrameError(str(exc))
    if not np.all(np.isfinite(out[0, :3])):
        raise FrameError("PROJ found no transformation")
    return float(out[0, 0]), float(out[0, 1]), float(out[0, 2])


def shift_m(xyz, epoch, source="ETRS89", target="ITRF2014", proj=None):
    """((dx, dy, dz), 3D distance) of the move between two frames at that date, in metres."""
    moved = convert_xyz(xyz, epoch, source, target, proj)
    delta = tuple(m - o for m, o in zip(moved, xyz))
    return delta, float(np.sqrt(sum(d * d for d in delta)))


# ---------------------------------------------------------------------------------------------------------------------
# The frame declared when positions are written into a Metashape chunk
# ---------------------------------------------------------------------------------------------------------------------
#
# Metashape converts the positions we give it from a declared geographic system to the system of the chunk, and it has no epoch: a
# conversion between a plate fixed frame (ETRS89, NAD83) and a global one (ITRF, WGS 84) is a null operation to it, 0.9 m wrong in Europe.
# So the declared frame must be the real frame of the bases, and the write is refused when the chunk is in a family of frames that
# Metashape cannot reach from it without an epoch.

EUROPE, NORTH_AMERICA, GLOBAL = "eu", "na", "global"

# frame group of a network (`Provider.frame_group`) -> (label, family, EPSG source, EPSG to retry with when Metashape does not know the first)
NETWORK_FRAMES = {
    "ETRS89": ("ETRS89 (RGF93 v2b, ETRF2000)", EUROPE, "EPSG::4258", None),
    "NAD83(2011)": ("NAD83(2011)", NORTH_AMERICA, "EPSG::6318", None),
    "IGS20": ("IGS20 (ITRF2020)", GLOBAL, "EPSG::9989", "EPSG::4326"),
}

# what the user can declare for a base of their own: key -> (label, family, EPSG source, retry)
OWN_FRAMES = {
    "etrs89": ("ETRS89 / RGF93 / ETRF2000", EUROPE, "EPSG::4258", None),
    "nad83": ("NAD83(2011)", NORTH_AMERICA, "EPSG::6318", None),
    "itrf": ("WGS 84 / ITRF / IGS20", GLOBAL, "EPSG::4326", None),
}

_FAMILY_WORDS = (
    (EUROPE, ("etrs", "etrf", "rgf93", "rgf 93", "lambert-93", "lambert 93")),
    (NORTH_AMERICA, ("nad83", "nad 83", "nad_83")),
    (GLOBAL, ("wgs 84", "wgs84", "wgs_84", "itrf", "igs20")),
)


def chunk_family(crs_name):
    """The family of frames a chunk coordinate system belongs to, read from its name: EUROPE, NORTH_AMERICA, GLOBAL, "" (no coordinate
    system: local coordinates) or None (a system whose datum the name does not tell)."""
    name = (crs_name or "").strip().lower()
    if not name:
        return ""
    for family, words in _FAMILY_WORDS:
        if any(word in name for word in words):
            return family
    return None


class ApplyFrame(object):
    """What `source_frame` decides: the frame to declare, or why nothing may be written.

    `source` / `retry` EPSG strings for `msapi.set_camera_positions`; `label` the frame in words; `problem` a message key suffix
    (`aeroppk.apply_frame_<problem>`) when the write must not happen; `checked` is False when the chunk system is one whose datum the name
    does not tell (Metashape's own conversion is then trusted, and the question says so)."""

    def __init__(self, label="", source=None, retry=None, problem="", checked=True, **fields):
        self.label, self.source, self.retry, self.problem, self.checked, self.fields = label, source, retry, problem, checked, fields

    @property
    def ok(self):
        return not self.problem


WGS84 = "EPSG::4326"


def simple_frame(stations, crs_name):
    """Simple mode (the default): the positions go into a chunk in WGS 84 as they are, with no change of frame, like DJI RTK does.

    The coordinates of French stations are RGF93; written as WGS 84, Metashape takes them to Lambert 93 without a shift, so a project
    in Lambert 93 agrees with its GCPs. Bases of several frames are refused (their results were averaged). A chunk in any other system
    gets `problem` "simple_only": the caller offers the Advanced mode (`source_frame`), where Metashape converts."""
    from .stations import catalog
    groups = catalog.frame_groups(stations)
    if len(groups) != 1:
        return ApplyFrame(problem="mixed" if groups else "none", frames=catalog.frames_text(stations, short=True))
    if chunk_family(crs_name) != GLOBAL:
        return ApplyFrame("WGS 84", problem="simple_only", crs=crs_name or "local coordinates")
    return ApplyFrame("WGS 84", WGS84)


def source_frame(stations, crs_name, own_frame=""):
    """Advanced mode: the frame to declare for photo positions computed from `stations` (the bases that count), written into a chunk
    named `crs_name`; Metashape converts them from that frame to the chunk system.

    A base of one network gives its frame; the user's own base gives the frame the user declared (`own_frame`, key of `OWN_FRAMES`);
    stations of several frames, an own base with no declared frame, a chunk with no coordinate system and a chunk of a family of frames
    that Metashape cannot reach without an epoch are refused (`problem`)."""
    from .stations import catalog
    groups = catalog.frame_groups(stations)
    if len(groups) != 1:
        return ApplyFrame(problem="mixed" if groups else "none", frames=catalog.frames_text(stations, short=True))
    group = groups[0]
    if group == "own":
        entry = OWN_FRAMES.get(own_frame or "")
        if entry is None:
            return ApplyFrame(problem="own")
    else:
        entry = NETWORK_FRAMES.get(group)
        if entry is None:
            return ApplyFrame(problem="unknown", frame=group)
    label, family, source, retry = entry
    chunk = chunk_family(crs_name)
    if chunk == "":
        return ApplyFrame(label, problem="local")
    if chunk is not None and chunk != family:
        return ApplyFrame(label, problem="mismatch", crs=crs_name, frame=label)
    return ApplyFrame(label, source, retry if chunk == GLOBAL else None, checked=chunk is not None)


def write_positions(chk, items, frame, setter=None):
    """Write the positions into the chunk declaring `frame` as their system. Returns what `msapi.set_camera_positions` returns.

    When Metashape does not know the first EPSG code of the frame (ITRF2020 is recent) and wrote nothing, the retry code is used: nothing
    was written, so nothing is mixed."""
    if setter is None:
        from ... import msapi
        setter = msapi.set_camera_positions
    outcome = setter(chk, items, source=frame.source)
    if frame.retry and items and not outcome["done"] and outcome["failed"]:
        outcome = setter(chk, items, source=frame.retry)
    return outcome
