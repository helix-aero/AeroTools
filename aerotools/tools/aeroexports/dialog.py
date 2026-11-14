"""Aero-Exports window (Qt): tick the orthomosaic and / or the DEM, say what is written for each, choose the coordinates, export.

Two panels. Left: what to export. Each ticked item shows what it will write: the orthomosaic in the chosen format, the DEM as heights
(always a GeoTIFF: heights cannot go into a JPEG) and / or as a coloured picture. Right: the format, the names and a live list of the
exact files that will be written, so the result can be predicted before pressing Export.

The files go to an `Outputs` folder next to the .psx, with a short name (`SITE_YYMMDD_3cm_ortho.tif`, `SITE_YYMMDD_3cm_dtm.tif`).
If the export already exists, the window asks: a new version (`_v2`, the default), or overwrite.

Formats: GeoTIFF (3 settings), JPEG 2000, ECW (Global Mapper), or an AutoCAD drawing (TOPO.dxf / TOPO.dwg) that places the
images (JPEG 2000 or ECW) at the right spot in two layers, orthomosaic and DTM. When a program is missing (Global Mapper,
AutoCAD, QGIS), the window says so, offers to point to the program or to buy it, and does not start anything without it.

Coordinates: the project CRS is shown; one can export in another CRS from the list (official catalogue, nothing to
create) with its height system. If the geoid grid is missing, the window offers to download it.

The export itself goes through Metashape's API (msapi.export_raster), which works in the main thread: it runs as a cooperative
kit task (the window stays alive between the steps; Cancel is honoured between two files).
"""

import datetime
import os
import shutil
import tempfile
import traceback

from ... import config, i18n, journal, msapi, outputdir, projtool, uikit
from ...qt import QtCore, QtGui, QtWidgets
from ...uikit import messages, tokens
from ...uikit.blocks import hbox, vbox
from ...uikit.controls import Button, ChoiceBox, ElidedLabel, IconButton, InfoButton, NumberField, Segmented, TextField, Toggle, label, parse_number
from ...uikit.metrics import px
from ...uikit.tasks import Cancelled
from . import crs, external, look as looks, naming, pipeline, plan, projdb, tiling
from .widgets import ClickLabel, ItemCard, OutputRow, StatusLine

try:
    import numpy  # noqa: F401  (Metashape ships it; without it only the exports that do not need it are offered)
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

SECTION = "aeroexports"
WATCH_MS = 400
COARSE_RATIO = 10.0                                  # a default DEM this many times coarser than the ortho is probably the wrong one
KIND_TITLE = {plan.ORTHO: "export.ortho", plan.DEM: "export.dem"}
CM = 100.0
DEFAULT_TEXT = {plan.ORTHO: "export.default_ortho", plan.DEM: "export.default_dem"}
SEVERAL_TEXT = {plan.ORTHO: "export.several_ortho", plan.DEM: "export.several_dem"}
SAME = "same"
NEW_VERSION, OVERWRITE = "new", "overwrite"
TILE_CHOICES = 6
THUMB = (72, 48)                                     # design px of the small DEM picture
# What each preset writes for the orthomosaic and for the coloured DEM picture (text keys), and what an extension is called.
ORTHO_OUT = {"full": "export.out_tif", "quick": "export.out_quick", "jpeg": "export.out_jpeg", "jp2": "export.img_jp2", "ecw": "export.img_ecw"}
PICTURE_OUT = {"tif": "export.out_tif_colour", "jpg": "export.out_jpeg", "jp2": "export.img_jp2", "ecw": "export.img_ecw"}
EXT_TEXT = {"tif": "export.out_tif", "jpg": "export.out_jpeg_short", "jp2": "export.img_jp2", "ecw": "export.img_ecw"}
WORLD_SIDE = {".jgw": "export.what_world", ".prj": "export.what_prj"}


def size_text(nbytes):
    for unit, step in (("GB", 1024.0 ** 3), ("MB", 1024.0 ** 2)):
        if nbytes >= step:
            return "{:.1f} {}".format(nbytes / step, unit)
    return "{:.0f} KB".format(nbytes / 1024.0)


def run_modal(window):
    """Run a modal window of ours until it closes (PySide2 and PySide6 name the call differently)."""
    return (getattr(window, "exec_", None) or window.exec)()


class ExportDialog(uikit.ToolWindow):
    MARK = "tool_exports"
    DESIGN_SIZE = (1000, 600)

    def __init__(self, parent=None):
        super(ExportDialog, self).__init__(parent, title=i18n.t("export.title"), info=i18n.t("export.info"))
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
        self._picture_choice = bool(config.get(SECTION, "dem_picture"))              # what the user chose for the DEM, kept while a format
        self._values_choice = config.get(SECTION, "dem_values") is not False         # forces or hides one of the two outputs
        self.tools = {}
        self.chunk_title = self.header.subtitle             # which chunk this window works on, always in view

        body = QtWidgets.QWidget()
        columns = hbox(spacing=tokens.L, parent=body)
        left = QtWidgets.QWidget()
        layout = vbox(spacing=tokens.L, parent=left)
        columns.addWidget(left, 1)
        columns.addWidget(uikit.Divider(vertical=True))
        right = QtWidgets.QWidget()
        right_layout = vbox(spacing=tokens.M, parent=right)
        columns.addWidget(right, 1)

        # -- what to export -------------------------------------------------------------------------
        what = uikit.Section(i18n.t("export.what_section"))
        self.cards, self.fields, self.native_labels = {}, {}, {}
        self.raster_details, self.raster_warnings = {}, {}
        for kind in plan.KINDS:
            card = ItemCard(i18n.t(KIND_TITLE[kind]))
            native = label("", "small", "muted", wrap=False)
            field = NumberField(0, 0.01, 100000, 2, 1, i18n.t("export.cm"), width=104, allow_empty=True)
            card.head.addWidget(native)
            card.head.addSpacing(px(tokens.S))
            card.head.addWidget(label(i18n.t("export.pixel"), "body", "muted", wrap=False))
            card.head.addWidget(field)
            card.button.toggled.connect(self._refresh_names)
            field.textChanged.connect(self._refresh_names)
            detail = label("", "small")                      # exactly which raster is exported (the chunk's default one): its size is a value, in the text colour
            detail.setVisible(False)                         # no empty line: it appears with its text
            warn = StatusLine("warning")                     # a warning when it is a poor choice
            card.rows.addWidget(detail)
            card.rows.addWidget(warn)
            what.add(card)
            self.cards[kind], self.fields[kind], self.native_labels[kind] = card, field, native
            self.raster_details[kind], self.raster_warnings[kind] = detail, warn
        self.ortho_saved = label("", "body", wrap=False)     # what the orthomosaic will be written as: the value that matters, in the text colour
        self.cards[plan.ORTHO].rows.addWidget(self.ortho_saved)
        self._build_dem_rows()
        layout.addWidget(what)
        self._build_boundary_row(layout)

        # -- coordinates ----------------------------------------------------------------------------
        more = vbox(spacing=tokens.S)                         # the rest of the left panel: one calm group
        layout.addLayout(more)
        self.crs_notice = StatusLine("warning")              # a raster in degrees: why a system is preselected, or what to pick
        more.addWidget(self.crs_notice)
        self.coords = uikit.Disclosure(i18n.t("export.crs_title_same"))
        self.crs = label("", "small", "muted")
        self.coords.add(self.crs)
        form = uikit.FormGrid()
        self.crs_search = TextField("", i18n.t("export.crs_find_hint"))
        self.crs_search.textChanged.connect(self._on_search)
        form.add_row(i18n.t("export.crs_find"), self.crs_search)
        self.crs_box = ChoiceBox()
        self.crs_box.activated.connect(self._on_crs)
        form.add_row(i18n.t("export.crs_to"), self.crs_box)
        self.vert_box = ChoiceBox()
        self.vert_box.activated.connect(self._on_vertical)
        form.add_row(i18n.t("export.vertical"), self.vert_box, info=i18n.t("export.vertical_tip"))
        self.coords.add(form)
        more.addWidget(self.coords)
        self.geoid_card = uikit.Banner("warning", "", closable=True)
        self.geoid_card.dismissed.connect(self._geoid_not_now)
        self.geoid_card.hide()
        more.addWidget(self.geoid_card)
        layout.addStretch(1)

        # -- format -----------------------------------------------------------------------------------
        fmt = uikit.Section(i18n.t("export.preset"))
        row = hbox(spacing=tokens.S)
        self.preset = ChoiceBox([(item["key"], i18n.t(item["title"])) for item in plan.PRESETS])
        saved = config.get(SECTION, "preset")
        if self.preset.findData(saved) >= 0:
            self.preset.setCurrentIndex(self.preset.findData(saved))
        self.preset.activated.connect(self._on_preset)
        row.addWidget(self.preset, 1)
        self.preset_tip = InfoButton("")                    # what the chosen format is, behind the "i"
        row.addWidget(self.preset_tip)
        fmt.add(self._wrap(row))

        self.cad_box = QtWidgets.QWidget()
        cad = vbox(spacing=tokens.S, parent=self.cad_box)
        self.cad_format = Segmented([("dxf", i18n.t("export.cad_dxf")), ("dwg", i18n.t("export.cad_dwg"))],
                                    config.get(SECTION, "cad_format") or "dxf")
        self.cad_images = Segmented([("jp2", i18n.t("export.img_jp2")), ("ecw", i18n.t("export.img_ecw"))],
                                    config.get(SECTION, "cad_images") or "jp2")
        for group in (self.cad_format, self.cad_images):
            group.changed.connect(self._on_cad_choice)
        cad_row = hbox(spacing=tokens.S)
        cad_row.addWidget(label(i18n.t("export.cad_format"), "body", "muted", wrap=False))
        cad_row.addWidget(self.cad_format)
        cad_row.addSpacing(px(tokens.M))
        cad_row.addWidget(label(i18n.t("export.cad_images"), "body", "muted", wrap=False))
        cad_row.addWidget(self.cad_images)
        cad_row.addWidget(InfoButton(i18n.t("export.img_jp2_tip") + " " + i18n.t("export.img_ecw_tip")))
        cad_row.addStretch(1)
        cad.addLayout(cad_row)
        self.cad_clip = Toggle(i18n.t("export.cad_clip"), config.get(SECTION, "cad_clip") is not False and HAS_NUMPY)
        self.cad_clip_row = QtWidgets.QWidget()
        clip = hbox(spacing=tokens.S, parent=self.cad_clip_row)
        clip.addWidget(self.cad_clip)
        clip.addWidget(InfoButton(i18n.t("export.cad_clip_tip") if HAS_NUMPY else i18n.t("export.look_no_numpy")))
        clip.addStretch(1)
        if not HAS_NUMPY:
            self.cad_clip.setEnabled(False)
            self.cad_clip.setToolTip(i18n.t("export.look_no_numpy"))
        self.cad_clip.toggled.connect(self._on_cad_choice)
        cad.addWidget(self.cad_clip_row)
        fmt.add(self.cad_box)

        self.tool_card = QtWidgets.QWidget()                 # a program the format needs is missing
        tool = vbox(spacing=tokens.S, parent=self.tool_card)
        self.tool_banner = uikit.Banner("warning", "", closable=False)
        self.tool_text = self.tool_banner.message
        tool.addWidget(self.tool_banner)
        tool_row = hbox(spacing=tokens.S)
        self.tool_choose = Button(i18n.t("export.choose_exe"), "secondary", handler=self._choose_tool)
        self.tool_buy = Button("", "secondary", handler=self._buy_tool)
        self.tool_other = Button(i18n.t("export.other_format"), "quiet", handler=self._cancel_tool)
        for widget in (self.tool_choose, self.tool_buy, self.tool_other):
            tool_row.addWidget(widget)
        tool_row.addStretch(1)
        tool.addLayout(tool_row)
        fmt.add(self.tool_card)
        self.tool_card.setVisible(False)

        self.advanced = uikit.Disclosure(i18n.t("export.advanced"))       # tiles
        tiles = hbox(spacing=tokens.S)
        self.tiles_check = Toggle(i18n.t("export.tiles"))
        self.tiles_check.toggled.connect(self._on_tiles)
        tiles.addWidget(self.tiles_check)
        self.tile_box = ChoiceBox()
        self.tile_box.setMinimumWidth(px(140))
        self.tile_box.activated.connect(self._refresh_names)
        tiles.addWidget(self.tile_box)
        tiles.addWidget(InfoButton(i18n.t("export.tiles_tip")))
        tiles.addStretch(1)
        self.advanced.add(self._wrap(tiles))
        fmt.add(self.advanced)
        right_layout.addWidget(fmt)

        # -- names and the live list of files ----------------------------------------------------------
        files = vbox(spacing=tokens.S)
        files_head = hbox(spacing=tokens.S)
        files_head.addWidget(label(i18n.t("export.names"), "lead", wrap=False))
        files_head.addWidget(InfoButton(i18n.t("export.files_tip")))
        files_head.addStretch(1)
        files_head.addWidget(label(i18n.t("export.site"), "body", "muted", wrap=False))
        self.site = TextField()
        self.site.setFixedWidth(px(180))
        self.site.textChanged.connect(self._refresh_names)
        files_head.addWidget(self.site)
        files_head.addWidget(label(i18n.t("export.date"), "body", "muted", wrap=False))
        self.date = label("", "body", wrap=False)
        files_head.addWidget(self.date)
        files.addLayout(files_head)
        self.files = uikit.DataTable([uikit.Column("file", i18n.t("export.col_file"), stretch=True),
                                      uikit.Column("what", i18n.t("export.col_what"))], selection="none", sortable=False,
                                     empty_title=i18n.t("export.nothing"))
        self.files.setMinimumHeight(px(tokens.CONTROL_H) * 3)
        files.addWidget(self.files, 1)
        self.files_note = label("", "small", "muted")
        files.addWidget(self.files_note)
        folder_row = hbox(spacing=tokens.S)
        self.folder = ElidedLabel("", "small", mode="middle")          # where the files will be written: a value that matters, in the text colour
        folder_row.addWidget(self.folder, 1)
        self.open_button = IconButton("folder", i18n.t("export.open_folder"), self._open_folder)
        folder_row.addWidget(self.open_button)
        files.addLayout(folder_row)
        right_layout.addLayout(files, 1)

        self.set_content(body)
        self.set_primary(i18n.t("export.go"), self._on_export)
        self.go_button = self.next_button
        self.close_button = self.cancel_button
        self.footer.set_cancel_mode("close")
        self._set_advanced(False)

        self.refresh()
        # The window follows Metashape by itself: a cheap look at the active chunk and its default rasters, a few times a second.
        self._watch = QtCore.QTimer(self)
        self._watch.setInterval(WATCH_MS)
        self._watch.timeout.connect(self._watch_tick)
        self._watch.start()

    @staticmethod
    def _wrap(layout):
        holder = QtWidgets.QWidget()
        holder.setLayout(layout)
        return holder

    # -- window events ------------------------------------------------------------------------------

    def present(self):
        """Show the window; one that is already open reads the project again first."""
        if self.isVisible():
            self.refresh()
        super(ExportDialog, self).present()

    def changeEvent(self, event):
        super(ExportDialog, self).changeEvent(event)
        if event.type() == QtCore.QEvent.ActivationChange and self.isActiveWindow():
            self._follow_chunk()                            # back in front: is it still what we show?

    def refresh_buttons(self, *_args):
        super(ExportDialog, self).refresh_buttons(*_args)
        if hasattr(self, "files_note"):
            self._apply_state()

    def _apply_state(self):
        """Enabled state of the Export button and of the controls: nothing is editable while an export runs."""
        busy = self.is_busy()
        blocked = bool(self._missing) or bool(self._geoid_missing)
        self.next_button.setEnabled(bool(self._jobs) and not busy and not blocked)
        self.stack.currentWidget().setEnabled(not busy)
        self.open_button.setEnabled(bool(self._folder()) and os.path.isdir(self._folder()))
        self.vert_box.setEnabled(not busy and self.crs_box.currentData() != SAME)

    def _watch_tick(self):
        """Timer: while the window is visible and idle, notice a chunk or default raster changed in Metashape. Never raises."""
        if not self.isVisible() or self.is_busy() or self._dialog_open:
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
        if self.is_busy():
            return False
        reason = self._change_reason(deep)
        if reason is None:
            return False
        self.refresh()
        if self._chunk is None:
            return True
        key = {"chunk": "export.chunk_changed", "dem": "export.dem_changed", "ortho": "export.ortho_changed",
               "shapes": "export.shapes_changed"}[reason]
        self.set_status(i18n.t(key, name=self._chunk_label))
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

    # -- the DEM outputs ---------------------------------------------------------------------------------

    def _build_dem_rows(self):
        """Under the DEM card: what the DEM writes (heights, coloured picture), and the look of that picture."""
        card = self.cards[plan.DEM]
        self.heights_row = OutputRow(i18n.t("export.heights"), True, i18n.t("export.heights_why"))
        self.values_check = self.heights_row.switch
        self.values_check.toggled.connect(self._on_picture)
        self.picture_row = OutputRow(i18n.t("export.picture"), self._picture_choice and HAS_NUMPY, i18n.t("export.dem_picture_tip"))
        self.picture_check = self.picture_row.switch
        self.picture_check.toggled.connect(self._on_picture)
        self.numpy_note = label(i18n.t("export.picture_no_numpy"), "small", "muted")
        self.numpy_note.setVisible(not HAS_NUMPY)
        if not HAS_NUMPY:
            self.picture_check.setEnabled(False)
            self.picture_check.setToolTip(i18n.t("export.look_no_numpy"))
        card.rows.addWidget(self.heights_row)
        card.rows.addWidget(self.picture_row)
        card.rows.addWidget(self.numpy_note)
        self.look_row = QtWidgets.QWidget()
        row = hbox(spacing=tokens.M, parent=self.look_row)
        self.look_button = Button(i18n.t("export.look_open"), "secondary", handler=self._open_look)
        row.addWidget(self.look_button)
        self.look_text = label("", "small", "muted")
        row.addWidget(self.look_text, 1)
        self.look_thumb = ClickLabel(self._open_look)
        self.look_thumb.setProperty("ui", "field")
        self.look_thumb.setFixedSize(px(THUMB[0]), px(THUMB[1]))
        self.look_thumb.setAlignment(QtCore.Qt.AlignCenter)
        self.look_thumb.setVisible(False)
        row.addWidget(self.look_thumb)
        card.rows.addWidget(self.look_row)
        try:
            from . import previewwin  # noqa: F401  (needs numpy)
            self._preview_ok = True
        except ImportError:
            self.look_button.setEnabled(False)
            self.look_button.setToolTip(i18n.t("export.look_no_numpy"))

    def _picture_wanted(self):
        """True when the coloured DEM picture is asked next to a GeoTIFF, JPEG 2000 or ECW export."""
        return HAS_NUMPY and self.preset.currentData() in plan.PICTURE_PRESETS and self._picture_choice

    def _values_wanted(self):
        """True when the heights (the DEM GeoTIFF) are written: always, unless only the picture was asked."""
        return self._values_choice or not self._picture_wanted()

    def _picture_ext(self):
        """Extension of the coloured DEM picture for the chosen format."""
        key = self.preset.currentData()
        if key == plan.CAD:
            return self._value(self.cad_images) or "jp2"
        return plan.PICTURE_PRESETS.get(key, "tif")

    def _set_checked(self, switch, on, enabled):
        switch.blockSignals(True)
        switch.setChecked(bool(on))
        switch.blockSignals(False)
        switch.setEnabled(bool(enabled))

    def _sync_dem_rows(self):
        """Show what the DEM will write for the chosen format: the heights (a GeoTIFF, always) and the coloured picture."""
        key = self.preset.currentData()
        cad = key == plan.CAD
        dem_on = self.cards[plan.DEM].button.isChecked() and bool(self._info.get(plan.DEM))
        self._set_checked(self.picture_check, True if cad else (self._picture_choice and HAS_NUMPY), dem_on and HAS_NUMPY and not cad)
        wanted = self._picture_wanted()
        forced = cad or not wanted                           # nothing else would be written: the heights stay
        self._set_checked(self.values_check, True if forced else self._values_choice, dem_on and not forced)
        self.heights_row.set_format(i18n.t("export.out_tif_always"))
        self.picture_row.set_format(i18n.t("export.out_cad", fmt=i18n.t(EXT_TEXT[self._picture_ext()])) if cad
                                    else i18n.t(PICTURE_OUT[self._picture_ext()]))
        tip = i18n.t("export.dem_picture_tip")
        if not cad and self._picture_ext() == "tif":
            tip += " " + i18n.t("export.picture_tif_note")
        self.picture_row.info.set_text(tip)
        self.look_row.setVisible(dem_on and (cad or wanted))
        self._refresh_look_text()

    def _refresh_look_row(self):
        self._sync_dem_rows()

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
        """A switch of the DEM outputs was clicked by the user (the programmatic changes of `_sync_dem_rows` do not come here)."""
        if self.picture_check.isEnabled():
            self._picture_choice = bool(self.picture_check.isChecked())
        if self.values_check.isEnabled():
            self._values_choice = bool(self.values_check.isChecked())
        config.put(SECTION, "dem_picture", self._picture_choice)
        config.put(SECTION, "dem_values", self._values_choice)
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
            self.show_banner("danger", i18n.t("export.no_chunk"))
            return None
        self.clear_banner()
        self.set_status(i18n.t("export.look_loading"))
        QtWidgets.QApplication.processEvents()              # before the call into Metashape, never inside it
        work, model, problem = None, None, ""
        try:
            box, native = self._info.get("box_dem"), self._info.get(plan.DEM)
            # an export already on disk is reused only if it is this chunk's DEM (same size, pixel and extent), not another chunk's
            # An export already on disk can have been made with or without the boundary clip: it is reused only when the chunk has
            # no boundary shape (then both are the same). With shapes, or when unsure, a small export is made with the setting shown.
            reusable = box and native and self._info.get("boundaries") == 0
            from . import preview
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
        self.set_status("")
        if problem:
            self.show_banner("danger", problem)
            return None
        self._preview, self._preview_key = model, self._preview_signature()
        return model

    def _open_look(self):
        """Open the window where the colours and the shading are chosen; keep (and save) the look if the user accepts it."""
        if self.is_busy() or not self._preview_ok:
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
            run_modal(window)
        finally:
            self._dialog_open -= 1
        if window.result_look is not None:
            looks.save(window.result_look)
        self._refresh_look_text()

    @staticmethod
    def _value(group):
        return group.value()

    # -- reading the project --------------------------------------------------------------

    def refresh(self):
        """Read the project again (what exists, its resolution, its name): on opening and each time it comes back to the front."""
        if self.is_busy():
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
        self.set_subtitle(i18n.t("export.chunk", name=self._chunk_label) if self._chunk is not None else i18n.t("export.chunk_none"))
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
        self.set_status("")
        self.clear_banner()
        self._fill_tiles()
        self._refresh_names()

    @staticmethod
    def _cm_text(pixel_m):
        """Pixel size in cm, without useless zeros: 0.5 m -> "50", 0.0125 m -> "1.25"."""
        text = "{:.2f}".format(pixel_m * CM).rstrip("0").rstrip(".")
        return text or "0"

    def _is_native(self, kind):
        """True if the field has not been touched (or is empty or invalid): Metashape's grid is then kept."""
        value = parse_number(self.fields[kind].text())
        if value is None or value <= 0:
            return True
        return abs(value - float(self._cm_text(self._native.get(kind, 0.0)))) < 1e-9

    def _pixel(self, kind):
        """Wanted pixel size in metres (the native size if the field is untouched, empty or invalid)."""
        if self._is_native(kind):
            return self._native.get(kind, 0.0)
        return parse_number(self.fields[kind].text()) / CM

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
            detail.setVisible(bool(text))
            warn.setText(warning)
            warn.setVisible(bool(warning))

    def _build_boundary_row(self, layout):
        """A switch, shown only when the chunk has boundary shapes: Metashape cuts an export to them unless told not to."""
        self.boundary_row = QtWidgets.QWidget()
        column = vbox(spacing=tokens.XS, parent=self.boundary_row)
        line = hbox(spacing=tokens.S)
        self.boundary_check = Toggle(i18n.t("export.boundary"), config.get(SECTION, "clip_to_boundary_shapes") is True)   # off by default: the export covers the whole default DEM, as seen in Metashape
        self.boundary_check.toggled.connect(self._on_boundary)
        self.boundary_label = self.boundary_check
        line.addWidget(self.boundary_check)
        line.addWidget(InfoButton(i18n.t("export.boundary_tip")))
        line.addStretch(1)
        column.addLayout(line)
        self.boundary_note = label("", "small", "muted")
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
            if not HAS_NUMPY and self.cards[plan.DEM].button.isChecked():
                return "numpy", "export.need_numpy_dem"       # the coloured DEM picture of a drawing is painted with numpy
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
            self.tool_banner.show_message("warning", i18n.t(key), None, None, None, False)
            self.tool_choose.setVisible(tool != "numpy")
            self.tool_buy.setVisible(tool != "numpy")
            if tool != "numpy":
                self.tool_buy.setText(i18n.t({"gm": "export.buy_gm", "acad": "export.buy_acad", "gdal": "export.get_qgis"}[tool]))
        self.refresh_buttons()

    def _choose_tool(self):
        if not self._missing or self._missing[0] == "numpy":
            return
        tool = self._missing[0]
        filters = {"gm": "global_mapper.exe", "acad": "acad.exe accoreconsole.exe", "gdal": "gdal_translate.exe"}[tool]
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, i18n.t("export.choose_exe").rstrip("."), "",
                                                        "{} ({})".format(filters.split()[0], filters))
        if not path:
            return
        if tool == "gdal":
            path = os.path.dirname(path)
        config.put(SECTION, {"gm": "path_gm", "acad": "path_acad", "gdal": "path_gdal"}[tool], path)
        self._detect_tools()
        self._check_tools()
        if self._missing and self._missing[0] == tool:
            self.tool_banner.show_message("warning", i18n.t("export.exe_invalid") + " " + i18n.t(self._missing[1]), None, None, None, False)

    def _buy_tool(self):
        if self._missing and self._missing[0] != "numpy":
            url = {"gm": external.GLOBAL_MAPPER_URL, "acad": external.AUTOCAD_URL, "gdal": external.QGIS_URL}[self._missing[0]]
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))

    def _cancel_tool(self):
        """Give up: back to the last format that works."""
        self.preset.setCurrentIndex(max(0, self.preset.findData("full")))
        if self._value(self.cad_images) == "ecw" and not self.tools["gm"]:
            self.cad_images.set_value("jp2")                    # JPEG 2000
        if self._value(self.cad_format) == "dwg" and not self.tools["acad"]:
            self.cad_format.set_value("dxf")                    # DXF
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
        elif not HAS_NUMPY:                                 # the height grids are read with numpy: ellipsoidal heights only
            self.vert_box.addItem(crs.label(crs.ELLIPSOID), crs.ELLIPSOID["id"])
            self.vert_box.setToolTip(i18n.t("export.vertical_no_numpy"))
            self.vert_box.setEnabled(False)
        else:
            from . import vertical
            for spec in vertical.compatible_verticals(int(epsg), lon, lat, folder=crs.own_dir()):
                self.vert_box.addItem(crs.label(spec), spec["id"])
            self.vert_box.setEnabled(not self.is_busy())
        self.vert_box.blockSignals(False)
        self._check_geoid()

    def _on_crs(self, *_args):
        self._crs_picked = True                             # his choice, even "Same as the project": we leave it alone
        if self.crs_box.currentData() != self._crs_auto:
            self._crs_auto = None
        self._fill_vertical()
        self._refresh_names()

    def _refresh_crs_title(self):
        """The closed "Coordinates" line says what is chosen, so nobody has to open it to know."""
        current = self.crs_box.currentData()
        if current in (None, SAME):
            title = i18n.t("export.crs_title_same")
        else:
            title = i18n.t("export.crs_title", name=self.crs_box.currentText().split("  (EPSG")[0])
        self.coords.head.setText(self.coords.head.fontMetrics().elidedText(title, QtCore.Qt.ElideRight, px(420)))

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
        if current in (None, SAME):
            self.coords.set_open(True)                      # a choice is awaited, and it is in there

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
        """State of the chosen height grid in our folder: missing or stale = warning, export frozen, download button.
        Otherwise, offers the grids of the project's zone that are missing (same banner, closable = "Not now"; Export makes it
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
        if not self._geoid_missing and HAS_NUMPY:
            declined = set(config.get(SECTION, "geoid_declined") or [])
            todo = [(spec, state) for spec, state in crs.zone_grids(self._project_epsg, lon, lat) if spec["id"] not in declined]
            if todo:
                self._geoid_ids = [spec["id"] for spec, _state in todo]
                self._geoid_states = {spec["id"]: state for spec, state in todo}
                text = i18n.t("export.geoid_zone", names=", ".join(crs.label(spec) for spec, _state in todo),
                              size=size_text(sum(spec["size"] for spec, _state in todo)))
        if text:
            action = (i18n.t("export.geoid_download" if len(self._geoid_ids) < 2 else "export.geoid_download_all"), self._download_geoid)
            self.geoid_card.show_message("warning", text, None, action if self._geoid_ids else None, None, not blocking)
            if blocking:
                self.coords.set_open(True)                  # the grid is the one chosen in there
        self.geoid_card.setVisible(bool(text))
        self.refresh_buttons()

    def _flash_geoid(self, steps=6):
        """Make the banner flash: an export was attempted while a decision on the grids is awaited."""
        uikit.style.set_prop(self.geoid_card, "kind", "danger" if steps % 2 == 1 else "warning")
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
        total = sum(crs.vertical_by_id(vid).get("size", 0) for vid in ids) or 1
        folder = crs.own_dir()
        states = dict(self._geoid_states)

        def work(progress):
            errors, done = [], 0
            for index, vid in enumerate(ids):
                text = i18n.t("export.geoid_progress", name=crs.label(crs.vertical_by_id(vid)), index=index + 1, total=len(ids))
                progress(done / float(total), text)
                try:
                    path = crs.download_geoid(vid, folder, overwrite=states.get(vid) == "stale",
                                              progress=lambda got, _t, base=done, text=text: progress(min(1.0, (base + got) / float(total)), text))
                    journal.write("Aero-Exports: geoid downloaded: {}".format(path))
                except FileExistsError:
                    pass
                except Exception as exc:
                    errors.append("{}: {}".format(vid, str(exc) or repr(exc)))
                    journal.write("Aero-Exports: geoid {}: {}".format(vid, errors[-1]))
                done += crs.vertical_by_id(vid).get("size", 0)
            if errors:
                raise RuntimeError(i18n.t("export.geoid_failed", error="; ".join(errors)))

        def finished(_result):
            self._geoid_busy = False
            self._check_geoid()
            self.set_status(i18n.t("export.geoid_done", name=", ".join(ids)))

        def failed(_exc):
            self._geoid_busy = False
            self._check_geoid()

        self.run(work, mode="threaded", name="geoid", cancellable=False, on_done=finished, on_error=failed, retry=False)

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
        self.advanced.set_open(shown)

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
        self.preset_tip.set_text(i18n.t(plan.preset(key)["tip"]))
        if self.sender() is self.preset:
            self._previous_preset = config.get(SECTION, "preset")
            config.put(SECTION, "preset", key)
        self.cad_box.setVisible(key == plan.CAD)
        self.cad_clip_row.setVisible(key == plan.CAD)           # "Crop white background" only concerns the drawings
        self._check_tools()
        self._refresh_names()

    def _on_cad_choice(self, *_args):
        if self._value(self.cad_format):
            config.put(SECTION, "cad_format", self._value(self.cad_format))
        if self._value(self.cad_images):
            config.put(SECTION, "cad_images", self._value(self.cad_images))
        if HAS_NUMPY:
            config.put(SECTION, "cad_clip", bool(self.cad_clip.isChecked()))
        self._check_tools()
        self._refresh_names()

    def _cad(self):
        return {"format": self._value(self.cad_format) or "dxf", "images": self._value(self.cad_images) or "jp2",
                "clip": bool(self.cad_clip.isChecked()) and HAS_NUMPY}

    def _wanted(self):
        return {kind: self._pixel(kind) for kind in plan.KINDS
                if self.cards[kind].button.isChecked() and self._pixel(kind) > 0}

    def _folder(self):
        return outputdir.outputs_folder(self._psx) if self._psx else ""

    def _plan(self, version=1):
        """List of the files for a version number (1 = base names; None = first free number)."""
        wanted = self._wanted()
        return plan.build_jobs(self.site.text().strip(), self._date, wanted, self.preset.currentData(), self._folder(),
                               native={k for k in wanted if self._is_native(k)}, cad=self._cad(),
                               version=version, dem_picture=self._picture_wanted(), tiled=bool(self._tile_m()),
                               dem_values=self._values_wanted())

    def _existing(self, jobs):
        return plan.existing_files(jobs, self.preset.currentData(), self._cad(), tiled=bool(self._tile_m()))

    def _ortho_saved_text(self):
        """What the orthomosaic is written as, for the chosen format (shown under its card)."""
        key = self.preset.currentData()
        if key == plan.CAD:
            return i18n.t("export.out_cad", fmt=i18n.t(EXT_TEXT[self._value(self.cad_images) or "jp2"]))
        return i18n.t(ORTHO_OUT.get(key, "export.out_tif"))

    def _file_rows(self, jobs, folder):
        """[(relative name, what it holds)] for the Files list: every file the jobs write, in the order they are written."""
        rows = []
        key = self.preset.currentData()
        if key == plan.CAD:
            rows.append((plan.cad_dxf_name(1, "dxf"), i18n.t("export.what_drawing")))
            if self._cad()["format"] == "dwg":
                rows.append((plan.cad_dxf_name(1, "dwg"), i18n.t("export.what_drawing")))
        for job in jobs:
            what = i18n.t("export.what_ortho" if job.kind == plan.ORTHO else "export.what_heights")
            if plan.values_wanted(job):
                rows.append((os.path.relpath(job.path, folder), what))
            if job.extra and job.extra.get("view_path"):
                rows.append((os.path.relpath(job.extra["view_path"], folder), i18n.t("export.what_picture")))
                for name in plan.view_aux_names(job.extra):
                    rows.append((os.path.relpath(os.path.join(os.path.dirname(job.extra["view_path"]), name), folder), i18n.t("export.what_world")))
            for name in plan.aux_names(job.name, job.spec):
                rows.append((name, i18n.t(WORLD_SIDE.get(os.path.splitext(name)[1], "export.what_prj"))))
        return rows

    def _refresh_names(self, *_args):
        for kind in plan.KINDS:
            self.fields[kind].setEnabled(self.cards[kind].button.isChecked() and bool(self._info.get(kind)))
        if hasattr(self, "look_row"):
            self._refresh_look_row()
        if hasattr(self, "crs_notice"):
            self._sync_auto_crs()
        if hasattr(self, "tile_box"):
            self._fill_tiles()
        self._refresh_crs_title()
        self.ortho_saved.setText(i18n.t("export.saved_as", fmt=self._ortho_saved_text()))
        self.ortho_saved.setEnabled(self.cards[plan.ORTHO].button.isChecked() and bool(self._info.get(plan.ORTHO)))
        self._jobs, rows, empty, note = [], [], "", ""
        folder = self._folder()
        wanted = self._wanted()
        site = self.site.text().strip()
        if not self._psx:
            empty = i18n.t("export.unsaved")
        elif not self._info.get(plan.ORTHO) and not self._info.get(plan.DEM):
            empty = i18n.t("export.nothing_built")
        elif not wanted:
            empty = i18n.t("export.nothing")
        elif not naming.sanitize_token(site):
            empty = i18n.t("export.site_missing")
        else:
            try:
                jobs = self._plan(1)
                self._jobs = jobs
                rows = self._file_rows(jobs, folder)
                notes = []
                if self._tile_m():
                    notes.append(i18n.t("export.tiles_note", m=int(self._tile_m())))
                if self._existing(jobs):
                    notes.append(i18n.t("export.exists_note", n=len(self._existing(jobs))))
                note = " ".join(notes)
                too_long = naming.check_path_length(folder, [os.path.relpath(j.path, folder) for j in jobs])
                if too_long:
                    self._jobs, rows = [], []
                    empty = i18n.t("export.too_long")
            except ValueError as exc:
                self._jobs, rows = [], []
                empty = str(exc)
        if rows:
            self.files.set_empty("", "")
            self.files.set_rows(rows)
        else:
            self.files.set_empty(empty)
            self.files.clear()
        self.files_note.setText(note)
        self.files_note.setVisible(bool(note))
        self.folder.setFullText(i18n.t("export.folder", path=folder) if folder else "")
        self.refresh_buttons()

    # -- export -----------------------------------------------------------------------------

    def _ask_exists(self, count, following):
        """"These files already exist": a new version (the default), overwrite (asked again), or cancel. Returns the choice or None."""
        dialog, choice = messages.build(self, i18n.t("export.title"), i18n.t("export.exists", n=count), [
            (i18n.t("ui.cancel"), None, "secondary"), (i18n.t("export.overwrite"), OVERWRITE, "secondary"),
            (i18n.t("export.newversion", n=following), NEW_VERSION, "primary")], "warning")
        self._dialog_open += 1
        try:
            run_modal(dialog)
        finally:
            self._dialog_open -= 1
        return choice["value"]

    def _confirm_overwrite(self, count):
        self._dialog_open += 1
        try:
            return uikit.confirm(self, i18n.t("export.title"), i18n.t("export.overwrite_sure", n=count),
                                 ok=i18n.t("export.overwrite"), danger=True)
        finally:
            self._dialog_open -= 1

    def _on_export(self):
        if self.is_busy():
            return
        if self._follow_chunk(deep=True):                   # never export silently on a chunk the user has not seen
            return
        if not self._jobs:
            return
        chunk = self._chunk                                 # the pinned chunk, the one named at the top of the window
        if chunk is None:
            self.show_banner("danger", i18n.t("export.no_chunk"))
            return
        if self._geoid_ids and self.geoid_card.isVisible():     # grids of the zone: Download or Later first
            self._flash_geoid()
            return
        jobs = list(self._jobs)
        if self._existing(jobs):                            # the export already exists: a new version, or overwrite
            try:
                following = self._plan(None)[0].version
            except ValueError:
                following = 2
            choice = self._ask_exists(len(self._existing(jobs)), following)
            if choice is None:
                return
            if choice == OVERWRITE and not self._confirm_overwrite(len(self._existing(jobs))):
                return
            if choice == NEW_VERSION:
                jobs = self._plan(None)
        feet = [e for e in (self._project_epsg if self.crs_box.currentData() in (None, SAME) else self.crs_box.currentData(),)
                if e and projdb.available() and not projdb.is_metric(e)]
        if feet and (self._tile_m() or any(not self._is_native(job.kind) for job in jobs) or self.preset.currentData() == plan.CAD):
            self.show_banner("danger", i18n.t("export.crs_feet"))        # pixel / tile sizes are in metres here, the system is not
            return
        if self._degrees_blocks(jobs):                      # told here, in words, before any file is written
            self.show_banner("danger", self._degrees_sentence() + " " + self._degrees_advice())
            return
        target, error = self._target()
        if error:
            self.show_banner("danger", error)
            return
        try:
            os.makedirs(self._folder(), exist_ok=True)
        except OSError as exc:
            self.show_banner("danger", i18n.t("export.failed", name=self._folder(), error=exc))
            return
        ctx = pipeline.Context(chunk, self._info, self._folder(), self.site.text().strip(), self._date, self.preset.currentData(),
                               msapi.export_raster, tools=dict(self.tools), crs=target, tile_m=self._tile_m(), cad=self._cad(),
                               snap=msapi.snapped_box, transform=msapi.transform_box,
                               tick=QtWidgets.QApplication.processEvents, vertical=self._vertical_choice(),
                               proj=projtool.Proj(geoid_dir=crs.own_dir()),
                               epsg=int(self.crs_box.currentData()) if target is not None else None, look=looks.load(),
                               clip_boundary=self._boundary_clip(), log=lambda text: journal.write("Aero-Exports: " + text))
        journal.write("Aero-Exports: export of {}".format(self._describe()))
        total = len(jobs)

        def work(progress):
            started = set()

            def report(fraction, name):
                text = i18n.t("export.working", name=name, i=min(total, int(fraction * total) + 1), n=total) if name else ""
                new_file = bool(name) and name not in started       # the first call for a file comes before anything of it is written
                started.add(name)
                try:
                    progress(fraction, text)
                except Cancelled:
                    if new_file:                            # stop between two files, never in the middle of one
                        raise
            try:
                return pipeline.run(ctx, jobs, report)
            except Cancelled:
                raise
            except pipeline.Failure as exc:
                journal.write("Aero-Exports: {}: {}".format(ctx.failed, exc))
                raise pipeline.Failure(i18n.t("export.failed", name=ctx.failed or jobs[0].name, error=exc))
            except Exception as exc:
                journal.write("Aero-Exports: {!r}".format(exc))
                journal.write("Aero-Exports: {}".format(traceback.format_exc().strip().replace("\n", " | ")))
                raise RuntimeError(i18n.t("export.failed", name=ctx.failed or jobs[0].name, error=repr(exc)))

        self.run(work, mode="cooperative", name="export", on_done=self._exported, on_error=self._export_failed,
                 on_cancel=self._export_cancelled, retry=False)

    def _exported(self, result):
        for path, _size in result.files:
            journal.write("Aero-Exports: {}".format(path))
        written = sum(size for _path, size in result.files)
        text = i18n.t("export.done", n=len(result.files), size=size_text(written)) + self._cad_report(result)
        clean = not result.warnings and not (result.check and result.check["failed"])
        self.show_banner("success" if clean else "warning", text, action=(i18n.t("export.open_folder"), self._open_folder))
        self._refresh_names()                           # the next version number is updated

    def _export_failed(self, exc):
        self.show_error(RuntimeError(str(exc)), retry=self._on_export, trace=self.runner().trace)

    def _export_cancelled(self):
        self.show_banner("info", i18n.t("export.cancelled"))
        self._refresh_names()

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

    def _open_folder(self):
        folder = self._folder()
        if folder and os.path.isdir(folder):
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(folder))
