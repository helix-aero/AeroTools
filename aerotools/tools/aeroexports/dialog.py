"""Aero-Exports window (Qt): tick the orthomosaic and / or the DTM, choose the coordinates and a format, export.

The files go to an `Outputs` folder next to the .psx, with a short name
(`SITE_YYMMDD_3cm_ortho.tif`, `SITE_YYMMDD_3cm_dtm.tif`). If the export already exists,
the window asks: overwrite or make a new version (`_v2`).

Formats: GeoTIFF (3 settings), JPEG 2000, ECW (Global Mapper), or an AutoCAD drawing (TOPO.dxf / TOPO.dwg) that places the
images (JPEG 2000 or ECW) at the right spot in two layers, orthomosaic and DTM. When a program is missing (Global Mapper,
AutoCAD, QGIS), the window says so, offers to point to the program or to buy it, and does not start anything without it.

Coordinates: the project CRS is shown; one can export in another CRS from the list (official catalogue, nothing to
create) with its height system. If the geoid grid is missing, the window offers to download it.

The export itself goes through Metashape's API (msapi.export_raster), which works in the main thread: the
window stays alive thanks to processEvents during the progress callbacks.
"""

import datetime
import os
import shutil
import tempfile
import threading
import traceback

from ... import config, i18n, journal, msapi, theme, ui
from ...qt import QtCore, QtGui, QtWidgets
from . import controls, crs, external, look as looks, naming, pipeline, plan, preview, projdb, projtool, tiling, vertical

SECTION = "aeroexports"
WIDTH, HEIGHT = 1080, 740
SCREEN_MARGIN = 70
WATCH_MS = 400
COARSE_RATIO = 10.0                                  # a default DEM this many times coarser than the ortho is probably the wrong one
KIND_TITLE = {plan.ORTHO: "export.ortho", plan.DEM: "export.dem"}
CM = 100.0
DEFAULT_TEXT = {plan.ORTHO: "export.default_ortho", plan.DEM: "export.default_dem"}
SEVERAL_TEXT = {plan.ORTHO: "export.several_ortho", plan.DEM: "export.several_dem"}
SAME = "same"
NEW_VERSION, OVERWRITE = "new", "overwrite"
TILE_CHOICES = 6


def size_text(nbytes):
    for unit, step in (("GB", 1024.0 ** 3), ("MB", 1024.0 ** 2)):
        if nbytes >= step:
            return "{:.1f} {}".format(nbytes / step, unit)
    return "{:.0f} KB".format(nbytes / 1024.0)


def combo():
    """Drop-down that may be narrower than its longest item (the names of systems and grids are long): the text is cut, not the window."""
    box = ui.combo()
    box.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
    box.setMinimumContentsLength(12)
    return box


class ClickLabel(QtWidgets.QLabel):
    """A label that calls `handler` when clicked (and keeps the click: it does not tick the card around it)."""

    def __init__(self, handler):
        super(ClickLabel, self).__init__()
        self._handler = handler
        self.setCursor(QtCore.Qt.PointingHandCursor)

    def mousePressEvent(self, event):
        event.accept()
        self._handler()


class ExistsDialog(QtWidgets.QDialog):
    """"These files already exist": overwrite, new version, or cancel."""

    def __init__(self, parent, count, next_version):
        super(ExistsDialog, self).__init__(parent)
        self.setWindowTitle(i18n.t("export.title"))
        self.setModal(True)
        self.setMinimumWidth(520)
        self.choice = None
        root = ui.dark_root(self)
        layout = QtWidgets.QVBoxLayout(root)
        pad = theme.PAD_L
        layout.setContentsMargins(pad, pad, pad, pad)
        layout.setSpacing(theme.PAD)
        layout.addWidget(ui.label(i18n.t("export.exists", n=count)))
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(theme.PAD)
        row.addWidget(ui.big_button("export.overwrite", lambda: self._pick(OVERWRITE), "aeroBtn", width=150))
        new = ui.big_button("export.newversion", lambda: self._pick(NEW_VERSION), "aeroGo", width=220)
        new.setText(i18n.t("export.newversion", n=next_version))
        row.addWidget(new)
        row.addStretch(1)
        row.addWidget(ui.big_button("export.cancel", self.reject, "aeroBtn", width=110))
        layout.addLayout(row)

    def _pick(self, choice):
        self.choice = choice
        self.accept()


class ExportDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super(ExportDialog, self).__init__(parent)
        self._busy = False
        self._info = {}
        self._chunk = None                                  # the chunk the window shows: pinned at refresh(), used by everything after
        self._pin = (None, None)                            # (project path, chunk key) of that chunk
        self._chunk_label = ""
        self._sig = None                                    # msapi.raster_signature of the pinned chunk (the timer compares it)
        self._dialog_open = 0                               # a window of ours is open on top (look window, "already exists"): no refresh
        self._psx = ""
        self._native = {}
        self._jobs = []
        self._date = datetime.date.today()
        self._missing = None                                # (tool, text key) when a program is missing
        self._geoid_missing = None                          # id of the chosen grid that is missing (blocks the export)
        self._geoid_busy = False
        self._geoid_states = {}
        self._project_epsg = None
        self._geoid_ids = []                                # ids offered for download (the chosen grid, or those of the zone)
        self._previous_preset = None
        self._tile_pixels = None
        self._preview = None                                # preview.Preview of the DEM (made when the user opens the look window)
        self._preview_key = None
        self._preview_ok = False
        self._crs_picked = False                            # the user chose the export system himself: we stop choosing for him
        self._crs_auto = None                               # EPSG we preselected because a raster is in degrees (None: none)
        self._trap_key = None
        self.tools = {}
        self.setWindowTitle(i18n.t("export.title"))
        self.setModal(False)
        self.resize(*self._size())
        root = ui.dark_root(self)
        columns = QtWidgets.QHBoxLayout(root)
        pad = theme.PAD_L
        columns.setContentsMargins(pad, pad, pad, pad)
        columns.setSpacing(pad)
        # Two panels side by side so that nothing hides below the fold: left = what to export and in which coordinates,
        # right = in which format, under which names, and the button. Each panel scrolls (with a visible bar) only on a small screen.
        left = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(left)
        layout.setContentsMargins(0, 0, theme.PAD, 0)
        layout.setSpacing(theme.PAD + theme.PAD_S)
        columns.addWidget(controls.scroll_page(left, cap=self._panel_cap()), 1)
        divider = QtWidgets.QFrame()
        divider.setFixedWidth(1)
        divider.setStyleSheet("background: {}; border: none;".format(theme.BORDER_SOFT))
        columns.addWidget(divider)
        right_column = QtWidgets.QVBoxLayout()
        right_column.setSpacing(theme.PAD)
        columns.addLayout(right_column, 1)
        right = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, theme.PAD, 0)
        right_layout.setSpacing(theme.PAD + theme.PAD_S)
        right_column.addWidget(controls.scroll_page(right, cap=self._panel_cap()), 1)

        # Which chunk this window works on, always in view: the export is of THIS chunk, whatever is active in Metashape later.
        self.chunk_title = controls.ElidedLabel("aeroTitle")
        layout.addWidget(self.chunk_title)
        layout.addWidget(ui.label(i18n.t("export.what_section"), "aeroSection"))
        self.cards, self.fields, self.native_labels = {}, {}, {}
        self.raster_details, self.raster_warnings = {}, {}
        for kind in plan.KINDS:
            check = QtWidgets.QCheckBox(i18n.t(KIND_TITLE[kind]))
            check.setCursor(QtCore.Qt.PointingHandCursor)
            card = ui.OptionCard(check)
            native = ui.label("", "aeroMeta", wrap=False)
            field = ui.SelectAllEdit()
            field.setFixedWidth(90)
            field.setAlignment(QtCore.Qt.AlignRight)
            field.setMaxLength(8)
            unit = ui.label(i18n.t("export.cm"), "aeroMeta", wrap=False)
            card.grid.setColumnStretch(1, 1)
            card.grid.addWidget(native, 0, 1)
            card.grid.addWidget(ui.label(i18n.t("export.pixel"), "aeroMeta", wrap=False), 0, 2)
            card.grid.addWidget(field, 0, 3)
            card.grid.addWidget(unit, 0, 4)
            check.toggled.connect(self._refresh_names)
            field.textChanged.connect(self._refresh_names)
            # exactly which raster is exported (the chunk's default one), and a warning when it is a poor choice
            detail = controls.WrapText(meta=True)
            card.grid.addWidget(detail, 1, 0, 1, 5)
            warn = ui.label("", "aeroWarn")
            warn.setVisible(False)
            card.grid.addWidget(warn, 2, 0, 1, 5)
            layout.addWidget(card)
            self.cards[kind], self.fields[kind], self.native_labels[kind] = card, field, native
            self.raster_details[kind], self.raster_warnings[kind] = detail, warn
        self._build_look_row()

        # -- coordinates ----------------------------------------------------------------------
        layout.addWidget(ui.label(i18n.t("export.crs_section"), "aeroSection"))
        self.crs = ui.label("", "aeroMeta")
        layout.addWidget(self.crs)
        self.crs_notice = ui.label("", "aeroWarn")                # a raster in degrees: why a system is preselected, or what to pick
        self.crs_notice.setVisible(False)
        layout.addWidget(self.crs_notice)
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(theme.PAD)
        grid.setVerticalSpacing(theme.PAD_S)
        grid.setColumnStretch(1, 1)
        grid.addWidget(ui.label(i18n.t("export.crs_find"), "aeroMeta", wrap=False), 0, 0)
        self.crs_search = QtWidgets.QLineEdit()
        self.crs_search.setPlaceholderText(i18n.t("export.crs_find_hint"))
        self.crs_search.textChanged.connect(self._on_search)
        grid.addWidget(self.crs_search, 0, 1)
        grid.addWidget(ui.label(i18n.t("export.crs_to"), "aeroMeta", wrap=False), 1, 0)
        self.crs_box = combo()
        self.crs_box.activated.connect(self._on_crs)
        grid.addWidget(self.crs_box, 1, 1)
        grid.addWidget(ui.label(i18n.t("export.vertical"), "aeroMeta", wrap=False), 2, 0)
        self.vert_box = combo()
        self.vert_box.activated.connect(self._on_vertical)
        grid.addWidget(self.vert_box, 2, 1)
        layout.addLayout(grid)
        # the two "what gets cut" options, right under the coordinate systems: the empty border (drawings) and the boundary shapes
        self.cad_clip = QtWidgets.QCheckBox(i18n.t("export.cad_clip"))
        self.cad_clip.setToolTip(i18n.t("export.cad_clip_tip"))
        self.cad_clip.setCursor(QtCore.Qt.PointingHandCursor)
        self.cad_clip.setChecked(config.get(SECTION, "cad_clip") is not False)
        self.cad_clip.toggled.connect(self._on_cad_choice)
        layout.addWidget(self.cad_clip)
        self._build_boundary_row(layout)
        self.geoid_card = QtWidgets.QFrame()
        self.geoid_card.setObjectName("aeroOption")
        geoid_box = QtWidgets.QVBoxLayout(self.geoid_card)
        geoid_box.setContentsMargins(14, 10, 14, 10)
        geoid_row = QtWidgets.QHBoxLayout()
        self.geoid_text = ui.label("")
        geoid_row.addWidget(self.geoid_text, 1)
        self.geoid_later = ui.button("export.geoid_later", self._geoid_not_now, width=90)
        geoid_row.addWidget(self.geoid_later)
        self.geoid_button = ui.button("export.geoid_download", self._download_geoid, "aeroRun", width=130)
        geoid_row.addWidget(self.geoid_button)
        geoid_box.addLayout(geoid_row)
        self.geoid_bar = QtWidgets.QProgressBar()
        self.geoid_bar.setObjectName("aeroProgress")
        self.geoid_bar.setTextVisible(False)
        self.geoid_bar.setVisible(False)
        geoid_box.addWidget(self.geoid_bar)
        layout.addWidget(self.geoid_card)
        layout.addStretch(1)

        # -- format -----------------------------------------------------------------------------
        layout = right_layout
        layout.addWidget(ui.label(i18n.t("export.preset"), "aeroSection"))
        self.preset = combo()
        self.preset.setMinimumHeight(40)
        for item in plan.PRESETS:
            self.preset.addItem(i18n.t(item["title"]), item["key"])
        saved = config.get(SECTION, "preset")
        if self.preset.findData(saved) >= 0:
            self.preset.setCurrentIndex(self.preset.findData(saved))
        self.preset.activated.connect(self._on_preset)
        layout.addWidget(self.preset)
        self.preset_tip = ui.label("", "aeroMeta")
        layout.addWidget(self.preset_tip)

        self.cad_box = QtWidgets.QWidget()
        cad = QtWidgets.QGridLayout(self.cad_box)
        cad.setContentsMargins(0, 0, 0, 0)
        cad.setHorizontalSpacing(theme.PAD_L)
        self.cad_format = self._radios(cad, 0, "export.cad_format", (("dxf", "export.cad_dxf"), ("dwg", "export.cad_dwg")),
                                       config.get(SECTION, "cad_format") or "dxf")
        self.cad_images = self._radios(cad, 1, "export.cad_images", (("jp2", "export.img_jp2"), ("ecw", "export.img_ecw")),
                                       config.get(SECTION, "cad_images") or "jp2")
        tips = {"jp2": "export.img_jp2_tip", "ecw": "export.img_ecw_tip"}
        for button in self.cad_images.buttons():
            button.setToolTip(i18n.t(tips[button.property("value")]))
        layout.addWidget(self.cad_box)

        self.tool_card = QtWidgets.QFrame()
        self.tool_card.setObjectName("aeroOption")
        self.tool_card.setProperty("danger", True)
        tool = QtWidgets.QVBoxLayout(self.tool_card)
        tool.setContentsMargins(14, 12, 14, 12)
        self.tool_text = ui.label("")
        tool.addWidget(self.tool_text)
        tool_row = QtWidgets.QHBoxLayout()
        tool_row.setSpacing(theme.PAD)
        self.tool_choose = ui.button("export.choose_exe", self._choose_tool, width=220)
        tool_row.addWidget(self.tool_choose)
        self.tool_buy = ui.button("export.buy_now", self._buy_tool, "aeroRun", width=170)
        tool_row.addWidget(self.tool_buy)
        tool_row.addStretch(1)
        tool_row.addWidget(ui.button("export.cancel", self._cancel_tool, width=110))
        tool.addLayout(tool_row)
        layout.addWidget(self.tool_card)

        # -- advanced options -------------------------------------------------------------------
        self.adv_toggle = ui.button("export.advanced", lambda: self._set_advanced(not self.adv_box.isVisible()), width=200)
        toggle_row = QtWidgets.QHBoxLayout()
        toggle_row.addWidget(self.adv_toggle)
        toggle_row.addStretch(1)
        layout.addLayout(toggle_row)
        self.adv_box = QtWidgets.QWidget()
        adv = QtWidgets.QGridLayout(self.adv_box)
        adv.setContentsMargins(0, 0, 0, 0)
        adv.setHorizontalSpacing(theme.PAD)
        self.tiles_check = QtWidgets.QCheckBox(i18n.t("export.tiles"))
        self.tiles_check.toggled.connect(self._on_tiles)
        adv.addWidget(self.tiles_check, 0, 0)
        self.tile_box = combo()
        self.tile_box.setMinimumWidth(140)
        self.tile_box.activated.connect(self._refresh_names)
        adv.addWidget(self.tile_box, 0, 1)
        self.tile_info = ui.label("", "aeroMeta")
        adv.addWidget(self.tile_info, 0, 2)
        adv.setColumnStretch(2, 1)
        layout.addWidget(self.adv_box)
        self._set_advanced(False)

        layout.addWidget(ui.label(i18n.t("export.names"), "aeroSection"))
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(theme.PAD)
        row.addWidget(ui.label(i18n.t("export.site"), "aeroMeta", wrap=False))
        self.site = QtWidgets.QLineEdit()
        self.site.setMaximumWidth(260)
        self.site.textChanged.connect(self._refresh_names)
        row.addWidget(self.site)
        row.addSpacing(theme.PAD_L)
        row.addWidget(ui.label(i18n.t("export.date"), "aeroMeta", wrap=False))
        self.date = ui.label("", wrap=False)
        row.addWidget(self.date)
        row.addStretch(1)
        layout.addLayout(row)
        self.names = controls.WrapText()                    # wraps anywhere: a path has no spaces, and must never widen the window
        layout.addWidget(self.names)
        self.folder = controls.WrapText(meta=True)
        layout.addWidget(self.folder)
        layout.addStretch(1)

        bottom_layout = right_column                     # the result and the buttons stay in view under the right panel
        self.bar = QtWidgets.QProgressBar()
        self.bar.setObjectName("aeroProgress")
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 1000)
        self.bar.setVisible(False)
        bottom_layout.addWidget(self.bar)
        line = QtWidgets.QHBoxLayout()
        line.setSpacing(theme.PAD_S)
        self.mark = ui.Mark()
        line.addWidget(self.mark, 0, QtCore.Qt.AlignTop)
        self.status = controls.WrapText()
        line.addWidget(self.status, 1)
        bottom_layout.addLayout(line)
        buttons = QtWidgets.QHBoxLayout()
        buttons.setSpacing(theme.PAD)
        self.open_button = ui.big_button("export.open_folder", self._open_folder, width=150)
        buttons.addWidget(self.open_button)
        buttons.addStretch(1)
        self.go_button = ui.big_button("export.go", self._on_export, "aeroGo", width=170)
        buttons.addWidget(self.go_button)
        self.close_button = ui.big_button("export.close", self.close, width=100)
        buttons.addWidget(self.close_button)
        bottom_layout.addLayout(buttons)

        self.refresh()
        # The window follows Metashape by itself: a cheap look at the active chunk and its default rasters, a few times a second.
        self._watch = QtCore.QTimer(self)
        self._watch.setInterval(WATCH_MS)
        self._watch.timeout.connect(self._watch_tick)
        self._watch.start()

    @staticmethod
    def _panel_cap():
        """Widest minimum asked of one panel: half of what a small screen leaves, so the window can always fit the screen."""
        return max(300, (controls.screen_width() - SCREEN_MARGIN - 4 * theme.PAD_L - 1) // 2)

    def showEvent(self, event):
        super(ExportDialog, self).showEvent(event)
        if not getattr(self, "_fitted", False):
            self._fitted = True
            controls.fit_window(self)                       # minimum size from the panels, once the styles (fonts, scaling) are applied

    def changeEvent(self, event):
        super(ExportDialog, self).changeEvent(event)
        if event.type() == QtCore.QEvent.ActivationChange and self.isActiveWindow():
            self._follow_chunk()                            # back in front: is it still what we show?

    def _watch_tick(self):
        """Timer: while the window is visible and idle, notice a chunk or default raster changed in Metashape. Never raises."""
        if not self.isVisible() or self._busy or self._dialog_open:
            return
        try:
            self._follow_chunk()
        except Exception as exc:                            # a read of Metashape that fails must never disturb the window
            journal.write("Aero-Exports: watch: {!r}".format(exc))

    @staticmethod
    def _current_pin():
        return (msapi.document_path(), msapi.chunk_key(msapi.chunk()))

    def _change_reason(self, deep=False):
        """Why the window is out of date: "chunk", "dem", "ortho", "shapes", or None when it still shows what Metashape has.
        `deep` also counts the boundary shapes of the chunk (a loop over its shapes: only done on a click, not in the timer)."""
        if self._current_pin() != self._pin:
            return "chunk"
        current = msapi.chunk()
        signature = msapi.raster_signature(current)
        if signature is not None and self._sig is not None and signature != self._sig:
            for index, reason in ((1, "dem"), (2, "ortho"), (3, "shapes")):
                if signature[index] != self._sig[index]:
                    return reason
            return "chunk"
        if deep and current is not None and msapi.boundary_count(current) != self._info.get("boundaries"):
            return "shapes"
        return None

    def _follow_chunk(self, deep=False):
        """If the active chunk, its default DEM or orthomosaic (a double-click in the Workspace pane) or its boundary shapes are no
        longer what this window shows, show the new state and say so in one line.
        Returns True when it changed (the caller stops there: nothing is previewed or exported that the user has not seen).
        Never during an export: the pinned chunk keeps being exported, whatever is active in Metashape meanwhile."""
        if self._busy:
            return False
        reason = self._change_reason(deep)
        if reason is None:
            return False
        self.refresh()
        if self._chunk is None:
            return True
        key = {"chunk": "export.chunk_changed", "dem": "export.dem_changed", "ortho": "export.ortho_changed",
               "shapes": "export.shapes_changed"}[reason]
        self.status.setText(i18n.t(key, name=self._chunk_label))
        journal.write("Aero-Exports: {} ({}), now {}".format(reason, i18n.t(key, name=self._chunk_label), self._describe()))
        return True

    def _describe(self):
        """One journal line on what the window works on: chunk, default rasters used, boundary clipping."""
        def raster(name):
            item = self._info.get(name + "_default")
            if not item:
                return "none"
            return "key {} '{}' {}x{} px {:g} m/px".format(item["key"], item["label"], item["width"], item["height"], item["resolution"])
        return "chunk '{}' (key {}), DEM {} (of {}), ortho {} (of {}), boundary shapes {}, clip_to_boundary={}".format(
            self._chunk_label, self._pin[1], raster("dem"), self._info.get("n_dems"), raster("ortho"), self._info.get("n_orthos"),
            self._info.get("boundaries"), self._boundary_clip())

    @staticmethod
    def _size():
        area = controls.screen_area()
        return min(WIDTH, area.width() - SCREEN_MARGIN), min(HEIGHT, area.height() - SCREEN_MARGIN)

    def _radios(self, grid, row, title_key, choices, saved):
        """A row of radio buttons (single choice): returns the group, each button carrying its value."""
        group = QtWidgets.QButtonGroup(self)
        holder = QtWidgets.QHBoxLayout()
        holder.setSpacing(theme.PAD_L)
        grid.addWidget(ui.label(i18n.t(title_key), "aeroMeta", wrap=False), row, 0)
        for value, key in choices:
            radio = QtWidgets.QRadioButton(i18n.t(key))
            radio.setProperty("value", value)
            radio.setCursor(QtCore.Qt.PointingHandCursor)
            group.addButton(radio)
            holder.addWidget(radio)
            radio.setChecked(value == saved)
            radio.toggled.connect(self._on_cad_choice)
        if not group.checkedButton():
            group.buttons()[0].setChecked(True)
        holder.addStretch(1)
        grid.addLayout(holder, row, 1)
        return group

    def _build_look_row(self):
        """Second line of the DEM card: the look of the DEM picture (button, small picture, one-line summary) and the option to
        write that picture next to a JPEG 2000 / ECW export."""
        card = self.cards[plan.DEM]
        self.look_row = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(self.look_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.PAD)
        self.look_thumb = ClickLabel(self._open_look)
        self.look_thumb.setFixedSize(112, 76)
        self.look_thumb.setVisible(False)
        row.addWidget(self.look_thumb)
        column = QtWidgets.QVBoxLayout()
        column.setSpacing(theme.PAD_S)
        self.look_text = ui.label("", "aeroMeta")
        column.addWidget(self.look_text)
        line = QtWidgets.QHBoxLayout()
        line.setSpacing(theme.PAD)
        self.look_button = ui.button("export.look_open", self._open_look, width=210)
        line.addWidget(self.look_button)
        self.picture_check = QtWidgets.QCheckBox(i18n.t("export.dem_picture", fmt="JP2"))
        self.picture_check.setToolTip(i18n.t("export.dem_picture_tip"))
        self.picture_check.setCursor(QtCore.Qt.PointingHandCursor)
        self.picture_check.setChecked(bool(config.get(SECTION, "dem_picture")))
        self.picture_check.toggled.connect(self._on_picture)
        line.addWidget(self.picture_check)
        line.addStretch(1)
        column.addLayout(line)
        row.addLayout(column, 1)
        card.grid.addWidget(self.look_row, 3, 0, 1, 5)
        try:
            from . import previewwin  # noqa: F401  (needs numpy)
            self._preview_ok = True
        except ImportError:
            self.look_button.setEnabled(False)
            self.look_button.setToolTip(i18n.t("export.look_no_numpy"))

    def _picture_wanted(self):
        """True when the coloured DEM picture is asked next to a JPEG 2000 / ECW export."""
        return self.preset.currentData() in plan.PICTURE_PRESETS and self.picture_check.isChecked()

    def _refresh_look_row(self):
        """Show the look line when the DEM is ticked and the format has a coloured DEM picture (drawing, JPEG 2000, ECW)."""
        key = self.preset.currentData()
        self.look_row.setVisible(self.cards[plan.DEM].button.isChecked() and (key == plan.CAD or key in plan.PICTURE_PRESETS))
        self.picture_check.setVisible(key in plan.PICTURE_PRESETS)
        if key in plan.PICTURE_PRESETS:
            self.picture_check.setText(i18n.t("export.dem_picture", fmt=plan.PICTURE_PRESETS[key].upper()))
        self._refresh_look_text()

    def _refresh_look_text(self):
        """One-line summary of the saved look; and the small picture once the preview exists."""
        look = looks.load()
        self.look_text.setText(i18n.t("export.look_now", look=self._look_summary(look)))
        model = self._preview_model_cached() if self._preview_ok else None
        if model is not None:
            from . import previewwin
            self.look_thumb.setPixmap(previewwin.thumbnail(model.render(look, self._void())))
        self.look_thumb.setVisible(model is not None)

    @staticmethod
    def _look_summary(look):
        palette = looks.label(looks.palette(look.palette))
        shading = i18n.t({looks.NONE: "export.shade_none", looks.SUN: "export.shade_sun", looks.LIGHTS4: "export.shade_lights4",
                          looks.SLOPE: "export.shade_slope"}[look.shading])
        return i18n.t("export.look_summary", palette=palette, shading=shading)

    def _void(self):
        """What the preview paints where the DEM has no data: nothing when the drawing crops the white background, else white."""
        return "transparent" if self.preset.currentData() == plan.CAD and self.cad_clip.isChecked() else "white"

    def _on_picture(self, *_args):
        config.put(SECTION, "dem_picture", bool(self.picture_check.isChecked()))
        self._check_tools()
        self._refresh_names()

    def _preview_signature(self):
        """What the small DEM was made from: the project file (and when it was saved), the DEM extent and size. A change makes a new one."""
        try:
            saved = os.path.getmtime(self._psx) if self._psx else 0
        except OSError:
            saved = 0
        return (self._psx, self._pin[1], saved, tuple(self._info.get("box_dem") or ()), tuple(self._info.get(plan.DEM) or ()))

    def _preview_model_cached(self):
        return self._preview if self._preview is not None and self._preview_key == self._preview_signature() else None

    def _preview_model(self):
        """The small DEM of the preview: the DEM already exported next to the project if it is still current, else a small export
        by Metashape (a few seconds at most; no progress callback into Metashape). None, with the reason shown, if it fails."""
        if self._follow_chunk(deep=True):                   # another chunk is active now: show it first, the user decides
            return None
        cached = self._preview_model_cached()
        if cached is not None:
            return cached
        chunk = self._chunk                                 # the pinned chunk, never a fresh msapi.chunk()
        if chunk is None or not self._info.get(plan.DEM):
            self._finish(i18n.t("export.no_chunk"), "error")
            return None
        self.mark.show_kind(None)
        self.status.setText(i18n.t("export.look_loading"))
        QtWidgets.QApplication.processEvents()              # before the call into Metashape, never inside it
        work, model, problem = None, None, ""
        try:
            box, native = self._info.get("box_dem"), self._info.get(plan.DEM)
            # an export already on disk is reused only if it is this chunk's DEM (same size, pixel and extent), not another chunk's
            # An export already on disk can have been made with or without the boundary clip: it is reused only when the chunk has
            # no boundary shape (then both are the same). With shapes, or when unsure, a small export is made with the setting shown.
            reusable = box and native and self._info.get("boundaries") == 0
            path = preview.usable_export([job.path for job in self._jobs if job.kind == plan.DEM] if reusable else [],
                                         self._psx, box, native)
            if path is None:
                work = tempfile.mkdtemp(prefix="aero_preview_")
                path = preview.fetch_small(msapi.export_raster, chunk, box, work, clip=self._boundary_clip())
            model = preview.Preview.from_tiff(path, exact=msapi.dem_range(chunk))
            journal.write("Aero-Exports: preview of {}; DEM footprint: {:.1%} of the pixels have data".format(
                self._describe(), 1.0 - float(model.void.mean()) if model.valid else 0.0))
            if not model.valid:
                problem = i18n.t("export.look_empty")
        except (ValueError, OSError, MemoryError) as exc:
            problem = i18n.t("export.look_failed", error=exc)
            journal.write("Aero-Exports: DEM preview: {!r}".format(exc))
        finally:
            if work:
                shutil.rmtree(work, ignore_errors=True)
        self.status.setText("")
        if problem:
            self._finish(problem, "error")
            return None
        self._preview, self._preview_key = model, self._preview_signature()
        return model

    def _open_look(self):
        """Open the window where the colours and the shading are chosen; keep (and save) the look if the user accepts it."""
        if self._busy or not self._preview_ok:
            return
        from . import previewwin
        model = self._preview_model()
        if model is None:
            return
        converted = bool(self._vertical_choice())
        notes = [i18n.t("export.look_height_note")] if converted else []
        if self._clips_boundary():
            notes.append(i18n.t("export.look_clipped"))
        window = previewwin.PreviewWindow(self, model, looks.load(), void=self._void(), note=" ".join(notes), custom_allowed=not converted)
        self._dialog_open += 1
        try:
            ui.run(window)
        finally:
            self._dialog_open -= 1
        if window.result_look is not None:
            looks.save(window.result_look)
        self._refresh_look_text()

    @staticmethod
    def _value(group):
        button = group.checkedButton()
        return button.property("value") if button else None

    # -- reading the project --------------------------------------------------------------

    def refresh(self):
        """Read the project again (what exists, its resolution, its name): on opening and each time it comes back to the front."""
        if self._busy:
            return
        # Pin the chunk with what we read from it: everything after (preview, export, clip, names) uses this chunk and this info,
        # so a chunk changed in Metashape meanwhile can never mix its raster with another chunk's box, system or date.
        self._psx = msapi.document_path()
        self._chunk = msapi.chunk()
        self._pin = (self._psx, msapi.chunk_key(self._chunk))
        self._chunk_label = msapi.chunk_label(self._chunk)
        self._info = msapi.raster_info(self._chunk)
        self._sig = msapi.raster_signature(self._chunk)
        self._preview, self._preview_key = None, None       # the small DEM of the look window belongs to the previous chunk
        self._jobs = []
        self.chunk_title.setFullText(i18n.t("export.chunk", name=self._chunk_label) if self._chunk is not None
                                     else i18n.t("export.chunk_none"))
        crs_object = self._info.get("crs_object")
        self._project_epsg = msapi.project_epsg(crs_object) if crs_object is not None else None
        self._detect_tools()
        for kind in plan.KINDS:
            found = self._info.get(kind)
            card, field, native = self.cards[kind], self.fields[kind], self.native_labels[kind]
            card.button.blockSignals(True)
            field.blockSignals(True)
            if found:
                self._native[kind] = found[0]
                card.button.setEnabled(True)
                card.button.setChecked(True)
                native.setText(i18n.t("export.native", size=naming.format_resolution(found[0])))
                field.setEnabled(True)
                field.setText(self._cm_text(found[0]))
            else:
                self._native[kind] = 0.0
                card.button.setEnabled(False)
                card.button.setChecked(False)
                native.setText(i18n.t("export.not_built"))
                field.setEnabled(False)
                field.setText("")
            card.button.blockSignals(False)
            field.blockSignals(False)
            card.sync()
        self._show_rasters()
        self._show_boundary_row()
        self.crs.setText(self._crs_text())
        key = (self._psx, tuple(k for k in plan.KINDS if self._info.get("geographic_" + k)))
        if key != self._trap_key:                           # another project, or another raster in degrees: choose afresh
            self._trap_key, self._crs_picked, self._crs_auto = key, False, None
        self._fill_crs()
        self.site.blockSignals(True)
        self.site.setText(naming.guess_site(self._psx) if self._psx else "")
        self.site.blockSignals(False)
        day, source = naming.flight_date(self._info.get("dates"))
        self._date = day
        self.date.setText(naming.format_date(day))
        self.date.setToolTip(i18n.t("export.date_today") if source == "today" else "")
        self._on_preset()
        self.status.setText("")
        self.mark.show_kind(None)
        self.bar.setVisible(False)
        self._fill_tiles()
        self._refresh_names()

    @staticmethod
    def _cm_text(pixel_m):
        """Pixel size in cm, without useless zeros: 0.5 m -> "50", 0.0125 m -> "1.25"."""
        text = "{:.2f}".format(pixel_m * CM).rstrip("0").rstrip(".")
        return text or "0"

    def _is_native(self, kind):
        """True if the field has not been touched (or is empty or invalid): Metashape's grid is then kept."""
        text = self.fields[kind].text().replace(",", ".").strip()
        try:
            value = float(text)
        except ValueError:
            return True
        return value <= 0 or text == self._cm_text(self._native.get(kind, 0.0))

    def _pixel(self, kind):
        """Wanted pixel size in metres (the native size if the field is untouched, empty or invalid)."""
        if self._is_native(kind):
            return self._native.get(kind, 0.0)
        return float(self.fields[kind].text().replace(",", ".").strip()) / CM

    # -- which raster, and the boundary clip ----------------------------------------------

    def _show_rasters(self):
        """Under each card: exactly which raster is exported (the chunk's default one), and a warning when the DEM is coarse."""
        for kind in plan.KINDS:
            item = self._info.get(kind + "_default")
            detail, warn = self.raster_details[kind], self.raster_warnings[kind]
            text, warning = "", ""
            if item:
                text = i18n.t(DEFAULT_TEXT[kind], w=item["width"], h=item["height"], cm=self._cm_text(item["resolution"]))
                count = self._info.get("n_dems" if kind == plan.DEM else "n_orthos") or 0
                if count > 1:
                    text += " " + i18n.t(SEVERAL_TEXT[kind], n=count)
            ortho = self._info.get("ortho_default")
            if kind == plan.DEM and item and ortho and ortho["resolution"] > 0 and item["resolution"] / ortho["resolution"] > COARSE_RATIO:
                warning = i18n.t("export.dem_coarse", cm=self._cm_text(item["resolution"]))
            detail.setText(text)
            warn.setText(warning)
            warn.setVisible(bool(warning))

    def _build_boundary_row(self, layout):
        """A tick box, shown only when the chunk has boundary shapes: Metashape cuts an export to them unless told not to."""
        self.boundary_row = QtWidgets.QWidget()
        column = QtWidgets.QVBoxLayout(self.boundary_row)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(theme.PAD_S)
        line = QtWidgets.QHBoxLayout()
        line.setSpacing(theme.PAD_S)
        self.boundary_check = QtWidgets.QCheckBox()
        self.boundary_check.setToolTip(i18n.t("export.boundary_tip"))
        self.boundary_check.setCursor(QtCore.Qt.PointingHandCursor)
        self.boundary_check.setChecked(config.get(SECTION, "clip_to_boundary_shapes") is True)      # off by default: the export covers the whole default DEM, as seen in Metashape
        self.boundary_check.toggled.connect(self._on_boundary)
        line.addWidget(self.boundary_check, 0, QtCore.Qt.AlignTop)
        # the text wraps (a tick box's own text never does, and would widen the panel at large font sizes)
        self.boundary_label = ClickLabel(self.boundary_check.toggle)
        self.boundary_label.setText(i18n.t("export.boundary"))
        self.boundary_label.setToolTip(i18n.t("export.boundary_tip"))
        self.boundary_label.setWordWrap(True)
        line.addWidget(self.boundary_label, 1)
        column.addLayout(line)
        self.boundary_note = controls.WrapText(meta=True)
        column.addWidget(self.boundary_note)
        self.boundary_row.setVisible(False)
        layout.addWidget(self.boundary_row)

    def _show_boundary_row(self):
        count = self._info.get("boundaries")
        self.boundary_row.setVisible(bool(count))
        self._refresh_boundary_note()

    def _refresh_boundary_note(self):
        count = self._info.get("boundaries") or 0
        if not count:
            self.boundary_note.setText("")
        elif not self.boundary_check.isChecked():
            self.boundary_note.setText(i18n.t("export.boundary_off"))
        else:
            self.boundary_note.setText(i18n.t("export.boundary_one" if count == 1 else "export.boundary_many", n=count))

    def _clips_boundary(self):
        """True when the exports are cut to boundary shapes that exist in the chunk."""
        return bool(self._info.get("boundaries")) and self.boundary_check.isChecked()

    def _boundary_clip(self):
        """The `clip_to_boundary` value for EVERY Metashape export of this window (preview, rasters, footprint mask). Without boundary
        shape, or when unsure, it is Metashape's own default (True): the result is the same as no clip."""
        if not self._info.get("boundaries"):
            return True
        return bool(self.boundary_check.isChecked())

    def _on_boundary(self, *_args):
        config.put(SECTION, "clip_to_boundary_shapes", bool(self.boundary_check.isChecked()))
        self._preview, self._preview_key = None, None       # the small DEM was made with the other setting
        self._refresh_boundary_note()
        self._refresh_look_text()

    # -- external software ----------------------------------------------------------------

    def _detect_tools(self):
        self.tools = {"gm": external.find_global_mapper(config.get(SECTION, "path_gm") or ""),
                      "acad": external.find_autocad(config.get(SECTION, "path_acad") or ""),
                      "gdal": external.find_gdal(config.get(SECTION, "path_gdal") or "")}

    def _requirement(self):
        """(tool, text key) of the first program that is missing for the chosen format, or None."""
        key = self.preset.currentData()
        if key == "ecw" and not self.tools["gm"]:
            return "gm", "export.need_ecw"
        if key == "jp2" and self._picture_wanted() and not (self.tools["gdal"] or self.tools["gm"]):
            return "gdal", "export.need_jp2"                # the coloured DEM picture needs an encoder (the ortho can use Metashape's)
        if key == plan.CAD:
            if self._value(self.cad_images) == "ecw" and not self.tools["gm"]:
                return "gm", "export.need_ecw"
            if self._value(self.cad_images) == "jp2" and not (self.tools["gdal"] or self.tools["gm"]):
                return "gdal", "export.need_jp2"
            if self._value(self.cad_format) == "dwg" and not self.tools["acad"]:
                return "acad", "export.need_dwg"
        return None

    def _check_tools(self):
        missing = self._requirement()
        self._missing = missing
        self.tool_card.setVisible(bool(missing))
        if missing:
            tool, key = missing
            self.tool_text.setText(i18n.t(key))
            self.tool_buy.setText(i18n.t({"gm": "export.buy_gm", "acad": "export.buy_acad", "gdal": "export.get_qgis"}[tool]))
        self._refresh_buttons()

    def _choose_tool(self):
        if not self._missing:
            return
        tool = self._missing[0]
        filters = {"gm": "global_mapper.exe", "acad": "acad.exe accoreconsole.exe", "gdal": "gdal_translate.exe"}[tool]
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, i18n.t("export.choose_exe"), "",
                                                        "{} ({})".format(filters.split()[0], filters))
        if not path:
            return
        if tool == "gdal":
            path = os.path.dirname(path)
        config.put(SECTION, {"gm": "path_gm", "acad": "path_acad", "gdal": "path_gdal"}[tool], path)
        self._detect_tools()
        self._check_tools()
        if self._missing and self._missing[0] == tool:
            self.tool_text.setText(i18n.t("export.exe_invalid") + "\n\n" + i18n.t(self._missing[1]))

    def _buy_tool(self):
        if self._missing:
            url = {"gm": external.GLOBAL_MAPPER_URL, "acad": external.AUTOCAD_URL, "gdal": external.QGIS_URL}[self._missing[0]]
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))

    def _cancel_tool(self):
        """Give up: back to the last format that works."""
        self.preset.setCurrentIndex(max(0, self.preset.findData("full")))
        if self._value(self.cad_images) == "ecw" and not self.tools["gm"]:
            self.cad_images.buttons()[0].setChecked(True)       # JPEG 2000
        if self._value(self.cad_format) == "dwg" and not self.tools["acad"]:
            self.cad_format.buttons()[0].setChecked(True)       # DXF
        self._on_preset()

    # -- coordinates ----------------------------------------------------------------------

    def _fill_crs(self):
        """Systems offered: those of the project's location (PROJ's EPSG database), or the result of the search typed."""
        center = self._info.get("center")
        lon, lat = center if center else (None, None)
        text = self.crs_search.text().strip()
        previous = self.crs_box.currentData()
        self.crs_box.blockSignals(True)
        self.crs_box.clear()
        self.crs_box.addItem(i18n.t("export.crs_same"), SAME)
        shown = projdb.candidates(lon, lat, text or None) if projdb.available() else []
        own = self._project_epsg
        if own and not text and all(code != own for code, _name in shown):
            row = projdb.candidates(lon, lat, text=str(own), limit=1)
            shown = row + shown
        for epsg, name in shown:
            self.crs_box.addItem("{}  (EPSG:{})".format(name, epsg), epsg)
        index = self.crs_box.findData(previous) if text or previous != SAME else 0
        self.crs_box.setCurrentIndex(max(0, index))
        self.crs_box.blockSignals(False)
        self._sync_auto_crs(fill=False)
        self._fill_vertical()

    def _on_search(self, *_args):
        self._fill_crs()
        self._refresh_names()

    def _fill_vertical(self):
        """Altitudes offered: those that go with the chosen horizontal CRS, at the project's location (see vertical.py)."""
        center = self._info.get("center")
        lon, lat = center if center else (None, None)
        self.vert_box.blockSignals(True)
        self.vert_box.clear()
        epsg = self.crs_box.currentData()
        if epsg in (None, SAME):
            self.vert_box.addItem(i18n.t("export.vertical_same"), SAME)
            self.vert_box.setEnabled(False)
        else:
            for spec in vertical.compatible_verticals(int(epsg), lon, lat, folder=crs.own_dir()):
                self.vert_box.addItem(crs.label(spec), spec["id"])
            self.vert_box.setEnabled(not self._busy)
        self.vert_box.blockSignals(False)
        self._check_geoid()

    def _on_crs(self, *_args):
        self._crs_picked = True                             # his choice, even "Same as the project": we leave it alone
        if self.crs_box.currentData() != self._crs_auto:
            self._crs_auto = None
        self._fill_vertical()
        self._refresh_names()

    # -- a raster in degrees --------------------------------------------------------------

    def _crs_text(self):
        """The coordinate system(s) of the project: one line, or one per raster when the ortho and the DEM differ (degrees next to metres)."""
        info = self._info
        names = {kind: str(info["crs_" + kind].name) for kind in plan.KINDS
                 if info.get("crs_" + kind) is not None and info.get(kind)}
        if len(set(names.values())) > 1:
            return i18n.t("export.crs_each", ortho=names.get(plan.ORTHO, "-"), dem=names.get(plan.DEM, "-"))
        return i18n.t("export.crs", crs=info["crs"]) if info.get("crs") else ""

    def _degrees_kinds(self):
        """The ticked rasters whose system is in degrees (a pixel in cm and a CAD drawing cannot use them)."""
        return [kind for kind in plan.KINDS if self.cards[kind].button.isChecked() and self._info.get(kind)
                and self._info.get("geographic_" + kind)]

    def _auto_epsg(self):
        """The projected system to suggest when a raster is in degrees: the DEM's own system when it is in metres (the user already
        works in it), else the best one for the place from the list. None when nothing can be said."""
        dem = self._info.get("crs_dem")
        if dem is not None and self._info.get(plan.DEM) and not self._info.get("geographic_dem"):
            code = msapi.project_epsg(dem)
            if code:
                return code
        lon, lat = self._info.get("center") or (None, None)
        return projdb.best_projected(lon, lat)

    def _crs_row(self, epsg):
        """(EPSG, name) of a system from the catalogue, or None."""
        lon, lat = self._info.get("center") or (None, None)
        rows = projdb.candidates(lon, lat, text=str(epsg), limit=1) if epsg and projdb.available() else []
        return rows[0] if rows else None

    def _select_crs(self, epsg):
        """Select `epsg` in the "Export in" list, adding it when the list does not hold it (a DEM's own system, a search in progress)."""
        index = self.crs_box.findData(epsg)
        if index < 0:
            row = self._crs_row(epsg)
            if row is None:
                return False
            self.crs_box.insertItem(1, "{}  (EPSG:{})".format(row[1], row[0]), epsg)
            index = 1
        self.crs_box.blockSignals(True)
        self.crs_box.setCurrentIndex(index)
        self.crs_box.blockSignals(False)
        return True

    def _sync_auto_crs(self, fill=True):
        """Keep "Export in" sensible: with a ticked raster in degrees and no choice of the user yet, suggest a projected system
        instead of "Same as the project"; when no such raster is ticked any more, give back the project's system we had replaced."""
        if not hasattr(self, "crs_notice"):
            return
        if self.crs_search.text().strip():
            self._refresh_crs_notice()
            return
        current = self.crs_box.currentData()
        changed = False
        if self._degrees_kinds() and not self._crs_picked and current in (None, SAME):
            epsg = self._auto_epsg()
            if epsg is not None and self._select_crs(epsg):
                self._crs_auto, changed = epsg, True
        elif not self._degrees_kinds() and self._crs_auto is not None and current == self._crs_auto:
            self.crs_box.blockSignals(True)
            self.crs_box.setCurrentIndex(0)
            self.crs_box.blockSignals(False)
            self._crs_auto, changed = None, True
        self._refresh_crs_notice()
        if changed and fill:
            self._fill_vertical()

    def _degrees_sentence(self):
        kinds = self._degrees_kinds()
        key = "export.crs_deg_both" if len(kinds) > 1 else ("export.crs_deg_ortho" if kinds == [plan.ORTHO] else "export.crs_deg_dem")
        return i18n.t(key)

    def _degrees_advice(self):
        """"Pick a projected system", with an example from the list when there is one."""
        row = self._crs_row(self._auto_epsg())
        return i18n.t("export.crs_deg_pick_example", name=row[1]) if row else i18n.t("export.crs_deg_pick")

    def _refresh_crs_notice(self):
        """One short notice under the coordinate system when a ticked raster is in degrees."""
        if not self._degrees_kinds():
            self.crs_notice.setVisible(False)
            return
        current = self.crs_box.currentData()
        if current in (None, SAME):
            text = self._degrees_sentence() + " " + self._degrees_advice()
        elif current == self._crs_auto:
            name = self.crs_box.currentText().split("  (EPSG")[0]
            text = self._degrees_sentence() + " " + i18n.t("export.crs_deg_auto", name=name)
        else:
            self.crs_notice.setVisible(False)
            return
        self.crs_notice.setText(text)
        self.crs_notice.setVisible(True)

    def _degrees_blocks(self, jobs):
        """True when the export would ask for metres from a raster in degrees: a drawing, a pixel in cm or tiles, with no projected system."""
        if self.crs_box.currentData() not in (None, SAME):
            return False
        kinds = [kind for kind in self._degrees_kinds() if any(job.kind == kind for job in jobs)]
        if not kinds:
            return False
        return self.preset.currentData() == plan.CAD or bool(self._tile_m()) or any(not self._is_native(kind) for kind in kinds)

    def _on_vertical(self, *_args):
        self._check_geoid()
        self._refresh_names()

    def _check_geoid(self):
        """State of the chosen height grid in our folder: missing or stale = orange banner, export frozen, download button.
        Otherwise, offers the grids of the project's zone that are missing (same banner, "Later"; Export makes it
        flash until a choice is made)."""
        if self._geoid_busy:
            return
        choice = self.vert_box.currentData()
        self._geoid_missing = None
        self._geoid_ids = []
        self._geoid_states = {}
        text, blocking = "", False
        lon, lat = self._info.get("center") or (None, None)
        if choice not in (None, SAME, "ellipsoid"):
            found = crs.check_geoid_presence(choice)
            spec = found["spec"]
            if found["status"] in ("missing", "stale"):
                self._geoid_missing = choice
                self._geoid_ids = [choice] if spec.get("url") else []
                self._geoid_states[choice] = found["status"]
                key = "export.geoid_missing" if found["status"] == "missing" else "export.geoid_stale"
                text = i18n.t(key, name=crs.label(spec), size=size_text(spec.get("size", 0)))
                blocking = True
        if not self._geoid_missing:
            declined = set(config.get(SECTION, "geoid_declined") or [])
            todo = [(spec, state) for spec, state in crs.zone_grids(self._project_epsg, lon, lat) if spec["id"] not in declined]
            if todo:
                self._geoid_ids = [spec["id"] for spec, _state in todo]
                self._geoid_states = {spec["id"]: state for spec, state in todo}
                text = i18n.t("export.geoid_zone", names=", ".join(crs.label(spec) for spec, _state in todo),
                              size=size_text(sum(spec["size"] for spec, _state in todo)))
        self._style_geoid()
        self.geoid_text.setText(text)
        self.geoid_button.setText(i18n.t("export.geoid_download" if len(self._geoid_ids) < 2 else "export.geoid_download_all"))
        self.geoid_button.setEnabled(bool(self._geoid_ids))
        self.geoid_later.setVisible(not blocking)
        self.geoid_bar.setVisible(False)
        self.geoid_card.setVisible(bool(text))
        self._refresh_buttons()

    def _style_geoid(self, on=True):
        """Banner of the missing grids in orange (the theme's alert); `on=False` turns it off (used for the flashing)."""
        self.geoid_card.setProperty("on", on)
        self.geoid_card.setProperty("danger", True)
        self.geoid_card.style().unpolish(self.geoid_card)
        self.geoid_card.style().polish(self.geoid_card)

    def _flash_geoid(self, steps=6):
        """Make the banner flash: an export was attempted while a decision on the grids is awaited."""
        self._style_geoid(steps % 2 == 1 or steps == 0)
        if steps > 0:
            QtCore.QTimer.singleShot(130, lambda: self._flash_geoid(steps - 1))

    def _geoid_not_now(self):
        """"Later": these grids will not be offered again on this workstation (the choice of a missing grid is still flagged)."""
        old = list(config.get(SECTION, "geoid_declined") or [])
        config.put(SECTION, "geoid_declined", sorted(set(old) | set(self._geoid_ids)))
        self._check_geoid()

    def _download_geoid(self):
        ids = list(self._geoid_ids)
        if not ids or self._geoid_busy:
            return
        self._geoid_busy = True
        self.geoid_button.setEnabled(False)
        self.geoid_later.setVisible(False)
        total = sum(crs.vertical_by_id(vid).get("size", 0) for vid in ids) or 1
        self.geoid_bar.setRange(0, total)
        self.geoid_bar.setValue(0)
        self.geoid_bar.setVisible(True)
        folder = crs.own_dir()
        states = dict(self._geoid_states)
        state = {"done": 0, "index": 0, "errors": []}

        def work():
            for index, vid in enumerate(ids):
                state["index"] = index
                before = state["done"]
                try:
                    path = crs.download_geoid(vid, folder, overwrite=states.get(vid) == "stale",
                                              progress=lambda got, _t, base=before: state.update(done=base + got))
                    journal.write("Aero-Exports: geoid downloaded: {}".format(path))
                except FileExistsError:
                    pass
                except Exception as exc:
                    state["errors"].append("{}: {}".format(vid, str(exc) or repr(exc)))
                    journal.write("Aero-Exports: geoid {}: {}".format(vid, state["errors"][-1]))
                state["done"] = before + crs.vertical_by_id(vid).get("size", 0)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        while thread.is_alive():
            name = crs.label(crs.vertical_by_id(ids[state["index"]]))
            self.geoid_text.setText(i18n.t("export.geoid_progress", name=name, index=state["index"] + 1, total=len(ids)))
            self.geoid_bar.setValue(min(state["done"], total))
            QtWidgets.QApplication.processEvents()
            thread.join(0.1)
        self._geoid_busy = False
        self._check_geoid()
        if state["errors"]:
            self._style_geoid()
            self.geoid_text.setText(i18n.t("export.geoid_failed", error="; ".join(state["errors"])))
            self.geoid_card.setVisible(True)
            self.geoid_button.setEnabled(True)
            return
        self.status.setText(i18n.t("export.geoid_done", name=", ".join(ids)))

    def _vertical_choice(self):
        """Identifier of the height grid to apply to the DTMs, or None (project CRS, or ellipsoidal heights)."""
        choice = self.vert_box.currentData()
        return None if choice in (None, SAME, "ellipsoid") or self.crs_box.currentData() == SAME else choice

    def _target(self):
        """(output horizontal CRS or None, error text). Heights are ellipsoidal as they leave Metashape; the chosen
        altitude is then computed by AeroTools."""
        epsg = self.crs_box.currentData()
        if epsg in (None, SAME):
            return None, ""
        target = msapi.target_crs(int(epsg))
        if target is None:
            return None, i18n.t("export.crs_refused")
        return target, ""

    # -- advanced options -----------------------------------------------------------------

    def _set_advanced(self, shown):
        self.adv_box.setVisible(shown)
        self.adv_toggle.setText(("▾ " if shown else "▸ ") + i18n.t("export.advanced"))

    def _fill_tiles(self):
        """Round tile sizes, compatible with the chosen pixels, around a third of the largest extent."""
        pixels = [round(self._pixel(k), 4) for k in plan.KINDS if self.cards[k].button.isChecked() and self._pixel(k) > 0]
        if pixels == self._tile_pixels and self.tile_box.count():
            return                                         # same pixels: the size already chosen is kept
        self._tile_pixels = pixels
        self.tile_box.blockSignals(True)
        self.tile_box.clear()
        boxes = [self._info.get("box_" + k) for k in plan.KINDS if self._info.get("box_" + k)]
        if pixels and boxes:
            side = max(max(b[2] - b[0], b[3] - b[1]) for b in boxes)
            try:
                for meters in tiling.nice_tile_sizes(pixels, max(side / 3.0, 10.0), TILE_CHOICES):
                    self.tile_box.addItem(i18n.t("export.tile_m", m=meters), float(meters))
            except Exception:
                pass
        self.tile_box.blockSignals(False)
        self.tiles_check.setEnabled(self.tile_box.count() > 0)
        self.tile_box.setEnabled(self.tiles_check.isChecked() and self.tile_box.count() > 0)

    def _on_tiles(self, *_args):
        self.tile_box.setEnabled(self.tiles_check.isChecked() and self.tile_box.count() > 0)
        self._refresh_names()

    def _tile_m(self):
        if self.tiles_check.isChecked() and self.tile_box.count():
            return self.tile_box.currentData()
        return None

    # -- names ------------------------------------------------------------------------------

    def _on_preset(self, *_args):
        key = self.preset.currentData()
        self.preset_tip.setText(i18n.t(plan.preset(key)["tip"]))
        if self.sender() is self.preset:
            self._previous_preset = config.get(SECTION, "preset")
            config.put(SECTION, "preset", key)
        self.cad_box.setVisible(key == plan.CAD)
        self.cad_clip.setVisible(key == plan.CAD)               # "Crop white background" only concerns the drawings
        self._check_tools()
        self._refresh_names()

    def _on_cad_choice(self, *_args):
        if self._value(self.cad_format):
            config.put(SECTION, "cad_format", self._value(self.cad_format))
        if self._value(self.cad_images):
            config.put(SECTION, "cad_images", self._value(self.cad_images))
        config.put(SECTION, "cad_clip", bool(self.cad_clip.isChecked()))
        self._check_tools()
        self._refresh_names()

    def _cad(self):
        return {"format": self._value(self.cad_format) or "dxf", "images": self._value(self.cad_images) or "jp2",
                "clip": bool(self.cad_clip.isChecked())}

    def _wanted(self):
        return {kind: self._pixel(kind) for kind in plan.KINDS
                if self.cards[kind].button.isChecked() and self._pixel(kind) > 0}

    def _folder(self):
        return naming.outputs_folder(self._psx) if self._psx else ""

    def _plan(self, version=1):
        """List of the files for a version number (1 = base names; None = first free number)."""
        wanted = self._wanted()
        return plan.build_jobs(self.site.text().strip(), self._date, wanted, self.preset.currentData(), self._folder(),
                               native={k for k in wanted if self._is_native(k)}, cad=self._cad(),
                               version=version, dem_picture=self._picture_wanted())

    def _existing(self, jobs):
        return [path for path in plan.planned_files(jobs, self.preset.currentData(), self._cad()) if os.path.exists(path)]

    def _refresh_names(self, *_args):
        for kind in plan.KINDS:
            self.fields[kind].setEnabled(self.cards[kind].button.isChecked() and bool(self._info.get(kind)))
        if hasattr(self, "look_row"):
            self._refresh_look_row()
        if hasattr(self, "crs_notice"):
            self._sync_auto_crs()
        if hasattr(self, "tile_box"):
            self._fill_tiles()
        self._jobs, text = [], ""
        folder = self._folder()
        wanted = self._wanted()
        site = self.site.text().strip()
        if not self._psx:
            text = i18n.t("export.unsaved")
        elif not self._info.get(plan.ORTHO) and not self._info.get(plan.DEM):
            text = i18n.t("export.nothing_built")
        elif not wanted:
            text = i18n.t("export.nothing")
        elif not naming.sanitize_token(site):
            text = i18n.t("export.site_missing")
        else:
            try:
                jobs = self._plan(1)
                self._jobs = jobs
                lines = []
                key = self.preset.currentData()
                if key == plan.CAD:
                    lines.append(plan.cad_dxf_name(1, "dxf"))
                    if self._cad()["format"] == "dwg":
                        lines.append(plan.cad_dxf_name(1, "dwg"))
                for job in jobs:
                    lines.append(os.path.relpath(job.path, folder))
                    if job.extra and job.extra.get("view_path"):
                        lines.append(os.path.relpath(job.extra["view_path"], folder))
                    lines.extend(plan.aux_names(job.name, job.spec))
                text = "\n".join(lines)
                if self._tile_m():
                    text += "\n" + i18n.t("export.tiles_note", m=int(self._tile_m()))
                if self._existing(jobs):
                    text += "\n" + i18n.t("export.exists_note", n=len(self._existing(jobs)))
                too_long = naming.check_path_length(folder, [os.path.relpath(j.path, folder) for j in jobs])
                if too_long:
                    self._jobs = []
                    text = i18n.t("export.too_long")
            except ValueError as exc:
                self._jobs = []
                text = str(exc)
        self.names.setText(text)
        self.folder.setText(i18n.t("export.folder", path=folder) if folder else "")
        self._refresh_buttons()

    def _refresh_buttons(self):
        blocked = bool(self._missing) or bool(self._geoid_missing)
        self.go_button.setEnabled(bool(self._jobs) and not self._busy and not blocked)
        self.close_button.setEnabled(not self._busy)
        self.open_button.setEnabled(bool(self._folder()) and os.path.isdir(self._folder()))
        for widget in (self.preset, self.site, self.crs_box):
            widget.setEnabled(not self._busy)
        self.vert_box.setEnabled(not self._busy and self.crs_box.currentData() != SAME)

    # -- export -----------------------------------------------------------------------------

    def _on_export(self):
        if self._busy:
            return
        if self._follow_chunk(deep=True):                   # never export silently on a chunk the user has not seen
            return
        if not self._jobs:
            return
        chunk = self._chunk                                 # the pinned chunk, the one named at the top of the window
        if chunk is None:
            self._finish(i18n.t("export.no_chunk"), "error")
            return
        if self._geoid_ids and self.geoid_card.isVisible():     # grids of the zone: Download or Later first
            self._flash_geoid()
            return
        jobs = list(self._jobs)
        if self._existing(jobs):                            # the export already exists: overwrite or new version
            try:
                following = self._plan(None)[0].version
            except ValueError:
                following = 2
            ask = ExistsDialog(self, len(self._existing(jobs)), following)
            self._dialog_open += 1
            try:
                ui.run(ask)
            finally:
                self._dialog_open -= 1
            if ask.choice is None:
                return
            if ask.choice == NEW_VERSION:
                jobs = self._plan(None)
        if self._degrees_blocks(jobs):                      # told here, in words, before any file is written
            self._finish(self._degrees_sentence() + " " + self._degrees_advice(), "error")
            return
        target, error = self._target()
        if error:
            self._finish(error, "error")
            return
        try:
            os.makedirs(self._folder(), exist_ok=True)
        except OSError as exc:
            self._finish(i18n.t("export.failed", name=self._folder(), error=exc), "error")
            return
        ctx = pipeline.Context(chunk, self._info, self._folder(), self.site.text().strip(), self._date, self.preset.currentData(),
                               msapi.export_raster, tools=dict(self.tools), crs=target, tile_m=self._tile_m(), cad=self._cad(),
                               snap=msapi.snapped_box, transform=msapi.transform_box,
                               tick=QtWidgets.QApplication.processEvents, vertical=self._vertical_choice(),
                               proj=projtool.Proj(geoid_dir=crs.own_dir()),
                               epsg=int(self.crs_box.currentData()) if target is not None else None, look=looks.load(),
                               clip_boundary=self._boundary_clip(), log=lambda text: journal.write("Aero-Exports: " + text))
        journal.write("Aero-Exports: export of {}".format(self._describe()))
        self._busy = True
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)
        self.bar.setVisible(True)
        self.mark.show_kind(None)
        self._refresh_buttons()

        def progress(fraction, name):
            self.bar.setValue(int(1000 * max(0.0, min(1.0, fraction))))
            if name:
                self.status.setText(i18n.t("export.working", name=name, i=1, n=len(jobs)))
            if not ctx.native:                    # called by Metashape in the middle of its computation: no event loop (repaint crash)
                QtWidgets.QApplication.processEvents()

        problem, result = "", None
        try:
            result = pipeline.run(ctx, jobs, progress)
        except pipeline.Failure as exc:
            problem = i18n.t("export.failed", name=ctx.failed or jobs[0].name, error=exc)
            journal.write("Aero-Exports: {}: {}".format(ctx.failed, exc))
        except Exception as exc:
            problem = i18n.t("export.failed", name=ctx.failed or jobs[0].name, error=repr(exc))
            journal.write("Aero-Exports: {!r}".format(exc))
            journal.write("Aero-Exports: {}".format(traceback.format_exc().strip().replace("\n", " | ")))
        self._busy = False
        if problem:
            self._finish(problem, "error")
            return
        for path, _size in result.files:
            journal.write("Aero-Exports: {}".format(path))
        written = sum(size for _path, size in result.files)
        text = i18n.t("export.done", n=len(result.files), size=size_text(written))
        text += self._cad_report(result)
        self._finish(text, "ok" if not result.warnings and not (result.check and result.check["failed"]) else "error")
        self._refresh_names()                           # the next version number is updated

    @staticmethod
    def _cad_report(result):
        lines = []
        if result.check is not None:
            if result.check["failed"]:
                lines.append(i18n.t("export.check_bad", names=", ".join(result.check["failed"])))
            elif result.check["images"]:
                lines.append(i18n.t("export.check_ok", n=result.check["images"]))
        if "clip" in result.warnings:
            lines.append(i18n.t("export.warn_clip"))
        if "stretch" in result.warnings:
            lines.append(i18n.t("export.warn_stretch"))
        return ("\n" + "\n".join(lines)) if lines else ""

    def _finish(self, text, kind):
        self.bar.setVisible(False)
        self.status.setText(text)
        self.mark.show_kind(kind)
        self._refresh_buttons()

    def _open_folder(self):
        folder = self._folder()
        if folder and os.path.isdir(folder):
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(folder))

    def closeEvent(self, event):
        if self._busy:
            event.ignore()
            return
        super(ExportDialog, self).closeEvent(event)


def show(parent=None, existing=None):
    """Open the window (non-modal). Returns the instance, to be kept."""
    dialog = existing
    try:
        if dialog is not None:
            dialog.objectName()
    except RuntimeError:
        dialog = None
    if dialog is None:
        dialog = ExportDialog(parent)
    else:
        dialog.refresh()
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog
