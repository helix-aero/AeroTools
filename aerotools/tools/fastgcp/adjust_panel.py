"""Display adjustment panel (Qt): four sliders and a "Reset" button.

The same panel is used in the Review panel and in the refinement window; both
read the same `imageadjust.Adjustments` object, so an adjustment made in one is
found in the other. The adjustments only affect the display (see imageadjust.py).
"""

from ... import i18n, theme
from ...qt import QtCore, QtWidgets
from . import imageadjust

SAVE_DELAY_MS = 400
LABELS = {"exposure": "review.exposure", "contrast": "review.contrast",
          "saturation": "review.saturation", "sharpness": "review.sharpness"}


def _signed(value):
    return "{:+d}".format(value) if value else "0"


class _ResetSlider(QtWidgets.QSlider):
    """Slider whose double-click brings back the neutral value (0), as in Lightroom."""

    def mouseDoubleClickEvent(self, event):
        self.setValue(0 if self.minimum() <= 0 <= self.maximum() else self.minimum())
        event.accept()


class _ResetLabel(QtWidgets.QLabel):
    """Label or value: double-click resets the matching adjustment."""

    def __init__(self, text, on_reset):
        super(_ResetLabel, self).__init__(text)
        self._on_reset = on_reset

    def mouseDoubleClickEvent(self, event):
        self._on_reset()
        event.accept()


class AdjustPanel(QtWidgets.QFrame):
    """Frame at the right of the images: `top` (optional widget, e.g. the zoom) then the adjustments."""

    def __init__(self, model, top=None, hint_key=None, parent=None):
        super(AdjustPanel, self).__init__(parent)
        self.model = model
        self.setObjectName("aeroCard")
        self.setFixedWidth(230)
        self._sliders, self._values = {}, {}
        layout = QtWidgets.QVBoxLayout(self)
        pad = theme.PAD + theme.PAD_S
        layout.setContentsMargins(pad, pad, pad, pad)
        layout.setSpacing(theme.PAD)

        if top is not None:
            layout.addWidget(top)
        self._eyebrow = QtWidgets.QLabel(i18n.t("review.view").upper())
        self._eyebrow.setObjectName("aeroEyebrow")
        layout.addWidget(self._eyebrow)

        for key in imageadjust.KEYS:
            layout.addLayout(self._row(key))
        self.reset_button = QtWidgets.QPushButton(i18n.t("review.reset"))
        self.reset_button.setObjectName("aeroGhost")
        self.reset_button.setProperty("pill", True)
        self.reset_button.setCursor(QtCore.Qt.PointingHandCursor)
        self.reset_button.setFixedHeight(28)
        self.reset_button.clicked.connect(lambda *_args: self.model.reset())
        layout.addWidget(self.reset_button)

        note = i18n.t("review.view_note") if imageadjust.available() else i18n.t("review.no_numpy")
        self.note = QtWidgets.QLabel(note)
        self.note.setObjectName("aeroMeta")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        if hint_key:
            self.hint = QtWidgets.QLabel(i18n.t(hint_key))
            self.hint.setObjectName("aeroMeta")
            self.hint.setWordWrap(True)
            layout.addWidget(self.hint)
        layout.addStretch(1)

        self._save_timer = QtCore.QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self.model.save)
        self.model.subscribe(self._on_model)
        self._sync()
        self.destroyed.connect(lambda *_args, m=model, f=self._on_model: m.unsubscribe(f))

    def _row(self, key):
        box = QtWidgets.QVBoxLayout()
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(2)
        line = QtWidgets.QHBoxLayout()
        def reset(_key=key):
            self.model.set(_key, 0)

        name = _ResetLabel(i18n.t(LABELS[key]), reset)
        name.setObjectName("aeroMeta")
        value = _ResetLabel("0", reset)
        value.setObjectName("aeroMeta")
        value.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        line.addWidget(name, 1)
        line.addWidget(value, 0)
        box.addLayout(line)
        slider = _ResetSlider(QtCore.Qt.Horizontal)
        slider.setObjectName("aeroSlider")
        slider.setToolTip(i18n.t("review.slider_tip"))
        slider.setRange(*imageadjust.RANGES[key])
        slider.setTracking(True)                 # the effect follows the slider while it is dragged
        slider.setEnabled(imageadjust.available())
        slider.valueChanged.connect(lambda v, k=key: self.model.set(k, v))
        box.addWidget(slider)
        self._sliders[key], self._values[key] = slider, value
        return box

    def _sync(self):
        for key, slider in self._sliders.items():
            current = self.model.get(key)
            if slider.value() != current:
                slider.blockSignals(True)
                slider.setValue(current)
                slider.blockSignals(False)
            self._values[key].setText(_signed(current))
        self.reset_button.setEnabled(not self.model.is_neutral())

    def _on_model(self):
        self._sync()
        self._save_timer.start(SAVE_DELAY_MS)
