"""Building blocks of a screen: Divider, Glyph, Section, Disclosure, FormGrid, Banner, EmptyState.

Layout rule of the kit: 4 px scale, 16 px inside a window, 24 px between sections, 12 px between form rows, 8 px inside a row.
"""

from .. import i18n
from ..qt import QtCore, QtGui, QtWidgets
from . import icons, metrics, style, tokens
from .controls import Button, ElidedLabel, IconButton, InfoButton, _Ring, _qcolor, label, set_tone
from .metrics import px

Qt = QtCore.Qt


def vbox(margins=(0, 0, 0, 0), spacing=0, parent=None):
    """A QVBoxLayout with margins (left, top, right, bottom) given in design px (scaled here)."""
    lay = QtWidgets.QVBoxLayout(parent) if parent is not None else QtWidgets.QVBoxLayout()
    lay.setContentsMargins(*[px(m) for m in margins])
    lay.setSpacing(px(spacing))
    return lay


def hbox(margins=(0, 0, 0, 0), spacing=0, parent=None):
    lay = QtWidgets.QHBoxLayout(parent) if parent is not None else QtWidgets.QHBoxLayout()
    lay.setContentsMargins(*[px(m) for m in margins])
    lay.setSpacing(px(spacing))
    return lay


class Divider(QtWidgets.QFrame):
    """A hairline (horizontal by default)."""

    def __init__(self, vertical=False, parent=None):
        super(Divider, self).__init__(parent)
        self.setProperty("ui", "divider")
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        if vertical:
            self.setFixedWidth(px(1))
        else:
            self.setFixedHeight(px(1))


# --------------------------------------------------------------------------------------------------
# Status glyph
# --------------------------------------------------------------------------------------------------

def _glyph_look(kind):
    """(icon, colour) of a status kind, read from the tokens when it is drawn (so a skin switch reaches it)."""
    t = tokens
    table = {
        "ok": ("success", t.SUCCESS), "success": ("success", t.SUCCESS), "error": ("error", t.DANGER),
        "warn": ("warn", t.WARNING), "warning": ("warn", t.WARNING), "info": ("info", t.INFO),
        "pending": ("dot", t.TEXT_DISABLED), "skipped": ("minus", t.TEXT_DISABLED), "cancelled": ("minus", t.WARNING),
        "running": ("spinner", t.ACCENT), "danger": ("error", t.DANGER),
    }
    return table.get(kind, table["pending"])


class Glyph(QtWidgets.QWidget):
    """A small round status mark: ok, error, warn, info, pending, skipped, cancelled, running (it turns while visible)."""

    def __init__(self, kind="pending", size=tokens.ICON_L, parent=None):
        super(Glyph, self).__init__(parent)
        self._kind = ""
        self._angle = 0.0
        self._anim = QtCore.QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(360.0)
        self._anim.setDuration(900)
        self._anim.setLoopCount(-1)
        self._anim.valueChanged.connect(self._turn)
        self.setFixedSize(px(size), px(size))
        self.set_kind(kind)

    def kind(self):
        return self._kind

    def set_kind(self, kind):
        self._kind = kind
        self.setAccessibleName(kind)
        self._sync()
        self.update()

    def _sync(self):
        if self._kind == "running" and self.isVisible():
            if self._anim.state() != QtCore.QAbstractAnimation.Running:
                self._anim.start()
        else:
            self._anim.stop()

    def showEvent(self, event):
        super(Glyph, self).showEvent(event)
        self._sync()

    def hideEvent(self, event):
        super(Glyph, self).hideEvent(event)
        self._sync()

    def _turn(self, value):
        self._angle = -float(value)
        self.update()

    def paintEvent(self, event):
        name, color = _glyph_look(self._kind)
        p = QtGui.QPainter(self)
        inset = self.width() * 0.1
        icons.paint(p, name, QtCore.QRectF(inset, inset, self.width() - 2 * inset, self.height() - 2 * inset), color,
                    angle=self._angle, background=tokens.BORDER_STRONG)
        p.end()


# --------------------------------------------------------------------------------------------------
# Section, Disclosure
# --------------------------------------------------------------------------------------------------

class Section(QtWidgets.QWidget):
    """A titled block of a screen: heading (with an optional "i"), then its content. No box: calm, the spacing does the grouping.

    Put content with `add(widget)` or `form()` (returns a `FormGrid` already added)."""

    def __init__(self, title, info=None, parent=None):
        super(Section, self).__init__(parent)
        outer = vbox(spacing=tokens.M, parent=self)
        head = hbox(spacing=tokens.S)
        self.title = label(title, "lead", wrap=False)
        head.addWidget(self.title)
        self.info = InfoButton(info) if info else None
        if self.info is not None:
            head.addWidget(self.info)
        head.addStretch(1)
        self.head = head
        outer.addLayout(head)
        self.body = vbox(spacing=tokens.M)
        outer.addLayout(self.body)

    def add(self, widget, stretch=0):
        self.body.addWidget(widget, stretch)
        return widget

    def add_right(self, widget):
        """A widget at the right end of the heading (a count, a quiet button)."""
        self.head.addWidget(widget)
        return widget

    def form(self):
        grid = FormGrid()
        self.body.addWidget(grid)
        return grid


class _Chevron(_Ring, QtWidgets.QAbstractButton):
    """The header of a `Disclosure`: chevron and text, checkable."""

    def __init__(self, text, parent=None):
        super(_Chevron, self).__init__(parent)
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSizePolicy(QtWidgets.QSizePolicy.Maximum, QtWidgets.QSizePolicy.Fixed)

    def sizeHint(self):
        fm = QtGui.QFontMetrics(metrics.font("body", strong=True))
        return QtCore.QSize(px(tokens.ICON) + px(tokens.S) + fm.horizontalAdvance(self.text()) + px(tokens.S), px(tokens.CONTROL_H))

    def minimumSizeHint(self):
        return QtCore.QSize(px(tokens.ICON) + px(tokens.S), px(tokens.CONTROL_H))

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        hot = self.underMouse() or self.hasFocus()
        color = tokens.TEXT if hot else tokens.TEXT_MUTED
        side = px(tokens.ICON)
        icons.paint(p, "chevron_down" if self.isChecked() else "chevron_right",
                    QtCore.QRectF(0, (self.height() - side) / 2.0, side, side), color)
        p.setFont(metrics.font("body", strong=True))
        p.setPen(_qcolor(color))
        left = side + px(tokens.S)
        shown = p.fontMetrics().elidedText(self.text(), Qt.ElideRight, max(px(8), self.width() - left))
        p.drawText(QtCore.QRectF(left, 0, self.width() - left, self.height()), Qt.AlignVCenter | Qt.AlignLeft, shown)
        p.end()
        if self.force_ring or (self.hasFocus() and self._keyboard):
            self._draw_ring()


class DisclosureGroup(object):
    """Disclosures that exclude each other: opening one closes the one that was open (closed first, so the window never grows by both)."""

    def __init__(self, *members):
        self.members = []
        for member in members:
            self.add(member)

    def add(self, disclosure):
        self.members.append(disclosure)
        disclosure.group = self
        return disclosure

    def opening(self, disclosure):
        for other in self.members:
            if other is not disclosure and other.is_open():
                other.set_open(False)


class Disclosure(QtWidgets.QWidget):
    """Content that does not fit goes behind a Disclosure ("Advanced"): closed by default, the body takes no room until opened.

    `group` (a `DisclosureGroup`): opening it closes its siblings first."""
    toggled = QtCore.Signal(bool)

    def __init__(self, title, body=None, open=False, group=None, parent=None):
        super(Disclosure, self).__init__(parent)
        self.group = None
        outer = vbox(spacing=tokens.S, parent=self)
        self.head = _Chevron(title)
        outer.addWidget(self.head, 0, Qt.AlignLeft)
        self.body = body if body is not None else QtWidgets.QWidget()
        if body is None:
            self.body_layout = vbox(spacing=tokens.M, parent=self.body)
        else:
            self.body_layout = self.body.layout()
        outer.addWidget(self.body)
        self.body.setVisible(False)
        self.head.toggled.connect(self._toggle)
        if group is not None:
            group.add(self)
        if open:
            self.set_open(True)

    def _toggle(self, on):
        if on and self.group is not None:
            self.group.opening(self)
        self.body.setVisible(bool(on))
        self.toggled.emit(bool(on))

    def add(self, widget):
        self.body_layout.addWidget(widget)
        return widget

    def is_open(self):
        return self.head.isChecked()

    def set_open(self, on):
        self.head.setChecked(bool(on))


# --------------------------------------------------------------------------------------------------
# FormGrid
# --------------------------------------------------------------------------------------------------

def _expands(control):
    """A field, a choice or a path fills the control column (up to a cap); everything else keeps its own size, left-aligned."""
    from .controls import PathField
    if control.minimumWidth() > 0 and control.minimumWidth() == control.maximumWidth():
        return False
    return isinstance(control, (QtWidgets.QLineEdit, QtWidgets.QComboBox, PathField))


class FormRow(QtWidgets.QWidget):
    """One row of a form: label | control | optional "i", and an inline error line under the control."""

    def __init__(self, text, control, info=None, parent=None):
        super(FormRow, self).__init__(parent)
        self.control = control
        outer = vbox(spacing=tokens.XS, parent=self)
        line = hbox(spacing=tokens.S)
        self.label = label(text, "field", wrap=True)
        self.label.setFixedWidth(px(tokens.LABEL_W))
        self.label.setMinimumHeight(px(tokens.CONTROL_H))
        self.label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        line.addWidget(self.label, 0, Qt.AlignTop)
        self.expands = _expands(control)
        if self.expands:
            if control.maximumWidth() > 100000:
                control.setMaximumWidth(px(tokens.FIELD_MAX))
            line.addWidget(control, 100, Qt.AlignTop)
        else:
            line.addWidget(control, 0, Qt.AlignTop)
        self.info = InfoButton(info) if info else None
        if self.info is not None:
            holder = QtWidgets.QWidget()
            holder.setFixedHeight(px(tokens.CONTROL_H))
            inner = vbox(parent=holder)
            inner.addStretch(1)
            inner.addWidget(self.info)
            inner.addStretch(1)
            holder.setFixedWidth(px(tokens.INFO_SIZE))
            line.addWidget(holder, 0, Qt.AlignTop)
        line.addStretch(1)
        outer.addLayout(line)
        self.error = label("", "small", "danger", wrap=True)
        self.error.setContentsMargins(px(tokens.LABEL_W + tokens.S), 0, 0, 0)
        self.error.hide()
        outer.addWidget(self.error)
        if hasattr(control, "errorChanged"):
            control.errorChanged.connect(self.set_error)

    def minimumSizeHint(self):
        """The control line plus ONE line for an error (not the error text wrapped at its narrowest width)."""
        base = self.layout().minimumSize()
        line = QtGui.QFontMetrics(metrics.font("small")).lineSpacing()
        return QtCore.QSize(base.width(), px(tokens.CONTROL_H) + ((line + px(tokens.XS)) if self.error.isVisibleTo(self) else 0))

    def set_error(self, message):
        message = str(message or "")
        self.error.setText(message)
        self.error.setVisible(bool(message))
        invalid = getattr(self.control, "set_invalid", None)
        if invalid is not None:
            invalid(bool(message))

    def clear_error(self):
        self.set_error("")

    def error_text(self):
        return self.error.text()

    def set_label(self, text):
        self.label.setText(text)

    def set_enabled(self, enabled):
        self.setEnabled(bool(enabled))


class FormGrid(QtWidgets.QWidget):
    """Rows of `label | control | (i)` on one grid: the label column has one width, rows are 12 px apart, Tab follows the rows."""

    def __init__(self, parent=None):
        super(FormGrid, self).__init__(parent)
        self._lay = vbox(spacing=tokens.M, parent=self)
        self.rows = []
        self._last_focus = None

    def add_row(self, text, control, info=None, key=None):
        row = FormRow(text, control, info)
        self._lay.addWidget(row)
        self.rows.append(row)
        if key:
            row.setObjectName(str(key))
        for widget in (control, row.info):
            if widget is not None and widget.focusPolicy() != Qt.NoFocus:
                if self._last_focus is not None:
                    QtWidgets.QWidget.setTabOrder(self._last_focus, widget)
                self._last_focus = widget
        return row

    def add_wide(self, widget):
        """A widget across the whole width (a banner, a note)."""
        self._lay.addWidget(widget)
        return widget

    def row(self, key):
        for row in self.rows:
            if row.objectName() == str(key):
                return row
        return None


# --------------------------------------------------------------------------------------------------
# Banner
# --------------------------------------------------------------------------------------------------

class Banner(QtWidgets.QFrame):
    """An inline message bar: info, success, warning or danger; an optional action button, an optional "Details" part, a close X.

    The one place errors end up: `show_message(kind, text, title=None, action=("Retry", callback), details=None)`; `clear()` hides it."""
    dismissed = QtCore.Signal()
    actionTriggered = QtCore.Signal()
    aboutToChange = QtCore.Signal()            # just before it is shown, changed or hidden (a window notes its height first)
    changed = QtCore.Signal()                  # shown, hidden or its text changed: the window may need another height

    def __init__(self, kind="info", text="", title="", action=None, details="", closable=True, parent=None):
        super(Banner, self).__init__(parent)
        self.setProperty("ui", "banner")
        self.setProperty("kind", kind)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._action_callback = None
        self._closable = closable
        outer = hbox((tokens.M, tokens.S + 2, tokens.S, tokens.S + 2), tokens.M, parent=self)
        self.glyph = Glyph(kind)
        outer.addWidget(self.glyph, 0, Qt.AlignTop)
        column = vbox(spacing=tokens.XS)
        self.title = label("", "strong", wrap=True)
        self.message = label("", "body", wrap=True, selectable=True, break_long=True)
        self.details = label("", "small", "muted", wrap=True, selectable=True, break_long=True)
        self.details.hide()
        column.addWidget(self.title)
        column.addWidget(self.message)
        column.addWidget(self.details)
        outer.addLayout(column, 1)
        self.details_button = Button(i18n.t("ui.details"), "quiet", handler=self._toggle_details)
        self.action_button = Button("", "secondary", handler=self._run_action)
        self.close_button = IconButton("cross", i18n.t("ui.dismiss"), self.clear)
        for widget in (self.details_button, self.action_button, self.close_button):
            outer.addWidget(widget, 0, Qt.AlignTop)
        self.show_message(kind, text, title or None, action, details or None, closable)
        if not text and not title:
            self.hide()

    def kind(self):
        return self.property("kind")

    def minimumSizeHint(self):
        """One line high: a window must not be as tall as the banner would be at its narrowest width (the real height comes from
        `heightForWidth`, and `ToolWindow` follows it)."""
        margins = self.layout().contentsMargins()
        line = max(px(tokens.BUTTON_H), QtGui.QFontMetrics(metrics.font("body")).lineSpacing())
        return QtCore.QSize(px(260), line + margins.top() + margins.bottom())

    def show_message(self, kind, text, title=None, action=None, details=None, closable=None):
        self.aboutToChange.emit()
        style.set_prop(self, "kind", kind)
        self.glyph.set_kind({"danger": "error", "warning": "warn"}.get(kind, kind))
        self.title.setText(title or "")
        self.title.setVisible(bool(title))
        self.message.setText(text or "")
        self.message.setVisible(bool(text))
        self.details.setText((details or "")[-1200:])
        self.details.hide()
        self.details_button.setText(i18n.t("ui.details"))
        self.details_button.setVisible(bool(details))
        self._action_callback = action[1] if action else None
        if action:
            self.action_button.setText(action[0])
        self.action_button.setVisible(bool(action))
        self.close_button.setVisible(self._closable if closable is None else bool(closable))
        self.setAccessibleName(text or title or "")
        self.show()
        self.changed.emit()

    def showEvent(self, event):
        super(Banner, self).showEvent(event)
        self.changed.emit()

    def hideEvent(self, event):
        super(Banner, self).hideEvent(event)
        self.changed.emit()

    def clear(self):
        self.aboutToChange.emit()
        self.hide()
        self.dismissed.emit()

    def _toggle_details(self):
        on = not self.details.isVisible()
        self.details.setVisible(on)
        self.details_button.setText(i18n.t("ui.hide_details") if on else i18n.t("ui.details"))
        self.changed.emit()

    def _run_action(self):
        self.actionTriggered.emit()
        if self._action_callback is not None:
            self._action_callback()


# --------------------------------------------------------------------------------------------------
# EmptyState
# --------------------------------------------------------------------------------------------------

class _EmptyIcon(Glyph):
    """The large icon of an `EmptyState`."""

    def __init__(self):
        super(_EmptyIcon, self).__init__("empty", tokens.ICON_XL)

    def set_kind(self, kind):
        self._kind = kind
        self._anim.stop()
        if kind == "loading" and self.isVisible():
            self._anim.start()
        self.update()

    def _sync(self):
        if self._kind == "loading" and self.isVisible():
            if self._anim.state() != QtCore.QAbstractAnimation.Running:
                self._anim.start()
        else:
            self._anim.stop()

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        if self._kind == "loading":
            icons.paint(p, "spinner", QtCore.QRectF(self.rect()), tokens.ACCENT, angle=self._angle, background=tokens.BORDER_STRONG)
        else:
            name, color = ("error", tokens.DANGER) if self._kind == "error" else ("inbox", tokens.TEXT_DISABLED)
            icons.paint(p, name, QtCore.QRectF(self.rect()).adjusted(4, 4, -4, -4), color, width=px(2))
        p.end()


class EmptyState(QtWidgets.QFrame):
    """What a list, table or screen shows when it has nothing to show: an icon, a short title, one line, maybe one action.

    `kind`: empty (a tray), error (a red cross), loading (a turning ring)."""

    def __init__(self, title="", text="", kind="empty", action=None, parent=None):
        super(EmptyState, self).__init__(parent)
        outer = vbox((tokens.XL, tokens.XL, tokens.XL, tokens.XL), tokens.S, parent=self)
        outer.addStretch(1)
        self.icon = _EmptyIcon()
        outer.addWidget(self.icon, 0, Qt.AlignHCenter)
        self.title = label("", "lead", wrap=True)
        self.title.setAlignment(Qt.AlignCenter)
        self.text = label("", "body", "muted", wrap=True)
        self.text.setAlignment(Qt.AlignCenter)
        self.text.setMaximumWidth(px(380))
        outer.addWidget(self.title)
        outer.addWidget(self.text, 0, Qt.AlignHCenter)
        self.button = Button("", "secondary")
        self.button.clicked.connect(lambda *_a: self._callback and self._callback())
        outer.addWidget(self.button, 0, Qt.AlignHCenter)
        outer.addStretch(1)
        self._callback = None
        self._kind = kind
        self.set_state(title, text, kind, action)

    def minimumSizeHint(self):
        margins = self.layout().contentsMargins()
        lines = QtGui.QFontMetrics(metrics.font("body")).lineSpacing() + QtGui.QFontMetrics(metrics.font("lead")).lineSpacing()
        return QtCore.QSize(px(200), margins.top() + margins.bottom() + px(tokens.ICON_XL) + lines + 2 * px(tokens.S))

    def set_card(self, on=True):
        """Draw it as a card (a bordered box), as inside a `DataTable`."""
        self.setProperty("ui", "card" if on else "")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.style().unpolish(self)
        self.style().polish(self)

    def set_state(self, title, text="", kind=None, action=None):
        self._kind = kind or self._kind
        self.icon.set_kind(self._kind)
        self.title.setText(title)
        self.title.setVisible(bool(title))
        self.text.setText(text)
        self.text.setVisible(bool(text))
        self._callback = action[1] if action else None
        if action:
            self.button.setText(action[0])
        self.button.setVisible(bool(action))
        self.update()
