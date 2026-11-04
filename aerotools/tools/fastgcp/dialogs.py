"""Boites de dialogue maison (Qt) : question Oui / Non et message simple.

Sous Metashape, un QMessageBox coupe son texte quand il est long ; ces boites ont une
largeur fixe, un texte a la ligne qui prend toute la hauteur dont il a besoin, et le
style du panneau. `ask` renvoie True pour Oui, `inform` n'a qu'un bouton.
"""

from ... import i18n, theme
from ...qt import QtCore, QtWidgets

WIDTH = 480          # largeur demandee ; la fenetre peut etre plus etroite, le texte suit
MIN_WIDTH = 300
BUTTON_HEIGHT = 40
BUTTON_WIDTH = 120


def _run(dialog):
    return (getattr(dialog, "exec_", None) or getattr(dialog, "exec"))()


def _button(text, handler, object_name="aeroGhost"):
    button = QtWidgets.QPushButton(text)
    button.setObjectName(object_name)
    button.setProperty("pill", True)
    button.setCursor(QtCore.Qt.PointingHandCursor)
    button.setFixedHeight(BUTTON_HEIGHT)
    button.setMinimumWidth(BUTTON_WIDTH)
    button.setAutoDefault(False)
    button.clicked.connect(lambda *_args: handler())
    return button


class _Box(QtWidgets.QDialog):
    """Fenetre dont le texte reste entier quelle que soit la largeur obtenue.

    Le QLabel passe a la ligne tout seul. Une fois la fenetre affichee on lui donne, une seule
    fois par largeur, la hauteur que ce texte demande : la mise en page seule la sous-estime
    parfois (le dernier paragraphe etait coupe). Pas de boucle : on ne refait rien tant que la
    largeur ne change pas.
    """
    label = None
    _fitted = None

    def _fit(self):
        label = self.label
        width = label.width() if label is not None else 0
        if width <= 0 or width == self._fitted:
            return
        self._fitted = width
        need = label.heightForWidth(width)
        if need > label.height():
            label.setMinimumHeight(need)
            self.resize(self.width(), max(self.height(), self.minimumSizeHint().height()))

    def showEvent(self, event):
        super(_Box, self).showEvent(event)
        QtCore.QTimer.singleShot(0, self._fit)


def _build(parent, title, text, buttons):
    """Fenetre modale : `buttons` = [(texte, valeur rendue, objectName)]. Renvoie (dialog, choix)."""
    dialog = _Box(parent)
    dialog.setWindowTitle(title)
    dialog.setModal(True)
    dialog.setMinimumWidth(MIN_WIDTH)
    dialog.resize(WIDTH, 10)          # la hauteur minimale de la mise en page fait le reste
    outer = QtWidgets.QVBoxLayout(dialog)
    outer.setContentsMargins(0, 0, 0, 0)
    root = QtWidgets.QWidget()
    root.setObjectName("aeroPanel")
    root.setAttribute(QtCore.Qt.WA_StyledBackground, True)
    root.setStyleSheet(theme.stylesheet())
    outer.addWidget(root)

    layout = QtWidgets.QVBoxLayout(root)
    pad = theme.PAD * 2
    layout.setContentsMargins(pad, pad, pad, pad)
    layout.setSpacing(theme.PAD * 2)
    # Un QLabel qui passe a la ligne, sans taille fixe : sa hauteur suit la largeur reellement obtenue
    # (sous Metashape la fenetre est parfois plus etroite que demandee).
    label = QtWidgets.QLabel(text)
    label.setWordWrap(True)
    label.setTextInteractionFlags(QtCore.Qt.NoTextInteraction)
    label.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Minimum)
    layout.addWidget(label)
    dialog.label = label

    choice = {"value": None}
    row = QtWidgets.QHBoxLayout()
    row.addStretch(1)
    for caption, value, object_name in buttons:
        def pick(_value=value):
            choice["value"] = _value
            dialog.accept()
        row.addWidget(_button(caption, pick, object_name))
    layout.addLayout(row)
    return dialog, choice


def ask(parent, title, text):
    """Question Oui / Non (Non a la fermeture). True si Oui.

    Ordre de Windows : la reponse positive a gauche, la negative a droite.
    """
    dialog, choice = _build(parent, title, text, [
        (i18n.t("dialogs.yes"), True, "aeroYes"), (i18n.t("dialogs.no"), False, "aeroNo")])
    _run(dialog)
    return bool(choice["value"])


def inform(parent, title, text):
    """Message avec un seul bouton."""
    dialog, _choice = _build(parent, title, text, [(i18n.t("dialogs.ok"), True, "aeroRun")])
    _run(dialog)
