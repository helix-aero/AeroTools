"""Choix des marqueurs de controle : donnees simulees dont on connait la bonne reponse."""
import math
import random

import pytest

from aerotools.tools.fastgcp import gcp_select as g


def rot_z(angle):
    c, s = math.cos(angle), math.sin(angle)
    return ((c, -s, 0.0), (s, c, 0.0), (0.0, 0.0, 1.0))


def rot_xyz(ax, ay, az):
    def mul(a, b):
        return tuple(tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3))
    rx = ((1, 0, 0), (0, math.cos(ax), -math.sin(ax)), (0, math.sin(ax), math.cos(ax)))
    ry = ((math.cos(ay), 0, math.sin(ay)), (0, 1, 0), (-math.sin(ay), 0, math.cos(ay)))
    return mul(rz_(az), mul(ry, rx))


def rz_(angle):
    return rot_z(angle)


def make_points(count=14, seed=3, noise=0.01, similarity=True, bias=(0.05, -0.03, 0.08)):
    """Points de reference dans un site de 500 x 300 m ; `est` = ce que Metashape estime avant calage."""
    rng = random.Random(seed)
    refs = [(rng.uniform(0, 500), rng.uniform(0, 300), rng.uniform(0, 30)) for _ in range(count)]
    points = []
    scale, rot, shift = 1.0004, rot_xyz(0.0004, -0.0003, 0.02), (130.0, -40.0, 12.0)
    for index, ref in enumerate(refs):
        if similarity:
            r = g._apply_rotation(rot, ref)
            est = tuple(scale * r[i] + shift[i] for i in range(3))
        else:
            est = tuple(ref[i] - bias[i] for i in range(3))
        est = tuple(est[i] + rng.gauss(0.0, noise) for i in range(3))
        points.append({"label": "P%d" % index, "est": est, "ref": ref, "n": 8})
    return points


def test_similarity_recovers_a_known_transform():
    points = make_points(noise=0.0)
    transform = g.fit_similarity(points)
    errs = g.stats(g.errors(transform, points))
    assert errs["total"] < 1e-6


def test_similarity_with_noise_is_close_to_the_noise_level():
    points = make_points(noise=0.01)
    errs = g.stats(g.errors(g.fit_similarity(points), points))
    assert errs["total"] < 0.02


def test_similarity_needs_three_points_not_on_a_line():
    points = make_points(count=3, noise=0.0)
    assert g.fit_similarity(points[:2]) is None
    line = [{"label": "L%d" % i, "est": (i * 10.0, 0.0, 0.0), "ref": (i * 10.0, 5.0, 1.0), "n": 5} for i in range(4)]
    assert g.fit_similarity(line) is None
    assert g.fit_similarity(points) is not None


def test_shift_is_the_mean_offset():
    points = make_points(count=6, similarity=False, noise=0.0, bias=(0.05, -0.03, 0.08))
    errs = g.stats(g.errors(g.fit_shift(points[:1]), points))
    assert errs["total"] < 1e-9
    assert g.fit_shift([]) is None


def test_recommendation_is_small_and_accurate():
    result = g.analyze(make_points(noise=0.01), model=g.SIMILARITY)
    assert result["reason"] == "" and result["usable"] == 14 and result["suspects"] == []
    ks = [item["k"] for item in result["curve"]]
    assert ks == [3, 4, 5, 6]
    chosen = result["curve"][result["recommended"]]
    assert chosen["k"] >= g.REDUNDANT[g.SIMILARITY]
    assert chosen["rmse"] < 0.03
    assert len(set(chosen["labels"])) == chosen["k"] and chosen["checks"] == 14 - chosen["k"]
    assert 0.0 <= chosen["spread"] <= 1.0


def test_curve_does_not_get_worse_by_much_with_more_points():
    result = g.analyze(make_points(noise=0.01))
    best = min(item["rmse"] for item in result["curve"])
    assert result["curve"][result["recommended"]]["rmse"] <= best * (1 + g.TOLERANCE_REL) + g.TOLERANCE_ABS + 1e-12


def test_a_bad_pick_is_flagged_and_never_chosen():
    points = make_points(noise=0.01)
    bad = points[5]
    bad["est"] = (bad["est"][0] + 0.18, bad["est"][1] - 0.12, bad["est"][2] + 0.10)
    result = g.analyze(points)
    assert [s["label"] for s in result["suspects"]] == [bad["label"]]
    assert result["suspects"][0]["error"] > 0.1
    for item in result["curve"]:
        assert bad["label"] not in item["labels"]


def test_markers_with_few_photos_are_left_out():
    points = make_points(noise=0.01)
    points[0]["n"] = 1
    points[1]["n"] = 0
    result = g.analyze(points)
    assert result["few"] == ["P0", "P1"] and result["usable"] == 12
    for item in result["curve"]:
        assert "P0" not in item["labels"] and "P1" not in item["labels"]


def test_shift_model_needs_few_markers():
    result = g.analyze(make_points(count=10, similarity=False, noise=0.01), model=g.SHIFT)
    chosen = result["curve"][result["recommended"]]
    assert chosen["k"] == 2 and chosen["rmse"] < 0.02
    assert [item["k"] for item in result["curve"]][0] == 1


def test_too_few_markers_gives_a_reason_and_no_curve():
    result = g.analyze(make_points(count=3, noise=0.0))
    assert result["reason"] == "too_few" and result["curve"] == [] and result["recommended"] is None
    assert g.analyze([], model=g.SHIFT)["reason"] == "too_few"


def test_greedy_search_is_used_when_the_budget_is_small():
    points = make_points(count=22, noise=0.01)
    result = g.analyze(points, budget=300)
    assert result["curve"] and result["recommended"] is not None
    assert all(item["rmse"] < 0.05 for item in result["curve"])


def test_current_stats_match_the_raw_differences():
    points = make_points(count=5, noise=0.0, similarity=False, bias=(0.01, 0.02, 0.03))
    control, check = g.current_stats(points, {"P0": True, "P1": True})
    expected = math.sqrt(0.01 ** 2 + 0.02 ** 2 + 0.03 ** 2)
    assert control["n"] == 2 and check["n"] == 3
    assert control["total"] == pytest.approx(expected) and check["total"] == pytest.approx(expected)
    assert g.current_stats(points, {})[0] is None


def test_spread_is_low_for_clustered_points_and_high_for_wide_ones():
    wide = [{"est": (0, 0, 0)}, {"est": (100, 0, 0)}, {"est": (0, 100, 0)}, {"est": (100, 100, 0)}]
    clustered = [{"est": (0, 0, 0)}, {"est": (100, 0, 0)}, {"est": (50, 3, 0)}]
    assert g.spread(wide, wide) == pytest.approx(1.0)
    assert g.spread(clustered, wide) < 0.1
    assert g.spread_label(0.9) == "good" and g.spread_label(0.4) == "mid" and g.spread_label(0.1) == "low"


def test_stats_of_nothing_is_none():
    assert g.stats([]) is None
