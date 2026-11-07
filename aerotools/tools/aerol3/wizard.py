"""Aero-L3: the wizard window. One window, four screens, a step bar on top, Cancel / Back / Next at the bottom.

The window talks only to a `Backend` (`backend.py`): `FakeBackend` in tests and screenshots, `msl3.MetashapeL3Backend` inside Metashape. A long
call runs through `aerofacade.tasks.Task` and the window watches it with a timer: the scan of the folder always runs on a worker thread (it only
reads files); the backend's own work runs on the main thread when the backend says `threaded = False` (Metashape's rule), and Qt keeps alive
through the backend's `pump()`.
"""

import os

from ... import i18n, journal, theme, ui
from ...qt import QtCore, QtGui, QtWidgets
from ..aeroexports import controls
from ..aerofacade.tasks import Task, plain_error
from . import screens, theme_l3
from . import state as S

POLL_MS = 100
WIDTH, HEIGHT = 1100, 760
SCREEN_MARGIN = 70


def tr(key, **fields):
    return i18n.t(key, **fields)


class StepBar(QtWidgets.QWidget):
    """The numbered steps, the current one filled green, the finished ones ticked."""

    def __init__(self, keys):
        super(StepBar, self).__init__()
        self._keys = list(keys)
        grid = QtWidgets.QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        self.circles, self.names = [], []
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
                line.setObjectName("l3StepLine")
                line.setFrameShape(QtWidgets.QFrame.NoFrame)
                grid.addWidget(line, 0, index * 2 + 1)
                grid.setColumnMinimumWidth(index * 2 + 1, 18)
        self.set_current(0)

    def set_current(self, current):
        self.current = current
        for index, (circle, name) in enumerate(zip(self.circles, self.names)):
            kind = "Current" if index == current else ("Done" if index < current else "Todo")
            circle.setObjectName("l3Step" + kind)
            name.setObjectName("l3StepName" + kind)
            circle.setText("✓" if kind == "Done" else str(index + 1))
            for widget in (circle, name):
                widget.style().unpolish(widget)
                widget.style().polish(widget)

    def state_of(self, index):
        return self.circles[index].objectName().replace("l3Step", "").lower()


class L3Wizard(QtWidgets.QDialog):
    """The Aero-L3 window."""

    def __init__(self, backend, parent=None):
        super(L3Wizard, self).__init__(parent)
        self.backend = backend
        self.state = S.WizardState()
        S.load_prefs(self.state)
        self._task = None
        self._on_progress = None
        self._on_done = None
        self._closing = False
        self._step = S.PHOTOS
        self.setWindowTitle(tr("l3.ui.title"))
        self.setModal(False)
        self.resize(*self._size())
        root = ui.dark_root(self)
        pad = theme.PAD_L
        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(pad, pad, pad, pad)
        layout.setSpacing(pad)

        head = QtWidgets.QHBoxLayout()
        head.setSpacing(pad)
        head.addWidget(ui.label(tr("l3.ui.title"), "aeroTitle", wrap=False))
        self.project_label = controls.ElidedLabel("aeroMeta")
        head.addWidget(self.project_label, 1)
        layout.addLayout(head)
        self.screens = [screens.PhotosScreen(self), screens.ChecksScreen(self), screens.ProjectScreen(self), screens.RunScreen(self)]
        self.steps = StepBar([cls.name_key for cls in (type(s) for s in self.screens)])
        layout.addWidget(self.steps)
        rule = QtWidgets.QFrame()
        rule.setFixedHeight(1)
        rule.setStyleSheet("background: {}; border: none;".format(theme.BORDER_SOFT))
        layout.addWidget(rule)
        self.stack = QtWidgets.QStackedWidget()
        layout.addWidget(self.stack, 1)
        for screen in self.screens:
            self.stack.addWidget(screen)

        foot = QtWidgets.QHBoxLayout()
        foot.setSpacing(theme.PAD)
        self.cancel_button = ui.big_button("l3.ui.cancel", self._on_cancel, "aeroBtn", width=120)
        foot.addWidget(self.cancel_button)
        self.status = controls.ElidedLabel("aeroMeta")
        foot.addWidget(self.status, 1)
        self.back_button = ui.big_button("l3.ui.back", self.back, "aeroBtn", width=120)
        self.next_button = ui.big_button("l3.ui.next", self.next, "aeroGo", width=150)
        foot.addWidget(self.back_button)
        foot.addWidget(self.next_button)
        layout.addLayout(foot)

        root.setStyleSheet(theme.stylesheet() + theme_l3.stylesheet())
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)
        self._load_project()
        self.go(S.PHOTOS, force=True)

    # -- size -------------------------------------------------------------------------------------

    @staticmethod
    def _size(factor=1.0):
        area = controls.screen_area()
        return (min(int(WIDTH * factor), area.width() - SCREEN_MARGIN), min(int(HEIGHT * factor), area.height() - SCREEN_MARGIN))

    def fit_to_fonts(self):
        """Open size: 1100 x 760 with the normal text size, bigger when the fonts are bigger (display scaling), never more than the screen."""
        self.ensurePolished()
        for child in self.findChildren(QtWidgets.QWidget):
            child.ensurePolished()
        factor = max(1.0, self.project_label.fontMetrics().height() / 16.0)
        self.resize(*self._size(factor))

    def showEvent(self, event):
        super(L3Wizard, self).showEvent(event)
        if not getattr(self, "_fitted", False):
            self._fitted = True
            controls.fit_window(self, margin=SCREEN_MARGIN)

    def keyPressEvent(self, event):
        if event.key() in (QtCore.Qt.Key_Escape, QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            event.ignore()                                  # Esc would close the window, Enter would press a random button
            return
        super(L3Wizard, self).keyPressEvent(event)

    # -- the project ------------------------------------------------------------------------------

    def _load_project(self):
        try:
            info = dict(self.backend.project_info() or {})
        except Exception as exc:
            info = {}
            journal.write("Aero-L3: project_info: {!r}".format(exc))
        self.info = info
        project = info.get("project") or tr("l3.ui.unsaved_project")
        self.project_label.setFullText(tr("l3.ui.project_line", project=project))

    # -- dialogs ----------------------------------------------------------------------------------

    def pick_folder(self, start):
        """Ask for a folder (the tests replace this method). Returns the path or ''."""
        return QtWidgets.QFileDialog.getExistingDirectory(self, tr("l3.ui.pick_title"), start) or ""

    def open_folder(self, folder):
        if folder and os.path.isdir(folder):
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(folder))

    # -- jobs -------------------------------------------------------------------------------------

    def busy(self):
        return self._task is not None

    def run_job(self, func, on_progress, on_done, name="", threaded=True):
        """Start one long call. `on_progress(task)` is called by the timer while it runs, `on_done(task)` once when it ends (also when it failed or
        was cancelled: see `task.error` / `task.was_cancelled`). One job at a time."""
        if self._task is not None:
            return False
        task = Task(func, name)
        self._task, self._on_progress, self._on_done = task, on_progress, on_done
        self.refresh_buttons()
        self._timer.start()
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
            journal.write("Aero-L3: {} failed: {!r}\n{}".format(task.name, task.error, task.trace))
        self.bring_to_front()
        try:
            if done is not None:
                done(task)
        finally:
            self.refresh_buttons()
            if self._closing:
                self.close()

    def bring_to_front(self):
        """Put the window back above Metashape's main window after one of its long operations (nothing happens when another application is in front)."""
        app = QtWidgets.QApplication.instance()
        if self.isVisible() and not self._closing and app is not None and app.activeWindow() is not None:
            self.raise_()
            self.activateWindow()

    def cancel_task(self):
        if self._task is not None:
            self._task.cancel()
            self.status.setFullText(tr("l3.ui.cancelling"))

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
        step = max(S.PHOTOS, min(S.RUN, step))
        if not force and (step == self._step or self.busy()):
            return
        if not force and not self.screens[self._step].leave(step > self._step):
            return
        self._step = step
        self.stack.setCurrentIndex(step)
        self.steps.set_current(step)
        self.screens[step].enter()
        self.refresh_buttons()
        self._keep_on_screen()

    def _keep_on_screen(self):
        """A screen never leaves the window taller or wider than the display."""
        area = controls.screen_area()
        width, height = min(self.width(), area.width() - SCREEN_MARGIN), min(self.height(), area.height() - SCREEN_MARGIN)
        if (width, height) != (self.width(), self.height()):
            self.resize(width, height)

    def next(self):
        screen = self.screen()
        if self.busy() or not screen.next_enabled():
            return
        if not screen.leave(True):
            return
        self.go(screen.next_step(), force=True)

    def back(self):
        screen = self.screen()
        if self.busy():
            return
        if not screen.leave(False):
            return
        self.go(screen.prev_step(), force=True)

    def refresh_buttons(self):
        screen = self.screens[self._step] if 0 <= self._step < len(self.screens) else None
        busy = self.busy() or (screen is not None and screen.busy())
        self.back_button.setEnabled(self._step > S.PHOTOS and not busy)
        self.back_button.setVisible(screen.show_back() if screen is not None else False)
        if screen is not None:
            self.next_button.setVisible(screen.show_next())
            self.next_button.setText(screen.next_text())
            self.next_button.setEnabled(screen.next_enabled() and not busy)
        leaving = (not busy) and self._step == S.RUN
        self.cancel_button.setText(tr("l3.ui.close") if leaving else tr("l3.ui.cancel"))
        self.status.setFullText("")

    def _on_cancel(self):
        if self.busy():
            self.cancel_task()
        else:
            self.close()

    # -- closing ----------------------------------------------------------------------------------

    def closeEvent(self, event):
        if self._task is not None:
            self._task.cancel()
            self._closing = True
            self.status.setFullText(tr("l3.ui.cancelling"))
            if not self._task.done:
                event.ignore()                              # the job is told to stop: the window closes when it has
                return
            self._task, self._on_progress, self._on_done = None, None, None      # it ended between two polls: nobody is left to hear of it
        self._timer.stop()
        try:
            S.save_prefs(self.state)
        except Exception:
            pass
        super(L3Wizard, self).closeEvent(event)
