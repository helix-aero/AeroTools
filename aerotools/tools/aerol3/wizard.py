"""Aero-L3: the wizard window. One `uikit.ToolWindow`, four screens, a step bar on top, Cancel / Back / Next at the bottom.

The window talks only to a `Backend` (`backend.py`): `FakeBackend` in tests and screenshots, `msl3.MetashapeL3Backend` inside Metashape. A long
call runs through `ToolWindow.run`: the scan of the folder always on a worker thread (it only reads files); the backend's own work on the
main thread when the backend says `threaded = False` (Metashape's rule), with Qt kept alive by the kit's cooperative runner.
"""

import os

from ... import i18n, journal, uikit
from ...qt import QtCore, QtGui, QtWidgets
from . import screens
from .runtime import Cancelled
from . import state as S


def tr(key, **fields):
    return i18n.t(key, **fields)


class Job(object):
    """How a long call ended, as the screens read it: `result`, `error` (an exception), `cancelled`."""

    def __init__(self, result=None, error=None, cancelled=False, trace=""):
        self.result, self.error, self.cancelled, self.trace = result, error, cancelled, trace


class L3Wizard(uikit.ToolWindow):
    """The Aero-L3 window."""
    MARK = "tool_l3"

    TITLE = "Aero-L3"
    DESIGN_SIZE = (900, 640)

    def __init__(self, backend, parent=None):
        super(L3Wizard, self).__init__(parent, title=tr("l3.ui.title"), wizard=True)
        self.backend = backend
        self.state = S.WizardState()
        S.load_prefs(self.state)
        self.info = {}
        self._closing = False
        self.runner().ended.connect(lambda: QtCore.QTimer.singleShot(0, self._after_task))
        for screen in (screens.PhotosScreen(self), screens.ChecksScreen(self), screens.ProjectScreen(self), screens.RunScreen(self)):
            self.add_screen(screen)
        self._load_project()
        self.go(S.PHOTOS, force=True)

    # -- the project ------------------------------------------------------------------------------

    def _load_project(self):
        try:
            info = dict(self.backend.project_info() or {})
        except Exception as exc:
            info = {}
            journal.write("Aero-L3: project_info: {!r}".format(exc))
        self.info = info
        project = info.get("project") or tr("l3.ui.unsaved_project")
        self.set_subtitle(tr("l3.ui.project_line", project=project))

    # -- dialogs ----------------------------------------------------------------------------------

    def pick_folder(self, start):
        """Ask for a folder (the tests replace this method). Returns the path or ''."""
        return QtWidgets.QFileDialog.getExistingDirectory(self, tr("l3.ui.pick_title"), start) or ""

    def open_folder(self, folder):
        if folder and os.path.isdir(folder):
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(folder))

    # -- jobs -------------------------------------------------------------------------------------

    def busy(self):
        return self.is_busy()

    def run_job(self, func, on_done, name="", threaded=True, on_progress=None, retry=True):
        """Start one long call: `func(progress, cancelled)` with the plain callbacks the backends know. `on_progress(fraction, text)` follows
        it, `on_done(job)` is called once when it ends (also when it failed or was cancelled: see `job.error` / `job.cancelled`). One job at
        a time; False when one runs."""
        runner = self.runner()

        def work(progress):
            def report(fraction, text=""):
                try:
                    progress(fraction, text)
                except uikit.Cancelled:
                    pass                                    # the backend polls `cancelled()` itself and stops in its own way
            try:
                return func(report, progress.is_cancelled)
            except Cancelled:
                raise uikit.Cancelled()                       # the backend's own stop is the kit's

        def finish(job):
            if on_progress is not None:
                try:
                    runner.progressed.disconnect(on_progress)
                except (TypeError, RuntimeError):
                    pass
            self.bring_to_front()
            if job.error is not None:
                journal.write("Aero-L3: {} failed: {!r}\n{}".format(name, job.error, job.trace))
            try:
                on_done(job)
            finally:
                self.refresh_buttons()

        if runner.is_running():
            return False
        if on_progress is not None:
            runner.progressed.connect(on_progress)
        started = self.run(work, mode="threaded" if threaded else "cooperative", name=name, retry=retry,
                           on_done=lambda result: finish(Job(result=result)),
                           on_error=lambda exc: finish(Job(error=exc, trace=runner.trace)),
                           on_cancel=lambda: finish(Job(result=runner.result, cancelled=True)))
        return started

    def bring_to_front(self):
        """Put the window back above Metashape's main window after one of its long operations (nothing happens when another application is in front)."""
        app = QtWidgets.QApplication.instance()
        if self.isVisible() and not self._closing and app is not None and app.activeWindow() is not None:
            self.raise_()
            self.activateWindow()

    def wait_idle(self, timeout_ms=15000):
        """Let the window finish the running job (tests, closing). True when no job is left."""
        if self.is_busy():
            loop = QtCore.QEventLoop()
            self.runner().ended.connect(loop.quit)              # connected after the window's own, so the busy count is down when it fires
            QtCore.QTimer.singleShot(timeout_ms, loop.quit)
            (getattr(loop, "exec", None) or loop.exec_)()
            try:
                self.runner().ended.disconnect(loop.quit)
            except (TypeError, RuntimeError):
                pass
        return not self.is_busy()

    # -- navigation -------------------------------------------------------------------------------

    def current_step(self):
        return max(0, self.stack.currentIndex())

    def screen(self):
        return self.current_screen()

    def refresh_buttons(self, *_args):
        index = self.stack.currentIndex()
        self.footer.cancel_mode = "close" if index == S.RUN else "cancel"
        super(L3Wizard, self).refresh_buttons()
        screen = self.current_screen()
        if screen is not None:
            self.next_button.setVisible(screen.show_next())

    def fix_tab_order(self):
        """Tab goes through the page, then through the "i" marks inside its scrolling list, then the footer."""
        chain = uikit.shell.focus_chain(self.root)
        screen = self.current_screen()
        extra = screen.extra_focus() if screen is not None else []
        if extra:
            inside = [w for w in chain if screen.isAncestorOf(w)]
            at = chain.index(inside[-1]) + 1 if inside else 0
            chain = chain[:at] + [w for w in extra if w not in chain] + chain[at:]
        for first, second in zip(chain, chain[1:]):
            QtWidgets.QWidget.setTabOrder(first, second)

    # -- closing ----------------------------------------------------------------------------------

    def can_close(self):
        return not self.is_busy()

    def closeEvent(self, event):
        if self.is_busy():
            self._closing = True                            # the job is told to stop: the window closes when it has
            self.cancel_task()
            event.ignore()
            return
        super(L3Wizard, self).closeEvent(event)

    def done(self, result):
        if self.is_busy():
            return
        try:
            S.save_prefs(self.state)
        except Exception:
            pass
        super(L3Wizard, self).done(result)

    def _after_task(self):
        if self._closing and not self.is_busy():
            self._closing = False
            self.close()
