"""Marking diagnostic window (Qt): the result of diagnostic.py, with photo opening.

Non-modal window: the list can stay open while you open a photo, fix it, then click
"Recompute". A double-click (or the button) opens the photo of the problem, zoomed on the marker.
"""

from ... import controls, i18n, uikit
from ...qt import QtWidgets
from . import diagnostic, widgets

DESIGN_SIZE = (680, 560)


def px_text(value):
    return "{:.1f}".format(value)


def cm_text(meters):
    return "{:.1f} cm".format(meters * 100.0)


def lines(result):
    """[(kind text, text, item)]: what the list shows, in order."""
    out = []
    for item in result["projections"]:
        out.append((i18n.t("diag.kind_pick"), i18n.t("diag.item_projection", label=item["label"], camera=item["camera_label"],
                                                      px=px_text(item["px"])), item))
    for item in result["photos"]:
        out.append((i18n.t("diag.kind_photo"), i18n.t("diag.item_photo", camera=item["camera_label"], px=px_text(item["px"]),
                                                       n=item["n"]), item))
    for item in result["markers"]:
        kind = item["kind"]
        if kind == "few":
            text = i18n.t("diag.item_few", label=item["label"], n=item["n"], need=diagnostic.MIN_PINNED)
        elif kind == "unchecked":
            text = i18n.t("diag.item_unchecked", label=item["label"])
        elif kind == "residual":
            text = i18n.t("diag.item_residual", label=item["label"], error=cm_text(item["total"]),
                          limit=cm_text(item["limit"]))
        else:
            text = i18n.t("diag.item_proposals", label=item["label"], n=item["n"])
        out.append((i18n.t("diag.kind_marker"), text, item))
    return out


def _tone(item):
    """Colour of a finding: a missing or unchecked marker is a warning, a pick off by pixels or a 3D error over the limit is an error."""
    kind = item.get("kind")
    return "warning" if kind in ("few", "unchecked", "proposals") else "danger" if kind == "residual" or item.get("px") is not None else None


def summary_text(result):
    s = result["summary"]
    if not s["observations"]:
        return i18n.t("diag.empty")
    text = i18n.t("diag.summary", markers=s["markers"], checked=s["checked"], obs=s["observations"],
                  photos=s["photos"], rms=px_text(s["rms_px"]), median=px_text(s["median_px"]))
    if s["below_1px"] is not None:
        text += " " + i18n.t("diag.summary_loo", percent="{:.0f}".format(100 * s["below_1px"]))
    return text


class DiagnosticDialog(controls.FollowsText, uikit.ToolWindow):
    CLOSE_ONLY = True

    def __init__(self, compute, opener, parent=None):
        """`compute()` returns the result of diagnostic.analyze; `opener(item)` opens the item's photo."""
        super(DiagnosticDialog, self).__init__(parent, title=i18n.t("diag.title"), info=i18n.t("diag.explain"), design_size=DESIGN_SIZE)
        self._compute = compute
        self._opener = opener
        self._items = []
        self.result = None

        body = QtWidgets.QWidget()
        lay = uikit.vbox(spacing=uikit.tokens.M, parent=body)
        self.summary = uikit.label("", "body", "muted")            # only when there is nothing to count
        lay.addWidget(self.summary)
        tiles = uikit.hbox(spacing=uikit.tokens.S)                   # the figures first: markers, picks, average gap, picks within 1 px
        self.tiles = {key: uikit.Stat("-", i18n.t("diag.stat_" + key)) for key in ("markers", "picks", "rms", "within")}
        for tile in self.tiles.values():
            tiles.addWidget(tile, 1)
        self.tiles_row = QtWidgets.QWidget()
        self.tiles_row.setLayout(tiles)
        tiles.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.tiles_row)
        self.table = uikit.DataTable([uikit.Column("kind", i18n.t("diag.col_kind"), width=84),
                                      uikit.Column("finding", i18n.t("diag.col_finding"), stretch=True)],
                                     sortable=False, empty_title=i18n.t("diag.clean"))
        self.table.rowActivated.connect(lambda _row: self._open())
        self.table.selectionChanged.connect(self._update)
        lay.addWidget(self.table, 1)
        self.set_content(body)

        self.footer.add_left(uikit.button("diag.refresh", self.refresh))
        self.set_primary(i18n.t("diag.open"), self._open)
        self.refresh()

    @property
    def open_button(self):
        return self.next_button

    def refresh(self):
        try:
            result = self._compute()
        except Exception as exc:
            self.summary.setText("")
            self.table.set_rows([])
            self.show_error(exc, self.refresh)
            return
        self.clear_banner()
        self.result = result
        self.summary.setText(summary_text(result) if not result["summary"]["observations"] else "")
        self.summary.setVisible(bool(self.summary.text()))
        self._fill_tiles(result["summary"])
        found = lines(result)
        self._items = [item for _kind, _text, item in found]
        clean = diagnostic.is_clean(result) and result["summary"]["observations"]
        self.table.set_empty(i18n.t("diag.clean") if clean else i18n.t("diag.empty"))
        self.table.set_rows([[uikit.cell(kind, tone=_tone(item), bold=True), uikit.cell(text, tone=_tone(item))] for kind, text, item in found])
        self.set_status(i18n.t("diag.limit", limit=px_text(result["limit_px"])) if result["projections"] else "")
        self._update()

    def _fill_tiles(self, s):
        self.tiles_row.setVisible(bool(s["observations"]))
        if not s["observations"]:
            return
        self.tiles["markers"].set_value(str(s["markers"]))
        self.tiles["markers"].set_caption(i18n.t("diag.stat_markers_of", checked=s["checked"]))
        self.tiles["picks"].set_value(str(s["observations"]))
        self.tiles["picks"].set_caption(i18n.t("diag.stat_picks_on", photos=s["photos"]))
        rms = s["rms_px"]
        self.tiles["rms"].set_value(px_text(rms) + " px", "success" if rms <= 1.0 else ("warning" if rms <= 2.0 else "danger"))
        within = s["below_1px"]
        self.tiles["within"].set_value("-" if within is None else "{:.0f} %".format(100 * within),
                                       None if within is None else ("success" if within >= 0.8 else "warning"))

    def refresh_buttons(self, *args):
        super(DiagnosticDialog, self).refresh_buttons(*args)
        if hasattr(self, "table"):
            self._update()                       # the shell re-enables the primary: Open needs a finding chosen

    def _current(self):
        rows = self.table.selected_rows()
        return self._items[rows[0]] if rows else None

    def _update(self):
        item = self._current()
        self.next_button.setEnabled(item is not None and bool(item.get("label") or item.get("camera") is not None))

    def _open(self):
        item = self._current()
        if item is not None:
            self._opener(item)


def show(panel, compute, opener, existing=None):
    """Opens (or refreshes) the window. Returns the instance, to be kept."""
    return widgets.present(existing, lambda: DiagnosticDialog(compute, opener, panel), lambda window: window.refresh())
