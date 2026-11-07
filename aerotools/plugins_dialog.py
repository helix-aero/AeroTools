"""Install plugins window (Qt): every tool of the suite, with a button to install it, update it or remove it.

One card per tool: its name, what it does, whether it is installed and what it weighs. "Install" downloads only that tool's files (and the
tools it needs, which the card says); the Aero menu gets its entry at once. A tool with choices to make (Aero-GCP: which AI engine) asks
them in a small window first, then runs the installer of that choice.

The network and disk work runs in a separate thread; the window follows it with a timer and stays in front. Without a connection the
cards still show what is installed and can remove it; only installing needs the server.
"""

import functools
import importlib
import threading

from . import config, controls, dialogs, i18n, journal, theme, ui, updater
from .qt import QtCore, QtWidgets

POLL_MS = 150
WIDTH = 600
ENGINE_CHOICES = (("none", "plugins.opt_none"), ("light", "plugins.opt_light"), ("precise", "plugins.opt_precise"),
                  ("both", "plugins.opt_both"))
OPTIONS = {"fastgcp": "engine"}                       # tools that ask something before they are installed


class _Job(threading.Thread):
    """Runs `work(job)` outside the Qt thread; the window reads `progress` and `result`."""

    def __init__(self, work):
        super(_Job, self).__init__(daemon=True)
        self.work = work
        self.progress = (0, 0)
        self.result = None
        self.finished = False
        self.cancelled = False

    def run(self):
        try:
            self.result = self.work(self)
        except Exception as exc:
            self.result = {"ok": False, "error": repr(exc)}
        self.finished = True


class EngineOptions(QtWidgets.QDialog):
    """Aero-GCP: which AI detection to install with it."""

    def __init__(self, title, parent=None):
        super(EngineOptions, self).__init__(parent)
        self.setWindowTitle(i18n.t("plugins.opt_title", title=title))
        self.setModal(True)
        self.setMinimumWidth(460)
        root = ui.styled_root(self)
        layout = QtWidgets.QVBoxLayout(root)
        pad = theme.PAD_L
        layout.setContentsMargins(pad, pad, pad, pad)
        layout.setSpacing(theme.PAD)
        layout.addWidget(ui.label(i18n.t("plugins.opt_intro")))
        self.radios = {}
        for value, key in ENGINE_CHOICES:
            radio = QtWidgets.QRadioButton(i18n.t(key))
            radio.setToolTip(i18n.t("plugins.opt_tip"))
            radio.toggled.connect(lambda *_args: self._update())
            layout.addWidget(radio)
            self.radios[value] = radio
        self.radios["light"].setChecked(True)
        self.training = QtWidgets.QCheckBox(i18n.t("plugins.opt_training"))
        self.training.setToolTip(i18n.t("plugins.opt_tip"))
        layout.addWidget(self.training)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(theme.PAD)
        row.addStretch(1)
        row.addWidget(ui.button("plugins.cancel", self.reject, width=96))
        row.addWidget(ui.button("plugins.opt_continue", self.accept, "aeroRun", width=130))
        layout.addLayout(row)
        self._update()

    def _update(self):
        self.training.setEnabled(not self.radios["none"].isChecked())
        if self.radios["none"].isChecked():
            self.training.setChecked(False)

    def choice(self):
        engine = next(value for value, radio in self.radios.items() if radio.isChecked())
        return (None if engine == "none" else engine), self.training.isChecked()


class _Card(object):
    """The widgets of one tool."""

    def __init__(self, entry, on_install, on_remove):
        self.entry = entry
        self.frame = QtWidgets.QFrame()
        self.frame.setObjectName("aeroCard")
        outer = QtWidgets.QHBoxLayout(self.frame)
        pad = theme.PAD + theme.PAD_S
        outer.setContentsMargins(pad, pad, pad, pad)
        outer.setSpacing(theme.PAD)
        left = QtWidgets.QVBoxLayout()
        left.setSpacing(theme.PAD_S)
        left.addWidget(ui.label(entry["title"], "aeroSection", wrap=False))
        left.addWidget(ui.label(i18n.t(entry["subtitle"]), "aeroMeta"))
        self.status = ui.label("", "aeroMeta")
        left.addWidget(self.status)
        self.bar = QtWidgets.QProgressBar()
        self.bar.setObjectName("aeroProgress")
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 1000)
        self.bar.setVisible(False)
        left.addWidget(self.bar)
        outer.addLayout(left, 1)
        buttons = QtWidgets.QVBoxLayout()
        buttons.setSpacing(theme.PAD_S)
        self.install = ui.button("plugins.install", functools.partial(on_install, entry["id"]), "aeroRun", width=120)
        self.remove = ui.button("plugins.remove", functools.partial(on_remove, entry["id"]), width=120)
        buttons.addWidget(self.install)
        buttons.addWidget(self.remove)
        outer.addLayout(buttons)


class PluginsDialog(controls.FollowsText, QtWidgets.QDialog):
    def __init__(self, parent=None):
        super(PluginsDialog, self).__init__(parent)
        self.setWindowTitle(i18n.t("plugins.title"))
        self.setModal(True)
        self.setFixedWidth(WIDTH)
        self.plugin = updater.plugins()[0]
        self.remote = None
        self.offline = ""
        self._job = None
        self._after = None
        self._busy_id = None
        root = ui.styled_root(self)
        layout = QtWidgets.QVBoxLayout(root)
        pad = theme.PAD_L
        layout.setContentsMargins(pad, pad, pad, pad)
        layout.setSpacing(theme.PAD)
        self.intro = ui.label(i18n.t("plugins.intro"))
        layout.addWidget(self.intro)
        self.cards = {}
        for entry in config.TOOLS:
            card = _Card(entry, self._on_install, self._on_remove)
            self.cards[entry["id"]] = card
            layout.addWidget(card.frame)
        self.footer = ui.label("", "aeroMeta")
        layout.addWidget(self.footer)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(theme.PAD)
        self.version = ui.label("", "aeroMeta", wrap=False)
        row.addWidget(self.version)
        row.addStretch(1)
        self.close_button = ui.button("plugins.close", self.accept, width=96)
        row.addWidget(self.close_button)
        layout.addLayout(row)
        self._clock = ui.timer(self, POLL_MS, self._poll)
        self._clock.start()
        self.refresh()
        self._launch(self._fetch, self._fetched)

    # -- state -----------------------------------------------------------------

    def _fetch(self, _job):
        try:
            remote, _raw = updater.remote_manifest(self.plugin)
            return {"ok": True, "remote": remote}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _fetched(self, result):
        if result.get("ok"):
            self.remote, self.offline = result["remote"], ""
        else:
            self.remote, self.offline = None, result.get("error", "")
        self.refresh()

    def refresh(self):
        """Cards from the disk (what is installed) and, when known, the server (sizes, updates)."""
        dev = updater.is_dev_copy(self.plugin)
        status = {item["id"]: item for item in updater.pack_status(self.plugin, self.remote)}
        for tool_id, card in self.cards.items():
            item = status[tool_id]
            busy = self._job is not None
            if item["installed"]:
                text = i18n.t("plugins.update" if item["update"] else "plugins.installed")
            else:
                text = i18n.t("plugins.not_installed", size=updater.format_size(item["bytes"]) if self.remote else "?")
                if item["needs"] and self.remote:
                    extra = sum(status[n]["bytes"] for n in item["needs"])
                    names = ", ".join(config.tool(n)["title"] for n in item["needs"])
                    text += "\n" + i18n.t("plugins.needs", names=names, size=updater.format_size(extra))
            card.status.setText(text)
            card.install.setText(i18n.t("plugins.update_button" if item["installed"] else "plugins.install"))
            card.install.setVisible(not dev and (not item["installed"] or bool(item["update"])))
            card.install.setEnabled(not busy and self.remote is not None)
            card.remove.setVisible(not dev and item["installed"])
            card.remove.setEnabled(not busy)
        if dev:
            self._say(i18n.t("plugins.dev"))
        elif self.offline and self._job is None:
            self._say(i18n.t("plugins.offline", message=self.offline), "error")
        info = updater.installed_info(self.plugin)
        version = (self.remote or {}).get("version") or info["version"]
        self.version.setText(i18n.t("plugins.version", version=version) if version else "")

    def _say(self, text, kind=None):
        self.footer.setText(text)
        self.footer.setStyleSheet("color: {};".format(theme.ORANGE_BRIGHT) if kind == "error" else "")

    # -- jobs ------------------------------------------------------------------

    def _launch(self, work, after):
        job = _Job(work)
        self._job, self._after = job, after
        job.start()

    def _poll(self):
        job = self._job
        if job is None:
            return
        if not job.finished:
            done, total = job.progress
            if self._busy_id and total:
                self.cards[self._busy_id].bar.setValue(int(1000.0 * done / total))
                self._say(i18n.t("plugins.installing", title=config.tool(self._busy_id)["title"],
                                 done=updater.format_size(done), total=updater.format_size(total)))
            return
        self._job = None
        after, self._after = self._after, None
        if self._busy_id:
            self.cards[self._busy_id].bar.setVisible(False)
        self._busy_id = None
        after(job.result or {})

    def _begin(self, tool_id, text):
        self._busy_id = tool_id
        card = self.cards[tool_id]
        card.bar.setValue(0)
        card.bar.setVisible(True)
        self._say(text)
        for other in self.cards.values():
            other.install.setEnabled(False)
            other.remove.setEnabled(False)
        self.close_button.setEnabled(False)

    # -- install -----------------------------------------------------------------

    def _on_install(self, tool_id):
        if self._job is not None:
            return
        entry = config.tool(tool_id)
        choice = None
        if OPTIONS.get(tool_id) == "engine" and not updater.tool_installed(tool_id):
            options = EngineOptions(entry["title"], self)
            if not ui.run(options):
                return
            choice = options.choice()
        self._begin(tool_id, i18n.t("plugins.installing", title=entry["title"], done="0 B", total="?"))

        def work(job):
            def progress(done, total, _text):
                job.progress = (done, total)
            return updater.install_pack(self.plugin, tool_id, progress, lambda: job.cancelled)

        self._launch(work, functools.partial(self._installed, tool_id, choice))

    def _installed(self, tool_id, choice, result):
        self.close_button.setEnabled(True)
        title = config.tool(tool_id)["title"]
        if not result.get("ok"):
            self.refresh()
            self._say(i18n.t("plugins.failed", message=result.get("error", "")), "error")
            return
        from . import bootstrap
        for pack_id in result.get("packs", []):
            bootstrap.add_tool_menu(pack_id)
        self.refresh()
        self._say(i18n.t("plugins.done_install", title=title))
        if choice and (choice[0] or choice[1]):
            self._install_engine(choice)

    def _install_engine(self, choice):
        """Aero-GCP: its files are there, now the detection engine of the choice (the installer's own window runs)."""
        try:
            module = importlib.import_module(config.pack("fastgcp")["setup"])
            module.run(self, choice[0], choice[1], confirm=False)
        except Exception as exc:
            journal.write("plugins: engine install failed: {!r}".format(exc))

    # -- remove ------------------------------------------------------------------

    def _on_remove(self, tool_id):
        if self._job is not None:
            return
        entry = config.tool(tool_id)
        users = [p for p in config.dependents(tool_id) if updater.tool_installed(p)]
        if users:
            dialogs.inform(self, entry["title"], i18n.t("plugins.needed_by", title=entry["title"],
                                                         others=", ".join(config.tool(p)["title"] for p in users)))
            return
        if not dialogs.ask(self, entry["title"], i18n.t("plugins.confirm_remove", title=entry["title"])):
            return
        from . import bootstrap
        bootstrap.forget_tool(tool_id)                         # closes its windows before the files go
        self._begin(tool_id, i18n.t("plugins.removing", title=entry["title"]))
        self._launch(lambda _job: updater.remove_pack(self.plugin, tool_id), functools.partial(self._removed, tool_id))

    def _removed(self, tool_id, result):
        self.close_button.setEnabled(True)
        self.refresh()
        if result.get("ok"):
            self._say(i18n.t("plugins.done_remove", title=config.tool(tool_id)["title"]))
        else:
            self._say(i18n.t("plugins.failed", message=result.get("error", "")), "error")

    # -- closing -----------------------------------------------------------------

    def closeEvent(self, event):
        if self._job is not None and self._busy_id:
            event.ignore()
            return
        super(PluginsDialog, self).closeEvent(event)

    def reject(self):
        if self._job is None or not self._busy_id:
            super(PluginsDialog, self).reject()


def show(parent=None):
    dialog = PluginsDialog(parent)
    ui.run(dialog)
