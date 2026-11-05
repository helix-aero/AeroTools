"""Fenetre Fast Export (Qt) : on coche l'ortho et / ou le MNT, on choisit un format, on exporte.

Les fichiers vont dans un dossier `Outputs` a cote du .psx, avec un nom court
(`SITE_AAMMJJ_3cm_ortho.tif`, `SITE_AAMMJJ_3cm_mnt.tif`, `_dtm` si l'interface est en anglais). Un export refait
le meme jour prend un numero de version (`_v2`) au lieu d'ecraser le precedent.

L'export lui-meme passe par l'API de Metashape (msapi.export_raster), qui travaille dans le fil principal : la
fenetre reste vivante grace a processEvents pendant les rappels de progression.
"""

import datetime
import os

from ... import config, i18n, journal, msapi, theme, ui
from ...qt import QtCore, QtGui, QtWidgets
from . import naming, plan

SECTION = "fastexport"
WIDTH, HEIGHT = 760, 700
KIND_TITLE = {plan.ORTHO: "export.ortho", plan.DEM: "export.dem"}
CM = 100.0


def size_text(nbytes):
    for unit, step in (("GB", 1024.0 ** 3), ("MB", 1024.0 ** 2)):
        if nbytes >= step:
            return "{:.1f} {}".format(nbytes / step, unit)
    return "{:.0f} KB".format(nbytes / 1024.0)


class ExportDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super(ExportDialog, self).__init__(parent)
        self._lang = i18n.lang()
        self._busy = False
        self._info = {}
        self._psx = ""
        self._native = {}
        self._jobs = []
        self._date = datetime.date.today()
        self.setWindowTitle(i18n.t("export.title"))
        self.setModal(False)
        self.resize(WIDTH, HEIGHT)
        root = ui.dark_root(self)
        layout = QtWidgets.QVBoxLayout(root)
        pad = theme.PAD_L
        layout.setContentsMargins(pad, pad, pad, pad)
        layout.setSpacing(theme.PAD + theme.PAD_S)

        self.cards, self.fields, self.native_labels = {}, {}, {}
        for kind in plan.KINDS:
            check = QtWidgets.QCheckBox(i18n.t(KIND_TITLE[kind]))
            check.setCursor(QtCore.Qt.PointingHandCursor)
            card = ui.OptionCard(check)
            native = ui.label("", "aeroMeta", wrap=False)
            field = QtWidgets.QLineEdit()
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
            layout.addWidget(card)
            self.cards[kind], self.fields[kind], self.native_labels[kind] = card, field, native

        self.crs = ui.label("", "aeroMeta")
        layout.addWidget(self.crs)

        layout.addWidget(ui.label(i18n.t("export.preset"), "aeroSection"))
        self.preset = QtWidgets.QComboBox()
        self.preset.setObjectName("aeroCombo")
        self.preset.setProperty("arrow", True)
        self.preset.setCursor(QtCore.Qt.PointingHandCursor)
        self.preset.setFocusPolicy(QtCore.Qt.NoFocus)
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
        self.names = ui.label("")
        self.names.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addWidget(self.names)
        self.folder = ui.label("", "aeroMeta")
        layout.addWidget(self.folder)

        layout.addStretch(1)
        self.bar = QtWidgets.QProgressBar()
        self.bar.setObjectName("aeroProgress")
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 1000)
        self.bar.setVisible(False)
        layout.addWidget(self.bar)
        line = QtWidgets.QHBoxLayout()
        line.setSpacing(theme.PAD_S)
        self.mark = ui.Mark()
        line.addWidget(self.mark, 0, QtCore.Qt.AlignTop)
        self.status = ui.label("")
        line.addWidget(self.status, 1)
        layout.addLayout(line)

        buttons = QtWidgets.QHBoxLayout()
        buttons.setSpacing(theme.PAD)
        self.open_button = ui.big_button("export.open_folder", self._open_folder, width=170)
        buttons.addWidget(self.open_button)
        buttons.addStretch(1)
        self.go_button = ui.big_button("export.go", self._on_export, "aeroGo", width=210)
        buttons.addWidget(self.go_button)
        self.close_button = ui.big_button("export.close", self.close, width=110)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

        self.refresh()

    # -- lecture du projet ----------------------------------------------------------------

    def refresh(self):
        """Relit le projet (ce qui existe, sa resolution, son nom) : a l'ouverture et a chaque retour devant."""
        if self._busy:
            return
        self._info = msapi.raster_info()
        self._psx = msapi.document_path()
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
        self.crs.setText(i18n.t("export.crs", crs=self._info["crs"]) if self._info.get("crs") else "")
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
        self._refresh_names()

    @staticmethod
    def _cm_text(pixel_m):
        text = "{:.2f}".format(pixel_m * CM).rstrip("0").rstrip(".")
        return text or "0"

    def _is_native(self, kind):
        """True si le champ n'a pas ete touche (ou est vide ou faux) : on garde alors la grille de Metashape."""
        text = self.fields[kind].text().replace(",", ".").strip()
        try:
            value = float(text)
        except ValueError:
            return True
        return value <= 0 or text == self._cm_text(self._native.get(kind, 0.0))

    def _pixel(self, kind):
        """Taille de pixel voulue en metres (la taille native si le champ est intact, vide ou faux)."""
        if self._is_native(kind):
            return self._native.get(kind, 0.0)
        return float(self.fields[kind].text().replace(",", ".").strip()) / CM

    # -- noms -------------------------------------------------------------------------------

    def _on_preset(self, *_args):
        key = self.preset.currentData()
        self.preset_tip.setText(i18n.t(plan.preset(key)["tip"]))
        if self.sender() is self.preset:
            config.put(SECTION, "preset", key)
        self._refresh_names()

    def _wanted(self):
        return {kind: self._pixel(kind) for kind in plan.KINDS
                if self.cards[kind].button.isChecked() and self._pixel(kind) > 0}

    def _folder(self):
        return naming.outputs_folder(self._psx) if self._psx else ""

    def _refresh_names(self, *_args):
        for kind in plan.KINDS:
            self.fields[kind].setEnabled(self.cards[kind].button.isChecked() and bool(self._info.get(kind)))
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
                self._jobs = plan.build_jobs(site, self._date, wanted, self.preset.currentData(), folder,
                                             lang=self._lang, native={k for k in wanted if self._is_native(k)})
                lines = [job.name for job in self._jobs]
                for job in self._jobs:
                    lines.extend(plan.aux_names(job.name, job.spec))
                text = "\n".join(lines)
                if self._jobs[0].version > 1:
                    text += "\n" + i18n.t("export.version", n=self._jobs[0].version)
                too_long = naming.check_path_length(folder, [job.name for job in self._jobs])
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
        self.go_button.setEnabled(bool(self._jobs) and not self._busy)
        self.close_button.setEnabled(not self._busy)
        self.open_button.setEnabled(bool(self._folder()) and os.path.isdir(self._folder()))
        for widget in (self.preset, self.site):
            widget.setEnabled(not self._busy)

    # -- export -----------------------------------------------------------------------------

    def _on_export(self):
        if self._busy or not self._jobs:
            return
        chunk = msapi.chunk()
        if chunk is None:
            self._finish(i18n.t("export.no_chunk"), "error")
            return
        jobs = list(self._jobs)
        try:
            os.makedirs(self._folder(), exist_ok=True)
        except OSError as exc:
            self._finish(i18n.t("export.failed", name=self._folder(), error=exc), "error")
            return
        self._busy = True
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)
        self.bar.setVisible(True)
        self.mark.show_kind(None)
        self._refresh_buttons()
        total, written = len(jobs), 0
        problem = ""
        for index, job in enumerate(jobs):
            self.status.setText(i18n.t("export.working", name=job.name, i=index + 1, n=total))
            QtWidgets.QApplication.processEvents()

            def progress(percent, index=index):
                self.bar.setValue(int(1000 * (index + max(0.0, min(100.0, float(percent))) / 100.0) / total))
                QtWidgets.QApplication.processEvents()

            found = self._info.get(job.kind)
            width, height = found[1], found[2]
            big = plan.needs_bigtiff(width, height, 4 if job.kind == plan.ORTHO else 1,
                                     1 if job.kind == plan.ORTHO else 4)
            box = self._info.get("box_" + job.kind)
            ok, error = msapi.export_raster(chunk, job.kind, job.spec, job.path,
                                            None if job.native else job.pixel_m, box, big=big, progress=progress)
            if ok and not os.path.isfile(job.path):
                ok, error = False, i18n.t("export.missing")
            if not ok:
                problem = i18n.t("export.failed", name=job.name, error=error)
                journal.write("Fast Export : {} : {}".format(job.path, error))
                break
            written += os.path.getsize(job.path)
            journal.write("Fast Export : {}".format(job.path))
        self._busy = False
        if problem:
            self._finish(problem, "error")
        else:
            self._finish(i18n.t("export.done", n=total, size=size_text(written)), "ok")
            self._refresh_names()               # le numero de version suivant se met a jour

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
    """Ouvre la fenetre (non modale). Renvoie l'instance, a garder."""
    dialog = existing
    try:
        if dialog is not None:
            dialog.objectName()
            if dialog._lang != i18n.lang():
                dialog.deleteLater()
                dialog = None
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
