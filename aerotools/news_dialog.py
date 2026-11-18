"""News window (Qt): the unseen items of news.json, newest first, in a small window that does not block Metashape.

Each item is a bold title, its date, a short text and, when it has one, a "Learn more" link button. At most MAX_SHOWN items (no scroll bar);
when there are more, "More news" opens the documentation. Bottom: the "Don't show news again" switch and Close. Closing (any way) marks the
items that were shown as seen and applies the switch to the Notifications setting (the same one as in the Install plugins window).
"""

from . import i18n, news, uikit
from .qt import QtCore, QtGui, QtWidgets

WIDTH = 460


def open_link(address):
    """A link of the news (https only, checked when the file is read) opens in the browser."""
    try:
        return QtGui.QDesktopServices.openUrl(QtCore.QUrl(address))
    except Exception:
        return False


class NewsDialog(uikit.ToolWindow):
    """`items`: the items to list, or None when the file could not be fetched (only possible on demand: at startup nothing is shown)."""

    def __init__(self, items, on_demand=False, parent=None):
        super(NewsDialog, self).__init__(parent, title=i18n.t("news.title"), modal=False)
        self.on_demand = bool(on_demand)
        self.items = list(items or [])[:news.MAX_SHOWN]
        self.more = len(items or []) > news.MAX_SHOWN
        self._applied = False
        self._off_before = not news.enabled()
        body = QtWidgets.QWidget()
        body.setMinimumWidth(uikit.px(WIDTH))
        lay = uikit.vbox(spacing=uikit.tokens.M, parent=body)
        if items is None:
            lay.addWidget(uikit.label(i18n.t("news.offline"), "body", "muted"))
        elif not self.items:
            lay.addWidget(uikit.label(i18n.t("news.empty"), "body", "muted"))
        for index, item in enumerate(self.items):
            if index:
                lay.addWidget(uikit.Divider())
            lay.addLayout(self._item(item))
        if self.more:
            link = uikit.Button(i18n.t("news.more"), "quiet", handler=lambda: open_link(news.MORE_URL))
            lay.addWidget(link, 0, QtCore.Qt.AlignLeft)
        self.set_content(body)
        self.cancel_button.hide()                           # one way out: Close (Esc and the title bar close it too)
        self.set_primary(i18n.t("news.close"), self.accept)
        self.toggle = uikit.Toggle(i18n.t("news.dont_show"), checked=self._off_before)
        self.footer.add_left(self.toggle)
        self.footer.add_left(uikit.InfoButton(i18n.t("news.dont_show_tip")))

    def _item(self, item):
        box = uikit.vbox(spacing=uikit.tokens.XS)
        head = uikit.hbox(spacing=uikit.tokens.S)
        head.addWidget(uikit.label(item["title"], "strong"), 1)
        if item["date"]:
            head.addWidget(uikit.label(item["date"], "small", "muted", wrap=False), 0, QtCore.Qt.AlignTop)
        box.addLayout(head)
        if item["body"]:
            box.addWidget(uikit.label(item["body"], "body"))
        if item["url"]:
            link = uikit.Button(i18n.t("news.learn_more"), "secondary", handler=lambda address=item["url"]: open_link(address))
            box.addWidget(link, 0, QtCore.Qt.AlignLeft)
        return box

    def settle(self):
        """What closing does, once: the items shown are seen; the switch sets the Notifications setting."""
        if self._applied:
            return
        self._applied = True
        if self.items:
            news.mark_seen([item["id"] for item in self.items])
        if self.toggle.isChecked() != self._off_before:
            news.set_enabled(not self.toggle.isChecked())

    def done(self, result):
        self.settle()
        super(NewsDialog, self).done(result)
