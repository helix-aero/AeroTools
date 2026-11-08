"""Small controls shared by the windows of the suite (no numpy here, so they load and test anywhere).

- `EditableValue`: the number written next to a slider ("315 degrees (NW)", "80 %"). Double-click it (or Tab to it and press Enter or F2),
  type an exact number, Enter applies it, Esc puts the old value back. A number outside the allowed range is refused on the spot, with the
  range written in the tooltip; the slider is only moved by a valid number.
- `reset_button`: the small round arrow next to a control that puts its default value back.
- `slider_row`: a label, a short slider, its editable value and its reset button, placed in one grid row.
"""

from . import i18n, theme, ui
from .qt import QtCore, QtGui, QtWidgets


def screen_area():
    """Room the screen leaves to a window (QRect); a generous default when Qt cannot say."""
    try:
        return QtWidgets.QApplication.primaryScreen().availableGeometry()
    except Exception:
        return QtCore.QRect(0, 0, 1920, 1040)


def fit_window(dialog, margin=70, again=True):
    """Make a window follow what its panels need: its minimum size becomes the layout's (it follows later changes, such as a font
    change), and it opens at least that big and no bigger than the screen. Call it from showEvent; it measures again a moment later,
    because some widgets only reach their real size once the first layout is done."""
    layout = dialog.layout()
    layout.setSizeConstraint(QtWidgets.QLayout.SetMinimumSize)
    dialog.ensurePolished()
    for child in dialog.findChildren(QtWidgets.QWidget):
        child.ensurePolished()
        if child.layout() is not None:
            child.layout().invalidate()
    layout.invalidate()
    layout.activate()
    need = layout.minimumSize()
    area = screen_area()
    room = (area.width() - margin, area.height() - margin)
    size = dialog.size()                                    # (not .height(): the look window has a slider of that name)
    wanted = (min(max(size.width(), need.width()), room[0]), min(max(size.height(), need.height()), room[1]))
    if (size.width(), size.height()) != wanted:
        dialog.resize(*wanted)
    if again:
        QtCore.QTimer.singleShot(0, lambda: fit_window(dialog, margin, again=False))


def sync_minimum(dialog):
    """Makes the least size of the window what its pages really need at its present width. Wrapped text makes the least height depend on the
    width; when Qt's least size said less than that, Windows stopped the drag at a size Qt did not know and moved the window instead of
    stopping. With the true least size the drag simply stops at the limit."""
    layout = dialog.layout()
    need_h = layout.minimumSize().height()
    if layout.hasHeightForWidth():
        need_h = max(need_h, layout.minimumHeightForWidth(dialog.width()))
    area = screen_area()
    size = (min(layout.minimumSize().width(), area.width()), min(need_h, area.height()))
    if (dialog.minimumWidth(), dialog.minimumHeight()) != size:
        dialog.setMinimumSize(*size)


def follow_content(dialog):
    """From now on the least size of the window follows its content: call `sync_minimum` again from the window's `resizeEvent` and when the
    page changes. The size constraint of `fit_window` is dropped, it would set the least size again behind our back."""
    dialog.layout().setSizeConstraint(QtWidgets.QLayout.SetNoConstraint)
    sync_minimum(dialog)


class FollowsText(object):
    """Mixin for a dialog (put it before `QDialog`) whose layout has wrapped text: its least height always matches what the text needs,
    so a drag on its edge stops at the limit instead of moving the window."""

    def showEvent(self, event):
        super(FollowsText, self).showEvent(event)
        if not getattr(self, "_follows", False):
            self._follows = True
            follow_content(self)

    def resizeEvent(self, event):
        super(FollowsText, self).resizeEvent(event)
        if getattr(self, "_follows", False):
            sync_minimum(self)

    def refit(self):
        """Call after a text changed: the least height follows it and the window grows if it is now too short."""
        sync_minimum(self)
        self.resize(self.width(), max(self.height(), self.minimumHeight()))
