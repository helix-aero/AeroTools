"""Fenetre Residual check : tous les marqueurs, tries du plus urgent au moins urgent."""
import pytest

from aerotools.tools.fastgcp import qc


def row(label, proj, x=0.0, y=0.0, z=0.0):
    return {"label": label, "proj": proj, "x": x, "y": y, "z": z}


def test_every_marker_is_listed_with_its_status():
    rows = [row("good", 40, 0.004, 0.002, 0.003), row("bad", 12, 0.09, 0.04, 0.1), row("few", 2, 0.01, 0.0, 0.0),
            row("never", 0, None, None, None), row("late", 9, None, None, None)]
    items = qc.table(rows)
    by_label = {item["label"]: item["status"] for item in items}
    assert by_label == {"good": qc.OK, "bad": qc.RESIDUAL, "few": qc.FEW, "never": qc.FEW, "late": qc.NONE}


def test_a_marker_pinned_90_times_is_listed_as_ok():
    items = qc.table([row("PS_12", 90, 0.007, -0.011, 0.003), row("PS_1", 47, -0.014, -0.003, 0.001)])
    assert [item["status"] for item in items] == [qc.OK, qc.OK]
    assert items[0]["proj"] == 90 or items[1]["proj"] == 90


def test_urgent_markers_come_first():
    rows = [row("ok_small", 20, 0.001), row("ok_big", 20, 0.012), row("res_small", 20, 0.08), row("res_big", 20, 0.2),
            row("few1", 1), row("few0", 0), row("none", 9, None, None, None)]
    order = [item["label"] for item in qc.table(rows)]
    assert order == ["res_big", "res_small", "few0", "few1", "none", "ok_big", "ok_small"]


def test_role_follows_the_enabled_map():
    items = qc.table([row("a", 9, 0.01), row("b", 9, 0.02), row("c", 9, 0.03)], {"a": True, "b": False})
    roles = {item["label"]: item["role"] for item in items}
    assert roles == {"a": True, "b": False, "c": None}


def test_group_rms_separates_control_and_check_points():
    rows = [row("a", 9, 0.03, 0.0, 0.0), row("b", 9, 0.04, 0.0, 0.0), row("c", 9, 0.0, 0.0, 0.05),
            row("d", 9, None, None, None), row("e", 9, 0.5)]
    items = qc.table(rows, {"a": True, "b": True, "c": False, "d": False})
    control, check = qc.group_rms(items)
    assert control[1] == 2 and control[0] == pytest.approx(((0.03 ** 2 + 0.04 ** 2) / 2) ** 0.5)
    assert check == (pytest.approx(0.05), 1)


def test_group_rms_ignores_markers_with_too_few_photos():
    rows = [row("a", 9, 0.02), row("unpinned", 0, 0.0, 0.0, 0.0), row("two", 2, 0.9)]
    items = qc.table(rows, {"a": True, "unpinned": True, "two": False})
    control, check = qc.group_rms(items)
    assert control == (pytest.approx(0.02), 1) and check is None


def test_group_rms_with_no_data_is_none():
    assert qc.group_rms(qc.table([])) == (None, None)
    assert qc.group_rms(qc.table([row("a", 9, 0.01)])) == (None, None)
