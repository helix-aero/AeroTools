"""Updates window (Qt): the installed modules, their date, what is available.

One row per installed module (today: just one) with its version, the date of the last update received, and what the repository offers
(number of files and download size). An "Update" button to the right of each row; the primary button of the window is "Update all".

The work (network, disk) runs in the window's task (`ToolWindow.run`, a worker thread): the footer shows the progress, the window stays
usable and cannot be closed during an update. At the end the files are re-read and verified, then on close AeroTools reloads its code
(without restarting Metashape).
"""

import functools

from . import i18n, journal, uikit, updater, workers
from .qt import QtCore, QtWidgets

CHANNELS = (("main", "update.channel_stable"), ("dev", "update.channel_test"))


class _Row(object):
    """The widgets of one module: name, version line, status line with its mark, and its Update button."""

    def __init__(self, plugin, on_update):
        self.plugin = plugin
        self.frame = QtWidgets.QWidget()
        outer = uikit.hbox(spacing=uikit.tokens.M, parent=self.frame)
        left = uikit.vbox(spacing=uikit.tokens.XS)
        self.title = uikit.label(plugin["title"], "lead", wrap=False)
        left.addWidget(self.title)
        self.info = uikit.label("", "small", "muted")
        left.addWidget(self.info)
        line = uikit.hbox(spacing=uikit.tokens.S)
        self.mark = uikit.Glyph("pending", uikit.tokens.ICON)
        self.mark.setVisible(False)
        self.status = uikit.label("")
        line.addWidget(self.mark, 0, QtCore.Qt.AlignVCenter)
        line.addWidget(self.status, 1)
        left.addLayout(line)
        outer.addLayout(left, 1)
        self.button = uikit.Button(i18n.t("update.update"), "secondary", handler=functools.partial(on_update, plugin["id"]))
        outer.addWidget(self.button, 0, QtCore.Qt.AlignVCenter)

    def set_status(self, text, kind=None):
        """Status text; `kind`: "ok" (green tick), "error" (red cross), "running" (turning ring) or nothing."""
        self.status.setText(text)
        self.mark.setVisible(bool(kind))
        if kind:
            self.mark.set_kind(kind)

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


class UpdateDialog(uikit.ToolWindow):
    CLOSE_ONLY = True

    def __init__(self, parent=None):
        super(UpdateDialog, self).__init__(parent, title=i18n.t("update.title"), info=i18n.t("update.intro"), modal=True)
        self.updated = False
        self._results = {}
        body = QtWidgets.QWidget()
        body.setMinimumWidth(uikit.px(640))
        lay = uikit.vbox(spacing=uikit.tokens.M, parent=body)
        self.rows = {}
        for plugin in updater.plugins():
            row = _Row(plugin, self._on_update_one)
            self.rows[plugin["id"]] = row
            lay.addWidget(row.frame)
            row.show_info()
        lay.addStretch(1)
        self.set_content(body)

        self.channel = uikit.ChoiceBox()
        self.channel.setToolTip(i18n.t("update.channel_tip"))
        self.channel.set_choices([(value, i18n.t(key)) for value, key in CHANNELS])
        current = updater.channel()
        if self.channel.findData(current) < 0:
            self.channel.addItem(current, current)       # branch typed by hand in settings.json
        self.channel.set_value(current)
        self.channel.changed.connect(lambda _key: self._on_channel())
        self.footer.add_left(self.channel)
        self.check_button = uikit.Button(i18n.t("update.check"), "secondary", handler=self._start_check)
        self.footer.add_left(self.check_button)
        self.set_primary(i18n.t("update.update_all"), self._on_update_all)
        self._update_buttons()
        QtCore.QTimer.singleShot(0, self._start_check)

    # -- button state --------------------------------------------------------

    def _available(self):
        return [pid for pid, result in self._results.items() if result.get("state") == "update"]

    def _update_buttons(self):
        busy = self.is_busy()
        self.check_button.setEnabled(not busy)
        self.channel.setEnabled(not busy)
        self.next_button.setEnabled(not busy and bool(self._available()))
        for pid, row in self.rows.items():
            state = self._results.get(pid, {}).get("state")
            row.button.setEnabled(not busy and state == "update")
            row.button.setVisible(state == "update")

    def refresh_buttons(self, *_args):
        super(UpdateDialog, self).refresh_buttons()
        if hasattr(self, "rows") and hasattr(self, "check_button"):
            self._update_buttons()

    def _show_result(self, pid):
        row, result = self.rows[pid], self._results[pid]
        state = result.get("state")
        row.show_info()
        if state == "update":
            row.set_status(i18n.t("update.status_available", n=len(result["changes"]), size=updater.format_size(result["bytes"])))
            shown = [c["path"] for c in result["changes"][:12]]
            more = len(result["changes"]) - len(shown)
            row.status.setToolTip("\n".join(shown + (["..."] if more > 0 else [])))
        elif state == "current":
            row.set_status(i18n.t("update.status_current"), "ok")
            row.status.setToolTip("")
        elif state == "dev":
            row.set_status(i18n.t("update.status_dev"), "info")
        else:
            row.set_status(i18n.t("update.status_error", message=result.get("message", "")), "error")

    # -- checking ------------------------------------------------------------

    def _on_channel(self):
        """Stable or dev branch: remember the choice, then check right away on that branch."""
        value = self.channel.value()
        if self.is_busy() or not value or value == updater.channel():
            return
        updater.set_channel(value)
        journal.write("Updates: followed branch = {}".format(value))
        self._results.clear()
        self._update_buttons()
        self._start_check()

    def _start_check(self):
        if self.is_busy():
            return
        for row in self.rows.values():
            row.set_status(i18n.t("update.status_checking"), "running")

        def job(progress):
            progress(0.0, i18n.t("update.status_checking"))
            return {plugin["id"]: updater.check(plugin) for plugin in updater.plugins()}

        self.run(job, name="update-check", cancellable=False, indeterminate=True, on_done=self._checked)

    def _checked(self, result):
        for pid, state in (result or {}).items():
            if pid in self.rows:
                self._results[pid] = state
                self._show_result(pid)

    # -- update -------------------------------------------------------------

    def _on_update_one(self, pid):
        self._start_update([pid])

    def _on_update_all(self):
        self._start_update(self._available())

    def _start_update(self, ids):
        if self.is_busy() or not ids:
            return
        plugins = [p for p in updater.plugins() if p["id"] in ids]
        for pid in ids:
            self.rows[pid].set_status(i18n.t("update.status_updating", done="0 B", total="..."), "running")
        self.show_banner("warning", i18n.t("update.warning"), closable=False)
        from . import bootstrap
        bootstrap.release_files()                      # the PROJ database must not be open while core files are replaced

        def job(progress):
            out = {}
            for index, plugin in enumerate(plugins):
                part = progress.sub(index / float(len(plugins)), (index + 1) / float(len(plugins)))

                def advance(done, total, _text, part=part):
                    workers.advance(part, done, total, i18n.t("update.status_updating", done=updater.format_size(done),
                                                              total=updater.format_size(total)))
                applied = updater.apply(plugin, progress=advance, cancelled=part.is_cancelled)
                entry = {"apply": applied}
                if applied.get("ok"):
                    part(1.0, i18n.t("update.status_verifying"))
                    entry["verify"] = updater.verify(plugin)
                out[plugin["id"]] = entry
            return out

        self.run(job, name="update-apply", on_done=self._updated, on_error=self._update_failed, on_cancel=self._update_cancelled)

    def _restore_rows(self):
        """The update did not finish: each row says again what the last check found."""
        for pid, row in self.rows.items():
            if pid in self._results:
                self._show_result(pid)
            else:
                row.set_status("")

    def _update_failed(self, _exc):
        self._restore_rows()

    def _update_cancelled(self):
        self.clear_banner()
        self._restore_rows()

    def _updated(self, result):
        problems = False
        for pid, entry in (result or {}).items():
            row = self.rows[pid]
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
        if self.updated and not problems:
            self.show_banner("success", i18n.t("update.finished"))
        else:
            self.clear_banner()


def _run_modal(dialog):
    return (getattr(dialog, "exec_", None) or getattr(dialog, "exec"))()


def show(parent=None):
    """Open the (modal) window. Returns True if an update was installed."""
    dialog = UpdateDialog(parent)
    _run_modal(dialog)
    return dialog.updated


def open_and_reload(parent=None):
    """Open the window; if code changed, reload AeroTools (like "Reload AeroTools")."""
    updated = show(parent)
    if updated:
        from . import bootstrap
        QtCore.QTimer.singleShot(0, bootstrap.reload_all)
    return updated
