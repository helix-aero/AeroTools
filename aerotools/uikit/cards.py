"""Compact components built on the kit's tokens: Card, Stat, KeyValue, ChoiceCard / ListRow (with `ChoiceGroup`), Chip, MultiSelect, Slider.

Every colour, size and radius is read from `tokens` when the widget paints, so a skin switch reaches all of them.
"""

from .. import i18n
from ..qt import QtCore, QtGui, QtWidgets
from . import icons, metrics, style, tokens
from .blocks import hbox, vbox
from .controls import InfoButton, NumberField, _Ring, _qcolor, label, set_tone
from .metrics import px

Qt = QtCore.Qt


# --------------------------------------------------------------------------------------------------
# Card, Stat, KeyValue
# --------------------------------------------------------------------------------------------------

class Card(QtWidgets.QFrame):
    """A bordered box: optional heading (with an optional "i"), then content added with `add(widget)` or `form()`."""

    def __init__(self, title="", info=None, parent=None):
        super(Card, self).__init__(parent)
        self.setProperty("ui", "card")
        self.setAttribute(Qt.WA_StyledBackground, True)
        outer = vbox((tokens.M,) * 4, tokens.S, parent=self)
        self.head = hbox(spacing=tokens.S)
        self.title = label(title, "strong", wrap=False)
        self.info = InfoButton(info) if info else None
        self.head.addWidget(self.title)
        if self.info is not None:
            self.head.addWidget(self.info)
        self.head.addStretch(1)
        self.title.setVisible(bool(title))
        if title or info:
            outer.addLayout(self.head)
        self.body = vbox(spacing=tokens.S)
        outer.addLayout(self.body)

    def add(self, widget, stretch=0):
        self.body.addWidget(widget, stretch)
        return widget

    def add_right(self, widget):
        """A widget at the right end of the heading."""
        self.head.addWidget(widget)
        return widget

    def form(self):
        from .blocks import FormGrid
        grid = FormGrid()
        self.body.addWidget(grid)
        return grid


class Stat(QtWidgets.QFrame):
    """A figure with its caption: a count, a size, a residual. `tone` colours the figure (success, warning, danger...)."""

    def __init__(self, value="", caption="", tone=None, parent=None):
        super(Stat, self).__init__(parent)
        self.setProperty("ui", "card")
        self.setAttribute(Qt.WA_StyledBackground, True)
        lay = vbox((tokens.M, tokens.S + 2, tokens.M, tokens.S + 2), 0, parent=self)
        self.value_label = label(str(value), "title", tone, wrap=False)
        self.caption_label = label(caption, "caption", wrap=False)
        lay.addWidget(self.value_label)
        lay.addWidget(self.caption_label)

    def set_value(self, value, tone=None):
        self.value_label.setText(str(value))
        set_tone(self.value_label, tone)

    def set_caption(self, text):
        self.caption_label.setText(text)

    def value(self):
        return self.value_label.text()


class KeyValue(QtWidgets.QWidget):
    """Facts as `key  value` lines: muted key column of one width, selectable value that wraps (a path breaks anywhere)."""

    def __init__(self, items=(), key_width=None, parent=None):
        super(KeyValue, self).__init__(parent)
        self._grid = QtWidgets.QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(px(tokens.M))
        self._grid.setVerticalSpacing(px(tokens.XS))
        self._grid.setColumnStretch(1, 1)
        self._key_width = key_width
        self.rows = []
        self.set_items(items)

    def set_items(self, items):
        for key_label, value_label in self.rows:
            key_label.deleteLater()
            value_label.deleteLater()
        self.rows = []
        for key, value in items:
            self.add(key, value)

    def add(self, key, value, tone=None):
        row = len(self.rows)
        key_label = label(key, "field", wrap=False)
        if self._key_width:
            key_label.setFixedWidth(px(self._key_width))
        value_label = label(str(value), "body", tone, selectable=True, break_long=True)
        self._grid.addWidget(key_label, row, 0, Qt.AlignTop)
        self._grid.addWidget(value_label, row, 1)
        self.rows.append((key_label, value_label))
        return value_label

    def value_of(self, key):
        for key_label, value_label in self.rows:
            if key_label.text() == key:
                return value_label.plain_text()
        return None


# --------------------------------------------------------------------------------------------------
# Selectable rows and cards
# --------------------------------------------------------------------------------------------------

class ChoiceGroup(QtCore.QObject):
    """Makes `ChoiceCard`s / `ListRow`s exclusive: one on at a time. `changed(value)` when the user picks one; Up / Down / Left / Right
    move the choice inside the group."""
    changed = QtCore.Signal(object)

    def __init__(self, parent=None):
        super(ChoiceGroup, self).__init__(parent)
        self.items = []

    def add(self, item):
        self.items.append(item)
        item._group = self
        item.clicked.connect(lambda *_a, it=item: self._picked(it))
        return item

    def _picked(self, item):
        for other in self.items:
            if other is not item:
                other.setChecked(False)
        item.setChecked(True)
        self.changed.emit(item.value)

    def value(self):
        for item in self.items:
            if item.isChecked():
                return item.value
        return None

    def set_value(self, value):
        for item in self.items:
            item.setChecked(item.value == value)

    def step(self, item, delta):
        enabled = [i for i in self.items if i.isEnabled()]
        if item in enabled:
            target = enabled[(enabled.index(item) + delta) % len(enabled)]
            self._picked(target)
            target.setFocus(Qt.TabFocusReason)


class _Selectable(_Ring, QtWidgets.QAbstractButton):
    """Shared behaviour of a card or a row you pick: checkable, one Tab stop, arrows move inside its group."""
    _group = None

    def __init__(self, value, parent=None):
        super(_Selectable, self).__init__(parent)
        self.value = value
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAutoExclusive(False)

    def keyPressEvent(self, event):
        if self._group is not None and event.key() in (Qt.Key_Down, Qt.Key_Right, Qt.Key_Up, Qt.Key_Left):
            self._group.step(self, 1 if event.key() in (Qt.Key_Down, Qt.Key_Right) else -1)
            event.accept()
            return
        super(_Selectable, self).keyPressEvent(event)


class ChoiceCard(_Selectable):
    """A radio card: a round mark, a title and a short description. Put several in a `ChoiceGroup`."""

    def __init__(self, title, text="", value=None, danger=False, parent=None):
        super(ChoiceCard, self).__init__(value if value is not None else title, parent)
        self.title, self.text_line, self.danger = title, text, danger
        self.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Minimum)
        policy = self.sizePolicy()
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def _ring_radius(self):
        return tokens.RADIUS_L

    def _text_rect_width(self, width):
        return max(px(40), width - 2 * px(tokens.M) - px(tokens.ICON) - px(tokens.M))

    def _desc_height(self, width):
        if not self.text_line:
            return 0
        fm = QtGui.QFontMetrics(metrics.font("small"))
        return fm.boundingRect(QtCore.QRect(0, 0, self._text_rect_width(width), 10000), Qt.TextWordWrap, self.text_line).height()

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        fm = QtGui.QFontMetrics(metrics.font("body", strong=True))
        gap = px(tokens.XS) if self.text_line else 0
        return 2 * px(tokens.S + 2) + fm.height() + gap + self._desc_height(width)

    def minimumSizeHint(self):
        return QtCore.QSize(px(160), self.heightForWidth(px(320)) if self.text_line else self.heightForWidth(px(160)))

    def sizeHint(self):
        return QtCore.QSize(px(320), self.heightForWidth(px(320)))

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        on, enabled, hover = self.isChecked(), self.isEnabled(), self.underMouse()
        edge = tokens.DANGER if (on and self.danger) else (tokens.ACCENT if on else (tokens.BORDER_STRONG if hover and enabled else tokens.BORDER))
        fill = tokens.DANGER_SOFT if (on and self.danger) else (tokens.ACCENT_SOFT if on else (tokens.SURFACE_HOVER if hover and enabled else tokens.CARD))
        radius = float(px(tokens.RADIUS_L))
        p.setPen(QtGui.QPen(_qcolor(edge), 1))
        p.setBrush(_qcolor(fill))
        p.drawRoundedRect(QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        side = px(tokens.ICON)
        pad = px(tokens.M)
        mark = QtCore.QRectF(pad, px(tokens.S + 2) + 1, side, side).adjusted(1.5, 1.5, -1.5, -1.5)
        p.setPen(QtGui.QPen(_qcolor(edge if enabled else tokens.TEXT_DISABLED), 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(mark)
        if on:
            p.setPen(Qt.NoPen)
            p.setBrush(_qcolor(tokens.DANGER if self.danger else tokens.ACCENT))
            p.drawEllipse(mark.center(), mark.width() * 0.28, mark.width() * 0.28)
        left = pad + side + px(tokens.M)
        width = self.width() - left - pad
        p.setFont(metrics.font("body", strong=True))
        p.setPen(_qcolor(tokens.TEXT_DISABLED if not enabled else tokens.TEXT_STRONG))
        fm = p.fontMetrics()
        top = px(tokens.S + 2)
        p.drawText(QtCore.QRectF(left, top, width, fm.height()), Qt.AlignLeft | Qt.AlignVCenter, fm.elidedText(self.title, Qt.ElideRight, width))
        if self.text_line:
            p.setFont(metrics.font("small"))
            p.setPen(_qcolor(tokens.TEXT_DISABLED if not enabled else tokens.TEXT_MUTED))
            p.drawText(QtCore.QRectF(left, top + fm.height() + px(tokens.XS), width, self._desc_height(self.width())),
                       Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, self.text_line)
        p.end()
        if self.force_ring or (self.hasFocus() and self._keyboard):
            self._draw_ring()


class ListRow(_Selectable):
    """A selectable row of a short list: `text`, an optional muted `detail` under it and an optional `trailing` text on the right."""

    def __init__(self, text, detail="", trailing="", value=None, parent=None):
        super(ListRow, self).__init__(value if value is not None else text, parent)
        self.row_text, self.detail, self.trailing = text, detail, trailing
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.setFixedHeight(self._height())

    def _height(self):
        if not self.detail:
            return px(tokens.CONTROL_H)
        return 2 * px(tokens.S) + QtGui.QFontMetrics(metrics.font("body")).height() + QtGui.QFontMetrics(metrics.font("small")).height()

    def sizeHint(self):
        return QtCore.QSize(px(240), self._height())

    def minimumSizeHint(self):
        return QtCore.QSize(px(120), self._height())

    def set_texts(self, text, detail="", trailing=""):
        self.row_text, self.detail, self.trailing = text, detail, trailing
        self.setFixedHeight(self._height())
        self.update()

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        on, enabled, hover = self.isChecked(), self.isEnabled(), self.underMouse()
        radius = float(px(tokens.RADIUS))
        if on or (hover and enabled):
            p.setPen(Qt.NoPen)
            p.setBrush(_qcolor(tokens.SELECTED_ROW if on else tokens.SURFACE_HOVER))
            p.drawRoundedRect(QtCore.QRectF(self.rect()), radius, radius)
        pad = px(tokens.S + 2)
        right = self.width() - pad
        p.setFont(metrics.font("body"))
        fm = p.fontMetrics()
        if self.trailing:
            p.setFont(metrics.font("small"))
            p.setPen(_qcolor(tokens.TEXT_DISABLED if not enabled else tokens.TEXT_MUTED))
            tw = p.fontMetrics().horizontalAdvance(self.trailing)
            p.drawText(QtCore.QRectF(right - tw, 0, tw, self.height()), Qt.AlignRight | Qt.AlignVCenter, self.trailing)
            right -= tw + px(tokens.M)
        p.setFont(metrics.font("body", strong=on))
        p.setPen(_qcolor(tokens.TEXT_DISABLED if not enabled else (tokens.TEXT_STRONG if on else tokens.TEXT)))
        width = max(px(8), right - pad)
        if self.detail:
            top = px(tokens.S) - 1
            p.drawText(QtCore.QRectF(pad, top, width, fm.height()), Qt.AlignLeft | Qt.AlignVCenter,
                       p.fontMetrics().elidedText(self.row_text, Qt.ElideRight, width))
            p.setFont(metrics.font("small"))
            p.setPen(_qcolor(tokens.TEXT_DISABLED if not enabled else tokens.TEXT_MUTED))
            p.drawText(QtCore.QRectF(pad, top + fm.height(), width, p.fontMetrics().height()), Qt.AlignLeft | Qt.AlignVCenter,
                       p.fontMetrics().elidedText(self.detail, Qt.ElideRight, width))
        else:
            p.drawText(QtCore.QRectF(pad, 0, width, self.height()), Qt.AlignLeft | Qt.AlignVCenter,
                       p.fontMetrics().elidedText(self.row_text, Qt.ElideRight, width))
        p.end()
        if self.force_ring or (self.hasFocus() and self._keyboard):
            self._draw_ring()


# --------------------------------------------------------------------------------------------------
# Chip
# --------------------------------------------------------------------------------------------------

class Chip(_Ring, QtWidgets.QAbstractButton):
    """A small on / off pill (a filter, a layer): checkable, same API as a checkbox (`isChecked`, `setChecked`, `toggled`)."""
    ring_radius = tokens.PILL

    def __init__(self, text="", checked=False, parent=None):
        super(Chip, self).__init__(parent)
        self.setText(text)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)

    def _h(self):
        return px(tokens.CONTROL_H - 8)

    def sizeHint(self):
        return QtCore.QSize(QtGui.QFontMetrics(metrics.font("small", strong=True)).horizontalAdvance(self.text()) + 2 * px(tokens.M) + 2, self._h())

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        on, enabled, hover = self.isChecked(), self.isEnabled(), self.underMouse()
        rect = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if on:
            p.setPen(QtGui.QPen(_qcolor(tokens.ACCENT if enabled else tokens.BORDER_STRONG), 1))
            p.setBrush(_qcolor(tokens.ACCENT_SOFT if enabled else tokens.SURFACE_PRESSED))
        else:
            p.setPen(QtGui.QPen(_qcolor(tokens.BORDER_STRONG if hover and enabled else tokens.BORDER), 1))
            p.setBrush(_qcolor(tokens.SURFACE_HOVER if hover and enabled else tokens.SURFACE))
        p.drawRoundedRect(rect, rect.height() / 2.0, rect.height() / 2.0)
        p.setFont(metrics.font("small", strong=on))
        p.setPen(_qcolor(tokens.TEXT_DISABLED if not enabled else (tokens.TEXT_STRONG if on else tokens.TEXT_MUTED)))
        p.drawText(rect, Qt.AlignCenter, self.text())
        p.end()
        if self.force_ring or (self.hasFocus() and self._keyboard):
            self._draw_ring()


# --------------------------------------------------------------------------------------------------
# MultiSelect: a menu button with ticks
# --------------------------------------------------------------------------------------------------

class _TickMenu(QtWidgets.QMenu):
    """A menu that stays open while ticking."""

    def mouseReleaseEvent(self, event):
        action = self.actionAt(event.pos())
        if action is not None and action.isCheckable() and action.isEnabled():
            action.trigger()
            event.accept()
            return
        super(_TickMenu, self).mouseReleaseEvent(event)


class MultiSelect(_Ring, QtWidgets.QAbstractButton):
    """A drop-down that lets you tick several options: the button shows `title` and the count; `changed(list of keys)`.

    `options` = [(key, text)]. `selected()` / `set_selected(keys)`. The menu stays open while you tick."""
    changed = QtCore.Signal(object)

    def __init__(self, options=(), title="", selected=(), parent=None):
        super(MultiSelect, self).__init__(parent)
        self._title = title
        self._options = []
        self._chosen = []
        self._menu = _TickMenu(self)
        self.setFixedHeight(px(tokens.CONTROL_H))
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        self.clicked.connect(self.open_menu)
        self.set_options(options, selected)

    def set_options(self, options, selected=()):
        self._options = list(options)
        self._chosen = [k for k, _t in self._options if k in selected]
        self._menu.clear()
        self._menu.setStyleSheet(style.menu_stylesheet())
        for key, text in self._options:
            action = self._menu.addAction(text)
            action.setCheckable(True)
            action.setChecked(key in self._chosen)
            action.toggled.connect(lambda on, k=key: self._toggled(k, on))
        self.update()

    def _toggled(self, key, on):
        if on and key not in self._chosen:
            self._chosen = [k for k, _t in self._options if k in self._chosen or k == key]
        elif not on and key in self._chosen:
            self._chosen.remove(key)
        self.update()
        self.changed.emit(list(self._chosen))

    def selected(self):
        return list(self._chosen)

    def set_selected(self, keys):
        self._chosen = [k for k, _t in self._options if k in keys]
        for action, (key, _t) in zip(self._menu.actions(), self._options):
            action.blockSignals(True)
            action.setChecked(key in self._chosen)
            action.blockSignals(False)
        self.update()

    def menu(self):
        return self._menu

    def summary(self):
        count = len(self._chosen)
        if count == 0:
            return self._title
        if count == 1:
            return dict(self._options).get(self._chosen[0], self._title)
        return "%s (%d)" % (self._title, count) if self._title else str(count)

    def open_menu(self):
        self._menu.setMinimumWidth(self.width())
        self._menu.popup(self.mapToGlobal(QtCore.QPoint(0, self.height() + px(2))))

    def sizeHint(self):
        fm = QtGui.QFontMetrics(metrics.font("body"))
        widest = max([fm.horizontalAdvance(self._title + " (99)")] + [fm.horizontalAdvance(t) for _k, t in self._options] + [0])
        return QtCore.QSize(widest + px(tokens.S + 2) + px(28), px(tokens.CONTROL_H))

    def minimumSizeHint(self):
        return QtCore.QSize(px(96), px(tokens.CONTROL_H))

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        enabled, hover = self.isEnabled(), self.underMouse()
        rect = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QtGui.QPen(_qcolor(tokens.BORDER_STRONG if hover and enabled else tokens.BORDER), 1))
        p.setBrush(_qcolor(tokens.SURFACE if enabled else tokens.BG))
        p.drawRoundedRect(rect, px(tokens.RADIUS), px(tokens.RADIUS))
        p.setFont(metrics.font("body"))
        p.setPen(_qcolor(tokens.TEXT_DISABLED if not enabled else (tokens.TEXT if self._chosen else tokens.TEXT_MUTED)))
        text = p.fontMetrics().elidedText(self.summary(), Qt.ElideRight, self.width() - px(tokens.S + 2) - px(28))
        p.drawText(QtCore.QRectF(px(tokens.S + 2), 0, self.width() - px(28), self.height()), Qt.AlignLeft | Qt.AlignVCenter, text)
        side = px(tokens.ICON)
        icons.paint(p, "chevron_down", QtCore.QRectF(self.width() - px(28) + (px(28) - side) / 2.0, (self.height() - side) / 2.0, side, side),
                    tokens.TEXT_DISABLED if not enabled else tokens.TEXT_MUTED)
        p.end()
        if self.force_ring or (self.hasFocus() and self._keyboard):
            self._draw_ring()


# --------------------------------------------------------------------------------------------------
# Slider
# --------------------------------------------------------------------------------------------------

class _Track(_Ring, QtWidgets.QWidget):
    """The painted groove and handle of a `Slider`."""
    moved = QtCore.Signal(float)

    def __init__(self, owner):
        super(_Track, self).__init__(owner)
        self.owner = owner
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(px(tokens.CONTROL_H))
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self._drag = False

    def _knob(self):
        return px(16)

    def _x_of(self, fraction):
        half = self._knob() / 2.0
        return half + fraction * max(1.0, self.width() - 2 * half)

    def _fraction_at(self, x):
        half = self._knob() / 2.0
        return min(1.0, max(0.0, (x - half) / max(1.0, self.width() - 2 * half)))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.isEnabled():
            self._drag = True
            self.setFocus(Qt.MouseFocusReason)
            self.owner._from_fraction(self._fraction_at(event.pos().x()))

    def mouseMoveEvent(self, event):
        if self._drag:
            self.owner._from_fraction(self._fraction_at(event.pos().x()))

    def mouseReleaseEvent(self, event):
        self._drag = False

    def wheelEvent(self, event):
        if self.hasFocus():
            self.owner._nudge(1 if event.angleDelta().y() > 0 else -1, bool(event.modifiers() & Qt.ShiftModifier))
            event.accept()
        else:
            event.ignore()

    def keyPressEvent(self, event):
        key = event.key()
        big = bool(event.modifiers() & Qt.ShiftModifier)
        if key in (Qt.Key_Left, Qt.Key_Down):
            self.owner._nudge(-1, big)
        elif key in (Qt.Key_Right, Qt.Key_Up):
            self.owner._nudge(1, big)
        elif key == Qt.Key_PageUp:
            self.owner._nudge(1, True)
        elif key == Qt.Key_PageDown:
            self.owner._nudge(-1, True)
        elif key == Qt.Key_Home:
            self.owner.set_value(self.owner.minimum, emit=True)
        elif key == Qt.Key_End:
            self.owner.set_value(self.owner.maximum, emit=True)
        else:
            super(_Track, self).keyPressEvent(event)
            return
        event.accept()

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        enabled = self.isEnabled()
        mid = self.height() / 2.0
        half = self._knob() / 2.0
        groove = px(4)
        x = self._x_of(self.owner.fraction())
        p.setPen(Qt.NoPen)
        p.setBrush(_qcolor(tokens.SURFACE_PRESSED))
        p.drawRoundedRect(QtCore.QRectF(half, mid - groove / 2.0, self.width() - 2 * half, groove), groove / 2.0, groove / 2.0)
        p.setBrush(_qcolor(tokens.ACCENT if enabled else tokens.BORDER_STRONG))
        p.drawRoundedRect(QtCore.QRectF(half, mid - groove / 2.0, max(0.0, x - half), groove), groove / 2.0, groove / 2.0)
        hot = self._drag or self.underMouse()
        p.setPen(QtGui.QPen(_qcolor(tokens.ACCENT if enabled else tokens.BORDER_STRONG), 2))
        p.setBrush(_qcolor(tokens.TEXT_STRONG if hot and enabled else tokens.TEXT if enabled else tokens.TEXT_DISABLED))
        p.drawEllipse(QtCore.QPointF(x, mid), half - 1.5, half - 1.5)
        p.end()
        if self.force_ring or (self.hasFocus() and self._keyboard):
            self._draw_ring_knob(x, mid)

    def _draw_ring_knob(self, x, mid):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        width = float(px(tokens.RING_W))
        pen = QtGui.QPen(_qcolor(tokens.RING))
        pen.setWidthF(width)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        radius = self._knob() / 2.0 + width / 2.0 + 1
        p.drawEllipse(QtCore.QPointF(x, mid), radius, radius)
        p.end()


class Slider(QtWidgets.QWidget):
    """A value between two limits: drag, click, Left / Right (Shift or Page = ten steps), Home / End. `typed=True` adds a number box
    that follows the slider and can be typed into. `valueChanged(float)` is sent when the user changes it."""
    valueChanged = QtCore.Signal(float)

    def __init__(self, value=0.0, minimum=0.0, maximum=100.0, decimals=0, step=1.0, unit="", typed=False, parent=None):
        super(Slider, self).__init__(parent)
        self.minimum, self.maximum, self.decimals, self.step, self.unit = float(minimum), float(maximum), int(decimals), float(step), unit
        self._value = self._snap(value)
        lay = hbox(spacing=tokens.M, parent=self)
        self.track = _Track(self)
        lay.addWidget(self.track, 1)
        self.number = None
        if typed:
            self.number = NumberField(self._value, self.minimum, self.maximum, self.decimals, self.step, unit)
            self.number.valueChanged.connect(lambda v: self.set_value(v, emit=True))
            lay.addWidget(self.number)
        self.setFocusProxy(self.track)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

    def _snap(self, value):
        value = min(self.maximum, max(self.minimum, float(value)))
        if self.step > 0:
            value = self.minimum + round((value - self.minimum) / self.step) * self.step
            value = min(self.maximum, max(self.minimum, value))
        return round(value, self.decimals)

    def fraction(self):
        span = self.maximum - self.minimum
        return 0.0 if span <= 0 else (self._value - self.minimum) / span

    def value(self):
        return self._value

    def set_value(self, value, emit=False):
        new = self._snap(value)
        changed = new != self._value
        self._value = new
        if self.number is not None and (self.number.value() is None or abs(self.number.value() - new) > 1e-12):
            self.number.set_value(new)
        self.track.update()
        if emit and changed:
            self.valueChanged.emit(new)

    def set_range(self, minimum, maximum):
        self.minimum, self.maximum = float(minimum), float(maximum)
        if self.number is not None:
            self.number.minimum, self.number.maximum = self.minimum, self.maximum
        self.set_value(self._value)

    def _from_fraction(self, fraction):
        self.set_value(self.minimum + fraction * (self.maximum - self.minimum), emit=True)

    def _nudge(self, direction, big):
        self.set_value(self._value + direction * self.step * (10 if big else 1), emit=True)

    def minimumSizeHint(self):
        return QtCore.QSize(px(120) + (px(tokens.FIELD_W + tokens.M) if self.number is not None else 0), px(tokens.CONTROL_H))

    def sizeHint(self):
        return QtCore.QSize(px(260), px(tokens.CONTROL_H))
