"""Message helpers: `confirm`, `inform`, `error` replace QMessageBox and Metashape's message box for questions and notices.

A small modal window in the kit's language: a status glyph, a heading, wrapped text; buttons in the fixed order (secondary left,
primary right-most); Enter presses the primary, Esc answers "no". The width is fixed, the height follows the text.
"""

from .. import i18n
from ..qt import QtCore, QtWidgets
from . import style, tokens
from .blocks import Glyph, hbox, vbox
from .controls import Button, label
from .metrics import px

Qt = QtCore.Qt


def _run(dialog):
    return (getattr(dialog, "exec_", None) or getattr(dialog, "exec"))()


class MessageBox(QtWidgets.QDialog):
    """The modal window behind the helpers. `buttons` = [(caption, value, role)]; roles: primary, danger, secondary, quiet."""
    _fitted = None

    def __init__(self, parent, title, text, buttons, kind="info", heading=None, details=None):
        super(MessageBox, self).__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.choice = {"value": None}
        self._default = None
        self.setFixedWidth(px(440))
        outer = vbox(parent=self)
        self.root = QtWidgets.QWidget()
        self.root.setAttribute(Qt.WA_StyledBackground, True)
        style.apply(self.root)
        outer.addWidget(self.root)
        lay = vbox((tokens.XL,) * 4, tokens.L, parent=self.root)
        row = hbox(spacing=tokens.M)
        self.glyph = Glyph({"danger": "error", "warning": "warn"}.get(kind, kind), tokens.ICON_XL // 2 + 4)
        row.addWidget(self.glyph, 0, Qt.AlignTop)
        column = vbox(spacing=tokens.S)
        self.heading = label(heading or "", "lead", wrap=True)
        self.heading.setVisible(bool(heading))
        self.text = label(text, "body", wrap=True)
        self.text.setTextInteractionFlags(Qt.NoTextInteraction)
        column.addWidget(self.heading)
        column.addWidget(self.text)
        if details:
            self.details = label(str(details)[-1200:], "small", "muted", wrap=True, selectable=True)
            column.addWidget(self.details)
        row.addLayout(column, 1)
        lay.addLayout(row)
        foot = hbox(spacing=tokens.S)
        primary = [b for b in buttons if b[2] in ("primary", "danger")]
        others = [b for b in buttons if b not in primary]
        self.buttons = []
        for caption, value, role in others:
            foot.addWidget(self._button(caption, value, role))
        foot.addStretch(1)
        for caption, value, role in primary:
            widget = self._button(caption, value, role)
            self._default = widget
            foot.addWidget(widget)
        lay.addLayout(foot)
        if self._default is None and self.buttons:
            self._default = self.buttons[-1]
        if self._default is not None:
            self._default.setFocus()
        self.cancel_value = next((v for _c, v, r in others if r != "danger"), None)

    def _button(self, caption, value, role):
        widget = Button(caption, role)

        def pick(_checked=False, v=value):
            self.choice["value"] = v
            self.accept()
        widget.clicked.connect(pick)
        self.buttons.append(widget)
        return widget

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            focus = QtWidgets.QApplication.focusWidget()
            target = focus if isinstance(focus, Button) else self._default
            if target is not None:
                target.click()
            event.accept()
            return
        if event.key() == Qt.Key_Escape:
            self.choice["value"] = self.cancel_value
            self.reject()
            event.accept()
            return
        super(MessageBox, self).keyPressEvent(event)

    def _fit(self):
        width = self.text.width()
        if width <= 0 or width == self._fitted:
            return
        self._fitted = width
        need = self.text.heightForWidth(width)
        if need > self.text.height():
            self.text.setMinimumHeight(need)
            self.resize(self.width(), max(self.height(), self.sizeHint().height()))

    def showEvent(self, event):
        super(MessageBox, self).showEvent(event)
        QtCore.QTimer.singleShot(0, self._fit)


def build(parent, title, text, buttons, kind="info", heading=None, details=None):
    """(dialog, choice) for custom buttons: `buttons` = [(caption, value, role)]; choice['value'] is the picked value (None when closed)."""
    dialog = MessageBox(parent, title, text, buttons, kind, heading, details)
    return dialog, dialog.choice


def confirm(parent, title, text, ok=None, cancel=None, danger=False, heading=None):
    """A question: Cancel on the left, the action on the right (red when `danger`). True when the action was chosen."""
    dialog = MessageBox(parent, title, text, [
        (cancel or i18n.t("ui.cancel"), False, "secondary"),
        (ok or i18n.t("ui.ok"), True, "danger" if danger else "primary")], "warning" if danger else "info", heading)
    _run(dialog)
    return bool(dialog.choice["value"])


def inform(parent, title, text, kind="info", ok=None, heading=None, details=None):
    """A notice with one button. `kind`: info, success, warning, danger."""
    dialog = MessageBox(parent, title, text, [(ok or i18n.t("ui.ok"), True, "primary")], kind, heading, details)
    _run(dialog)


def error(parent, title, text, details=None):
    """A failure notice (red), with the technical details under the text when given."""
    inform(parent, title, text, "danger", details=details)
