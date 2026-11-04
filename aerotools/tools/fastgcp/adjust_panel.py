"""Panneau de reglages d'affichage (Qt) : quatre curseurs et « Reinitialiser ».

Le meme panneau sert dans le Review panel et dans la fenetre d'affinage ; les deux
lisent le meme objet `imageadjust.Adjustments`, donc un reglage fait dans l'un se
retrouve dans l'autre. Les reglages ne touchent que l'affichage (voir imageadjust.py).
"""

from ... import i18n, theme
from ...qt import QtCore, QtWidgets
from . import imageadjust

SAVE_DELAY_MS = 400
LABELS = {"exposure": "review.exposure", "contrast": "review.contrast",
          "saturation": "review.saturation", "sharpness": "review.sharpness"}


def _signed(value):
    return "{:+d}".format(value) if value else "0"


class AdjustPanel(QtWidgets.QFrame):
    """Cadre a droite des images : `top` (widget facultatif, ex. le zoom) puis les reglages."""

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
        name = QtWidgets.QLabel(i18n.t(LABELS[key]))
        name.setObjectName("aeroMeta")
        value = QtWidgets.QLabel("0")
        value.setObjectName("aeroMeta")
        value.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        line.addWidget(name, 1)
        line.addWidget(value, 0)
        box.addLayout(line)
        slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        slider.setObjectName("aeroSlider")
        slider.setRange(*imageadjust.RANGES[key])
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
