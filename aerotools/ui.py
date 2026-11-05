"""Petits morceaux d'interface communs aux fenetres d'AeroTools (installation, mise a jour).

Meme style que le panneau FastGCP : sous Metashape un QDialog ne peint pas le fond voulu,
un QWidget interne le fait (voir `styled_root`).
"""

from . import i18n, theme
from .qt import QtCore, QtWidgets


def styled_root(dialog):
    root = QtWidgets.QWidget()
    root.setObjectName("aeroPanel")
    root.setAttribute(QtCore.Qt.WA_StyledBackground, True)
    root.setStyleSheet(theme.stylesheet())
    outer = QtWidgets.QVBoxLayout(dialog)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.addWidget(root)
    return root


def run(dialog):
    """exec_ (PySide2) ou exec (PySide6)."""
    return (getattr(dialog, "exec_", None) or getattr(dialog, "exec"))()


def button(text_key, handler, object_name="aeroGhost", width=96, **fields):
    widget = QtWidgets.QPushButton(i18n.t(text_key, **fields) if fields else i18n.t(text_key))
    widget.setObjectName(object_name)
    widget.setProperty("pill", True)
    widget.setCursor(QtCore.Qt.PointingHandCursor)
    widget.setFixedHeight(32)
    widget.setMinimumWidth(width)
    widget.setAutoDefault(False)
    widget.clicked.connect(lambda *_args: handler())
    return widget


def big_button(text_key, handler, kind="aeroBtn", width=0, **fields):
    """Gros bouton (40 px, texte centre) des fenetres sombres. `aeroGo` = vert, `aeroBtn` = gris."""
    widget = QtWidgets.QPushButton(i18n.t(text_key, **fields) if fields else i18n.t(text_key))
    widget.setObjectName(kind)
    widget.setCursor(QtCore.Qt.PointingHandCursor)
    widget.setFixedHeight(40)
    if width:
        widget.setMinimumWidth(width)
    widget.setAutoDefault(False)
    widget.clicked.connect(lambda *_args: handler())
    return widget


def dark_root(dialog):
    """Comme `styled_root`, avec le fond sombre des fenetres Parametres."""
    root = styled_root(dialog)
    root.setProperty("settings", True)
    return root


def tile(caption=""):
    """Petite case chiffree : (cadre, etiquette du chiffre, etiquette du titre)."""
    frame = QtWidgets.QFrame()
    frame.setObjectName("aeroStat")
    layout = QtWidgets.QVBoxLayout(frame)
    layout.setContentsMargins(8, 6, 8, 6)
    layout.setSpacing(0)
    number = QtWidgets.QLabel("-")
    number.setObjectName("aeroStatNum")
    number.setAlignment(QtCore.Qt.AlignCenter)
    title = QtWidgets.QLabel(caption)
    title.setObjectName("aeroStatLabel")
    title.setAlignment(QtCore.Qt.AlignCenter)
    layout.addWidget(number)
    layout.addWidget(title)
    return frame, number, title


MARKS = {"ok": ("\u2713", theme.GREEN_BRIGHT), "error": ("\u2715", theme.ORANGE_BRIGHT)}


class Mark(QtWidgets.QLabel):
    """Petite coche verte (reussite) ou croix orange (echec) devant un texte d'etat."""

    def __init__(self, size=16):
        super(Mark, self).__init__()
        self.setFixedWidth(size + 4)
        self.setAlignment(QtCore.Qt.AlignCenter)
        self._size = size
        self.setVisible(False)

    def show_kind(self, kind):
        symbol, color = MARKS.get(kind, ("", ""))
        self.setText(symbol)
        self.setStyleSheet("color: {}; font-size: {}px; font-weight: 700; background: transparent;".format(
            color, self._size))
        self.setVisible(bool(symbol))


class OptionCard(QtWidgets.QFrame):
    """Une option : un bouton (rond ou case) dans un cadre qui passe au vert une fois choisi (orange si `danger`).

    Le cadre est cliquable partout. `grid` sert a poser d'autres elements a cote du bouton (colonne 1 et suivantes)."""

    def __init__(self, button, danger=False):
        super(OptionCard, self).__init__()
        self.setObjectName("aeroOption")
        self.setProperty("on", False)
        self.setProperty("danger", bool(danger))
        self.button = button
        self.grid = QtWidgets.QGridLayout(self)
        self.grid.setContentsMargins(14, 12, 14, 12)
        self.grid.setHorizontalSpacing(theme.PAD)
        self.grid.setVerticalSpacing(theme.PAD_S)
        self.grid.addWidget(button, 0, 0)
        button.toggled.connect(self.sync)

    def sync(self, checked=None):
        self.setProperty("on", bool(self.button.isChecked() if checked is None else checked))
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event):
        if self.button.isEnabled():
            if isinstance(self.button, QtWidgets.QRadioButton):
                self.button.setChecked(True)
            else:
                self.button.toggle()
        super(OptionCard, self).mousePressEvent(event)


TABLE_STYLE = """
    QTableWidget {{
        background: {pane}; alternate-background-color: {head}; color: {fg_1};
        selection-background-color: {soft}; selection-color: {fg_0};
        gridline-color: transparent; border: 1px solid {border}; border-radius: 8px; outline: none;
    }}
    QTableWidget::item {{ padding: 0 8px; border: none; }}
    QTableWidget::item:selected, QTableWidget::item:selected:!active {{ background: {soft}; color: {fg_0}; }}
    QHeaderView::section {{
        background: {head}; color: {fg_2}; border: none; padding: 7px 8px; font-weight: bold;
    }}
    QTableCornerButton::section {{ background: {head}; border: none; }}
"""


def table_style():
    """Style d'un tableau sur fond noir (pop-ups des marqueurs)."""
    return TABLE_STYLE.format(pane=theme.PANE, head=theme.PANE_HEAD, fg_0=theme.FG_0, fg_1=theme.FG_1,
                              fg_2=theme.FG_2, border=theme.BORDER_SOFT, soft=theme.GREEN_SOFT)


def label(text="", object_name=None, wrap=True):
    widget = QtWidgets.QLabel(text)
    if object_name:
        widget.setObjectName(object_name)
    widget.setWordWrap(wrap)
    return widget


def timer(parent, interval_ms, handler):
    clock = QtCore.QTimer(parent)
    clock.setInterval(interval_ms)
    clock.timeout.connect(handler)
    return clock
