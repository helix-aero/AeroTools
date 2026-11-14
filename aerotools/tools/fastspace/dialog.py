"""Fast Space window (Qt): pick a drive, scan, lighten or delete.

The table shows, for each Metashape project: the .psx file, its 3 parent folders (to recognise the site), what "dense cloud + depth maps"
weigh (what lightening frees), what the whole project weighs (to see whether deleting it is worthwhile), the date and the state (open,
stale lock, orphan folder). Sorting is done by clicking a header.

The "Kept" column says what stays next to the project when it is deleted (an exclamation mark flags an internal DEM or orthomosaic
with no export found).

Two actions:
  - Lighten (the primary button)  removes the dense cloud and the depth maps, keeps everything else;
  - Delete project (red, on the left)  removes the .psx and its .files, never the files next to it.
Each action opens a confirmation that says what goes, how much space it frees and what stays; it asks to choose the recycle bin or
permanent deletion, and permanent deletion asks to type DELETE. The scan and the deletions run as tasks of the window (a worker
thread: the window stays usable); lightening goes through Metashape's API, which works in the main thread: it is a cooperative task,
the window stays alive between two steps and Cancel stops the batch after the project in hand.
"""

import os
import shutil
import string
import time

from ... import config, i18n, journal, uikit, workers
from ...qt import QtCore, QtGui, QtWidgets
from ...uikit import table as kit_table
from . import actions, scanner

SECTION = "fastspace"
ROLE_PROJECT = QtCore.Qt.UserRole + 2                      # the project of a row (the kit keeps UserRole and UserRole + 1)
ITEM_TEXT = {"dense": ("space.item_dense", "space.hint_dense"), "depth": ("space.item_depth", "space.hint_depth"),
             "ortho": ("space.item_ortho", "space.hint_ortho")}
ELLIPSIS = "\u2026"
CHECKBOX_ZONE = 30                                         # design pixels from the left edge of the cell: the check box
WARNING = "\u26a0"
COL_TOTAL = 5


def _columns():
    Column = uikit.Column
    return [Column("project", i18n.t("space.col_project"), width=230), Column("folder", i18n.t("space.col_folder"), stretch=True),
            Column("dense", i18n.t("space.col_dense"), "right", 100, kind="number"),
            Column("depth", i18n.t("space.col_depth"), "right", 100, kind="number"),
            Column("ortho", i18n.t("space.col_ortho"), "right", 100, kind="number"),
            Column("total", i18n.t("space.col_total"), "right", 110, kind="number"),
            Column("kept", i18n.t("space.col_kept"), "right", 90, kind="number"),
            Column("modified", i18n.t("space.col_modified"), width=96),
            Column("state", i18n.t("space.col_state"), width=170)]


def _split_mode(text):
    """'Title (detail)' -> ('Title', 'Detail.'): the first level of a choice card is what it does, the second what follows from it."""
    if " (" in text and text.endswith(")"):
        title, detail = text[:-1].split(" (", 1)
        return title, detail[:1].upper() + detail[1:] + "."
    if ": " in text:
        title, detail = text.split(": ", 1)
        return title, detail[:1].upper() + detail[1:] + ("" if detail.endswith(".") else ".")
    return text, ""


def _exec(dialog):
    """Run a modal window (replaced by the tests, where nobody is there to click)."""
    return (getattr(dialog, "exec_", None) or getattr(dialog, "exec"))()


def _caption(text, info=""):
    """A small muted heading of the confirmation, with its "i" when there is more to say."""
    line = uikit.hbox(spacing=uikit.tokens.S)
    line.addWidget(uikit.label(text, "small", "muted", wrap=False))
    if info:
        line.addWidget(uikit.InfoButton(info))
    line.addStretch(1)
    return line


class _Notice(QtWidgets.QFrame):
    """A warning inside the confirmation, drawn like a kit banner but with each line on its own: a banner's wrapped text is measured
    at the narrowest width and would make this window taller than it is (kit request: a banner that does not wrap)."""

    def __init__(self, kind, text):
        super(_Notice, self).__init__()
        self.setProperty("ui", "banner")
        self.setProperty("kind", kind)
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        row = uikit.hbox((uikit.tokens.M, uikit.tokens.S + 2, uikit.tokens.M, uikit.tokens.S + 2), uikit.tokens.M, parent=self)
        row.addWidget(uikit.Glyph({"danger": "error", "warning": "warn"}.get(kind, kind)), 0, QtCore.Qt.AlignTop)
        column = uikit.vbox(spacing=uikit.tokens.XS)
        for line in str(text).split(chr(10)):
            column.addWidget(uikit.label(line, "body", wrap=False))
        row.addLayout(column, 1)


class ConfirmDialog(uikit.ToolWindow):
    """The question before anything is removed: what goes, how much space it frees, what stays, how it goes (recycle bin or
    permanently; permanent asks to type DELETE) and one red button that says what it does. Enter never confirms by itself."""
    MARK = "tool_space"

    def __init__(self, parent, text, recycle_ok, na_key="space.mode_recycle_na", choices=None, upgrade=0, plan=None):
        """`choices`: [(key, text, explanation, bytes, projects, ticked)] of the switches (lighten: what to remove).
        `upgrade`: number of projects written by an older Metashape than this one (a save would upgrade their format): a switch,
        off, lets the user allow it. `plan`: {"title", "remove": [lines], "remove_info", "kept": [lines], "kept_info",
        "warnings": [(kind, text)], "go"}; without it, `text` (paragraphs apart by a blank line) says it all."""
        paragraphs = [part.strip() for part in str(text).split("\n\n") if part.strip()]
        plan = plan or {"title": paragraphs[0] if paragraphs else "", "remove": [], "kept": paragraphs[1:], "warnings": [],
                         "go": i18n.t("space.confirm")}
        super(ConfirmDialog, self).__init__(parent, title=i18n.t("space.title"), modal=True)
        self.set_title(plan["title"])
        self.mode = None
        self.allow_upgrade = False
        self.upgrade_box = None
        self.items = []                                     # keys of the switches on when validated
        self._boxes = {}
        self._sizes = {}
        self._go = plan.get("go") or i18n.t("space.confirm")
        self._lighten = bool(choices)
        body = QtWidgets.QWidget()
        body.setMinimumWidth(uikit.px(600))
        lay = uikit.vbox(spacing=uikit.tokens.S, parent=body)

        lay.addLayout(_caption(i18n.t("space.sec_remove"), plan.get("remove_info", "")))
        for key, name, hint, size, count, checked in (choices or []):
            line = uikit.hbox(spacing=uikit.tokens.S)
            box = uikit.Toggle(name, bool(checked) and size > 0)
            box.setEnabled(size > 0)
            self._boxes[key], self._sizes[key] = box, size
            line.addWidget(box, 1)
            line.addWidget(uikit.label(i18n.t("space.item_size", size=scanner.gb(size), n=count), "body", wrap=False))
            line.addWidget(uikit.InfoButton(hint))
            lay.addLayout(line)
            box.toggled.connect(self._update)
        for line_text in plan.get("remove") or []:
            lay.addWidget(uikit.label(line_text, "strong", wrap=False))           # what goes and how much: the first level
        self.free = uikit.label("", "strong")
        if choices:
            lay.addWidget(self.free)

        kept = plan.get("kept") or []
        if kept:
            lay.addLayout(_caption(i18n.t("space.sec_kept"), plan.get("kept_info", "")))
            for line_text in kept:
                lay.addWidget(uikit.label(line_text, "small", "muted", wrap=False))
        for kind, warning in plan.get("warnings") or []:
            lay.addWidget(_Notice(kind, warning))
        if upgrade:
            self.upgrade_box = uikit.Toggle(i18n.t("space.allow_upgrade", n=upgrade))
            lay.addWidget(self.upgrade_box)

        title, detail = _split_mode(i18n.t("space.mode_recycle" if recycle_ok else na_key))
        self.recycle = uikit.ChoiceCard(title, detail, "recycle")
        self.recycle.setEnabled(recycle_ok)
        title, detail = _split_mode(i18n.t("space.mode_permanent"))
        self.permanent = uikit.ChoiceCard(title, detail, "permanent", danger=True)
        self._group = QtWidgets.QButtonGroup(self)
        self._group.addButton(self.recycle)
        self._group.addButton(self.permanent)
        modes = uikit.hbox(spacing=uikit.tokens.S)                  # side by side: the window must stay short on a 768 px screen
        modes.addWidget(self.recycle, 1)
        modes.addWidget(self.permanent, 1)
        lay.addLayout(modes)
        self.word = uikit.TextField("", i18n.t("space.type_delete", word=actions.CONFIRM_WORD))
        lay.addWidget(self.word)
        lay.addStretch(1)
        self.set_content(body)
        self.set_primary(self._go, self._accept, role="danger")
        self.recycle.toggled.connect(self._update)
        self.permanent.toggled.connect(self._update)
        self.word.textChanged.connect(self._update)
        (self.recycle if recycle_ok else self.permanent).setChecked(True)
        self._update()

    def showEvent(self, event):
        super(ConfirmDialog, self).showEvent(event)
        QtCore.QTimer.singleShot(0, self._tighten)

    def _tighten(self):
        """Wrapped texts ask for a taller window than they need: give the height back."""
        need = self.layout().totalMinimumSize().height()
        if self.isVisible() and 0 < need < self.height():
            self.resize(self.width(), need)

    def keyPressEvent(self, event):
        """Enter presses the focused button only: the red one is never pressed by a stray Enter."""
        if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            focus = QtWidgets.QApplication.focusWidget()
            if isinstance(focus, QtWidgets.QAbstractButton) and focus.isEnabled():
                focus.click()
            event.accept()
            return
        super(ConfirmDialog, self).keyPressEvent(event)

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
        if not self._lighten:
            self.footer.set_primary(i18n.t("space.go_permanent" if permanent else "space.go_recycle"), "danger")
        self.next_button.setEnabled(ready)

    def refresh_buttons(self, *_args):
        super(ConfirmDialog, self).refresh_buttons()
        if hasattr(self, "word"):
            self._update()

    def _accept(self):
        self.allow_upgrade = bool(self.upgrade_box is not None and self.upgrade_box.isChecked())
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


class SpaceDialog(uikit.ToolWindow):
    MARK = "tool_space"
    DESIGN_SIZE = (1180, 640)
    CLOSE_ONLY = True
    outcome = QtCore.Signal(object, object)                 # (project, result) of a batch step: the table is only touched by the UI thread

    def __init__(self, parent=None):
        super(SpaceDialog, self).__init__(parent, title=i18n.t("space.title"), info=i18n.t("space.intro"))
        self.projects = []
        self._batch = None
        body = QtWidgets.QWidget()
        lay = uikit.vbox(spacing=uikit.tokens.M, parent=body)

        top = uikit.hbox(spacing=uikit.tokens.S)
        self.drive = uikit.ChoiceBox()
        top.addWidget(self.drive, 1)
        self.folder_button = uikit.Button(i18n.t("space.folder"), "secondary", handler=self._pick_folder)
        top.addWidget(self.folder_button)
        self.scan_button = uikit.Button(i18n.t("space.scan"), "secondary", handler=self._on_scan)
        top.addWidget(self.scan_button)
        lay.addLayout(top)

        filters = uikit.hbox(spacing=uikit.tokens.S)
        self.search = uikit.TextField("", i18n.t("space.search"))
        self.search.textChanged.connect(self._apply_filter)
        filters.addWidget(self.search, 1)
        self.hide_open = uikit.Toggle(i18n.t("space.hide_open"))
        self.hide_open.toggled.connect(self._apply_filter)
        filters.addWidget(self.hide_open)
        self.select_all_button = uikit.Button(i18n.t("space.select_all"), "quiet", handler=lambda: self._select(True))
        self.select_none_button = uikit.Button(i18n.t("space.select_none"), "quiet", handler=lambda: self._select(False))
        filters.addWidget(self.select_all_button)
        filters.addWidget(self.select_none_button)
        lay.addLayout(filters)

        self.grid = uikit.DataTable(_columns(), selection="extended")
        self.table = self.grid.table
        self.table.horizontalHeaderItem(4).setToolTip(i18n.t("space.tip_col_ortho"))
        self.table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        self.table.itemChanged.connect(self._on_changed)
        self.grid.selectionChanged.connect(self._refresh_summary)
        self.table.viewport().installEventFilter(self)
        self.grid.set_empty(i18n.t("space.empty_start"), i18n.t("space.empty_start_text"))
        lay.addWidget(self.grid, 1)

        self.status = uikit.ElidedLabel("", "small", "muted")      # what the last scan or batch found (one line, cut when short of room)
        lay.addWidget(self.status)
        tiles = uikit.hbox(spacing=uikit.tokens.S)               # the figures the user came for, in the first level of the hierarchy
        self.stats = {}
        for key in ("count", "dense", "depth", "ortho", "total"):
            tile = uikit.Stat("-", i18n.t("space.stat_" + key))
            self.stats[key] = tile
            tiles.addWidget(tile, 1)
        lay.addLayout(tiles)
        self.set_content(body)

        self.log_button = uikit.Button(i18n.t("space.open_log"), "secondary", handler=self._open_log)
        self.footer.add_left(self.log_button)
        self.delete_button = uikit.Button(i18n.t("space.delete"), "danger", handler=self._on_delete)
        self.delete_button.setToolTip(i18n.t("space.delete_tip"))
        self.footer.add_left(self.delete_button)
        self.set_primary(i18n.t("space.lighten"), self._on_lighten)
        self.lighten_button = self.next_button
        self.lighten_button.setToolTip(i18n.t("space.lighten_tip"))
        self.close_button = self.cancel_button
        self.outcome.connect(self._on_outcome)
        self._fill_drives()
        self._refresh_summary()

    def _window_id(self):
        try:
            return int(self.winId())
        except Exception:
            return 0

    # -- drive to scan -------------------------------------------------------------

    def _fill_drives(self):
        saved = config.get(SECTION, "root")
        self.drive.set_choices([(root, text) for root, text in drives()])
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

    def _on_scan(self):
        if self.is_busy():
            return
        root = self.drive.currentData()
        if not root or not os.path.isdir(root):
            return
        config.put(SECTION, "root", root)
        self.table.setRowCount(0)
        self.projects = []
        self.status.setFullText("")
        self.grid.set_loading(i18n.t("space.finding", name="").strip())

        def work(progress):
            def report(done, total, text):
                if total:
                    workers.advance(progress, done, total, i18n.t("space.scanning", done=done, total=total, name=text))
                else:
                    workers.advance(progress, 0, 0, i18n.t("space.finding", name=text[-80:]))
            return scanner.scan(root, progress=report, cancelled=progress.is_cancelled)

        self.run(work, name="space-scan", indeterminate=True, on_done=self._scan_finished,
                 on_cancel=lambda: self._scan_finished(self.runner().result), on_error=lambda _exc: self._fill_table())

    def _scan_finished(self, result):
        if not result or "projects" not in result:
            self._fill_table()
            if (result or {}).get("error"):
                self.show_banner("danger", result["error"])
            return
        self.projects = result["projects"]
        self._fill_table()
        orphans = sum(1 for p in self.projects if p.orphan)
        stopped = i18n.t("space.scan_stopped") + " " if result.get("cancelled") else ""
        self.status.setFullText(stopped + i18n.t(
            "space.scanned", n=len(self.projects) - orphans, orphans=orphans,
            total=scanner.gb(sum(p.total for p in self.projects)),
            dense=scanner.gb(sum(p.dense for p in self.projects)), depth=scanner.gb(sum(p.depth for p in self.projects)),
            ortho=scanner.gb(sum(p.ortho_photos for p in self.projects)), seconds="{:.0f}".format(result["seconds"])))

    # -- table ----------------------------------------------------------------------

    def _fill_table(self):
        here = actions.current_document_path()
        self.table.blockSignals(True)
        if self.projects:
            self.grid.set_rows([self._cells(project, here) for project in self.projects])
            self.table.setSortingEnabled(False)               # a changed cell of the sorted column would move rows under our feet
            for row in range(self.table.rowCount()):
                self._mark_row(row, self.projects[self.table.item(row, 0).data(QtCore.Qt.UserRole)])
            self.table.setSortingEnabled(True)
            self.grid.sort_by("total", descending=True)
        else:
            self.grid.set_empty(i18n.t("space.empty_none"), i18n.t("space.empty_none_text"))
            self.grid.set_rows([])
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

    def _cells(self, project, here=None):
        """The nine cells of a row: (text, sort value)."""
        here = actions.current_document_path() if here is None else here
        size = lambda item: (scanner.gb(project.size_of(item)) if project.size_of(item) else "-", project.size_of(item))
        stamp = time.strftime("%Y-%m-%d", time.localtime(project.modified)) if project.modified else ""
        state = self._state_of(project, here)
        return [uikit.cell(project.name, project.name.lower(), bold=True), (project.display_folder(), project.display_folder().lower()),
                size("dense"), size("depth"), size("ortho"), (scanner.gb(project.total), project.total),
                (self._kept_text(project), project.kept.size), (stamp, project.modified), (state, state)]

    def _mark_row(self, row, project):
        """What a cell cannot say through its text: the project of the row, the tick box, the tooltips."""
        name = self.table.item(row, 0)
        name.setData(ROLE_PROJECT, project)
        name.setToolTip(project.path)
        flags = QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable
        if not actions.delete_refusal(project):
            flags |= QtCore.Qt.ItemIsUserCheckable
        name.setFlags(flags)
        if flags & QtCore.Qt.ItemIsUserCheckable:
            name.setCheckState(QtCore.Qt.Unchecked)
        self.table.item(row, 1).setToolTip(os.path.dirname(project.path))
        self.table.item(row, 2).setToolTip(self._parts_text(project))
        self.table.item(row, 6).setToolTip(self._kept_tip(project))
        if project.lock_owner:
            self.table.item(row, 8).setToolTip(i18n.t("space.tip_lock", owner=project.lock_owner))

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
        if pos.x() - self.table.visualRect(index).left() > uikit.px(CHECKBOX_ZONE):
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
        shown = {"count": (str(len(picked)), i18n.t("space.stat_ticked" if key == "space.selection" else "space.stat_highlighted", n=len(picked))),
                 "dense": (scanner.gb(sum(p.dense for p in picked)), None), "depth": (scanner.gb(sum(p.depth for p in picked)), None),
                 "ortho": (scanner.gb(sum(p.ortho_photos for p in picked)), None), "total": (scanner.gb(sum(p.total for p in picked)), None)}
        for name, tile in self.stats.items():
            value, caption = shown[name]
            tile.set_value(value if picked else "-", None if picked else "faint")
            tile.set_caption(caption if (picked and caption) else i18n.t("space.stat_" + name))
        self.stats["total"].set_value(shown["total"][0] if picked else "-", "accent" if picked else "faint")
        self._refresh_buttons()

    def _refresh_buttons(self):
        if not hasattr(self, "delete_button"):
            return
        picked = self._checked() if hasattr(self, "table") else []
        idle = not self.is_busy()
        self.lighten_button.setEnabled(idle and any(not p.orphan and not actions.refusal(p) and p.removable(scanner.ITEMS)
                                                    for p in picked))
        self.delete_button.setEnabled(idle and bool(picked))
        self.scan_button.setEnabled(idle)
        self.folder_button.setEnabled(idle)
        self.drive.setEnabled(idle)

    def refresh_buttons(self, *_args):
        super(SpaceDialog, self).refresh_buttons()
        self._refresh_buttons()

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
        idle = not self.is_busy()
        menu = QtWidgets.QMenu(self.table)
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
        here = actions.current_document_path()
        picked = [p for p in (self._checked() if projects is None else projects)
                  if not p.orphan and not actions.refusal(p, here) and p.removable(scanner.ITEMS)]       # nothing to remove: left alone
        if not picked:
            return
        warnings = [("warning", i18n.t("space.lighten_warning"))]
        stale = [p for p in picked if p.lock == scanner.STALE]
        if stale:
            owners = ", ".join(sorted({p.lock_owner for p in stale if p.lock_owner})) or "?"
            warnings.append(("warning", i18n.t("space.stale_note", n=len(stale), owners=owners)))
        saved = config.get(SECTION, "items")
        wanted = [item for item in saved if item in scanner.ITEMS] if isinstance(saved, list) else list(scanner.DEFAULT_ITEMS)
        choices = []
        for item in scanner.ITEMS:
            having = [p for p in picked if p.size_of(item)]
            choices.append((item, i18n.t(ITEM_TEXT[item][0]), i18n.t(ITEM_TEXT[item][1]),
                            sum(p.size_of(item) for p in picked), len(having), item in wanted))
        upgrade = sum(1 for p in picked if actions.upgrade_note(p))
        title = i18n.t("space.ask_lighten", n=len(picked))
        plan = {"title": title, "remove": [], "remove_info": i18n.t("space.lighten_slow"),
                "kept": [i18n.t("space.lighten_kept")],
                "kept_info": i18n.t("space.confirm_lighten_kept"), "warnings": warnings, "go": i18n.t("space.go_lighten")}
        self._confirm_and_run("lighten", picked, title, choices, upgrade=upgrade, plan=plan)

    def _on_delete(self, projects=None):
        picked = self._checked() if projects is None else list(projects)
        if not picked:
            return
        busy = [p for p in picked if p.in_use]
        if busy:                                    # lock held elsewhere: only continue on explicit confirmation
            names = ", ".join(p.name for p in busy[:5]) + (ELLIPSIS if len(busy) > 5 else "")
            owners = ", ".join(sorted({p.lock_owner for p in busy if p.lock_owner})) or "?"
            if not uikit.confirm(self, i18n.t("space.title"), i18n.t("space.force_text", n=len(busy), names=names) + "\n\n"
                                 + i18n.t("space.force_owners", owners=owners), ok=i18n.t("space.force_confirm"), danger=True):
                return
        size = scanner.gb(sum(p.total for p in picked))
        files, kept_size, risky = scanner.delete_impact(picked)
        kept = [i18n.t("space.confirm_delete_kept", files=files, size=scanner.gb(kept_size))] if files else []
        kept.append(i18n.t("space.delete_kept_exports"))
        warnings = []
        if risky:
            names = ", ".join(p.name for p in risky[:5]) + (ELLIPSIS if len(risky) > 5 else "")
            names = names if len(names) <= 60 else names[:59] + ELLIPSIS             # one line: the Kept column marks each project anyway
            warnings.append(("danger", i18n.t("space.confirm_delete_risk", names=names,
                                              size=scanner.gb(sum(p.internal_products for p in risky)))))
        title = i18n.t("space.ask_delete", n=len(picked))
        plan = {"title": title, "remove": [i18n.t("space.delete_remove", n=len(picked), size=size), i18n.t("space.delete_remove_more")],
                "kept": kept, "warnings": warnings, "go": i18n.t("space.go_permanent")}
        self._confirm_and_run("delete", picked, title, forced=busy, plan=plan)

    def _confirm_and_run(self, kind, picked, text, choices=None, forced=(), upgrade=0, plan=None):
        if kind == "lighten":
            # Metashape deletes its own files when it saves: no recycle bin possible.
            dialog = ConfirmDialog(self, text, False, "space.mode_recycle_none", choices, upgrade, plan)
        else:
            dialog = ConfirmDialog(self, text, all(actions.recycle_available(p.path) for p in picked), plan=plan)
        _exec(dialog)
        if dialog.mode is None:
            return
        items = list(dialog.items)
        if kind == "lighten":
            config.put(SECTION, "items", items)
        journal.write("Aero-Space: {} of {} project(s), mode {}{}".format(
            kind, len(picked), dialog.mode, ", " + "+".join(items) if kind == "lighten" else ""))
        self._batch = {"kind": kind, "mode": dialog.mode, "todo": list(picked), "total": len(picked), "done": 0,
                       "freed": 0, "bin": 0, "ok": 0, "failed": [], "sizes": {},
                       "items": items, "forced": {id(p) for p in forced}, "allow_upgrade": dialog.allow_upgrade,
                       "nothing": 0, "kept_clouds": 0, "unbinned": 0}
        self._start_batch(self._batch)

    # -- the batch ------------------------------------------------------------------

    def _start_batch(self, batch):
        """Lighten goes through Metashape (UI thread, cooperative); delete runs in a worker thread. One project at a time either way."""
        lighten = batch["kind"] == "lighten"
        here, hwnd = actions.current_document_path(), self._window_id()          # read on this thread, used in the worker
        action = i18n.t("space.act_lighten" if lighten else "space.act_delete")

        def work(progress):
            index = 0
            while batch["todo"]:
                if progress.is_cancelled():                           # Cancel: stop before the next project, never in the middle of one
                    break
                project = batch["todo"].pop(0)
                # Size concerned BEFORE the action (lightening updates the project): this is the one that is journalled.
                batch["sizes"][id(project)] = project.removable(batch["items"]) if lighten else project.total
                workers.advance(progress, index, batch["total"], i18n.t("space.working", action=action, i=index + 1, n=batch["total"],
                                                                        name=project.name))
                if lighten:
                    outcome = actions.lighten(project, batch["mode"], progress=lambda _text: workers.tick(progress),
                                              items=batch["items"], allow_upgrade=batch["allow_upgrade"])
                else:
                    confirmed = id(project) in batch["forced"]        # only the projects the user saw as open and confirmed
                    try:
                        outcome = actions.delete_project(project, batch["mode"], force=confirmed, here=here, hwnd=hwnd)
                    except Exception as exc:
                        outcome = {"error": repr(exc)}
                self.outcome.emit(project, outcome)
                index += 1
            return batch

        self.run(work, mode="cooperative" if lighten else "threaded", name="space-" + batch["kind"], retry=False,
                 on_done=lambda _result: self._finish_batch(batch), on_cancel=lambda: self._finish_batch(batch, stopped=True),
                 on_error=lambda exc: self._batch_failed(exc))

    def _on_outcome(self, project, outcome):
        if self._batch is not None:
            self._record(self._batch, project, outcome)

    def _record(self, batch, project, outcome):
        outcome = outcome or {}
        ok = bool(outcome.get("ok"))
        size = batch["sizes"].get(id(project), 0)
        note = outcome.get("error", "") or outcome.get("note", "") or (
            "safety copy: " + outcome["backup"] if outcome.get("backup") else "")
        if ok and outcome.get("nothing"):
            note = "nothing to remove: project not touched"
        if ok and outcome.get("kept_clouds"):
            note = (note + "; " if note else "") + "{} point cloud(s) kept (not built by Metashape)".format(outcome["kept_clouds"])
        actions.log_action(batch["kind"], batch["mode"], project, ok, note, size)
        batch["done"] += 1
        if ok:
            batch["ok"] += 1
            batch["freed"] += outcome.get("freed", 0)
            batch["bin"] += outcome.get("in_bin", 0)
            batch["nothing"] += 1 if outcome.get("nothing") else 0
            batch["kept_clouds"] += outcome.get("kept_clouds", 0)
            batch["unbinned"] += 1 if outcome.get("note") else 0
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
                for column, cell in enumerate(self._cells(project)):
                    text, key = (cell["text"], cell["sort"]) if isinstance(cell, dict) else cell
                    item = self.table.item(row, column)
                    item.setText(text)
                    item.setData(kit_table.SORT_ROLE, key if not isinstance(key, str) else key.lower())
                self._mark_row(row, project)
                self.table.setSortingEnabled(True)
                self.table.blockSignals(False)
                break

    def _finish_batch(self, batch, stopped=False):
        self._batch = None
        left = len(batch["todo"])
        self.status.setFullText(i18n.t("space.done", ok=batch["ok"], freed=scanner.gb(batch["freed"]),
                                   bin=scanner.gb(batch["bin"]), failed=len(batch["failed"])))
        extra = []
        if stopped and left:
            extra.append(i18n.t("space.stopped", n=left))
        if batch["nothing"]:
            extra.append(i18n.t("space.done_nothing", n=batch["nothing"]))
        if batch["kept_clouds"]:
            extra.append(i18n.t("space.done_kept", n=batch["kept_clouds"]))
        if batch["unbinned"]:
            extra.append(i18n.t("space.done_unbinned", n=batch["unbinned"]))
        if extra:
            self.status.setFullText(self.status.fullText() + " " + " ".join(extra))
        self._refresh_summary()
        if batch["failed"]:
            lines = [i18n.t("space.failed_line", name=name, error=error) for name, error in batch["failed"][:15]]
            self.show_banner("danger", lines[0] if len(lines) == 1 else i18n.t("space.failed_many", n=len(batch["failed"])),
                             i18n.t("space.failed_title"), details="\n".join(lines))

    def _batch_failed(self, exc):
        journal.write("Aero-Space: batch interrupted: {!r}".format(exc))
        self._batch = None
        self.status.setFullText(repr(exc))
        self._refresh_buttons()

    def batch_running(self):
        """A batch (Lighten / Delete) is running: the window must not be closed or deleted."""
        return self._batch is not None

    def can_close(self):
        return self._batch is None

    def reject(self):
        if self._batch is None:
            if self.is_busy():
                self.cancel_task()                           # a scan stops with its window
            super(SpaceDialog, self).reject()

    def closeEvent(self, event):
        if self._batch is not None:
            event.ignore()                                   # a batch is never cancelled by a click on the corner cross
            return
        if self.is_busy():
            self.cancel_task()                               # a scan stops with its window
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
    dialog.present()
    return dialog
