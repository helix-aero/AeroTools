"""Aero-Facade (feasibility prototype): find vertical walls in a sparse point cloud.

Pure numpy, no Metashape, no Qt. Input: an (N, 3) array of metric coordinates (tie points, in a local or projected frame).
Output: a list of `Wall` (a vertical plane patch: two end points in plan view, a height range, the fitted tilt, a score).

Recommended entry: `detect_in_polygon(points, polygon, progress, cancelled, cameras)` -> list of `model.Facade` (method "auto" =
`two_pass`: a demanding vertical-occupancy pass for the dense, thick façades of real tie points, then a tolerant pass on what is left,
then duplicate / relief suppression, a direction gate and parallel / perpendicular regularisation; see docs/AERO_FACADE_SPEC.md,
"Engine notes"). The original detectors stay available for comparison.

Three detectors share one validation step (`_finish`), so they can be compared on the same data:

  grid_hough   plan-view grid -> cells with a large vertical extent -> Hough transform on those cells -> point-level refit
  ransac_line  sequential RANSAC of vertical planes (lines in plan view) on the points that stand above the local ground
  ransac_plane RANSAC of near-vertical 3D planes on loosely pre-selected points (walls that lean up to `tilt_max` degrees)

A wall is evidence of "many points over a thin footprint, spread over a height range". The score used to rank a hypothesis is the
number of occupied 1 m x 1 m cells of its (along-wall, height) image: a wall fills a rectangle, a roof or a ground strip fills one
row, so they cannot win.
"""

import math
from dataclasses import dataclass, field, replace

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
    max_fails: int = 100         # peaks in a row that are not walls before the search stops
    seed: int = 0
    occ_cell: float = 0.5        # plan cell (m) of the vertical-occupancy test (a wall fills many height bins in a thin column)
    occ_zbin: float = 0.5        # height bin (m) of that test
    dup_offset: float = 0.7      # parallel pieces closer than this (m) and overlapping along the wall are the same wall seen twice
    relief_offset: float = 1.8   # a short piece this close in front of / behind a longer parallel wall is relief (balcony, pilaster)
    relief_len: float = 8.0      # ... when it is shorter than this (m); longer ones are setbacks and stay separate façades
    snap_tol: float = 4.5        # degrees: walls this close to the main axes (or their perpendiculars) are made exactly parallel / perpendicular
    axis_tol: float = 3.0        # degrees: a wall within this of an anchor wall (or of its perpendicular) is "aligned"
    contrast_free: float = 12.0  # contrast a wall with no parallel / perpendicular partner must reach
    partner_dist: float = 80.0   # metres: how far its partner wall may be
    anchor_dist: float = 30.0    # metres: a wall dwarfed (5 x fewer points) by one this close is not an anchor
    shadow: float = 1.2          # cells this close to an accepted wall (m, either side) cannot seed another wall
    min_area: float = 40.0       # smallest wall (length x height, m2): below that it is a parapet, a garden wall or a bush
    back_ratio: float = 0.10     # thick façades: the density behind the wall may be at most this share of the density in its band
    min_fill: float = 0.5        # a wall fills at least this fraction of the 1 m cells of its (length x height) rectangle
    min_density: float = 0.5     # ... and holds at least this many tie points per m2 of wall (the study: 0.5 is the floor for detection)
    occ_min: int = 8             # least number of occupied height bins in a cell: ~4 m of wall covered by points (0 = old extent test)


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
    back: float = 0.0            # density behind the wall / density in its band (0 = empty behind, as a real wall; ~1 = a tree)

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


def occupancy_cells(P, p):
    """Indices of the points that stand in a plan cell whose column holds points in at least `occ_min` different `occ_zbin` height bins.

    A wall fills a column of height bins (tie points over its whole height), a flat or pitched roof one or two, the ground one, so
    roofs and the ground drop out whatever their point density, and stray points under a roof (bad matches) are too few to fill
    a column. Foliage fills columns too, but as a wide blob, not as a thin line: the Hough step that follows rejects it."""
    if len(P) == 0:
        return np.zeros(0, dtype=np.int64)
    ij = np.floor(P[:, :2] / p.occ_cell).astype(np.int64)
    ck = (ij[:, 0] + (1 << 30)) * (1 << 31) + (ij[:, 1] + (1 << 30))
    zb = np.floor((P[:, 2] - P[:, 2].min()) / p.occ_zbin).astype(np.int64)
    pair = ck * 8192 + np.minimum(zb, 8191)
    upair, first = np.unique(pair, return_index=True)
    cells_of_pairs = ck[first]
    ucell, inv = np.unique(cells_of_pairs, return_inverse=True)
    occ = np.bincount(inv)
    good = ucell[occ >= p.occ_min]
    return np.flatnonzero(np.isin(ck, good))


def wall_cells(P, p, cell=None, hmin=None):
    """Elevated points of the grid cells whose vertical extent (over the elevated points) is >= hmin and that hold >= cell_pts
    of them. Only elevated points count, so a lone outlier above the ground does not make a wall. Returns an index array."""
    if p.occ_min > 0 and cell is None and hmin is None:
        return occupancy_cells(P, p)
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


def _z_extent(z):
    """Foot and top of a wall from the heights of its points: the 1 % and 99 % heights widened by the share they cut off (the top of
    a wall is a sharp edge, the foot is fuzzy: bad matches go below the ground, so a min/max would be wrong)."""
    lo, hi = np.percentile(z, (1.0, 99.0))
    half = 0.5 * (hi - lo) / 0.98
    mid = 0.5 * (hi + lo)
    return float(mid - half), float(mid + half)


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
        z5, z95 = _z_extent(z)
        if z95 - z5 < p.hmin:
            continue
        if np.std(ts) > 1e-6 and np.std(z) > 1e-6 and abs(np.corrcoef(ts, z)[0, 1]) > p.max_corr:
            continue
        occ = _occupied(ts, z)
        if occ < p.min_occ:
            continue
        height = z95 - z5
        # contrast: density in the band against the flanking bands, over the same stretch of wall
        RQ = Q if ref is None else ref
        rr = RQ @ n3 - d
        ua = RQ[:, :2] @ u
        along = (ua >= ts[0]) & (ua <= ts[-1])
        flank_in = int((along & (rr >= p.tol) & (rr < 4 * p.tol)).sum())
        flank_out = int((along & (rr <= -p.tol) & (rr > -4 * p.tol)).sum())
        inband = int((along & (np.abs(rr) < p.tol)).sum())
        contrast = 3.0 * inband / max(flank_in + flank_out, 1)         # both flanks together: a thin slab in empty space
        # (thick façades: balconies, shutters and cornices fill the outside flank, so a wall that FILLS its rectangle with points is also
        # accepted on its weaker flank only: the inside of a building is never seen. A hedge or a tree fills both flanks.)
        contrast_thick = 3.0 * inband / max(min(flank_in, flank_out), 1)
        thin_ok = contrast >= p.contrast
        thick_ok = (contrast_thick >= p.contrast and length * height >= p.min_area
                    and occ >= p.min_fill * math.ceil(length) * math.ceil(height) and len(sel) >= p.min_density * length * height)
        back_ratio = 0.0
        if thick_ok or thin_ok:
            # a real wall has nothing behind it (the inside is never seen), a tree or a bush has as many points behind as in front:
            # compare the density in the 0.6 to 2.5 m behind the emptier side with the density in the band, over the wall's own heights
            mid = along & (RQ[:, 2] > z5 + 1.0) & (RQ[:, 2] < z95 - 1.0)
            behind = min(int((mid & (rr > 0.6) & (rr < 2.5)).sum()), int((mid & (rr < -0.6) & (rr > -2.5)).sum()))
            back_ratio = (behind / 1.9) / max(inband / (2.0 * p.tol), 1e-9)
            if not thin_ok and back_ratio > p.back_ratio:
                thick_ok = False
        if not (thin_ok or thick_ok):
            continue
        contrast = max(contrast, contrast_thick if thick_ok else 0.0)
        zm = 0.5 * (z5 + z95)
        offs = (d - n3[2] * zm) / h                       # plan-view offset of the wall trace at mid-height
        p0 = n2 * offs + u * ts[0]
        p1 = n2 * offs + u * ts[-1]
        res = r[sel]
        walls.append(Wall(float(p0[0]), float(p0[1]), float(p1[0]), float(p1[1]), float(z5), float(z95), float(n2[0]),
                          float(n2[1]), float(offs), tilt, int(len(sel)), float(np.sqrt(np.mean(res ** 2))), float(contrast),
                          int(occ), pool[sel], float(back_ratio)))
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

def _snap(offsets, d, p):
    """Move a Hough line to the densest plane within `relief_offset` of it: a thick façade (balconies a metre in front of the wall)
    votes for several parallel lines, and the one to keep is the main plane, the one that holds most points."""
    r = np.asarray(offsets) - d
    reach = p.relief_offset
    if reach < 0.3:
        return d
    r = r[np.abs(r) < reach]
    if len(r) < p.min_pts:
        return d
    bins = np.arange(-reach, reach + 0.05, 0.05)
    h, e = np.histogram(r, bins=bins)
    k = np.array([1.0, 2.0, 3.0, 2.0, 1.0])
    hs = np.convolve(h, k, mode="same")
    j = int(hs.argmax())
    return d + 0.5 * (e[j] + e[j + 1])


def grid_hough(P, p=None, exclude=None, clean=True):
    """Plan-view grid + Hough. The Hough transform runs on the centres of the wall cells (a few thousand), not on the points, so the
    time hardly depends on the number of tie points; the accumulator is updated incrementally when a wall is taken out.

    Wall cells come from the vertical-occupancy test (`occupancy_cells`). The strongest line is snapped to the densest plane within
    `relief_offset` (thick façades), refitted on the points, cut into pieces at gaps and validated (`_finish`); the cells within
    `shadow` of an accepted wall stop voting (relief cannot seed a wall), and at the end `suppress` and `aligned_with_axes` clean up."""
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
    # cells: centroid, weight (a cell counts by its points, capped), and a CSR index cell -> candidate positions
    order, starts, counts = _group(_keys(P[cand, :2], p.cell))
    cxy = np.add.reduceat(P[cand[order], :2], starts, axis=0) / counts[:, None]
    weight = np.minimum(counts, 8).astype(np.float64)
    cell_of = np.empty(len(cand), np.int64)
    cell_of[order] = np.repeat(np.arange(len(starts)), counts)
    cand_pos = np.full(len(P), -1, dtype=np.int64)
    cand_pos[cand] = np.arange(len(cand))
    thetas = np.radians(np.arange(0.0, 180.0, 0.5))
    nt = len(thetas)
    ct, st = np.cos(thetas), np.sin(thetas)
    rho_bin = max(p.cell * 0.5, p.tol)
    rho = cxy[:, :1] * ct[None, :] + cxy[:, 1:2] * st[None, :]
    rmin = rho.min()
    bins = np.floor((rho - rmin) / rho_bin).astype(np.int64)
    nb = int(bins.max()) + 1
    tcol = np.arange(nt)[None, :] * nb

    def votes(cells):
        keys = (tcol + bins[cells]).ravel()
        return np.bincount(keys, weights=np.repeat(weight[cells], nt), minlength=nt * nb).reshape(nt, nb)

    acc = votes(np.arange(len(cxy)))
    alive = np.ones(len(cxy), bool)
    min_votes = max(3, int(p.lmin / p.cell)) * 2.0
    pool_mask = np.ones(len(cand), bool)            # candidate points still available
    walls = []

    def points_of(cells):
        """Candidate positions of the points of some cells."""
        if len(cells) == 0:
            return np.zeros(0, dtype=np.int64)
        c = counts[cells]
        first = starts[cells] - np.cumsum(np.r_[0, c[:-1]])
        return order[np.repeat(first, c) + np.arange(int(c.sum()))]

    def drop_cells(cells):
        cells = cells[alive[cells]]
        if len(cells) == 0:
            return
        alive[cells] = False
        acc[...] -= votes(cells)

    reach = max(1.0, 4 * p.tol)
    fails = 0
    for _ in range(p.max_walls * 100):
        if fails >= p.max_fails:                       # that many peaks in a row that are not walls: what is left is clutter
            break
        j, b = np.unravel_index(int(acc.argmax()), acc.shape)
        if acc[j, b] < min_votes:
            break
        n2 = np.array([ct[j], st[j]])
        d = rmin + (b + 0.5) * rho_bin
        n3 = np.array([n2[0], n2[1], 0.0])
        near_cells = np.flatnonzero(np.abs(cxy @ n2 - d) < reach + 1.5 * p.cell)
        pos = points_of(near_cells)
        pos = pos[pool_mask[pos]]
        near = cand[pos]
        near = near[np.abs(P[near, :2] @ n2 - d) < reach]      # only the points around that line: a failed peak stays cheap
        d = _snap(P[near, :2] @ n2, d, p)
        found, used = _finish(P, near, n3, d, p, True, ref)
        if len(used):
            fails = 0
            walls.extend(found)
            pu = cand_pos[used]
            pool_mask[pu] = False                                   # only the inliers leave the pool (a corner wall may still use shadow points)
            gone = [cell_of[pu]]
            for w in found:                                         # the zone around an accepted wall cannot start another wall
                nrm = np.array([w.nx, w.ny])
                uu = np.array([-w.ny, w.nx])
                ta, tb = sorted((float(np.array([w.x0, w.y0]) @ uu), float(np.array([w.x1, w.y1]) @ uu)))
                along = cxy @ uu
                zone = (np.abs(cxy @ nrm - float(np.array([w.x0, w.y0]) @ nrm)) < p.shadow) & (along > ta - 0.5) & (along < tb + 0.5)
                gone.append(np.flatnonzero(zone))
            drop_cells(np.unique(np.concatenate(gone)))
        else:
            fails += 1
            acc[max(0, j - 4):j + 5, max(0, b - 2):b + 3] = 0.0      # not a wall: silence that peak only
    return suppress(regularize(P, aligned_with_axes(suppress(walls, p), p), p), p) if clean else walls


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


def _geom(w):
    """(unit direction u, unit normal n, offset of the middle along n, start and end along u) of a wall trace in plan view."""
    a = np.array([w.x0, w.y0])
    b = np.array([w.x1, w.y1])
    d = b - a
    ln = float(np.hypot(*d))
    u = d / ln if ln > 0 else np.array([1.0, 0.0])
    n = np.array([-u[1], u[0]])
    mid = 0.5 * (a + b)
    return u, n, float(mid @ n), float(a @ u), float(b @ u), mid


def _dist_to_wall(pt, w):
    """Plan-view distance from a point to the trace of a wall (segment)."""
    a = np.array([w.x0, w.y0])
    d = np.array([w.x1, w.y1]) - a
    t = float(np.clip((pt - a) @ d / max(float(d @ d), 1e-12), 0.0, 1.0))
    return float(np.hypot(*(pt - (a + t * d))))


def aligned_with_axes(walls, p):
    """Gate on direction. The walls of a building run along a few directions and their perpendiculars; a slanted cut through a thick
    façade, or through a bush, does not. A wall is an *anchor* when no wall within `anchor_dist` metres holds more than five times its
    points (nothing dwarfs it). Anchors are kept. Any other wall is kept only when an anchor within `partner_dist` metres is parallel or
    perpendicular to it (within `axis_tol` degrees), or when its contrast reaches `contrast_free`. Several buildings with different
    directions keep their own walls, the debris around the main façade goes."""
    mids = [np.array([(w.x0 + w.x1) / 2, (w.y0 + w.y1) / 2]) for w in walls]
    anchor = []
    for i, w in enumerate(walls):
        anchor.append(not any(k is not w and k.n > 5 * w.n and _dist_to_wall(mids[i], k) <= p.anchor_dist for k in walls))
    out = []
    for i, w in enumerate(walls):
        if anchor[i] or w.contrast >= p.contrast_free:
            out.append(w)
            continue
        a = w.azimuth % 90.0
        for j, k in enumerate(walls):
            if not anchor[j] or k is w:
                continue
            b = k.azimuth % 90.0
            if min(abs(a - b), 90.0 - abs(a - b)) <= p.axis_tol and _dist_to_wall(mids[i], k) <= p.partner_dist:
                out.append(w)
                break
    return out


def suppress(walls, p):
    """Keep one wall per physical wall. Walls are taken in decreasing order of points; a later wall is dropped when it is

    * a duplicate: parallel (4 degrees), its middle less than `dup_offset` from the kept wall's plane, and at least half of it
      overlapping the kept wall along its length;
    * relief: parallel, between `dup_offset` and `relief_offset` from the plane of a longer kept wall, lying along that wall, and
      either shorter than `relief_len` or holding under a third of its points (balconies, pilasters, bays, the back of a loggia).
      Longer, comparably dense parallel pieces at another depth are setbacks and are kept.
    """
    kept = []
    for w in sorted(walls, key=lambda x: -x.n):
        ug, ng, og, s0, s1, mid = _geom(w)
        drop = False
        for k in kept:
            uk, nk, ok, k0, k1, _m = _geom(k)
            if abs(ug @ nk) > math.sin(math.radians(4.0 if w.length >= p.relief_len else 8.0)):     # a short piece has a poor direction
                continue
            off = abs(float(mid @ nk) - float(np.array([k.x0, k.y0]) @ nk))
            ta, tb = sorted((float(np.array([w.x0, w.y0]) @ uk), float(np.array([w.x1, w.y1]) @ uk)))
            over = min(tb, max(k0, k1)) - max(ta, min(k0, k1))
            frac = over / max(tb - ta, 1e-6)
            if off < p.dup_offset and frac >= 0.5:
                drop = True
            elif off < p.relief_offset and frac >= 0.5 and (w.n < 0.3 * k.n or (w.length < p.relief_len and k.length > w.length)):
                drop = True
            if drop:
                break
        if not drop:
            kept.append(w)
    return kept


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


def regularize(P, walls, p):
    """Make the façades of a building parallel and perpendicular when the data allow it. The reference axis is the n-weighted mean
    direction (modulo 90 degrees) of the main walls (at least a fifth of the points of the strongest, within 5 degrees of it). A wall
    within `snap_tol` degrees of that axis or of its perpendicular is turned to it exactly (the offset becomes the densest plane of its
    points) when at least 85 % of the inliers it had stay inliers; otherwise it keeps its own fitted direction (a really skewed wing).
    A short or thick piece has a poor least-squares direction, the shared one is far better, so this makes the planes (and the orthophotos
    built on them) orthogonal to the centimetre where the building is."""
    if len(walls) < 2:
        return walls
    top = max(w.n for w in walls)
    main = [w for w in walls if w.n >= 0.2 * top]
    lead = max(main, key=lambda w: w.n).azimuth % 90.0
    sel = [w for w in main if min(abs(w.azimuth % 90.0 - lead), 90.0 - abs(w.azimuth % 90.0 - lead)) <= 5.0]
    z = sum(w.n * np.exp(4j * np.radians(w.azimuth % 90.0)) for w in sel)
    axis = (math.degrees(np.angle(z)) / 4.0) % 90.0
    for w in walls:
        phi = w.azimuth
        diff = ((phi - axis + 45.0) % 90.0) - 45.0
        if abs(diff) > p.snap_tol or abs(diff) < 0.05 or w.idx is None:
            continue
        X = P[w.idx, :2]
        u_old = np.array([w.x1 - w.x0, w.y1 - w.y0])
        u_old /= np.hypot(*u_old)
        ang = math.radians(phi - diff)
        u = np.array([math.cos(ang), math.sin(ang)])
        if u @ u_old < 0:
            u = -u
        n2 = np.array([u[1], -u[0]])
        n_old = np.array([w.nx, w.ny])
        old_in = float((np.abs(X @ n_old - w.d) < 1.5 * p.tol).mean())
        r = X @ n2
        d0 = float(np.median(r))
        d = d0 + _snap(r, d0, replace(p, relief_offset=0.4)) - d0
        inl = np.abs(r - d) < 1.5 * p.tol
        if float(inl.mean()) < 0.85 * old_in:
            continue
        band = np.abs(r - d) < p.tol
        if band.sum() < p.min_pts:
            continue
        t = X[band] @ u
        a0, a1 = n2 * d + u * float(t.min()), n2 * d + u * float(t.max())
        w.x0, w.y0, w.x1, w.y1 = float(a0[0]), float(a0[1]), float(a1[0]), float(a1[1])
        w.nx, w.ny, w.d = float(n2[0]), float(n2[1]), float(d)
        w.rms = float(np.sqrt(np.mean((r[band] - d) ** 2)))
    return walls


def zone_points(P, walls, width):
    """Indices of the points within `width` metres (plan view) of the trace of any of the walls, ends extended by 1 m."""
    mask = np.zeros(len(P), bool)
    for w in walls:
        nrm = np.array([w.nx, w.ny])
        uu = np.array([-w.ny, w.nx])
        a = np.array([w.x0, w.y0])
        ta, tb = sorted((float(a @ uu), float(np.array([w.x1, w.y1]) @ uu)))
        x0, x1 = sorted((w.x0, w.x1))
        y0, y1 = sorted((w.y0, w.y1))
        box = np.flatnonzero((P[:, 0] > x0 - width - 1.0) & (P[:, 0] < x1 + width + 1.0) & (P[:, 1] > y0 - width - 1.0) & (P[:, 1] < y1 + width + 1.0))
        xy = P[box, :2]
        along = xy @ uu
        ok = (np.abs(xy @ nrm - float(a @ nrm)) < width) & (along > ta - 1.0) & (along < tb + 1.0)
        mask[box[ok]] = True
    return np.flatnonzero(mask)


def two_pass(P, p=None):
    """Recommended. Pass 1 looks for the dense walls with a fine and demanding test (0.5 m columns, 8 height bins: the vertical-occupancy
    test): the main façades of real tie points, thick with balconies and noise, clean of the roofs and the clutter around them. Pass 2 runs
    the tolerant test (1 m cells with a vertical extent of at least `hmin`) on the points that are NOT within `shadow` of a wall of pass 1,
    to catch sparse walls (a small house, a back wall seen from afar) without the debris that thick façades throw up. One suppression and
    direction gate at the end."""
    p = p or Params()
    P = np.asarray(P, dtype=np.float64)
    first = grid_hough(P, replace(p, occ_cell=0.5, occ_min=8), clean=False)
    left = zone_points(P, first, p.shadow + 0.5)
    second = grid_hough(P, replace(p, occ_min=0), exclude=left, clean=False)
    return suppress(regularize(P, aligned_with_axes(suppress(first + second, p), p), p), p)


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


DETECTORS = {"auto": two_pass, "grid_hough": grid_hough, "ransac_line": ransac_line, "ransac_plane": ransac_plane, "combined": combined}


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


def detect(P, method="auto", params=None, origin=True, max_points=600000):
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


def outside_normal(P, w, cameras=None):
    """Unit plan-view normal (nx, ny) of wall `w` pointing to the side the viewer (the cameras) stands on.

    The data decide first: the inside of a building is never seen, so the side that holds points near the foot of the wall (ground,
    cars, trunks; roofs and the corner walls are excluded) is the outside. When that is not decisive the cameras vote (mean side of the cameras: weak
    when they surround the building, so it only breaks ties), and last the direction away from the middle of the cloud."""
    n = np.array([w.nx, w.ny])
    p0 = np.array([w.x0, w.y0])
    u = np.array([-n[1], n[0]])
    rel = P[:, :2] - p0
    s = rel @ u
    t = rel @ n
    s_len = float(np.array([w.x1 - w.x0, w.y1 - w.y0]) @ u)
    lo, hi = min(0.0, s_len), max(0.0, s_len)
    pad = min(1.5, 0.2 * (hi - lo))                       # keep away from the corners: the neighbouring walls start there
    m = (s >= lo + pad) & (s <= hi - pad) & (np.abs(t) > 0.6) & (np.abs(t) < 8.0) & (P[:, 2] < w.z0 + 2.5)
    pos, neg = int((m & (t > 0)).sum()), int((m & (t < 0)).sum())
    if max(pos, neg) >= 20 and max(pos, neg) >= 1.5 * min(pos, neg):
        return tuple(n if pos > neg else -n)
    if cameras is not None and len(cameras):
        C = np.asarray(cameras, dtype=np.float64)
        side = float(((C[:, :2] - p0) @ n).mean())
        if abs(side) > 1e-6:
            return tuple(n if side > 0 else -n)
    c = P[:, :2].mean(axis=0)
    return tuple(n if (p0 - c) @ n > 0 else -n)


def walls_to_facades(walls, P, cameras=None):
    """Convert detector walls to `model.Facade` (named A, B, C ... by decreasing area, bottom-left origin, normal to the cameras)."""
    from .model import Facade, colour_for, letter_name
    walls = sorted(walls, key=lambda w: -(w.length * w.height))
    out = []
    for i, w in enumerate(walls):
        nx, ny = outside_normal(P, w, cameras)
        ux, uy = -ny, nx                               # n = (u_y, -u_x): u is n turned by +90 degrees
        a = np.array([w.x0, w.y0])
        b = np.array([w.x1, w.y1])
        start = a if (a @ np.array([ux, uy])) <= (b @ np.array([ux, uy])) else b
        area = max(w.length * w.height, 1e-6)
        score = (0.30 * min(1.0, area / 150.0) + 0.25 * min(1.0, w.n / area / 3.0) + 0.25 * min(1.0, w.contrast / 5.0)
                 + 0.20 * (1.0 - min(1.0, w.rms / 0.12)))
        out.append(Facade.from_frame(letter_name(i), i, (start[0], start[1], w.z0), (ux, uy), w.length, w.height, points=w.n,
                                     score=float(score), n=(nx, ny), tilt=w.tilt, rms=w.rms))
    return out


def detect_in_polygon(points, polygon, progress=None, cancelled=None, cameras=None, params=None, method="auto"):
    """Façades inside a search polygon: the entry the backends call. `points` (N, 3) ENU metres, `polygon` [(x, y)] in the same frame.
    Returns list[Facade]. Raises backend.Cancelled when `cancelled()` becomes true between stages."""
    from .backend import Progress
    from .geometry import filter_points
    from .methods import stage_text
    prog = Progress.of(progress, cancelled)
    prog(0.02, stage_text("select"))
    Q, _keep = filter_points(points, polygon)
    prog(0.15, stage_text("search"))
    walls = detect(Q, method, params)
    prog(0.9, stage_text("name"))
    facades = walls_to_facades(walls, Q, cameras)
    prog(1.0, stage_text("done", n=len(facades)))
    return facades


def main(argv=None):
    """Command line: `python -m aerotools.tools.aerofacade.detect tie_enu.npy [--method auto|grid_hough|combined] [--out walls.json]`.

    Reads an (N, 3) array (.npy, or an .npz with an `enu` or `xyz` array), prints the walls and writes them as JSON (plan-view end points,
    heights, direction, tilt, confidence) in the same frame as the input."""
    import argparse
    import json
    parser = argparse.ArgumentParser(description="Find vertical walls in a sparse point cloud.")
    parser.add_argument("path")
    parser.add_argument("--method", default="auto", choices=sorted(DETECTORS))
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
