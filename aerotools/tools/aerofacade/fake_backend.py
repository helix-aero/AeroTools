"""Aero-Facade `FakeBackend`: a synthetic building behind the `Backend` protocol (no Metashape, no Qt).

Used by the wizard's tests and offscreen screenshots, and by the engine's own tests. Deterministic (seeded). The scene is a
66 m x 14 m block seen from the south: a stepped south front (three sections of 15, 20 and 26 m, the middle one set back 2.5 m), a
66 m north wall, two gables, trees, a small neighbour house, sloping terrain, a road and a courtyard. The tie points are
synthetic, and `detect` runs the REAL detector on them inside the search polygon. The orthomosaic preview and the façade pictures
are drawn procedurally (numpy only) and written as real small PNG / TIFF / JPEG (JPEG only when Pillow is installed) files.

Frames: the preview "crs" and the façade frames are the same local east/north/up metres here (no conversion needed).

    fb = FakeBackend()                       # all methods available, 0.05 s per simulated stage
    fb = FakeBackend(delay=0, dense_cloud=False)   # tests: no sleep, no dense cloud
    fb = FakeBackend(fail={"B": "Not enough depth maps cover this façade"})
"""

import math
import os
import struct
import tempfile
import time
import zlib

import numpy as np

from . import detect as D
from .backend import Backend, Cancelled, Progress
from .geometry import georef_lines
from .model import Availability, Method, assign_names, margins_for

# ---------------------------------------------------------------------------------------------------------------------
# The synthetic world (building frame: bx east along the front, by north; rotated 5.3 degrees into the world frame)
# ---------------------------------------------------------------------------------------------------------------------

THETA = math.radians(5.3)
CENTRE = (33.0, 7.0)                       # rotation centre, building frame = world position of that point
EXTENT = (-35.0, -45.0, 105.0, 55.0)       # (left, bottom, right, top) of the orthomosaic preview, metres

# (name, p0, p1, outward normal, height m, visibility 0..1)
WALLS = (
    ("S1", (0.0, 0.0), (22.0, 0.0), (0, -1), 15.0, 1.0),
    ("S2", (22.0, 2.5), (44.0, 2.5), (0, -1), 20.0, 1.0),
    ("S3", (44.0, 0.0), (66.0, 0.0), (0, -1), 26.0, 1.0),
    ("N1", (0.0, 14.0), (22.0, 14.0), (0, 1), 15.0, 0.7),
    ("N2", (22.0, 14.0), (44.0, 14.0), (0, 1), 20.0, 0.7),
    ("N3", (44.0, 14.0), (66.0, 14.0), (0, 1), 26.0, 0.7),
    ("W", (0.0, 0.0), (0.0, 14.0), (-1, 0), 15.0, 0.8),
    ("E", (66.0, 0.0), (66.0, 14.0), (1, 0), 26.0, 0.8),
    ("R1", (22.0, 0.0), (22.0, 2.5), (1, 0), 15.0, 0.8),         # the two returns of the set-back: 2.5 m, too short to be a façade
    ("R2", (44.0, 0.0), (44.0, 2.5), (-1, 0), 26.0, 0.8),
)
BLOCKS = ((0.0, 0.0, 22.0, 14.0, 15.0), (22.0, 2.5, 44.0, 14.0, 20.0), (44.0, 0.0, 66.0, 14.0, 26.0))   # x0, y0, x1, y1, roof height
TREES = ((6, -9, 2.6, 7), (15, -11, 3.0, 8), (30, -8, 2.2, 6), (50, -10, 3.2, 9), (60, -8.5, 2.4, 7), (-7, 5, 2.8, 8),
         (10, 19, 2.5, 7), (40, 20, 3.0, 8), (73, 6, 2.6, 7))          # building-frame x, y, radius, height
HOUSE = dict(cx=-26.0, cy=30.0, w=10.0, d=8.0, h=6.0, angle=math.radians(20.0))   # a neighbour, in world coordinates
HOUSE_WALLS_VIS = 0.8


def _rot(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s], [s, c]])


def building_to_world(b):
    """(…, 2) building-frame points to world (preview) metres."""
    b = np.asarray(b, dtype=np.float64)
    return (b - np.array(CENTRE)) @ _rot(THETA).T + np.array(CENTRE)


def world_to_building(w):
    w = np.asarray(w, dtype=np.float64)
    return (w - np.array(CENTRE)) @ _rot(-THETA).T + np.array(CENTRE)


def _house_corners():
    h = HOUSE
    c = np.array([[-h["w"] / 2, -h["d"] / 2], [h["w"] / 2, -h["d"] / 2], [h["w"] / 2, h["d"] / 2], [-h["w"] / 2, h["d"] / 2]])
    return c @ _rot(h["angle"]).T + np.array([h["cx"], h["cy"]])


def terrain(x, y):
    """Ground height (m) of the synthetic site: a gentle slope and a low mound."""
    return 0.015 * (x - 33.0) + 0.01 * (y - 7.0) + 0.8 * np.exp(-(((x - 85.0) ** 2 + (y + 20.0) ** 2) / 200.0))


def _in_footprints(xy):
    """Mask: the world points that lie under a building (no ground tie point there: the cameras never see it)."""
    b = world_to_building(xy)
    m = np.zeros(len(b), bool)
    for x0, y0, x1, y1, _h in BLOCKS:
        m |= (b[:, 0] >= x0 - 0.3) & (b[:, 0] <= x1 + 0.3) & (b[:, 1] >= y0 - 0.3) & (b[:, 1] <= y1 + 0.3)
    h = HOUSE
    r = (np.asarray(xy) - np.array([h["cx"], h["cy"]])) @ _rot(-h["angle"]).T
    m |= (np.abs(r[:, 0]) <= h["w"] / 2 + 0.3) & (np.abs(r[:, 1]) <= h["d"] / 2 + 0.3)
    return m


def build_scene(seed=1, wall_density=3.5, ground_density=1.2, noise=0.04):
    """Return dict(points (N,3), labels (N,), cameras (K,3), truth [dict]) of the synthetic site, local ENU metres.

    labels: 0 ground, 1 wall, 2 roof, 3 tree. truth: one dict per wall piece (name, world end points, z0, z1, normal, ...)."""
    rng = np.random.RandomState(seed)
    pts, lab, truth = [], [], []

    # ground
    x0, y0, x1, y1 = EXTENT
    n = int((x1 - x0 + 10) * (y1 - y0 + 10) * ground_density)
    g = np.column_stack([rng.uniform(x0 - 5, x1 + 5, n), rng.uniform(y0 - 5, y1 + 5, n)])
    g = g[~_in_footprints(g)]
    pts.append(np.column_stack([g, terrain(g[:, 0], g[:, 1]) + rng.normal(0, 0.05, len(g))]))
    lab.append(np.zeros(len(g), int))

    def face(p0, p1, out, base, height, vis, name, kind="wall"):
        p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
        length = float(np.hypot(*(p1 - p0)))
        m = rng.poisson(length * height * wall_density * vis)
        t = rng.uniform(0, length, m)
        v = rng.uniform(0, height, m)
        u = (p1 - p0) / length
        o = np.asarray(out, float)
        xy = p0[None, :] + t[:, None] * u[None, :] + rng.normal(0, noise, m)[:, None] * o[None, :]
        pts.append(np.column_stack([xy, base + v + rng.normal(0, noise * 0.5, m)]))
        lab.append(np.full(m, 1))
        truth.append(dict(name=name, kind=kind, x0=float(p0[0]), y0=float(p0[1]), x1=float(p1[0]), y1=float(p1[1]), z0=float(base),
                          z1=float(base + height), nx=float(o[0]), ny=float(o[1]), length=length, height=float(height), vis=vis))

    base_b = float(terrain(*CENTRE))
    for name, a, b, out, h, vis in WALLS:
        wa, wb = building_to_world(np.array([a, b]))
        wo = _rot(THETA) @ np.array(out, float)
        face(wa, wb, wo, base_b, h, vis, name, "return" if name.startswith("R") else "wall")
    hc = _house_corners()
    for k in range(4):
        a, b = hc[k], hc[(k + 1) % 4]
        mid = (a + b) / 2 - np.array([HOUSE["cx"], HOUSE["cy"]])
        out = mid / np.linalg.norm(mid)
        face(a, b, out, float(terrain(HOUSE["cx"], HOUSE["cy"])), HOUSE["h"], HOUSE_WALLS_VIS, "H%d" % (k + 1), "house")

    # roofs (flat), seen from above
    for bx0, by0, bx1, by1, h in BLOCKS:
        m = rng.poisson((bx1 - bx0) * (by1 - by0) * 2.5)
        w = building_to_world(np.column_stack([rng.uniform(bx0, bx1, m), rng.uniform(by0, by1, m)]))
        pts.append(np.column_stack([w, base_b + h + rng.normal(0, 0.05, m)]))
        lab.append(np.full(m, 2))
    m = rng.poisson(HOUSE["w"] * HOUSE["d"] * 2.5)
    r = np.column_stack([rng.uniform(-HOUSE["w"] / 2, HOUSE["w"] / 2, m), rng.uniform(-HOUSE["d"] / 2, HOUSE["d"] / 2, m)])
    w = r @ _rot(HOUSE["angle"]).T + np.array([HOUSE["cx"], HOUSE["cy"]])
    pts.append(np.column_stack([w, float(terrain(HOUSE["cx"], HOUSE["cy"])) + HOUSE["h"] + rng.normal(0, 0.05, m)]))
    lab.append(np.full(m, 2))

    # trees
    for tx, ty, rad, hh in TREES:
        c = building_to_world(np.array([[tx, ty]]))[0]
        m = rng.poisson(3.0 * 4.0 / 3.0 * math.pi * rad * rad * hh / 2)
        q = np.column_stack([rng.normal(0, rad / 2, m) + c[0], rng.normal(0, rad / 2, m) + c[1],
                             float(terrain(*c)) + np.clip(rng.normal(hh * 0.65, hh * 0.2, m), 1.0, hh)])
        pts.append(q)
        lab.append(np.full(m, 3))

    P = np.vstack(pts)
    L = np.concatenate(lab)
    # the cameras: two rings around the site (aerial survey)
    cams = []
    for radius, height, k in ((60.0, 45.0, 24), (30.0, 32.0, 12)):
        a = np.linspace(0, 2 * math.pi, k, endpoint=False)
        cams.append(np.column_stack([CENTRE[0] + radius * np.cos(a), CENTRE[1] + radius * np.sin(a), np.full(k, height)]))
    return dict(points=P, labels=L, cameras=np.vstack(cams), truth=truth)


# ---------------------------------------------------------------------------------------------------------------------
# Procedural pictures (numpy only)
# ---------------------------------------------------------------------------------------------------------------------

def _value_noise(h, w, cell, rng):
    """Smooth random field in [0, 1], bilinear between a coarse random grid (cell in pixels)."""
    gh, gw = h // cell + 2, w // cell + 2
    g = rng.uniform(0, 1, (gh, gw))
    yy = np.arange(h) / float(cell)
    xx = np.arange(w) / float(cell)
    y0, x0 = yy.astype(int), xx.astype(int)
    fy, fx = (yy - y0)[:, None], (xx - x0)[None, :]
    a = g[y0][:, x0] * (1 - fx) + g[y0][:, x0 + 1] * fx
    b = g[y0 + 1][:, x0] * (1 - fx) + g[y0 + 1][:, x0 + 1] * fx
    return a * (1 - fy) + b * fy


def _paint(img, mask, colour, jitter=None):
    c = np.asarray(colour, dtype=np.float64)
    if jitter is None:
        img[mask] = c
    else:
        img[mask] = c[None, :] * (1.0 + jitter[mask][:, None])


def render_ortho(max_px=1600, seed=1):
    """The synthetic orthomosaic: (RGB uint8 H x W x 3, extent (left, bottom, right, top)). Roof tiles, courtyard, road, grass, trees."""
    left, bottom, right, top = EXTENT
    w_m, h_m = right - left, top - bottom
    scale = max_px / max(w_m, h_m)
    W, H = int(round(w_m * scale)), int(round(h_m * scale))
    rng = np.random.RandomState(seed)
    X = left + (np.arange(W) + 0.5) / scale
    Y = top - (np.arange(H) + 0.5) / scale
    XX, YY = np.meshgrid(X, Y)
    wxy = np.column_stack([XX.ravel(), YY.ravel()])
    b = world_to_building(wxy)
    BX, BY = b[:, 0].reshape(H, W), b[:, 1].reshape(H, W)
    cell = max(4, int(1.5 * scale))
    nz = _value_noise(H, W, cell, rng) - 0.5
    fine = rng.uniform(-0.5, 0.5, (H, W))
    img = np.zeros((H, W, 3))

    # grass, with lighter and darker patches
    img[:] = np.array([84.0, 120.0, 58.0])
    img *= (1.0 + 0.32 * nz + 0.05 * fine)[:, :, None]
    # a dry strip of earth at the east side
    _paint(img, (BX > 78) & (BX < 100) & (BY > -12) & (BY < 40), (150, 128, 90), 0.2 * nz + 0.04 * fine)
    # roads: one along the front (building frame y -27 .. -21), one to the east (x 84 .. 90)
    road = ((BY > -27) & (BY < -21)) | ((BX > 84) & (BX < 90) & (BY > -40))
    _paint(img, road, (88, 88, 92), 0.1 * nz + 0.05 * fine)
    dash = (np.abs(BY + 24) < 0.12) & ((BX % 6.0) < 3.0)
    _paint(img, dash, (225, 225, 215))
    _paint(img, (np.abs(BX - 87) < 0.12) & ((BY % 6.0) < 3.0) & (BY > -40) & ~((BY > -27) & (BY < -21)), (225, 225, 215))
    # courtyard and car park in front of the building, with parking bays
    yard = (BX > 4) & (BX < 62) & (BY > -19) & (BY < -3.5)
    _paint(img, yard, (172, 166, 154), 0.1 * nz + 0.05 * fine)
    bays = yard & (BY < -8) & (BY > -17.5) & (np.abs(((BX - 6.0) % 2.6) - 0.0) < 0.07)
    _paint(img, bays, (240, 240, 235))
    _paint(img, (BX > 4) & (BX < 62) & (np.abs(BY + 3.5) < 0.25), (130, 126, 116))
    # shadow of the building (sun in the south-west): the footprint pushed to the north-east
    foot = np.zeros((H, W), bool)
    for x0, y0, x1, y1, _h in BLOCKS:
        foot |= (BX >= x0) & (BX <= x1) & (BY >= y0) & (BY <= y1)
    shift = int(round(5.0 * scale))
    sh = np.zeros_like(foot)
    sh[: H - shift // 2 or None, shift:] = foot[shift // 2:, : W - shift]
    img[sh & ~foot] *= 0.62
    # the neighbour house
    hr = (wxy - np.array([HOUSE["cx"], HOUSE["cy"]])) @ _rot(-HOUSE["angle"]).T
    hmask = ((np.abs(hr[:, 0]) <= HOUSE["w"] / 2) & (np.abs(hr[:, 1]) <= HOUSE["d"] / 2)).reshape(H, W)
    hedge = ((np.abs(hr[:, 0]) <= HOUSE["w"] / 2 + 0.5) & (np.abs(hr[:, 1]) <= HOUSE["d"] / 2 + 0.5)).reshape(H, W)
    _paint(img, hedge & ~hmask, (215, 212, 205))
    ridge = hr[:, 1].reshape(H, W) > 0
    _paint(img, hmask & ridge, (176, 98, 74), 0.1 * nz + 0.05 * fine)
    _paint(img, hmask & ~ridge, (150, 80, 62), 0.1 * nz + 0.05 * fine)
    # the building: eaves line, tile rows, one shade per section, a ridge on each
    eaves = np.zeros((H, W), bool)
    roof = np.zeros((H, W), bool)
    shades = ((192, 100, 72), (176, 88, 62), (160, 78, 58))
    for (x0, y0, x1, y1, _h), col in zip(BLOCKS, shades):
        inside = (BX >= x0) & (BX <= x1) & (BY >= y0) & (BY <= y1)
        eaves |= (BX >= x0 - 0.5) & (BX <= x1 + 0.5) & (BY >= y0 - 0.5) & (BY <= y1 + 0.5)
        roof |= inside
        rows = ((BY - y0) / 0.42) % 1.0
        tile = -0.10 * (rows < 0.12) + 0.05 * fine + 0.10 * nz
        mid = 0.5 * (y0 + y1)
        north = BY > mid
        _paint(img, inside & north, col, tile - 0.07)
        _paint(img, inside & ~north, col, tile + 0.05)
        _paint(img, inside & (np.abs(BY - mid) < 0.25), (125, 70, 55))
    _paint(img, eaves & ~roof, (226, 222, 214))
    # trees: dark blobs with a lit side
    for tx, ty, rad, _hh in TREES:
        c = building_to_world(np.array([[tx, ty]]))[0]
        r = np.hypot(XX - c[0], YY - c[1])
        crown = r < rad * 1.45
        lit = 1.0 - 0.45 * np.clip(((XX - c[0]) * -0.6 + (YY - c[1]) * 0.6) / (rad * 1.45) + 0.5, 0, 1)
        _paint(img, crown, (46, 88, 40))
        img[crown] *= (lit[crown] * (1.0 + 0.5 * fine[crown]))[:, None]
    return np.clip(img, 0, 255).astype(np.uint8), EXTENT


def render_facade(facade, margins, pixel_m, seed=0, max_side=1400):
    """A procedural orthophoto of one façade (plaster, floors, windows with shutters, balconies, an arcade at the foot, sky and ground
    in the margins). Returns (RGB uint8 array, effective pixel size in metres): the picture is limited to `max_side` pixels."""
    L, H = facade.length, facade.height
    ml, mr, mt, mb = margins.left, margins.right, margins.top, margins.bottom
    wid, hei = L + ml + mr, H + mt + mb
    pix = max(float(pixel_m), wid / max_side, hei / max_side)
    Wp, Hp = max(2, int(round(wid / pix))), max(2, int(round(hei / pix)))
    rng = np.random.RandomState(seed + 17 * (ord(facade.letter[0]) if facade.letter else 0))
    s = -ml + (np.arange(Wp) + 0.5) * pix
    t = H + mt - (np.arange(Hp) + 0.5) * pix
    S, T = np.meshgrid(s, t)
    img = np.zeros((Hp, Wp, 3))
    nz = _value_noise(Hp, Wp, max(4, int(1.2 / pix)), rng) - 0.5
    fine = rng.uniform(-0.5, 0.5, (Hp, Wp))
    # sky, brighter at the horizon
    k = np.clip((T - 0) / max(H + mt, 1e-6), 0, 1)
    sky = np.array([205.0, 225.0, 242.0])[None, None, :] * (1 - k[..., None]) + np.array([150.0, 190.0, 230.0])[None, None, :] * k[..., None]
    img[:] = sky
    # ground in the bottom margin
    ground = T < 0
    _paint(img, ground, (126, 124, 116), 0.12 * nz + 0.06 * fine)
    wall = (S >= 0) & (S <= L) & (T >= 0) & (T <= H)
    img[wall] = np.array([238.0, 235.0, 228.0]) * (1.0 + 0.06 * nz[wall] + 0.02 * fine[wall])[:, None]
    streak = wall & (_value_noise(Hp, Wp, 3, rng)[:, ::1] > 0.97)
    img[streak] *= 0.93
    floor_h = 3.0
    fl = np.floor(T / floor_h)
    tf = T - fl * floor_h
    pitch = 3.3
    sp = (S % pitch) - pitch / 2
    ok = wall & (fl >= 1) & ((fl * floor_h + 2.55) < (H - 0.45))          # only whole floors get windows
    window = ok & (tf > 0.85) & (tf < 2.45) & (np.abs(sp) < 0.62)
    frame = ok & (tf > 0.80) & (tf < 2.50) & (np.abs(sp) < 0.68)
    shutter = ok & (tf > 0.80) & (tf < 2.50) & (np.abs(sp) >= 0.68) & (np.abs(sp) < 1.2)
    img[frame] = (245, 245, 240)
    glass = 55.0 + 30.0 * np.clip((T - fl * floor_h - 0.85) / 1.6, 0, 1)
    img[window] = np.stack([glass[window] * 0.95, glass[window] * 1.12, glass[window] * 1.35], axis=1)
    slats = ((T * 18.0) % 1.0) < 0.35
    _paint(img, shutter & slats, (96, 112, 92))
    _paint(img, shutter & ~slats, (118, 134, 112))
    # balconies on the fronts that face the street (south-ish), as slabs at every floor
    if 120.0 <= facade.azimuth <= 240.0:
        slab = ok & (tf < 0.18)
        _paint(img, slab, (150, 148, 142))
        rail = ok & (tf >= 0.18) & (tf < 0.95) & (((S * 4.0) % 1.0) < 0.5) & ((S % pitch) > 0.5) & ((S % pitch) < pitch - 0.5)
        _paint(img, rail & ~window, (70, 76, 84))
    # the arcade at the foot: dark openings, and a plinth
    arcade = wall & (T < 2.7) & (((S % 5.0) > 0.9) & ((S % 5.0) < 4.1))
    _paint(img, arcade, (46, 46, 52), 0.2 * nz)
    _paint(img, wall & (T < 0.25), (160, 156, 148))
    # gutter and parapet under the roof line
    _paint(img, wall & (T > H - 0.45), (176, 150, 130))
    _paint(img, (S >= -0.05) & (S <= 0.05) & (T >= 0) & (T <= H), (170, 168, 160))
    _paint(img, (S >= L - 0.05) & (S <= L + 0.05) & (T >= 0) & (T <= H), (170, 168, 160))
    return np.clip(img, 0, 255).astype(np.uint8), pix


def resize_nearest(img, width):
    """Resize an RGB array to `width` pixels wide (nearest neighbour, keeps the aspect ratio)."""
    h, w = img.shape[:2]
    if w <= width:
        return img.copy()
    nh = max(1, int(round(h * width / float(w))))
    yi = np.minimum(h - 1, ((np.arange(nh) + 0.5) * h / nh).astype(int))
    xi = np.minimum(w - 1, ((np.arange(width) + 0.5) * w / width).astype(int))
    return img[yi][:, xi]


# ---------------------------------------------------------------------------------------------------------------------
# Image files without any library
# ---------------------------------------------------------------------------------------------------------------------

def write_png(path, rgb):
    """Write an RGB uint8 array as PNG (zlib + struct only)."""
    a = np.ascontiguousarray(rgb, dtype=np.uint8)
    h, w = a.shape[:2]
    raw = b"".join(b"\x00" + a[i].tobytes() for i in range(h))

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 6))
                + chunk(b"IEND", b""))


def write_tiff(path, rgb):
    """Write an RGB uint8 array as an uncompressed baseline TIFF."""
    a = np.ascontiguousarray(rgb, dtype=np.uint8)
    h, w = a.shape[:2]
    data = a.tobytes()
    entries = [(256, 4, 1, w), (257, 4, 1, h), (258, 3, 3, 0), (259, 3, 1, 1), (262, 3, 1, 2), (273, 4, 1, 0), (277, 3, 1, 3), (278, 4, 1, h),
               (279, 4, 1, len(data))]
    ifd_off = 8
    ifd_size = 2 + 12 * len(entries) + 4
    bps_off = ifd_off + ifd_size
    data_off = bps_off + 6
    out = [struct.pack("<2sHI", b"II", 42, ifd_off), struct.pack("<H", len(entries))]
    for tag, typ, cnt, val in entries:
        if tag == 258:
            val = bps_off
        if tag == 273:
            val = data_off
        out.append(struct.pack("<HHII", tag, typ, cnt, val) if typ == 4 or tag == 258 else struct.pack("<HHIHH", tag, typ, cnt, val, 0))
    out.append(struct.pack("<I", 0))
    out.append(struct.pack("<HHH", 8, 8, 8))
    out.append(data)
    with open(path, "wb") as f:
        f.write(b"".join(out))


def write_image(path, rgb, fmt):
    """png and tif need nothing; jpg needs Pillow (raises RuntimeError if it is missing)."""
    fmt = fmt.lower().lstrip(".")
    if fmt == "png":
        write_png(path, rgb)
    elif fmt in ("tif", "tiff"):
        write_tiff(path, rgb)
    elif fmt in ("jpg", "jpeg"):
        try:
            from PIL import Image
        except ImportError:
            raise RuntimeError("JPEG output needs the Pillow library, which is not installed here")
        Image.fromarray(np.ascontiguousarray(rgb, dtype=np.uint8)).save(path, quality=92)
    else:
        raise ValueError("unknown format: %s" % fmt)


# ---------------------------------------------------------------------------------------------------------------------
# The backend
# ---------------------------------------------------------------------------------------------------------------------

class FakeBackend(Backend):
    """Synthetic building behind the Backend protocol. See the module docstring.

    delay: seconds slept per simulated stage (0 in tests). Detection takes ~6 stages, a DEM ~4 per façade, an orthophoto ~3.
    fail: {letter: message} DEM builds that fail; fail_ortho: same for the orthophoto step; fail_export: same for export.
    existing: letters whose DEM is already in the chunk (for every method/margins) when the wizard starts (tests screen 5's shortcut).
    strict: an orthophoto needs its DEM first, a thumbnail/export needs its orthophoto (like the real adapter)."""

    def __init__(self, delay=0.05, tie_points=True, depth_maps=True, dense_cloud=True, ortho=True, fail=None, fail_ortho=None,
                 fail_export=None, existing=(), strict=True, seed=1, site="CARRO", date="261106", outputs=None, chunk="Chunk 1"):
        self.delay = float(delay)
        self.flags = dict(tie_points=tie_points, depth_maps=depth_maps, dense_cloud=dense_cloud)
        self.has_ortho = ortho
        self.fail = dict(fail or {})
        self.fail_ortho = dict(fail_ortho or {})
        self.fail_export = dict(fail_export or {})
        self.existing = set(existing)
        self.strict = strict
        self.site, self.date, self.chunk = site, date, chunk
        self.outputs = outputs or os.path.join(tempfile.gettempdir(), "aerofacade_fake_outputs")
        self.seed = seed
        scene = build_scene(seed)
        self.tie_points = scene["points"]
        self.labels = scene["labels"]
        self.cameras = scene["cameras"]
        self.truth = scene["truth"]
        self._preview = {}
        self._dems = set()
        self._orthos = {}
        self.calls = []

    # ---- helpers ---------------------------------------------------------------------------------------------------
    def _wait(self, prog, f0, f1, text, ticks=4):
        """Simulate a stage: report f0 -> f1 while sleeping `delay` seconds in `ticks` slices (cancel is checked every slice)."""
        for i in range(ticks):
            prog(f0 + (f1 - f0) * i / float(ticks), text)
            if self.delay > 0:
                time.sleep(self.delay / ticks)
        prog(f1, text)

    @staticmethod
    def _mkey(margins, facade):
        m = margins_for(margins, facade)
        return (round(m.left, 3), round(m.right, 3), round(m.top, 3), round(m.bottom, 3), round(m.depth, 3))

    def _dem_key(self, facade, margins, method):
        return (facade.letter, Method.from_key(method).value, self._mkey(margins, facade))

    # ---- protocol --------------------------------------------------------------------------------------------------
    def project_info(self):
        return {"chunk": self.chunk, "site": self.site, "date": self.date, "outputs": self.outputs, "crs": "Local ENU (metres)"}

    def availability(self):
        reasons = {"tie_points": "no tie points in this chunk", "depth_maps": "no depth maps in this chunk",
                   "dense_cloud": "no dense cloud in this chunk"}
        return {k: (bool(v), "" if v else reasons[k]) for k, v in self.flags.items()}

    def ortho_preview(self, max_px=1600):
        self.calls.append(("ortho_preview", max_px))
        if not self.has_ortho:
            raise RuntimeError("This chunk has no orthomosaic: build one first (Workflow > Build Orthomosaic).")
        if max_px not in self._preview:
            self._preview[max_px] = render_ortho(max_px, self.seed)
        img, extent = self._preview[max_px]
        return img.copy(), tuple(extent)

    def detect(self, polygon, progress, cancelled):
        self.calls.append(("detect", len(polygon)))
        prog = Progress.of(progress, cancelled)
        self._wait(prog, 0.00, 0.12, "Reading the tie points")
        self._wait(prog, 0.12, 0.25, "Keeping the points inside the search area")
        self._wait(prog, 0.25, 0.35, "Thinning the cloud")
        inner = prog.sub(0.35, 0.95)
        facades = D.detect_in_polygon(self.tie_points, polygon, inner, cancelled, cameras=self.cameras)
        self._wait(prog, 0.95, 1.0, "%d façade(s) found" % len(facades), ticks=2)
        assign_names(facades)
        return facades

    def existing_dems(self, facades, margins, method):
        return {f.letter: (f.letter in self.existing or self._dem_key(f, margins, method) in self._dems) for f in facades}

    def build_dems(self, facades, margins, method, progress, cancelled):
        self.calls.append(("build_dems", [f.letter for f in facades], Method.from_key(method).value))
        prog = Progress.of(progress, cancelled)
        todo = [f for f in facades if not f.removed]
        result = {}
        slow = {"dense_cloud": 1.0, "depth_maps": 2.0, "tie_points": 0.5}[Method.from_key(method).value]
        for i, f in enumerate(todo):
            if prog.is_cancelled():
                break
            a, b = i / float(len(todo)), (i + 1) / float(len(todo))
            try:
                self._wait(prog, a, a + (b - a) * 0.3, "Facade %s: preparing the region" % f.letter)
                if f.letter in self.fail:
                    result[f.letter] = (False, self.fail[f.letter])
                    prog(b, "Facade %s failed" % f.letter)
                    continue
                if self.delay > 0:
                    time.sleep(self.delay * (slow - 1.0) if slow > 1.0 else 0)
                self._wait(prog, a + (b - a) * 0.3, a + (b - a) * 0.9, "Facade %s: building the DEM" % f.letter)
                self._dems.add(self._dem_key(f, margins, method))
                result[f.letter] = (True, "DEM built")
                prog(b, "Facade %s done" % f.letter)
            except Cancelled:
                break
        return result

    def build_orthos(self, facades, margins, method, pixel_m, progress, cancelled):
        self.calls.append(("build_orthos", [f.letter for f in facades], pixel_m))
        prog = Progress.of(progress, cancelled)
        todo = [f for f in facades if not f.removed]
        result = {}
        for i, f in enumerate(todo):
            if prog.is_cancelled():
                break
            a, b = i / float(len(todo)), (i + 1) / float(len(todo))
            try:
                self._wait(prog, a, a + (b - a) * 0.4, "Facade %s: building the orthophoto" % f.letter)
                if self.strict and f.letter not in self.existing and self._dem_key(f, margins, method) not in self._dems:
                    result[f.letter] = (False, "The DEM of this facade is not built yet.")
                    continue
                if f.letter in self.fail_ortho:
                    result[f.letter] = (False, self.fail_ortho[f.letter])
                    continue
                img, pix = render_facade(f, margins_for(margins, f), pixel_m, self.seed)
                self._orthos[f.letter] = dict(img=img, pixel=pix, wanted=pixel_m, margins=margins_for(margins, f).copy(), facade=f)
                self._wait(prog, a + (b - a) * 0.4, b, "Facade %s: saving the picture" % f.letter, ticks=2)
                result[f.letter] = (True, "%d x %d px" % (img.shape[1], img.shape[0]))
            except Cancelled:
                break
        return result

    def thumbnail(self, facade):
        o = self._orthos.get(facade.letter)
        if o is None:
            raise RuntimeError("The orthophoto of facade %s is not built yet." % facade.letter)
        return resize_nearest(o["img"], 240)

    def export(self, facades, folder, fmt, options, progress, cancelled):
        self.calls.append(("export", [f.letter for f in facades], fmt))
        options = options or {}
        prog = Progress.of(progress, cancelled)
        fmt = fmt.lower().lstrip(".")
        ext = {"jpeg": "jpg", "tiff": "tif"}.get(fmt, fmt)
        os.makedirs(folder, exist_ok=True)
        todo = [f for f in facades if not f.removed]
        out = []
        for i, f in enumerate(todo):
            if prog.is_cancelled():
                break
            a, b = i / float(len(todo)), (i + 1) / float(len(todo))
            path = os.path.join(folder, "%s_%s_facade_%s.%s" % (self.site, self.date, f.letter, ext))
            try:
                self._wait(prog, a, a + (b - a) * 0.8, "Writing %s" % os.path.basename(path), ticks=2)
                o = self._orthos.get(f.letter)
                if o is None:
                    out.append((path, False, "The orthophoto of facade %s is not built yet." % f.letter))
                    continue
                if f.letter in self.fail_export:
                    out.append((path, False, self.fail_export[f.letter]))
                    continue
                write_image(path, o["img"], ext)
                if options.get("georef_txt", True):
                    self._write_georef(os.path.splitext(path)[0] + ".txt", f, o)
                if options.get("dxf"):
                    self._write_dxf(os.path.splitext(path)[0] + ".dxf", f, o)
                out.append((path, True, "%d x %d px" % (o["img"].shape[1], o["img"].shape[0])))
                prog(b, "Facade %s exported" % f.letter)
            except Cancelled:
                break
            except (RuntimeError, ValueError, OSError) as exc:
                out.append((path, False, str(exc)))
        return out

    # ---- sidecar files ---------------------------------------------------------------------------------------------
    @staticmethod
    def _write_georef(path, f, o):
        with open(path, "w") as fh:
            fh.write("\n".join(georef_lines(f, o["margins"], o["pixel"])) + "\n")

    @staticmethod
    def _write_dxf(path, f, o):
        m = o["margins"]
        corners = [f.point_at(-m.left, -m.bottom), f.point_at(f.length + m.right, -m.bottom),
                   f.point_at(f.length + m.right, f.height + m.top), f.point_at(-m.left, f.height + m.top)]
        rows = ["0", "SECTION", "2", "ENTITIES"]
        for k in range(4):
            a, b = corners[k], corners[(k + 1) % 4]
            rows += ["0", "LINE", "8", "AERO_FACADE_%s" % f.letter, "10", "%.4f" % a[0], "20", "%.4f" % a[1], "30", "%.4f" % a[2],
                     "11", "%.4f" % b[0], "21", "%.4f" % b[1], "31", "%.4f" % b[2]]
        rows += ["0", "ENDSEC", "0", "EOF"]
        with open(path, "w") as fh:
            fh.write("\n".join(rows) + "\n")
