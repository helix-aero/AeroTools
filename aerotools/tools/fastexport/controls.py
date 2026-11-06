"""Fast Export: small controls shared by the look window (no numpy here, so they load and test anywhere).

- `EditableValue`: the number written next to a slider ("315 degrees (NW)", "80 %"). Double-click it (or Tab to it and press Enter or F2),
  type an exact number, Enter applies it, Esc puts the old value back. A number outside the allowed range is refused on the spot, with the
  range written in the tooltip; the slider is only moved by a valid number.
- `reset_button`: the small round arrow next to a control that puts its default value back.
- `slider_row`: a label, a short slider, its editable value and its reset button, placed in one grid row.
"""

from ... import i18n, theme, ui
from ...qt import QtCore, QtWidgets

RESET_SIZE = 28
EDIT_WIDTH = 64
VALUE_WIDTH = 84
SLIDER_WIDTH = 140
UNIT_MARKS = ("°", "%", " ")


def parse_number(text):
    """Float of a typed value (comma or dot), or None."""
    try:
        value = float(str(text).replace(",", ".").strip())
    except ValueError:
        return None
    return value if value == value and abs(value) != float("inf") else None


def parse_typed(text, low, high, modulo=None):
    """Whole number typed in a value field: unit marks (degree sign, %) are ignored; None when it is not a number or lies outside
    low..high. With `modulo` (a compass: 0 to 360), the result wraps (360 gives 0)."""
    cleaned = str(text)
    for mark in UNIT_MARKS:
        cleaned = cleaned.replace(mark, " ")
    cleaned = cleaned.replace("deg", " ").strip()
    value = parse_number(cleaned)
    if value is None or not low <= value <= high:
        return None
    whole = int(round(value))
    return whole % modulo if modulo else whole


class _ValueLabel(QtWidgets.QLabel):
    """The shown value: a double-click, or Enter / F2 when it has the keyboard focus, asks for the editor."""

    def __init__(self, owner):
        super(_ValueLabel, self).__init__()
        self._owner = owner
        self.setObjectName("aeroValue")
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setCursor(QtCore.Qt.IBeamCursor)
        self.setStyleSheet(
            "QLabel#aeroValue { color: %s; font-size: 9pt; font-weight: 400; padding: 3px 4px; border: 1px solid transparent; border-bottom: 1px dashed %s; }"
            "QLabel#aeroValue:hover { border: 1px solid %s; border-radius: 4px; }"
            "QLabel#aeroValue:focus { border: 1px solid %s; border-radius: 4px; }"
            "QLabel#aeroValue:disabled { color: %s; border-color: transparent; }" % (
                theme.FG_1, theme.FG_3, theme.BORDER_STRONG, theme.GREEN, theme.FG_3))

    def mouseDoubleClickEvent(self, event):
        event.accept()
        self._owner.begin_edit()

    def keyPressEvent(self, event):
        if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter, QtCore.Qt.Key_F2):
            event.accept()
            self._owner.begin_edit()
            return
        super(_ValueLabel, self).keyPressEvent(event)


class _ValueEdit(ui.SelectAllEdit):
    """The editor: Enter and Esc are taken here, so the window around never sees them (Esc would close it)."""

    committed = QtCore.Signal()
    cancelled = QtCore.Signal()
    left = QtCore.Signal()

    def keyPressEvent(self, event):
        if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            event.accept()
            self.committed.emit()
            return
        if event.key() == QtCore.Qt.Key_Escape:
            event.accept()
            self.cancelled.emit()
            return
        super(_ValueEdit, self).keyPressEvent(event)

    def focusOutEvent(self, event):
        super(_ValueEdit, self).focusOutEvent(event)
        if event.reason() not in (QtCore.Qt.ActiveWindowFocusReason, QtCore.Qt.PopupFocusReason):
            self.left.emit()


class EditableValue(QtWidgets.QStackedWidget):
    """A value shown as text that turns into a field when double-clicked. `edited(int)` is sent with each valid new value.

    `low`..`high`: what may be typed; `modulo`: wrap (a direction); `show(value)`: the text displayed for a value (units, compass)."""

    edited = QtCore.Signal(int)

    def __init__(self, low, high, show, modulo=None):
        super(EditableValue, self).__init__()
        self.low, self.high, self.modulo, self._show = low, high, modulo, show
        self._value, self._editing = low, False
        self.label = _ValueLabel(self)
        self.edit = _ValueEdit()
        self.edit.setFixedWidth(EDIT_WIDTH)
        self.edit.setAlignment(QtCore.Qt.AlignRight)
        self.edit.setMaxLength(8)
        self.edit.committed.connect(self._commit)
        self.edit.cancelled.connect(self._cancel)
        self.edit.left.connect(self._left)
        self.addWidget(self.label)
        self.addWidget(self.edit)
        self.setMinimumWidth(VALUE_WIDTH)
        self.setToolTip(i18n.t("export.value_hint", low=low, high=high if not modulo else modulo - 1))
        self.set_value(low)

    # -- shown value ------------------------------------------------------------------------

    def set_value(self, value):
        self._value = int(value)
        self.label.setText(self._show(self._value))

    def value(self):
        return self._value

    def text(self):
        return self.label.text()

    def setText(self, _text):                             # the text is always made from the value
        self.label.setText(self._show(self._value))

    def setEnabled(self, enabled):
        super(EditableValue, self).setEnabled(enabled)
        if not enabled and self._editing:
            self._cancel()

    def is_editing(self):
        return self._editing

    # -- editing ----------------------------------------------------------------------------

    def begin_edit(self):
        """Show the field with the current number selected. Does nothing when the control is greyed out."""
        if not self.isEnabled() or self._editing:
            return
        self._editing = True
        self._mark_bad(False)
        self.edit.setText(str(self._value))
        self.setCurrentWidget(self.edit)
        self.edit.setFocus(QtCore.Qt.OtherFocusReason)
        self.edit.selectAll()

    def _typed(self):
        return parse_typed(self.edit.text(), self.low, self.high, self.modulo)

    def _mark_bad(self, bad):
        self.edit.setProperty("bad", bool(bad))
        self.edit.setStyleSheet("QLineEdit { border: 1px solid %s; }" % theme.ORANGE_BRIGHT if bad else "")
        self.edit.setToolTip(i18n.t("export.value_bad", low=self.low, high=self.high if not self.modulo else self.modulo - 1)
                             if bad else "")

    def _close(self):
        self._editing = False
        self._mark_bad(False)
        self.setCurrentWidget(self.label)

    def _commit(self):
        """Enter: a valid number is applied, a wrong one keeps the field open with a red frame."""
        if not self._editing:
            return
        number = self._typed()
        if number is None:
            self._mark_bad(True)
            return
        self._close()
        self.label.setFocus(QtCore.Qt.OtherFocusReason)
        if number != self._value:
            self.set_value(number)
            self.edited.emit(number)

    def _cancel(self):
        """Esc: the old value stays."""
        if not self._editing:
            return
        self._close()
        self.label.setFocus(QtCore.Qt.OtherFocusReason)

    def _left(self):
        """The focus went elsewhere: a valid number is applied, anything else is dropped."""
        if not self._editing:
            return
        number = self._typed()
        self._close()
        if number is not None and number != self._value:
            self.set_value(number)
            self.edited.emit(number)


def reset_button(handler, tip=""):
    """Small round "back to the default" button."""
    widget = QtWidgets.QPushButton("↺")
    widget.setObjectName("aeroGhost")
    widget.setProperty("round", True)
    widget.setCursor(QtCore.Qt.PointingHandCursor)
    widget.setFixedSize(RESET_SIZE, RESET_SIZE)
    widget.setAutoDefault(False)
    widget.setToolTip(tip)
    widget.setStyleSheet("QPushButton { font-size: 14pt; padding: 0 0 3px 0; }")
    widget.clicked.connect(lambda *_args: handler())
    return widget


def scroll_page(content):
    """A scroll area around `content` for a window that may be too small for it: no frame, the bar is wide and easy to see
    (the theme's own bar is thin and nearly invisible)."""
    area = QtWidgets.QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QtWidgets.QFrame.NoFrame)
    area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
    area.setWidget(content)
    area.viewport().setAutoFillBackground(False)
    content.setAutoFillBackground(False)
    area.setStyleSheet(
        "QScrollArea { background: transparent; border: none; }"
        "QScrollBar:vertical { background: %s; width: 14px; margin: 0; border-radius: 7px; }"
        "QScrollBar::handle:vertical { background: %s; min-height: 36px; margin: 2px; border-radius: 5px; }"
        "QScrollBar::handle:vertical:hover { background: %s; }"
        "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; background: none; }"
        "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }" % (
            theme.GROUND_3, theme.FG_3, theme.FG_2))
    return area


def slider_row(grid, row, key, low, high, show, default, modulo=None, typed_high=None):
    """One line of the settings: label, short slider, editable value, reset button. Returns (slider, value, reset).

    `default`: the number the reset button puts back. `typed_high`: the largest number that may be typed (a compass accepts 360)."""
    grid.addWidget(ui.label(i18n.t(key), "aeroMeta", wrap=False), row, 0)
    slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
    slider.setObjectName("aeroSlider")
    slider.setRange(low, high)
    slider.setCursor(QtCore.Qt.PointingHandCursor)
    slider.setFixedWidth(SLIDER_WIDTH)
    grid.addWidget(slider, row, 1)
    value = EditableValue(low, typed_high if typed_high is not None else high, show, modulo)
    value.edited.connect(slider.setValue)
    grid.addWidget(value, row, 2)
    reset = reset_button(lambda: slider.setValue(default))
    grid.addWidget(reset, row, 3)
    return slider, value, reset
