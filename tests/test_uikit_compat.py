"""The names tools already import from theme.py, ui.py and dialogs.py keep working, as thin aliases onto the kit."""

import inspect

from aerotools import dialogs, theme, ui
from aerotools.uikit import tokens

THEME_NAMES = ["GROUND_0", "GROUND_1", "GROUND_2", "GROUND_3", "GROUND_4", "FG_0", "FG_1", "FG_2", "FG_3", "FG_4", "BORDER_FAINT",
               "BORDER_SOFT", "BORDER_STRONG", "FIELD_BG", "OFF_TEXT", "GREEN", "GREEN_BRIGHT", "GREEN_ON", "GREEN_SOFT", "GREEN_DEEP",
               "WINDOW", "PANE", "PANE_HEAD", "BTN", "BTN_HOVER", "ORANGE", "ORANGE_BRIGHT", "ORANGE_DEEP", "BLUE", "LOGO_0", "LOGO_1",
               "LOGO_2", "PAD", "PAD_S", "PAD_L", "RADIUS", "RADIUS_S", "ARROW_DOWN", "CHECK_MARK", "RADIO_OFF", "RADIO_ON"]


# Constants and functions only some tools need are stripped from a release tree that does not ship those tools.
OPTIONAL_NAMES = ("GROUND_0", "text_family")


def test_every_theme_constant_survives():
    for name in THEME_NAMES:
        if name in OPTIONAL_NAMES and not hasattr(theme, name):
            continue
        assert isinstance(getattr(theme, name), (str, int)), name


def test_theme_colours_come_from_the_kit_tokens():
    assert theme.GREEN == tokens.ACCENT and theme.GROUND_1 == tokens.BG and theme.FG_1 == tokens.TEXT
    assert (theme.PAD_S, theme.PAD, theme.PAD_L) == (tokens.XS, tokens.S, tokens.L)


def test_theme_functions_survive():
    assert theme.load_fonts(None) == (tokens.FONT_FAMILY, tokens.FONT_FAMILY)
    assert theme.title_family() == tokens.FONT_FAMILY
    if hasattr(theme, "text_family"):
        assert theme.text_family() == tokens.FONT_FAMILY
    assert "#aeroPanel" in theme.stylesheet() and "QToolTip" in theme.tooltip_stylesheet() and "QMenu" in theme.menu_stylesheet()
    assert theme.stylesheet() is theme.stylesheet()
    for name in ("eyebrow", "gradient_rule", "separator"):
        assert callable(getattr(theme, name))


def test_ui_names_survive():
    for name in ("styled_root", "dark_root", "run", "button", "big_button", "tile", "label", "timer", "table_style", "Mark", "SelectAllEdit",
                 "OptionCard", "combo"):
        assert hasattr(ui, name), name
    assert list(inspect.signature(ui.button).parameters)[:4] == ["text_key", "handler", "object_name", "width"]
    assert list(inspect.signature(ui.big_button).parameters)[:4] == ["text_key", "handler", "kind", "width"]
    assert "QTableWidget" in ui.table_style() and ui.TABLE_STYLE == ui.table_style()


def test_dialogs_keep_their_shape():
    assert list(inspect.signature(dialogs.ask).parameters) == ["parent", "title", "text"]
    assert list(inspect.signature(dialogs.inform).parameters) == ["parent", "title", "text"]
    assert callable(dialogs._build) and callable(dialogs._run) and callable(dialogs.confirm)
