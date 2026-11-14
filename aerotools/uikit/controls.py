"""Controls of the kit: labels, buttons, the "i", fields, choices, toggle, segmented, path.

Every control has one height (`tokens.CONTROL_H`), one radius, one focus ring and the same disabled look. Colours and sizes come from
`tokens` / `metrics`; the look of QSS-drawn widgets comes from `style.py` through dynamic properties.
"""

import os
import re

from .. import i18n
from ..qt import QtCore, QtGui, QtWidgets
from . import fonts, icons, metrics, style, tokens
from .metrics import px

Qt = QtCore.Qt


def _qcolor(value):
    return QtGui.QColor(value)


def _screen_rect(widget=None):
    try:
        screen = widget.screen() if widget is not None else None
        screen = screen or QtWidgets.QApplication.primaryScreen()
        return screen.availableGeometry()
    except Exception:
        return QtCore.QRect(0, 0, 1920, 1040)


# --------------------------------------------------------------------------------------------------
# Focus ring: drawn over the control, so it never changes a size
# --------------------------------------------------------------------------------------------------

class _Ring(object):
    """Mixin for a widget: after it has painted itself, draws the 2 px focus ring when it holds the keyboard focus.

    A mouse click on a button gives focus without a ring (only Tab and shortcuts show it); a text field always shows it."""
    ring_always = False
    ring_role = "ring"                 # which ring colour: ring, on_accent (a green button), on_danger (a red one); read at paint time
    ring_radius = None                 # None: the control radius of the skin; "button": its button radius; or a number of design px
    force_ring = False                 # a gallery or screenshot may show the ring without the focus
    _keyboard = False                  # the focus came from Tab or a shortcut: only then buttons show the ring

    def focusInEvent(self, event):
        reason = event.reason()
        if reason in (Qt.TabFocusReason, Qt.BacktabFocusReason, Qt.ShortcutFocusReason):
            self._keyboard = True
        elif reason != Qt.ActiveWindowFocusReason and reason != Qt.PopupFocusReason:
            self._keyboard = False
        super(_Ring, self).focusInEvent(event)
        self.update()

    def focusOutEvent(self, event):
        super(_Ring, self).focusOutEvent(event)
        self.update()

    def paintEvent(self, event):
        super(_Ring, self).paintEvent(event)
        if self.force_ring or (self.hasFocus() and (self.ring_always or self._keyboard)):
            self._draw_ring()

    @property
    def ring_color(self):
        return getattr(tokens, {"on_primary": "ON_PRIMARY", "on_accent": "RING_ON_ACCENT", "on_danger": "RING_ON_DANGER"}.get(self.ring_role, "RING"))

    def _ring_radius(self):
        if self.ring_radius is None:
            return tokens.RADIUS
        return tokens.BUTTON_RADIUS if self.ring_radius == "button" else self.ring_radius

    def _draw_ring(self):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        width = float(px(tokens.RING_W))
        pen = QtGui.QPen(_qcolor(self.ring_color))
        pen.setWidthF(width)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        rect = QtCore.QRectF(self.rect()).adjusted(width / 2.0, width / 2.0, -width / 2.0, -width / 2.0)
        radius = min(px(self._ring_radius()) - width / 2.0, rect.height() / 2.0)
        p.drawRoundedRect(rect, max(0.0, radius), max(0.0, radius))
        p.end()


# --------------------------------------------------------------------------------------------------
# Labels
# --------------------------------------------------------------------------------------------------

_BREAK_AFTER = "/\\_-.:,;=&?#%+@"
_ZWSP = "​"


def breakable(text, run=18):
    """`text` with invisible break points inside every word longer than `run` characters (after path and name separators, and every `run`
    characters of an unbroken run), so a file name or a path wraps instead of running out of its box. `unbreak` takes them out again."""
    out = []
    for word in re.split(r"(\s+)", str(text)):
        if len(word) <= run or word.isspace():
            out.append(word)
            continue
        since = 0
        for ch in word:
            out.append(ch)
            since += 1
            if ch in _BREAK_AFTER or since >= run:
                out.append(_ZWSP)
                since = 0
    return "".join(out)


def unbreak(text):
    return str(text).replace(_ZWSP, "")


class WrapLabel(QtWidgets.QLabel):
    """A wrapped text with honest sizes: its minimum is ONE line (a window never has to be taller than needed because a label was
    measured at its narrowest width), its preferred size is the text on one line up to a cap, and `heightForWidth` is exact.

    `break_long=True` lets a file name or a path break anywhere (see `breakable`); `plain_text()` gives the text without break marks."""

    def __init__(self, text="", break_long=False, parent=None):
        super(WrapLabel, self).__init__(parent)
        self._break = bool(break_long)
        self._plain = ""
        self.setWordWrap(True)
        self.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Minimum)
        self.setMinimumWidth(px(24))
        self.setText(text)

    def setText(self, text):
        self._plain = unbreak(text)
        super(WrapLabel, self).setText(breakable(self._plain) if self._break else self._plain)
        self.updateGeometry()

    def plain_text(self):
        return self._plain

    def text(self):
        return self._plain

    def _width_for(self, text_width):
        return max(px(24), min(text_width, px(560)))

    def minimumSizeHint(self):
        return QtCore.QSize(px(24), self.fontMetrics().lineSpacing() + 2 * self.margin())

    def sizeHint(self):
        hint = self.fontMetrics().horizontalAdvance(super(WrapLabel, self).text())
        width = self._width_for(hint + 2 * self.margin() + self.indent() if self.indent() > 0 else hint + 2 * self.margin())
        return QtCore.QSize(width, max(self.heightForWidth(width), self.minimumSizeHint().height()))


def label(text="", type="body", tone=None, wrap=True, selectable=False, break_long=False):
    """A text. `type`: caption, small, body, lead, title, display, strong, field. `tone`: muted, faint, accent, success, warning, danger, info.
    A wrapped one is a `WrapLabel`; `break_long=True` lets it break a file name or a path anywhere."""
    widget = WrapLabel(text, break_long) if wrap else QtWidgets.QLabel(text)
    widget.setProperty("type", type)
    if tone:
        widget.setProperty("tone", tone)
    if selectable:
        widget.setTextInteractionFlags(Qt.TextSelectableByMouse)
    if type in ("field", "lead"):
        metrics.apply_case(widget, "label")
    elif type in ("title", "display"):
        metrics.apply_case(widget, "title")
    return widget


def set_tone(widget, tone):
    """Change the tone of a label (None = normal)."""
    style.set_prop(widget, "tone", tone or "")


class ElidedLabel(QtWidgets.QLabel):
    """One line cut with an ellipsis when it does not fit; it never widens its window. The whole text is the tooltip, only when cut."""

    def __init__(self, text="", type="body", tone=None, mode="right", fit=False):
        super(ElidedLabel, self).__init__()
        self.setProperty("type", type)
        if tone:
            self.setProperty("tone", tone)
        self._mode = Qt.ElideMiddle if mode == "middle" else Qt.ElideRight
        self._full = ""
        self.setSizePolicy(QtWidgets.QSizePolicy.Preferred if fit else QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        self.setFullText(text)

    def setFullText(self, text):
        self._full = str(text)
        self._elide()
        self.updateGeometry()

    def fullText(self):
        return self._full

    def minimumSizeHint(self):
        return QtCore.QSize(px(24), self.fontMetrics().height() + 2)

    def sizeHint(self):
        return QtCore.QSize(min(self.fontMetrics().horizontalAdvance(self._full) + 2, px(480)), self.fontMetrics().height() + 2)

    def resizeEvent(self, event):
        super(ElidedLabel, self).resizeEvent(event)
        self._elide()

    def changeEvent(self, event):
        super(ElidedLabel, self).changeEvent(event)
        if event.type() in (QtCore.QEvent.FontChange, QtCore.QEvent.StyleChange):
            self._elide()

    def _elide(self):
        width = self.width()
        shown = self.fontMetrics().elidedText(self._full, self._mode, max(px(8), width)) if width > 1 else self._full
        if shown != self.text():
            self.setText(shown)
        self.setToolTip(self._full if shown != self._full else "")


# --------------------------------------------------------------------------------------------------
# Buttons
# --------------------------------------------------------------------------------------------------

_RING_BY_ROLE = {"primary": "on_primary", "danger": "on_danger"}


class Button(_Ring, QtWidgets.QPushButton):
    """A button. `role`: primary (one per view, right-most), secondary, quiet, danger. Height is the one button height."""
    ring_radius = "button"

    def __init__(self, text="", role="secondary", icon=None, handler=None, parent=None):
        super(Button, self).__init__(text, parent)
        self.setProperty("role", role)
        self.ring_role = _RING_BY_ROLE.get(role, "ring")
        self.setFixedHeight(px(tokens.BUTTON_H))
        metrics.apply_case(self, "button")
        self.setCursor(Qt.PointingHandCursor)
        self.setAutoDefault(False)
        self.setFocusPolicy(Qt.StrongFocus)
        if role in ("primary", "secondary", "danger"):
            self.setMinimumWidth(px(88))
        self._icon_name = icon
        if icon:
            self.refresh_skin()
        if handler is not None:
            self.clicked.connect(lambda *_args: handler())

    def setFixedWidth(self, width):
        self._fixed_width = width
        super(Button, self).setFixedWidth(width)

    def event(self, event):
        """A fixed width is kept when the host application's own style sheet polishes the button: Metashape's gives every QPushButton a
        minimum width of about 65 px, which pushed the small icon buttons of the headers apart and squeezed the title out (seen live)."""
        result = super(Button, self).event(event)
        fixed = getattr(self, "_fixed_width", None)
        if fixed is not None and event.type() in (QtCore.QEvent.Polish, QtCore.QEvent.StyleChange, QtCore.QEvent.Show)                 and self.minimumWidth() != fixed:
            super(Button, self).setFixedWidth(fixed)
        return result

    def refresh_skin(self):
        """Draw the icon again in the colours of the active skin."""
        if getattr(self, "_icon_name", None):
            role = self.property("role")
            color = tokens.ON_PRIMARY if role == "primary" else (tokens.ON_DANGER if role == "danger" else tokens.BUTTON_TEXT)
            self.setIcon(icons.icon(self._icon_name, color, tokens.TEXT_DISABLED, px(tokens.ICON)))
            self.setIconSize(QtCore.QSize(px(tokens.ICON), px(tokens.ICON)))

    def role(self):
        return self.property("role")

    def set_role(self, role):
        self.ring_role = _RING_BY_ROLE.get(role, "ring")
        style.set_prop(self, "role", role)


def button(text_key, handler=None, role="secondary", icon=None, **fields):
    """A `Button` whose text is an i18n key."""
    return Button(i18n.t(text_key, **fields) if fields else i18n.t(text_key), role, icon, handler)


class IconButton(_Ring, QtWidgets.QAbstractButton):
    """A square quiet button that shows only an icon (a vector one); `tip` is its tooltip and accessible name."""

    def __init__(self, name, tip="", handler=None, parent=None):
        super(IconButton, self).__init__(parent)
        self._name = name
        self.setFixedSize(px(tokens.CONTROL_H), px(tokens.CONTROL_H))
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setToolTip(tip)
        self.setAccessibleName(tip)
        if handler is not None:
            self.clicked.connect(lambda *_args: handler())

    def set_icon_name(self, name):
        self._name = name
        self.update()

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        if self.isEnabled() and (self.underMouse() or self.isDown()):
            p.setPen(Qt.NoPen)
            p.setBrush(_qcolor(tokens.SURFACE_PRESSED if self.isDown() else tokens.SURFACE_HOVER))
            p.drawRoundedRect(QtCore.QRectF(self.rect()), px(tokens.RADIUS), px(tokens.RADIUS))
        color = tokens.TEXT_DISABLED if not self.isEnabled() else (tokens.TEXT if self.underMouse() else tokens.TEXT_MUTED)
        side = px(tokens.ICON)
        rect = QtCore.QRectF((self.width() - side) / 2.0, (self.height() - side) / 2.0, side, side)
        icons.paint(p, self._name, rect, color)
        p.end()
        if self.force_ring or (self.hasFocus() and self._keyboard):
            self._draw_ring()


class ThemeButton(_Ring, QtWidgets.QAbstractButton):
    """The small round button of every window header: one click cycles Dark, Medium, Light. A filled disc is dark, a half-filled disc
    medium, a ring light. It restyles every open window at once and saves the choice (`style.switch_skin`)."""
    ring_radius = tokens.PILL

    def __init__(self, parent=None):
        super(ThemeButton, self).__init__(parent)
        self.setFixedSize(px(28), px(28))
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.clicked.connect(lambda *_a: style.switch_skin())
        self.refresh_skin()

    def refresh_skin(self):
        name = tokens.active_skin()
        label = {"dark": i18n.t("ui.theme_dark"), "medium": i18n.t("ui.theme_medium"), "light": i18n.t("ui.theme_light")}.get(name, name)
        self.setToolTip(i18n.t("ui.theme", name=label))
        self.setAccessibleName(i18n.t("ui.theme", name=label))

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        hot = self.underMouse() or self.hasFocus()
        rect = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QtGui.QPen(_qcolor(tokens.TEXT if hot else tokens.BUTTON_BORDER if tokens.BUTTON_BORDER != "transparent" else tokens.BORDER_STRONG), 1))
        p.setBrush(_qcolor(tokens.SURFACE_HOVER) if self.underMouse() else Qt.NoBrush)
        p.drawEllipse(rect)
        disc = QtCore.QRectF(self.rect()).adjusted(0, 0, 0, 0)
        side = px(14)
        disc = QtCore.QRectF((self.width() - side) / 2.0, (self.height() - side) / 2.0, side, side)
        ink = _qcolor(tokens.TEXT)
        p.setPen(QtGui.QPen(ink, 1.5))
        p.setBrush(Qt.NoBrush)
        name = tokens.active_skin()
        if name == "dark":
            p.setBrush(ink)
            p.drawEllipse(disc.adjusted(0.75, 0.75, -0.75, -0.75))
        elif name == "medium":
            inner = disc.adjusted(0.75, 0.75, -0.75, -0.75)
            p.drawEllipse(inner)
            path = QtGui.QPainterPath()
            path.moveTo(inner.center().x(), inner.top())
            path.arcTo(inner, 90, 180)
            path.closeSubpath()
            p.setPen(Qt.NoPen)
            p.setBrush(ink)
            p.drawPath(path)
        else:
            p.drawEllipse(disc.adjusted(0.75, 0.75, -0.75, -0.75))
        p.end()
        if self.force_ring or (self.hasFocus() and self._keyboard):
            self._draw_ring()


class FontButton(_Ring, QtWidgets.QAbstractButton):
    """The second small round button of every window header, right next to the theme button: "Aa". One click cycles the three fonts
    (System, Brand, Large), restyles every open window at once and saves the choice (`style.switch_font`)."""
    ring_radius = tokens.PILL

    def __init__(self, parent=None):
        super(FontButton, self).__init__(parent)
        self.setFixedSize(px(28), px(28))
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.clicked.connect(lambda *_a: style.switch_font())
        self.refresh_skin()

    def refresh_skin(self):
        name = font_label(tokens.active_font())
        self.setToolTip(i18n.t("ui.font", name=name))
        self.setAccessibleName(i18n.t("ui.font", name=name))

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        hot = self.underMouse() or self.hasFocus()
        rect = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QtGui.QPen(_qcolor(tokens.TEXT if hot else tokens.BUTTON_BORDER if tokens.BUTTON_BORDER != "transparent" else tokens.BORDER_STRONG), 1))
        p.setBrush(_qcolor(tokens.SURFACE_HOVER) if self.underMouse() else Qt.NoBrush)
        p.drawEllipse(rect)
        p.setPen(_qcolor(tokens.TEXT))
        big, small = metrics.font("body", strong=True), metrics.font("small", strong=True)
        big.setPixelSize(px(15))
        small.setPixelSize(px(12))
        fm_big, fm_small = QtGui.QFontMetrics(big), QtGui.QFontMetrics(small)
        total = fm_big.horizontalAdvance("A") + fm_small.horizontalAdvance("a")
        x = (self.width() - total) / 2.0
        base = (self.height() + fm_big.ascent() - fm_big.descent()) / 2.0
        p.setFont(big)
        p.drawText(QtCore.QPointF(x, base), "A")
        p.setFont(small)
        p.drawText(QtCore.QPointF(x + fm_big.horizontalAdvance("A"), base), "a")
        p.end()
        if self.force_ring or (self.hasFocus() and self._keyboard):
            self._draw_ring()


def font_label(name):
    """The words the UI shows for a font name (System, Brand, Large)."""
    key = "ui.font_" + str(name)
    text = i18n.t(key)
    return text if text != key else str(name)


def theme_label(name):
    key = "ui.theme_" + str(name)
    text = i18n.t(key)
    return text if text != key else str(name)


# --------------------------------------------------------------------------------------------------
# The "i": all explanations live behind it
# --------------------------------------------------------------------------------------------------

class Popover(QtWidgets.QFrame):
    """The styled bubble behind an `InfoButton`: wrapped text, at most 320 px wide, never off screen. One at a time."""
    _shared = None

    def __init__(self):
        super(Popover, self).__init__(None, Qt.ToolTip | Qt.FramelessWindowHint)
        self.setObjectName("uikitPopover")
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._label = QtWidgets.QLabel()
        self._label.setWordWrap(True)
        self._label.setTextInteractionFlags(Qt.NoTextInteraction)
        lay = QtWidgets.QVBoxLayout(self)
        pad = px(tokens.M)
        lay.setContentsMargins(pad, px(tokens.S), pad, px(tokens.S))
        lay.addWidget(self._label)
        self.owner = None

    @classmethod
    def shared(cls):
        if cls._shared is None:
            cls._shared = cls()
        return cls._shared

    def show_for(self, anchor, text):
        self.owner = anchor
        self.setStyleSheet(style.popover_stylesheet())
        width = min(px(tokens.POPOVER_W), max(px(160), _screen_rect(anchor).width() // 3))
        self._label.setText(text)
        self._label.setFixedWidth(width)
        self.layout().invalidate()
        self.adjustSize()
        area = _screen_rect(anchor)
        spot = anchor.mapToGlobal(QtCore.QPoint(anchor.width() // 2 - px(12), anchor.height() + px(tokens.XS)))
        x = max(area.left() + px(4), min(spot.x(), area.right() - self.width() - px(4)))
        y = spot.y()
        if y + self.height() > area.bottom():
            y = anchor.mapToGlobal(QtCore.QPoint(0, 0)).y() - self.height() - px(tokens.XS)
        self.move(x, max(area.top(), y))
        self.show()
        self.raise_()

    def hide_for(self, anchor):
        if self.owner is anchor:
            self.hide()
            self.owner = None


class InfoButton(_Ring, QtWidgets.QAbstractButton):
    """The small "i". Shows `text` in a styled bubble on hover, on click and on keyboard focus. Short text only."""
    ring_radius = tokens.INFO_SIZE // 2

    def __init__(self, text="", parent=None):
        super(InfoButton, self).__init__(parent)
        self._text = ""
        self._hover_timer = QtCore.QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(280)
        self._hover_timer.timeout.connect(self._hover_fire)
        self.setFixedSize(px(tokens.INFO_SIZE), px(tokens.INFO_SIZE))
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName(i18n.t("ui.info"))
        self.set_text(text)
        self.clicked.connect(self.show_tip)

    @classmethod
    def key(cls, text_key, **fields):
        """An info button whose text is the i18n key `text_key`."""
        return cls(i18n.t(text_key, **fields))

    def set_text(self, text):
        self._text = str(text or "")
        self.setAccessibleDescription(self._text)

    def text_tip(self):
        return self._text

    def show_tip(self):
        if self._text:
            Popover.shared().show_for(self, self._text)

    def hide_tip(self):
        Popover.shared().hide_for(self)

    def tip_visible(self):
        pop = Popover.shared()
        return pop.isVisible() and pop.owner is self

    def enterEvent(self, event):
        super(InfoButton, self).enterEvent(event)
        self._hover_timer.start()
        self.update()

    def _hover_fire(self):
        if self.underMouse():
            self.show_tip()

    def leaveEvent(self, event):
        super(InfoButton, self).leaveEvent(event)
        self._hover_timer.stop()
        if not self.hasFocus():
            self.hide_tip()
        self.update()

    def focusInEvent(self, event):
        super(InfoButton, self).focusInEvent(event)
        if event.reason() != Qt.MouseFocusReason:
            self.show_tip()

    def focusOutEvent(self, event):
        super(InfoButton, self).focusOutEvent(event)
        self.hide_tip()

    def hideEvent(self, event):
        super(InfoButton, self).hideEvent(event)
        self._hover_timer.stop()
        self.hide_tip()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and self.tip_visible():
            self.hide_tip()
            event.accept()
            return
        super(InfoButton, self).keyPressEvent(event)

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        hot = self.underMouse() or self.hasFocus() or self.tip_visible()
        side = px(tokens.ICON)
        rect = QtCore.QRectF((self.width() - side) / 2.0, (self.height() - side) / 2.0, side, side)
        icons.paint(p, "info", rect, tokens.TEXT_STRONG if hot else tokens.INFO_ICON, width=max(1.3, side / 12.0))
        p.end()
        if self.force_ring or (self.hasFocus() and self._keyboard):
            self._draw_ring()


# --------------------------------------------------------------------------------------------------
# Fields
# --------------------------------------------------------------------------------------------------

class TextField(_Ring, QtWidgets.QLineEdit):
    """A one-line text box. `set_invalid(True)` shows the error edge (the message belongs to the `FormGrid` row)."""
    ring_always = True

    def __init__(self, text="", placeholder="", parent=None):
        super(TextField, self).__init__(text, parent)
        self.setFixedHeight(px(tokens.CONTROL_H))
        self.setFocusPolicy(Qt.StrongFocus)
        if placeholder:
            self.setPlaceholderText(placeholder)
        self._error = ""

    def set_invalid(self, invalid=True):
        style.set_prop(self, "invalid", bool(invalid))

    def error_text(self):
        return self._error


def parse_number(text):
    """Float of a typed value (comma or point), or None."""
    try:
        value = float(str(text).replace(",", ".").replace(" ", "").strip())
    except ValueError:
        return None
    return value if value == value and abs(value) != float("inf") else None


class NumberField(TextField):
    """A number box: decimals, range, unit written inside, Up / Down step it, Enter or leaving the box commits.

    `valueChanged(float)` is sent with each valid new value. A typed value that is not a number or lies outside the range keeps the
    last valid value, shows the error edge, and `errorChanged(text)` carries the message ('' when fine); `FormGrid.add_row` shows it."""
    valueChanged = QtCore.Signal(float)
    errorChanged = QtCore.Signal(str)

    def __init__(self, value=0.0, minimum=None, maximum=None, decimals=2, step=1.0, unit="", width=tokens.FIELD_W, allow_empty=False):
        super(NumberField, self).__init__()
        self.minimum, self.maximum, self.decimals, self.step = minimum, maximum, int(decimals), float(step)
        self.unit, self.allow_empty = unit, allow_empty
        self._value = value
        self.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.setFixedWidth(px(width))
        self._unit_width = 0
        self.set_unit(unit)
        self.setText(self._format(value) if value is not None else "")
        self.textEdited.connect(self._live)
        self.editingFinished.connect(self.commit)

    def _format(self, value):
        return i18n.fmt_number(value, self.decimals) if self.decimals else str(int(round(value)))

    def set_unit(self, unit):
        self.unit = unit
        self._unit_width = (QtGui.QFontMetrics(metrics.font("body")).horizontalAdvance(unit) + px(tokens.S)) if unit else 0
        self.setTextMargins(0, 0, self._unit_width, 0)
        self.update()

    def value(self):
        return self._value

    def set_value(self, value, emit=False):
        self._value = None if value is None else self._clamped(float(value))
        self.setText("" if self._value is None else self._format(self._value))
        self._set_error("")
        if emit and self._value is not None:
            self.valueChanged.emit(self._value)

    def _clamped(self, value):
        value = round(float(value), self.decimals)             # a value never carries more decimals than the box declares
        if self.minimum is not None:
            value = max(self.minimum, value)
        if self.maximum is not None:
            value = min(self.maximum, value)
        return value

    def _problem(self, text):
        """'' when `text` is acceptable, else the message."""
        if not text.strip():
            return "" if self.allow_empty else i18n.t("ui.number_required")
        number = parse_number(text)
        if number is None:
            return i18n.t("ui.number_invalid")
        if (self.minimum is not None and number < self.minimum) or (self.maximum is not None and number > self.maximum):
            low = "" if self.minimum is None else self._format(self.minimum)
            high = "" if self.maximum is None else self._format(self.maximum)
            return i18n.t("ui.number_range", low=low, high=high)
        return ""

    def _set_error(self, message):
        if message != self._error:
            self._error = message
            self.set_invalid(bool(message))
            self.errorChanged.emit(message)

    def _live(self, text):
        self._set_error(self._problem(text))

    def commit(self):
        """Take the typed text: a valid number becomes the value, anything else leaves the value and keeps the error."""
        text = self.text()
        problem = self._problem(text)
        self._set_error(problem)
        if problem:
            return False
        if not text.strip():
            self._value = None
            return True
        number = self._clamped(parse_number(text))
        self.setText(self._format(number))
        if self._value is None or abs(number - self._value) > 1e-12:
            self._value = number
            self.valueChanged.emit(number)
        return True

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Up, Qt.Key_Down) and not self._problem(self.text()):
            sign = 1 if event.key() == Qt.Key_Up else -1
            current = parse_number(self.text())
            base = 0.0 if current is None else current
            factor = 10.0 if event.modifiers() & Qt.ShiftModifier else 1.0           # Shift: ten steps at once
            self.set_value(base + sign * self.step * factor, emit=True)
            self.selectAll()
            event.accept()
            return
        super(NumberField, self).keyPressEvent(event)

    def focusInEvent(self, event):
        super(NumberField, self).focusInEvent(event)
        if event.reason() in (Qt.TabFocusReason, Qt.BacktabFocusReason, Qt.ShortcutFocusReason):
            QtCore.QTimer.singleShot(0, self.selectAll)             # tabbing into a number selects it: typing replaces it

    def paintEvent(self, event):
        super(NumberField, self).paintEvent(event)
        if self.unit:
            p = QtGui.QPainter(self)
            p.setFont(metrics.font("body"))
            p.setPen(_qcolor(tokens.TEXT_DISABLED if not self.isEnabled() else tokens.TEXT_MUTED))
            area = self.rect().adjusted(0, 0, -px(tokens.S + 2), 0)
            p.drawText(area, Qt.AlignRight | Qt.AlignVCenter, self.unit)
            p.end()


class ChoiceBox(_Ring, QtWidgets.QComboBox):
    """A drop-down list, reachable by Tab. Items are (key, text); `value()` is the key. `changed(key)` is sent when the user picks."""
    changed = QtCore.Signal(object)
    ring_always = False

    def __init__(self, choices=(), parent=None):
        super(ChoiceBox, self).__init__(parent)
        self.setFixedHeight(px(tokens.CONTROL_H))
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(12)
        self.setMaxVisibleItems(12)
        self.set_choices(choices)
        self.activated.connect(lambda _i: self.changed.emit(self.value()))

    def set_choices(self, choices, value=None):
        self.blockSignals(True)
        self.clear()
        for key, text in choices:
            self.addItem(text, key)
        self.blockSignals(False)
        if value is not None:
            self.set_value(value)

    def value(self):
        return self.currentData()

    def set_value(self, key):
        index = self.findData(key)
        if index >= 0:
            self.setCurrentIndex(index)

    def set_invalid(self, invalid=True):
        style.set_prop(self, "invalid", bool(invalid))

    def showPopup(self):
        fm = self.fontMetrics()
        widest = max([fm.horizontalAdvance(self.itemText(i)) for i in range(self.count())] or [0])
        self.view().setMinimumWidth(min(max(self.width(), widest + px(40)), _screen_rect(self).width() // 2))
        super(ChoiceBox, self).showPopup()

    def wheelEvent(self, event):
        if self.hasFocus():
            super(ChoiceBox, self).wheelEvent(event)
        else:
            event.ignore()                      # scrolling a page must not change a value

    def paintEvent(self, event):
        super(ChoiceBox, self).paintEvent(event)
        p = QtGui.QPainter(self)
        side = px(tokens.ICON)
        rect = QtCore.QRectF(self.width() - px(28) + (px(28) - side) / 2.0, (self.height() - side) / 2.0, side, side)
        icons.paint(p, "chevron_down", rect, tokens.TEXT_DISABLED if not self.isEnabled() else tokens.TEXT_MUTED)
        p.end()


class Toggle(_Ring, QtWidgets.QAbstractButton):
    """An on / off switch with its text on the right. Same API as a checkbox (`isChecked`, `setChecked`, `toggled`)."""
    ring_radius = tokens.PILL

    def __init__(self, text="", checked=False, parent=None):
        super(Toggle, self).__init__(parent)
        self.setText(text)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)

    def _track(self):
        return QtCore.QSize(px(tokens.TOGGLE_W), px(tokens.TOGGLE_H))

    def sizeHint(self):
        fm = QtGui.QFontMetrics(metrics.font("body"))
        text = self.text()
        gap = px(tokens.S + 2) if text else 0
        return QtCore.QSize(self._track().width() + gap + (fm.horizontalAdvance(text) if text else 0) + 2, max(px(tokens.CONTROL_H), fm.height()))

    def minimumSizeHint(self):
        return QtCore.QSize(self._track().width(), px(tokens.CONTROL_H))

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        track = self._track()
        top = (self.height() - track.height()) / 2.0
        rect = QtCore.QRectF(1, top, track.width(), track.height())
        on, enabled = self.isChecked(), self.isEnabled()
        if on:
            fill = tokens.ACCENT if enabled else tokens.SURFACE_PRESSED
            if enabled and self.underMouse():
                fill = tokens.ACCENT_HOVER
        else:
            fill = tokens.SURFACE_HOVER if self.underMouse() and enabled else tokens.SURFACE
        p.setPen(QtGui.QPen(_qcolor(tokens.ACCENT if on and enabled else tokens.BORDER_STRONG), 1))
        p.setBrush(_qcolor(fill))
        p.drawRoundedRect(rect, rect.height() / 2.0, rect.height() / 2.0)
        knob = rect.height() - 2 * px(3)
        x = rect.right() - px(3) - knob if on else rect.left() + px(3)
        p.setPen(Qt.NoPen)
        p.setBrush(_qcolor(tokens.ON_ACCENT if on and enabled else (tokens.TEXT if enabled else tokens.TEXT_DISABLED)))
        p.drawEllipse(QtCore.QRectF(x, rect.top() + px(3), knob, knob))
        if self.text():
            p.setFont(metrics.font("body"))
            p.setPen(_qcolor(tokens.TEXT if enabled else tokens.TEXT_DISABLED))
            left = track.width() + px(tokens.S + 2) + 1
            fm = p.fontMetrics()
            text = fm.elidedText(self.text(), Qt.ElideRight, max(px(8), self.width() - left))
            p.drawText(QtCore.QRectF(left, 0, self.width() - left, self.height()), Qt.AlignVCenter | Qt.AlignLeft, text)
        p.end()
        if self.force_ring or (self.hasFocus() and self._keyboard):
            self._draw_ring()

    def _draw_ring(self):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        width = float(px(tokens.RING_W))
        pen = QtGui.QPen(_qcolor(tokens.RING))
        pen.setWidthF(width)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        track = self._track()
        top = (self.height() - track.height()) / 2.0
        rect = QtCore.QRectF(1, top, track.width(), track.height()).adjusted(-width, -width, width, width)
        p.drawRoundedRect(rect, rect.height() / 2.0, rect.height() / 2.0)
        p.end()


class Segmented(_Ring, QtWidgets.QWidget):
    """A choice between a few options side by side (two to five short words). One Tab stop; Left / Right change the choice.

    `options` = [(key, text)]. `changed(key)` is sent when the user changes it."""
    changed = QtCore.Signal(object)

    def __init__(self, options, value=None, parent=None):
        super(Segmented, self).__init__(parent)
        self._options = list(options)
        self._value = value if value is not None else (self._options[0][0] if self._options else None)
        self._hover = -1
        self._disabled_keys = set()
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(px(tokens.CONTROL_H))
        self.setSizePolicy(QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Fixed)

    def _widths(self):
        fm = QtGui.QFontMetrics(metrics.font("body", strong=True))
        base = [fm.horizontalAdvance(text) + 2 * px(tokens.L) for _k, text in self._options]
        floor = max([px(72)] + [0])
        widest = max(base + [floor])
        return [widest for _ in base]

    def sizeHint(self):
        return QtCore.QSize(sum(self._widths()) + 2, px(tokens.CONTROL_H))

    def minimumSizeHint(self):
        fm = QtGui.QFontMetrics(metrics.font("body", strong=True))
        return QtCore.QSize(sum(fm.horizontalAdvance(t) + px(tokens.M) for _k, t in self._options) + 2, px(tokens.CONTROL_H))

    def value(self):
        return self._value

    def set_value(self, key):
        if key != self._value and any(k == key for k, _t in self._options):
            self._value = key
            self.update()

    def set_option_enabled(self, key, enabled):
        (self._disabled_keys.discard if enabled else self._disabled_keys.add)(key)
        self.update()

    def _rects(self):
        count = len(self._options)
        width = self.width() - 2
        out, x = [], 1.0
        for index in range(count):
            right = 1.0 + width * (index + 1) / count
            out.append(QtCore.QRectF(x, 1, right - x, self.height() - 2))
            x = right
        return out

    def _pick(self, index):
        key = self._options[index][0]
        if key in self._disabled_keys or key == self._value:
            return
        self._value = key
        self.update()
        self.changed.emit(key)

    def _index_at(self, pos):
        for index, rect in enumerate(self._rects()):
            if rect.contains(QtCore.QPointF(pos)):
                return index
        return -1

    def mouseMoveEvent(self, event):
        index = self._index_at(event.pos())
        if index != self._hover:
            self._hover = index
            self.update()

    def leaveEvent(self, event):
        self._hover = -1
        self.update()

    def mousePressEvent(self, event):
        index = self._index_at(event.pos())
        if index >= 0 and self.isEnabled():
            self._pick(index)
            self.setFocus(Qt.MouseFocusReason)

    def keyPressEvent(self, event):
        keys = [k for k, _t in self._options]
        if event.key() in (Qt.Key_Left, Qt.Key_Right) and self._value in keys:
            step = -1 if event.key() == Qt.Key_Left else 1
            index = keys.index(self._value)
            for _ in range(len(keys)):
                index += step
                if not 0 <= index < len(keys):
                    break
                if keys[index] not in self._disabled_keys:
                    self._pick(index)
                    break
            event.accept()
            return
        super(Segmented, self).keyPressEvent(event)

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        radius = float(px(tokens.RADIUS))
        outer = QtCore.QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        p.setPen(QtGui.QPen(_qcolor(tokens.BORDER if self.isEnabled() else tokens.BORDER), 1))
        p.setBrush(_qcolor(tokens.SURFACE if self.isEnabled() else tokens.BG))
        p.drawRoundedRect(outer, radius, radius)
        rects = self._rects()
        for index, (key, text) in enumerate(self._options):
            rect = rects[index]
            on = key == self._value
            off = (not self.isEnabled()) or key in self._disabled_keys
            if on:
                p.setPen(QtGui.QPen(_qcolor(tokens.ACCENT if not off else tokens.BORDER_STRONG), 1))
                p.setBrush(_qcolor(tokens.ACCENT_SOFT if not off else tokens.SURFACE_PRESSED))
                p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius - 1, radius - 1)
            elif index == self._hover and not off:
                p.setPen(Qt.NoPen)
                p.setBrush(_qcolor(tokens.SURFACE_HOVER))
                p.drawRoundedRect(rect.adjusted(1, 1, -1, -1), radius - 1, radius - 1)
            p.setFont(metrics.font("body", strong=on))
            p.setPen(_qcolor(tokens.TEXT_DISABLED if off else (tokens.TEXT_STRONG if on else tokens.TEXT_MUTED)))
            shown = p.fontMetrics().elidedText(text, Qt.ElideRight, int(rect.width() - px(tokens.S)))
            p.drawText(rect, Qt.AlignCenter, shown)
        p.end()
        if self.force_ring or (self.hasFocus() and self._keyboard):
            self._draw_ring()


class PathField(QtWidgets.QWidget):
    """A folder or file chooser: the path shown cut in the middle, a Browse button. `changed(path)` is sent on a new choice.

    `mode`: 'dir', 'file' or 'save'. Override `pick()` (tests do) to avoid the native dialog."""
    changed = QtCore.Signal(str)

    def __init__(self, mode="dir", path="", placeholder="", title="", name_filter="", parent=None):
        super(PathField, self).__init__(parent)
        self.mode, self._path, self._title, self._filter = mode, "", title, name_filter
        self._placeholder = placeholder
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(px(tokens.S))
        self.field = QtWidgets.QFrame()
        self.field.setProperty("ui", "field")
        self.field.setFixedHeight(px(tokens.CONTROL_H))
        inner = QtWidgets.QHBoxLayout(self.field)
        inner.setContentsMargins(px(tokens.S + 2), 0, px(tokens.S + 2), 0)
        inner.setSpacing(px(tokens.S))
        self.text = ElidedLabel("", "body", mode="middle")
        inner.addWidget(self.text, 1)
        lay.addWidget(self.field, 1)
        self.browse = Button(i18n.t("ui.browse"), "secondary", handler=self._browse)
        lay.addWidget(self.browse)
        self.setFocusProxy(self.browse)
        self.set_path(path)

    def path(self):
        return self._path

    def set_path(self, path):
        self._path = str(path or "")
        self.text.setFullText(self._path or self._placeholder)
        set_tone(self.text, None if self._path else "faint")
        self.setToolTip("")

    def set_invalid(self, invalid=True):
        style.set_prop(self.field, "invalid", bool(invalid))

    def pick(self):
        """Ask for a path (the tests replace this method). Returns it, or ''."""
        start = self._path if os.path.exists(self._path) else ""
        if self.mode == "dir":
            return QtWidgets.QFileDialog.getExistingDirectory(self, self._title or i18n.t("ui.choose_folder"), start) or ""
        if self.mode == "save":
            return QtWidgets.QFileDialog.getSaveFileName(self, self._title or i18n.t("ui.choose_file"), start, self._filter)[0] or ""
        return QtWidgets.QFileDialog.getOpenFileName(self, self._title or i18n.t("ui.choose_file"), start, self._filter)[0] or ""

    def _browse(self):
        chosen = self.pick()
        if chosen and chosen != self._path:
            self.set_path(chosen)
            self.changed.emit(chosen)

    def mousePressEvent(self, event):
        if self.field.geometry().contains(event.pos()) and self.isEnabled():
            self._browse()
        else:
            super(PathField, self).mousePressEvent(event)


class ThemeChoice(Segmented):
    """The colour theme as a Segmented control (Dark | Medium | Light), bound to `style.switch_skin`. It follows the header button and the
    other controls: `restyle_all` calls `refresh_skin`, so a change made anywhere shows here."""

    def __init__(self, parent=None):
        super(ThemeChoice, self).__init__([(n, theme_label(n)) for n in tokens.skin_names()], tokens.active_skin(), parent)
        self.changed.connect(lambda key: style.switch_skin(key))

    def refresh_skin(self):
        self.set_value(tokens.active_skin())


class FontChoice(Segmented):
    """The font as a Segmented control (System | Brand | Large), bound to `style.switch_font` and kept in sync like `ThemeChoice`."""

    def __init__(self, parent=None):
        super(FontChoice, self).__init__([(n, font_label(n)) for n in fonts.names()], tokens.active_font(), parent)
        self.changed.connect(lambda key: style.switch_font(key))

    def refresh_skin(self):
        self.set_value(tokens.active_font())
        self.updateGeometry()


class AppearanceBar(QtWidgets.QWidget):
    """"Theme [Dark|Medium|Light]   Font [System|Brand|Large]" on one line: the same two choices as the header buttons."""

    def __init__(self, parent=None, theme=True, font=True):
        super(AppearanceBar, self).__init__(parent)
        from .blocks import hbox
        lay = hbox(spacing=tokens.S, parent=self)
        self.theme = ThemeChoice() if theme else None
        self.font = FontChoice() if font else None
        for caption, control in ((i18n.t("ui.appearance_theme"), self.theme), (i18n.t("ui.appearance_font"), self.font)):
            if control is None:
                continue
            if lay.count():
                lay.addSpacing(px(tokens.L))
            lay.addWidget(label(caption, "field", wrap=False), 0, Qt.AlignVCenter)
            lay.addWidget(control, 0, Qt.AlignVCenter)
        lay.addStretch(1)
