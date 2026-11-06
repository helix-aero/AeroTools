"""Residual check pop-up (Qt): all markers with their status, most urgent first.

For each marker the table shows: the number of pinned photos, the error in X, Y, Z and in 3D
(in cm, like Metashape's Reference table), its role (control or check, from the box
ticked in Reference) and its status (OK, high residual, too few photos, cannot be computed). The three
tiles at the top summarise: the error of the control points, that of the check points, and the
number of markers to watch. A double-click opens the marker (and its worst photo in case of a residual).
Non-modal window: leave it open, fix things, then "Refresh".
"""

from ... import i18n, theme, ui
from ...qt import QtCore, QtGui, QtWidgets
from . import qc

ROLE = QtCore.Qt.UserRole
WIDTH, HEIGHT = 700, 560
COLUMNS = ("qc.col_marker", "qc.col_photos", "qc.col_x", "qc.col_y", "qc.col_z", "qc.col_total",
           "qc.col_role", "qc.col_status")
STATUS_TEXT = {qc.OK: "qc.st_ok", qc.RESIDUAL: "qc.st_residual", qc.FEW: "qc.st_few", qc.NONE: "qc.st_none"}
STATUS_COLOR = {qc.OK: "#6ee79a", qc.RESIDUAL: theme.ORANGE_BRIGHT, qc.FEW: "#e5c453", qc.NONE: theme.FG_3}


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


class _Item(QtWidgets.QTableWidgetItem):
    """Cell sorted on its value (number) rather than on its text."""

    def __lt__(self, other):
        mine, theirs = self.data(ROLE + 1), other.data(ROLE + 1)
        try:
            return mine < theirs
        except TypeError:
            return str(mine) < str(theirs)


class QcDialog(QtWidgets.QDialog):
    def __init__(self, compute, opener, diagnostic, parent=None):
        """`compute()` returns {"items", "groups", "limit"}; `opener(item)` opens the marker; `diagnostic()` the full diagnostic."""
        super(QcDialog, self).__init__(parent)
        self._compute = compute
        self._opener = opener
        self.setWindowTitle(i18n.t("qc.title"))
        self.setModal(False)
        self.resize(WIDTH, HEIGHT)
        root = ui.dark_root(self)
        layout = QtWidgets.QVBoxLayout(root)
        pad = theme.PAD_L
        layout.setContentsMargins(pad, pad, pad, pad)
        layout.setSpacing(theme.PAD + theme.PAD_S)

        tiles = QtWidgets.QHBoxLayout()
        tiles.setSpacing(theme.PAD)
        control, self.control_number, self.control_caption = ui.tile()
        check, self.check_number, self.check_caption = ui.tile()
        watch, self.watch_number, self.watch_caption = ui.tile(i18n.t("qc.stat_watch"))
        for frame in (control, check, watch):
            tiles.addWidget(frame, 1)
        layout.addLayout(tiles)

        self.table = QtWidgets.QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels([i18n.t(key) for key in COLUMNS])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.setWordWrap(False)
        self.table.setStyleSheet(ui.table_style())
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        for column, width in enumerate((130, 64, 62, 62, 62, 70, 100)):
            self.table.setColumnWidth(column, width)
        self.table.itemDoubleClicked.connect(lambda _item: self._open())
        self.table.itemSelectionChanged.connect(self._update)
        layout.addWidget(self.table, 1)

        self.note = ui.label("", "aeroMeta")
        layout.addWidget(self.note)

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(theme.PAD)
        row.addWidget(ui.big_button("diag.menu", diagnostic, width=170))
        row.addWidget(ui.big_button("qc.refresh", self.refresh, width=110))
        row.addStretch(1)
        self.open_button = ui.big_button("qc.open", self._open, "aeroGo", width=110)
        row.addWidget(self.open_button)
        row.addWidget(ui.big_button("qc.close", self.close, width=110))
        layout.addLayout(row)
        self.refresh()

    def refresh(self):
        try:
            data = self._compute()
        except Exception as exc:
            self.note.setText(i18n.t("qc.failed", error=repr(exc)))
            return
        items = data["items"]
        control, check = data["groups"]
        self._tile(self.control_number, self.control_caption, control, "qc.stat_control")
        self._tile(self.check_number, self.check_caption, check, "qc.stat_check")
        watch = sum(1 for item in items if item["status"] in (qc.RESIDUAL, qc.FEW))
        self.watch_number.setText(str(watch))
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(items))
        for row, item in enumerate(items):
            self._set_row(row, item)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(7, QtCore.Qt.AscendingOrder)
        self.note.setText(i18n.t("qc.limit", limit=cm_abs(data["limit"])) if items else i18n.t("qc.empty"))
        self._update()

    @staticmethod
    def _tile(number, caption, group, key):
        if group is None:
            number.setText("-")
            caption.setText(i18n.t(key, n=0))
        else:
            number.setText("{} cm".format(cm_abs(group[0])))
            caption.setText(i18n.t(key, n=group[1]))

    def _set_row(self, row, item):
        order = {qc.RESIDUAL: 0, qc.FEW: 1, qc.NONE: 2, qc.OK: 3}[item["status"]]
        cells = (
            (item["label"], item["label"].lower(), QtCore.Qt.AlignLeft),
            (str(item["proj"]), item["proj"], QtCore.Qt.AlignRight),
            (cm(item["x"]), item["x"] if item["x"] is not None else 0.0, QtCore.Qt.AlignRight),
            (cm(item["y"]), item["y"] if item["y"] is not None else 0.0, QtCore.Qt.AlignRight),
            (cm(item["z"]), item["z"] if item["z"] is not None else 0.0, QtCore.Qt.AlignRight),
            (cm_abs(item["total"]), item["total"] if item["total"] is not None else -1.0, QtCore.Qt.AlignRight),
            (role_text(item["role"]), str(item["role"]), QtCore.Qt.AlignLeft),
            (i18n.t(STATUS_TEXT[item["status"]]), order, QtCore.Qt.AlignLeft),
        )
        for column, (text, key, align) in enumerate(cells):
            cell = _Item(text)
            cell.setData(ROLE, item)
            cell.setData(ROLE + 1, key)
            cell.setTextAlignment(align | QtCore.Qt.AlignVCenter)
            if column == 7:
                cell.setForeground(QtGui.QBrush(QtGui.QColor(STATUS_COLOR[item["status"]])))
                font = cell.font()
                font.setBold(True)
                cell.setFont(font)
            self.table.setItem(row, column, cell)

    def _current(self):
        row = self.table.currentRow()
        cell = self.table.item(row, 0) if row >= 0 else None
        return cell.data(ROLE) if cell is not None else None

    def _update(self):
        self.open_button.setEnabled(self._current() is not None)

    def _open(self):
        item = self._current()
        if item is not None:
            self._opener(item)


def show(panel, compute, opener, diagnostic, existing=None):
    """Opens (or refreshes) the window. Returns the instance, to be kept."""
    dialog = existing
    try:
        if dialog is not None:
            dialog.objectName()
    except RuntimeError:
        dialog = None
    if dialog is None:
        dialog = QcDialog(compute, opener, diagnostic, panel)
    dialog.refresh()
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog
