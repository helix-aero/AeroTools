"""Review panel: window for reviewing the AI's picks (Qt).

On the left the markers that have proposals, in the middle one thumbnail per photo:
a crop around the proposed point, with a red cross on that point. On the right, a
panel: zoom in the thumbnails and display settings (exposure, contrast,
saturation, sharpness), which only serve the eye (see imageadjust.py).

One click selects a thumbnail, another click deselects it: so you click all the
bad ones, then "Exclude" (or right-click). "Refine" opens the selected thumbnails
in ONE large window: you click the true centre of each (wheel = zoom, middle
button = pan), "Next" moves to the next one, "Validate" places the points; each
point becomes a manual pin and the correction is kept for future
retraining (see review.py). A thumbnail that was already refined can be refined again.

Wheel over the thumbnails: Shift = zoom in the thumbnails, Ctrl = thumbnail size.

Each decision is applied to the project right away. No direct call to Metashape:
everything goes through review.py and msapi.
"""

from ... import config, i18n, journal, msapi, theme
from ...qt import QShortcut, QtCore, QtGui, QtWidgets, modifiers
from . import adjust_panel, dialogs, imageadjust, photos, review

SECTION = "aigcp"
THUMB_KEY = "review_thumb"
ZOOM_KEY = "review_zoom"
DEFAULT_THUMB = 170
MIN_THUMB, MAX_THUMB, THUMB_STEP = 96, 360, 20
DEFAULT_ZOOM, MIN_ZOOM, MAX_ZOOM, ZOOM_STEP = 100, 50, 400, 25
SCROLL_ROOM = 16                      # room for the right panel's scroll bar
PREVIEW = 190                         # selected photo, at the top of the right panel (230 px minus margins, a bit less to fit in height)
REFRESH_MS = 70                       # wait after an adjustment before repainting the images
VIEW_MIN = 560                        # minimum side of the large refinement view
VIEW_ZOOM_STEP = 0.8                  # one wheel notch in the refinement view
ROLE_ENTRY = QtCore.Qt.UserRole


def _styled_root(dialog):
    """Inner widget that carries the panel's background and styles.

    As in the FastGCP panel: under Metashape, a QDialog does not paint the wanted
    background, an inner QWidget does.
    """
    root = QtWidgets.QWidget()
    root.setObjectName("aeroPanel")
    root.setAttribute(QtCore.Qt.WA_StyledBackground, True)
    root.setStyleSheet(theme.stylesheet())
    outer = QtWidgets.QVBoxLayout(dialog)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.addWidget(root)
    return root


def _exec(dialog, *args):
    run = getattr(dialog, "exec_", None) or getattr(dialog, "exec")
    return run(*args)


def _saved(key, default, low, high):
    value = config.get(SECTION, key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return int(min(high, max(low, value)))


def thumb_size():
    return _saved(THUMB_KEY, DEFAULT_THUMB, MIN_THUMB, MAX_THUMB)


def zoom_percent():
    return _saved(ZOOM_KEY, DEFAULT_ZOOM, MIN_ZOOM, MAX_ZOOM)


def _button(text_key, handler, object_name="aeroGhost", width=96):
    button = QtWidgets.QPushButton(i18n.t(text_key))
    button.setObjectName(object_name)
    button.setProperty("pill", True)
    button.setCursor(QtCore.Qt.PointingHandCursor)
    button.setFixedHeight(32)
    button.setMinimumWidth(width)
    button.setAutoDefault(False)
    button.clicked.connect(lambda *_args: handler())
    return button


def _restyle(widget, object_name):
    """Changes a widget's objectName and re-applies its style (QSS)."""
    if widget.objectName() == object_name:
        return
    widget.setObjectName(object_name)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)


def _timer(parent, handler):
    timer = QtCore.QTimer(parent)
    timer.setSingleShot(True)
    timer.timeout.connect(handler)
    return timer


def _event_pos(event):
    getter = getattr(event, "position", None)
    point = getter() if getter is not None else event.pos()
    return float(point.x()), float(point.y())


def _cross(painter, x, y, arm, gap, color, width):
    """Four-arm cross around (x, y), with a gap at the centre to see the target."""
    pen = QtGui.QPen(QtGui.QColor(*color))
    pen.setWidth(width)
    painter.setPen(pen)
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        painter.drawLine(int(x + dx * gap), int(y + dy * gap), int(x + dx * arm), int(y + dy * arm))


_RED = (255, 74, 46)
_GREEN = (60, 220, 120)
_DARK_RED = (225, 55, 55)
_WHITE = (255, 255, 255)
EXCLUDABLE = (review.KEPT, review.ACCEPTED, review.REFINED)    # a point can also be excluded after validation
STATUS_TEXTS = {review.KEPT: "review.status_kept", review.REMOVED: "review.status_removed",
                review.REFINED: "review.status_refined", review.ACCEPTED: "review.status_accepted"}


def _load(cache, path):
    pixmap = cache.get(path)
    if pixmap is None:
        pixmap = QtGui.QPixmap(path)
        cache[path] = pixmap
    return pixmap


def _paint_crop(painter, entry, size, zoom, cache):
    """Draws the crop at the requested zoom in the `size` square. Returns False without a thumbnail."""
    if not review.has_crop(entry):
        return False
    source = _load(cache, entry["big"])
    if source.isNull():
        return False
    left, top, side = review.source_rect(entry, zoom, source.width())
    painter.drawPixmap(QtCore.QRectF(0, 0, size, size), source, QtCore.QRectF(left, top, side, side))
    return True


def _badge(painter, size, state):
    """Status badge at the top right: tick (refined, validated) or cross (excluded)."""
    if state not in (review.REMOVED, review.REFINED, review.ACCEPTED):
        return
    d = max(16, size // 8)
    x, y = size - d - 4, 4
    painter.save()
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    if state == review.REMOVED:
        fill, mark = QtGui.QColor(*_DARK_RED), QtGui.QColor(*_WHITE)
    elif state == review.REFINED:
        fill, mark = QtGui.QColor(*_GREEN), QtGui.QColor(*_WHITE)
    else:
        fill, mark = QtGui.QColor(30, 30, 30, 200), QtGui.QColor(*_GREEN)
    painter.setPen(QtGui.QPen(QtGui.QColor(*_GREEN), 1.5) if state == review.ACCEPTED else QtCore.Qt.NoPen)
    painter.setBrush(fill)
    painter.drawEllipse(QtCore.QRectF(x, y, d, d))
    pen = QtGui.QPen(mark, max(1.6, d / 9.0))
    pen.setCapStyle(QtCore.Qt.RoundCap)
    pen.setJoinStyle(QtCore.Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(QtCore.Qt.NoBrush)
    if state == review.REMOVED:
        a = d * 0.3
        painter.drawLine(QtCore.QPointF(x + a, y + a), QtCore.QPointF(x + d - a, y + d - a))
        painter.drawLine(QtCore.QPointF(x + d - a, y + a), QtCore.QPointF(x + a, y + d - a))
    else:
        painter.drawPolyline([QtCore.QPointF(x + d * 0.27, y + d * 0.52),
                              QtCore.QPointF(x + d * 0.44, y + d * 0.68),
                              QtCore.QPointF(x + d * 0.74, y + d * 0.34)])
    painter.restore()


def render_thumb(entry, size, zoom, cache, adjust=None):
    """Square thumbnail of `size` px: adjusted crop, cross on the AI point, status."""
    base = QtGui.QImage(size, size, QtGui.QImage.Format_RGB32)
    base.fill(QtGui.QColor(45, 45, 45))
    painter = QtGui.QPainter(base)
    try:
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        has_image = _paint_crop(painter, entry, size, zoom, cache)
    finally:
        painter.end()
    if has_image and adjust is not None:
        base = imageadjust.apply_image(base, adjust)
    canvas = QtGui.QPixmap.fromImage(base)
    painter = QtGui.QPainter(canvas)
    try:
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        if has_image:
            u, v = review.to_view(entry, entry["ai_x"], entry["ai_y"], size, zoom)
            arm, gap = max(8, size // 7), max(3, size // 40)
            _cross(painter, u, v, arm, gap, _RED, 2)
            if entry["state"] == review.REFINED:
                u, v = review.to_view(entry, entry["x"], entry["y"], size, zoom)
                _cross(painter, u, v, arm, gap, _GREEN, 2)
        else:
            painter.setPen(QtGui.QColor(200, 200, 200))
            painter.drawText(QtCore.QRect(0, 0, size, size), QtCore.Qt.AlignCenter,
                             i18n.t("review.no_crop"))
        # Confidence at the top left.
        painter.fillRect(0, 0, 38, 16, QtGui.QColor(0, 0, 0, 150))
        painter.setPen(QtGui.QColor(*_WHITE))
        painter.drawText(4, 12, "{:.2f}".format(entry["conf"]))
        if entry["state"] == review.REMOVED:
            painter.fillRect(0, 0, size, size, QtGui.QColor(0, 0, 0, 120))
        _badge(painter, size, entry["state"])
    finally:
        painter.end()
    return canvas


class _RefineView(QtWidgets.QWidget):
    """Large view of the photo: click = place the centre, wheel = zoom, middle button = pan."""

    def __init__(self, on_pick, parent=None):
        super(_RefineView, self).__init__(parent)
        self._on_pick = on_pick
        self._entry = None
        self._image = None
        self._center = (0.0, 0.0)
        self._side = 1.0
        self._pick = None
        self._current = None           # already refined point of this thumbnail, shown faded
        self._pan = None
        self.setMinimumSize(VIEW_MIN, VIEW_MIN)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.setCursor(QtCore.Qt.CrossCursor)

    def show_entry(self, entry, image, side, pick):
        self._entry, self._image = entry, image
        self._side = side
        self._center = (entry["ai_x"], entry["ai_y"])
        self._pick = pick
        self._current = (entry["x"], entry["y"]) if entry["state"] == review.REFINED else None
        self.update()

    def set_image(self, image):
        self._image = image
        self.update()

    def set_pick(self, pick):
        self._pick = pick
        self.update()

    def _scale(self):
        return min(self.width(), self.height()) / self._side

    def _to_photo(self, u, v):
        scale = self._scale()
        return (self._center[0] + (u - self.width() / 2.0) / scale,
                self._center[1] + (v - self.height() / 2.0) / scale)

    def _to_widget(self, x, y):
        scale = self._scale()
        return ((x - self._center[0]) * scale + self.width() / 2.0,
                (y - self._center[1]) * scale + self.height() / 2.0)

    def _pick_at(self, event):
        self._pick = self._to_photo(*_event_pos(event))
        self.update()
        self._on_pick(*self._pick)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton and self._entry is not None:
            self._pick_at(event)
        elif event.button() == QtCore.Qt.MiddleButton:
            self._pan = _event_pos(event)
            self.setCursor(QtCore.Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self._pan is not None:
            u, v = _event_pos(event)
            scale = self._scale()
            self._center = (self._center[0] - (u - self._pan[0]) / scale,
                            self._center[1] - (v - self._pan[1]) / scale)
            self._pan = (u, v)
            self.update()
        elif event.buttons() & QtCore.Qt.LeftButton and self._entry is not None:
            self._pick_at(event)

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.MiddleButton and self._pan is not None:
            self._pan = None
            self.setCursor(QtCore.Qt.CrossCursor)

    def wheelEvent(self, event):
        if self._entry is None:
            return
        delta = event.angleDelta().y()
        if not delta:
            return
        u, v = _event_pos(event)
        before = self._to_photo(u, v)
        win = float(self._entry["win"])
        side = self._side * (VIEW_ZOOM_STEP if delta > 0 else 1.0 / VIEW_ZOOM_STEP)
        self._side = max(win / 16.0, min(win * 1.2, side))
        scale = self._scale()                  # the point under the cursor stays under the cursor
        self._center = (before[0] - (u - self.width() / 2.0) / scale,
                        before[1] - (v - self.height() / 2.0) / scale)
        self.update()
        event.accept()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        try:
            painter.fillRect(self.rect(), QtGui.QColor(45, 45, 45))
            if self._entry is None:
                return
            if self._image is not None and not self._image.isNull():
                painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
                painter.save()
                scale = self._scale()
                painter.translate(self.width() / 2.0, self.height() / 2.0)
                painter.scale(scale, scale)
                painter.translate(-self._center[0], -self._center[1])
                entry = self._entry
                painter.drawImage(QtCore.QRectF(entry["x0"], entry["y0"], entry["win"], entry["win"]),
                                  self._image)
                painter.restore()
            ai = self._to_widget(self._entry["ai_x"], self._entry["ai_y"])
            _cross(painter, ai[0], ai[1], 26, 6, _RED, 2)
            if self._pick is None and self._current is not None:
                u, v = self._to_widget(*self._current)
                _cross(painter, u, v, 20, 5, (60, 220, 120), 1)
            if self._pick is not None:
                u, v = self._to_widget(*self._pick)
                _cross(painter, u, v, 26, 4, _GREEN, 2)
        finally:
            painter.end()


class BusyDialog(QtWidgets.QDialog):
    """Small modal "applying" window with a progress bar.

    Blocks the review window while the points are placed (no double click on
    Validate). `step(done, total)` advances the bar and lets Qt repaint.
    """

    def __init__(self, text, parent=None):
        super(BusyDialog, self).__init__(parent)
        self.setModal(True)
        self.setWindowTitle(i18n.t("review.busy_title"))
        self.setWindowFlags(QtCore.Qt.Dialog | QtCore.Qt.CustomizeWindowHint | QtCore.Qt.WindowTitleHint)
        self.setFixedWidth(380)
        root = _styled_root(self)
        layout = QtWidgets.QVBoxLayout(root)
        pad = theme.PAD * 2
        layout.setContentsMargins(pad, pad, pad, pad)
        layout.setSpacing(theme.PAD)
        self.label = QtWidgets.QLabel(text)
        self.label.setWordWrap(True)
        layout.addWidget(self.label)
        self.bar = QtWidgets.QProgressBar()
        self.bar.setObjectName("aeroProgress")
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 0)
        layout.addWidget(self.bar)
        self.note = QtWidgets.QLabel(i18n.t("review.busy_note"))
        self.note.setObjectName("aeroMeta")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)

    def step(self, done, total):
        if total > 0:
            self.bar.setRange(0, total)
            self.bar.setValue(min(done, total))
        QtWidgets.QApplication.processEvents()

    def open(self):
        """Shows the window without blocking: the caller advances the bar with step()."""
        self.show()
        self.raise_()
        QtWidgets.QApplication.processEvents()


class RefineDialog(QtWidgets.QDialog):
    """Modal refinement window: all the chosen thumbnails, one after the other.

    The points are only placed on "Validate"; `picks()` returns them. "Cancel" places none.
    """

    def __init__(self, entries, zoom, model, parent=None):
        super(RefineDialog, self).__init__(parent)
        self._entries = list(entries)
        self._zoom = zoom
        self._model = model
        self._picks = {}
        self._index = 0
        self._source = None
        root = _styled_root(self)
        self.resize(1010, 760)
        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(theme.PAD_L, theme.PAD_L, theme.PAD_L, theme.PAD_L)
        layout.setSpacing(theme.PAD)

        self.header = QtWidgets.QLabel()
        self.header.setObjectName("aeroValue")
        layout.addWidget(self.header)

        body = QtWidgets.QHBoxLayout()
        body.setSpacing(theme.PAD)
        self.view = _RefineView(self._on_pick)
        body.addWidget(self.view, 1)
        self.panel = adjust_panel.AdjustPanel(model, hint_key="review.refine_hint")
        body.addWidget(self.panel, 0)
        layout.addLayout(body, 1)

        bottom = QtWidgets.QHBoxLayout()
        bottom.setSpacing(theme.PAD)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setObjectName("aeroProgress")
        self.progress.setTextVisible(False)
        self.progress.setRange(0, max(1, len(self._entries)))
        bottom.addWidget(self.progress, 1)
        self.progress_label = QtWidgets.QLabel()
        self.progress_label.setObjectName("aeroMeta")
        self.progress_label.setMinimumWidth(110)
        bottom.addWidget(self.progress_label)
        self.cancel_button = _button("review.refine_cancel", self.reject)
        self.next_button = _button("review.refine_next", self._next)
        self.validate_button = _button("review.refine_validate_exit", self.accept, width=150)
        # Windows order: the positive action on the left, Cancel on the far right.
        for button in (self.validate_button, self.next_button, self.cancel_button):
            bottom.addWidget(button)
        layout.addLayout(bottom)

        for key in ("Return", "Enter"):
            shortcut = QShortcut(QtGui.QKeySequence(key), self)
            shortcut.setContext(QtCore.Qt.WindowShortcut)
            shortcut.activated.connect(self._on_enter)

        self._refresh = _timer(self, self._reload_image)
        model.subscribe(self._on_model)
        self.finished.connect(lambda *_args: model.unsubscribe(self._on_model))
        self._show(0)

    # -- current entry --------------------------------------------------------------

    def _entry(self):
        return self._entries[self._index]

    def _show(self, index):
        self._index = index
        entry = self._entry()
        source = QtGui.QImage(entry["big"]) if review.has_crop(entry) else None
        self._source = source if source is not None and not source.isNull() else None
        text = i18n.t("review.refine_title", label=entry["label"], camera=entry["camera_label"])
        position = i18n.t("review.refine_photo", n=index + 1, total=len(self._entries))
        self.header.setText("{}  ·  {}".format(text, position))
        self.setWindowTitle("{} ({})".format(text, position))
        side = review.view_rect(entry, self._zoom)[2]
        self.view.show_entry(entry, self._adjusted(), side, self._picks.get(index))
        self._update_state()

    def _adjusted(self):
        return imageadjust.apply_image(self._source, self._model) if self._source is not None else None

    def _reload_image(self):
        self.view.set_image(self._adjusted())

    def _on_model(self):
        if not self._refresh.isActive():        # the image follows the slider continuously, not at the end of the gesture
            self._refresh.start(REFRESH_MS)

    # -- decisions ---------------------------------------------------------------------

    def _on_pick(self, x, y):
        self._picks[self._index] = (x, y)
        self._update_state()

    def _update_state(self):
        total, done = len(self._entries), len(self._picks)
        self.progress.setValue(done)
        self.progress_label.setText(i18n.t("review.refine_progress", done=done, total=total))
        self.next_button.setEnabled(total > 1)
        complete = done == total
        self.validate_button.setEnabled(done > 0)
        self.validate_button.setText(i18n.t("review.refine_validate" if complete
                                            else "review.refine_validate_exit"))
        _restyle(self.validate_button, "aeroRun" if complete else "aeroGhost")

    def _next(self):
        """Moves to the next thumbnail without a point (wrapping around); otherwise to the next one."""
        total = len(self._entries)
        for step in range(1, total + 1):
            index = (self._index + step) % total
            if index not in self._picks:
                self._show(index)
                return
        self._show((self._index + 1) % total)

    def _on_enter(self):
        if self._index not in self._picks:
            return
        if len(self._picks) == len(self._entries):
            self.accept()
        else:
            self._next()

    def picks(self):
        """[(entry, x, y)] of the placed points, in photo position."""
        return [(self._entries[i], x, y) for i, (x, y) in sorted(self._picks.items())]


class _PreviewLabel(QtWidgets.QLabel):
    """Selected photo: a left click in it gives the position (u, v) of the new cross."""

    def __init__(self, on_click, parent=None):
        super(_PreviewLabel, self).__init__(parent)
        self._on_click = on_click
        self.setCursor(QtCore.Qt.CrossCursor)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            u, v = _event_pos(event)
            self._on_click(u, v)
        super(_PreviewLabel, self).mousePressEvent(event)


class _ThumbList(QtWidgets.QListWidget):
    """Thumbnail list: Ctrl + wheel = thumbnail size, Shift + wheel = zoom inside them."""

    def __init__(self, on_resize, on_zoom, parent=None):
        super(_ThumbList, self).__init__(parent)
        self._on_resize = on_resize
        self._on_zoom = on_zoom

    def wheelEvent(self, event):
        ctrl, shift, _alt = modifiers(event)
        if ctrl or shift:
            # Qt may turn Shift + wheel into horizontal scrolling: both axes are read.
            delta = event.angleDelta().y() or event.angleDelta().x()
            if delta:
                (self._on_resize if ctrl else self._on_zoom)(1 if delta > 0 else -1)
            event.accept()
            return
        super(_ThumbList, self).wheelEvent(event)


class ReviewDialog(QtWidgets.QDialog):
    """Markers on the left, thumbnails in the middle, settings panel on the right. Non-modal."""

    def __init__(self, state, rev, parent=None):
        super(ReviewDialog, self).__init__(parent)
        self.review = rev
        self._state = state
        self._cache = {}
        self._thumb = thumb_size()
        self._zoom = zoom_percent() / 100.0
        self._items = {}
        self.adjust = imageadjust.load()
        root = _styled_root(self)
        self.setWindowTitle(i18n.t("review.title"))
        self.resize(1280, 820)

        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(theme.PAD_L, theme.PAD_L, theme.PAD_L, theme.PAD_L)
        layout.setSpacing(theme.PAD)

        body = QtWidgets.QHBoxLayout()
        body.setSpacing(theme.PAD)
        self.markers = QtWidgets.QListWidget()
        self.markers.setObjectName("aeroThumbs")
        self.markers.setFixedWidth(190)
        self.markers.currentRowChanged.connect(self._on_marker)
        body.addWidget(self.markers)
        self.thumbs = _ThumbList(self._resize_thumbs, self._step_zoom)
        self.thumbs.setObjectName("aeroThumbs")
        self.thumbs.setViewMode(QtWidgets.QListView.IconMode)
        self.thumbs.setResizeMode(QtWidgets.QListView.Adjust)
        self.thumbs.setMovement(QtWidgets.QListView.Static)
        self.thumbs.setUniformItemSizes(True)
        self.thumbs.setWordWrap(True)
        self.thumbs.setSpacing(theme.PAD)
        # A click makes the photo active (top right); Ctrl or Shift + click to pick several.
        self.thumbs.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.thumbs.itemSelectionChanged.connect(self._update_buttons)
        self.thumbs.itemDoubleClicked.connect(self._on_double_click)
        self.thumbs.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.thumbs.customContextMenuRequested.connect(self._on_menu)
        body.addWidget(self.thumbs, 1)
        self._viewed = None
        self.panel = adjust_panel.AdjustPanel(self.adjust, top=self._build_top(), hint_key="review.hint")
        # If the window is short, the panel scrolls: its sliders never overlap.
        scroller = QtWidgets.QScrollArea()
        scroller.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroller.setWidgetResizable(True)
        scroller.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroller.setFixedWidth(self.panel.width() + SCROLL_ROOM)
        scroller.setWidget(self.panel)
        scroller.viewport().setAutoFillBackground(False)
        self.panel.setAutoFillBackground(False)
        body.addWidget(scroller, 0)
        layout.addLayout(body, 1)

        bottom = QtWidgets.QHBoxLayout()
        bottom.setSpacing(theme.PAD)
        self.exclude_button = _button("review.exclude", self._on_exclude)
        self.restore_button = _button("review.restore", self._on_restore)
        self.refine_button = _button("review.refine", self._on_refine)
        for button in (self.exclude_button, self.restore_button, self.refine_button):
            bottom.addWidget(button)
        bottom.addStretch(1)
        self.summary = QtWidgets.QLabel()
        self.summary.setObjectName("aeroMeta")
        bottom.addWidget(self.summary)
        self.validate_button = _button("review.accept", self._on_accept)
        bottom.addWidget(self.validate_button)
        layout.addLayout(bottom)

        for key, handler in (("Delete", self._on_exclude), ("Return", self._on_refine)):
            shortcut = QShortcut(QtGui.QKeySequence(key), self)
            shortcut.setContext(QtCore.Qt.WindowShortcut)
            shortcut.activated.connect(handler)

        self._refresh = _timer(self, self._repaint_all)
        self.adjust.subscribe(self._on_adjust)
        self.finished.connect(lambda *_args: self.adjust.unsubscribe(self._on_adjust))
        self._update_zoom_label()
        self._fill_markers()

    def showEvent(self, event):
        super(ReviewDialog, self).showEvent(event)
        # The window is reused after being closed: `finished` removed the settings subscription,
        # so it is put back (without it, exposure, contrast... no longer repainted the thumbnails).
        self.adjust.subscribe(self._on_adjust)

    def _build_top(self):
        """Top of the right panel: the selected photo enlarged, then the zoom."""
        box = QtWidgets.QWidget()
        column = QtWidgets.QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(theme.PAD_S)
        title = QtWidgets.QLabel(i18n.t("review.preview").upper())
        title.setObjectName("aeroEyebrow")
        column.addWidget(title)
        self.preview = _PreviewLabel(self._on_preview_click)
        self.preview.setFixedSize(PREVIEW, PREVIEW)
        self.preview.setAlignment(QtCore.Qt.AlignCenter)
        self.preview.setWordWrap(True)
        self.preview.setStyleSheet("background: {}; border-radius: 4px; color: {};".format(theme.GROUND_3, theme.FG_3))
        column.addWidget(self.preview)
        self.preview_name = QtWidgets.QLabel()
        self.preview_name.setObjectName("aeroMeta")
        self.preview_name.setWordWrap(True)
        column.addWidget(self.preview_name)
        column.addSpacing(theme.PAD_S)
        column.addWidget(self._build_zoom())
        self._update_preview()
        return box

    def _preview_entry(self):
        """The entry to show: the last thumbnail clicked, otherwise the last one viewed (the selection empties after a decision)."""
        item = self.thumbs.currentItem()
        found = self._items.get(item.data(ROLE_ENTRY)) if item is not None and item.isSelected() else None
        if found is None:
            picked = self._selected()
            found = picked[-1] if picked else None
        if found is not None:
            self._viewed = found[1]
        elif self._viewed is not None and id(self._viewed) not in self._items:
            self._viewed = None
        return self._viewed

    def _on_preview_click(self, u, v):
        """Click in the selected photo: places a new cross (refined point) at that spot."""
        entry = self._viewed
        found = self._items.get(id(entry)) if entry is not None else None
        if found is None or not review.has_crop(entry):
            return
        x, y = review.to_source(entry, u, v, PREVIEW, self._zoom)
        try:
            done = review.refine(self._state, self.review, entry, x, y)
        except Exception as exc:
            journal.write("AI review: cross placed in the selected photo: {!r}".format(exc))
            return
        if done:
            self._paint_item(found[0], entry)
            self._update_preview()
            self._after_change({entry["label"]})

    def _update_preview(self):
        entry = self._preview_entry()
        if entry is None:
            self.preview.setPixmap(QtGui.QPixmap())
            self.preview.setText(i18n.t("review.preview_none"))
            self.preview_name.setText("")
            return
        self.preview.setPixmap(render_thumb(entry, PREVIEW, self._zoom, self._cache, self.adjust))
        self.preview_name.setText("{} · {:.2f} · {}".format(
            entry["camera_label"], entry["conf"], i18n.t(STATUS_TEXTS[entry["state"]])))

    def _build_zoom(self):
        """Zoom row, under the selected photo: slimmer than a bar at the top of the window."""
        box = QtWidgets.QWidget()
        column = QtWidgets.QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        line = QtWidgets.QHBoxLayout()
        name = QtWidgets.QLabel(i18n.t("review.zoom"))
        name.setObjectName("aeroMeta")
        self.zoom_label = QtWidgets.QLabel()
        self.zoom_label.setObjectName("aeroMeta")
        self.zoom_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        line.addWidget(name, 1)
        line.addWidget(self.zoom_label, 0)
        column.addLayout(line)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(theme.PAD_S)
        row.setAlignment(QtCore.Qt.AlignVCenter)
        self.zoom_out = self._zoom_button("−", -ZOOM_STEP)
        row.addWidget(self.zoom_out, 0, QtCore.Qt.AlignVCenter)
        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setObjectName("aeroSlider")
        self.slider.setRange(MIN_ZOOM, MAX_ZOOM)
        self.slider.setValue(int(round(self._zoom * 100)))
        self.slider.setFixedHeight(26)           # same height as the buttons: everything is centred on the same line
        self.slider.valueChanged.connect(self._on_zoom)
        self.slider.sliderReleased.connect(self._save_zoom)
        row.addWidget(self.slider, 1, QtCore.Qt.AlignVCenter)
        self.zoom_in = self._zoom_button("+", ZOOM_STEP)
        row.addWidget(self.zoom_in, 0, QtCore.Qt.AlignVCenter)
        column.addLayout(row)
        return box

    def _zoom_button(self, text, step):
        button = QtWidgets.QToolButton()
        button.setObjectName("aeroZoomButton")
        button.setText(text)
        button.setCursor(QtCore.Qt.PointingHandCursor)
        button.setFixedSize(26, 26)
        button.clicked.connect(lambda *_args: self.slider.setValue(self.slider.value() + step))
        return button

    # -- filling -------------------------------------------------------------

    def _marker_text(self, label):
        entries = self.review.entries_for(label)
        kept = sum(1 for e in entries if e["state"] in (review.KEPT, review.REFINED, review.ACCEPTED))
        return "{}   {}/{}   ·  {:.0f} %".format(label, kept, len(entries), 100 * self.review.weakest(label))

    def _fill_markers(self):
        self.markers.clear()
        for label in self.review.labels():
            self.markers.addItem(self._marker_text(label))
            self.markers.item(self.markers.count() - 1).setToolTip(
                i18n.t("review.weakest", conf="{:.0f}".format(100 * self.review.weakest(label))))
        if self.markers.count():
            self.markers.setCurrentRow(0)
        self._update_summary()
        self._update_buttons()

    def _current_label(self):
        row = self.markers.currentRow()
        labels = self.review.labels()
        return labels[row] if 0 <= row < len(labels) else None

    def _on_marker(self, _row):
        self._fill_thumbs()

    def _grid(self):
        self.thumbs.setIconSize(QtCore.QSize(self._thumb, self._thumb))
        self.thumbs.setGridSize(QtCore.QSize(self._thumb + 16, self._thumb + 40))

    def _fill_thumbs(self):
        self.thumbs.clear()
        self._items = {}
        self._viewed = None
        self._grid()
        label = self._current_label()
        if label is not None:
            for entry in self.review.entries_for(label):
                item = QtWidgets.QListWidgetItem()
                item.setData(ROLE_ENTRY, id(entry))
                self.thumbs.addItem(item)
                self._items[id(entry)] = (item, entry)
                self._paint_item(item, entry)
        self._update_buttons()

    def _paint_item(self, item, entry):
        item.setIcon(QtGui.QIcon(render_thumb(entry, self._thumb, self._zoom, self._cache, self.adjust)))
        item.setText(entry["camera_label"].split("_", 2)[-1])
        item.setToolTip("{} · {:.2f} · {}".format(entry["camera_label"], entry["conf"],
                                                  i18n.t(STATUS_TEXTS[entry["state"]])))

    def _repaint_all(self):
        for item, entry in self._items.values():
            self._paint_item(item, entry)
        self._update_preview()

    def _on_adjust(self):
        self._update_preview()                  # the selected photo follows the slider continuously
        if not self._refresh.isActive():        # the thumbnails follow at the repaint rate, not at the end of the gesture
            self._refresh.start(REFRESH_MS)

    def _selected(self):
        out = []
        for item in self.thumbs.selectedItems():
            found = self._items.get(item.data(ROLE_ENTRY))
            if found is not None:
                out.append(found)
        return out

    # -- sizes and zoom ----------------------------------------------------------

    def _resize_thumbs(self, direction):
        """Ctrl + wheel: larger or smaller thumbnails."""
        size = min(MAX_THUMB, max(MIN_THUMB, self._thumb + direction * THUMB_STEP))
        if size == self._thumb:
            return
        self._thumb = size
        self._grid()
        self._repaint_all()
        config.put(SECTION, THUMB_KEY, size)

    def _step_zoom(self, direction):
        """Shift + wheel: same effect as the zoom slider."""
        self.slider.setValue(self.slider.value() + direction * ZOOM_STEP)

    def _update_zoom_label(self):
        self.zoom_label.setText("{}%".format(int(round(self._zoom * 100))))

    def _on_zoom(self, value):
        """+/- slider: more or less context around the point, in each thumbnail."""
        self._zoom = value / 100.0
        self._update_zoom_label()
        self._repaint_all()
        if not self.slider.isSliderDown():
            self._save_zoom()

    def _save_zoom(self):
        config.put(SECTION, ZOOM_KEY, int(round(self._zoom * 100)))

    # -- button state ---------------------------------------------------------------

    def _update_buttons(self):
        picked = [entry for _item, entry in self._selected()]
        self.exclude_button.setEnabled(any(e["state"] in EXCLUDABLE for e in picked))
        self.restore_button.setEnabled(any(e["state"] == review.REMOVED for e in picked))
        self.refine_button.setEnabled(any(review.has_crop(e) for e in picked))
        self.validate_button.setEnabled(any(e["state"] == review.KEPT for e in self.review.entries))
        if hasattr(self, "preview"):
            self._update_preview()

    def _update_summary(self):
        counts = self.review.counts()
        self.summary.setText(i18n.t(
            "review.summary", kept=counts[review.KEPT], removed=counts[review.REMOVED],
            refined=counts[review.REFINED], accepted=counts[review.ACCEPTED]))

    def _after_change(self, labels=None, everything=False):
        """Refreshes the window and the FastGCP panel (X/Y/Z errors, number of projections).

        `labels`: markers for which a proposal has just changed; `everything`: the whole table.
        """
        row = self.markers.currentRow()
        labels = self.review.labels()
        for index, label in enumerate(labels):
            self.markers.item(index).setText(self._marker_text(label))
        if 0 <= row < len(labels):
            self.markers.setCurrentRow(row)
        self._update_summary()
        self._update_buttons()
        photos.panel_call(self._state, "update_aigcp")
        photos.panel_call(self._state, "refresh_rows", None if everything else (labels or []), True)
        photos.panel_call(self._state, "update_active_card")

    # -- decisions -----------------------------------------------------------------------

    def _apply(self, action):
        changed, labels = 0, set()
        for item, entry in self._selected():
            try:
                if action(self._state, self.review, entry):
                    changed += 1
                    labels.add(entry["label"])
                    self._paint_item(item, entry)
            except Exception as exc:
                journal.write("AI review: {!r}".format(exc))
        if changed:
            self.thumbs.clearSelection()
            self._after_change(labels)

    def _on_exclude(self):
        # Excluding an already validated or refined point undoes a manual pin: ask first.
        done = sum(1 for _item, e in self._selected() if e["state"] in (review.ACCEPTED, review.REFINED))
        if done and not dialogs.ask(self, i18n.t("review.exclude"), i18n.t("review.confirm_exclude_done", n=done)):
            return
        self._apply(review.remove)

    def _on_restore(self):
        self._apply(review.restore)

    def _on_double_click(self, item):
        found = self._items.get(item.data(ROLE_ENTRY))
        if found is not None:
            self._refine([found])

    def _on_refine(self):
        self._refine(self._selected())

    def _refine(self, pairs):
        """Opens the chosen thumbnails in the refinement window, then places the validated points."""
        pairs = [(i, e) for i, e in pairs if review.has_crop(e)]
        if not pairs:
            return
        by_entry = {id(entry): item for item, entry in pairs}
        done, labels = 0, set()
        try:
            dialog = RefineDialog([e for _i, e in pairs], max(1.0, self._zoom), self.adjust, self)
            if _exec(dialog):
                picks = dialog.picks()
                busy = BusyDialog(i18n.t("review.busy_refine", n=len(picks)), self) if len(picks) > 1 else None
                if busy is not None:
                    busy.open()
                try:
                    for index, (entry, x, y) in enumerate(picks):
                        if busy is not None:
                            busy.step(index, len(picks))
                        try:
                            if review.refine(self._state, self.review, entry, x, y):
                                self._paint_item(by_entry[id(entry)], entry)
                                labels.add(entry["label"])
                                done += 1
                        except Exception as exc:
                            journal.write("AI review: refinement: {!r}".format(exc))
                finally:
                    if busy is not None:
                        busy.close()
        except Exception as exc:
            journal.write("AI review: refinement: {!r}".format(exc))
        self._repaint_all()           # the settings may have changed in the refinement window
        if done:
            self.thumbs.clearSelection()
            self._after_change(labels)

    def _on_menu(self, pos):
        item = self.thumbs.itemAt(pos)
        if item is not None and not item.isSelected():
            item.setSelected(True)
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet(theme.menu_stylesheet())
        for text_key, handler, enabled in (
                ("review.exclude", self._on_exclude, self.exclude_button.isEnabled()),
                ("review.restore", self._on_restore, self.restore_button.isEnabled()),
                ("review.refine", self._on_refine, self.refine_button.isEnabled())):
            action = menu.addAction(i18n.t(text_key))
            action.setEnabled(enabled)
            action.triggered.connect(lambda *_args, h=handler: h())
        _exec(menu, self.thumbs.viewport().mapToGlobal(pos))

    def _on_accept(self):
        count = sum(1 for e in self.review.entries if e["state"] == review.KEPT)
        if not count:
            return
        if not dialogs.ask(self, i18n.t("review.accept"), i18n.t("review.confirm_accept", n=count)):
            return
        busy = BusyDialog(i18n.t("review.busy_validate", n=count), self)
        busy.open()
        try:
            done = review.accept_kept(self._state, self.review, busy.step)
            busy.label.setText(i18n.t("review.busy_refresh"))
            busy.bar.setRange(0, 0)
            QtWidgets.QApplication.processEvents()
            photos.panel_call(self._state, "set_status", i18n.t("review.accepted_status", n=done))
            self._fill_thumbs()
            self._after_change(everything=True)
        finally:
            busy.close()


def show(state, parent=None, existing=None):
    """Opens (or brings to front) the Review panel of the last detection. Returns the window."""
    rev = getattr(state, "aigcp_review", None)
    if rev is None or not rev.entries:
        photos.panel_call(state, "set_status", i18n.t("review.empty"))
        return existing
    try:
        if existing is not None and existing.review is rev:
            existing.show()
            existing.raise_()
            existing.activateWindow()
            return existing
    except RuntimeError:
        pass
    close(existing)
    dialog = ReviewDialog(state, rev, msapi.main_window() or parent)
    dialog.show()
    return dialog


def close(dialog):
    """Closes the window if it still exists. Never raises."""
    try:
        if dialog is not None:
            dialog.close()
    except Exception:
        pass
