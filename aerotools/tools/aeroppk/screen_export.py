"""Last step of the Aero-PPK wizard: what to do with the result. On the left the files (report, tables), on the right the photo positions of
the Metashape chunk (replaced after a backup, with an Undo). The primary button does everything that is ticked, files first, then the
positions. Messages end in the window's banner; each row also says what was done."""

import os

import numpy as np

from ... import config, journal, msapi, uikit
from ...qt import QtWidgets
from ...uikit import tokens
from . import backup, frames, outputs, reportdata, report, rtklib, state as S, summary
from .stations import catalog, rgp
from .ui_common import Screen, glyph, tr, value

# The EXIF rewrite ("aeroppk.out.exif*" in lang/en.py) was taken off this screen on purpose: it cannot be undone and the positions of
# Metashape are the safe way to use the result. Its texts stay in the catalog for the day it comes back.
SECTION = "aeroppk"
KIND_KEYS = (("pdf", "aeroppk.out.pdf", "aeroppk.out.pdf_tip"), ("csv", "aeroppk.out.csv", "aeroppk.out.csv_tip"),
             ("kml", "aeroppk.out.kml", "aeroppk.out.kml_tip"), ("txt", "aeroppk.out.txt", "aeroppk.out.txt_tip"))


def counted_stations(state):
    """The bases that count in the results (the ones the user excluded left out)."""
    stations = []
    for result in state.results:
        left = set(getattr(result, "excluded", None) or ())
        stations.extend(run.base.spec.station for run in result.runs if run.base.name not in left)
    return stations


def base_frame_text(state):
    """The frame of the base coordinates in words, for the Simple mode question."""
    return catalog.frames_text(counted_stations(state), short=True) or tr("aeroppk.own.frame_short")


class ExportRow(QtWidgets.QWidget):
    """One thing to do: a check, its words and an "i", and under it (once it ran) a mark and what was done."""

    def __init__(self, text, tip="", parent=None):
        super(ExportRow, self).__init__(parent)
        box = uikit.vbox(spacing=tokens.XS, parent=self)
        top = uikit.hbox(spacing=tokens.S)
        self.toggle = uikit.Toggle(text, False)
        top.addWidget(self.toggle, 1)
        if tip:
            top.addWidget(uikit.InfoButton(tip))
        box.addLayout(top)
        self.result = QtWidgets.QWidget()
        line = uikit.hbox((tokens.TOGGLE_W + tokens.S, 0, 0, 0), tokens.S, parent=self.result)
        self.mark = glyph("ok")
        line.addWidget(self.mark)
        self.text = uikit.ElidedLabel("", "small", "muted", fit=True)
        line.addWidget(self.text, 1)
        box.addWidget(self.result)
        self.result.setVisible(False)

    def show_result(self, kind, text, tip=""):
        self.mark.set_kind(kind)
        self.text.setFullText(text)
        self.text.setToolTip(tip)
        self.result.setVisible(bool(text))

    def clear_result(self):
        self.result.setVisible(False)


class ExportScreen(Screen):
    step = S.EXPORT
    name_key = "aeroppk.step_export"

    def __init__(self, wizard):
        super(ExportScreen, self).__init__(wizard)
        self.state.outputs = outputs.OutputOptions.from_dict(config.get(SECTION, "outputs", {}))
        self.backup_path = ""
        self.exported = []
        self.export_state = {}                 # file kind -> path written, or None when it could not be written (empty: not written yet)
        self.replace_result = ""               # what the last replacement said (the row shows it)
        self._auto_off = False                 # the replace row was unticked by the screen (no chunk), not by the user
        self._signature = None                 # the results the marks of the rows are about
        self.content.addWidget(uikit.label(tr("aeroppk.export.lead"), "lead", wrap=False))
        columns = uikit.hbox(spacing=tokens.L)
        left = uikit.Card(tr("aeroppk.export.files"))
        self.rows = {}
        for kind, text_key, tip_key in KIND_KEYS:
            row = ExportRow(tr(text_key), tr(tip_key))
            row.toggle.setChecked(getattr(self.state.outputs, kind))
            row.toggle.toggled.connect(lambda on, k=kind: self._toggled(k, on))
            left.add(row)
            self.rows[kind] = row
        left.add(uikit.Divider())
        folder = uikit.hbox(spacing=tokens.S)
        folder.addWidget(uikit.label(tr("aeroppk.out.s_folder"), "small", "muted", wrap=False))
        self.folder_label = value(mode="middle", kind="strong")        # where the files go: the answer the user looks for
        folder.addWidget(self.folder_label, 1)
        self.open_button = uikit.Button(tr("aeroppk.export.open_folder"), "secondary", handler=self.open_folder)
        self.open_button.setVisible(False)
        folder.addWidget(self.open_button)
        holder = QtWidgets.QWidget()
        holder.setLayout(folder)
        left.add(holder)
        left.body.addStretch(1)
        columns.addWidget(left, 1)
        right = uikit.Card(tr("aeroppk.export.metashape"))
        self.replace_row = ExportRow(tr("aeroppk.export.replace_row", chunk="-"))
        self.replace_row.toggle.setChecked(True)
        self.replace_row.toggle.toggled.connect(lambda _on: self.wizard.refresh_buttons())
        right.add(self.replace_row)
        self.replace_note = uikit.label(tr("aeroppk.export.replace_note"), "small", "muted", wrap=False)      # (one line: a wrapped text makes the whole page a height for width layout)
        right.add(self.replace_note)
        undo = uikit.hbox(spacing=tokens.S)
        self.undo_button = uikit.Button(tr("aeroppk.res.restore"), "secondary", handler=self._undo)
        self.undo_button.setToolTip(tr("aeroppk.export.undo_tip"))
        undo.addWidget(self.undo_button)
        undo.addStretch(1)
        holder = QtWidgets.QWidget()
        holder.setLayout(undo)
        right.add(holder)
        right.body.addStretch(1)
        columns.addWidget(right, 1)
        self.content.addLayout(columns)
        self.content.addStretch(1)

    # ------------------------------------------------------------------ the footer of the window

    def say(self, kind, text, title=None, details=None):
        """A message of this step: the banner of the window (the one place messages end up)."""
        self.wizard.show_banner(kind, text, title=title, details=details)

    def next_text(self):
        return tr("aeroppk.res.export")

    def can_replace(self):
        return self.wizard.bound_chunk() is not None

    def wanted(self):
        """What is ticked and possible: (file kinds, replace the positions)."""
        kinds = [k for k in self.state.outputs.kinds()] if self.state.results else []
        return kinds, bool(self.replace_row.toggle.isChecked() and self.can_replace() and self.state.results)

    def next_enabled(self):
        kinds, replace = self.wanted()
        return bool(kinds or replace) and not self.wizard.busy()

    def hint(self):
        kinds, replace = self.wanted()
        return "" if (kinds or replace) else tr("aeroppk.export.nothing")

    def enter(self):
        signature = tuple(id(r) for r in self.state.results)
        if signature != self._signature:                               # other results than the ones the rows speak of: their marks go
            self._signature = signature
            self.exported, self.export_state, self.replace_result = [], {}, ""
            for row in list(self.rows.values()) + [self.replace_row]:
                row.clear_result()
            self.open_button.setVisible(False)
        name = self.wizard.project_name()
        for kind, row in self.rows.items():
            row.toggle.setText(outputs.base_file_name({"title": name}, kind))
        self.folder_label.setFullText(self.folder())
        self._refresh_chunk()
        self.wizard.refresh_buttons()

    def _refresh_chunk(self):
        """The Metashape card follows the chunk the window is bound to: its name, whether it can be written to, whether Undo has a backup."""
        chk = self.wizard.bound_chunk()
        has = chk is not None
        self.replace_row.toggle.setText(tr("aeroppk.export.replace_row", chunk=self.wizard.bound_name() or "-"))
        self.replace_row.toggle.setEnabled(has)
        if not has:
            self.replace_row.toggle.setChecked(False)                  # nothing is ticked that is not possible
            self._auto_off = True
        elif self._auto_off:                                           # the chunk is back: the row is ticked again, as the user left it
            self.replace_row.toggle.setChecked(True)
            self._auto_off = False
        self.replace_row.toggle.setToolTip("" if has else tr("aeroppk.export.no_chunk"))
        if not has:
            self.replace_row.show_result("warn", tr("aeroppk.export.no_chunk"))
        self.undo_button.setEnabled(has and self._has_backup(chk))

    def _has_backup(self, chk):
        project = msapi.document_path()
        found = backup.find(backup.folder_for(project, rgp.cache_dir()), project, msapi.chunk_label(chk), chunk_key=msapi.chunk_key(chk),
                            legacy=self._label_is_unique(chk))
        return bool(found)

    def _toggled(self, kind, on):
        setattr(self.state.outputs, kind, bool(on))
        config.put(SECTION, "outputs", self.state.outputs.to_dict())
        self.wizard.refresh_buttons()

    def folder(self):
        flight = self.state.flights[0]["files"].folder if self.state.flights else ""
        return outputs.output_folder(msapi.document_path(), flight)

    def open_folder(self):
        self.wizard.open_folder(self.folder())

    # ------------------------------------------------------------------ doing it

    def run(self):
        """Everything that is ticked: the files, then the positions. Stops at the first thing that fails or that the user declines."""
        kinds, replace = self.wanted()
        if not (kinds or replace):
            return
        if not self._sure():
            return
        for row in list(self.rows.values()) + [self.replace_row]:
            row.clear_result()
        if kinds:
            done = self.export()
            if done is not True:
                return
        if replace:
            self.apply()
            self._show_replace()
        self._refresh_chunk()

    def _sure(self):
        """A result that is not good asks once before anything is written (not again when the Result screen already asked)."""
        results = self.state.results
        if summary.verdict(results) == summary.GOOD or getattr(self.wizard, "export_asked", False):
            return True
        totals = summary.figures(results)
        counts = summary.level_counts(results)
        pct = 100.0 * counts[0] / max(1, totals["photos"])
        if not self.wizard.confirm(tr("aeroppk.export.weak_ask", pct=int(round(pct))), ok=tr("aeroppk.result.export_anyway")):
            return False
        self.wizard.export_asked = True
        return True

    def _show_files(self):
        for kind, row in self.rows.items():
            if kind not in self.export_state:
                row.clear_result()
            elif self.export_state[kind]:
                path = self.export_state[kind]
                row.show_result("ok", tr("aeroppk.export.written", name=os.path.basename(path)), path)
            else:
                row.show_result("error", tr("aeroppk.export.not_written"))
        self.open_button.setVisible(bool(self.exported))

    def _show_replace(self):
        if self.replace_result:
            self.replace_row.show_result("ok", self.replace_result)
        else:
            self.replace_row.clear_result()

    def _undo(self):
        self.restore()
        self.replace_row.clear_result()
        self._refresh_chunk()

    # ------------------------------------------------------------------ use the result

    def _items(self):
        items, unfit = [], 0
        for result in self.state.results:
            comb, cam = result.combined, result.cam
            for i, event in enumerate(result.events):
                if comb.q[i] == 0 or not np.isfinite(cam["lat"][i]) or not event.photo:
                    unfit += 1
                    continue
                items.append({"path": event.photo, "lat": float(cam["lat"][i]), "lon": float(cam["lon"][i]), "h": float(cam["h"][i]),
                              "sd_h": max(0.01, float(np.hypot(comb.sdn[i], comb.sde[i]) / 1.4142)), "sd_v": max(0.015, float(comb.sdu[i]))})
        return items, unfit

    def _bound(self):
        """The chunk these results were made for (the one the window is bound to), found again by its key. Says so when it is gone."""
        chk = self.wizard.bound_chunk()
        if chk is None:
            self.say("warning", tr("aeroppk.chunk.gone"))
        return chk

    def _refuse(self, frame, name):
        """Replace must not write: the banner says that nothing was written, why, and what to do (the rules are `frames.source_frame`)."""
        key = "aeroppk.apply_frame_" + frame.problem
        fields = dict(chunk=name, **frame.fields)
        why = tr(key + "_why", **fields)
        self.say("warning", tr(key, **fields), title=tr("aeroppk.apply_refused"), details=None if why == key + "_why" else why)

    def apply(self):
        """Replace the photo positions of the bound chunk by the PPK ones (after a backup). Returns the number of positions written, or None
        when nothing was written."""
        self.replace_result = ""
        chk = self._bound()
        if chk is None:
            return None
        items, unfit = self._items()
        entries = msapi.camera_reference_entries(chk)
        inside = set(msapi.camera_path_key(e["path"]) for e in entries)
        total = len(items)
        items = [i for i in items if msapi.camera_path_key(i["path"]) in inside]
        outside = total - len(items)
        name = msapi.chunk_label(chk)
        if not items:
            if outside:
                self.say("warning", tr("aeroppk.apply_none_in_chunk", chunk=name))
            return None
        frame = self._frame(chk)
        advanced = False
        if frame.problem == "simple_only":      # a chunk not in WGS 84: only the Advanced mode writes there, and the user chooses it
            ask = tr("aeroppk.apply_advanced_ask", chunk=name, **frame.fields)
            if not self.wizard.confirm(ask, ok=tr("aeroppk.apply_advanced_ok")):
                return None
            advanced = True
            frame = self._frame(chk, advanced)
        if not frame.ok:
            self._refuse(frame, name)
            return None
        if advanced:
            question = tr("aeroppk.apply_ask", n=len(items), chunk=name) + "\n\n" + tr("aeroppk.apply_frame_note", frame=frame.label)
            if not frame.checked:
                question += "\n\n" + tr("aeroppk.apply_frame_unchecked", crs=msapi.chunk_crs_name(chk))
        else:
            question = tr("aeroppk.apply_ask", n=len(items), chunk=name) + "\n\n" + tr("aeroppk.apply_simple_note", frame=base_frame_text(self.state))
        if not self.wizard.confirm(question, ok=tr("aeroppk.export.replace")):
            return None
        chk = self._bound()                     # the question was open for a while
        if chk is None:
            return None
        project = msapi.document_path()
        folder = backup.folder_for(project, rgp.cache_dir())
        path = backup.new_path(folder, project, msapi.chunk_label(chk), chunk_key=msapi.chunk_key(chk))
        try:
            backup.save(path, msapi.camera_reference_entries(chk), msapi.chunk_crs_name(chk), "before Aero-PPK", chunk=name,
                        chunk_key=msapi.chunk_key(chk))
        except OSError as exc:
            self.say("danger", tr("aeroppk.save_failed", error=exc))
            return None
        frame = self._frame(chk, advanced)     # the chunk may have changed while the question was open
        if not frame.ok:
            self._refuse(frame, name)
            return None
        outcome = frames.write_positions(chk, items, frame)
        msapi.refresh()
        journal.write("Aero-PPK: %d positions replaced in a chunk" % outcome["done"])
        text = tr("aeroppk.apply_done", done=outcome["done"], chunk=name)
        kind = "success"
        if outcome["missing"] or outcome["failed"]:
            text = tr("aeroppk.apply_partial", done=outcome["done"], missing=len(outcome["missing"]), failed=len(outcome["failed"]), chunk=name)
            kind = "warning"
        if unfit:
            text += "\n" + tr("aeroppk.apply_unfit", n=unfit)
        if outside:
            text += "\n" + tr("aeroppk.apply_outside", n=outside)
        if outcome["failed"]:                   # Metashape refused the conversion (a geoid grid it does not have, or a place outside it)
            text += "\n" + tr("aeroppk.apply_failed_why", crs=msapi.chunk_crs_name(chk))
        if advanced and outcome["done"]:
            text += "\n" + tr("aeroppk.apply_advanced_done", crs=msapi.chunk_crs_name(chk))
        self.say(kind, text, details=tr("aeroppk.apply_backup", path=path))
        self.replace_result = tr("aeroppk.export.replaced", n=outcome["done"]) if outcome["done"] else ""
        return outcome["done"]

    def _frame(self, chk, advanced=False):
        """The frame the positions are written in (`frames.ApplyFrame`), or the reason nothing is written. Simple mode writes into a chunk
        in WGS 84 only, with the coordinates as they are; Advanced declares the frame of the bases and lets Metashape convert."""
        if not advanced:
            return frames.simple_frame(counted_stations(self.state), msapi.chunk_crs_name(chk))
        return frames.source_frame(counted_stations(self.state), msapi.chunk_crs_name(chk), getattr(self.state, "own_frame", ""))

    def restore(self):
        chk = self._bound()
        if chk is None:
            return
        project = msapi.document_path()
        found = backup.find(backup.folder_for(project, rgp.cache_dir()), project, msapi.chunk_label(chk), chunk_key=msapi.chunk_key(chk),
                            legacy=self._label_is_unique(chk))
        if not found:
            self.say("info", tr("aeroppk.restore_none"))
            return
        name = msapi.chunk_label(chk)
        if not self.wizard.confirm(tr("aeroppk.restore_ask", chunk=name), ok=tr("aeroppk.res.restore")):
            return
        chk = self._bound()
        if chk is None:
            return
        try:
            data = backup.load(found[0])
        except (OSError, ValueError) as exc:
            self.say("danger", tr("aeroppk.save_failed", error=exc))
            return
        count = msapi.restore_camera_references(chk, data["entries"])
        msapi.refresh()
        self.say("success", tr("aeroppk.restore_done", n=count, chunk=name))

    @staticmethod
    def _label_is_unique(chk):
        """Backups made before they carried the chunk key are known by the label only: they are trusted when no other chunk has it."""
        label = msapi.chunk_label(chk)
        return sum(1 for c in msapi.chunks() if msapi.chunk_label(c) == label) <= 1

    # ------------------------------------------------------------------ report and export

    def report_data(self):
        state = self.state
        project = self.wizard.project_name()
        needs = {f["files"].folder: f["needs"] for f in state.flights}
        from ... import __version__
        if state.mode == S.MODE_OWN:
            source = {"name": tr("aeroppk.own.source"), "licence": "", "url": "", "kind": "own"}
            frame = tr("aeroppk.own.frame")
        else:
            stations = []
            for result in state.results:
                for run in result.runs:
                    if all(run.base.spec.station.code != s.code for s in stations):
                        stations.append(run.base.spec.station)
            source = catalog.source_of(stations)
            frame = catalog.frames_text(stations) or rgp.FRAME_LABEL
        data = reportdata.build(
            project, state.results, rtklib.Options(), source, frame, __version__, state.choice().name, needs,
        )
        return data

    def export(self):
        """Write the ticked files. True when they were written, False when they could not be, None when nothing was done or the user
        gave up."""
        if not self.state.results or not self.state.outputs.kinds():
            return None
        folder = self.wizard.output_folder()
        include_table = True
        if "pdf" in self.state.outputs.kinds():
            photos = sum(len(result.events) for result in self.state.results)
            include_table = self.wizard.ask_report_options(photos)
            if include_table is None:                       # the user gave up
                return None
        try:
            data = self.report_data()
            data["table"] = bool(include_table)
            written = outputs.export(data, self.state.outputs, folder, report.build_pdf, report.write_csv)
        except Exception as exc:
            self.say("danger", tr("aeroppk.save_failed", error=exc))
            self.export_state = {kind: None for kind in self.state.outputs.kinds()}
            self._show_files()
            return False
        self.exported = written
        self.export_state = {kind: next((w for w in written if w.lower().endswith(outputs.EXTENSIONS[kind])), None)
                             for kind in self.state.outputs.kinds()}
        self._show_files()
        self.say("success", tr("aeroppk.res.exported", n=len(written)), details="\n".join(written))
        return True
