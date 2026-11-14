"""FastGCP Settings window (Qt): the gear of the panel.

Three pages, so that nothing scrolls:
  AI        the detection engine (light or precise) and its installation state, and the AI model;
  Training  the folder where the corrected markings, the models and the trials are kept, and the
            retraining: corrections -> "Train" -> new model (the original model is never modified);
  Options   the check boxes and the log (the `panel.more` block, built by the panel).
At the bottom: "Check for updates" and "Close".

The training (training.py) runs in a separate Python and ties up the computer for a while
(graphics card): a warning says so before starting, the window follows the progress and
allows cancelling. The models produced are called "Local 1.1", "Local 1.2"... and one can
go back to the original model.
"""

import datetime
import functools
import os

from ... import i18n, journal, uikit
from ...qt import QtCore, QtGui, QtWidgets
from ...uikit import tokens
from . import aigcp, engines, install_ui, training, widgets

POLL_MS = 700
DESIGN_SIZE = (600, 0)
PAGES = (("ai", "settings.page_ai"), ("train", "settings.page_train"), ("options", "settings.page_options"), ("appearance", "settings.page_appearance"))


def _run(dialog):
    """Show a modal window and wait for it (the name of the call differs between the two Qt bindings)."""
    return (getattr(dialog, "exec_", None) or getattr(dialog, "exec"))()


def model_text(model):
    """Displayed name of a model: "Aero V1.0" or "Local 1.2 (2026-11-04)"."""
    if model.get("base") or not model.get("created"):
        return model["name"]
    try:
        day = datetime.datetime.fromisoformat(model["created"]).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return model["name"]
    return "{} ({})".format(model["name"], day)


class ManageDialog(uikit.ToolWindow):
    """List of the models: local ones can be deleted, never the original model."""
    MARK = "tool_gcp"
    CLOSE_ONLY = True

    def __init__(self, parent=None):
        super(ManageDialog, self).__init__(parent, title=i18n.t("settings.manage_title"), modal=True, design_size=(480, 420))
        self.table = uikit.DataTable([uikit.Column("name", i18n.t("settings.col_model"), stretch=True),
                                      uikit.Column("active", i18n.t("settings.col_active"), width=80)], sortable=False)
        self.table.selectionChanged.connect(self._update)
        self.set_content(self.table)
        self.delete_button = self.footer.add_left(uikit.button("settings.delete", self._delete, role="danger"))
        self.next_button.hide()                      # nothing to confirm here: models are deleted one by one
        self._models = []
        self._fill()

    def _fill(self):
        active = training.active_model()
        self._models = list(training.list_models())
        self.table.set_rows([[model_text(model), i18n.t("settings.active_mark") if model["id"] == active else ""]
                             for model in self._models])
        self._update()

    def _current(self):
        rows = self.table.selected_rows()
        return self._models[rows[0]] if rows else None

    def _update(self):
        model = self._current()
        self.delete_button.setEnabled(model is not None and not model.get("base"))

    def _delete(self):
        model = self._current()
        if model is None or model.get("base"):
            return
        name = model_text(model)
        if not uikit.confirm(self, i18n.t("settings.manage_title"), i18n.t("settings.delete_confirm", name=name),
                             ok=i18n.t("settings.delete"), danger=True):
            return
        try:
            training.delete_model(model["id"])
        except Exception as exc:
            journal.write("AI models: cannot delete: {!r}".format(exc))
            self.show_error(exc)
        self._fill()


class _EngineRow(QtWidgets.QWidget):
    """What a form row shows for an engine: "Installed", or the Install button."""

    def __init__(self, on_install, parent=None):
        super(_EngineRow, self).__init__(parent)
        lay = uikit.hbox(spacing=tokens.S, parent=self)
        self.chip = QtWidgets.QWidget()
        chip_lay = uikit.hbox(spacing=tokens.S, parent=self.chip)
        chip_lay.addWidget(uikit.Glyph("ok"))
        chip_lay.addWidget(uikit.label(i18n.t("settings.installed"), "status", "success", wrap=False))
        self.install = uikit.Button(i18n.t("settings.install"), "secondary", handler=on_install)
        lay.addWidget(self.chip)
        lay.addWidget(self.install)
        lay.addStretch(1)

    def set_state(self, installed, enabled):
        self.chip.setVisible(installed)
        self.install.setVisible(not installed)
        self.install.setEnabled(enabled)


class SettingsDialog(uikit.ToolWindow):
    MARK = "tool_gcp"
    CLOSE_ONLY = True

    def __init__(self, panel, parent=None):
        super(SettingsDialog, self).__init__(parent, title=i18n.t("settings.title"), info=i18n.t("settings.info"), modal=True,
                                             design_size=DESIGN_SIZE)
        self.panel = panel
        self._job = None
        self.engine_rows = {}

        body = QtWidgets.QWidget()
        lay = uikit.vbox(spacing=tokens.L, parent=body)
        self.tabs = uikit.Segmented([(key, i18n.t(text)) for key, text in PAGES])
        self.tabs.changed.connect(self._on_page)
        lay.addWidget(self.tabs, 0, QtCore.Qt.AlignLeft)
        self.pages = QtWidgets.QStackedWidget()
        self.pages.addWidget(self._build_ai())
        self.pages.addWidget(self._build_training())
        self.pages.addWidget(self._build_options())
        self.pages.addWidget(self._build_appearance())
        lay.addWidget(self.pages, 1)
        self.set_content(body)

        self.footer.add_left(uikit.button("settings.update_open", self._open_updates))
        self.next_button.hide()                      # every change is applied as it is made: only Close is needed

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)
        self.refresh()

    def _on_page(self, key):
        self.pages.setCurrentIndex([k for k, _t in PAGES].index(key))

    def _open_updates(self):
        from ... import update_dialog
        update_dialog.open_and_reload(self)

    # -- AI: engine and model ----------------------------------------------------------

    def _build_ai(self):
        page = QtWidgets.QWidget()
        lay = uikit.vbox(spacing=tokens.XL, parent=page)

        detect = uikit.Section(i18n.t("settings.sec_detect"), info=i18n.t("settings.sec_detect_info"))
        form = detect.form()
        self.engine_combo = uikit.ChoiceBox([(choice, i18n.t(engines.CHOICE_TEXT[choice])) for choice in engines.CHOICES])
        self.engine_combo.changed.connect(self._on_engine)
        form.add_row(i18n.t("settings.engine"), self.engine_combo)
        for key in ("light", "precise"):
            row = _EngineRow(functools.partial(self._install, key))
            self.engine_rows[key] = row
            form.add_row(i18n.t(engines.NAME_TEXT[key]), row)
        row = _EngineRow(functools.partial(self._install, "training"))
        self.tools_widgets = row
        form.add_row(i18n.t(engines.NAME_TEXT["training"]), row)
        self.engine_hint = uikit.Banner("warning", "")
        self.engine_hint.hide()
        detect.add(self.engine_hint)
        lay.addWidget(detect)

        model = uikit.Section(i18n.t("settings.sec_model"), info=i18n.t("settings.sec_model_info"))
        form = model.form()
        holder = QtWidgets.QWidget()
        row = uikit.hbox(spacing=tokens.S, parent=holder)
        self.models = uikit.ChoiceBox()
        self.models.changed.connect(self._on_model)
        row.addWidget(self.models, 1)
        row.addWidget(uikit.button("settings.manage", self._manage))
        form.add_row(i18n.t("settings.active_model"), holder)
        lay.addWidget(model)
        lay.addStretch(1)
        return page

    def refresh_engines(self):
        """State of the installed engines (a short query of the external Python)."""
        state = engines.status()
        busy = self._job is not None
        self.engine_combo.set_value(engines.selected())
        can = engines.can_install()
        installed = dict(engines.describe(state))
        for key, row in self.engine_rows.items():
            row.set_state(bool(installed.get(key, False)), not busy and can)
        self.tools_widgets.set_state(bool(installed.get("training", False)), not busy and can)
        if not can:
            self._warn(i18n.t("settings.engine_custom_python", path=aigcp.python_path()))
        elif engines.resolve(state=state) is None:
            self._warn(i18n.t("settings.engine_none"))
        else:
            self._warn("")

    def _warn(self, text):
        if text:
            self.engine_hint.show_message("warning", text)
        else:
            self.engine_hint.hide()

    def _on_engine(self, choice):
        engines.set_selected(choice)
        journal.write("Detection engine: {}".format(choice))
        wanted = engines.missing(choice)
        if wanted is not None and engines.can_install():
            what = i18n.t(engines.WHAT_TEXT[wanted])
            if uikit.confirm(self, i18n.t("aigcp.setup_title"), i18n.t("settings.engine_missing_offer", what=what),
                             ok=i18n.t("settings.install")):
                install_ui.run(self, wanted, confirm=False)
        self.refresh()

    def _install(self, key):
        if self._job is not None:
            return
        if key == "training":
            install_ui.run(self, None, training=True)
        else:
            install_ui.run(self, key)
        self.refresh()

    def _on_model(self, model_id):
        training.set_active_model(model_id)
        self.refresh()

    def _manage(self):
        _run(ManageDialog(self))
        self.refresh()

    # -- Training: data and retraining ---------------------------------------------------

    def _build_training(self):
        page = QtWidgets.QWidget()
        lay = uikit.vbox(spacing=tokens.XL, parent=page)

        data = uikit.Section(i18n.t("settings.sec_data"), info=i18n.t("settings.sec_data_info"))
        holder = QtWidgets.QWidget()
        row = uikit.hbox(spacing=tokens.S, parent=holder)
        self.storage = uikit.TextField()
        self.storage.setReadOnly(True)
        row.addWidget(self.storage, 1)
        row.addWidget(uikit.button("ui.browse", self._browse))
        row.addWidget(uikit.button("settings.open_folder", self._open_folder))
        data.add(holder)
        tiles = uikit.hbox(spacing=tokens.S)
        self.stat_numbers = {}
        for key, label in (("annotations", "settings.stat_notes"), ("models", "settings.stat_models"),
                           ("detections", "settings.stat_detections"), ("trainings", "settings.stat_trainings")):
            tile = widgets.Stat(i18n.t(label))
            tiles.addWidget(tile, 1)
            self.stat_numbers[key] = tile
        data.body.addLayout(tiles)
        self.use_refine = uikit.Toggle(i18n.t("settings.use_refine"))
        self.use_refine.toggled.connect(self._on_use_refine)
        data.add(self.use_refine)
        lay.addWidget(data)

        train = uikit.Section(i18n.t("settings.sec_train"), info=i18n.t("settings.sec_train_info"))
        flow = uikit.hbox(spacing=tokens.S)
        self.fixes_tile = widgets.Stat(i18n.t("settings.flow_fixes"))
        flow.addWidget(self.fixes_tile, 1)
        flow.addWidget(widgets.arrow())
        self.train_button = uikit.button("settings.refine_model", self._on_train)
        flow.addWidget(self.train_button, 1)
        flow.addWidget(widgets.arrow())
        self.active_tile = widgets.Stat(i18n.t("settings.flow_model"))
        flow.addWidget(self.active_tile, 1)
        train.body.addLayout(flow)
        self.revert_button = uikit.Button("", "secondary", handler=self._on_revert)
        train.add(self.revert_button)
        self.progress = uikit.ProgressRow(cancellable=True)
        self.progress.cancelRequested.connect(self._on_cancel)
        self.progress.setVisible(False)
        train.add(self.progress)
        lay.addWidget(train)
        lay.addStretch(1)
        return page

    def _status(self, text, kind="info"):
        if text:
            self.show_banner(kind, text)
        else:
            self.clear_banner()

    # -- Options -------------------------------------------------------------------------

    def _build_options(self):
        page = QtWidgets.QWidget()
        lay = uikit.vbox(spacing=tokens.XL, parent=page)
        section = uikit.Section(i18n.t("settings.sec_options"))
        self.panel.more.setParent(None)
        section.add(self.panel.more)
        self.panel.more.show()
        lay.addWidget(section)
        lay.addStretch(1)
        return page

    def _build_appearance(self):
        """Theme and font: the same two choices as the two round buttons of every header, and kept in sync with them."""
        page = QtWidgets.QWidget()
        lay = uikit.vbox(spacing=tokens.XL, parent=page)
        section = uikit.Section(i18n.t("settings.page_appearance"), info=i18n.t("settings.appearance_info"))
        form = uikit.FormGrid()
        self.theme_choice = uikit.ThemeChoice()
        self.font_choice = uikit.FontChoice()
        form.add_row(i18n.t("ui.appearance_theme"), self.theme_choice)
        form.add_row(i18n.t("ui.appearance_font"), self.font_choice)
        self.font_sample = uikit.label(i18n.t("settings.appearance_sample"), "body")     # drawn in the font that is on
        self.font_sample.setContentsMargins(uikit.px(tokens.LABEL_W + tokens.S), 0, 0, 0)
        form.add_wide(self.font_sample)
        section.add(form)
        lay.addWidget(section)
        lay.addStretch(1)
        return page

    # -- state ---------------------------------------------------------------------------

    def refresh(self):
        """Reread the settings, the models and the number of annotations."""
        training_on = self._job is not None
        self.use_refine.blockSignals(True)
        self.use_refine.setChecked(training.collect_enabled())
        self.use_refine.blockSignals(False)
        self.storage.setText(training.storage_dir())
        self.storage.setCursorPosition(0)
        self.storage.setToolTip(training.storage_dir())
        summary = training.storage_summary()
        for key, tile in self.stat_numbers.items():
            tile.set_number(str(summary.get(key, 0)))

        counts = training.feedback_counts()
        positives = sum(c["positives"] for c in counts.values())
        self.fixes_tile.set_number(str(positives))

        models = training.list_models()
        active = training.active_model()
        self.models.set_choices([(model["id"], model_text(model)) for model in models], active)
        current = [m for m in models if m["id"] == active]
        base = models[0]
        self.active_tile.set_number(current[0]["name"] if current else base["name"])
        self.revert_button.setText(i18n.t("settings.revert", name=base["name"]))
        self.revert_button.setVisible(active != base["id"])
        self.revert_button.setEnabled(not training_on)
        self.train_button.setEnabled(not training_on and positives > 0)
        self.train_button.setToolTip("" if positives > 0 else i18n.t("settings.train_need_data"))
        self.models.setEnabled(not training_on)
        self.use_refine.setEnabled(not training_on)
        self.refresh_engines()

    # -- settings ------------------------------------------------------------------------

    def _on_use_refine(self, checked):
        training.set_collect(bool(checked))
        journal.write("AI models: manual refinements {}".format("recorded" if checked else "not recorded"))

    def _open_folder(self):
        folder = training.storage_dir()
        try:
            os.makedirs(folder, exist_ok=True)
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(folder))
        except Exception as exc:
            journal.write("AI models: cannot open the folder: {!r}".format(exc))

    def _browse(self):
        old = training.storage_dir()
        chosen = QtWidgets.QFileDialog.getExistingDirectory(self, i18n.t("settings.storage_pick"), old)
        if not chosen:
            return
        chosen = os.path.normpath(chosen)
        if chosen != os.path.normpath(old):
            self._offer_copy(old, chosen)
        training.set_storage_dir(chosen)
        journal.write("AI models: storage folder: {}".format(chosen))
        self.refresh()

    def _offer_copy(self, old, new):
        """New folder empty and old folder full: offers to copy the data (nothing is deleted)."""
        before, after = training.storage_summary(old), training.storage_summary(new)
        if not (before["annotations"] or before["models"]) or after["annotations"] or after["models"]:
            return
        text = i18n.t("settings.copy_confirm", a=before["annotations"], m=before["models"])
        if not uikit.confirm(self, i18n.t("settings.sec_data"), text, ok=i18n.t("settings.copy")):
            return
        try:
            training.copy_storage(old, new)
            journal.write("AI models: data copied to {}".format(new))
        except Exception as exc:
            journal.write("AI models: cannot copy: {!r}".format(exc))
            self._status(i18n.t("settings.copy_failed", message=str(exc)), "danger")

    def _on_revert(self):
        training.revert_to_base()
        self._status(i18n.t("settings.reverted"), "success")
        self.refresh()

    # -- retraining ----------------------------------------------------------------------

    def _on_train(self):
        if self._job is not None:
            return
        state = self.panel._state
        if state is not None and aigcp.busy(state):
            self._status(i18n.t("settings.train_busy_detect"), "warning")
            return
        missing = training.missing_tools()
        if missing:
            offer = i18n.t("settings.train_tools_offer", tools=", ".join(missing))
            if not uikit.confirm(self, i18n.t("settings.train_title"), offer, ok=i18n.t("settings.install")):
                return
            if not install_ui.run(self, None, training=True, confirm=False):
                return
            self.refresh()
        counts = training.feedback_counts()
        positives = sum(c["positives"] for c in counts.values())
        negatives = sum(c["negatives"] for c in counts.values())
        text = i18n.t("settings.train_confirm", p=positives, r=negatives)
        if not uikit.confirm(self, i18n.t("settings.train_title"), text, ok=i18n.t("settings.refine_model")):
            return
        try:
            self._job = training.Training().start()
        except Exception as exc:
            self._job = None
            self._status(i18n.t("settings.train_failed", message=str(exc)), "danger")
            journal.write("AI models: cannot start: {!r}".format(exc))
            self.refresh()
            return
        self.clear_banner()
        self.progress.reset()
        self.progress.set_indeterminate(i18n.t("settings.training"))
        self.progress.setVisible(True)
        self._timer.start()
        self.refresh()

    @staticmethod
    def _failure_text(result):
        """Message of a failure, according to its code; fallback: the text of the module."""
        code, text = result.get("code"), result.get("message", "")
        detail = text.split(": ", 1)[1] if ": " in text else text
        if code == "busy":
            return i18n.t("settings.err_busy")
        if code == "no_python":
            return i18n.t("settings.err_no_python", detail=detail)
        if code == "no_script":
            return i18n.t("settings.err_no_script", detail=detail)
        if code == "no_data":
            return i18n.t("settings.err_no_data", n=training.MIN_POSITIVES)
        return text

    def _on_cancel(self):
        if self._job is not None:
            self._job.cancel()
            self.progress.text.setFullText(i18n.t("settings.train_cancelling"))

    def _poll(self):
        job = self._job
        if job is None:
            self._timer.stop()
            return
        try:
            result = job.poll()
        except Exception as exc:
            result = {"ok": False, "code": "failed", "message": str(exc)}
        if result is None:
            fraction, text = job.progress()
            label = i18n.t("settings.training") + (" " + text if text else "")
            if fraction is None:
                self.progress.set_indeterminate(label)
            else:
                self.progress.set_progress(max(0.0, min(1.0, fraction)), label)
            return
        self._timer.stop()
        self._job = None
        self.progress.setVisible(False)
        if result.get("ok"):
            training.set_active_model(result["model_id"])
            message = i18n.t("settings.train_done", name=result["name"])
            kind = "success"
            if result.get("warnings"):
                message += " " + i18n.t("settings.train_warning")
                kind = "warning"
        elif result.get("code") == "cancelled":
            message, kind = i18n.t("settings.train_cancelled"), "info"
        else:
            message, kind = i18n.t("settings.train_failed", message=self._failure_text(result)), "danger"
        self._status(message, kind)
        journal.write("AI models: {}".format(message))
        self.refresh()

    # -- window life ---------------------------------------------------------------------

    def can_close(self):
        return self._job is None and super(SettingsDialog, self).can_close()

    def closeEvent(self, event):
        if self._job is not None:
            self._status(i18n.t("settings.train_running_close"), "warning")
            event.ignore()
            return
        super(SettingsDialog, self).closeEvent(event)

    def release(self):
        """Give the options block back to the panel before the window disappears."""
        self.panel.more.setParent(None)


def show(panel, existing=None):
    """Open the Settings window (modal). Reuses the instance already built."""
    window = existing
    try:
        if window is not None:
            window.objectName()
    except RuntimeError:
        window = None
    if window is None:
        window = SettingsDialog(panel, panel)
    window.refresh()
    window.setModal(True)
    _run(window)
    return window
