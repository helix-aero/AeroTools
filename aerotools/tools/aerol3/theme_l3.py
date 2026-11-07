"""Aero-L3 UI: the few styles the wizard needs on top of the Aero theme (`aerotools/theme.py`).

Sizes are written in `pt` on purpose: Windows display scaling and the Qt harness grow the fonts, and a size in pixels would not follow.
"""

from ... import theme


def stylesheet():
    """Style sheet appended to `theme.stylesheet()` on the root widget of the wizard."""
    return """
    #aeroPanel QLabel#l3StepTodo, #aeroPanel QLabel#l3StepDone, #aeroPanel QLabel#l3StepCurrent {{
        border-radius: 15px;
        font-family: "{title}";
        font-weight: 700;
        font-size: 10pt;
        qproperty-alignment: AlignCenter;
    }}
    #aeroPanel QLabel#l3StepTodo {{ background: {ground_3}; color: {fg_3}; border: 1px solid {border_soft}; }}
    #aeroPanel QLabel#l3StepDone {{ background: {green_soft}; color: {green_bright}; border: 1px solid {green}; }}
    #aeroPanel QLabel#l3StepCurrent {{ background: {green}; color: {green_on}; border: 1px solid {green}; }}
    #aeroPanel QLabel#l3StepNameTodo {{ color: {fg_3}; font-size: 9pt; }}
    #aeroPanel QLabel#l3StepNameDone {{ color: {fg_2}; font-size: 9pt; }}
    #aeroPanel QLabel#l3StepNameCurrent {{ color: {fg_0}; font-size: 9pt; font-weight: 700; }}
    #aeroPanel QFrame#l3StepLine {{ background: {border_soft}; border: none; min-height: 2px; max-height: 2px; }}
    #aeroPanel QFrame#l3Card {{
        background: {ground_2};
        border: 1px solid {border_faint};
        border-radius: 8px;
    }}
    #aeroPanel QLabel#l3Big {{ color: {fg_0}; font-family: "{title}"; font-size: 15pt; font-weight: 700; }}
    #aeroPanel QLabel#l3Head {{ color: {fg_0}; font-size: 11pt; font-weight: 700; }}
    #aeroPanel QLabel#l3Hint {{ color: {fg_2}; font-size: 9.5pt; }}
    #aeroPanel QLabel#l3Good {{ color: {green_bright}; font-size: 11pt; font-weight: 700; }}
    #aeroPanel QLabel#l3Bad {{ color: {orange_bright}; font-size: 11pt; font-weight: 700; }}
    #aeroPanel QLabel#l3BadgeError, #aeroPanel QLabel#l3BadgeWarning, #aeroPanel QLabel#l3BadgeInfo {{
        border-radius: 4px;
        padding: 2px 6px;
        font-size: 8.5pt;
        font-weight: 700;
        qproperty-alignment: AlignCenter;
    }}
    #aeroPanel QLabel#l3BadgeError {{ background: {orange}; color: #ffffff; }}
    #aeroPanel QLabel#l3BadgeWarning {{ background: rgba(255, 122, 60, 0.22); color: {orange_bright}; border: 1px solid {orange_bright}; }}
    #aeroPanel QLabel#l3BadgeInfo {{ background: {ground_3}; color: {fg_1}; border: 1px solid {border_soft}; }}
    #aeroPanel QProgressBar#l3Bar {{
        background: {ground_3};
        border: 1px solid {border_faint};
        border-radius: 7px;
        min-height: 14px;
        max-height: 14px;
        text-align: center;
        color: transparent;
    }}
    #aeroPanel QProgressBar#l3Bar::chunk {{ background: {green}; border-radius: 6px; }}
    #aeroPanel QLabel#l3StepOk {{ color: {green_bright}; font-weight: 700; }}
    #aeroPanel QLabel#l3StepFail {{ color: {orange_bright}; font-weight: 700; }}
    #aeroPanel QLabel#l3StepWait {{ color: {fg_3}; }}
    """.format(ground_2=theme.GROUND_2, ground_3=theme.GROUND_3, fg_0=theme.FG_0, fg_1=theme.FG_1, fg_2=theme.FG_2, fg_3=theme.FG_3,
               green=theme.GREEN, green_bright=theme.GREEN_BRIGHT, green_on=theme.GREEN_ON, green_soft=theme.GREEN_SOFT,
               border_soft=theme.BORDER_SOFT, border_faint=theme.BORDER_FAINT, orange=theme.ORANGE, orange_bright=theme.ORANGE_BRIGHT,
               title=theme.title_family())
