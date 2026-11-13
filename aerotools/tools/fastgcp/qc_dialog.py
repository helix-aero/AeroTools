"""Residual check window (Qt): all markers with their status, most urgent first.

For each marker the table shows: the number of pinned photos, the error in X, Y, Z and in 3D
(in cm, like Metashape's Reference table), its role (control or check, from the box
ticked in Reference) and its status (OK, high residual, too few photos, cannot be computed). The three
tiles at the top summarise: the error of the control points, that of the check points, and the
number of markers to watch. A double-click opens the marker (and its worst photo in case of a residual).
Non-modal window: leave it open, fix things, then "Refresh".
"""

from ... import i18n, uikit
from ...qt import QtCore, QtGui, QtWidgets
from ...uikit import tokens
from . import qc, widgets

ROLE = QtCore.Qt.UserRole
DESIGN_SIZE = (720, 560)
STATUS_TEXT = {qc.OK: "qc.st_ok", qc.RESIDUAL: "qc.st_residual", qc.FEW: "qc.st_few", qc.NONE: "qc.st_none"}
STATUS_TONE = {qc.OK: "success", qc.RESIDUAL: "danger", qc.FEW: "warning", qc.NONE: "faint"}          # colour, with an icon and a word
STATUS_ICON = {qc.OK: "ok", qc.RESIDUAL: "error", qc.FEW: "warn", qc.NONE: "pending"}
STATUS_ORDER = {qc.RESIDUAL: 0, qc.FEW: 1, qc.NONE: 2, qc.OK: 3}


def cm(meters):
    return "-" if meters is None else "{:+.1f}".format(meters * 100.0)


def cm_abs(meters):
    return "-" if meters is None else "{:.1f}".format(meters * 100.0)


def role_text(role):
    if role is True:
        return i18n.t("qc.role_control")
    if role is False:
        return i18n.t("qc.role_check")
    return "-"


def _columns():
    return [uikit.Column("marker", i18n.t("qc.col_marker"), width=92),
            uikit.Column("photos", i18n.t("qc.col_photos"), "right", 62, kind="number"),
            uikit.Column("x", i18n.t("qc.col_x"), "right", 64, kind="number"),
            uikit.Column("y", i18n.t("qc.col_y"), "right", 64, kind="number"),
            uikit.Column("z", i18n.t("qc.col_z"), "right", 64, kind="number"),
            uikit.Column("total", i18n.t("qc.col_total"), "right", 66, kind="number"),
            uikit.Column("role", i18n.t("qc.col_role"), width=72),
            uikit.Column("icon", "", kind="status", width=32),
            uikit.Column("status", i18n.t("qc.col_status"), stretch=True, kind="number")]


class QcDialog(uikit.ToolWindow):
    MARK = "tool_gcp"
    CLOSE_ONLY = True

    def __init__(self, compute, opener, diagnostic, parent=None):
        """`compute()` returns {"items", "groups", "limit"}; `opener(item)` opens the marker; `diagnostic()` the full diagnostic."""
        super(QcDialog, self).__init__(parent, title=i18n.t("qc.title"), info=i18n.t("qc.info"), design_size=DESIGN_SIZE)
        self._compute = compute
        self._opener = opener
        self._items = []

        body = QtWidgets.QWidget()
        lay = uikit.vbox(spacing=tokens.M, parent=body)
        tiles = uikit.hbox(spacing=tokens.S)
        self.control_tile = widgets.Stat()
        self.check_tile = widgets.Stat()
        self.watch_tile = widgets.Stat(i18n.t("qc.stat_watch"))
        for tile in (self.control_tile, self.check_tile, self.watch_tile):
            tiles.addWidget(tile, 1)
        lay.addLayout(tiles)

        self.table = uikit.DataTable(_columns(), empty_title=i18n.t("qc.empty"))
        self.table.rowActivated.connect(lambda _row: self._open())
        self.table.selectionChanged.connect(self._update)
        lay.addWidget(self.table, 1)
        self.set_content(body)

        self.footer.add_left(uikit.button("diag.menu", diagnostic))
        self.footer.add_left(uikit.button("qc.refresh", self.refresh))
        self.set_primary(i18n.t("qc.open"), self._open)
        self.refresh()

    # Names the tests and the panel rely on.
    @property
    def open_button(self):
        return self.next_button

    @property
    def note(self):
        return self.status

    def refresh(self):
        try:
            data = self._compute()
        except Exception as exc:
            self.show_error(exc, self.refresh)
            return
        self.clear_banner()
        items = data["items"]
        control, check = data["groups"]
        self._tile(self.control_tile, control, "qc.stat_control", data["limit"])
        self._tile(self.check_tile, check, "qc.stat_check", data["limit"])
        watch = sum(1 for item in items if item["status"] in (qc.RESIDUAL, qc.FEW))
        self.watch_tile.set_number(str(watch), "warning" if watch else "success")
        self._items = list(items)
        self.table.set_rows([self._row(item, data["limit"]) for item in items])
        self.table.sort_by("status")
        self.set_status(i18n.t("qc.limit", limit=cm_abs(data["limit"])) if items else "")
        self._update()

    @staticmethod
    def _tile(tile, group, key, limit=None):
        if group is None:
            tile.set_number("-", "faint")
            tile.set_caption(i18n.t(key, n=0))
        else:
            tile.set_number("{} cm".format(cm_abs(group[0])), ("danger" if limit is not None and group[0] > limit else "success"))
            tile.set_caption(i18n.t(key, n=group[1]))

    @staticmethod
    def _row(item, limit=None):
        over = limit is not None and item["total"] is not None and item["total"] > limit              # out of tolerance: the semantic colour
        status = item["status"]
        return [uikit.cell(item["label"], bold=True),
                (str(item["proj"]), item["proj"]),
                (cm(item["x"]), item["x"] if item["x"] is not None else 0.0),
                (cm(item["y"]), item["y"] if item["y"] is not None else 0.0),
                (cm(item["z"]), item["z"] if item["z"] is not None else 0.0),
                uikit.cell(cm_abs(item["total"]), item["total"] if item["total"] is not None else -1.0, tone="danger" if over else None, bold=over),
                role_text(item["role"]),
                uikit.cell("", STATUS_ORDER[status], icon=STATUS_ICON[status]),
                uikit.cell(i18n.t(STATUS_TEXT[status]), STATUS_ORDER[status], tone=STATUS_TONE[status], bold=True)]

    def refresh_buttons(self, *args):
        super(QcDialog, self).refresh_buttons(*args)
        if hasattr(self, "table"):
            self._update()                       # the shell re-enables the primary: Open needs a marker chosen

    def _current(self):
        rows = self.table.selected_rows()
        return self._items[rows[0]] if rows else None

    def _update(self):
        self.next_button.setEnabled(self._current() is not None)

    def _open(self):
        item = self._current()
        if item is not None:
            self._opener(item)


def show(panel, compute, opener, diagnostic, existing=None):
    """Opens (or refreshes) the window. Returns the instance, to be kept."""
    return widgets.present(existing, lambda: QcDialog(compute, opener, diagnostic, panel), lambda window: window.refresh())
