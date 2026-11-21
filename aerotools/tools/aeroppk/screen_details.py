"""The Details window of the Aero-PPK results: a separate window, opened from the Result screen, for the user who wants to judge the solution
before trusting it. It holds the figures (photos, fixed, float, precision), the checks and notes, the spread of the precision, the bases and
the facts of the selected one, the plan coloured by level and the processing settings that were used. With several bases it also holds the
averaged table and the choice of the combination, and a base can be taken out there (the Result screen is drawn again afterwards)."""

from ... import i18n, uikit
from ...qt import QtCore, QtWidgets
from ...uikit import shell, style, tokens
from . import pipeline, rtklib, state as S, summary
from .screen_export import counted_stations
from .stations import catalog, rgp
from .ui_common import card, clear_layout, glyph, glyph_kind, heading, one_line, tr
from .ui_views import PlanView
from .widgets import KeyValues, status_icon, tile

TONES = {"good": "success", "grey": "muted", "": None}            # the tone words of `summary.tone` in kit tones
VIEWS = ("summary", "plan", "settings")


def warning_text(name, fields):
    """Warning key + fields -> sentence."""
    key = "aeroppk.warn." + name
    if i18n.t(key) == key:
        return str(name)
    return i18n.t(key, code=fields.get("code", ""), n=fields.get("n", ""))


def invalidate_all(layout):
    """Forget the sizes cached by a layout and by every layout inside it (the layouts of the widgets it holds too)."""
    for index in range(layout.count()):
        item = layout.itemAt(index)
        inner = item.layout() or (item.widget().layout() if item.widget() is not None else None)
        if inner is not None:
            invalidate_all(inner)
    layout.invalidate()


class _FlatLayout(QtWidgets.QVBoxLayout):
    """The layout of the window itself: it says it has no height for width (the wrapped lines inside still wrap at the width they get). A
    top-level window whose layout has one is sized by the window system to that height, which under Qt 5 left the screen."""

    def hasHeightForWidth(self):
        return False


class DetailsDialog(QtWidgets.QDialog):
    """Not modal: the user keeps the Result screen and this window side by side. `show_results()` fills it from the wizard's results."""

    def __init__(self, wizard, screen):
        super(DetailsDialog, self).__init__(wizard)
        self.wizard = wizard
        self.screen = screen                    # the Result screen: it knows the notes of the calculation and the options that were used
        self.state = wizard.state
        self.setWindowTitle(tr("aeroppk.details.title"))
        self.setModal(False)
        self.rows = []
        self.selected = 0                       # index in `rows` of the base whose facts are shown
        self._silent = False
        area = shell.screen_area(wizard)
        self.resize(min(uikit.px(1060), area.width() - uikit.px(tokens.SCREEN_MARGIN)),
                    min(uikit.px(660), area.height() - uikit.px(tokens.SCREEN_MARGIN + tokens.XL)))
        outer = _FlatLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.root = QtWidgets.QWidget()
        self.root.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        style.apply(self.root)
        outer.addWidget(self.root)
        lay = uikit.vbox((tokens.XL,) * 4, tokens.L, parent=self.root)
        row = uikit.hbox(spacing=tokens.L)
        left = uikit.vbox(spacing=tokens.S)
        head = uikit.hbox(spacing=tokens.L)
        self.rail = uikit.Segmented([("summary", tr("aeroppk.res.v_summary")), ("plan", tr("aeroppk.res.v_plan")),
                                     ("settings", tr("aeroppk.details.v_tab"))])
        self.rail.changed.connect(self._on_view)
        head.addWidget(self.rail)
        self.verdict_glyph = glyph("ok")                                # the verdict is an icon + a colour + words
        head.addWidget(self.verdict_glyph)
        self.verdict = uikit.label("", "lead")                          # it wraps when the window is narrow: the sentence is never cut
        head.addWidget(self.verdict, 1)
        left.addLayout(head)
        self.views = QtWidgets.QStackedWidget()
        self.views.addWidget(self._build_summary())
        self.plan = PlanView()
        self.views.addWidget(self.plan)
        self.views.addWidget(self._build_settings())
        left.addWidget(self.views, 1)
        row.addLayout(left, 1)
        row.addWidget(self._build_bases())
        lay.addLayout(row, 1)
        lay.addLayout(self._build_bottom())

    def _fit(self):
        """Never smaller than the content needs, never larger than the screen. Measured once the window is filled and polished: a part that is
        hidden by the filling (the averaged table of one base) still counted in a measure taken before, and Qt 5 then made the window as wide as
        that table and left the screen."""
        area = shell.screen_area(self.wizard)
        self.ensurePolished()
        for widget in self.findChildren(QtWidgets.QWidget):
            widget.ensurePolished()
            widget.updateGeometry()                                      # (a window that is not shown yet keeps the sizes its layouts and their items
        invalidate_all(self.layout())                                    # cached before the filling)
        self.layout().activate()
        need = self.layout().minimumSize()
        self.resize(min(max(self.width(), need.width()), area.width() - uikit.px(tokens.SCREEN_MARGIN)),
                    min(max(self.height(), need.height()), area.height() - uikit.px(tokens.SCREEN_MARGIN + tokens.XL)))

    # ------------------------------------------------------------------ construction

    def _build_summary(self):
        page = QtWidgets.QWidget()
        box = uikit.vbox(spacing=tokens.XS, parent=page)
        top = uikit.hbox(spacing=tokens.M)
        self.tile_photos = tile(tr("aeroppk.res.t_photos"))
        self.tile_fix = tile(tr("aeroppk.res.t_fix"), tr("aeroppk.res.t_fix_tip"))
        self.tile_float = tile(tr("aeroppk.res.t_float"), tr("aeroppk.res.t_float_tip"))
        self.tile_sd = tile(tr("aeroppk.res.t_sd"), summary.precision_tip())
        self.sd_info = self.tile_sd[3]
        for entry in (self.tile_photos, self.tile_fix, self.tile_float, self.tile_sd):
            top.addWidget(entry[0])
        top.addStretch(1)
        box.addLayout(top)
        box.addLayout(heading(tr("aeroppk.details.bins"), tr("aeroppk.details.bins_tip")))
        bins = uikit.hbox(spacing=tokens.M)
        self.bin_tiles = []
        for key in ("1", "2", "3", "none"):
            entry = tile(tr("aeroppk.details.bin_" + key))
            self.bin_tiles.append(entry)
            bins.addWidget(entry[0])
        bins.addStretch(1)
        box.addLayout(bins)
        self.checks_box = uikit.vbox(spacing=tokens.XS)
        box.addLayout(self.checks_box)
        self.notes_box = uikit.vbox(spacing=tokens.XS)
        box.addLayout(self.notes_box)
        box.addStretch(1)
        return page

    def _build_settings(self):
        """What the calculation used, as a plain list: label and value, read from the options the calculation was started with."""
        frame, box = card(tokens.S)
        box.addLayout(heading(tr("aeroppk.details.v_settings"), tr("aeroppk.details.settings_tip")))
        self.settings = KeyValues()
        for key in summary.SETTING_KEYS:
            self.settings.add_row(tr("aeroppk.details.o_" + key), "-", key)
        for key in ("frame", "heights"):                                # the frame of the positions is told here, not in the footer
            self.settings.add_row(tr("aeroppk.details.o_" + key), "-", key)
        box.addWidget(self.settings)
        box.addStretch(1)
        return frame

    def _build_bases(self):
        frame, box = card(tokens.S)
        frame.setFixedWidth(uikit.px(tokens.POPOVER_W + tokens.XL + tokens.S))
        box.addLayout(heading(tr("aeroppk.res.v_bases"), tr("aeroppk.res.bases_tip")))
        self.bases = uikit.DataTable([uikit.Column("base", tr("aeroppk.res.p_base"), stretch=True),
                                      uikit.Column("role", tr("aeroppk.res.p_role")),
                                      uikit.Column("fix", tr("aeroppk.res.t_fix"), align="right"),
                                      uikit.Column("km", tr("aeroppk.res.c_km"), align="right")], sortable=False)       # (the three small columns are as wide as their widest text)
        one_line(self.bases)
        self.bases.setMinimumHeight(uikit.px(tokens.CONTROL_H * 3))
        self.bases.selectionChanged.connect(self._on_base_selected)
        self.bases.table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.bases.table.customContextMenuRequested.connect(self._on_base_menu)
        box.addWidget(self.bases, 1)
        box.addWidget(uikit.Divider())
        self.g_base = KeyValues()
        for key in ("base", "role", "baseline", "fix", "used", "bias", "data", "device", "product"):
            self.g_base.add_row(tr("aeroppk.res.p_" + key), "-", key)
        box.addWidget(self.g_base)
        return frame

    def _build_bottom(self):
        bar = uikit.hbox(spacing=tokens.S)
        bar.addStretch(1)
        self.close_button = uikit.Button(tr("ui.close"), "secondary", handler=self.close)
        bar.addWidget(self.close_button)
        return bar

    # ------------------------------------------------------------------ showing the results

    def show_results(self):
        """Fill the window from the results of the wizard and bring it to the front."""
        self._render()
        self._fit()
        self.show()
        self.raise_()
        self.activateWindow()

    def _render(self):
        results = self.state.results
        if not results:
            self.hide()
            return
        self.rows = summary.base_rows(results)
        self.selected = min(self.selected, max(0, len(self.rows) - 1))
        self._fill_summary()
        self._fill_bases()
        self._fill_settings()
        self.plan.set_points([e.onboard[:2] for r in results for e in r.events if e.onboard] or
                             [(float(a), float(b)) for r in results for a, b in zip(r.cam["lat"], r.cam["lon"])])
        self.plan.set_levels(summary.levels(results))

    def refresh_skin(self):
        """The skin changed: the status icons of the bases are drawn again."""
        if self.rows and self.isVisible():
            self._fill_bases()


    def _fill_summary(self):
        results = self.state.results
        totals = summary.figures(results)
        text, kind, tone, _key = self.screen.verdict_info()
        self.verdict.setText(text)
        uikit.set_tone(self.verdict, tone)
        self.verdict_glyph.set_kind(glyph_kind(kind))
        photos = max(1, totals["photos"])
        float_pct = 100.0 * totals["float"] / photos
        none_pct = 100.0 * totals["none"] / photos
        self.tile_photos[1].setText(str(totals["photos"]))
        self.tile_fix[1].setText(i18n.fmt_number(totals["fix_pct"], 0 if totals["fix_pct"] in (0.0, 100.0) else 1) + " %")
        self.tile_float[1].setText(i18n.fmt_number(float_pct, 0 if float_pct in (0.0, 100.0) else 1) + " %")
        self.tile_sd[1].setText(("%s cm" % i18n.fmt_number(100.0 * totals["h_mean"], 1)) if totals["h_mean"] is not None else "-")
        self._tone(self.tile_fix[1], summary.tone("fix", totals["fix_pct"] if totals["photos"] else None))
        self._tone(self.tile_float[1], summary.tone("float", float_pct if totals["photos"] else None))
        self._tone(self.tile_sd[1], summary.tone("sd", totals["h_mean"]))
        tip = summary.precision_tip()
        if totals["h_mean"] is not None:
            tip += "\n" + tr("aeroppk.res.range", hmin=i18n.fmt_number(100 * totals["h_min"], 1), hmax=i18n.fmt_number(100 * totals["h_max"], 1),
                             v=i18n.fmt_number(100 * totals["v_mean"], 1))
        self.sd_info.set_text(tip)
        for entry, count in zip(self.bin_tiles, summary.precision_bins(results)):
            entry[1].setText(str(count))
        self._fill_checks(totals, float_pct, none_pct)
        clear_layout(self.notes_box)
        notes = [warning_text(n, f) for n, f in summary.warning_keys(results)]
        notes += [tr("aeroppk.warn.coverage", code=e.split(":")[1]) if e.startswith("no_data") else ":".join(e.split(":")[1:])
                  for e in self.screen.errors]
        for text in notes:
            self._add_line(self.notes_box, "note", text)


    @staticmethod
    def _tone(widget, tone):
        """Colour of a tile number: green for a good figure, plain otherwise (never red); the plain colour without a figure."""
        uikit.set_tone(widget, TONES.get(tone))

    @staticmethod
    def _add_line(box, kind, text, tip=""):
        """One line of a list: a mark (ok, warn, note, pending), the text, and an "i" when there is something to explain."""
        row = uikit.hbox(spacing=tokens.S)
        row.addWidget(glyph(kind), 0, QtCore.Qt.AlignTop)
        row.addWidget(uikit.label(text, "body"), 1)                         # a long line wraps (a kit wrapped label counts one line in the least size)
        if tip:
            row.addWidget(uikit.InfoButton(tip), 0, QtCore.Qt.AlignTop)
        holder = QtWidgets.QWidget()
        holder.setLayout(row)
        box.addWidget(holder)

    def _add_check(self, kind, text, tip=""):
        self._add_line(self.checks_box, kind, text, tip)

    def _fill_checks(self, totals, float_pct, none_pct):
        """What the client can check: the positions, the base and the drone's own position (for information)."""
        clear_layout(self.checks_box)
        results = self.state.results
        seen, names = set(), []
        for row in self.rows:
            if row["code"] not in seen:
                seen.add(row["code"])
                names.append("%s (%s km)" % (row["code"], i18n.fmt_number(row["baseline_km"], 1)))
        fix = i18n.fmt_number(totals["fix_pct"], 0 if totals["fix_pct"] in (0.0, 100.0) else 1)
        if summary.tone("fix", totals["fix_pct"]) == "good" and not totals["none"]:
            if len(names) <= 1:                                             # the tiles say it already: the line is for the one-base case
                self._add_check("ok", tr("aeroppk.res.c_fixed", fix=fix))
        else:
            self._add_check("note", tr("aeroppk.res.c_fixed_less", fix=fix, flt=i18n.fmt_number(float_pct, 0 if float_pct in (0.0, 100.0) else 1),
                                       none=i18n.fmt_number(none_pct, 0 if none_pct in (0.0, 100.0) else 1)))
        if len(names) == 1:                                                 # with several bases the panel on the right lists them
            good = all(row["status"] == "ok" for row in self.rows)
            self._add_check("ok" if good else "note", tr("aeroppk.res.c_base", codes=", ".join(names)))
        figures = summary.onboard_figures(results)
        cm = lambda value, digits=1: i18n.fmt_number(100.0 * value, digits)
        if figures:
            close = summary.tone("onboard", max(figures["offset_h"], abs(figures["offset_u"]))) == "good"
            self._add_check("ok" if close else "note", tr("aeroppk.res.c_onboard", n=figures["n"], oh=cm(figures["offset_h"]), ou=cm(figures["offset_u"]),
                                                          sh=cm(figures["spread_h"]), su=cm(figures["spread_u"])), tr("aeroppk.res.c_onboard_tip"))
        else:
            self._add_check("note", tr("aeroppk.res.no_drone"))

    def _fill_settings(self):
        """The processing settings the calculation started with (the defaults when it did not say)."""
        options = getattr(self.screen, "options_used", None) or rtklib.Options()
        for key, text in summary.settings_rows(options):
            self.settings.set_text(key, text)
        self.settings.set_text("frame", self._frame_text())
        self.settings.set_text("heights", tr("aeroppk.details.v_ellipsoidal"))

    def _frame_text(self):
        """The frame of the base coordinates, which the positions are in: the network's, or what the user said for an own base."""
        if self.state.mode == S.MODE_OWN:
            return tr("aeroppk.own.frame")
        return catalog.frames_text(counted_stations(self.state)) or rgp.FRAME_LABEL

    def _fill_bases(self):
        """The bases, one row each, in the panel on the right."""
        multi = len(self.state.results) > 1
        data, marks, tips = [], [], []
        for row in self.rows:
            name = tr("aeroppk.details.base_in_flight", code=row["code"], n=row["flight"] + 1) if multi else row["code"]
            data.append([name, tr("aeroppk.res.role_" + row["role"]), "%s %%" % i18n.fmt_number(row["fix_pct"], 0),
                         "%s km" % i18n.fmt_number(row["baseline_km"], 1)])
            marks.append("ok" if row["status"] == "ok" else "note")             # green when good, plain otherwise
            tips.append(self._status_tip(row))
        self._silent = True
        try:
            self.bases.set_rows(data)
            for r, (mark, tip) in enumerate(zip(marks, tips)):
                for column in range(self.bases.table.columnCount()):
                    item = self.bases.table.item(r, column)
                    if item is not None:
                        item.setToolTip(tip)
                        if column == 0:
                            item.setIcon(status_icon(mark))
            if 0 <= self.selected < len(data):
                self.bases.select_row(self.selected)
        finally:
            self._silent = False
        self._fill_grid()

    def _on_base_selected(self):
        if self._silent:
            return
        rows = self.bases.selected_rows()
        if rows:
            self._select(rows[0])

    def _on_base_menu(self, pos):
        table = self.bases.table
        item = table.item(table.rowAt(pos.y()), 0)
        if item is None:
            return
        index = item.data(QtCore.Qt.UserRole)
        self._select(index)

    def _select(self, index):
        if 0 <= index < len(self.rows) and index != self.selected:
            self.selected = index
            self._silent = True
            try:
                self.bases.select_row(index)
            finally:
                self._silent = False
            self._fill_grid()


    def showEvent(self, event):
        super(DetailsDialog, self).showEvent(event)

    def _status_tip(self, row):
        if row["role"] == summary.ROLE_ASIDE:
            return tr("aeroppk.res.tip_aside", why=tr("aeroppk.why_" + (row["reason"] or "bias")))
        if row["status"] == "warn":
            return tr("aeroppk.res.tip_weak")
        if row["status"] == "stop":
            return tr("aeroppk.why_no_fix")
        return tr("aeroppk.res.tip_ok")

    def _selected_row(self):
        return self.rows[self.selected] if 0 <= self.selected < len(self.rows) else None

    def _fill_grid(self):
        row = self._selected_row()
        g = self.g_base
        if row is None:
            for key in g.values:
                g.set_text(key, "-")
            return
        cm = lambda v: i18n.fmt_number(v * 100.0, 1) if v is not None else "-"
        g.set_text("base", row["code"])
        g.set_text("role", tr("aeroppk.res.role_" + row["role"]))
        g.set_text("baseline", "%s km" % i18n.fmt_number(row["baseline_km"], 2))
        g.set_text("fix", tr("aeroppk.res.fix_line", fix=i18n.fmt_number(row["fix_pct"], 1), flt=i18n.fmt_number(row["float_pct"], 1)))
        g.set_text("used", tr("aeroppk.res.used_line", used=row["used"], dropped=row["dropped"]))
        bn, be, bu = row["bias"]
        g.set_text("bias", "%s / %s / %s cm" % (cm(bn), cm(be), cm(bu)) if bn is not None else "-")
        g.set_text("data", tr("aeroppk.res.data_line", rate=i18n.fmt_number(row["interval"], 0), systems=row["systems"] or "-") if row["interval"] else (row["systems"] or "-"))
        g.set_text("device", "%s / %s" % (row["receiver"] or "-", row["antenna"] or "-"))
        g.set_text("product", row["product"] or "-")
        g.set_tone("fix", "success" if row["fix_pct"] >= summary.GOOD_FIX_PCT else None)

    def _on_view(self, key):
        self.views.setCurrentIndex(VIEWS.index(key))
