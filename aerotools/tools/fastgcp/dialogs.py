"""Boites de dialogue maison (Qt) : question Oui / Non et message simple.

Sous Metashape, un QMessageBox coupe son texte quand il est long ; ces boites ont une
largeur fixe, un texte a la ligne qui prend toute la hauteur dont il a besoin, et le
style du panneau. `ask` renvoie True pour Oui, `inform` n'a qu'un bouton.
"""

from ... import i18n, theme
from ...qt import QtCore, QtWidgets

WIDTH = 480          # largeur demandee ; la fenetre peut etre plus etroite, le texte suit
MIN_WIDTH = 300
MAX_TEXT_HEIGHT = 520


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


class _Box(QtWidgets.QDialog):
    """Fenetre dont le texte s'adapte a la largeur REELLEMENT obtenue.

    Sous Metashape la fenetre est plus etroite que demandee : un QLabel a largeur fixe
    etait coupe a droite. Un champ de texte passe a la ligne tout seul a la largeur dont il
    dispose, et on fixe sa hauteur une fois, a l'ouverture (sans reboucler sur la taille).
    S'il manquait de la place, il aurait une barre de defilement : jamais de texte perdu.
    """
    view = None

    def _fit(self):
        view = self.view
        if view is None:
            return
        width = view.viewport().width()
        document = view.document()
        document.setTextWidth(width if width > 0 else WIDTH - 4 * theme.PAD)
        view.setFixedHeight(min(int(document.size().height()) + 10, MAX_TEXT_HEIGHT))
        self.resize(self.width(), self.sizeHint().height())

    def showEvent(self, event):
        super(_Box, self).showEvent(event)
        self._fit()
        QtCore.QTimer.singleShot(0, self._fit)       # une fois la mise en page definitive


def _build(parent, title, text, buttons):
    """Fenetre modale : `buttons` = [(texte, valeur rendue, objectName)]. Renvoie (dialog, choix)."""
    dialog = _Box(parent)
    dialog.setWindowTitle(title)
    dialog.setModal(True)
    dialog.setMinimumWidth(MIN_WIDTH)
    dialog.resize(WIDTH, 100)
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
    view = QtWidgets.QTextBrowser()
    view.setPlainText(text)
    view.setFrameShape(QtWidgets.QFrame.NoFrame)
    view.setStyleSheet("QTextBrowser {{ background: transparent; border: none; color: {}; }}".format(theme.FG_1))
    view.viewport().setAutoFillBackground(False)
    view.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
    view.setFocusPolicy(QtCore.Qt.NoFocus)
    view.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
    layout.addWidget(view)
    dialog.view = view

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
        (i18n.t("dialogs.yes"), True, "aeroGhost"), (i18n.t("dialogs.no"), False, "aeroGhost")])
    _run(dialog)
    return bool(choice["value"])


def inform(parent, title, text):
    """Message avec un seul bouton."""
    dialog, _choice = _build(parent, title, text, [(i18n.t("dialogs.ok"), True, "aeroRun")])
    _run(dialog)
