"""HELIX AERO theme: the long-standing names of the colours, fonts and style sheets, now thin aliases onto the UI kit.

The design decisions live in `aerotools/uikit/tokens.py` (colours, type scale, spacing, radii, control heights) and the style sheet
in `aerotools/uikit/style.py`. New code uses the kit (see docs/UIKIT.md) and never needs anything from this module except
`load_fonts()`. Everything below exists so a window that has not been migrated yet keeps working and keeps looking like the rest.
"""

import os

from .uikit import style as _style
from .uikit import tokens as _t

# --------------------------------------------------------------------------
# Colours (aliases of the kit tokens)
# --------------------------------------------------------------------------

GROUND_1 = _t.BG
GROUND_2 = _t.SURFACE
GROUND_3 = _t.SURFACE
GROUND_4 = _t.SURFACE_HOVER
FG_0 = _t.TEXT_STRONG
FG_1 = _t.TEXT
FG_2 = _t.TEXT_MUTED
FG_3 = _t.TEXT_DISABLED
FG_4 = _t.BORDER_STRONG
BORDER_FAINT = _t.BORDER
BORDER_SOFT = _t.BORDER
BORDER_STRONG = _t.BORDER_STRONG
FIELD_BG = _t.SURFACE
GREEN = _t.ACCENT
GREEN_BRIGHT = _t.ACCENT_HOVER
GREEN_ON = _t.ON_ACCENT
GREEN_SOFT = _t.ACCENT_SOFT
GREEN_DEEP = "#03a132"

WINDOW = _t.BG
PANE = _t.SURFACE
PANE_HEAD = _t.SURFACE_HOVER
BTN = _t.SURFACE_HOVER
BTN_HOVER = _t.SURFACE_PRESSED

# Older alert colours, kept as they were until the windows that use them are migrated (the kit has DANGER / WARNING).
ORANGE = "#ff5210"
ORANGE_BRIGHT = "#ff7a3c"
ORANGE_DEEP = "#b83504"
OFF_TEXT = ORANGE_BRIGHT
BLUE = "#4aa8ff"

LOGO_0 = _t.LOGO_0
LOGO_1 = _t.LOGO_1
LOGO_2 = _t.LOGO_2

PAD = _t.S
PAD_S = _t.XS
PAD_L = _t.L

RADIUS = _t.RADIUS_L
RADIUS_S = _t.RADIUS

_UI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "ui")
ARROW_DOWN = os.path.join(_UI, "arrow_down.png").replace("\\", "/")
CHECK_MARK = os.path.join(_UI, "check.png").replace("\\", "/")
RADIO_OFF = os.path.join(_UI, "radio_off.png").replace("\\", "/")
RADIO_ON = os.path.join(_UI, "radio_on.png").replace("\\", "/")

_TITLE_FAMILY = _t.FONT_FAMILY
_TEXT_FAMILY = _t.FONT_FAMILY


# --------------------------------------------------------------------------
# Fonts
# --------------------------------------------------------------------------

def load_fonts(QtGui):
    """Panel font: the Metashape one (Segoe UI), more legible than
    Rubik and Manrope at small sizes. Nothing to load."""
    return _TITLE_FAMILY, _TEXT_FAMILY


def title_family():
    return _TITLE_FAMILY


# --------------------------------------------------------------------------
# Style sheets (built once per scale, cached, by the kit)
# --------------------------------------------------------------------------

def stylesheet():
    """The style sheet of a window root named `aeroPanel` (the kit's, with the older object names mapped onto it)."""
    return _style.stylesheet()


def tooltip_stylesheet():
    """Tooltip rule for a widget outside a kit window."""
    return _style.tooltip_stylesheet()


def menu_stylesheet():
    """Menu (and tooltip) rules for a pop-up menu, which is a window of its own."""
    return _style.menu_stylesheet()


# --------------------------------------------------------------------------
# Small shared components
# --------------------------------------------------------------------------

def eyebrow(QtWidgets, text):
    """Spaced-capitals heading above a block."""
    label = QtWidgets.QLabel(text.upper())
    label.setObjectName("aeroEyebrow")
    return label


def gradient_rule(QtWidgets, height=2):
    """Logo gradient rule: the visual signature of the panel."""
    rule = QtWidgets.QFrame()
    rule.setObjectName("aeroRule")
    rule.setFixedHeight(height)
    rule.setFrameShape(QtWidgets.QFrame.NoFrame)
    return rule


def separator(QtWidgets):
    line = QtWidgets.QFrame()
    line.setFrameShape(QtWidgets.QFrame.HLine)
    line.setFrameShadow(QtWidgets.QFrame.Plain)
    line.setFixedHeight(1)
    line.setStyleSheet("background: {}; border: none;".format(BORDER_SOFT))
    return line
