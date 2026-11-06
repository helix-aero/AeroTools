"""Aero-Facade data model: plain dataclasses, no Qt, no Metashape, no numpy (Python 3.9).

Everything the three layers (engine, Metashape adapter, Qt wizard) exchange is defined here:

* `Facade`       one detected wall: letter name, colour, plane frame in local ENU metres, size, quality, `removed` flag;
* `Margins`      how much to take around the plane (left / right / top / bottom / depth) with the two "link" badges;
* `Method`       how the surface under a facade is built (dense cloud, depth maps, tie points);
* `Availability` which methods the chunk can offer, and why not when it cannot;
* `letter_name`  0 -> A, 25 -> Z, 26 -> AA, 27 -> AB ...

The plane frame of a facade follows the Metashape convention used by the study (docs/AERO_FACADE_STUDY.md, section 4):
`u` runs along the wall to the viewer's right, `v` points up, `n` points toward the viewer (= toward the cameras) and
`u x v = n`. All coordinates are metres in the chunk's local east/north/up frame (the frame of the exported tie points).
For a plumb wall `v = (0, 0, 1)` and `n = (u_y, -u_x, 0)`.
"""

import math
from dataclasses import dataclass, field, fields
from enum import Enum

# Twelve colours that stay distinguishable from each other on a dark and on a light background (Trubetskoy palette, reordered so
# that neighbouring letters differ strongly).
PALETTE = (
    "#E6194B",  # red
    "#3CB44B",  # green
    "#4363D8",  # blue
    "#F58231",  # orange
    "#911EB4",  # purple
    "#42D4F4",  # cyan
    "#FFE119",  # yellow
    "#F032E6",  # magenta
    "#BFEF45",  # lime
    "#469990",  # teal
    "#FABED4",  # pink
    "#DCBEFF",  # lavender
)


def letter_name(i):
    """Name of the i-th facade (0-based): A..Z, then AA, AB .. AZ, BA .. (bijective base 26)."""
    if i < 0:
        raise ValueError("index must be >= 0")
    s = ""
    i += 1
    while i > 0:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def letter_index(name):
    """Inverse of `letter_name`: 'A' -> 0, 'Z' -> 25, 'AA' -> 26."""
    name = (name or "").strip().upper()
    if not name or not name.isalpha() or not name.isascii():
        raise ValueError("not a facade letter: %r" % (name,))
    n = 0
    for ch in name:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def colour_for(i):
    """Colour (hex string) of the i-th facade; the palette repeats after 12."""
    return PALETTE[i % len(PALETTE)]


_COMPASS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def compass_name(bearing):
    """8-point compass name of a bearing in degrees (0 = north, clockwise)."""
    return _COMPASS[int(((bearing % 360.0) + 22.5) // 45.0) % 8]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(a):
    n = math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])
    if n < 1e-12:
        raise ValueError("zero-length vector")
    return (a[0] / n, a[1] / n, a[2] / n)


class Method(Enum):
    """How the surface (the DEM) under a facade is built. Order = quality order, best first (see methods.py)."""
    DENSE_CLOUD = "dense_cloud"
    DEPTH_MAPS = "depth_maps"
    TIE_POINTS = "tie_points"

    @property
    def key(self):
        """Key used by Backend.availability() and in JSON."""
        return self.value

    @classmethod
    def from_key(cls, key):
        if isinstance(key, cls):
            return key
        for m in cls:
            if m.value == key or m.name == key:
                return m
        raise ValueError("unknown method: %r" % (key,))


@dataclass
class Availability:
    """What the chunk can offer. Each field is (ok, reason): `reason` explains a False in plain English ('' when ok)."""
    tie_points: tuple = (True, "")
    depth_maps: tuple = (False, "")
    dense_cloud: tuple = (False, "")

    def get(self, method):
        """(ok, reason) of a Method."""
        return tuple(getattr(self, Method.from_key(method).value))

    def ok(self, method):
        return bool(self.get(method)[0])

    def reason(self, method):
        return self.get(method)[1]

    def any_surface(self):
        """True if at least one method can be used."""
        return any(self.ok(m) for m in Method)

    def to_dict(self):
        return {m.value: [bool(self.get(m)[0]), str(self.get(m)[1])] for m in Method}

    @classmethod
    def from_dict(cls, d):
        """Build from the dict returned by Backend.availability(); a missing entry counts as unavailable."""
        d = d or {}
        out = {}
        for m in Method:
            v = d.get(m.value)
            if v is None:
                out[m.value] = (False, "unknown")
            elif isinstance(v, (tuple, list)):
                out[m.value] = (bool(v[0]), str(v[1]) if len(v) > 1 else "")
            else:
                out[m.value] = (bool(v), "")
        return cls(**out)


def _clamp(v, lo=0.0, hi=100.0):
    v = float(v)
    if v != v:  # NaN
        return lo
    return min(hi, max(lo, v))


@dataclass
class Margins:
    """Metres taken around the detected plane. left/right/top/bottom extend the picture, depth is kept in front of and behind the wall.

    `link_lr` / `link_tb` are the two link badges of screen 3: while linked, setting one side sets the opposite one too."""
    left: float = 1.0
    right: float = 1.0
    top: float = 1.0
    bottom: float = 1.0
    depth: float = 2.0
    link_lr: bool = True
    link_tb: bool = True

    MAX_SIDE = 50.0
    MAX_DEPTH = 20.0

    def set_left(self, v):
        self.left = _clamp(v, 0.0, self.MAX_SIDE)
        if self.link_lr:
            self.right = self.left

    def set_right(self, v):
        self.right = _clamp(v, 0.0, self.MAX_SIDE)
        if self.link_lr:
            self.left = self.right

    def set_top(self, v):
        self.top = _clamp(v, 0.0, self.MAX_SIDE)
        if self.link_tb:
            self.bottom = self.top

    def set_bottom(self, v):
        self.bottom = _clamp(v, 0.0, self.MAX_SIDE)
        if self.link_tb:
            self.top = self.bottom

    def set_depth(self, v):
        self.depth = _clamp(v, 0.1, self.MAX_DEPTH)

    def set_link_lr(self, linked):
        """Switch the left/right badge; linking again makes both sides equal to the left one."""
        self.link_lr = bool(linked)
        if self.link_lr:
            self.right = self.left

    def set_link_tb(self, linked):
        self.link_tb = bool(linked)
        if self.link_tb:
            self.bottom = self.top

    def set_all(self, v):
        """The same margin on the four sides (the depth is left alone)."""
        self.left = self.right = self.top = self.bottom = _clamp(v, 0.0, self.MAX_SIDE)

    def copy(self):
        return Margins.from_dict(self.to_dict())

    def to_dict(self):
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, d):
        d = d or {}
        m = cls()
        for key in ("left", "right", "top", "bottom"):
            if key in d:
                setattr(m, key, _clamp(d[key], 0.0, cls.MAX_SIDE))
        if "depth" in d:
            m.depth = _clamp(d["depth"], 0.1, cls.MAX_DEPTH)
        if "link_lr" in d:
            m.link_lr = bool(d["link_lr"])
        if "link_tb" in d:
            m.link_tb = bool(d["link_tb"])
        return m


def margins_for(margins, facade):
    """The Margins that apply to `facade`. `margins` is one `Margins` (same for all facades) or a dict letter -> Margins."""
    if isinstance(margins, dict):
        return margins.get(facade.letter) or margins.get("*") or Margins()
    return margins or Margins()


@dataclass
class Facade:
    """One detected wall.

    Plane frame (local ENU metres): `origin` = bottom-left corner of the wall as seen by someone standing in front of it,
    `u` along the wall to the viewer's right, `n` toward the cameras, `v` up. The wall rectangle is
    origin + s*u + t*v, 0 <= s <= length, 0 <= t <= height. `azimuth` is the compass bearing (degrees, 0 = north, clockwise)
    of the direction the facade FACES (the normal `n`), so a south-facing front reads 180.
    """
    letter: str = "A"
    colour: str = PALETTE[0]
    x0: float = 0.0
    y0: float = 0.0
    z0: float = 0.0
    ux: float = 1.0
    uy: float = 0.0
    uz: float = 0.0
    nx: float = 0.0
    ny: float = -1.0
    nz: float = 0.0
    length: float = 0.0
    height: float = 0.0
    azimuth: float = 180.0
    points: int = 0
    score: float = 0.0          # quality 0..1 (1 = long, tall, dense, flat, well isolated from its surroundings)
    removed: bool = False
    tilt: float = 0.0           # degrees the wall leans from plumb (informative, the frame itself is exact)
    rms: float = 0.0            # rms distance of the tie points to the plane (m)

    # ---- frame --------------------------------------------------------------------------------------------------
    @property
    def origin(self):
        return (self.x0, self.y0, self.z0)

    @property
    def u(self):
        return (self.ux, self.uy, self.uz)

    @property
    def n(self):
        return (self.nx, self.ny, self.nz)

    @property
    def v(self):
        """Up direction in the plane, n x u (= (0, 0, 1) for a plumb wall)."""
        return _cross(self.n, self.u)

    @property
    def facing(self):
        """Compass name of the direction the facade faces ('S' for a south front)."""
        return compass_name(self.azimuth)

    def point_at(self, s, t, d=0.0):
        """ENU point at (s along, t up, d toward the cameras) in the plane frame."""
        v = self.v
        return (self.x0 + s * self.ux + t * v[0] + d * self.nx,
                self.y0 + s * self.uy + t * v[1] + d * self.ny,
                self.z0 + s * self.uz + t * v[2] + d * self.nz)

    @property
    def end(self):
        """Bottom-right end of the wall."""
        return self.point_at(self.length, 0.0)

    @property
    def centre(self):
        """Middle of the wall rectangle."""
        return self.point_at(self.length / 2.0, self.height / 2.0)

    def polyline_xy(self):
        """The wall trace in plan view: [(x, y), (x, y)] (what the UI draws on the orthomosaic)."""
        e = self.end
        return [(self.x0, self.y0), (e[0], e[1])]

    @property
    def area(self):
        return self.length * self.height

    @property
    def density(self):
        """Tie points per square metre of facade."""
        return self.points / self.area if self.area > 0 else 0.0

    # ---- JSON ---------------------------------------------------------------------------------------------------
    def to_dict(self):
        d = {f.name: getattr(self, f.name) for f in fields(self)}
        d["facing"] = self.facing
        return d

    @classmethod
    def from_dict(cls, d):
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})

    @classmethod
    def from_frame(cls, letter, index, origin, u, length, height, points=0, score=0.0, n=None, **kw):
        """Build a plumb facade from its bottom-left origin, the unit vector along it (horizontal part is used) and its size.

        If `n` is not given it is (u_y, -u_x, 0), the plane normal that goes with a plumb wall seen from the right-hand side."""
        ux, uy = u[0], u[1]
        h = math.hypot(ux, uy)
        ux, uy = ux / h, uy / h
        nx, ny = (uy, -ux) if n is None else (n[0], n[1])
        hn = math.hypot(nx, ny)
        nx, ny = nx / hn, ny / hn
        az = math.degrees(math.atan2(nx, ny)) % 360.0     # compass bearing: atan2(east, north)
        return cls(letter=letter, colour=colour_for(index), x0=float(origin[0]), y0=float(origin[1]), z0=float(origin[2]),
                   ux=ux, uy=uy, uz=0.0, nx=nx, ny=ny, nz=0.0, length=float(length), height=float(height), azimuth=az,
                   points=int(points), score=float(score), **kw)


def facades_to_json(facades):
    """List of dicts, ready for json.dump."""
    return [f.to_dict() for f in facades]


def facades_from_json(rows):
    return [Facade.from_dict(r) for r in rows]


def assign_names(facades, order=None):
    """Give each facade its letter and colour in the current list order (A, B, C ...). Returns the same list."""
    for i, f in enumerate(facades):
        f.letter = letter_name(i)
        f.colour = colour_for(i)
    return facades


def active(facades):
    """The facades that are not removed (the only ones processed)."""
    return [f for f in facades if not f.removed]


@dataclass
class Settings:
    """What the wizard collects, so a whole session can be saved as one JSON."""
    margins: Margins = field(default_factory=Margins)
    method: str = Method.DEPTH_MAPS.value
    pixel_m: float = 0.03

    def to_dict(self):
        return {"margins": self.margins.to_dict(), "method": self.method, "pixel_m": self.pixel_m}

    @classmethod
    def from_dict(cls, d):
        d = d or {}
        return cls(margins=Margins.from_dict(d.get("margins")), method=Method.from_key(d.get("method", "depth_maps")).value,
                   pixel_m=float(d.get("pixel_m", 0.03)))
