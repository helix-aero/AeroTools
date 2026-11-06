"""Aero-Facade: the margin editor (screen 3) and the method picker (screen 4).

* `NumberField`      a number typed in metres or centimetres: Enter or leaving the field applies it, Esc puts the old one back, a wrong
                     entry gets a red frame and is refused, Up / Down change it by a small step (Shift: ten times more);
* `LinkBadge`        the round chain between two fields: green and closed = linked (changing one changes the other), grey and open = free;
* `MarginSchematic`  a little drawing of a wall with the margin area shaded and the depth, redrawn as the numbers change;
* `MarginEditor`     the four margins with their two badges, the depth, the pixel size and the "apply to all" switch;
* `MethodPicker`     radio cards for the three ways of building the surface under a wall, best first, greyed when unavailable.
"""

from ... import theme, ui
from ...qt import QtCore, QtGui, QtWidgets
from ..aeroexports import controls
from .common import LINK_KEYS, SIDE_KEYS, metres, tr
from .model import Margins

STEP = 0.1


def parse_decimal(text):
    """Float of a typed number (comma or dot), or None."""
    return controls.parse_number(text)


# --------------------------------------------------------------------------------------------------
# Number field
# --------------------------------------------------------------------------------------------------

class NumberField(ui.SelectAllEdit):
    """A number with a range. `value_edited(float)` is sent with each valid new value (typed or stepped)."""

    value_edited = QtCore.Signal(float)

    def __init__(self, low, high, decimals=2, step=STEP, owner=None):
        super(NumberField, self).__init__()
        self.low, self.high, self.decimals, self.step = float(low), float(high), int(decimals), float(step)
        self._value = self.low
        self.setAlignment(QtCore.Qt.AlignRight)
        self.setMaxLength(8)
        self.setToolTip(tr("facade.ui.number_hint", low=metres(self.low, 1), high=metres(self.high, 1)))
        self.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        self.set_value(self.low)

    # -- value ----------------------------------------------------------------------------------

    def show_text(self, value):
        text = i18n_number(value, self.decimals)
        return text

    def set_value(self, value):
        self._value = min(self.high, max(self.low, float(value)))
        self._mark_bad(False)
        self.setText(self.show_text(self._value))

    def value(self):
        return self._value

    def typed(self):
        """Number written in the field, or None when it is not a number in range."""
        number = parse_decimal(self.text())
        if number is None or not self.low - 1e-9 <= number <= self.high + 1e-9:
            return None
        return number

    def _mark_bad(self, bad):
        self.setStyleSheet("QLineEdit { border: 1px solid %s; }" % theme.ORANGE_BRIGHT if bad else "")
        self.setToolTip(tr("facade.ui.number_bad", low=metres(self.low, 1), high=metres(self.high, 1)) if bad
                        else tr("facade.ui.number_hint", low=metres(self.low, 1), high=metres(self.high, 1)))

    def commit(self):
        """Apply the typed number (True), or refuse it with a red frame (False)."""
        number = self.typed()
        if number is None:
            self._mark_bad(True)
            return False
        number = round(number, self.decimals)
        changed = abs(number - self._value) > 1e-9
        self.set_value(number)
        if changed:
            self.value_edited.emit(self._value)
        return True

    # -- keyboard -------------------------------------------------------------------------------

    def keyPressEvent(self, event):
        key = event.key()
        if key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            event.accept()
            self.commit()
            self.selectAll()
            return
        if key == QtCore.Qt.Key_Escape:
            event.accept()
            self.set_value(self._value)
            self.selectAll()
            return
        if key in (QtCore.Qt.Key_Up, QtCore.Qt.Key_Down):
            event.accept()
            base = self.typed()
            base = self._value if base is None else base
            step = self.step * (10 if event.modifiers() & QtCore.Qt.ShiftModifier else 1) * (1 if key == QtCore.Qt.Key_Up else -1)
            self.setText(self.show_text(min(self.high, max(self.low, base + step))))
            self.commit()
            self.selectAll()
            return
        super(NumberField, self).keyPressEvent(event)

    def wheelEvent(self, event):
        if self.hasFocus():
            event.accept()
            key = QtCore.Qt.Key_Up if event.angleDelta().y() > 0 else QtCore.Qt.Key_Down
            self.keyPressEvent(QtGui.QKeyEvent(QtCore.QEvent.KeyPress, key, QtCore.Qt.NoModifier))
        else:
            event.ignore()

    def focusOutEvent(self, event):
        if event.reason() not in (QtCore.Qt.ActiveWindowFocusReason, QtCore.Qt.PopupFocusReason):
            if not self.commit():
                self.set_value(self._value)                      # a wrong entry is dropped when the field is left
        super(NumberField, self).focusOutEvent(event)

    # -- size follows the font ------------------------------------------------------------------

    def sizeHint(self):
        base = super(NumberField, self).sizeHint()
        width = self.fontMetrics().horizontalAdvance("00000.00") + 30
        return QtCore.QSize(width, base.height())

    def minimumSizeHint(self):
        return self.sizeHint()


def i18n_number(value, decimals):
    """The number as written with the decimal mark of the interface ("1.5"), at least one decimal."""
    text = metres(value, decimals)
    mark = "," if "," in text else "."
    if mark in text:
        whole, _sep, frac = text.partition(mark)
        frac = frac.rstrip("0")
        return whole + mark + (frac or "0")
    return text + mark + "0"


# --------------------------------------------------------------------------------------------------
# Link badge
# --------------------------------------------------------------------------------------------------

class LinkBadge(QtWidgets.QAbstractButton):
    """A round chain button. Checked = linked (the two fields move together)."""

    def __init__(self, tip_on, tip_off):
        super(LinkBadge, self).__init__()
        self.setCheckable(True)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self._tips = (tip_on, tip_off)
        self.setFixedSize(40, 40)
        self.toggled.connect(self._tip)
        self._tip(False)

    def _tip(self, checked):
        text = self._tips[0] if checked else self._tips[1]
        self.setToolTip(text)
        self.setAccessibleName(text)

    def sizeHint(self):
        return QtCore.QSize(40, 40)

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        on = self.isChecked()
        hover = self.underMouse()
        rect = QtCore.QRectF(self.rect()).adjusted(3, 3, -3, -3)
        painter.setPen(QtGui.QPen(QtGui.QColor(theme.GREEN if on else theme.BORDER_STRONG), 2))
        painter.setBrush(QtGui.QColor(30, 202, 89, 70 if not hover else 110) if on else QtGui.QColor(theme.GROUND_3 if not hover
                                                                                                          else theme.GROUND_4))
        painter.drawEllipse(rect)
        colour = QtGui.QColor(theme.GREEN_BRIGHT if on else theme.FG_2)
        pen = QtGui.QPen(colour, 2.4)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.NoBrush)
        cx, cy = rect.center().x(), rect.center().y()
        gap = 0.0 if on else 5.0
        painter.drawRoundedRect(QtCore.QRectF(cx - 12 - gap / 2.0, cy - 4.5, 15, 9), 4.5, 4.5)
        painter.drawRoundedRect(QtCore.QRectF(cx - 3 + gap / 2.0, cy - 4.5, 15, 9), 4.5, 4.5)
        if not on:
            painter.setPen(QtGui.QPen(QtGui.QColor(theme.ORANGE_BRIGHT), 2.2, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap))
            painter.drawLine(QtCore.QPointF(cx - 4, cy + 9), QtCore.QPointF(cx + 4, cy - 9))
        if self.hasFocus():
            painter.setPen(QtGui.QPen(QtGui.QColor(theme.GREEN), 1, QtCore.Qt.DotLine))
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.drawEllipse(QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5))

    def enterEvent(self, event):
        super(LinkBadge, self).enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super(LinkBadge, self).leaveEvent(event)
        self.update()


# --------------------------------------------------------------------------------------------------
# Schematic drawing
# --------------------------------------------------------------------------------------------------

class MarginSchematic(QtWidgets.QWidget):
    """Top: the wall seen from the front with the picture area (wall plus margins) shaded.
    Bottom: the wall seen from above, with the depth kept in front of it and behind it."""

    def __init__(self, parent=None):
        super(MarginSchematic, self).__init__(parent)
        self._margins = Margins()
        self._size = (12.0, 6.0)
        self.setMinimumHeight(250)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.MinimumExpanding)

    def set_margins(self, margins):
        self._margins = margins.copy()
        self.update()

    def set_wall_size(self, length, height):
        if length > 0 and height > 0:
            self._size = (float(length), float(height))
            self.update()

    def minimumSizeHint(self):
        return QtCore.QSize(260, 250 + self.fontMetrics().height() * 3)

    def sizeHint(self):
        return QtCore.QSize(380, 290 + self.fontMetrics().height() * 3)

    @staticmethod
    def drawn(value, wall):
        """A margin as drawn: the true size, but no more than 1.2 times the wall so a huge number cannot hide the wall."""
        return min(float(value), wall * 1.2)

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        fm = painter.fontMetrics()
        m = self._margins
        length, height = self._size
        full = QtCore.QRectF(self.rect()).adjusted(2, 2, -2, -2)
        painter.setPen(QtGui.QPen(QtGui.QColor(theme.BORDER_SOFT), 1))
        painter.setBrush(QtGui.QColor(theme.GROUND_3))
        painter.drawRoundedRect(full, 8, 8)
        line = fm.height()
        top_area = QtCore.QRectF(full.left() + 12, full.top() + 10 + line, full.width() - 24, full.height() * 0.62 - 10 - line)
        # -- front view ----------------------------------------------------------------------------
        painter.setPen(QtGui.QColor(theme.FG_2))
        painter.drawText(QtCore.QRectF(full.left() + 12, full.top() + 6, full.width() - 24, line), QtCore.Qt.AlignLeft,
                         tr("facade.ui.sketch_front"))
        left, right = self.drawn(m.left, length), self.drawn(m.right, length)
        top, bottom = self.drawn(m.top, height), self.drawn(m.bottom, height)
        total_w, total_h = left + length + right, top + height + bottom
        pad_x, pad_y = fm.horizontalAdvance("00.0 m") + 8, line + 4
        room = top_area.adjusted(pad_x, pad_y, -pad_x, -pad_y)
        scale = max(0.01, min(room.width() / total_w, room.height() / total_h))
        area = QtCore.QRectF(0, 0, total_w * scale, total_h * scale)
        area.moveCenter(room.center())
        wall = QtCore.QRectF(area.left() + left * scale, area.top() + top * scale, length * scale, height * scale)
        # the picture area (shaded) and its dashed border
        painter.setPen(QtGui.QPen(QtGui.QColor(theme.GREEN), 2, QtCore.Qt.DashLine))
        painter.setBrush(QtGui.QColor(30, 202, 89, 60))
        painter.drawRect(area)
        # the wall
        painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0, 150), 1))
        painter.setBrush(QtGui.QColor("#8a9099"))
        painter.drawRect(wall)
        painter.setBrush(QtGui.QColor("#4f6a86"))
        rows, cols = (2 if height > 5 else 1), max(2, min(6, int(length / 2.5)))
        cell_w, cell_h = wall.width() / cols, wall.height() / (rows + 0.6)
        for r in range(rows):
            for c in range(cols):
                painter.drawRect(QtCore.QRectF(wall.left() + c * cell_w + cell_w * 0.2, wall.top() + (r + 0.3) * cell_h,
                                               cell_w * 0.6, cell_h * 0.7))
        # numbers on the four sides
        painter.setPen(QtGui.QColor(theme.GREEN_BRIGHT))
        mid_y, mid_x = area.center().y(), area.center().x()
        painter.drawText(QtCore.QRectF(area.left() - pad_x, mid_y - line / 2.0, pad_x - 4, line), QtCore.Qt.AlignRight, tr("facade.ui.sketch_m", v=metres(m.left)))
        painter.drawText(QtCore.QRectF(area.right() + 4, mid_y - line / 2.0, pad_x, line), QtCore.Qt.AlignLeft, tr("facade.ui.sketch_m", v=metres(m.right)))
        painter.drawText(QtCore.QRectF(mid_x - pad_x, area.top() - line - 2, 2 * pad_x, line), QtCore.Qt.AlignCenter, tr("facade.ui.sketch_m", v=metres(m.top)))
        painter.drawText(QtCore.QRectF(mid_x - pad_x, area.bottom() + 2, 2 * pad_x, line), QtCore.Qt.AlignCenter, tr("facade.ui.sketch_m", v=metres(m.bottom)))
        # -- top view (depth) ----------------------------------------------------------------------
        base = full.top() + full.height() * 0.62
        painter.setPen(QtGui.QColor(theme.FG_2))
        painter.drawText(QtCore.QRectF(full.left() + 12, base, full.width() - 24, line), QtCore.Qt.AlignLeft, tr("facade.ui.sketch_top"))
        depth_area = QtCore.QRectF(full.left() + 12, base + line + 6, full.width() - 24, full.bottom() - base - line - 16)
        label_w = max(fm.horizontalAdvance(tr("facade.ui.sketch_depth", v=metres(m.depth))), fm.horizontalAdvance(tr("facade.ui.sketch_wall"))) + 12
        strip_w = max(40.0, min(depth_area.width() - label_w - 8, depth_area.height() * 3.4))
        depth_scale = max(0.01, min(depth_area.height() / (2.0 * max(m.depth, 0.5) + 0.5), 30.0))
        band = max(m.depth * depth_scale, 3.0)
        band = min(band, depth_area.height() / 2.0)
        mid_y = depth_area.center().y()
        strip = QtCore.QRectF(0, 0, strip_w, 2 * band)
        strip.moveCenter(QtCore.QPointF(depth_area.left() + (depth_area.width() - label_w - strip_w) / 2.0 + strip_w / 2.0, mid_y))
        painter.setPen(QtGui.QPen(QtGui.QColor(theme.GREEN), 2, QtCore.Qt.DashLine))
        painter.setBrush(QtGui.QColor(30, 202, 89, 60))
        painter.drawRect(strip)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor("#8a9099"))
        painter.drawRect(QtCore.QRectF(strip.left(), mid_y - 2, strip.width(), 4))
        text_left = strip.right() + 8
        painter.setPen(QtGui.QColor(theme.GREEN_BRIGHT))
        painter.drawText(QtCore.QRectF(text_left, mid_y - line - 2, label_w, line), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                         tr("facade.ui.sketch_depth", v=metres(m.depth)))
        painter.setPen(QtGui.QColor(theme.FG_3))
        painter.drawText(QtCore.QRectF(text_left, mid_y + 2, label_w, line), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                         tr("facade.ui.sketch_wall"))


# --------------------------------------------------------------------------------------------------
# Margin editor
# --------------------------------------------------------------------------------------------------

PIXEL_LOW_CM, PIXEL_HIGH_CM = 0.5, 50.0


class MarginEditor(QtWidgets.QWidget):
    """The numbers of screen 3. It edits a copy of a `Margins` (with its two link flags); whoever uses it applies the changes.

    Signals: `margin_edited(name, value)` ("left", "right", "top", "bottom", "depth"), `link_toggled(pair, linked)` ("lr" or "tb"),
    `pixel_edited(cm)`, `apply_all_toggled(bool)`."""

    margin_edited = QtCore.Signal(str, float)
    link_toggled = QtCore.Signal(str, bool)
    pixel_edited = QtCore.Signal(float)
    apply_all_toggled = QtCore.Signal(bool)

    def __init__(self, parent=None):
        super(MarginEditor, self).__init__(parent)
        self._margins = Margins()
        self._syncing = False
        self.body = QtWidgets.QBoxLayout(QtWidgets.QBoxLayout.LeftToRight, self)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(theme.PAD_L)
        self.schematic = MarginSchematic()
        self.body.addWidget(self.schematic, 4)
        column = QtWidgets.QVBoxLayout()
        column.setSpacing(theme.PAD + 4)
        self.body.addLayout(column, 5)
        layout = column
        card = QtWidgets.QFrame()
        card.setObjectName("facadeCard")
        grid = QtWidgets.QGridLayout(card)
        grid.setContentsMargins(14, 12, 14, 12)
        grid.setHorizontalSpacing(theme.PAD + 2)
        grid.setVerticalSpacing(theme.PAD + 4)
        self.fields = {}
        for row, (first, second, pair) in enumerate((("left", "right", "lr"), ("top", "bottom", "tb"))):
            self._field(grid, row, 0, first)
            badge = LinkBadge(tr(LINK_KEYS[(pair, True)]), tr(LINK_KEYS[(pair, False)]))
            badge.setChecked(True)
            badge.toggled.connect(lambda checked, p=pair: self._on_link(p, checked))
            grid.addWidget(badge, row, 3, QtCore.Qt.AlignCenter)
            self.fields[pair] = badge
            self._field(grid, row, 4, second)
        grid.addWidget(ui.label(tr("facade.ui.depth"), "aeroMeta", wrap=False), 2, 0)
        self.depth = NumberField(0.1, Margins.MAX_DEPTH, 2)
        self.depth.value_edited.connect(lambda v: self._on_number("depth", v))
        grid.addWidget(self.depth, 2, 1)
        grid.addWidget(ui.label(tr("facade.ui.unit_m"), "aeroMeta", wrap=False), 2, 2)
        self.depth_hint = ui.label(tr("facade.ui.depth_hint"), "aeroMeta")
        grid.addWidget(self.depth_hint, 3, 0, 1, 6)
        grid.addWidget(ui.label(tr("facade.ui.pixel"), "aeroMeta", wrap=False), 4, 0)
        self.pixel = NumberField(PIXEL_LOW_CM, PIXEL_HIGH_CM, 1, step=0.5)
        self.pixel.value_edited.connect(self.pixel_edited.emit)
        self.pixel.set_value(3.0)
        grid.addWidget(self.pixel, 4, 1)
        grid.addWidget(ui.label(tr("facade.ui.unit_cm"), "aeroMeta", wrap=False), 4, 2)
        self.pixel_hint = ui.label(tr("facade.ui.pixel_hint"), "aeroMeta")
        grid.addWidget(self.pixel_hint, 5, 0, 1, 6)
        grid.setColumnStretch(5, 1)
        layout.addWidget(card)

        self.apply_all = QtWidgets.QCheckBox(tr("facade.ui.apply_all"))
        self.apply_all.setCursor(QtCore.Qt.PointingHandCursor)
        self.apply_all.setChecked(True)
        self.apply_all.toggled.connect(self.apply_all_toggled.emit)
        layout.addWidget(self.apply_all)
        self.target_note = ui.label("", "aeroMeta")
        layout.addWidget(self.target_note)
        layout.addStretch(1)

    def resizeEvent(self, event):
        super(MarginEditor, self).resizeEvent(event)
        wide = self.width() >= self.schematic.minimumSizeHint().width() + 420 * max(1.0, self.fontMetrics().height() / 20.0)
        direction = QtWidgets.QBoxLayout.LeftToRight if wide else QtWidgets.QBoxLayout.TopToBottom
        if self.body.direction() != direction:
            self.body.setDirection(direction)

    def _field(self, grid, row, col, name):
        grid.addWidget(ui.label(tr(SIDE_KEYS[name]), "aeroMeta", wrap=False), row, col)
        field = NumberField(0.0, Margins.MAX_SIDE, 2)
        field.value_edited.connect(lambda v, n=name: self._on_number(n, v))
        grid.addWidget(field, row, col + 1)
        grid.addWidget(ui.label(tr("facade.ui.unit_m"), "aeroMeta", wrap=False), row, col + 2)
        self.fields[name] = field
        return field

    # -- values ---------------------------------------------------------------------------------

    def set_margins(self, margins):
        self._margins = margins.copy()
        self._refresh()

    def margins(self):
        return self._margins.copy()

    def set_pixel_cm(self, cm):
        self.pixel.set_value(cm)

    def pixel_cm(self):
        return self.pixel.value()

    def pixel_m(self):
        return self.pixel.value() / 100.0

    def set_wall_size(self, length, height):
        self.schematic.set_wall_size(length, height)

    def set_target_note(self, text):
        self.target_note.setText(text)

    def _refresh(self):
        self._syncing = True
        try:
            m = self._margins
            for name in ("left", "right", "top", "bottom"):
                self.fields[name].set_value(getattr(m, name))
            self.depth.set_value(m.depth)
            for pair, linked in (("lr", m.link_lr), ("tb", m.link_tb)):
                self.fields[pair].setChecked(bool(linked))
            self.schematic.set_margins(m)
        finally:
            self._syncing = False

    def _on_number(self, name, value):
        if self._syncing:
            return
        getattr(self._margins, "set_" + name)(value)
        self._refresh()
        self.margin_edited.emit(name, float(value))

    def _on_link(self, pair, checked):
        if self._syncing:
            return
        getattr(self._margins, "set_link_" + pair)(checked)
        self._refresh()
        self.link_toggled.emit(pair, bool(checked))


# --------------------------------------------------------------------------------------------------
# Method picker
# --------------------------------------------------------------------------------------------------

class MethodOption(object):
    """One way of building the surface: `key` ("dense_cloud", "depth_maps", "tie_points"), the texts shown on its card (`title`,
    one-line `explain`, `quality` label), whether the chunk can do it (`ok`) and why not (`reason`), a time hint and a gentle warning."""

    def __init__(self, key, ok=True, reason="", title="", explain="", quality="", time_text="", warning="", default=False):
        self.key, self.ok, self.reason = key, bool(ok), reason or ""
        self.title, self.explain, self.quality = title or key, explain or "", quality or ""
        self.time_text, self.warning, self.default = time_text or "", warning or "", bool(default)


class MethodPicker(QtWidgets.QWidget):
    """Radio cards in quality order. An unavailable method is greyed with its reason and cannot be picked. Signal: `method_changed(key)`."""

    method_changed = QtCore.Signal(str)

    def __init__(self, parent=None):
        super(MethodPicker, self).__init__(parent)
        self._cards = {}
        self._options = {}
        self._group = QtWidgets.QButtonGroup(self)
        self._group.setExclusive(True)
        self._layout = QtWidgets.QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(theme.PAD + 4)
        self._syncing = False

    def set_options(self, options, selected=None):
        """Build the cards from `options` (list of MethodOption, best first). `selected`: the key to tick (default: the first usable)."""
        for card in self._cards.values():
            self._group.removeButton(card.button)
            card.setParent(None)
            card.deleteLater()
        self._cards, self._options = {}, {}
        self._syncing = True
        try:
            for rank, option in enumerate(options):
                card = self._build_card(rank, option)
                self._cards[option.key] = card
                self._options[option.key] = option
                self._layout.addWidget(card)
            usable = [o.key for o in options if o.ok]
            pick = selected if selected in usable else (usable[0] if usable else None)
            for key, card in self._cards.items():
                card.button.setChecked(key == pick)
                card.sync()
        finally:
            self._syncing = False
        self.method_changed.emit(pick or "")

    def selected(self):
        for key, card in self._cards.items():
            if card.button.isChecked():
                return key
        return ""

    def select(self, key):
        card = self._cards.get(key)
        if card is not None and self._options[key].ok:
            card.button.setChecked(True)

    def is_enabled(self, key):
        return key in self._options and self._options[key].ok

    def keys(self):
        return list(self._cards)

    def _build_card(self, rank, option):
        radio = QtWidgets.QRadioButton(option.title)
        radio.setCursor(QtCore.Qt.PointingHandCursor if option.ok else QtCore.Qt.ForbiddenCursor)
        radio.setEnabled(option.ok)
        radio.setFocusPolicy(QtCore.Qt.StrongFocus if option.ok else QtCore.Qt.NoFocus)
        radio.setStyleSheet("QRadioButton { font-size: 12pt; font-weight: 700; }")
        self._group.addButton(radio)
        card = ui.OptionCard(radio)
        card.grid.setColumnStretch(0, 1)
        if option.quality and option.ok:                      # "best quality" is not said of a method that cannot be used
            tag = ui.label(option.quality, "aeroMeta", wrap=False)
            tag.setStyleSheet("color: {}; font-weight: 700;".format(theme.GREEN_BRIGHT if option.ok and rank == 0 else theme.FG_2))
            card.grid.addWidget(tag, 0, 1, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        row = 1
        if option.ok:                                         # the explanation is only true when the method is available
            text = ui.label(option.explain)
            text.setContentsMargins(32, 0, 0, 0)
            card.grid.addWidget(text, row, 0, 1, 2)
            row += 1
        if not option.ok:
            reason = ui.label(tr("facade.ui.not_available", reason=option.reason or tr("facade.ui.not_available_default")), "aeroWarn")
            reason.setContentsMargins(32, 0, 0, 0)
            card.grid.addWidget(reason, row, 0, 1, 2)
        else:
            for text_value, name in ((option.time_text, "aeroMeta"), (option.warning, "aeroWarn")):
                if text_value:
                    line = ui.label(text_value, name)
                    line.setContentsMargins(32, 0, 0, 0)
                    card.grid.addWidget(line, row, 0, 1, 2)
                    row += 1
        radio.toggled.connect(lambda checked, k=option.key: self._on_toggled(k, checked))
        return card

    def _on_toggled(self, key, checked):
        if checked and not self._syncing:
            self.method_changed.emit(key)
