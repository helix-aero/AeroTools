"""Theme HELIX AERO pour les panneaux Qt d'AeroTools.

Les valeurs viennent du design system HELIX (tokens.css, releve le 03/11/2026)
et des couleurs du logo HELIX AERO. Une couleur existe ici une fois : les
panneaux ne font que de la mise en page.

Fonds (le gris de Metashape, un seul theme) :
  ground-0 #282828 (fenetre), ground-1 #404040 (fond), ground-2 #484848 (carte),
  ground-3 #353535 (releve : champs, listes), ground-4 #555555 (survol).
Textes : fg-0 blanc, fg-1 #eeeeee (texte courant), fg-2 #b9b9b9 (secondaire).
Accent : vert de marque #1eca59 (survol #34d56b, texte dessus #06241a) — un
remplissage, jamais du texte sur fond clair.
Alerte : orange #ff5210 (outil a l'arret).
Degrade du logo : #163056 -> #139168 -> #03a132, du bas gauche vers le haut droit.
"""

import os

# --------------------------------------------------------------------------
# Jetons
# --------------------------------------------------------------------------

# Une seule palette : le gris de Metashape (Window #282828, Base #404040, Text #eeeeee),
# pour que le panneau et les fenetres d'AeroTools se fondent dans l'interface.
# Le vert de marque est le seul accent ; l'orange et le degrade du logo sont communs.
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

# Fenetre Parametres : fond sombre, sections presque noires (comme les volets de Metashape).
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



# Pas de 8 px (et demi-pas de 4), comme le design system.
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
    """Feuille de style Qt du panneau. Les objectName cibles :

      #aeroPanel      racine du panneau
      #aeroHeader     bandeau de titre
      #aeroRule       filet degrade du logo (2 px)
      #aeroEyebrow    intitule en capitales espacees
      #aeroTitle      titre d'outil
      #aeroValue      valeur mise en avant (marqueur courant)
      #aeroMeta       ligne secondaire
      #aeroBanner     bandeau du haut (logo AERO sur le fond clair #F3F4F1 du kit, pub) ; #aeroBannerTop : son filet vert
      #aeroCard       bloc de reglages ; #aeroCardAccent : bloc mis en avant (cadre vert)
      #aeroSection    titre de section d'une fenetre (parametres)
      #aeroProgress   barre de progression fine (parametres, affinage) ; #aeroProgressText : celle de la detection IA, avec son texte
      #aeroCombo      liste deroulante (choix de la detection IA)
      #aeroThumbs     listes de la fenetre de revue ; #aeroSlider : curseur de taille
      #aeroPlayOn / #aeroPlayOff  bouton rond Pause / Play
      #aeroState / #aeroStateOff  texte « Tool actif » / « Tool inactif »
      #aeroCounter    pastille-compteur
      #aeroRun        bouton d'action vert (Run, Valider quand tout est fait)
      #aeroYes / #aeroNo  Oui (liseré vert) et Non (liseré rouge) des boites de dialogue
      #aeroGhost      bouton secondaire discret ([round="true"] : rond,
                      [pill="true"] : gelule avec texte)
      #aeroZoomButton boutons +/- de la fenetre de revue
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
    /* Barre de la detection IA : le texte est dedans (vert du logo, lisible sur le fond comme sur le remplissage) */
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

    /* Bouton d'action : meme vert que le Play */
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

    /* Boutons +/- du zoom de la fenetre de revue */
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

    /* Choix exclusif : un rond vide ou plein, dans une carte qui passe au vert une fois choisie */
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

    /* Fenetre Parametres : fond sombre, sections noires, gros boutons au texte centre */
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
