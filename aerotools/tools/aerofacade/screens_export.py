"""Aero-Facade UI: screen 6 (build the orthophotos, tick the ones to export) and screen 7 (export settings, export, result).

Screen 6 (`PicturesScreen`) builds the orthophotos one wall at a time (a row per wall), then shows a small picture of each with a tick. The ticked
letters live in the shared `WizardState` (`picture_choice`), so screen 7 reads them and Back keeps them.

Screen 7 (`ExportScreen`) holds everything that decides what is written: folder, picture format, the extra files, the drawing sheet (DXF / DWG,
altitudes with their height system, title language), the example file names, the Export button with its progress, and the result card.
It is laid out in two columns with a bar at the bottom so that it fits the usual window without a scroll bar.
"""

import os

from ... import config, theme, ui
from ...qt import QtCore, QtWidgets
from ..aeroexports import controls
from . import sheetout
from . import state as S
from .backend import Cancelled
from .common import FORMAT_KEYS, FORMAT_NEED_KEYS, tr
from .screens_base import Screen, heading, right_panel
from .screens_build import apply_items, batch_worker
from .tasks import plain_error
from .widgets import STATE_WAITING, BatchProgress, OrthoThumbnails
from .widgets_heights import HeightPicker

EXTENSIONS = {"png": "png", "jpg": "jpg", "tif": "tif", "jp2": "jp2", "ecw": "ecw"}

PAGE_BUILD, PAGE_CHOOSE = 0, 1                 # screen 6
PAGE_SETTINGS, PAGE_DONE = 0, 1                # screen 7


# --------------------------------------------------------------------------------------------------
# Screen 6
# --------------------------------------------------------------------------------------------------

class PicturesScreen(Screen):
    """Screen 6: the orthophotos are built (a row per wall), then the operator ticks the pictures to export. Next goes to the export settings."""

    step = S.PICTURES
    name_key = "facade.ui.step_pictures"

    def __init__(self, wizard):
        super(PicturesScreen, self).__init__(wizard)
        self._running = False
        self._job = ""
        self._shown = {}
        self._thumbs = {}
        self.stack = QtWidgets.QStackedWidget()
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.stack)
        self._build_page_build()
        self._build_page_choose()

    # -- pages ------------------------------------------------------------------------------------

    def _build_page_build(self):
        page = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(page)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.PAD_L)
        area, col = right_panel(width_hint=340)
        col.addWidget(heading(6, "facade.ui.title_pictures"))
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
        left_col.addWidget(heading(6, "facade.ui.title_pictures"))
        self.thumbs = OrthoThumbnails()
        self.thumbs.checks_changed.connect(self._checks_changed)
        left_col.addWidget(self.thumbs, 1)
        row.addWidget(left, 3)
        area, col = right_panel(width_hint=340)
        col.addWidget(ui.label(tr("facade.ui.choose_intro")))
        self.choose_count = ui.label("", "aeroSection")
        col.addWidget(self.choose_count)
        col.addWidget(ui.label(tr("facade.ui.choose_next"), "aeroMeta"))
        col.addStretch(1)
        row.addWidget(area, 2)
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
        return self.stack.currentIndex() == PAGE_CHOOSE

    def next_step(self):
        return S.EXPORT

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
            self._thumbs_new = True                           # the pictures were rebuilt: the small ones must be redrawn
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

    # -- choosing ---------------------------------------------------------------------------------

    def _show_choose(self):
        """Show the small pictures with their ticks. A rebuild of the same walls keeps the ticks; other walls start all ticked."""
        st = self.state
        facades = [f for f in st.active() if f.letter in st.ortho_ok_letters()]
        previous = set(st.picture_choice) if st.picture_choice is not None else set(self.thumbs.checked_letters())
        same = [f.letter for f in facades] == self.thumbs.letters()
        if not same or getattr(self, "_thumbs_new", False):
            self._thumbs_new = False
            self.thumbs.set_items(facades, checked=True)
            for facade in facades:
                self.thumbs.set_thumbnail(facade.letter, self._thumbs.get(facade.letter))
            if same and previous:
                self.thumbs.set_checked(previous)
        elif previous:
            self.thumbs.set_checked(previous)
        self.stack.setCurrentIndex(PAGE_CHOOSE)
        self._checks_changed()

    def _checks_changed(self, *_args):
        """The ticks are the shared choice of the wizard: screen 7 exports exactly these."""
        letters = self.thumbs.checked_letters()
        self.state.picture_choice = set(letters)
        self.choose_count.setText(tr("facade.ui.choose_count", n=len(letters), total=self.thumbs.count()))
        self.wizard.refresh_buttons()


# --------------------------------------------------------------------------------------------------
# Screen 7
# --------------------------------------------------------------------------------------------------

class ExportScreen(Screen):
    """Screen 7: folder, format, extra files, drawing sheet, example names, Export with its progress; the last page lists what was written."""

    step = S.EXPORT
    name_key = "facade.ui.step_export"

    def __init__(self, wizard):
        super(ExportScreen, self).__init__(wizard)
        self._running = False
        self._result = []
        self._folder_done = ""
        self.stack = QtWidgets.QStackedWidget()
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.stack)
        self._build_page_settings()
        self._build_page_done()

    # -- pages ------------------------------------------------------------------------------------

    def _build_page_settings(self):
        content = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(content)
        outer.setContentsMargins(0, 0, theme.PAD, 0)
        outer.setSpacing(theme.PAD)
        outer.addWidget(heading(7, "facade.ui.title_export"))
        columns = QtWidgets.QHBoxLayout()
        columns.setSpacing(theme.PAD_L)
        left, right = QtWidgets.QVBoxLayout(), QtWidgets.QVBoxLayout()
        for column in (left, right):
            column.setSpacing(theme.PAD)
        columns.addLayout(left, 1)
        columns.addLayout(right, 1)
        outer.addLayout(columns, 1)

        # -- left: folder, format, extra files
        left.addWidget(ui.label(tr("facade.ui.where_title"), "aeroSection"))
        folder_row = QtWidgets.QHBoxLayout()
        folder_row.setSpacing(theme.PAD)
        self.folder = QtWidgets.QLineEdit()
        self.folder.textChanged.connect(self._refresh)
        folder_row.addWidget(self.folder, 1)
        folder_row.addWidget(ui.button("facade.ui.browse", self._browse, "aeroBtn", width=96))
        left.addLayout(folder_row)
        left.addWidget(ui.label(tr("facade.ui.format_title"), "aeroSection"))
        self.formats = QtWidgets.QButtonGroup(self)
        saved = config.get(S.SECTION, "fmt")
        self.tools = sheetout.detect_tools()                     # {"gm", "acad", "gdal"}: what JPEG 2000, ECW and DWG need
        have = {key: not sheetout.tools_for_formats(self.tools, key, None) for key in S.FORMATS}
        saved = saved if saved in S.FORMATS and have.get(saved) else "png"
        for key in S.FORMATS:
            radio = QtWidgets.QRadioButton(tr(FORMAT_KEYS[key][0]))
            radio.setProperty("value", key)
            radio.setCursor(QtCore.Qt.PointingHandCursor)
            radio.setToolTip(tr(FORMAT_KEYS[key][2]))
            radio.setChecked(key == saved)
            radio.setEnabled(have[key])
            radio.toggled.connect(lambda _c: self._refresh())
            self.formats.addButton(radio)
            card = ui.OptionCard(radio)
            card.grid.setContentsMargins(12, 6, 12, 6)           # compact: five of them must fit one above the other
            card.grid.setColumnStretch(1, 1)
            card.grid.addWidget(ui.label(tr(FORMAT_KEYS[key][1] if have[key] else FORMAT_NEED_KEYS[key]), "aeroMeta"), 0, 1)
            left.addWidget(card)
        left.addWidget(ui.label(tr("facade.ui.extras_title"), "aeroSection"))
        self.georef = QtWidgets.QCheckBox(tr("facade.ui.opt_georef"))
        self.georef.setChecked(config.get(S.SECTION, "georef_txt") is not False)
        self.georef.setToolTip(tr("facade.ui.opt_georef_tip"))
        self.georef.toggled.connect(self._refresh)
        self.dxf = QtWidgets.QCheckBox(tr("facade.ui.opt_dxf"))
        self.dxf.setChecked(config.get(S.SECTION, "dxf") is True)
        self.dxf.setToolTip(tr("facade.ui.opt_dxf_tip"))
        self.dxf.toggled.connect(self._refresh)
        left.addWidget(self.georef)
        left.addWidget(self.dxf)
        left.addStretch(1)

        # -- right: the drawing sheet
        self._build_sheet_group(right)
        right.addStretch(1)

        # -- bottom bar: what will be written, and the Export button (replaced by the progress while it runs)
        bar = QtWidgets.QHBoxLayout()
        bar.setSpacing(theme.PAD_L)
        self.names = controls.WrapText(meta=True)
        bar.addWidget(self.names, 3)
        self.export_button = ui.big_button("facade.ui.export", self.start_export, "aeroGo", width=250)
        bar.addWidget(self.export_button, 0, QtCore.Qt.AlignBottom)
        self.export_box = QtWidgets.QFrame()
        self.export_box.setObjectName("facadeCard")
        box = QtWidgets.QHBoxLayout(self.export_box)
        box.setContentsMargins(14, 10, 14, 10)
        box.setSpacing(theme.PAD)
        inner = QtWidgets.QVBoxLayout()
        self.export_text = controls.ElidedLabel()
        inner.addWidget(self.export_text)
        self.export_bar = QtWidgets.QProgressBar()
        self.export_bar.setObjectName("facadeBar")
        self.export_bar.setRange(0, 1000)
        self.export_bar.setTextVisible(False)
        inner.addWidget(self.export_bar)
        box.addLayout(inner, 1)
        box.addWidget(ui.big_button("facade.ui.cancel_job", self.cancel_job, "aeroBtn", width=110))
        self.export_box.setVisible(False)
        bar.addWidget(self.export_box, 3)
        outer.addLayout(bar)
        self.settings_area = controls.scroll_page(content, preferred=0, cap=0)    # bars only show when the window is smaller than the content
        self.stack.addWidget(self.settings_area)
        self._sheet_changed()

    def _build_sheet_group(self, col):
        """The drawing sheet: one DXF or DWG with every ticked picture, titles, level lines (docs/AERO_FACADE_SPEC.md, "Drawing sheet")."""
        saved = config.section(S.SECTION)
        col.addWidget(ui.label(tr("facade.ui.sheet_title"), "aeroSection"))
        self.sheet_on = QtWidgets.QCheckBox(tr("facade.ui.sheet_on"))
        self.sheet_on.setChecked(saved.get("sheet") is True)
        self.sheet_on.setToolTip(tr("facade.ui.sheet_on_tip"))
        self.sheet_on.toggled.connect(self._sheet_changed)
        col.addWidget(self.sheet_on)
        self.sheet_box = QtWidgets.QWidget()
        inner = QtWidgets.QVBoxLayout(self.sheet_box)
        inner.setContentsMargins(24, 0, 0, 0)
        inner.setSpacing(theme.PAD)
        have_dwg = bool(self.tools.get("acad"))
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(theme.PAD_L)
        self.sheet_fmt = QtWidgets.QButtonGroup(self)
        for key, text, tip in (("dxf", "facade.ui.sheet_dxf", "facade.ui.sheet_dxf_tip"), ("dwg", "facade.ui.sheet_dwg", "facade.ui.sheet_dwg_tip")):
            radio = QtWidgets.QRadioButton(tr(text))
            radio.setProperty("value", key)
            radio.setToolTip(tr(tip))
            radio.setCursor(QtCore.Qt.PointingHandCursor)
            radio.setEnabled(key != "dwg" or have_dwg)
            radio.setChecked(key == ("dwg" if saved.get("sheet_fmt") == "dwg" and have_dwg else "dxf"))
            radio.toggled.connect(self._sheet_changed)
            self.sheet_fmt.addButton(radio)
            row.addWidget(radio)
        row.addStretch(1)
        inner.addLayout(row)
        self.sheet_dwg_note = ui.label(tr("facade.ui.sheet_need_dwg"), "aeroMeta")
        self.sheet_dwg_note.setVisible(not have_dwg)
        inner.addWidget(self.sheet_dwg_note)
        self.sheet_pictures = QtWidgets.QCheckBox(tr("facade.ui.sheet_pictures"))
        self.sheet_pictures.setChecked(saved.get("sheet_pictures") is not False)
        self.sheet_pictures.setToolTip(tr("facade.ui.sheet_pictures_tip"))
        self.sheet_pictures.toggled.connect(self._sheet_changed)
        inner.addWidget(self.sheet_pictures)
        lang_row = QtWidgets.QHBoxLayout()
        lang_row.setSpacing(theme.PAD)
        lang_row.addWidget(ui.label(tr("facade.ui.sheet_titles"), "aeroMeta", wrap=False))
        self.sheet_titles = ui.combo()
        self.sheet_titles.addItem(tr("facade.ui.sheet_lang_fr"), "fr")
        self.sheet_titles.addItem(tr("facade.ui.sheet_lang_en"), "en")
        self.sheet_titles.setCurrentIndex(max(0, self.sheet_titles.findData(saved.get("sheet_titles") or "fr")))
        self.sheet_titles.activated.connect(self._sheet_changed)
        lang_row.addWidget(self.sheet_titles, 1)
        inner.addLayout(lang_row)
        self.sheet_z = QtWidgets.QCheckBox(tr("facade.ui.sheet_z"))
        self.sheet_z.setChecked(saved.get("sheet_z") is True)
        self.sheet_z.setToolTip(tr("facade.ui.sheet_z_tip"))
        self.sheet_z.toggled.connect(self._sheet_changed)
        inner.addWidget(self.sheet_z)
        self.heights = HeightPicker()
        self.heights.changed.connect(self._refresh)
        inner.addWidget(self.heights)
        self.sheet_z_note = ui.label(tr("facade.ui.sheet_relative"), "aeroMeta")
        inner.addWidget(self.sheet_z_note)
        self._saved_vertical = saved.get("sheet_vertical")
        self._height_ready = False
        col.addWidget(self.sheet_box)

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
        row.addWidget(ui.big_button("facade.ui.back_to_settings", self._show_settings, "aeroBtn", width=190))
        row.addWidget(ui.big_button("facade.ui.close", self.wizard.close, "aeroBtn", width=110))
        row.addStretch(1)
        col.addLayout(row)
        outer.addWidget(card, 0, QtCore.Qt.AlignHCenter)
        outer.addStretch(1)
        self.stack.addWidget(page)

    # -- wizard hooks -----------------------------------------------------------------------------

    def enter(self):
        if not self.folder.text().strip():
            self.folder.setText(self.wizard.info.get("outputs", ""))
        self._load_height_context()
        self._show_settings()

    def busy(self):
        return self._running

    def show_next(self):
        return False

    def next_enabled(self):
        return False

    def prev_step(self):
        return S.PICTURES

    # -- the sheet --------------------------------------------------------------------------------

    def _load_height_context(self):
        """Ask the backend where the site is (once): the height systems offered depend on its coordinate system and place."""
        if self._height_ready:
            return
        self._height_ready = True
        ask = getattr(self.wizard.backend, "height_context", None)
        try:
            context = ask() if ask else {}
        except Exception:
            context = {}
        self._height_epsg = context.get("epsg")
        self.heights.set_context(self._height_epsg, context.get("lonlat"), keep=self._saved_vertical)
        self._sheet_changed()

    def _sheet_changed(self, *_args):
        """Show what the sheet needs: its options only when ticked, the height systems only when altitudes are asked."""
        on = self.sheet_on.isChecked()
        self.sheet_box.setVisible(on)
        has_epsg = bool(getattr(self, "_height_epsg", None)) and self.heights.count() > 0
        self.sheet_z.setEnabled(has_epsg)
        z = self.sheet_z.isChecked() and has_epsg
        self.heights.setVisible(z)
        self.sheet_z_note.setText(tr("facade.ui.sheet_relative") if has_epsg or not self._height_ready else tr("facade.ui.sheet_z_none"))
        self.sheet_z_note.setVisible(not z)
        self._refresh()

    def sheet_options(self):
        """`options["sheet"]` of the Backend protocol, from the controls."""
        if not self.sheet_on.isChecked():
            return {"dxf": False, "dwg": False, "z": False, "vertical": None, "titles": self.sheet_titles.currentData() or "fr"}
        has_epsg = bool(getattr(self, "_height_epsg", None)) and self.heights.count() > 0
        z = self.sheet_z.isChecked() and has_epsg
        dwg = any(radio.isChecked() and radio.property("value") == "dwg" for radio in self.sheet_fmt.buttons())
        return {"dxf": True, "dwg": dwg, "z": z, "vertical": self.heights.selected() if z else None,
                "titles": self.sheet_titles.currentData() or "fr"}

    # -- settings ---------------------------------------------------------------------------------

    def chosen(self):
        """The facades to export: the ones ticked on screen 6."""
        wanted = set(self.state.chosen_letters())
        return [f for f in self.state.active() if f.letter in wanted]

    def format(self):
        for radio in self.formats.buttons():
            if radio.isChecked():
                return radio.property("value")
        return "png"

    def options(self):
        return {"georef_txt": self.georef.isChecked(), "dxf": self.dxf.isChecked(), "sheet": self.sheet_options(),
                "pictures": self.sheet_pictures.isChecked() or not self.sheet_on.isChecked(), "tools": dict(self.tools)}

    def _browse(self):
        start = self.folder.text().strip() or self.wizard.info.get("outputs", "")
        chosen = QtWidgets.QFileDialog.getExistingDirectory(self, tr("facade.ui.browse_title"), start)
        if chosen:
            self.folder.setText(os.path.normpath(chosen))

    def _show_settings(self):
        self.stack.setCurrentIndex(PAGE_SETTINGS)
        self._refresh()
        self.wizard.refresh_buttons()

    def _refresh(self, *_args):
        if not hasattr(self, "names"):
            return                                                 # called while the page is still being built
        facades = self.chosen()
        letters = [f.letter for f in facades]
        folder = self.folder.text().strip()
        info = self.wizard.info
        ext = EXTENSIONS[self.format()]
        sample = "%s_%s_facade_%s.%s" % (info.get("site", "SITE"), info.get("date", "000000"), letters[0] if letters else "A", ext)
        if letters:
            text = tr("facade.ui.export_summary", n=len(letters), letters=", ".join(letters)) + "\n" + tr("facade.ui.names_example", name=sample)
        else:
            text = tr("facade.ui.names_none")
        sheet = self.sheet_options()
        if letters and (sheet["dxf"] or sheet["dwg"]):
            sheet_name = sheetout.sheet_stem(info.get("site", "SITE"), info.get("date", "000000")) + (".dwg" if sheet["dwg"] else ".dxf")
            text += "\n" + tr("facade.ui.names_sheet", name=sheet_name)
        self.names.setText(text)
        waiting = sheet["z"] and self.heights.blocking()               # the chosen height grid must be on this computer first
        self.export_button.setEnabled(bool(letters) and bool(folder) and not self._running and not waiting)
        self.export_button.setText(tr("facade.ui.export_n", n=len(letters)) if letters else tr("facade.ui.export"))
        self.export_button.setVisible(not self._running)
        self.export_box.setVisible(self._running)

    def start_export(self):
        if self._running:
            return False
        facades = self.chosen()
        folder = self.folder.text().strip()
        if not facades or not folder:
            return False
        fmt, options = self.format(), self.options()
        sheet = options["sheet"]
        S.save_prefs(self.state, fmt=fmt, georef_txt=options["georef_txt"], dxf=options["dxf"], sheet=self.sheet_on.isChecked(),
                     sheet_fmt="dwg" if sheet["dwg"] else "dxf", sheet_pictures=self.sheet_pictures.isChecked(),
                     sheet_titles=sheet["titles"], sheet_z=self.sheet_z.isChecked(), sheet_vertical=self.heights.selected() or self._saved_vertical)
        backend = self.wizard.backend
        self._running = True
        self.export_bar.setValue(0)
        self.export_text.setFullText(tr("facade.ui.export_start"))
        self._refresh()
        started = self.wizard.run_job(lambda progress, cancelled: backend.export(facades, folder, fmt, options, progress, cancelled),
                                      self._on_export_progress, lambda task: self._on_export_done(task, folder), "export")
        if not started:
            self._running = False
            self._refresh()
        self.wizard.refresh_buttons()
        return started

    def _on_export_progress(self, task):
        self.export_bar.setValue(int(task.fraction * 1000))
        if task.text:
            self.export_text.setFullText(task.text)

    def _on_export_done(self, task, folder):
        self._running = False
        self._result = list(task.result or [])
        self._refresh()
        if task.error is not None:
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
        folder = self._folder_done or self.folder.text().strip()
        self.wizard.open_folder(folder)
