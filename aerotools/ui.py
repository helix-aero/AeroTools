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
