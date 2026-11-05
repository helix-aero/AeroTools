"""Pop-up « Ajuster » (Qt) : quels marqueurs cocher comme points de controle (voir gcp_select.py).

Le calcul tourne dans un fil a part (quelques secondes au plus) ; la lecture des marqueurs dans
Metashape se fait avant, dans le fil principal. Le tableau donne, pour 3, 4, 5... marqueurs de
controle, le meilleur jeu et l'erreur mesuree sur les marqueurs laisses de cote ; la ligne conseillee
est preselectionnee. « Cocher ces marqueurs » coche ce jeu dans Metashape (Reference) et decoche les
autres : ensuite c'est a l'utilisateur d'enregistrer, d'optimiser les cameras et de verifier.
"""

import threading

from ... import i18n, theme, ui
from ...qt import QtCore, QtGui, QtWidgets
from . import dialogs, gcp_select

ROLE = QtCore.Qt.UserRole
WIDTH, HEIGHT = 720, 560
POLL_MS = 120
COLUMNS = ("gcp.col_count", "gcp.col_markers", "gcp.col_error", "gcp.col_z", "gcp.col_max", "gcp.col_spread")
SPREAD_TEXT = {"good": "gcp.spread_good", "mid": "gcp.spread_mid", "low": "gcp.spread_low"}
SPREAD_COLOR = {"good": "#6ee79a", "mid": "#e5c453", "low": theme.ORANGE_BRIGHT}


def cm(meters):
    return "{:.1f}".format(meters * 100.0)


class _Worker(threading.Thread):
    def __init__(self, job):
        super(_Worker, self).__init__(daemon=True)
        self.job = job
        self.result = None
        self.finished = False

    def run(self):
        try:
            self.result = self.job()
        except Exception as exc:
            self.result = {"error": repr(exc)}
        self.finished = True


class GcpSelectDialog(QtWidgets.QDialog):
    def __init__(self, collect, apply, parent=None):
        """`collect()` lit les marqueurs (fil principal) : {"points", "enabled", "ppk", "why"} ;
        `apply(labels)` coche ces marqueurs dans Metashape et rend (nombre coche, message d'erreur)."""
        super(GcpSelectDialog, self).__init__(parent)
        self._collect = collect
        self._apply = apply
        self._lang = i18n.lang()
        self._worker = None
        self._result = None
        self.setWindowTitle(i18n.t("gcp.title"))
        self.setModal(False)
        self.resize(WIDTH, HEIGHT)
        root = ui.dark_root(self)
        layout = QtWidgets.QVBoxLayout(root)
        pad = theme.PAD_L
        layout.setContentsMargins(pad, pad, pad, pad)
        layout.setSpacing(theme.PAD + theme.PAD_S)

        top = QtWidgets.QHBoxLayout()
        top.setSpacing(theme.PAD)
        control, self.control_number, self.control_caption = ui.tile()
        check, self.check_number, self.check_caption = ui.tile()
        top.addWidget(control, 1)
        top.addWidget(check, 1)
        self.mode = QtWidgets.QComboBox()
        self.mode.setObjectName("aeroCombo")
        self.mode.setCursor(QtCore.Qt.PointingHandCursor)
        self.mode.setFocusPolicy(QtCore.Qt.NoFocus)
        self.mode.addItem(i18n.t("gcp.mode_full"), gcp_select.SIMILARITY)
        self.mode.addItem(i18n.t("gcp.mode_shift"), gcp_select.SHIFT)
        self.mode.setMinimumHeight(40)
        self.mode.activated.connect(lambda _index: self.refresh(keep_mode=True))
        top.addWidget(self.mode, 2)
        layout.addLayout(top)

        self.table = QtWidgets.QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels([i18n.t(key) for key in COLUMNS])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setStyleSheet(ui.table_style())
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        for column, width in enumerate((74, 0, 100, 70, 70, 100)):
            if width:
                self.table.setColumnWidth(column, width)
        self.table.horizontalHeaderItem(2).setToolTip(i18n.t("gcp.col_error_tip"))
        self.table.itemSelectionChanged.connect(self._update)
        layout.addWidget(self.table, 1)

        self.bar = QtWidgets.QProgressBar()
        self.bar.setObjectName("aeroProgress")
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 0)
        self.bar.setVisible(False)
        layout.addWidget(self.bar)
        self.warning = QtWidgets.QLabel()
        self.warning.setObjectName("aeroWarn")
        self.warning.setWordWrap(True)
        self.warning.setVisible(False)
        layout.addWidget(self.warning)

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(theme.PAD)
        row.addWidget(ui.big_button("gcp.recompute", self.refresh, width=130))
        row.addStretch(1)
        self.apply_button = ui.big_button("gcp.apply", self._on_apply, "aeroGo", width=210)
        row.addWidget(self.apply_button)
        row.addWidget(ui.big_button("qc.close", self.close, width=110))
        layout.addLayout(row)

        self._clock = ui.timer(self, POLL_MS, self._poll)
        self.refresh()

    # -- calcul ---------------------------------------------------------------------

    def refresh(self, keep_mode=False):
        if self._worker is not None:
            return
        try:
            data = self._collect()
        except Exception as exc:
            self._warn(i18n.t("gcp.failed_read", error=repr(exc)))
            return
        self._data = data
        if data.get("why") and not data["points"]:
            self._clear()
            self._warn(i18n.t("gcp.no_frame", why=data["why"]))
            return
        if not keep_mode:
            index = self.mode.findData(gcp_select.SHIFT if data.get("ppk") else gcp_select.SIMILARITY)
            self.mode.setCurrentIndex(max(0, index))
        model = self.mode.currentData()
        points, enabled = data["points"], data["enabled"]
        self._clear()
        self.bar.setVisible(True)
        self.apply_button.setEnabled(False)
        self._worker = _Worker(lambda: gcp_select.analyze(points, enabled, model))
        self._worker.start()
        self._clock.start()

    def _poll(self):
        worker = self._worker
        if worker is None:
            self._clock.stop()
            return
        if not worker.finished:
            return
        self._clock.stop()
        self._worker = None
        self.bar.setVisible(False)
        result = worker.result or {}
        if "error" in result:
            self._warn(i18n.t("gcp.failed_compute", error=result["error"]))
            return
        self._show(result)

    def _clear(self):
        self.table.setRowCount(0)
        self.warning.setVisible(False)
        self._result = None
        self._update()

    # -- affichage ------------------------------------------------------------------

    def _show(self, result):
        self._result = result
        control, check = result["current"]
        for number, caption, stats, key in ((self.control_number, self.control_caption, control, "gcp.now_control"),
                                            (self.check_number, self.check_caption, check, "gcp.now_check")):
            number.setText("{} cm".format(cm(stats["total"])) if stats else "-")
            caption.setText(i18n.t(key, n=stats["n"] if stats else 0))
        curve = result["curve"]
        self.table.setRowCount(len(curve))
        for row, item in enumerate(curve):
            recommended = row == result["recommended"]
            label = gcp_select.spread_label(item["spread"])
            cells = (
                (("✓ " if recommended else "") + str(item["k"]), None),
                (", ".join(item["labels"]), None),
                (cm(item["rmse"]), None),
                (cm(item["z"]), None),
                (cm(item["max"]), None),
                (i18n.t(SPREAD_TEXT[label]), SPREAD_COLOR[label]),
            )
            for column, (text, color) in enumerate(cells):
                cell = QtWidgets.QTableWidgetItem(text)
                cell.setData(ROLE, item)
                if column in (0, 2, 3, 4):
                    cell.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                else:
                    cell.setTextAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
                if color:
                    cell.setForeground(QtGui.QBrush(QtGui.QColor(color)))
                if recommended:
                    font = cell.font()
                    font.setBold(True)
                    cell.setFont(font)
                self.table.setItem(row, column, cell)
        if result["recommended"] is not None:
            self.table.selectRow(result["recommended"])
        lines = []
        if result["reason"] == "too_few":
            lines.append(i18n.t("gcp.too_few", n=gcp_select.MIN_CONTROL[result["model"]] + 1,
                                photos=gcp_select.MIN_PHOTOS))
        if result["suspects"]:
            names = ", ".join("{} ({} cm)".format(s["label"], cm(s["error"])) for s in result["suspects"])
            lines.append(i18n.t("gcp.suspects", names=names))
        if result["few"]:
            lines.append(i18n.t("gcp.few", names=", ".join(result["few"]), n=gcp_select.MIN_PHOTOS))
        self._warn("\n".join(lines))
        self._update()

    def _warn(self, text):
        self.warning.setText(text)
        self.warning.setVisible(bool(text))

    def _chosen(self):
        row = self.table.currentRow()
        cell = self.table.item(row, 0) if row >= 0 else None
        return cell.data(ROLE) if cell is not None else None

    def _update(self):
        self.apply_button.setEnabled(self._worker is None and self._chosen() is not None)

    # -- cocher dans Metashape ------------------------------------------------------

    def _on_apply(self):
        item = self._chosen()
        if item is None:
            return
        names = ", ".join(item["labels"])
        if not dialogs.ask(self, i18n.t("gcp.title"), i18n.t("gcp.confirm", n=item["k"], names=names)):
            return
        count, error = self._apply(item["labels"])
        if error:
            dialogs.inform(self, i18n.t("gcp.title"), i18n.t("gcp.failed_apply", error=error))
            return
        dialogs.inform(self, i18n.t("gcp.title"), i18n.t("gcp.done", n=count, names=names))

    def closeEvent(self, event):
        self._clock.stop()
        super(GcpSelectDialog, self).closeEvent(event)


def show(panel, collect, apply, existing=None):
    """Ouvre (ou recalcule) la fenetre. Renvoie l'instance, a garder."""
    dialog = existing
    try:
        if dialog is not None:
            dialog.objectName()
            if dialog._lang != i18n.lang():
                dialog.deleteLater()
                dialog = None
    except RuntimeError:
        dialog = None
    if dialog is None:
        dialog = GcpSelectDialog(collect, apply, panel)
    else:
        dialog.refresh()
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog
