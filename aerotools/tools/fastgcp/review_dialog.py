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

from ... import config, i18n, journal, msapi, uikit
from ...qt import QShortcut, QtCore, QtGui, QtWidgets, modifiers
from ...uikit import tokens
from . import adjust_panel, imageadjust, photos, review, widgets

SECTION = "aigcp"
THUMB_KEY = "review_thumb"
ZOOM_KEY = "review_zoom"
DEFAULT_THUMB = 170
MIN_THUMB, MAX_THUMB, THUMB_STEP = 96, 360, 20
DEFAULT_ZOOM, MIN_ZOOM, MAX_ZOOM, ZOOM_STEP = 100, 50, 400, 25
PREVIEW = 150                         # selected photo, at the top of the right panel
REFRESH_MS = 70                       # wait after an adjustment before repainting the images
VIEW_MIN = 480                        # minimum side of the large refinement view
VIEW_ZOOM_STEP = 0.8                  # one wheel notch in the refinement view
ROLE_ENTRY = QtCore.Qt.UserRole


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
    pen = QtGui.QPen(QtGui.QColor(color))
    pen.setWidth(width)
    painter.setPen(pen)
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        painter.drawLine(int(x + dx * gap), int(y + dy * gap), int(x + dx * arm), int(y + dy * arm))




def _shade(color, alpha):
    """A kit colour with some transparency (the dark veils over a photo)."""
    out = QtGui.QColor(color)
    out.setAlpha(alpha)
    return out
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
        fill, mark = QtGui.QColor(tokens.DANGER_SOLID), QtGui.QColor(tokens.TEXT_STRONG)
    elif state == review.REFINED:
        fill, mark = QtGui.QColor(tokens.ACCENT), QtGui.QColor(tokens.ON_ACCENT)
    else:
        fill, mark = _shade(tokens.BG, 220), QtGui.QColor(tokens.ACCENT)
    painter.setPen(QtGui.QPen(QtGui.QColor(tokens.ACCENT), 1.5) if state == review.ACCEPTED else QtCore.Qt.NoPen)
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
    base.fill(QtGui.QColor(tokens.SURFACE))
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
            _cross(painter, u, v, arm, gap, tokens.DANGER, 2)
            if entry["state"] == review.REFINED:
                u, v = review.to_view(entry, entry["x"], entry["y"], size, zoom)
                _cross(painter, u, v, arm, gap, tokens.ACCENT, 2)
        else:
            painter.setPen(QtGui.QColor(tokens.TEXT_MUTED))
            painter.drawText(QtCore.QRect(0, 0, size, size), QtCore.Qt.AlignCenter,
                             i18n.t("review.no_crop"))
        # Confidence at the top left.
        painter.fillRect(0, 0, 38, 16, _shade(tokens.BG, 200))
        painter.setPen(QtGui.QColor(tokens.TEXT_STRONG))
        painter.drawText(4, 12, "{:.2f}".format(entry["conf"]))
        if entry["state"] == review.REMOVED:
            painter.fillRect(0, 0, size, size, _shade(tokens.BG, 150))
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
            painter.fillRect(self.rect(), QtGui.QColor(tokens.SURFACE))
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
            _cross(painter, ai[0], ai[1], 26, 6, tokens.DANGER, 2)
            if self._pick is None and self._current is not None:
                u, v = self._to_widget(*self._current)
                _cross(painter, u, v, 20, 5, tokens.ACCENT, 1)
            if self._pick is not None:
                u, v = self._to_widget(*self._pick)
                _cross(painter, u, v, 26, 4, tokens.ACCENT, 2)
        finally:
            painter.end()


BusyDialog = widgets.BusyDialog


class RefineDialog(uikit.ToolWindow):
    """Modal refinement window: all the chosen thumbnails, one after the other.

    The points are only placed on "Validate"; `picks()` returns them. "Cancel" places none.
    """
    MARK = "tool_gcp"
    DESIGN_SIZE = (1100, 740)

    def __init__(self, entries, zoom, model, parent=None):
        super(RefineDialog, self).__init__(parent, title=i18n.t("review.title"), modal=True)
        self._entries = list(entries)
        self._zoom = zoom
        self._model = model
        self._picks = {}
        self._index = 0
        self._source = None

        body = QtWidgets.QWidget()
        row = uikit.hbox(spacing=tokens.M, parent=body)
        self.view = _RefineView(self._on_pick)
        row.addWidget(self.view, 1)
        self.panel = adjust_panel.AdjustPanel(model, hint_key="review.refine_hint")
        row.addWidget(self.panel, 0)
        self.set_content(body)

        self.skip_button = self.footer.add_left(uikit.button("review.refine_next", self._next))
        self.set_primary(i18n.t("review.refine_validate_exit"), self.accept)
        self.footer.show_progress(True)                      # the footer shows how many points are placed
        self.footer.progress.set_cancellable(False)

        for key in ("Return", "Enter"):
            shortcut = QShortcut(QtGui.QKeySequence(key), self)
            shortcut.setContext(QtCore.Qt.WindowShortcut)
            shortcut.activated.connect(self._on_enter)

        self._refresh = _timer(self, self._reload_image)
        model.subscribe(self._on_model)
        self.finished.connect(lambda *_args: model.unsubscribe(self._on_model))
        self._show(0)

    @property
    def validate_button(self):
        return self.next_button

    def refresh_buttons(self, *args):
        super(RefineDialog, self).refresh_buttons(*args)
        if hasattr(self, "skip_button"):
            self._update_state()                           # the shell re-enables the primary: Validate needs a point first

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
        self.set_title(text)
        self.set_subtitle(position)
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
        self.footer.progress.set_progress(done / float(max(1, total)), i18n.t("review.refine_progress", done=done, total=total))
        self.skip_button.setEnabled(total > 1)
        complete = done == total
        self.next_button.setEnabled(done > 0)
        self.next_button.setText(i18n.t("review.refine_validate" if complete else "review.refine_validate_exit"))

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


class _MarkerDelegate(QtWidgets.QStyledItemDelegate):
    """A marker of the list: its name first, in the primary text colour and semibold; what is kept and how sure the AI is, muted after it."""

    def paint(self, painter, option, index):
        opt = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text, opt.text = opt.text, ""
        widget = option.widget
        (widget.style() if widget is not None else QtWidgets.QApplication.style()).drawControl(QtWidgets.QStyle.CE_ItemViewItem, opt, painter, widget)
        name, _sep, rest = text.partition("   ")
        rect = option.rect.adjusted(uikit.px(tokens.S), 0, -uikit.px(tokens.S), 0)
        painter.save()
        painter.setFont(uikit.metrics.font("body", strong=True))
        painter.setPen(QtGui.QColor(tokens.TEXT_STRONG))
        width = painter.fontMetrics().horizontalAdvance(name)
        painter.drawText(rect, QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, name)
        painter.setFont(uikit.metrics.font("small"))
        painter.setPen(QtGui.QColor(tokens.TEXT_MUTED))
        painter.drawText(rect.adjusted(width + uikit.px(tokens.S), 0, 0, 0), QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, " ".join(rest.split()))
        painter.restore()

    def sizeHint(self, option, index):
        return QtCore.QSize(option.rect.width(), uikit.px(tokens.CONTROL_H))


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


class ReviewDialog(uikit.ToolWindow):
    """Markers on the left, thumbnails in the middle, settings panel on the right. Non-modal."""
    MARK = "tool_gcp"
    CLOSE_ONLY = True
    DESIGN_SIZE = (1240, 740)

    def __init__(self, state, rev, parent=None):
        super(ReviewDialog, self).__init__(parent, title=i18n.t("review.title"))
        self.review = rev
        self._state = state
        self._cache = {}
        self._thumb = thumb_size()
        self._zoom = zoom_percent() / 100.0
        self._items = {}
        self.adjust = imageadjust.load()

        body = QtWidgets.QWidget()
        layout = uikit.hbox(spacing=tokens.M, parent=body)
        self.markers = QtWidgets.QListWidget()
        self.markers.setObjectName("aeroThumbs")
        self.markers.setFixedWidth(uikit.px(210))
        self.markers.setItemDelegate(_MarkerDelegate(self.markers))
        self.markers.currentRowChanged.connect(self._on_marker)
        layout.addWidget(self.markers)
        self.thumbs = _ThumbList(self._resize_thumbs, self._step_zoom)
        self.thumbs.setObjectName("aeroThumbs")
        self.thumbs.setViewMode(QtWidgets.QListView.IconMode)
        self.thumbs.setResizeMode(QtWidgets.QListView.Adjust)
        self.thumbs.setMovement(QtWidgets.QListView.Static)
        self.thumbs.setUniformItemSizes(True)
        self.thumbs.setWordWrap(True)
        self.thumbs.setSpacing(uikit.px(tokens.S))
        # A click makes the photo active (top right); Ctrl or Shift + click to pick several.
        self.thumbs.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.thumbs.itemSelectionChanged.connect(self._update_buttons)
        self.thumbs.itemDoubleClicked.connect(self._on_double_click)
        self.thumbs.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.thumbs.customContextMenuRequested.connect(self._on_menu)
        layout.addWidget(self.thumbs, 1)
        self._viewed = None
        self.panel = adjust_panel.AdjustPanel(self.adjust, top=self._build_top(), hint_key="review.hint")
        layout.addWidget(self.panel, 0)
        self.set_content(body)
        # The three decisions on the selected thumbnails sit with Close; Validate (every kept point) is the primary.
        self.exclude_button = self.footer.add_left(uikit.button("review.exclude", self._on_exclude))
        self.restore_button = self.footer.add_left(uikit.button("review.restore", self._on_restore))
        self.refine_button = self.footer.add_left(uikit.button("review.refine", self._on_refine))
        self.set_primary(i18n.t("review.accept"), self._on_accept)

        for key, handler in (("Delete", self._on_exclude), ("Return", self._on_refine)):
            shortcut = QShortcut(QtGui.QKeySequence(key), self)
            shortcut.setContext(QtCore.Qt.WindowShortcut)
            shortcut.activated.connect(handler)

        self._refresh = _timer(self, self._repaint_all)
        self.adjust.subscribe(self._on_adjust)
        self.finished.connect(lambda *_args: self.adjust.unsubscribe(self._on_adjust))
        self._update_zoom_label()
        self._fill_markers()

    @property
    def validate_button(self):
        return self.next_button

    @property
    def summary(self):
        return self.status

    def refresh_buttons(self, *args):
        super(ReviewDialog, self).refresh_buttons(*args)
        if hasattr(self, "refine_button"):
            self._update_buttons()                         # the shell re-enables the primary: Validate needs a kept point

    def showEvent(self, event):
        super(ReviewDialog, self).showEvent(event)
        # The window is reused after being closed: `finished` removed the settings subscription,
        # so it is put back (without it, exposure, contrast... no longer repainted the thumbnails).
        self.adjust.subscribe(self._on_adjust)

    def _build_top(self):
        """Top of the right panel: the selected photo enlarged, then the zoom."""
        box = QtWidgets.QWidget()
        column = uikit.vbox(spacing=tokens.S, parent=box)
        title = uikit.label(i18n.t("review.preview"), "lead", wrap=False)
        column.addWidget(title)
        self.preview = _PreviewLabel(self._on_preview_click)
        self.preview.setFixedSize(uikit.px(PREVIEW), uikit.px(PREVIEW))
        self.preview.setAlignment(QtCore.Qt.AlignCenter)
        self.preview.setWordWrap(True)
        self.preview.setProperty("ui", "card")
        self.preview.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        column.addWidget(self.preview)
        self.preview_name = uikit.ElidedLabel("", "small", "muted")
        column.addWidget(self.preview_name)
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
            self.preview_name.setFullText("")
            return
        self.preview.setPixmap(render_thumb(entry, PREVIEW, self._zoom, self._cache, self.adjust))
        self.preview_name.setFullText("{} · {:.2f} · {}".format(
            entry["camera_label"], entry["conf"], i18n.t(STATUS_TEXTS[entry["state"]])))

    def _build_zoom(self):
        """Zoom row, under the selected photo: slimmer than a bar at the top of the window."""
        box = QtWidgets.QWidget()
        column = uikit.vbox(spacing=tokens.XS, parent=box)
        line = uikit.hbox()
        name = uikit.label(i18n.t("review.zoom"), "small", "muted", wrap=False)
        self.zoom_label = uikit.label("", "small", "muted", wrap=False)
        self.zoom_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        line.addWidget(name, 1)
        line.addWidget(self.zoom_label, 0)
        column.addLayout(line)
        row = uikit.hbox(spacing=tokens.S)
        self.zoom_out = self._zoom_button("minus", -ZOOM_STEP)
        row.addWidget(self.zoom_out, 0, QtCore.Qt.AlignVCenter)
        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setObjectName("aeroSlider")
        self.slider.setRange(MIN_ZOOM, MAX_ZOOM)
        self.slider.setValue(int(round(self._zoom * 100)))
        self.slider.setFixedHeight(uikit.px(tokens.CONTROL_H))
        self.slider.valueChanged.connect(self._on_zoom)
        self.slider.sliderReleased.connect(self._save_zoom)
        row.addWidget(self.slider, 1, QtCore.Qt.AlignVCenter)
        self.zoom_in = self._zoom_button("plus", ZOOM_STEP)
        row.addWidget(self.zoom_in, 0, QtCore.Qt.AlignVCenter)
        column.addLayout(row)
        return box

    def _zoom_button(self, icon_name, step):
        tip = i18n.t("review.zoom_out" if step < 0 else "review.zoom_in")
        return uikit.IconButton(icon_name, tip, lambda: self.slider.setValue(self.slider.value() + step))

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
        self.set_status(i18n.t(
            "review.summary", kept=counts[review.KEPT], removed=counts[review.REMOVED],
            refined=counts[review.REFINED], accepted=counts[review.ACCEPTED]))

    def _after_change(self, labels=None, everything=False):
        """Refreshes the window and the FastGCP panel (X/Y/Z errors, number of projections).

        `labels`: markers for which a proposal has just changed; `everything`: the whole table.
        """
        row = self.markers.currentRow()
        shown = self.review.labels()                       # every marker of the list; `labels` stays the ones that changed
        for index, label in enumerate(shown):
            self.markers.item(index).setText(self._marker_text(label))
        if 0 <= row < len(shown):
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
        if done and not uikit.confirm(self, i18n.t("review.exclude"), i18n.t("review.confirm_exclude_done", n=done),
                                      ok=i18n.t("review.exclude"), danger=True):
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
        menu = QtWidgets.QMenu(self.thumbs)
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
        if not uikit.confirm(self, i18n.t("review.accept"), i18n.t("review.confirm_accept", n=count), ok=i18n.t("review.accept")):
            return
        busy = BusyDialog(i18n.t("review.busy_validate", n=count), self)
        busy.open()
        try:
            done = review.accept_kept(self._state, self.review, busy.step)
            busy.set_text(i18n.t("review.busy_refresh"))
            busy.indeterminate()
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
