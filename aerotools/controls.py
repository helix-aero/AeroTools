"""Small controls shared by the windows of the suite (no numpy here, so they load and test anywhere).

- `EditableValue`: the number written next to a slider ("315 degrees (NW)", "80 %"). Double-click it (or Tab to it and press Enter or F2),
  type an exact number, Enter applies it, Esc puts the old value back. A number outside the allowed range is refused on the spot, with the
  range written in the tooltip; the slider is only moved by a valid number.
- `reset_button`: the small round arrow next to a control that puts its default value back.
- `slider_row`: a label, a short slider, its editable value and its reset button, placed in one grid row.
"""

from . import i18n, theme, ui
from .qt import QtCore, QtGui, QtWidgets

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
        self.setToolTip(i18n.t("controls.value_hint", low=low, high=high if not modulo else modulo - 1))
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
        self.edit.setToolTip(i18n.t("controls.value_bad", low=self.low, high=self.high if not self.modulo else self.modulo - 1)
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


BAR_WIDTH = 14


class FitScroll(QtWidgets.QScrollArea):
    """A scroll area that reports the size of its content as its own minimum (so a window built from it cannot be made smaller
    than the content needs, whatever the font or the display scaling), no larger than `cap` pixels so a small screen still works.
    Bars only show when the window is smaller than that minimum; they are wide and easy to see."""

    def __init__(self, content, preferred=0, cap=0):
        super(FitScroll, self).__init__()
        self._content, self._preferred, self._cap = content, preferred, cap
        self.setWidgetResizable(True)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.setWidget(content)
        self.viewport().setAutoFillBackground(False)
        content.setAutoFillBackground(False)
        self.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollBar:vertical { background: %(track)s; width: %(w)dpx; margin: 0; border-radius: 7px; }"
            "QScrollBar:horizontal { background: %(track)s; height: %(w)dpx; margin: 0; border-radius: 7px; }"
            "QScrollBar::handle:vertical { background: %(handle)s; min-height: 36px; margin: 2px; border-radius: 5px; }"
            "QScrollBar::handle:horizontal { background: %(handle)s; min-width: 36px; margin: 2px; border-radius: 5px; }"
            "QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover { background: %(hover)s; }"
            "QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; background: none; }"
            "QScrollBar::add-page, QScrollBar::sub-page { background: none; }" % {
                "track": theme.GROUND_3, "handle": theme.FG_3, "hover": theme.FG_2, "w": BAR_WIDTH})

        content.installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is self._content and event.type() == QtCore.QEvent.LayoutRequest:
            self.updateGeometry()                           # the content's needs changed (font, text): the window must hear of it
        return super(FitScroll, self).eventFilter(watched, event)

    def minimumSizeHint(self):
        width = self._content.minimumSizeHint().width() + BAR_WIDTH + 8
        if self._cap:
            width = min(width, self._cap)
        return QtCore.QSize(width, 120)

    def sizeHint(self):
        return QtCore.QSize(max(self._preferred, self.minimumSizeHint().width()), 400)


def scroll_page(content, preferred=0, cap=0):
    return FitScroll(content, preferred, cap)


def screen_area():
    """Room the screen leaves to a window (QRect), taskbar excluded: the screen that holds the Metashape main window, the primary one when
    that is not known; a generous default when Qt cannot say."""
    try:
        areas = available_areas()
        host = host_rect(None)
        if host is not None and len(areas) > 1:
            x, y, w, h = areas[area_for(host, areas)]
            return QtCore.QRect(x, y, w, h)
        return QtWidgets.QApplication.primaryScreen().availableGeometry()
    except Exception:
        return QtCore.QRect(0, 0, 1920, 1040)


def available_areas():
    """The usable area (taskbar excluded) of every screen, the primary one first, as (x, y, width, height) tuples."""
    out = []
    for screen in QtWidgets.QApplication.screens():
        g = screen.availableGeometry()
        out.append((g.x(), g.y(), g.width(), g.height()))
    return out or [(0, 0, 1920, 1040)]


def host_rect(window=None):
    """The frame (x, y, width, height) of the Metashape main window: the top window above `window` when it has a parent, else the main
    window found among the top windows. None when there is none."""
    top = None
    try:
        parent = window.parentWidget() if window is not None else None
        top = parent.window() if parent is not None else None
        if top is None:
            for widget in QtWidgets.QApplication.topLevelWidgets():
                if isinstance(widget, QtWidgets.QMainWindow) and widget.isVisible():
                    top = widget
                    break
        if top is None:
            return None
        g = top.normalGeometry() if top.isMinimized() else top.frameGeometry()
        return (g.x(), g.y(), g.width(), g.height())
    except Exception:
        return None


def area_for(rect, areas):
    """Index of the area (x, y, w, h) that holds the centre of `rect`, else the nearest one; 0 without area. Pure."""
    if not areas:
        return 0
    cx, cy = rect[0] + rect[2] / 2.0, rect[1] + rect[3] / 2.0
    best, best_gap = 0, None
    for index, (x, y, w, h) in enumerate(areas):
        gap = max(x - cx, 0, cx - (x + w)) ** 2 + max(y - cy, 0, cy - (y + h)) ** 2
        if best_gap is None or gap < best_gap:
            best, best_gap = index, gap
    return best


def screen_width():
    return screen_area().width()


class CappedStack(QtWidgets.QWidget):
    """The pages of a wizard, one shown at a time. Only the page on show counts for the size of the window, and never more than the display
    has: a plain QStackedWidget counts the tallest page whatever is shown, so a window with a long page (22 flights, many walls) could not be made
    smaller than that page, grew past the bottom of the screen and flooded Metashape with resize warnings while it was dragged.
    `reserve` is what the rest of the window needs around the stack (title, step bar, buttons)."""

    def __init__(self, reserve=340, margin=70):
        super(CappedStack, self).__init__()
        self._reserve, self._margin = reserve, margin
        self._pages, self._current = [], -1
        box = QtWidgets.QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        self._box = box

    def addWidget(self, page):
        self._pages.append(page)
        self._box.addWidget(page)
        page.setVisible(len(self._pages) - 1 == self._current)
        return len(self._pages) - 1

    def setCurrentIndex(self, index):
        if not 0 <= index < len(self._pages):
            return
        self._current = index
        for number, page in enumerate(self._pages):
            page.setVisible(number == index)
        self.updateGeometry()

    def currentIndex(self):
        return self._current

    def currentWidget(self):
        return self._pages[self._current] if 0 <= self._current < len(self._pages) else None

    def widget(self, index):
        return self._pages[index] if 0 <= index < len(self._pages) else None

    def count(self):
        return len(self._pages)

    def sizeHint(self):
        hint = super(CappedStack, self).sizeHint()
        area = screen_area()
        return QtCore.QSize(min(hint.width(), area.width() - self._margin), min(hint.height(), max(300, area.height() - self._margin - self._reserve)))


def fit_window(dialog, margin=70, again=True):
    """Make a window follow what its panels need: its minimum size becomes the layout's (it follows later changes, such as a font
    change), and it opens at least that big and no bigger than the screen. Call it from showEvent; it measures again a moment later,
    because some widgets only reach their real size once the first layout is done."""
    layout = dialog.layout()
    layout.setSizeConstraint(QtWidgets.QLayout.SetMinimumSize)
    dialog.ensurePolished()
    for child in dialog.findChildren(QtWidgets.QWidget):
        child.ensurePolished()
        if child.layout() is not None:
            child.layout().invalidate()
    layout.invalidate()
    layout.activate()
    need = layout.minimumSize()
    area = screen_area()
    room = (area.width() - margin, area.height() - margin)
    size = dialog.size()                                    # (not .height(): the look window has a slider of that name)
    wanted = (min(max(size.width(), need.width()), room[0]), min(max(size.height(), need.height()), room[1]))
    if (size.width(), size.height()) != wanted:
        dialog.resize(*wanted)
    if again:
        QtCore.QTimer.singleShot(0, lambda: fit_window(dialog, margin, again=False))


def sync_minimum(dialog):
    """Makes the least size of the window what its pages really need at its present width. Wrapped text makes the least height depend on the
    width; when Qt's least size said less than that, Windows stopped the drag at a size Qt did not know and moved the window instead of
    stopping. With the true least size the drag simply stops at the limit."""
    layout = dialog.layout()
    need_h = layout.minimumSize().height()
    if layout.hasHeightForWidth():
        need_h = max(need_h, layout.minimumHeightForWidth(dialog.width()))
    area = screen_area()
    size = (min(layout.minimumSize().width(), area.width()), min(need_h, area.height()))
    if (dialog.minimumWidth(), dialog.minimumHeight()) != size:
        dialog.setMinimumSize(*size)


def follow_content(dialog):
    """From now on the least size of the window follows its content: call `sync_minimum` again from the window's `resizeEvent` and when the
    page changes. The size constraint of `fit_window` is dropped, it would set the least size again behind our back."""
    dialog.layout().setSizeConstraint(QtWidgets.QLayout.SetNoConstraint)
    sync_minimum(dialog)


class FollowsText(object):
    """Mixin for a dialog (put it before `QDialog`) whose layout has wrapped text: its least height always matches what the text needs,
    so a drag on its edge stops at the limit instead of moving the window."""

    def showEvent(self, event):
        super(FollowsText, self).showEvent(event)
        if not getattr(self, "_follows", False):
            self._follows = True
            follow_content(self)

    def resizeEvent(self, event):
        super(FollowsText, self).resizeEvent(event)
        if getattr(self, "_follows", False):
            sync_minimum(self)

    def refit(self):
        """Call after a text changed: the least height follows it and the window grows if it is now too short."""
        sync_minimum(self)
        self.resize(self.width(), max(self.height(), self.minimumHeight()))


class WrapText(QtWidgets.QTextEdit):
    """Read-only text that wraps anywhere (a path has no spaces to wrap at), can be selected and copied, and never makes its window wider.
    Looks like a label; `setText` / `text` work like a label's, and the height follows the text."""

    def __init__(self, meta=False):
        super(WrapText, self).__init__()
        self.setReadOnly(True)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setLineWrapMode(QtWidgets.QTextEdit.WidgetWidth)
        self.setWordWrapMode(QtGui.QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.document().setDocumentMargin(0)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.setStyleSheet("QTextEdit { background: transparent; border: none; color: %s; font-size: %s; }" % (
            theme.FG_2 if meta else theme.FG_1, "9pt" if meta else "10pt"))
        self.setFixedHeight(0)

    def setText(self, text):
        self.setPlainText(text)
        self._fit()

    def text(self):
        return self.toPlainText()

    def minimumSizeHint(self):
        return QtCore.QSize(40, self.height())

    def sizeHint(self):
        return QtCore.QSize(200, self.height())

    def resizeEvent(self, event):
        super(WrapText, self).resizeEvent(event)
        self._fit()

    def _fit(self):
        width = self.viewport().width()
        if width <= 0:
            return
        self.document().setTextWidth(width)
        height = int(self.document().size().height() + 2) if self.toPlainText() else 0
        if height != self.height():
            self.setFixedHeight(height)


class ElidedLabel(QtWidgets.QLabel):
    """One line that is cut with "..." when it does not fit, and never makes its window wider. The whole text is the tooltip."""

    def __init__(self, object_name=None):
        super(ElidedLabel, self).__init__()
        if object_name:
            self.setObjectName(object_name)
        self._full = ""
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)

    def setFullText(self, text):
        self._full = str(text)
        self.setToolTip(self._full)
        self._elide()

    def fullText(self):
        return self._full

    def minimumSizeHint(self):
        return QtCore.QSize(40, super(ElidedLabel, self).minimumSizeHint().height())

    def sizeHint(self):
        return QtCore.QSize(200, super(ElidedLabel, self).sizeHint().height())

    def resizeEvent(self, event):
        super(ElidedLabel, self).resizeEvent(event)
        self._elide()

    def _elide(self):
        width = self.width()
        shown = self.fontMetrics().elidedText(self._full, QtCore.Qt.ElideRight, max(20, width)) if width > 1 else self._full
        if shown != self.text():
            self.setText(shown)


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
