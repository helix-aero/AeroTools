"""AeroPPK report: everything that does not need Qt (formats, names, verdict, histograms, pagination, map geometry, CSV, texts).

The painting itself is checked by tests_qt/run_qt_aeroppk_report.py (real Qt, real PDF and PNG pages).
"""

import ast
import copy
import csv
import datetime
import math
import os
import string

import pytest

from aerotools import i18n
from aerotools.tools.aeroppk import report, reportdata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT_PY = os.path.join(ROOT, "aerotools", "tools", "aeroppk", "report.py")


@pytest.fixture
def english():
    """Kept so the tests read the same: the catalog is English only."""
    return None


def _catalog():
    return {k: v for k, v in i18n.texts().items() if k.startswith(("aeroppk.report.", "aeroppk.avg."))}


@pytest.fixture
def sample():
    return reportdata.sample()


def row(index, q=1, level=0, **kw):
    base = {"index": index, "name": "IMG_%04d.JPG" % index, "time_utc": datetime.datetime(2026, 11, 6, 10, 0, index % 60),
            "lat": 45.3 + index * 1e-5, "lon": 5.15, "h": 500.0, "sdn": 0.01, "sde": 0.01, "sdu": 0.02, "q": q, "level": level,
            "flags": [], "used": ["STNA"], "spread": 0.0, "dn": None, "de": None, "du": None, "onboard_q": None}
    base.update(kw)
    return base


def flight_of(n, name="F", **kw):
    rows = [row(i + 1) for i in range(n)]
    out = {"name": name, "drone": "D", "folder": "x", "photos": n, "events": n, "files": [], "bases": [], "quality": {},
           "onboard": {}, "photos_rows": rows, "track": [], "center": None, "warnings": []}
    out.update(kw)
    return out


def report_of(*flights, **kw):
    data = {"title": "T", "generated": datetime.datetime(2026, 11, 6, 10, 0), "tool": {"name": "Aero-PPK", "version": "1"},
            "engine": {}, "frame": {"label": "F"}, "source": {"name": "S", "licence": "L", "url": "u"}, "flights": list(flights),
            "totals": {}, "warnings": []}
    data.update(kw)
    return data


# --------------------------------------------------------------------------
# Formats
# --------------------------------------------------------------------------

def test_numbers_use_the_application_format(english):
    assert report.fmt_num(1.2345, 2) == "1.23"
    assert report.fmt_cm(0.0123) == "1.2"
    assert report.fmt_pct(91.67) == "91.7 %"
    assert report.fmt_km(0.5) == "0.50 km" and report.fmt_km(21.2) == "21.2 km"


def test_unknown_numbers_are_a_dash():
    for value in (None, float("nan"), "x", float("inf")):
        assert report.fmt_num(value) == report.EM_DASH
        assert report.fmt_cm(value) == report.EM_DASH
        assert report.fmt_signed_cm(value) == report.EM_DASH
        assert report.fmt_pct(value) == report.EM_DASH
        assert report.fmt_km(value) == report.EM_DASH


def test_signed_numbers_use_a_typographic_minus(english):
    assert report.fmt_signed(1.234) == "+1.2"
    assert report.fmt_signed(-1.234) == report.MINUS + "1.2"
    assert report.fmt_signed(0.0) == "0.0"
    assert report.fmt_signed(-0.001) == "0.0"
    assert report.fmt_signed_cm(0.048) == "+4.8"


def test_lengths_and_durations():
    assert report.fmt_length_m(200) == "200 m"
    assert report.fmt_length_m(5000) == "5 km"
    assert report.fmt_length_m(2500) == "2.5 km" or report.fmt_length_m(2500) == "2,5 km"
    assert report.fmt_duration(11) == "11 s"
    assert report.fmt_duration(125) == "2 min 05 s"
    assert report.fmt_duration(3725) == "1 h 02 min"
    assert report.fmt_duration(None) == report.EM_DASH


def test_times_and_dates(english):
    moment = datetime.datetime(2026, 11, 6, 9, 5, 3, 400000)
    assert report.fmt_time(moment) == "09:05:03"
    assert report.fmt_time(None) == report.EM_DASH
    assert report.fmt_datetime(moment) == "2026-11-06 09:05"


def test_systems_names():
    assert report.systems_names("GRE") == "GPS, GLONASS, Galileo"
    assert report.systems_names("GREC") == "GPS, GLONASS, Galileo, BeiDou"
    assert report.systems_names("GG") == "GPS"
    assert report.systems_names("") == "" and report.systems_names(None) == ""
    assert report.systems_names("X") == "X"


def test_quality_and_level_keys():
    assert [report.quality_key(q) for q in (1, 2, 0, 5, None)] == ["fix", "float", "none", "none", "none"]
    assert [report.level_key(v) for v in (0, 1, 2, 7)] == ["ok", "warn", "bad", "bad"]


def test_suggested_filename(english, sample):
    assert report.suggested_filename(sample, ".pdf") == "2026-11-06_PPK_report_Demo_project.pdf"
    assert report.suggested_filename(sample, ".csv") == "2026-11-06_PPK_positions_Demo_project.csv"
    data = dict(sample, title="Site é/west: n°2")
    assert report.suggested_filename(data, ".pdf").endswith("_Site_e_west_n2.pdf")
    assert report.suggested_filename({"title": ""}, ".pdf", today=datetime.date(2026, 1, 2)).startswith("2026-01-02_")


# --------------------------------------------------------------------------
# Names and texts
# --------------------------------------------------------------------------

def test_flag_names_have_texts(english):
    for flag in report.FLAG_ORDER:
        assert report.flag_label(flag) == _catalog()["aeroppk.report.flag_long_" + flag]
        assert report.flag_label(flag, short=True) == _catalog()["aeroppk.report.flag_short_" + flag]


def test_unknown_flag_is_shown_as_it_is(english):
    assert report.flag_label("some_new_flag") == "some new flag"
    assert report.flag_label("some_new_flag", short=True) == "some new flag"


def test_warning_text_falls_back_to_the_key_and_never_crashes(english):
    assert report.warning_text(("warning", "aeroppk.warn.never_heard_of_it", {})) == "aeroppk.warn.never_heard_of_it"
    assert "STNB" in report.warning_text(("warning", "aeroppk.warn.suspect_base", {"code": "STNB"}))
    assert report.warning_text(("warning", "aeroppk.warn.suspect_base", None))      # missing fields do not crash
    assert report.warning_text(("warning",)) == ""
    assert report.warning_level(("weird", "k", {})) == "warning"
    assert report.warning_level(("error", "k", {})) == "error"
    assert report.warning_level(()) == "warning"


def test_need_texts_have_a_fallback(english):
    name, why = report.need_texts("rover_obs")
    assert "RINEX" in name and why
    assert report.need_texts("something_else") == ("something_else", "")


def test_texts_come_from_the_catalog_first(english, monkeypatch):
    monkeypatch.setattr(i18n, "has", lambda key, code=None: True)
    monkeypatch.setattr(i18n, "t", lambda key, **fields: "catalog:" + key)
    assert report._t("aeroppk.report.title") == "catalog:aeroppk.report.title"


def test_texts_come_from_the_catalog_then_the_key(english):
    assert report._t("aeroppk.report.title") == "PPK Report"
    assert report._t("aeroppk.report.footer_page", page=2, total=5) == "Page 2 of 5"
    assert report._t("aeroppk.report.no_such_text") == "aeroppk.report.no_such_text"
    assert report._t("aeroppk.report.footer_page") == "Page {page} of {total}"          # a missing field stays visible


def fields_of(text):
    return sorted(name for _lit, name, _spec, _conv in string.Formatter().parse(text) if name is not None)


def test_texts_follow_the_catalog_style_rules():
    table = _catalog()
    assert len(table) > 150
    for key, text in table.items():
        assert isinstance(text, str) and text.strip(), key
        assert text == text.strip(), key
        assert ";" not in text, key
        assert " " not in text and " " not in text, key


def _literal_keys(path):
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    keys = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.args and isinstance(node.func, ast.Name) and node.func.id in ("_t", "_has"):
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                keys.add(first.value)
    return keys


def test_every_text_key_used_by_the_report_exists():
    used = _literal_keys(REPORT_PY)
    assert len(used) > 80
    assert sorted(k for k in used if k not in _catalog()) == []


def test_computed_text_keys_exist():
    needed = ["aeroppk.report.q_" + k for k in ("fix", "float", "none")] + ["aeroppk.report.lv_" + k for k in ("ok", "warn", "bad")]
    needed += ["aeroppk.report.verdict_" + k for k in ("ok", "check", "stop")] + ["aeroppk.report.verdict_sub_" + k for k in ("ok", "check", "stop")]
    needed += ["aeroppk.report.csv_" + k for k in report.CSV_KEYS]
    needed += ["aeroppk.report.opt_" + k for k in report.OPTION_KEYS]
    needed += ["aeroppk.report.flag_%s_%s" % (kind, f) for kind in ("short", "long") for f in report.FLAG_ORDER]
    needed += ["aeroppk.report.limit_%d" % k for k in range(1, 7)]
    needed += ["aeroppk.report.method_sub_1", "aeroppk.report.method_sub_2", "aeroppk.report.method_sub_3"]
    needed += ["aeroppk.report.status_" + k for k in ("ok", "check", "stop")] + ["aeroppk.report.pill_valid", "aeroppk.report.pill_partial"]
    needed += ["aeroppk.report.file_present", "aeroppk.report.file_absent", "aeroppk.report.set_pos_network", "aeroppk.report.set_pos_own"]
    needed += ["aeroppk.report.stat_" + k for k in ("avg", "std", "rms", "min", "max")]
    needed += ["aeroppk.report.disclaimer_public", "aeroppk.report.disclaimer_own", "aeroppk.report.disclaimer_tool"]
    needed += ["aeroppk.report.table_col_" + k for k in ("index", "photo", "time", "lat", "lon", "h", "sn", "se", "su", "q", "bases", "flags")]
    assert [k for k in needed if k not in _catalog()] == []


# --------------------------------------------------------------------------
# Summary and verdict
# --------------------------------------------------------------------------

def test_flight_counts_come_from_the_quality_dictionary_or_the_rows():
    f = flight_of(4)
    f["photos_rows"][1]["q"] = 2
    f["photos_rows"][1]["level"] = 1
    f["photos_rows"][2]["q"] = 0
    f["photos_rows"][2]["level"] = 2
    counts = report.flight_counts(f)
    assert counts == {"photos": 4, "fix": 2, "float": 1, "none": 1, "ok": 2, "warn": 1, "bad": 1}
    f["quality"] = {"photos": 10, "fix": 7}
    counts = report.flight_counts(f)
    assert counts["photos"] == 10 and counts["fix"] == 7 and counts["float"] == 1


def test_totals_are_summed_when_missing(sample):
    data = report_of(flight_of(3), flight_of(2))
    tot = report.totals(data)
    assert tot["photos"] == 5 and tot["fix"] == 5 and tot["fix_pct"] == 100.0
    assert report.totals(sample)["photos"] == 12


def test_warnings_are_listed_once_errors_first(english):
    data = report_of(
        flight_of(2, "A", warnings=[("warning", "aeroppk.warn.x", {}), ("error", "aeroppk.warn.bad", {})]),
        flight_of(2, "B", warnings=[("warning", "aeroppk.warn.x", {}), ("info", "aeroppk.warn.y", {})]),
        warnings=[("warning", "aeroppk.warn.global", {}), ("warning", "aeroppk.warn.global", {})])
    items = report.collect_warnings(data)
    assert [w["text"] for w in items] == ["aeroppk.warn.bad", "aeroppk.warn.global", "aeroppk.warn.x", "aeroppk.warn.y"]
    by_text = {w["text"]: w["flight"] for w in items}
    assert by_text["aeroppk.warn.x"] is None            # the same warning in every flight is not tied to one
    assert by_text["aeroppk.warn.bad"] == "A" and by_text["aeroppk.warn.y"] == "B"
    assert by_text["aeroppk.warn.global"] is None


def test_a_flight_warning_equal_to_a_global_one_is_not_repeated(english, sample):
    data = copy.deepcopy(sample)
    assert len(report.collect_warnings(data)) == 1


def test_verdict_green_amber_red(sample):
    clean = report_of(flight_of(20))
    assert report.verdict(clean) == ("ok", 0)
    assert report.verdict(sample)[0] == "check"             # one photo to check and one suspect base
    amber = report_of(flight_of(20, bases=[{"code": "A", "suspect": True}]))
    assert report.verdict(amber)[0] == "check"
    warned = report_of(flight_of(20), warnings=[("warning", "k", {})])
    assert report.verdict(warned)[0] == "check"
    info_only = report_of(flight_of(20), warnings=[("info", "k", {})])
    assert report.verdict(info_only)[0] == "ok"
    assert report.verdict(report_of(flight_of(20), warnings=[("error", "k", {})]))[0] == "stop"
    assert report.verdict(report_of())[0] == "stop"                      # nothing to use
    gross = report_of(flight_of(20, onboard={"gross_error": True}))
    assert report.verdict(gross)[0] == "stop"
    blocking = report_of(flight_of(20, files=[{"key": "events", "present": False, "level": "blocking"}]))
    assert report.verdict(blocking)[0] == "stop"
    advised_missing = report_of(flight_of(20, files=[{"key": "rover_nav", "present": False, "level": "advised"}]))
    assert report.verdict(advised_missing)[0] == "ok"


def test_verdict_thresholds_on_bad_and_float_photos():
    f = flight_of(20)
    for i in range(3):
        f["photos_rows"][i]["level"] = 2
        f["photos_rows"][i]["q"] = 0
    assert report.verdict(report_of(f))[0] == "stop"         # 15 % not reliable
    f = flight_of(20)
    f["photos_rows"][0]["level"] = 2
    f["photos_rows"][0]["q"] = 0
    assert report.verdict(report_of(f)) == ("check", 1)      # 5 %
    f = flight_of(20)
    for r in f["photos_rows"][:12]:
        r["q"] = 2
    assert report.verdict(report_of(f))[0] == "stop"         # only 40 % fixed


def test_base_codes_and_suspects(sample):
    assert report.base_codes(sample) == ["STNA", "STNB"]
    assert report.suspect_codes(sample) == ["STNB"]


def _public_source():
    """The source of report.py without the blocks that belong to the private pages."""
    with open(REPORT_PY, encoding="utf-8") as handle:
        lines = handle.read().split("\n")
    kept, hidden = [], False
    for line in lines:
        stripped = line.strip()
        if stripped == "# tool:private":
            hidden = True
        elif stripped == "# /tool":
            hidden = False
        elif not hidden and "# tool:private" not in line:
            kept.append(line)
    return "\n".join(kept)


def test_the_report_has_no_orange_or_red():
    """Green is for what is good, every other value is grey: no orange or red constant and no warm colour in the painting code."""
    import colorsys
    import re
    source = _public_source()
    for name in ("AMBER", "ORANGE", "RED", "NAVY"):
        assert re.search(r"\b%s\b" % name, source) is None, name
    for code in re.findall(r"#[0-9a-fA-F]{6}\b", source):
        r, g, b = (int(code[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
        hue, light, sat = colorsys.rgb_to_hls(r, g, b)[0] * 360.0, colorsys.rgb_to_hls(r, g, b)[1], colorsys.rgb_to_hls(r, g, b)[2]
        warm = (hue < 50.0 or hue > 330.0) and sat > 0.2 and 0.1 < light < 0.95
        assert not warm, code


def test_the_report_has_no_company_name_in_its_texts():
    """The logo carries the brand: no company name in the texts of the report, and nothing about a mandatory file in red."""
    table = _catalog()
    for key, text in table.items():
        if key.startswith("aeroppk.report."):
            assert "helix" not in text.lower(), key
            assert "mandatory" not in text.lower() and "required" not in text.lower(), key


def test_good_figures_are_green_and_the_others_grey():
    assert report._good_text("fix", 100.0) == report.GOOD_TEXT
    assert report._good_text("fix", 90.0) == report.GREY_DARK
    assert report._good_text("float", 5.0) == report.GOOD_TEXT and report._good_text("float", 8.0) == report.GREY_DARK
    assert report._good_text("sd", 0.1) == report.GOOD_TEXT and report._good_text("sd", 0.2) == report.GREY_DARK
    assert report.GOOD_TEXT == report.GREEN_DEEP
    assert set(report.QUALITY_COLORS.values()) == {report.GREEN, report.GREY_MID, report.GREY_LIGHT}


def test_stats_of_a_list():
    assert report._stats([]) is None
    mean, std, rms, low, high = report._stats([1.0, 3.0])
    assert (mean, std, low, high) == (2.0, 1.0, 1.0, 3.0) and rms == pytest.approx(math.sqrt(5.0))


def test_combined_horizontal_sigma():
    assert report.sd_h({"sdn": 0.03, "sde": 0.04}) == pytest.approx(0.05)
    assert report.sd_h({"sdn": None, "sde": 0.04}) is None
    assert report.sd_h({}) is None


def test_onboard_rows_use_only_the_drones_rtk_photos():
    f = flight_of(4)
    for i, r in enumerate(f["photos_rows"]):
        r.update(dn=0.01 * i, de=0.0, du=0.0, onboard_q=50 if i < 2 else 34)
    assert report.onboard_rows(f) == [(0.0, 0.0, 0.0), (0.01, 0.0, 0.0)]
    for r in f["photos_rows"]:
        r["onboard_q"] = None                              # rows without the drone status: every difference counts
    assert len(report.onboard_rows(f)) == 4
    f["photos_rows"][0]["dn"] = None
    assert len(report.onboard_rows(f)) == 3
    for r in f["photos_rows"]:
        r["onboard_q"] = 34
    assert report.onboard_rows(f) == []


# --------------------------------------------------------------------------
# Histograms
# --------------------------------------------------------------------------

def test_histogram_without_values_is_none():
    assert report.histogram([]) is None
    assert report.histogram([None, float("nan")]) is None
    assert report.robust_top([]) is None


def test_histogram_unsigned_covers_the_values():
    values = [1.0 + 0.1 * i for i in range(30)]
    h = report.histogram(values)
    assert h["low"] == 0.0 and h["high"] >= max(values)
    assert sum(h["counts"]) + h["below"] + h["above"] == 30 and h["above"] == 0
    assert len(h["edges"]) == len(h["counts"]) + 1 and h["n"] == 30
    assert len(h["counts"]) >= 14 and len(h["counts"]) % h["k"] == 0


def test_histogram_sends_far_values_to_the_extra_bar():
    values = [1.5] * 100 + [20.0, 25.0, 30.0]
    h = report.histogram(values)
    assert h["above"] == 3 and h["high"] < 20.0
    assert sum(h["counts"]) == 100


def test_histogram_signed_is_centred_on_zero():
    values = [-0.4, -0.1, 0.0, 0.2, 0.5, 0.45]
    h = report.histogram(values, signed=True)
    assert h["low"] == -h["high"]
    assert len(h["counts"]) % 2 == 0
    assert 0.0 in [round(e, 9) for e in h["edges"]]
    assert sum(h["counts"]) + h["below"] + h["above"] == 6


def test_bin_counts_edges():
    counts, below, above = report.bin_counts([0.0, 1.0, 1.0, 2.0, 2.5, -1.0], 0.0, 2.0, 2)
    assert counts == [1, 3] and below == 1 and above == 1


def test_histogram_with_one_value_or_identical_values():
    for values in ([3.0], [0.0, 0.0], [2.0] * 5):
        h = report.histogram(values)
        assert h is not None and sum(h["counts"]) + h["above"] == len(values)


# --------------------------------------------------------------------------
# Pagination
# --------------------------------------------------------------------------

def rows_in(plan, flight):
    return sum(e[3] - e[2] for page in plan for e in page if e[0] == "rows" and e[1] == flight)


def test_table_plan_keeps_every_row():
    for counts in ([0], [1], [12], [300], [140, 120, 60], [3, 0, 5], [46, 46, 46], [0, 0]):
        plan = report.table_plan(counts)
        for index, n in enumerate(counts):
            assert rows_in(plan, index) == n, (counts, index)


def test_table_plan_rows_follow_each_other_without_gaps():
    plan = report.table_plan([300, 17])
    seen = {}
    for page in plan:
        for e in page:
            if e[0] == "rows":
                assert e[2] == seen.get(e[1], 0)
                seen[e[1]] = e[3]
    assert seen == {0: 300, 1: 17}


def test_table_plan_pages_fit_the_capacity():
    capacity = report.table_capacity()
    for counts in ([300], [140, 120, 60], [5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5]):
        for page in report.table_plan(counts):
            used = 0.0
            for e in page:
                used += report.FLIGHT_ROW_H if e[0] == "flight" else (e[3] - e[2]) * report.ROW_H if e[0] == "rows" else report.ROW_H
            assert used <= capacity + 1e-6


def test_a_flight_band_is_never_alone_at_the_bottom_of_a_page():
    capacity = report.table_capacity()
    per_page = int((capacity - report.FLIGHT_ROW_H) // report.ROW_H)
    plan = report.table_plan([per_page - 1, 10])           # the second flight would start with no room for its rows
    for page in plan:
        for index, e in enumerate(page):
            if e[0] == "flight":
                assert index + 1 < len(page) and page[index + 1][0] in ("rows", "empty")
                if page[index + 1][0] == "rows":
                    assert page[index + 1][3] - page[index + 1][2] >= 3 or page[index + 1][3] == 10
    assert len(plan) == 2


def test_continued_flights_are_marked():
    plan = report.table_plan([300])
    flags = [e[2] for page in plan for e in page if e[0] == "flight"]
    assert flags[0] is False and all(flags[1:])
    assert len(plan) > 5


def test_empty_flight_gets_a_line():
    plan = report.table_plan([0, 2])
    assert ("empty", 0) in plan[0] and plan[0][0] == ("flight", 0, False)


def test_page_plan_and_count(sample):
    plan = report.page_plan(sample)
    assert [p[0] for p in plan] == ["cover", "imported", "stations", "solution", "maps", "quality", "table", "method", "method", "method"]
    assert report.page_count(sample) == 10
    big = report_of(flight_of(140), flight_of(120), flight_of(60))
    plan = report.page_plan(big)
    kinds = [p[0] for p in plan]
    assert kinds[:6] == ["cover", "imported", "stations", "solution", "maps", "quality"] and kinds[6:7] == ["imported"]
    assert kinds[-3:] == ["method", "method", "method"] and kinds.count("table") == len(report.table_plan([140, 120, 60]))
    assert report.page_count(report_of()) == 4                       # cover and the three method pages


# --------------------------------------------------------------------------
# Map geometry
# --------------------------------------------------------------------------

def test_local_xy_directions_and_scale():
    east, north = report.local_xy(45.001, 5.0, 45.0, 5.0)
    assert east == pytest.approx(0.0, abs=1e-6) and north == pytest.approx(111.19, abs=0.2)
    east, north = report.local_xy(45.0, 5.001, 45.0, 5.0)
    assert north == pytest.approx(0.0, abs=1e-6) and east == pytest.approx(111.19 * math.cos(math.radians(45.0)), abs=0.2)
    assert report.local_xy(45.0, 5.0, 45.0, 5.0) == (0.0, 0.0)


def test_nice_lengths_and_rings():
    assert report.nice_length(0.7) == 0.5 and report.nice_length(1) == 1 and report.nice_length(37) == 20
    assert report.nice_length(930) == 500 and report.nice_length(12000) == 10000 and report.nice_length(0) == 1.0
    for half in (200.0, 700.0, 5000.0, 35000.0):
        step = report.ring_step(half)
        assert 1 <= half / step <= 6.5


def test_map_view_is_north_up_and_isotropic():
    view = report.MapView((10, 20, 200, 100), (-100, -50, 100, 50))
    assert view.k == pytest.approx(1.0)
    assert view.pt(0, 0) == pytest.approx((110.0, 70.0))
    x1, y1 = view.pt(10, 10)
    assert x1 > 110.0 and y1 < 70.0                                  # east is right, north is up
    view = report.MapView((0, 0, 200, 100), (-100, -100, 100, 100))
    assert view.k == pytest.approx(0.5)                              # the smaller side limits the scale
    assert view.pt(0, 0) == pytest.approx((100.0, 50.0))
    assert view.inside(0, 0) and not view.inside(500, 0) and view.inside(0, 99) and not view.inside(0, 99, margin=5)


def test_map_view_edge_point_lies_on_the_inset_border():
    view = report.MapView((0, 0, 200, 100), (-100, -50, 100, 50))
    x, y, angle = view.edge_point(1000.0, 0.0, margin=10.0)
    assert x == pytest.approx(190.0) and y == pytest.approx(50.0) and angle == pytest.approx(0.0)
    x, y, angle = view.edge_point(0.0, 5000.0, margin=10.0)
    assert x == pytest.approx(100.0) and y == pytest.approx(10.0) and angle == pytest.approx(-math.pi / 2)
    x, y, _angle = view.edge_point(-3000.0, -3000.0, margin=10.0)
    assert 10.0 - 1e-9 <= x <= 190.0 + 1e-9 and 10.0 - 1e-9 <= y <= 90.0 + 1e-9
    assert view.edge_point(0.0, 0.0)[:2] == pytest.approx((100.0, 50.0))


def test_track_segments_share_their_end_points():
    track = [(0, 0, 1), (1, 0, 1), (2, 0, 2), (3, 0, 2), (4, 0, 0), (5, 0, 1)]
    runs = report.track_segments(track)
    assert [r[0] for r in runs] == ["fix", "float", "none", "fix"]
    assert runs[0][1] == [(0, 0), (1, 0)] and runs[1][1] == [(1, 0), (2, 0), (3, 0)]
    assert runs[3][1] == [(4, 0), (5, 0)]
    assert report.track_segments([]) == []
    assert report.track_segments([(0, 0, 1)]) == [("fix", [(0, 0)])]


def test_flight_geometry_places_bases_around_the_flight(sample):
    geom = report.flight_geometry(sample["flights"][0])
    assert geom["origin"] == (45.3, 5.15)
    assert len(geom["track"]) == 12 and len(geom["photos"]) == 12
    by_code = {b["code"]: (e, n) for e, n, b in geom["bases"]}
    assert by_code["STNA"] == pytest.approx(report.local_xy(45.2988, 5.1528, 45.3, 5.15), abs=0.01)
    east, north = by_code["STNA"]
    assert east > 0 > north and math.hypot(east, north) == pytest.approx(256.4, abs=3.0)      # south east, a few hundred metres
    east, north = by_code["STNB"]
    assert east > 15000 and north < -15000                                                   # twenty kilometres away
    for east, north, _q in geom["track"]:
        assert abs(east) < 50 and abs(north) < 50


def test_flight_geometry_survives_missing_data():
    empty = report.flight_geometry({})
    assert empty == {"origin": None, "track": [], "photos": [], "bases": []}
    f = flight_of(3)
    f["photos_rows"][1]["lat"] = float("nan")
    geom = report.flight_geometry(f)                                  # no track: the photo positions are used
    assert len(geom["track"]) == 2 and geom["origin"] is not None
    f["bases"] = [{"code": "X", "lat": None, "lon": None}]
    assert report.flight_geometry(f)["bases"] == []


# --------------------------------------------------------------------------
# CSV
# --------------------------------------------------------------------------

def read_csv(path, delimiter):
    with open(path, "rb") as handle:
        raw = handle.read()
    assert raw[:3] == b"\xef\xbb\xbf"
    return list(csv.reader(raw.decode("utf-8-sig").splitlines(), delimiter=delimiter))


def test_csv_english(tmp_path, english, sample):
    path = report.write_csv(sample, str(tmp_path / "a.csv"))
    rows = read_csv(path, ",")
    assert rows[0] == [_catalog()["aeroppk.report.csv_" + k] for k in report.CSV_KEYS]
    assert rows[0][:6] == ["flight", "photo", "time_utc", "lat", "lon", "h_ellipsoidal_m"]
    assert len(rows) == 13 and all(len(r) == 17 for r in rows)
    first = rows[1]
    assert first[0] == "FLIGHT_DEMO_001" and first[1] == "IMG_0001.JPG"
    assert first[2] == "2026-11-06 10:00:00.000" and first[3] == "45.300000000" and first[5] == "500.0000"
    assert first[6] == "0.0120" and first[9] == "fix" and first[10] == "ok" and first[11] == "STNA+STNB"
    assert first[12] == "0.0200" and first[13] == "" and first[14] == "0.0120" and first[15] == "-0.0080" and first[16] == "-0.0500"
    float_row = rows[6]
    assert float_row[9] == "float" and float_row[10] == "warn" and float_row[13] == "float"


def test_csv_leaves_unknown_values_empty_and_joins_flags(tmp_path, english):
    f = flight_of(2)
    f["photos_rows"][0].update(lat=float("nan"), lon=float("nan"), h=None, q=0, level=2, flags=["no_solution"], used=[], spread=None)
    f["photos_rows"][1].update(flags=["float", "spread"], used=["A", "B", "C"], dn=0.1, de=None, du=0.0, time_utc=None)
    rows = read_csv(report.write_csv(report_of(f), str(tmp_path / "c.csv")), ",")
    bad, other = rows[1], rows[2]
    assert bad[3] == "" and bad[4] == "" and bad[5] == "" and bad[9] == "none" and bad[10] == "bad" and bad[11] == "" and bad[12] == ""
    assert bad[13] == "no_solution"
    assert other[13] == "float|spread" and other[11] == "A+B+C" and other[14] == "0.1000" and other[15] == "" and other[2] == ""


def test_csv_one_row_per_photo_over_all_flights(tmp_path, english):
    data = report_of(flight_of(5, "A"), flight_of(0, "B"), flight_of(3, "C"))
    rows = read_csv(report.write_csv(data, str(tmp_path / "d.csv")), ",")
    assert len(rows) == 1 + 8
    assert [r[0] for r in rows[1:]] == ["A"] * 5 + ["C"] * 3
    assert report.csv_rows(report_of()) == [report.csv_header()]


def test_csv_header_uses_the_catalog():
    assert report.csv_header()[0] == "flight"
