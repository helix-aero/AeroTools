"""Synthetic scene for the Aero-Facade study: terrain + buildings (walls, roofs) + trees + outliers, with ground truth.

Labels: 0 ground, 1 wall, 2 roof, 3 vegetation, 4 outlier, 5 low wall (fence, below the wall height limit).
"""

import math

import numpy as np

GROUND, WALL, ROOF, TREE, OUTLIER, FENCE = range(6)


def _rot(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s], [s, c]])


def make_scene(seed=1, size=100.0, ground_density=8.0, wall_density=2.0, roof_density=6.0, noise=0.04, n_buildings=5,
               lean_deg=0.0, curved=True, trees=6, tree_density=3.0, outliers=0.002, rotation_deg=None, offset=(1.2e6, 6.2e6),
               visible_range=(0.3, 1.0), gable=True):
    """Return (points (N,3), labels (N,), walls) ; walls = list of dict(x0,y0,x1,y1,z0,z1,tilt,kind) in the output frame.

    wall_density: tie points per m2 of facade (before the random per-face visibility factor in `visible_range`).
    noise: standard deviation of the position of a wall point, perpendicular to the wall (m)."""
    rng = np.random.RandomState(seed)
    pts, lab, walls = [], [], []

    # terrain: gentle slope + two low hills + noise
    n = int(size * size * ground_density)
    g = rng.uniform(0, size, (n, 2))
    gz = 0.03 * g[:, 0] + 0.015 * g[:, 1] + 1.5 * np.exp(-(((g[:, 0] - 30) ** 2 + (g[:, 1] - 70) ** 2) / 300.0)) \
        + rng.normal(0, 0.04, n)
    pts.append(np.column_stack([g, gz]))
    lab.append(np.full(n, GROUND))

    def terrain(x, y):
        return 0.03 * x + 0.015 * y + 1.5 * np.exp(-(((x - 30) ** 2 + (y - 70) ** 2) / 300.0))

    def face(p0, p1, z_base, height, lean, density, kind, vis):
        """Points on the vertical (or leaning) rectangle from p0 to p1; `lean` = degrees, + = leaning outward."""
        length = float(np.hypot(*(np.asarray(p1) - np.asarray(p0))))
        m = rng.poisson(length * height * density * vis)
        if m == 0:
            return
        t = rng.uniform(0, length, m)
        v = rng.uniform(0, height, m)
        u = (np.asarray(p1) - np.asarray(p0)) / length
        nrm = np.array([-u[1], u[0]])
        off = rng.normal(0, noise, m) + v * math.tan(math.radians(lean))
        xy = np.asarray(p0)[None, :] + t[:, None] * u[None, :] + off[:, None] * nrm[None, :]
        z = z_base + v + rng.normal(0, noise * 0.5, m)
        pts.append(np.column_stack([xy, z]))
        lab.append(np.full(m, kind))

    # buildings
    placed = []
    for b in range(n_buildings):
        for _ in range(50):
            w, d_, h = rng.uniform(8, 22), rng.uniform(6, 14), rng.uniform(4.5, 11)
            cx, cy = rng.uniform(15, size - 15, 2)
            if all(math.hypot(cx - q[0], cy - q[1]) > 28 for q in placed):
                break
        placed.append((cx, cy))
        ang = rng.uniform(0, math.pi)
        R = _rot(ang)
        corners = np.array([[-w / 2, -d_ / 2], [w / 2, -d_ / 2], [w / 2, d_ / 2], [-w / 2, d_ / 2]])
        C = corners @ R.T + np.array([cx, cy])
        zb = float(terrain(cx, cy))
        lean = lean_deg if (b == 0 and lean_deg) else 0.0
        for k in range(4):
            p0, p1 = C[k], C[(k + 1) % 4]
            vis = rng.uniform(*visible_range)
            face(p0, p1, zb, h, lean, wall_density, WALL, vis)
            walls.append(dict(x0=p0[0], y0=p0[1], x1=p1[0], y1=p1[1], z0=zb, z1=zb + h, tilt=lean, kind="wall", vis=vis,
                              building=b))
        # roof: flat or gable, sampled uniformly in plan
        m = rng.poisson(w * d_ * roof_density)
        local = np.column_stack([rng.uniform(-w / 2, w / 2, m), rng.uniform(-d_ / 2, d_ / 2, m)])
        if gable and b % 2 == 1:
            rz = zb + h + (d_ / 2 - np.abs(local[:, 1])) * math.tan(math.radians(28))
        else:
            rz = np.full(m, zb + h)
        xy = local @ R.T + np.array([cx, cy])
        pts.append(np.column_stack([xy, rz + rng.normal(0, 0.05, m)]))
        lab.append(np.full(m, ROOF))

    # a curved wall (arc of radius 18 m, 60 degrees, 5 m high), only the outside is visible
    if curved:
        cx, cy, r = size * 0.5, size * 0.12, 18.0
        a0 = rng.uniform(0, math.pi)
        arc = np.radians(np.linspace(0, 60, 13))
        P = np.column_stack([cx + r * np.cos(a0 + arc), cy + r * np.sin(a0 + arc)])
        zb = float(terrain(cx, cy))
        for k in range(12):
            face(P[k], P[k + 1], zb, 5.0, 0.0, wall_density, WALL, 0.8)
            walls.append(dict(x0=P[k, 0], y0=P[k, 1], x1=P[k + 1, 0], y1=P[k + 1, 1], z0=zb, z1=zb + 5.0, tilt=0.0,
                              kind="curved", vis=0.8, building=-1))

    # a low wall (1.2 m): must not be reported as a wall when hmin = 2 m
    p0, p1 = np.array([size * 0.12, size * 0.9]), np.array([size * 0.4, size * 0.88])
    face(p0, p1, float(terrain(*p0)), 1.2, 0.0, wall_density * 2, FENCE, 1.0)
    walls.append(dict(x0=p0[0], y0=p0[1], x1=p1[0], y1=p1[1], z0=float(terrain(*p0)), z1=float(terrain(*p0)) + 1.2, tilt=0.0,
                      kind="fence", vis=1.0, building=-2))

    # trees: gaussian 3D blobs
    for _ in range(trees):
        cx, cy = rng.uniform(5, size - 5, 2)
        rad, hh = rng.uniform(1.5, 3.0), rng.uniform(5, 9)
        m = rng.poisson(tree_density * 4 / 3 * math.pi * rad * rad * hh / 2)
        q = np.column_stack([rng.normal(0, rad / 2, m) + cx, rng.normal(0, rad / 2, m) + cy,
                             float(terrain(cx, cy)) + np.clip(rng.normal(hh * 0.6, hh * 0.2, m), 1.0, hh)])
        pts.append(q)
        lab.append(np.full(m, TREE))

    # outliers
    m = int(size * size * outliers * 10)
    pts.append(np.column_stack([rng.uniform(0, size, (m, 2)), rng.uniform(-2, 15, m)]))
    lab.append(np.full(m, OUTLIER))

    P = np.vstack(pts)
    L = np.concatenate(lab)
    # rotate the whole scene in plan and move it to projected-like coordinates
    a = math.radians(rng.uniform(0, 180) if rotation_deg is None else rotation_deg)
    R = _rot(a)
    c = np.array([size / 2, size / 2])
    P[:, :2] = (P[:, :2] - c) @ R.T + np.array(offset)
    for w in walls:
        for k in ("0", "1"):
            xy = (np.array([w["x" + k], w["y" + k]]) - c) @ R.T + np.array(offset)
            w["x" + k], w["y" + k] = float(xy[0]), float(xy[1])
    return P, L, walls
