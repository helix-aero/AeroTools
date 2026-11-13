"""Install plugins window (Qt): every tool of the suite, with a button to install it, update it or remove it.

One row per tool: its name (the "i" says what it does), whether it is installed and what it weighs. "Install" downloads only that tool's
files (and the tools it needs, which the row says); the Aero menu gets its entry at once. A tool with choices to make (Aero-GCP: which AI
engine) asks them in a small window first, then runs the installer of that choice.

The network and disk work runs in the window's task (`ToolWindow.run`, a worker thread): the footer shows its progress and the window
stays in front. Without a connection the rows still show what is installed and can remove it; only installing needs the server. Reading the
installed files (digests, to tell what needs an update) is part of that work: the window itself only reads what the task brought back.

A tool that needs a Python package this Metashape does not have (config.PACKS `requires_modules`) says so on its row and asks before
downloading. When an installation replaced files of the core, the code in memory is out of date: AeroTools reloads once the window closes.
"""

import functools
import importlib

from . import config, i18n, journal, uikit, updater, workers
from .qt import QtCore, QtGui, QtWidgets

ENGINE_CHOICES = (("none", "plugins.opt_none"), ("light", "plugins.opt_light"), ("precise", "plugins.opt_precise"),
                  ("both", "plugins.opt_both"))
OPTIONS = {"fastgcp": "engine"}                       # tools that ask something before they are installed


def _safe_status(plugin, remote):
    """updater.pack_status for the worker thread: never raises (the window then shows what the disk alone says)."""
    try:
        return updater.pack_status(plugin, remote)
    except Exception as exc:
        journal.write("plugins: status failed: {!r}".format(exc))
        return None


def _run_modal(dialog):
    return (getattr(dialog, "exec_", None) or getattr(dialog, "exec"))()


class EngineOptions(uikit.ToolWindow):
    """Aero-GCP: which AI detection to install with it."""

    def __init__(self, title, parent=None):
        super(EngineOptions, self).__init__(parent, title=i18n.t("plugins.opt_title", title=title), info=i18n.t("plugins.opt_tip"),
                                            modal=True)
        body = QtWidgets.QWidget()
        lay = uikit.vbox(spacing=uikit.tokens.M, parent=body)
        lay.addWidget(uikit.label(i18n.t("plugins.opt_intro")))
        self.training = uikit.Toggle(i18n.t("plugins.opt_training"))
        self.radios = {}
        group = QtWidgets.QButtonGroup(self)
        for value, key in ENGINE_CHOICES:
            radio = QtWidgets.QRadioButton(i18n.t(key))
            radio.setCursor(QtCore.Qt.PointingHandCursor)
            radio.toggled.connect(lambda *_args: self._update())
            group.addButton(radio)
            lay.addWidget(radio)
            self.radios[value] = radio
        self.radios["light"].setChecked(True)
        lay.addSpacing(uikit.px(uikit.tokens.S))
        lay.addWidget(self.training)
        self.set_content(body)
        self.set_primary(i18n.t("plugins.opt_continue"), self.accept)
        self._update()

    def _update(self):
        self.training.setEnabled(not self.radios["none"].isChecked())
        if self.radios["none"].isChecked():
            self.training.setChecked(False)

    def choice(self):
        engine = next(value for value, radio in self.radios.items() if radio.isChecked())
        return (None if engine == "none" else engine), self.training.isChecked()


TOOL_GLYPH = {
    "fastgcp": "gcp",
    "fastspace": "space",
    "aeroexports": "exports",
    "aerol3": "l3",
}


class _ToolIcon(QtWidgets.QWidget):
    """The tool's glyph in the list row, painted from the tokens (so it follows the skin)."""

    def __init__(self, tool_id, parent=None):
        super(_ToolIcon, self).__init__(parent)
        self._name = "glyph_" + TOOL_GLYPH.get(tool_id, "suite")
        self.setFixedSize(uikit.px(uikit.tokens.ICON_L), uikit.px(uikit.tokens.ICON_L))

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        uikit.icons.paint(p, self._name, QtCore.QRectF(self.rect()), uikit.tokens.ACCENT)
        p.end()


class _Card(object):
    """The widgets of one tool: name and "i", status line, Install / Update and Remove."""

    def __init__(self, entry, on_install, on_remove):
        self.entry = entry
        self.frame = QtWidgets.QWidget()
        outer = uikit.hbox(spacing=uikit.tokens.M, parent=self.frame)
        left = uikit.vbox(spacing=uikit.tokens.XS)
        head = uikit.hbox(spacing=uikit.tokens.S)
        head.addWidget(_ToolIcon(entry["id"]))
        head.addWidget(uikit.label(entry["title"], "strong", wrap=False))
        head.addWidget(uikit.InfoButton(i18n.t(entry["subtitle"])))
        head.addStretch(1)
        left.addLayout(head)
        self.status = uikit.label("", "status", "muted")
        self.glyph = uikit.Glyph("pending", 16)                     # status is a colour, an icon and a word
        line = uikit.hbox(spacing=uikit.tokens.S)
        line.addWidget(self.glyph, 0, QtCore.Qt.AlignTop)
        line.addWidget(self.status, 1)
        left.addLayout(line)
        outer.addLayout(left, 1)
        self.install = uikit.Button(i18n.t("plugins.install"), "secondary", handler=functools.partial(on_install, entry["id"]))
        self.remove = uikit.Button(i18n.t("plugins.remove"), "quiet", handler=functools.partial(on_remove, entry["id"]))
        outer.addWidget(self.install, 0, QtCore.Qt.AlignVCenter)
        outer.addWidget(self.remove, 0, QtCore.Qt.AlignVCenter)


class PluginsDialog(uikit.ToolWindow):
    MARK = "tool_suite"
    CLOSE_ONLY = True

    def __init__(self, parent=None):
        super(PluginsDialog, self).__init__(parent, title=i18n.t("plugins.title"), info=i18n.t("plugins.intro"), modal=True)
        self.plugin = updater.plugins()[0]
        self.remote = None
        self.offline = ""
        self.core_changed = False                         # files of the core were replaced: reload the code when the window closes
        self._status = None                               # pack_status computed by the task (None: only the marker files are looked at)
        self._modules = {entry["id"]: config.missing_modules(entry["id"]) for entry in config.TOOLS}
        body = QtWidgets.QWidget()
        body.setMinimumWidth(uikit.px(640))
        lay = uikit.vbox(spacing=uikit.tokens.XS, parent=body)
        self.cards = {}
        for index, entry in enumerate(config.TOOLS):
            if index:
                lay.addWidget(uikit.Divider())
            card = _Card(entry, self._on_install, self._on_remove)
            self.cards[entry["id"]] = card
            lay.addWidget(card.frame)
        lay.addSpacing(uikit.px(uikit.tokens.S))
        lay.addWidget(uikit.Divider())
        self.appearance = uikit.AppearanceBar()                # the same two choices as the "Aa" and theme buttons of the header
        lay.addWidget(self.appearance)
        self.set_content(body)
        self.next_button.hide()                            # nothing to confirm here: every row has its own action
        self.refresh()
        QtCore.QTimer.singleShot(0, self._start_fetch)

    # -- state -----------------------------------------------------------------

    def _start_fetch(self):
        if self.is_busy():
            return
        self.run(self._fetch, name="plugins-fetch", indeterminate=True, on_done=self._fetched, on_cancel=self.refresh)

    def _fetch(self, progress):
        progress(0.0, i18n.t("plugins.checking"))
        try:
            remote, _raw = updater.remote_manifest(self.plugin)
        except Exception as exc:
            return {"error": str(exc)}
        return {"remote": remote, "status": _safe_status(self.plugin, remote)}

    def _fetched(self, result):
        if "error" in result:
            self.remote, self.offline = None, result["error"]
            self.show_banner("danger", i18n.t("plugins.offline"), i18n.t("plugins.offline_title"),
                             (i18n.t("ui.retry"), self._start_fetch), self.offline, True)
        else:
            self.remote, self.offline = result["remote"], ""
            self._status = result.get("status") or self._status
        self.refresh()

    def refresh(self):
        """Rows from the disk (what is installed) and, when known, the server (sizes, updates)."""
        dev = updater.is_dev_copy(self.plugin)
        status = {item["id"]: item for item in (self._status or updater.pack_status(self.plugin, None))}
        for tool_id, card in self.cards.items():
            item = status[tool_id]
            if item["installed"]:
                text = i18n.t("plugins.update" if item["update"] else "plugins.installed")
            else:
                text = i18n.t("plugins.not_installed", size=updater.format_size(item["bytes"]) if self.remote else "?")
                if item["needs"] and self.remote:
                    extra = sum(status[n]["bytes"] for n in item["needs"])
                    names = ", ".join(config.tool(n)["title"] for n in item["needs"])
                    text += "\n" + i18n.t("plugins.needs", names=names, size=updater.format_size(extra))
                if self._modules.get(tool_id):
                    text += "\n" + i18n.t("plugins.module_missing", modules=", ".join(self._modules[tool_id]))
            card.status.setText(text)
            uikit.set_tone(card.status, "success" if item["installed"] and not item["update"] else ("warning" if item["update"] else None))
            card.glyph.set_kind("ok" if item["installed"] and not item["update"] else ("warn" if item["update"] or (not item["installed"] and self._modules.get(tool_id)) else "pending"))
            card.install.setText(i18n.t("plugins.update_button" if item["installed"] else "plugins.install"))
            card.install.setVisible(not dev and (not item["installed"] or bool(item["update"])))
            card.remove.setVisible(not dev and item["installed"])
        self._sync_buttons()
        if dev:
            self.set_status(i18n.t("plugins.dev"))
        info = updater.installed_info(self.plugin)
        version = (self.remote or {}).get("version") or info["version"]
        self.set_subtitle(i18n.t("plugins.version", version=version) if version else "")

    def _sync_buttons(self):
        busy = self.is_busy()
        for card in self.cards.values():
            card.install.setEnabled(not busy and self.remote is not None)
            card.remove.setEnabled(not busy)

    def refresh_buttons(self, *_args):
        super(PluginsDialog, self).refresh_buttons()
        if hasattr(self, "cards"):                         # busy or idle: the row buttons follow
            self._sync_buttons()

    # -- install -----------------------------------------------------------------

    def _on_install(self, tool_id):
        if self.is_busy():
            return
        entry = config.tool(tool_id)
        choice = None
        missing = self._modules.get(tool_id)
        if missing and not updater.tool_installed(tool_id):
            if not uikit.confirm(self, entry["title"], i18n.t("plugins.module_ask", title=entry["title"], modules=", ".join(missing)),
                                 ok=i18n.t("plugins.install")):
                return
        if OPTIONS.get(tool_id) == "engine" and not updater.tool_installed(tool_id):
            options = EngineOptions(entry["title"], self)
            _run_modal(options)
            if options.result() != QtWidgets.QDialog.Accepted:
                return
            choice = options.choice()

        from . import bootstrap
        bootstrap.release_files()                              # the PROJ database must not be open while core files are replaced

        def work(progress):
            def advance(done, total, _text):
                workers.advance(progress, done, total, i18n.t("plugins.installing", title=entry["title"],
                                                              done=updater.format_size(done), total=updater.format_size(total)))
            progress(0.0, i18n.t("plugins.installing", title=entry["title"], done="0 B", total="?"))
            result = updater.install_pack(self.plugin, tool_id, advance, progress.is_cancelled)
            if not result.get("ok") and progress.is_cancelled():
                progress.check()
            result["status"] = _safe_status(self.plugin, self.remote)
            return result

        self.run(work, name="plugins-install", on_done=functools.partial(self._installed, tool_id, choice), on_cancel=self.refresh)

    def _installed(self, tool_id, choice, result):
        title = config.tool(tool_id)["title"]
        self._status = result.get("status") or self._status
        if not result.get("ok"):
            self.refresh()
            self.show_banner("danger", i18n.t("plugins.failed", message=result.get("error", "")), None,
                             (i18n.t("ui.retry"), functools.partial(self._on_install, tool_id)))
            return
        from . import bootstrap
        self.core_changed = self.core_changed or bool(result.get("core_changed"))
        for pack_id in result.get("packs", []):
            bootstrap.add_tool_menu(pack_id)
        self.refresh()
        self.show_banner("success", i18n.t("plugins.done_install", title=title))
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
        if self.is_busy():
            return
        entry = config.tool(tool_id)
        users = [p for p in config.dependents(tool_id) if updater.tool_installed(p)]
        if users:
            uikit.inform(self, entry["title"], i18n.t("plugins.needed_by", title=entry["title"],
                                                      others=", ".join(config.tool(p)["title"] for p in users)), "warning")
            return
        if not uikit.confirm(self, entry["title"], i18n.t("plugins.confirm_remove", title=entry["title"]),
                             ok=i18n.t("plugins.remove"), danger=True):
            return
        from . import bootstrap
        bootstrap.forget_tool(tool_id)                         # closes its windows before the files go

        def work(progress):
            progress(0.0, i18n.t("plugins.removing", title=entry["title"]))
            result = updater.remove_pack(self.plugin, tool_id)
            result["status"] = _safe_status(self.plugin, self.remote)
            return result

        self.run(work, name="plugins-remove", cancellable=False, indeterminate=True, on_done=functools.partial(self._removed, tool_id))

    def _removed(self, tool_id, result):
        self._status = result.get("status") or self._status
        self.refresh()
        if result.get("ok"):
            self.show_banner("success", i18n.t("plugins.done_remove", title=config.tool(tool_id)["title"]))
        else:
            self.show_banner("danger", i18n.t("plugins.failed", message=result.get("error", "")))


def show(parent=None):
    """Open the (modal) window. When it replaced files of the core, the code is reloaded once it is closed (like after an update)."""
    dialog = PluginsDialog(parent)
    _run_modal(dialog)
    if dialog.core_changed:
        from . import bootstrap
        QtCore.QTimer.singleShot(0, bootstrap.reload_all)
    return dialog.core_changed
