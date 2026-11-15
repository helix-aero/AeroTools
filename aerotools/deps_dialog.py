"""Dependencies window (Qt): the Python packages the tools need, one row each, with a button to install what is missing.

Two ways in. A tool that cannot start for lack of a package opens it with only the missing rows (`show(parent, tool_id=...)`), and once
everything is there offers to start that tool. Aero > Dependencies shows every dependency of the installed tools with its state, so the
packages can be installed before a tool is used (`show(parent)`).

One row: the name and an "i" (which tools need it), the state with a small progress bar while it installs, an Install button, and one line
that says what it is. "Install all" (bottom right) runs the rows that are missing or failed, pip first. The installing is done by
aerotools/deps.py in a worker thread (aerotools/workers.py); the window polls the shared state every 100 ms, so Qt is only touched from
its own thread and the journal lines are written from here. A failure says the short reason on its row; "Copy log" puts the whole log
(commands and pip's output) on the clipboard for support.

Fixed size, no scroll bar: at most MAX_ROWS rows are listed ("and N more" for the rest, which Install all still installs).
"""

import functools

from . import config, deps, i18n, journal, uikit, workers
from .qt import QtCore, QtWidgets

WIDTH = 560
MAX_ROWS = 5
STATUS_WIDTH = 220                      # design px: the state text is cut with an ellipsis, never wider
POLL_MS = 100

# Row states. `present` was there when the window opened; the others happen in this window.
MISSING, QUEUED, INSTALLING, DONE, RESTART, FAILED, PRESENT = "missing", "queued", "installing", "done", "restart", "failed", "present"
TODO = (MISSING, FAILED)                # what "Install all" and the row button pick up


class _Row(object):
    """The widgets of one dependency."""

    def __init__(self, item, on_install):
        self.item = item
        self.name = item["id"]
        self.frame = QtWidgets.QWidget()
        outer = uikit.vbox(spacing=uikit.tokens.XS, parent=self.frame)
        head = uikit.hbox(spacing=uikit.tokens.S)
        centre = QtCore.Qt.AlignVCenter
        head.addWidget(uikit.label(item["title"], "strong", wrap=False), 0, centre)
        if item["users"]:
            head.addWidget(uikit.InfoButton(i18n.t("deps.needed_by", tools=", ".join(item["users"]))), 0, centre)
        head.addStretch(1)
        self.status = uikit.ElidedLabel("", "status", "muted")
        self.status.setFixedWidth(uikit.px(STATUS_WIDTH))
        self.status.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)     # (the label's own "Ignored" lets the row overflow)
        self.status.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        head.addWidget(self.status, 0, centre)
        self.glyph = uikit.Glyph("pending", 16)                  # after the text: the marks line up whatever the text says
        head.addWidget(self.glyph, 0, centre)
        self.button = uikit.Button(i18n.t("deps.install"), "secondary", handler=functools.partial(on_install, item["id"]))
        policy = self.button.sizePolicy()
        policy.setRetainSizeWhenHidden(True)                     # the column keeps its width when the button goes
        self.button.setSizePolicy(policy)
        head.addWidget(self.button, 0, centre)
        outer.addLayout(head)
        self.info = uikit.label(i18n.t(item["info"]) if item["info"] else "", "small", "muted")
        outer.addWidget(self.info)
        self.bar = uikit.ProgressBar()
        policy = self.bar.sizePolicy()
        policy.setRetainSizeWhenHidden(True)                      # the row keeps its height when the bar is not needed
        self.bar.setSizePolicy(policy)
        self.bar.hide()
        outer.addWidget(self.bar)


class DependenciesDialog(uikit.ToolWindow):
    MARK = "tool_suite"
    CLOSE_ONLY = True

    def __init__(self, parent=None, tool_id=None, modules=(), offer_start=True):
        super(DependenciesDialog, self).__init__(parent, title=i18n.t("deps.title"), info=i18n.t("deps.tip"), modal=True)
        self.tool_id = tool_id
        self.offer_start = bool(offer_start and tool_id)
        self.start_requested = False
        self.python, self.tried = deps.find_python()
        items = deps.collect([tool_id] if tool_id else None, only_missing=bool(tool_id), extra_modules=modules)
        self.items = items
        self.state = {item["id"]: {"status": PRESENT if item["version"] else MISSING, "version": item["version"], "fraction": 0.0,
                                   "step": "", "size": "", "reason": ""} for item in items}
        self.log_lines = []                                      # everything the commands said, for "Copy log"
        self._drained = 0                                        # how many of them the journal has already received
        self._worker = None
        self._queue = []
        self.rows = {}

        body = QtWidgets.QWidget()
        body.setMinimumWidth(uikit.px(WIDTH))
        lay = uikit.vbox(spacing=uikit.tokens.S, parent=body)
        if not items:
            lay.addWidget(uikit.label(i18n.t("deps.empty"), "body", "muted"))
        for index, item in enumerate(items[:MAX_ROWS]):
            if index:
                lay.addWidget(uikit.Divider())
            row = _Row(item, self._on_install)
            self.rows[item["id"]] = row
            lay.addWidget(row.frame)
        if len(items) > MAX_ROWS:
            lay.addWidget(uikit.label(i18n.t("deps.more", count=len(items) - MAX_ROWS), "small", "muted", wrap=False))
        self.set_content(body)

        self.copy_button = uikit.Button(i18n.t("deps.copy_log"), "quiet", handler=self.copy_log)
        self.copy_button.setToolTip(i18n.t("deps.copy_log_tip"))
        self.footer.add_left(self.copy_button)
        self.copy_button.hide()
        self.set_primary(i18n.t("deps.install_all"), self._on_primary)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(POLL_MS)
        self.timer.timeout.connect(self._poll)
        self._render_all()
        self._sync()

    # -- rows -------------------------------------------------------------------------------------

    def _reason_text(self, key):
        return i18n.t("deps.err_" + key)

    def _render(self, name):
        row, st = self.rows.get(name), self.state[name]
        if row is None:
            return
        status = st["status"]
        text, glyph, tone = "", "pending", None
        if status == PRESENT:
            text, glyph, tone = i18n.t("deps.status_present", version=st["version"]), "ok", "success"
        elif status == MISSING:
            text, glyph, tone = i18n.t("deps.status_missing"), "warn", "warning"
        elif status == QUEUED:
            text, glyph = i18n.t("deps.status_queued"), "pending"
        elif status == INSTALLING:
            key = "deps.step_download_size" if st["step"] == "download" and st["size"] else "deps.step_" + (st["step"] or "collect")
            text, glyph = i18n.t(key, size=st["size"]), "running"
        elif status == DONE:
            text, glyph, tone = i18n.t("deps.status_done"), "ok", "success"
        elif status == RESTART:
            text, glyph, tone = i18n.t("deps.status_restart"), "warn", "warning"
        elif status == FAILED:
            text, glyph, tone = i18n.t("deps.status_failed", reason=self._reason_text(st["reason"])), "error", "danger"
        row.status.setFullText(text)
        uikit.set_tone(row.status, tone)
        row.glyph.set_kind(glyph)
        row.bar.setVisible(status == INSTALLING)
        if status == INSTALLING:
            row.bar.set_fraction(st["fraction"])
        row.button.setVisible(status in TODO)
        row.button.setEnabled(not self.installing())

    def _render_all(self):
        for name in self.state:
            self._render(name)

    def installing(self):
        return self._worker is not None and not self._worker.finished

    def todo(self):
        """Names `Install all` would run: missing or failed, pip first (the order of the rows)."""
        return [item["id"] for item in self.items if self.state[item["id"]]["status"] in TODO]

    def _sync(self):
        """Primary button, footer and Close/Stop follow the state of the rows."""
        busy = self.installing()
        todo = self.todo()
        failed = [n for n, st in self.state.items() if st["status"] == FAILED]
        ready_to_start = self.offer_start and self.items and not todo and not busy and all(
            st["status"] in (PRESENT, DONE) for st in self.state.values())
        if ready_to_start:
            self.set_primary(i18n.t("deps.start", title=config.tool(self.tool_id)["title"]), self._on_start)
            self.next_button.show()
            self.next_button.setEnabled(True)
        elif todo or busy:
            self.set_primary(i18n.t("deps.install_all"), self._on_primary)
            self.next_button.show()
            self.next_button.setEnabled(bool(todo) and not busy and bool(self.python))
        else:
            self.next_button.hide()
        self.copy_button.setVisible(bool(self.log_lines))
        self.cancel_button.setText(i18n.t("deps.stop") if busy else i18n.t("ui.close"))
        if busy:
            self.set_status("")
        elif not self.python and todo:
            self.set_status(i18n.t("deps.python_missing"), "danger")
        elif failed:
            self.set_status(i18n.t("deps.some_failed"), "danger")
        elif self.items and not todo and any(st["status"] in (DONE, RESTART) for st in self.state.values()):
            self.set_status(i18n.t("deps.all_done"), "success")
        else:
            self.set_status("")
        self._render_all()

    def refresh_buttons(self, *_args):
        super(DependenciesDialog, self).refresh_buttons()
        if hasattr(self, "state"):                           # the shell re-reads the buttons when it shows: ours follow the rows
            self._sync()

    # -- actions ----------------------------------------------------------------------------------

    def _on_primary(self):
        self.install(self.todo())

    def _on_install(self, name):
        self.install([name])

    def _on_start(self):
        self.start_requested = True
        self.accept()

    def install(self, names):
        """Install these dependencies one after the other in a worker thread. pip goes first when it is on the list or a package needs it."""
        if self.installing():
            return False
        names = [n for n in names if n in self.state]
        if not names:
            return False
        pip = self.state.get(deps.PIP)
        if deps.PIP in self.state and deps.PIP not in names and pip["status"] in TODO and any(n != deps.PIP for n in names):
            names.insert(0, deps.PIP)
        names.sort(key=lambda n: n != deps.PIP)
        self._queue = names
        for name in names:
            self.state[name].update(status=QUEUED, fraction=0.0, step="", size="", reason="")
        self._log("install: {}".format(", ".join(names)))
        python, state = self.python, self.state

        def job(worker):
            return self._job(worker, python, names, state)

        self._worker = workers.Worker(job)
        self._worker.start()
        self.timer.start()
        self._sync()
        return True

    def _job(self, worker, python, names, state):
        """The worker thread: install each name, update its shared state, never touch Qt."""
        pip_failed = False
        for name in names:
            st = state[name]
            if worker.cancel:
                st.update(status=FAILED, reason="stopped")
                continue
            if pip_failed and name != deps.PIP:
                st.update(status=FAILED, reason="needs_pip")
                continue

            def progress(fraction, step, size, st=st):
                st.update(fraction=fraction, step=step, size=size)

            st.update(status=INSTALLING, fraction=0.0, step="collect")
            result = deps.install_one(name, python, self._log, progress, lambda: worker.cancel)
            if result["ok"]:
                st.update(status=RESTART if result["restart"] else DONE, fraction=1.0, version=deps.state_of(name))
            else:
                st.update(status=FAILED, reason=result["reason"])
                pip_failed = pip_failed or name == deps.PIP
        return True

    def _log(self, text):
        """Called from the worker thread and from here; list.append is atomic. The journal gets the lines from `_poll`."""
        self.log_lines.append(text)

    def _poll(self):
        """Every POLL_MS: new log lines to the journal, rows from the shared state, and the end of the job."""
        lines, self._drained = self.log_lines[self._drained:], len(self.log_lines)
        for line in lines:
            journal.write("deps: " + line)
        self._render_all()
        if self._worker is not None and self._worker.finished:
            self.timer.stop()
            self._worker = None
            self._sync()

    def can_close(self):
        """Closing while it installs stops the running command first (the title bar's cross and Esc do the same as Stop)."""
        if self.installing():
            self._worker.cancel = True
            self.footer.set_status(i18n.t("deps.stopped"))
            return False
        return True

    def cancel_clicked(self):
        if self.installing():
            self.can_close()
        else:
            self.reject()

    def log_text(self):
        """The log for support: the system, the Python found, then every command and what it said."""
        import platform
        import sys
        head = ["AeroTools dependencies log", "OS: {}".format(platform.platform()),
                "Metashape Python: {}".format(sys.version.split()[0]), "python.exe: {}".format(self.python or "not found")]
        if not self.python:
            head.append("looked in: " + "; ".join(self.tried))
        return "\n".join(head + [""] + self.log_lines)

    def copy_log(self):
        text = self.log_text()
        QtWidgets.QApplication.clipboard().setText(text)
        self.set_status(i18n.t("deps.log_copied"), "success")
        return text

    def closeEvent(self, event):
        if self._worker is not None:                        # it was asked to stop: the thread ends by itself
            self._worker.cancel = True
        self.timer.stop()
        super(DependenciesDialog, self).closeEvent(event)

    def showEvent(self, event):
        super(DependenciesDialog, self).showEvent(event)
        if not getattr(self, "_fixed", False):
            self._fixed = True
            self.layout().setSizeConstraint(QtWidgets.QLayout.SetNoConstraint)     # the shell's minimum-size rule would undo the fixed size
            self.setFixedSize(self.size())                  # a fixed-size window: no grip, nothing to scroll


def _run_modal(dialog):
    return (getattr(dialog, "exec_", None) or getattr(dialog, "exec"))()


def show(parent=None, tool_id=None, modules=(), offer_start=True):
    """Open the (modal) window. Returns True when the person chose to start the tool that asked (`tool_id`) once everything was installed."""
    dialog = DependenciesDialog(parent, tool_id, modules, offer_start)
    _run_modal(dialog)
    return dialog.start_requested
