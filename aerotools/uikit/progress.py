"""Progress: a thin bar, one row (text, percent, cancel) and a list of steps with state glyphs. No timer polls anything: the bar
animates only while it is visible and indeterminate."""

from .. import i18n
from ..qt import QtCore, QtGui, QtWidgets
from . import metrics, tokens
from .blocks import Glyph, hbox, vbox
from .controls import Button, ElidedLabel, IconButton, _qcolor, set_tone
from .metrics import px

Qt = QtCore.Qt


class ProgressBar(QtWidgets.QWidget):
    """A thin rounded bar. `set_fraction(0..1)`, or `set_indeterminate(True)` for a sliding segment. `state`: running, done, error."""

    def __init__(self, parent=None):
        super(ProgressBar, self).__init__(parent)
        self._fraction = 0.0
        self._indeterminate = False
        self._state = "running"
        self._phase = 0.0
        self._anim = QtCore.QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(1300)
        self._anim.setLoopCount(-1)
        self._anim.valueChanged.connect(self._slide)
        self.setFixedHeight(px(tokens.BAR_H))
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

    def fraction(self):
        return self._fraction

    def set_fraction(self, value):
        self._fraction = min(1.0, max(0.0, float(value)))
        self._indeterminate = False
        self._sync()
        self.update()

    def set_indeterminate(self, on=True):
        self._indeterminate = bool(on)
        self._sync()
        self.update()

    def is_indeterminate(self):
        return self._indeterminate

    def set_state(self, state):
        self._state = state
        self.update()

    def _sync(self):
        if self._indeterminate and self.isVisible():
            if self._anim.state() != QtCore.QAbstractAnimation.Running:
                self._anim.start()
        else:
            self._anim.stop()

    def showEvent(self, event):
        super(ProgressBar, self).showEvent(event)
        self._sync()

    def hideEvent(self, event):
        super(ProgressBar, self).hideEvent(event)
        self._sync()

    def _slide(self, value):
        self._phase = float(value)
        self.update()

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        rect = QtCore.QRectF(self.rect())
        radius = rect.height() / 2.0
        p.setPen(Qt.NoPen)
        p.setBrush(_qcolor(tokens.SURFACE_PRESSED))
        p.drawRoundedRect(rect, radius, radius)
        color = {"error": tokens.DANGER, "done": tokens.SUCCESS, "cancelled": tokens.WARNING}.get(self._state, tokens.ACCENT)
        p.setBrush(_qcolor(color))
        if self._indeterminate:
            seg = rect.width() * 0.32
            x = -seg + (rect.width() + seg) * self._phase
            p.setClipRect(rect)
            p.drawRoundedRect(QtCore.QRectF(x, 0, seg, rect.height()), radius, radius)
        elif self._fraction > 0:
            p.drawRoundedRect(QtCore.QRectF(0, 0, max(rect.height(), rect.width() * self._fraction), rect.height()), radius, radius)
        p.end()


class ProgressRow(QtWidgets.QWidget):
    """What a long task shows: its current text on the left, the percentage on the right, an optional Cancel, the bar underneath.

    `set_progress(fraction, text)`, `set_indeterminate(text)`, `set_state('running'|'done'|'error'|'cancelled', text)`, `reset()`.
    `cancelRequested` is sent by the Cancel button (shown only with `cancellable=True`); with `icon_cancel=True` it is a small cross
    (for narrow docks) with "Cancel" as its tooltip instead of a text button."""
    cancelRequested = QtCore.Signal()

    def __init__(self, cancellable=False, parent=None, icon_cancel=False):
        super(ProgressRow, self).__init__(parent)
        outer = vbox(spacing=tokens.XS + 2, parent=self)
        top = hbox(spacing=tokens.S)
        self.text = ElidedLabel("", "small")
        self.percent = QtWidgets.QLabel("")
        self.percent.setProperty("type", "small")
        self.percent.setProperty("tone", "muted")
        if icon_cancel:
            self.cancel_button = IconButton("cross", i18n.t("ui.cancel"), handler=self.cancelRequested.emit)
            self.cancel_button.setFixedSize(px(tokens.CONTROL_H - 8), px(tokens.CONTROL_H - 8))
        else:
            self.cancel_button = Button(i18n.t("ui.cancel"), "quiet", handler=self.cancelRequested.emit)
            self.cancel_button.setFixedHeight(px(tokens.CONTROL_H - 8))
        self.cancel_button.setVisible(cancellable)
        top.addWidget(self.text, 1)
        top.addWidget(self.percent)
        top.addWidget(self.cancel_button)
        outer.addLayout(top)
        self.bar = ProgressBar()
        outer.addWidget(self.bar)
        self._state = "running"

    def set_cancellable(self, on):
        self.cancel_button.setVisible(bool(on))

    def set_progress(self, fraction, text=None):
        self.bar.set_fraction(fraction)
        self.percent.setText("{}%".format(int(round(min(1.0, max(0.0, fraction)) * 100))))
        if text is not None:
            self.text.setFullText(text)

    def set_indeterminate(self, text=None):
        self.bar.set_indeterminate(True)
        self.percent.setText("")
        if text is not None:
            self.text.setFullText(text)

    def set_state(self, state, text=None):
        self._state = state
        self.bar.set_state(state)
        if state in ("done", "error", "cancelled"):
            self.bar.set_indeterminate(False)
            if state == "done":
                self.bar.set_fraction(1.0)
                self.percent.setText("100%")
        set_tone(self.text, {"error": "danger", "done": "success", "cancelled": "warning"}.get(state))
        if text is not None:
            self.text.setFullText(text)

    def reset(self):
        self._state = "running"
        self.bar.set_state("running")
        self.bar.set_fraction(0.0)
        self.percent.setText("")
        self.text.setFullText("")
        set_tone(self.text, None)

    def state(self):
        return self._state


class StepProgress(QtWidgets.QWidget):
    """A list of steps of one job, each with a state glyph (pending, running, done, error, skipped, cancelled) and an optional detail.

    `set_steps([(key, text)])`, `set_state(key, state, detail='')`, `state(key)`, `reset()`."""

    def __init__(self, steps=(), parent=None):
        super(StepProgress, self).__init__(parent)
        self._lay = vbox(spacing=tokens.S, parent=self)
        self._rows = {}
        self.set_steps(steps)

    def set_steps(self, steps):
        for key in list(self._rows):
            row = self._rows.pop(key)
            self._lay.removeWidget(row["widget"])
            row["widget"].deleteLater()
        for key, text in steps:
            holder = QtWidgets.QWidget()
            line = hbox(spacing=tokens.S + 2, parent=holder)
            glyph = Glyph("pending")
            name = ElidedLabel(text, "body")
            detail = ElidedLabel("", "small", "muted")
            detail.setMaximumWidth(px(220))
            line.addWidget(glyph)
            line.addWidget(name, 1)
            line.addWidget(detail)
            self._lay.addWidget(holder)
            self._rows[key] = {"widget": holder, "glyph": glyph, "name": name, "detail": detail}

    def set_state(self, key, state, detail=""):
        row = self._rows.get(key)
        if row is None:
            return
        row["glyph"].set_kind(state)
        row["detail"].setFullText(detail)
        set_tone(row["name"], {"pending": "faint", "skipped": "faint"}.get(state))
        set_tone(row["detail"], {"error": "danger", "cancelled": "warning"}.get(state, "muted"))

    def state(self, key):
        row = self._rows.get(key)
        return row["glyph"].kind() if row else None

    def keys(self):
        return list(self._rows)

    def reset(self):
        for key in self._rows:
            self.set_state(key, "pending")
