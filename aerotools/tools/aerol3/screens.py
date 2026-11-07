"""Aero-L3 UI: the five screens of the wizard (folder, flights, checks, project, create). Qt widgets only; every rule is in `state.py` and `checks.py`."""

import os

from ... import i18n, theme, ui
from ...qt import QtCore, QtGui, QtWidgets
from ..aeroexports import controls
from . import checks, scan
from . import state as S

CHECK_LEVEL_KEYS = {checks.ERROR: "l3.ui.level_error", checks.WARNING: "l3.ui.level_warning", checks.INFO: "l3.ui.level_info"}
BADGE_NAMES = {checks.ERROR: "l3BadgeError", checks.WARNING: "l3BadgeWarning", checks.INFO: "l3BadgeInfo"}


def tr(key, **fields):
    return i18n.t(key, **fields)


def card():
    frame = QtWidgets.QFrame()
    frame.setObjectName("l3Card")
    layout = QtWidgets.QVBoxLayout(frame)
    layout.setContentsMargins(theme.PAD_L, theme.PAD_L, theme.PAD_L, theme.PAD_L)
    layout.setSpacing(theme.PAD)
    return frame, layout


def hint(text):
    label = ui.label(text, "l3Hint")
    label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
    return label


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


def fmt_time(moment):
    return moment.strftime("%Y-%m-%d %H:%M") if moment is not None else "-"


class Screen(QtWidgets.QWidget):
    """One screen. The wizard calls `enter()` when it appears and `leave(forward)` when it goes away (False refuses), and asks the
    methods below for the state of the footer buttons."""

    step = 0
    name_key = ""

    def __init__(self, wizard):
        super(Screen, self).__init__()
        self.wizard = wizard
        self.state = wizard.state

    def enter(self):
        pass

    def leave(self, forward):
        return True

    def next_enabled(self):
        return self.state.can_next(self.step)

    def next_text(self):
        return tr("l3.ui.next")

    def show_next(self):
        return True

    def show_back(self):
        return self.step > S.FOLDER

    def busy(self):
        return False

    def next_step(self):
        return self.step + 1

    def prev_step(self):
        return self.step - 1


# --------------------------------------------------------------------------------------------------
# 1. Folder
# --------------------------------------------------------------------------------------------------

class FolderScreen(Screen):
    step = S.FOLDER
    name_key = "l3.ui.step_folder"

    def __init__(self, wizard):
        super(FolderScreen, self).__init__(wizard)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.PAD_L)
        layout.addWidget(ui.label(tr("l3.ui.folder_title"), "l3Big"))
        layout.addWidget(ui.label(tr("l3.ui.folder_text")))
        frame, inner = card()
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(theme.PAD)
        self.path = QtWidgets.QLineEdit()
        self.path.setPlaceholderText(tr("l3.ui.folder_placeholder"))
        self.path.setMinimumHeight(36)
        self.path.returnPressed.connect(self.scan_typed)
        self.browse = ui.big_button("l3.ui.browse", self.choose, "aeroBtn", width=150)
        self.scan_button = ui.big_button("l3.ui.scan", self.scan_typed, "aeroGo", width=120)
        row.addWidget(self.path, 1)
        row.addWidget(self.browse)
        row.addWidget(self.scan_button)
        inner.addLayout(row)
        self.bar = QtWidgets.QProgressBar()
        self.bar.setObjectName("l3Bar")
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setVisible(False)
        inner.addWidget(self.bar)
        self.status = ui.label("", "l3Head")
        inner.addWidget(self.status)
        self.detail = hint("")
        inner.addWidget(self.detail)
        layout.addWidget(frame)
        layout.addWidget(hint(tr("l3.ui.folder_hint")))
        layout.addStretch(1)

    def enter(self):
        if self.state.root and not self.path.text():
            self.path.setText(self.state.root)
        self.refresh()

    def busy(self):
        return self.wizard.busy()

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
            self.status.setText(tr("l3.ui.folder_missing"))
            self.status.setObjectName("l3Bad")
            self.status.style().unpolish(self.status)
            self.status.style().polish(self.status)
            return
        self.state.root = folder
        self.bar.setValue(0)
        self.bar.setVisible(True)
        self.status.setObjectName("l3Head")
        self.status.setText(tr("l3.ui.scanning"))
        self.detail.setText("")
        self.wizard.run_job(lambda progress, cancelled: scan.scan_folder(folder, progress, cancelled), self._progress, self._done, "scan", threaded=True)

    def _progress(self, task):
        self.bar.setValue(int(task.fraction * 1000))
        self.detail.setText(task.text)

    def _done(self, task):
        self.bar.setVisible(False)
        if task.error is not None:
            self.status.setText(tr("l3.ui.scan_failed", reason=str(task.error)))
            self.detail.setText("")
            return
        result = task.result
        if task.was_cancelled or (result is not None and result.cancelled):
            self.status.setText(tr("l3.ui.scan_cancelled"))
            self.detail.setText("")
            return
        self.state.set_scan(result)
        self.refresh()

    def refresh(self):
        scan_result = self.state.scan
        self.status.setObjectName("l3Head")
        if scan_result is None:
            self.status.setText("")
            self.detail.setText("")
            return
        if not scan_result.records:
            self.status.setText(tr("l3.ui.none_found"))
            self.detail.setText(tr("l3.ui.none_found_detail", n=len(scan_result.foreign) + len(scan_result.unreadable)))
            return
        self.status.setText(tr("l3.ui.found", photos=fmt_count(len(scan_result.records)), flights=len(scan_result.flights)))
        extra = []
        if scan_result.foreign:
            extra.append(tr("l3.ui.foreign", n=len(scan_result.foreign)))
        if scan_result.unreadable:
            extra.append(tr("l3.ui.unreadable", n=len(scan_result.unreadable)))
        self.detail.setText("  ".join(extra))


# --------------------------------------------------------------------------------------------------
# 2. Flights
# --------------------------------------------------------------------------------------------------

COLUMNS = ("l3.ui.col_flight", "l3.ui.col_photos", "l3.ui.col_pairs", "l3.ui.col_fix", "l3.ui.col_tilt", "l3.ui.col_files", "l3.ui.col_start")


class FlightsScreen(Screen):
    step = S.FLIGHTS
    name_key = "l3.ui.step_flights"

    def __init__(self, wizard):
        super(FlightsScreen, self).__init__(wizard)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.PAD_L)
        layout.addWidget(ui.label(tr("l3.ui.flights_title"), "l3Big"))
        tiles = QtWidgets.QHBoxLayout()
        tiles.setSpacing(theme.PAD)
        self.tiles = {}
        for key in ("photos", "flights", "pairs", "rtk", "period"):
            frame, number, caption = ui.tile(tr("l3.ui.tile_" + key))
            frame.setMinimumWidth(150)
            self.tiles[key] = (number, caption)
            tiles.addWidget(frame, 2 if key == "period" else 1)
        layout.addLayout(tiles)
        self.table = QtWidgets.QTableWidget(0, len(COLUMNS))
        self.table.setStyleSheet(ui.table_style())
        self.table.setHorizontalHeaderLabels([tr(k) for k in COLUMNS])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.table.itemChanged.connect(self._ticked)
        layout.addWidget(self.table, 1)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(theme.PAD)
        self.all_button = ui.big_button("l3.ui.select_all", lambda: self._set_all(True), "aeroBtn", width=140)
        self.none_button = ui.big_button("l3.ui.select_none", lambda: self._set_all(False), "aeroBtn", width=140)
        row.addWidget(self.all_button)
        row.addWidget(self.none_button)
        row.addStretch(1)
        layout.addLayout(row)
        self.note = hint(tr("l3.ui.flights_hint"))
        layout.addWidget(self.note)
        self._building = False

    def enter(self):
        self.rebuild()

    def rebuild(self):
        self._building = True
        flights = self.state.scan.flights if self.state.scan is not None else []
        self.table.setRowCount(len(flights))
        for row, flight in enumerate(flights):
            info = checks.flight_row(flight)
            name = QtWidgets.QTableWidgetItem(info["name"])
            name.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsUserCheckable)
            name.setCheckState(QtCore.Qt.Checked if self.state.selection.get(flight.key) else QtCore.Qt.Unchecked)
            name.setData(QtCore.Qt.UserRole, flight.key)
            name.setToolTip(flight.key)
            self.table.setItem(row, 0, name)
            files = [tr("l3.ui.file_mrk") if info["mrk"] else tr("l3.ui.file_no_mrk")]
            if info["lidar"]:
                files.append(tr("l3.ui.file_lidar"))
            values = [fmt_count(info["photos"]), fmt_count(info["pairs"]), "{:.0f} %".format(info["fix"] * 100.0),
                      tr("l3.ui.tilt_value", deg=i18n.fmt_number(info["tilt"], 1)) if info["tilt"] is not None else "-", ", ".join(files),
                      fmt_time(info["start"])]
            for col, value in enumerate(values, start=1):
                item = QtWidgets.QTableWidgetItem(value)
                item.setFlags(QtCore.Qt.ItemIsEnabled)
                if col in (1, 2, 3, 4):
                    item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self._building = False
        self.update_tiles()

    def _ticked(self, item):
        if self._building or item.column() != 0:
            return
        self.state.set_selected(item.data(QtCore.Qt.UserRole), item.checkState() == QtCore.Qt.Checked)
        self.update_tiles()
        self.wizard.refresh_buttons()

    def _set_all(self, value):
        self._building = True
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            item.setCheckState(QtCore.Qt.Checked if value else QtCore.Qt.Unchecked)
            self.state.selection[item.data(QtCore.Qt.UserRole)] = value
        self._building = False
        self.state.refresh_analysis()
        self.update_tiles()
        self.wizard.refresh_buttons()

    def update_tiles(self):
        analysis = self.state.analysis
        values = {"photos": "-", "flights": "-", "pairs": "-", "rtk": "-", "period": "-"}
        if analysis is not None:
            s = analysis.summary
            used = sum(s.rtk.values())
            values = {
                "photos": fmt_count(analysis.plan.photo_count), "flights": str(s.flights), "pairs": fmt_count(s.pairs),
                "rtk": "{:.0f} %".format(100.0 * s.rtk["fix"] / used) if used else "-",
                "period": "{} - {}".format(fmt_time(s.first_utc), s.last_utc.strftime("%H:%M")) if s.first_utc is not None and s.last_utc is not None else "-",
            }
        for key, (number, _caption) in self.tiles.items():
            number.setText(values[key])


# --------------------------------------------------------------------------------------------------
# 3. Checks
# --------------------------------------------------------------------------------------------------

class ChecksScreen(Screen):
    step = S.CHECKS
    name_key = "l3.ui.step_checks"

    def __init__(self, wizard):
        super(ChecksScreen, self).__init__(wizard)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.PAD_L)
        layout.addWidget(ui.label(tr("l3.ui.checks_title"), "l3Big"))
        self.verdict = ui.label("", "l3Head")
        layout.addWidget(self.verdict)
        self.page = QtWidgets.QWidget()
        self.rows = QtWidgets.QVBoxLayout(self.page)
        self.rows.setContentsMargins(0, 0, theme.PAD, 0)
        self.rows.setSpacing(theme.PAD)
        self.scroll = controls.scroll_page(self.page, preferred=600)
        layout.addWidget(self.scroll, 1)

    def enter(self):
        self.state.refresh_analysis()
        self.rebuild()

    def rebuild(self):
        clear_layout(self.rows)
        analysis = self.state.analysis
        if analysis is None:
            self.verdict.setText(tr("l3.ui.checks_none"))
            return
        errors = len(analysis.blocking)
        warnings = sum(1 for f in analysis.findings if f.level == checks.WARNING)
        if errors:
            self.verdict.setObjectName("l3Bad")
            self.verdict.setText(tr("l3.ui.verdict_errors", n=errors))
        elif warnings:
            self.verdict.setObjectName("l3Head")
            self.verdict.setText(tr("l3.ui.verdict_warnings", n=warnings))
        else:
            self.verdict.setObjectName("l3Good")
            self.verdict.setText(tr("l3.ui.verdict_ok"))
        self.verdict.style().unpolish(self.verdict)
        self.verdict.style().polish(self.verdict)
        for finding in analysis.findings:
            self.rows.addWidget(self._row(finding))
        self._add_summary(analysis)
        self.rows.addStretch(1)

    def _row(self, finding):
        frame, inner = card()
        inner.setSpacing(2)
        top = QtWidgets.QHBoxLayout()
        top.setSpacing(theme.PAD)
        badge = QtWidgets.QLabel(tr(CHECK_LEVEL_KEYS[finding.level]))
        badge.setObjectName(BADGE_NAMES[finding.level])
        badge.setFixedWidth(84)
        top.addWidget(badge, 0, QtCore.Qt.AlignTop)
        text = ui.label(finding.text())
        text.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        top.addWidget(text, 1)
        inner.addLayout(top)
        detail = finding.detail()
        if detail:
            inner.addWidget(hint(detail))
        return frame

    def _add_summary(self, analysis):
        s = analysis.summary
        frame, inner = card()
        inner.addWidget(ui.label(tr("l3.ui.summary_title"), "l3Head"))
        plan = analysis.plan
        lines = [tr("l3.ui.summary_photos", photos=fmt_count(plan.photo_count), pairs=fmt_count(s.pairs), flights=s.flights)]
        for lens, name in (("L", "l3.ui.lens_left"), ("R", "l3.ui.lens_right")):
            sensor = plan.sensors.get(lens)
            if sensor is not None:
                c = sensor.calibration
                lines.append(tr("l3.ui.summary_sensor", name=tr(name), serial=sensor.serial or "?", f=i18n.fmt_number(c.f, 1), cx=i18n.fmt_number(c.cx, 1),
                                cy=i18n.fmt_number(c.cy, 1), w=c.width, h=c.height))
        if s.tilt_min is not None:
            lines.append(tr("l3.ui.summary_tilt", low=i18n.fmt_number(s.tilt_min, 1), high=i18n.fmt_number(s.tilt_max, 1)))
        if s.mean_v_mm is not None:
            lines.append(tr("l3.ui.summary_mrk_v", v=i18n.fmt_number(s.mean_v_mm / 1000.0, 3)))
        for line in lines:
            label = ui.label(line)
            label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            inner.addWidget(label)
        self.rows.addWidget(frame)


# --------------------------------------------------------------------------------------------------
# 4. Project options
# --------------------------------------------------------------------------------------------------

class NumberField(QtWidgets.QLineEdit):
    """A number typed in a box: Enter or leaving the box applies it, a wrong text goes back to the last good value."""

    def __init__(self, value, low, high, decimals, on_change, width=90):
        super(NumberField, self).__init__()
        self.low, self.high, self.decimals, self.on_change = low, high, decimals, on_change
        self.value = value
        self.setFixedWidth(width)
        self.setMinimumHeight(34)
        self.setAlignment(QtCore.Qt.AlignRight)
        self.set_value(value)
        self.editingFinished.connect(self._finished)

    def set_value(self, value):
        self.value = value
        self.setText(i18n.fmt_number(value, self.decimals))

    def _finished(self):
        parsed = controls.parse_number(self.text())
        if parsed is None or not self.low <= parsed <= self.high:
            self.setText(i18n.fmt_number(self.value, self.decimals))
            return
        if abs(parsed - self.value) > 1e-12:
            self.value = parsed
            self.on_change(parsed)
        self.setText(i18n.fmt_number(self.value, self.decimals))


class OptionsScreen(Screen):
    step = S.OPTIONS
    name_key = "l3.ui.step_options"

    def __init__(self, wizard):
        super(OptionsScreen, self).__init__(wizard)
        self.page = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = controls.scroll_page(self.page, preferred=900)
        outer.addWidget(self.scroll)
        layout = QtWidgets.QVBoxLayout(self.page)
        layout.setContentsMargins(0, 0, theme.PAD, 0)
        layout.setSpacing(theme.PAD_L)
        layout.addWidget(ui.label(tr("l3.ui.options_title"), "l3Big"))
        options = self.state.options

        columns = QtWidgets.QHBoxLayout()
        columns.setSpacing(theme.PAD_L)
        left, lv = card()
        right, rv = card()
        lv.addWidget(ui.label(tr("l3.ui.opt_project"), "l3Head"))
        self.name = QtWidgets.QLineEdit(options.chunk_name)
        self.name.setMinimumHeight(34)
        self.name.textEdited.connect(self._name_edited)
        lv.addWidget(ui.label(tr("l3.ui.opt_name")))
        lv.addWidget(self.name)
        self.frame = ui.combo()
        for key in ("wgs84", "etrs89"):
            self.frame.addItem(tr("l3.ui.frame_" + key), key)
        self.frame.currentIndexChanged.connect(self._frame_changed)
        lv.addWidget(ui.label(tr("l3.ui.opt_frame")))
        lv.addWidget(self.frame)
        lv.addWidget(hint(tr("l3.ui.opt_frame_hint")))
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(theme.PAD)
        self.fields = {}
        rows = (("accuracy_fixed", "l3.ui.opt_acc_fixed", 0.001, 100.0, 3), ("accuracy_float", "l3.ui.opt_acc_float", 0.001, 100.0, 3),
                ("accuracy_single", "l3.ui.opt_acc_single", 0.001, 1000.0, 2), ("z_shift", "l3.ui.opt_zshift", -50.0, 50.0, 3))
        for r, (key, text_key, low, high, decimals) in enumerate(rows):
            grid.addWidget(ui.label(tr(text_key)), r, 0)
            field = NumberField(getattr(options, key), low, high, decimals, lambda v, k=key: self._set(k, v))
            self.fields[key] = field
            grid.addWidget(field, r, 1)
        grid.setColumnStretch(0, 1)
        lv.addLayout(grid)
        lv.addWidget(hint(tr("l3.ui.opt_zshift_hint")))
        lv.addStretch(1)

        rv.addWidget(ui.label(tr("l3.ui.opt_cameras"), "l3Head"))
        grid2 = QtWidgets.QGridLayout()
        grid2.addWidget(ui.label(tr("l3.ui.opt_rot_acc")), 0, 0)
        self.fields["rotation_accuracy"] = NumberField(options.rotation_accuracy, 0.5, 180.0, 1, lambda v: self._set("rotation_accuracy", v))
        grid2.addWidget(self.fields["rotation_accuracy"], 0, 1)
        grid2.setColumnStretch(0, 1)
        rv.addLayout(grid2)
        rv.addWidget(hint(tr("l3.ui.opt_rot_hint")))
        self.fixed_cal = QtWidgets.QCheckBox(tr("l3.ui.opt_fixed_cal"))
        self.fixed_cal.toggled.connect(lambda v: self._set("calibration_fixed", bool(v)))
        rv.addWidget(self.fixed_cal)
        rv.addWidget(hint(tr("l3.ui.opt_fixed_cal_hint")))
        self.unpaired = QtWidgets.QCheckBox(tr("l3.ui.opt_unpaired"))
        self.unpaired.toggled.connect(lambda v: self._set("include_unpaired", bool(v)))
        rv.addWidget(self.unpaired)
        rv.addWidget(hint(tr("l3.ui.opt_unpaired_hint")))
        rv.addStretch(1)
        columns.addWidget(left, 1)
        columns.addWidget(right, 1)
        layout.addLayout(columns)

        steps, sv = card()
        sv.addWidget(ui.label(tr("l3.ui.opt_steps"), "l3Head"))
        sv.addWidget(hint(tr("l3.ui.opt_steps_hint")))
        self.boxes = {}
        self.quick = QtWidgets.QCheckBox(tr("l3.ui.opt_quick"))
        self.quick.toggled.connect(lambda v: self._set("quick_test", bool(v)))
        sv.addWidget(self.quick)
        for key in ("align", "repair", "optimize", "dem", "ortho"):
            box = QtWidgets.QCheckBox(tr("l3.ui.opt_step_" + key))
            box.toggled.connect(lambda v, k=key: self._step(k, bool(v)))
            self.boxes[key] = box
            sv.addWidget(box)
            sv.addWidget(hint(tr("l3.ui.opt_step_" + key + "_hint")))
        pixel = QtWidgets.QHBoxLayout()
        pixel.addWidget(ui.label(tr("l3.ui.opt_pixel")))
        self.fields["pixel_cm"] = NumberField(options.pixel_cm, 0.5, 500.0, 1, lambda v: self._set("pixel_cm", v))
        pixel.addWidget(self.fields["pixel_cm"])
        pixel.addStretch(1)
        sv.addLayout(pixel)
        layout.addWidget(steps)
        layout.addStretch(1)
        self.load()

    def load(self):
        options = self.state.options
        self.name.blockSignals(True)
        self.name.setText(options.chunk_name)
        self.name.blockSignals(False)
        self.frame.blockSignals(True)
        self.frame.setCurrentIndex(max(0, self.frame.findData(options.frame)))
        self.frame.blockSignals(False)
        for key, field in self.fields.items():
            field.set_value(getattr(options, key))
        for widget, value in ((self.fixed_cal, options.calibration_fixed), (self.unpaired, options.include_unpaired), (self.quick, options.quick_test)):
            widget.blockSignals(True)
            widget.setChecked(bool(value))
            widget.blockSignals(False)
        for key, box in self.boxes.items():
            box.blockSignals(True)
            box.setChecked(bool(options.steps.get(key)))
            box.blockSignals(False)

    def enter(self):
        self.load()

    def _name_edited(self, text):
        self.state.options.chunk_name = text
        self.wizard.refresh_buttons()

    def _frame_changed(self, _index):
        self._set("frame", self.frame.currentData())

    def _set(self, key, value):
        self.state.set_option(key, value)
        self.wizard.refresh_buttons()

    def _step(self, key, value):
        self.state.options.steps[key] = value
        self.wizard.refresh_buttons()

    def next_text(self):
        return tr("l3.ui.create")


# --------------------------------------------------------------------------------------------------
# 5. Create
# --------------------------------------------------------------------------------------------------

class RunScreen(Screen):
    step = S.RUN
    name_key = "l3.ui.step_run"

    def __init__(self, wizard):
        super(RunScreen, self).__init__(wizard)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.PAD_L)
        layout.addWidget(ui.label(tr("l3.ui.run_title"), "l3Big"))
        self.headline = ui.label("", "l3Head")
        layout.addWidget(self.headline)
        self.bar = QtWidgets.QProgressBar()
        self.bar.setObjectName("l3Bar")
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        layout.addWidget(self.bar)
        self.text = hint("")
        layout.addWidget(self.text)
        self.page = QtWidgets.QWidget()
        self.rows = QtWidgets.QVBoxLayout(self.page)
        self.rows.setContentsMargins(0, 0, theme.PAD, 0)
        self.rows.setSpacing(theme.PAD)
        layout.addWidget(controls.scroll_page(self.page, preferred=600), 1)
        self._started = False

    def enter(self):
        if self._started or self.wizard.busy():
            return
        self._started = True
        self.start()

    def leave(self, forward):
        if forward:
            return True
        self._started = False
        return not self.wizard.busy()

    def busy(self):
        return self.wizard.busy()

    def show_next(self):
        return False

    def show_back(self):
        return False                 # a second Create would make a second chunk: close the window instead

    def next_enabled(self):
        return False

    def start(self):
        analysis = self.state.analysis
        if analysis is None or not analysis.ok:
            return
        clear_layout(self.rows)
        S.save_prefs(self.state)
        plan, steps, backend = analysis.plan, self.state.steps_to_run(), self.wizard.backend
        self.headline.setObjectName("l3Head")
        self.headline.setText(tr("l3.ui.running", name=plan.options.chunk_name))
        self.bar.setValue(0)
        self.state.result = None
        threaded = bool(getattr(backend, "threaded", False))
        self.wizard.run_job(lambda progress, cancelled: backend.run(plan, steps, progress, cancelled), self._progress, self._done, "run", threaded=threaded)

    def _progress(self, task):
        self.bar.setValue(int(task.fraction * 1000))
        self.text.setText(task.text)

    def _done(self, task):
        self._started = True
        self.bar.setValue(1000 if task.error is None and not task.was_cancelled else self.bar.value())
        self.text.setText("")
        if task.error is not None:
            self.headline.setObjectName("l3Bad")
            self.headline.setText(tr("l3.ui.run_failed", reason=str(task.error)))
            self._restyle()
            return
        result = task.result
        self.state.result = result
        if result is None:
            return
        if result.cancelled or task.was_cancelled:
            self.headline.setObjectName("l3Bad")
            self.headline.setText(tr("l3.ui.run_cancelled", name=result.chunk_label or ""))
        elif result.ok:
            self.headline.setObjectName("l3Good")
            self.headline.setText(tr("l3.ui.run_ok", name=result.chunk_label))
        else:
            self.headline.setObjectName("l3Bad")
            self.headline.setText(tr("l3.ui.run_problem", name=result.chunk_label or ""))
        self._restyle()
        for step in result.steps:
            self.rows.addWidget(self._step_row(step))
        if result.ok and not result.cancelled:
            self.rows.addWidget(self._next_card(result))
        self.rows.addStretch(1)

    def _restyle(self):
        self.headline.style().unpolish(self.headline)
        self.headline.style().polish(self.headline)

    def _step_row(self, step):
        frame, inner = card()
        inner.setSpacing(2)
        top = QtWidgets.QHBoxLayout()
        mark = QtWidgets.QLabel("✓" if step.ok else "✕")
        mark.setObjectName("l3StepOk" if step.ok else "l3StepFail")
        mark.setFixedWidth(22)
        top.addWidget(mark, 0, QtCore.Qt.AlignTop)
        text = ui.label(step.text)
        text.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        top.addWidget(text, 1)
        inner.addLayout(top)
        for line in step.lines:
            inner.addWidget(hint(line))
        return frame

    def _next_card(self, result):
        frame, inner = card()
        inner.addWidget(ui.label(tr("l3.ui.run_next_title"), "l3Head"))
        key = "l3.ui.run_next_aligned" if result.alignment is not None else "l3.ui.run_next_align"
        label = ui.label(tr(key))
        label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        inner.addWidget(label)
        inner.addWidget(hint(tr("l3.ui.run_next_note")))
        return frame
