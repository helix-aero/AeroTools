"""UI kit fonts: three choices in one data table, a complete type scale each, nothing below 12 px, unknown means system, the font is
independent of the skin, and the legibility floor (muted text, status text, form labels) holds in every skin."""

import os

import pytest

from aerotools import config
from aerotools.uikit import fonts, metrics, style, tokens

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKINS = tokens.skin_names()


@pytest.fixture(autouse=True)
def _restore():
    metrics.set_scale(1.0)
    yield
    metrics.set_scale(None)
    tokens.use_font("system")
    tokens.use("dark")


def test_three_fonts_system_first():
    assert fonts.names() == ["system", "brand", "clear"] and fonts.DEFAULT == "system"
    assert fonts.SETTING == ("ui", "font")


@pytest.mark.parametrize("name", fonts.names())
def test_every_font_has_a_complete_valid_scale(name):
    assert fonts.problems(name) == []
    values = tokens.resolve("dark", name)
    assert set(values["TYPE"]) == set(fonts.ROLES)
    sizes = [values["TYPE"][r] for r in fonts.ROLES]
    assert sizes == sorted(sizes) and min(sizes) >= fonts.MIN_SIZE
    assert values["FONT_FAMILIES"] and values["HEADING_FAMILIES"] and values["LABEL_FAMILIES"] and values["MONO_FAMILIES"]


@pytest.mark.parametrize("name", fonts.names())
def test_no_tracked_or_upper_case_text_below_twelve_px(name):
    entry = fonts.get(name)
    tracked = entry["label_case"] == "upper" or entry["label_spacing"] > 0
    assert not tracked or min(entry["scale"].values()) >= 12


def test_the_default_is_the_system_face_and_clear_is_one_pixel_larger():
    system, clear = fonts.get("system"), fonts.get("clear")
    assert system["family"][0] == "Segoe UI" and system["heading_family"][0] == "Segoe UI"
    assert all(clear["scale"][r] == system["scale"][r] + 1 for r in ("caption", "small", "body", "lead"))
    assert fonts.get("brand")["heading_family"][0] == "Rubik" and fonts.get("brand")["family"][0] == "Manrope"


def test_unknown_names_mean_system(monkeypatch):
    assert fonts.get("nonsense") == fonts.get("system")
    assert tokens.use_font("nonsense") == "system" and tokens.active_font() == "system"
    monkeypatch.setattr(config, "get", lambda section, key, default=None: "comic")
    assert tokens.configured_font() == "system"
    monkeypatch.setattr(config, "get", lambda section, key, default=None: "brand")
    assert tokens.configured_font() == "brand"


def test_a_broken_font_entry_falls_back_to_the_system_values(monkeypatch):
    broken = fonts.get("brand")
    broken["scale"]["caption"] = 9
    broken["family"] = []
    monkeypatch.setitem(fonts.FONTS, "brand", broken)
    values = tokens.resolve("dark", "brand")
    assert values["TYPE"]["caption"] >= 12 and values["FONT_FAMILIES"] == tuple(fonts.get("system")["family"])


def test_the_cycle_is_system_brand_clear_system():
    assert [tokens.next_font(n) for n in ("system", "brand", "clear")] == ["brand", "clear", "system"]


def test_the_font_files_exist_and_are_static():
    for name in fonts.names():
        for file in fonts.get(name)["files"]:
            assert os.path.isfile(os.path.join(ROOT, "assets", "fonts", file)), file
            assert "Variable" not in file          # Qt 5 shows a variable font at one weight only


def test_skins_carry_no_type_section():
    for name in SKINS:
        assert "type" not in tokens.load_skin(name) and tokens.problems(tokens.load_skin(name)) == []


def test_the_font_is_independent_of_the_skin():
    tokens.use("light")
    tokens.use_font("clear")
    assert tokens.active_skin() == "light" and tokens.active_font() == "clear" and tokens.BG == tokens.resolve("light")["BG"]
    assert tokens.TYPE["body"] == fonts.get("clear")["scale"]["body"]
    tokens.use("dark")
    assert tokens.active_font() == "clear" and tokens.TYPE["body"] == fonts.get("clear")["scale"]["body"]


def test_the_style_sheet_is_cached_per_font_and_follows_the_scale_of_the_font():
    tokens.use_font("system")
    a = style.stylesheet()
    tokens.use_font("brand")
    b = style.stylesheet()
    assert a is not b and '"Manrope"' in b and '"Manrope"' not in a and style.stylesheet() is b
    tokens.use_font("system")
    assert style.stylesheet() is a
    assert "$" not in b and b.count("{") == b.count("}")
    assert 'type="status"' in a


def test_switch_font_stores_the_choice_and_none_cycles():
    assert style.switch_font("brand") == "brand" and config.get("ui", "font", "") == "brand"
    assert style.switch_font() == "clear" and config.get("ui", "font", "") == "clear"
    assert style.switch_font() == "system"
    assert style.switch_font("nope", save=False) == "system"


# -- the legibility floor, in every skin ------------------------------------------------------------

@pytest.mark.parametrize("skin", SKINS)
def test_muted_text_reaches_4_5_on_every_surface_it_sits_on(skin):
    v = tokens.resolve(skin)
    for ground in ("BG", "SURFACE", "CARD", "SURFACE_HOVER", "SELECTED_ROW", "ACCENT_SOFT", "TABLE_HEADER"):
        assert tokens.contrast(v["TEXT_MUTED"], v[ground]) >= 4.5, (skin, ground)


@pytest.mark.parametrize("skin", SKINS)
def test_form_labels_and_status_colours_are_in_the_checked_pairs(skin):
    text, _d, _g = tokens.pairs(tokens.resolve(skin))
    names = {n for n, _f, _b in text}
    for pair in ("TEXT_SECONDARY on BG", "TEXT_SECONDARY on SURFACE", "TEXT_SECONDARY on CARD", "TEXT_MUTED on CARD", "TEXT_MUTED on SURFACE",
                 "SUCCESS on SURFACE", "SUCCESS on CARD", "WARNING on CARD", "DANGER on CARD", "INFO on CARD"):
        assert pair in names, pair
    v = tokens.resolve(skin)
    for ground in ("BG", "SURFACE", "CARD"):
        assert tokens.contrast(v["TEXT_SECONDARY"], v[ground]) >= 7.0, (skin, ground)    # form labels are better than AA
        assert tokens.contrast(v["TEXT_SECONDARY"], v[ground]) >= tokens.contrast(v["TEXT_MUTED"], v[ground])
    assert tokens.contrast_failures(skin) == []


def test_form_labels_use_the_secondary_colour_at_body_size_and_status_is_semibold():
    sheet = style.stylesheet()
    field = [line for line in sheet.splitlines() if 'QLabel[type="field"]' in line][0]
    assert tokens.TEXT_SECONDARY in field and "font-size: %dpx" % metrics.font_px("body") in field
    status = [line for line in sheet.splitlines() if 'QLabel[type="status"]' in line][0]
    assert "font-weight: 600" in status and "font-size: %dpx" % metrics.font_px("body") in status
