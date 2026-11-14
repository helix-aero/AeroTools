"""Small pieces the Aero-GCP windows share, built from kit tokens and components only.

Kit requests: the "arrow between steps" mark does not exist in the kit (the window-reuse helper is `uikit.present`).
"""


from ... import i18n, uikit
from ...qt import QtCore, QtGui, QtWidgets
from ...uikit import tokens


class Stat(uikit.Stat):
    """A figure over a short caption, in a card (the kit's Stat): the summary tiles of the Residual check and the training flow.
    `set_number(text, tone=None)` keeps the names the windows already use."""

    def __init__(self, caption="", number="0", parent=None):
        super(Stat, self).__init__(number, caption, None, parent)

    @property
    def number(self):
        return self.value_label

    @property
    def caption(self):
        return self.caption_label

    def set_number(self, text, tone=None):
        self.set_value(text, tone)


def arrow(parent=None):
    """The small arrow between the steps of a flow."""
    label = QtWidgets.QLabel(parent)
    label.setPixmap(uikit.icons.pixmap("arrow_right", tokens.TEXT_MUTED, uikit.px(tokens.ICON)))
    label.setAlignment(QtCore.Qt.AlignCenter)
    return label


def present(existing, make, refresh=None):
    """Reuse `existing` (a window still alive) or build it with `make()`; refresh it if asked, show it and bring it to the front.

    One place for what each secondary window used to copy: the panel keeps the returned window and passes it back next time."""
    window = existing
    try:
        if window is not None:
            window.objectName()
    except RuntimeError:
        window = None
    if window is None:
        window = make()
    elif refresh is not None:
        refresh(window)
    window.show()
    window.raise_()
    window.activateWindow()
    return window


def icon(name, color=tokens.TEXT):
    return QtGui.QIcon(uikit.icons.pixmap(name, color, uikit.px(tokens.ICON)))


class BusyDialog(uikit.ToolWindow):
    """Small modal "applying" window with a progress bar.

    Blocks the window behind it while Metashape is changed (no double click on Validate). `step(done, total)` advances the bar and lets
    Qt repaint; `set_text` / `indeterminate` change what it says. It has no footer: only the caller closes it."""

    def __init__(self, text, parent=None, title=None):
        super(BusyDialog, self).__init__(parent, title=title or i18n.t("review.busy_title"), modal=True, design_size=(420, 0))
        self.setWindowFlags(QtCore.Qt.Dialog | QtCore.Qt.CustomizeWindowHint | QtCore.Qt.WindowTitleHint)
        body = QtWidgets.QWidget()
        lay = uikit.vbox(spacing=tokens.M, parent=body)
        self.label = uikit.label(text, "body")
        lay.addWidget(self.label)
        self.row = uikit.ProgressRow()
        self.row.set_indeterminate("")
        lay.addWidget(self.row)
        lay.addWidget(uikit.label(i18n.t("review.busy_note"), "small", "muted"))
        self.set_content(body)
        self.footer.hide()
        self.footer_divider.hide()

    def set_text(self, text):
        self.label.setText(text)

    def indeterminate(self):
        self.row.set_indeterminate("")
        QtWidgets.QApplication.processEvents()

    def step(self, done, total):
        if total > 0:
            self.row.set_progress(min(done, total) / float(total))
        QtWidgets.QApplication.processEvents()

    def open(self, *_args):
        """Shows the window without blocking: the caller advances the bar with step()."""
        self.show()
        self.raise_()
        QtWidgets.QApplication.processEvents()

    def keyPressEvent(self, event):
        event.ignore()                                    # Escape and Enter do nothing: only the caller closes it
