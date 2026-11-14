"""Design tokens of the AeroTools UI kit: the only place where a colour, a size or a radius is read.

Pure Python, no Qt: the numbers are plain data, so a test can compute the contrast of every text / background pair.

The colours and shapes live in a SKIN, one small data-only file in `uikit/skins/` (see docs/UIKIT.md, "Skins"); the families, the type
scale and the weights live in a FONT, one entry of `uikit/fonts.py` (docs/UIKIT.md, "Fonts"). The two choices are independent. This module loads the active
skin, checks it (a missing or malformed key falls back to the default skin's value) and exposes it under the names below, so no tool
or widget ever knows which skin is on. The active skin is the setting `ui.skin` (default `dark`) and the active font the setting `ui.font` (default `system`), both read when this
module is imported; `use(name)` and `use_font(name)` switch them in a running process (tests, the renders).

Sizes are written in px at 100 % scaling. The kit multiplies them by `metrics.px()`; a tool never needs a literal size.

Roles in short:
  BG            the one window background
  SURFACE       inputs and raised parts (the one raised colour); SURFACE_HOVER / SURFACE_PRESSED are its states; CARD is a card or a table body
  BORDER        the one hairline (BORDER_STRONG: hover / emphasis only)
  TEXT_STRONG, TEXT, TEXT_SECONDARY (form-row labels), TEXT_MUTED, TEXT_DISABLED
  ACCENT        the one accent; ON_ACCENT is the text on top of it; PRIMARY_FILL (text on it: ON_PRIMARY) the fill of a primary button (one colour or a gradient)
  SUCCESS, WARNING, DANGER, INFO   semantic colours (text / icons on BG or SURFACE), each with an opaque *_SOFT tint for banners
  OVERLAY, ON_OVERLAY, SHADOW_INK  what is drawn over a photo or a canvas (a label backing, its text, a halo); DATA_PALETTE for data colours
"""

import copy
import importlib
import os
import re

from . import fonts

DEFAULT_SKIN = "dark"
ORDER = ("dark", "medium", "light")      # the order the theme button cycles through
SETTING = ("ui", "skin")                 # config section / key of the choice


def _rgb(value):
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def mix(top, bottom, amount):
    """Opaque colour: `top` laid over `bottom` with the given opacity (0..1)."""
    a, b = _rgb(top), _rgb(bottom)
    return "#{:02x}{:02x}{:02x}".format(*[int(round(a[i] * amount + b[i] * (1.0 - amount))) for i in range(3)])


def luminance(value):
    """Relative luminance (WCAG 2.x) of a #rrggbb colour."""
    channels = []
    for c in _rgb(value):
        c = c / 255.0
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast(foreground, background):
    """WCAG contrast ratio (1..21) of two #rrggbb colours."""
    a, b = luminance(foreground), luminance(background)
    if a < b:
        a, b = b, a
    return (a + 0.05) / (b + 0.05)


# -- loading a skin --------------------------------------------------------------------------------

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_SKIN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skins")
_TYPE_ROLES = fonts.ROLES
_COLOUR_KEYS = ("bg", "surface", "surface_hover", "surface_pressed", "border", "border_strong", "text_strong", "text", "text_secondary", "text_muted",
                "text_disabled", "accent", "accent_hover", "accent_pressed", "on_accent", "accent_soft", "ring", "ring_on_accent",
                "success", "warning", "danger", "info", "danger_solid", "danger_solid_hover", "danger_solid_pressed", "on_danger",
                "on_primary", "button_text", "info_icon", "ring_on_danger", "success_soft", "warning_soft", "danger_soft", "info_soft", "logo_0", "logo_1", "logo_2",
                "overlay", "on_overlay", "shadow_ink")
_FILL_KEYS = ("primary", "button", "button_border", "card", "table_header", "selected_row", "header", "header_rule")
_NO_FILL = (None, "transparent")


def skin_names():
    """Names of the skins shipped in `uikit/skins/`, in cycling order (dark, medium, light; the default first)."""
    try:
        found = sorted(n[:-3] for n in os.listdir(_SKIN_DIR) if n.endswith(".py") and not n.startswith("_"))
    except OSError:
        found = []
    return [n for n in ORDER if n in found] + [n for n in found if n not in ORDER]


def next_skin(name=None):
    """The skin after `name` (the active one) in the cycle dark -> medium -> light -> dark."""
    names = skin_names()
    current = _state["name"] if name is None else name
    return names[(names.index(current) + 1) % len(names)] if current in names else DEFAULT_SKIN


def load_skin(name):
    """The raw `SKIN` dict of a skin file (a copy). An unknown or broken skin gives the default one."""
    for candidate in (name, DEFAULT_SKIN):
        try:
            module = importlib.import_module(__package__ + ".skins." + str(candidate))
            return copy.deepcopy(module.SKIN)
        except Exception:
            continue
    raise RuntimeError("the default skin is missing")


def _merge(base, over):
    """`over` laid on `base`, key by key (a section missing from `over` is the base's)."""
    out = copy.deepcopy(base)
    if not isinstance(over, dict):
        return out
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _is_colour(value):
    return isinstance(value, str) and bool(_HEX.match(value))


def _is_fill(value):
    if value in _NO_FILL or _is_colour(value):
        return True
    return isinstance(value, (list, tuple)) and len(value) >= 2 and all(_is_colour(v) for v in value)


def problems(skin):
    """What is wrong in a raw skin dict, as a list of texts (empty when fine). Wrong values fall back to the default's at load."""
    base = load_skin(DEFAULT_SKIN)
    out = []
    if not isinstance(skin, dict):
        return ["the skin is not a dict"]
    for section in ("colors", "fills", "space", "shape", "data"):
        if section not in skin:
            out.append("missing section " + section)
    for key in _COLOUR_KEYS:
        if key not in (skin.get("colors") or {}):
            out.append("missing colour " + key)
        elif not _is_colour(skin["colors"][key]):
            out.append("not a #rrggbb colour: " + key)
    for key in _FILL_KEYS:
        if key not in (skin.get("fills") or {}):
            out.append("missing fill " + key)
        elif not _is_fill(skin["fills"][key]):
            out.append("not a colour, a list of colours or None: fills." + key)
    for key in base.get("shape", {}):
        if key not in (skin.get("shape") or {}):
            out.append("missing shape." + key)
    return out


def _fallback(skin):
    """The skin with every missing or malformed value replaced by the default skin's."""
    base = load_skin(DEFAULT_SKIN)
    merged = _merge(base, skin)
    for key in _COLOUR_KEYS:
        if not _is_colour(merged["colors"].get(key)):
            merged["colors"][key] = base["colors"][key]
    for key in _FILL_KEYS:
        if key not in merged["fills"] or not _is_fill(merged["fills"][key]):
            merged["fills"][key] = base["fills"][key]
    space = merged.get("space")
    if not (isinstance(space, (list, tuple)) and len(space) == 6 and all(isinstance(v, int) and v >= 0 for v in space)):
        merged["space"] = base["space"]
    for key, value in base["shape"].items():
        if not isinstance(merged["shape"].get(key), int) or merged["shape"][key] < 0:
            merged["shape"][key] = value
    palette = merged["data"].get("palette")
    if not (isinstance(palette, (list, tuple)) and palette and all(_is_colour(v) for v in palette)):
        merged["data"]["palette"] = base["data"]["palette"]
    return merged


def _solid(fill, fallback):
    """The first colour of a fill ('transparent' and None give `fallback`)."""
    if isinstance(fill, (list, tuple)):
        return fill[0]
    return fallback if fill in _NO_FILL else fill


def _checked_font(name):
    """The entry of font `name` with every value checked; a missing or malformed one is the system font's (the default's)."""
    base = fonts.get(fonts.DEFAULT)
    t = fonts.get(name)
    for role in _TYPE_ROLES:
        if not isinstance(t["scale"].get(role), int) or t["scale"][role] < fonts.MIN_SIZE:
            t["scale"][role] = max(base["scale"][role], fonts.MIN_SIZE)
    for key in ("family", "heading_family", "label_family", "mono"):
        value = t.get(key)
        if isinstance(value, str):
            t[key] = [value]
        elif not (isinstance(value, (list, tuple)) and value and all(isinstance(v, str) for v in value)):
            t[key] = base[key]
    return t


def resolve(name_or_skin=None, font=None):
    """Every token of a skin and a font as {NAME: value}: a skin name, a raw skin dict, or None for the active skin; a font name or None
    for the active font. Checked, with fallbacks."""
    if name_or_skin is None:
        name_or_skin = _state["name"]
    skin = load_skin(name_or_skin) if isinstance(name_or_skin, str) else name_or_skin
    s = _fallback(skin)
    c, f, t, sh = s["colors"], s["fills"], _checked_font(_state["font"] if font is None else font), s["shape"]
    v = {k.upper(): c[k] for k in _COLOUR_KEYS}
    v["PRIMARY_FILL"] = tuple(f["primary"]) if isinstance(f["primary"], (list, tuple)) else (f["primary"],)
    v["BUTTON_FILL"] = f["button"] if f["button"] is not None else "transparent"
    v["BUTTON_BORDER"] = f["button_border"]
    v["CARD"] = f["card"]
    v["TABLE_HEADER"] = f["table_header"]
    v["SELECTED_ROW"] = f["selected_row"]
    v["HEADER_FILL"] = tuple(f["header"]) if isinstance(f["header"], (list, tuple)) else ((f["header"],) if f["header"] not in _NO_FILL else ())
    v["HEADER_RULE"] = tuple(f["header_rule"]) if isinstance(f["header_rule"], (list, tuple)) else ((f["header_rule"],) if f["header_rule"] not in _NO_FILL else ())
    v["RULE_H"] = sh["rule_h"]
    v["SOFT"] = {"success": v["SUCCESS_SOFT"], "warning": v["WARNING_SOFT"], "danger": v["DANGER_SOFT"], "info": v["INFO_SOFT"]}
    v["SEMANTIC"] = {"success": v["SUCCESS"], "warning": v["WARNING"], "danger": v["DANGER"], "info": v["INFO"]}
    v["DATA_PALETTE"] = tuple(s["data"]["palette"])
    v["FONT_FAMILIES"] = tuple(t["family"])
    v["FONT_FAMILY"] = t["family"][0]
    v["HEADING_FAMILIES"] = tuple(t["heading_family"])
    v["LABEL_FAMILIES"] = tuple(t["label_family"])
    v["MONO_FAMILIES"] = tuple(t["mono"])
    v["MONO_FAMILY"] = t["mono"][0]
    v["TYPE"] = {role: t["scale"][role] for role in _TYPE_ROLES}
    v["WEIGHT_REGULAR"] = int(t.get("weight_regular", 400))
    v["WEIGHT_STRONG"] = int(t.get("weight_strong", 600))
    v["WEIGHT_LABEL"] = int(t.get("weight_label", 400))
    v["LABEL_CASE"] = "upper" if t.get("label_case") == "upper" else "none"
    v["LABEL_SPACING"] = float(t.get("label_spacing", 0.0) or 0.0)
    v["FIELD_TYPE"] = t.get("field_size") if t.get("field_size") in _TYPE_ROLES else "body"
    v["FONT_FILES"] = tuple(t.get("files") or ())
    xs, s_, m, l, xl, xxl = s["space"]
    v.update(XS=xs, S=s_, M=m, L=l, XL=xl, XXL=xxl, SPACE=(0, xs, s_, m, l, xl, xxl))
    v["RADIUS"], v["RADIUS_L"], v["BUTTON_RADIUS"] = sh["radius"], sh["radius_l"], sh["button_radius"]
    v["CONTROL_H"], v["BUTTON_H"], v["RING_W"], v["WINDOW_MARGIN"] = sh["control_h"], sh["button_h"], sh["ring_w"], sh["window_margin"]
    v["WINDOW_RADIUS"] = sh["window_radius"]       # for a frameless host; a native window keeps its own square frame
    v["FONT_NAME"] = (_state["font"] if font is None else font) if fonts.known(_state["font"] if font is None else font) else fonts.DEFAULT
    v["SKIN_LABEL"] = s.get("label", "")
    v["DARK"] = bool(s.get("dark", True))
    return v


# -- contrast of a skin ------------------------------------------------------------------------------

def pairs(values=None):
    """(TEXT_PAIRS, DISABLED_PAIRS, GRAPHIC_PAIRS) of a resolved skin: every (name, text colour, background) the kit draws.

    Text pairs need WCAG AA (4.5, large text 3); disabled text and graphics (focus ring, field edge) need 3:1."""
    v = values or resolve()
    g = v.get
    transparent = g("BG")

    def solid(fill):
        return fill if _is_colour(fill) else transparent

    prim = list(v["PRIMARY_FILL"])
    header = list(v["HEADER_FILL"]) or [g("BG")]
    text = [
        ("TEXT on BG", g("TEXT"), g("BG")), ("TEXT on SURFACE", g("TEXT"), g("SURFACE")),
        ("TEXT on SURFACE_HOVER", g("TEXT"), g("SURFACE_HOVER")), ("TEXT on SURFACE_PRESSED", g("TEXT"), g("SURFACE_PRESSED")),
        ("TEXT_STRONG on SURFACE_PRESSED", g("TEXT_STRONG"), g("SURFACE_PRESSED")),
        ("TEXT on ACCENT_SOFT", g("TEXT"), g("ACCENT_SOFT")), ("TEXT on CARD", g("TEXT"), g("CARD")),
        ("TEXT on SELECTED_ROW", g("TEXT"), g("SELECTED_ROW")), ("TEXT_STRONG on SELECTED_ROW", g("TEXT_STRONG"), g("SELECTED_ROW")),
        ("BUTTON_TEXT on BUTTON_FILL", g("BUTTON_TEXT"), solid(g("BUTTON_FILL"))), ("BUTTON_TEXT on SURFACE_HOVER", g("BUTTON_TEXT"), g("SURFACE_HOVER")),
        ("BUTTON_TEXT on SURFACE_PRESSED", g("BUTTON_TEXT"), g("SURFACE_PRESSED")),
        ("TEXT_SECONDARY on BG", g("TEXT_SECONDARY"), g("BG")), ("TEXT_SECONDARY on SURFACE", g("TEXT_SECONDARY"), g("SURFACE")),
        ("TEXT_SECONDARY on CARD", g("TEXT_SECONDARY"), g("CARD")),
        ("TEXT_MUTED on SELECTED_ROW", g("TEXT_MUTED"), g("SELECTED_ROW")), ("TEXT_MUTED on ACCENT_SOFT", g("TEXT_MUTED"), g("ACCENT_SOFT")),
        ("TEXT_MUTED on SURFACE_PRESSED", g("TEXT_MUTED"), g("SURFACE_PRESSED")),
        ("SUCCESS on CARD", g("SUCCESS"), g("CARD")), ("WARNING on CARD", g("WARNING"), g("CARD")),
        ("DANGER on CARD", g("DANGER"), g("CARD")), ("INFO on CARD", g("INFO"), g("CARD")),
        ("TEXT_MUTED on BG", g("TEXT_MUTED"), g("BG")), ("TEXT_MUTED on SURFACE", g("TEXT_MUTED"), g("SURFACE")),
        ("TEXT_MUTED on SURFACE_HOVER", g("TEXT_MUTED"), g("SURFACE_HOVER")), ("TEXT_MUTED on CARD", g("TEXT_MUTED"), g("CARD")),
        ("TEXT_MUTED on TABLE_HEADER", g("TEXT_MUTED"), g("TABLE_HEADER")),
        ("TEXT_STRONG on HEADER_FILL", g("TEXT_STRONG"), header[0]), ("TEXT_STRONG on HEADER_FILL end", g("TEXT_STRONG"), header[-1]),
        ("TEXT_MUTED on HEADER_FILL", g("TEXT_MUTED"), header[0]), ("TEXT_MUTED on HEADER_FILL end", g("TEXT_MUTED"), header[-1]),
        ("ON_ACCENT on ACCENT", g("ON_ACCENT"), g("ACCENT")), ("ON_ACCENT on ACCENT_HOVER", g("ON_ACCENT"), g("ACCENT_HOVER")),
        ("ON_ACCENT on ACCENT_PRESSED", g("ON_ACCENT"), g("ACCENT_PRESSED")),
        ("ON_PRIMARY on PRIMARY_FILL", g("ON_PRIMARY"), prim[0]), ("ON_PRIMARY on PRIMARY_FILL end", g("ON_PRIMARY"), prim[-1]),
        ("ON_DANGER on DANGER_SOLID", g("ON_DANGER"), g("DANGER_SOLID")),
        ("ON_DANGER on DANGER_SOLID_HOVER", g("ON_DANGER"), g("DANGER_SOLID_HOVER")),
        ("ON_DANGER on DANGER_SOLID_PRESSED", g("ON_DANGER"), g("DANGER_SOLID_PRESSED")),
        ("ACCENT on BG", g("ACCENT"), g("BG")), ("ACCENT on SURFACE", g("ACCENT"), g("SURFACE")),
        ("SUCCESS on BG", g("SUCCESS"), g("BG")), ("SUCCESS on SURFACE", g("SUCCESS"), g("SURFACE")),
        ("WARNING on BG", g("WARNING"), g("BG")), ("WARNING on SURFACE", g("WARNING"), g("SURFACE")),
        ("DANGER on BG", g("DANGER"), g("BG")), ("DANGER on SURFACE", g("DANGER"), g("SURFACE")),
        ("INFO on BG", g("INFO"), g("BG")), ("INFO on SURFACE", g("INFO"), g("SURFACE")),
    ]
    for tint in ("SUCCESS", "WARNING", "DANGER", "INFO"):
        soft = g(tint + "_SOFT")
        text += [("TEXT on %s_SOFT" % tint, g("TEXT"), soft), ("TEXT_MUTED on %s_SOFT" % tint, g("TEXT_MUTED"), soft),
                 ("%s on %s_SOFT" % (tint, tint), g(tint), soft)]
    disabled = [("TEXT_DISABLED on BG", g("TEXT_DISABLED"), g("BG")), ("TEXT_DISABLED on SURFACE", g("TEXT_DISABLED"), g("SURFACE"))]
    graphic = [("RING on BG", g("RING"), g("BG")), ("RING on SURFACE", g("RING"), g("SURFACE")),
               ("RING_ON_ACCENT on ACCENT", g("RING_ON_ACCENT"), g("ACCENT")),
               ("RING_ON_DANGER on DANGER_SOLID", g("RING_ON_DANGER"), g("DANGER_SOLID")),
               ("BORDER_STRONG on BG", g("BORDER_STRONG"), g("BG")), ("INFO_ICON on BG", g("INFO_ICON"), g("BG")),
               ("INFO_ICON on SURFACE", g("INFO_ICON"), g("SURFACE"))]
    return text, disabled, graphic


def contrast_failures(name_or_skin=None):
    """[(pair name, ratio, needed)] of every pair of a skin below WCAG AA (empty when the skin is fine)."""
    text, disabled, graphic = pairs(resolve(name_or_skin))
    bad = [(n, round(contrast(f, b), 2), 4.5) for n, f, b in text if contrast(f, b) < 4.5]
    bad += [(n, round(contrast(f, b), 2), 3.0) for n, f, b in disabled + graphic if contrast(f, b) < 3.0]
    return bad


# -- the active skin ---------------------------------------------------------------------------------

_state = {"name": DEFAULT_SKIN, "font": fonts.DEFAULT}


def configured_skin():
    """The skin named by the setting `ui.skin` ('dark' when unset, unreadable or unknown)."""
    try:
        from .. import config
        name = config.get(SETTING[0], SETTING[1], DEFAULT_SKIN)
    except Exception:
        name = DEFAULT_SKIN
    return name if isinstance(name, str) and name in skin_names() else DEFAULT_SKIN


def active_skin():
    """Name of the skin in use."""
    return _state["name"]


def configured_font():
    """The font named by the setting `ui.font` ('system' when unset, unreadable or unknown)."""
    try:
        from .. import config
        name = config.get(fonts.SETTING[0], fonts.SETTING[1], fonts.DEFAULT)
    except Exception:
        name = fonts.DEFAULT
    return name if fonts.known(name) else fonts.DEFAULT


def active_font():
    """Name of the font in use (system, brand or clear)."""
    return _state["font"]


def next_font(name=None):
    """The font after `name` (the active one) in the cycle system -> brand -> clear -> system."""
    return fonts.next_font(_state["font"] if name is None else name)


def _publish():
    values = resolve(_state["name"])
    globals().update(values)
    globals()["TEXT_PAIRS"], globals()["DISABLED_PAIRS"], globals()["GRAPHIC_PAIRS"] = pairs(values)


def use(name=None):
    """Make a skin the active one (None = the setting). Updates every token of this module; returns the name."""
    name = configured_skin() if name is None else (name if name in skin_names() else DEFAULT_SKIN)
    _state["name"] = name
    _publish()
    return name


def use_font(name=None):
    """Make a font the active one (None = the setting; an unknown name is `system`). Updates every token of this module; returns the name."""
    name = configured_font() if name is None else (name if fonts.known(name) else fonts.DEFAULT)
    _state["font"] = name
    _publish()
    return name


# -- values that are the same in every skin ------------------------------------------------------------

PILL = 999            # chips, toggles, progress bars (clamped to half the height)
ICON = 16
ICON_L = 20
ICON_XL = 40          # empty states
INFO_SIZE = 20        # hit area of the "i" (the drawn circle is 16)
STEP_DOT = 24
TOGGLE_W, TOGGLE_H = 36, 20
BAR_H = 6
LABEL_W = 168         # label column of a form
FIELD_W = 112         # a number box
FIELD_MAX = 480       # widest a text field, choice or path grows in a form
POPOVER_W = 320
MIN_WINDOW_W = 520    # least width of a window that sizes itself to its content
SCREEN_MARGIN = 48    # room a window leaves around itself on the screen

_state["font"] = configured_font()
use(configured_skin())
