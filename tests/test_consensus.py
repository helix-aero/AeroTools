import math
import random

import pytest

from aerotools.tools.fastgcp import consensus

TARGET = (0.0, 0.0, 0.0)


def ray_to(origin, target):
    return origin, tuple(t - o for t, o in zip(target, origin))


def ring(count, radius=30.0, height=-40.0):
    return [(radius * math.cos(2 * math.pi * k / count),
             radius * math.sin(2 * math.pi * k / count), height) for k in range(count)]


def test_rays_to_one_point_are_all_inliers_and_find_the_point():
    rays = [ray_to(origin, TARGET) for origin in ring(8)]
    flags, point = consensus.consensus(rays, 0.1)
    assert flags == [True] * 8
    assert point == pytest.approx(TARGET, abs=1e-6)


def test_off_target_detections_are_flagged():
    rays = [ray_to(origin, TARGET) for origin in ring(8)]
    rays[2] = ray_to(ring(8)[2], (6.0, 0.0, 0.0))     # a roof next to it
    rays[5] = ray_to(ring(8)[5], (0.0, -7.0, 3.0))
    flags, point = consensus.consensus(rays, 0.1)
    assert [i for i, ok in enumerate(flags) if not ok] == [2, 5]
    assert point == pytest.approx(TARGET, abs=1e-3)


def test_threshold_decides_what_is_close_enough():
    rays = [ray_to(origin, TARGET) for origin in ring(6)]
    rays[0] = ray_to(ring(6)[0], (0.3, 0.0, 0.0))
    assert consensus.consensus(rays, 0.1)[0][0] is False
    assert consensus.consensus(rays, 1.0)[0][0] is True


def test_fewer_than_three_rays_cannot_be_judged():
    rays = [ray_to(origin, TARGET) for origin in ring(2)]
    assert consensus.consensus(rays, 0.1) == (None, None)
    assert consensus.consensus([None, None, rays[0]], 0.1) == (None, None)


def test_parallel_rays_cannot_be_judged():
    rays = [((float(k), 0.0, -40.0), (0.0, 0.0, 1.0)) for k in range(5)]
    assert consensus.consensus(rays, 0.1) == ([False] * 5, None)            # enough rays, no point: nothing confirmed


def test_no_point_with_enough_rays_discards_every_detection_of_the_marker():
    from aerotools.tools.fastgcp import aigcp
    from aerotools import msapi
    group = [({"camera": k}, {"x": 1.0, "y": 2.0}) for k in range(5)]
    original = msapi.pixel_ray
    msapi.pixel_ray = lambda camera, x, y: ((float(camera), 0.0, -40.0), (0.0, 0.0, 1.0))
    scale = msapi.chunk_scale
    msapi.chunk_scale = lambda chk: 1.0
    try:
        kept, dropped = aigcp._consistent(None, group)
    finally:
        msapi.pixel_ray, msapi.chunk_scale = original, scale
    assert kept == [] and len(dropped) == 5


def test_unrelated_rays_share_no_point():
    rng = random.Random(3)
    rays = []
    for origin in ring(6):
        target = (rng.uniform(-8, 8), rng.uniform(-8, 8), rng.uniform(-3, 3))
        rays.append(ray_to(origin, target))
    flags, _point = consensus.consensus(rays, 0.05)
    assert sum(flags) <= 2


def test_a_missing_ray_is_never_an_inlier():
    rays = [ray_to(origin, TARGET) for origin in ring(5)]
    rays.insert(2, None)
    flags, _point = consensus.consensus(rays, 0.1)
    assert flags[2] is False and sum(flags) == 5


def test_a_point_behind_the_camera_is_not_an_inlier():
    flags, point = consensus.consensus([ray_to(origin, TARGET) for origin in ring(5)], 0.1)
    behind = ((0.0, 0.0, 10.0), (0.0, 0.0, 1.0))       # looks towards +Z, the target is behind
    distance, depth = consensus.distance_to_ray(point, behind)
    assert distance == pytest.approx(0.0, abs=1e-6) and depth < 0


def test_triangulate_two_skew_rays_gives_the_midpoint():
    a = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0))        # the line y = 0, z = 0
    b = ((1.0, 2.0, -1.0), (0.0, 0.0, 1.0))       # the line x = 1, y = 2
    assert consensus.triangulate([a, b]) == pytest.approx((1.0, 1.0, 0.0), abs=1e-9)
