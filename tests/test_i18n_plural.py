"""Plurals: `{n} {n|file|files}` in the catalog, `i18n.plural` and `i18n.tn` in code. No "(s)" and no "1 files" anywhere."""

import re

from aerotools import i18n
from aerotools.lang import en


def test_inline_plural_follows_the_count_and_zero_is_plural():
    assert i18n.plural(1, "file", "files") == "file" and i18n.plural(0, "file", "files") == "files"
    assert i18n.plural(2, "file", "files") == "files" and i18n.plural("1", "file", "files") == "file"
    assert i18n.plural(None, "file", "files") == "files"


def test_a_catalog_text_has_the_right_form_for_one_and_for_several():
    assert i18n.t("space.ask_delete", n=1) == "Delete 1 project?"
    assert i18n.t("space.ask_delete", n=2) == "Delete 2 projects?"
    assert "1 project was" in i18n.t("space.failed_many", n=1) and "2 projects were" in i18n.t("space.failed_many", n=2)


def test_a_missing_count_leaves_the_marker_instead_of_guessing():
    assert i18n.t("update.status_done") == "Up to date. {n} {n|file|files} checked."


def test_tn_picks_between_two_keys(monkeypatch):
    monkeypatch.setitem(i18n._texts, "x.one", "one thing")
    monkeypatch.setitem(i18n._texts, "x.other", "{n} things")
    assert i18n.tn("x.one", "x.other", 1) == "one thing" and i18n.tn("x.one", "x.other", 4) == "4 things"


def test_the_catalog_has_no_optional_plural():
    for key, text in en.TEXTS.items():
        assert "(s)" not in text and "(es)" not in text, key


def test_every_inline_plural_names_a_field_the_text_can_receive():
    for key, text in en.TEXTS.items():
        for name, one, other in re.findall(r"\{(\w+)\|([^{}|]*)\|([^{}|]*)\}", text):
            assert one != other, key
