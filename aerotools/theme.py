"""Theme HELIX AERO pour les panneaux Qt d'AeroTools.

Les valeurs viennent du design system HELIX (tokens.css, releve le 03/11/2026)
et des couleurs du logo HELIX AERO. Une couleur existe ici une fois : les
panneaux ne font que de la mise en page.

Fonds (theme sombre, celui du produit) :
  ground-1 #111214 (fond), ground-2 #191b1e (carte), ground-3 #222428 (releve),
  ground-4 #2b2e33 (survol d'un bouton secondaire).
Textes : fg-0 blanc, fg-1 #d5d6da (texte courant), fg-2 #8c8e96 (secondaire).
Accent : vert de marque #1eca59 (survol #34d56b, texte dessus #06241a) — un
remplissage, jamais du texte sur fond clair.
Alerte : orange #ff5210 (outil a l'arret).
Degrade du logo : #163056 -> #139168 -> #03a132, du bas gauche vers le haut droit.
"""

import os

from . import config

# --------------------------------------------------------------------------
# Jetons
# --------------------------------------------------------------------------

# Deux palettes : « light » (par defaut, surfaces papier du design system) et
# « dark » (fond encre du produit). Les constantes du module prennent les
# valeurs de la palette active (le vert est repete dans chacune : on peut
# l'ajuster par theme) ; l'orange et le degrade du logo sont communs.
_PALETTES = {
    "light": dict(
        GROUND_0="#ecece8", GROUND_1="#f6f6f4", GROUND_2="#ffffff",
        GROUND_3="#ecece8", GROUND_4="#e0e0db",
        FG_0="#111214", FG_1="#2b2e33", FG_2="#5d6067", FG_3="#8c8e96",
        FG_4="#d5d6da",
        BORDER_FAINT="rgba(17, 18, 20, 0.10)",
        BORDER_SOFT="rgba(17, 18, 20, 0.14)",
        BORDER_STRONG="rgba(17, 18, 20, 0.22)",
        FIELD_BG="#ffffff",
        OFF_TEXT="#b83504",
        GREEN="#1eca59", GREEN_BRIGHT="#34d56b", GREEN_ON="#06241a",
        GREEN_SOFT="rgba(30, 202, 89, 0.14)",
    ),
    "dark": dict(
        GROUND_0="#0b0c0e", GROUND_1="#111214", GROUND_2="#191b1e",
        GROUND_3="#222428", GROUND_4="#2b2e33",
        FG_0="#ffffff", FG_1="#d5d6da", FG_2="#8c8e96", FG_3="#6f7177",
        FG_4="#2f3236",
        BORDER_FAINT="rgba(255, 255, 255, 0.06)",
        BORDER_SOFT="rgba(255, 255, 255, 0.10)",
        BORDER_STRONG="rgba(255, 255, 255, 0.18)",
        FIELD_BG="rgba(255, 255, 255, 0.03)",
        OFF_TEXT="#ff7a3c",
        GREEN="#1eca59", GREEN_BRIGHT="#34d56b", GREEN_ON="#06241a",
        GREEN_SOFT="rgba(30, 202, 89, 0.14)",
    ),
}
MODE = "light"


def set_mode(mode):
    """Active une palette. Un nom inconnu laisse la palette claire."""
    global MODE
    if mode not in _PALETTES:
        mode = "light"
    globals().update(_PALETTES[mode])
    MODE = mode


GREEN_DEEP = "#03a132"

ORANGE = "#ff5210"
ORANGE_BRIGHT = "#ff7a3c"
ORANGE_DEEP = "#b83504"


LOGO_0 = "#163056"
LOGO_1 = "#139168"
LOGO_2 = "#03a132"


def saved_mode():
    """Mode enregistre pour ce poste ; clair au moindre souci."""
    try:
        mode = config.get("theme", "mode", "light")
    except Exception:
        return "light"
    return mode if mode in _PALETTES else "light"


try:
    set_mode(saved_mode())
except Exception:
    set_mode("light")

# Pas de 8 px (et demi-pas de 4), comme le design system.
PAD = 8
PAD_S = 4
PAD_L = 16

RADIUS = 6
RADIUS_S = 4

_FONTS_LOADED = False
_TITLE_FAMILY = "Segoe UI"
_TEXT_FAMILY = "Segoe UI"


# --------------------------------------------------------------------------
# Polices
# --------------------------------------------------------------------------

def load_fonts(QtGui):
    """Police du panneau : celle de Metashape (Segoe UI), plus lisible que
    Rubik et Manrope aux petites tailles. Rien a charger."""
    global _FONTS_LOADED
    _FONTS_LOADED = True
    return _TITLE_FAMILY, _TEXT_FAMILY


def title_family():
    return _TITLE_FAMILY


def text_family():
    return _TEXT_FAMILY


# --------------------------------------------------------------------------
# Feuille de style
# --------------------------------------------------------------------------

def tooltip_stylesheet():
    """Style des infobulles. Un QToolTip est une fenetre a part : il ne lit pas
    la feuille du panneau, on pose donc cette regle sur le widget survole."""
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
    """Style du menu d'export. Un menu deroulant est une fenetre a part : comme
    pour l'infobulle, on pose la regle sur le menu lui-meme."""
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
    """.format(
        ground_2=GROUND_2,
        fg_0=FG_0,
        fg_1=FG_1,
        green_soft=GREEN_SOFT,
        border_strong=BORDER_STRONG,
        radius_s=RADIUS_S,
        text=text_family(),
    )


def stylesheet():
    """Feuille de style Qt du panneau. Les objectName cibles :

      #aeroPanel      racine du panneau
      #aeroHeader     bandeau de titre
      #aeroRule       filet degrade du logo (2 px)
      #aeroEyebrow    intitule en capitales espacees
      #aeroTitle      titre d'outil
      #aeroValue      valeur mise en avant (marqueur courant)
      #aeroMeta       ligne secondaire
      #aeroCard       bloc de reglages
      #aeroPlayOn / #aeroPlayOff  bouton rond Pause / Play
      #aeroState / #aeroStateOff  texte « Tool actif » / « Tool inactif »
      #aeroCounter    pastille-compteur
      #aeroGhost      bouton secondaire discret ([round="true"] : rond,
                      [pill="true"] : gelule avec texte)
      #aeroLang       selecteur de langue (gelule) ; #aeroLangBtn : un bouton par
                      langue, [active="true"] pour celle qui est choisie
      #aeroGridBox    cadre du tableau des marqueurs ; #aeroGrid : la grille
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

    /* Play / Pause : bouton rond (l'icone est peinte) + etat en clair */
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

    /* Compteurs du marqueur actif */
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

    /* Boutons secondaires */
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
    #aeroPanel QPushButton#aeroGhost[round="true"]:checked {{
        background: {ground_4};
        border-color: {border_strong};
    }}

    /* Selecteur de langue : boutons accoles dans une gelule, la langue active en vert doux */
    #aeroPanel #aeroLang {{
        background: {ground_3};
        border: 1px solid {border_soft};
        border-radius: 16px;
    }}
    #aeroPanel QPushButton#aeroLangBtn {{
        background: transparent;
        border: 1px solid transparent;
        border-radius: 13px;
        color: {fg_2};
        font-family: "{title}";
        font-size: 9pt;
        font-weight: 600;
        padding: 0;
    }}
    #aeroPanel QPushButton#aeroLangBtn:hover {{
        background: {ground_4};
        color: {fg_0};
    }}
    #aeroPanel QPushButton#aeroLangBtn[active="true"] {{
        background: {green_soft};
        border-color: {green};
        color: {fg_0};
    }}

    /* Champs */
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

    /* Cases a cocher */
    #aeroPanel QCheckBox {{
        color: {fg_1};
        spacing: 8px;
        padding: 3px 0;
    }}
    #aeroPanel QCheckBox:hover {{
        color: {fg_0};
    }}
    #aeroPanel QCheckBox::indicator {{
        width: 14px;
        height: 14px;
        border-radius: 3px;
        border: 1px solid {border_strong};
        background: {field_bg};
    }}
    #aeroPanel QCheckBox::indicator:checked {{
        background: {green};
        border-color: {green};
    }}
    #aeroPanel QCheckBox::indicator:disabled {{
        border-color: {fg_4};
    }}

    /* Tableau des marqueurs : un cadre arrondi, la grille dedans sans bordure */
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

    /* Barres de defilement */
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
        title=title_family(),
        text=text_family(),
    )


# --------------------------------------------------------------------------
# Petits composants communs
# --------------------------------------------------------------------------

def eyebrow(QtWidgets, text):
    """Intitule en capitales espacees, au-dessus d'un bloc."""
    label = QtWidgets.QLabel(text.upper())
    label.setObjectName("aeroEyebrow")
    return label


def gradient_rule(QtWidgets, height=2):
    """Filet degrade du logo : la signature visuelle du panneau."""
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
