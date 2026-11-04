"""Review panel : fenetre de revue des pointes de l'IA (Qt).

A gauche les marqueurs qui ont des propositions, au milieu une vignette par photo :
un recadrage autour du point propose, avec une croix rouge sur ce point. Un clic
selectionne une vignette, un autre clic la deselectionne : on clique donc toutes les
mauvaises, puis « Exclure » (ou clic droit). « Affiner » ouvre, l'une apres l'autre,
chaque vignette selectionnee dans une grande fenetre : on clique le vrai centre,
Entree valide et passe a la suivante ; le point devient un pointage manuel et la
correction est gardee pour le futur reentrainement (voir review.py).

Le curseur +/- zoome dans les vignettes (plus ou moins de contexte autour du point).
Ctrl + molette agrandit ou reduit les vignettes elles-memes.

Chaque decision est appliquee tout de suite au projet. Aucun appel direct a Metashape :
tout passe par review.py et msapi.
"""

from ... import config, i18n, journal, msapi, theme
from ...qt import QShortcut, QtCore, QtGui, QtWidgets, modifiers
from . import photos, review

SECTION = "aigcp"
THUMB_KEY = "review_thumb"
ZOOM_KEY = "review_zoom"
DEFAULT_THUMB = 170
MIN_THUMB, MAX_THUMB, THUMB_STEP = 96, 360, 20
DEFAULT_ZOOM, MIN_ZOOM, MAX_ZOOM, ZOOM_STEP = 100, 50, 400, 25
REFINE_VIEW = 640
ROLE_ENTRY = QtCore.Qt.UserRole


def _styled_root(dialog):
    """Widget interne qui porte le fond et les styles du panneau.

    Comme dans le panneau FastGCP : sous Metashape, un QDialog ne peint pas le fond
    voulu, un QWidget interne si.
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


def _button(text_key, handler):
    button = QtWidgets.QPushButton(i18n.t(text_key))
    button.setObjectName("aeroGhost")
    button.setProperty("pill", True)
    button.setCursor(QtCore.Qt.PointingHandCursor)
    button.setFixedHeight(32)
    button.setMinimumWidth(96)
    button.clicked.connect(lambda *_args: handler())
    return button


def _cross(painter, x, y, arm, gap, color, width):
    """Croix a quatre branches autour de (x, y), avec un vide au centre pour voir la cible."""
    pen = QtGui.QPen(QtGui.QColor(*color))
    pen.setWidth(width)
    painter.setPen(pen)
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        painter.drawLine(int(x + dx * gap), int(y + dy * gap), int(x + dx * arm), int(y + dy * arm))


_RED = (255, 74, 46)
_GREEN = (60, 220, 120)
_DARK_RED = (235, 60, 60)


def _load(cache, path):
    pixmap = cache.get(path)
    if pixmap is None:
        pixmap = QtGui.QPixmap(path)
        cache[path] = pixmap
    return pixmap


def _paint_crop(painter, entry, size, zoom, cache):
    """Dessine dans le carre `size` le recadrage au zoom demande. Renvoie False sans vignette."""
    if not review.has_crop(entry):
        return False
    source = _load(cache, entry["big"])
    if source.isNull():
        return False
    left, top, side = review.source_rect(entry, zoom, source.width())
    painter.drawPixmap(QtCore.QRectF(0, 0, size, size), source, QtCore.QRectF(left, top, side, side))
    return True


def render_thumb(entry, size, zoom, cache):
    """Pastille carree de `size` px : recadrage, croix sur le point de l'IA, etat de la decision."""
    canvas = QtGui.QPixmap(size, size)
    canvas.fill(QtGui.QColor(45, 45, 45))
    painter = QtGui.QPainter(canvas)
    try:
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        if _paint_crop(painter, entry, size, zoom, cache):
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
        # Confiance en haut a gauche.
        painter.fillRect(0, 0, 38, 16, QtGui.QColor(0, 0, 0, 150))
        painter.setPen(QtGui.QColor(255, 255, 255))
        painter.drawText(4, 12, "{:.2f}".format(entry["conf"]))
        if entry["state"] == review.REMOVED:
            painter.fillRect(0, 0, size, size, QtGui.QColor(0, 0, 0, 150))
            pen = QtGui.QPen(QtGui.QColor(*_DARK_RED))
            pen.setWidth(4)
            painter.setPen(pen)
            painter.drawLine(size // 4, size // 4, 3 * size // 4, 3 * size // 4)
            painter.drawLine(3 * size // 4, size // 4, size // 4, 3 * size // 4)
        elif entry["state"] in (review.REFINED, review.ACCEPTED):
            pen = QtGui.QPen(QtGui.QColor(*_GREEN))
            pen.setWidth(5)
            painter.setPen(pen)
            painter.drawRect(2, 2, size - 4, size - 4)
    finally:
        painter.end()
    return canvas


class _ClickLabel(QtWidgets.QLabel):
    """Grande vignette : un clic (ou un glisse) pose le centre de la cible."""

    def __init__(self, pixmap, entry, zoom, on_point, parent=None):
        super(_ClickLabel, self).__init__(parent)
        self._entry = entry
        self._zoom = zoom
        self._on_point = on_point
        self._point = None
        self.setPixmap(pixmap)
        self.setFixedSize(pixmap.size())
        self.setCursor(QtCore.Qt.CrossCursor)

    @staticmethod
    def _pos(event):
        getter = getattr(event, "position", None)
        point = getter().toPoint() if getter is not None else event.pos()
        return float(point.x()), float(point.y())

    def _set(self, event):
        self._point = self._pos(event)
        self.update()
        self._on_point(*self._point)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self._set(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & QtCore.Qt.LeftButton:
            self._set(event)

    def paintEvent(self, event):
        super(_ClickLabel, self).paintEvent(event)
        painter = QtGui.QPainter(self)
        try:
            u, v = review.to_view(self._entry, self._entry["ai_x"], self._entry["ai_y"],
                                  REFINE_VIEW, self._zoom)
            _cross(painter, u, v, 26, 6, _RED, 2)
            if self._point is not None:
                _cross(painter, self._point[0], self._point[1], 26, 4, _GREEN, 2)
        finally:
            painter.end()


class RefineDialog(QtWidgets.QDialog):
    """Fenetre modale : l'operateur clique le vrai centre de la cible, Entree valide."""

    def __init__(self, entry, zoom, number, total, parent=None):
        super(RefineDialog, self).__init__(parent)
        self._entry = entry
        self._zoom = zoom
        self._chosen = None
        root = _styled_root(self)
        self.setWindowTitle(i18n.t("review.refine_title", label=entry["label"],
                                   camera=entry["camera_label"], n=number, total=total))
        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(theme.PAD_L, theme.PAD_L, theme.PAD_L, theme.PAD_L)
        layout.setSpacing(theme.PAD)
        hint = QtWidgets.QLabel(i18n.t("review.refine_hint"))
        hint.setObjectName("aeroMeta")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addWidget(_ClickLabel(self._render(), entry, zoom, self._on_point), 0, QtCore.Qt.AlignCenter)
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        self._cancel = self._make_button("review.refine_cancel", self.reject)
        self._ok = self._make_button("review.refine_ok", self.accept)
        self._ok.setDefault(True)
        self._ok.setEnabled(False)
        row.addWidget(self._cancel)
        row.addWidget(self._ok)
        layout.addLayout(row)

    def _render(self):
        canvas = QtGui.QPixmap(REFINE_VIEW, REFINE_VIEW)
        canvas.fill(QtGui.QColor(45, 45, 45))
        painter = QtGui.QPainter(canvas)
        try:
            painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
            _paint_crop(painter, self._entry, REFINE_VIEW, self._zoom, {})
        finally:
            painter.end()
        return canvas

    @staticmethod
    def _make_button(text_key, handler):
        button = QtWidgets.QPushButton(i18n.t(text_key))
        button.setObjectName("aeroGhost")
        button.setProperty("pill", True)
        button.setCursor(QtCore.Qt.PointingHandCursor)
        button.setFixedHeight(32)
        button.setMinimumWidth(110)
        button.setAutoDefault(True)
        button.clicked.connect(lambda *_args: handler())
        return button

    def _on_point(self, u, v):
        self._chosen = review.to_source(self._entry, u, v, REFINE_VIEW, self._zoom)
        self._ok.setEnabled(True)

    def chosen(self):
        """(x, y) en position de la photo, ou None."""
        return self._chosen


class _ThumbList(QtWidgets.QListWidget):
    """Liste de vignettes : Ctrl + molette change la taille des vignettes."""

    def __init__(self, on_resize, parent=None):
        super(_ThumbList, self).__init__(parent)
        self._on_resize = on_resize

    def wheelEvent(self, event):
        if modifiers(event)[0]:
            delta = event.angleDelta().y()
            if delta:
                self._on_resize(1 if delta > 0 else -1)
            event.accept()
            return
        super(_ThumbList, self).wheelEvent(event)


class ReviewDialog(QtWidgets.QDialog):
    """Marqueurs a gauche, vignettes au milieu, decisions en bas. Non modale."""

    def __init__(self, state, rev, parent=None):
        super(ReviewDialog, self).__init__(parent)
        self.review = rev
        self._state = state
        self._cache = {}
        self._thumb = thumb_size()
        self._zoom = zoom_percent() / 100.0
        self._items = {}
        root = _styled_root(self)
        self.setWindowTitle(i18n.t("review.title"))
        self.resize(1120, 740)

        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(theme.PAD_L, theme.PAD_L, theme.PAD_L, theme.PAD_L)
        layout.setSpacing(theme.PAD)

        top = QtWidgets.QHBoxLayout()
        hint = QtWidgets.QLabel(i18n.t("review.hint"))
        hint.setObjectName("aeroMeta")
        hint.setWordWrap(True)
        top.addWidget(hint, 1)
        self.zoom_out = self._zoom_button("−", -ZOOM_STEP)
        top.addWidget(self.zoom_out)
        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setObjectName("aeroSlider")
        self.slider.setRange(MIN_ZOOM, MAX_ZOOM)
        self.slider.setValue(int(round(self._zoom * 100)))
        self.slider.setFixedWidth(110)
        self.slider.valueChanged.connect(self._on_zoom)
        self.slider.sliderReleased.connect(self._save_zoom)
        top.addWidget(self.slider)
        self.zoom_in = self._zoom_button("+", ZOOM_STEP)
        top.addWidget(self.zoom_in)
        self.zoom_label = QtWidgets.QLabel()
        self.zoom_label.setObjectName("aeroMeta")
        self.zoom_label.setFixedWidth(44)
        top.addWidget(self.zoom_label)
        layout.addLayout(top)

        body = QtWidgets.QHBoxLayout()
        body.setSpacing(theme.PAD)
        self.markers = QtWidgets.QListWidget()
        self.markers.setObjectName("aeroThumbs")
        self.markers.setFixedWidth(190)
        self.markers.currentRowChanged.connect(self._on_marker)
        body.addWidget(self.markers)
        self.thumbs = _ThumbList(self._resize_thumbs)
        self.thumbs.setObjectName("aeroThumbs")
        self.thumbs.setViewMode(QtWidgets.QListView.IconMode)
        self.thumbs.setResizeMode(QtWidgets.QListView.Adjust)
        self.thumbs.setMovement(QtWidgets.QListView.Static)
        self.thumbs.setUniformItemSizes(True)
        self.thumbs.setWordWrap(True)
        self.thumbs.setSpacing(theme.PAD)
        # Un clic bascule la selection de la vignette : pas besoin de Ctrl pour en prendre plusieurs.
        self.thumbs.setSelectionMode(QtWidgets.QAbstractItemView.MultiSelection)
        self.thumbs.itemSelectionChanged.connect(self._update_buttons)
        self.thumbs.itemDoubleClicked.connect(self._on_double_click)
        self.thumbs.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.thumbs.customContextMenuRequested.connect(self._on_menu)
        body.addWidget(self.thumbs, 1)
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

        self._update_zoom_label()
        self._fill_markers()

    def _zoom_button(self, text, step):
        button = QtWidgets.QToolButton()
        button.setObjectName("aeroZoomButton")
        button.setText(text)
        button.setCursor(QtCore.Qt.PointingHandCursor)
        button.setFixedSize(26, 26)
        button.clicked.connect(lambda *_args: self.slider.setValue(self.slider.value() + step))
        return button

    # -- remplissage ---------------------------------------------------------

    def _marker_text(self, label):
        entries = self.review.entries_for(label)
        kept = sum(1 for e in entries if e["state"] in (review.KEPT, review.REFINED, review.ACCEPTED))
        return "{}   {}/{}".format(label, kept, len(entries))

    def _fill_markers(self):
        self.markers.clear()
        for label in self.review.labels():
            self.markers.addItem(self._marker_text(label))
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
        item.setIcon(QtGui.QIcon(render_thumb(entry, self._thumb, self._zoom, self._cache)))
        item.setText(entry["camera_label"].split("_", 2)[-1])
        item.setToolTip("{} · {:.2f}".format(entry["camera_label"], entry["conf"]))

    def _repaint_all(self):
        for item, entry in self._items.values():
            self._paint_item(item, entry)

    def _selected(self):
        out = []
        for item in self.thumbs.selectedItems():
            found = self._items.get(item.data(ROLE_ENTRY))
            if found is not None:
                out.append(found)
        return out

    # -- tailles et zoom ---------------------------------------------------------

    def _resize_thumbs(self, direction):
        """Ctrl + molette : vignettes plus grandes ou plus petites."""
        size = min(MAX_THUMB, max(MIN_THUMB, self._thumb + direction * THUMB_STEP))
        if size == self._thumb:
            return
        self._thumb = size
        self._grid()
        self._repaint_all()
        config.put(SECTION, THUMB_KEY, size)

    def _update_zoom_label(self):
        self.zoom_label.setText("{}%".format(int(round(self._zoom * 100))))

    def _on_zoom(self, value):
        """Curseur +/- : plus ou moins de contexte autour du point, dans chaque vignette."""
        self._zoom = value / 100.0
        self._update_zoom_label()
        self._repaint_all()
        if not self.slider.isSliderDown():
            self._save_zoom()

    def _save_zoom(self):
        config.put(SECTION, ZOOM_KEY, int(round(self._zoom * 100)))

    # -- etat des boutons -----------------------------------------------------------

    def _update_buttons(self):
        picked = [entry for _item, entry in self._selected()]
        self.exclude_button.setEnabled(any(e["state"] == review.KEPT for e in picked))
        self.restore_button.setEnabled(any(e["state"] == review.REMOVED for e in picked))
        self.refine_button.setEnabled(any(
            review.has_crop(e) and e["state"] in (review.KEPT, review.REMOVED) for e in picked))
        self.validate_button.setEnabled(any(e["state"] == review.KEPT for e in self.review.entries))

    def _update_summary(self):
        counts = self.review.counts()
        self.summary.setText(i18n.t(
            "review.summary", kept=counts[review.KEPT], removed=counts[review.REMOVED],
            refined=counts[review.REFINED], accepted=counts[review.ACCEPTED]))

    def _after_change(self):
        row = self.markers.currentRow()
        labels = self.review.labels()
        for index, label in enumerate(labels):
            self.markers.item(index).setText(self._marker_text(label))
        if 0 <= row < len(labels):
            self.markers.setCurrentRow(row)
        self._update_summary()
        self._update_buttons()
        for method in ("update_aigcp", "update_active_card", "refresh_errors"):
            photos.panel_call(self._state, method)

    # -- decisions -----------------------------------------------------------------------

    def _apply(self, action):
        changed = 0
        for item, entry in self._selected():
            try:
                if action(self._state, self.review, entry):
                    changed += 1
                    self._paint_item(item, entry)
            except Exception as exc:
                journal.write("revue IA : {!r}".format(exc))
        if changed:
            self.thumbs.clearSelection()
            self._after_change()

    def _on_exclude(self):
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
        """Ouvre chaque vignette dans la fenetre d'affinage, l'une apres l'autre."""
        pairs = [(i, e) for i, e in pairs
                 if review.has_crop(e) and e["state"] in (review.KEPT, review.REMOVED)]
        done = 0
        for number, (item, entry) in enumerate(pairs, 1):
            try:
                dialog = RefineDialog(entry, max(1.0, self._zoom), number, len(pairs), self)
                if not _exec(dialog) or dialog.chosen() is None:
                    break
                x, y = dialog.chosen()
                if review.refine(self._state, self.review, entry, x, y):
                    self._paint_item(item, entry)
                    done += 1
            except Exception as exc:
                journal.write("revue IA : affinage : {!r}".format(exc))
                break
        if done:
            self.thumbs.clearSelection()
            self._after_change()

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
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle(i18n.t("review.accept"))
        box.setText(i18n.t("review.confirm_accept", n=count))
        box.setStandardButtons(QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        if _exec(box) != QtWidgets.QMessageBox.Yes:
            return
        done = review.accept_kept(self._state, self.review)
        photos.panel_call(self._state, "set_status", i18n.t("review.accepted_status", n=done))
        self._fill_thumbs()
        self._after_change()


def show(state, parent=None, existing=None):
    """Ouvre (ou ramene devant) le Review panel de la derniere detection. Renvoie la fenetre."""
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
    """Ferme la fenetre si elle existe encore. Ne leve jamais."""
    try:
        if dialog is not None:
            dialog.close()
    except Exception:
        pass
