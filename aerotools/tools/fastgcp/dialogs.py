"""Boites de dialogue maison (Qt) : question Oui / Non et message simple.

Sous Metashape, un QMessageBox coupe son texte quand il est long ; ces boites ont une
largeur fixe, un texte a la ligne qui prend toute la hauteur dont il a besoin, et le
style du panneau. `ask` renvoie True pour Oui, `inform` n'a qu'un bouton.
"""

from ... import i18n, theme
from ...qt import QtCore, QtWidgets

WIDTH = 480


def _run(dialog):
    return (getattr(dialog, "exec_", None) or getattr(dialog, "exec"))()


def _button(text, handler, object_name="aeroGhost"):
    button = QtWidgets.QPushButton(text)
    button.setObjectName(object_name)
    button.setProperty("pill", True)
    button.setCursor(QtCore.Qt.PointingHandCursor)
    button.setFixedHeight(32)
    button.setMinimumWidth(96)
    button.setAutoDefault(False)
    button.clicked.connect(lambda *_args: handler())
    return button


def _build(parent, title, text, buttons):
    """Fenetre modale : `buttons` = [(texte, valeur rendue, objectName)]. Renvoie (dialog, choix)."""
    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setModal(True)
    dialog.setFixedWidth(WIDTH)
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
    label = QtWidgets.QLabel(text)
    label.setWordWrap(True)
    label.setTextInteractionFlags(QtCore.Qt.NoTextInteraction)
    layout.addWidget(label, 1)               # le label recoit son style en entrant dans la fenetre
    # Largeur fixe, puis hauteur calculee pour cette largeur : sans cela le texte long est coupe.
    label.setFixedWidth(WIDTH - 2 * pad)
    label.ensurePolished()                  # la police du style doit etre en place avant de mesurer
    wanted = label.fontMetrics().boundingRect(QtCore.QRect(0, 0, WIDTH - 2 * pad, 10000),
                                              int(QtCore.Qt.TextWordWrap), text)
    # On prend la plus grande des deux mesures : un texte coupe est pire qu'une marge en trop.
    label.setFixedHeight(max(wanted.height(), label.heightForWidth(WIDTH - 2 * pad)) + 8)

    choice = {"value": None}
    row = QtWidgets.QHBoxLayout()
    row.addStretch(1)
    for caption, value, object_name in buttons:
        def pick(_value=value):
            choice["value"] = _value
            dialog.accept()
        row.addWidget(_button(caption, pick, object_name))
    layout.addLayout(row)
    dialog.adjustSize()
    return dialog, choice


def ask(parent, title, text):
    """Question Oui / Non (Non a la fermeture). True si Oui.

    Ordre de Windows : la reponse positive a gauche, la negative a droite.
    """
    dialog, choice = _build(parent, title, text, [
        (i18n.t("dialogs.yes"), True, "aeroGhost"), (i18n.t("dialogs.no"), False, "aeroGhost")])
    _run(dialog)
    return bool(choice["value"])


def inform(parent, title, text):
    """Message avec un seul bouton."""
    dialog, _choice = _build(parent, title, text, [(i18n.t("dialogs.ok"), True, "aeroRun")])
    _run(dialog)
