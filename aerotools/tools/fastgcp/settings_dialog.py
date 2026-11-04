"""Fenetre Parametres de FastGCP (Qt) : l'engrenage du panneau.

Trois blocs, dans cet ordre :
  - Parametres : cases a cocher et journal (la carte `panel.more`, construite par le panneau) ;
  - Modeles IA : stockage des affinages manuels, choix du modele de detection, gestion des
    modeles locaux, et « Affiner mon modele avec mes donnees » (reentrainement) ;
  - le tout dans une zone defilante.

Le reentrainement (training.py) tourne dans un Python a part et bloque l'ordinateur un
moment (carte graphique) : un avertissement le dit avant de commencer, la fenetre suit
l'avancement et permet d'annuler. Le modele de depart n'est jamais modifie ; les modeles
produits s'appellent « Local 1.1 », « Local 1.2 »... et on peut revenir au modele d'origine.
"""

import datetime
import functools
import os

from ... import i18n, journal, theme
from ...qt import QtCore, QtGui, QtWidgets
from . import aigcp, dialogs, engines, install_ui, training
from .review_dialog import _button, _exec, _restyle, _styled_root

POLL_MS = 700
WIDTH, HEIGHT = 580, 700


def _section(text_key):
    label = QtWidgets.QLabel(i18n.t(text_key))
    label.setObjectName("aeroSection")
    return label


def _card():
    card = QtWidgets.QFrame()
    card.setObjectName("aeroCard")
    layout = QtWidgets.QVBoxLayout(card)
    pad = theme.PAD + theme.PAD_S
    layout.setContentsMargins(pad, pad, pad, pad)
    layout.setSpacing(theme.PAD)
    return card, layout


def _meta(text=""):
    label = QtWidgets.QLabel(text)
    label.setObjectName("aeroMeta")
    label.setWordWrap(True)
    return label


def model_text(model):
    """Nom affiche d'un modele : « Aero V1.0 » ou « Local 1.2 (04/11/2026) »."""
    if model.get("base") or not model.get("created"):
        return model["name"]
    try:
        day = datetime.datetime.fromisoformat(model["created"]).strftime("%d/%m/%Y")
    except (TypeError, ValueError):
        return model["name"]
    return "{} ({})".format(model["name"], day)


class ManageDialog(QtWidgets.QDialog):
    """Liste des modeles : on supprime les locaux, jamais le modele d'origine."""

    def __init__(self, parent=None):
        super(ManageDialog, self).__init__(parent)
        self.setWindowTitle(i18n.t("settings.manage_title"))
        self.setModal(True)
        self.resize(420, 360)
        root = _styled_root(self)
        layout = QtWidgets.QVBoxLayout(root)
        pad = theme.PAD * 2
        layout.setContentsMargins(pad, pad, pad, pad)
        layout.setSpacing(theme.PAD)
        self.list = QtWidgets.QListWidget()
        self.list.setObjectName("aeroThumbs")
        self.list.itemSelectionChanged.connect(self._update)
        layout.addWidget(self.list, 1)
        self.note = _meta(i18n.t("settings.base_locked"))
        layout.addWidget(self.note)
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        self.delete_button = _button("settings.delete", self._delete)
        row.addWidget(self.delete_button)
        row.addWidget(_button("settings.close", self.accept))
        layout.addLayout(row)
        self._fill()

    def _fill(self):
        self.list.clear()
        active = training.active_model()
        for model in training.list_models():
            item = QtWidgets.QListWidgetItem(model_text(model) + ("  *" if model["id"] == active else ""))
            item.setData(QtCore.Qt.UserRole, model["id"])
            item.setData(QtCore.Qt.UserRole + 1, bool(model.get("base")))
            self.list.addItem(item)
        self._update()

    def _update(self):
        item = self.list.currentItem()
        self.delete_button.setEnabled(item is not None and not item.data(QtCore.Qt.UserRole + 1))

    def _delete(self):
        item = self.list.currentItem()
        if item is None or item.data(QtCore.Qt.UserRole + 1):
            return
        name = item.text().replace("  *", "")
        if not dialogs.ask(self, i18n.t("settings.manage_title"), i18n.t("settings.delete_confirm", name=name)):
            return
        try:
            training.delete_model(item.data(QtCore.Qt.UserRole))
        except Exception as exc:
            journal.write("Modeles IA : suppression impossible : {!r}".format(exc))
        self._fill()


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, panel, parent=None):
        super(SettingsDialog, self).__init__(parent)
        self.panel = panel
        self._job = None
        self._lang = i18n.lang()
        self.setWindowTitle(i18n.t("settings.title"))
        self.resize(WIDTH, HEIGHT)
        root = _styled_root(self)
        outer = QtWidgets.QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QtWidgets.QScrollArea()
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.viewport().setAutoFillBackground(False)
        outer.addWidget(scroll, 1)
        body = QtWidgets.QWidget()
        scroll.setWidget(body)
        # setWidget remet le remplissage du fond : on le coupe apres, pour voir le fond du panneau.
        body.setAutoFillBackground(False)
        layout = QtWidgets.QVBoxLayout(body)
        pad = theme.PAD_L
        layout.setContentsMargins(pad, pad, pad, pad)
        layout.setSpacing(theme.PAD)

        self._add_title(layout, "settings.general")
        panel.more.setParent(body)
        layout.addWidget(panel.more)
        panel.more.show()
        self._add_title(layout, "settings.update_title")
        layout.addWidget(self._build_update())
        self._add_title(layout, "settings.engine_title")
        layout.addWidget(self._build_engine())
        self._add_title(layout, "settings.ai_models")
        layout.addWidget(self._build_models())
        layout.addStretch(1)

        bottom = QtWidgets.QHBoxLayout()
        bottom.setContentsMargins(pad, theme.PAD, pad, pad)
        bottom.addStretch(1)
        bottom.addWidget(_button("settings.close", self.close))
        outer.addLayout(bottom)

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)
        self.refresh()

    def _add_title(self, layout, text_key):
        layout.addWidget(_section(text_key))

    # -- construction du bloc Mises a jour ---------------------------------------------

    def _build_update(self):
        card, layout = _card()
        layout.addWidget(_meta(i18n.t("settings.update_note")))
        row = QtWidgets.QHBoxLayout()
        row.addWidget(_button("settings.update_open", self._open_updates, width=220))
        row.addStretch(1)
        layout.addLayout(row)
        return card

    def _open_updates(self):
        from ... import update_dialog
        update_dialog.open_and_reload(self)

    # -- construction du bloc Moteur de detection --------------------------------------

    def _build_engine(self):
        card, layout = _card()
        layout.addWidget(self._caption("settings.engine_choice"))
        self.engine_combo = QtWidgets.QComboBox()
        self.engine_combo.setObjectName("aeroCombo")
        self.engine_combo.setCursor(QtCore.Qt.PointingHandCursor)
        self.engine_combo.setFocusPolicy(QtCore.Qt.NoFocus)
        for choice in engines.CHOICES:
            self.engine_combo.addItem(i18n.t(engines.CHOICE_TEXT[choice]), choice)
        self.engine_combo.activated.connect(self._on_engine)
        layout.addWidget(self.engine_combo)
        layout.addWidget(_meta(i18n.t("settings.engine_note")))
        layout.addSpacing(theme.PAD_S)
        self.engine_rows = {}
        for key in ("light", "precise", "training"):
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(theme.PAD)
            text = QtWidgets.QLabel()
            row.addWidget(text, 1)
            install = _button("settings.install", functools.partial(self._install, key), width=96)
            row.addWidget(install)
            layout.addLayout(row)
            self.engine_rows[key] = (text, install)
        self.engine_hint = _meta()
        layout.addWidget(self.engine_hint)
        return card

    def refresh_engines(self):
        """Etat des moteurs installes (une courte interrogation du Python externe)."""
        state = engines.status()
        busy = self._job is not None
        index = self.engine_combo.findData(engines.selected())
        self.engine_combo.blockSignals(True)
        self.engine_combo.setCurrentIndex(max(0, index))
        self.engine_combo.blockSignals(False)
        for key, installed in engines.describe(state):
            text, button = self.engine_rows[key]
            text.setText(i18n.t("settings.engine_row", name=i18n.t(engines.NAME_TEXT[key]),
                                state=install_ui.state_text(installed)))
            button.setVisible(not installed)
            button.setEnabled(not busy and engines.can_install())
        if not engines.can_install():
            self.engine_hint.setText(i18n.t("settings.engine_custom_python", path=aigcp.python_path()))
        elif engines.resolve(state=state) is None:
            self.engine_hint.setText(i18n.t("settings.engine_none"))
        else:
            self.engine_hint.setText(i18n.t("settings.engine_in_use",
                                            name=i18n.t(engines.NAME_TEXT[engines.resolve(state=state)])))

    def _on_engine(self, index):
        choice = self.engine_combo.itemData(index)
        engines.set_selected(choice)
        journal.write("Moteur de detection : {}".format(choice))
        wanted = engines.missing(choice)
        if wanted is not None and engines.can_install():
            what = i18n.t(engines.WHAT_TEXT[wanted])
            if dialogs.ask(self, i18n.t("aigcp.setup_title"), i18n.t("settings.engine_missing_offer", what=what)):
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

    # -- construction du bloc Modeles IA ---------------------------------------------

    def _build_models(self):
        card, layout = _card()

        self.use_refine = QtWidgets.QCheckBox(i18n.t("settings.use_refine"))
        self.use_refine.setCursor(QtCore.Qt.PointingHandCursor)
        self.use_refine.toggled.connect(self._on_use_refine)
        layout.addWidget(self.use_refine)

        layout.addWidget(self._caption("settings.storage"))
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(theme.PAD)
        self.storage = QtWidgets.QLineEdit()
        self.storage.setReadOnly(True)
        row.addWidget(self.storage, 1)
        row.addWidget(_button("settings.browse", self._browse, width=96))
        row.addWidget(_button("settings.open_folder", self._open_folder, width=96))
        layout.addLayout(row)
        self.content = _meta()
        layout.addWidget(self.content)
        self.counts = _meta()
        layout.addWidget(self.counts)

        layout.addSpacing(theme.PAD_S)
        layout.addWidget(self._caption("settings.model"))
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(theme.PAD)
        self.models = QtWidgets.QComboBox()
        self.models.setObjectName("aeroCombo")
        self.models.setCursor(QtCore.Qt.PointingHandCursor)
        self.models.setFocusPolicy(QtCore.Qt.NoFocus)
        self.models.activated.connect(self._on_model)
        row.addWidget(self.models, 1)
        self.add_button = _button("settings.add", lambda: None, width=80)
        self.add_button.setEnabled(False)
        self.add_button.setToolTip(i18n.t("settings.add_soon"))
        row.addWidget(self.add_button)
        row.addWidget(_button("settings.manage", self._manage, width=96))
        layout.addLayout(row)
        self.model_info = _meta()
        layout.addWidget(self.model_info)

        layout.addSpacing(theme.PAD_S)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(theme.PAD)
        self.train_button = _button("settings.refine_model", self._on_train, object_name="aeroRun", width=220)
        row.addWidget(self.train_button)
        self.revert_button = _button("settings.revert", self._on_revert, width=160)
        row.addWidget(self.revert_button)
        row.addStretch(1)
        layout.addLayout(row)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setObjectName("aeroProgress")
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        self.train_status = _meta()
        layout.addWidget(self.train_status)
        self.cancel_button = _button("settings.train_cancel", self._on_cancel)
        self.cancel_button.setVisible(False)
        layout.addWidget(self.cancel_button, 0, QtCore.Qt.AlignLeft)
        return card

    @staticmethod
    def _caption(text_key):
        label = QtWidgets.QLabel(i18n.t(text_key))
        label.setObjectName("aeroEyebrow")
        return label

    # -- etat -------------------------------------------------------------------------

    def refresh(self):
        """Relit les reglages, les modeles et le nombre d'annotations."""
        training_on = self._job is not None
        self.use_refine.blockSignals(True)
        self.use_refine.setChecked(training.collect_enabled())
        self.use_refine.blockSignals(False)
        self.storage.setText(training.storage_dir())
        self.storage.setCursorPosition(0)
        self.storage.setToolTip(training.storage_dir())
        summary = training.storage_summary()
        self.content.setText(i18n.t("settings.storage_content", a=summary["annotations"], m=summary["models"],
                                    d=summary["detections"], t=summary["trainings"]))

        counts = training.feedback_counts()
        positives = sum(c["positives"] for c in counts.values())
        negatives = sum(c["negatives"] for c in counts.values())
        if positives + negatives:
            self.counts.setText(i18n.t("settings.data_counts", p=positives, r=negatives,
                                       damier=counts["damier"]["samples"], bombe=counts["bombe"]["samples"]))
        else:
            self.counts.setText(i18n.t("settings.data_none"))

        self.models.blockSignals(True)
        self.models.clear()
        active = training.active_model()
        for model in training.list_models():
            self.models.addItem(model_text(model), model["id"])
            if model["id"] == active:
                self.models.setCurrentIndex(self.models.count() - 1)
        self.models.blockSignals(False)
        current = [m for m in training.list_models() if m["id"] == active]
        base = training.list_models()[0]
        if current and not current[0].get("base"):
            self.model_info.setText(i18n.t("settings.model_local", types=", ".join(current[0].get("types") or [])))
        else:
            self.model_info.setText(i18n.t("settings.model_base"))
        self.revert_button.setText(i18n.t("settings.revert", name=base["name"]))
        self.revert_button.setEnabled(not training_on and active != base["id"])
        self.train_button.setEnabled(not training_on and positives > 0)
        self.models.setEnabled(not training_on)
        self.use_refine.setEnabled(not training_on)
        self.refresh_engines()

    # -- reglages ---------------------------------------------------------------------

    def _on_use_refine(self, checked):
        training.set_collect(bool(checked))
        journal.write("Modeles IA : affinages manuels {}".format("enregistres" if checked else "non enregistres"))

    def _open_folder(self):
        folder = training.storage_dir()
        try:
            os.makedirs(folder, exist_ok=True)
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(folder))
        except Exception as exc:
            journal.write("Modeles IA : ouverture du dossier impossible : {!r}".format(exc))

    def _browse(self):
        old = training.storage_dir()
        chosen = QtWidgets.QFileDialog.getExistingDirectory(self, i18n.t("settings.storage_pick"), old)
        if not chosen:
            return
        chosen = os.path.normpath(chosen)
        if chosen != os.path.normpath(old):
            self._offer_copy(old, chosen)
        training.set_storage_dir(chosen)
        journal.write("Modeles IA : dossier de stockage : {}".format(chosen))
        self.refresh()

    def _offer_copy(self, old, new):
        """Nouveau dossier vide et ancien dossier plein : propose de copier les donnees (rien n'est efface)."""
        before, after = training.storage_summary(old), training.storage_summary(new)
        if not (before["annotations"] or before["models"]) or after["annotations"] or after["models"]:
            return
        text = i18n.t("settings.copy_confirm", a=before["annotations"], m=before["models"])
        if not dialogs.ask(self, i18n.t("settings.storage"), text):
            return
        try:
            training.copy_storage(old, new)
            journal.write("Modeles IA : donnees copiees vers {}".format(new))
        except Exception as exc:
            journal.write("Modeles IA : copie impossible : {!r}".format(exc))
            self._say(i18n.t("settings.copy_failed", message=str(exc)))

    def _on_model(self, index):
        training.set_active_model(self.models.itemData(index))
        self.refresh()

    def _manage(self):
        _exec(ManageDialog(self))
        self.refresh()

    def _on_revert(self):
        training.revert_to_base()
        self.train_status.setText(i18n.t("settings.reverted"))
        self.refresh()

    # -- reentrainement ---------------------------------------------------------------

    def _say(self, text):
        dialogs.inform(self, i18n.t("settings.train_title"), text)

    def _on_train(self):
        if self._job is not None:
            return
        state = self.panel._state
        if state is not None and aigcp.busy(state):
            self._say(i18n.t("settings.train_busy_detect"))
            return
        missing = training.missing_tools()
        if missing:
            offer = i18n.t("settings.train_tools_offer", tools=", ".join(missing))
            if not dialogs.ask(self, i18n.t("settings.train_title"), offer):
                return
            if not install_ui.run(self, None, training=True, confirm=False):
                return
            self.refresh()
        counts = training.feedback_counts()
        positives = sum(c["positives"] for c in counts.values())
        negatives = sum(c["negatives"] for c in counts.values())
        text = i18n.t("settings.train_confirm", p=positives, r=negatives)
        if not dialogs.ask(self, i18n.t("settings.train_title"), text):
            return
        try:
            self._job = training.Training().start()
        except Exception as exc:
            self._job = None
            self.train_status.setText(i18n.t("settings.train_failed", message=str(exc)))
            journal.write("Modeles IA : lancement impossible : {!r}".format(exc))
            self.refresh()
            return
        self.progress.setRange(0, 0)
        self.progress.setVisible(True)
        self.cancel_button.setVisible(True)
        self.train_status.setText(i18n.t("settings.training"))
        self._timer.start()
        self.refresh()

    @staticmethod
    def _failure_text(result):
        """Message d'un echec dans la langue courante, d'apres son code ; repli : le texte du module."""
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
            self.train_status.setText(i18n.t("settings.train_cancelling"))

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
            if fraction is None:
                self.progress.setRange(0, 0)
            else:
                self.progress.setRange(0, 1000)
                self.progress.setValue(int(max(0.0, min(1.0, fraction)) * 1000))
            self.train_status.setText(i18n.t("settings.training") + (" " + text if text else ""))
            return
        self._timer.stop()
        self._job = None
        self.progress.setVisible(False)
        self.cancel_button.setVisible(False)
        if result.get("ok"):
            training.set_active_model(result["model_id"])
            message = i18n.t("settings.train_done", name=result["name"])
            if result.get("warnings"):
                message += " " + i18n.t("settings.train_warning")
        elif result.get("code") == "cancelled":
            message = i18n.t("settings.train_cancelled")
        else:
            message = i18n.t("settings.train_failed", message=self._failure_text(result))
        self.train_status.setText(message)
        journal.write("Modeles IA : {}".format(message))
        self.refresh()
        self._say(message)

    # -- vie de la fenetre ------------------------------------------------------------

    def closeEvent(self, event):
        if self._job is not None:
            self._say(i18n.t("settings.train_running_close"))
            event.ignore()
            return
        super(SettingsDialog, self).closeEvent(event)

    def release(self):
        """Rend au panneau la carte des cases a cocher avant que la fenetre disparaisse."""
        self.panel.more.setParent(None)


def show(panel, existing=None):
    """Ouvre la fenetre Parametres (modale). Reutilise l'instance deja construite.

    Les textes sont fixes a la construction : apres un changement de langue la fenetre
    est refaite.
    """
    dialog = existing
    try:
        if dialog is not None:
            dialog.objectName()
            if dialog._lang != i18n.lang():
                dialog.release()
                dialog.deleteLater()
                dialog = None
    except RuntimeError:
        dialog = None
    if dialog is None:
        dialog = SettingsDialog(panel, panel)
    dialog.refresh()
    dialog.setModal(True)
    _exec(dialog)
    return dialog
