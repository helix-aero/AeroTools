"""HELIX AERO theme for the AeroTools Qt panels.

The values come from the HELIX design system (tokens.css, taken on 2026-11-03)
and from the colors of the HELIX AERO logo. A color exists once here: the
panels only do layout.

Backgrounds (the Metashape grey, a single theme):
  ground-0 #282828 (window), ground-1 #404040 (background), ground-2 #484848 (card),
  ground-3 #353535 (raised: fields, lists), ground-4 #555555 (hover).
Text: fg-0 white, fg-1 #eeeeee (body text), fg-2 #b9b9b9 (secondary).
Accent: brand green #1eca59 (hover #34d56b, text on top #06241a) - a
fill, never text on a light background.
Alert: orange #ff5210 (tool stopped).
Logo gradient: #163056 -> #139168 -> #03a132, from bottom left to top right.
"""

import os

# --------------------------------------------------------------------------
# Tokens
# --------------------------------------------------------------------------

# A single palette: the Metashape grey (Window #282828, Base #404040, Text #eeeeee),
# so that the panel and the AeroTools windows blend into the interface.
# Brand green is the only accent; orange and the logo gradient are shared.
GROUND_0 = "#282828"
GROUND_1 = "#404040"
GROUND_2 = "#484848"
GROUND_3 = "#353535"
GROUND_4 = "#555555"
FG_0 = "#ffffff"
FG_1 = "#eeeeee"
FG_2 = "#b9b9b9"
FG_3 = "#8f8f8f"
FG_4 = "#5a5a5a"
BORDER_FAINT = "rgba(255, 255, 255, 0.08)"
BORDER_SOFT = "rgba(255, 255, 255, 0.13)"
BORDER_STRONG = "rgba(255, 255, 255, 0.24)"
FIELD_BG = "#353535"
OFF_TEXT = "#ff7a3c"
GREEN = "#1eca59"
GREEN_BRIGHT = "#34d56b"
GREEN_ON = "#06241a"
GREEN_SOFT = "rgba(30, 202, 89, 0.18)"

GREEN_DEEP = "#03a132"

# Settings window: dark background, near-black sections (like the Metashape panes).
WINDOW = "#303030"
PANE = "#1b1b1b"
PANE_HEAD = "#262626"
BTN = "#525252"
BTN_HOVER = "#646464"

ORANGE = "#ff5210"
ORANGE_BRIGHT = "#ff7a3c"
ORANGE_DEEP = "#b83504"


LOGO_0 = "#163056"
LOGO_1 = "#139168"
LOGO_2 = "#03a132"



# 8 px step (and half-step of 4), like the design system.
PAD = 8
PAD_S = 4
PAD_L = 16

RADIUS = 6
RADIUS_S = 4

ARROW_DOWN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "ui", "arrow_down.png").replace("\\", "/")
CHECK_MARK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "ui", "check.png").replace("\\", "/")
RADIO_OFF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "ui", "radio_off.png").replace("\\", "/")
RADIO_ON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "ui", "radio_on.png").replace("\\", "/")

_FONTS_LOADED = False
_TITLE_FAMILY = "Segoe UI"
_TEXT_FAMILY = "Segoe UI"


# --------------------------------------------------------------------------
# Fonts
# --------------------------------------------------------------------------

def load_fonts(QtGui):
    """Panel font: the Metashape one (Segoe UI), more legible than
    Rubik and Manrope at small sizes. Nothing to load."""
    global _FONTS_LOADED
    _FONTS_LOADED = True
    return _TITLE_FAMILY, _TEXT_FAMILY


def title_family():
    return _TITLE_FAMILY


def text_family():
    return _TEXT_FAMILY


# --------------------------------------------------------------------------
# Style sheet
# --------------------------------------------------------------------------

def tooltip_stylesheet():
    """Tooltip style. A QToolTip is a separate window: it does not read
    the panel style sheet, so this rule is set on the hovered widget."""
    return """
    QToolTip {{
        background: {ground_2};
        color: {fg_0};
        border: 1px solid {border_strong};
        border-radius: {radius_s}px;
        padding: 4px 8px;
        font-family: "{text}";
        font-size: 10pt;
    }}
    """.format(
        ground_2=GROUND_2,
        fg_0=FG_0,
        border_strong=BORDER_STRONG,
        radius_s=RADIUS_S,
        text=text_family(),
    )


def menu_stylesheet():
    """Export menu style. A drop-down menu is a separate window: as
    for the tooltip, the rule is set on the menu itself."""
    return """
    QMenu {{
        background: {ground_2};
        color: {fg_1};
        border: 1px solid {border_strong};
        border-radius: {radius_s}px;
        padding: 4px;
        font-family: "{text}";
        font-size: 10pt;
    }}
    QMenu::item {{
        padding: 6px 18px 6px 12px;
        border-radius: {radius_s}px;
    }}
    QMenu::item:selected {{
        background: {green_soft};
        color: {fg_0};
    }}
    QMenu::item:disabled {{
        color: {fg_4};
        background: transparent;
    }}
    QMenu::separator {{
        height: 1px;
        background: {border_strong};
        margin: 4px 8px;
    }}
    """.format(
        ground_2=GROUND_2,
        fg_0=FG_0,
        fg_1=FG_1,
        fg_4=FG_4,
        green_soft=GREEN_SOFT,
        border_strong=BORDER_STRONG,
        radius_s=RADIUS_S,
        text=text_family(),
    )


def stylesheet():
    """Qt style sheet of the panel. The targeted objectNames:

      #aeroPanel      panel root
      #aeroHeader     title banner
      #aeroRule       logo gradient rule (2 px)
      #aeroEyebrow    spaced-capitals heading
      #aeroTitle      tool title
      #aeroValue      highlighted value (current marker)
      #aeroMeta       secondary line
      #aeroBanner     top banner (AERO logo on the kit's light #F3F4F1 background, advert) ; #aeroBannerTop : its green rule
      #aeroCard       settings block ; #aeroCardAccent : highlighted block (green frame)
      #aeroSection    section title of a window (settings)
      #aeroProgress   thin progress bar (settings, refinement) ; #aeroProgressText : the AI detection one, with its text
      #aeroCombo      drop-down list (AI detection choice)
      #aeroThumbs     lists of the review window ; #aeroSlider : size slider
      #aeroPlayOn / #aeroPlayOff  round Pause / Play button
      #aeroState / #aeroStateOff  "Tool active" / "Tool inactive" text
      #aeroCounter    counter pill
      #aeroRun        green action button (Run, Validate when everything is done)
      #aeroYes / #aeroNo  Yes (green edge) and No (red edge) of the dialog boxes
      #aeroGhost      discreet secondary button ([round="true"] : round,
                      [pill="true"] : pill with text)
      #aeroZoomButton +/- buttons of the review window
      #aeroGridBox    frame of the marker table ; #aeroGrid : the grid
    """
    return """
    #aeroPanel {{
        background: {ground_1};
    }}
    #aeroPanel QWidget {{
        color: {fg_1};
        font-family: "{text}";
        font-size: 10pt;
    }}
    #aeroPanel #aeroHeader {{
        background: {ground_1};
    }}
    #aeroPanel #aeroBanner {{
        background: #f3f4f1;
    }}
    #aeroPanel #aeroBanner QLabel#aeroMeta {{
        color: #4a4d52;
    }}
    #aeroPanel #aeroBannerTop {{
        border: none;
        background: {green};
    }}
    #aeroPanel #aeroRule {{
        border: none;
        background: qlineargradient(x1:0, y1:1, x2:1, y2:0,
            stop:0 {logo_0}, stop:0.5 {logo_1}, stop:1 {logo_2});
    }}
    #aeroPanel #aeroEyebrow {{
        color: {fg_2};
        font-family: "{title}";
        font-size: 8pt;
        font-weight: 600;
        letter-spacing: 1.4px;
        text-transform: uppercase;
    }}
    #aeroPanel #aeroTitle {{
        color: {fg_0};
        font-family: "{title}";
        font-size: 13pt;
        font-weight: 600;
    }}
    #aeroPanel #aeroValue {{
        color: {fg_0};
        font-family: "{title}";
        font-size: 16pt;
        font-weight: 600;
    }}
    #aeroPanel #aeroValueEmpty {{
        color: {fg_3};
        font-family: "{title}";
        font-size: 16pt;
        font-weight: 600;
    }}
    #aeroPanel #aeroMeta {{
        color: {fg_2};
        font-size: 9pt;
    }}
    #aeroPanel #aeroCard {{
        background: {ground_2};
        border: 1px solid {border_faint};
        border-radius: {radius}px;
    }}
    #aeroPanel #aeroCardActive {{
        background: #4b5650;
        border: 1px solid rgba(30, 202, 89, 0.32);
        border-radius: {radius}px;
    }}
    #aeroPanel #aeroCardAccent {{
        background: {ground_2};
        border: 1px solid {green};
        border-radius: {radius}px;
    }}
    #aeroPanel #aeroSection {{
        color: {fg_0};
        font-family: "{title}";
        font-size: 11pt;
        font-weight: 600;
    }}
    #aeroPanel #aeroAccentTitle {{
        color: {green};
        font-family: "{title}";
        font-size: 9pt;
        font-weight: 700;
        letter-spacing: 1.4px;
    }}
    #aeroPanel QComboBox#aeroCombo {{
        background: {field_bg};
        border: 1px solid {border_faint};
        border-radius: {radius_s}px;
        color: {fg_0};
        padding: 4px 8px;
        min-height: 20px;
    }}
    #aeroPanel QComboBox#aeroCombo:hover {{
        border-color: {border_strong};
    }}
    #aeroPanel QComboBox#aeroCombo::drop-down {{
        border: none;
        width: 18px;
    }}
    #aeroPanel QComboBox#aeroCombo[arrow="true"]::down-arrow {{
        image: url({arrow});
        width: 12px;
        height: 8px;
    }}
    #aeroPanel QComboBox QAbstractItemView {{
        background: {ground_2};
        color: {fg_0};
        border: 1px solid {border_strong};
        selection-background-color: {green_soft};
        selection-color: {fg_0};
        outline: 0;
    }}
    #aeroPanel QListWidget#aeroThumbs {{
        background: {ground_1};
        border: 1px solid {border_faint};
        border-radius: {radius_s}px;
        outline: 0;
    }}
    #aeroPanel QListWidget#aeroThumbs::item {{
        color: {fg_1};
        border: 1px solid transparent;
        border-radius: {radius_s}px;
    }}
    #aeroPanel QListWidget#aeroThumbs::item:selected {{
        background: {green_soft};
        border: 1px solid {green};
        color: {fg_0};
    }}
    #aeroPanel QSlider#aeroSlider::groove:horizontal {{
        height: 4px;
        background: {ground_3};
        border-radius: 2px;
    }}
    #aeroPanel QSlider#aeroSlider::handle:horizontal {{
        background: {green};
        width: 12px;
        margin: -4px 0;
        border-radius: 6px;
    }}
    #aeroPanel QProgressBar#aeroProgress {{
        background: {ground_3};
        border: none;
        border-radius: 3px;
        min-height: 6px;
        max-height: 6px;
    }}
    #aeroPanel QProgressBar#aeroProgress::chunk {{
        background: {green};
        border-radius: 3px;
    }}
    /* AI detection bar: the text is inside it (logo green, legible on the background as on the fill) */
    #aeroPanel QProgressBar#aeroProgressText {{
        background: {ground_3};
        border: none;
        border-radius: 4px;
        color: {fg_0};
        text-align: center;
        min-height: 22px;
        max-height: 22px;
    }}
    #aeroPanel QProgressBar#aeroProgressText::chunk {{
        background: #139168;
        border-radius: 4px;
    }}

    /* Play / Pause: round button (the icon is painted) + plain state text */
    #aeroPanel QPushButton#aeroPlayOn {{
        background: {green};
        border: none;
        border-radius: 18px;
        padding: 0;
    }}
    #aeroPanel QPushButton#aeroPlayOn:hover {{
        background: {green_bright};
    }}
    #aeroPanel QPushButton#aeroPlayOff {{
        background: {ground_3};
        border: 1px solid {border_strong};
        border-radius: 18px;
        padding: 0;
    }}
    #aeroPanel QPushButton#aeroPlayOff:hover {{
        background: {ground_4};
    }}
    #aeroPanel #aeroState {{
        color: {fg_0};
        font-family: "{title}";
        font-size: 11pt;
        font-weight: 600;
    }}
    #aeroPanel #aeroStateOff {{
        color: {fg_2};
        font-family: "{title}";
        font-size: 11pt;
        font-weight: 600;
    }}

    /* Counters of the active marker */
    #aeroPanel #aeroCounter {{
        background: {ground_3};
        border: 1px solid {border_faint};
        border-radius: 11px;
    }}
    #aeroPanel #aeroCounterNum {{
        color: {fg_0};
        font-family: "{title}";
        font-size: 10pt;
        font-weight: 600;
    }}

    /* Secondary buttons */
    #aeroPanel QPushButton {{
        background: {ground_3};
        color: {fg_1};
        border: 1px solid {border_faint};
        border-radius: {radius_s}px;
        font-family: "{title}";
        font-size: 9pt;
        font-weight: 600;
        padding: 6px 10px;
    }}
    #aeroPanel QPushButton:hover {{
        background: {ground_4};
        color: {fg_0};
    }}
    #aeroPanel QPushButton:disabled {{
        background: {ground_2};
        color: {fg_3};
        border-color: {border_faint};
    }}
    #aeroPanel QPushButton#aeroGhost {{
        background: transparent;
        border: 1px solid {border_soft};
        color: {fg_2};
    }}
    #aeroPanel QPushButton#aeroGhost:hover {{
        background: {ground_3};
        color: {fg_1};
    }}
    #aeroPanel QPushButton#aeroGhost:disabled {{
        background: transparent;
        border-color: {border_faint};
        color: {fg_3};
    }}
    #aeroPanel QPushButton#aeroGhost[round="true"] {{
        border-radius: 16px;
        padding: 0;
    }}
    #aeroPanel QPushButton#aeroGhost[pill="true"] {{
        border-radius: 16px;
        padding: 0 12px 0 8px;
        color: {fg_1};
    }}
    #aeroPanel QPushButton#aeroGhost[pill="true"]:hover {{
        color: {fg_0};
    }}
    #aeroPanel QPushButton#aeroGhost[pill="true"]:disabled {{
        color: {fg_4};
        border-color: {border_faint};
    }}
    #aeroPanel QPushButton#aeroYes, #aeroPanel QPushButton#aeroNo {{
        background: transparent;
        color: {fg_0};
        border-radius: 20px;
        padding: 0 20px;
        font-size: 10pt;
        font-weight: 700;
    }}
    #aeroPanel QPushButton#aeroYes {{
        border: 2px solid {green};
    }}
    #aeroPanel QPushButton#aeroNo {{
        border: 2px solid #e5484d;
    }}
    #aeroPanel QPushButton#aeroYes:hover {{
        background: rgba(30, 202, 89, 0.18);
    }}
    #aeroPanel QPushButton#aeroNo:hover {{
        background: rgba(229, 72, 77, 0.18);
    }}
    #aeroPanel QPushButton#aeroGhost[round="true"]:checked {{
        background: {ground_4};
        border-color: {border_strong};
    }}

    /* Action button: same green as Play */
    #aeroPanel QPushButton#aeroRun {{
        background: {green};
        border: none;
        border-radius: 16px;
        color: {green_on};
        font-weight: 700;
        padding: 0 14px;
    }}
    #aeroPanel QPushButton#aeroRun:hover {{
        background: {green_bright};
        color: {green_on};
    }}
    #aeroPanel QPushButton#aeroRun:disabled {{
        background: {ground_2};
        color: {fg_4};
    }}

    /* Zoom +/- buttons of the review window */
    #aeroPanel QToolButton#aeroZoomButton {{
        background: {ground_3};
        border: 1px solid {border_soft};
        border-radius: 13px;
        color: {fg_1};
        font-size: 13pt;
        font-weight: 600;
        padding: 0;
    }}
    #aeroPanel QToolButton#aeroZoomButton:hover {{
        background: {ground_4};
        color: {fg_0};
    }}

    /* Fields */
    #aeroPanel QLineEdit {{
        background: {field_bg};
        border: 1px solid {border_faint};
        border-radius: {radius_s}px;
        color: {fg_0};
        padding: 6px 8px;
        selection-background-color: {green};
        selection-color: {green_on};
    }}
    #aeroPanel QLineEdit:focus {{
        border-color: {green};
    }}

    /* Checkboxes */
    #aeroPanel QCheckBox {{
        color: {fg_1};
        spacing: 8px;
        padding: 3px 0;
    }}
    #aeroPanel QCheckBox:hover {{
        color: {fg_0};
    }}
    #aeroPanel QCheckBox::indicator {{
        width: 16px;
        height: 16px;
        border-radius: 3px;
        border: 1px solid {border_strong};
        background: {field_bg};
    }}
    #aeroPanel QCheckBox::indicator:checked {{
        background: {green};
        border-color: {green};
        image: url({check});
    }}
    #aeroPanel QCheckBox::indicator:checked:disabled {{
        background: {fg_4};
        border-color: {fg_4};
    }}
    #aeroPanel QCheckBox::indicator:disabled {{
        border-color: {fg_4};
    }}

    /* Exclusive choice: an empty or filled circle, in a card that turns green once chosen */
    #aeroPanel QRadioButton {{
        color: {fg_0};
        spacing: 12px;
        background: transparent;
    }}
    #aeroPanel QRadioButton:disabled {{
        color: {fg_4};
    }}
    #aeroPanel QRadioButton::indicator {{
        width: 20px;
        height: 20px;
        image: url({radio_off});
    }}
    #aeroPanel QRadioButton::indicator:checked {{
        image: url({radio_on});
    }}
    #aeroPanel QFrame#aeroOption {{
        background: {ground_1};
        border: 1px solid {border_strong};
        border-radius: {radius}px;
    }}
    #aeroPanel QFrame#aeroOption[on="true"] {{
        background: {green_soft};
        border: 1px solid {green};
    }}
    #aeroPanel QFrame#aeroOption[on="true"][danger="true"] {{
        background: rgba(255, 82, 16, 0.14);
        border: 1px solid {orange_bright};
    }}

    /* Marker table: a rounded frame, the grid inside without borders */
    #aeroPanel #aeroGridBox {{
        background: {ground_2};
        border: 1px solid {border_faint};
        border-radius: {radius}px;
    }}
    #aeroPanel #aeroGrid {{
        background: transparent;
        border: none;
        outline: none;
    }}
    #aeroPanel #aeroGrid::item {{
        padding: 0 4px;
        border: none;
    }}
    #aeroPanel #aeroGrid::item:hover {{
        background: {ground_3};
    }}
    #aeroPanel #aeroGrid::item:selected,
    #aeroPanel #aeroGrid::item:selected:!active {{
        background: {green_soft};
        color: {fg_0};
    }}
    #aeroPanel #aeroGrid QHeaderView {{
        background: transparent;
        border: none;
    }}

    /* Journal */
    #aeroPanel #aeroLog {{
        background: {ground_0};
        border: 1px solid {border_faint};
        border-radius: {radius_s}px;
        color: {fg_2};
        font-family: Consolas, "Courier New", monospace;
        font-size: 8pt;
    }}
    #aeroPanel #aeroStatus {{
        color: {fg_2};
        font-size: 9pt;
    }}

    /* Settings window: dark background, black sections, big buttons with centered text */
    #aeroPanel[settings="true"] {{
        background: {window};
    }}
    #aeroPanel #aeroPane {{
        background: {pane};
        border: 1px solid {border_soft};
        border-radius: 8px;
    }}
    #aeroPanel #aeroPaneHead {{
        background: {pane_head};
        border: none;
        border-bottom: 1px solid {border_faint};
        border-top-left-radius: 8px;
        border-top-right-radius: 8px;
    }}
    #aeroPanel #aeroPaneBody, #aeroPanel #aeroBare {{
        background: transparent;
        border: none;
    }}
    #aeroPanel #aeroPaneTitle {{
        color: {fg_0};
        font-family: "{title}";
        font-size: 11pt;
        font-weight: 700;
    }}
    #aeroPanel #aeroBadge {{
        background: {green};
        color: {green_on};
        border-radius: 10px;
        font-family: "{title}";
        font-size: 9pt;
        font-weight: 700;
    }}
    #aeroPanel #aeroBottom {{
        background: {pane_head};
        border-top: 1px solid {border_soft};
    }}
    #aeroPanel #aeroPane QPushButton {{
        background: {btn};
        color: {fg_0};
        border: 1px solid {border_strong};
        border-radius: 8px;
        min-height: 26px;
        padding: 4px 14px;
        font-size: 10pt;
        font-weight: 700;
    }}
    #aeroPanel #aeroPane QPushButton:hover {{
        background: {btn_hover};
    }}
    #aeroPanel #aeroPane QComboBox#aeroCombo {{
        padding: 8px 10px;
        min-height: 24px;
        font-size: 10pt;
    }}
    #aeroPanel #aeroPane QComboBox#aeroCombo::drop-down {{
        border: none;
        width: 28px;
    }}
    #aeroPanel #aeroPane QComboBox#aeroCombo::down-arrow {{
        image: url({arrow});
        width: 12px;
        height: 8px;
        margin-right: 10px;
    }}
    #aeroPanel #aeroPane QLineEdit {{
        padding: 8px 10px;
        font-size: 10pt;
    }}
    #aeroPanel #aeroPane QCheckBox {{
        padding: 5px 0;
        font-size: 10pt;
    }}
    #aeroPanel QPushButton#aeroBtn, #aeroPanel #aeroPane QPushButton#aeroBtn {{
        background: {btn};
        color: {fg_0};
        border: 1px solid {border_strong};
        border-radius: 8px;
        padding: 0 18px;
        font-family: "{title}";
        font-size: 10pt;
        font-weight: 700;
        text-align: center;
    }}
    #aeroPanel QPushButton#aeroBtn:hover, #aeroPanel #aeroPane QPushButton#aeroBtn:hover {{
        background: {btn_hover};
    }}
    #aeroPanel QPushButton#aeroBtn:disabled, #aeroPanel #aeroPane QPushButton#aeroBtn:disabled {{
        background: {pane_head};
        color: {fg_4};
        border-color: {border_faint};
    }}
    #aeroPanel QPushButton#aeroGo, #aeroPanel #aeroPane QPushButton#aeroGo {{
        background: {green};
        color: {green_on};
        border: none;
        border-radius: 8px;
        padding: 0 18px;
        font-family: "{title}";
        font-size: 10.5pt;
        font-weight: 700;
        text-align: center;
    }}
    #aeroPanel QPushButton#aeroGo:hover, #aeroPanel #aeroPane QPushButton#aeroGo:hover {{
        background: {green_bright};
    }}
    #aeroPanel QPushButton#aeroGo:disabled, #aeroPanel #aeroPane QPushButton#aeroGo:disabled {{
        background: {pane_head};
        color: {fg_4};
    }}
    #aeroPanel #aeroStat {{
        background: {pane_head};
        border: 1px solid {border_faint};
        border-radius: 8px;
    }}
    #aeroPanel #aeroStatNum {{
        color: {fg_0};
        font-family: "{title}";
        font-size: 14pt;
        font-weight: 700;
    }}
    #aeroPanel #aeroStatLabel {{
        color: {fg_3};
        font-size: 8pt;
    }}
    #aeroPanel #aeroArrow {{
        color: {fg_3};
        font-size: 16pt;
    }}
    #aeroPanel #aeroChipOk {{
        background: {green_soft};
        color: #6ee79a;
        border-radius: 11px;
        padding: 3px 12px;
        font-weight: 700;
    }}
    #aeroPanel #aeroWarn {{
        color: {orange_bright};
        font-size: 9pt;
    }}

    /* Scroll bars */
    #aeroPanel QScrollBar:vertical {{
        background: transparent;
        width: 8px;
        margin: 0;
    }}
    #aeroPanel QScrollBar::handle:vertical {{
        background: {ground_4};
        border-radius: 4px;
        min-height: 24px;
    }}
    #aeroPanel QScrollBar::handle:vertical:hover {{
        background: {fg_3};
    }}
    #aeroPanel QScrollBar::add-line:vertical,
    #aeroPanel QScrollBar::sub-line:vertical,
    #aeroPanel QScrollBar::add-page:vertical,
    #aeroPanel QScrollBar::sub-page:vertical {{
        background: none;
        height: 0;
    }}
    """.format(
        ground_0=GROUND_0,
        ground_1=GROUND_1,
        ground_2=GROUND_2,
        ground_3=GROUND_3,
        ground_4=GROUND_4,
        fg_0=FG_0,
        fg_1=FG_1,
        fg_2=FG_2,
        fg_3=FG_3,
        fg_4=FG_4,
        green=GREEN,
        green_bright=GREEN_BRIGHT,
        green_on=GREEN_ON,
        green_soft=GREEN_SOFT,
        border_faint=BORDER_FAINT,
        border_soft=BORDER_SOFT,
        border_strong=BORDER_STRONG,
        field_bg=FIELD_BG,
        logo_0=LOGO_0,
        logo_1=LOGO_1,
        logo_2=LOGO_2,
        radius=RADIUS,
        radius_s=RADIUS_S,
        window=WINDOW,
        pane=PANE,
        pane_head=PANE_HEAD,
        btn=BTN,
        btn_hover=BTN_HOVER,
        orange_bright=ORANGE_BRIGHT,
        arrow=ARROW_DOWN,
        check=CHECK_MARK,
        radio_off=RADIO_OFF,
        radio_on=RADIO_ON,
        title=title_family(),
        text=text_family(),
    )


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
    line.setStyleSheet("background: {}; border: none;".format(FG_4))
    return line
