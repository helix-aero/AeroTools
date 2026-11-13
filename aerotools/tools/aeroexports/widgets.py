"""Aero-Exports: the few window pieces the UI kit does not have, built from kit tokens and components only.

    StatusLine   a status: mark + text in the colour of its kind (a warning is never plain body text)
    ItemCard     a card for one thing to export (head row with the tick, the pixel size, then rows below)
    OutputRow    one file the item will write: a switch, what it is written as, an optional "i"
    ClickLabel   a label that calls a handler when clicked (the small DEM picture)
    SliderRow    label, slider, typed value, reset button (the look window)

Kit requests (kept here until the kit has them): a checkbox-style "choice row", a slider row with a typed value.
"""

from ...qt import QtCore, QtWidgets
from ...uikit import tokens
from ...uikit.blocks import Glyph, hbox, vbox
from ...uikit.controls import IconButton, InfoButton, NumberField, Toggle, label
from ...uikit.metrics import px

Qt = QtCore.Qt


class StatusLine(QtWidgets.QWidget):
    """A status in one line: the kit's status mark, then the text in the colour of its kind (colour + icon + words, never plain body text).
    `setText` / `text` work like a label's; hidden while it has no text."""

    def __init__(self, kind="warning"):
        super(StatusLine, self).__init__()
        row = hbox(spacing=tokens.S, parent=self)
        self.glyph = Glyph(kind, tokens.ICON)
        row.addWidget(self.glyph, 0, Qt.AlignTop)
        self.label = label("", "small", kind)
        row.addWidget(self.label, 1)
        self.setVisible(False)

    def setText(self, text):
        self.label.setText(text)

    def text(self):
        return self.label.text()


class ClickLabel(QtWidgets.QLabel):
    """A label that calls `handler` when clicked."""

    def __init__(self, handler):
        super(ClickLabel, self).__init__()
        self._handler = handler
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event):
        event.accept()
        if self.isEnabled():
            self._handler()


class ItemCard(QtWidgets.QFrame):
    """A kit card holding one thing to export. `button` is the tick (a switch), `head` the first row, `rows` the vertical layout below it."""

    def __init__(self, title):
        super(ItemCard, self).__init__()
        self.setProperty("ui", "card")
        outer = vbox((tokens.M, tokens.M, tokens.M, tokens.M), tokens.S, parent=self)
        self.head = hbox(spacing=tokens.S)
        self.button = Toggle(title, True)
        self.head.addWidget(self.button)
        self.head.addStretch(1)
        outer.addLayout(self.head)
        self.rows = vbox(spacing=tokens.S)
        outer.addLayout(self.rows)


class OutputRow(QtWidgets.QWidget):
    """One output of an item: `switch` (a `Toggle`), the format it is written as (muted, right), and an "i" when there is something to explain."""

    def __init__(self, title, checked=True, info=""):
        super(OutputRow, self).__init__()
        row = hbox(spacing=tokens.S, parent=self)
        self.switch = Toggle(title, checked)
        row.addWidget(self.switch)
        self.info = InfoButton(info)
        self.info.setVisible(bool(info))
        row.addWidget(self.info)
        row.addStretch(1)
        self.format = label("", "small", "muted", wrap=False)
        row.addWidget(self.format)

    def set_format(self, text):
        self.format.setText(text)


class SliderRow(object):
    """Label, slider, typed number (with its unit), reset button, laid on the columns of `grid` at `row`.

    `slider` and `field` stay in step; `changed` callbacks go through the slider (`slider.valueChanged`). `modulo` makes a typed 360 mean 0."""

    def __init__(self, grid, row, title, low, high, unit, value, modulo=None, typed_high=None):
        self.modulo = modulo
        self.label = label(title, "body", "muted", wrap=False)
        grid.addWidget(self.label, row, 0)
        self.slider = QtWidgets.QSlider(Qt.Horizontal)
        self.slider.setObjectName("aeroSlider")
        self.slider.setRange(low, high)
        self.slider.setValue(value)
        self.slider.setFixedHeight(px(tokens.CONTROL_H))
        grid.addWidget(self.slider, row, 1)
        self.field = NumberField(value, low, typed_high if typed_high is not None else high, 0, 1, unit, width=88)
        grid.addWidget(self.field, row, 2)
        self.reset = IconButton("refresh", "")
        grid.addWidget(self.reset, row, 3)
        self.slider.valueChanged.connect(self._from_slider)
        self.field.valueChanged.connect(self._from_field)

    def _from_slider(self, value):
        self.field.set_value(value)

    def _from_field(self, value):
        number = int(round(value))
        if self.modulo:
            number %= self.modulo
        self.slider.setValue(number)
        self.field.set_value(number)

    def set_value(self, value):
        self.slider.setValue(int(value))
        self.field.set_value(self.slider.value())

    def value(self):
        return self.slider.value()

    def set_enabled(self, enabled):
        for widget in (self.label, self.slider, self.field):
            widget.setEnabled(bool(enabled))
