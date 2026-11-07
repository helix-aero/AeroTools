"""Aero-Facade: the few styles the wizard needs on top of the Aero theme (`aerotools/theme.py`).

Sizes are written in `pt` on purpose: the Qt harness (and Windows display scaling) grow the fonts by changing them, and a size in
pixels would not follow. Paddings in pixels are fine, they hold room, not text.
"""

from ... import theme


def stylesheet():
    """Style sheet appended to `theme.stylesheet()` on the root widget of the wizard."""
    return """
    #aeroPanel QFrame#facadeCanvasFrame {{
        background: {ground_0};
        border: 1px solid {border_soft};
        border-radius: 8px;
    }}
    #aeroPanel QListWidget#facadeList, #aeroPanel QListWidget#facadeThumbs {{
        background: {pane};
        border: 1px solid {border_soft};
        border-radius: 8px;
        outline: 0;
        padding: 4px;
    }}
    #aeroPanel QListWidget#facadeList::item, #aeroPanel QListWidget#facadeThumbs::item {{
        border-radius: 6px;
        margin: 2px 0;
    }}
    #aeroPanel QListWidget#facadeList::item:hover, #aeroPanel QListWidget#facadeThumbs::item:hover {{
        background: {ground_3};
    }}
    #aeroPanel QListWidget#facadeList::item:selected, #aeroPanel QListWidget#facadeList::item:selected:!active {{
        background: {green_soft};
    }}
    #aeroPanel QListWidget#facadeThumbs::item:selected, #aeroPanel QListWidget#facadeThumbs::item:selected:!active {{
        background: {ground_3};
    }}
    #aeroPanel QListWidget#facadeList QScrollBar:vertical {{
        background: {ground_3};
        width: 12px;
        margin: 2px 0;
        border-radius: 6px;
    }}
    #aeroPanel QListWidget#facadeList QScrollBar::handle:vertical {{
        background: {fg_3};
        border-radius: 5px;
        min-height: 30px;
        margin: 1px;
    }}
    #aeroPanel QListWidget#facadeList QScrollBar::handle:vertical:hover {{
        background: {fg_2};
    }}
    #aeroPanel QFrame#facadeThumbPane {{
        background: {pane};
        border: 1px solid {border_soft};
        border-radius: 8px;
    }}
    #aeroPanel QFrame#facadeThumbCard {{
        background: {ground_2};
        border: 1px solid {border_faint};
        border-radius: 8px;
    }}
    #aeroPanel QFrame#facadeThumbCard[hover="true"] {{
        background: {ground_3};
        border: 1px solid {border_soft};
    }}
    #aeroPanel QFrame#facadeThumbCard[on="true"] {{
        border: 1px solid {green};
    }}
    #aeroPanel QFrame#facadeThumbCard[on="false"] QLabel {{
        color: {fg_3};
    }}
    #aeroPanel QLabel#facadeThumbTitle {{
        color: {fg_0};
        font-weight: 700;
    }}
    #aeroPanel QLabel#facadeStepTodo, #aeroPanel QLabel#facadeStepDone, #aeroPanel QLabel#facadeStepCurrent {{
        border-radius: 15px;
        font-family: "{title}";
        font-weight: 700;
        font-size: 10pt;
        qproperty-alignment: AlignCenter;
    }}
    #aeroPanel QLabel#facadeStepTodo {{
        background: {ground_3};
        color: {fg_3};
        border: 1px solid {border_soft};
    }}
    #aeroPanel QLabel#facadeStepDone {{
        background: {green_soft};
        color: {green_bright};
        border: 1px solid {green};
    }}
    #aeroPanel QLabel#facadeStepCurrent {{
        background: {green};
        color: {green_on};
        border: 1px solid {green};
    }}
    #aeroPanel QLabel#facadeStepNameTodo {{
        color: {fg_3};
        font-size: 9pt;
    }}
    #aeroPanel QLabel#facadeStepNameDone {{
        color: {fg_2};
        font-size: 9pt;
    }}
    #aeroPanel QLabel#facadeStepNameCurrent {{
        color: {fg_0};
        font-size: 9pt;
        font-weight: 700;
    }}
    #aeroPanel QFrame#facadeStepLine {{
        background: {border_soft};
        border: none;
        min-height: 2px;
        max-height: 2px;
    }}
    #aeroPanel QFrame#facadeWait {{
        background: rgba(255, 82, 16, 0.14);
        border: 1px solid {orange_bright};
        border-radius: 8px;
    }}
    #aeroPanel QLabel#facadeWaitText {{
        color: {fg_0};
        font-weight: 700;
        font-size: 11pt;
    }}
    #aeroPanel QFrame#facadeCard {{
        background: {ground_2};
        border: 1px solid {border_faint};
        border-radius: 8px;
    }}
    #aeroPanel QFrame#facadeNote {{
        background: {ground_3};
        border: 1px solid {border_faint};
        border-radius: 8px;
    }}
    #aeroPanel QLabel#facadeBig {{
        color: {fg_0};
        font-family: "{title}";
        font-size: 15pt;
        font-weight: 700;
    }}
    #aeroPanel QLabel#facadeEmpty {{
        color: {fg_2};
        font-size: 11pt;
    }}
    #aeroPanel QPushButton#facadeLink {{
        background: transparent;
        border: none;
        padding: 0;
    }}
    #aeroPanel QToolButton#aeroZoomButton {{
        min-width: 28px;
        min-height: 28px;
    }}
    #aeroPanel QProgressBar#facadeBar {{
        background: {ground_3};
        border: 1px solid {border_faint};
        border-radius: 7px;
        min-height: 14px;
        max-height: 14px;
        text-align: center;
        color: transparent;
    }}
    #aeroPanel QProgressBar#facadeBar::chunk {{
        background: {green};
        border-radius: 6px;
    }}
    """.format(ground_0=theme.GROUND_0, ground_2=theme.GROUND_2, ground_3=theme.GROUND_3, pane=theme.PANE,
               fg_0=theme.FG_0, fg_2=theme.FG_2, fg_3=theme.FG_3, green=theme.GREEN, green_bright=theme.GREEN_BRIGHT,
               green_on=theme.GREEN_ON, green_soft=theme.GREEN_SOFT, border_soft=theme.BORDER_SOFT,
               border_faint=theme.BORDER_FAINT, orange_bright=theme.ORANGE_BRIGHT, title=theme.title_family())
