"""Aero-L3 UI: the four screens of the wizard (photos, checks, project, create), built from the UI kit (`aerotools/uikit`).

Qt widgets only; every rule is in `state.py` and `checks.py`. A screen is a few controls and the result. What needs more words sits in the
bubble of a small "i"."""

import os
import re

from ... import i18n, uikit
from ...qt import QtCore, QtWidgets
from ...uikit import tokens
from . import backend as backend_module
from . import checks, scan
from . import state as S

Qt = QtCore.Qt


def tr(key, **fields):
    return i18n.t(key, **fields)


def plural(key, count, **fields):
    """Text of `key.one` when `count` is 1 and of `key.other` otherwise; `count` is the field `n` unless the call gives one."""
    return i18n.tn(key + ".one", key + ".other", count, **fields)


def clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())


def fmt_count(n):
    return "{:,}".format(int(n)).replace(",", " ")


def group_digits(text):
    """Whole numbers of four digits or more written with a space between thousands ('12345' gives '12 345'); decimals and codes stay as they are."""
    return re.sub(r"(?<![\w.:,])(\d{4,})(?![\w.,])", lambda m: fmt_count(m.group(1)), text)


def fmt_time(moment):
    return moment.strftime("%Y-%m-%d %H:%M") if moment is not None else "-"


def plain_scroll(page):
    """A list that scrolls inside itself (vertical only, no frame, no own background) around `page`."""
    area = QtWidgets.QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QtWidgets.QFrame.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    area.setWidget(page)
    area.setFocusPolicy(Qt.NoFocus)
    area.viewport().setAutoFillBackground(False)
    page.setAutoFillBackground(False)
    area.setMinimumHeight(uikit.px(120))
    return area


class EnterMoves(QtCore.QObject):
    """Enter in a box of the form takes the typed value and goes to the next box (it does not press Create project)."""

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.KeyPress and event.key() in (Qt.Key_Return, Qt.Key_Enter):
            obj.focusNextChild()
            return True
        return False


class Screen(uikit.StepScreen):
    """One page of the wizard. The wizard asks it for the state of the footer (`next_enabled`, `next_text`, `show_next`, `show_back`)."""

    step = 0
    name_key = ""

    def __init__(self, wizard):
        super(Screen, self).__init__(wizard)
        self.wizard = wizard
        self.state = wizard.state

    def next_enabled(self):
        return self.state.can_next(self.step)

    def show_next(self):
        return True

    def busy(self):
        return self.wizard.busy()

    def extra_focus(self):
        """Controls inside a scrolling list that the Tab key must still reach, in order."""
        return []


# --------------------------------------------------------------------------------------------------
# 1. Photos
# --------------------------------------------------------------------------------------------------

class PhotosScreen(Screen):
    step = S.PHOTOS
    name_key = "l3.ui.step_photos"

    def __init__(self, wizard):
        super(PhotosScreen, self).__init__(wizard)
        layout = self.content
        layout.setSpacing(tokens.L)
        row = uikit.hbox(spacing=tokens.S)
        self.path = uikit.TextField(placeholder=tr("l3.ui.folder_placeholder"))
        self.path.returnPressed.connect(self.scan_typed)
        self.browse = uikit.button("ui.browse", self.choose)
        self.scan_button = uikit.button("l3.ui.scan", self.scan_typed)
        row.addWidget(self.path, 1)
        row.addWidget(self.browse)
        row.addWidget(self.scan_button)
        layout.addLayout(row)

        line = uikit.hbox(spacing=tokens.L)
        self.status = uikit.label("", "strong", wrap=False)
        self.skipped = uikit.label("", "small", "muted", wrap=False)
        line.addWidget(self.status)
        line.addStretch(1)
        line.addWidget(self.skipped)
        layout.addLayout(line)

        self.empty = uikit.EmptyState(tr("l3.ui.photos_empty"))
        self.empty.set_card(True)
        layout.addWidget(self.empty, 1)
        columns = [uikit.Column("on", "", width=44), uikit.Column("flight", tr("l3.ui.col_flight"), stretch=True),
                   uikit.Column("shots", tr("l3.ui.col_shots"), "right", kind="number"),
                   uikit.Column("fix", tr("l3.ui.col_fix"), "right", kind="number"), uikit.Column("date", tr("l3.ui.col_date"))]
        self.scroll = uikit.DataTable(columns, selection="extended", sortable=False)       # a selected row is a ticked flight
        self.table = self.scroll.table
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.MultiSelection)
        self.table.setAccessibleName(tr("l3.ui.flights_info"))
        self.scroll.selectionChanged.connect(self._selection_changed)
        layout.addWidget(self.scroll, 1)

        foot = uikit.hbox(spacing=tokens.S)
        self.summary = uikit.label("", "strong", wrap=False)
        self.summary_info = uikit.InfoButton(tr("l3.ui.flights_info"))
        self.pick_all = uikit.Button(tr("l3.ui.pick_all"), "quiet", handler=lambda: self._pick_all(True))
        self.pick_none = uikit.Button(tr("l3.ui.pick_none"), "quiet", handler=lambda: self._pick_all(False))
        foot.addWidget(self.summary)
        foot.addWidget(self.summary_info)
        foot.addStretch(1)
        foot.addWidget(self.pick_all)
        foot.addWidget(self.pick_none)
        self.foot = QtWidgets.QWidget()
        self.foot.setLayout(foot)
        layout.addWidget(self.foot)
        self.keys = []
        self._building = False
        self._settle_later = QtCore.QTimer(self)
        self._settle_later.setSingleShot(True)
        self._settle_later.setInterval(250)
        self._settle_later.timeout.connect(self._run_checks)
        self._show_list(False)

    def enter(self):
        if self.state.root and not self.path.text():
            self.path.setText(self.state.root)
        self.refresh()

    def can_leave(self, forward=True):
        self.settle()
        return True

    def choose(self):
        picked = self.wizard.pick_folder(self.path.text() or self.state.root or os.path.expanduser("~"))
        if picked:
            self.path.setText(picked)
            self.scan_typed()

    def scan_typed(self):
        folder = self.path.text().strip().strip('"')
        if self.wizard.busy():
            return
        if not folder or not os.path.isdir(folder):
            self.path.set_invalid(True)
            self._say(tr("l3.ui.folder_missing"), True)
            return
        self.path.set_invalid(False)
        self.state.root = folder
        self._say(tr("l3.ui.scanning"))
        self.skipped.setText("")
        self.wizard.run_job(lambda progress, cancelled: scan.scan_folder(folder, progress, cancelled), self._done, "scan", threaded=True)

    def _say(self, text, bad=False):
        self.status.setText(text)
        uikit.set_tone(self.status, "danger" if bad else None)

    def _show_list(self, on):
        self.scroll.setVisible(on)
        self.foot.setVisible(on)
        self.empty.setVisible(not on)

    def _done(self, job):
        if job.error is not None:
            self._say(tr("l3.ui.scan_failed"), True)
            return
        result = job.result
        if job.cancelled or (result is not None and result.cancelled):
            self._say(tr("l3.ui.scan_cancelled"), True)
            return
        self.state.set_scan(result)
        self.refresh()

    def refresh(self):
        scan_result = self.state.scan
        if scan_result is None:
            self._say("")
            self.skipped.setText("")
            self._show_list(False)
            return
        skipped = len(scan_result.foreign) + len(scan_result.unreadable)
        self.skipped.setText(plural("l3.ui.skipped", skipped) if skipped else "")
        if not scan_result.records:
            self._say(tr("l3.ui.none_found"), True)
            self._show_list(False)
            return
        self._say(plural("l3.ui.found", len(scan_result.flights), photos=fmt_count(len(scan_result.records)), flights=len(scan_result.flights)))
        self.rebuild()

    # -- the list of flights -----------------------------------------------------------------------

    def rebuild(self):
        self._building = True
        flights = self.state.scan.flights if self.state.scan is not None else []
        self.keys = [flight.key for flight in flights]
        rows = []
        for flight in flights:
            info = checks.flight_row(flight)
            fix_tone = None if info["fix"] >= 0.999 else ("danger" if info["fix"] < 0.5 else "warning")       # RTK below 100 %: coloured, with its value
            rows.append(["", uikit.cell(info["name"], tone="strong"), (fmt_count(info["pairs"]), info["pairs"]),
                         uikit.cell("{:.0f} %".format(info["fix"] * 100.0), sort=info["fix"], tone=fix_tone), fmt_time(info["start"])])
        self.scroll.set_rows(rows)
        model = self.table.model()
        selection = self.table.selectionModel()
        selection.clearSelection()
        for index, key in enumerate(self.keys):
            self.table.item(index, 0).setTextAlignment(Qt.AlignCenter)
            self.table.item(index, 1).setToolTip(key)
            if self.state.selection.get(key):
                span = QtCore.QItemSelection(model.index(index, 0), model.index(index, model.columnCount() - 1))
                selection.select(span, QtCore.QItemSelectionModel.Select)
        self._building = False
        self._paint_ticks()
        self._show_list(True)
        self.update_summary()

    def _paint_ticks(self):
        ticked = set(self.scroll.selected_rows())
        for index in range(len(self.keys)):
            self.table.item(index, 0).setText("✓" if index in ticked else "")

    def is_ticked(self, key):
        return key in self.keys and self.keys.index(key) in self.scroll.selected_rows()

    def set_ticked(self, key, on):
        """Tick or untick one flight like a click on its row would."""
        if key in self.keys and self.is_ticked(key) != bool(on):
            index = self.keys.index(key)
            model = self.table.model()
            span = QtCore.QItemSelection(model.index(index, 0), model.index(index, model.columnCount() - 1))
            self.table.selectionModel().select(span, QtCore.QItemSelectionModel.Select if on else QtCore.QItemSelectionModel.Deselect)

    def row_point(self, key):
        """Centre of the row of a flight, in the viewport of the list (where a click ticks it)."""
        return self.table.visualRect(self.table.model().index(self.keys.index(key), 1)).center()

    def _selection_changed(self):
        if self._building:
            return
        ticked = set(self.scroll.selected_rows())
        for index, key in enumerate(self.keys):
            self.state.selection[key] = index in ticked
        self._paint_ticks()
        self._settle_later.start()                          # the checks are made again once the clicks stop, not once per click
        self.wizard.refresh_buttons()

    def settle(self):
        """Makes the checks of the ticked flights now when a click is waiting for them."""
        if self._settle_later.isActive():
            self._settle_later.stop()
            self._run_checks()

    def _run_checks(self):
        self.state.refresh_analysis()
        self.update_summary()
        self.wizard.refresh_buttons()

    def next_enabled(self):
        return not self._settle_later.isActive() and super(PhotosScreen, self).next_enabled()

    def _pick_all(self, value):
        self._settle_later.stop()
        self._building = True
        selection = self.table.selectionModel()
        if value:
            model = self.table.model()
            for index in range(len(self.keys)):
                span = QtCore.QItemSelection(model.index(index, 0), model.index(index, model.columnCount() - 1))
                selection.select(span, QtCore.QItemSelectionModel.Select)
        else:
            selection.clearSelection()
        for key in self.keys:
            self.state.selection[key] = bool(value)
        self._building = False
        self._paint_ticks()
        self.state.refresh_analysis()
        self.update_summary()
        self.wizard.refresh_buttons()

    def update_summary(self):
        analysis = self.state.analysis
        total = len(self.keys)
        picked = len(self.state.selected_flights())
        self.pick_all.setEnabled(picked < total)
        self.pick_none.setEnabled(picked > 0)
        if analysis is None or not picked:
            self.summary.setText(tr("l3.ui.selected_none"))
            return
        self.summary.setText(plural("l3.ui.selected", total, n=picked, total=total, shots=fmt_count(analysis.summary.pairs)))


# --------------------------------------------------------------------------------------------------
# 2. Checks
# --------------------------------------------------------------------------------------------------

LEVEL_GLYPH = {checks.ERROR: "error", checks.WARNING: "warn", checks.INFO: "info"}
LEVEL_TONE = {checks.ERROR: "danger", checks.WARNING: "warning", checks.INFO: "info"}
LEVEL_NAME = {checks.ERROR: "l3.ui.level_error", checks.WARNING: "l3.ui.level_warning", checks.INFO: "l3.ui.level_note"}


class FindingRow(object):
    """One line of the list of findings: the mark, a few words, the number, and the "i" with the explanation. The widgets sit in the
    columns of the grid of the screen, so the numbers line up."""

    def __init__(self, grid, row, finding):
        self.glyph = uikit.Glyph(LEVEL_GLYPH[finding.level])
        self.glyph.setAccessibleName(tr(LEVEL_NAME[finding.level]))
        tone = LEVEL_TONE[finding.level]
        self.word = uikit.label(tr(LEVEL_NAME[finding.level]), "small", tone, wrap=False)           # the status is mark + word + colour
        self.label = uikit.label(finding.short(), "body", wrap=False)
        self.count = uikit.label(finding.count_text(), "strong", wrap=False)
        self.count.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.info = uikit.InfoButton(finding.tip())
        grid.addWidget(self.glyph, row, 0)
        grid.addWidget(self.word, row, 1)
        grid.addWidget(self.label, row, 2)
        grid.addWidget(self.count, row, 3)
        grid.addWidget(self.info, row, 4)


class ChecksScreen(Screen):
    step = S.CHECKS
    name_key = "l3.ui.step_checks"

    def __init__(self, wizard):
        super(ChecksScreen, self).__init__(wizard)
        layout = self.content
        layout.setSpacing(tokens.L)
        head = uikit.hbox(spacing=tokens.M)
        self.verdict_glyph = uikit.Glyph("pending", tokens.ICON_L + 8)
        self.verdict = uikit.label("", "title", wrap=False)
        head.addWidget(self.verdict_glyph)
        head.addWidget(self.verdict)
        head.addStretch(1)
        layout.addLayout(head)
        strip = uikit.hbox(spacing=tokens.M)
        self.tiles = {}
        for key in ("shots", "flights", "left", "right", "tilt"):
            card = QtWidgets.QFrame()
            card.setProperty("ui", "card")
            card.setAttribute(Qt.WA_StyledBackground, True)
            inner = uikit.vbox((tokens.M, tokens.S, tokens.M, tokens.S), 0, parent=card)
            inner.addWidget(uikit.label(tr("l3.ui.tile_" + key), "caption", wrap=False))
            number = uikit.ElidedLabel("-", "lead")                   # the figure the user came for: larger, primary colour
            inner.addWidget(number)
            self.tiles[key] = number
            strip.addWidget(card, 2 if key in ("left", "right") else 1)           # serial numbers get the room: never cut when it exists
        layout.addLayout(strip)
        self.page = QtWidgets.QWidget()
        outer = uikit.vbox(spacing=tokens.L, parent=self.page)
        self.rows = QtWidgets.QGridLayout()
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setHorizontalSpacing(tokens.M)
        self.rows.setVerticalSpacing(tokens.M)
        self.rows.setColumnStretch(2, 1)
        outer.addLayout(self.rows)
        self.tail = uikit.vbox()
        outer.addLayout(self.tail)
        outer.addStretch(1)
        self.scroll = plain_scroll(self.page)
        layout.addWidget(self.scroll, 1)
        self.finding_rows = []
        self.passed = None

    def enter(self):
        self.state.refresh_analysis()
        self.rebuild()

    def extra_focus(self):
        return [row.info for row in self.finding_rows]

    def rebuild(self):
        clear_layout(self.rows)
        clear_layout(self.tail)
        self.finding_rows = []
        self.passed = None
        analysis = self.state.analysis
        if analysis is None:
            self.verdict.setText(tr("l3.ui.checks_none"))
            uikit.set_tone(self.verdict, None)
            self.verdict_glyph.set_kind("pending")
            for number in self.tiles.values():
                number.setFullText("-")
            return
        errors = len(analysis.blocking)
        warnings = sum(1 for f in analysis.findings if f.level == checks.WARNING)
        if errors:
            self.verdict.setText(tr("l3.ui.verdict_block", n=errors))
            uikit.set_tone(self.verdict, "danger")
            self.verdict_glyph.set_kind("error")
        elif warnings:
            self.verdict.setText(tr("l3.ui.verdict_check", n=warnings))
            uikit.set_tone(self.verdict, "warning")
            self.verdict_glyph.set_kind("warn")
        else:
            self.verdict.setText(tr("l3.ui.verdict_ready"))
            uikit.set_tone(self.verdict, "success")
            self.verdict_glyph.set_kind("ok")
        self._fill_tiles(analysis)
        for index, finding in enumerate(analysis.findings):
            self.finding_rows.append(FindingRow(self.rows, index, finding))
        passed = analysis.passed
        if passed:
            body = QtWidgets.QWidget()
            inner = QtWidgets.QGridLayout(body)
            inner.setContentsMargins(0, 0, 0, 0)
            inner.setHorizontalSpacing(tokens.M)
            inner.setVerticalSpacing(tokens.M)
            inner.setColumnStretch(2, 1)
            inner.setColumnStretch(5, 1)
            half = (len(passed) + 1) // 2                       # two columns: the list stays short
            for index, key in enumerate(passed):
                glyph = uikit.Glyph("ok")
                glyph.setAccessibleName(tr("l3.ui.level_ok"))
                row, base = index % half, (index // half) * 3
                inner.addWidget(glyph, row, base)
                inner.addWidget(uikit.label(tr("l3.pass." + key), "body", wrap=False), row, base + 1)
            self.passed = uikit.Disclosure(plural("l3.ui.passed", len(passed)), body)
            self.tail.addWidget(self.passed)

    def _fill_tiles(self, analysis):
        s = analysis.summary
        self.tiles["shots"].setFullText(fmt_count(s.pairs))
        self.tiles["flights"].setFullText(str(s.flights))
        for key, lens in (("left", "L"), ("right", "R")):
            self.tiles[key].setFullText(s.serials.get(lens) or "-")
        if s.tilt_min is None:
            tilt = "-"
        elif round(s.tilt_min, 1) == round(s.tilt_max, 1):
            tilt = tr("l3.ui.tilt_value", deg=i18n.fmt_number(s.tilt_min, 1))
        else:
            tilt = tr("l3.ui.tilt_range", low=i18n.fmt_number(s.tilt_min, 1), high=i18n.fmt_number(s.tilt_max, 1))
        self.tiles["tilt"].setFullText(tilt)


# --------------------------------------------------------------------------------------------------
# 3. Project
# --------------------------------------------------------------------------------------------------

class ProjectScreen(Screen):
    step = S.PROJECT
    name_key = "l3.ui.step_project"
    FIELDS = (("accuracy_fixed", "l3.ui.opt_acc_fixed", 0.001, 100.0, 3, 0.01), ("accuracy_float", "l3.ui.opt_acc_float", 0.001, 100.0, 3, 0.01),
              ("accuracy_single", "l3.ui.opt_acc_single", 0.001, 1000.0, 2, 0.1))

    def __init__(self, wizard):
        super(ProjectScreen, self).__init__(wizard)
        options = self.state.options
        form = uikit.FormGrid()
        self.fields = {}

        self.chunk = uikit.TextField(options.chunk_name)
        self.chunk.textEdited.connect(self._name_edited)
        self.name_row = form.add_row(tr("l3.ui.opt_name"), self.chunk)

        self.frame = uikit.Segmented([("wgs84", tr("l3.ui.frame_wgs84")), ("etrs89", tr("l3.ui.frame_etrs89"))])
        self.frame.changed.connect(lambda key: self._set("frame", key))
        form.add_row(tr("l3.ui.opt_frame"), self.frame, info=tr("l3.ui.opt_frame_info"))

        chain = QtWidgets.QWidget()
        chain_row = uikit.hbox(spacing=tokens.L, parent=chain)
        self.boxes = {}
        for key in ("align", "optimize", "dem", "ortho"):
            box = uikit.Toggle(tr("l3.ui.opt_step_" + key))
            box.toggled.connect(lambda on, k=key: self._step(k, bool(on)))
            self.boxes[key] = box
            chain_row.addWidget(box)
        form.add_row(tr("l3.ui.opt_after"), chain, info=tr("l3.ui.opt_after_info"))

        self.fields["pixel_cm"] = uikit.NumberField(options.pixel_cm, 0.5, 500.0, 1, 0.5, "cm")
        self.fields["pixel_cm"].valueChanged.connect(lambda v: self._set("pixel_cm", v))
        self.pixel_row = form.add_row(tr("l3.ui.opt_pixel"), self.fields["pixel_cm"])

        self.quick = uikit.Toggle()
        self.quick.toggled.connect(lambda v: self._set("quick_test", bool(v)))
        form.add_row(tr("l3.ui.opt_quick"), self.quick, info=tr("l3.ui.opt_quick_info"))
        self.content.addWidget(form)

        left, right = uikit.FormGrid(), uikit.FormGrid()
        for key, text_key, low, high, decimals, step in self.FIELDS:
            field = uikit.NumberField(getattr(options, key), low, high, decimals, step, "m")
            field.valueChanged.connect(lambda v, k=key: self._set(k, v))
            self.fields[key] = field
            left.add_row(tr(text_key), field)
        self.fields["z_shift"] = uikit.NumberField(options.z_shift, -50.0, 50.0, 3, 0.1, "m")
        self.fields["z_shift"].valueChanged.connect(lambda v: self._set("z_shift", v))
        right.add_row(tr("l3.ui.opt_zshift"), self.fields["z_shift"], info=tr("l3.ui.opt_zshift_info"))
        self.fields["rotation_accuracy"] = uikit.NumberField(options.rotation_accuracy, 0.5, 180.0, 1, 0.5, "deg")
        self.fields["rotation_accuracy"].valueChanged.connect(lambda v: self._set("rotation_accuracy", v))
        right.add_row(tr("l3.ui.opt_rot_acc"), self.fields["rotation_accuracy"], info=tr("l3.ui.opt_rot_info"))
        self.fixed_cal = uikit.Toggle()
        self.fixed_cal.toggled.connect(lambda v: self._set("calibration_fixed", bool(v)))
        right.add_row(tr("l3.ui.opt_fixed_cal"), self.fixed_cal)
        self.unpaired = uikit.Toggle()
        self.unpaired.toggled.connect(lambda v: self._set("include_unpaired", bool(v)))
        right.add_row(tr("l3.ui.opt_unpaired"), self.unpaired)
        more = QtWidgets.QWidget()
        columns = uikit.hbox(spacing=tokens.XL, parent=more)
        columns.addWidget(left, 1, Qt.AlignTop)
        columns.addWidget(right, 1, Qt.AlignTop)
        self.advanced = uikit.Disclosure(tr("l3.ui.opt_advanced"))
        self.advanced.add(more)
        self.content.addWidget(self.advanced)
        self.content.addStretch(1)
        self._enter = EnterMoves(self)
        for box in [self.chunk] + list(self.fields.values()):
            box.installEventFilter(self._enter)
        self.load()

    def load(self):
        options = self.state.options
        self.chunk.blockSignals(True)
        self.chunk.setText(options.chunk_name)
        self.chunk.blockSignals(False)
        self.name_row.clear_error()
        self.frame.blockSignals(True)
        self.frame.set_value(options.frame)
        self.frame.blockSignals(False)
        for key, field in self.fields.items():
            field.set_value(getattr(options, key))
        for widget, value in ((self.fixed_cal, options.calibration_fixed), (self.unpaired, options.include_unpaired), (self.quick, options.quick_test)):
            widget.blockSignals(True)
            widget.setChecked(bool(value))
            widget.blockSignals(False)
        self.sync_steps()

    def sync_steps(self):
        steps = self.state.options.steps
        for key, box in self.boxes.items():
            box.blockSignals(True)
            box.setChecked(bool(steps.get(key)))
            box.blockSignals(False)
        self.pixel_row.setEnabled(bool(steps.get("ortho")))

    def enter(self):
        self.load()

    def _name_edited(self, text):
        self.state.options.chunk_name = text
        self.name_row.set_error("" if text.strip() else tr("l3.ui.name_required"))
        self.changed.emit()

    def _set(self, key, value):
        self.state.set_option(key, value)
        self.changed.emit()

    def _step(self, key, value):
        self.state.set_step(key, value)
        self.sync_steps()
        self.changed.emit()

    def next_text(self):
        return tr("l3.ui.create")


# --------------------------------------------------------------------------------------------------
# 4. Create
# --------------------------------------------------------------------------------------------------

def display_keys(state):
    """The steps the Create screen lists, in order. The repair of flipped cameras belongs to the alignment and has no line of its own
    (one only appears afterwards when something was repaired or could not be)."""
    keys = ["create", "verify"] + [k for k in state.steps_to_run() if k != "repair"]
    return keys


def display_spans(state):
    spans = []
    for key, low, high in backend_module.step_spans(state.steps_to_run()):
        if key == "repair" and spans and spans[-1][0] == "align":
            spans[-1] = ("align", spans[-1][1], high)
        elif key != "repair":
            spans.append((key, low, high))
    return spans


class StepLine(object):
    """The widgets of one line of the list: mark, name, key figure and "i" (the detail lines of the step)."""

    def __init__(self, grid, row, key, name):
        self.key = key
        self.glyph = uikit.Glyph("pending")
        self.name = uikit.label(name, "body", "muted", wrap=False)
        self.figure = uikit.ElidedLabel("", "small", "muted", fit=True)
        self.info = uikit.InfoButton("")
        self.info.setVisible(False)
        grid.addWidget(self.glyph, row, 0, Qt.AlignVCenter)
        grid.addWidget(self.name, row, 1)
        grid.addWidget(self.figure, row, 2)
        grid.addWidget(self.info, row, 3)

    def set(self, kind, figure="", tip="", bad=False):
        self.glyph.set_kind(kind)
        self.figure.setFullText(figure)
        uikit.style.set_prop(self.figure, "type", "strong" if figure else "small")           # the result of a step reads first, in primary colour
        uikit.set_tone(self.figure, "danger" if bad else None)
        self.info.setVisible(bool(tip))
        if tip:
            self.info.set_text(tip)
        uikit.set_tone(self.name, "muted" if kind == "pending" else None)


class RunScreen(Screen):
    step = S.RUN
    name_key = "l3.ui.step_run"

    def __init__(self, wizard):
        super(RunScreen, self).__init__(wizard)
        layout = self.content
        layout.setSpacing(tokens.L)
        head = uikit.hbox(spacing=tokens.M)
        self.head_glyph = uikit.Glyph("pending", tokens.ICON_L + 8)
        self.headline = uikit.label("", "title", wrap=False)
        head.addWidget(self.head_glyph)
        head.addWidget(self.headline)
        head.addStretch(1)
        layout.addLayout(head)
        self.page = QtWidgets.QWidget()
        self.grid = QtWidgets.QGridLayout(self.page)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(tokens.M)
        self.grid.setVerticalSpacing(tokens.M)
        self.grid.setColumnStretch(4, 1)
        layout.addWidget(self.page)
        self.next_line = uikit.label("", "body", wrap=True)
        layout.addWidget(self.next_line)
        layout.addStretch(1)
        self.lines = []
        self._spans = []
        self._started = False

    def enter(self):
        if self._started or self.wizard.busy():
            return
        self._started = True
        self.start()

    def can_leave(self, forward=True):
        if forward:
            return True
        self._started = False
        return not self.wizard.busy()

    def show_next(self):
        return False

    def show_back(self):
        return False                 # a second Create would make a second chunk: close the window instead

    def next_enabled(self):
        return False

    def extra_focus(self):
        return [line.info for line in self.lines if line.info.isVisibleTo(self)]

    def _plan_lines(self):
        clear_layout(self.grid)
        self.lines = []
        for row, key in enumerate(display_keys(self.state)):
            self.lines.append(StepLine(self.grid, row, key, tr("l3.run.name_" + key)))

    def start(self):
        analysis = self.state.analysis
        if analysis is None or not analysis.ok:
            return
        S.save_prefs(self.state)
        plan, steps, backend = analysis.plan, self.state.steps_to_run(), self.wizard.backend
        self._plan_lines()
        self._spans = display_spans(self.state)
        self.headline.setText(tr("l3.ui.running", name=plan.options.chunk_name))
        uikit.set_tone(self.headline, None)
        self.head_glyph.set_kind("running")
        self.next_line.setText("")
        self._show_progress(0.0, "")
        self.state.result = None
        threaded = bool(getattr(backend, "threaded", False))
        self.wizard.run_job(lambda progress, cancelled: backend.run(plan, steps, progress, cancelled), self._done, "run", threaded=threaded,
                            on_progress=self._progress, retry=False)             # a second run would make a second chunk

    def _show_progress(self, fraction, text):
        current = len(self.lines) - 1
        for index, (_key, low, high) in enumerate(self._spans):
            if fraction < high or index == len(self._spans) - 1:
                current = index
                break
        for index, line in enumerate(self.lines):
            if index < current:
                line.set("ok")
            elif index == current:
                line.set("running", text)
            else:
                line.set("pending")

    def _progress(self, fraction, text):
        self._show_progress(fraction, text)

    def _done(self, job):
        self._started = True
        if job.error is not None:
            for line in self.lines:
                if line.glyph.kind() == "running":
                    line.set("error")
            self._headline("danger", "error", tr("l3.ui.run_failed"))
            return
        result = job.result
        self.state.result = result
        if result is None:
            return
        self._fill_result(result)
        name = result.chunk_label or ""
        if result.cancelled or job.cancelled:
            self._headline("warning", "warn", tr("l3.ui.run_cancelled"))
        elif result.ok:
            self._headline("success", "ok", tr("l3.ui.run_ok", name=name))
            self.next_line.setText(tr("l3.ui.run_next_aligned" if result.alignment is not None else "l3.ui.run_next_align"))
        else:
            self._headline("warning", "warn", tr("l3.ui.run_problem", name=name))

    def _headline(self, tone, glyph, text):
        self.headline.setText(text)
        uikit.set_tone(self.headline, tone)
        self.head_glyph.set_kind(glyph)

    def _fill_result(self, result):
        """One line per step with its key figure; the lines that did not run stay pending."""
        wanted = [line for line in self.lines]
        repair = result.step("repair")
        show_repair = repair is not None and (not repair.ok or bool(result.alignment is not None and result.alignment.repaired))
        if show_repair:
            index = next((i for i, line in enumerate(self.lines) if line.key == "align"), len(self.lines) - 1)
            keys = [line.key for line in self.lines]
            keys.insert(index + 1, "repair")
            clear_layout(self.grid)
            self.lines = [StepLine(self.grid, row, key, tr("l3.run.name_" + key)) for row, key in enumerate(keys)]
            wanted = self.lines
        for line in wanted:
            step = result.step(line.key)
            if step is None:
                line.set("pending")
            elif step.ok and not step.skipped:
                line.set("ok", group_digits(step.text), "\n".join(step.lines))
            elif step.skipped:
                line.set("pending", group_digits(step.text), "\n".join(step.lines))
            else:
                line.set("error", group_digits(step.text), "\n".join(step.lines), bad=True)
