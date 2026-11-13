"""UI kit style sheet and scaling: built once and cached per scale, no placeholder left, every role styled, px() rounds."""

import pytest

from aerotools.uikit import metrics, style, tokens


@pytest.fixture(autouse=True)
def _scale():
    metrics.set_scale(1.0)
    yield
    metrics.set_scale(None)


def test_stylesheet_is_complete_and_balanced():
    sheet = style.stylesheet()
    assert "$" not in sheet
    assert sheet.count("{") == sheet.count("}")
    for role in ("primary", "quiet", "danger"):
        assert 'role="{}"'.format(role) in sheet
    for kind in ("info", "success", "warning", "danger"):
        assert 'kind="{}"'.format(kind) in sheet
    for name in tokens.TYPE:
        if name not in ("body",):
            assert 'type="{}"'.format(name) in sheet
    assert "QToolTip" in sheet


def test_stylesheet_is_built_once_per_scale():
    first = style.stylesheet()
    assert style.stylesheet() is first
    metrics.set_scale(1.5)
    second = style.stylesheet()
    assert second is not first and style.stylesheet() is second
    metrics.set_scale(1.0)
    assert style.stylesheet() is first


def test_sizes_scale_with_the_factor():
    metrics.set_scale(1.0)
    one = style.stylesheet()
    metrics.set_scale(1.5)
    big = style.stylesheet()
    assert "font-size: 13px" in one and "font-size: 20px" in big      # 13 * 1.5 = 19.5 rounds to 20
    assert "border-radius: 10px" in one and "border-radius: 15px" in big


def test_px_rounds_and_never_returns_zero_for_a_positive_size():
    metrics.set_scale(1.25)
    assert metrics.px(32) == 40 and metrics.px(1) == 1 and metrics.px(0) == 0
    metrics.set_scale(1.5)
    assert metrics.px(4) == 6 and metrics.px(6) == 9


def test_scale_without_an_application_is_one():
    metrics.set_scale(None)
    assert metrics.scale() >= 1.0


def test_one_control_height_is_used_everywhere():
    sheet = style.stylesheet()
    assert "min-height: {}px".format(tokens.CONTROL_H - 2) in sheet


def test_older_object_names_are_still_styled():
    sheet = style.stylesheet()
    for name in ("aeroRun", "aeroGhost", "aeroYes", "aeroNo", "aeroOption", "aeroGrid", "aeroMeta", "aeroStat"):
        assert name in sheet, name


def test_popup_sheets_exist_for_widgets_outside_a_window():
    assert "QToolTip" in style.tooltip_stylesheet() and "QMenu" not in style.tooltip_stylesheet()
    assert "QMenu" in style.menu_stylesheet() and "QToolTip" in style.menu_stylesheet()
    assert "QTableWidget" in style.table_stylesheet()
    assert "uikitPopover" in style.popover_stylesheet()
