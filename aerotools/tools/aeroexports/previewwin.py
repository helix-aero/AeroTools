"""Aero-Exports: the window where the user chooses the colours and the shading of the DEM picture, with a live preview.

Opened (modal) from the DEM card of the export window. The preview is a `preview.Preview` (a small DEM painted with numpy, 30 to
130 ms per repaint): a control only asks for a repaint, a 35 ms timer paints once with the latest settings, so a moving slider never
queues work. Nothing runs in a thread and nothing touches Metashape here. "Use this look" keeps the look in `result_look`; the
caller saves it (look.save), and the export paints the DXF's DEM image with the very same look.
"""

import numpy as np

from ... import i18n, theme, ui
from ...qt import QtCore, QtGui, QtWidgets
from . import controls, look as looks
from .controls import parse_number  # noqa: F401  (kept here: the tests and older code import it from this module)

DELAY_MS = 35
WIDTH, HEIGHT = 1120, 740
PANEL_WIDTH = 410
SCREEN_MARGIN = 70
LEGEND_WIDTH = 96
THUMB = (112, 76)
SHADE_KEYS = {looks.NONE: "export.shade_none", looks.SUN: "export.shade_sun", looks.LIGHTS4: "export.shade_lights4",
              looks.SLOPE: "export.shade_slope"}


def qimage(rgba):
    """QImage (RGBA, copied) from a rows x columns x 4 uint8 array."""
    data = np.ascontiguousarray(rgba, dtype=np.uint8)
    rows, cols = data.shape[:2]
    return QtGui.QImage(data.data, cols, rows, 4 * cols, QtGui.QImage.Format_RGBA8888).copy()


def thumbnail(rgba, size=THUMB):
    """Small QPixmap of a rendered preview, for the card of the export window."""
    return QtGui.QPixmap.fromImage(qimage(rgba).scaled(size[0], size[1], QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))


def shading_name(shading):
    return i18n.t(SHADE_KEYS.get(shading, SHADE_KEYS[looks.SUN]))


def summary(look):
    """One line for the card: "Metashape, Sun"."""
    look = looks.normalized(look)
    return i18n.t("export.look_summary", palette=looks.label(looks.palette(look.palette), i18n.lang()),
                  shading=shading_name(look.shading))


def compass(azimuth):
    """Compass name of a direction in degrees (N, NE, E...), in the language of the interface."""
    names = i18n.t("export.look_compass").split("|")
    return names[int(((azimuth % 360.0) + 22.5) // 45.0) % 8]


class ImageView(QtWidgets.QWidget):
    """The preview picture, scaled to the available room (aspect kept)."""

    def __init__(self):
        super(ImageView, self).__init__()
        self._image = None
        self.setMinimumSize(480, 360)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

    def set_image(self, image):
        self._image = image
        self.update()

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor(theme.PANE))
        if self._image is not None and not self._image.isNull():
            painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
            scaled = self._image.size()
            scaled.scale(self.width(), self.height(), QtCore.Qt.KeepAspectRatio)
            target = QtCore.QRect(0, 0, scaled.width(), scaled.height())
            target.moveCenter(self.rect().center())
            painter.drawImage(target, self._image)
        painter.end()


class LegendBar(QtWidgets.QWidget):
    """Colour bar with its height labels (lowest at the bottom)."""

    def __init__(self):
        super(LegendBar, self).__init__()
        self._image, self._ticks = None, []
        self.setFixedWidth(LEGEND_WIDTH)
        self.setMinimumHeight(240)

    def set_data(self, bar, ticks):
        """`bar`: palette as rows x 3 uint8, highest first. `ticks`: [(fraction 0..1 from the bottom, text)]."""
        data = np.ascontiguousarray(bar, dtype=np.uint8)
        self._image = QtGui.QImage(data.data, 1, data.shape[0], 3, QtGui.QImage.Format_RGB888).copy()
        self._ticks = list(ticks)
        self.update()

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        margin = 10
        top, bottom = margin, self.height() - margin
        if self._image is not None and bottom > top:
            painter.drawImage(QtCore.QRect(0, top, 22, bottom - top), self._image)
            painter.setPen(QtGui.QColor(theme.FG_1))
            metrics = painter.fontMetrics()
            for fraction, text in self._ticks:
                y = bottom - fraction * (bottom - top)
                painter.drawLine(22, int(y), 28, int(y))
                painter.drawText(32, int(y + metrics.ascent() / 2.0 - 1), text)
        painter.end()


class PreviewWindow(QtWidgets.QDialog):
    """Choose palette, light and colour range; `result_look` is the look to keep once "Use this look" was clicked (else None).

    `void`: "transparent" or "white" (what is painted where the DEM has no data). `custom_allowed`: False when the colour range
    cannot be typed in the project's heights (the export converts them)."""

    def __init__(self, parent, preview, look, void="transparent", note="", custom_allowed=True):
        super(PreviewWindow, self).__init__(parent)
        self.preview, self.void, self.result_look = preview, void, None
        self._look = looks.normalized(look)
        self._custom_allowed = custom_allowed
        self._busy = False
        self.setWindowTitle(i18n.t("export.look_title"))
        self.setModal(True)
        self.resize(*self._size())
        root = ui.dark_root(self)
        layout = QtWidgets.QHBoxLayout(root)
        pad = theme.PAD_L
        layout.setContentsMargins(pad, pad, pad, pad)
        layout.setSpacing(pad)
        # left: the picture, as large as the window allows
        self.view = ImageView()
        layout.addWidget(self.view, 1)
        self.legend = LegendBar()
        layout.addWidget(self.legend)

        # right: the settings, in a column of fixed width; the buttons stay visible under it
        side = QtWidgets.QVBoxLayout()
        side.setSpacing(theme.PAD)
        layout.addLayout(side)
        page = QtWidgets.QWidget()
        form = QtWidgets.QVBoxLayout(page)
        form.setContentsMargins(0, 0, theme.PAD, 0)
        form.setSpacing(theme.PAD)
        form.addWidget(ui.label(i18n.t("export.look_intro")))
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(theme.PAD)
        grid.setVerticalSpacing(theme.PAD)
        grid.setColumnStretch(1, 1)
        self.palette_box, self.palette_reset = self._combo(grid, 0, "export.look_palette", self._reset_palette)
        lang = i18n.lang()
        for ident, entry in looks.palettes().items():
            self.palette_box.addItem(QtGui.QIcon(self._swatch(entry)), looks.label(entry, lang), ident)
        self.shading_box, self.shading_reset = self._combo(grid, 1, "export.look_shading", self._reset_shading)
        for shading in looks.SHADINGS:
            self.shading_box.addItem(shading_name(shading), shading)
        default = looks.DEFAULT
        self.direction, self.direction_text, self.direction_reset = controls.slider_row(
            grid, 2, "export.look_direction", 0, 359, lambda v: "{}° ({})".format(v, compass(v)), int(default.azimuth),
            modulo=360, typed_high=360)
        self.height, self.height_text, self.height_reset = controls.slider_row(
            grid, 3, "export.look_height", int(looks.ALTITUDE_RANGE[0]), int(looks.ALTITUDE_RANGE[1]),
            lambda v: "{}°".format(v), int(default.altitude))
        self.strength, self.strength_text, self.strength_reset = controls.slider_row(
            grid, 4, "export.look_strength", 0, 100, lambda v: "{} %".format(v), int(round(default.strength * 100)))
        for widget, tip_key in ((self.palette_reset, "export.look_palette"), (self.shading_reset, "export.look_shading"),
                                (self.direction_reset, "export.look_direction"), (self.height_reset, "export.look_height"),
                                (self.strength_reset, "export.look_strength")):
            widget.setToolTip(i18n.t("export.reset_one", name=i18n.t(tip_key)))
        top = QtCore.Qt.AlignTop
        grid.addWidget(ui.label(i18n.t("export.look_range"), "aeroMeta", wrap=False), 5, 0, top)
        holder = QtWidgets.QVBoxLayout()
        holder.setSpacing(theme.PAD_S)
        self.range_group = QtWidgets.QButtonGroup(self)
        self.range_radios = {}
        for value, key in ((looks.AUTO, "export.range_auto"), (looks.FULL, "export.range_full"), (looks.CUSTOM, "export.range_custom")):
            radio = QtWidgets.QRadioButton(i18n.t(key))
            radio.setProperty("value", value)
            radio.setCursor(QtCore.Qt.PointingHandCursor)
            self.range_group.addButton(radio)
            holder.addWidget(radio)
            self.range_radios[value] = radio
            radio.toggled.connect(self._on_range_choice)
        self.range_radios[looks.AUTO].setToolTip(i18n.t("export.range_auto_tip"))
        self.range_radios[looks.CUSTOM].setEnabled(custom_allowed)
        grid.addLayout(holder, 5, 1, 1, 2)
        self.range_reset = controls.reset_button(self._reset_range, i18n.t("export.reset_one", name=i18n.t("export.look_range")))
        grid.addWidget(self.range_reset, 5, 3, top)
        self.low_edit, self.high_edit = ui.SelectAllEdit(), ui.SelectAllEdit()
        for edit in (self.low_edit, self.high_edit):
            edit.setFixedWidth(80)
            edit.setAlignment(QtCore.Qt.AlignRight)
            edit.setMaxLength(12)
            edit.textChanged.connect(self._on_range_text)
        numbers = QtWidgets.QHBoxLayout()
        numbers.setSpacing(theme.PAD)
        numbers.addWidget(self.low_edit)
        numbers.addWidget(ui.label(i18n.t("export.look_to"), "aeroMeta", wrap=False))
        numbers.addWidget(self.high_edit)
        numbers.addStretch(1)
        grid.addLayout(numbers, 6, 1, 1, 2)
        self.range_error = ui.label(i18n.t("export.look_range_bad"), "aeroMeta")
        self.range_error.setVisible(False)
        grid.addWidget(self.range_error, 7, 1, 1, 3)
        form.addLayout(grid)
        if preview.geographic:
            note = (note + chr(10) if note else "") + i18n.t("export.look_no_shade")
        self.note = ui.label(note, "aeroMeta")
        self.note.setVisible(bool(note))
        form.addWidget(self.note)
        form.addStretch(1)
        side.addWidget(controls.scroll_page(page, preferred=PANEL_WIDTH, cap=self._panel_cap()), 1)

        buttons = QtWidgets.QHBoxLayout()
        buttons.setSpacing(theme.PAD)
        self.apply_button = ui.big_button("export.look_apply", self._apply, "aeroGo", width=170)
        buttons.addWidget(self.apply_button, 1)
        buttons.addWidget(ui.big_button("export.cancel", self.reject, "aeroBtn", width=100))
        side.addWidget(ui.big_button("export.look_reset", self._reset, "aeroBtn"))
        side.addLayout(buttons)

        self.palette_box.activated.connect(self._on_change)
        self.shading_box.activated.connect(self._on_change)
        for slider in (self.direction, self.height, self.strength):
            slider.valueChanged.connect(self._on_change)
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(DELAY_MS)
        self._timer.timeout.connect(self._paint)
        self.paints = 0                                   # repaints done (the tests read it)
        self._show_look(self._look)

    @staticmethod
    def _panel_cap():
        """Widest minimum asked of the settings panel: what a small screen leaves beside the picture (its own minimum) and the legend."""
        return max(320, controls.screen_width() - SCREEN_MARGIN - 480 - LEGEND_WIDTH - 5 * theme.PAD_L)

    def showEvent(self, event):
        super(PreviewWindow, self).showEvent(event)
        if not getattr(self, "_fitted", False):
            self._fitted = True
            controls.fit_window(self)

    # -- building ----------------------------------------------------------------------------

    @staticmethod
    def _size():
        area = controls.screen_area()
        return min(WIDTH, area.width() - SCREEN_MARGIN), min(HEIGHT, area.height() - SCREEN_MARGIN)

    @staticmethod
    def _combo(grid, row, key, reset):
        """A drop-down with its label and its reset button: returns (box, reset button)."""
        grid.addWidget(ui.label(i18n.t(key), "aeroMeta", wrap=False), row, 0)
        box = ui.combo()
        box.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        box.setMinimumContentsLength(10)
        grid.addWidget(box, row, 1, 1, 2)
        button = controls.reset_button(reset)
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
        widgets = (self.palette_box, self.shading_box, self.direction, self.height, self.strength, self.low_edit, self.high_edit,
                   *self.range_radios.values())
        for widget in widgets:
            widget.blockSignals(True)
        self.palette_box.setCurrentIndex(max(0, self.palette_box.findData(look.palette)))
        self.shading_box.setCurrentIndex(max(0, self.shading_box.findData(look.shading)))
        self.direction.setValue(int(round(look.azimuth)) % 360)
        self.height.setValue(int(round(look.altitude)))
        self.strength.setValue(int(round(look.strength * 100)))
        for slider, text in ((self.direction, self.direction_text), (self.height, self.height_text), (self.strength, self.strength_text)):
            text.set_value(slider.value())
        stretch = look.stretch if (look.stretch != looks.CUSTOM or self._custom_allowed) else looks.FULL
        self.range_radios[stretch].setChecked(True)
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
        for value, radio in self.range_radios.items():
            if radio.isChecked():
                return value
        return looks.FULL

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
                                           float(self.height.value()), self.strength.value() / 100.0, stretch,
                                           low if stretch == looks.CUSTOM else None, high if stretch == looks.CUSTOM else None))

    def _refresh_enabled(self):
        shading = self.shading_box.currentData()
        lit = shading in (looks.SUN, looks.LIGHTS4) and not self.preview.geographic
        self.direction.setEnabled(lit)
        self.height.setEnabled(lit)
        self.strength.setEnabled(shading != looks.NONE and not self.preview.geographic)
        self.shading_box.setEnabled(not self.preview.geographic)
        custom = self._range_choice() == looks.CUSTOM
        self.low_edit.setEnabled(custom)
        self.high_edit.setEnabled(custom)
        self.direction_text.set_value(self.direction.value())
        self.height_text.set_value(self.height.value())
        self.strength_text.set_value(self.strength.value())
        for text, slider in ((self.direction_text, self.direction), (self.height_text, self.height), (self.strength_text, self.strength)):
            text.setEnabled(slider.isEnabled())
        default = looks.DEFAULT
        self.palette_reset.setEnabled(self.palette_box.currentData() != default.palette)
        self.shading_reset.setEnabled(self.shading_box.isEnabled() and self.shading_box.currentData() != default.shading)
        self.direction_reset.setEnabled(self.direction.isEnabled() and self.direction.value() != int(default.azimuth))
        self.height_reset.setEnabled(self.height.isEnabled() and self.height.value() != int(default.altitude))
        self.strength_reset.setEnabled(self.strength.isEnabled() and self.strength.value() != int(round(default.strength * 100)))
        self.range_reset.setEnabled(self._range_choice() != default.stretch)

    # -- events ------------------------------------------------------------------------------

    def _on_change(self, *_args):
        self._refresh_enabled()
        self._schedule()

    def _on_range_choice(self, checked=True):
        if not checked:
            return
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

    def _reset_range(self):
        """The colour range goes back to "lowest to highest" (the typed numbers follow the DEM again)."""
        self.range_radios[looks.DEFAULT.stretch].setChecked(True)

    def _apply(self):
        look = self._read()
        if look is None:
            return
        self.result_look = look
        self.accept()

    def current_look(self):
        return self._look
