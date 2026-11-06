"""Texts of AeroTools: lookup, fallback to the key, fields, number / date / CSV formats."""

import datetime

import pytest

from aerotools import i18n


@pytest.fixture
def fake_catalog(monkeypatch):
    """A tiny catalog in place of the real one: the fallbacks are under control."""
    texts = {"a": "Hello {name}", "x": "X {n}"}
    monkeypatch.setattr(i18n, "_texts", texts)
    return texts


# --------------------------------------------------------------------------
# t(): lookup and robustness
# --------------------------------------------------------------------------

def test_t_returns_the_text_of_the_catalog():
    assert i18n.t("panel.export") == "Export"


def test_t_falls_back_to_the_key(fake_catalog):
    assert i18n.t("a", name="Lea") == "Hello Lea"
    assert i18n.t("unknown.key") == "unknown.key"


def test_t_fills_fields():
    assert i18n.t("undo.done", label="GCP3") == "Undone: GCP3"


def test_t_never_fails_with_incomplete_or_wrong_fields(fake_catalog):
    assert i18n.t("a") == "Hello {name}"
    assert i18n.t("a", other=1) == "Hello {name}"
    assert i18n.t("x", n=None) == "X None"
    assert i18n.t("unknown.key", n=3) == "unknown.key"


def test_t_survives_a_broken_format_string(fake_catalog):
    fake_catalog["broken"] = "Value {0} and {n:d} and {obj.attr}"
    assert i18n.t("broken", n="not an integer") == "Value {0} and {n:d} and {obj.attr}"
    fake_catalog["brace"] = "Text { alone"
    assert i18n.t("brace", n=1) == "Text { alone"


def test_t_with_a_non_string_key_does_not_raise():
    assert i18n.t(42) == "42"
    assert i18n.t(None) == "None"


def test_has_tells_whether_a_key_exists():
    assert i18n.has("panel.export")
    assert not i18n.has("panel.does.not.exist")


def test_texts_and_formats_are_copies():
    i18n.texts()["panel.export"] = "changed"
    i18n.formats()["decimal"] = ","
    assert i18n.t("panel.export") == "Export"
    assert i18n.decimal_separator() == "."


# --------------------------------------------------------------------------
# Formats
# --------------------------------------------------------------------------

def test_fmt_number_uses_a_point():
    assert i18n.fmt_number(0.12345) == "0.123"
    assert i18n.fmt_number(1234567.891, 2) == "1234567.89"


def test_fmt_number_has_no_thousands_separator_and_no_negative_zero():
    assert i18n.fmt_number(1000000) == "1000000.000"
    assert i18n.fmt_number(-0.0004) == "0.000"
    assert i18n.fmt_number(-0.0456) == "-0.046"
    assert i18n.fmt_number(-12.5, 1) == "-12.5"


def test_fmt_number_decimals_and_integers():
    assert i18n.fmt_number(12, 0) == "12"
    assert i18n.fmt_number(2.5, 0) in ("2", "3")
    assert i18n.fmt_number(4.56, 1) == "4.6"
    assert i18n.fmt_number("3.5", 1) == "3.5"


def test_fmt_number_gives_an_empty_text_when_unknown():
    for value in (None, float("nan"), float("inf"), float("-inf"), "abc", [], object()):
        assert i18n.fmt_number(value) == ""


def test_fmt_date_is_iso():
    day = datetime.date(2026, 11, 3)
    assert i18n.fmt_date(day) == "2026-11-03"
    assert i18n.fmt_date(datetime.datetime(2026, 11, 3, 17, 5)) == "2026-11-03"


def test_fmt_date_leaves_anything_else_alone():
    assert i18n.fmt_date("yesterday") == "yesterday"
    assert i18n.fmt_date(None) == "None"


def test_csv_delimiter_is_a_comma():
    assert i18n.csv_delimiter() == ","
