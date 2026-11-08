"""FastGCP PDF and CSV report: the part that does not need Qt.

The rendering itself is checked by tests_qt/run_qt_report.py (real Qt).
Here: marker reading, statistics, formats, CSV, file names,
page distribution and export orchestration.
"""

import datetime
import math
import types

import pytest

from aerotools import msapi
from aerotools.tools.fastgcp import report


# --------------------------------------------------------------------------
# Test doubles
# --------------------------------------------------------------------------

def make_world(ms, rows):
    """Chunk with one marker per row (label, projection count, errors or None)."""
    cameras = [ms.Camera(key, ms.Vector(0, 0, 0)) for key in range(1, 12)]
    markers, errors = [], {}
    for label, count, err in rows:
        projections = {cameras[i]: ms.Marker.Projection(ms.Vector(1, 2, 0), True)
                       for i in range(count)}
        marker = ms.Marker(label, projections=projections)
        markers.append(marker)
        errors[label] = err
    chunk = ms.Chunk(cameras, markers)
    chunk.label = "Survey Z\u00fcrich n°2"
    ms.app.document.chunk = chunk

    def fake_errors(_chk, marker):
        err = errors[marker.label]
        if err is None:
            return {"x": None, "y": None, "z": None, "total": None}
        x, y, z = err
        return {"x": x, "y": y, "z": z, "total": math.sqrt(x * x + y * y + z * z)}

    return chunk, fake_errors


@pytest.fixture
def world(ms, monkeypatch):
    rows = [
        ("GCP01", 4, (0.010, 0.020, 0.030)),
        ("GCP02", 3, (-0.020, 0.010, -0.040)),
        ("GCP03", 5, (0.030, -0.010, 0.020)),
        ("GCP04", 0, None),
        ("GCP05", 2, (0.200, -0.150, 0.300)),
    ]
    chunk, fake = make_world(ms, rows)
    monkeypatch.setattr(msapi, "marker_errors", fake, raising=False)
    return chunk


# --------------------------------------------------------------------------
# Formats
# --------------------------------------------------------------------------

def test_fmt_num_uses_decimal_point_and_three_decimals():
    assert report.fmt_num(0.12345) == "0.123"
    assert report.fmt_num(12) == "12.000"
    assert report.fmt_num(-0.0456) == "-0.046"
    assert report.fmt_num(4.56, 1) == "4.6"


def test_fmt_num_never_prints_negative_zero():
    assert report.fmt_num(-0.0004) == "0.000"


def test_fmt_num_gives_a_dash_when_unknown():
    for value in (None, float("nan"), float("inf"), "abc"):
        assert report.fmt_num(value) == report.EM_DASH


def test_fmt_m_adds_the_unit():
    assert report.fmt_m(0.0346) == "0.035 m"
    assert report.fmt_m(None) == report.EM_DASH



# --------------------------------------------------------------------------
# File name
# --------------------------------------------------------------------------

def test_safe_name_strips_accents_spaces_and_symbols():
    assert report.safe_name("Survey Z\u00fcrich n°2") == "Survey_Zurich_n2"
    assert report.safe_name("  Chunk 1 / Nord  ") == "Chunk_1_Nord"
    assert report.safe_name("") == "Chunk"
    assert report.safe_name("\u00fc?*") == "u"
    assert report.safe_name("日本") == "Chunk"


def test_safe_name_is_bounded():
    assert len(report.safe_name("a" * 300)) == 60


def test_suggested_filename_follows_the_house_pattern():
    day = datetime.date(2026, 11, 3)
    assert report.suggested_filename("Chunk 1", today=day) == "2026-11-03_GCP_report_Chunk_1.pdf"
    assert report.suggested_filename("Chunk 1", ".csv", today=day, kind="GCP_table") == \
        "2026-11-03_GCP_table_Chunk_1.csv"
    name = report.suggested_filename("Survey Z\u00fcrich")
    assert " " not in name and name.isascii() and name.endswith(".pdf")


def test_default_directory_prefers_the_project_folder(ms, tmp_path):
    project = tmp_path / "site.psx"
    ms.app.document.path = str(project)
    assert report.project_directory() == str(tmp_path)
    assert report.default_directory() == str(tmp_path)


def test_default_directory_without_project_falls_back(ms):
    ms.app.document.path = ""
    assert report.project_directory() is None
    ms.app.document.path = "Z:/folder/that/does/not/exist/p.psx"
    assert report.project_directory() is None
    assert report.default_directory()


# --------------------------------------------------------------------------
# collect_data
# --------------------------------------------------------------------------

def test_collect_data_lists_every_marker_with_its_numbers(world):
    data = report.collect_data(world, today=datetime.date(2026, 11, 3))
    assert data["chunk"] == "Survey Z\u00fcrich n°2"
    assert data["date"] == datetime.date(2026, 11, 3)
    labels = [m["label"] for m in data["markers"]]
    assert labels == ["GCP01", "GCP02", "GCP03", "GCP04", "GCP05"]
    first = data["markers"][0]
    assert (first["x"], first["y"], first["z"]) == (0.010, 0.020, 0.030)
    assert first["total"] == pytest.approx(math.sqrt(0.0014))
    assert first["projections"] == 4 and first["pointed"] is True
    unpointed = data["markers"][3]
    assert unpointed["pointed"] is False and unpointed["total"] is None
    assert unpointed["projections"] == 0


def test_collect_data_statistics(world):
    stats = report.collect_data(world)["stats"]
    assert stats["n_markers"] == 5
    assert stats["n_pointed"] == 4
    assert stats["n_with_error"] == 4
    assert stats["total_projections"] == 14
    assert stats["mean_projections"] == pytest.approx(2.8)
    assert stats["median_projections"] == 3
    assert stats["min_projections"] == 0 and stats["max_projections"] == 5
    assert stats["n_low_projections"] == 1            # GCP05: 2 projections
    xs = [0.010, -0.020, 0.030, 0.200]
    assert stats["rms_x"] == pytest.approx(math.sqrt(sum(v * v for v in xs) / 4))
    assert stats["rms_xy"] == pytest.approx(math.hypot(stats["rms_x"], stats["rms_y"]))
    totals = [math.sqrt(0.0014), math.sqrt(0.0021), math.sqrt(0.0014), math.sqrt(0.1525)]
    assert stats["rms_total"] == pytest.approx(math.sqrt(sum(t * t for t in totals) / 4))
    assert stats["worst_label"] == "GCP05"
    assert stats["worst_total"] == pytest.approx(math.sqrt(0.1525))
    assert stats["threshold_total"] == pytest.approx(2 * stats["rms_total"])
    assert stats["threshold_x"] == pytest.approx(2 * stats["rms_x"])


def test_collect_data_reads_the_meta(world, ms):
    ms.app.document.path = "D:/projects/SiteA.psx"
    ms.app.version = "2.2.1"
    meta = report.collect_data(world)["meta"]
    assert meta["project"] == "SiteA.psx"
    assert meta["metashape"] == "2.2.1"


def test_collect_data_unsaved_project(world, ms):
    ms.app.document.path = ""
    assert "Unsaved" in report.collect_data(world)["meta"]["project"]


def test_no_error_at_all_gives_none_statistics(ms, monkeypatch):
    chunk, fake = make_world(ms, [("A", 3, None), ("B", 0, None)])
    monkeypatch.setattr(msapi, "marker_errors", fake, raising=False)
    stats = report.collect_data(chunk)["stats"]
    assert stats["rms_x"] is None and stats["rms_total"] is None
    assert stats["worst_label"] is None and stats["worst_total"] is None
    assert stats["threshold_total"] is None
    assert stats["n_with_error"] == 0 and stats["n_pointed"] == 1


def test_threshold_needs_three_measures(ms, monkeypatch):
    rows = [("A", 3, (0.01, 0.01, 0.01)), ("B", 3, (0.5, 0.5, 0.5))]
    chunk, fake = make_world(ms, rows)
    monkeypatch.setattr(msapi, "marker_errors", fake, raising=False)
    assert report.collect_data(chunk)["stats"]["threshold_total"] is None


def test_zero_errors_have_no_threshold(ms, monkeypatch):
    rows = [(n, 3, (0.0, 0.0, 0.0)) for n in "ABCD"]
    chunk, fake = make_world(ms, rows)
    monkeypatch.setattr(msapi, "marker_errors", fake, raising=False)
    stats = report.collect_data(chunk)["stats"]
    assert stats["rms_total"] == 0.0 and stats["threshold_total"] is None


def test_empty_chunk(ms):
    chunk = ms.Chunk([], [])
    data = report.collect_data(chunk)
    assert data["markers"] == []
    assert data["stats"]["n_markers"] == 0
    assert data["stats"]["mean_projections"] is None
    assert data["stats"]["max_projections"] is None


def test_error_reader_that_raises_is_tolerated(ms, monkeypatch):
    chunk, _fake = make_world(ms, [("A", 2, None)])

    def boom(_chk, _marker):
        raise RuntimeError("invalid marker")

    monkeypatch.setattr(msapi, "marker_errors", boom, raising=False)
    marker = report.collect_data(chunk)["markers"][0]
    assert marker["total"] is None and marker["projections"] == 2


def test_nan_and_garbage_errors_become_none(ms, monkeypatch):
    chunk, _fake = make_world(ms, [("A", 2, None)])
    monkeypatch.setattr(msapi, "marker_errors",
                        lambda c, m: {"x": float("nan"), "y": "abc", "z": 0.1, "total": None},
                        raising=False)
    marker = report.collect_data(chunk)["markers"][0]
    assert marker["x"] is None and marker["y"] is None and marker["z"] == 0.1
    assert marker["total"] is None


def test_total_is_rebuilt_from_the_axes_when_missing(ms, monkeypatch):
    chunk, _fake = make_world(ms, [("A", 2, None)])
    monkeypatch.setattr(msapi, "marker_errors",
                        lambda c, m: {"x": 0.03, "y": 0.04, "z": 0.0}, raising=False)
    assert report.collect_data(chunk)["markers"][0]["total"] == pytest.approx(0.05)


def test_projection_count_failure_counts_zero(ms, monkeypatch):
    chunk, fake = make_world(ms, [("A", 2, (0.1, 0.1, 0.1))])
    monkeypatch.setattr(msapi, "marker_errors", fake, raising=False)

    def boom(_marker):
        raise RuntimeError

    monkeypatch.setattr(msapi, "projection_count", boom)
    marker = report.collect_data(chunk)["markers"][0]
    assert marker["projections"] == 0 and marker["pointed"] is False


def test_reference_coordinates_are_read_when_known(ms, monkeypatch):
    chunk, fake = make_world(ms, [("A", 2, (0.1, 0.1, 0.1)), ("B", 2, (0.1, 0.1, 0.1))])
    monkeypatch.setattr(msapi, "marker_errors", fake, raising=False)
    chunk.markers[0].reference.location = ms.Vector(500000.5, 4700000.25, 12.0)
    markers = report.collect_data(chunk)["markers"]
    assert markers[0]["ref"] == (500000.5, 4700000.25, 12.0)
    assert markers[1]["ref"] is None


def test_fallback_errors_follow_the_api_recipe(ms):
    class Frame(object):
        def mulv(self, delta):
            return (delta.x, delta.y, delta.z)

    class Crs(object):
        def project(self, v):
            return v

        def unproject(self, v):
            return v

        def localframe(self, _world):
            return Frame()

    chunk = ms.Chunk([], [])
    chunk.crs = Crs()
    chunk.transform.matrix = types.SimpleNamespace(mulp=lambda p: p)
    marker = ms.Marker("A", position=ms.Vector(10.03, 20.0, 5.04))
    marker.reference.location = ms.Vector(10.0, 20.0, 5.0)
    out = report._fallback_errors(chunk, marker)
    assert out["x"] == pytest.approx(0.03) and out["z"] == pytest.approx(0.04)
    assert out["total"] == pytest.approx(0.05)
    marker.position = None
    assert report._fallback_errors(chunk, marker) is None


def test_fallback_is_used_when_msapi_has_no_marker_errors(ms, monkeypatch):
    chunk, _fake = make_world(ms, [("A", 2, None)])
    monkeypatch.delattr(msapi, "marker_errors", raising=False)
    marker = report.collect_data(chunk)["markers"][0]
    assert marker["total"] is None and marker["projections"] == 2


# --------------------------------------------------------------------------
# Highlighting and histogram
# --------------------------------------------------------------------------

def test_over_threshold():
    assert report.over_threshold(0.3, 0.2) is True
    assert report.over_threshold(-0.3, 0.2) is True
    assert report.over_threshold(0.1, 0.2) is False
    assert report.over_threshold(0.3, None) is False
    assert report.over_threshold(None, 0.2) is False


def test_projection_bins_one_bar_per_value_when_small():
    bins = report.projection_bins([0, 0, 2, 3, 3, 3])
    assert [b[0] for b in bins] == ["0", "1", "2", "3"]
    assert [b[1] for b in bins] == [2, 0, 1, 3]
    assert [b[2] for b in bins] == [True, False, False, False]


def test_projection_bins_groups_when_large():
    counts = list(range(0, 41))
    bins = report.projection_bins(counts, max_bins=12)
    assert len(bins) <= 12
    assert sum(b[1] for b in bins) == len(counts)
    assert bins[0][0].startswith("0-")


def test_projection_bins_empty():
    assert report.projection_bins([]) == []


def test_nice_axis_covers_the_data():
    for value in (0.003, 0.0456, 0.2, 0.87, 3.2, 120.0):
        top, step = report._nice_top(value, 4)
        assert top >= value and step > 0
        assert top / step <= 8.0001
    assert report._nice_top(0)[0] > 0
    assert report._tick_decimals(0.05) == 2
    assert report._tick_decimals(0.25) == 2
    assert report._tick_decimals(2) == 0


# --------------------------------------------------------------------------
# Pagination
# --------------------------------------------------------------------------

def test_table_plan_keeps_every_row():
    for n in (0, 1, 30, 33, 34, 45, 46, 90, 200, 1000):
        plan, _inline = report._table_plan(n, 841.89)
        assert sum(plan) == n
        assert all(rows <= 46 for rows in plan)


def test_thirty_markers_fit_on_one_table_page_with_the_histogram():
    plan, inline = report._table_plan(30, 841.89)
    assert plan == [30] and inline is True
    assert report._table_plan(32, 841.89) == ([32], True)
    assert report._table_plan(33, 841.89) == ([33], False)


def test_a_long_table_pushes_the_histogram_to_its_own_page_when_full():
    plan, inline = report._table_plan(45, 841.89)
    assert inline is False and plan == [45]


def test_page_count_matches_the_plan(world):
    data = report.collect_data(world)
    assert report.page_count(data, None) == 2
    assert report.page_count(data, {"panel": object()}) == 3
    assert report.page_count(data, {"crops": [{"image": 1}]}) == 3
    assert report.page_count(data, {"photo": None, "crops": []}) == 2


# --------------------------------------------------------------------------
# CSV
# --------------------------------------------------------------------------

def _ai_items(n, prefix="IMG"):
    return [{"image": object(), "camera": "{}_{:04d}".format(prefix, k), "caption": "0.90",
             "color": "#000", "ring": "#000"} for k in range(n)]


def test_ai_pages_are_empty_without_picks():
    assert report._ai_plan([]) == []
    assert report._ai_plan([{"label": "PS_1", "items": []}]) == []


def test_ai_plan_lays_thumbnails_in_rows_under_a_marker_title():
    pages = report._ai_plan([{"label": "PS_1", "items": _ai_items(20)}])
    assert len(pages) == 1
    assert pages[0][0][0] == "head" and pages[0][0][2] is False
    rows = [e[1] for e in pages[0] if e[0] == "row"]
    cols = report._ai_columns(report.A4_W)
    assert cols == 8 and [len(r) for r in rows] == [8, 8, 4]


def test_ai_plan_shares_a_page_between_small_markers():
    sections = [{"label": "PS_{}".format(k), "items": _ai_items(5)} for k in range(1, 4)]
    pages = report._ai_plan(sections)
    assert len(pages) == 1
    assert [e[1]["label"] for e in pages[0] if e[0] == "head"] == ["PS_1", "PS_2", "PS_3"]


def test_ai_plan_continues_a_long_marker_on_the_next_page():
    pages = report._ai_plan([{"label": "PS_1", "items": _ai_items(150)}])
    assert len(pages) >= 2
    heads = [e for page in pages for e in page if e[0] == "head"]
    assert heads[0][2] is False and all(h[2] is True for h in heads[1:])
    assert sum(len(e[1]) for page in pages for e in page if e[0] == "row") == 150


def test_ai_plan_never_overflows_a_page():
    pages = report._ai_plan([{"label": "PS_{}".format(k), "items": _ai_items(37)} for k in range(1, 9)])
    row_h = report.AI_CELL + report.AI_CAPTION_H + report.AI_GAP
    for page in pages:
        y = report.TABLE_TOP + 4.0
        for entry in page:
            y += report.AI_HEAD_H if entry[0] == "head" else row_h
        assert y <= report.A4_H - 50.0 + 1e-6


def test_page_count_includes_the_ai_pages(world):
    data = report.collect_data(msapi.chunk())
    base = report.page_count(data, None)
    picks = [{"label": "PS_1", "items": _ai_items(150)}]
    assert report.page_count(data, {"ai_picks": picks}) == base + len(report._ai_plan(picks))
    assert report.page_count(data, {"ai_picks": []}) == base


def test_ai_sections_keep_only_live_picks_with_their_confidence(ms, monkeypatch):
    from aerotools.tools.fastgcp import review
    cams = [ms.Camera(k, ms.Vector(0, 0, 0)) for k in range(1, 5)]
    marker = ms.Marker("PS_2", projections={cams[0]: ms.Marker.Projection(ms.Vector(1, 2, 0), False),
                                            cams[1]: ms.Marker.Projection(ms.Vector(1, 2, 0), True),
                                            cams[2]: ms.Marker.Projection(ms.Vector(1, 2, 0), False)})
    ms.app.document.chunk = ms.Chunk(cams, [marker])

    def entry(cam, state, conf):
        return {"marker": marker, "label": "PS_2", "camera": cam, "camera_label": cam.label,
                "state": state, "conf": conf, "x": 1.0, "y": 2.0}

    log = [entry(cams[0], review.KEPT, 0.91), entry(cams[1], review.REFINED, 0.80),
           entry(cams[2], review.REMOVED, 0.70), entry(cams[3], review.KEPT, 0.60)]
    monkeypatch.setattr(report, "_ai_thumb", lambda e, excluded=False: object())
    sections = report.ai_sections(types.SimpleNamespace(aigcp_log=log))
    assert [s["label"] for s in sections] == ["PS_2"]
    items = sections[0]["items"]
    # the excluded photo appears with its red cross; a kept one without a projection is absent
    assert [i["camera"] for i in items] == [cams[0].label, cams[1].label, cams[2].label]
    assert items[0]["caption"] == report.fmt_num(0.91, 2) and items[1]["caption"] == "manual"
    assert items[2]["caption"] == "excluded" and items[2]["ring"] == report.AI_RED
    assert sections[0]["info"]["excluded"] == 1
    assert "1 photo(s) excluded" in report.ai_info_parts(sections[0]["info"])


def test_ai_info_parts_describe_the_marker():
    info = {"total": 0.0123, "x": 0.003, "y": -0.002, "z": 0.011, "median_px": 0.84, "max_px": 2.1,
            "conf_mean": 0.9234, "conf_min": 0.9, "projections": 40, "gcp": True, "accuracy": 0.005}
    parts = report.ai_info_parts(info)
    text = " | ".join(parts)
    assert "0.012" in text and "0.8" in text and "2.1" in text      # 3D error, median offset, maximum
    assert "0.92" in text and "0.90" in text                        # mean and minimum confidence
    assert "40" in text and "GCP" in text and "0.005" in text
    assert len(parts) == 5


def test_ai_info_parts_say_when_it_is_a_check_point_and_skip_the_unknown():
    parts = report.ai_info_parts({"gcp": False})
    assert len(parts) == 1 and "Check point" in parts[0]
    assert report.ai_info_parts({}) == []
    assert report.ai_info_parts({"gcp": None, "accuracy": 0.01}) == []


def test_marker_reference_info_reads_gcp_use_and_accuracy(ms):
    marker = ms.Marker("M")
    marker.reference.enabled = True
    marker.reference.accuracy = ms.Vector(0.005, 0.005, 0.02)
    assert msapi.marker_reference_info(marker) == (True, 0.02)
    marker.reference.enabled = False
    marker.reference.accuracy = 0.01
    assert msapi.marker_reference_info(marker) == (False, 0.01)
    assert msapi.marker_reference_info(ms.Marker("N")) == (None, None)


def test_marker_reference_accuracy_falls_back_on_the_chunk_default(ms):
    marker = ms.Marker("M")
    marker.reference.enabled = True
    marker.reference.accuracy = None
    chunk = ms.Chunk([], [marker])
    chunk.marker_location_accuracy = ms.Vector(0.005, 0.005, 0.005)
    assert msapi.marker_reference_info(marker, chunk) == (True, 0.005)
    assert msapi.marker_reference_info(marker) == (True, None)


def test_ai_sections_carry_the_marker_information(ms, monkeypatch):
    from aerotools.tools.fastgcp import review
    cams = [ms.Camera(k, ms.Vector(0, 0, -10)) for k in (1, 2)]
    marker = ms.Marker("PS_5", position=ms.Vector(0, 0, 0), projections={
        cams[0]: ms.Marker.Projection(ms.Vector(2001.0, 1500.0, 0), False),
        cams[1]: ms.Marker.Projection(ms.Vector(2000.0, 1503.0, 0), False)})
    marker.reference.enabled = True
    marker.reference.accuracy = 0.005
    ms.app.document.chunk = ms.Chunk(cams, [marker])
    log = [{"marker": marker, "label": "PS_5", "camera": cams[0], "camera_label": cams[0].label,
            "state": review.KEPT, "conf": 0.90, "x": 2001.0, "y": 1500.0},
           {"marker": marker, "label": "PS_5", "camera": cams[1], "camera_label": cams[1].label,
            "state": review.KEPT, "conf": 0.94, "x": 2000.0, "y": 1503.0}]
    data = {"markers": [{"label": "PS_5", "total": 0.02, "x": 0.01, "y": 0.01, "z": 0.015, "projections": 12}]}
    monkeypatch.setattr(report, "_ai_thumb", lambda e, excluded=False: object())
    info = report.ai_sections(types.SimpleNamespace(aigcp_log=log), data)[0]["info"]
    assert info["total"] == 0.02 and info["projections"] == 12
    assert info["median_px"] == pytest.approx(2.0) and info["max_px"] == pytest.approx(3.0)   # offsets of 1 px and 3 px
    assert info["conf_mean"] == pytest.approx(0.92) and info["conf_min"] == pytest.approx(0.90)
    assert info["gcp"] is True and info["accuracy"] == pytest.approx(0.005)


def test_ai_sections_skip_unreadable_photos(ms, monkeypatch):
    from aerotools.tools.fastgcp import review
    cam = ms.Camera(1, ms.Vector(0, 0, 0))
    marker = ms.Marker("PS_1", projections={cam: ms.Marker.Projection(ms.Vector(1, 2, 0), False)})
    ms.app.document.chunk = ms.Chunk([cam], [marker])
    log = [{"marker": marker, "label": "PS_1", "camera": cam, "camera_label": cam.label,
            "state": review.KEPT, "conf": 0.9, "x": 1.0, "y": 2.0}]
    monkeypatch.setattr(report, "_ai_thumb", lambda e, excluded=False: None)
    assert report.ai_sections(types.SimpleNamespace(aigcp_log=log)) == []
    assert report.ai_sections(types.SimpleNamespace()) == []


def test_csv_rows_without_reference(world):
    rows = report.csv_rows(report.collect_data(world))
    assert rows[0] == ["ID", "Error X (m)", "Error Y (m)", "Error Z (m)",
                       "Total error (m)", "Projections"]
    assert rows[1][:5] == ["GCP01", "0.010", "0.020", "0.030", "0.037"]
    assert rows[1][5] == "4"
    assert rows[4] == ["GCP04", "", "", "", "", "0"]
    assert len(rows) == 6


def test_csv_rows_with_reference_columns(ms, monkeypatch):
    chunk, fake = make_world(ms, [("A", 2, (0.1, 0.2, 0.3)), ("B", 2, (0.1, 0.2, 0.3))])
    monkeypatch.setattr(msapi, "marker_errors", fake, raising=False)
    chunk.markers[0].reference.location = ms.Vector(500000.1234, 4700000.5, 12.3456)
    rows = report.csv_rows(report.collect_data(chunk))
    assert rows[0][-3:] == ["Reference X", "Reference Y", "Reference Z"]
    assert rows[1][-3:] == ["500000.123", "4700000.500", "12.346"]
    assert rows[2][-3:] == ["", "", ""]


def test_csv_geographic_reference_keeps_eight_decimals(ms, monkeypatch):
    chunk, fake = make_world(ms, [("A", 2, (0.1, 0.2, 0.3))])
    monkeypatch.setattr(msapi, "marker_errors", fake, raising=False)
    chunk.markers[0].reference.location = ms.Vector(9.123456789, 42.5, 120.0)
    rows = report.csv_rows(report.collect_data(chunk))
    assert rows[1][-3:] == ["9.12345679", "42.50000000", "120.000"]


def test_write_csv_is_excel_friendly(world, tmp_path, monkeypatch):
    chunk = world
    chunk.markers[0].label = "GCP,01 \"north\""
    monkeypatch.setattr(msapi, "marker_errors", lambda c, m: {
        "x": 0.010, "y": 0.020, "z": 0.030, "total": 0.0374}, raising=False)
    path = tmp_path / "t.csv"
    report.write_csv(report.collect_data(chunk), str(path))
    raw = path.read_bytes()
    assert raw[:3] == b"\xef\xbb\xbf"
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    text = raw.decode("utf-8-sig")
    lines = text.split("\r\n")
    assert lines[0] == "ID,Error X (m),Error Y (m),Error Z (m),Total error (m),Projections"
    assert lines[1].startswith('"GCP,01 ""north""",0.010,0.020,0.030,0.037,4')
    assert lines[2] == "GCP02,0.010,0.020,0.030,0.037,3"


# --------------------------------------------------------------------------
# Export orchestration (no real Qt)
# --------------------------------------------------------------------------

@pytest.fixture
def ui(monkeypatch, tmp_path):
    shown = []
    monkeypatch.setattr(report, "show_message",
                        lambda parent, title, text, warning=False: shown.append((title, text)))
    monkeypatch.setattr(report, "offer_open",
                        lambda parent, title, path: shown.append(("open", path)) or False)
    monkeypatch.setattr(report, "collect_images", lambda state, data: {})
    state = types.SimpleNamespace(panel=types.SimpleNamespace(status=[]))
    state.panel.set_status = state.panel.status.append
    return types.SimpleNamespace(shown=shown, state=state, tmp=tmp_path)


def test_export_pdf_without_chunk_says_so(ms, ui):
    ms.app.document.chunk = None
    assert report.export_pdf(ui.state) is None
    assert ui.shown and "chunk" in ui.shown[0][1].lower()


def test_export_pdf_without_markers_says_so(ms, ui):
    ms.app.document.chunk = ms.Chunk([], [])
    assert report.export_pdf(ui.state) is None
    assert "no markers" in ui.shown[0][1].lower()


def test_export_pdf_cancel_is_silent(world, ui, monkeypatch):
    monkeypatch.setattr(report, "ask_save_path", lambda *a: "")
    assert report.export_pdf(ui.state) is None
    assert ui.shown == []


def test_export_pdf_proposes_a_good_default_name_and_adds_the_extension(world, ui, monkeypatch, ms):
    ms.app.document.path = str(ui.tmp / "site.psx")
    seen = {}

    def ask(parent, caption, default, flt):
        seen["default"] = default
        return str(ui.tmp / "no_extension")

    monkeypatch.setattr(report, "ask_save_path", ask)
    calls = []

    def fake_build(data, path, images):
        calls.append(path)
        return 3

    monkeypatch.setattr(report, "build_pdf", fake_build)
    result = report.export_pdf(ui.state)
    assert result == str(ui.tmp / "no_extension.pdf") == calls[0]
    default = seen["default"]
    assert default.startswith(str(ui.tmp))
    assert default.endswith("_GCP_report_Survey_Zurich_n2.pdf")
    assert ("open", result) in ui.shown
    assert ui.state.panel.status == ["PDF report exported"]


def test_export_pdf_logs_the_result(world, ui, monkeypatch):
    from aerotools import journal
    journal.clear()
    monkeypatch.setattr(report, "ask_save_path", lambda *a: str(ui.tmp / "r.pdf"))
    monkeypatch.setattr(report, "build_pdf", lambda data, path, images: 3)
    report.export_pdf(ui.state)
    assert any("PDF report written" in line and "3 pages" in line for line in journal.lines())


def test_export_pdf_failure_never_raises(world, ui, monkeypatch):
    from aerotools import journal
    journal.clear()
    monkeypatch.setattr(report, "ask_save_path", lambda *a: str(ui.tmp / "r.pdf"))

    def boom(*args):
        raise PermissionError("locked")

    monkeypatch.setattr(report, "build_pdf", boom)
    assert report.export_pdf(ui.state) is None
    assert any("PDF export failed" in title for title, _t in ui.shown)
    assert any("Write denied" in text for _t, text in ui.shown)
    assert any("PDF export failed" in line for line in journal.lines())
    assert ui.state.panel.status[-1] == "PDF export failed"


def test_export_pdf_survives_a_broken_dialog(world, ui, monkeypatch):
    def boom(*args):
        raise RuntimeError("dead window")

    monkeypatch.setattr(report, "ask_save_path", boom)
    assert report.export_pdf(ui.state) is None


def test_export_pdf_survives_a_state_without_panel(world, ui, monkeypatch):
    monkeypatch.setattr(report, "ask_save_path", lambda *a: str(ui.tmp / "r.pdf"))
    monkeypatch.setattr(report, "build_pdf", lambda data, path, images: 2)
    assert report.export_pdf(types.SimpleNamespace()) == str(ui.tmp / "r.pdf")


def test_export_csv_writes_the_file(world, ui, monkeypatch):
    target = ui.tmp / "table"
    monkeypatch.setattr(report, "ask_save_path", lambda *a: str(target))
    result = report.export_csv(ui.state)
    assert result == str(target) + ".csv"
    text = (ui.tmp / "table.csv").read_bytes().decode("utf-8-sig")
    assert text.startswith("ID,Error X (m)")
    assert text.count("\r\n") == 6
    assert ui.state.panel.status == ["CSV table exported"]


def test_export_csv_failure_never_raises(world, ui, monkeypatch):
    monkeypatch.setattr(report, "ask_save_path", lambda *a: str(ui.tmp / "absent" / "t.csv"))
    assert report.export_csv(ui.state) is None
    assert any("CSV export failed" in title for title, _t in ui.shown)


def test_explain_gives_readable_text():
    assert "Write denied" in report._explain(PermissionError())
    assert report._explain(RuntimeError("boom")) == "boom"
    assert report._explain(RuntimeError()) == "RuntimeError"
