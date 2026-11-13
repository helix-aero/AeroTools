"""UI kit tokens: contrast of every text pair (WCAG AA), the scales, one control height, no literal colour outside tokens.py."""

import glob
import os
import re

from aerotools.uikit import tokens

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIT = os.path.join(ROOT, "aerotools", "uikit")


def test_contrast_helper_matches_known_values():
    assert abs(tokens.contrast("#000000", "#ffffff") - 21.0) < 0.01
    assert abs(tokens.contrast("#777777", "#ffffff") - 4.48) < 0.02
    assert tokens.contrast("#123456", "#123456") == 1.0


def test_every_text_pair_meets_wcag_aa():
    bad = [(name, round(tokens.contrast(fg, bg), 2)) for name, fg, bg in tokens.TEXT_PAIRS if tokens.contrast(fg, bg) < 4.5]
    assert not bad, bad


def test_disabled_text_and_graphics_stay_readable():
    bad = [(name, round(tokens.contrast(fg, bg), 2)) for name, fg, bg in tokens.DISABLED_PAIRS + tokens.GRAPHIC_PAIRS
           if tokens.contrast(fg, bg) < 3.0]
    assert not bad, bad


def test_the_pair_list_covers_every_text_colour_on_every_ground():
    names = " ".join(name for name, _fg, _bg in tokens.TEXT_PAIRS)
    for text in ("TEXT", "TEXT_MUTED", "ACCENT", "SUCCESS", "WARNING", "DANGER", "INFO", "ON_ACCENT", "ON_DANGER"):
        assert text in names
    for ground in ("BG", "SURFACE", "SURFACE_HOVER", "SUCCESS_SOFT", "WARNING_SOFT", "DANGER_SOFT", "INFO_SOFT"):
        assert ground in names


def test_type_scale_has_six_increasing_steps():
    sizes = list(tokens.TYPE.values())
    assert len(sizes) == 6 and sizes == sorted(sizes) and len(set(sizes)) >= 5 and min(sizes) >= 12      # caption and small may share 12 px
    assert set(tokens.TYPE) == {"caption", "small", "body", "lead", "title", "display"}


def test_spacing_is_a_four_pixel_scale():
    assert all(value % 4 == 0 for value in tokens.SPACE)
    assert (tokens.XS, tokens.S, tokens.M, tokens.L, tokens.XL, tokens.XXL) == (4, 8, 12, 16, 24, 32)
    assert tokens.WINDOW_MARGIN % 4 == 0


def test_radii_are_ten_twelve_and_pill():
    assert (tokens.RADIUS, tokens.RADIUS_L, tokens.BUTTON_RADIUS) == (10, 12, 999) and tokens.PILL >= 100


def test_one_control_height_and_one_button_height():
    assert isinstance(tokens.CONTROL_H, int) and isinstance(tokens.BUTTON_H, int)
    assert tokens.CONTROL_H == tokens.BUTTON_H, "a button next to a field must line up"


def test_the_only_accent_is_the_brand_green():
    assert tokens.ACCENT == "#06bd3b"
    assert tokens.RING == tokens.ACCENT


def test_soft_tints_are_opaque_hex_colours():
    for tint in tokens.SOFT.values():
        assert re.fullmatch(r"#[0-9a-f]{6}", tint)


def test_no_literal_colour_in_the_kit_outside_tokens():
    for path in glob.glob(os.path.join(KIT, "*.py")):
        if os.path.basename(path) == "tokens.py":
            continue
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        assert not re.search(r"""["']#[0-9a-fA-F]{3,8}["']""", text), os.path.basename(path)
        assert not re.search(r"\brgba?\(", text), os.path.basename(path)


def test_no_literal_font_size_or_radius_in_the_kit_outside_tokens_and_style():
    for path in glob.glob(os.path.join(KIT, "*.py")):
        if os.path.basename(path) in ("tokens.py", "style.py"):
            continue
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        assert not re.search(r"font-size\s*:\s*\d", text), os.path.basename(path)
        assert not re.search(r"border-radius\s*:\s*\d", text), os.path.basename(path)


# -- skins -----------------------------------------------------------------------------------------

import copy

import pytest

SKINS = tokens.skin_names()


def test_three_skins_ship_dark_first():
    assert SKINS == ["dark", "medium", "light"] and tokens.DEFAULT_SKIN == "dark"


@pytest.mark.parametrize("name", SKINS)
def test_every_skin_meets_wcag_aa(name):
    assert tokens.contrast_failures(name) == []


@pytest.mark.parametrize("name", SKINS)
def test_every_skin_file_is_complete_and_data_only(name):
    assert tokens.problems(tokens.load_skin(name)) == []
    with open(os.path.join(KIT, "skins", name + ".py"), encoding="utf-8") as handle:
        text = handle.read()
    assert "import " not in text and "def " not in text and "class " not in text, "a skin is data only"


def test_the_three_skins_share_one_geometry_so_a_live_switch_only_recolours():
    a = tokens.load_skin("dark")
    for name in ("medium", "light"):
        b = tokens.load_skin(name)
        assert (a["space"], a["shape"]) == (b["space"], b["shape"])
        assert "type" not in b, "families and sizes live in uikit/fonts.py"


def test_a_missing_or_malformed_value_falls_back_to_the_dark_skin():
    base = tokens.resolve("dark")
    skin = copy.deepcopy(tokens.load_skin("light"))
    del skin["colors"]["accent"]
    skin["colors"]["text"] = "red"
    del skin["shape"]["control_h"]
    resolved = tokens.resolve(skin)
    assert resolved["ACCENT"] == base["ACCENT"] and resolved["TEXT"] == base["TEXT"]
    assert resolved["CONTROL_H"] == base["CONTROL_H"] and resolved["TYPE"] == base["TYPE"]
    assert resolved["BG"] == tokens.resolve("light")["BG"], "what is present is kept"
    assert tokens.problems(skin), "the problems are reported"


def test_an_unknown_skin_name_gives_the_dark_skin():
    assert tokens.resolve(tokens.load_skin("no_such_skin")) == tokens.resolve("dark")


def test_the_active_skin_is_the_setting_and_switching_updates_every_token():
    from aerotools import config
    try:
        assert tokens.active_skin() == "dark"
        config.put("ui", "skin", "light")
        assert tokens.configured_skin() == "light"
        tokens.use()
        assert tokens.active_skin() == "light" and tokens.BG == tokens.resolve("light")["BG"] and not tokens.DARK
        assert tokens.SOFT["danger"] == tokens.DANGER_SOFT
        config.put("ui", "skin", "nonsense")
        assert tokens.configured_skin() == "dark", "an unknown value in the settings means dark"
    finally:
        tokens.use("dark")
    assert tokens.DARK and tokens.BG == "#111214"


def test_the_cycle_is_dark_medium_light_dark():
    assert [tokens.next_skin(n) for n in ("dark", "medium", "light")] == ["medium", "light", "dark"]


def test_the_stylesheet_cache_is_keyed_by_skin_and_scale():
    from aerotools.uikit import style
    try:
        tokens.use("dark")
        a = style.stylesheet()
        tokens.use("light")
        b = style.stylesheet()
        assert a != b and tokens.resolve("light")["BG"] in b and style.stylesheet() is b
        tokens.use("dark")
        assert style.stylesheet() is a
    finally:
        tokens.use("dark")


def test_font_files_named_by_a_font_exist_in_the_repo():
    from aerotools.uikit import fonts
    for name in fonts.names():
        for file in tokens.resolve("dark", name)["FONT_FILES"]:
            assert os.path.isfile(os.path.join(ROOT, "assets", "fonts", file)), file
