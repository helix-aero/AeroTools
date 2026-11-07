"""Aero-Facade: the wizard window. One wide window, seven screens, a step bar on top, Back / Next / Cancel at the bottom.

The window talks only to a `Backend` (see `backend.py`): the `FakeBackend` in tests and screenshots, `msfacade.MetashapeBackend` inside
Metashape. Long backend calls run on a worker thread (`tasks.Task`); the window watches them with a timer and never lets the backend touch
a widget. The rules that enable the buttons are in `state.py`.
"""

import os

from ... import i18n, journal, theme, ui
from ...qt import QtCore, QtGui, QtWidgets
from ..aeroexports import controls
from . import state as S
from . import theme_facade
from .common import tr
from .screens_build import BuildScreen
from .screens_export import ExportScreen, PicturesScreen
from .screens_margins import MarginsScreen, MethodScreen
from .screens_search import FacadesScreen, SearchScreen
from .tasks import Task, plain_error
from .widgets import OrthoCanvas

POLL_MS = 100
WIDTH, HEIGHT = 1280, 800
SCREEN_MARGIN = 70
PICTURE_PX = 1600


class StepBar(QtWidgets.QWidget):
    """The numbered steps (seven), the current one filled green, the finished ones ticked."""

    def __init__(self, keys):
        super(StepBar, self).__init__()
        self._keys = list(keys)
        grid = QtWidgets.QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        self.circles, self.names, self.lines = [], [], []
        for index, key in enumerate(self._keys):
            circle = QtWidgets.QLabel(str(index + 1))
            circle.setFixedSize(30, 30)
            circle.setAlignment(QtCore.Qt.AlignCenter)
            name = QtWidgets.QLabel(tr(key))
            name.setAlignment(QtCore.Qt.AlignCenter)
            name.setWordWrap(True)
            grid.addWidget(circle, 0, index * 2, QtCore.Qt.AlignCenter)
            grid.addWidget(name, 1, index * 2)
            grid.setColumnStretch(index * 2, 1)
            self.circles.append(circle)
            self.names.append(name)
            if index < len(self._keys) - 1:
                line = QtWidgets.QFrame()
                line.setObjectName("facadeStepLine")
                line.setFrameShape(QtWidgets.QFrame.NoFrame)
                grid.addWidget(line, 0, index * 2 + 1)
                grid.setColumnMinimumWidth(index * 2 + 1, 18)
                self.lines.append(line)
        self.set_current(0)

    def set_current(self, current):
        self.current = current
        for index, (circle, name) in enumerate(zip(self.circles, self.names)):
            kind = "Current" if index == current else ("Done" if index < current else "Todo")
            circle.setObjectName("facadeStep" + kind)
            name.setObjectName("facadeStepName" + kind)
            circle.setText("✓" if kind == "Done" else str(index + 1))
            for widget in (circle, name):
                widget.style().unpolish(widget)
                widget.style().polish(widget)

    def state_of(self, index):
        return self.circles[index].objectName().replace("facadeStep", "").lower()


class ChoiceDialog(QtWidgets.QDialog):
    """A message with big buttons; `choice` is the key of the one pressed (None when it was closed)."""

    def __init__(self, parent, title, text, buttons):
        super(ChoiceDialog, self).__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(520)
        self.choice = None
        root = ui.dark_root(self)
        layout = QtWidgets.QVBoxLayout(root)
        pad = theme.PAD_L
        layout.setContentsMargins(pad, pad, pad, pad)
        layout.setSpacing(pad)
        layout.addWidget(ui.label(title, "facadeBig"))
        layout.addWidget(ui.label(text))
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(theme.PAD)
        self.buttons = {}
        for index, (key, text_key) in enumerate(buttons):
            button = ui.big_button(text_key, lambda k=key: self._pick(k), "aeroGo" if index == len(buttons) - 1 else "aeroBtn", width=150)
            self.buttons[key] = button
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)

    def _pick(self, key):
        self.choice = key
        self.accept()


class FacadeWizard(QtWidgets.QDialog):
    """The Aero-Facade window."""

    def __init__(self, backend, parent=None):
        super(FacadeWizard, self).__init__(parent)
        self.backend = backend
        self.state = S.WizardState()
        S.load_prefs(self.state)
        self.info = {}
        self._task = None
        self._on_progress = None
        self._on_done = None
        self._closing = False
        self._step = S.SEARCH
        self._picture_error = ""
        self.setWindowTitle(tr("facade.ui.title"))
        self.setModal(False)
        self.setWindowFlag(QtCore.Qt.MSWindowsFixedSizeDialogHint, True)             # no dragging of the edges: the size follows the content
        self.resize(*self._size())
        root = ui.dark_root(self)
        pad = theme.PAD_L
        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(pad, pad, pad, pad)
        layout.setSpacing(pad)

        head = QtWidgets.QHBoxLayout()
        head.setSpacing(pad)
        title = ui.label(tr("facade.ui.title"), "aeroTitle", wrap=False)
        head.addWidget(title)
        self.chunk_label = controls.ElidedLabel("aeroMeta")
        head.addWidget(self.chunk_label, 1)
        layout.addLayout(head)
        self.steps = StepBar([cls.name_key for cls in (SearchScreen, FacadesScreen, MarginsScreen, MethodScreen, BuildScreen, PicturesScreen,
                                                       ExportScreen)])
        layout.addWidget(self.steps)
        rule = QtWidgets.QFrame()
        rule.setFixedHeight(1)
        rule.setStyleSheet("background: {}; border: none;".format(theme.BORDER_SOFT))
        layout.addWidget(rule)

        self.canvas = OrthoCanvas()
        self.stack = controls.CappedStack()
        layout.addWidget(self.stack, 1)
        self.screens = [SearchScreen(self), FacadesScreen(self), MarginsScreen(self), MethodScreen(self), BuildScreen(self),
                        PicturesScreen(self), ExportScreen(self)]
        for screen in self.screens:
            self.stack.addWidget(screen)

        foot = QtWidgets.QHBoxLayout()
        foot.setSpacing(theme.PAD)
        self.cancel_button = ui.big_button("facade.ui.cancel", self._on_cancel, "aeroBtn", width=120)
        foot.addWidget(self.cancel_button)
        self.status = controls.ElidedLabel("aeroMeta")
        foot.addWidget(self.status, 1)
        self.back_button = ui.big_button("facade.ui.back", self.back, "aeroBtn", width=120)
        self.next_button = ui.big_button("facade.ui.next", self.next, "aeroGo", width=150)
        foot.addWidget(self.back_button)
        foot.addWidget(self.next_button)
        layout.addLayout(foot)

        # the styles are set once everything exists (set earlier, Qt leaves the widgets created afterwards half styled)
        root.setStyleSheet(theme.stylesheet() + theme_facade.stylesheet())
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)
        self._load_project()
        self.go(S.SEARCH, force=True)
        self.fit_to_fonts()

    # -- size -------------------------------------------------------------------------------------

    @staticmethod
    def _size(factor=1.0):
        area = controls.screen_area()
        return (min(int(WIDTH * factor), area.width() - SCREEN_MARGIN), min(int(HEIGHT * factor), area.height() - SCREEN_MARGIN))

    def fit_to_fonts(self):
        """Open size: 1280 x 800 with the normal text size, bigger when the fonts are bigger (display scaling), never more than the
        screen. Call it before `show()`."""
        self.ensurePolished()
        for child in self.findChildren(QtWidgets.QWidget):
            child.ensurePolished()
        factor = max(1.0, self.chunk_label.fontMetrics().height() / 16.0)
        self.resize(*self._size(factor))

    def showEvent(self, event):
        super(FacadeWizard, self).showEvent(event)
        if not getattr(self, "_fitted", False):
            self._fitted = True
            controls.fit_window(self, margin=SCREEN_MARGIN)

    def keyPressEvent(self, event):
        if event.key() in (QtCore.Qt.Key_Escape, QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            event.ignore()                                  # Esc would close the window, Enter would press a random button
            return
        super(FacadeWizard, self).keyPressEvent(event)

    # -- the project ------------------------------------------------------------------------------

    def _load_project(self):
        try:
            self.info = dict(self.backend.project_info() or {})
        except Exception as exc:
            self.info = {}
            journal.write("Aero-Facade: project_info: {!r}".format(exc))
        text = tr("facade.ui.chunk_line", chunk=self.info.get("chunk", ""), crs=self.info.get("crs", "")) if self.info else ""
        self.chunk_label.setFullText(text)
        try:
            self.state.availability = self.read_availability()
        except Exception as exc:
            journal.write("Aero-Facade: availability: {!r}".format(exc))
        self.canvas.set_empty_text(tr("facade.ui.canvas_loading"))
        QtCore.QTimer.singleShot(0, self._load_picture)

    def read_availability(self):
        from .model import Availability
        return Availability.from_dict(self.backend.availability())

    def _load_picture(self):
        backend = self.backend
        started = self.run_job(lambda progress, cancelled: backend.ortho_preview(PICTURE_PX), None, self._picture_done, "picture")
        if not started:
            QtCore.QTimer.singleShot(200, self._load_picture)

    def _picture_done(self, task):
        if task.error is not None or not task.result:
            self._picture_error = tr("facade.ui.picture_failed", reason=plain_error(task.error) if task.error else tr("facade.ui.failed_default"))
            self.canvas.set_empty_text(self._picture_error)
            self.refresh_buttons()
            return
        rgb, extent = task.result
        self.canvas.set_picture(rgb, extent)
        search = self.screens[S.SEARCH]
        search.picture_ready()
        self.refresh_buttons()

    # -- jobs -------------------------------------------------------------------------------------

    def busy(self):
        return self._task is not None

    def run_job(self, func, on_progress, on_done, name="", items=None, extras=None):
        """Start one long backend call on a worker thread. `on_progress(task)` is called by the timer while it runs, `on_done(task)` once
        when it ends (also when it failed or was cancelled: look at `task.error` / `task.was_cancelled`). One job at a time."""
        if self._task is not None:
            return False
        task = Task(func, name, items=items, extras=extras)
        self._task, self._on_progress, self._on_done = task, on_progress, on_done
        self.refresh_buttons()
        threaded = getattr(self.backend, "threaded", True)
        self._timer.start()                                 # before the call: a backend on the main thread lets Qt run while it works
        task.start(threaded=bool(threaded))
        if not threaded:
            self._poll()
        return True

    def _poll(self):
        task = self._task
        if task is None:
            self._timer.stop()
            return
        if not task.done:
            if self._on_progress is not None:
                self._on_progress(task)
            return
        done = self._on_done
        self._task, self._on_progress, self._on_done = None, None, None
        self._timer.stop()
        if task.error is not None:
            journal.write("Aero-Facade: {} failed: {!r}\n{}".format(task.name, task.error, task.trace))
        self.bring_to_front()
        try:
            if done is not None:
                done(task)
        finally:
            self.refresh_buttons()
            if self._closing:
                self.close()

    def bring_to_front(self):
        """Put the window back above Metashape's main window. Metashape's own long operations (and its progress dialog) can leave
        the main window on top, and the wizard would then look gone. Nothing happens when another application is in front."""
        app = QtWidgets.QApplication.instance()
        if self.isVisible() and not self._closing and app is not None and app.activeWindow() is not None:
            self.raise_()
            self.activateWindow()

    def cancel_task(self):
        if self._task is not None:
            self._task.cancel()
            self.status.setFullText(tr("facade.ui.cancelling"))

    def wait_idle(self, timeout_ms=15000):
        """Let the window finish the running job (tests, closing). True when no job is left."""
        import time
        end = time.time() + timeout_ms / 1000.0
        app = QtWidgets.QApplication.instance()
        while self._task is not None and time.time() < end:
            app.processEvents()
            self._poll()
            time.sleep(0.01)
        return self._task is None

    # -- navigation -------------------------------------------------------------------------------

    def current_step(self):
        return self._step

    def screen(self):
        return self.screens[self._step]

    def go(self, step, force=False):
        step = max(S.SEARCH, min(S.EXPORT, step))
        if not force and step == self._step:
            return
        if not force and self.busy():
            return
        if not force:
            if not self.screens[self._step].leave(step > self._step):
                return
        self._step = step
        self.stack.setCurrentIndex(step)
        self.steps.set_current(step)
        self.screens[step].enter()
        self.refresh_buttons()

    def next(self):
        screen = self.screen()
        if self.busy() or not screen.next_enabled():
            return
        if not screen.leave(True):
            return
        target = screen.next_step()
        self._step = -1                                   # leave() was done: go() must not do it twice
        self.go(target, force=True)

    def back(self):
        screen = self.screen()
        if self.busy():
            return
        if not screen.leave(False):
            return
        target = screen.prev_step()
        self.go(target, force=True)

    def refresh_buttons(self):
        screen = self.screens[self._step] if 0 <= self._step < len(self.screens) else None
        busy = self.busy() or (screen is not None and screen.busy())
        self.back_button.setEnabled(self._step > S.SEARCH and not busy)
        self.back_button.setVisible(self._step > S.SEARCH)
        if screen is not None:
            self.next_button.setVisible(screen.show_next())
            self.next_button.setText(screen.next_text())
            self.next_button.setEnabled(screen.next_enabled() and not busy)
        leaving = (not busy) and self._step == S.EXPORT
        self.cancel_button.setText(tr("facade.ui.cancel_job") if busy else (tr("facade.ui.close") if leaving else tr("facade.ui.cancel")))
        self.status.setFullText(tr("facade.ui.working") if busy else "")

    def _on_cancel(self):
        if self.busy():
            self.cancel_task()
        else:
            self.close()

    # -- dialogs ----------------------------------------------------------------------------------

    def ask(self, title, text, buttons):
        """A message with big buttons ((key, text key), ...): returns the key pressed (None if closed). Tests replace this method."""
        dialog = ChoiceDialog(self, title, text, buttons)
        ui.run(dialog)
        return dialog.choice

    def open_folder(self, folder):
        if folder and os.path.isdir(folder):
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(folder))

    # -- closing ----------------------------------------------------------------------------------

    def closeEvent(self, event):
        if self._task is not None:
            self._task.cancel()
            self._closing = True
            self.status.setFullText(tr("facade.ui.cancelling"))
            if not self._task.done:
                event.ignore()                              # the job is told to stop: the window closes when it has
                return
        self._timer.stop()
        super(FacadeWizard, self).closeEvent(event)
