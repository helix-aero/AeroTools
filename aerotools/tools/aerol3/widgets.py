"""Aero-L3 UI: the small widgets of the wizard. Everything is drawn with QPainter, so no glyph depends on the font and nothing changes with the
display scaling except the size, which follows the text height. Colours come from `theme.py` only."""

import html

from ... import i18n, theme
from ...qt import QtCore, QtGui, QtWidgets

KINDS = ("ok", "warn", "stop", "note", "pending", "running")


def _color(value):
    return QtGui.QColor(value)


def _unit(widget, factor=1.15, low=16):
    return max(low, int(widget.fontMetrics().height() * factor))


class Glyph(QtWidgets.QWidget):
    """A round status mark: ok (tick), warn (exclamation mark), stop (cross), note (dot), pending (empty ring), running (turning arc)."""

    def __init__(self, kind="pending", scale=1.15, parent=None):
        super(Glyph, self).__init__(parent)
        self._kind = kind
        self._scale = scale
        self._angle = 0
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._turn)
        self.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        self.setAccessibleName(kind)

    def kind(self):
        return self._kind

    def set_kind(self, kind):
        self._kind = kind
        self.setAccessibleName(kind)
        if kind == "running" and self.isVisible():
            self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def sizeHint(self):
        d = _unit(self, self._scale)
        return QtCore.QSize(d, d)

    def minimumSizeHint(self):
        return self.sizeHint()

    def showEvent(self, event):
        super(Glyph, self).showEvent(event)
        if self._kind == "running":
            self._timer.start()

    def hideEvent(self, event):
        super(Glyph, self).hideEvent(event)
        self._timer.stop()

    def _turn(self):
        self._angle = (self._angle - 24) % 360
        self.update()

    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        d = min(self.width(), self.height())
        w = max(1.6, d / 11.0)
        r = QtCore.QRectF((self.width() - d) / 2.0 + w, (self.height() - d) / 2.0 + w, d - 2 * w, d - 2 * w)
        c = r.center()
        u = r.width() / 2.0
        p.setBrush(QtCore.Qt.NoBrush)
        kind = self._kind
        if kind == "ok":
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(_color(theme.GREEN))
            p.drawEllipse(r)
            self._stroke(p, theme.GREEN_ON, w * 1.1)
            p.drawPolyline([QtCore.QPointF(c.x() - 0.45 * u, c.y() + 0.02 * u), QtCore.QPointF(c.x() - 0.1 * u, c.y() + 0.38 * u),
                            QtCore.QPointF(c.x() + 0.5 * u, c.y() - 0.35 * u)])
        elif kind == "stop":
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(_color(theme.ORANGE))
            p.drawEllipse(r)
            self._stroke(p, "#ffffff", w * 1.1)
            k = 0.36 * u
            p.drawLine(QtCore.QPointF(c.x() - k, c.y() - k), QtCore.QPointF(c.x() + k, c.y() + k))
            p.drawLine(QtCore.QPointF(c.x() - k, c.y() + k), QtCore.QPointF(c.x() + k, c.y() - k))
        elif kind == "warn":
            self._stroke(p, theme.ORANGE_BRIGHT, w)
            p.drawEllipse(r)
            p.drawLine(QtCore.QPointF(c.x(), c.y() - 0.52 * u), QtCore.QPointF(c.x(), c.y() + 0.12 * u))
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(_color(theme.ORANGE_BRIGHT))
            p.drawEllipse(QtCore.QPointF(c.x(), c.y() + 0.5 * u), w * 0.75, w * 0.75)
        elif kind == "note":
            self._stroke(p, theme.FG_3, w)
            p.drawEllipse(r)
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(_color(theme.FG_3))
            p.drawEllipse(c, w * 1.1, w * 1.1)
        elif kind == "running":
            self._stroke(p, theme.BORDER_STRONG, w)
            p.drawEllipse(r)
            self._stroke(p, theme.GREEN_BRIGHT, w * 1.25)
            p.drawArc(r, self._angle * 16, 100 * 16)
        else:
            self._stroke(p, theme.FG_3, w)
            p.drawEllipse(r)
        p.end()

    @staticmethod
    def _stroke(p, color, width):
        pen = QtGui.QPen(_color(color))
        pen.setWidthF(width)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        pen.setJoinStyle(QtCore.Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(QtCore.Qt.NoBrush)


class Arrow(QtWidgets.QWidget):
    """A thin chevron pointing right, between the steps of a chain."""

    def __init__(self, parent=None):
        super(Arrow, self).__init__(parent)
        self.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)

    def sizeHint(self):
        d = self.fontMetrics().height()
        return QtCore.QSize(int(d * 0.8), d)

    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        pen = QtGui.QPen(_color(theme.FG_3))
        pen.setWidthF(max(1.5, self.height() / 12.0))
        pen.setCapStyle(QtCore.Qt.RoundCap)
        pen.setJoinStyle(QtCore.Qt.RoundJoin)
        p.setPen(pen)
        c = QtCore.QPointF(self.width() / 2.0, self.height() / 2.0)
        h = self.height() * 0.2
        p.drawPolyline([QtCore.QPointF(c.x() - h * 0.5, c.y() - h), QtCore.QPointF(c.x() + h * 0.5, c.y()), QtCore.QPointF(c.x() - h * 0.5, c.y() + h)])
        p.end()


def tooltip_html(text):
    """Plain text as a rich text, which a tooltip wraps at a readable width (a plain one stays on one line)."""
    return "<p>" + html.escape(text).replace("\n", "<br>") + "</p>"


class InfoButton(QtWidgets.QToolButton):
    """A small round "i". The tip shows on hover and on click or Space, so a touch screen and the keyboard get it too."""

    def __init__(self, text, parent=None):
        super(InfoButton, self).__init__(parent)
        self._text = text
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        self.setStyleSheet(theme.tooltip_stylesheet())
        self.setAccessibleName(i18n.t("l3.ui.info"))
        self.set_text(text)
        self.clicked.connect(self.show_tip)

    def set_text(self, text):
        self._text = text
        self.setToolTip(tooltip_html(text))
        self.setAccessibleDescription(text)

    def text_tip(self):
        return self._text

    def sizeHint(self):
        d = _unit(self, 1.0, 16)
        return QtCore.QSize(d, d)

    def minimumSizeHint(self):
        return self.sizeHint()

    def show_tip(self):
        QtWidgets.QToolTip.showText(self.mapToGlobal(QtCore.QPoint(0, self.height())), tooltip_html(self._text), self)

    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        d = min(self.width(), self.height())
        w = max(1.3, d / 14.0)
        r = QtCore.QRectF((self.width() - d) / 2.0 + w, (self.height() - d) / 2.0 + w, d - 2 * w, d - 2 * w)
        c = r.center()
        u = r.width() / 2.0
        hot = self.underMouse() or self.hasFocus()
        color = _color(theme.FG_0 if hot else theme.FG_3)
        if self.hasFocus():
            color = _color(theme.GREEN_BRIGHT)
        pen = QtGui.QPen(color)
        pen.setWidthF(w)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(QtCore.Qt.NoBrush)
        p.drawEllipse(r)
        p.drawLine(QtCore.QPointF(c.x(), c.y() - 0.05 * u), QtCore.QPointF(c.x(), c.y() + 0.5 * u))
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(QtCore.QPointF(c.x(), c.y() - 0.48 * u), w * 0.85, w * 0.85)
        p.end()


class Segmented(QtWidgets.QWidget):
    """A choice between a few options, side by side. `changed(key)` is sent when the choice changes."""

    changed = QtCore.Signal(str)

    def __init__(self, options, parent=None):
        super(Segmented, self).__init__(parent)
        self._buttons = {}
        self._group = QtWidgets.QButtonGroup(self)
        self._group.setExclusive(True)
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        count = len(options)
        for index, (key, text) in enumerate(options):
            button = QtWidgets.QPushButton(text)
            button.setObjectName("l3Seg")
            button.setCheckable(True)
            button.setCursor(QtCore.Qt.PointingHandCursor)
            button.setAutoDefault(False)
            button.setMinimumHeight(36)
            button.setMinimumWidth(96)
            button.setProperty("seg", "first" if index == 0 else ("last" if index == count - 1 else "mid"))
            button.clicked.connect(lambda _checked=False, k=key: self._picked(k))
            self._group.addButton(button)
            self._buttons[key] = button
            row.addWidget(button)
        self._value = options[0][0]
        self._buttons[self._value].setChecked(True)

    def _picked(self, key):
        if key != self._value:
            self._value = key
            self.changed.emit(key)

    def value(self):
        return self._value

    def set_value(self, key):
        if key in self._buttons:
            self._value = key
            self._buttons[key].setChecked(True)

    def button(self, key):
        return self._buttons[key]


class ChevronButton(QtWidgets.QAbstractButton):
    """A line of text with a chevron in front that turns down when the button is on. Used to open and close a part of the screen."""

    def __init__(self, text, parent=None):
        super(ChevronButton, self).__init__(parent)
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setSizePolicy(QtWidgets.QSizePolicy.Maximum, QtWidgets.QSizePolicy.Fixed)

    def sizeHint(self):
        fm = self.fontMetrics()
        return QtCore.QSize(fm.horizontalAdvance(self.text()) + fm.height() + 16, fm.height() + 12)

    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        fm = self.fontMetrics()
        hot = self.underMouse() or self.hasFocus()
        color = _color(theme.FG_0 if hot else theme.FG_2)
        size = fm.height() * 0.5
        cx, cy = 4 + size / 2.0, self.height() / 2.0
        pen = QtGui.QPen(color)
        pen.setWidthF(max(1.6, fm.height() / 11.0))
        pen.setCapStyle(QtCore.Qt.RoundCap)
        pen.setJoinStyle(QtCore.Qt.RoundJoin)
        p.setPen(pen)
        h = size * 0.38
        if self.isChecked():
            p.drawPolyline([QtCore.QPointF(cx - h, cy - h * 0.5), QtCore.QPointF(cx, cy + h * 0.6), QtCore.QPointF(cx + h, cy - h * 0.5)])
        else:
            p.drawPolyline([QtCore.QPointF(cx - h * 0.5, cy - h), QtCore.QPointF(cx + h * 0.6, cy), QtCore.QPointF(cx - h * 0.5, cy + h)])
        p.setFont(self.font())
        p.drawText(QtCore.QRectF(size + 14, 0, self.width() - size - 14, self.height()), QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, self.text())
        if self.hasFocus():
            pen = QtGui.QPen(_color(theme.GREEN))
            pen.setWidthF(1.0)
            p.setPen(pen)
            p.setBrush(QtCore.Qt.NoBrush)
            p.drawRoundedRect(QtCore.QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 4, 4)
        p.end()


class Disclosure(QtWidgets.QWidget):
    """A closed part of the screen opened by a `ChevronButton`. Starts closed; the body takes room only when it is open."""

    toggled = QtCore.Signal(bool)

    def __init__(self, text, body, parent=None):
        super(Disclosure, self).__init__(parent)
        self.body = body
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.PAD)
        self.head = ChevronButton(text)
        self.head.toggled.connect(self._toggle)
        layout.addWidget(self.head, 0, QtCore.Qt.AlignLeft)
        layout.addWidget(body)
        body.setVisible(False)

    def _toggle(self, on):
        self.body.setVisible(bool(on))
        self.toggled.emit(bool(on))

    def is_open(self):
        return self.head.isChecked()

    def set_open(self, on):
        self.head.setChecked(bool(on))
