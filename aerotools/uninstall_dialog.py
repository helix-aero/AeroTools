"""Uninstall window (Qt): tick what to remove, one button.

The Aero menu launcher always goes; the settings and the AI environment are optional, with their size next to them.
The deletion runs in a separate thread (an AI environment can hold thousands of files); the window follows it with a timer.
Metashape cannot take a menu entry away while it runs: the window says the menu disappears at the next start.
"""

import threading

from . import controls, i18n, journal, theme, ui, uninstall, updater
from .qt import QtCore, QtWidgets

POLL_MS = 150
WIDTH = 520


class UninstallDialog(controls.FollowsText, QtWidgets.QDialog):
    def __init__(self, parent=None):
        super(UninstallDialog, self).__init__(parent)
        self.setWindowTitle(i18n.t("uninstall.title"))
        self.setModal(True)
        self.setFixedWidth(WIDTH)
        self.completed = False
        self._worker = None
        self._current = ""
        root = ui.styled_root(self)
        layout = QtWidgets.QVBoxLayout(root)
        pad = theme.PAD_L
        layout.setContentsMargins(pad, pad, pad, pad)
        layout.setSpacing(theme.PAD)
        layout.addWidget(ui.label(i18n.t("uninstall.intro")))
        self.boxes = {}
        self._found = {entry["id"]: entry for entry in uninstall.items()}
        for item_id in uninstall.ORDER:
            layout.addWidget(self._row(item_id))
        layout.addStretch(1)
        self.status = ui.label("", "aeroMeta")
        layout.addWidget(self.status)
        buttons = QtWidgets.QHBoxLayout()
        buttons.setSpacing(theme.PAD)
        buttons.addStretch(1)
        self.close_button = ui.button("uninstall.cancel", self.reject, width=96)
        buttons.addWidget(self.close_button)
        self.go_button = ui.button("uninstall.go", self._on_go, "aeroNo", width=130)
        buttons.addWidget(self.go_button)
        layout.addLayout(buttons)
        self._clock = ui.timer(self, POLL_MS, self._poll)
        self.go_button.setEnabled(any(entry["paths"] for entry in self._found.values()))

    def _row(self, item_id):
        entry = self._found[item_id]
        frame = QtWidgets.QFrame()
        frame.setObjectName("aeroCard")
        line = QtWidgets.QHBoxLayout(frame)
        pad = theme.PAD + theme.PAD_S
        line.setContentsMargins(pad, pad, pad, pad)
        line.setSpacing(theme.PAD)
        box = QtWidgets.QCheckBox(i18n.t("uninstall." + item_id))
        box.setToolTip(i18n.t("uninstall." + item_id + "_tip", path=(entry["paths"] or [""])[0]))
        present = bool(entry["paths"])
        locked = item_id in uninstall.LOCKED
        box.setChecked(present and item_id in uninstall.DEFAULT_ON)
        box.setEnabled(present and not locked)
        self.boxes[item_id] = box
        line.addWidget(box, 1)
        size = updater.format_size(entry["bytes"]) if present else i18n.t("uninstall.absent")
        line.addWidget(ui.label(size, "aeroMeta", wrap=False))
        return frame

    def ids(self):
        return [item_id for item_id, box in self.boxes.items() if box.isChecked()]

    def _on_go(self):
        if self._worker is not None or self.completed:
            return
        ids = self.ids()
        if not ids:
            return
        journal.write("Uninstall: {}".format(", ".join(ids)))            # before: removing the settings removes the log too
        for box in self.boxes.values():
            box.setEnabled(False)
        self.go_button.setEnabled(False)
        self.close_button.setEnabled(False)
        worker = _Worker(ids, self._progress)
        self._worker = worker
        self._clock.start()
        worker.start()
        self._poll()

    def _progress(self, item_id):
        self._current = item_id

    def _poll(self):
        worker = self._worker
        if worker is None:
            return
        if not worker.finished:
            item = i18n.t("uninstall." + self._current).lower() if self._current else ""
            self.status.setText(i18n.t("uninstall.working", item=item))
            return
        self._clock.stop()
        self._worker = None
        self._finish(worker.result)

    def _finish(self, result):
        self.completed = True
        errors = result.get("errors", {})
        if errors:
            names = ", ".join(i18n.t("uninstall." + item_id).lower() for item_id in errors)
            self.status.setText(i18n.t("uninstall.partial", items=names))
            self.status.setToolTip("\n".join(errors.values()))
        else:
            from . import bootstrap
            self.status.setText(i18n.t("uninstall.done", root=bootstrap.root()))
        self.go_button.setVisible(False)
        self.close_button.setText(i18n.t("uninstall.close"))
        self.close_button.setEnabled(True)
        self.refit()

    def closeEvent(self, event):
        if self._worker is not None:
            event.ignore()
            return
        super(UninstallDialog, self).closeEvent(event)

    def reject(self):
        if self._worker is None:
            super(UninstallDialog, self).reject()


class _Worker(threading.Thread):
    def __init__(self, ids, progress):
        super(_Worker, self).__init__(daemon=True)
        self.ids = ids
        self.progress = progress
        self.result = {}
        self.finished = False

    def run(self):
        try:
            self.result = uninstall.remove(self.ids, self.progress)
        except Exception as exc:
            self.result = {"removed": [], "errors": {uninstall.MENU: repr(exc)}}
        self.finished = True


def show(parent=None):
    dialog = UninstallDialog(parent)
    ui.run(dialog)
    return dialog.completed
