import math

import pytest

from aerotools.tools.fastgcp import qc


def _row(label, proj, x=0.005, y=0.005, z=0.005):
    return {"label": label, "proj": proj, "x": x, "y": y, "z": z}


def test_progress_counts_markers_with_enough_photos():
    rows = [_row("A", 0), _row("B", 2), _row("C", 3), _row("D", 40)]
    assert qc.progress(rows) == (2, 4)
    assert qc.progress([]) == (0, 0)


def test_total_error_needs_the_three_components():
    assert qc.total_error(_row("A", 5, 0.03, 0.04, 0.0)) == pytest.approx(0.05)
    assert qc.total_error(_row("A", 5, None, 0.04, 0.0)) is None
    assert qc.total_error({"label": "A"}) is None


def test_limit_has_a_floor_and_follows_a_noisy_chunk():
    quiet = [_row(str(i), 10) for i in range(5)]
    assert qc.residual_limit(quiet) == qc.RESIDUAL_FLOOR_M
    noisy = [_row(str(i), 10, 0.1, 0.1, 0.1) for i in range(5)]
    assert qc.residual_limit(noisy) == pytest.approx(
        qc.RESIDUAL_FACTOR * math.sqrt(3 * 0.1 ** 2))
    assert qc.residual_limit([]) == qc.RESIDUAL_FLOOR_M


def test_watchlist_flags_residuals_and_few_photos_in_order():
    rows = [
        _row("OK", 40),
        _row("BIG", 30, 0.04, 0.04, 0.04),
        _row("BIGGER", 30, 0.2, 0.0, 0.0),
        _row("ONE", 1, None, None, None),
        _row("ZERO", 0, None, None, None),
        _row("TWO", 2),
    ]
    result = qc.watchlist(rows)
    assert [i["label"] for i in result["residual"]] == ["BIGGER", "BIG"]
    assert [i["label"] for i in result["few"]] == ["ZERO", "ONE", "TWO"]
    assert result["limit"] == qc.RESIDUAL_FLOOR_M


def test_a_marker_can_be_in_both_lists():
    rows = [_row("OK", 40), _row("BOTH", 2, 0.5, 0.5, 0.5)]
    result = qc.watchlist(rows)
    assert [i["label"] for i in result["residual"]] == ["BOTH"]
    assert [i["label"] for i in result["few"]] == ["BOTH"]


def test_nothing_to_watch():
    result = qc.watchlist([_row("A", 10), _row("B", 12)])
    assert result["residual"] == [] and result["few"] == []
