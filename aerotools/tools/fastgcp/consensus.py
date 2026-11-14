"""Geometric consistency of the detections of one marker (no Metashape).

Each detection on a photo is a ray: the origin is the camera centre, the
direction points towards the detected pixel. The true detections of one target
intersect at a single 3D point; a false one (roof, tree, neighbouring stain)
falls elsewhere, differently on each photo, and intersects nothing.

`consensus` looks for the 3D point that gathers the most rays (RANSAC over pairs
of rays, then least-squares refinement) and says which detections are inliers.
No prior knowledge of the marker is needed: it also works for a marker with no
pin at all.

Rays are given in the chunk's internal coordinates: the threshold is expressed
in the same unit (the caller converts metres using the scale).
"""

import itertools
import math
import random

MIN_RAYS = 3
MAX_PAIRS = 400
# Two nearly parallel rays do not define a point well.
MIN_PAIR_ANGLE_DEG = 0.5


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _unit(v):
    n = math.sqrt(_dot(v, v))
    if not n > 0:
        return None
    return (v[0] / n, v[1] / n, v[2] / n)


def _clean(rays):
    """Rays with a normalised direction; None for an unusable ray."""
    out = []
    for ray in rays:
        try:
            origin = (float(ray[0][0]), float(ray[0][1]), float(ray[0][2]))
            direction = _unit((float(ray[1][0]), float(ray[1][1]), float(ray[1][2])))
        except (TypeError, ValueError, IndexError):
            out.append(None)
            continue
        out.append(None if direction is None else (origin, direction))
    return out


def distance_to_ray(point, ray):
    """(distance from the point to the ray, depth of the point along the ray)."""
    delta = _sub(point, ray[0])
    depth = _dot(delta, ray[1])
    perpendicular = _sub(delta, (ray[1][0] * depth, ray[1][1] * depth, ray[1][2] * depth))
    return math.sqrt(_dot(perpendicular, perpendicular)), depth


def triangulate(rays):
    """Point that minimises the sum of squared distances to the rays, or None."""
    a = [[0.0] * 3 for _ in range(3)]
    b = [0.0] * 3
    for origin, u in rays:
        for i in range(3):
            for j in range(3):
                m = (1.0 if i == j else 0.0) - u[i] * u[j]
                a[i][j] += m
                b[i] += m * origin[j]
    return _solve3(a, b)


def _solve3(a, b):
    def det(m):
        return (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
                - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
                + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))

    d = det(a)
    scale = max(abs(v) for row in a for v in row) ** 3
    if not scale > 0 or abs(d) < 1e-9 * scale:
        return None
    out = []
    for k in range(3):
        m = [row[:] for row in a]
        for i in range(3):
            m[i][k] = b[i]
        out.append(det(m) / d)
    return tuple(out)


def _inliers(point, rays, threshold):
    flags, total = [], 0.0
    for ray in rays:
        if ray is None:
            flags.append(False)
            continue
        distance, depth = distance_to_ray(point, ray)
        ok = depth > 0 and distance <= threshold
        flags.append(ok)
        if ok:
            total += distance
    return flags, total


def _pairs(count, rng):
    pairs = list(itertools.combinations(range(count), 2))
    if len(pairs) > MAX_PAIRS:
        pairs = rng.sample(pairs, MAX_PAIRS)
    return pairs


def consensus(rays, threshold, seed=0):
    """The 3D point that gathers the most rays.

    `rays`: list of (origin, direction); None is accepted for a detection without a
    ray. Returns (flags, point): `flags[i]` is true if ray i passes within
    `threshold` of the point. (None, None) means "cannot be judged": fewer than MIN_RAYS usable
    rays. When there are enough rays but no point emerges (all the pairs nearly parallel or
    singular), the answer is ([False, ...], None): nothing gathers, nothing is confirmed.
    """
    clean = _clean(rays)
    usable = [i for i, ray in enumerate(clean) if ray is not None]
    if len(usable) < MIN_RAYS:
        return None, None
    rng = random.Random(seed)
    cos_limit = math.cos(math.radians(MIN_PAIR_ANGLE_DEG))
    best = None
    for i, j in _pairs(len(usable), rng):
        first, second = clean[usable[i]], clean[usable[j]]
        if abs(_dot(first[1], second[1])) > cos_limit:
            continue
        point = triangulate([first, second])
        if point is None:
            continue
        flags, total = _inliers(point, clean, threshold)
        score = (sum(flags), -total)
        if best is None or score > best[0]:
            best = (score, flags, point)
    if best is None:
        return [False] * len(clean), None
    flags, point = best[1], best[2]
    for _ in range(2):
        chosen = [clean[i] for i, ok in enumerate(flags) if ok]
        refined = triangulate(chosen) if len(chosen) >= 2 else None
        if refined is None:
            break
        new_flags, _total = _inliers(refined, clean, threshold)
        if sum(new_flags) < sum(flags):
            break
        flags, point = new_flags, refined
    return flags, point
