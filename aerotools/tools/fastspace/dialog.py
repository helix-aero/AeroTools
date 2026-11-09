"""Fast Space window (Qt): pick a drive, scan, lighten or delete.

The table shows, for each Metashape project: the .psx file, its 3 parent folders (to
recognise the site), what "dense cloud + depth maps" weigh (what lightening frees),
what the whole project weighs (to see whether deleting it is worthwhile), the date and the state
(open, stale lock, orphan folder). Sorting is done by clicking a header.

The "Kept" column says what stays next to the project when it is deleted (an exclamation mark
flags an internal DEM or orthomosaic with no export found).

Two buttons:
  - Lighten           removes the dense cloud and the depth maps, keeps everything else;
  - Delete the project  removes the .psx and its .files, never the files next to it.
Each action asks to choose recycle bin or permanent deletion; permanent deletion
asks to type DELETE. The scan and the deletions do not block the window; lightening goes through
Metashape's API, which works in the main thread: the window freezes for a moment per project.
"""

import os
import shutil
import string
import threading
import time

from ... import config, i18n, journal, theme, ui
from ...qt import QtCore, QtGui, QtWidgets
from ... import controls
from . import actions, scanner

POLL_MS = 150
WIDTH, HEIGHT = 1500, 780
SCREEN_MARGIN = 60                                         # the window never exceeds the screen
ROLE_PROJECT = QtCore.Qt.UserRole
ROLE_SORT = QtCore.Qt.UserRole + 1
SECTION = "fastspace"
COLUMNS = ("space.col_project", "space.col_folder", "space.col_dense", "space.col_depth", "space.col_ortho",
           "space.col_total", "space.col_kept", "space.col_modified", "space.col_state")
COLUMN_WIDTHS = (250, 210, 105, 105, 110, 110, 100, 90)    # the last column takes the rest
COL_TOTAL = 5
ITEM_TEXT = {"dense": ("space.item_dense", "space.hint_dense"), "depth": ("space.item_depth", "space.hint_depth"),
             "ortho": ("space.item_ortho", "space.hint_ortho")}
ELLIPSIS = "\u2026"
CHECKBOX_ZONE = 30                                         # pixels from the left edge of the cell: the check box
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
    """Cell that sorts on a chosen numeric or text value, not on the displayed text."""

    def __lt__(self, other):
        mine, theirs = self.data(ROLE_SORT), other.data(ROLE_SORT)
        try:
            return mine < theirs
        except TypeError:
            return str(mine) < str(theirs)


class _Worker(threading.Thread):
    """Run `job(worker)` outside the Qt thread."""

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


class _Option(QtWidgets.QFrame):
    """An option to choose: a card with its radio button, clickable anywhere, green once chosen."""

    def __init__(self, radio, danger=False, toggle=False, side=None, note=None):
        super(_Option, self).__init__()
        self.setObjectName("aeroOption")
        self.setProperty("on", False)
        self.setProperty("danger", bool(danger))
        self.radio = radio
        self.toggle = toggle                               # a box ticks and unticks; a radio button can only be ticked
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(4)
        top = QtWidgets.QHBoxLayout()
        top.addWidget(radio)
        if side is not None:                               # text to the right of the button (the size)
            top.addStretch(1)
            top.addWidget(side)
        layout.addLayout(top)
        if note is not None:                               # explanation under the button
            layout.addWidget(note)
        radio.toggled.connect(self.sync)

    def sync(self, checked=None):
        self.setProperty("on", bool(self.radio.isChecked() if checked is None else checked))
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event):
        if self.radio.isEnabled():
            self.radio.setChecked(not self.radio.isChecked() if self.toggle else True)
        super(_Option, self).mousePressEvent(event)


class ForceDialog(QtWidgets.QDialog):
    """Extra screen before deleting a project that seems to be open elsewhere (lock file held)."""

    def __init__(self, parent, text):
        super(ForceDialog, self).__init__(parent)
        self.setWindowTitle(i18n.t("space.title"))
        self.setModal(True)
        self.setMinimumWidth(560)
        root = ui.styled_root(self)
        layout = QtWidgets.QVBoxLayout(root)
        pad = theme.PAD_L
        layout.setContentsMargins(pad, pad, pad, pad)
        layout.setSpacing(theme.PAD)
        layout.addWidget(ui.label(text))
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        row.addWidget(ui.button("space.force_confirm", self.accept, "aeroNo", width=170))
        row.addWidget(ui.button("space.cancel", self.reject, width=110))
        layout.addLayout(row)


class ConfirmDialog(QtWidgets.QDialog):
    """Choice of fate (recycle bin or permanent); permanent asks to type DELETE."""

    def __init__(self, parent, text, recycle_ok, na_key="space.mode_recycle_na", choices=None):
        """`choices`: [(key, text, explanation, bytes, projects, ticked)] of the check boxes (lighten: what to remove)."""
        super(ConfirmDialog, self).__init__(parent)
        self.setWindowTitle(i18n.t("space.title"))
        self.setModal(True)
        self.setMinimumWidth(680 if choices else 560)
        self.mode = None
        self.items = []                                     # keys of the boxes ticked on validation
        self._boxes = {}
        self._sizes = {}
        root = ui.styled_root(self)
        layout = QtWidgets.QVBoxLayout(root)
        self._layout = layout
        pad = theme.PAD_L
        layout.setContentsMargins(pad, pad, pad, pad)
        layout.setSpacing(theme.PAD)
        layout.addWidget(ui.label(text))
        self.free = ui.label("", "aeroSection")
        self._cards = []
        for key, name, hint, size, count, checked in (choices or []):
            box = QtWidgets.QCheckBox(name)
            box.setChecked(bool(checked))
            amount = ui.label(i18n.t("space.item_size", size=scanner.gb(size), n=count), "aeroSection", wrap=False)
            note = ui.label(hint, "aeroMeta")
            note.setContentsMargins(28, 0, 0, 0)               # under the text of the box, not under the box
            card = _Option(box, toggle=True, side=amount, note=note)
            card.sync()
            box.setEnabled(size > 0)
            if size <= 0:
                box.setChecked(False)
            self._boxes[key], self._sizes[key] = box, size
            self._cards.append(card)
            layout.addWidget(card)
            box.toggled.connect(self._update)
        if choices:
            layout.addWidget(self.free)
        layout.addWidget(ui.label(i18n.t("space.choose"), "aeroSection"))
        self.recycle = QtWidgets.QRadioButton(i18n.t("space.mode_recycle" if recycle_ok else na_key))
        self.recycle.setEnabled(recycle_ok)
        self.permanent = QtWidgets.QRadioButton(i18n.t("space.mode_permanent"))
        self._group = QtWidgets.QButtonGroup(self)          # the two radio buttons are in separate cards
        self._group.addButton(self.recycle)
        self._group.addButton(self.permanent)
        self._options = [_Option(self.recycle), _Option(self.permanent, danger=True)]
        for option in self._options:
            layout.addWidget(option)
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
        for option in self._options:
            option.sync()
        self._update()

    def showEvent(self, event):
        super(ConfirmDialog, self).showEvent(event)
        self._fit()

    def _fit(self):
        """Texts that wrap need more height than Qt foresees: they are given it."""
        grown = 0
        for label in self.findChildren(QtWidgets.QLabel):
            if label.wordWrap() and label.width() > 0:
                need = label.heightForWidth(label.width())
                if need > label.height():
                    grown += need - label.height()
                    label.setMinimumHeight(need)
        if grown:
            self.resize(self.width(), self.height() + grown)

    def _chosen(self):
        return [key for key, box in self._boxes.items() if box.isChecked()]

    def _update(self, *_args):
        permanent = self.permanent.isChecked()
        self.word.setEnabled(permanent)
        if not permanent:
            self.word.clear()
        ready = self.recycle.isChecked() or (permanent and actions.confirmed(self.word.text()))
        if self._boxes:
            chosen = self._chosen()
            self.free.setText(i18n.t("space.free_total", size=scanner.gb(sum(self._sizes[key] for key in chosen)))
                              if chosen else i18n.t("space.pick_one"))
            ready = ready and bool(chosen)
        self.ok.setEnabled(ready)

    def _accept(self):
        self.items = [key for key in scanner.ITEMS if key in self._chosen()]
        if self._boxes and not self.items:
            return
        if self.recycle.isChecked():
            self.mode = actions.RECYCLE
        elif self.permanent.isChecked() and actions.confirmed(self.word.text()):
            self.mode = actions.PERMANENT
        else:
            return
        self.accept()


def drives():
    """Drives present: [(root, text)] with the free space when it is quick to read."""
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


class SpaceDialog(controls.FollowsText, QtWidgets.QDialog):
    def __init__(self, parent=None):
        super(SpaceDialog, self).__init__(parent)
        self.setWindowTitle(i18n.t("space.title"))
        self.setModal(False)
        self.resize(*self._size())
        self.projects = []
        self._worker = None
        self._batch = None
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
        for column, width in enumerate(COLUMN_WIDTHS):
            self.table.setColumnWidth(column, width)
        self.table.horizontalHeaderItem(4).setToolTip(i18n.t("space.tip_col_ortho"))
        self.table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        self.table.itemChanged.connect(self._on_changed)
        self.table.itemSelectionChanged.connect(self._refresh_summary)
        self.table.viewport().installEventFilter(self)
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

    @staticmethod
    def _size():
        """Large window, but never larger than the screen."""
        try:
            area = QtWidgets.QApplication.primaryScreen().availableGeometry()
            return min(WIDTH, area.width() - SCREEN_MARGIN), min(HEIGHT, area.height() - SCREEN_MARGIN)
        except Exception:
            return WIDTH, HEIGHT

    # -- drive to scan -------------------------------------------------------------

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
            dense=scanner.gb(sum(p.dense for p in self.projects)), depth=scanner.gb(sum(p.depth for p in self.projects)),
            ortho=scanner.gb(sum(p.ortho_photos for p in self.projects)), seconds="{:.0f}".format(result["seconds"])))

    # -- table ----------------------------------------------------------------------

    def _fill_table(self):
        here = actions.current_document_path()
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self.projects))
        for row, project in enumerate(self.projects):
            self._set_row(row, project, here)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(COL_TOTAL, QtCore.Qt.DescendingOrder)
        self.table.blockSignals(False)
        self._apply_filter()
        self._refresh_summary()

    def _state_of(self, project, here):
        if project.orphan:
            return i18n.t("space.state_orphan")
        if project.lock == scanner.UNKNOWN:
            return i18n.t("space.state_unknown")
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
        locked = bool(actions.delete_refusal(project))
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

        for column, item in ((2, "dense"), (3, "depth"), (4, "ortho")):
            cell = _Item(scanner.gb(project.size_of(item)) if project.size_of(item) else "-")
            cell.setData(ROLE_SORT, project.size_of(item))
            cell.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            if column == 2:
                cell.setToolTip(self._parts_text(project))
            self.table.setItem(row, column, cell)

        total = _Item(scanner.gb(project.total))
        total.setData(ROLE_SORT, project.total)
        total.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.table.setItem(row, 5, total)

        kept = _Item(self._kept_text(project))
        kept.setData(ROLE_SORT, project.kept.size)
        kept.setToolTip(self._kept_tip(project))
        kept.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.table.setItem(row, 6, kept)

        stamp = time.strftime("%Y-%m-%d", time.localtime(project.modified)) if project.modified else ""
        modified = _Item(stamp)
        modified.setData(ROLE_SORT, project.modified)
        self.table.setItem(row, 7, modified)

        state = _Item(self._state_of(project, here))
        state.setData(ROLE_SORT, state.text())
        if project.lock_owner:
            state.setToolTip(i18n.t("space.tip_lock", owner=project.lock_owner))
        self.table.setItem(row, 8, state)

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
        known = {"dense_cloud", "depth_maps", "point_cloud", "elevation", "orthomosaic", "ortho_photos"}
        other = sum(size for name, size in parts.items() if name not in known)
        return i18n.t("space.tip_parts", dense=scanner.gb(project.dense), depth=scanner.gb(project.depth),
                      photos=scanner.gb(project.ortho_photos), tie=scanner.gb(parts.get("point_cloud", 0)),
                      dem=scanner.gb(parts.get("elevation", 0)), ortho=scanner.gb(parts.get("orthomosaic", 0)),
                      other=scanner.gb(other))

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
                if value and item.data(ROLE_PROJECT).in_use:
                    continue                    # a project open elsewhere is not ticked in bulk
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

    def eventFilter(self, source, event):
        """A click on the box of a highlighted row ticks or unticks all the highlighted rows."""
        if (source is self.table.viewport() and event.type() == QtCore.QEvent.MouseButtonPress
                and event.button() == QtCore.Qt.LeftButton
                and not event.modifiers() & (QtCore.Qt.ShiftModifier | QtCore.Qt.ControlModifier)):
            index = self.table.indexAt(event.pos())
            if index.isValid() and index.column() == 0 and self._toggle_highlighted(event.pos(), index):
                return True
        return super(SpaceDialog, self).eventFilter(source, event)

    def _toggle_highlighted(self, pos, index):
        """True if the click was on the box of one row among several highlighted ones (and was handled here)."""
        if pos.x() - self.table.visualRect(index).left() > CHECKBOX_ZONE:
            return False
        rows = {item.row() for item in self.table.selectedItems() if not self.table.isRowHidden(item.row())}
        clicked = self.table.item(index.row(), 0)
        if len(rows) < 2 or index.row() not in rows or clicked is None:
            return False
        if not clicked.flags() & QtCore.Qt.ItemIsUserCheckable:
            return False
        state = QtCore.Qt.Unchecked if clicked.checkState() == QtCore.Qt.Checked else QtCore.Qt.Checked
        self.table.blockSignals(True)
        for row in rows:
            item = self.table.item(row, 0)
            if item is not None and item.flags() & QtCore.Qt.ItemIsUserCheckable:
                item.setCheckState(state)
        self.table.blockSignals(False)
        self._refresh_summary()
        return True

    def _highlighted(self):
        """Projects of the highlighted rows (click, Shift or Ctrl): used for the sum, not for the buttons."""
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
                                        dense=scanner.gb(sum(p.dense for p in picked)),
                                        depth=scanner.gb(sum(p.depth for p in picked)),
                                        ortho=scanner.gb(sum(p.ortho_photos for p in picked)),
                                        total=scanner.gb(sum(p.total for p in picked))))
        else:
            self.summary.setText(i18n.t("space.selection_none"))
        self._refresh_buttons()

    def _refresh_buttons(self):
        picked = self._checked() if hasattr(self, "table") else []
        idle = not self._busy()
        self.lighten_button.setEnabled(idle and any(not p.orphan and not actions.refusal(p) and p.removable(scanner.ITEMS)
                                                    for p in picked))
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
            journal.write("Aero-Space: opening the journal: {!r}".format(exc))

    # -- actions --------------------------------------------------------------------

    def _on_context_menu(self, pos):
        """Right click: lighten / delete the highlighted rows; a single project: show it in Explorer."""
        index = self.table.indexAt(pos)
        if not index.isValid():
            return
        if not self.table.item(index.row(), 0).isSelected():
            self.table.clearSelection()
            self.table.selectRow(index.row())
        projects = self._highlighted()
        if not projects:
            return
        usable = [p for p in projects if not actions.delete_refusal(p)]
        lightenable = [p for p in projects if not p.orphan and not actions.refusal(p)]
        idle = not self._busy()
        menu = QtWidgets.QMenu(self.table)
        menu.setStyleSheet(theme.menu_stylesheet())
        many = len(projects) > 1
        lighten = menu.addAction(i18n.t("space.menu_lighten_n", n=len(projects)) if many else i18n.t("space.menu_lighten"))
        lighten.setEnabled(idle and bool(lightenable))
        lighten.triggered.connect(lambda *_a: self._on_lighten(lightenable))
        delete = menu.addAction(i18n.t("space.menu_delete_n", n=len(projects)) if many else i18n.t("space.menu_delete"))
        delete.setEnabled(idle and bool(usable))
        delete.triggered.connect(lambda *_a: self._on_delete(usable))
        if not many:
            menu.addSeparator()
            reveal = menu.addAction(i18n.t("space.menu_reveal"))
            reveal.triggered.connect(lambda *_a: self._reveal(projects[0]))
        (getattr(menu, "exec_", None) or getattr(menu, "exec"))(self.table.viewport().mapToGlobal(pos))

    @staticmethod
    def _reveal(project):
        if not actions.reveal(project.path):
            journal.write("Aero-Space: cannot open Explorer on {}".format(project.path))

    def _on_lighten(self, projects=None):
        picked = [p for p in (self._checked() if projects is None else projects) if not p.orphan and not actions.refusal(p)]
        if not picked:
            return
        text = i18n.t("space.confirm_lighten", n=len(picked))
        stale = [p for p in picked if p.lock == scanner.STALE]
        if stale:
            owners = ", ".join(sorted({p.lock_owner for p in stale if p.lock_owner})) or "?"
            text += "\n\n" + i18n.t("space.stale_note", n=len(stale), owners=owners)
        saved = config.get(SECTION, "items")
        wanted = [item for item in saved if item in scanner.ITEMS] if isinstance(saved, list) else list(scanner.DEFAULT_ITEMS)
        choices = []
        for item in scanner.ITEMS:
            having = [p for p in picked if p.size_of(item)]
            choices.append((item, i18n.t(ITEM_TEXT[item][0]), i18n.t(ITEM_TEXT[item][1]),
                            sum(p.size_of(item) for p in picked), len(having), item in wanted))
        self._confirm_and_run("lighten", picked, text, choices)

    def _on_delete(self, projects=None):
        picked = self._checked() if projects is None else list(projects)
        if not picked:
            return
        busy = [p for p in picked if p.in_use]
        if busy:                                    # lock held elsewhere: only continue on explicit confirmation
            names = ", ".join(p.name for p in busy[:5]) + (ELLIPSIS if len(busy) > 5 else "")
            owners = ", ".join(sorted({p.lock_owner for p in busy if p.lock_owner})) or "?"
            force = ForceDialog(self, i18n.t("space.force_text", n=len(busy), names=names) + "\n\n"
                                + i18n.t("space.force_owners", owners=owners))
            ui.run(force)
            if force.result() != QtWidgets.QDialog.Accepted:
                return
        text = i18n.t("space.confirm_delete", n=len(picked), size=scanner.gb(sum(p.total for p in picked)))
        files, kept_size, risky = scanner.delete_impact(picked)
        if files:
            text += "\n\n" + i18n.t("space.confirm_delete_kept", files=files, size=scanner.gb(kept_size))
        if risky:
            names = ", ".join(p.name for p in risky[:5]) + (ELLIPSIS if len(risky) > 5 else "")
            text += "\n\n" + i18n.t("space.confirm_delete_risk", names=names,
                           size=scanner.gb(sum(p.internal_products for p in risky)))
        self._confirm_and_run("delete", picked, text, forced=busy)

    def _confirm_and_run(self, kind, picked, text, choices=None, forced=()):
        if kind == "lighten":
            # Metashape deletes its own files when it saves: no recycle bin possible.
            dialog = ConfirmDialog(self, text, False, "space.mode_recycle_none", choices)
        else:
            dialog = ConfirmDialog(self, text, all(actions.recycle_available(p.path) for p in picked))
        ui.run(dialog)
        if dialog.mode is None:
            return
        items = list(dialog.items)
        if kind == "lighten":
            config.put(SECTION, "items", items)
        journal.write("Aero-Space: {} of {} project(s), mode {}{}".format(
            kind, len(picked), dialog.mode, ", " + "+".join(items) if kind == "lighten" else ""))
        self._batch = {"kind": kind, "mode": dialog.mode, "todo": list(picked), "total": len(picked), "done": 0,
                       "freed": 0, "bin": 0, "ok": 0, "failed": [], "thread": None, "current": None, "sizes": {},
                       "items": items, "forced": {id(p) for p in forced}}
        self.bar.setRange(0, len(picked))
        self.bar.setValue(0)
        self.bar.setVisible(True)
        self._refresh_buttons()
        self._clock.start()

    def _step_batch(self):
        """One step of the batch: starts the next project or collects the result of the deletion thread."""
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
        # Size concerned BEFORE the action (lightening updates the project): this is the one that is journalled.
        batch["sizes"][id(project)] = (project.removable(batch["items"]) if batch["kind"] == "lighten"
                                       else project.total)
        action = i18n.t("space.act_lighten" if batch["kind"] == "lighten" else "space.act_delete")
        self.status.setText(i18n.t("space.working", action=action, i=batch["done"] + 1, n=batch["total"],
                                   name=project.name))
        self.bar.setValue(batch["done"])
        if batch["kind"] == "lighten":
            # Metashape's API only works in the main thread.
            QtWidgets.QApplication.processEvents()
            outcome = actions.lighten(project, batch["mode"],
                                      progress=lambda text: QtWidgets.QApplication.processEvents(),
                                      items=batch["items"])
            self._record(batch, project, outcome)
            batch["current"] = None
        else:
            mode = batch["mode"]
            confirmed = id(project) in batch["forced"]        # only the projects the user saw as open and confirmed
            worker = _Worker(lambda _w, p=project: actions.delete_project(p, mode, force=confirmed))
            batch["thread"] = worker
            worker.start()

    def _record(self, batch, project, outcome):
        outcome = outcome or {}
        ok = bool(outcome.get("ok"))
        size = batch["sizes"].get(id(project), 0)
        note = outcome.get("error", "") or ("safety copy: " + outcome["backup"] if outcome.get("backup") else "")
        actions.log_action(batch["kind"], batch["mode"], project, ok, note, size)
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
            from ... import dialogs
            lines = [i18n.t("space.failed_line", name=name, error=error) for name, error in batch["failed"][:15]]
            dialogs.inform(self, i18n.t("space.failed_title"), "\n".join(lines))

    # -- timer ----------------------------------------------------------------------

    def _poll(self):
        if self._batch is not None:
            if self._stepping:
                return                  # a project is in progress: processEvents must not start another step
            self._stepping = True
            try:
                self._step_batch()
            except Exception as exc:
                journal.write("Aero-Space: batch interrupted: {!r}".format(exc))
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
            journal.write("Aero-Space: showing the scan: {!r}".format(exc))
        self._refresh_buttons()

    def closeEvent(self, event):
        if self._batch is not None:
            event.ignore()
            return
        if self._worker is not None:
            self._worker.cancel = True
        super(SpaceDialog, self).closeEvent(event)


def show(parent=None, existing=None):
    """Open the window (non-modal). Returns the instance, to be kept."""
    dialog = existing
    try:
        if dialog is not None:
            dialog.objectName()
    except RuntimeError:
        dialog = None
    if dialog is None:
        dialog = SpaceDialog(parent)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog
