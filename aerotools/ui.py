"""Small UI building blocks shared by the AeroTools windows, kept for the windows that are not migrated to the kit yet.

Everything here is a thin alias onto `aerotools/uikit` (see docs/UIKIT.md). Under Metashape a QDialog does not paint the wanted background,
an inner QWidget does (see `styled_root`).
"""

from . import i18n, theme
from .qt import QtCore, QtWidgets


def styled_root(dialog):
    """The inner widget (named aeroPanel) that carries the kit's style sheet and the window background; it fills `dialog`.

    New windows use `uikit.ToolWindow` instead, which does this and more."""
    from .uikit import style
    root = QtWidgets.QWidget()
    root.setAttribute(QtCore.Qt.WA_StyledBackground, True)
    style.apply(root)
    outer = QtWidgets.QVBoxLayout(dialog)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.addWidget(root)
    return root


def run(dialog):
    """exec_ (PySide2) or exec (PySide6)."""
    return (getattr(dialog, "exec_", None) or getattr(dialog, "exec"))()


# Older object names -> the kit's button roles.
_ROLES = {"aeroRun": "primary", "aeroGo": "primary", "aeroYes": "primary", "aeroNo": "danger", "aeroGhost": "secondary",
          "aeroBtn": "secondary"}


def button(text_key, handler, object_name="aeroGhost", width=96, **fields):
    """A kit `Button` (one height, role taken from the older object name). New code: `uikit.button(key, handler, role)`."""
    from .uikit import controls
    widget = controls.Button(i18n.t(text_key, **fields) if fields else i18n.t(text_key), _ROLES.get(object_name, "secondary"))
    widget.setObjectName(object_name)
    widget.setMinimumWidth(width)
    widget.clicked.connect(lambda *_args: handler())
    return widget


def big_button(text_key, handler, kind="aeroBtn", width=0, **fields):
    """Same as `button` (there is only one button height now): `aeroGo` = primary, `aeroBtn` = secondary."""
    from .uikit import controls
    widget = controls.Button(i18n.t(text_key, **fields) if fields else i18n.t(text_key), _ROLES.get(kind, "secondary"))
    widget.setObjectName(kind)
    if width:
        widget.setMinimumWidth(width)
    widget.clicked.connect(lambda *_args: handler())
    return widget


def combo(parent=None):
    """Drop-down list of the kit (reachable by Tab). New code: `uikit.ChoiceBox`."""
    from .uikit import controls
    box = controls.ChoiceBox(parent=parent)
    box.setObjectName("aeroCombo")
    return box


def dark_root(dialog):
    """Same as `styled_root` (there is one window background now)."""
    return styled_root(dialog)


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


def table_style():
    """Style of a table that is not a kit `DataTable` yet (a QTableWidget on the window background)."""
    from .uikit import style
    return style.table_stylesheet()


def __getattr__(name):
    if name == "TABLE_STYLE":
        return table_style()
    raise AttributeError("module 'aerotools.ui' has no attribute " + repr(name))


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
