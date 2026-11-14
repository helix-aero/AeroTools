"""Small helpers shared by the windows of the suite (no numpy here, so they load and test anywhere): typed numbers and the placing of a
window on the screen. The look of every control lives in the UI kit (`aerotools/uikit`)."""

from .qt import QtCore, QtWidgets


def parse_number(text):
    """Float of a typed value (comma or dot), or None."""
    try:
        value = float(str(text).replace(",", ".").strip())
    except ValueError:
        return None
    return value if value == value and abs(value) != float("inf") else None


UNIT_MARKS = ("°", "%", " ")


def parse_typed(text, low, high, modulo=None):
    """Whole number typed in a value field: unit marks (degree sign, %) are ignored; None when it is not a number or lies outside
    low..high. With `modulo` (a compass: 0 to 360), the result wraps (360 gives 0)."""
    cleaned = str(text)
    for mark in UNIT_MARKS:
        cleaned = cleaned.replace(mark, " ")
    cleaned = cleaned.replace("deg", " ").strip()
    value = parse_number(cleaned)
    if value is None or not low <= value <= high:
        return None
    whole = int(round(value))
    return whole % modulo if modulo else whole


def screen_area():
    """Room the screen leaves to a window (QRect), taskbar excluded: the screen that holds the Metashape main window, the primary one when
    that is not known; a generous default when Qt cannot say."""
    try:
        areas = available_areas()
        host = host_rect(None)
        if host is not None and len(areas) > 1:
            x, y, w, h = areas[area_for(host, areas)]
            return QtCore.QRect(x, y, w, h)
        return QtWidgets.QApplication.primaryScreen().availableGeometry()
    except Exception:
        return QtCore.QRect(0, 0, 1920, 1040)


def available_areas():
    """The usable area (taskbar excluded) of every screen, the primary one first, as (x, y, width, height) tuples."""
    out = []
    for screen in QtWidgets.QApplication.screens():
        g = screen.availableGeometry()
        out.append((g.x(), g.y(), g.width(), g.height()))
    return out or [(0, 0, 1920, 1040)]


def host_rect(window=None):
    """The frame (x, y, width, height) of the Metashape main window: the top window above `window` when it has a parent, else the main
    window found among the top windows. None when there is none."""
    top = None
    try:
        parent = window.parentWidget() if window is not None else None
        top = parent.window() if parent is not None else None
        if top is None:
            for widget in QtWidgets.QApplication.topLevelWidgets():
                if isinstance(widget, QtWidgets.QMainWindow) and widget.isVisible():
                    top = widget
                    break
        if top is None:
            return None
        g = top.normalGeometry() if top.isMinimized() else top.frameGeometry()
        return (g.x(), g.y(), g.width(), g.height())
    except Exception:
        return None


def area_for(rect, areas):
    """Index of the area (x, y, w, h) that holds the centre of `rect`, else the nearest one; 0 without area. Pure."""
    if not areas:
        return 0
    cx, cy = rect[0] + rect[2] / 2.0, rect[1] + rect[3] / 2.0
    best, best_gap = 0, None
    for index, (x, y, w, h) in enumerate(areas):
        gap = max(x - cx, 0, cx - (x + w)) ** 2 + max(y - cy, 0, cy - (y + h)) ** 2
        if best_gap is None or gap < best_gap:
            best, best_gap = index, gap
    return best


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
        self._refit_now()
        QtCore.QTimer.singleShot(0, self._refit_now)        # Qt5 lays wrapped text out one event later: measure again

    def _refit_now(self):
        layout = self.layout()
        if layout is not None:
            layout.invalidate()
            layout.activate()
        sync_minimum(self)
        self.resize(self.width(), max(self.height(), self.minimumHeight()))
