"""Aero-PPK, the figures of the results screen: what is green and what stays grey, the parts of the solution precision, the comparison with
the drone's own position as information. Synthetic flights, no solver, no Qt."""
import numpy as np
import pytest

from aerotools import i18n
from aerotools.tools.aeroppk import combine, pipeline, summary

from aeroppk_synth import planned_result

N = 60


def test_a_good_value_is_green_and_every_other_one_is_grey():
    assert summary.tone("fix", 100.0) == "good" and summary.tone("fix", 95.0) == "good"
    assert summary.tone("fix", 94.9) == "grey"
    assert summary.tone("float", 0.0) == "good" and summary.tone("float", 5.0) == "good"
    assert summary.tone("float", 5.1) == "grey" and summary.tone("float", 8.3) == "grey"
    assert summary.tone("sd", 0.014) == "good" and summary.tone("sd", 0.10) == "good" and summary.tone("sd", 0.11) == "grey"
    assert summary.tone("onboard", 0.1) == "good" and summary.tone("onboard", 0.25) == "grey"
    assert summary.tone("sd", None) == "" and summary.tone("unknown", 1.0) == ""


def test_the_limits_are_constants_of_one_place_and_the_two_shares_agree():
    assert summary.GOOD_ERROR_M == pytest.approx(0.10)
    assert summary.GOOD_FIX_PCT + summary.MAX_FLOAT_PCT == pytest.approx(100.0)


def test_the_parts_of_the_precision_come_from_the_policy():
    policy = combine.Policy()
    terms = summary.precision_terms()
    assert terms == {"fh": pytest.approx(100.0 * policy.fix_floor_h), "fv": pytest.approx(100.0 * policy.fix_floor_v),
                     "ppm": pytest.approx(1e6 * policy.ppm_h)}
    changed = summary.precision_terms(combine.Policy(fix_floor_h=0.02, fix_floor_v=0.03, ppm_h=2e-6))
    assert changed == {"fh": pytest.approx(2.0), "fv": pytest.approx(3.0), "ppm": pytest.approx(2.0)}


def test_the_text_of_the_precision_names_its_three_parts_with_those_numbers():
    text = summary.precision_tip()
    assert "forward and backward" in text
    assert "0.8 cm horizontal, 1.6 cm vertical" in text
    assert "1.2 mm per km" in text
    assert text.rstrip().endswith("not a check against ground truth.")
    assert "uncertainty" not in text.lower()


def test_the_difference_with_the_drone_is_information_with_an_offset_and_a_spread():
    result = planned_result([{"north": 0.0, "sd": 0.01}], n=N, km=[3.0])
    pipeline.recombine(result)
    figures = summary.onboard_figures([result])
    assert figures["n"] == N
    assert figures["offset_h"] == pytest.approx(0.0, abs=0.01) and figures["spread_h"] == pytest.approx(0.0, abs=0.01)
    shifted = planned_result([{"north": 0.03, "up": -0.05, "sd": 0.01}], n=N, km=[3.0])
    pipeline.recombine(shifted)
    figures = summary.onboard_figures([shifted])
    assert figures["offset_h"] == pytest.approx(0.03, abs=0.005)
    assert abs(figures["offset_u"]) == pytest.approx(0.05, abs=0.005)


def test_without_the_drones_own_position_there_is_nothing_to_compare():
    result = planned_result([{"sd": 0.01}], n=N, km=[3.0])
    for event in result.events:
        event.onboard, event.onboard_q = None, 0
    result.check = pipeline.compare_onboard(result.events, {"lat": np.zeros(N), "lon": np.zeros(N), "h": np.zeros(N)})
    assert summary.onboard_figures([result]) is None


def test_the_figures_of_a_flight_with_some_float_photos():
    result = planned_result([{"sd": 0.02, "q": [2] * 5 + [1] * (N - 5)}], n=N, km=[3.0])
    pipeline.recombine(result)
    totals = summary.figures([result])
    assert totals["photos"] == N and totals["float"] == 5 and totals["fix"] == N - 5
    assert summary.tone("fix", totals["fix_pct"]) == "grey"                    # 91.7 % fixed is below the limit
