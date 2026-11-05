"""Fenetre Fast Space (Qt) : on choisit un disque, on scanne, on allege ou on supprime.

Le tableau montre, pour chaque projet Metashape : le fichier .psx, ses 3 dossiers parents (pour
reconnaitre le chantier), ce que pese « nuage dense + cartes de profondeur » (ce que l'allegement libere),
ce que pese le projet en entier (pour voir si cela vaut la peine de le supprimer), la date et l'etat
(ouvert, verrou oublie, dossier orphelin). Le tri se fait en cliquant un en-tete.

La colonne « Gardes » dit ce qui reste a cote du projet quand on le supprime (un point d'exclamation
signale un MNT ou une ortho internes sans export trouve).

Deux boutons :
  - Alleger             retire le nuage dense et les cartes de profondeur, garde tout le reste ;
  - Supprimer le projet retire le .psx et son .files, jamais les fichiers a cote.
Chaque action demande de choisir corbeille ou suppression definitive ; la suppression definitive
demande de taper DELETE. Le scan et les suppressions ne bloquent pas la fenetre ; l'allegement passe
par l'API de Metashape, qui travaille dans le fil principal : la fenetre se fige un instant par projet.
"""

import os
import shutil
import string
import threading
import time

from ... import config, i18n, journal, theme, ui
from ...qt import QtCore, QtGui, QtWidgets
from . import actions, scanner

POLL_MS = 150
WIDTH, HEIGHT = 1040, 680
ROLE_PROJECT = QtCore.Qt.UserRole
ROLE_SORT = QtCore.Qt.UserRole + 1
SECTION = "fastspace"
COLUMNS = ("space.col_project", "space.col_folder", "space.col_light", "space.col_total", "space.col_kept",
           "space.col_share", "space.col_modified", "space.col_state")
ELLIPSIS = "\u2026"
WARNING = "\u26a0"
TABLE_STYLE = """
    QTableWidget {{
        background: {ground_2}; alternate-background-color: {ground_3}; color: {fg_1};
        gridline-color: transparent; border: 1px solid {border}; border-radius: 8px; outline: none;
    }}
    QTableWidget::item {{ padding: 0 6px; border: none; }}
    QTableWidget::item:selected, QTableWidget::item:selected:!active {{ background: {soft}; color: {fg_0}; }}
    QHeaderView::section {{
        background: {ground_1}; color: {fg_2}; border: none; padding: 6px 8px; font-weight: bold;
    }}
    QTableCornerButton::section {{ background: {ground_1}; border: none; }}
"""


class _Item(QtWidgets.QTableWidgetItem):
    """Cellule qui se trie sur une valeur numerique ou textuelle choisie, pas sur le texte affiche."""

    def __lt__(self, other):
        mine, theirs = self.data(ROLE_SORT), other.data(ROLE_SORT)
        try:
            return mine < theirs
        except TypeError:
            return str(mine) < str(theirs)


class _Worker(threading.Thread):
    """Execute `job(worker)` hors du fil Qt."""

    def __init__(self, job):
        super(_Worker, self).__init__(daemon=True)
        self.job = job
        self.progress = (0, 0, "")
        self.result = None
        self.finished = False
        self.cancel = False

    def run(self):
        try:
            self.result = self.job(self)
        except Exception as exc:
            self.result = {"error": repr(exc)}
        self.finished = True


class ConfirmDialog(QtWidgets.QDialog):
    """Choix du sort (corbeille ou definitif) ; le definitif demande de taper DELETE."""

    def __init__(self, parent, text, recycle_ok, na_key="space.mode_recycle_na"):
        super(ConfirmDialog, self).__init__(parent)
        self.setWindowTitle(i18n.t("space.title"))
        self.setModal(True)
        self.setMinimumWidth(520)
        self.mode = None
        root = ui.styled_root(self)
        layout = QtWidgets.QVBoxLayout(root)
        pad = theme.PAD_L
        layout.setContentsMargins(pad, pad, pad, pad)
        layout.setSpacing(theme.PAD)
        layout.addWidget(ui.label(text))
        self.recycle = QtWidgets.QRadioButton(i18n.t("space.mode_recycle" if recycle_ok else na_key))
        self.recycle.setEnabled(recycle_ok)
        self.permanent = QtWidgets.QRadioButton(i18n.t("space.mode_permanent"))
        layout.addWidget(self.recycle)
        layout.addWidget(self.permanent)
        self.word = QtWidgets.QLineEdit()
        self.word.setPlaceholderText(i18n.t("space.type_delete", word=actions.CONFIRM_WORD))
        layout.addWidget(self.word)
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        self.ok = ui.button("space.confirm", self._accept, "aeroRun", width=120)
        row.addWidget(self.ok)
        row.addWidget(ui.button("space.cancel", self.reject, width=110))
        layout.addLayout(row)
        self.recycle.toggled.connect(self._update)
        self.permanent.toggled.connect(self._update)
        self.word.textChanged.connect(self._update)
        (self.recycle if recycle_ok else self.permanent).setChecked(True)
        self._update()

    def _update(self, *_args):
        permanent = self.permanent.isChecked()
        self.word.setEnabled(permanent)
        if not permanent:
            self.word.clear()
        self.ok.setEnabled(self.recycle.isChecked() or (permanent and actions.confirmed(self.word.text())))

    def _accept(self):
        if self.recycle.isChecked():
            self.mode = actions.RECYCLE
        elif self.permanent.isChecked() and actions.confirmed(self.word.text()):
            self.mode = actions.PERMANENT
        else:
            return
        self.accept()


def drives():
    """Lecteurs presents : [(racine, texte)] avec l'espace libre quand c'est rapide a lire."""
    found = []
    for letter in string.ascii_uppercase:
        root = letter + ":\\"
        if not os.path.exists(root):
            continue
        text = root
        if not actions.is_network(root):
            try:
                usage = shutil.disk_usage(root)
                text = i18n.t("space.drive_item", drive=root, free=scanner.gb(usage.free), total=scanner.gb(usage.total))
            except OSError:
                pass
        found.append((root, text))
    return found


class SpaceDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super(SpaceDialog, self).__init__(parent)
        self.setWindowTitle(i18n.t("space.title"))
        self.setModal(False)
        self.resize(WIDTH, HEIGHT)
        self.projects = []
        self._worker = None
        self._batch = None
        self._lang = i18n.lang()
        self._stepping = False
        root = ui.styled_root(self)
        layout = QtWidgets.QVBoxLayout(root)
        pad = theme.PAD_L
        layout.setContentsMargins(pad, pad, pad, pad)
        layout.setSpacing(theme.PAD)
        layout.addWidget(ui.label(i18n.t("space.intro"), "aeroMeta"))

        top = QtWidgets.QHBoxLayout()
        top.setSpacing(theme.PAD)
        self.drive = QtWidgets.QComboBox()
        self.drive.setObjectName("aeroCombo")
        self.drive.setFocusPolicy(QtCore.Qt.NoFocus)
        self.drive.setMinimumWidth(320)
        top.addWidget(self.drive, 1)
        top.addWidget(ui.button("space.folder", self._pick_folder, width=110))
        self.scan_button = ui.button("space.scan", self._on_scan, "aeroRun", width=110)
        top.addWidget(self.scan_button)
        layout.addLayout(top)

        self.bar = QtWidgets.QProgressBar()
        self.bar.setObjectName("aeroProgress")
        self.bar.setTextVisible(False)
        self.bar.setVisible(False)
        layout.addWidget(self.bar)
        self.status = ui.label("", "aeroMeta")
        layout.addWidget(self.status)

        filters = QtWidgets.QHBoxLayout()
        filters.setSpacing(theme.PAD)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText(i18n.t("space.search"))
        self.search.textChanged.connect(self._apply_filter)
        filters.addWidget(self.search, 1)
        self.hide_open = QtWidgets.QCheckBox(i18n.t("space.hide_open"))
        self.hide_open.toggled.connect(self._apply_filter)
        filters.addWidget(self.hide_open)
        filters.addWidget(ui.button("space.select_all", lambda: self._select(True), width=110))
        filters.addWidget(ui.button("space.select_none", lambda: self._select(False), width=110))
        layout.addLayout(filters)

        self.table = QtWidgets.QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels([i18n.t(key) for key in COLUMNS])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.setWordWrap(False)
        self.table.setStyleSheet(TABLE_STYLE.format(
            ground_1=theme.GROUND_1, ground_2=theme.GROUND_2, ground_3=theme.GROUND_3, fg_0=theme.FG_0,
            fg_1=theme.FG_1, fg_2=theme.FG_2, border=theme.BORDER_FAINT, soft=theme.GREEN_SOFT))
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        for column, width in enumerate((220, 190, 140, 110, 90, 56, 90)):
            self.table.setColumnWidth(column, width)
        self.table.itemChanged.connect(self._on_changed)
        self.table.itemSelectionChanged.connect(self._refresh_summary)
        layout.addWidget(self.table, 1)

        self.summary = ui.label("")
        layout.addWidget(self.summary)

        bottom = QtWidgets.QHBoxLayout()
        bottom.setSpacing(theme.PAD)
        bottom.addWidget(ui.button("space.open_log", self._open_log, width=150))
        bottom.addStretch(1)
        self.lighten_button = ui.button("space.lighten", self._on_lighten, "aeroRun", width=230)
        self.lighten_button.setToolTip(i18n.t("space.lighten_tip"))
        bottom.addWidget(self.lighten_button)
        self.delete_button = ui.button("space.delete", self._on_delete, "aeroNo", width=230)
        self.delete_button.setToolTip(i18n.t("space.delete_tip"))
        bottom.addWidget(self.delete_button)
        self.close_button = ui.button("space.close", self.close, width=96)
        bottom.addWidget(self.close_button)
        layout.addLayout(bottom)

        self._clock = ui.timer(self, POLL_MS, self._poll)
        self._fill_drives()
        self._refresh_buttons()

    # -- disque a scanner ----------------------------------------------------------

    def _fill_drives(self):
        saved = config.get(SECTION, "root")
        self.drive.clear()
        for root, text in drives():
            self.drive.addItem(text, root)
        if isinstance(saved, str) and saved and self.drive.findData(saved) < 0 and os.path.isdir(saved):
            self.drive.addItem(saved, saved)
        if isinstance(saved, str) and self.drive.findData(saved) >= 0:
            self.drive.setCurrentIndex(self.drive.findData(saved))
        elif self.drive.count() > 1:
            self.drive.setCurrentIndex(1)

    def _pick_folder(self):
        start = self.drive.currentData() or "C:\\"
        chosen = QtWidgets.QFileDialog.getExistingDirectory(self, i18n.t("space.pick_folder"), start)
        if not chosen:
            return
        chosen = os.path.normpath(chosen)
        index = self.drive.findData(chosen)
        if index < 0:
            self.drive.addItem(chosen, chosen)
            index = self.drive.count() - 1
        self.drive.setCurrentIndex(index)

    # -- scan -----------------------------------------------------------------------

    def _busy(self):
        return self._worker is not None or self._batch is not None

    def _on_scan(self):
        if self._worker is not None:
            self._worker.cancel = True
            return
        if self._batch is not None:
            return
        root = self.drive.currentData()
        if not root or not os.path.isdir(root):
            return
        config.put(SECTION, "root", root)
        self.table.setRowCount(0)
        self.projects = []
        self.bar.setRange(0, 0)
        self.bar.setVisible(True)
        self.scan_button.setText(i18n.t("space.stop"))

        def job(worker):
            def progress(done, total, text):
                worker.progress = (done, total, text)
            return scanner.scan(root, progress=progress, cancelled=lambda: worker.cancel)

        self._worker = _Worker(job)
        self._worker.start()
        self._clock.start()
        self._refresh_buttons()

    def _scan_finished(self, result):
        self.bar.setVisible(False)
        self.scan_button.setText(i18n.t("space.scan"))
        if not result or "projects" not in result:
            self.status.setText((result or {}).get("error", ""))
            return
        self.projects = result["projects"]
        self._fill_table()
        orphans = sum(1 for p in self.projects if p.orphan)
        self.status.setText(i18n.t(
            "space.scanned", n=len(self.projects) - orphans, orphans=orphans,
            total=scanner.gb(sum(p.total for p in self.projects)),
            light=scanner.gb(sum(p.light for p in self.projects)), seconds="{:.0f}".format(result["seconds"])))

    # -- tableau --------------------------------------------------------------------

    def _fill_table(self):
        here = actions.current_document_path()
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self.projects))
        for row, project in enumerate(self.projects):
            self._set_row(row, project, here)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(3, QtCore.Qt.DescendingOrder)
        self.table.blockSignals(False)
        self._apply_filter()
        self._refresh_summary()

    def _state_of(self, project, here):
        if project.orphan:
            return i18n.t("space.state_orphan")
        if project.in_use:
            return i18n.t("space.state_here") if actions.same_path(project.path, here) else i18n.t("space.state_open")
        if project.lock == scanner.STALE:
            return i18n.t("space.state_stale")
        return ""

    def _set_row(self, row, project, here=None):
        here = actions.current_document_path() if here is None else here
        name = _Item(project.name)
        name.setData(ROLE_PROJECT, project)
        name.setData(ROLE_SORT, project.name.lower())
        name.setToolTip(project.path)
        locked = bool(actions.refusal(project))
        flags = QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable
        if not locked:
            flags |= QtCore.Qt.ItemIsUserCheckable
        name.setFlags(flags)
        if not locked:
            name.setCheckState(QtCore.Qt.Unchecked)
        self.table.setItem(row, 0, name)

        folder = _Item(project.display_folder())
        folder.setData(ROLE_SORT, project.display_folder().lower())
        folder.setToolTip(os.path.dirname(project.path))
        self.table.setItem(row, 1, folder)

        light = _Item(scanner.gb(project.light))
        light.setData(ROLE_SORT, project.light)
        light.setToolTip(self._parts_text(project))
        light.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.table.setItem(row, 2, light)

        total = _Item(scanner.gb(project.total))
        total.setData(ROLE_SORT, project.total)
        total.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.table.setItem(row, 3, total)

        kept = _Item(self._kept_text(project))
        kept.setData(ROLE_SORT, project.kept.size)
        kept.setToolTip(self._kept_tip(project))
        kept.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.table.setItem(row, 4, kept)

        share = project.light / float(project.total) if project.total else 0.0
        part = _Item("{:.0f} %".format(100 * share))
        part.setData(ROLE_SORT, share)
        part.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.table.setItem(row, 5, part)

        stamp = time.strftime("%Y-%m-%d", time.localtime(project.modified)) if project.modified else ""
        modified = _Item(stamp)
        modified.setData(ROLE_SORT, project.modified)
        self.table.setItem(row, 6, modified)

        state = _Item(self._state_of(project, here))
        state.setData(ROLE_SORT, state.text())
        self.table.setItem(row, 7, state)

    @staticmethod
    def _kept_text(project):
        if project.orphan:
            return ""
        text = scanner.gb(project.kept.size) if project.kept.files else "-"
        return (WARNING + " " + text) if project.at_risk else text

    @staticmethod
    def _kept_tip(project):
        if project.orphan:
            return ""
        kept = project.kept
        lines = []
        if kept.files or kept.dirs:
            lines.append(i18n.t("space.tip_kept", files=kept.files, dirs=kept.dirs, size=scanner.gb(kept.size)))
            if kept.biggest:
                names = ", ".join("{} ({})".format(name, scanner.gb(size)) for size, name in kept.biggest)
                lines.append(i18n.t("space.tip_biggest", names=names))
        else:
            lines.append(i18n.t("space.tip_kept_none"))
        if project.at_risk:
            lines.append(i18n.t("space.tip_kept_risk", size=scanner.gb(project.internal_products)))
        return "\n".join(lines)

    @staticmethod
    def _parts_text(project):
        parts = project.parts
        known = {"dense_cloud", "depth_maps", "point_cloud", "elevation", "orthomosaic"}
        other = sum(size for name, size in parts.items() if name not in known)
        return i18n.t("space.tip_parts", dense=scanner.gb(project.dense), depth=scanner.gb(project.depth),
                      tie=scanner.gb(parts.get("point_cloud", 0)), dem=scanner.gb(parts.get("elevation", 0)),
                      ortho=scanner.gb(parts.get("orthomosaic", 0)), other=scanner.gb(other))

    def _project_at(self, row):
        item = self.table.item(row, 0)
        return item.data(ROLE_PROJECT) if item is not None else None

    def _apply_filter(self, *_args):
        needle = self.search.text().strip().lower()
        hide_open = self.hide_open.isChecked()
        for row in range(self.table.rowCount()):
            project = self._project_at(row)
            if project is None:
                continue
            hay = (project.path).lower()
            hidden = (bool(needle) and needle not in hay) or (hide_open and bool(actions.refusal(project)))
            self.table.setRowHidden(row, hidden)
        self._refresh_summary()

    def _select(self, value):
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and not self.table.isRowHidden(row) and item.flags() & QtCore.Qt.ItemIsUserCheckable:
                item.setCheckState(QtCore.Qt.Checked if value else QtCore.Qt.Unchecked)
        self.table.blockSignals(False)
        self._refresh_summary()

    def _on_changed(self, item):
        if item.column() == 0:
            self._refresh_summary()

    def _checked(self):
        found = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and not self.table.isRowHidden(row) and item.checkState() == QtCore.Qt.Checked:
                found.append(item.data(ROLE_PROJECT))
        return found

    def _highlighted(self):
        """Projets des lignes surlignees (clic, Maj ou Ctrl) : sert a la somme, pas aux boutons."""
        rows = sorted({item.row() for item in self.table.selectedItems()})
        found = [self._project_at(row) for row in rows if not self.table.isRowHidden(row)]
        return [project for project in found if project is not None]

    def _refresh_summary(self, *_args):
        picked = self._checked()
        key = "space.selection"
        if not picked:
            picked, key = self._highlighted(), "space.selection_rows"
        if picked:
            self.summary.setText(i18n.t(key, n=len(picked),
                                        light=scanner.gb(sum(p.light for p in picked if not p.orphan)),
                                        total=scanner.gb(sum(p.total for p in picked))))
        else:
            self.summary.setText(i18n.t("space.selection_none"))
        self._refresh_buttons()

    def _refresh_buttons(self):
        picked = self._checked() if hasattr(self, "table") else []
        idle = not self._busy()
        self.lighten_button.setEnabled(idle and any(not p.orphan for p in picked))
        self.delete_button.setEnabled(idle and bool(picked))
        self.close_button.setEnabled(self._batch is None)

    def _open_log(self):
        path = actions.log_path()
        folder = os.path.dirname(path)
        try:
            os.makedirs(folder, exist_ok=True)
            target = path if os.path.exists(path) else folder
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(target))
        except Exception as exc:
            journal.write("Fast Space : ouverture du journal : {!r}".format(exc))

    # -- actions --------------------------------------------------------------------

    def _on_lighten(self):
        picked = [p for p in self._checked() if not p.orphan]
        if not picked:
            return
        text = i18n.t("space.confirm_lighten", n=len(picked), size=scanner.gb(sum(p.light for p in picked)))
        self._confirm_and_run("lighten", picked, text)

    def _on_delete(self):
        picked = self._checked()
        if not picked:
            return
        text = i18n.t("space.confirm_delete", n=len(picked), size=scanner.gb(sum(p.total for p in picked)))
        files, kept_size, risky = scanner.delete_impact(picked)
        if files:
            text += "\n\n" + i18n.t("space.confirm_delete_kept", files=files, size=scanner.gb(kept_size))
        if risky:
            names = ", ".join(p.name for p in risky[:5]) + (ELLIPSIS if len(risky) > 5 else "")
            text += "\n\n" + i18n.t("space.confirm_delete_risk", names=names,
                           size=scanner.gb(sum(p.internal_products for p in risky)))
        self._confirm_and_run("delete", picked, text)

    def _confirm_and_run(self, kind, picked, text):
        if kind == "lighten":
            # Metashape efface lui-meme ses fichiers quand il enregistre : pas de corbeille possible.
            dialog = ConfirmDialog(self, text, False, "space.mode_recycle_none")
        else:
            dialog = ConfirmDialog(self, text, all(actions.recycle_available(p.path) for p in picked))
        ui.run(dialog)
        if dialog.mode is None:
            return
        journal.write("Fast Space : {} de {} projet(s), mode {}".format(kind, len(picked), dialog.mode))
        self._batch = {"kind": kind, "mode": dialog.mode, "todo": list(picked), "total": len(picked), "done": 0,
                       "freed": 0, "bin": 0, "ok": 0, "failed": [], "thread": None, "current": None, "sizes": {}}
        self.bar.setRange(0, len(picked))
        self.bar.setValue(0)
        self.bar.setVisible(True)
        self._refresh_buttons()
        self._clock.start()

    def _step_batch(self):
        """Un pas du lot : lance le projet suivant ou recueille le resultat du fil de suppression."""
        batch = self._batch
        thread = batch["thread"]
        if thread is not None:
            if not thread.finished:
                return
            self._record(batch, batch["current"], thread.result)
            batch["thread"], batch["current"] = None, None
        if not batch["todo"]:
            self._finish_batch(batch)
            return
        project = batch["todo"].pop(0)
        batch["current"] = project
        # Taille concernee AVANT l'action (l'allegement met le projet a jour) : c'est elle qu'on journalise.
        batch["sizes"][id(project)] = project.light if batch["kind"] == "lighten" else project.total
        action = i18n.t("space.act_lighten" if batch["kind"] == "lighten" else "space.act_delete")
        self.status.setText(i18n.t("space.working", action=action, i=batch["done"] + 1, n=batch["total"],
                                   name=project.name))
        self.bar.setValue(batch["done"])
        if batch["kind"] == "lighten":
            # L'API de Metashape ne travaille que dans le fil principal.
            QtWidgets.QApplication.processEvents()
            outcome = actions.lighten(project, batch["mode"],
                                      progress=lambda text: QtWidgets.QApplication.processEvents())
            self._record(batch, project, outcome)
            batch["current"] = None
        else:
            mode = batch["mode"]
            worker = _Worker(lambda _w, p=project: actions.delete_project(p, mode))
            batch["thread"] = worker
            worker.start()

    def _record(self, batch, project, outcome):
        outcome = outcome or {}
        ok = bool(outcome.get("ok"))
        size = batch["sizes"].get(id(project), 0)
        actions.log_action(batch["kind"], batch["mode"], project, ok, outcome.get("error", ""), size)
        batch["done"] += 1
        if ok:
            batch["ok"] += 1
            batch["freed"] += outcome.get("freed", 0)
            batch["bin"] += outcome.get("in_bin", 0)
            if batch["kind"] == "delete":
                self._remove_row(project)
            else:
                self._update_row(project)
        else:
            batch["failed"].append((project.name, outcome.get("error", "")))

    def _remove_row(self, project):
        if project in self.projects:
            self.projects.remove(project)
        for row in range(self.table.rowCount()):
            if self._project_at(row) is project:
                self.table.removeRow(row)
                break

    def _update_row(self, project):
        for row in range(self.table.rowCount()):
            if self._project_at(row) is project:
                self.table.blockSignals(True)
                self.table.setSortingEnabled(False)
                self._set_row(row, project)
                self.table.setSortingEnabled(True)
                self.table.blockSignals(False)
                break

    def _finish_batch(self, batch):
        self._batch = None
        self.bar.setVisible(False)
        self.status.setText(i18n.t("space.done", ok=batch["ok"], freed=scanner.gb(batch["freed"]),
                                   bin=scanner.gb(batch["bin"]), failed=len(batch["failed"])))
        self._refresh_summary()
        if batch["failed"]:
            from ..fastgcp import dialogs
            lines = [i18n.t("space.failed_line", name=name, error=error) for name, error in batch["failed"][:15]]
            dialogs.inform(self, i18n.t("space.failed_title"), "\n".join(lines))

    # -- minuterie ------------------------------------------------------------------

    def _poll(self):
        if self._batch is not None:
            if self._stepping:
                return                  # un projet est en cours : processEvents ne doit pas relancer un pas
            self._stepping = True
            try:
                self._step_batch()
            except Exception as exc:
                journal.write("Fast Space : lot interrompu : {!r}".format(exc))
                self._batch = None
                self.bar.setVisible(False)
                self.status.setText(repr(exc))
                self._refresh_buttons()
            finally:
                self._stepping = False
            return
        worker = self._worker
        if worker is None:
            self._clock.stop()
            return
        done, total, text = worker.progress
        if total:
            self.bar.setRange(0, total)
            self.bar.setValue(done)
            self.status.setText(i18n.t("space.scanning", done=done, total=total, name=text))
        else:
            self.status.setText(i18n.t("space.finding", name=text[-80:]))
        if not worker.finished:
            return
        self._clock.stop()
        self._worker = None
        try:
            self._scan_finished(worker.result)
        except Exception as exc:
            journal.write("Fast Space : affichage du scan : {!r}".format(exc))
        self._refresh_buttons()

    def closeEvent(self, event):
        if self._batch is not None:
            event.ignore()
            return
        if self._worker is not None:
            self._worker.cancel = True
        super(SpaceDialog, self).closeEvent(event)


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
        dialog = SpaceDialog(parent)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog
