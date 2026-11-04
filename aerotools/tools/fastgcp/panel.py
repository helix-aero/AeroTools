"""Panneau Qt de FastGCP : un dock a droite de Metashape.

Vue principale : titre, bouton Annuler, selecteur de langue et bouton de theme,
Play / Pause avec l'etat de l'outil, marqueur courant (deux compteurs), recherche, tableau des
marqueurs (ID, erreurs X / Y / Z, nombre de projections, colonnes triables).
Le pied porte le statut, le menu Exporter et l'engrenage ; le reste
(enregistrement des pointages, journal) est dans la carte « Plus », repliee par
defaut.

Le filtre des photos, la synchronisation avec le panneau Reference et le
passage a la photo suivante sont automatiques : le panneau ne les propose pas.

Regles de ce module :
  - aucun appel direct a Metashape : tout passe par `msapi` ;
  - aucun style en dur : couleurs, marges et objectName viennent de `theme` ;
  - aucune phrase d'aide, et aucune infobulle hors des deux pastilles-compteur ;
  - aucun texte visible en dur : tout passe par `i18n.t(...)`, et `retranslate()`
    remet le panneau dans la langue choisie sans le recreer ;
  - jamais d'exception vers l'appelant : une reference de marqueur devient
    invalide (RuntimeError) des que l'on change de chunk, le panneau doit
    survivre et relire la liste.
"""

import functools
import math
import re
import time

from ... import config, i18n, journal, msapi, theme
from ...qt import QtCore, QtGui, QtWidgets
from . import aigcp, history, qc

# Lignes visibles dans le journal deplie.
LOG_VISIBLE_LINES = 6

# Largeur mini du dock : le tableau des marqueurs a besoin de place.
MIN_PANEL_WIDTH = 420

# Texte de chaque choix des listes deroulantes de la detection IA (cle de texte litterale,
# pour que le test des textes inutilises la voie).
AIGCP_CHOICE_TEXTS = {
    ("type", "damier"): "aigcp.type_damier",
    ("type", "bombe"): "aigcp.type_bombe",
    ("scope", "marker"): "aigcp.scope_marker",
    ("scope", "all"): "aigcp.scope_all",
    ("ignore", "both"): "aigcp.ignore_both",
    ("ignore", "green"): "aigcp.ignore_green",
    ("ignore", "blue"): "aigcp.ignore_blue",
    ("ignore", "none"): "aigcp.ignore_none",
}

# Largeur d'un bouton du selecteur de langue (code a deux lettres).
LANG_BUTTON_W = 32

# Hauteur mini du tableau des marqueurs (il prend tout le reste du dock).
LIST_MIN_HEIGHT = 120

# Colonnes du tableau : (cle de texte de l'intitule, cle de tri, largeur en pixels ;
# 0 = s'elargit). L'intitule se lit avec `i18n.t`, voir `column_titles()`.
COLUMNS = (
    ("panel.col.id", "id", 0),
    ("panel.col.x", "x", 76),
    ("panel.col.y", "y", 76),
    ("panel.col.z", "z", 76),
    ("panel.col.proj", "proj", 54),
)
HEADER_HEIGHT = 30
HEADER_FONT_PX = 12
NO_VALUE = "–"

# Entrees du menu Exporter : (cle, cle de texte). Les fonctions sont dans report.py.
EXPORTS = (
    ("pdf", "panel.export.pdf"),
    ("csv", "panel.export.csv"),
)

# Largeur maximale du texte du bouton Exporter : au-dela, il est elide.
EXPORT_TEXT_MAX_PX = 150

# Qt ignore `letter-spacing` dans les feuilles de style : on l'applique sur la
# police des intitules (en pixels, comme la valeur du theme).
EYEBROW_SPACING = 1.4

# Un clic sur un nouvel item declenche currentItemChanged puis itemClicked :
# on ne traite qu'une fois le meme marqueur dans cette fenetre.
PICK_DEBOUNCE_S = 0.2

DOT_SIZE = 10    # diametre de la pastille de la liste, en pixels
ROW_HEIGHT = 28  # hauteur d'une ligne de la liste
_DOT_ICONS = {}
_ICONS = {}

# Les icones sont peintes a deux fois la taille puis marquees comme telles :
# nettes sur ecran normal et sur ecran a forte densite.
ICON_SCALE = 2

ROLE_MARKER = QtCore.Qt.UserRole
ROLE_LABEL = QtCore.Qt.UserRole + 1


# ----------------------------------------------------------------------
# Tableau : formats et tris (sans Qt, testables seuls)
# ----------------------------------------------------------------------

def format_error(value):
    """Erreur en metres, format court (0.012). Sans valeur : un tiret."""
    if value is None:
        return NO_VALUE
    try:
        number = float(value)
    except (TypeError, ValueError):
        return NO_VALUE
    if number != number or number in (float("inf"), float("-inf")):
        return NO_VALUE
    magnitude = abs(number)
    if magnitude >= 1000:
        text = "{:.1f}".format(number)
    elif magnitude >= 100:
        text = "{:.2f}".format(number)
    else:
        text = "{:.3f}".format(number)
    # -0.000 n'apprend rien : on l'ecrit 0.000.
    return text[1:] if text.startswith("-") and float(text) == 0 else text


def natural_key(label):
    """Cle de tri naturel : GCP2 avant GCP10, sans tenir compte de la casse."""
    parts = re.split(r"(\d+)", str(label).lower())
    return tuple((0, int(part)) if part.isdigit() else (1, part) for part in parts if part)


def sort_rows(rows, key, descending=False):
    """Trie les lignes du tableau. `key` : "id", "x", "y", "z", "proj" ou None.

    None (ou une cle inconnue) garde l'ordre du projet. L'ID se trie en ordre
    naturel ; les erreurs et le nombre de projections se trient en valeur
    absolue, les lignes sans valeur restent toujours a la fin, quel que soit
    le sens. A egalite, l'ordre du projet est conserve.
    """
    rows = list(rows)
    if key == "id":
        return sorted(rows, key=lambda row: natural_key(row["label"]), reverse=descending)
    if key not in ("x", "y", "z", "proj"):
        return rows
    valued = [row for row in rows if row.get(key) is not None]
    missing = [row for row in rows if row.get(key) is None]
    valued.sort(key=lambda row: abs(row[key]), reverse=descending)
    return valued + missing


def _qcolor(css):
    """QColor depuis une couleur du theme, y compris au format rgba(r, g, b, a)."""
    if css.startswith("rgba"):
        numbers = [float(part) for part in re.findall(r"[\d.]+", css)]
        red, green, blue = [int(n) for n in numbers[:3]]
        return QtGui.QColor(red, green, blue, int(round(numbers[3] * 255)))
    return QtGui.QColor(css)


def _text_width(metrics, text):
    """Largeur d'un texte en pixels (Qt 5.11+ et Qt 6)."""
    if hasattr(metrics, "horizontalAdvance"):
        return metrics.horizontalAdvance(text)
    return metrics.width(text)


def column_titles():
    """Intitules du tableau dans la langue courante."""
    return [i18n.t(key) for key, _sort, _width in COLUMNS]


def _repolish(widget):
    """Reapplique la feuille de style apres un changement d'objectName."""
    try:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()
    except RuntimeError:
        pass


def _eyebrow(text):
    """Intitule du theme (capitales espacees), elide s'il est trop long.

    Meme objectName que `theme.eyebrow`, avec l'espacement de lettres que le
    QSS ne sait pas faire.
    """
    label = _ElidedLabel("aeroEyebrow")
    font = label.font()
    font.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, EYEBROW_SPACING)
    label.setFont(font)
    label.set_full_text(text.upper())
    return label


# ----------------------------------------------------------------------
# Icones peintes (QPainter -> QPixmap -> QIcon), jamais de caractere unicode
# ----------------------------------------------------------------------

def _paint(width, height, draw):
    """Pixmap transparent `width` x `height` (px logiques), dessine par `draw(painter)`."""
    pixmap = QtGui.QPixmap(width * ICON_SCALE, height * ICON_SCALE)
    pixmap.fill(QtCore.Qt.transparent)
    pixmap.setDevicePixelRatio(ICON_SCALE)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    try:
        draw(painter)
    finally:
        painter.end()
    return pixmap


def _pen(color, width):
    pen = QtGui.QPen(QtGui.QColor(color), width)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    pen.setJoinStyle(QtCore.Qt.RoundJoin)
    return pen


def _cached(key, build):
    pixmap = _ICONS.get(key)
    if pixmap is None:
        pixmap = build()
        _ICONS[key] = pixmap
    return pixmap


# Bouton rond Play / Pause : une seule icone, sans texte.
PLAY_BUTTON = 36
PLAY_ICON = 16


def _play_pixmap(running, color):
    def build():
        def draw(painter):
            painter.setBrush(QtGui.QColor(color))
            if running:
                # PAUSE : deux barres.
                painter.setPen(QtCore.Qt.NoPen)
                painter.drawRoundedRect(QtCore.QRectF(3.2, 2.2, 3.2, 11.6), 1.2, 1.2)
                painter.drawRoundedRect(QtCore.QRectF(9.6, 2.2, 3.2, 11.6), 1.2, 1.2)
            else:
                # PLAY : triangle, decale a droite pour paraitre centre.
                painter.setPen(_pen(color, 1.4))
                painter.drawPolygon(QtGui.QPolygonF([
                    QtCore.QPointF(4.6, 2.6),
                    QtCore.QPointF(13.0, 8.0),
                    QtCore.QPointF(4.6, 13.4),
                ]))
        return _paint(PLAY_ICON, PLAY_ICON, draw)
    return _cached(("play", running, color), build)


def _play_icon(running):
    """PAUSE quand l'outil tourne (sur le vert), PLAY quand il est coupe."""
    color = theme.GREEN_ON if running else theme.FG_1
    return QtGui.QIcon(_play_pixmap(running, color))


STATE_DOT = 10


def _state_dot_pixmap(running):
    """Petite pastille ronde : verte si l'outil tourne, grise sinon."""
    color = theme.GREEN if running else theme.FG_2

    def build():
        def draw(painter):
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor(color))
            painter.drawEllipse(QtCore.QRectF(0.5, 0.5, STATE_DOT - 1.0, STATE_DOT - 1.0))
        return _paint(STATE_DOT, STATE_DOT, draw)
    return _cached(("state", running, color), build)


COUNTER_ICON = 14


def _counter_pixmap(kind, color):
    def build():
        def draw(painter):
            size = float(COUNTER_ICON)
            if kind == "target":
                painter.setPen(_pen(color, 1.4))
                painter.setBrush(QtCore.Qt.NoBrush)
                painter.drawEllipse(QtCore.QPointF(size / 2, size / 2), 5.4, 5.4)
                painter.setPen(QtCore.Qt.NoPen)
                painter.setBrush(QtGui.QColor(color))
                painter.drawEllipse(QtCore.QPointF(size / 2, size / 2), 1.9, 1.9)
            else:
                painter.setPen(_pen(color, 1.3))
                painter.setBrush(QtCore.Qt.NoBrush)
                painter.drawRoundedRect(QtCore.QRectF(1.2, 2.2, 11.6, 9.6), 2.2, 2.2)
                relief = QtGui.QPainterPath()
                relief.moveTo(2.8, 10.2)
                relief.lineTo(5.6, 6.6)
                relief.lineTo(7.6, 8.8)
                relief.lineTo(9.0, 7.4)
                relief.lineTo(11.2, 10.2)
                painter.drawPath(relief)
                painter.setPen(QtCore.Qt.NoPen)
                painter.setBrush(QtGui.QColor(color))
                painter.drawEllipse(QtCore.QPointF(9.6, 5.0), 0.95, 0.95)
        return _paint(COUNTER_ICON, COUNTER_ICON, draw)
    return _cached(("counter", kind, color), build)


GEAR_ICON = 18


def _gear_pixmap(color):
    def build():
        def draw(painter):
            size = float(GEAR_ICON)
            centre = size / 2
            outer, inner, teeth = 8.2, 6.0, 8
            path = QtGui.QPainterPath()
            path.setFillRule(QtCore.Qt.OddEvenFill)
            points = []
            step = math.pi * 2 / teeth
            half_tooth = step * 0.22
            half_gap = step * 0.28
            for i in range(teeth):
                mid = i * step - math.pi / 2
                for angle, radius in (
                    (mid - half_gap - half_tooth, inner),
                    (mid - half_tooth, outer),
                    (mid + half_tooth, outer),
                    (mid + half_gap + half_tooth, inner),
                ):
                    points.append(QtCore.QPointF(
                        centre + radius * math.cos(angle),
                        centre + radius * math.sin(angle),
                    ))
            path.addPolygon(QtGui.QPolygonF(points + [points[0]]))
            path.addEllipse(QtCore.QPointF(centre, centre), 2.6, 2.6)
            painter.setPen(_pen(color, 0.8))
            painter.setBrush(QtGui.QColor(color))
            painter.drawPath(path)
        return _paint(GEAR_ICON, GEAR_ICON, draw)
    return _cached(("gear", color), build)


THEME_ICON = 18


def _theme_pixmap(mode, color):
    """Lune (theme clair : on passe au sombre) ou soleil (theme sombre)."""
    def build():
        def draw(painter):
            size = float(THEME_ICON)
            centre = size / 2
            if mode == "light":
                moon = QtGui.QPainterPath()
                moon.addEllipse(QtCore.QPointF(centre - 0.4, centre), 6.4, 6.4)
                bite = QtGui.QPainterPath()
                bite.addEllipse(QtCore.QPointF(centre + 3.2, centre - 2.6), 5.4, 5.4)
                painter.setPen(QtCore.Qt.NoPen)
                painter.setBrush(QtGui.QColor(color))
                painter.drawPath(moon.subtracted(bite))
            else:
                painter.setPen(QtCore.Qt.NoPen)
                painter.setBrush(QtGui.QColor(color))
                painter.drawEllipse(QtCore.QPointF(centre, centre), 3.5, 3.5)
                painter.setPen(_pen(color, 1.4))
                for i in range(8):
                    angle = i * math.pi / 4
                    painter.drawLine(
                        QtCore.QPointF(centre + 5.7 * math.cos(angle), centre + 5.7 * math.sin(angle)),
                        QtCore.QPointF(centre + 7.6 * math.cos(angle), centre + 7.6 * math.sin(angle)),
                    )
        return _paint(THEME_ICON, THEME_ICON, draw)
    return _cached(("theme", mode, color), build)


UNDO_ICON = 18


def _undo_pixmap(color, opacity=1.0):
    """Fleche courbe qui revient en arriere (annuler)."""
    def build():
        def draw(painter):
            painter.setOpacity(opacity)
            painter.setPen(_pen(color, 1.6))
            painter.setBrush(QtCore.Qt.NoBrush)
            path = QtGui.QPainterPath()
            path.moveTo(4.6, 6.4)
            path.lineTo(10.2, 6.4)
            path.cubicTo(13.6, 6.4, 15.4, 8.4, 15.4, 10.9)
            path.cubicTo(15.4, 13.4, 13.4, 15.2, 10.6, 15.2)
            path.lineTo(6.4, 15.2)
            painter.drawPath(path)
            head = QtGui.QPainterPath()
            head.moveTo(7.6, 3.2)
            head.lineTo(4.0, 6.4)
            head.lineTo(7.6, 9.6)
            painter.drawPath(head)
        return _paint(UNDO_ICON, UNDO_ICON, draw)
    return _cached(("undo", color, opacity), build)


def _undo_icon():
    """Icone du bouton Annuler, avec sa version grisee."""
    icon = QtGui.QIcon()
    icon.addPixmap(_undo_pixmap(theme.FG_2), QtGui.QIcon.Normal)
    icon.addPixmap(_undo_pixmap(theme.FG_3, 0.45), QtGui.QIcon.Disabled)
    return icon


EXPORT_ICON = 16


def _export_pixmap(color):
    """Fleche qui descend dans un bac (exporter)."""
    def build():
        def draw(painter):
            painter.setPen(_pen(color, 1.5))
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.drawLine(QtCore.QPointF(8.0, 2.0), QtCore.QPointF(8.0, 9.6))
            head = QtGui.QPainterPath()
            head.moveTo(4.8, 6.6)
            head.lineTo(8.0, 9.8)
            head.lineTo(11.2, 6.6)
            painter.drawPath(head)
            tray = QtGui.QPainterPath()
            tray.moveTo(2.6, 10.4)
            tray.lineTo(2.6, 13.4)
            tray.lineTo(13.4, 13.4)
            tray.lineTo(13.4, 10.4)
            painter.drawPath(tray)
        return _paint(EXPORT_ICON, EXPORT_ICON, draw)
    return _cached(("export", color), build)


def _dot_icon(done):
    """Pastille ronde peinte : verte si le marqueur est pointe, grise sinon.

    On la dessine au lieu d'utiliser les caracteres ● ○ : leur presence depend
    de la police et ils ne prennent pas la couleur du theme dans une liste QSS.
    """
    icon = _DOT_ICONS.get(done)
    if icon is not None:
        return icon
    size = DOT_SIZE + 2
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    if done:
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(theme.GREEN))
    else:
        painter.setPen(QtGui.QPen(QtGui.QColor(theme.FG_3), 1.4))
        painter.setBrush(QtCore.Qt.NoBrush)
    painter.drawEllipse(1, 1, DOT_SIZE - 1, DOT_SIZE - 1)
    painter.end()
    icon = QtGui.QIcon(pixmap)
    _DOT_ICONS[done] = icon
    return icon


# ----------------------------------------------------------------------
# Petits widgets
# ----------------------------------------------------------------------

class _ElidedLabel(QtWidgets.QLabel):
    """Etiquette sur une ligne, tronquee par « … » au lieu d'elargir le dock."""

    def __init__(self, object_name, parent=None):
        super(_ElidedLabel, self).__init__(parent)
        self.setObjectName(object_name)
        self._full = ""
        self.setMinimumWidth(0)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred
        )

    def set_full_text(self, text):
        self._full = text or ""
        self._elide()

    def _elide(self):
        width = self.width()
        if width <= 0:
            shown = self._full
        else:
            shown = self.fontMetrics().elidedText(
                self._full, QtCore.Qt.ElideRight, width
            )
        if shown != self.text():
            QtWidgets.QLabel.setText(self, shown)

    def resizeEvent(self, event):
        super(_ElidedLabel, self).resizeEvent(event)
        self._elide()

    def changeEvent(self, event):
        super(_ElidedLabel, self).changeEvent(event)
        if event.type() in (QtCore.QEvent.FontChange, QtCore.QEvent.StyleChange):
            self._elide()


class _ElidedButton(QtWidgets.QPushButton):
    """Bouton pleine largeur dont le texte est tronque par des points de suspension s'il ne tient pas."""

    def __init__(self, text="", parent=None):
        super(_ElidedButton, self).__init__(parent)
        self._full = ""
        self.setMinimumWidth(0)
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        self.set_full_text(text)

    def set_full_text(self, text):
        self._full = text or ""
        self._elide()

    def _elide(self):
        room = self.width() - 28
        shown = self._full
        if room > 0:
            shown = self.fontMetrics().elidedText(self._full, QtCore.Qt.ElideRight, room)
        if shown != self.text():
            QtWidgets.QPushButton.setText(self, shown)

    def resizeEvent(self, event):
        super(_ElidedButton, self).resizeEvent(event)
        self._elide()

    def changeEvent(self, event):
        super(_ElidedButton, self).changeEvent(event)
        if event.type() in (QtCore.QEvent.FontChange, QtCore.QEvent.StyleChange):
            self._elide()


class _ElidedCheck(QtWidgets.QCheckBox):
    """Case a cocher dont le texte est tronque par des points de suspension s'il ne tient pas."""

    INDICATOR_ROOM = 14 + 8 + 6   # case, espace, marge

    def __init__(self, text="", parent=None):
        super(_ElidedCheck, self).__init__(parent)
        self._full = ""
        self.setMinimumWidth(0)
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        self.set_full_text(text)

    def set_full_text(self, text):
        self._full = text or ""
        self._elide()

    def _elide(self):
        room = self.width() - self.INDICATOR_ROOM
        shown = self._full
        if room > 0:
            shown = self.fontMetrics().elidedText(self._full, QtCore.Qt.ElideRight, room)
        if shown != self.text():
            QtWidgets.QCheckBox.setText(self, shown)

    def resizeEvent(self, event):
        super(_ElidedCheck, self).resizeEvent(event)
        self._elide()

    def changeEvent(self, event):
        super(_ElidedCheck, self).changeEvent(event)
        if event.type() in (QtCore.QEvent.FontChange, QtCore.QEvent.StyleChange):
            self._elide()


class _GridHeader(QtWidgets.QHeaderView):
    """En-tete du tableau, peint a la main : fond du cadre, intitule discret,
    filet dessous, fleche de tri dessinee aux couleurs du theme (le QSS ne sait
    pas peindre la fleche sans image externe)."""

    PAD_X = 7
    ARROW_W = 7
    ARROW_H = 4
    GAP = 4

    def __init__(self, parent=None):
        super(_GridHeader, self).__init__(QtCore.Qt.Horizontal, parent)
        self.setSectionsClickable(True)
        self.setSectionsMovable(False)
        self.setHighlightSections(False)
        self.setSortIndicatorShown(True)
        self.setCursor(QtCore.Qt.PointingHandCursor)

    def sizeHint(self):
        base = super(_GridHeader, self).sizeHint()
        return QtCore.QSize(base.width(), HEADER_HEIGHT)

    def paintSection(self, painter, rect, logical_index):
        if not rect.isValid():
            return
        painter.save()
        try:
            self._paint(painter, rect, logical_index)
        finally:
            painter.restore()

    def _paint(self, painter, rect, index):
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        # Filet sous l'en-tete.
        painter.setPen(QtGui.QPen(_qcolor(theme.BORDER_SOFT), 1))
        painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())

        model = self.model()
        text = ""
        if model is not None:
            text = model.headerData(index, QtCore.Qt.Horizontal) or ""
        sorted_here = self.sortIndicatorSection() == index
        color = _qcolor(theme.FG_0 if sorted_here else theme.FG_1)
        font = QtGui.QFont(self.font())
        font.setPixelSize(HEADER_FONT_PX)
        font.setWeight(QtGui.QFont.DemiBold)
        painter.setFont(font)
        metrics = QtGui.QFontMetrics(font)

        right_aligned = index != 0
        room = rect.width() - self.PAD_X - 3
        if sorted_here:
            room -= self.ARROW_W + self.GAP
        shown = metrics.elidedText(str(text), QtCore.Qt.ElideRight, max(room, 0))
        width = _text_width(metrics, shown)
        arrow_total = (self.ARROW_W + self.GAP) if sorted_here else 0
        if right_aligned:
            text_x = rect.right() - self.PAD_X - width + 1
            arrow_x = text_x - self.GAP - self.ARROW_W
        else:
            text_x = rect.left() + self.PAD_X + 1
            arrow_x = text_x + width + self.GAP
        baseline = int(round(
            rect.top() + (rect.height() - 1 + metrics.ascent() - metrics.descent()) / 2.0
        ))
        painter.setPen(color)
        painter.drawText(int(text_x), baseline, shown)

        if sorted_here and arrow_total:
            middle = (rect.height() - 1) / 2.0 + rect.top()
            up = self.sortIndicatorOrder() == QtCore.Qt.AscendingOrder
            half_h = self.ARROW_H / 2.0
            top = middle - half_h
            bottom = middle + half_h
            if up:
                points = [(arrow_x, bottom), (arrow_x + self.ARROW_W, bottom),
                          (arrow_x + self.ARROW_W / 2.0, top)]
            else:
                points = [(arrow_x, top), (arrow_x + self.ARROW_W, top),
                          (arrow_x + self.ARROW_W / 2.0, bottom)]
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(color)
            painter.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(x, y) for x, y in points]))


class _NumberDelegate(QtWidgets.QStyledItemDelegate):
    """Colonnes de valeurs : texte aligne a droite, chiffres tabulaires.

    Les polices du theme n'ont pas de chiffres a chasse fixe (le 1 est plus
    etroit) et Qt 5 ne sait pas activer la fonction tnum : chaque chiffre est
    donc pose dans une cellule de la largeur du plus large, ce qui aligne les
    decimales d'une ligne a l'autre. Le fond, la selection et le survol restent
    ceux du style (donc du QSS)."""

    PAD_RIGHT = 8

    def paint(self, painter, option, index):
        opt = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text = opt.text
        opt.text = ""
        widget = opt.widget
        style = widget.style() if widget is not None else QtWidgets.QApplication.style()
        style.drawControl(QtWidgets.QStyle.CE_ItemViewItem, opt, painter, widget)

        selected = bool(opt.state & QtWidgets.QStyle.State_Selected)
        brush = index.data(QtCore.Qt.ForegroundRole)
        muted = False
        warn = False
        if brush is not None:
            try:
                name = brush.color().name().lower()
                muted = name == _qcolor(theme.FG_3).name().lower()
                warn = name == _qcolor(theme.OFF_TEXT).name().lower()
            except Exception:
                muted = warn = False
        if warn:
            color = _qcolor(theme.OFF_TEXT)
        elif selected:
            color = _qcolor(theme.FG_1 if muted else theme.FG_0)
        else:
            color = _qcolor(theme.FG_3 if muted else theme.FG_1)

        painter.save()
        try:
            painter.setFont(opt.font)
            painter.setPen(color)
            metrics = QtGui.QFontMetrics(opt.font)
            cell = max(_text_width(metrics, digit) for digit in "0123456789")
            baseline = int(round(
                opt.rect.top() + (opt.rect.height() + metrics.ascent() - metrics.descent()) / 2.0
            ))
            x = opt.rect.right() - self.PAD_RIGHT + 1
            for char in reversed(text):
                natural = _text_width(metrics, char)
                advance = cell if char.isdigit() else natural
                x -= advance
                painter.drawText(int(x + (advance - natural) / 2.0), baseline, char)
        finally:
            painter.restore()


class _Counter(QtWidgets.QFrame):
    """Pastille-compteur : une petite icone peinte et un nombre, aucun mot.

    Seule partie du panneau qui porte une infobulle. Les enfants laissent
    passer la souris : survoler l'icone ou le nombre revient a survoler la
    pastille entiere.
    """

    def __init__(self, kind, tip, parent=None):
        super(_Counter, self).__init__(parent)
        self.setObjectName("aeroCounter")
        self._kind = kind
        self.set_tip(tip)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(8, 3, 10, 3)
        layout.setSpacing(5)
        self._icon = QtWidgets.QLabel()
        self._icon.setFixedSize(COUNTER_ICON, COUNTER_ICON)
        self._number = QtWidgets.QLabel("0")
        self._number.setObjectName("aeroCounterNum")
        for child in (self._icon, self._number):
            child.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        layout.addWidget(self._icon)
        layout.addWidget(self._number)
        self.setSizePolicy(QtWidgets.QSizePolicy.Maximum, QtWidgets.QSizePolicy.Fixed)
        self.apply_theme()

    def apply_theme(self):
        """Icone aux couleurs de la palette active, infobulle au style du theme."""
        self._icon.setPixmap(_counter_pixmap(self._kind, theme.FG_1))
        self.setStyleSheet(theme.tooltip_stylesheet())

    def set_number(self, number):
        self._number.setText(str(int(number)))

    def set_tip(self, tip):
        self.setToolTip(tip)


class FastGcpPanel(QtWidgets.QDockWidget):
    """Dock FastGCP. Contrat public : voir les methodes sans underscore."""

    # Les lignes du journal peuvent venir d'un autre thread : un signal les
    # remet dans le fil de l'interface.
    _line_added = QtCore.Signal(str)

    def __init__(self, state, parent=None):
        super(FastGcpPanel, self).__init__("FastGCP", parent)
        self._state = state
        self._journal_cb = None
        self._log_open = False
        self._running = True
        self._last_pick = (None, 0.0)
        self._checks = {}
        self._lang_buttons = {}
        self._aigcp_combos = {}
        self._review_dialog = None
        self._lang_cb = None
        # Tri du tableau : (cle, decroissant) ou None = ordre du projet.
        self._sort = None
        # Nom du chunk affiche a la suite de la progression (pose par tool.py).
        self._chunk_name = ""

        self.setObjectName("AeroFastGcpDock")
        # L'en-tete maison fait office de barre : la barre native disparait.
        self.setTitleBarWidget(QtWidgets.QWidget())
        self._root = self._build_root()
        self.setWidget(self._root)

        self._load_options()
        self._refresh_icons()
        self._subscribe_journal()
        history.subscribe(self._on_history_changed)
        self._lang_cb = self.retranslate
        i18n.subscribe(self._lang_cb)
        self._refresh_lang_buttons()
        self._update_undo_button()
        self.update_active_card()
        self.set_status("")

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_root(self):
        root = QtWidgets.QWidget()
        root.setObjectName("aeroPanel")
        root.setMinimumWidth(MIN_PANEL_WIDTH)
        root.setStyleSheet(theme.stylesheet())

        outer = QtWidgets.QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QtWidgets.QScrollArea()
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)

        body = QtWidgets.QWidget()
        scroll.setWidget(body)
        # QScrollArea peint son contenu : on laisse voir le fond du panneau.
        body.setAutoFillBackground(False)
        scroll.viewport().setAutoFillBackground(False)

        column = QtWidgets.QVBoxLayout(body)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self._build_header())
        column.addWidget(theme.gradient_rule(QtWidgets))

        content = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(content)
        layout.setContentsMargins(theme.PAD_L, theme.PAD_L, theme.PAD_L, theme.PAD_L)
        layout.setSpacing(theme.PAD)
        layout.addLayout(self._build_play())
        layout.addWidget(self._build_aigcp_card())
        layout.addWidget(self._build_active_card())
        layout.addWidget(self._build_search())
        layout.addWidget(self._build_list(), 1)
        layout.addLayout(self._build_tools())
        layout.addWidget(self._build_more())
        layout.addLayout(self._build_footer())
        column.addWidget(content, 1)
        return root

    def _build_header(self):
        """Titre a gauche, a droite : Annuler, selecteur de langue, theme (lune / soleil)."""
        header = QtWidgets.QWidget()
        header.setObjectName("aeroHeader")
        layout = QtWidgets.QHBoxLayout(header)
        layout.setContentsMargins(theme.PAD_L, theme.PAD + theme.PAD_S, theme.PAD_L, theme.PAD)
        layout.setSpacing(theme.PAD)
        title = QtWidgets.QLabel("FastGCP")
        title.setObjectName("aeroTitle")
        layout.addWidget(title, 1)

        self.undo_button = QtWidgets.QPushButton()
        self.undo_button.setObjectName("aeroGhost")
        self.undo_button.setProperty("round", True)
        self.undo_button.setCursor(QtCore.Qt.PointingHandCursor)
        self.undo_button.setFixedSize(32, 32)
        self.undo_button.setIconSize(QtCore.QSize(UNDO_ICON, UNDO_ICON))
        self.undo_button.setEnabled(False)
        self.undo_button.clicked.connect(self._on_undo_clicked)
        layout.addWidget(self.undo_button, 0)

        layout.addWidget(self._build_lang_switch(), 0)

        self.theme_button = QtWidgets.QPushButton()
        self.theme_button.setObjectName("aeroGhost")
        self.theme_button.setProperty("round", True)
        self.theme_button.setCursor(QtCore.Qt.PointingHandCursor)
        self.theme_button.setFixedSize(32, 32)
        self.theme_button.setIconSize(QtCore.QSize(THEME_ICON, THEME_ICON))
        self.theme_button.clicked.connect(self._on_theme_clicked)
        layout.addWidget(self.theme_button, 0)
        return header

    def _build_lang_switch(self):
        """Selecteur segmente "FR | EN" : un bouton par langue de `i18n.LANGUES`.

        Les boutons portent le code de la langue (jamais traduit) ; celui de la
        langue active est surligne par la feuille de style (propriete `active`).
        """
        frame = QtWidgets.QFrame()
        frame.setObjectName("aeroLang")
        frame.setFixedHeight(32)
        layout = QtWidgets.QHBoxLayout(frame)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        for code in i18n.codes():
            button = QtWidgets.QPushButton(code.upper())
            button.setObjectName("aeroLangBtn")
            button.setProperty("active", False)
            button.setCursor(QtCore.Qt.PointingHandCursor)
            button.setFocusPolicy(QtCore.Qt.NoFocus)
            button.setFixedSize(LANG_BUTTON_W, 26)
            button.clicked.connect(functools.partial(self._on_lang_clicked, code))
            layout.addWidget(button)
            self._lang_buttons[code] = button
        return frame

    def _build_play(self):
        """Bouton rond Play / Pause, puis pastille et texte d'etat."""
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.PAD + theme.PAD_S)

        self.toggle = QtWidgets.QPushButton()
        self.toggle.setObjectName("aeroPlayOn")
        self.toggle.setCursor(QtCore.Qt.PointingHandCursor)
        self.toggle.setFixedSize(PLAY_BUTTON, PLAY_BUTTON)
        self.toggle.setIconSize(QtCore.QSize(PLAY_ICON, PLAY_ICON))
        self.toggle.clicked.connect(self._on_toggle_clicked)
        row.addWidget(self.toggle, 0)

        self.state_dot = QtWidgets.QLabel()
        self.state_dot.setFixedSize(STATE_DOT, STATE_DOT)
        row.addWidget(self.state_dot, 0, QtCore.Qt.AlignVCenter)

        self.state_text = _ElidedLabel("aeroState")
        self.state_text.set_full_text(i18n.t("panel.state_on"))
        row.addWidget(self.state_text, 1)
        return row

    def _build_aigcp_card(self):
        """Detection IA, en haut : type de cible, portee (marqueur actif ou tous), lancer."""
        card = QtWidgets.QFrame()
        card.setObjectName("aeroCard")
        layout = QtWidgets.QVBoxLayout(card)
        pad = theme.PAD + theme.PAD_S
        layout.setContentsMargins(pad, pad, pad, pad)
        layout.setSpacing(theme.PAD)
        self._eyebrow_aigcp = _eyebrow(i18n.t("panel.aigcp"))
        layout.addWidget(self._eyebrow_aigcp)

        choices = QtWidgets.QHBoxLayout()
        choices.setContentsMargins(0, 0, 0, 0)
        choices.setSpacing(theme.PAD)
        for key, values, stretch in (("type", aigcp.TYPES, 3), ("scope", aigcp.SCOPES, 4),
                                     ("ignore", aigcp.IGNORES, 4)):
            choices.addWidget(self._build_combo(key, values), stretch)
        layout.addLayout(choices)

        actions = QtWidgets.QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(theme.PAD)
        self.aigcp_button = self._tool_button("aigcp.run", self._on_aigcp_run)
        self.aigcp_clear_button = self._tool_button("aigcp.clear", self._on_aigcp_clear)
        self.aigcp_review_button = self._tool_button("aigcp.review", self._on_aigcp_review)
        actions.addWidget(self.aigcp_button, 3)
        actions.addWidget(self.aigcp_clear_button, 3)
        actions.addWidget(self.aigcp_review_button, 2)
        layout.addLayout(actions)

        self.aigcp_progress = QtWidgets.QProgressBar()
        self.aigcp_progress.setObjectName("aeroProgress")
        self.aigcp_progress.setTextVisible(False)
        self.aigcp_progress.setRange(0, 100)
        self.aigcp_progress.setVisible(False)
        layout.addWidget(self.aigcp_progress)
        self._retranslate_aigcp()
        return card

    def _build_combo(self, key, values):
        """Liste deroulante d'un choix de la detection IA ; les textes sont poses par la traduction."""
        combo = QtWidgets.QComboBox()
        combo.setObjectName("aeroCombo")
        combo.setCursor(QtCore.Qt.PointingHandCursor)
        combo.setFocusPolicy(QtCore.Qt.NoFocus)
        combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLength)
        combo.setMinimumContentsLength(4)
        for value in values:
            combo.addItem("", value)
        combo.activated.connect(functools.partial(self._on_aigcp_combo, key))
        self._aigcp_combos[key] = combo
        return combo

    def _build_active_card(self):
        card = QtWidgets.QFrame()
        card.setObjectName("aeroCard")
        layout = QtWidgets.QVBoxLayout(card)
        pad = theme.PAD + theme.PAD_S
        layout.setContentsMargins(pad, pad, pad, pad)
        layout.setSpacing(theme.PAD)

        head = QtWidgets.QVBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(2)
        self._eyebrow_active = _eyebrow(i18n.t("panel.active_marker"))
        head.addWidget(self._eyebrow_active)
        self.active_value = _ElidedLabel("aeroValueEmpty")
        self.active_value.set_full_text("—")
        head.addWidget(self.active_value)
        layout.addLayout(head)

        # Les pastilles gardent leur place quand elles sont masquees : la carte
        # ne change pas de hauteur d'un marqueur a l'autre.
        self.counters = QtWidgets.QWidget()
        policy = self.counters.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.counters.setSizePolicy(policy)
        row = QtWidgets.QHBoxLayout(self.counters)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.PAD)
        self.count_pointed = _Counter("target", i18n.t("panel.tip_pointed"))
        self.count_photos = _Counter("image", i18n.t("panel.tip_seen"))
        row.addWidget(self.count_pointed)
        row.addWidget(self.count_photos)
        row.addStretch(1)
        layout.addWidget(self.counters)
        return card

    def _build_search(self):
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText(i18n.t("panel.search"))
        self.search.setClearButtonEnabled(False)
        self.search.textChanged.connect(self._on_search)
        return self.search

    def _build_list(self):
        """Tableau des marqueurs : ID, erreurs X / Y / Z, nombre de projections."""
        box = QtWidgets.QFrame()
        box.setObjectName("aeroGridBox")
        box.setMinimumHeight(LIST_MIN_HEIGHT)
        box.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding
        )
        inner = QtWidgets.QVBoxLayout(box)
        # Les coins carres de la grille restent a l'abri du cadre arrondi.
        inner.setContentsMargins(3, 3, 3, 3)
        inner.setSpacing(0)

        grid = QtWidgets.QTreeWidget()
        grid.setObjectName("aeroGrid")
        self._header = _GridHeader(grid)
        grid.setHeader(self._header)
        grid.setColumnCount(len(COLUMNS))
        grid.setHeaderLabels(column_titles())
        grid.setRootIsDecorated(False)
        grid.setIndentation(0)
        grid.setItemsExpandable(False)
        grid.setUniformRowHeights(True)
        grid.setAllColumnsShowFocus(True)
        grid.setSortingEnabled(False)
        grid.setTextElideMode(QtCore.Qt.ElideRight)
        grid.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        grid.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        grid.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        grid.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        grid.setIconSize(QtCore.QSize(DOT_SIZE + 2, DOT_SIZE + 2))
        grid.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding
        )

        header = self._header
        # QTreeView.setHeader remet ces reglages a zero : on les repose apres.
        header.setSectionsClickable(True)
        header.setSectionsMovable(False)
        header.setSortIndicatorShown(True)
        header.setStretchLastSection(False)
        for column, (_title, _key, width) in enumerate(COLUMNS):
            if width:
                header.setSectionResizeMode(column, QtWidgets.QHeaderView.Fixed)
                header.resizeSection(column, width)
            else:
                header.setSectionResizeMode(column, QtWidgets.QHeaderView.Stretch)
        header.setSortIndicator(-1, QtCore.Qt.AscendingOrder)
        header.sectionClicked.connect(self._on_header_clicked)
        self._number_delegate = _NumberDelegate(grid)
        for column in range(1, len(COLUMNS)):
            grid.setItemDelegateForColumn(column, self._number_delegate)

        grid.currentItemChanged.connect(self._on_current_changed)
        grid.itemClicked.connect(self._on_item_clicked)
        inner.addWidget(grid)
        self.grid = grid
        # Ancien nom du widget, garde pour les appelants existants.
        self.list = grid
        return box

    def _build_tools(self):
        """Sous le tableau : controle des residus."""
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.PAD)
        self.qc_button = self._tool_button("panel.qc", self._show_qc_menu)
        row.addWidget(self.qc_button, 1)
        return row

    def _tool_button(self, text_key, handler):
        button = _ElidedButton(i18n.t(text_key))
        button.setObjectName("aeroGhost")
        button.setProperty("pill", True)
        button.setCursor(QtCore.Qt.PointingHandCursor)
        button.setFixedHeight(32)
        button.clicked.connect(handler)
        return button

    def _build_more(self):
        """Carte « Plus » : enregistrement des pointages et journal. Repliee au depart."""
        self.more = QtWidgets.QFrame()
        self.more.setObjectName("aeroCard")
        layout = QtWidgets.QVBoxLayout(self.more)
        pad = theme.PAD + theme.PAD_S
        layout.setContentsMargins(pad, pad, pad, pad)
        layout.setSpacing(theme.PAD_S)

        layout.addWidget(self._check("collect_dataset", i18n.t("panel.save_picks")))

        self.log_button = _ElidedButton(i18n.t("panel.log"))
        self.log_button.setCursor(QtCore.Qt.PointingHandCursor)
        self.log_button.clicked.connect(self._on_log_button)
        layout.addSpacing(theme.PAD_S)
        layout.addWidget(self.log_button)

        self.log = QtWidgets.QPlainTextEdit()
        self.log.setObjectName("aeroLog")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(journal.MAX_LINES)
        self.log.setLineWrapMode(QtWidgets.QPlainTextEdit.WidgetWidth)
        self.log.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.log.setVisible(False)
        layout.addWidget(self.log)

        self.more.setVisible(False)
        return self.more

    def _check(self, key, text):
        box = _ElidedCheck(text)
        self._checks[key] = box
        box.toggled.connect(functools.partial(self._on_check, key))
        return box

    def _build_footer(self):
        """Statut a gauche, bouton Exporter puis engrenage rond a droite."""
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.PAD)
        self.status = _ElidedLabel("aeroStatus")
        row.addWidget(self.status, 1)

        self.export_button = QtWidgets.QPushButton()
        self._set_export_text()
        self.export_button.setObjectName("aeroGhost")
        self.export_button.setProperty("pill", True)
        self.export_button.setCursor(QtCore.Qt.PointingHandCursor)
        self.export_button.setFixedHeight(32)
        self.export_button.setIconSize(QtCore.QSize(EXPORT_ICON, EXPORT_ICON))
        self.export_button.clicked.connect(self._show_export_menu)
        self.export_menu = QtWidgets.QMenu(self.export_button)
        self._export_actions = {}
        for kind, text_key in EXPORTS:
            action = self.export_menu.addAction(i18n.t(text_key))
            action.triggered.connect(functools.partial(self._on_export, kind))
            self._export_actions[kind] = action
        row.addWidget(self.export_button, 0)

        self.more_button = QtWidgets.QPushButton()
        self.more_button.setObjectName("aeroGhost")
        self.more_button.setProperty("round", True)
        self.more_button.setCheckable(True)
        self.more_button.setCursor(QtCore.Qt.PointingHandCursor)
        self.more_button.setFixedSize(32, 32)
        self.more_button.setIconSize(QtCore.QSize(GEAR_ICON, GEAR_ICON))
        self.more_button.toggled.connect(self._on_more_toggled)
        row.addWidget(self.more_button, 0)
        return row

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------

    def _refresh_icons(self):
        """Repeint toutes les icones aux couleurs de la palette active."""
        try:
            self._apply_toggle(self._running)
            self.more_button.setIcon(QtGui.QIcon(_gear_pixmap(theme.FG_2)))
            self.theme_button.setIcon(QtGui.QIcon(_theme_pixmap(theme.MODE, theme.FG_2)))
            self.undo_button.setIcon(_undo_icon())
            self.export_button.setIcon(QtGui.QIcon(_export_pixmap(theme.FG_2)))
            self.export_menu.setStyleSheet(theme.menu_stylesheet())
            self._paint_placeholder()
            self.count_pointed.apply_theme()
            self.count_photos.apply_theme()
        except RuntimeError:
            pass

    def _paint_placeholder(self):
        """Texte indicatif de la recherche en FG_2 (le QSS le laisse trop pale
        sur certaines versions de Qt)."""
        palette = self.search.palette()
        role = getattr(QtGui.QPalette, "PlaceholderText", None)
        if role is None:
            return
        palette.setColor(role, _qcolor(theme.FG_2))
        self.search.setPalette(palette)

    def _on_theme_clicked(self):
        """Bascule clair / sombre, enregistre le choix et repeint tout le panneau."""
        mode = "dark" if theme.MODE == "light" else "light"
        theme.set_mode(mode)
        try:
            config.put("theme", "mode", mode)
        except Exception as exc:
            journal.write("FastGCP : theme non enregistre : {!r}".format(exc))
        self._apply_theme()
        journal.write("Theme {}".format("sombre" if mode == "dark" else "clair"))

    def _apply_theme(self):
        try:
            self._root.setStyleSheet(theme.stylesheet())
            _DOT_ICONS.clear()
            _ICONS.clear()
            self._refresh_icons()
            self.refresh_markers()
            self._header.viewport().update()
        except RuntimeError:
            pass

    # ------------------------------------------------------------------
    # Langue
    # ------------------------------------------------------------------

    def _on_lang_clicked(self, code, _checked=False):
        """Un clic sur FR / EN : change la langue, `retranslate` suit par abonnement."""
        # Abonnement (re)pose a chaque clic : un abonnement perdu ne doit pas
        # rendre le selecteur muet jusqu'au prochain redemarrage de l'outil.
        if self._lang_cb is not None:
            i18n.subscribe(self._lang_cb)
        if code == i18n.lang():
            self.retranslate()
            return
        if not i18n.set_lang(code):
            return
        journal.write("Langue : {}".format(code))

    def _refresh_lang_buttons(self):
        """Surligne le bouton de la langue active."""
        current = i18n.lang()
        for code, button in self._lang_buttons.items():
            wanted = code == current
            try:
                if bool(button.property("active")) != wanted:
                    button.setProperty("active", wanted)
                _repolish(button)
            except RuntimeError:
                pass

    def _set_export_text(self):
        """Texte du bouton Exporter, elide s'il est trop long dans la langue choisie."""
        text = i18n.t("panel.export")
        metrics = self.export_button.fontMetrics()
        self.export_button.setText(
            metrics.elidedText(text, QtCore.Qt.ElideRight, EXPORT_TEXT_MAX_PX))

    def retranslate(self):
        """Remet tout le panneau dans la langue courante, sans le recreer.

        Appele par l'abonnement i18n. Les textes vivants (statut, carte du
        marqueur, grille) sont relus ; le statut revient au nombre de marqueurs.
        """
        try:
            self.objectName()
        except RuntimeError:
            # Panneau deja detruit par Qt : plus rien a traduire.
            i18n.unsubscribe(self._lang_cb)
            return
        # Le surlignage FR / EN d'abord : il suit la langue meme si une etape
        # plus bas echoue. Chaque etape est isolee, et une erreur (Metashape
        # leve des RuntimeError pour des objets perimes) ne coupe ni les etapes
        # suivantes ni l'abonnement : sinon le selecteur resterait muet.
        for name, step in (
            ("lang_buttons", self._refresh_lang_buttons),
            ("eyebrow", self._retranslate_eyebrow),
            ("search", self._retranslate_search),
            ("grid", self._retranslate_grid),
            ("checks", self._retranslate_checks),
            ("bottom_buttons", self._retranslate_buttons),
            ("aigcp", self._retranslate_aigcp),
            ("export", self._retranslate_export),
            ("state", lambda: self._apply_toggle(self._running)),
            ("markers", self.refresh_markers),
            ("card", self.update_active_card),
        ):
            try:
                step()
            except Exception as exc:
                try:
                    journal.write("Langue : etape {} en erreur : {!r}".format(name, exc))
                except Exception:
                    pass

    def _retranslate_eyebrow(self):
        self._eyebrow_active.set_full_text(i18n.t("panel.active_marker").upper())

    def _retranslate_search(self):
        self.search.setPlaceholderText(i18n.t("panel.search"))

    def _retranslate_grid(self):
        head = self.grid.headerItem()
        for column, title in enumerate(column_titles()):
            head.setText(column, title)
        self._header.viewport().update()

    def _retranslate_checks(self):
        for box in self._checks.values():
            box.set_full_text(i18n.t("panel.save_picks"))

    def _retranslate_buttons(self):
        self.log_button.set_full_text(i18n.t("panel.log"))
        self.qc_button.set_full_text(i18n.t("panel.qc"))
        self.count_pointed.set_tip(i18n.t("panel.tip_pointed"))
        self.count_photos.set_tip(i18n.t("panel.tip_seen"))

    def _retranslate_aigcp(self):
        """Textes de la carte Detection IA, choix affiches, etat des boutons."""
        self._eyebrow_aigcp.set_full_text(i18n.t("panel.aigcp").upper())
        for key, combo in self._aigcp_combos.items():
            for index in range(combo.count()):
                combo.setItemText(index, i18n.t(AIGCP_CHOICE_TEXTS[(key, combo.itemData(index))]))
        self.aigcp_button.set_full_text(i18n.t("aigcp.run"))
        self.aigcp_clear_button.set_full_text(i18n.t("aigcp.clear"))
        self.aigcp_review_button.set_full_text(i18n.t("aigcp.review"))
        self.update_aigcp()

    def _retranslate_export(self):
        self._set_export_text()
        for kind, text_key in EXPORTS:
            action = self._export_actions.get(kind)
            if action is not None:
                action.setText(i18n.t(text_key))

    # ------------------------------------------------------------------
    # Options
    # ------------------------------------------------------------------

    def _load_options(self):
        """Pose les widgets depuis `state` sans declencher les signaux."""
        state = self._state
        if state is None:
            return
        try:
            self._apply_toggle(bool(state.enabled))

            for key, box in self._checks.items():
                box.blockSignals(True)
                box.setChecked(bool(state.option(key)))
                box.blockSignals(False)

            self._set_log_open(bool(state.option("show_log")), persist=False)
        except Exception as exc:
            journal.write("FastGCP : lecture des reglages : {!r}".format(exc))

    def _set_option(self, key, value):
        state = self._state
        if state is None:
            return
        try:
            state.set_option(key, value)
        except Exception as exc:
            journal.write("FastGCP : reglage {} non enregistre : {!r}".format(key, exc))

    def _on_check(self, key, checked):
        self._set_option(key, bool(checked))
        journal.write("Reglage {} : {}".format(key, "oui" if checked else "non"))

    # ------------------------------------------------------------------
    # Play / Pause
    # ------------------------------------------------------------------

    def _apply_toggle(self, running):
        """Aligne bouton, pastille et texte d'etat sur `running`."""
        self._running = bool(running)
        self.toggle.setObjectName("aeroPlayOn" if running else "aeroPlayOff")
        self.toggle.setIcon(_play_icon(running))
        self.state_dot.setPixmap(_state_dot_pixmap(running))
        self.state_text.set_full_text(
            i18n.t("panel.state_on" if running else "panel.state_off"))
        self.state_text.setObjectName("aeroState" if running else "aeroStateOff")
        _repolish(self.toggle)
        _repolish(self.state_text)
        self._update_undo_button()

    def _on_toggle_clicked(self):
        state = self._state
        if state is None:
            return
        running = not bool(state.enabled)
        state.enabled = running
        self._apply_toggle(running)
        # Le raccourci Ctrl+Z (tool.py) suit l'etat de l'outil.
        history.notify()
        journal.write("FastGCP {}".format("actif" if running else "en pause"))

    # ------------------------------------------------------------------
    # Carte « Plus »
    # ------------------------------------------------------------------

    def _on_more_toggled(self, opened):
        # Etat volontairement non memorise : la carte est repliee a chaque demarrage.
        self.more.setVisible(bool(opened))
        if opened and self._log_open:
            self._fit_log_height()
            self._scroll_log_to_end()

    # ------------------------------------------------------------------
    # Marqueur courant
    # ------------------------------------------------------------------

    def update_active_card(self):
        """Relit le marqueur courant depuis `state` et redessine la carte."""
        state = self._state
        if state is None:
            return
        try:
            label = state.active_label()
        except Exception:
            label = None

        if label:
            wanted = "aeroValue"
            self.active_value.set_full_text(label)
            self.count_pointed.set_number(self._pointed_count(state.active_marker))
            self.count_photos.set_number(getattr(state, "last_photo_count", 0) or 0)
            self.counters.setVisible(True)
        else:
            wanted = "aeroValueEmpty"
            self.active_value.set_full_text("—")
            self.counters.setVisible(False)

        if self.active_value.objectName() != wanted:
            self.active_value.setObjectName(wanted)
            _repolish(self.active_value)
        self._refresh_row(label)

    @staticmethod
    def _pointed_count(marker):
        if marker is None:
            return 0
        try:
            return int(msapi.projection_count(marker))
        except Exception:
            return 0

    def set_active_marker_by_label(self, label, from_sync=False):
        """Rend courant le marqueur de ce nom (selection du panneau Reference)."""
        state = self._state
        if state is None:
            return False
        try:
            marker = self._find_marker(label)
            if marker is None:
                journal.write("Marqueur introuvable dans le chunk : {}".format(label))
                return False
            state.active_marker = marker
            self._select_row(label)
            self.update_active_card()
            journal.write("Marqueur courant : {}{}".format(
                label, " (selection Reference)" if from_sync else ""
            ))
            if not from_sync:
                self._filter_photos(marker)
            return True
        except Exception as exc:
            journal.write("FastGCP : marqueur {} non active : {!r}".format(label, exc))
            return False

    @staticmethod
    def _find_marker(label):
        chunk = msapi.chunk()
        if chunk is None:
            return None
        for marker in msapi.markers(chunk):
            if msapi.marker_label(marker) == label:
                return marker
        return None

    def _select_row(self, label):
        """Selectionne la ligne sans declencher _pick (pas de boucle)."""
        self.grid.blockSignals(True)
        try:
            item = self._item_for(label)
            if item is None:
                self.grid.clearSelection()
            else:
                self.grid.setCurrentItem(item)
                self.grid.scrollToItem(item)
        finally:
            self.grid.blockSignals(False)

    def _item_for(self, label):
        if not label:
            return None
        for row in range(self.grid.topLevelItemCount()):
            item = self.grid.topLevelItem(row)
            if item.data(0, ROLE_LABEL) == label:
                return item
        return None

    def _refresh_row(self, label):
        """Remet a jour la ligne du marqueur (pastille, projections, erreurs)."""
        item = self._item_for(label)
        if item is None:
            return
        marker = self._marker_of(item)
        if marker is not None:
            self._style_item(item, marker, label)

    def refresh_errors(self):
        """Apres un pointage ou une annulation : erreurs et projections du marqueur courant.

        Les erreurs d'un marqueur ne dependent que de ses propres projections :
        une seule ligne change. Si le tableau est trie sur une colonne de
        valeurs, on relit tout pour que l'ordre reste juste.
        """
        state = self._state
        if state is None:
            return
        try:
            if self._sort is not None and self._sort[0] != "id":
                self.refresh_markers()
            else:
                self._refresh_row(state.active_label())
            self._update_undo_button()
        except Exception as exc:
            journal.write("FastGCP : erreurs non rafraichies : {!r}".format(exc))

    def _filter_photos(self, marker):
        state = self._state
        if state is None:
            return
        try:
            # Metashape se recentre sur son marqueur selectionne quand on change de
            # photo : on lui donne le meme marqueur qu'a FastGCP.
            if msapi.select_marker(msapi.chunk(), marker):
                state.last_selected_label = msapi.marker_label(marker)
        except Exception as exc:
            journal.write("FastGCP : selection Reference : {!r}".format(exc))
        try:
            from . import photos
            photos.filter_for_marker(state, marker, force=True)
        except Exception as exc:
            journal.write("FastGCP : filtre photos : {!r}".format(exc))
        self.update_active_card()

    # ------------------------------------------------------------------
    # Liste des marqueurs
    # ------------------------------------------------------------------

    def refresh_markers(self):
        """Relit les marqueurs du chunk courant. Ne leve jamais."""
        if self._state is None:
            return
        try:
            self._rebuild_list()
        except Exception as exc:
            journal.write("FastGCP : relecture des marqueurs : {!r}".format(exc))
            try:
                self.set_status(i18n.t("panel.status.reread_failed"))
            except Exception:
                pass

    def _rebuild_list(self):
        chunk = msapi.chunk()
        needle = self.search.text().strip().lower()
        active = self._state.active_label()
        total = 0
        rows = []

        if chunk is not None:
            everything = msapi.markers(chunk)
            total = len(everything)
            for marker in everything:
                label = msapi.marker_label(marker)
                if not label:
                    continue
                if needle and needle not in label.lower():
                    continue
                rows.append(self._row_data(chunk, marker, label))
        key, descending = self._sort if self._sort is not None else (None, False)
        rows = sort_rows(rows, key, descending)
        current = None

        self.grid.blockSignals(True)
        self.grid.setUpdatesEnabled(False)
        try:
            self.grid.clear()
            for row in rows:
                item = self._make_item(row)
                self.grid.addTopLevelItem(item)
                if row["label"] == active:
                    current = item
            if current is not None:
                self.grid.setCurrentItem(current)
                self.grid.scrollToItem(current)
        finally:
            self.grid.setUpdatesEnabled(True)
            self.grid.blockSignals(False)

        # Le statut ne dit que le nombre : « 14 », ou « 3/14 » pendant une recherche.
        if chunk is None:
            self.set_status("\u2014")
        elif needle:
            self.set_status("{}/{}".format(len(rows), total))
        else:
            # Avancement : marqueurs avec au moins 3 photos pointees.
            done, count = qc.progress(rows)
            if self._chunk_name:
                self.set_status(i18n.t("panel.status.progress_chunk", done=done,
                                       total=count, name=self._chunk_name))
            else:
                self.set_status(i18n.t("panel.status.progress", done=done, total=count))
        self.update_active_card()

    @staticmethod
    def _row_data(chunk, marker, label):
        """Valeurs d'une ligne : nom, projections, erreurs en metres (ou None)."""
        errors = msapi.marker_errors(chunk, marker)
        return {
            "label": label,
            "marker": marker,
            "proj": FastGcpPanel._pointed_count(marker),
            "x": errors.get("x"),
            "y": errors.get("y"),
            "z": errors.get("z"),
        }

    def _make_item(self, row):
        item = QtWidgets.QTreeWidgetItem()
        item.setData(0, ROLE_MARKER, row["marker"])
        item.setData(0, ROLE_LABEL, row["label"])
        item.setSizeHint(0, QtCore.QSize(0, ROW_HEIGHT))
        self._fill_item(item, row)
        return item

    def _fill_item(self, item, row):
        item.setText(0, row["label"])
        item.setIcon(0, _dot_icon(row["proj"] > 0))
        id_font = QtGui.QFont(self.grid.font())
        id_font.setWeight(QtGui.QFont.Medium)
        item.setFont(0, id_font)
        item.setForeground(0, QtGui.QBrush(_qcolor(theme.FG_0)))
        muted = QtGui.QBrush(_qcolor(theme.FG_3))
        normal = QtGui.QBrush(_qcolor(theme.FG_1))
        warn = QtGui.QBrush(_qcolor(theme.OFF_TEXT))
        for column, (_title, key, _width) in enumerate(COLUMNS):
            if column == 0:
                continue
            value = row.get(key)
            if key == "proj":
                item.setText(column, str(int(value)))
                # Moins de 3 photos pointees : a completer, en couleur d'alerte.
                item.setForeground(column, warn if qc.is_few(row) else normal)
            else:
                item.setText(column, format_error(value))
                item.setForeground(column, muted if value is None else normal)

    def _style_item(self, item, marker, label):
        chunk = msapi.chunk()
        self._fill_item(item, self._row_data(chunk, marker, label))

    @staticmethod
    def _marker_of(item):
        """Marqueur d'une ligne, ou None si la reference est devenue invalide."""
        try:
            marker = item.data(0, ROLE_MARKER)
            if marker is None:
                return None
            marker.label  # leve RuntimeError si le marqueur n'existe plus
            return marker
        except Exception:
            return None

    def _on_search(self, _text):
        self.refresh_markers()

    def _on_current_changed(self, current, _previous):
        if current is not None:
            self._pick(current)

    def _on_item_clicked(self, item, _column=0):
        if item is not None:
            self._pick(item)

    # -- tri par clic sur un en-tete --------------------------------------

    def sort_state(self):
        """(cle, decroissant) du tri en cours, ou None pour l'ordre du projet."""
        return self._sort

    def set_sort(self, key, descending=False):
        """Pose le tri (cle de COLUMNS, ou None) et relit le tableau."""
        valid = [column_key for _title, column_key, _width in COLUMNS]
        if key not in valid:
            key = None
        self._sort = (key, bool(descending)) if key is not None else None
        header = self._header
        header.blockSignals(True)
        try:
            if self._sort is None:
                header.setSortIndicator(-1, QtCore.Qt.AscendingOrder)
            else:
                header.setSortIndicator(
                    valid.index(key),
                    QtCore.Qt.DescendingOrder if descending else QtCore.Qt.AscendingOrder,
                )
        finally:
            header.blockSignals(False)
        header.viewport().update()
        self.refresh_markers()

    def _on_header_clicked(self, column):
        """Clics successifs sur un en-tete : croissant, decroissant, ordre du projet."""
        try:
            key = COLUMNS[column][1]
        except IndexError:
            return
        if self._sort is None or self._sort[0] != key:
            self.set_sort(key, False)
        elif not self._sort[1]:
            self.set_sort(key, True)
        else:
            self.set_sort(None)

    def _pick(self, item):
        """Un clic (ou une fleche) dans la liste : marqueur courant + filtre."""
        state = self._state
        if state is None:
            return
        try:
            label = item.data(0, ROLE_LABEL)
            now = time.time()
            last_label, last_time = self._last_pick
            if label == last_label and now - last_time < PICK_DEBOUNCE_S:
                return
            self._last_pick = (label, now)

            marker = self._marker_of(item)
            if marker is None:
                self.set_status(i18n.t("panel.status.list_reread"))
                self.refresh_markers()
                return
            state.active_marker = marker
            self.update_active_card()
            journal.write("Marqueur courant : {}".format(label))
            self._filter_photos(marker)
        except Exception as exc:
            journal.write("FastGCP : choix du marqueur : {!r}".format(exc))

    # ------------------------------------------------------------------
    # Annuler
    # ------------------------------------------------------------------

    def _on_history_changed(self):
        try:
            self._update_undo_button()
        except RuntimeError:
            history.unsubscribe(self._on_history_changed)

    def _update_undo_button(self):
        """Actif seulement quand l'outil tourne et qu'il y a un pointage a defaire."""
        state = self._state
        active = state is not None and bool(state.enabled) and history.can_undo()
        self.undo_button.setEnabled(bool(active))

    def _on_undo_clicked(self):
        state = self._state
        if state is None:
            return
        try:
            from . import marking
            marking.undo_last(state)
        except Exception as exc:
            journal.write("FastGCP : annulation : {!r}".format(exc))
        self._update_undo_button()

    # ------------------------------------------------------------------
    # Controle des residus et marqueurs a surveiller
    # ------------------------------------------------------------------

    def _all_rows(self):
        """Lignes de tous les marqueurs du chunk (pas seulement ceux de la recherche)."""
        chunk = msapi.chunk()
        rows = []
        for marker in msapi.markers(chunk):
            label = msapi.marker_label(marker)
            if label:
                rows.append(self._row_data(chunk, marker, label))
        return rows

    def _show_qc_menu(self, _checked=False):
        """Menu des marqueurs a surveiller : residu eleve, puis moins de 3 photos."""
        try:
            rows = self._all_rows()
            result = qc.watchlist(rows)
            by_label = {row["label"]: row for row in rows}
            menu = QtWidgets.QMenu(self)
            if not result["residual"] and not result["few"]:
                menu.addAction(i18n.t("qc.none")).setEnabled(False)
            if result["residual"]:
                title = i18n.t("qc.title_residual", limit=format_error(result["limit"]))
                menu.addAction(title).setEnabled(False)
                for item in result["residual"]:
                    worst = msapi.worst_projection(by_label[item["label"]]["marker"])
                    total = format_error(item["total"])
                    if worst is None:
                        text = i18n.t("qc.item_residual_plain", label=item["label"], total=total)
                    else:
                        text = i18n.t("qc.item_residual", label=item["label"], total=total,
                                      px="{:.1f}".format(worst[1]))
                    action = menu.addAction(text)
                    action.triggered.connect(
                        functools.partial(self._on_qc_pick, item["label"], True))
            if result["few"]:
                if result["residual"]:
                    menu.addSeparator()
                menu.addAction(i18n.t("qc.title_few", n=qc.MIN_PHOTOS)).setEnabled(False)
                for item in result["few"]:
                    action = menu.addAction(
                        i18n.t("qc.item_few", label=item["label"], n=item["proj"]))
                    action.triggered.connect(
                        functools.partial(self._on_qc_pick, item["label"], False))
            self._qc_menu = menu
            size = menu.sizeHint()
            button = self.qc_button
            menu.popup(button.mapToGlobal(
                QtCore.QPoint(0, -size.height() - theme.PAD_S)))
        except Exception as exc:
            journal.write("FastGCP : controle des residus : {!r}".format(exc))

    def _on_qc_pick(self, label, open_worst, _checked=False):
        """Un clic dans le menu : marqueur courant ; pour un residu, ouvre la pire photo."""
        try:
            if not self.set_active_marker_by_label(label) or not open_worst:
                return
            state = self._state
            marker = self._find_marker(label)
            worst = msapi.worst_projection(marker) if marker is not None else None
            if worst is None:
                return
            ok, _message = msapi.open_photo(worst[0])
            if ok:
                from . import photos
                photos.zoom_on_marker(state, marker, worst[0], force=True)
                self.set_status(i18n.t("qc.opened", label=label))
        except Exception as exc:
            journal.write("FastGCP : choix dans le controle : {!r}".format(exc))

    # ------------------------------------------------------------------
    # Detection IA
    # ------------------------------------------------------------------

    def update_aigcp(self):
        """Montre les choix memorises ; grise Lancer pendant un calcul ; barre de progression."""
        state = self._state
        chosen = {"type": aigcp.selected_type(), "scope": aigcp.selected_scope(),
                  "ignore": aigcp.selected_ignore()}
        for key, combo in self._aigcp_combos.items():
            try:
                index = combo.findData(chosen[key])
                if index >= 0 and combo.currentIndex() != index:
                    combo.blockSignals(True)
                    combo.setCurrentIndex(index)
                    combo.blockSignals(False)
            except RuntimeError:
                pass
        running = state is not None and aigcp.busy(state)
        proposals = state is not None and bool(getattr(state, "aigcp_applied", None))
        self.aigcp_button.setEnabled(not running)
        self.aigcp_clear_button.setEnabled(proposals and not running)
        self.aigcp_review_button.setEnabled(state is not None and bool(getattr(state, "aigcp_review", None)))
        # Barre : pleine largeur et en va-et-vient tant que le runner charge son modele,
        # puis au prorata des photos traitees.
        progress = aigcp.progress_of(state) if running else None
        if progress is not None and progress[1] > 0:
            self.aigcp_progress.setRange(0, progress[1])
            self.aigcp_progress.setValue(min(progress[0], progress[1]))
        else:
            self.aigcp_progress.setRange(0, 0 if running else 100)
            self.aigcp_progress.setValue(0)
        self.aigcp_progress.setVisible(bool(running))

    def _on_aigcp_combo(self, key, index):
        combo = self._aigcp_combos[key]
        aigcp.set_choice(key, combo.itemData(index))
        self.update_aigcp()

    def _on_aigcp_review(self, _checked=False):
        self.open_review()

    def open_review(self):
        """Ouvre la fenetre de revue des pointes de la derniere detection. Ne leve jamais."""
        try:
            from . import review_dialog
            self._review_dialog = review_dialog.show(self._state, self, self._review_dialog)
        except Exception as exc:
            journal.write("FastGCP : revue des pointes IA : {!r}".format(exc))

    def close_review(self):
        try:
            from . import review_dialog
            review_dialog.close(self._review_dialog)
        except Exception as exc:
            journal.write("FastGCP : fermeture de la revue IA : {!r}".format(exc))
        self._review_dialog = None

    def _on_aigcp_run(self, _checked=False):
        try:
            aigcp.start(self._state)
        except Exception as exc:
            journal.write("FastGCP : detection IA : {!r}".format(exc))
        self.update_aigcp()

    def _on_aigcp_clear(self, _checked=False):
        try:
            aigcp.clear(self._state)
        except Exception as exc:
            journal.write("FastGCP : effacement des propositions : {!r}".format(exc))
        self.update_aigcp()

    # ------------------------------------------------------------------
    # Exporter
    # ------------------------------------------------------------------

    def _show_export_menu(self):
        """Ouvre le menu au-dessus du bouton, aligne sur son bord droit."""
        button = self.export_button
        menu = self.export_menu
        size = menu.sizeHint()
        origin = button.mapToGlobal(
            QtCore.QPoint(button.width() - size.width(), -size.height() - theme.PAD_S)
        )
        menu.popup(origin)

    def _on_export(self, kind, _checked=False):
        """Lance l'export demande. L'import de report.py se fait ici, au clic."""
        name = str(kind).upper()
        try:
            from . import report
            function = getattr(report, "export_" + kind)
        except Exception as exc:
            self.set_status(i18n.t("panel.status.export_unavailable"))
            journal.write("Export {} indisponible : {!r}".format(name, exc))
            return
        journal.write("Export : {}".format(name))
        try:
            function(self._state, self)
        except Exception as exc:
            self.set_status(i18n.t("panel.status.export_failed"))
            journal.write("Export {} impossible : {!r}".format(name, exc))

    # ------------------------------------------------------------------
    # Statut
    # ------------------------------------------------------------------

    def set_chunk_name(self, name):
        """Nom du chunk courant, repris dans le statut au prochain rafraichissement."""
        self._chunk_name = str(name or "")

    def set_status(self, texte):
        """Ligne de statut du pied du panneau."""
        try:
            self.status.set_full_text(texte or "")
        except RuntimeError:
            pass

    # ------------------------------------------------------------------
    # Journal
    # ------------------------------------------------------------------

    def _subscribe_journal(self):
        self._line_added.connect(self._append_log)
        self._journal_cb = self._on_journal_line
        lines = journal.lines()
        if lines:
            self.log.setPlainText("\n".join(lines))
            self._scroll_log_to_end()
        journal.subscribe(self._journal_cb)

    def _on_journal_line(self, line):
        self._line_added.emit(line)

    def _append_log(self, line):
        try:
            self.log.appendPlainText(line)
        except RuntimeError:
            pass

    def _scroll_log_to_end(self):
        bar = self.log.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _fit_log_height(self):
        self.log.ensurePolished()
        metrics = self.log.fontMetrics()
        frame = self.log.frameWidth() * 2
        margin = int(self.log.document().documentMargin() * 2)
        self.log.setFixedHeight(metrics.lineSpacing() * LOG_VISIBLE_LINES + frame + margin)

    def _on_log_button(self):
        self._set_log_open(not self._log_open, persist=True)

    def _set_log_open(self, opened, persist):
        self._log_open = bool(opened)
        if self._log_open:
            self._fit_log_height()
        self.log.setVisible(self._log_open)
        if self._log_open:
            self._scroll_log_to_end()
        if persist:
            self._set_option("show_log", self._log_open)

    # ------------------------------------------------------------------
    # Fermeture
    # ------------------------------------------------------------------

    def detach(self):
        """Coupe les liens avec le reste du programme. Ne leve jamais."""
        try:
            self.close_review()
        except Exception:
            pass
        try:
            if self._journal_cb is not None:
                journal.unsubscribe(self._journal_cb)
        except Exception:
            pass
        self._journal_cb = None
        try:
            history.unsubscribe(self._on_history_changed)
        except Exception:
            pass
        try:
            if self._lang_cb is not None:
                i18n.unsubscribe(self._lang_cb)
        except Exception:
            pass
        self._lang_cb = None
        self._state = None
