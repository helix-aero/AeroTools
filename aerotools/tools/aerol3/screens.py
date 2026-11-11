"""Aero-L3 UI: the four screens of the wizard (photos, checks, project, create). Qt widgets only; every rule is in `state.py` and `checks.py`.

A screen is a title, the controls and the result. What needs more words sits in the tip of a small "i" (`widgets.InfoButton`)."""

import os
import re

from ... import i18n, theme, ui
from ...qt import QtCore, QtWidgets
from ... import controls
from . import backend as backend_module
from . import checks, scan
from . import state as S
from .widgets import Arrow, Disclosure, Glyph, InfoButton, Segmented

SPACE = theme.PAD_L


def tr(key, **fields):
    return i18n.t(key, **fields)


def plural(key, count, **fields):
    """Text of `key.one` when `count` is 1 and of `key.other` otherwise; `count` is the field `n` unless the call gives one."""
    fields.setdefault("n", count)
    return i18n.t(key + (".one" if count == 1 else ".other"), **fields)


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


def restyle(widget, name):
    widget.setObjectName(name)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def title(text):
    return ui.label(text, "l3Big", wrap=False)


def page_layout(widget):
    layout = QtWidgets.QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(SPACE)
    return layout


def thin_bar():
    bar = QtWidgets.QProgressBar()
    bar.setObjectName("l3Bar")
    bar.setRange(0, 1000)
    bar.setTextVisible(False)
    return bar


class TextField(QtWidgets.QLineEdit):
    """A line of text as high as its font needs (a fixed height would cut the text when the display scaling is large)."""

    def sizeHint(self):
        return QtCore.QSize(super(TextField, self).sizeHint().width(), max(40, self.fontMetrics().height() + 22))

    def minimumSizeHint(self):
        return QtCore.QSize(60, self.sizeHint().height())


class ListScroll(controls.FitScroll):
    """A list that takes the height it needs and no more; it scrolls inside only when the screen has less room than that."""

    def __init__(self, content, preferred=0):
        super(ListScroll, self).__init__(content, preferred)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Maximum)

    def sizeHint(self):
        room = max(200, controls.screen_area().height() - 440)          # a long list (22 flights) must not ask for more than the screen has
        return QtCore.QSize(super(ListScroll, self).sizeHint().width(), min(self.widget().sizeHint().height() + 4, room))


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
        return self.step > S.PHOTOS

    def busy(self):
        return False

    def next_step(self):
        return self.step + 1

    def prev_step(self):
        return self.step - 1


# --------------------------------------------------------------------------------------------------
# 1. Photos
# --------------------------------------------------------------------------------------------------

class ClickFrame(QtWidgets.QFrame):
    clicked = QtCore.Signal()

    def mousePressEvent(self, event):
        self.clicked.emit()
        super(ClickFrame, self).mousePressEvent(event)


def cell(text, name="l3Cell", right=False, wide=False):
    label = controls.ElidedLabel(name) if wide else QtWidgets.QLabel(text)
    if wide:
        label.setFullText(text)
        label.setTextInteractionFlags(QtCore.Qt.NoTextInteraction)
    else:
        label.setObjectName(name)
    label.setContentsMargins(12, 8, 12, 8)
    if right:
        label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
    return label


class PhotosScreen(Screen):
    step = S.PHOTOS
    name_key = "l3.ui.step_photos"

    def __init__(self, wizard):
        super(PhotosScreen, self).__init__(wizard)
        layout = page_layout(self)
        layout.addWidget(title(tr("l3.ui.photos_title")))
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(theme.PAD)
        self.path = TextField()
        self.path.setPlaceholderText(tr("l3.ui.folder_placeholder"))
        self.path.returnPressed.connect(self.scan_typed)
        self.browse = ui.big_button("l3.ui.browse", self.choose, "aeroBtn", width=130)
        self.scan_button = ui.big_button("l3.ui.scan", self.scan_typed, "aeroGo", width=100)
        row.addWidget(self.path, 1)
        row.addWidget(self.browse)
        row.addWidget(self.scan_button)
        layout.addLayout(row)

        line = QtWidgets.QHBoxLayout()
        line.setSpacing(SPACE)
        self.status = ui.label("", "l3Head", wrap=False)
        self.skipped = ui.label("", "l3Muted", wrap=False)
        line.addWidget(self.status)
        line.addStretch(1)
        line.addWidget(self.skipped)
        layout.addLayout(line)
        self.bar = thin_bar()
        self.bar.setVisible(False)
        layout.addWidget(self.bar)

        self.page = QtWidgets.QWidget()
        self.grid = QtWidgets.QGridLayout(self.page)
        self.grid.setContentsMargins(0, 0, theme.PAD, 0)
        self.grid.setHorizontalSpacing(theme.PAD)
        self.grid.setVerticalSpacing(theme.PAD_S)
        self.scroll = ListScroll(self.page, preferred=600)
        layout.addWidget(self.scroll)
        self.scroll.setVisible(False)
        self.summary = ui.label("", "l3Head", wrap=False)
        layout.addWidget(self.summary)
        self.summary.setVisible(False)
        layout.addStretch(1)
        self.rows = {}
        self.all_box = None
        self._building = False
        self._settle_later = QtCore.QTimer(self)
        self._settle_later.setSingleShot(True)
        self._settle_later.setInterval(250)
        self._settle_later.timeout.connect(self._run_checks)

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
            self._say(tr("l3.ui.folder_missing"), True)
            return
        self.state.root = folder
        self.bar.setValue(0)
        self.bar.setVisible(True)
        self._say(tr("l3.ui.scanning"))
        self.skipped.setText("")
        self.wizard.run_job(lambda progress, cancelled: scan.scan_folder(folder, progress, cancelled), self._progress, self._done, "scan", threaded=True)

    def _say(self, text, bad=False):
        self.status.setText(text)
        restyle(self.status, "l3Bad" if bad else "l3Head")

    def _progress(self, task):
        self.bar.setValue(int(task.fraction * 1000))

    def _done(self, task):
        self.bar.setVisible(False)
        if task.error is not None:
            self._say(tr("l3.ui.scan_failed", reason=str(task.error)), True)
            return
        result = task.result
        if task.was_cancelled or (result is not None and result.cancelled):
            self._say(tr("l3.ui.scan_cancelled"), True)
            return
        self.state.set_scan(result)
        self.refresh()

    def refresh(self):
        scan_result = self.state.scan
        if scan_result is None:
            self._say("")
            self.skipped.setText("")
            self.scroll.setVisible(False)
            self.summary.setVisible(False)
            return
        skipped = len(scan_result.foreign) + len(scan_result.unreadable)
        self.skipped.setText(plural("l3.ui.skipped", skipped) if skipped else "")
        if not scan_result.records:
            self._say(tr("l3.ui.none_found"), True)
            self.scroll.setVisible(False)
            self.summary.setVisible(False)
            return
        self._say(plural("l3.ui.found", len(scan_result.flights), photos=fmt_count(len(scan_result.records)), flights=len(scan_result.flights)))
        self.rebuild()

    # -- the list of flights -----------------------------------------------------------------------

    def rebuild(self):
        self._building = True
        clear_layout(self.grid)
        self.rows = {}
        flights = self.state.scan.flights if self.state.scan is not None else []
        self.all_box = QtWidgets.QCheckBox()
        self.all_box.setAccessibleName(tr("l3.ui.all_flights"))
        self.all_box.setToolTip(tr("l3.ui.all_flights"))
        self.all_box.clicked.connect(self._all_clicked)
        self.grid.addWidget(self.all_box, 0, 0, QtCore.Qt.AlignCenter)
        head = QtWidgets.QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        first = cell(tr("l3.ui.col_flight"), "l3ColHead")
        first.setContentsMargins(12, 0, 4, 0)
        head.addWidget(first)
        head.addWidget(InfoButton(tr("l3.ui.flights_info")))
        head.addStretch(1)
        holder = QtWidgets.QWidget()
        holder.setLayout(head)
        self.grid.addWidget(holder, 0, 1)
        for col, key in ((2, "l3.ui.col_shots"), (3, "l3.ui.col_fix"), (4, "l3.ui.col_date")):
            label = cell(tr(key), "l3ColHead", right=(col < 4))
            label.setContentsMargins(12, 0, 12, 0)
            self.grid.addWidget(label, 0, col)
        for index, flight in enumerate(flights, start=1):
            info = checks.flight_row(flight)
            box = QtWidgets.QCheckBox()
            box.setAccessibleName(info["name"])
            frame = ClickFrame()
            frame.setObjectName("l3Row")
            frame.setToolTip(flight.key)
            labels = [cell(info["name"], wide=True), cell(fmt_count(info["pairs"]), right=True),
                      cell("{:.0f} %".format(info["fix"] * 100.0), right=True), cell(fmt_time(info["start"]))]
            self.grid.addWidget(box, index, 0, QtCore.Qt.AlignCenter)
            self.grid.addWidget(frame, index, 1, 1, 4)
            for col, label in enumerate(labels, start=1):
                label.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
                self.grid.addWidget(label, index, col)
            box.toggled.connect(lambda on, k=flight.key: self._ticked(k, on))
            frame.clicked.connect(box.toggle)
            self.rows[flight.key] = (box, frame, labels)
            box.setChecked(bool(self.state.selection.get(flight.key)))
            self._paint_row(flight.key)
        self.grid.setColumnStretch(1, 1)
        self.grid.setRowStretch(len(flights) + 1, 1)
        self._building = False
        self.scroll.setVisible(True)
        self.summary.setVisible(True)
        self.update_summary()

    def _paint_row(self, key):
        box, frame, labels = self.rows[key]
        on = box.isChecked()
        frame.setProperty("on", on)
        frame.style().unpolish(frame)
        frame.style().polish(frame)
        for label in labels:
            restyle(label, "l3Cell" if on else "l3CellOff")

    def _ticked(self, key, on):
        if self._building:
            return
        self.state.selection[key] = bool(on)
        self._paint_row(key)
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

    def leave(self, forward):
        self.settle()
        return super(PhotosScreen, self).leave(forward)

    def _all_clicked(self):
        self._settle_later.stop()
        value = self.all_box.isChecked()
        self._building = True
        for key, (box, _frame, _labels) in self.rows.items():
            box.setChecked(value)
            self.state.selection[key] = value
            self._paint_row(key)
        self._building = False
        self.state.refresh_analysis()
        self.update_summary()
        self.wizard.refresh_buttons()

    def update_summary(self):
        analysis = self.state.analysis
        total = len(self.rows)
        picked = len(self.state.selected_flights())
        if self.all_box is not None:
            self.all_box.blockSignals(True)
            self.all_box.setChecked(bool(total) and picked == total)
            self.all_box.blockSignals(False)
        if analysis is None or not picked:
            self.summary.setText(tr("l3.ui.selected_none"))
            return
        self.summary.setText(plural("l3.ui.selected", total, n=picked, total=total, shots=fmt_count(analysis.summary.pairs)))


# --------------------------------------------------------------------------------------------------
# 2. Checks
# --------------------------------------------------------------------------------------------------

LEVEL_GLYPH = {checks.ERROR: "stop", checks.WARNING: "warn", checks.INFO: "note"}
LEVEL_NAME = {checks.ERROR: "l3.ui.level_error", checks.WARNING: "l3.ui.level_warning", checks.INFO: "l3.ui.level_note"}


class FindingRow(object):
    """One line of the list of findings: the mark, a few words, the number, and the "i" with the explanation. The widgets sit in the
    columns of the grid of the screen, so the numbers line up."""

    def __init__(self, grid, row, finding):
        self.glyph = Glyph(LEVEL_GLYPH[finding.level])
        self.glyph.setAccessibleName(tr(LEVEL_NAME[finding.level]))
        self.label = ui.label(finding.short(), "l3Cell", wrap=False)
        self.count = ui.label(finding.count_text(), "l3Count", wrap=False)
        self.count.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.info = InfoButton(finding.tip())
        grid.addWidget(self.glyph, row, 0)
        grid.addWidget(self.label, row, 1)
        grid.addWidget(self.count, row, 2)
        grid.addWidget(self.info, row, 3)
        for widget in (self.glyph, self.label, self.count):
            widget.setToolTip(self.info.toolTip())


class ChecksScreen(Screen):
    step = S.CHECKS
    name_key = "l3.ui.step_checks"

    def __init__(self, wizard):
        super(ChecksScreen, self).__init__(wizard)
        layout = page_layout(self)
        layout.addWidget(title(tr("l3.ui.checks_title")))
        head = QtWidgets.QHBoxLayout()
        head.setSpacing(theme.PAD + 4)
        self.verdict_glyph = Glyph("pending", scale=2.0)
        self.verdict = ui.label("", "l3VerdictReady", wrap=False)
        head.addWidget(self.verdict_glyph)
        head.addWidget(self.verdict)
        head.addStretch(1)
        layout.addLayout(head)
        strip = QtWidgets.QHBoxLayout()
        strip.setSpacing(theme.PAD)
        self.tiles = {}
        for key in ("shots", "flights", "left", "right", "tilt"):
            frame, number, _caption = ui.tile(tr("l3.ui.tile_" + key))
            self.tiles[key] = number
            strip.addWidget(frame, 1)
        layout.addLayout(strip)
        self.page = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(self.page)
        outer.setContentsMargins(0, 0, theme.PAD, 0)
        outer.setSpacing(theme.PAD)
        self.rows = QtWidgets.QGridLayout()
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setHorizontalSpacing(theme.PAD + 4)
        self.rows.setVerticalSpacing(theme.PAD + 4)
        self.rows.setColumnStretch(4, 1)
        outer.addLayout(self.rows)
        self.tail = QtWidgets.QVBoxLayout()
        self.tail.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(self.tail)
        self.scroll = ListScroll(self.page, preferred=600)
        layout.addWidget(self.scroll)
        layout.addStretch(1)
        self.finding_rows = []
        self.passed = None

    def enter(self):
        self.state.refresh_analysis()
        self.rebuild()

    def rebuild(self):
        clear_layout(self.rows)
        clear_layout(self.tail)
        self.finding_rows = []
        self.passed = None
        analysis = self.state.analysis
        if analysis is None:
            self.verdict.setText(tr("l3.ui.checks_none"))
            self.verdict_glyph.set_kind("pending")
            for number in self.tiles.values():
                number.setText("-")
            return
        errors = len(analysis.blocking)
        warnings = sum(1 for f in analysis.findings if f.level == checks.WARNING)
        if errors:
            self.verdict.setText(tr("l3.ui.verdict_block", n=errors))
            restyle(self.verdict, "l3VerdictBlock")
            self.verdict_glyph.set_kind("stop")
        elif warnings:
            self.verdict.setText(tr("l3.ui.verdict_check", n=warnings))
            restyle(self.verdict, "l3VerdictCheck")
            self.verdict_glyph.set_kind("warn")
        else:
            self.verdict.setText(tr("l3.ui.verdict_ready"))
            restyle(self.verdict, "l3VerdictReady")
            self.verdict_glyph.set_kind("ok")
        self._fill_tiles(analysis)
        for index, finding in enumerate(analysis.findings):
            self.finding_rows.append(FindingRow(self.rows, index, finding))
        passed = analysis.passed
        if passed:
            body = QtWidgets.QWidget()
            inner = QtWidgets.QGridLayout(body)
            inner.setContentsMargins(0, 0, 0, 0)
            inner.setHorizontalSpacing(theme.PAD + 4)
            inner.setVerticalSpacing(theme.PAD + 4)
            inner.setColumnStretch(5, 1)
            inner.setColumnMinimumWidth(2, SPACE * 3)
            half = (len(passed) + 1) // 2                       # two columns: the list stays short
            for index, key in enumerate(passed):
                glyph = Glyph("ok")
                glyph.setAccessibleName(tr("l3.ui.level_ok"))
                row, base = index % half, (index // half) * 3
                inner.addWidget(glyph, row, base)
                inner.addWidget(ui.label(tr("l3.pass." + key), "l3Cell", wrap=False), row, base + 1)
            self.passed = Disclosure(plural("l3.ui.passed", len(passed)), body)
            self.tail.addWidget(self.passed)

    def _fill_tiles(self, analysis):
        s = analysis.summary
        self.tiles["shots"].setText(fmt_count(s.pairs))
        self.tiles["flights"].setText(str(s.flights))
        for key, lens in (("left", "L"), ("right", "R")):
            self.tiles[key].setText(s.serials.get(lens) or "-")
            self.tiles[key].setStyleSheet("font-size: 11pt;")
        if s.tilt_min is None:
            tilt = "-"
        elif round(s.tilt_min, 1) == round(s.tilt_max, 1):
            tilt = tr("l3.ui.tilt_value", deg=i18n.fmt_number(s.tilt_min, 1))
        else:
            tilt = tr("l3.ui.tilt_range", low=i18n.fmt_number(s.tilt_min, 1), high=i18n.fmt_number(s.tilt_max, 1))
        self.tiles["tilt"].setText(tilt)


# --------------------------------------------------------------------------------------------------
# 3. Project
# --------------------------------------------------------------------------------------------------

class NumberField(TextField):
    """A number typed in a box: Enter or leaving the box applies it, a wrong text goes back to the last good value."""

    def __init__(self, value, low, high, decimals, on_change):
        super(NumberField, self).__init__()
        self.low, self.high, self.decimals, self.on_change = low, high, decimals, on_change
        self.value = value
        self.setAlignment(QtCore.Qt.AlignRight)
        self.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        self.set_value(value)
        self.editingFinished.connect(self._finished)

    def sizeHint(self):
        return QtCore.QSize(self.fontMetrics().horizontalAdvance("00000.000") + 28, max(36, self.fontMetrics().height() + 18))

    def minimumSizeHint(self):
        return self.sizeHint()

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


def with_info(widget_or_text, tip):
    """A label (or a widget) followed by its small "i"."""
    holder = QtWidgets.QWidget()
    row = QtWidgets.QHBoxLayout(holder)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(theme.PAD)
    first = ui.label(widget_or_text, "l3Cell", wrap=False) if isinstance(widget_or_text, str) else widget_or_text
    row.addWidget(first)
    row.addWidget(InfoButton(tip))
    row.addStretch(1)
    return holder


def form_label(text):
    return ui.label(text, "l3Cell", wrap=False)


class ProjectScreen(Screen):
    step = S.PROJECT
    name_key = "l3.ui.step_project"
    FIELDS = (("accuracy_fixed", "l3.ui.opt_acc_fixed", 0.001, 100.0, 3), ("accuracy_float", "l3.ui.opt_acc_float", 0.001, 100.0, 3),
              ("accuracy_single", "l3.ui.opt_acc_single", 0.001, 1000.0, 2))

    def __init__(self, wizard):
        super(ProjectScreen, self).__init__(wizard)
        layout = page_layout(self)
        layout.addWidget(title(tr("l3.ui.project_title")))
        options = self.state.options
        form = QtWidgets.QGridLayout()
        form.setHorizontalSpacing(SPACE * 2)
        form.setVerticalSpacing(SPACE)
        form.setColumnStretch(1, 1)
        self.fields = {}

        self.name = TextField(options.chunk_name)
        self.name.textEdited.connect(self._name_edited)
        form.addWidget(form_label(tr("l3.ui.opt_name")), 0, 0)
        form.addWidget(self.name, 0, 1)

        self.frame = Segmented([("wgs84", tr("l3.ui.frame_wgs84")), ("etrs89", tr("l3.ui.frame_etrs89"))])
        self.frame.changed.connect(lambda key: self._set("frame", key))
        form.addWidget(with_info(tr("l3.ui.opt_frame"), tr("l3.ui.opt_frame_info")), 1, 0)
        form.addWidget(self.frame, 1, 1, QtCore.Qt.AlignLeft)

        chain = QtWidgets.QHBoxLayout()
        chain.setSpacing(theme.PAD)
        self.boxes = {}
        for index, key in enumerate(("align", "optimize", "dem", "ortho")):
            if index:
                chain.addWidget(Arrow())
            box = QtWidgets.QCheckBox(tr("l3.ui.opt_step_" + key))
            box.toggled.connect(lambda on, k=key: self._step(k, bool(on)))
            self.boxes[key] = box
            chain.addWidget(box)
        chain.addSpacing(SPACE)
        self.pixel_label = form_label(tr("l3.ui.opt_pixel"))
        self.fields["pixel_cm"] = NumberField(options.pixel_cm, 0.5, 500.0, 1, lambda v: self._set("pixel_cm", v))
        chain.addWidget(self.pixel_label)
        chain.addWidget(self.fields["pixel_cm"])
        chain.addStretch(1)
        form.addWidget(form_label(tr("l3.ui.opt_after")), 2, 0)
        form.addLayout(chain, 2, 1)

        quick = QtWidgets.QHBoxLayout()
        quick.setSpacing(theme.PAD)
        self.quick = QtWidgets.QCheckBox(tr("l3.ui.opt_quick"))
        self.quick.toggled.connect(lambda v: self._set("quick_test", bool(v)))
        quick.addWidget(self.quick)
        self.quick_info = InfoButton(tr("l3.ui.opt_quick_info"))
        quick.addWidget(self.quick_info)
        quick.addStretch(1)
        form.addLayout(quick, 3, 1)
        layout.addLayout(form)

        body = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(body)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(SPACE)
        grid.setVerticalSpacing(theme.PAD)
        for row, (key, text_key, low, high, decimals) in enumerate(self.FIELDS):
            grid.addWidget(form_label(tr(text_key)), row, 0)
            field = NumberField(getattr(options, key), low, high, decimals, lambda v, k=key: self._set(k, v))
            self.fields[key] = field
            grid.addWidget(field, row, 1)
        grid.setColumnMinimumWidth(2, SPACE * 3)
        z_info = with_info(tr("l3.ui.opt_zshift"), tr("l3.ui.opt_zshift_info"))
        grid.addWidget(z_info, 0, 3)
        self.fields["z_shift"] = NumberField(options.z_shift, -50.0, 50.0, 3, lambda v: self._set("z_shift", v))
        grid.addWidget(self.fields["z_shift"], 0, 4)
        r_info = with_info(tr("l3.ui.opt_rot_acc"), tr("l3.ui.opt_rot_info"))
        grid.addWidget(r_info, 1, 3)
        self.fields["rotation_accuracy"] = NumberField(options.rotation_accuracy, 0.5, 180.0, 1, lambda v: self._set("rotation_accuracy", v))
        grid.addWidget(self.fields["rotation_accuracy"], 1, 4)
        self.fixed_cal = QtWidgets.QCheckBox(tr("l3.ui.opt_fixed_cal"))
        self.fixed_cal.toggled.connect(lambda v: self._set("calibration_fixed", bool(v)))
        self.unpaired = QtWidgets.QCheckBox(tr("l3.ui.opt_unpaired"))
        self.unpaired.toggled.connect(lambda v: self._set("include_unpaired", bool(v)))
        grid.addWidget(self.fixed_cal, 2, 3, 1, 2)
        grid.addWidget(self.unpaired, 3, 3, 1, 2)
        grid.setColumnStretch(5, 1)
        self.advanced = Disclosure(tr("l3.ui.opt_advanced"), body)
        layout.addWidget(self.advanced)
        order = [self.quick_info, self.advanced.head, self.fields["accuracy_fixed"], self.fields["accuracy_float"], self.fields["accuracy_single"],
                 z_info.findChildren(InfoButton)[0], self.fields["z_shift"], r_info.findChildren(InfoButton)[0], self.fields["rotation_accuracy"],
                 self.fixed_cal, self.unpaired]
        for before, after in zip(order, order[1:]):
            QtWidgets.QWidget.setTabOrder(before, after)                   # the keyboard follows the screen, not the order of creation
        layout.addStretch(1)
        self.load()

    def load(self):
        options = self.state.options
        self.name.blockSignals(True)
        self.name.setText(options.chunk_name)
        self.name.blockSignals(False)
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
        ortho = bool(steps.get("ortho"))
        self.fields["pixel_cm"].setEnabled(ortho)
        self.pixel_label.setEnabled(ortho)

    def enter(self):
        self.load()

    def _name_edited(self, text):
        self.state.options.chunk_name = text
        self.wizard.refresh_buttons()

    def _set(self, key, value):
        self.state.set_option(key, value)
        self.wizard.refresh_buttons()

    def _step(self, key, value):
        self.state.set_step(key, value)
        self.sync_steps()
        self.wizard.refresh_buttons()

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
    """The widgets of one line of the list: mark, name, key figure, "i", and under them the bar of the step that is running."""

    def __init__(self, grid, row, key, name):
        self.key = key
        self.glyph = Glyph("pending")
        self.name = ui.label(name, "l3Cell", wrap=False)
        self.figure = ui.label("", "l3Muted", wrap=False)
        self.info = InfoButton("")
        self.info.setVisible(False)
        self.bar = thin_bar()
        self.bar.setVisible(False)
        grid.addWidget(self.glyph, row * 2, 0, QtCore.Qt.AlignVCenter)
        grid.addWidget(self.name, row * 2, 1)
        grid.addWidget(self.figure, row * 2, 2)
        grid.addWidget(self.info, row * 2, 3)
        grid.addWidget(self.bar, row * 2 + 1, 1, 1, 3)

    def set(self, kind, figure="", tip="", progress=None, bad=False):
        self.glyph.set_kind(kind)
        self.figure.setText(figure)
        restyle(self.figure, "l3Bad" if bad else "l3Muted")
        self.info.setVisible(bool(tip))
        if tip:
            self.info.set_text(tip)
        self.bar.setVisible(progress is not None)
        if progress is not None:
            self.bar.setValue(int(progress * 1000))
        restyle(self.name, "l3CellOff" if kind == "pending" else "l3Cell")


class RunScreen(Screen):
    step = S.RUN
    name_key = "l3.ui.step_run"

    def __init__(self, wizard):
        super(RunScreen, self).__init__(wizard)
        layout = page_layout(self)
        layout.addWidget(title(tr("l3.ui.run_title")))
        head = QtWidgets.QHBoxLayout()
        head.setSpacing(theme.PAD + 4)
        self.head_glyph = Glyph("pending", scale=2.0)
        self.headline = ui.label("", "l3VerdictRun", wrap=False)
        head.addWidget(self.head_glyph)
        head.addWidget(self.headline)
        head.addStretch(1)
        layout.addLayout(head)
        self.note = ui.label("", "l3Muted")
        self.note.setVisible(False)
        layout.addWidget(self.note)
        self.page = QtWidgets.QWidget()
        self.grid = QtWidgets.QGridLayout(self.page)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(theme.PAD + 4)
        self.grid.setVerticalSpacing(theme.PAD + 4)
        self.grid.setColumnStretch(4, 1)
        layout.addWidget(self.page)
        layout.addSpacing(theme.PAD)
        self.next_line = ui.label("", "l3Cell")
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
        restyle(self.headline, "l3VerdictRun")
        self.head_glyph.set_kind("running")
        self.note.setText("")
        self.note.setVisible(False)
        self.next_line.setText("")
        self._show_progress(0.0, "")
        self.state.result = None
        threaded = bool(getattr(backend, "threaded", False))
        self.wizard.run_job(lambda progress, cancelled: backend.run(plan, steps, progress, cancelled), self._progress, self._done, "run", threaded=threaded)

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
                low, high = self._spans[index][1], self._spans[index][2]
                line.set("running", text, progress=max(0.0, min(1.0, (fraction - low) / max(1e-9, high - low))))
            else:
                line.set("pending")

    def _progress(self, task):
        self._show_progress(task.fraction, task.text)

    def _done(self, task):
        self._started = True
        if task.error is not None:
            for line in self.lines:
                if line.glyph.kind() == "running":
                    line.set("stop")
            self._headline("l3VerdictBlock", "stop", tr("l3.ui.run_failed"))
            self.note.setText(str(task.error))
            self.note.setVisible(True)
            return
        result = task.result
        self.state.result = result
        if result is None:
            return
        self._fill_result(result)
        name = result.chunk_label or ""
        if result.cancelled or task.was_cancelled:
            self._headline("l3VerdictCheck", "warn", tr("l3.ui.run_cancelled"))
        elif result.ok:
            self._headline("l3VerdictReady", "ok", tr("l3.ui.run_ok", name=name))
            self.next_line.setText(tr("l3.ui.run_next_aligned" if result.alignment is not None else "l3.ui.run_next_align"))
        else:
            self._headline("l3VerdictCheck", "warn", tr("l3.ui.run_problem", name=name))

    def _headline(self, style, glyph, text):
        self.headline.setText(text)
        restyle(self.headline, style)
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
                line.set("stop", group_digits(step.text), "\n".join(step.lines), bad=True)
