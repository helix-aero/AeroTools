"""Fenetre Parametres de FastGCP (Qt) : l'engrenage du panneau.

Quatre etapes numerotees, dans l'ordre ou l'outil se construit, puis les options :
  1 Detection    le moteur qui calcule (leger ou precis) et son etat d'installation ;
  2 Modele       le modele d'IA utilise pour detecter les cibles ;
  3 Donnees      le dossier ou sont gardes les pointages corriges, les modeles et les essais ;
  4 Entrainement corrections -> « Entrainer » -> nouveau modele (le modele d'origine n'est jamais modifie).
Options : les cases a cocher et le journal (la carte `panel.more`, construite par le panneau).
En bas : « Verifier les mises a jour » et « Fermer ».

L'entrainement (training.py) tourne dans un Python a part et bloque l'ordinateur un moment
(carte graphique) : un avertissement le dit avant de commencer, la fenetre suit l'avancement et
permet d'annuler. Les modeles produits s'appellent « Local 1.1 », « Local 1.2 »... et on peut
revenir au modele d'origine.
"""

import datetime
import functools
import os

from ... import i18n, journal, theme
from ...qt import QtCore, QtGui, QtWidgets
from . import aigcp, dialogs, engines, training
from . import install_ui
from .review_dialog import _exec, _restyle, _styled_root

POLL_MS = 700
WIDTH, HEIGHT = 540, 760
BUTTON_HEIGHT = 40


def _btn(text_key, handler, kind="aeroBtn", width=0):
    """Gros bouton : texte centre, 40 px de haut. `aeroGo` = vert (l'action), `aeroBtn` = gris."""
    button = QtWidgets.QPushButton(i18n.t(text_key))
    button.setObjectName(kind)
    button.setCursor(QtCore.Qt.PointingHandCursor)
    button.setFixedHeight(BUTTON_HEIGHT)
    if width:
        button.setMinimumWidth(width)
    button.setAutoDefault(False)
    button.clicked.connect(lambda *_args: handler())
    return button


def _pane(title_key, number=None):
    """Section noire avec un bandeau de titre : (cadre, mise en page du corps, mise en page du bandeau)."""
    pane = QtWidgets.QFrame()
    pane.setObjectName("aeroPane")
    outer = QtWidgets.QVBoxLayout(pane)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)
    head = QtWidgets.QFrame()
    head.setObjectName("aeroPaneHead")
    head_layout = QtWidgets.QHBoxLayout(head)
    head_layout.setContentsMargins(12, 8, 12, 8)
    head_layout.setSpacing(theme.PAD)
    if number is not None:
        badge = QtWidgets.QLabel(str(number))
        badge.setObjectName("aeroBadge")
        badge.setFixedSize(20, 20)
        badge.setAlignment(QtCore.Qt.AlignCenter)
        head_layout.addWidget(badge)
    title = QtWidgets.QLabel(i18n.t(title_key))
    title.setObjectName("aeroPaneTitle")
    head_layout.addWidget(title)
    head_layout.addStretch(1)
    outer.addWidget(head)
    body = QtWidgets.QWidget()
    body.setObjectName("aeroPaneBody")
    layout = QtWidgets.QVBoxLayout(body)
    layout.setContentsMargins(12, 10, 12, 12)
    layout.setSpacing(theme.PAD)
    outer.addWidget(body)
    return pane, layout, head_layout


def _tile(label_key):
    """Petite case chiffree : (cadre, etiquette du chiffre)."""
    tile = QtWidgets.QFrame()
    tile.setObjectName("aeroStat")
    layout = QtWidgets.QVBoxLayout(tile)
    layout.setContentsMargins(8, 6, 8, 6)
    layout.setSpacing(0)
    number = QtWidgets.QLabel("0")
    number.setObjectName("aeroStatNum")
    number.setAlignment(QtCore.Qt.AlignCenter)
    caption = QtWidgets.QLabel(i18n.t(label_key))
    caption.setObjectName("aeroStatLabel")
    caption.setAlignment(QtCore.Qt.AlignCenter)
    layout.addWidget(number)
    layout.addWidget(caption)
    return tile, number


def _combo():
    combo = QtWidgets.QComboBox()
    combo.setObjectName("aeroCombo")
    combo.setCursor(QtCore.Qt.PointingHandCursor)
    combo.setFocusPolicy(QtCore.Qt.NoFocus)
    return combo


def _warn():
    label = QtWidgets.QLabel()
    label.setObjectName("aeroWarn")
    label.setWordWrap(True)
    label.setVisible(False)
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
        root.setProperty("settings", True)
        layout = QtWidgets.QVBoxLayout(root)
        pad = theme.PAD * 2
        layout.setContentsMargins(pad, pad, pad, pad)
        layout.setSpacing(theme.PAD)
        self.list = QtWidgets.QListWidget()
        self.list.setObjectName("aeroThumbs")
        self.list.itemSelectionChanged.connect(self._update)
        layout.addWidget(self.list, 1)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(theme.PAD)
        row.addStretch(1)
        self.delete_button = _btn("settings.delete", self._delete, width=110)
        row.addWidget(self.delete_button)
        row.addWidget(_btn("settings.close", self.accept, width=110))
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
        root.setProperty("settings", True)
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
        # setWidget remet le remplissage du fond : on le coupe apres, pour voir le fond de la fenetre.
        body.setAutoFillBackground(False)
        layout = QtWidgets.QVBoxLayout(body)
        pad = theme.PAD_L
        layout.setContentsMargins(pad, pad, pad, pad)
        layout.setSpacing(theme.PAD + theme.PAD_S)

        layout.addWidget(self._build_detection())
        layout.addWidget(self._build_model())
        layout.addWidget(self._build_data())
        layout.addWidget(self._build_training())
        layout.addWidget(self._build_options())
        layout.addStretch(1)

        bottom = QtWidgets.QFrame()
        bottom.setObjectName("aeroBottom")
        row = QtWidgets.QHBoxLayout(bottom)
        row.setContentsMargins(pad, theme.PAD + theme.PAD_S, pad, theme.PAD + theme.PAD_S)
        row.setSpacing(theme.PAD)
        row.addWidget(_btn("settings.update_open", self._open_updates, width=200))
        row.addStretch(1)
        row.addWidget(_btn("settings.close", self.close, width=120))
        outer.addWidget(bottom)

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)
        self.refresh()

    def _open_updates(self):
        from ... import update_dialog
        update_dialog.open_and_reload(self)

    # -- 1. Detection ------------------------------------------------------------------

    def _build_detection(self):
        pane, layout, _head = _pane("settings.sec_detect", 1)
        self.engine_combo = _combo()
        for choice in engines.CHOICES:
            self.engine_combo.addItem(i18n.t(engines.CHOICE_TEXT[choice]), choice)
        self.engine_combo.activated.connect(self._on_engine)
        layout.addWidget(self.engine_combo)
        self.engine_rows = {}
        for key in ("light", "precise"):
            layout.addLayout(self._engine_row(key))
        self.engine_hint = _warn()
        layout.addWidget(self.engine_hint)
        return pane

    def _engine_row(self, key):
        """Une ligne : nom du moteur, puis « Installe » ou le bouton Installer."""
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(theme.PAD)
        name = QtWidgets.QLabel(i18n.t(engines.NAME_TEXT[key]))
        row.addWidget(name, 1)
        chip = QtWidgets.QLabel(i18n.t("settings.installed"))
        chip.setObjectName("aeroChipOk")
        chip.setAlignment(QtCore.Qt.AlignCenter)
        row.addWidget(chip)
        install = _btn("settings.install", functools.partial(self._install, key), width=110)
        install.setFixedHeight(32)
        row.addWidget(install)
        self.engine_rows[key] = (chip, install)
        return row

    def refresh_engines(self):
        """Etat des moteurs installes (une courte interrogation du Python externe)."""
        state = engines.status()
        busy = self._job is not None
        index = self.engine_combo.findData(engines.selected())
        self.engine_combo.blockSignals(True)
        self.engine_combo.setCurrentIndex(max(0, index))
        self.engine_combo.blockSignals(False)
        can = engines.can_install()
        for key, installed in engines.describe(state):
            if key not in self.engine_rows:
                continue
            chip, button = self.engine_rows[key]
            chip.setVisible(installed)
            button.setVisible(not installed)
            button.setEnabled(not busy and can)
        training_chip, training_button = self.tools_widgets
        installed_training = dict(engines.describe(state)).get("training", False)
        training_chip.setVisible(installed_training)
        training_button.setVisible(not installed_training)
        training_button.setEnabled(not busy and can)
        if not can:
            self._warn(i18n.t("settings.engine_custom_python", path=aigcp.python_path()))
        elif engines.resolve(state=state) is None:
            self._warn(i18n.t("settings.engine_none"))
        else:
            self._warn("")

    def _warn(self, text):
        self.engine_hint.setText(text)
        self.engine_hint.setVisible(bool(text))

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

    # -- 2. Modele ---------------------------------------------------------------------

    def _build_model(self):
        pane, layout, _head = _pane("settings.sec_model", 2)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(theme.PAD)
        self.models = _combo()
        self.models.activated.connect(self._on_model)
        row.addWidget(self.models, 1)
        row.addWidget(_btn("settings.manage", self._manage, width=110))
        layout.addLayout(row)
        return pane

    # -- 3. Donnees --------------------------------------------------------------------

    def _build_data(self):
        pane, layout, _head = _pane("settings.sec_data", 3)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(theme.PAD)
        self.storage = QtWidgets.QLineEdit()
        self.storage.setReadOnly(True)
        row.addWidget(self.storage, 1)
        row.addWidget(_btn("settings.browse", self._browse, width=110))
        row.addWidget(_btn("settings.open_folder", self._open_folder, width=90))
        layout.addLayout(row)
        tiles = QtWidgets.QHBoxLayout()
        tiles.setSpacing(theme.PAD)
        self.stat_numbers = {}
        for key, label in (("annotations", "settings.stat_notes"), ("models", "settings.stat_models"),
                           ("detections", "settings.stat_detections"), ("trainings", "settings.stat_trainings")):
            tile, number = _tile(label)
            tiles.addWidget(tile, 1)
            self.stat_numbers[key] = number
        layout.addLayout(tiles)
        self.use_refine = QtWidgets.QCheckBox(i18n.t("settings.use_refine"))
        self.use_refine.setCursor(QtCore.Qt.PointingHandCursor)
        self.use_refine.toggled.connect(self._on_use_refine)
        layout.addWidget(self.use_refine)
        return pane

    # -- 4. Entrainement ---------------------------------------------------------------

    def _build_training(self):
        pane, layout, _head = _pane("settings.sec_train", 4)
        flow = QtWidgets.QHBoxLayout()
        flow.setSpacing(theme.PAD)
        fixes, self.fixes_number = _tile("settings.flow_fixes")
        flow.addWidget(fixes, 1)
        flow.addWidget(self._arrow())
        self.train_button = _btn("settings.refine_model", self._on_train, kind="aeroGo", width=120)
        self.train_button.setFixedHeight(52)
        flow.addWidget(self.train_button, 1)
        flow.addWidget(self._arrow())
        active, self.active_number = _tile("settings.flow_model")
        self.active_number.setStyleSheet("font-size: 11pt;")
        flow.addWidget(active, 1)
        layout.addLayout(flow)

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(theme.PAD)
        row.addWidget(QtWidgets.QLabel(i18n.t(engines.NAME_TEXT["training"])), 1)
        chip = QtWidgets.QLabel(i18n.t("settings.installed"))
        chip.setObjectName("aeroChipOk")
        chip.setAlignment(QtCore.Qt.AlignCenter)
        row.addWidget(chip)
        install = _btn("settings.install", functools.partial(self._install, "training"), width=110)
        install.setFixedHeight(32)
        row.addWidget(install)
        self.tools_widgets = (chip, install)
        layout.addLayout(row)

        self.revert_button = _btn("settings.revert", self._on_revert)
        layout.addWidget(self.revert_button)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setObjectName("aeroProgress")
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        self.train_status = QtWidgets.QLabel()
        self.train_status.setObjectName("aeroMeta")
        self.train_status.setWordWrap(True)
        self.train_status.setVisible(False)
        layout.addWidget(self.train_status)
        self.cancel_button = _btn("settings.train_cancel", self._on_cancel)
        self.cancel_button.setVisible(False)
        layout.addWidget(self.cancel_button)
        return pane

    def _status(self, text):
        self.train_status.setText(text)
        self.train_status.setVisible(bool(text))

    @staticmethod
    def _arrow():
        label = QtWidgets.QLabel("→")
        label.setObjectName("aeroArrow")
        label.setAlignment(QtCore.Qt.AlignCenter)
        return label

    # -- Options -----------------------------------------------------------------------

    def _build_options(self):
        pane, layout, _head = _pane("settings.sec_options")
        _restyle(self.panel.more, "aeroBare")
        self.panel.more.setParent(pane)
        layout.addWidget(self.panel.more)
        self.panel.more.show()
        return pane

    # -- etat --------------------------------------------------------------------------

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
        for key, number in self.stat_numbers.items():
            number.setText(str(summary.get(key, 0)))

        counts = training.feedback_counts()
        positives = sum(c["positives"] for c in counts.values())
        self.fixes_number.setText(str(positives))

        models = training.list_models()
        active = training.active_model()
        self.models.blockSignals(True)
        self.models.clear()
        for model in models:
            self.models.addItem(model_text(model), model["id"])
            if model["id"] == active:
                self.models.setCurrentIndex(self.models.count() - 1)
        self.models.blockSignals(False)
        current = [m for m in models if m["id"] == active]
        base = models[0]
        self.active_number.setText(current[0]["name"] if current else base["name"])
        self.revert_button.setText(i18n.t("settings.revert", name=base["name"]))
        self.revert_button.setVisible(active != base["id"])
        self.revert_button.setEnabled(not training_on)
        self.train_button.setEnabled(not training_on and positives > 0)
        self.train_button.setToolTip("" if positives > 0 else i18n.t("settings.train_need_data"))
        self.models.setEnabled(not training_on)
        self.use_refine.setEnabled(not training_on)
        self.refresh_engines()

    # -- reglages ----------------------------------------------------------------------

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
        if not dialogs.ask(self, i18n.t("settings.sec_data"), text):
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
        self._status(i18n.t("settings.reverted"))
        self.refresh()

    # -- reentrainement ----------------------------------------------------------------

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
            self._status(i18n.t("settings.train_failed", message=str(exc)))
            journal.write("Modeles IA : lancement impossible : {!r}".format(exc))
            self.refresh()
            return
        self.progress.setRange(0, 0)
        self.progress.setVisible(True)
        self.cancel_button.setVisible(True)
        self._status(i18n.t("settings.training"))
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
            self._status(i18n.t("settings.train_cancelling"))

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
            self._status(i18n.t("settings.training") + (" " + text if text else ""))
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
        self._status(message)
        journal.write("Modeles IA : {}".format(message))
        self.refresh()
        self._say(message)

    # -- vie de la fenetre -------------------------------------------------------------

    def closeEvent(self, event):
        if self._job is not None:
            self._say(i18n.t("settings.train_running_close"))
            event.ignore()
            return
        super(SettingsDialog, self).closeEvent(event)

    def release(self):
        """Rend au panneau la carte des options avant que la fenetre disparaisse."""
        _restyle(self.panel.more, "aeroCard")
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
