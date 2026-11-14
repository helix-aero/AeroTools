"""Window shells: `ToolWindow` (a floating tool window), `DockShell` (a dock panel), and the parts they share:
`Header`, `StepBar`, `StepScreen`, `FooterBar`.

One window = header (tool mark, title, "i") / optional step bar / content / banner / footer.
Footer order, always: Cancel or Close on the left, Back then the primary button on the right. The primary is the right-most button.
"""

from .. import i18n
from ..qt import QtCore, QtGui, QtWidgets
from . import icons, metrics, style, tokens
from .blocks import Banner, Divider, hbox, vbox
from .controls import Button, ElidedLabel, FontButton, IconButton, InfoButton, ThemeButton, Toggle, _qcolor, label, set_tone
from .metrics import px
from .progress import ProgressRow
from .tasks import Cancelled, TaskRunner, describe

Qt = QtCore.Qt
_INSTANCES = {}


def _alive(widget):
    try:
        widget.objectName()
        return True
    except RuntimeError:
        return False


def present(window, parent=None, *args, **kwargs):
    """The window-reuse helper. Given a window, show it, un-minimise it, raise it and focus it. Given a widget class, keep one instance per
    class: build it with (parent, *args, **kwargs) the first time, then only bring the same one to the front. Returns the window."""
    if isinstance(window, type):
        existing = _INSTANCES.get(window)
        if existing is None or not _alive(existing):
            existing = window(parent, *args, **kwargs)
            _INSTANCES[window] = existing
            existing.destroyed.connect(lambda *_a, c=window: _INSTANCES.pop(c, None))
        window = existing
    if window.isMinimized():
        window.setWindowState(window.windowState() & ~Qt.WindowMinimized)
    window.show()
    window.raise_()
    window.activateWindow()
    return window


def screen_area(widget=None):
    """Usable area (QRect, taskbar excluded) of the screen that holds `widget`, else the screen of the host (the active window), else the primary."""
    try:
        screen = None
        if widget is not None:
            parent = widget.parentWidget()
            host = parent.window() if parent is not None else None
            handle = (host or widget).windowHandle() if (host or widget).isVisible() else None
            screen = handle.screen() if handle is not None else None
        if screen is None:
            active = QtWidgets.QApplication.activeWindow()
            handle = active.windowHandle() if active is not None else None
            screen = handle.screen() if handle is not None else QtWidgets.QApplication.primaryScreen()
        return screen.availableGeometry()
    except Exception:
        return QtCore.QRect(0, 0, 1920, 1040)


# --------------------------------------------------------------------------------------------------
# Header
# --------------------------------------------------------------------------------------------------

class _Mark(QtWidgets.QWidget):
    """The tool mark: a small rounded square in the logo gradient."""

    def __init__(self, name="mark", parent=None):
        super(_Mark, self).__init__(parent)
        self._name = name
        self.setFixedSize(px(28), px(28))

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        icons.paint(p, self._name, QtCore.QRectF(self.rect()), tokens.TEXT)
        p.end()


def _fill_brush(stops, rect, vertical=False):
    """A QBrush for a fill of the skin: one colour, or a gradient left to right (top to bottom when `vertical`)."""
    if len(stops) == 1:
        return QtGui.QBrush(QtGui.QColor(stops[0]))
    gradient = QtGui.QLinearGradient(rect.topLeft(), rect.bottomLeft() if vertical else rect.topRight())
    for i, colour in enumerate(stops):
        gradient.setColorAt(i / float(len(stops) - 1), QtGui.QColor(colour))
    return QtGui.QBrush(gradient)


class HeaderBand(QtWidgets.QWidget):
    """The strip behind a window's header: the skin's header fill and the rule under it (a gradient in some skins), edge to edge.
    Nothing is drawn in a skin that has neither, so the window looks as before."""

    def __init__(self, parent=None):
        super(HeaderBand, self).__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, False)

    def paintEvent(self, event):
        fill, rule = tokens.HEADER_FILL, tokens.HEADER_RULE
        if not fill and not rule:
            return
        p = QtGui.QPainter(self)
        rect = QtCore.QRectF(self.rect())
        if fill:
            p.fillRect(rect, _fill_brush(fill, rect, vertical=True))
        if rule:
            line = QtCore.QRectF(0, rect.height() - px(tokens.RULE_H), rect.width(), px(tokens.RULE_H))
            p.fillRect(line, _fill_brush(rule, line))
        p.end()


class _Wordmark(QtWidgets.QWidget):
    """The AERO wordmark, small and quiet, at the right end of a header. It hides itself when it would crowd the title."""

    def __init__(self, parent=None):
        super(_Wordmark, self).__init__(parent)
        self.setFixedSize(int(icons.wordmark_width(px(13))) + 1, px(13))
        self.setAccessibleName("AERO")
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        icons.paint_wordmark(p, QtCore.QRectF(self.rect()))
        p.end()


class Header(QtWidgets.QWidget):
    """Tool mark, title, subtitle (the project or chunk, one muted line) and the "i". `right` is a layout for a few quiet icon buttons."""

    def __init__(self, title="", subtitle="", info="", mark="mark", parent=None):
        super(Header, self).__init__(parent)
        outer = hbox(spacing=tokens.S, parent=self)
        self.mark = _Mark(mark) if mark else None
        if self.mark is not None:
            outer.addWidget(self.mark, 0, Qt.AlignVCenter)
        column = vbox(spacing=0)
        row = hbox(spacing=tokens.S)
        self.title = ElidedLabel(title, "title", fit=True)
        metrics.apply_case(self.title, "title")
        self.info = InfoButton(info)
        self.info.setVisible(bool(info))
        row.addWidget(self.title)
        row.addWidget(self.info)
        row.addStretch(1)
        column.addLayout(row)
        self.subtitle = ElidedLabel(subtitle, "small", "muted")
        self.subtitle.setVisible(bool(subtitle))
        column.addWidget(self.subtitle)
        outer.addLayout(column, 1)
        self.wordmark = _Wordmark()
        outer.addWidget(self.wordmark, 0, Qt.AlignVCenter)
        self.theme_button = ThemeButton()
        self.font_button = FontButton()
        look = QtWidgets.QWidget()                                    # the two round look buttons sit close together
        look_lay = hbox(spacing=tokens.XS, parent=look)
        look_lay.addWidget(self.theme_button)
        look_lay.addWidget(self.font_button)
        outer.addWidget(look, 0, Qt.AlignVCenter)
        self.right = hbox(spacing=tokens.XS)
        outer.addLayout(self.right)

    def resizeEvent(self, event):
        super(Header, self).resizeEvent(event)
        self._fit_wordmark()

    def _fit_wordmark(self):
        """Keep the title whole: when it would be cut, the "Aa" button steps aside first (the font is also in Aero-GCP Settings and in
        Install plugins); show the wordmark only when the title keeps its whole text and some air besides it."""
        used = self.title.sizeHint().width() + (self.info.width() + px(tokens.S) if self.info.isVisible() else 0)
        used += (self.mark.width() + px(tokens.S)) if self.mark is not None else 0
        used += self.theme_button.width() + px(tokens.S)
        used += sum(self.right.itemAt(i).widget().width() + px(tokens.XS) for i in range(self.right.count()) if self.right.itemAt(i).widget())
        margin = 2 * px(tokens.M)
        font_room = self.width() - used - px(tokens.S) - self.font_button.width() - px(tokens.XS)
        show_font = font_room >= 0
        if show_font != self.font_button.isVisibleTo(self):
            self.font_button.setVisible(show_font)
        room = font_room if show_font else self.width() - used - margin
        want = room >= self.wordmark.width() + px(tokens.L)
        if want != self.wordmark.isVisibleTo(self):
            self.wordmark.setVisible(want)

    def set_title(self, text):
        self.title.setFullText(text)

    def set_subtitle(self, text):
        self.subtitle.setFullText(text)
        self.subtitle.setVisible(bool(text))

    def set_info(self, text):
        self.info.set_text(text)
        self.info.setVisible(bool(text))


# --------------------------------------------------------------------------------------------------
# Steps
# --------------------------------------------------------------------------------------------------

class _Dot(QtWidgets.QWidget):
    """One numbered circle of the step bar: todo (ring), current (green), done (green with a tick)."""

    def __init__(self, number, parent=None):
        super(_Dot, self).__init__(parent)
        self.number, self.state = number, "todo"
        self.setFixedSize(px(tokens.STEP_DOT), px(tokens.STEP_DOT))

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        rect = QtCore.QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self.state == "todo":
            p.setPen(QtGui.QPen(_qcolor(tokens.BORDER_STRONG), 1.5))
            p.setBrush(Qt.NoBrush)
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(_qcolor(tokens.ACCENT))
        p.drawEllipse(rect)
        if self.state == "done":
            icons.paint(p, "check", rect.adjusted(3, 3, -3, -3), tokens.ON_ACCENT, width=max(1.6, rect.width() / 9.0))
        else:
            p.setFont(metrics.font("small", strong=True))
            p.setPen(_qcolor(tokens.ON_ACCENT if self.state == "current" else tokens.TEXT_MUTED))
            p.drawText(rect, Qt.AlignCenter, str(self.number))
        p.end()


class _Line(QtWidgets.QWidget):
    """The line between two steps; green when the step before it is done."""

    def __init__(self, parent=None):
        super(_Line, self).__init__(parent)
        self.done = False
        self.setFixedHeight(px(tokens.STEP_DOT))
        self.setMinimumWidth(px(tokens.L))
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setPen(QtGui.QPen(_qcolor(tokens.ACCENT if self.done else tokens.BORDER), 2))
        y = self.height() / 2.0
        p.drawLine(QtCore.QPointF(px(2), y), QtCore.QPointF(self.width() - px(2), y))
        p.end()


class StepBar(QtWidgets.QWidget):
    """The numbered steps of a wizard in one row. `set_current(i)`, `state_of(i)` ('todo' / 'current' / 'done'). Names are cut when short of room."""

    def __init__(self, names=(), parent=None):
        super(StepBar, self).__init__(parent)
        self._lay = hbox(spacing=tokens.S, parent=self)
        self.circles, self.names, self.lines = [], [], []
        self.current = 0
        self._tail = None
        for name in names:
            self.add_step(name)
        self.set_current(0)

    def add_step(self, name):
        index = len(self.circles)
        if index:
            line = _Line()
            self.lines.append(line)
            self._lay.addWidget(line, 1)
        dot = _Dot(index + 1)
        text = ElidedLabel(name, "body", "muted", fit=True)
        metrics.apply_case(text, "label")
        self.circles.append(dot)
        self.names.append(text)
        self._lay.addWidget(dot)
        self._lay.addWidget(text)
        self.set_current(self.current)

    def set_current(self, current):
        self.current = current
        for index, (dot, name) in enumerate(zip(self.circles, self.names)):
            dot.state = "current" if index == current else ("done" if index < current else "todo")
            dot.update()
            style.set_prop(name, "type", "strong" if index == current else "body")
            set_tone(name, None if index <= current else "muted")
        for index, line in enumerate(self.lines):
            line.done = index < current
            line.update()

    def state_of(self, index):
        return self.circles[index].state


class StepScreen(QtWidgets.QWidget):
    """One page of a wizard. Override what you need:

      enter() / leave()      the page is shown / left
      can_leave(forward)     False vetoes the move (show why with `shell.show_banner`)
      next_enabled()         is the primary button usable on this page
      next_text()            text of the primary button here (default Next, Done on the last page)
      show_back()            is Back offered here
      hint()                 one muted line for the footer
    Call `self.changed.emit()` whenever the answers to these may have changed. Lay content out in `self.content`."""
    changed = QtCore.Signal()
    title = ""
    name_key = ""

    def __init__(self, shell=None, parent=None):
        super(StepScreen, self).__init__(parent)
        self.shell = shell
        self.content = vbox(spacing=tokens.XL, parent=self)

    def name(self):
        return i18n.t(self.name_key) if self.name_key else self.title

    def enter(self):
        pass

    def leave(self):
        pass

    def can_leave(self, forward=True):
        return True

    def next_enabled(self):
        return True

    def next_text(self):
        return None

    def show_back(self):
        return True

    def hint(self):
        return ""


# --------------------------------------------------------------------------------------------------
# Footer
# --------------------------------------------------------------------------------------------------

class FooterBar(QtWidgets.QWidget):
    """[Cancel | Close] [extras] ... status or progress ... [Back] [Primary]. Always this order.

    `cancel_button`, `back_button`, `next_button` (the primary), `status`, `progress`. Destructive actions never go next to the primary:
    put them with `add_left(widget)`."""

    def __init__(self, parent=None):
        super(FooterBar, self).__init__(parent)
        self._lay = hbox(spacing=tokens.S, parent=self)
        self.cancel_button = Button(i18n.t("ui.cancel"), "secondary")
        self._lay.addWidget(self.cancel_button)
        self._extra_at = 1
        self.center = QtWidgets.QStackedWidget()
        self.center.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        self.status = ElidedLabel("", "small", "muted")
        self.progress = ProgressRow()
        self.center.addWidget(self.status)
        self.center.addWidget(self.progress)
        self._lay.addWidget(self.center, 1)
        self.back_button = Button(i18n.t("ui.back"), "secondary")
        self.next_button = Button(i18n.t("ui.next"), "primary")
        self._lay.addWidget(self.back_button)
        self._lay.addWidget(self.next_button)
        self.cancel_mode = "cancel"

    def set_cancel_mode(self, mode):
        """'cancel' (the window does something) or 'close' (it only shows things): the text of the left button."""
        self.cancel_mode = mode
        self.cancel_button.setText(i18n.t("ui.close" if mode == "close" else "ui.cancel"))

    def add_left(self, widget):
        """A secondary action (Open folder, Action log, Delete) right after Cancel / Close, never next to the primary."""
        self._lay.insertWidget(self._extra_at, widget)
        self._extra_at += 1
        return widget

    def set_primary(self, text, role="primary"):
        self.next_button.setText(text)
        self.next_button.set_role(role)

    def set_status(self, text, tone=None):
        self.status.setFullText(text)
        set_tone(self.status, tone)

    def show_progress(self, on):
        self.center.setCurrentWidget(self.progress if on else self.status)


# --------------------------------------------------------------------------------------------------
# ToolWindow
# --------------------------------------------------------------------------------------------------

def focus_chain(widget):
    """Focusable widgets under `widget` in layout (visual) order, hidden ones skipped."""
    out = []

    def walk(w):
        if w.isHidden() and w is not widget:
            return
        if w is not widget and w.focusPolicy() & Qt.TabFocus and w.isEnabled():
            out.append(w)
        if isinstance(w, QtWidgets.QStackedWidget):
            current = w.currentWidget()
            if current is not None:
                walk(current)
            return
        if isinstance(w, QtWidgets.QAbstractScrollArea) or isinstance(w, QtWidgets.QAbstractSpinBox):
            return
        lay = w.layout()
        if lay is not None:
            visit(lay)
        else:
            for child in w.children():
                if isinstance(child, QtWidgets.QWidget) and not child.isWindow():
                    walk(child)

    def visit(lay):
        for i in range(lay.count()):
            item = lay.itemAt(i)
            if item.widget() is not None:
                walk(item.widget())
            elif item.layout() is not None:
                visit(item.layout())

    walk(widget)
    return out


class ToolWindow(QtWidgets.QDialog):
    """The shell of every tool window.

    Subclass it, call `super().__init__(parent, title=..., info=..., wizard=...)`, then either `set_content(widget)` (one screen) or
    `add_screen(StepScreen)` (a wizard). It provides:

      header / steps / stack / banner / footer          the parts; `next_button`, `back_button`, `cancel_button`, `status` are aliases
      go(i), next(), back(), refresh_buttons()          navigation of a wizard
      set_primary(text, handler)                        the primary button of a one-screen window
      show_banner(kind, text, ...), show_error(exc)     the one place messages and errors appear
      run(func, ...)                                    a long task with progress, cancel, retry and the error banner
      set_busy(on) / is_busy()                          while busy: Esc cancels the task, closing is refused, navigation is disabled
      ToolWindow.open(parent, ...)                      one instance per class: a second call raises the first

    Keys: Enter presses the primary button (or the focused button), Esc cancels the running task, else closes."""

    TITLE = ""
    SUBTITLE = ""
    INFO = ""
    DESIGN_SIZE = None          # (width, height) in design px at 100 %, or None to size to the content
    MARK = "mark"
    CLOSE_ONLY = False          # True: the left button says Close (the window only shows things)

    def __init__(self, parent=None, title=None, subtitle=None, info=None, wizard=False, design_size=None, modal=False, mark=None):
        super(ToolWindow, self).__init__(parent)
        self.setModal(bool(modal))
        self.setWindowTitle(title if title is not None else self.TITLE)
        self.setSizeGripEnabled(False)
        self._design = design_size or self.DESIGN_SIZE
        self._fitted = False
        self._busy = 0
        self._runner = None
        self._cancellable = True
        self._last_run = None
        self._primary_handler = None
        self._status_override = False
        self.screens = []

        outer = vbox(parent=self)
        outer.setSizeConstraint(QtWidgets.QLayout.SetMinimumSize)
        self.root = QtWidgets.QWidget()
        self.root.setAttribute(Qt.WA_StyledBackground, True)
        style.apply(self.root)
        outer.addWidget(self.root)
        frame = vbox(parent=self.root)
        self.band = HeaderBand()
        band_lay = vbox((tokens.WINDOW_MARGIN, tokens.WINDOW_MARGIN, tokens.WINDOW_MARGIN,
                         tokens.M + tokens.RULE_H if (tokens.HEADER_FILL or tokens.HEADER_RULE) else 0), 0, parent=self.band)
        frame.addWidget(self.band)
        self.content_area = QtWidgets.QWidget()
        frame.addWidget(self.content_area, 1)
        lay = vbox((tokens.WINDOW_MARGIN, tokens.L, tokens.WINDOW_MARGIN, tokens.WINDOW_MARGIN), tokens.L, parent=self.content_area)

        self.header = Header(title if title is not None else self.TITLE, subtitle if subtitle is not None else self.SUBTITLE,
                             info if info is not None else self.INFO, mark if mark is not None else self.MARK)
        band_lay.addWidget(self.header)
        self.steps = StepBar() if wizard else None
        if self.steps is not None:
            lay.addWidget(self.steps)
        self.stack = QtWidgets.QStackedWidget()
        lay.addWidget(self.stack, 1)
        self.banner = Banner("info", "")
        self.banner.hide()
        self.banner.aboutToChange.connect(self._banner_changing)
        self.banner.changed.connect(lambda: QtCore.QTimer.singleShot(0, self._sync_banner_height))
        self._banner_h = 0
        self._banner_state = False
        self._h_base = 0
        lay.addWidget(self.banner)
        self.footer_divider = Divider()
        lay.addWidget(self.footer_divider)
        self.footer = FooterBar()
        lay.addWidget(self.footer)
        self.footer.set_cancel_mode("close" if self.CLOSE_ONLY else "cancel")

        self.cancel_button = self.footer.cancel_button
        self.back_button = self.footer.back_button
        self.next_button = self.footer.next_button
        self.status = self.footer.status
        self.back_button.setVisible(bool(wizard))
        self.cancel_button.clicked.connect(self.cancel_clicked)
        self.back_button.clicked.connect(self.back)
        self.next_button.clicked.connect(self._primary_clicked)

    # -- content ----------------------------------------------------------------------------------

    def set_content(self, widget):
        """The one screen of a window that is not a wizard."""
        self.stack.addWidget(widget)
        self.stack.setCurrentWidget(widget)
        return widget

    def add_screen(self, screen):
        """Add a page to the wizard (a `StepScreen`); the step bar grows by one step."""
        screen.shell = self
        self.screens.append(screen)
        self.stack.addWidget(screen)
        if self.steps is not None:
            self.steps.add_step(screen.name())
        screen.changed.connect(self.refresh_buttons)
        return screen

    def set_title(self, text):
        self.header.set_title(text)
        self.setWindowTitle(text)

    def set_subtitle(self, text):
        self.header.set_subtitle(text)

    def set_primary(self, text, handler=None, role="primary"):
        """Text, role and action of the primary button of a window that is not a wizard."""
        self.footer.set_primary(text, role)
        self._primary_handler = handler

    def set_status(self, text, tone=None):
        self._status_override = bool(text)
        self.footer.set_status(text, tone)

    # -- wizard navigation ------------------------------------------------------------------------

    def current_screen(self):
        index = self.stack.currentIndex()
        return self.screens[index] if 0 <= index < len(self.screens) else None

    def go(self, index, force=False):
        """Show page `index`. A page can refuse to be left (`can_leave`). Returns whether the page changed."""
        if not 0 <= index < len(self.screens):
            return False
        current = self.stack.currentIndex()
        if not force and index != current and self.is_busy():
            return False
        if not force and index != current and 0 <= current < len(self.screens):
            screen = self.screens[current]
            if not screen.can_leave(index > current):
                return False
            screen.leave()
        self.clear_banner()
        self.stack.setCurrentIndex(index)
        if self.steps is not None:
            self.steps.set_current(index)
        self.screens[index].enter()
        self._status_override = False
        self.refresh_buttons()
        self.fix_tab_order()
        self.focus_initial()
        return True

    def next(self):
        index = self.stack.currentIndex()
        if index >= len(self.screens) - 1:
            self.finish()
        else:
            self.go(index + 1)

    def back(self):
        self.go(self.stack.currentIndex() - 1)

    def finish(self):
        """The primary button on the last page. Default: close the window."""
        self.accept()

    def refresh_buttons(self, *_args):
        """Re-read the current page and the busy state into Back / primary / status."""
        busy = self.is_busy()
        screen = self.current_screen()
        index = self.stack.currentIndex()
        if screen is not None:
            last = index >= len(self.screens) - 1
            self.back_button.setVisible(index > 0 and screen.show_back())
            self.footer.set_primary(screen.next_text() or i18n.t("ui.done" if last else "ui.next"), self.next_button.role())
            self.next_button.setEnabled(bool(screen.next_enabled()) and not busy)
            if not self._status_override and not busy:
                self.footer.set_status(screen.hint())
        self.back_button.setEnabled(not busy)
        if screen is None:
            self.next_button.setEnabled(not busy)
        if busy:
            self.cancel_button.setText(i18n.t("ui.cancel"))
        else:
            self.footer.set_cancel_mode(self.footer.cancel_mode)

    # -- keys and closing -------------------------------------------------------------------------

    def _primary_clicked(self):
        if self._primary_handler is not None:
            self._primary_handler()
        elif self.screens:
            self.next()

    def cancel_clicked(self):
        """The left button: cancels the running task, else closes the window."""
        if self.is_busy():
            self.cancel_task()
        else:
            self.reject()

    def cancel_task(self):
        if self._runner is not None and self._runner.is_running() and self._cancellable:
            self._runner.cancel()
            self.footer.progress.text.setFullText(i18n.t("ui.cancelling"))
            return True
        return False

    def can_close(self):
        """False while a task runs (cancel it first). Override to add more reasons; a window may also ask the operator."""
        return not self.is_busy()

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key_Escape:
            event.accept()
            if self.is_busy():
                self.cancel_task()
            else:
                self.reject()
            return
        if key in (Qt.Key_Return, Qt.Key_Enter):
            focus = QtWidgets.QApplication.focusWidget()
            if isinstance(focus, (QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit)):
                super(ToolWindow, self).keyPressEvent(event)
                return
            event.accept()
            if isinstance(focus, QtWidgets.QAbstractButton) and focus.isEnabled() and focus is not self.next_button:
                focus.click()
            elif self.next_button.isVisible() and self.next_button.isEnabled():
                self.next_button.click()
            return
        super(ToolWindow, self).keyPressEvent(event)

    def reject(self):
        if self.can_close():
            super(ToolWindow, self).reject()

    def closeEvent(self, event):
        if not self.can_close():
            event.ignore()
            self.cancel_task()
            return
        super(ToolWindow, self).closeEvent(event)

    # -- messages ---------------------------------------------------------------------------------

    def show_banner(self, kind, text, title=None, action=None, details=None, closable=None):
        """Show a message above the footer: kind = info / success / warning / danger; action = (text, callback)."""
        self.banner.show_message(kind, text, title, action, details, closable)

    def clear_banner(self):
        if self.banner.isVisible():
            self.banner.aboutToChange.emit()
            self.banner.hide()

    def show_error(self, exc, retry=None, title=None, trace=""):
        """The single error path: a danger banner with the plain message, a Details part and, if `retry` is given, a Retry button."""
        message, details = describe(exc, trace)
        self.show_banner("danger", message, title or i18n.t("ui.task_failed"),
                         (i18n.t("ui.retry"), retry) if retry is not None else None, details or None, True)

    # -- busy and tasks ---------------------------------------------------------------------------

    def is_busy(self):
        return self._busy > 0

    def set_busy(self, on):
        """Mark the window busy for work it runs itself (prefer `run`). Calls nest."""
        self._busy = max(0, self._busy + (1 if on else -1))
        self.footer.show_progress(self.is_busy())
        self.refresh_buttons()

    def runner(self):
        if self._runner is None:
            self._runner = TaskRunner(self)
        return self._runner

    def run(self, func, mode="threaded", name="", cancellable=True, on_done=None, on_error=None, on_cancel=None, retry=True,
            indeterminate=False, success=None, title=None):
        """Run `func(progress)` as the window's task: the footer shows a progress row (and Cancel), the buttons are disabled, Esc cancels.

        On success `on_done(result)` is called (and `success` text shown as a success banner); on failure the danger banner appears with
        Retry (unless `retry=False`) and `on_error(exc)` is called; on cancel `on_cancel()` is called. `mode='cooperative'` for Metashape work.
        Returns False if a task already runs."""
        runner = self.runner()
        if runner.is_running():
            return False
        self.clear_banner()
        row = self.footer.progress
        row.reset()
        row.set_cancellable(False)                         # the left button cancels; the row only reports
        if indeterminate:
            row.set_indeterminate("")
        self._last_run = dict(func=func, mode=mode, name=name, cancellable=cancellable, on_done=on_done, on_error=on_error,
                              on_cancel=on_cancel, retry=retry, indeterminate=indeterminate, success=success, title=title)

        after = []                                         # callbacks run once the window is idle again (a new job may start from them)

        def progressed(fraction, text):
            if not indeterminate:
                row.set_progress(fraction, text if text else None)
            elif text:
                row.text.setFullText(text)

        def finished(result):
            row.set_state("done")
            if success:
                self.show_banner("success", success)
            if on_done is not None:
                after.append(lambda: on_done(result))

        def failed(exc):
            row.set_state("error")
            self.show_error(exc, self.retry_last if retry else None, title, runner.trace)
            if on_error is not None:
                after.append(lambda: on_error(exc))

        def cancelled():
            row.set_state("cancelled", i18n.t("ui.cancelled"))
            if on_cancel is not None:
                after.append(on_cancel)

        def ended():
            for signal, slot in ((runner.progressed, progressed), (runner.finished, finished), (runner.failed, failed),
                                 (runner.cancelled, cancelled), (runner.ended, ended)):
                signal.disconnect(slot)
            self._busy = max(0, self._busy - 1)
            self._cancellable = True
            self.footer.show_progress(False)
            self.cancel_button.setEnabled(True)
            self.refresh_buttons()
            for callback in after:                         # only now: a job started here must not hear the end of this one
                callback()
            if after and not self.is_busy():               # what a callback enabled or disabled is read again
                self.refresh_buttons()

        runner.progressed.connect(progressed)
        runner.finished.connect(finished)
        runner.failed.connect(failed)
        runner.cancelled.connect(cancelled)
        runner.ended.connect(ended)
        self._busy += 1
        self._cancellable = bool(cancellable)
        self.footer.show_progress(True)
        self.refresh_buttons()
        self.cancel_button.setEnabled(bool(cancellable))
        return runner.start(func, name, mode)

    def retry_last(self):
        """Run the last task again (the Retry button of the error banner)."""
        if self._last_run is not None and not self.is_busy():
            self.run(**self._last_run)

    # -- geometry ---------------------------------------------------------------------------------

    @classmethod
    def open(cls, parent=None, *args, **kwargs):
        """The single-instance entry point: raise the window if it exists, else build it. Returns the window."""
        window = _INSTANCES.get(cls)
        if window is not None and _alive(window):
            window.present()
            return window
        window = cls(parent, *args, **kwargs)
        _INSTANCES[cls] = window
        window.destroyed.connect(lambda *_a, c=cls: _INSTANCES.pop(c, None))
        window.present()
        return window

    @classmethod
    def forget(cls):
        """Drop the remembered instance (tests)."""
        _INSTANCES.pop(cls, None)

    def present(self):
        """Show, raise and focus the window (also when it is minimised)."""
        present(self)

    def _banner_need(self):
        if self.banner.isHidden():
            return 0
        return self.banner.heightForWidth(max(px(100), self.root.width() - 2 * px(tokens.WINDOW_MARGIN))) + px(tokens.L)

    def _banner_changing(self):
        self._banner_state = None                      # Qt is about to resize for the banner: do not take that size as the window's own

    def _sync_banner_height(self):
        """A banner appearing, changing or going away moves the window height by exactly its own height (a wrapped text is measured at
        the real width), so the window neither clips the banner nor stays taller than it needs once the banner is gone."""
        if not self.isVisible() or not self._fitted:
            return
        need = self._banner_need()
        self._banner_h = need
        self._banner_state = self.banner.isVisible()
        if self._design:
            return
        room_h = screen_area(self).height() - px(tokens.SCREEN_MARGIN)
        target = min(room_h, self._h_base + need)
        if target != self.height():
            self.setMinimumHeight(0)                       # the minimum of the old content must not hold the window up
            self.resize(self.width(), target)
            self.layout().invalidate()                     # Qt sets the new minimum from what is left
            area = screen_area(self)
            frame = self.frameGeometry()
            if frame.bottom() > area.bottom() or frame.top() < area.top():          # a taller window must stay on the screen
                self.move(frame.left(), max(area.top(), min(frame.top(), area.bottom() - frame.height() + 1)))

    def resizeEvent(self, event):
        super(ToolWindow, self).resizeEvent(event)
        if self._fitted and self._banner_state is not None and self.banner.isVisible() == self._banner_state:
            self._h_base = self.height() - self._banner_h        # what the window is without its banner (the user may resize it)

    def fit_to_content(self):
        """Size the window: its design size or its content size, never smaller than the content needs, never larger than the screen."""
        self.ensurePolished()
        self.layout().activate()
        area = screen_area(self)
        room_w, room_h = area.width() - px(tokens.SCREEN_MARGIN), area.height() - px(tokens.SCREEN_MARGIN)
        need = self.layout().totalMinimumSize()
        if self._design:
            width, height = px(self._design[0]), px(self._design[1])
        else:
            hint = self.sizeHint()
            width, height = max(hint.width(), px(tokens.MIN_WINDOW_W)), hint.height()
        width = min(max(width, need.width()), room_w)
        height = min(max(height, need.height()), room_h)
        if self.layout().hasHeightForWidth():
            wanted = self.layout().totalHeightForWidth(width)        # wrapped texts measured at the real width, not the narrowest
            height = min(max(height if self._design else wanted, self.layout().minimumHeightForWidth(width)), room_h)
        self.resize(width, height)
        frame = QtCore.QRect(0, 0, width, height)
        frame.moveCenter(area.center())
        self.move(max(area.left(), frame.left()), max(area.top(), frame.top()))
        self.fits_screen = need.width() <= room_w and need.height() <= room_h
        self._banner_h = self._banner_need()
        self._banner_state = self.banner.isVisible()
        self._h_base = self.height() - self._banner_h

    def showEvent(self, event):
        style.apply(self.root)
        if not self._fitted:
            self._fitted = True
            self.fit_to_content()
            self.refresh_buttons()
            self.fix_tab_order()
            self.focus_initial()
        super(ToolWindow, self).showEvent(event)

    def focus_initial(self):
        """Put the keyboard focus on the first field of the current page (not on a button or an "i"), else on the primary button."""
        scope = self.stack.currentWidget()
        for widget in (focus_chain(scope) if scope is not None else []):
            if not isinstance(widget, (InfoButton, IconButton, QtWidgets.QAbstractButton)) or isinstance(widget, Toggle):
                widget.setFocus(Qt.MouseFocusReason)
                return
        if self.next_button.isEnabled():
            self.next_button.setFocus(Qt.MouseFocusReason)

    def fix_tab_order(self):
        """Tab goes through the controls in visual order: header, steps, current page, banner, footer."""
        chain = focus_chain(self.root)
        for first, second in zip(chain, chain[1:]):
            QtWidgets.QWidget.setTabOrder(first, second)


# --------------------------------------------------------------------------------------------------
# DockShell
# --------------------------------------------------------------------------------------------------

class DockShell(QtWidgets.QDockWidget):
    """The same language for a dock panel: header (mark, title, "i", optional close X), content, banner, optional footer.

    Put content in `body` (a QVBoxLayout, 16 px margins, 12 px spacing). `banner`, `status` (muted line under the content) and
    `add_footer()` (a `FooterBar` with only the primary button) are there when needed."""

    def __init__(self, title="", info="", parent=None, closable=True, mark="mark", min_width=360):
        super(DockShell, self).__init__(title, parent)
        self.setObjectName("dock_" + (title or "panel").replace(" ", "_"))
        self.setFeatures(QtWidgets.QDockWidget.DockWidgetClosable if closable else QtWidgets.QDockWidget.NoDockWidgetFeatures)
        self.root = QtWidgets.QWidget()
        self.root.setAttribute(Qt.WA_StyledBackground, True)
        style.apply(self.root)
        outer = vbox((tokens.WINDOW_MARGIN,) * 4, tokens.M, parent=self.root)
        self.header = Header(title, "", info, mark)
        if closable:
            self.close_button = IconButton("cross", i18n.t("ui.close"), self.close)
            self.header.right.addWidget(self.close_button)
        outer.addWidget(self.header)
        self.body = vbox(spacing=tokens.M)
        outer.addLayout(self.body, 1)
        self.banner = Banner("info", "")
        self.banner.hide()
        outer.addWidget(self.banner)
        self.status = ElidedLabel("", "small", "muted")
        self.status.hide()
        outer.addWidget(self.status)
        self.footer = None
        self._outer = outer
        self.setTitleBarWidget(QtWidgets.QWidget())          # the header inside the panel is the title: hide the native bar
        self.setWidget(self.root)
        self.setMinimumWidth(px(min_width))

    def add_footer(self):
        """A footer with the primary button only (Cancel / Back hidden); returns the `FooterBar`."""
        if self.footer is None:
            self._outer.addWidget(Divider())
            self.footer = FooterBar()
            self.footer.cancel_button.hide()
            self.footer.back_button.hide()
            self._outer.addWidget(self.footer)
        return self.footer

    def set_status(self, text, tone=None):
        self.status.setFullText(text)
        set_tone(self.status, tone)
        self.status.setVisible(bool(text))

    def show_banner(self, kind, text, title=None, action=None, details=None, closable=None):
        self.banner.show_message(kind, text, title, action, details, closable)

    def clear_banner(self):
        self.banner.hide()
