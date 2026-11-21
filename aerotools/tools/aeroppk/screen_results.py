"""Step 5 of the Aero-PPK wizard: the calculation, then the result. While it runs: the real steps and a plan of the photos that light up.
When it is done: one sentence that says whether the PPK is good, how the photos split (reliable, to check, not reliable, no position), the
base and the typical precision, what to do next and four buttons (Details, another base, store this solution, compare). The advanced view is
the Details window (`screen_details.py`); what uses the result is the Export step. Messages end in the window's banner."""

from ... import config, i18n, uikit
from ...qt import QtCore, QtGui, QtWidgets
from ...uikit import tokens
from . import antex, ownbase, pipeline, rtklib, runner, solutions, state as S, summary
from .screen_compare import CompareDialog, differs, stored_list
from .screen_details import DetailsDialog
from .stations import net
from .ui_common import OneAtATimeStack, Screen, card, glyph, glyph_kind, heading, tr
from .ui_views import PlanView

SECTION = "aeroppk"
SEGMENTS = (("good", "success"), ("check", "warning"), ("bad", "danger"), ("none", "muted"))   # the four levels: key, kit tone
VERDICT = {summary.GOOD: ("good", "ok", "success"), summary.CHECK: ("check", "warn", "warning"), summary.BAD: ("bad", "stop", "danger")}


def _fill_color(tone):
    """The kit colour of a level (read when painting, so a skin switch is followed)."""
    return {"success": tokens.SUCCESS, "warning": tokens.WARNING, "danger": tokens.DANGER, "muted": tokens.TEXT_DISABLED}[tone]


class StackedBar(QtWidgets.QWidget):
    """A thin horizontal bar cut in parts, one per level, as wide as their count. Only kit colours."""

    def __init__(self, parent=None):
        super(StackedBar, self).__init__(parent)
        self._counts = [0, 0, 0, 0]
        self.setFixedHeight(uikit.px(tokens.M))
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

    def set_counts(self, counts):
        self._counts = [max(0, int(c)) for c in counts]
        self.update()

    def counts(self):
        return list(self._counts)

    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        rect = QtCore.QRectF(self.rect())
        radius = rect.height() / 2.0
        path = QtGui.QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        p.setClipPath(path)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor(tokens.BORDER))
        p.drawRect(rect)
        total = sum(self._counts)
        x = rect.left()
        for count, (_key, tone) in zip(self._counts, SEGMENTS):
            if not count or not total:
                continue
            width = max(2.0, rect.width() * count / float(total))
            p.setBrush(QtGui.QColor(_fill_color(tone)))
            p.drawRect(QtCore.QRectF(x, rect.top(), width, rect.height()))
            x += width
        p.end()


class ResultsScreen(Screen):
    step = S.RESULTS
    name_key = "aeroppk.step_results"

    def __init__(self, wizard):
        super(ResultsScreen, self).__init__(wizard)
        self.mode = "idle"                     # idle, running, done, failed
        self.stages = {}
        self.stage_order = []
        self.lit_total = 0
        self.errors = []
        self.options_used = None               # the `rtklib.Options` of the last calculation (the Details window lists them)
        self.details = None
        self.compare_window = None
        self.pages = OneAtATimeStack()                    # (only the page on show counts: the wrapped text of the running page made the whole window taller)
        self.content.addWidget(self.pages, 1)
        self.pages.addWidget(self._build_running())
        self.pages.addWidget(self._build_done())
        self.pages.addWidget(self._build_failed())
        self.pages.setCurrentIndex(0)

    # ------------------------------------------------------------------ construction

    def _build_running(self):
        page = QtWidgets.QWidget()
        row = uikit.hbox(spacing=tokens.L, parent=page)
        self.plan_run = PlanView()
        row.addWidget(self.plan_run, 3)
        frame, box = card()
        frame.setMinimumWidth(uikit.px(tokens.POPOVER_W))
        box.addLayout(heading(tr("aeroppk.res.running")))
        self.progress_steps = uikit.StepProgress()
        box.addWidget(self.progress_steps)
        self.run_status = uikit.label("", "small", "muted")
        box.addWidget(self.run_status)
        box.addStretch(1)
        row.addWidget(frame, 2)
        return page

    def _build_failed(self):
        from .ui_common import one_line
        self.failed_state = uikit.EmptyState(tr("aeroppk.res.no_result"), tr("aeroppk.res.failed_hint"), "error")
        one_line(self.failed_state)
        self.failed_state.set_card(True)
        return self.failed_state

    def _build_done(self):
        """The simple page: the verdict, the bar and its four counters, two facts, one line of advice, four buttons."""
        frame, box = card(tokens.L)
        frame.setMinimumWidth(uikit.px(tokens.POPOVER_W * 2))
        frame.setMaximumWidth(uikit.px(tokens.POPOVER_W * 3))
        head = uikit.hbox(spacing=tokens.M)
        self.verdict_glyph = glyph("ok")                                # the verdict is an icon + a colour + words
        self.verdict_glyph.setFixedSize(uikit.px(tokens.ICON_XL), uikit.px(tokens.ICON_XL))
        head.addWidget(self.verdict_glyph)
        self.verdict = uikit.ElidedLabel("", "lead", fit=True)
        head.addWidget(self.verdict, 1)
        box.addLayout(head)
        self.bar = StackedBar()
        box.addWidget(self.bar)
        counters = uikit.hbox(spacing=tokens.M)
        self.counters = {}
        for key, tone in SEGMENTS:
            stat = uikit.Stat("-", tr("aeroppk.result.c_" + key), tone)
            self.counters[key] = stat
            counters.addWidget(stat, 1)
        box.addLayout(counters)
        self.fact_base = uikit.ElidedLabel("", "body", fit=True)
        box.addWidget(self.fact_base)
        precision = uikit.hbox(spacing=tokens.S)
        self.fact_precision = uikit.ElidedLabel("", "body", fit=True)
        precision.addWidget(self.fact_precision)
        self.precision_info = uikit.InfoButton(tr("aeroppk.result.typical_tip"))
        precision.addWidget(self.precision_info)
        precision.addStretch(1)
        box.addLayout(precision)
        self.advice = uikit.ElidedLabel("", "strong", fit=True)
        box.addWidget(self.advice)
        buttons = uikit.hbox(spacing=tokens.S)
        self.details_button = uikit.Button(tr("aeroppk.result.b_details"), "secondary", handler=self.open_details)
        self.other_button = uikit.Button(tr("aeroppk.result.b_other"), "secondary", handler=self.try_another)
        self.other_button.setToolTip(tr("aeroppk.result.b_other_tip"))
        self.store_button = uikit.Button(tr("aeroppk.result.b_store"), "secondary", handler=self.store)
        self.store_button.setToolTip(tr("aeroppk.result.b_store_tip"))
        self.compare_button = uikit.Button(tr("aeroppk.result.b_compare"), "secondary", handler=self.open_compare)
        for button in (self.details_button, self.other_button, self.store_button, self.compare_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        box.addLayout(buttons)
        holder = QtWidgets.QWidget()                                    # the card sits in the upper middle of the page
        column = uikit.vbox(parent=holder)
        column.addStretch(1)
        row = uikit.hbox()
        row.addStretch(1)
        row.addWidget(frame, 6)
        row.addStretch(1)
        column.addLayout(row)
        column.addStretch(2)
        return holder

    # ------------------------------------------------------------------ the footer of the window

    def say(self, kind, text, title=None, details=None):
        """A message of this step: the banner of the window (the one place messages end up)."""
        self.wizard.show_banner(kind, text, title=title, details=details)

    def show_next(self):
        return self.mode == "done"

    def show_back(self):
        return True

    def busy(self):
        return self.mode == "running"

    def next_text(self):
        return tr("aeroppk.result.continue")

    def next_enabled(self):
        return self.mode == "done"

    def can_leave(self, forward=True):
        """Going on with a result that is not reliable asks first (the Export step does not ask again)."""
        if forward and self.mode == "done" and summary.verdict(self.state.results) == summary.BAD:
            if not self.wizard.confirm(tr("aeroppk.result.bad_ask"), ok=tr("aeroppk.result.export_anyway")):
                return False
            self.wizard.export_asked = True
        return True

    # ------------------------------------------------------------------ starting

    def enter(self):
        if getattr(self.wizard, "pending_start", False):
            self.wizard.pending_start = False
            self.start()
        else:
            self._show_mode()
            if self.mode == "done":
                self._update_buttons()

    def _show_mode(self):
        self.pages.setCurrentIndex({"done": 1, "failed": 2}.get(self.mode, 0))
        self.wizard.refresh_buttons()

    def start(self):
        """Build the bases, then run the calculation in a worker thread."""
        state = self.state
        self.wizard.clear_banner()
        self.wizard.export_asked = False
        for window in (self.details, self.compare_window):
            if window is not None:
                window.hide()
        programs = rtklib.find_programs(config.get(SECTION, "rtklib_dir", None))
        if not programs.get("rnx2rtkp"):
            self._fail([tr("aeroppk.engine_missing")])
            return
        site = None
        site_info = None
        from . import flights as flight_tools
        site_info = flight_tools.site_of(state.flights)
        if site_info:
            site = site_info[:2]
        flights = list(state.flights)
        for flight in flights:
            flight["antenna_z"] = None if state.choice().antenna_known else state.antenna_z
        window = runner.union_window(flights, 60.0)
        try:
            if state.mode == S.MODE_OWN:
                state.own_base = ownbase.make_base(state.own, state.own_position, site, window)
        except pipeline.PipelineError as exc:
            self._fail([str(exc)])
            return
        bases = state.prepared_bases()
        if not bases:
            self._fail([tr("aeroppk.res.no_base")])
            return
        options = rtklib.Options()
        self.options_used = options
        antex_wanted = bool(config.get(SECTION, "base_antenna_correction", False))
        antex_extra = str(config.get(SECTION, "base_antenna_extra", ""))          # "ngs20": also read the NGS calibration file
        self.mode = "running"
        state.results = []
        self.errors = []
        self._build_stages(bases)
        points = [e.onboard[:2] for f in flights for e in f["events"] if e.onboard]
        self.plan_run.set_points(points)
        self.lit_total = len(points)
        self.run_status.setText("")
        self._show_mode()

        def work(post, cancelled):
            opts = options
            if antex_wanted:
                try:
                    opts.antex_file = antex.ensure(cancelled=cancelled)
                    if antex_extra in antex.SOURCES and antex_extra != antex.DEFAULT_SOURCE:
                        opts.antex_extra = [antex.ensure(cancelled=cancelled, source=antex_extra)]
                except net.NetError:
                    pass
            return runner.calculate(
                flights, bases, site, programs, opts, post, cancelled,
            )
        self.wizard.run_job(work, self._message, self._finished, "calc", text=tr("aeroppk.res.st_prepare"))

    def _build_stages(self, bases):
        entries = [("prepare", tr("aeroppk.res.st_prepare"))]
        for base in bases:
            entries.append(("solve:" + base.name, tr("aeroppk.res.st_solve", code=base.name)))
        entries += [("combine", tr("aeroppk.res.st_combine")), ("check", tr("aeroppk.res.st_check"))]
        self.progress_steps.set_steps(entries)
        self.stages = dict((key, key) for key, _t in entries)
        self.stage_order = [k for k, _t in entries]
        self._mark("prepare", "running")

    def _mark(self, key, kind):
        """Put `key` in state `kind` and every stage before it done."""
        if key not in self.stage_order:
            return
        index = self.stage_order.index(key)
        for earlier in self.stage_order[:index]:
            self.progress_steps.set_state(earlier, "ok")
        self.progress_steps.set_state(key, kind)

    def _message(self, kind, fields):
        if kind == "fraction":
            self.plan_run.set_lit(int(fields["value"] * self.lit_total))
        elif kind == "stage":
            if fields["name"] == "solve" and fields.get("code"):
                self._mark("solve:" + fields["code"], "running")
            elif fields["name"] == "combine":
                self._mark("combine", "running")
        elif kind == "run":
            key = "solve:" + fields["code"]
            if key in self.stages:
                self.progress_steps.set_state(key, "ok")
        elif kind == "flight":
            self.run_status.setText(tr("aeroppk.res.flight", **fields) if fields.get("n", 1) > 1 else "")

    def _finished(self, job):
        if job.state == "stopped":
            self.mode = "idle"
            self.run_status.setText(tr("aeroppk.stopped"))
            self.wizard.go(S.BASE, force=True)                      # the calculation was cancelled: back to where it was asked for
            self.wizard.set_status(tr("aeroppk.stopped"))
            return
        if job.state == "failed":
            self._fail([job.error], job.trace)
            return
        outcome = job.result
        self.state.results = outcome["results"]
        self.errors = outcome["errors"]
        if not self.state.results:
            self._fail([e.split(":", 2)[-1] for e in self.errors] or ["?"])
            return
        self._mark("check", "running")
        self._mark("check", "ok")
        self.mode = "done"
        self._render()
        self._show_mode()

    def _fail(self, errors, details=None):
        """The calculation did not give a result: the error in the banner (with Retry), and the page says so."""
        self.mode = "failed"
        self.errors = errors
        self._show_mode()
        self.wizard.show_banner("danger", tr("aeroppk.failed", error="; ".join(errors)), action=(tr("ui.retry"), self.start),
                                details=details or None, closable=True)

    # ------------------------------------------------------------------ showing the result

    def _render(self):
        """Draw the simple page from the results (also after the Details window combined the bases again)."""
        self.wizard.export_asked = False
        self._fill()
        self._update_buttons()
        self.wizard.refresh_buttons()

    def refresh_done(self):
        """The results changed under the page (a base was taken out in the Details window): draw it again."""
        if self.mode == "done" and self.state.results:
            self._render()
            self._show_mode()

    def refresh_skin(self):
        """The skin changed: the Details window draws the status icons of its bases again."""
        if self.details is not None:
            self.details.refresh_skin()

    @staticmethod
    def _pct(count, total):
        return int(round(100.0 * count / total)) if total else 0

    def base_text(self):
        """"Base: CRTE, 12.6 km", "Base: 3 stations" or "Base: your base"."""
        if self.state.mode == S.MODE_OWN:
            return tr("aeroppk.result.base_own")
        codes = solutions.base_codes(self.state.results)
        if len(codes) > 1:
            return tr("aeroppk.result.base_many", n=len(codes))
        rows = summary.base_rows(self.state.results)
        km = rows[0]["baseline_km"] if rows else 0.0
        return tr("aeroppk.result.base_one", code=codes[0] if codes else "-", km=i18n.fmt_number(km, 1))

    def verdict_info(self):
        """(the sentence, the mark, the kit tone, the key word good / check / bad) of the results on show."""
        results = self.state.results
        photos = summary.figures(results)["photos"]
        ok = summary.level_counts(results)[0]
        key, kind, tone = VERDICT[summary.verdict(results)]
        return tr("aeroppk.result." + key, ok=ok, n=photos, look=photos - ok), kind, tone, key

    def _fill(self):
        results = self.state.results
        totals = summary.figures(results)
        counts = summary.level_counts(results)
        photos = totals["photos"]
        text, kind, tone, key = self.verdict_info()
        self.verdict.setFullText(text)
        uikit.set_tone(self.verdict, tone)
        self.verdict_glyph.set_kind(glyph_kind(kind))
        self.bar.set_counts(counts)
        shown = {}
        for (name, _tone), count in zip(SEGMENTS, counts):
            shown[name] = tr("aeroppk.result.count", n=count, pct=self._pct(count, photos))
            self.counters[name].set_value(shown[name], _tone if count else "muted")
        self.bar.setAccessibleName(tr("aeroppk.result.bar_tip", **shown))
        self.bar.setToolTip(tr("aeroppk.result.bar_tip", **shown))
        self.fact_base.setFullText(self.base_text())
        typ = summary.typical(results)
        if typ:
            self.fact_precision.setFullText(tr("aeroppk.result.typical", h=i18n.fmt_number(100.0 * typ["h"], 1), v=i18n.fmt_number(100.0 * typ["v"], 1)))
        else:
            self.fact_precision.setFullText(tr("aeroppk.result.typical_none"))
        self.advice.setFullText(tr("aeroppk.result.advice_" + key))

    def _update_buttons(self):
        """Compare is offered when a stored solution differs from the one on show."""
        can = self.mode == "done" and bool(self.state.results) and differs(self.wizard)
        self.compare_button.setEnabled(can)
        self.compare_button.setToolTip("" if can else tr("aeroppk.result.b_compare_none"))
        self.store_button.setEnabled(self.mode == "done" and bool(self.state.results))

    # ------------------------------------------------------------------ the four buttons

    def open_details(self):
        if not self.state.results:
            return
        if self.details is None:
            self.details = DetailsDialog(self.wizard, self)
        self.details.show_results()

    def try_another(self):
        """Back to the choice of the mode. The results stay until a new calculation starts."""
        self.wizard.go(S.MODE, force=True)

    def store(self):
        """Keep the results on show in this window, to compare them with another run."""
        if not self.state.results:
            return
        stored = stored_list(self.wizard)
        sol = solutions.snapshot(self.state)
        if any(solutions.same(old, sol) for old in stored):
            self.say("info", tr("aeroppk.result.already_stored"))
            return
        stored.append(sol)
        self.say("info", tr("aeroppk.result.stored", name=sol.name))
        self._update_buttons()

    def open_compare(self):
        if self.compare_window is None:
            self.compare_window = CompareDialog(self.wizard, self._update_buttons)
        self.compare_window.show_all()
