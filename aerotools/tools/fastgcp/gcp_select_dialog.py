"""Adjust window (Qt): which markers to tick as control points (see gcp_select.py).

The computation runs in a separate thread (a few seconds at most); the markers are read from
Metashape beforehand, in the main thread. For 3, 4, 5... control markers the table gives the best
set and the error measured on the markers left aside; the recommended row is preselected.
"Tick these markers" ticks that set in Metashape (Reference) and unticks the others: it is then up
to the user to save, optimise the cameras and check.
"""

from ... import i18n, uikit
from ...workers import Worker
from ...qt import QtCore, QtGui, QtWidgets
from ...uikit import tokens
from . import gcp_select, widgets

DESIGN_SIZE = (1040, 600)
POLL_MS = 120
ERR_KEYS = (("x", "gcp.col_x"), ("y", "gcp.col_y"), ("z", "gcp.col_z"), ("xy", "gcp.col_xy"), ("total", "gcp.col_xyz"),
            ("max", "gcp.col_max"))
ROWS_NOW = ("GCP", "CTRL")
SPREAD_TEXT = {"good": "gcp.spread_good", "mid": "gcp.spread_mid", "low": "gcp.spread_low"}
ROLE = QtCore.Qt.UserRole


def cm(meters):
    return "{:.1f}".format(meters * 100.0)


def _error_texts(stats):
    """Err X, Y, Z, 2D, 3D, max in cm (dashes if there is nothing to measure)."""
    if not stats:
        return ["-"] * len(ERR_KEYS)
    return [cm(stats[key]) for key in ("x", "y", "z", "xy", "total", "max")]


def _err_columns():
    return [uikit.Column(key, i18n.t(text), "right", 92, kind="number") for key, text in ERR_KEYS]


class GcpSelectDialog(uikit.ToolWindow):
    MARK = "tool_gcp"
    CLOSE_ONLY = True

    def __init__(self, collect, apply, parent=None):
        """`collect()` reads the markers (main thread): {"points", "enabled", "ppk", "why"};
        `apply(labels, context, analysed)` ticks these markers in Metashape and returns (number ticked, error message): `context` is what
        `collect` said about the chunk the result was computed for (the apply refuses another chunk), `analysed` the labels that were in
        the computation (the others are not touched)."""
        super(GcpSelectDialog, self).__init__(parent, title=i18n.t("gcp.title"), info=i18n.t("gcp.info"), design_size=DESIGN_SIZE)
        self._collect = collect
        self._apply = apply
        self._worker = None
        self._result = None
        self._context = None
        self._analysed = frozenset()
        self._curve = []

        body = QtWidgets.QWidget()
        lay = uikit.vbox(spacing=tokens.M, parent=body)
        self.mode = uikit.ChoiceBox([(gcp_select.SIMILARITY, i18n.t("gcp.mode_full")), (gcp_select.SHIFT, i18n.t("gcp.mode_shift"))])
        self.mode.setMinimumContentsLength(34)
        self.mode.changed.connect(lambda _key: self.refresh(keep_mode=True))
        lay.addWidget(self.mode, 0, QtCore.Qt.AlignLeft)

        lay.addWidget(uikit.label(i18n.t("gcp.now"), "lead", wrap=False))
        self.now = uikit.DataTable([uikit.Column("name", "", width=84), uikit.Column("count", i18n.t("gcp.col_count"), stretch=True)]
                                   + _err_columns(), selection="none", sortable=False)
        self.now.setFixedHeight(uikit.px(tokens.CONTROL_H) * (len(ROWS_NOW) + 1) + uikit.px(tokens.S))
        self.now.table.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        lay.addWidget(self.now)

        lay.addWidget(uikit.label(i18n.t("gcp.options"), "lead", wrap=False))
        self.table = uikit.DataTable([uikit.Column("count", i18n.t("gcp.col_count"), "right", 70, kind="number"),
                                      uikit.Column("markers", i18n.t("gcp.col_markers"), stretch=True)]
                                     + _err_columns() + [uikit.Column("spread", i18n.t("gcp.col_spread"), width=84)],
                                     sortable=False)
        self.table.table.horizontalHeaderItem(2 + [k for k, _t in ERR_KEYS].index("total")).setToolTip(i18n.t("gcp.col_xyz_tip"))
        self.table.selectionChanged.connect(self._update)
        lay.addWidget(self.table, 1)

        self.bar = uikit.ProgressRow()
        self.bar.set_indeterminate(i18n.t("gcp.computing"))
        self.bar.setVisible(False)
        lay.addWidget(self.bar)
        self.set_content(body)

        self.footer.add_left(uikit.button("gcp.recompute", self.refresh))
        self.set_primary(i18n.t("gcp.apply"), self._on_apply)
        self._fill_now(None, None, 0)

        self._clock = QtCore.QTimer(self)
        self._clock.setInterval(POLL_MS)
        self._clock.timeout.connect(self._poll)
        self.refresh()

    @property
    def apply_button(self):
        return self.next_button

    def _fill_now(self, control, check, total):
        """The two "current" rows: GCP (ticked) and CTRL (not ticked), as Metashape measures them."""
        rows = []
        for (name, tone), stats in zip((("GCP", "success"), ("CTRL", "warning")), (control, check)):
            count = "{}/{}".format(stats["n"] if stats else 0, total) if total else "-"
            rows.append([uikit.cell(name, tone=tone, bold=True), count] + _error_texts(stats))
        self.now.set_rows(rows)

    # -- computation ----------------------------------------------------------------

    def refresh(self, keep_mode=False):
        if self._worker is not None and not self._worker.abandoned:
            return                              # a computation of this window is still running
        try:
            data = self._collect()
        except Exception as exc:
            self._warn(i18n.t("gcp.failed_read", error=repr(exc)), "danger")
            return
        self._data = data
        self._context = data.get("context")
        self._analysed = frozenset(p["label"] for p in data["points"] if int(p.get("n") or 0) >= gcp_select.MIN_PHOTOS)
        if data.get("why") and not data["points"]:
            self._clear()
            self._warn(i18n.t("gcp.no_frame", why=data["why"]), "warning")
            return
        if not keep_mode:
            self.mode.set_value(gcp_select.SHIFT if data.get("ppk") else gcp_select.SIMILARITY)
        model = self.mode.value()
        points, enabled = data["points"], data["enabled"]
        self._clear()
        self.bar.setVisible(True)
        self.next_button.setEnabled(False)
        self._worker = Worker(lambda _w: gcp_select.analyze(points, enabled, model))
        self._worker.start()
        self._clock.start()

    def _poll(self):
        worker = self._worker
        if worker is None or worker.abandoned:
            self._clock.stop()
            return
        if not worker.finished:
            return
        self._clock.stop()
        self._worker = None
        self.bar.setVisible(False)
        result = worker.result or {}
        if "error" in result:
            self._warn(i18n.t("gcp.failed_compute", error=result["error"]), "danger")
            return
        self._show(result)

    def _clear(self):
        self._fill_now(None, None, 0)
        self.table.set_rows([])
        self._curve = []
        self.clear_banner()
        self._result = None
        self._update()

    # -- display --------------------------------------------------------------------

    def _show(self, result):
        self._result = result
        control, check = result["current"]
        total = result["usable"]
        self._fill_now(control, check, total)
        curve = result["curve"]
        self._curve = list(curve)
        rows = []
        for row, item in enumerate(curve):
            recommended = row == result["recommended"]
            label = gcp_select.spread_label(item["spread"])
            tone = {"good": "success", "mid": "warning", "low": "danger"}[label]               # colour, and the word beside it
            first = uikit.cell("{}/{}".format(item["k"], total), item["k"], tone="accent" if recommended else None, bold=recommended)
            rows.append([first, uikit.cell(", ".join(item["labels"]), bold=recommended)]
                        + [uikit.cell(cm(item[key]), item[key], bold=recommended) for key in ("x", "y", "z", "xy", "rmse", "max")]
                        + [uikit.cell(i18n.t(SPREAD_TEXT[label]), tone=tone, bold=True)])
        self.table.set_rows(rows)
        if result["recommended"] is not None:
            self.table.select_row(result["recommended"])
        lines = []
        if result["reason"] == "too_few":
            lines.append(i18n.t("gcp.too_few", n=gcp_select.MIN_CONTROL[result["model"]] + 1,
                                photos=gcp_select.MIN_PHOTOS))
        if result["suspects"]:
            names = ", ".join("{} ({} cm)".format(s["label"], cm(s["error"])) for s in result["suspects"])
            lines.append(i18n.t("gcp.suspects", names=names))
        if result["few"]:
            lines.append(i18n.t("gcp.few", names=", ".join(result["few"]), n=gcp_select.MIN_PHOTOS))
        self._warn("\n".join(lines), "warning")
        self._update()

    def _warn(self, text, kind="warning"):
        if text:
            self.show_banner(kind, text)
        else:
            self.clear_banner()

    def refresh_buttons(self, *args):
        super(GcpSelectDialog, self).refresh_buttons(*args)
        if hasattr(self, "_curve"):
            self._update()                       # the shell re-enables the primary: it needs a row chosen and no computation running

    def _chosen(self):
        rows = self.table.selected_rows()
        return self._curve[rows[0]] if rows and rows[0] < len(self._curve) else None

    def _update(self):
        self.next_button.setEnabled(self._worker is None and self._chosen() is not None)

    # -- ticking in Metashape -------------------------------------------------------

    def _on_apply(self):
        item = self._chosen()
        if item is None:
            return
        names = ", ".join(item["labels"])
        if not uikit.confirm(self, i18n.t("gcp.title"), i18n.t("gcp.confirm", n=item["k"], names=names), ok=i18n.t("gcp.apply")):
            return
        count, error = self._apply(item["labels"], self._context, self._analysed)
        if error:
            uikit.error(self, i18n.t("gcp.title"), i18n.t("gcp.failed_apply", error=error))
            return
        uikit.inform(self, i18n.t("gcp.title"), i18n.t("gcp.done", n=count, names=names), "success")

    def closeEvent(self, event):
        self._clock.stop()
        worker = self._worker
        if worker is not None:
            worker.abandon()                    # nobody will read it: the window must not stay "busy" for ever
            self._worker = None
            self.bar.setVisible(False)
        super(GcpSelectDialog, self).closeEvent(event)


def show(panel, collect, apply, existing=None):
    """Opens (or recomputes) the window. Returns the instance, to be kept."""
    return widgets.present(existing, lambda: GcpSelectDialog(collect, apply, panel), lambda window: window.refresh())
