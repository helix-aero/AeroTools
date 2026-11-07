"""Custom dialog boxes (Qt): Yes / No question and simple message.

Under Metashape, a QMessageBox cuts its text when it is long; these boxes have a
fixed width, wrapped text that takes all the height it needs, and the
panel style. `ask` returns True for Yes, `inform` has a single button.
"""

from . import i18n, theme
from .qt import QtCore, QtWidgets

WIDTH = 480          # requested width; the window may be narrower, the text follows
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
    """Window whose text stays whole whatever width it ends up with.

    The QLabel wraps by itself. Once the window is shown it is given, once per width, the
    height this text asks for: the layout alone sometimes underestimates it (the last
    paragraph used to be cut). No loop: nothing is redone while the width does not change.
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
    """Modal window: `buttons` = [(text, returned value, objectName)]. Returns (dialog, choice)."""
    dialog = _Box(parent)
    dialog.setWindowTitle(title)
    dialog.setModal(True)
    dialog.setMinimumWidth(MIN_WIDTH)
    dialog.resize(WIDTH, 10)          # the minimum height of the layout does the rest
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
    # A wrapping QLabel without a fixed size: its height follows the width actually obtained
    # (under Metashape the window is sometimes narrower than requested).
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
    """Yes / No question (No on close). True if Yes.

    Windows order: the positive answer on the left, the negative one on the right.
    """
    dialog, choice = _build(parent, title, text, [
        (i18n.t("dialogs.yes"), True, "aeroYes"), (i18n.t("dialogs.no"), False, "aeroNo")])
    _run(dialog)
    return bool(choice["value"])


def inform(parent, title, text):
    """Message with a single button."""
    dialog, _choice = _build(parent, title, text, [(i18n.t("dialogs.ok"), True, "aeroRun")])
    _run(dialog)
