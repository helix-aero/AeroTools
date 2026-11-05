"""Fenetre « Mises a jour » (Qt) : les modules installes, leur date, ce qui est disponible.

Une ligne par module installe (aujourd'hui : un seul) avec sa version, la date de la derniere
mise a jour recue, et ce que le depot propose (nombre de fichiers et poids a telecharger). Un bouton
« Mettre a jour » a droite de chaque ligne ; en bas a droite, « Tout mettre a jour ».

Le travail (reseau, disque) se fait dans un fil a part ; la fenetre le suit par un minuteur, reste
utilisable et dit de ne pas la fermer pendant une mise a jour. A la fin les fichiers sont relus et
verifies, puis a la fermeture AeroTools recharge son code (sans redemarrer Metashape).
"""

import functools
import threading

from . import i18n, journal, theme, ui, updater
from .qt import QtCore, QtWidgets

POLL_MS = 150
WIDTH = 640
CHANNELS = (("main", "update.channel_stable"), ("dev", "update.channel_test"))


class _Worker(threading.Thread):
    """Execute `job(worker)` hors du fil Qt ; `progress` et `result` se lisent depuis la fenetre."""

    def __init__(self, job):
        super(_Worker, self).__init__(daemon=True)
        self.job = job
        self.progress = (0, 0, "")
        self.result = None
        self.finished = False
        self.cancel = False

    def run(self):
        try:
            self.result = self.job(self)
        except Exception as exc:
            self.result = {"error": repr(exc)}
        self.finished = True


class _Row(object):
    """Les widgets d'un module."""

    def __init__(self, plugin, on_update):
        self.plugin = plugin
        self.frame = QtWidgets.QFrame()
        self.frame.setObjectName("aeroCard")
        outer = QtWidgets.QHBoxLayout(self.frame)
        pad = theme.PAD + theme.PAD_S
        outer.setContentsMargins(pad, pad, pad, pad)
        outer.setSpacing(theme.PAD)
        left = QtWidgets.QVBoxLayout()
        left.setSpacing(theme.PAD_S)
        self.title = ui.label(plugin["title"], "aeroSection", wrap=False)
        left.addWidget(self.title)
        self.info = ui.label("", "aeroMeta")
        left.addWidget(self.info)
        self.status = ui.label("")
        self.mark = ui.Mark()
        line = QtWidgets.QHBoxLayout()
        line.setSpacing(theme.PAD_S)
        line.addWidget(self.mark, 0, QtCore.Qt.AlignTop)
        line.addWidget(self.status, 1)
        left.addLayout(line)
        self.bar = QtWidgets.QProgressBar()
        self.bar.setObjectName("aeroProgress")
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 1000)
        self.bar.setVisible(False)
        left.addWidget(self.bar)
        outer.addLayout(left, 1)
        self.button = ui.button("update.update", functools.partial(on_update, plugin["id"]), "aeroRun", width=150)
        outer.addWidget(self.button, 0, QtCore.Qt.AlignVCenter)

    def set_status(self, text, kind=None):
        """Texte d'etat ; `kind` : "ok" (coche verte), "error" (croix orange) ou rien."""
        self.status.setText(text)
        self.mark.show_kind(kind)

    def show_info(self):
        data = updater.installed_info(self.plugin)
        parts = [i18n.t("update.version", version=data["version"]) if data["version"] else i18n.t("update.version_unknown")]
        if data["updated"]:
            parts.append(i18n.t("update.updated_on", date=updater.describe_time(data["updated"])))
        elif data["built"]:
            parts.append(i18n.t("update.built_on", date=updater.describe_time(data["built"])))
        else:
            parts.append(i18n.t("update.never"))
        self.info.setText("  ·  ".join(parts))


class UpdateDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super(UpdateDialog, self).__init__(parent)
        self.setWindowTitle(i18n.t("update.title"))
        self.setModal(True)
        self.setMinimumHeight(320)
        self.resize(WIDTH, 340)
        self.updated = False
        self._worker = None
        self._results = {}
        root = ui.styled_root(self)
        layout = QtWidgets.QVBoxLayout(root)
        pad = theme.PAD_L
        layout.setContentsMargins(pad, pad, pad, pad)
        layout.setSpacing(theme.PAD)
        layout.addWidget(ui.label(i18n.t("update.intro")))
        self.rows = {}
        for plugin in updater.plugins():
            row = _Row(plugin, self._on_update_one)
            self.rows[plugin["id"]] = row
            layout.addWidget(row.frame)
            row.show_info()
        layout.addStretch(1)
        self.footer = ui.label("", "aeroMeta")
        self.footer_mark = ui.Mark()
        foot = QtWidgets.QHBoxLayout()
        foot.setSpacing(theme.PAD_S)
        foot.addWidget(self.footer_mark, 0, QtCore.Qt.AlignTop)
        foot.addWidget(self.footer, 1)
        layout.addLayout(foot)
        buttons = QtWidgets.QHBoxLayout()
        buttons.setSpacing(theme.PAD)
        self.channel = QtWidgets.QComboBox()
        self.channel.setObjectName("aeroCombo")
        self.channel.setProperty("arrow", True)
        self.channel.setCursor(QtCore.Qt.PointingHandCursor)
        self.channel.setFocusPolicy(QtCore.Qt.NoFocus)
        self.channel.setMinimumWidth(130)
        self.channel.setToolTip(i18n.t("update.channel_tip"))
        for value, key in CHANNELS:
            self.channel.addItem(i18n.t(key), value)
        current = updater.channel()
        if self.channel.findData(current) < 0:
            self.channel.addItem(current, current)       # branche saisie a la main dans settings.json
        self.channel.setCurrentIndex(self.channel.findData(current))
        self.channel.activated.connect(lambda _index: self._on_channel())
        buttons.addWidget(self.channel)
        buttons.addStretch(1)
        self.check_button = ui.button("update.check", self._start_check, width=200)
        buttons.addWidget(self.check_button)
        self.all_button = ui.button("update.update_all", self._on_update_all, "aeroRun", width=170)
        buttons.addWidget(self.all_button)
        self.close_button = ui.button("update.close", self.accept, width=96)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)
        self._clock = ui.timer(self, POLL_MS, self._poll)
        self._update_buttons()
        QtCore.QTimer.singleShot(0, self._start_check)

    # -- etat des boutons --------------------------------------------------------

    def _busy(self):
        return self._worker is not None

    def _set_footer(self, text, kind=None):
        self.footer.setText(text)
        self.footer_mark.show_kind(kind if text else None)

    def _available(self):
        return [pid for pid, result in self._results.items() if result.get("state") == "update"]

    def _update_buttons(self):
        busy = self._busy()
        self.check_button.setEnabled(not busy)
        self.channel.setEnabled(not busy)
        self.close_button.setEnabled(not busy)
        self.all_button.setEnabled(not busy and bool(self._available()))
        for pid, row in self.rows.items():
            result = self._results.get(pid, {})
            row.button.setEnabled(not busy and result.get("state") == "update")
            row.button.setVisible(result.get("state") == "update" or busy)

    def _show_result(self, pid):
        row, result = self.rows[pid], self._results[pid]
        state = result.get("state")
        row.show_info()
        if state == "update":
            row.set_status(i18n.t("update.status_available", n=len(result["changes"]),
                                  size=updater.format_size(result["bytes"])))
            shown = [c["path"] for c in result["changes"][:12]]
            more = len(result["changes"]) - len(shown)
            row.status.setToolTip("\n".join(shown + (["..."] if more > 0 else [])))
        elif state == "current":
            row.set_status(i18n.t("update.status_current"), "ok")
            row.status.setToolTip("")
        elif state == "dev":
            row.set_status(i18n.t("update.status_dev"))
        else:
            row.set_status(i18n.t("update.status_error", message=result.get("message", "")), "error")

    # -- verification ------------------------------------------------------------

    def _on_channel(self):
        """Stable ou Test : on retient le choix puis on verifie tout de suite sur cette branche."""
        value = self.channel.currentData()
        if self._busy() or not value or value == updater.channel():
            return
        updater.set_channel(value)
        journal.write("Mises a jour : branche suivie = {}".format(value))
        self._results.clear()
        self._update_buttons()
        self._start_check()

    def _start_check(self):
        if self._busy():
            return
        for row in self.rows.values():
            row.set_status(i18n.t("update.status_checking"))
            row.bar.setVisible(False)
        self._set_footer("")

        def job(_worker):
            return {plugin["id"]: updater.check(plugin) for plugin in updater.plugins()}

        self._launch(job, self._checked)

    def _checked(self, result):
        for pid, state in (result or {}).items():
            if pid in self.rows:
                self._results[pid] = state
                self._show_result(pid)
        if not self._available():
            self._set_footer("")

    # -- mise a jour -------------------------------------------------------------

    def _on_update_one(self, pid):
        self._start_update([pid])

    def _on_update_all(self):
        self._start_update(self._available())

    def _start_update(self, ids):
        if self._busy() or not ids:
            return
        plugins = [p for p in updater.plugins() if p["id"] in ids]
        for pid in ids:
            self.rows[pid].bar.setValue(0)
            self.rows[pid].bar.setVisible(True)
            self.rows[pid].set_status(i18n.t("update.status_updating", done="0 B", total="..."))
        self._set_footer(i18n.t("update.warning"))

        def job(worker):
            out = {}
            for plugin in plugins:
                def progress(done, total, text, pid=plugin["id"]):
                    worker.progress = (pid, done, total, text)
                applied = updater.apply(plugin, progress=progress, cancelled=lambda: worker.cancel)
                entry = {"apply": applied}
                if applied.get("ok"):
                    worker.progress = (plugin["id"], -1, -1, "")
                    count, bad = updater.verify(plugin)
                    entry["verify"] = (count, bad)
                out[plugin["id"]] = entry
            return out

        self._launch(job, self._updated)

    def _updated(self, result):
        problems = False
        for pid, entry in (result or {}).items():
            row = self.rows[pid]
            row.bar.setVisible(False)
            applied = entry.get("apply") or {}
            if not applied.get("ok"):
                row.set_status(i18n.t("update.status_failed", message=applied.get("error", "")), "error")
                problems = True
                continue
            if applied.get("updated"):
                self.updated = True
            count, bad = entry.get("verify", (0, []))
            if bad:
                row.set_status(i18n.t("update.status_verify_bad", n=len(bad)), "error")
                problems = True
            else:
                row.set_status(i18n.t("update.status_done", n=count), "ok")
            row.show_info()
            self._results[pid] = {"state": "current", "changes": [], "bytes": 0}
        self._set_footer(i18n.t("update.finished") if self.updated and not problems else "", "ok")

    # -- fil de travail ----------------------------------------------------------

    def _launch(self, job, when_done):
        self._worker = _Worker(job)
        self._when_done = when_done
        self._update_buttons()
        self._worker.start()
        self._clock.start()

    def _poll(self):
        worker = self._worker
        if worker is None:
            self._clock.stop()
            return
        progress = worker.progress
        if len(progress) == 4 and progress[0] in self.rows:
            pid, done, total, text = progress
            row = self.rows[pid]
            if done < 0:
                row.bar.setRange(0, 0)
                row.set_status(i18n.t("update.status_verifying"))
            else:
                row.bar.setRange(0, 1000)
                row.bar.setValue(int(1000 * done / float(max(1, total))))
                row.set_status(i18n.t("update.status_updating", done=updater.format_size(done),
                                      total=updater.format_size(total)))
        if not worker.finished:
            return
        self._clock.stop()
        self._worker = None
        try:
            self._when_done(worker.result)
        except Exception as exc:
            journal.write("Mises a jour : affichage : {!r}".format(exc))
        self._update_buttons()

    # -- vie de la fenetre -------------------------------------------------------

    def closeEvent(self, event):
        if self._busy():
            event.ignore()
            return
        super(UpdateDialog, self).closeEvent(event)

    def reject(self):
        if not self._busy():
            super(UpdateDialog, self).reject()


def show(parent=None):
    """Ouvre la fenetre (modale). Renvoie True si une mise a jour a ete installee."""
    dialog = UpdateDialog(parent)
    ui.run(dialog)
    return dialog.updated


def open_and_reload(parent=None):
    """Ouvre la fenetre ; si du code a change, recharge AeroTools (comme « Recharger AeroTools »)."""
    updated = show(parent)
    if updated:
        from . import bootstrap
        QtCore.QTimer.singleShot(0, bootstrap.reload_all)
    return updated

