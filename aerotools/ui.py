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
