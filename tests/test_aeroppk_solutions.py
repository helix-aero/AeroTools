"""Aero-PPK, the typical precision, the precision bins, the stored solutions and their comparison. Synthetic flights, no solver, no Qt."""
import datetime
import types

import numpy as np
import pytest

from aerotools import i18n
from aerotools.tools.aeroppk import pipeline, solutions, summary

from aeroppk_synth import planned_result

N = 60


def combined(plan, **kwargs):
    result = planned_result(plan, n=N, km=[3.0] * len(plan), **kwargs)
    pipeline.recombine(result)
    return result


def state_of(*results, **fields):
    return types.SimpleNamespace(results=list(results), mode=fields.get("mode", "single"))


# -- typical precision ------------------------------------------------------------------------------------------------

def test_typical_precision_is_the_median_not_the_mean():
    sd = np.full(N, 0.01)
    sd[:5] = 0.5                                                    # a few very bad photos
    result = combined([{"sd": sd}])
    typ = summary.typical([result])
    assert typ["n"] == N and typ["h"] < 0.05 and typ["v"] < 0.05
    assert summary.figures([result])["h_mean"] > typ["h"]            # the mean is pulled up, the median is not


def test_typical_precision_ignores_photos_without_a_position():
    result = combined([{"sd": 0.02, "q": [0] * 10 + [1] * (N - 10)}])
    typ = summary.typical([result])
    assert typ["n"] == N - 10
    none = combined([{"q": 0}])
    assert summary.typical([none]) is None
    assert summary.typical([]) is None


def test_typical_precision_over_two_flights():
    a, b = combined([{"sd": 0.01}]), combined([{"sd": 0.01}])
    assert summary.typical([a, b])["n"] == 2 * N


# -- levels and bins --------------------------------------------------------------------------------------------------

def test_level_counts_add_up_and_follow_the_levels():
    result = combined([{"q": [0] * 6 + [1] * (N - 6)}])
    counts = summary.level_counts([result])
    assert sum(counts) == N and counts[summary.LEVEL_NONE] == 6
    assert counts == [summary.levels([result]).count(k) for k in range(4)]


def test_precision_bins_sum_to_the_photos_and_keep_the_edges_inclusive():
    result = combined([{"sd": 0.02, "q": [0] * 4 + [1] * (N - 4)}])
    bins = summary.precision_bins([result])
    assert sum(bins) == N and bins[3] == 4
    h = float(np.median(np.hypot(result.combined.sdn, result.combined.sde)[result.combined.q > 0]))
    assert bins[0] == N - 4 if h <= 0.03 else bins[1] == N - 4
    wide = combined([{"sd": 0.2}])
    assert summary.precision_bins([wide]) == [0, 0, N, 0]


# -- stored solutions -------------------------------------------------------------------------------------------------

def test_snapshot_has_a_name_the_figures_and_one_entry_per_photo():
    result = combined([{"sd": 0.01}])
    sol = solutions.snapshot(state_of(result), now=datetime.datetime(2026, 11, 21, 14, 2))
    assert sol.name == i18n.t("aeroppk.sol.name", bases="B1", mode=i18n.t("aeroppk.sol.mode_single"), time="14:02")
    assert sol.name.startswith("B1, ") and sol.name.endswith("14:02")
    assert sol.bases == ["B1"] and len(sol.keys) == N and len(sol.lat) == N and len(sol.level) == N
    assert sol.figures["photos"] == N and sum(sol.figures["levels"]) == N and sol.figures["h_median"] > 0
    assert solutions.snapshot(state_of(result), "mine").name == "mine"


def test_snapshot_does_not_change_when_the_results_are_recombined_later():
    result = combined([{"sd": 0.01}, {"north": 0.2, "sd": 0.01}])
    sol = solutions.snapshot(state_of(result))
    before = sol.lat.copy()
    result.combined.lat[:] = 0.0
    assert np.array_equal(sol.lat, before)


def test_bases_taken_out_are_not_named():
    result = combined([{"sd": 0.01}, {"sd": 0.01}])
    result.excluded = ["B2"]
    assert solutions.base_codes([result]) == ["B1"]


def test_the_same_results_are_the_same_solution_and_another_base_is_not():
    a = combined([{"sd": 0.01}])
    one = solutions.snapshot(state_of(a))
    assert solutions.same(one, solutions.snapshot(state_of(a)))
    shifted = combined([{"north": 0.04, "sd": 0.01}])
    assert not solutions.same(one, solutions.snapshot(state_of(shifted)))


# -- comparison -------------------------------------------------------------------------------------------------------

def test_compare_measures_the_horizontal_and_vertical_difference():
    one = solutions.snapshot(state_of(combined([{"sd": 0.01}])))
    other = solutions.snapshot(state_of(combined([{"north": 0.03, "east": 0.04, "up": 0.02, "sd": 0.01}])))
    diff = solutions.compare(one, other)
    assert diff["n"] == N and diff["common"] == N
    assert diff["h_median"] == pytest.approx(0.05, abs=0.002) and diff["h_max"] == pytest.approx(0.05, abs=0.002)
    assert diff["v_median"] == pytest.approx(0.02, abs=0.002)
    assert diff["over"] == 0                                         # 5 cm is not more than 5 cm
    assert solutions.agree_within(diff) == pytest.approx(0.05, abs=0.002)


def test_compare_counts_the_photos_that_differ_by_more_than_5_cm():
    north = np.zeros(N)
    north[:7] = 0.2
    one = solutions.snapshot(state_of(combined([{"sd": 0.01}])))
    other = solutions.snapshot(state_of(combined([{"north": north, "sd": 0.01}])))
    diff = solutions.compare(one, other)
    assert diff["over"] == 7 and diff["h_max"] == pytest.approx(0.2, abs=0.005) and diff["h_median"] < 0.01


def test_compare_is_symmetric_in_size_and_zero_for_the_same_solution():
    one = solutions.snapshot(state_of(combined([{"sd": 0.01}])))
    diff = solutions.compare(one, one)
    assert diff["n"] == N and diff["h_max"] == pytest.approx(0.0, abs=1e-6) and diff["over"] == 0


def test_compare_uses_only_the_photos_both_have_and_both_solved():
    a = combined([{"sd": 0.01}])
    b = combined([{"sd": 0.01, "q": [0] * 10 + [1] * (N - 10)}])
    diff = solutions.compare(solutions.snapshot(state_of(a)), solutions.snapshot(state_of(b)))
    assert diff["common"] == N and diff["n"] == N - 10
    for event in a.events:                                           # other photos: nothing in common
        event.photo = "other/" + str(event.index)
    c = solutions.snapshot(state_of(a))
    for event in b.events:
        event.photo = "mine/" + str(event.index)
    far = solutions.compare(c, solutions.snapshot(state_of(b)))
    assert far["common"] == 0 and far["n"] == 0 and far["h_median"] is None
    assert solutions.agree_within(far) is None


def test_the_sentence_of_the_comparison_names_the_distance():
    one = solutions.snapshot(state_of(combined([{"sd": 0.01}])))
    other = solutions.snapshot(state_of(combined([{"north": 0.02, "sd": 0.01}])))
    cm = i18n.fmt_number(100.0 * solutions.agree_within(solutions.compare(one, other)), 1)
    assert i18n.t("aeroppk.sol.agree", x=cm, pct="95") == "These two solutions agree within %s cm for 95 %% of the photos." % cm
