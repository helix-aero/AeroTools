"""Small widgets of the Aero-PPK window that the UI kit does not have, built from kit tokens only (no colour, size or radius of their own):
a selectable row, a network chip, the satellite mark of the orbits and the drop-down of the base station networks."""

from ...qt import QtCore, QtGui, QtWidgets
from ... import i18n, uikit
from ...uikit import icons, metrics, tokens
from ...uikit.controls import Button
from ...uikit.metrics import px

Qt = QtCore.Qt


def qcolor(value, alpha=None):
    color = QtGui.QColor(value)
    if alpha is not None:
        color.setAlpha(alpha)
    return color


def _status_icons():
    return {"ok": ("success", tokens.SUCCESS), "warn": ("warn", tokens.WARNING), "stop": ("error", tokens.DANGER),
           "note": ("info", tokens.INFO), "pending": ("dot", tokens.TEXT_DISABLED)}


def status_icon(kind):
    """A QIcon of a status mark (ok, warn, stop, note, pending) for a table cell."""
    name, color = _status_icons().get(kind, _status_icons()["pending"])
    return icons.icon(name, color, None, px(tokens.ICON))


# --------------------------------------------------------------------------------------------------
# A row that can be picked (basket, bases of the results)
# --------------------------------------------------------------------------------------------------

class PickRow(QtWidgets.QFrame):
    """A thin clickable row: surface, accent edge and tint when it is the picked one. Put labels and marks in its layout."""

    def __init__(self, picked=False, parent=None):
        super(PickRow, self).__init__(parent)
        self._picked = bool(picked)
        self._hover = False
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)

    def picked(self):
        return self._picked

    def set_picked(self, on):
        if bool(on) != self._picked:
            self._picked = bool(on)
            self.update()

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super(PickRow, self).enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super(PickRow, self).leaveEvent(event)

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        fill = tokens.ACCENT_SOFT if self._picked else (tokens.SURFACE_HOVER if self._hover else tokens.SURFACE)
        p.setBrush(qcolor(fill))
        p.setPen(QtGui.QPen(qcolor(tokens.ACCENT if self._picked else tokens.BORDER), 1))
        p.drawRoundedRect(QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), px(tokens.RADIUS), px(tokens.RADIUS))
        p.end()


# --------------------------------------------------------------------------------------------------
# Network chip
# --------------------------------------------------------------------------------------------------

class NetChip(QtWidgets.QWidget):
    """A small tag in the colour of a network (a data colour) with its short name."""

    def __init__(self, text, color, parent=None):
        super(NetChip, self).__init__(parent)
        self._text, self._color = str(text), color
        self.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)

    def text(self):
        return self._text

    def sizeHint(self):
        fm = QtGui.QFontMetrics(metrics.font("caption", strong=True))
        return QtCore.QSize(fm.horizontalAdvance(self._text) + 2 * px(tokens.S), fm.height() + px(tokens.XS))

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setBrush(qcolor(self._color))
        p.drawRoundedRect(QtCore.QRectF(self.rect()), px(tokens.XS), px(tokens.XS))
        p.setFont(metrics.font("caption", strong=True))
        p.setPen(qcolor(tokens.ON_ACCENT))
        p.drawText(QtCore.QRectF(self.rect()), Qt.AlignCenter, self._text)
        p.end()


# --------------------------------------------------------------------------------------------------
# Satellite mark of the orbits
# --------------------------------------------------------------------------------------------------

class OrbitMark(QtWidgets.QWidget):
    """A small satellite for the orbits that exist for the flight day: plain = broadcast only, blue = rapid, green = final."""

    LEVELS = ("broadcast", "rapid", "final")

    @staticmethod
    def colors():
        return {"broadcast": tokens.TEXT, "rapid": tokens.INFO, "final": tokens.ACCENT}          # read when painting (skin switch)

    def __init__(self, parent=None):
        super(OrbitMark, self).__init__(parent)
        self._level = "broadcast"
        self.setFixedSize(px(tokens.ICON_L + tokens.XS), px(tokens.ICON_L + tokens.XS))

    def level(self):
        return self._level

    def set_state(self, level, tip):
        self._level = level if level in self.LEVELS else "broadcast"
        self.setToolTip(tip)
        self.setAccessibleName(tip)
        self.update()

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        d = min(self.width(), self.height())
        w = max(1.5, d / 15.0)
        u = (d / 2.0 - w) / 1.04                                                # the rotated panels stay inside the square
        color = qcolor(self.colors()[self._level])
        pen = QtGui.QPen(color)
        pen.setWidthF(w)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.translate(self.width() / 2.0, self.height() / 2.0)
        p.rotate(-40)
        for side in (-1, 1):                                                    # a solar panel on each side, a strut to the body
            p.drawRect(QtCore.QRectF(min(side * 0.42 * u, side * 1.0 * u), -0.3 * u, 0.58 * u, 0.6 * u))
            p.drawLine(QtCore.QPointF(side * 0.71 * u, -0.3 * u), QtCore.QPointF(side * 0.71 * u, 0.3 * u))
        p.setBrush(color)
        p.drawRoundedRect(QtCore.QRectF(-0.22 * u, -0.3 * u, 0.44 * u, 0.6 * u), 0.08 * u, 0.08 * u)
        p.setBrush(Qt.NoBrush)
        p.drawLine(QtCore.QPointF(0, -0.3 * u), QtCore.QPointF(0, -0.62 * u))                # antenna
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(QtCore.QPointF(0, -0.66 * u), w * 0.9, w * 0.9)
        p.end()


# --------------------------------------------------------------------------------------------------
# Networks of base stations
# --------------------------------------------------------------------------------------------------

def _swatch(color):
    side = px(tokens.ICON - 4)
    pixmap = QtGui.QPixmap(side, side)
    pixmap.fill(Qt.transparent)
    p = QtGui.QPainter(pixmap)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(qcolor(color))
    p.drawRoundedRect(QtCore.QRectF(1, 1, side - 2, side - 2), px(tokens.XS), px(tokens.XS))
    p.end()
    return QtGui.QIcon(pixmap)


class NetworkFilter(Button):
    """A drop-down with one tick per network of base stations and the number of its stations inside the search radius.

    Nothing chosen yet means automatic: the networks that have stations around the site are on. `chosen()` is None then, else the ticked
    ids. `changed` is sent when the user ticks something or asks for the automatic choice (not when the window sets the state)."""

    changed = QtCore.Signal()

    def __init__(self, providers, parent=None):
        super(NetworkFilter, self).__init__("", "secondary", parent=parent)
        self._providers = list(providers)
        self._auto = True
        self._menu = QtWidgets.QMenu(self)
        self._actions = {}
        for provider in self._providers:
            action = self._menu.addAction(_swatch(provider.color), provider.short)
            action.setCheckable(True)
            action.triggered.connect(self._picked)
            self._actions[provider.id] = action
        self._menu.addSeparator()
        self._auto_action = self._menu.addAction(i18n.t("aeroppk.base.networks_auto"))
        self._auto_action.setCheckable(True)
        self._auto_action.triggered.connect(self._reset)
        self.setMenu(self._menu)
        self.set_state({}, [], True)

    def set_state(self, counts, enabled, auto):
        """Show which networks are on (`enabled` ids), how many stations each has around the site (`counts`) and whether it is automatic."""
        self._auto = bool(auto)
        for provider in self._providers:
            action = self._actions[provider.id]
            action.setChecked(provider.id in enabled)
            count = counts.get(provider.id)
            action.setText("%s  %s%s" % (provider.short, provider.frame_short, "" if count is None else "   %d" % count))
        self._auto_action.setChecked(self._auto)
        self.setText(i18n.t("aeroppk.base.networks_button", n=len([p for p in self._providers if p.id in enabled])))

    def chosen(self):
        """None while automatic, else the ids that are ticked."""
        if self._auto:
            return None
        return [pid for pid, action in self._actions.items() if action.isChecked()]

    def _picked(self, _checked=False):
        self._auto = False
        self._auto_action.setChecked(False)
        self.setText(i18n.t("aeroppk.base.networks_button", n=len(self.chosen())))
        self.changed.emit()

    def _reset(self, _checked=False):
        self._auto = True
        self.changed.emit()


# --------------------------------------------------------------------------------------------------
# A figure of the results and a list of facts
# --------------------------------------------------------------------------------------------------

def tile(caption, info=""):
    """A figure of the results: a card with the value (large) and its caption (small), an "i" when there is something to explain.

    Returns (frame, value label, caption label, info button or None)."""
    frame = QtWidgets.QFrame()
    frame.setProperty("ui", "card")
    frame.setAttribute(Qt.WA_StyledBackground, True)
    box = uikit.hbox((tokens.M, tokens.XS + 2, tokens.S, tokens.XS + 2), tokens.S, parent=frame)
    value = uikit.label("-", "lead", wrap=False)
    box.addWidget(value)
    name = uikit.label(caption, "small", "muted", wrap=False)
    box.addWidget(name)
    button = None
    if info:
        button = uikit.InfoButton(info)
        box.addWidget(button)
    return frame, value, name, button


class KeyValues(QtWidgets.QWidget):
    """A plain list of "label | value" rows (`add_row`, `set_text`, `set_tone`, `values`): the facts of a base."""

    def __init__(self, parent=None):
        super(KeyValues, self).__init__(parent)
        self.grid = QtWidgets.QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(px(tokens.S))
        self.grid.setVerticalSpacing(px(tokens.XS // 2))
        self.grid.setColumnStretch(1, 1)
        self.values = {}
        self._row = 0

    def add_row(self, label, value="-", key=None):
        name = uikit.label(label, "caption", "muted", wrap=False)                  # the label is quiet ...
        widget = uikit.ElidedLabel(str(value), "strong", fit=True)                # ... the value is what the user came for
        self.grid.addWidget(name, self._row, 0, Qt.AlignTop)
        self.grid.addWidget(widget, self._row, 1)
        self.values[key or label] = widget
        self._row += 1
        return widget

    def set_text(self, key, text):
        widget = self.values.get(key)
        if widget is not None:
            widget.setFullText(str(text))

    def set_tone(self, key, tone):
        widget = self.values.get(key)
        if widget is not None:
            uikit.set_tone(widget, tone)
