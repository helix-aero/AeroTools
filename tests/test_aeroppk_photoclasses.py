"""Aero-PPK, the photos in three classes (median, worst, best), the rule of the box "Include the table of all photos" and the pages of the
table. Synthetic rows, no solver, no Qt."""
import math

import pytest

from aerotools.tools.aeroppk import outputs, photoclasses, report

from test_aeroppk_report import report_of, row


def photo(index, sd, q=1, sdu=None):
    return row(index, q=q, sdn=sd, sde=0.0, sdu=sd * 2.0 if sdu is None else sdu)


def test_the_shares_add_up_to_one_hundred():
    for counts in ([88, 10, 2], [1, 1, 1], [0, 0, 0], [5, 0, 0], [33, 33, 34], [1, 2, 997]):
        out = photoclasses.shares(counts)
        assert sum(out) == (100 if sum(counts) else 0) and all(isinstance(v, int) and v >= 0 for v in out)


def test_the_rows_come_in_the_order_median_worst_best_and_cut_by_rank():
    rows = [photo(i + 1, 0.01 + 0.001 * i) for i in range(100)]
    info = photoclasses.classify(rows)
    assert [r["kind"] for r in info["rows"]] == ["median", "worst", "best"]
    assert [r["count"] for r in info["rows"]] == [65, 10, 25]
    assert [r["share"] for r in info["rows"]] == [65, 10, 25] and sum(r["share"] for r in info["rows"]) == 100
    median, worst, best = info["rows"]
    assert best["name"] == "IMG_0001.JPG" and best["sd_h"] == pytest.approx(0.01)
    assert worst["name"] == "IMG_0100.JPG" and worst["sd_h"] == pytest.approx(0.109)
    assert 26 <= int(median["name"][4:8]) <= 90                  # a photo of the middle class, the middle one of it
    assert median["sd_h"] == pytest.approx(0.01 + 0.001 * (int(median["name"][4:8]) - 1))


def test_the_vertical_precision_is_the_one_of_the_photo_shown():
    rows = [photo(i + 1, 0.01 + 0.001 * i, sdu=0.5 + i) for i in range(20)]
    info = photoclasses.classify(rows)
    for entry in info["rows"]:
        index = int(entry["name"][4:8]) - 1
        assert entry["sd_u"] == pytest.approx(0.5 + index)


def test_photos_without_a_position_are_counted_apart():
    rows = [photo(i + 1, 0.01 + 0.001 * i) for i in range(30)] + [photo(31, 0.5, q=0), photo(32, 0.5, q=0)]
    info = photoclasses.classify(rows)
    assert info["n"] == 30 and info["without"] == 2
    assert sum(r["count"] for r in info["rows"]) == 30


def test_nothing_to_classify_gives_none():
    assert photoclasses.classify([]) is None
    assert photoclasses.classify([photo(1, 0.01, q=0)]) is None
    assert photoclasses.classify([row(1, sdn=float("nan"), sde=0.0)]) is None


def test_a_very_small_flight_keeps_its_middle_class():
    for n in (1, 2, 3, 4):
        info = photoclasses.classify([photo(i + 1, 0.01 * (i + 1)) for i in range(n)])
        median = info["rows"][0]
        assert median["kind"] == "median" and median["count"] >= 1 and median["name"]
        assert sum(r["share"] for r in info["rows"]) == 100


def test_equal_precisions_give_the_same_shares():
    info = photoclasses.classify([photo(i + 1, 0.012) for i in range(40)])
    assert [r["share"] for r in info["rows"]] == [65, 10, 25]


def test_the_share_of_good_photos_uses_the_limit_of_the_report():
    rows = [photo(i + 1, 0.02) for i in range(90)] + [photo(91 + i, 0.2) for i in range(10)]
    assert photoclasses.classify(rows, 0.10)["good_pct"] == pytest.approx(90.0)
    assert photoclasses.classify(rows, 0.5)["good_pct"] == pytest.approx(100.0)


# -- the box of the export ----------------------------------------------------------------------------------------------------------

def test_the_table_is_in_the_report_by_default_up_to_500_photos():
    assert outputs.PHOTO_TABLE_LIMIT == 500
    assert outputs.default_include_table(0) and outputs.default_include_table(500)
    assert not outputs.default_include_table(501) and not outputs.default_include_table(14000)


def test_the_warning_about_the_pages_is_for_more_than_500_photos():
    assert not outputs.table_warning(500) and outputs.table_warning(501)


def test_the_page_estimate_follows_the_rows_of_a_page():
    per_page = int((report.table_capacity() - report.FLIGHT_ROW_H) // report.ROW_H)
    assert report.photo_table_pages(0) == 0 and report.photo_table_pages(1) == 1
    assert report.photo_table_pages(per_page) == 1 and report.photo_table_pages(per_page + 1) == 2
    assert report.photo_table_pages(14000) == math.ceil(14000 / float(per_page))
    exact = len(report.table_plan([1000] * 14))
    assert abs(report.photo_table_pages(14000) - exact) <= 14               # the flight bands add at most a page each


def test_without_the_table_the_report_has_no_table_page():
    from test_aeroppk_report import flight_of
    data = report_of(flight_of(100), flight_of(60))
    with_table = [p[0] for p in report.page_plan(data)]
    data["table"] = False
    without = [p[0] for p in report.page_plan(data)]
    assert with_table.count("table") >= 2 and without.count("table") == 0
    assert without == [k for k in with_table if k != "table"]
    data["table"] = True
    assert [p[0] for p in report.page_plan(data)] == with_table


def test_the_notes_for_a_report_without_the_table_exist():
    from aerotools import i18n
    for key in ("aeroppk.report.exports_no_table", "aeroppk.report.exports_csv_all", "aeroppk.report.verdict_sub_check_csv",
                "aeroppk.report.flags_intro_csv", "aeroppk.export.table", "aeroppk.export.pages", "aeroppk.export.pages_tip"):
        assert i18n.t(key) != key, key
    assert "{pages}" in i18n.t("aeroppk.export.pages") and "Include the table of all photos" in i18n.t("aeroppk.export.table")
