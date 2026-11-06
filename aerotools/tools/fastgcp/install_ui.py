"""Install for me: runs the installer from the interface and waits for the end (Qt).

For users who are not developers: when an engine or the retraining tools are missing, a
button offers to install them. The installer (install_all.ps1) runs in a visible console
that displays "Please wait..."; this window stays in front of Metashape, says not to
close it, and says at the end whether everything went well.
"""

from ... import i18n, journal, ui
from ...qt import QtWidgets
from . import dialogs, engines

POLL_MS = 1000


def what_text(engine, training):
    """What is being installed, in one phrase: "the light engine (...)"."""
    parts = []
    if engine in ("light", "both"):
        parts.append(i18n.t(engines.WHAT_TEXT["light"]))
    if engine in ("precise", "both"):
        parts.append(i18n.t(engines.WHAT_TEXT["precise"]))
    if training:
        parts.append(i18n.t(engines.WHAT_TEXT["training"]))
    text = parts[0] if parts else ""
    for part in parts[1:]:
        text = i18n.t("engine.join", first=text, second=part)
    return text


class _Progress(QtWidgets.QDialog):
    """Modal window during the installation: text, endless bar, cancel."""

    def __init__(self, parent, what, job):
        super(_Progress, self).__init__(parent)
        self.job = job
        self.result = None
        self.setWindowTitle(i18n.t("install.title"))
        self.setModal(True)
        self.setMinimumWidth(460)
        root = ui.styled_root(self)
        layout = QtWidgets.QVBoxLayout(root)
        pad = 20
        layout.setContentsMargins(pad, pad, pad, pad)
        layout.setSpacing(14)
        layout.addWidget(ui.label(i18n.t("install.running", what=what)))
        bar = QtWidgets.QProgressBar()
        bar.setObjectName("aeroProgress")
        bar.setTextVisible(False)
        bar.setRange(0, 0)
        layout.addWidget(bar)
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        row.addWidget(ui.button("install.cancel", self._on_cancel, width=110))
        layout.addLayout(row)
        self._clock = ui.timer(self, POLL_MS, self._poll)
        self._clock.start()

    def _poll(self):
        try:
            result = self.job.poll()
        except Exception as exc:
            result = {"ok": False, "code": repr(exc)}
        if result is None:
            return
        self._clock.stop()
        self.result = result
        self.accept()

    def _on_cancel(self):
        if dialogs.ask(self, i18n.t("install.title"), i18n.t("install.cancel_confirm")):
            self.job.cancel()
            self._clock.stop()
            self.result = {"ok": False, "code": "cancelled"}
            self.accept()

    def reject(self):
        """Escape does not close the window: only the end or the Cancel button do."""
        return None

    def closeEvent(self, event):
        if self.result is None:
            event.ignore()
        else:
            super(_Progress, self).closeEvent(event)


def run(parent, engine=None, training=False, confirm=True):
    """Install `engine` ("light", "precise", "both" or None) and, if `training`, the retraining tools.

    Returns True if the installation finished correctly. Never raises."""
    try:
        what = what_text(engine, training)
        if confirm and not dialogs.ask(parent, i18n.t("install.title"), i18n.t("install.confirm", what=what)):
            return False
        if not engines.can_install():
            dialogs.inform(parent, i18n.t("install.title"), i18n.t("install.manual", script=engines.script_path()))
            return False
        try:
            job = engines.InstallJob(engine, training).start()
        except Exception as exc:
            journal.write("Installation: could not start: {!r}".format(exc))
            dialogs.inform(parent, i18n.t("install.title"), i18n.t("install.not_started", error=str(exc)))
            return False
        journal.write("Installation: {} started".format(what))
        window = _Progress(parent, what, job)
        ui.run(window)
        result = window.result or {"ok": False, "code": "?"}
        engines.forget()
        if result.get("ok"):
            journal.write("Installation: finished")
            dialogs.inform(parent, i18n.t("install.title"), i18n.t("install.done", what=what))
            return True
        journal.write("Installation: failed ({})".format(result.get("code")))
        if result.get("code") == "cancelled":
            dialogs.inform(parent, i18n.t("install.title"), i18n.t("install.cancelled"))
        else:
            dialogs.inform(parent, i18n.t("install.title"), i18n.t("install.failed", code=result.get("code")))
        return False
    except Exception as exc:
        journal.write("Installation: {!r}".format(exc))
        return False


# For the settings screen: is the engine installed or missing.
def state_text(installed):
    return i18n.t("engine.installed" if installed else "engine.missing")

