"""Fenetre de revue des pointes de l'IA (Qt).

A gauche les marqueurs qui ont des propositions, au milieu une vignette par photo :
un recadrage autour du point propose, avec une croix sur ce point. On repere d'un
coup d'oeil les mauvais pointes, on les selectionne (clic, Ctrl / Maj, rectangle) et
on les retire ; un double-clic ouvre une fenetre qui permet d'affiner : on clique le
vrai centre, le point devient un pointage manuel et la correction est gardee pour le
futur reentrainement (voir review.py). Le curseur change la taille des vignettes.

Chaque decision est appliquee tout de suite au projet. Aucun appel direct a Metashape :
tout passe par review.py et msapi.
"""

import functools

from ... import config, i18n, journal, msapi, theme
from ...qt import QShortcut, QtCore, QtGui, QtWidgets
from . import photos, review

SECTION = "aigcp"
THUMB_KEY = "review_thumb"
DEFAULT_THUMB = 170
MIN_THUMB, MAX_THUMB = 96, 360
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


def _exec(dialog):
    run = getattr(dialog, "exec_", None) or getattr(dialog, "exec")
    return run()


def thumb_size():
    value = config.get(SECTION, THUMB_KEY)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return DEFAULT_THUMB
    return int(min(MAX_THUMB, max(MIN_THUMB, value)))


def _cross(painter, x, y, arm, gap, color, width):
    """Croix a quatre branches autour de (x, y), avec un vide au centre pour voir la cible."""
    pen = QtGui.QPen(QtGui.QColor(*color))
    pen.setWidth(width)
    painter.setPen(pen)
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        painter.drawLine(int(x + dx * gap), int(y + dy * gap), int(x + dx * arm), int(y + dy * arm))


_ORANGE = (255, 150, 30)
_GREEN = (60, 220, 120)
_RED = (235, 60, 60)


def _load(cache, path):
    pixmap = cache.get(path)
    if pixmap is None:
        pixmap = QtGui.QPixmap(path)
        cache[path] = pixmap
    return pixmap


def render_thumb(entry, size, cache):
    """Pastille carree de `size` px : recadrage, croix sur le point de l'IA, etat de la decision."""
    canvas = QtGui.QPixmap(size, size)
    canvas.fill(QtGui.QColor(55, 55, 55))
    painter = QtGui.QPainter(canvas)
    try:
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        if review.has_crop(entry):
            source = _load(cache, entry["thumb"])
            if not source.isNull():
                painter.drawPixmap(0, 0, source.scaled(
                    size, size, QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation))
                u, v = review.to_view(entry, entry["ai_x"], entry["ai_y"], size)
                arm, gap = max(8, size // 7), max(3, size // 40)
                _cross(painter, u, v, arm, gap, _ORANGE, 2)
                if entry["state"] == review.REFINED:
                    u, v = review.to_view(entry, entry["x"], entry["y"], size)
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
            pen = QtGui.QPen(QtGui.QColor(*_RED))
            pen.setWidth(4)
            painter.setPen(pen)
            painter.drawLine(size // 4, size // 4, 3 * size // 4, 3 * size // 4)
            painter.drawLine(3 * size // 4, size // 4, size // 4, 3 * size // 4)
        elif entry["state"] in (review.REFINED, review.ACCEPTED):
            pen = QtGui.QPen(QtGui.QColor(*_GREEN))
            pen.setWidth(4)
            painter.setPen(pen)
            painter.drawRect(2, 2, size - 4, size - 4)
    finally:
        painter.end()
    return canvas


class _ClickLabel(QtWidgets.QLabel):
    """Grande vignette : un clic (ou un glisse) pose le centre de la cible."""

    def __init__(self, pixmap, entry, on_point, parent=None):
        super(_ClickLabel, self).__init__(parent)
        self._entry = entry
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
            u, v = review.to_view(self._entry, self._entry["ai_x"], self._entry["ai_y"], review.BIG_SIZE)
            _cross(painter, u, v, 26, 6, _ORANGE, 2)
            if self._point is not None:
                _cross(painter, self._point[0], self._point[1], 26, 4, _GREEN, 2)
        finally:
            painter.end()


class RefineDialog(QtWidgets.QDialog):
    """Fenetre modale : l'operateur clique le vrai centre de la cible."""

    def __init__(self, entry, parent=None):
        super(RefineDialog, self).__init__(parent)
        self._entry = entry
        self._chosen = None
        root = _styled_root(self)
        self.setWindowTitle(i18n.t("review.refine_title", label=entry["label"], camera=entry["camera_label"]))
        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(theme.PAD_L, theme.PAD_L, theme.PAD_L, theme.PAD_L)
        layout.setSpacing(theme.PAD)
        hint = QtWidgets.QLabel(i18n.t("review.refine_hint"))
        hint.setObjectName("aeroMeta")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        pixmap = QtGui.QPixmap(entry["big"])
        layout.addWidget(_ClickLabel(pixmap, entry, self._on_point), 0, QtCore.Qt.AlignCenter)
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        self._cancel = self._button("review.refine_cancel", self.reject)
        self._ok = self._button("review.refine_ok", self.accept)
        self._ok.setEnabled(False)
        row.addWidget(self._cancel)
        row.addWidget(self._ok)
        layout.addLayout(row)

    @staticmethod
    def _button(text_key, handler):
        button = QtWidgets.QPushButton(i18n.t(text_key))
        button.setObjectName("aeroGhost")
        button.setProperty("pill", True)
        button.setCursor(QtCore.Qt.PointingHandCursor)
        button.setFixedHeight(32)
        button.setMinimumWidth(110)
        button.clicked.connect(handler)
        return button

    def _on_point(self, u, v):
        self._chosen = review.to_source(self._entry, u, v, review.BIG_SIZE)
        self._ok.setEnabled(True)

    def chosen(self):
        """(x, y) en position de la photo, ou None."""
        return self._chosen


class ReviewDialog(QtWidgets.QDialog):
    """Marqueurs a gauche, vignettes au milieu, decisions en bas. Non modale."""

    def __init__(self, state, rev, parent=None):
        super(ReviewDialog, self).__init__(parent)
        self.review = rev
        self._state = state
        self._cache = {}
        self._thumb = thumb_size()
        self._items = {}
        root = _styled_root(self)
        self.setWindowTitle(i18n.t("review.title", kind=rev.kind))
        self.resize(1120, 740)

        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(theme.PAD_L, theme.PAD_L, theme.PAD_L, theme.PAD_L)
        layout.setSpacing(theme.PAD)

        top = QtWidgets.QHBoxLayout()
        hint = QtWidgets.QLabel(i18n.t("review.hint"))
        hint.setObjectName("aeroMeta")
        hint.setWordWrap(True)
        top.addWidget(hint, 1)
        top.addWidget(QtWidgets.QLabel("−"))
        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setObjectName("aeroSlider")
        self.slider.setRange(MIN_THUMB, MAX_THUMB)
        self.slider.setValue(self._thumb)
        self.slider.setFixedWidth(180)
        self.slider.valueChanged.connect(self._on_zoom)
        self.slider.sliderReleased.connect(self._save_zoom)
        top.addWidget(self.slider)
        top.addWidget(QtWidgets.QLabel("+"))
        layout.addLayout(top)

        body = QtWidgets.QHBoxLayout()
        body.setSpacing(theme.PAD)
        self.markers = QtWidgets.QListWidget()
        self.markers.setObjectName("aeroThumbs")
        self.markers.setFixedWidth(190)
        self.markers.currentRowChanged.connect(self._on_marker)
        body.addWidget(self.markers)
        self.thumbs = QtWidgets.QListWidget()
        self.thumbs.setObjectName("aeroThumbs")
        self.thumbs.setViewMode(QtWidgets.QListView.IconMode)
        self.thumbs.setResizeMode(QtWidgets.QListView.Adjust)
        self.thumbs.setMovement(QtWidgets.QListView.Static)
        self.thumbs.setUniformItemSizes(True)
        self.thumbs.setWordWrap(True)
        self.thumbs.setSpacing(theme.PAD)
        self.thumbs.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.thumbs.itemDoubleClicked.connect(lambda _item: self._on_refine())
        body.addWidget(self.thumbs, 1)
        layout.addLayout(body, 1)

        bottom = QtWidgets.QHBoxLayout()
        bottom.setSpacing(theme.PAD)
        self.remove_button = self._button("review.remove", self._on_remove)
        self.restore_button = self._button("review.restore", self._on_restore)
        self.refine_button = self._button("review.refine", self._on_refine)
        for button in (self.remove_button, self.restore_button, self.refine_button):
            bottom.addWidget(button)
        bottom.addStretch(1)
        self.summary = QtWidgets.QLabel()
        self.summary.setObjectName("aeroMeta")
        bottom.addWidget(self.summary)
        self.accept_button = self._button("review.accept", self._on_accept)
        self.close_button = self._button("review.close", self.close)
        bottom.addWidget(self.accept_button)
        bottom.addWidget(self.close_button)
        layout.addLayout(bottom)

        for key, handler in (("Delete", self._on_remove), ("Return", self._on_refine)):
            shortcut = QShortcut(QtGui.QKeySequence(key), self)
            shortcut.setContext(QtCore.Qt.WindowShortcut)
            shortcut.activated.connect(handler)

        self._fill_markers()

    @staticmethod
    def _button(text_key, handler):
        button = QtWidgets.QPushButton(i18n.t(text_key))
        button.setObjectName("aeroGhost")
        button.setProperty("pill", True)
        button.setCursor(QtCore.Qt.PointingHandCursor)
        button.setFixedHeight(32)
        button.setMinimumWidth(96)
        button.clicked.connect(functools.partial(_ignore_args, handler))
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

    def _current_label(self):
        row = self.markers.currentRow()
        labels = self.review.labels()
        return labels[row] if 0 <= row < len(labels) else None

    def _on_marker(self, _row):
        self._fill_thumbs()

    def _fill_thumbs(self):
        self.thumbs.clear()
        self._items = {}
        size = self._thumb
        self.thumbs.setIconSize(QtCore.QSize(size, size))
        self.thumbs.setGridSize(QtCore.QSize(size + 16, size + 40))
        label = self._current_label()
        if label is None:
            return
        for entry in self.review.entries_for(label):
            item = QtWidgets.QListWidgetItem()
            item.setData(ROLE_ENTRY, id(entry))
            self.thumbs.addItem(item)
            self._items[id(entry)] = (item, entry)
            self._paint_item(item, entry)

    def _paint_item(self, item, entry):
        item.setIcon(QtGui.QIcon(render_thumb(entry, self._thumb, self._cache)))
        item.setText(entry["camera_label"].split("_", 2)[-1])
        item.setToolTip("{} · {:.2f}".format(entry["camera_label"], entry["conf"]))

    def _selected(self):
        out = []
        for item in self.thumbs.selectedItems():
            found = self._items.get(item.data(ROLE_ENTRY))
            if found is not None:
                out.append(found)
        return out

    # -- zoom ------------------------------------------------------------------

    def _on_zoom(self, value):
        self._thumb = int(value)
        self.thumbs.setIconSize(QtCore.QSize(self._thumb, self._thumb))
        self.thumbs.setGridSize(QtCore.QSize(self._thumb + 16, self._thumb + 40))
        for item, entry in self._items.values():
            self._paint_item(item, entry)

    def _save_zoom(self):
        config.put(SECTION, THUMB_KEY, self._thumb)

    # -- decisions -------------------------------------------------------------

    def _after_change(self):
        row = self.markers.currentRow()
        labels = self.review.labels()
        for index, label in enumerate(labels):
            self.markers.item(index).setText(self._marker_text(label))
        if 0 <= row < len(labels):
            self.markers.setCurrentRow(row)
        self._update_summary()
        for method in ("update_aigcp", "update_active_card", "refresh_errors"):
            photos.panel_call(self._state, method)

    def _update_summary(self):
        counts = self.review.counts()
        self.summary.setText(i18n.t(
            "review.summary", kept=counts[review.KEPT], removed=counts[review.REMOVED],
            refined=counts[review.REFINED], accepted=counts[review.ACCEPTED]))

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
            self._after_change()

    def _on_remove(self):
        self._apply(review.remove)

    def _on_restore(self):
        self._apply(review.restore)

    def _on_refine(self):
        picked = self._selected()
        if len(picked) != 1:
            return
        item, entry = picked[0]
        if not review.has_crop(entry):
            return
        try:
            dialog = RefineDialog(entry, self)
            if not _exec(dialog) or dialog.chosen() is None:
                return
            x, y = dialog.chosen()
            if review.refine(self._state, self.review, entry, x, y):
                self._paint_item(item, entry)
                self._after_change()
        except Exception as exc:
            journal.write("revue IA : affinage : {!r}".format(exc))

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


def _ignore_args(handler, *_args):
    handler()


def show(state, parent=None, existing=None):
    """Ouvre (ou ramene devant) la fenetre de revue de la derniere detection. Renvoie la fenetre."""
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
