"""Aero-Facade UI: screen 5 (build the surface under each wall) and screen 6 (orthophotos and export).

The long steps run one facade at a time, so that each wall has its own row with its own result: `batch_worker` is the function that
runs on the worker thread. It only writes plain dictionaries (`items`, `extras`); the screens read them with the wizard's timer.
"""

import os

from ... import config, theme, ui
from ...qt import QtCore, QtWidgets
from ..aeroexports import controls
from . import state as S
from .backend import Cancelled, Progress
from .common import FORMAT_KEYS, tr
from .screens_base import Screen, heading, right_panel
from .tasks import plain_error
from .widgets import (STATE_CANCELLED, STATE_DONE, STATE_FAILED, STATE_RUNNING, STATE_WAITING, BatchProgress, OrthoThumbnails)

EXTENSIONS = {"png": "png", "jpg": "jpg", "tif": "tif"}


def batch_worker(facades, call, items, extras=None, after=None):
    """Function for `Task`: run `call(facade, progress)` -> (ok, message) on each facade in turn.

    `items[letter] = (state, text)` is updated as it goes. Cancelling marks the rest "cancelled" and stops; an exception on one
    facade is that facade's failure, the others still run. `after(facade, extras)` runs after a success (thumbnails)."""
    facades = list(facades)

    def work(progress, cancelled):
        count = max(1, len(facades))
        results = {}
        for index, facade in enumerate(facades):
            if cancelled():
                for rest in facades[index:]:
                    items[rest.letter] = (STATE_CANCELLED, "")
                break
            letter = facade.letter
            items[letter] = (STATE_RUNNING, "")
            sub = Progress(progress, cancelled, index / float(count), (index + 1) / float(count))
            try:
                ok, message = call(facade, sub)
            except Cancelled:
                for rest in facades[index:]:
                    items[rest.letter] = (STATE_CANCELLED, "")
                break
            except Exception as exc:                          # one wall failing must not stop the others
                ok, message = False, plain_error(exc)
            if ok and after is not None:
                try:
                    after(facade, extras)
                except Exception:
                    pass                                      # a missing thumbnail is not a failure
            results[letter] = (bool(ok), message or "")
            items[letter] = (STATE_DONE if ok else STATE_FAILED, "" if ok else (message or tr("facade.ui.failed_default")))
            progress((index + 1) / float(count), "")
        return results

    return work


def apply_items(widget, items, shown):
    """Copy the worker's `items` into the rows of a BatchProgress; `shown` remembers what is already displayed."""
    for letter, entry in list(items.items()):
        if shown.get(letter) != entry:
            shown[letter] = entry
            widget.set_state(letter, entry[0], entry[1] if entry[0] == STATE_FAILED else "")


class BuildScreen(Screen):
    """Screen 5: the surfaces are built one wall after the other, with a row per wall, a banner and a Cancel button."""

    step = S.BUILD
    name_key = "facade.ui.step_build"

    def __init__(self, wizard):
        super(BuildScreen, self).__init__(wizard)
        self._running = False
        self._shown = {}
        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(theme.PAD_L)
        area, col = right_panel(width_hint=340)
        col.addWidget(heading(5, "facade.ui.title_build"))
        self.intro = ui.label("")
        col.addWidget(self.intro)
        card = QtWidgets.QFrame()
        card.setObjectName("facadeNote")
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.addWidget(ui.label(tr("facade.ui.build_why")))
        col.addWidget(card)
        self.build_button = ui.big_button("facade.ui.build", self.start_build, "aeroGo")
        col.addWidget(self.build_button)
        self.note = ui.label("", "aeroMeta")
        col.addWidget(self.note)
        col.addStretch(1)
        outer.addWidget(area, 2)
        self.batch = BatchProgress(show_retry=False)          # the Build button on the left does the same, once
        outer.addWidget(self.batch, 3)
        self.batch.cancel_requested.connect(self.cancel_job)
        self.batch.retry_requested.connect(self._retry)

    # -- wizard hooks -----------------------------------------------------------------------------

    def enter(self):
        st = self.state
        facades = st.active()
        self.batch.set_items(facades)
        self._shown = {}
        for facade in facades:
            ok, message = st.dem_state.get(facade.letter, (None, ""))
            if ok:
                self.batch.set_state(facade.letter, STATE_DONE, tr("facade.ui.already_built"))
            elif ok is False:
                self.batch.set_state(facade.letter, STATE_FAILED, message or tr("facade.ui.failed_default"))
        self.intro.setText(tr("facade.ui.build_intro", n=len(facades)))
        self._refresh()

    def busy(self):
        return self._running

    def next_text(self):
        return tr("facade.ui.next_ortho")

    def _todo(self, letters=None):
        st = self.state
        return [f for f in st.active() if (letters is None or f.letter in letters) and not st.dem_state.get(f.letter, (False, ""))[0]]

    def _refresh(self):
        todo = self._todo()
        done = len(self.state.dem_ok_letters())
        self.build_button.setVisible(not self._running and bool(todo))
        failed_before = any(self.state.dem_state.get(f.letter, (None, ""))[0] is False for f in self.state.active())
        self.build_button.setText(tr("facade.ui.build_again") if failed_before else tr("facade.ui.build_n", n=len(todo)))
        if self._running:
            self.note.setText(tr("facade.ui.build_running"))
        elif todo:
            self.note.setText(tr("facade.ui.build_left", n=len(todo)) if done else "")
        elif done:
            self.note.setText(tr("facade.ui.build_all_done"))
        else:
            self.note.setText("")
        self.note.setVisible(bool(self.note.text()))
        self.wizard.refresh_buttons()

    # -- the build --------------------------------------------------------------------------------

    def start_build(self, letters=None):
        if self._running:
            return False
        todo = self._todo(letters)
        if not todo:
            return False
        st = self.state
        backend = self.wizard.backend
        margins, method = st.margins_map(), st.method
        items, shown = {}, self._shown
        for facade in todo:
            self.batch.set_state(facade.letter, STATE_WAITING)
            shown.pop(facade.letter, None)

        def call(facade, sub):
            result = backend.build_dems([facade], margins, method, sub, sub.cancelled)
            entry = result.get(facade.letter)
            if entry is None:                                 # a backend leaves a wall out only when the job was cancelled: our
                raise Cancelled()                             # Cancel, or the Cancel button of Metashape's own progress window
            return bool(entry[0]), entry[1]

        work = batch_worker(todo, call, items)
        self._running = True
        self.batch.set_running(True)
        self.batch.set_progress(0.0, tr("facade.ui.build_start"))
        started = self.wizard.run_job(work, self._on_progress, self._on_done, "build", items=items)
        if not started:
            self._running = False
            self.batch.set_running(False)
        self._refresh()
        return started

    def _retry(self, letters):
        self.start_build(set(letters))

    def _on_progress(self, task):
        apply_items(self.batch, task.items, self._shown)
        self.batch.set_progress(task.fraction, task.text)

    def _on_done(self, task):
        self._running = False
        self.batch.set_running(False)
        apply_items(self.batch, task.items, self._shown)
        st = self.state
        if task.error is not None:
            self.note.setText(tr("facade.ui.job_failed", reason=plain_error(task.error)))
            self.note.setVisible(True)
        else:
            for letter, entry in (task.result or {}).items():
                st.dem_state[letter] = entry
        self.batch.set_progress(1.0 if not task.was_cancelled and task.error is None else task.fraction, "")
        self._refresh()
        everything = bool(st.active()) and len(st.dem_ok_letters()) == len(st.active())
        if everything and not task.was_cancelled and task.error is None:
            choice = self.wizard.ask(tr("facade.ui.build_done_title"), tr("facade.ui.build_done_text", n=len(st.active())),
                                     (("exit", "facade.ui.exit"), ("continue", "facade.ui.continue_ortho")))
            if choice == "continue":
                self.wizard.go(S.EXPORT)
            elif choice == "exit":
                self.wizard.close()


# --------------------------------------------------------------------------------------------------
# Screen 6
# --------------------------------------------------------------------------------------------------

PAGE_BUILD, PAGE_CHOOSE, PAGE_DONE = 0, 1, 2


class ExportScreen(Screen):
    """Screen 6: the orthophotos are built (a row per wall), then the operator ticks the pictures to keep, picks a folder and a
    format, and exports. The last page lists what was written, with Open folder."""

    step = S.EXPORT
    name_key = "facade.ui.step_export"

    def __init__(self, wizard):
        super(ExportScreen, self).__init__(wizard)
        self._running = False
        self._job = ""
        self._shown = {}
        self._thumbs = {}
        self._result = []
        self.stack = QtWidgets.QStackedWidget()
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.stack)
        self._build_page_build()
        self._build_page_choose()
        self._build_page_done()

    # -- pages ------------------------------------------------------------------------------------

    def _build_page_build(self):
        page = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(page)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.PAD_L)
        area, col = right_panel(width_hint=340)
        col.addWidget(heading(6, "facade.ui.title_export"))
        self.build_intro = ui.label(tr("facade.ui.ortho_intro"))
        col.addWidget(self.build_intro)
        self.keep_button = ui.big_button("facade.ui.keep_going", self._show_choose, "aeroGo")
        col.addWidget(self.keep_button)
        self.build_note = ui.label("", "aeroWarn")
        col.addWidget(self.build_note)
        col.addStretch(1)
        row.addWidget(area, 2)
        self.batch = BatchProgress()
        self.batch.cancel_requested.connect(self.cancel_job)
        self.batch.retry_requested.connect(self._retry)
        row.addWidget(self.batch, 3)
        self.stack.addWidget(page)

    def _build_page_choose(self):
        page = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(page)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.PAD_L)
        left = QtWidgets.QWidget()
        left_col = QtWidgets.QVBoxLayout(left)
        left_col.setContentsMargins(0, 0, 0, 0)
        left_col.setSpacing(theme.PAD + 4)
        left_col.addWidget(heading(6, "facade.ui.title_export"))
        left_col.addWidget(ui.label(tr("facade.ui.choose_intro")))
        self.thumbs = OrthoThumbnails()
        self.thumbs.checks_changed.connect(self._refresh_choose)
        left_col.addWidget(self.thumbs, 1)
        row.addWidget(left, 3)

        area, col = right_panel(width_hint=380)
        col.addWidget(ui.label(tr("facade.ui.where_title"), "aeroSection"))
        folder_row = QtWidgets.QHBoxLayout()
        folder_row.setSpacing(theme.PAD)
        self.folder = QtWidgets.QLineEdit()
        self.folder.textChanged.connect(self._refresh_choose)
        folder_row.addWidget(self.folder, 1)
        folder_row.addWidget(ui.button("facade.ui.browse", self._browse, "aeroBtn", width=96))
        col.addLayout(folder_row)
        col.addWidget(ui.label(tr("facade.ui.format_title"), "aeroSection"))
        self.formats = QtWidgets.QButtonGroup(self)
        saved = config.get(S.SECTION, "fmt")
        for key in S.FORMATS:
            radio = QtWidgets.QRadioButton(tr(FORMAT_KEYS[key][0]))
            radio.setProperty("value", key)
            radio.setCursor(QtCore.Qt.PointingHandCursor)
            radio.setToolTip(tr(FORMAT_KEYS[key][2]))
            radio.setChecked(key == (saved if saved in S.FORMATS else "png"))
            radio.toggled.connect(lambda _c: self._refresh_choose())
            self.formats.addButton(radio)
            card = ui.OptionCard(radio)
            card.grid.setColumnStretch(1, 1)
            card.grid.addWidget(ui.label(tr(FORMAT_KEYS[key][1]), "aeroMeta"), 0, 1)
            col.addWidget(card)
        self.georef = QtWidgets.QCheckBox(tr("facade.ui.opt_georef"))
        self.georef.setChecked(config.get(S.SECTION, "georef_txt") is not False)
        self.georef.setToolTip(tr("facade.ui.opt_georef_tip"))
        self.georef.toggled.connect(self._refresh_choose)
        self.dxf = QtWidgets.QCheckBox(tr("facade.ui.opt_dxf"))
        self.dxf.setChecked(config.get(S.SECTION, "dxf") is True)
        self.dxf.setToolTip(tr("facade.ui.opt_dxf_tip"))
        self.dxf.toggled.connect(self._refresh_choose)
        col.addWidget(self.georef)
        col.addWidget(self.dxf)
        self.names = controls.WrapText(meta=True)
        col.addWidget(self.names)
        self.export_button = ui.big_button("facade.ui.export", self.start_export, "aeroGo")
        col.addWidget(self.export_button)
        self.export_box = QtWidgets.QFrame()
        self.export_box.setObjectName("facadeCard")
        box = QtWidgets.QVBoxLayout(self.export_box)
        box.setContentsMargins(14, 12, 14, 12)
        self.export_text = controls.ElidedLabel()
        box.addWidget(self.export_text)
        self.export_bar = QtWidgets.QProgressBar()
        self.export_bar.setObjectName("facadeBar")
        self.export_bar.setRange(0, 1000)
        self.export_bar.setTextVisible(False)
        box.addWidget(self.export_bar)
        box.addWidget(ui.big_button("facade.ui.cancel_job", self.cancel_job, "aeroBtn", width=130), 0, QtCore.Qt.AlignLeft)
        self.export_box.setVisible(False)
        col.addWidget(self.export_box)
        col.addStretch(1)
        row.addWidget(area, 2)
        self.stack.addWidget(page)

    def _build_page_done(self):
        page = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(page)
        outer.setContentsMargins(0, theme.PAD_L, 0, 0)
        card = QtWidgets.QFrame()
        card.setObjectName("facadeCard")
        card.setMaximumWidth(760)
        col = QtWidgets.QVBoxLayout(card)
        col.setContentsMargins(24, 22, 24, 22)
        col.setSpacing(theme.PAD + 4)
        head = QtWidgets.QHBoxLayout()
        head.setSpacing(theme.PAD_L)
        tick = QtWidgets.QLabel("✓")
        tick.setAlignment(QtCore.Qt.AlignCenter)
        tick.setFixedSize(44, 44)
        tick.setStyleSheet("background: {}; color: {}; border-radius: 22px; font-size: 18pt; font-weight: 700;".format(
            theme.GREEN, theme.GREEN_ON))
        head.addWidget(tick)
        self.done_title = ui.label("", "facadeBig")
        head.addWidget(self.done_title, 1)
        col.addLayout(head)
        self.done_folder = controls.WrapText()
        col.addWidget(self.done_folder)
        self.done_files = controls.WrapText(meta=True)
        col.addWidget(self.done_files)
        self.done_bad = ui.label("", "aeroWarn")
        col.addWidget(self.done_bad)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(theme.PAD)
        self.open_button = ui.big_button("facade.ui.open_folder", self._open_folder, "aeroGo", width=170)
        row.addWidget(self.open_button)
        row.addWidget(ui.big_button("facade.ui.back_to_pictures", self._show_choose, "aeroBtn", width=190))
        row.addWidget(ui.big_button("facade.ui.close", self.wizard.close, "aeroBtn", width=110))
        row.addStretch(1)
        col.addLayout(row)
        outer.addWidget(card, 0, QtCore.Qt.AlignHCenter)
        outer.addStretch(1)
        self.stack.addWidget(page)

    # -- wizard hooks -----------------------------------------------------------------------------

    def enter(self):
        st = self.state
        facades = [f for f in st.active() if f.letter in st.dem_ok_letters()]
        if st.ortho_key == st.signature() and st.ortho_ok_letters() and self._thumbs_ready():
            self._show_choose()
            return
        self.stack.setCurrentIndex(PAGE_BUILD)
        self.batch.set_items(facades)
        self._shown = {}
        self.keep_button.setVisible(False)
        self.build_note.setText("")
        self.start_orthos(None)

    def busy(self):
        return self._running

    def show_next(self):
        return False

    def prev_step(self):
        return S.METHOD if self.state.skipped_build else S.BUILD

    def _thumbs_ready(self):
        return self.thumbs.count() > 0

    # -- the orthophotos --------------------------------------------------------------------------

    def start_orthos(self, letters):
        if self._running:
            return False
        st = self.state
        todo = [f for f in st.active() if f.letter in st.dem_ok_letters() and (letters is None or f.letter in letters)]
        if not todo:
            self.build_note.setText(tr("facade.ui.ortho_nothing"))
            return False
        backend, margins, method, pixel = self.wizard.backend, st.margins_map(), st.method, st.pixel_cm / 100.0
        items, extras = {}, {"thumbs": {}}
        for facade in todo:
            self.batch.set_state(facade.letter, STATE_WAITING)
            self._shown.pop(facade.letter, None)

        def call(facade, sub):
            result = backend.build_orthos([facade], margins, method, pixel, sub, sub.cancelled)
            entry = result.get(facade.letter)
            if entry is None:                                 # a backend leaves a wall out only when the job was cancelled: our
                raise Cancelled()                             # Cancel, or the Cancel button of Metashape's own progress window
            return bool(entry[0]), entry[1]

        def thumbnail(facade, store):
            store["thumbs"][facade.letter] = backend.thumbnail(facade)

        work = batch_worker(todo, call, items, extras, after=thumbnail)
        self._running, self._job = True, "orthos"
        self.batch.set_running(True)
        self.batch.set_progress(0.0, tr("facade.ui.build_start"))
        started = self.wizard.run_job(work, self._on_progress, self._on_orthos_done, "orthos", items=items, extras=extras)
        if not started:
            self._running = False
            self.batch.set_running(False)
        self.wizard.refresh_buttons()
        return started

    def _retry(self, letters):
        self.start_orthos(set(letters))

    def _on_progress(self, task):
        apply_items(self.batch, task.items, self._shown)
        self.batch.set_progress(task.fraction, task.text)

    def _on_orthos_done(self, task):
        self._running = False
        self.batch.set_running(False)
        apply_items(self.batch, task.items, self._shown)
        st = self.state
        if task.error is not None:
            self.build_note.setText(tr("facade.ui.job_failed", reason=plain_error(task.error)))
        else:
            for letter, entry in (task.result or {}).items():
                st.ortho_state[letter] = entry
            self._thumbs.update(task.extras.get("thumbs", {}))
        st.ortho_key = st.signature()
        ok = st.ortho_ok_letters()
        self.batch.set_progress(1.0 if not task.was_cancelled and task.error is None else task.fraction, "")
        failed = [f for f in st.active() if f.letter in st.ortho_state and not st.ortho_state[f.letter][0]]
        everything = len(ok) == len([f for f in st.active() if f.letter in st.dem_ok_letters()])
        if everything and not task.was_cancelled and task.error is None:
            self._show_choose()
        else:
            if task.was_cancelled:
                self.build_note.setText(tr("facade.ui.ortho_cancelled"))
            elif failed:
                self.build_note.setText(tr("facade.ui.ortho_some_failed", n=len(failed)))
            self.keep_button.setVisible(bool(ok))
        self.wizard.refresh_buttons()

    # -- choosing and exporting -------------------------------------------------------------------

    def _show_choose(self):
        st = self.state
        facades = [f for f in st.active() if f.letter in st.ortho_ok_letters()]
        previous = set(self.thumbs.checked_letters())
        same = [f.letter for f in facades] == self.thumbs.letters()
        if not same:
            self.thumbs.set_items(facades, checked=True)
            for facade in facades:
                self.thumbs.set_thumbnail(facade.letter, self._thumbs.get(facade.letter))
        elif previous:
            self.thumbs.set_checked(previous)
        if not self.folder.text().strip():
            self.folder.setText(self.wizard.info.get("outputs", ""))
        self.stack.setCurrentIndex(PAGE_CHOOSE)
        self._refresh_choose()
        self.wizard.refresh_buttons()

    def format(self):
        for radio in self.formats.buttons():
            if radio.isChecked():
                return radio.property("value")
        return "png"

    def options(self):
        return {"georef_txt": self.georef.isChecked(), "dxf": self.dxf.isChecked()}

    def _browse(self):
        start = self.folder.text().strip() or self.wizard.info.get("outputs", "")
        chosen = QtWidgets.QFileDialog.getExistingDirectory(self, tr("facade.ui.browse_title"), start)
        if chosen:
            self.folder.setText(os.path.normpath(chosen))

    def _refresh_choose(self, *_args):
        letters = self.thumbs.checked_letters()
        folder = self.folder.text().strip()
        info = self.wizard.info
        ext = EXTENSIONS[self.format()]
        sample = "%s_%s_facade_%s.%s" % (info.get("site", "SITE"), info.get("date", "000000"), letters[0] if letters else "A", ext)
        self.names.setText(tr("facade.ui.names_example", name=sample) if letters else tr("facade.ui.names_none"))
        self.export_button.setEnabled(bool(letters) and bool(folder) and not self._running)
        self.export_button.setText(tr("facade.ui.export_n", n=len(letters)) if letters else tr("facade.ui.export"))
        self.export_button.setVisible(not self._running)
        self.export_box.setVisible(self._running)

    def start_export(self):
        if self._running:
            return False
        st = self.state
        letters = set(self.thumbs.checked_letters())
        folder = self.folder.text().strip()
        facades = [f for f in st.active() if f.letter in letters]
        if not facades or not folder:
            return False
        fmt, options = self.format(), self.options()
        S.save_prefs(st, fmt=fmt, georef_txt=options["georef_txt"], dxf=options["dxf"])
        backend = self.wizard.backend
        self._running, self._job = True, "export"
        self.export_bar.setValue(0)
        self.export_text.setFullText(tr("facade.ui.export_start"))
        self._refresh_choose()
        started = self.wizard.run_job(lambda progress, cancelled: backend.export(facades, folder, fmt, options, progress, cancelled),
                                      self._on_export_progress, lambda task: self._on_export_done(task, folder), "export")
        if not started:
            self._running = False
            self._refresh_choose()
        self.wizard.refresh_buttons()
        return started

    def _on_export_progress(self, task):
        self.export_bar.setValue(int(task.fraction * 1000))
        if task.text:
            self.export_text.setFullText(task.text)

    def _on_export_done(self, task, folder):
        self._running = False
        self._result = list(task.result or [])
        self._refresh_choose()
        if task.error is not None:
            self.build_note.setText(tr("facade.ui.job_failed", reason=plain_error(task.error)))
            self.names.setText(tr("facade.ui.job_failed", reason=plain_error(task.error)))
            self.wizard.refresh_buttons()
            return
        written = [r for r in self._result if r[1]]
        bad = [r for r in self._result if not r[1]]
        self._folder_done = folder
        self.done_title.setText(tr("facade.ui.done_title", n=len(written)) if written else tr("facade.ui.done_none"))
        self.done_folder.setText(tr("facade.ui.done_folder", folder=folder) if written else "")
        self.done_files.setText("\n".join(os.path.basename(r[0]) for r in written[:12]) + ("\n..." if len(written) > 12 else ""))
        lines = ["%s: %s" % (os.path.basename(r[0]), r[2] or tr("facade.ui.failed_default")) for r in bad]
        if task.was_cancelled:
            lines.append(tr("facade.ui.export_cancelled"))
        self.done_bad.setText("\n".join(lines))
        self.done_bad.setVisible(bool(lines))
        self.open_button.setEnabled(bool(written) and os.path.isdir(folder))
        self.stack.setCurrentIndex(PAGE_DONE)
        self.wizard.refresh_buttons()

    def _open_folder(self):
        folder = getattr(self, "_folder_done", "") or self.folder.text().strip()
        self.wizard.open_folder(folder)
