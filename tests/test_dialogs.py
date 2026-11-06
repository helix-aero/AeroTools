"""Custom Yes / No boxes: button order (Windows convention: Yes on the left, No on the right)."""

from aerotools import i18n
from aerotools.tools.fastgcp import dialogs


def test_ask_puts_yes_on_the_left_and_no_on_the_right(monkeypatch):
    seen = {}

    def fake_build(parent, title, text, buttons):
        seen["buttons"] = buttons
        return object(), {"value": True}

    monkeypatch.setattr(dialogs, "_build", fake_build)
    monkeypatch.setattr(dialogs, "_run", lambda dialog: 0)
    assert dialogs.ask(None, "t", "x") is True
    assert [caption for caption, _value, _name in seen["buttons"]] == [i18n.t("dialogs.yes"), i18n.t("dialogs.no")]
    assert [value for _caption, value, _name in seen["buttons"]] == [True, False]


def test_ask_answers_no_when_closed(monkeypatch):
    monkeypatch.setattr(dialogs, "_build", lambda *args: (object(), {"value": None}))
    monkeypatch.setattr(dialogs, "_run", lambda dialog: 0)
    assert dialogs.ask(None, "t", "x") is False
