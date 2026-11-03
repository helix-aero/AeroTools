import json
import os

import pytest

from aerotools import config
from aerotools.tools.fastgcp import session as session_mod
from aerotools.tools.fastgcp import snap

YELLOW = (230, 210, 40)
BLACK = (28, 28, 28)


def _checker(size=80, cx=40, cy=40, a=YELLOW, b=BLACK):
    rows = []
    for y in range(size):
        row = []
        for x in range(size):
            row.append(a if (x < cx) == (y < cy) else b)
        rows.append(row)
    return rows


def test_snap_pixel_perfect_on_yellow_black():
    hit = snap.refine_in_rows(_checker(), 0, 0, 38.0, 42.0)
    assert hit is not None
    assert abs(hit[0] - 40) <= 0.75
    assert abs(hit[1] - 40) <= 0.75


def test_snap_stays_on_center():
    hit = snap.refine_in_rows(_checker(), 0, 0, 40.0, 40.0)
    assert hit is not None
    assert abs(hit[0] - 40) <= 0.5 and abs(hit[1] - 40) <= 0.5
    assert hit[2] <= 0.5


def test_snap_works_with_white_black_and_origin_offset():
    rows = _checker(a=(240, 240, 240), b=(20, 20, 20))
    # La zone commence en (1000, 500) dans l'image : le croisement est en (1040, 540).
    hit = snap.refine_in_rows(rows, 1000, 500, 1042.0, 538.0)
    assert hit is not None
    assert abs(hit[0] - 1040) <= 0.75 and abs(hit[1] - 540) <= 0.75


def test_snap_refuses_when_crossing_is_too_far():
    # Croisement a 10 px du clic : on ne le suit pas.
    assert snap.refine_in_rows(_checker(), 0, 0, 50.0, 40.0) is None


def test_snap_ignores_a_flat_zone():
    flat = [[(120, 120, 120)] * 60 for _ in range(60)]
    assert snap.refine_in_rows(flat, 0, 0, 30.0, 30.0) is None


def test_snap_ignores_a_blurry_low_contrast_zone():
    rows = _checker(a=(130, 130, 130), b=(126, 126, 126))
    assert snap.refine_in_rows(rows, 0, 0, 40.0, 40.0) is None


def test_snap_near_the_zone_edge_does_not_crash():
    assert snap.refine_in_rows(_checker(size=20, cx=10, cy=10), 0, 0, 1.0, 1.0) is None


def test_max_shift_argument_tightens_the_limit():
    assert snap.refine_in_rows(_checker(), 0, 0, 38.0, 42.0, max_shift=1.0) is None


def test_snap_click_disabled_or_missing_image(ms):
    camera = ms.Camera(1, ms.Vector(0, 0, 0))
    assert snap.snap_click(camera, 10, 10, max_shift=0)[0] is None
    result = snap.snap_click(camera, 10, 10)
    assert result[0] is None and "image" in result[1]


def _write_stored(data):
    os.makedirs(config.settings_dir(), exist_ok=True)
    with open(config.settings_path(), "w", encoding="utf-8") as handle:
        json.dump({"fastgcp": data}, handle)


@pytest.mark.parametrize("stored, expected", [
    ({}, 0.0),
    ({"recalage_max_px": 0}, 0.0),
    ({"recalage_max_px": 3.5}, 3.5),
    ({"recalage_max_px": 6}, 6.0),
    ({"recalage_max_px": True}, 0.0),
    ({"recalage_max_px": "beaucoup"}, 0.0),
    ({"recalage_max_px": -2}, 0.0),
    ({"recalage_max_px": 500}, 0.0),
])
def test_snap_setting(local_appdata, stored, expected):
    session_mod.reset()
    _write_stored(stored)
    assert session_mod.Session().snap_px == expected
    session_mod.reset()
