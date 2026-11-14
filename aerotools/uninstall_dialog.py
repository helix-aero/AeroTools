"""Uninstall window (Qt): tick what to remove, one red button, one question.

The Aero menu launcher always goes; the settings and the AI environment are optional, with their size next to them.
The deletion runs in the window's task (an AI environment can hold thousands of files); the footer says what is being removed.
Metashape cannot take a menu entry away while it runs: the window says the menu disappears at the next start.
"""

from . import i18n, journal, uikit, uninstall, updater, workers
from .qt import QtWidgets

WIDTH = 520


class UninstallDialog(uikit.ToolWindow):
    def __init__(self, parent=None):
        super(UninstallDialog, self).__init__(parent, title=i18n.t("uninstall.title"), modal=True)
        self.completed = False
        self.boxes = {}
        self._found = {entry["id"]: entry for entry in uninstall.items()}
        body = QtWidgets.QWidget()
        body.setMinimumWidth(uikit.px(WIDTH))
        lay = uikit.vbox(spacing=uikit.tokens.L, parent=body)
        lay.addWidget(uikit.label(i18n.t("uninstall.intro"), "body", "muted"))
        for item_id in uninstall.ORDER:
            lay.addLayout(self._row(item_id))
        self.set_content(body)
        self.set_primary(i18n.t("uninstall.go"), self._on_ask, role="danger")
        self.next_button.setEnabled(any(entry["paths"] for entry in self._found.values()))

    def _row(self, item_id):
        entry = self._found[item_id]
        present = bool(entry["paths"])
        line = uikit.hbox(spacing=uikit.tokens.S)
        box = uikit.Toggle(i18n.t("uninstall." + item_id))
        box.setChecked(present and item_id in uninstall.DEFAULT_ON)
        box.setEnabled(present and uninstall.LOCKED.count(item_id) == 0)
        self.boxes[item_id] = box
        line.addWidget(box, 1)
        line.addWidget(uikit.label(updater.format_size(entry["bytes"]) if present else i18n.t("uninstall.absent"), "small", "muted",
                                   wrap=False))
        line.addWidget(uikit.InfoButton(i18n.t("uninstall." + item_id + "_tip", path=(entry["paths"] or [""])[0])))
        return line

    def ids(self):
        return [item_id for item_id, box in self.boxes.items() if box.isChecked()]

    def refresh_buttons(self, *_args):
        super(UninstallDialog, self).refresh_buttons()
        if hasattr(self, "boxes"):
            found = any(entry["paths"] for entry in self._found.values())
            self.next_button.setEnabled(found and not self.is_busy() and not self.completed)

    def _on_ask(self):
        """The red button: say what goes, then go on only if the operator confirms."""
        ids = self.ids()
        if self.is_busy() or self.completed or not ids:
            return
        names = "\n".join("• " + i18n.t("uninstall." + item_id) for item_id in ids)
        if uikit.confirm(self, i18n.t("uninstall.title"), i18n.t("uninstall.confirm", items=names), ok=i18n.t("uninstall.go"),
                         danger=True):
            self._on_go()

    def _on_go(self):
        if self.is_busy() or self.completed:
            return
        ids = self.ids()
        if not ids:
            return
        journal.write("Uninstall: {}".format(", ".join(ids)))            # before: removing the settings removes the log too
        for box in self.boxes.values():
            box.setEnabled(False)

        def work(progress):
            def say(item_id):
                workers.advance(progress, 0, 0, i18n.t("uninstall.working", item=i18n.t("uninstall." + item_id).lower()))
            try:
                return uninstall.remove(ids, say)
            except Exception as exc:
                return {"removed": [], "errors": {uninstall.MENU: repr(exc)}}

        self.run(work, name="uninstall", cancellable=False, indeterminate=True, retry=False, on_done=self._finish,
                 on_cancel=lambda: self._finish(self.runner().result or {}))

    def _finish(self, result):
        self.completed = True
        errors = result.get("errors", {})
        if errors:
            names = ", ".join(i18n.t("uninstall." + item_id).lower() for item_id in errors)
            self.show_banner("warning", i18n.t("uninstall.partial", items=names), details="\n".join(errors.values()))
        else:
            from . import bootstrap
            self.show_banner("success", i18n.t("uninstall.done"), details=i18n.t("uninstall.left", root=bootstrap.root()))
        self.next_button.hide()
        self.footer.set_cancel_mode("close")
        self.refresh_buttons()


def _run_modal(dialog):
    return (getattr(dialog, "exec_", None) or getattr(dialog, "exec"))()


def show(parent=None):
    dialog = UninstallDialog(parent)
    _run_modal(dialog)
    return dialog.completed
