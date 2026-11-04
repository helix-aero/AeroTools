"""« Installer pour moi » : lance l'installeur depuis l'interface et attend la fin (Qt).

Pour des utilisateurs qui ne sont pas des developpeurs : quand un moteur ou les outils de
reentrainement manquent, un bouton propose de les installer. L'installeur (install_all.ps1)
tourne dans une console visible qui affiche « Please wait... » ; cette fenetre reste devant
Metashape, indique de ne pas fermer, et dit a la fin si tout s'est bien passe.
"""

from ... import i18n, journal, ui
from ...qt import QtWidgets
from . import dialogs, engines

POLL_MS = 1000


def what_text(engine, training):
    """Ce qu'on installe, en une phrase : « le moteur leger (...) »."""
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
    """Fenetre modale pendant l'installation : texte, barre sans fin, annulation."""

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
        """Echap ne ferme pas la fenetre : seule la fin ou le bouton Annuler le font."""
        return None

    def closeEvent(self, event):
        if self.result is None:
            event.ignore()
        else:
            super(_Progress, self).closeEvent(event)


def run(parent, engine=None, training=False, confirm=True):
    """Installe `engine` ("light", "precise", "both" ou None) et, si `training`, les outils de reentrainement.

    Renvoie True si l'installation s'est terminee correctement. Ne leve jamais."""
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
            journal.write("Installation : lancement impossible : {!r}".format(exc))
            dialogs.inform(parent, i18n.t("install.title"), i18n.t("install.not_started", error=str(exc)))
            return False
        journal.write("Installation : {} lancee".format(what))
        window = _Progress(parent, what, job)
        ui.run(window)
        result = window.result or {"ok": False, "code": "?"}
        engines.forget()
        if result.get("ok"):
            journal.write("Installation : terminee")
            dialogs.inform(parent, i18n.t("install.title"), i18n.t("install.done", what=what))
            return True
        journal.write("Installation : echec ({})".format(result.get("code")))
        if result.get("code") == "cancelled":
            dialogs.inform(parent, i18n.t("install.title"), i18n.t("install.cancelled"))
        else:
            dialogs.inform(parent, i18n.t("install.title"), i18n.t("install.failed", code=result.get("code")))
        return False
    except Exception as exc:
        journal.write("Installation : {!r}".format(exc))
        return False


# Pour l'ecran de reglages : qu'a-t-on installe, dans quelle langue.
def state_text(installed):
    return i18n.t("engine.installed" if installed else "engine.missing")

