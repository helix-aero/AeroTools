"""Aero-Exports: the number typed in the value field next to a slider (pure part; the field itself is tested by tests_qt)."""

import pytest

from aerotools import controls


@pytest.mark.parametrize("text,low,high,modulo,expected", [
    ("45", 0, 360, 360, 45),
    ("315°", 0, 360, 360, 315),
    ("  35 %", 0, 100, None, 35),
    ("12,6", 0, 100, None, 13),                 # comma accepted, rounded to a whole number
    ("360", 0, 360, 360, 0),                    # a compass wraps
    ("359.6", 0, 360, 360, 0),
    ("5", 5, 85, None, 5),
    ("85deg", 5, 85, None, 85),
])
def test_a_valid_number_is_read(text, low, high, modulo, expected):
    assert controls.parse_typed(text, low, high, modulo) == expected


@pytest.mark.parametrize("text,low,high", [
    ("", 0, 100), ("abc", 0, 100), ("4", 5, 85), ("86", 5, 85), ("-1", 0, 100), ("101 %", 0, 100), ("1e999", 0, 100), ("nan", 0, 100),
    ("12 13", 0, 100),
])
def test_a_wrong_number_is_refused(text, low, high):
    assert controls.parse_typed(text, low, high) is None
