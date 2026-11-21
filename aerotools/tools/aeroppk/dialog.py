"""Aero-PPK window (Qt): a wizard with six steps on the UI kit (`uikit.ToolWindow`: header, step bar, footer, banner).

  1 Drone   which drone took the photos (the device and its antenna)
  2 Files   the files of each flight, and which flights to compute
  3 Mode    where the base comes from: a public station, several stations or your own base
  4 Base    the stations of a public network (map and basket) or your own base files
  5 Result  the calculation, then a verdict and what to do next
  6 Export  what to write and whether to replace the positions in Metashape

Long work goes through the kit's task runner (`ToolWindow.run`, on a worker thread: nothing here touches Metashape from it, see msapi) and
reports to the window through queued signals. The rules of each step are in the pure modules (`state.py`, `basket.py`, `ownbase.py`,
`devices.py`, `outputs.py`, `summary.py`); the screens only draw them.
"""

import os
import time

from ... import msapi, uikit
from ...qt import QtCore, QtGui, QtWidgets
from ...uikit import tokens
from . import chunks, pipeline, state as S
from .exportprompt import ExportPrompt
from .screen_base import BaseScreen
from .screen_drone import DroneScreen
from .screen_export import ExportScreen
from .screen_files import FilesScreen
from .screen_mode import ModeScreen
from .screen_results import ResultsScreen
from .ui_common import OneAtATimeStack, tr

SECTION = "aeroppk"
DESIGN = (1180, 780)                       # the design size of the window (the kit clamps it to the screen it opens on)
CHUNK_POLL_MS = 1500                       # how often the active chunk of Metashape is looked at, while the window is shown


class _Modal(object):
    """While a question or a file dialog is open the window must not start again under it (the timers keep running)."""

    def __init__(self, dialog):
        self.dialog = dialog

    def __enter__(self):
        self.dialog._dialogs += 1

    def __exit__(self, *_exc):
        self.dialog._dialogs -= 1
        return False


class JobResult(object):
    """What a finished job hands to its `on_done`: `state` is done, failed or stopped; `result`, `error` (a sentence), `trace` (details)."""

    def __init__(self, tag=None):
        self.tag = tag
        self.state = "running"
        self.result = None
        self.error = ""
        self.trace = ""


class _Mailbox(QtCore.QObject):
    """What a worker posts reaches the window on the UI thread through this queued signal (no timer polls a job)."""
    message = QtCore.Signal(str, object)

    def __init__(self, handler, parent=None):
        super(_Mailbox, self).__init__(parent)
        self._handler = handler
        self.message.connect(self._deliver)

    def post(self, kind, fields):
        self.message.emit(kind, fields)

    def _deliver(self, kind, fields):
        self._handler(kind, fields)


def _error_text(exc):
    if isinstance(exc, pipeline.PipelineError):
        return str(exc)
    return "%s: %s" % (type(exc).__name__, exc)


class PpkDialog(uikit.ToolWindow):
    MARK = "tool_ppk"

    def __init__(self, parent=None):
        super(PpkDialog, self).__init__(parent, title=tr("aeroppk.title"), wizard=True, design_size=DESIGN)
        self._count_one_page()
        self.state = S.WizardState()
        self.pending_start = False
        self.stored = []                   # the solutions kept for comparison (memory only, for this window)
        self.rover_dirty = False
        self._side = []                    # small jobs that may overlap the exclusive one: (TaskRunner, on_done, JobResult)
        self._closing = False
        self._seen = False
        self.bound_key = msapi.chunk_key(msapi.chunk())      # the chunk this window works on (None: no chunk)
        self.bound_label = msapi.chunk_label(msapi.chunk())
        self._kept = None                  # active chunk of Metashape that was left alone to keep the results of the bound one
        self._rows_shown = None
        self._dialogs = 0                  # > 0 while a modal question is open: the window must not start again under it
        self._syncing = False
        self._build_chunk_bar()
        self.project_label = self.header.subtitle
        for screen in self._make_screens():
            self.add_screen(screen)
        self.chunk_timer = QtCore.QTimer(self)
        self.chunk_timer.setInterval(CHUNK_POLL_MS)
        self.chunk_timer.timeout.connect(self.sync_chunk)
        self._load_project()
        self._fill_chunks()
        self._polish()
        self.go(S.DRONE, force=True)

    def _count_one_page(self):
        """Only the step on show counts for the size of the window (the window keeps one size from step to step): the stack of the kit is
        replaced, before any step is added, by one that counts one page and hides the one that goes first."""
        old = self.stack
        stack = OneAtATimeStack()
        stack.currentChanged.connect(lambda _i: QtCore.QTimer.singleShot(0, self._sync_minimum))
        old.parentWidget().layout().replaceWidget(old, stack)
        old.hide()
        old.deleteLater()
        self.stack = stack

    def _polish(self):
        """Apply the style sheet to every widget now, the hidden ones too: a part that is shown later (a closed group, another page) would
        be measured unstyled at the moment it opens, and the window would grow for a moment past the screen."""
        self.ensurePolished()
        for widget in self.findChildren(QtWidgets.QWidget):
            widget.ensurePolished()

    def _build_chunk_bar(self):
        """The chunk the window works on, at the right of the header."""
        right = self.header.right
        right.setSpacing(uikit.px(tokens.S))
        right.addWidget(uikit.label(tr("aeroppk.chunk.caption"), "small", "muted", wrap=False))
        self.chunk_combo = uikit.ChoiceBox()
        self.chunk_combo.setMinimumWidth(uikit.px(tokens.POPOVER_W // 2))
        self.chunk_combo.setMaximumWidth(uikit.px(tokens.POPOVER_W))
        self.chunk_combo.activated.connect(self._on_chunk_picked)
        right.addWidget(self.chunk_combo)
        right.addWidget(uikit.InfoButton(tr("aeroppk.chunk.tip")))

    # -- window -----------------------------------------------------------------------------------

    def showEvent(self, event):
        super(PpkDialog, self).showEvent(event)
        self._seen = True
        self._closing = False
        self.chunk_timer.start()
        self._size_chunk_combo()
        self.sync_chunk()

    def hideEvent(self, event):
        self.chunk_timer.stop()
        super(PpkDialog, self).hideEvent(event)

    def changeEvent(self, event):
        super(PpkDialog, self).changeEvent(event)
        if event.type() == QtCore.QEvent.ActivationChange and self.isActiveWindow():
            self.sync_chunk()

    def present(self):
        """Raise the window; a window that is opened again first follows the active chunk."""
        if self._seen:
            self.refresh()
        super(PpkDialog, self).present()

    def refresh(self):
        """Called when the window is opened again: follow the active chunk, else read the present step again."""
        self._load_project()
        if not self.sync_chunk():
            self.screens[self._step].enter()

    def _load_project(self):
        path = msapi.document_path()
        name = os.path.splitext(os.path.basename(path))[0] if path else tr("aeroppk.unsaved")
        self.set_subtitle(tr("aeroppk.project", project=name))

    def project_name(self):
        """The name the files and the report carry: the project, and the chunk when the project has several (reports of two chunks must differ)."""
        project = os.path.splitext(os.path.basename(msapi.document_path() or ""))[0] or "Aero-PPK"
        if len(msapi.chunks()) > 1 and self.bound_name():
            project = "%s - %s" % (project, self.bound_name())
        return project

    def _modal(self):
        return _Modal(self)

    def pick_folder(self, start):
        """Ask for a folder (the tests replace this method). Returns the path or ''."""
        with self._modal():
            return QtWidgets.QFileDialog.getExistingDirectory(self, tr("aeroppk.rover.folder_title"), start) or ""

    def pick_file(self, title, filt, start):
        with self._modal():
            path, _f = QtWidgets.QFileDialog.getOpenFileName(self, title, start, filt)
        return path or ""

    def pick_files(self, title, filt, start):
        with self._modal():
            paths, _f = QtWidgets.QFileDialog.getOpenFileNames(self, title, start, filt)
        return list(paths or [])

    def confirm(self, text, ok=None, danger=False):
        with self._modal():
            return uikit.confirm(self, tr("aeroppk.title"), text, ok=ok, danger=danger)

    def ask_report_options(self, photos):
        """The question before the PDF report is written (the tests replace this method): True when the table of every photo goes in, False
        when it does not, None when the user gave up."""
        with self._modal():
            prompt = ExportPrompt(self, photos)
            return prompt.include() if prompt.ask() else None

    def open_folder(self, folder):
        if folder and os.path.isdir(folder):
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(folder))

    def output_folder(self):
        return self.screens[S.EXPORT].folder()

    # -- the chunk --------------------------------------------------------------------------------
    # The window works on ONE chunk, the active chunk of Metashape, found again by its key. Picking another one in the header makes it the
    # active chunk of Metashape; changing the active chunk in Metashape does the same here. Either way the window starts again (a new
    # state, new pages, the first step) and reads the new chunk. While a calculation or a download runs the bound chunk stays.

    def bound_chunk(self):
        """The Metashape chunk the window is bound to, looked up by key; None when there is none or it was removed."""
        return msapi.chunk_by_key(self.bound_key)

    def bound_name(self):
        chk = self.bound_chunk()
        return msapi.chunk_label(chk) if chk is not None else self.bound_label

    def chunk_locked(self):
        return self._closing or self.busy() or any(screen.busy() for screen in self.screens)

    def sync_chunk(self):
        """Follow the active chunk of Metashape (see `chunks.decide`). True when the window started again."""
        return self._sync(False)

    def _sync(self, after_job):
        if self._syncing or self._dialogs or self._closing:
            return False
        self._syncing = True
        changed = False
        try:
            active_chunk = msapi.chunk()
            active = msapi.chunk_key(active_chunk)
            action = chunks.decide(self.bound_key, active, self.chunk_locked(), bool(self.state.results),
                                   self.bound_chunk() is not None, self._kept, after_job)
            if action == chunks.REBIND:
                self._rebind(active, msapi.chunk_label(active_chunk))
                changed = True
            elif action == chunks.KEEP:
                self._kept = active
            elif active == self.bound_key:
                self._kept = None
            self._fill_chunks()
        finally:
            self._syncing = False
        return changed

    def _on_chunk_picked(self, index):
        key = self.chunk_combo.itemData(index)
        if key is None or key == self.bound_key:
            return
        if self.chunk_locked():
            self._fill_chunks(force=True)
            return
        chk = msapi.chunk_by_key(key)
        if chk is None:
            self._fill_chunks(force=True)
            self.set_status(tr("aeroppk.chunk.gone"), "warning")
            return
        if not msapi.set_active_chunk(chk):
            self._fill_chunks(force=True)
            self.set_status(tr("aeroppk.chunk.switch_failed"), "warning")
            return
        self._rebind(key, msapi.chunk_label(chk))
        self._fill_chunks(force=True)

    def _rebind(self, key, label):
        """Start again on the chunk `key`: stop what is small and running, drop the state and the pages, back to the first step."""
        self.bound_key, self.bound_label, self._kept = key, str(label or ""), None
        self._stop_side()
        try:
            self.screens[S.BASE].net.map.shutdown()
        except Exception:
            pass
        self.state = S.WizardState()
        self.pending_start = False
        self.stored = []
        self.rover_dirty = False
        old = list(self.screens)
        self.screens = []
        for screen in old:
            self.stack.removeWidget(screen)
            screen.hide()
            screen.setParent(None)
            screen.deleteLater()
        for screen in self._make_screens():                        # the same pages again: the steps of the bar stay
            screen.shell = self
            self.screens.append(screen)
            self.stack.addWidget(screen)
            screen.changed.connect(self.refresh_buttons)
        self._polish()
        self._status_override = False
        self.clear_banner()
        self.go(S.DRONE, force=True)

    def _make_screens(self):
        return [DroneScreen(self), FilesScreen(self), ModeScreen(self), BaseScreen(self), ResultsScreen(self), ExportScreen(self)]

    def _chunk_rows(self):
        rows = chunks.entries([(msapi.chunk_key(c), msapi.chunk_label(c), msapi.chunk_camera_count(c)) for c in msapi.chunks()])
        if self.bound_key is not None and all(r["key"] != self.bound_key for r in rows):
            rows.append({"key": self.bound_key, "label": self.bound_label, "photos": 0,
                         "text": tr("aeroppk.chunk.removed", name=self.bound_label or tr("aeroppk.chunk.unnamed"))})
        for row in rows:
            if row["key"] == self.bound_key:
                self.bound_label = row["label"]
        return rows

    def _fill_chunks(self, force=False):
        """The drop-down of the header: every chunk of the project, the bound one selected. Locked while the work runs."""
        combo = self.chunk_combo
        rows = self._chunk_rows()
        shown = ([(r["key"], r["text"]) for r in rows], self.bound_key)
        popup = False
        try:
            popup = combo.view().isVisible()
        except Exception:
            pass
        if force or (shown != self._rows_shown and not popup):
            choices = [(row["key"], row["text"]) for row in rows] or [(None, tr("aeroppk.chunk.none"))]
            combo.set_choices(choices)
            combo.setCurrentIndex(max(0, combo.findData(self.bound_key)) if rows else 0)
            self._size_chunk_combo()
            self._rows_shown = shown
        self._update_chunk_lock(bool(rows))

    def _size_chunk_combo(self):
        """As wide as its longest name needs, with room for the arrow and the padding (a name is not cut). Measured with the font the style
        sheet gives: the window measures again when it is shown, the sheet is applied once more there and the font may differ (Qt 5)."""
        combo = self.chunk_combo
        combo.ensurePolished()
        widest = max([combo.fontMetrics().horizontalAdvance(combo.itemText(i)) for i in range(combo.count())] or [0])
        option = QtWidgets.QStyleOptionComboBox()
        combo.initStyleOption(option)
        edit = combo.style().subControlRect(QtWidgets.QStyle.CC_ComboBox, option, QtWidgets.QStyle.SC_ComboBoxEditField, combo)
        around = combo.width() - edit.width() + uikit.px(tokens.XS)           # what the box keeps for the arrow and the padding, as the style says
        combo.setMinimumWidth(min(uikit.px(tokens.POPOVER_W), max(uikit.px(tokens.POPOVER_W // 2), widest + around)))

    def _update_chunk_lock(self, has_rows=None):
        locked = self.chunk_locked()
        if has_rows is None:
            has_rows = self.chunk_combo.count() > 0 and self.chunk_combo.itemData(0) is not None
        self.chunk_combo.setEnabled(has_rows and not locked)
        self.chunk_combo.setToolTip(tr("aeroppk.chunk.busy") if locked else "")

    # -- jobs -------------------------------------------------------------------------------------
    # `work(post, cancelled)` runs on a worker thread; `post(kind, **fields)` reaches `on_message(kind, fields)` on the UI thread, in order,
    # before `on_done(job)` is called once at the end (`job` is a `JobResult`).

    def busy(self):
        return self.is_busy()

    def run_job(self, work, on_message, on_done, name="", text="", indeterminate=False):
        """The exclusive job (download, calculation): the footer shows its progress, Cancel stops it, the buttons wait. False if one runs."""
        if self.is_busy():
            return False
        outcome = JobResult(name)
        box = _Mailbox(on_message, self)

        def func(progress):
            def post(kind, **fields):
                box.post(kind, fields)
                if kind == "fraction":
                    progress(fields.get("value"))
                elif kind == "text":
                    progress(None, fields.get("text", ""))
            if text:
                progress(None, text)
            try:
                outcome.result = work(post, progress.is_cancelled)
            except pipeline.Cancelled:
                raise uikit.Cancelled()
            return outcome.result

        def finished(_result):
            outcome.state = "done"

        def failed(exc):
            outcome.state, outcome.error = "failed", _error_text(exc)
            outcome.trace = self.runner().trace

        def cancelled():
            outcome.state = "stopped"

        def settled():
            box.deleteLater()
            on_done(outcome)
            self.refresh_buttons()
            if self._closing:
                self.close()
            else:
                self._sync(True)

        started = self.run(func, mode="threaded", name=name, on_done=finished, on_error=failed, on_cancel=cancelled, retry=False,
                           indeterminate=indeterminate)
        if not started:
            box.deleteLater()
            return False
        self._once_ended(settled)
        return True

    def _once_ended(self, callback):
        """Call `callback` once, when the running task has ended (after its outcome signal)."""
        runner = self.runner()

        def fire():
            runner.ended.disconnect(fire)
            callback()
        runner.ended.connect(fire)

    def run_side(self, work, on_done):
        """A small job that may run next to the exclusive one (the look at a station, the recognition of base files)."""
        task = uikit.TaskRunner(self)
        outcome = JobResult("side")
        entry = (task, on_done, outcome)

        def func(progress):
            return work(lambda kind, **fields: None, progress.is_cancelled)

        def finished(result):
            outcome.state, outcome.result = "done", result

        def failed(exc):
            outcome.state, outcome.error = "failed", _error_text(exc)

        def cancelled():
            outcome.state = "stopped"

        def ended():
            task.deleteLater()
            if entry in self._side:
                self._side.remove(entry)
                if not self._closing:
                    on_done(outcome)

        task.finished.connect(finished)
        task.failed.connect(failed)
        task.cancelled.connect(cancelled)
        task.ended.connect(ended)
        self._side.append(entry)
        task.start(func, "side", "threaded")

    def _stop_side(self):
        for task, _done, _outcome in self._side:
            task.cancel()
        self._side = []

    def wait_idle(self, timeout_s=600.0):
        """Let the running job finish (a hook of the tests; the window itself never waits). True when no job is left."""
        end = time.time() + timeout_s
        app = QtWidgets.QApplication.instance()
        while (self.is_busy() or self._side) and time.time() < end:
            app.processEvents()
            time.sleep(0.02)
        return not self.is_busy() and not self._side

    # -- steps ------------------------------------------------------------------------------------

    def current_step(self):
        return self.stack.currentIndex()

    @property
    def _step(self):
        return max(0, self.stack.currentIndex())

    def screen(self):
        return self.screens[self._step]

    def next(self):
        screen = self.screen()
        if self.busy() or not screen.next_enabled():
            return
        super(PpkDialog, self).next()

    def finish(self):
        """The primary button of the last page does what is ticked on the Export screen (the window stays open)."""
        self.screens[S.EXPORT].run()

    def rover_changed(self):
        """The photos, the device or a file changed: what depends on them starts again."""
        self.state.basket.clear_results()
        self.state.results = []
        self.state.own_base = None
        self.rover_dirty = True
        self.refresh_buttons()

    def info_line(self):
        """What the footer says when nothing needs saying: the photos that count and the flights they are in, and the flights left out."""
        photos = sum(len(f["files"].photos) for f in self.state.flights)
        if not photos:
            return ""
        text = tr("aeroppk.status", photos=photos, flights=len(self.state.flights))
        left_out = len(self.state.all_flights) - len(self.state.flights)
        return text + tr("aeroppk.status_left_out", n=left_out) if left_out else text

    def refresh_buttons(self, *_args):
        screen = self.current_screen()
        if screen is None:
            super(PpkDialog, self).refresh_buttons()
            return
        busy = self.busy() or screen.busy()
        self.footer.cancel_mode = "close" if (not busy and self._step in (S.RESULTS, S.EXPORT)) else "cancel"
        super(PpkDialog, self).refresh_buttons()
        self.next_button.setVisible(screen.show_next())
        if busy:
            self.next_button.setEnabled(False)
            self.back_button.setEnabled(False)
        if not self._status_override and not self.is_busy():
            hint = screen.hint() if (screen.show_next() and not screen.next_enabled()) else ""
            self.footer.set_status(hint or self.info_line())
        if hasattr(self, "chunk_combo"):
            self._update_chunk_lock()

    # -- closing ----------------------------------------------------------------------------------

    def closeEvent(self, event):
        if self.is_busy():
            self._closing = True                # the calculation is stopped, then the window closes by itself
        super(PpkDialog, self).closeEvent(event)

    def done(self, code):
        self._stop_side()
        self.chunk_timer.stop()
        try:
            self.screens[S.BASE].net.map.shutdown()
        except Exception:
            pass
        super(PpkDialog, self).done(code)
