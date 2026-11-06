"""Formats et tris du tableau des marqueurs (fonctions pures de panel.py)."""

from aerotools import i18n
from aerotools.tools.fastgcp import panel


def _row(label, proj=0, x=None, y=None, z=None):
    return {"label": label, "proj": proj, "x": x, "y": y, "z": z}


def _labels(rows):
    return [row["label"] for row in rows]


def test_natural_order_puts_gcp2_before_gcp10():
    labels = ["GCP10", "GCP2", "gcp1", "GCP_A", "GCP11", "CP3"]
    ordered = sorted(labels, key=panel.natural_key)
    assert ordered == ["CP3", "gcp1", "GCP2", "GCP10", "GCP11", "GCP_A"]


def test_format_error_is_short_and_signed():
    assert panel.format_error(0.0123) == "0.012"
    assert panel.format_error(-0.0456) == "-0.046"
    assert panel.format_error(0) == "0.000"
    assert panel.format_error(-0.0003) == "0.000"
    assert panel.format_error(12.3456) == "12.346"
    assert panel.format_error(123.456) == "123.46"
    assert panel.format_error(12345.67) == "12345.7"


def test_format_error_without_value_is_a_dash():
    for value in (None, "abc", float("nan"), float("inf")):
        assert panel.format_error(value) == "–"


def test_default_order_is_the_project_order():
    rows = [_row("GCP10"), _row("GCP2"), _row("GCP1")]
    assert _labels(panel.sort_rows(rows, None)) == ["GCP10", "GCP2", "GCP1"]
    assert _labels(panel.sort_rows(rows, "inconnue", True)) == ["GCP10", "GCP2", "GCP1"]


def test_sort_by_id_is_natural_both_ways():
    rows = [_row("GCP10"), _row("GCP2"), _row("GCP1")]
    assert _labels(panel.sort_rows(rows, "id", False)) == ["GCP1", "GCP2", "GCP10"]
    assert _labels(panel.sort_rows(rows, "id", True)) == ["GCP10", "GCP2", "GCP1"]


def test_sort_by_error_uses_absolute_value():
    rows = [_row("A", x=-0.05), _row("B", x=0.01), _row("C", x=0.03), _row("D", x=-0.02)]
    assert _labels(panel.sort_rows(rows, "x", False)) == ["B", "D", "C", "A"]
    assert _labels(panel.sort_rows(rows, "x", True)) == ["A", "C", "D", "B"]


def test_rows_without_error_stay_at_the_end_in_both_directions():
    rows = [_row("A"), _row("B", z=0.2), _row("C"), _row("D", z=-0.1)]
    assert _labels(panel.sort_rows(rows, "z", False)) == ["D", "B", "A", "C"]
    assert _labels(panel.sort_rows(rows, "z", True)) == ["B", "D", "A", "C"]


def test_sort_by_projection_count_is_numeric_and_stable():
    rows = [_row("A", proj=10), _row("B", proj=2), _row("C", proj=2), _row("D", proj=0)]
    assert _labels(panel.sort_rows(rows, "proj", False)) == ["D", "B", "C", "A"]
    assert _labels(panel.sort_rows(rows, "proj", True)) == ["A", "B", "C", "D"]


def test_sort_does_not_modify_its_input():
    rows = [_row("B", x=2.0), _row("A", x=1.0)]
    panel.sort_rows(rows, "x")
    assert _labels(rows) == ["B", "A"]


def test_columns_are_exactly_the_five_asked_for():
    # COLUMNS and EXPORTS hold text keys: they are read through the catalog.
    assert panel.column_titles() == ["ID", "Error X", "Error Y", "Error Z", "Proj."]
    assert [key for _title, key, _width in panel.COLUMNS] == ["id", "x", "y", "z", "proj"]
    assert panel.MIN_PANEL_WIDTH >= 420
    assert [i18n.t(title) for _key, title in panel.EXPORTS] == ["PDF report", "CSV table"]
