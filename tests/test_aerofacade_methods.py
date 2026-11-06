"""Aero-Facade methods.py: ranking, defaults, texts (both languages), time hints, warnings."""

import math

import pytest

np = pytest.importorskip("numpy")

from aerotools import i18n  # noqa: E402
from aerotools.tools.aerofacade import methods as ME  # noqa: E402
from aerotools.tools.aerofacade.model import Availability, Facade, Margins, Method  # noqa: E402

ALL = {"tie_points": (True, ""), "depth_maps": (True, ""), "dense_cloud": (True, "")}
NO_DENSE = {"tie_points": (True, ""), "depth_maps": (True, ""), "dense_cloud": (False, "no dense cloud in this chunk")}
TIE_ONLY = {"tie_points": (True, ""), "depth_maps": (False, "no depth maps in this chunk"), "dense_cloud": (False, "")}
NOTHING = {"tie_points": (False, "no tie points"), "depth_maps": (False, ""), "dense_cloud": (False, "")}


def _facades(n=3, length=20.0, height=12.0):
    out = []
    for i in range(n):
        f = Facade.from_frame("ABCDEFGH"[i], i, (0, 10 * i, 0), (1, 0, 0), length, height)
        out.append(f)
    return out


def test_rank_follows_the_spec_order_and_picks_the_best_available():
    rows = ME.rank_methods(ALL)
    assert [r[0] for r in rows] == [Method.DENSE_CLOUD, Method.DEPTH_MAPS, Method.TIE_POINTS]
    assert [r[3] for r in rows] == [True, False, False]
    rows = ME.rank_methods(NO_DENSE)
    assert [(r[0], r[1], r[3]) for r in rows] == [(Method.DENSE_CLOUD, False, False), (Method.DEPTH_MAPS, True, True),
                                                  (Method.TIE_POINTS, True, False)]
    assert rows[0][2] == "no dense cloud in this chunk"                 # the backend's reason is kept
    assert rows[1][2] == ""
    rows = ME.rank_methods(TIE_ONLY)
    assert [r[3] for r in rows] == [False, False, True]
    assert rows[0][2] == ME.DEFAULT_REASON[Method.DENSE_CLOUD]           # an empty reason gets an English default


def test_choose_default():
    assert ME.choose_default(ALL) is Method.DENSE_CLOUD
    assert ME.choose_default(NO_DENSE) is Method.DEPTH_MAPS
    assert ME.choose_default(TIE_ONLY) is Method.TIE_POINTS
    assert ME.choose_default(NOTHING) is None
    assert ME.choose_default({}) is None
    assert ME.choose_default(Availability(tie_points=(True, ""), depth_maps=(True, ""), dense_cloud=(False, ""))) is Method.DEPTH_MAPS
    assert not any(r[3] for r in ME.rank_methods(NOTHING))
    assert ME.is_available(NO_DENSE, "depth_maps") and not ME.is_available(NO_DENSE, Method.DENSE_CLOUD)


def test_unknown_reason_gets_the_default():
    rows = ME.rank_methods({"tie_points": (True, "")})
    assert rows[1][1] is False and rows[1][2] == ME.DEFAULT_REASON[Method.DEPTH_MAPS]


@pytest.fixture(params=["en", "fr"])
def lang(request):
    old = i18n.lang()
    i18n.set_lang(request.param, persist=False)
    yield request.param
    i18n.set_lang(old, persist=False)


def _all_keys():
    keys = list(ME.METHOD_KEYS.values()) + list(ME.MARGIN_KEYS.values()) + list(ME.DETECT_KEYS.values())
    keys += list(ME.DIRECTION_KEYS.values()) + list(ME.QUALITY_KEYS.values()) + list(ME.STAGE_KEYS.values())
    for m in Method:
        keys += [v for v in ME.method_keys(m).values() if v]
    keys += ["facade.method.time_under", "facade.method.time_about", "facade.method.time_range", "facade.method.time_unknown",
             "facade.method.time_none"]
    return keys


def test_every_key_exists_in_both_catalogs():
    keys = _all_keys()
    assert len(keys) > 50
    for code in ("en", "fr"):
        texts = i18n.texts(code)
        missing = [k for k in keys if k not in texts]
        assert missing == [], (code, missing)


def test_every_facade_engine_key_is_listed_in_the_tables():
    """No orphan text: every facade.method / margin / detect key of the catalog is reachable from methods.py."""
    listed = set(_all_keys())
    ours = [k for k in i18n.texts("en") if k.startswith(("facade.method.", "facade.margin.", "facade.detect."))]
    assert [k for k in ours if k not in listed] == []


def test_texts_have_matching_fields_in_both_languages_and_render(lang):
    for k in _all_keys():
        text = i18n.t(k, n=2, low=1, high=3, w=100, h=50, mp="5.0", mb="9", fmt="PNG", min_cm=5, length="10.0", height="5.0",
                      direction="x", error="e")
        assert text and text != k and "{" not in text, (lang, k)


def test_french_has_accents_and_english_has_none_of_them():
    en, fr = i18n.texts("en"), i18n.texts("fr")
    assert "façade" in en["facade.margin.title"]
    assert "façade" in fr["facade.margin.title"]
    assert "Très" in fr["facade.method.depth_maps.quality"]


def test_describe_renders_rows_in_quality_order(lang):
    rows = ME.describe(NO_DENSE, _facades(), Margins(), translate=None)
    assert [r["key"] for r in rows] == ["dense_cloud", "depth_maps", "tie_points"]
    assert [r["default"] for r in rows] == [False, True, False]
    assert rows[0]["available"] is False and rows[0]["reason"] and rows[0]["detail"] == "no dense cloud in this chunk"
    assert rows[1]["reason"] == "" and rows[1]["time"]
    assert rows[2]["warning"] and rows[0]["warning"] == ""
    assert rows[0]["time"] == ""                              # no time for a method that cannot be used
    assert all(r["title"] and r["explain"] and r["quality"] for r in rows)
    assert ME.describe(NO_DENSE)[1]["time"] == ""             # without façades, no time hint


def test_describe_accepts_a_custom_translator():
    rows = ME.describe(ALL, _facades(), translate=lambda key, **f: "<%s>" % key)
    assert rows[0]["title"] == "<facade.method.dense_cloud.title>"


def test_time_hint_forms():
    fs = _facades(2, 10.0, 5.0)
    h = ME.time_hint("tie_points", fs, Margins())
    assert h.key == "facade.method.time_under" and h.params == {"n": 2} and not h.rough and not h.long
    big = _facades(8, 60.0, 25.0)
    h = ME.time_hint("depth_maps", big, Margins())
    assert h.key == "facade.method.time_range" and h.params["low"] < h.params["high"] and h.params["n"] == 8
    assert h.rough                                               # depth-map numbers are first estimates
    assert h.long and h.high_s > ME.LONG_SECONDS
    mid = ME.time_hint("depth_maps", _facades(1, 20.0, 12.0), Margins())
    assert mid.key in ("facade.method.time_range", "facade.method.time_about")
    none = ME.time_hint("depth_maps", fs, Margins(), existing=("A", "B"))
    assert none.key == "facade.method.time_none"
    fs[0].removed = True
    assert ME.time_hint("tie_points", fs, Margins()).params["n"] == 1
    assert ME.time_hint("tie_points", [], Margins()).key == "facade.method.time_none"


def test_time_hint_range_uses_whole_minutes_and_is_ordered():
    for n in (1, 3, 6, 8):
        h = ME.time_hint("depth_maps", _facades(n, 40.0, 20.0), Margins())
        if h.key == "facade.method.time_range":
            assert 1 <= h.params["low"] < h.params["high"]
            assert h.params["low"] <= math.ceil(h.low_s / 60.0) + 1


def test_warnings():
    assert ME.warnings_for("tie_points") == [("facade.method.tie_points.warning", {})]
    assert ME.warnings_for("dense_cloud") == []
    w = ME.warnings_for("depth_maps", _facades(8, 60.0, 25.0), Margins())
    assert [k for k, _f in w] == ["facade.method.long_notice"] and w[0][1]["low"] < w[0][1]["high"]
    assert ME.warnings_for("depth_maps", _facades(1, 10.0, 5.0), Margins()) == []


def test_facade_summary_and_levels(lang):
    f = Facade.from_frame("A", 0, (0, 0, 0), (1, 0, 0), 66.24, 21.8, score=0.9)
    s = ME.facade_summary(f)
    assert "66.2" in s["size"] and "21.8" in s["size"]
    assert s["facing"] and "{" not in s["facing"]
    assert s["quality"] == i18n.t("facade.detect.quality.high")
    assert [ME.quality_level(x) for x in (0.0, 0.44, 0.45, 0.74, 0.75, 1.0)] == ["low", "low", "medium", "medium", "high", "high"]
    assert ME.direction_key("SW") == "facade.detect.dir.SW"
    assert ME.direction_key(f) == "facade.detect.dir.S"


def test_stage_text_renders_in_both_languages(lang):
    assert ME.stage_text("done", n=3)
    assert "3" in ME.stage_text("done", n=3)
    assert ME.stage_text("read") != ME.stage_text("search")
