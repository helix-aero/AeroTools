"""Step 2 of the Aero-PPK wizard: the files of each flight, and which flights to compute. One row per flight with a tick (a flight that is
left out stays in the list, greyed, and is never computed, exported or written), then the three files of the chosen flight and the notes
about the photos."""

import os

from ... import msapi, uikit
from ...qt import QtCore, QtWidgets
from ...uikit import tokens
from . import flights as flight_tools, gnsstime, state as S
from .ui_common import Screen, card, clear_layout, fmt_duration, fmt_time, glyph, one_line, tr

# (need key, label of the file, attribute of the flight's files, title of the file dialog, file filter)
KINDS = (("events", "aeroppk.rover.file_events", "events_file", "aeroppk.rover.pick_events", "MRK (*.mrk *.MRK);;*.*"),
         ("rover_obs", "aeroppk.rover.file_obs", "obs", "aeroppk.rover.pick_obs", "RINEX (*.obs *.o *.rnx *.??o *.crx);;*.*"),
         ("rover_nav", "aeroppk.rover.file_nav", "nav", "aeroppk.rover.pick_nav", "RINEX (*.nav *.n *.rnx *.??n *.??p);;*.*"))


class FilesScreen(Screen):
    step = S.FILES
    name_key = "aeroppk.step_files"

    def __init__(self, wizard):
        super(FilesScreen, self).__init__(wizard)
        self.selected = 0                # index of the chosen flight in `state.all_flights`
        self._chunk_paths = None         # photo paths of the bound chunk (read again each time the step is shown)
        self._rows = []                  # table row -> index of the flight
        self._silent = False
        self.content.setSpacing(uikit.px(tokens.M))
        self.content.addWidget(uikit.label(tr("aeroppk.files.question"), "strong", wrap=False))
        self.table = uikit.DataTable([uikit.Column("use", "", kind="check"),
                                      uikit.Column("name", tr("aeroppk.rover.c_flight"), stretch=True),
                                      uikit.Column("photos", tr("aeroppk.rover.f_photos"), align="right", width=90),
                                      uikit.Column("start", tr("aeroppk.rover.f_start"), width=170),
                                      uikit.Column("duration", tr("aeroppk.rover.f_duration"), width=90),
                                      uikit.Column("mark", "", kind="status"),
                                      uikit.Column("status", tr("aeroppk.files.c_status"))],
                                     sortable=False, empty_title=tr("aeroppk.rover.empty"), empty_text=tr("aeroppk.rover.empty_hint"))
        one_line(self.table)
        self.table.set_min_rows(3)
        self.table.row_actions = True
        self.table.selectionChanged.connect(self._on_pick)
        self.table.checkedChanged.connect(self._on_checked)
        self.table.rowContextMenu.connect(self._row_menu)
        self.content.addWidget(self.table, 1)
        self.count = uikit.ElidedLabel("", "strong", fit=True)           # what will be computed: updates with every tick
        self.content.addWidget(self.count)
        self.notes_box = uikit.vbox(spacing=tokens.XS)
        self.content.addLayout(self.notes_box)
        detail, box = card(tokens.S)
        self.flight_name = uikit.ElidedLabel("", "strong", fit=True)
        box.addWidget(self.flight_name)
        self.files_box = uikit.vbox(spacing=tokens.XS)
        box.addLayout(self.files_box)
        self.content.addWidget(detail)

    # -- the bound chunk --------------------------------------------------------------------------

    def chunk_paths(self):
        """Comparable paths of the photos of the bound chunk (None without chunk)."""
        if self._chunk_paths is None:
            chk = self.wizard.bound_chunk()
            if chk is None:
                return None
            self._chunk_paths = set(msapi.camera_path_key(e["path"]) for e in msapi.camera_reference_entries(chk))
        return self._chunk_paths

    def outside_count(self):
        """Photos of the flights that are not in the bound chunk (a folder may hold more than the chunk): they are listed, never changed."""
        inside = self.chunk_paths()
        if inside is None:
            return 0
        return sum(1 for f in self.state.all_flights for p in f["files"].photos if msapi.camera_path_key(p) not in inside)

    def uncovered_count(self):
        """Photos of the bound chunk that are in none of the listed flights: nothing can be computed for them."""
        inside = self.chunk_paths()
        if not inside or not self.state.all_flights:
            return 0
        covered = set(msapi.camera_path_key(p) for f in self.state.all_flights for p in f["files"].photos)
        return len(inside - covered)

    def excluded_count(self):
        """Photos of the bound chunk that belong to a flight the user left out: they keep their own position."""
        inside = self.chunk_paths()
        if not inside:
            return 0
        left_out = set(msapi.camera_path_key(p) for f in self.state.all_flights if self.state.is_skipped(f) for p in f["files"].photos)
        return len(inside & left_out)

    # -- flights ----------------------------------------------------------------------------------

    def enter(self):
        self._chunk_paths = None
        if not 0 <= self.selected < len(self.state.all_flights):
            self.selected = 0
        self.refresh()

    def refresh_skin(self):
        """The skin changed: the marks of the list are drawn again."""
        self.refresh()

    def set_included(self, folder, on):
        """Put a flight in or out of the calculation (the tick and the menu come here). True when something changed."""
        if not self.state.skip_flight(folder, not on):
            return False
        self.wizard.rover_changed()
        self.refresh()
        return True

    def _on_checked(self):
        """A tick was clicked. The state follows at once; the list is drawn again after the click is over (it replaces the items)."""
        if self._silent:
            return
        ticked = set(self.table.checked_rows())
        changed = False
        for index, flight in enumerate(self.state.all_flights):
            folder = flight["files"].folder
            if (index in ticked) == (not self.state.is_skipped(flight)):
                continue
            if self.state.skip_flight(folder, index not in ticked):
                changed = True
        if changed:
            self.wizard.rover_changed()
        QtCore.QTimer.singleShot(0, self.refresh)                        # puts back the tick of the last flight when it was refused

    def _row_menu(self, row, global_pos):
        flights = self.state.all_flights
        if not 0 <= row < len(self._rows) or self._rows[row] < 0:
            return
        flight = flights[self._rows[row]]
        left_out = self.state.is_skipped(flight)
        menu = QtWidgets.QMenu(self)
        action = menu.addAction(tr("aeroppk.files.include" if left_out else "aeroppk.files.exclude"))
        action.setEnabled(left_out or len(self.state.flights) > 1)       # at least one flight stays
        action.triggered.connect(lambda *_a: self.set_included(flight["files"].folder, left_out))
        menu.exec_(global_pos)

    def select(self, index):
        self.selected = index
        self.refresh()

    def _on_pick(self):
        if self._silent:
            return
        rows = self.table.selected_rows()
        if rows and 0 <= rows[0] < len(self._rows):
            index = self._rows[rows[0]]
            if index >= 0 and index != self.selected:
                self.select(index)

    def _flight(self):
        flights = self.state.all_flights
        return flights[self.selected] if 0 <= self.selected < len(flights) else None

    # -- drawing ----------------------------------------------------------------------------------

    def status_of(self, flight):
        """(icon, text) of a flight: ready, or what is missing."""
        if self.state.is_skipped(flight):
            return "skipped", tr("aeroppk.files.s_excluded")
        missing = [n for n in flight["needs"] if not n.present and n.level in ("blocking", "advised")]
        if not missing:
            return "ok", tr("aeroppk.files.s_ready")
        names = ", ".join(tr("aeroppk.files.need_" + n.key) for n in missing)
        return ("error" if any(n.level == "blocking" for n in missing) else "warn"), tr("aeroppk.files.s_missing", what=names)

    def refresh(self):
        flights = self.state.all_flights
        rows, included = [], []
        self._rows = []
        for index, flight in enumerate(flights):
            info = flight_tools.describe(flight)
            left_out = self.state.is_skipped(flight)
            start = gnsstime.gps_to_utc(info["start"]) if info["start"] else None
            icon, text = self.status_of(flight)
            tone = "faint" if left_out else None
            rows.append([uikit.cell("", None), uikit.cell(info["name"], tone=tone), uikit.cell(str(info["photos"]), tone=tone),
                         uikit.cell(fmt_time(start) if start else "-", tone=tone),
                         uikit.cell(fmt_duration(info["duration"]) if info["duration"] is not None else "-", tone=tone),
                         uikit.cell(icon, icon=icon), uikit.cell(text, tone="faint" if left_out else ("warning" if icon in ("warn", "error") else None))])
            if not left_out:
                included.append(index)
            self._rows.append(index)
        self._silent = True
        try:
            chk = self.wizard.bound_chunk()
            empty_chunk = chk is not None and not msapi.chunk_camera_count(chk)
            self.table.set_empty(tr("aeroppk.rover.empty_chunk" if empty_chunk else "aeroppk.rover.empty"),
                                 tr("aeroppk.rover.empty_chunk_hint" if empty_chunk else "aeroppk.rover.empty_hint"))
            self.table.set_rows(rows, checked=included)
            if 0 <= self.selected < len(flights):
                self.table.select_row(self.selected)
        finally:
            self._silent = False
        self._fill_count()
        self._fill_notes()
        self._fill_files()
        self.wizard.refresh_buttons()

    def count_text(self):
        """"225 photos in 1 of 2 flights will be computed." (or without the "of" when every flight counts)."""
        total, used = len(self.state.all_flights), self.state.flights
        photos = sum(len(f["files"].photos) for f in used)
        if not total:
            return ""
        if len(used) == total:
            return tr("aeroppk.files.count_all", photos=photos, flights=total)
        return tr("aeroppk.files.count", photos=photos, used=len(used), flights=total)

    def _fill_count(self):
        self.count.setFullText(self.count_text())

    def _fill_notes(self):
        notes = []
        outside = self.outside_count()
        if outside:
            notes.append(tr("aeroppk.rover.outside", n=outside))
        uncovered = self.uncovered_count()
        if uncovered:
            notes.append(tr("aeroppk.rover.uncovered", n=uncovered))
        if self.state.unread:                                            # the kit draws a tick in every row: these folders are a note, not rows
            names = [os.path.basename(folder.rstrip("/\\")) or folder for folder, _n in self.state.unread]
            notes.append(tr("aeroppk.files.unread_note", n=len(names), names=", ".join(names[:2]) + (", ..." if len(names) > 2 else "")))
        excluded = self.excluded_count()
        if excluded:
            notes.append(tr("aeroppk.files.excluded_note", n=excluded))
        for flight in self.state.flights:
            events, photos = len(flight["events"]), len(flight["files"].photos)
            no_time = (getattr(flight["files"], "extra", None) or {}).get("no_time") or []
            if no_time:
                notes.append(tr("aeroppk.rover.no_time", n=len(no_time), names=", ".join(n or "#%d" % i for i, n in no_time[:2])
                                + (", ..." if len(no_time) > 2 else "")))
            elif events and events != photos:
                notes.append(tr("aeroppk.rover.mismatch", events=events, photos=photos))
        clear_layout(self.notes_box)
        for text in notes:
            line = uikit.hbox(spacing=tokens.S)
            line.addWidget(glyph("warn"), 0, QtCore.Qt.AlignTop)
            line.addWidget(uikit.ElidedLabel(text, "small", "warning"), 1)
            self.notes_box.addLayout(line)

    def _fill_files(self):
        clear_layout(self.files_box)
        flight = self._flight()
        self.flight_name.setFullText(tr("aeroppk.files.of", name=flight["files"].name) if flight is not None else "")
        needs = {n.key: n for n in flight["needs"]} if flight is not None else {}
        for key, text_key, attr, pick_key, filt in KINDS:
            need = needs.get(key)
            present = need is not None and need.present
            row = uikit.hbox(spacing=tokens.S)
            row.addWidget(glyph("ok" if present else ("pending" if flight is None else "warn")))
            name = uikit.label(tr(text_key), "body", "muted", wrap=False)
            name.setFixedWidth(uikit.px(tokens.LABEL_W))
            row.addWidget(name)
            if present:
                row.addWidget(uikit.ElidedLabel(os.path.basename(need.path or need.detail or ""), "body"), 1)
            elif flight is None:
                row.addStretch(1)
            else:
                row.addWidget(uikit.label(tr("aeroppk.rover.missing"), "body", "warning", wrap=False))
                row.addWidget(uikit.Button(tr("ui.browse"), "secondary", handler=lambda a=attr, t=pick_key, f=filt: self.browse(a, t, f)))
                row.addStretch(1)
                row.addWidget(uikit.InfoButton(tr("aeroppk.why." + key)))
            holder = QtWidgets.QWidget()
            holder.setLayout(row)
            self.files_box.addWidget(holder)

    def browse(self, attr, title_key, filt):
        flight = self._flight()
        if flight is None:
            return
        start = os.path.dirname(getattr(flight["files"], attr, None) or flight["files"].folder) or flight["files"].folder
        path = self.wizard.pick_file(tr(title_key), filt, start)
        if not path:
            return
        files = flight["files"]
        setattr(files, attr, path)
        self.state.all_flights[self.selected] = flight_tools.refresh(flight["reader"], files)   # same folder: the flight keeps its tick
        self.wizard.rover_changed()
        self.refresh()

    # -- footer -----------------------------------------------------------------------------------

    def hint(self):
        if not self.state.flights:
            return tr("aeroppk.files.hint_none")
        if self.state.blocking_gaps():
            return tr("aeroppk.rover.hint_files")
        return ""
