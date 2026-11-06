"""Aero-Facade (feasibility prototype): find vertical walls in a sparse point cloud.

Pure numpy, no Metashape, no Qt. Input: an (N, 3) array of metric coordinates (tie points, in a local or projected frame).
Output: a list of `Wall` (a vertical plane patch: two end points in plan view, a height range, the fitted tilt, a score).

Three detectors share one validation step (`_finish`), so they can be compared on the same data:

  grid_hough   plan-view grid -> cells with a large vertical extent -> Hough transform on those cells -> point-level refit
  ransac_line  sequential RANSAC of vertical planes (lines in plan view) on the points that stand above the local ground
  ransac_plane RANSAC of near-vertical 3D planes on loosely pre-selected points (walls that lean up to `tilt_max` degrees)

A wall is evidence of "many points over a thin footprint, spread over a height range". The score used to rank a hypothesis is the
number of occupied 1 m x 1 m cells of its (along-wall, height) image: a wall fills a rectangle, a roof or a ground strip fills one
row, so they cannot win.
"""

import math
from dataclasses import dataclass, field

import numpy as np


@dataclass
class Params:
    tol: float = 0.15            # half width of the band around a wall (m): tie point noise + wall flatness
    hmin: float = 2.0            # smallest vertical extent (5-95 % of the heights) of a wall (m)
    lmin: float = 3.0            # shortest wall (m)
    gap: float = 4.0             # a wall is cut where the points leave a gap longer than this (m)
    cell: float = 1.0            # plan-view grid cell (m), grid_hough and the pre-selection of ransac_plane
    cell_pts: int = 2            # least number of points in a wall cell
    min_pts: int = 25            # least number of points in a wall
    min_occ: int = 20            # least number of occupied 1 m cells in the (along, height) image (~ 4 m x 5 m of wall)
    contrast: float = 2.5        # points in the band / points in the two flanking bands (density ratio)
    max_corr: float = 0.85       # |corr(height, along)| above this = a slope (roof, terrain), not a wall
    ground_h: float = 0.4        # a point is "elevated" when it stands this much above the local ground (m)
    ground_cell: float = 3.0     # size of the cell whose lowest point is the local ground (m)
    tilt_max: float = 12.0       # ransac_plane: planes leaning more than this are ignored (degrees)
    n_iter: int = 4000           # RANSAC hypotheses
    n_sub: int = 80000           # points used to score the hypotheses (the refit uses all of them)
    max_walls: int = 40
    seed: int = 0


@dataclass
class Wall:
    x0: float
    y0: float
    x1: float
    y1: float
    z0: float                    # low and high end of the wall (5 % and 95 % of the heights)
    z1: float
    nx: float                    # unit horizontal normal (pointing to +x/+y side, sign is arbitrary)
    ny: float
    d: float                     # plane: nx*x + ny*y = d (for a leaning plane: at mid-height)
    tilt: float                  # degrees from vertical (0 = plumb)
    n: int
    rms: float                   # rms distance of the points to the plane (m)
    contrast: float
    score: int
    idx: np.ndarray = field(default=None, repr=False)   # indices of the points (into the input array)

    @property
    def length(self):
        return math.hypot(self.x1 - self.x0, self.y1 - self.y0)

    @property
    def height(self):
        return self.z1 - self.z0

    @property
    def azimuth(self):
        """Direction of the wall in plan view, degrees in [0, 180)."""
        return math.degrees(math.atan2(self.y1 - self.y0, self.x1 - self.x0)) % 180.0


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------

def _keys(xy, cell):
    ij = np.floor(xy / cell).astype(np.int64)
    return (ij[:, 0] + (1 << 30)) * (1 << 31) + (ij[:, 1] + (1 << 30))


def _group(keys):
    """Sort order, group starts and counts of equal keys."""
    order = np.argsort(keys, kind="stable")
    ks = keys[order]
    starts = np.flatnonzero(np.r_[True, ks[1:] != ks[:-1]])
    counts = np.diff(np.r_[starts, len(ks)])
    return order, starts, counts


def elevated(P, p):
    """Mask of the points that stand at least `ground_h` above the lowest point of their `ground_cell` cell."""
    if len(P) == 0:
        return np.zeros(0, bool)
    order, starts, counts = _group(_keys(P[:, :2], p.ground_cell))
    zmin = np.minimum.reduceat(P[order, 2], starts)
    low = np.empty(len(P))
    low[order] = np.repeat(zmin, counts)
    return P[:, 2] - low >= p.ground_h


def wall_cells(P, p, cell=None, hmin=None):
    """Elevated points of the grid cells whose vertical extent (over the elevated points) is >= hmin and that hold >= cell_pts
    of them. Only elevated points count, so a lone outlier above the ground does not make a wall. Returns an index array."""
    cell = cell or p.cell
    hmin = p.hmin if hmin is None else hmin
    ev = np.flatnonzero(elevated(P, p))
    if len(ev) == 0:
        return ev
    order, starts, counts = _group(_keys(P[ev, :2], cell))
    z = P[ev[order], 2]
    zr = np.maximum.reduceat(z, starts) - np.minimum.reduceat(z, starts)
    good = (zr >= hmin) & (counts >= p.cell_pts)
    keep = np.repeat(good, counts)
    return ev[order[keep]]


def _fit_line(xy):
    """Total least squares line through 2D points: (unit normal, offset, unit direction)."""
    c = xy.mean(axis=0)
    u, s, vt = np.linalg.svd(xy - c, full_matrices=False)
    direction = vt[0]
    normal = np.array([-direction[1], direction[0]])
    return normal, float(normal @ c), direction


def _fit_plane(P):
    """Total least squares plane: (unit normal, offset, centroid)."""
    c = P.mean(axis=0)
    u, s, vt = np.linalg.svd(P - c, full_matrices=False)
    n = vt[2]
    return n, float(n @ c), c


def _occupied(t, z, cell=1.0):
    k = np.floor(t / cell).astype(np.int64) * 100003 + np.floor(z / cell).astype(np.int64)
    return len(np.unique(k))


def _score(Q, n, d, u, p):
    """Occupied (along, height) cells of the band |n.x - d| < tol around a vertical-ish plane. Q: (M, 3) points."""
    r = Q @ n - d
    m = np.abs(r) < p.tol
    cnt = int(m.sum())
    if cnt < p.min_pts:
        return 0
    A = Q[m]
    return _occupied(A[:, 0] * u[0] + A[:, 1] * u[1], A[:, 2])


# --------------------------------------------------------------------------
# Segmenting and validating one fitted plane (shared by the three detectors)
# --------------------------------------------------------------------------

def _finish(P, pool, n3, d, p, vertical=True, ref=None):
    """From a plane hypothesis (unit normal n3 with a horizontal-ish normal, offset d) to a list of validated Wall pieces.

    P: all points. pool: indices of the points still available. Returns (walls, used indices)."""
    Q = P[pool]
    for _ in range(2):                                  # refit on the inliers, twice
        r = Q @ n3 - d
        sel = np.abs(r) < p.tol * 1.5
        if sel.sum() < p.min_pts:
            return [], np.empty(0, dtype=np.int64)
        if vertical:
            n2v, d, _dir = _fit_line(Q[sel, :2])
            n3 = np.array([n2v[0], n2v[1], 0.0])
        else:
            n3, d, _c = _fit_plane(Q[sel])
        if n3[:2] @ n3[:2] < 1e-9:
            return [], np.empty(0, dtype=np.int64)
    h = np.hypot(n3[0], n3[1])
    n2 = n3[:2] / h
    if n2[0] < -1e-9 or (abs(n2[0]) <= 1e-9 and n2[1] < 0):
        n3, d, n2 = -n3, -d, -n2
    u = np.array([-n2[1], n2[0]])
    r = Q @ n3 - d
    band = np.abs(r) < p.tol
    ib = np.flatnonzero(band)
    if len(ib) < p.min_pts:
        return [], np.empty(0, dtype=np.int64)
    t = Q[ib, :2] @ u
    order = np.argsort(t)
    ib, t = ib[order], t[order]
    cuts = np.flatnonzero(np.diff(t) > p.gap) + 1
    walls, used = [], []
    tilt = math.degrees(math.asin(min(1.0, abs(n3[2]))))
    for run in np.split(np.arange(len(ib)), cuts):
        if len(run) < p.min_pts:
            continue
        sel = ib[run]
        ts = t[run]
        z = Q[sel, 2]
        length = ts[-1] - ts[0]
        if length < p.lmin:
            continue
        z5, z95 = np.percentile(z, (5, 95))
        if z95 - z5 < p.hmin:
            continue
        if np.std(ts) > 1e-6 and np.std(z) > 1e-6 and abs(np.corrcoef(ts, z)[0, 1]) > p.max_corr:
            continue
        occ = _occupied(ts, z)
        if occ < p.min_occ:
            continue
        # contrast: density in the band against the two flanking bands, over the same stretch of wall
        RQ = Q if ref is None else ref
        rr = RQ @ n3 - d
        ua = RQ[:, :2] @ u
        along = (ua >= ts[0]) & (ua <= ts[-1])
        flank = int((along & (np.abs(rr) >= p.tol) & (np.abs(rr) < 4 * p.tol)).sum())
        inband = int((along & (np.abs(rr) < p.tol)).sum())
        contrast = 3.0 * inband / max(flank, 1)
        if contrast < p.contrast:
            continue
        zm = 0.5 * (z5 + z95)
        offs = (d - n3[2] * zm) / h                       # plan-view offset of the wall trace at mid-height
        p0 = n2 * offs + u * ts[0]
        p1 = n2 * offs + u * ts[-1]
        res = r[sel]
        walls.append(Wall(float(p0[0]), float(p0[1]), float(p1[0]), float(p1[1]), float(z5), float(z95), float(n2[0]),
                          float(n2[1]), float(offs), tilt, int(len(sel)), float(np.sqrt(np.mean(res ** 2))), float(contrast),
                          int(occ), pool[sel]))
        used.append(pool[sel])
    return walls, (np.concatenate(used) if used else np.empty(0, dtype=np.int64))


def _lazy_greedy(P, pool, hyps, p, scorer, vertical=True, ref=None):
    """Pick the best hypotheses one after the other, re-scoring against the points left (lazy greedy selection)."""
    alive = np.zeros(len(P), bool)
    alive[pool] = True
    scored = sorted(((s, i) for i, s in enumerate(scorer(hyps, alive)) if s > 0), reverse=True)
    walls = []
    heap = [list(x) for x in scored]
    heap.sort(reverse=True)
    guard = 0
    while heap and len(walls) < p.max_walls and guard < 4000:
        guard += 1
        s, i = heap.pop(0)
        s2 = scorer([hyps[i]], alive)[0]
        if heap and s2 < heap[0][0]:
            if s2 > 0:
                heap.append([s2, i])
                heap.sort(reverse=True)
            continue
        if s2 <= 0:
            continue
        n3, d = hyps[i]
        found, used = _finish(P, np.flatnonzero(alive), n3, d, p, vertical, ref)
        if len(used):
            alive[used] = False
            walls.extend(found)
        else:
            # the hypothesis was a false alarm: forget it (its points stay available)
            continue
    return walls


# --------------------------------------------------------------------------
# Detector 1: plan-view grid + Hough
# --------------------------------------------------------------------------

def grid_hough(P, p=None, exclude=None):
    """Plan-view grid + Hough. The Hough transform runs on the centres of the wall cells (a few thousand), not on the points, so the
    time hardly depends on the number of tie points; the accumulator is updated incrementally when a wall is taken out."""
    p = p or Params()
    P = np.asarray(P, dtype=np.float64)
    cand = wall_cells(P, p)
    if exclude is not None and len(cand):
        keep = np.ones(len(P), bool)
        keep[exclude] = False
        cand = cand[keep[cand]]
    if len(cand) < p.min_pts:
        return []
    ref = P[elevated(P, p)]
    if len(ref) > 120000:                       # the contrast is a ratio of counts: a random subset gives the same ratio
        ref = ref[np.random.RandomState(p.seed).choice(len(ref), 120000, replace=False)]
    # cell centroids (weights = 1: a wall cell counts once whatever its number of points)
    order, starts, counts = _group(_keys(P[cand, :2], p.cell))
    cxy = np.add.reduceat(P[cand[order], :2], starts, axis=0) / counts[:, None]
    weight = np.minimum(counts, 8).astype(np.float64)       # a cell counts by its points, capped
    cell_of = np.empty(len(cand), np.int64)
    cell_of[order] = np.repeat(np.arange(len(starts)), counts)
    thetas = np.radians(np.arange(0.0, 180.0, 0.5))
    ct, st = np.cos(thetas), np.sin(thetas)
    rho_bin = max(p.cell * 0.5, p.tol)
    rho = cxy[:, :1] * ct[None, :] + cxy[:, 1:2] * st[None, :]
    rmin = rho.min()
    bins = np.floor((rho - rmin) / rho_bin).astype(np.int64)
    nb = int(bins.max()) + 1
    acc = np.zeros((len(thetas), nb), np.float64)
    for j in range(len(thetas)):
        acc[j] = np.bincount(bins[:, j], weights=weight, minlength=nb)
    alive = np.ones(len(cxy), bool)
    min_votes = max(3, int(p.lmin / p.cell)) * 2.0
    pool_mask = np.ones(len(cand), bool)            # candidate points still available
    walls = []

    def drop_cells(cells):
        cells = cells[alive[cells]]
        if len(cells) == 0:
            return
        alive[cells] = False
        for j in range(len(thetas)):
            acc[j] -= np.bincount(bins[cells, j], weights=weight[cells], minlength=nb)

    fails = 0
    for _ in range(p.max_walls * 100):
        if fails >= 150:                               # 150 peaks in a row that are not walls: what is left is clutter
            break
        j, b = np.unravel_index(int(acc.argmax()), acc.shape)
        if acc[j, b] < min_votes:
            break
        n2 = np.array([ct[j], st[j]])
        d = rmin + (b + 0.5) * rho_bin
        n3 = np.array([n2[0], n2[1], 0.0])
        pool = cand[pool_mask]
        near = pool[np.abs(P[pool, :2] @ n2 - d) < max(1.0, 4 * p.tol)]      # only the points around that line: a failed peak stays cheap
        found, used = _finish(P, near, n3, d, p, True, ref)
        if len(used):
            fails = 0
            walls.extend(found)
            gone = np.isin(cand, used)
            pool_mask[gone] = False
            drop_cells(np.unique(cell_of[gone]))
        else:
            fails += 1
            acc[max(0, j - 4):j + 5, max(0, b - 2):b + 3] = 0.0      # not a wall: silence that peak only
    return walls


# --------------------------------------------------------------------------
# Detector 2: sequential RANSAC of vertical planes (lines in plan view)
# --------------------------------------------------------------------------

def ransac_line(P, p=None):
    p = p or Params()
    P = np.asarray(P, dtype=np.float64)
    rng = np.random.RandomState(p.seed)
    pool = np.flatnonzero(elevated(P, p))
    if len(pool) < p.min_pts:
        return []
    sub = pool if len(pool) <= p.n_sub else rng.choice(pool, p.n_sub, replace=False)
    S = P[sub]
    a = rng.randint(0, len(S), p.n_iter)
    b = rng.randint(0, len(S), p.n_iter)
    dx, dy = S[b, 0] - S[a, 0], S[b, 1] - S[a, 1]
    ln = np.hypot(dx, dy)
    ok = ln > 1.0
    dx, dy, ln, a = dx[ok], dy[ok], ln[ok], a[ok]
    hyps = []
    for i in range(len(a)):
        n2 = np.array([-dy[i] / ln[i], dx[i] / ln[i]])
        hyps.append((np.array([n2[0], n2[1], 0.0]), float(n2 @ S[a[i], :2])))

    # the hypotheses are scored on the subsample only (speed): rebuild a view with the subsample alive
    sub_alive = np.zeros(len(P), bool)
    sub_alive[sub] = True

    def scorer_sub(hs, alive):
        Q = P[alive & sub_alive]
        out = []
        for n3, d in hs:
            u = np.array([-n3[1], n3[0]])
            out.append(_score(Q, n3, d, u, p))
        return out

    return _lazy_greedy(P, pool, hyps, p, scorer_sub, True, P[pool])


# --------------------------------------------------------------------------
# Detector 3: RANSAC of near-vertical 3D planes (walls that lean)
# --------------------------------------------------------------------------

def ransac_plane(P, p=None, exclude=None):
    p = p or Params()
    P = np.asarray(P, dtype=np.float64)
    rng = np.random.RandomState(p.seed)
    # loose pre-selection: a leaning wall has a smaller vertical extent per cell, so the extent is relaxed
    loose = max(p.hmin * 0.5, 1.0)
    cand = wall_cells(P, p, cell=max(p.cell, 1.0), hmin=loose)
    if exclude is not None and len(cand):
        keep = np.ones(len(P), bool)
        keep[exclude] = False
        cand = cand[keep[cand]]
    if len(cand) < p.min_pts:
        return []
    sub = cand if len(cand) <= p.n_sub else rng.choice(cand, p.n_sub, replace=False)
    S = P[sub]
    smax = math.sin(math.radians(p.tilt_max))
    hyps = []
    tries = 0
    while len(hyps) < p.n_iter and tries < p.n_iter * 6:
        tries += 1
        i0, i1, i2 = rng.randint(0, len(S), 3)
        nrm = np.cross(S[i1] - S[i0], S[i2] - S[i0])
        ln = np.linalg.norm(nrm)
        if ln < 1e-6:
            continue
        nrm /= ln
        if abs(nrm[2]) > smax:
            continue
        if min(np.linalg.norm(S[i1] - S[i0]), np.linalg.norm(S[i2] - S[i0])) < 1.0:
            continue
        hyps.append((nrm, float(nrm @ S[i0])))
    sub_alive = np.zeros(len(P), bool)
    sub_alive[sub] = True

    def scorer_sub(hs, alive):
        Q = P[alive & sub_alive]
        out = []
        for n3, d in hs:
            h = math.hypot(n3[0], n3[1])
            u = np.array([-n3[1], n3[0]]) / h
            r = Q @ n3 - d
            m = np.abs(r) < p.tol
            if int(m.sum()) < p.min_pts:
                out.append(0)
                continue
            A = Q[m]
            out.append(_occupied(A[:, 0] * u[0] + A[:, 1] * u[1], A[:, 2]))
        return out

    return _lazy_greedy(P, cand, hyps, p, scorer_sub, vertical=False, ref=P[elevated(P, p)])


def _same_wall(a, b):
    """True if two walls lie on the same plan-view line (within 0.5 m, 6 degrees) and overlap along it."""
    da = abs(((a.azimuth - b.azimuth + 90.0) % 180.0) - 90.0)
    if da > 6.0:
        return False
    u = np.array([math.cos(math.radians(a.azimuth)), math.sin(math.radians(a.azimuth))])
    n = np.array([-u[1], u[0]])
    mid_b = np.array([(b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2])
    mid_a = np.array([(a.x0 + a.x1) / 2, (a.y0 + a.y1) / 2])
    if abs((mid_b - mid_a) @ n) > 0.5:
        return False
    ta = sorted([np.array([a.x0, a.y0]) @ u, np.array([a.x1, a.y1]) @ u])
    tb = sorted([np.array([b.x0, b.y0]) @ u, np.array([b.x1, b.y1]) @ u])
    return min(ta[1], tb[1]) - max(ta[0], tb[0]) > 0.3 * min(ta[1] - ta[0], tb[1] - tb[0])


def combined(P, p=None):
    """Recommended: plumb walls by grid_hough (fast, precise), then leaning walls by plane RANSAC on what is left."""
    p = p or Params()
    P = np.asarray(P, dtype=np.float64)
    walls = grid_hough(P, p)
    used = np.concatenate([w.idx for w in walls]) if walls else np.empty(0, dtype=np.int64)
    extra = ransac_plane(P, p, exclude=used)
    for w in extra:
        # the second pass is stricter (more contrast, flatter): it only has to catch what the plumb pass cannot
        if w.contrast >= 1.8 * p.contrast and w.rms <= 0.6 * p.tol and not any(_same_wall(w, v) for v in walls):
            walls.append(w)
    return walls


DETECTORS = {"grid_hough": grid_hough, "ransac_line": ransac_line, "ransac_plane": ransac_plane, "combined": combined}


def thin(P, voxel=0.10, max_points=600000):
    """Voxel thinning: one point per voxel, the voxel growing until at most `max_points` remain. Dense areas (ground, roofs, a dense
    cloud) lose points, a sparse wall keeps all of them. Returns the indices kept."""
    idx = np.arange(len(P))
    while len(idx) > max_points:
        ij = np.floor(P[idx] / voxel).astype(np.int64)
        key = (ij[:, 0] + (1 << 20)) * (1 << 42) + (ij[:, 1] + (1 << 20)) * (1 << 21) + (ij[:, 2] + (1 << 20))
        _u, first = np.unique(key, return_index=True)
        idx = idx[np.sort(first)]
        voxel *= 1.4
    return idx


def detect(P, method="combined", params=None, origin=True, max_points=600000):
    """Detect walls in P (N, 3). The cloud is shifted to its own centre first (keeps the numbers small whatever the CRS) and
    voxel-thinned if it holds more than `max_points`; the walls come back in the input frame and `Wall.idx` indexes the input array."""
    P = np.asarray(P, dtype=np.float64)
    if len(P) < 3:
        return []
    shift = P.mean(axis=0) if origin else np.zeros(3)
    sel = None
    Q = P - shift
    if max_points and len(P) > max_points:
        sel = thin(Q, 0.10, max_points)
        Q = Q[sel]
    walls = DETECTORS[method](Q, params)
    for w in walls:
        w.x0 += shift[0]
        w.x1 += shift[0]
        w.y0 += shift[1]
        w.y1 += shift[1]
        w.z0 += shift[2]
        w.z1 += shift[2]
        w.d += w.nx * shift[0] + w.ny * shift[1]
        if sel is not None:
            w.idx = sel[w.idx]
    return walls


def main(argv=None):
    """Command line: `python -m aerotools.tools.aerofacade.detect tie_enu.npy [--method grid_hough|combined] [--out walls.json]`.

    Reads an (N, 3) array (.npy, or an .npz with an `enu` or `xyz` array), prints the walls and writes them as JSON (plan-view end points,
    heights, direction, tilt, confidence) in the same frame as the input."""
    import argparse
    import json
    parser = argparse.ArgumentParser(description="Find vertical walls in a sparse point cloud.")
    parser.add_argument("path")
    parser.add_argument("--method", default="grid_hough", choices=sorted(DETECTORS))
    parser.add_argument("--out", default="")
    parser.add_argument("--tol", type=float, default=Params().tol)
    args = parser.parse_args(argv)
    data = np.load(args.path)
    if hasattr(data, "files"):
        data = data["enu"] if "enu" in data.files else data["xyz"]
    walls = detect(data, args.method, Params(tol=args.tol))
    walls.sort(key=lambda w: -(w.length * w.height * w.contrast))
    rows = []
    for k, w in enumerate(walls):
        rows.append(dict(id=k, x0=w.x0, y0=w.y0, x1=w.x1, y1=w.y1, z0=w.z0, z1=w.z1, length=w.length, height=w.height, azimuth=w.azimuth,
                         tilt=w.tilt, points=w.n, contrast=w.contrast, rms=w.rms))
        print("%2d  length %6.1f m  height %5.1f m  azimuth %5.1f  tilt %4.1f  points %6d  contrast %4.1f" % (k, w.length, w.height, w.azimuth, w.tilt, w.n,
                                                                                                           w.contrast))
    if args.out:
        with open(args.out, "w") as handle:
            json.dump(rows, handle, indent=1)
    return rows


if __name__ == "__main__":
    main()
