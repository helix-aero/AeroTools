"""Small UI building blocks shared by the AeroTools windows (install, update).

Same style as the FastGCP panel: under Metashape a QDialog does not paint the wanted background,
an inner QWidget does (see `styled_root`).
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
    """exec_ (PySide2) or exec (PySide6)."""
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
    """Big button (40 px, centered text) of the dark windows. `aeroGo` = green, `aeroBtn` = grey."""
    widget = QtWidgets.QPushButton(i18n.t(text_key, **fields) if fields else i18n.t(text_key))
    widget.setObjectName(kind)
    widget.setCursor(QtCore.Qt.PointingHandCursor)
    widget.setFixedHeight(40)
    if width:
        widget.setMinimumWidth(width)
    widget.setAutoDefault(False)
    widget.clicked.connect(lambda *_args: handler())
    return widget


def combo(parent=None):
    """Drop-down list in the style of the dark windows."""
    box = QtWidgets.QComboBox(parent)
    box.setObjectName("aeroCombo")
    box.setProperty("arrow", True)
    box.setCursor(QtCore.Qt.PointingHandCursor)
    box.setFocusPolicy(QtCore.Qt.NoFocus)
    box.setMinimumHeight(36)
    return box


def dark_root(dialog):
    """Like `styled_root`, with the dark background of the Settings windows."""
    root = styled_root(dialog)
    root.setProperty("settings", True)
    return root


def tile(caption=""):
    """Small number tile: (frame, number label, title label)."""
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
    """Small green tick (success) or orange cross (failure) in front of a status text."""

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


class SelectAllEdit(QtWidgets.QLineEdit):
    """Input field whose whole text is selected as soon as it is entered (click or Tab): typing replaces the value."""

    def focusInEvent(self, event):
        super(SelectAllEdit, self).focusInEvent(event)
        QtCore.QTimer.singleShot(0, self.selectAll)         # after the click, which would otherwise place the cursor

    def mousePressEvent(self, event):
        entering = not self.hasFocus()
        super(SelectAllEdit, self).mousePressEvent(event)
        if entering:
            QtCore.QTimer.singleShot(0, self.selectAll)


class OptionCard(QtWidgets.QFrame):
    """An option: a button (radio or checkbox) in a frame that turns green once chosen (orange if `danger`).

    The whole frame is clickable. `grid` is used to place other elements next to the button (column 1 onwards)."""

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
    """Style of a table on a black background (marker pop-ups)."""
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
