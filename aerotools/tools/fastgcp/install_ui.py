"""Install for me: runs the installer from the interface and waits for the end (Qt).

For users who are not developers: when an engine or the retraining tools are missing, a
button offers to install them. The installer (install_all.ps1) runs in a visible console
that displays "Please wait..."; this window stays in front of Metashape, says not to
close it, and says at the end whether everything went well.
"""

from ... import i18n, journal, uikit
from ...qt import QtCore, QtWidgets
from . import engines

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


class _Progress(uikit.ToolWindow):
    """Modal window during the installation: text, endless bar, cancel."""

    def __init__(self, parent, what, job):
        super(_Progress, self).__init__(parent, title=i18n.t("install.title"), info=i18n.t("install.note"), modal=True,
                                        design_size=(520, 0))
        self.job = job
        self.result = None
        body = QtWidgets.QWidget()
        lay = uikit.vbox(spacing=uikit.tokens.M, parent=body)
        lay.addWidget(uikit.label(i18n.t("install.running", what=what)))
        self.row = uikit.ProgressRow()
        self.row.set_indeterminate("")
        lay.addWidget(self.row)
        self.set_content(body)
        self.next_button.hide()                      # nothing to confirm: the window closes by itself at the end
        self._clock = QtCore.QTimer(self)
        self._clock.setInterval(POLL_MS)
        self._clock.timeout.connect(self._poll)
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
        if uikit.confirm(self, i18n.t("install.title"), i18n.t("install.cancel_confirm"), ok=i18n.t("install.stop"),
                         cancel=i18n.t("install.keep")):
            self.job.cancel()
            self._clock.stop()
            self.result = {"ok": False, "code": "cancelled"}
            self.accept()

    def reject(self):
        """Escape and Cancel ask first: only the end of the installation or a confirmed stop close the window."""
        self._on_cancel()

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
        if confirm and not uikit.confirm(parent, i18n.t("install.title"), i18n.t("install.confirm", what=what), ok=i18n.t("settings.install")):
            return False
        if not engines.can_install():
            uikit.inform(parent, i18n.t("install.title"), i18n.t("install.manual", script=engines.script_path()), "warning")
            return False
        try:
            job = engines.InstallJob(engine, training).start()
        except Exception as exc:
            journal.write("Installation: could not start: {!r}".format(exc))
            uikit.error(parent, i18n.t("install.title"), i18n.t("install.not_started", error=str(exc)))
            return False
        journal.write("Installation: {} started".format(what))
        window = _Progress(parent, what, job)
        (getattr(window, "exec_", None) or getattr(window, "exec"))()
        result = window.result or {"ok": False, "code": "?"}
        engines.forget()
        if result.get("ok"):
            journal.write("Installation: finished")
            uikit.inform(parent, i18n.t("install.title"), i18n.t("install.done", what=what), "success")
            return True
        journal.write("Installation: failed ({})".format(result.get("code")))
        if result.get("code") == "cancelled":
            uikit.inform(parent, i18n.t("install.title"), i18n.t("install.cancelled"))
        else:
            uikit.error(parent, i18n.t("install.title"), i18n.t("install.failed", code=result.get("code")))
        return False
    except Exception as exc:
        journal.write("Installation: {!r}".format(exc))
        return False


# For the settings screen: is the engine installed or missing.
def state_text(installed):
    return i18n.t("engine.installed" if installed else "engine.missing")

