"""Aero-Facade UI: what every screen of the wizard shares."""

from ... import theme, ui
from ...qt import QtWidgets
from ... import controls
from .common import tr
from . import state as S


class Screen(QtWidgets.QWidget):
    """One screen of the wizard. The wizard window calls `enter()` when the screen appears and `leave(forward)` when it goes away
    (return False to refuse), asks `next_enabled()` / `next_text()` / `show_next()` for the Next button, `busy()` to know whether a
    long step is running, and `next_step()` / `prev_step()` for the screen to jump to (a screen may be skipped)."""

    step = 0
    name_key = ""

    def __init__(self, wizard):
        super(Screen, self).__init__()
        self.wizard = wizard
        self.state = wizard.state

    def enter(self):
        pass

    def leave(self, forward):
        return True

    def next_enabled(self):
        return self.state.can_next(self.step)

    def next_text(self):
        return tr("facade.ui.next")

    def show_next(self):
        return True

    def busy(self):
        return False

    def next_step(self):
        return self.step + 1

    def prev_step(self):
        return self.step - 1

    def cancel_job(self):
        """The footer Cancel button while this screen is busy."""
        self.wizard.cancel_task()


def heading(number, key):
    """"1  Draw the search area": the title of a right-hand panel."""
    label = ui.label("%d   %s" % (number, tr(key)), "facadeBig", wrap=True)
    return label


def right_panel(width_hint=360):
    """(scroll area, layout) of a right-hand panel: its width follows what it holds, it scrolls only on a small window."""
    page = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(page)
    layout.setContentsMargins(0, 0, theme.PAD, 0)
    layout.setSpacing(theme.PAD + 4)
    area = controls.scroll_page(page, preferred=width_hint, cap=520)
    return area, layout


def canvas_frame():
    """The rounded frame the shared picture is placed in; returns (frame, layout)."""
    frame = QtWidgets.QFrame()
    frame.setObjectName("facadeCanvasFrame")
    layout = QtWidgets.QVBoxLayout(frame)
    layout.setContentsMargins(2, 2, 2, 2)
    layout.setSpacing(0)
    return frame, layout


def two_columns(left, right, left_stretch=3, right_stretch=1):
    row = QtWidgets.QHBoxLayout()
    row.setSpacing(theme.PAD_L)
    row.addWidget(left, left_stretch)
    row.addWidget(right, right_stretch)
    return row


__all__ = ["Screen", "heading", "right_panel", "canvas_frame", "two_columns", "S"]
