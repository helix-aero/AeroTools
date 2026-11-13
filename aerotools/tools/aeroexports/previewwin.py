"""Aero-Exports: the window where the user chooses the colours and the shading of the DEM picture, with a live preview.

Opened (modal) from the DEM card of the export window. The preview is a `preview.Preview` (a small DEM painted with numpy, 30 to
130 ms per repaint): a control only asks for a repaint, a 35 ms timer paints once with the latest settings, so a moving slider never
queues work. Nothing runs in a thread and nothing touches Metashape here. "Use this look" keeps the look in `result_look`; the
caller saves it (look.save), and the export paints the DXF's DEM image with the very same look.
"""

import numpy as np

from ... import i18n, uikit
from ...qt import QtCore, QtGui, QtWidgets
from ...uikit import tokens
from ...uikit.blocks import hbox, vbox
from ...uikit.controls import Button, ChoiceBox, IconButton, NumberField, label, parse_number
from ...uikit.metrics import font, px
from . import look as looks
from .widgets import SliderRow

DELAY_MS = 35
LEGEND_WIDTH = 96
THUMB = (96, 64)
SHADE_KEYS = {looks.NONE: "export.shade_none", looks.SUN: "export.shade_sun", looks.LIGHTS4: "export.shade_lights4",
              looks.SLOPE: "export.shade_slope"}
RANGE_KEYS = ((looks.AUTO, "export.range_auto"), (looks.FULL, "export.range_full"), (looks.CUSTOM, "export.range_custom"))
Qt = QtCore.Qt


def qimage(rgba):
    """QImage (RGBA, copied) from a rows x columns x 4 uint8 array."""
    data = np.ascontiguousarray(rgba, dtype=np.uint8)
    rows, cols = data.shape[:2]
    return QtGui.QImage(data.data, cols, rows, 4 * cols, QtGui.QImage.Format_RGBA8888).copy()


def thumbnail(rgba, size=None):
    """Small QPixmap of a rendered preview, for the card of the export window."""
    size = size or (px(THUMB[0]), px(THUMB[1]))
    return QtGui.QPixmap.fromImage(qimage(rgba).scaled(size[0], size[1], Qt.KeepAspectRatio, Qt.SmoothTransformation))


def shading_name(shading):
    return i18n.t(SHADE_KEYS.get(shading, SHADE_KEYS[looks.SUN]))


def summary(look):
    """One line for the card: "Metashape, Sun"."""
    look = looks.normalized(look)
    return i18n.t("export.look_summary", palette=looks.label(looks.palette(look.palette)),
                  shading=shading_name(look.shading))


def compass(azimuth):
    """Compass name of a direction in degrees (N, NE, E...)."""
    names = i18n.t("export.look_compass").split("|")
    return names[int(((azimuth % 360.0) + 22.5) // 45.0) % 8]


class ImageView(QtWidgets.QWidget):
    """The preview picture, scaled to the available room (aspect kept)."""

    def __init__(self):
        super(ImageView, self).__init__()
        self._image = None
        self.setMinimumSize(px(440), px(320))
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

    def set_image(self, image):
        self._image = image
        self.update()

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor(tokens.SURFACE))
        if self._image is not None and not self._image.isNull():
            painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
            scaled = self._image.size()
            scaled.scale(self.width(), self.height(), Qt.KeepAspectRatio)
            target = QtCore.QRect(0, 0, scaled.width(), scaled.height())
            target.moveCenter(self.rect().center())
            painter.drawImage(target, self._image)
        painter.end()


class LegendBar(QtWidgets.QWidget):
    """Colour bar with its height labels (lowest at the bottom)."""

    def __init__(self):
        super(LegendBar, self).__init__()
        self._image, self._ticks = None, []
        self.setFixedWidth(px(LEGEND_WIDTH))
        self.setMinimumHeight(px(240))

    def set_data(self, bar, ticks):
        """`bar`: palette as rows x 3 uint8, highest first. `ticks`: [(fraction 0..1 from the bottom, text)]."""
        data = np.ascontiguousarray(bar, dtype=np.uint8)
        self._image = QtGui.QImage(data.data, 1, data.shape[0], 3, QtGui.QImage.Format_RGB888).copy()
        self._ticks = list(ticks)
        self.update()

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        margin = px(10)
        bar_w, tick = px(22), px(6)
        top, bottom = margin, self.height() - margin
        if self._image is not None and bottom > top:
            painter.drawImage(QtCore.QRect(0, top, bar_w, bottom - top), self._image)
            painter.setFont(font("small"))
            painter.setPen(QtGui.QColor(tokens.TEXT))
            metrics = painter.fontMetrics()
            for fraction, text in self._ticks:
                y = bottom - fraction * (bottom - top)
                painter.drawLine(bar_w, int(y), bar_w + tick, int(y))
                painter.drawText(bar_w + tick + px(4), int(y + metrics.ascent() / 2.0 - 1), text)
        painter.end()


class PreviewWindow(uikit.ToolWindow):
    """Choose palette, light and colour range; `result_look` is the look to keep once "Use this look" was clicked (else None).

    `void`: "transparent" or "white" (what is painted where the DEM has no data). `custom_allowed`: False when the colour range
    cannot be typed in the project's heights (the export converts them)."""
    MARK = "tool_exports"

    DESIGN_SIZE = (1040, 640)

    def __init__(self, parent, preview, look, void="transparent", note="", custom_allowed=True):
        super(PreviewWindow, self).__init__(parent, title=i18n.t("export.look_title"), info=i18n.t("export.look_intro"), modal=True)
        self.preview, self.void, self.result_look = preview, void, None
        self._look = looks.normalized(look)
        self._custom_allowed = custom_allowed
        body = QtWidgets.QWidget()
        layout = hbox(spacing=tokens.L, parent=body)
        # left: the picture, as large as the window allows
        self.view = ImageView()
        layout.addWidget(self.view, 1)
        self.legend = LegendBar()
        layout.addWidget(self.legend)

        # right: the settings
        side = vbox(spacing=tokens.M)
        layout.addLayout(side)
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(px(tokens.S))
        grid.setVerticalSpacing(px(tokens.M))
        grid.setColumnStretch(1, 1)
        grid.setColumnMinimumWidth(0, px(112))
        self.palette_box, self.palette_reset = self._choice(grid, 0, "export.look_palette", self._reset_palette)
        for ident, entry in looks.palettes().items():
            self.palette_box.addItem(QtGui.QIcon(self._swatch(entry)), looks.label(entry), ident)
        self.shading_box, self.shading_reset = self._choice(grid, 1, "export.look_shading", self._reset_shading)
        for shading in looks.SHADINGS:
            self.shading_box.addItem(shading_name(shading), shading)
        default = looks.DEFAULT
        self._direction = SliderRow(grid, 2, i18n.t("export.look_direction"), 0, 359, "°", int(default.azimuth), modulo=360, typed_high=360)
        self._height = SliderRow(grid, 3, i18n.t("export.look_height"), int(looks.ALTITUDE_RANGE[0]), int(looks.ALTITUDE_RANGE[1]), "°",
                                 int(default.altitude))
        self._strength = SliderRow(grid, 4, i18n.t("export.look_strength"), 0, 100, "%", int(round(default.strength * 100)))
        self.direction, self.direction_text, self.direction_reset = self._direction.slider, self._direction.field, self._direction.reset
        self.light_height, self.height_text, self.height_reset = self._height.slider, self._height.field, self._height.reset
        self.strength, self.strength_text, self.strength_reset = self._strength.slider, self._strength.field, self._strength.reset
        for widget, tip_key, handler in ((self.direction_reset, "export.look_direction", lambda: self._reset_slider(self._direction, default.azimuth)),
                                         (self.height_reset, "export.look_height", lambda: self._reset_slider(self._height, default.altitude)),
                                         (self.strength_reset, "export.look_strength",
                                          lambda: self._reset_slider(self._strength, default.strength * 100))):
            widget.setToolTip(i18n.t("export.reset_one", name=i18n.t(tip_key)))
            widget.clicked.connect(lambda *_a, h=handler: h())
        grid.addWidget(label(i18n.t("export.look_range"), "body", "muted", wrap=False), 5, 0)
        self.range_box = ChoiceBox([(value, i18n.t(key)) for value, key in RANGE_KEYS])
        self.range_box.setToolTip(i18n.t("export.range_auto_tip"))
        if not custom_allowed:
            item = self.range_box.model().item(self.range_box.findData(looks.CUSTOM))
            item.setEnabled(False)
        grid.addWidget(self.range_box, 5, 1, 1, 2)
        self.range_reset = IconButton("refresh", i18n.t("export.reset_one", name=i18n.t("export.look_range")), self._reset_range)
        grid.addWidget(self.range_reset, 5, 3)
        self.low_edit, self.high_edit = NumberField(0, None, None, 3, 1, "", width=96), NumberField(0, None, None, 3, 1, "", width=96)
        for edit in (self.low_edit, self.high_edit):
            edit.textChanged.connect(self._on_range_text)
        numbers = hbox(spacing=tokens.S)
        numbers.addWidget(self.low_edit)
        numbers.addWidget(label(i18n.t("export.look_to"), "body", "muted", wrap=False))
        numbers.addWidget(self.high_edit)
        numbers.addStretch(1)
        grid.addLayout(numbers, 6, 1, 1, 3)
        self.range_error = label(i18n.t("export.look_range_bad"), "small", "danger")
        self.range_error.setVisible(False)
        grid.addWidget(self.range_error, 7, 1, 1, 3)
        side.addLayout(grid)
        if preview.geographic:
            note = (note + chr(10) if note else "") + i18n.t("export.look_no_shade")
        self.note = label(note, "small", "muted")
        self.note.setVisible(bool(note))
        side.addWidget(self.note)
        side.addStretch(1)
        side.setSizeConstraint(QtWidgets.QLayout.SetMinimumSize)
        self.set_content(body)

        self.set_primary(i18n.t("export.look_apply"), self._apply)
        self.apply_button = self.next_button
        self.reset_button = Button(i18n.t("export.look_reset"), "quiet", handler=self._reset)
        self.footer.add_left(self.reset_button)

        self.palette_box.activated.connect(self._on_change)
        self.shading_box.activated.connect(self._on_change)
        self.range_box.activated.connect(self._on_range_choice)
        for slider in (self.direction, self.light_height, self.strength):
            slider.valueChanged.connect(self._on_change)
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(DELAY_MS)
        self._timer.timeout.connect(self._paint)
        self.paints = 0                                   # repaints done (the tests read it)
        self._show_look(self._look)

    # -- building ----------------------------------------------------------------------------

    @staticmethod
    def _choice(grid, row, key, reset):
        """A drop-down with its label and its reset button: returns (box, reset button)."""
        grid.addWidget(label(i18n.t(key), "body", "muted", wrap=False), row, 0)
        box = ChoiceBox()
        grid.addWidget(box, row, 1, 1, 2)
        button = IconButton("refresh", i18n.t("export.reset_one", name=i18n.t(key)), reset)
        grid.addWidget(button, row, 3)
        return box, button

    @staticmethod
    def _swatch(entry):
        """Small gradient icon of a palette."""
        from . import relief
        table = relief.table_for(entry)
        index = np.rint(np.linspace(0, 255, 64)).astype(np.int64)
        strip = np.ascontiguousarray(np.repeat(table[index][None, :, :], 14, axis=0))
        return QtGui.QPixmap.fromImage(QtGui.QImage(strip.data, 64, 14, 192, QtGui.QImage.Format_RGB888).copy())

    # -- state <-> controls ------------------------------------------------------------------

    def _show_look(self, look):
        """Put `look` in the controls (without triggering repaints one by one), then paint it once."""
        widgets = (self.palette_box, self.shading_box, self.direction, self.light_height, self.strength, self.low_edit, self.high_edit,
                   self.range_box)
        for widget in widgets:
            widget.blockSignals(True)
        self.palette_box.setCurrentIndex(max(0, self.palette_box.findData(look.palette)))
        self.shading_box.setCurrentIndex(max(0, self.shading_box.findData(look.shading)))
        self._direction.set_value(int(round(look.azimuth)) % 360)
        self._height.set_value(int(round(look.altitude)))
        self._strength.set_value(int(round(look.strength * 100)))
        stretch = look.stretch if (look.stretch != looks.CUSTOM or self._custom_allowed) else looks.FULL
        self.range_box.set_value(stretch)
        low, high = (look.low, look.high) if stretch == looks.CUSTOM else self.preview.span()
        self.low_edit.setText(self._text(low))
        self.high_edit.setText(self._text(high))
        for widget in widgets:
            widget.blockSignals(False)
        self._look = looks.normalized(look._replace(stretch=stretch) if stretch != look.stretch else look)
        self._refresh_enabled()
        self._paint()

    @staticmethod
    def _text(value):
        return "" if value is None else "{:.3f}".format(value).rstrip("0").rstrip(".")

    def _range_choice(self):
        return self.range_box.value() or looks.FULL

    def _read(self):
        """The look the controls describe (an unusable custom range keeps the previous valid look and shows the message)."""
        stretch = self._range_choice()
        low, high = parse_number(self.low_edit.text()), parse_number(self.high_edit.text())
        bad = stretch == looks.CUSTOM and (low is None or high is None or high <= low)
        self.range_error.setVisible(bad)
        self.apply_button.setEnabled(not bad)
        if bad:
            return None
        return looks.normalized(looks.Look(self.palette_box.currentData(), self.shading_box.currentData(), float(self.direction.value()),
                                           float(self.light_height.value()), self.strength.value() / 100.0, stretch,
                                           low if stretch == looks.CUSTOM else None, high if stretch == looks.CUSTOM else None))

    def _refresh_enabled(self):
        shading = self.shading_box.currentData()
        lit = shading in (looks.SUN, looks.LIGHTS4) and not self.preview.geographic
        self._direction.set_enabled(lit)
        self._height.set_enabled(lit)
        self._strength.set_enabled(shading != looks.NONE and not self.preview.geographic)
        self.shading_box.setEnabled(not self.preview.geographic)
        custom = self._range_choice() == looks.CUSTOM
        self.low_edit.setEnabled(custom)
        self.high_edit.setEnabled(custom)
        default = looks.DEFAULT
        self.palette_reset.setEnabled(self.palette_box.currentData() != default.palette)
        self.shading_reset.setEnabled(self.shading_box.isEnabled() and self.shading_box.currentData() != default.shading)
        self.direction_reset.setEnabled(self.direction.isEnabled() and self.direction.value() != int(default.azimuth))
        self.height_reset.setEnabled(self.light_height.isEnabled() and self.light_height.value() != int(default.altitude))
        self.strength_reset.setEnabled(self.strength.isEnabled() and self.strength.value() != int(round(default.strength * 100)))
        self.range_reset.setEnabled(self._range_choice() != default.stretch)

    # -- events ------------------------------------------------------------------------------

    def _on_change(self, *_args):
        self._refresh_enabled()
        self._schedule()

    def _on_range_choice(self, *_args):
        if self._range_choice() == looks.CUSTOM and self._look.stretch != looks.CUSTOM:
            low, high = self.preview.colour_range(self._look)    # start from what the picture uses now: the user edits real numbers
            self.low_edit.setText(self._text(low))
            self.high_edit.setText(self._text(high))
        self._on_change()

    def _on_range_text(self, *_args):
        if self._range_choice() == looks.CUSTOM:
            self._on_change()

    def _schedule(self):
        """Ask for a repaint: one timer, so a burst of changes gives one repaint, with the latest settings."""
        if not self._timer.isActive():
            self._timer.start()

    def _paint(self):
        look = self._read()
        if look is None:
            return
        self._look = look
        self.view.set_image(qimage(self.preview.render(look, self.void)))
        data = self.preview.legend(look)
        self.legend.set_data(self.preview.colour_bar(look), data["ticks"])
        self.paints += 1

    def _reset(self):
        self._show_look(looks.DEFAULT)

    def _reset_palette(self):
        self.palette_box.setCurrentIndex(max(0, self.palette_box.findData(looks.DEFAULT.palette)))
        self._on_change()

    def _reset_shading(self):
        self.shading_box.setCurrentIndex(max(0, self.shading_box.findData(looks.DEFAULT.shading)))
        self._on_change()

    @staticmethod
    def _reset_slider(row, value):
        row.set_value(int(round(value)))

    def _reset_range(self):
        """The colour range goes back to "lowest to highest" (the typed numbers follow the DEM again)."""
        self.range_box.set_value(looks.DEFAULT.stretch)
        self._on_range_choice()

    def _apply(self):
        look = self._read()
        if look is None:
            return
        self.result_look = look
        self.accept()

    def current_look(self):
        return self._look
