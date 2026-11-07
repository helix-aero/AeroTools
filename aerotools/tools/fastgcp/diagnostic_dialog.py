"""Marking diagnostic window (Qt): the result of diagnostic.py, with photo opening.

Non-modal window: the list can stay open while you open a photo, fix it, then click
"Recompute". A double-click (or the button) opens the photo of the problem, zoomed on the marker.
"""

from ... import i18n, theme, ui
from ...qt import QtCore, QtWidgets
from ..aeroexports import controls
from . import diagnostic

ROLE = QtCore.Qt.UserRole
WIDTH, HEIGHT = 640, 620


def px_text(value):
    return "{:.1f}".format(value)


def cm_text(meters):
    return "{:.1f} cm".format(meters * 100.0)


def lines(result):
    """[(text, item or None, is title?)]: what the list shows, in order."""
    out = []
    if result["projections"]:
        out.append((i18n.t("diag.sec_projections", limit=px_text(result["limit_px"])), None, True))
        for item in result["projections"]:
            out.append((i18n.t("diag.item_projection", label=item["label"], camera=item["camera_label"],
                               px=px_text(item["px"])), item, False))
    if result["photos"]:
        out.append((i18n.t("diag.sec_photos"), None, True))
        for item in result["photos"]:
            out.append((i18n.t("diag.item_photo", camera=item["camera_label"], px=px_text(item["px"]),
                               n=item["n"]), item, False))
    if result["markers"]:
        out.append((i18n.t("diag.sec_markers"), None, True))
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
            out.append((text, item, False))
    return out


def summary_text(result):
    s = result["summary"]
    if not s["observations"]:
        return i18n.t("diag.empty")
    text = i18n.t("diag.summary", markers=s["markers"], checked=s["checked"], obs=s["observations"],
                  photos=s["photos"], rms=px_text(s["rms_px"]), median=px_text(s["median_px"]))
    if s["below_1px"] is not None:
        text += " " + i18n.t("diag.summary_loo", percent="{:.0f}".format(100 * s["below_1px"]))
    return text


class DiagnosticDialog(controls.FollowsText, QtWidgets.QDialog):
    def __init__(self, compute, opener, parent=None):
        """`compute()` returns the result of diagnostic.analyze; `opener(item)` opens the item's photo."""
        super(DiagnosticDialog, self).__init__(parent)
        self._compute = compute
        self._opener = opener
        self.setWindowTitle(i18n.t("diag.title"))
        self.setModal(False)
        self.resize(WIDTH, HEIGHT)
        root = ui.styled_root(self)
        layout = QtWidgets.QVBoxLayout(root)
        pad = theme.PAD_L
        layout.setContentsMargins(pad, pad, pad, pad)
        layout.setSpacing(theme.PAD)
        layout.addWidget(ui.label(i18n.t("diag.explain"), "aeroMeta"))
        self.summary = ui.label("")
        layout.addWidget(self.summary)
        self.list = QtWidgets.QListWidget()
        self.list.setObjectName("aeroThumbs")
        self.list.setWordWrap(True)
        self.list.setSpacing(2)
        self.list.itemDoubleClicked.connect(lambda _item: self._open())
        self.list.itemSelectionChanged.connect(self._update)
        layout.addWidget(self.list, 1)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(theme.PAD)
        self.open_button = ui.button("diag.open", self._open, "aeroRun", width=170)
        row.addWidget(self.open_button)
        row.addStretch(1)
        row.addWidget(ui.button("diag.refresh", self.refresh, width=130))
        row.addWidget(ui.button("diag.close", self.close, width=96))
        layout.addLayout(row)
        self.refresh()

    def refresh(self):
        try:
            result = self._compute()
        except Exception as exc:
            self.summary.setText(i18n.t("diag.failed", error=repr(exc)))
            self.list.clear()
            return
        self.result = result
        self.summary.setText(summary_text(result))
        self.list.clear()
        if diagnostic.is_clean(result) and result["summary"]["observations"]:
            self._add(i18n.t("diag.clean"), None, False)
        for text, item, title in lines(result):
            self._add(text, item, title)
        self._update()

    def _add(self, text, item, title):
        entry = QtWidgets.QListWidgetItem(text)
        if title:
            font = entry.font()
            font.setBold(True)
            entry.setFont(font)
            entry.setFlags(QtCore.Qt.ItemIsEnabled)
        elif item is None:
            entry.setFlags(QtCore.Qt.ItemIsEnabled)
        entry.setData(ROLE, item)
        self.list.addItem(entry)

    def _current(self):
        entry = self.list.currentItem()
        return entry.data(ROLE) if entry is not None else None

    def _update(self):
        item = self._current()
        self.open_button.setEnabled(item is not None and bool(item.get("label") or item.get("camera") is not None))

    def _open(self):
        item = self._current()
        if item is not None:
            self._opener(item)


def show(panel, compute, opener, existing=None):
    """Opens (or refreshes) the window. Returns the instance, to be kept."""
    dialog = existing
    try:
        if dialog is not None:
            dialog.objectName()
    except RuntimeError:
        dialog = None
    if dialog is None:
        dialog = DiagnosticDialog(compute, opener, panel)
    dialog.refresh()
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog

