"""Aero-Facade: the widgets of the wizard.

This module holds the picture and the lists:

* `OrthoCanvas`      the orthomosaic picture with wheel zoom, middle / right drag pan, the polygon tool of the search area
                     and the overlay of the façades (colour, letter, hover, selection);
* `FacadeList`       the detected façades as rows (swatch, letter, length, height, facing), multi-select, Remove, Undo;
* `BatchProgress`    waiting / running / done / failed per façade, the "please wait" banner, Cancel and Retry;
* `OrthoThumbnails`  the built orthophotos with a check box each (export screen).

The margin editor and the method picker are in `widgets_margins.py` and re-exported here, so `widgets.MarginEditor` and
`widgets.MethodPicker` work too. The pure helpers (`PictureFrame`, `polygon_problem`, `RemovalLog`...) have no Qt in them and are
tested without a display. Texts live in the language catalogs under `facade.ui.`.
"""

import math

from ... import theme, ui
from ...qt import QtCore, QtGui, QtWidgets, modifiers
from ..aeroexports import controls
from .common import POLY_KEYS, STATE_KEYS, dir_key, metres, qt_shortcut, readable_on, tr

MIN_AREA = 1e-9
CLOSE_PX = 14                                         # a click this close to the first point closes the polygon
HIT_PX = 11                                           # a click this close to a façade line selects it
DRAG_PX = 4                                           # a press that moves less than this is a click, not a drag
ZOOM_STEP = 1.25
STATE_WAITING, STATE_RUNNING, STATE_DONE, STATE_FAILED, STATE_CANCELLED = "waiting", "running", "done", "failed", "cancelled"


# --------------------------------------------------------------------------------------------------
# Pure helpers (no Qt): picture frame, polygon check, removal log, colours
# --------------------------------------------------------------------------------------------------

class PictureFrame(object):
    """Maps picture pixels (x right, y down, origin top-left) to metres of the extent (left, bottom, right, top) and back."""

    def __init__(self, width, height, extent):
        self.width = max(1, int(width))
        self.height = max(1, int(height))
        left, bottom, right, top = [float(v) for v in extent]
        self.left, self.bottom, self.right, self.top = left, bottom, right, top

    @property
    def metres_per_pixel(self):
        return (self.right - self.left) / float(self.width)

    def to_world(self, px, py):
        return (self.left + px / float(self.width) * (self.right - self.left),
                self.top - py / float(self.height) * (self.top - self.bottom))

    def to_pixel(self, x, y):
        return ((x - self.left) / (self.right - self.left) * self.width,
                (self.top - y) / (self.top - self.bottom) * self.height)


def segment_distance(point, a, b):
    """Distance from `point` to the segment a-b (all (x, y))."""
    px, py = point
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    if length2 <= 1e-12:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _orient(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def segments_cross(a, b, c, d):
    """True when the open segments a-b and c-d properly cross."""
    o1, o2, o3, o4 = _orient(a, b, c), _orient(a, b, d), _orient(c, d, a), _orient(c, d, b)
    return ((o1 > 0) != (o2 > 0)) and ((o3 > 0) != (o4 > 0)) and 0 not in (o1, o2, o3, o4)


def polygon_area(points):
    total = 0.0
    for index, (x0, y0) in enumerate(points):
        x1, y1 = points[(index + 1) % len(points)]
        total += x0 * y1 - x1 * y0
    return abs(total) / 2.0


def polygon_problem(points):
    """Why a closed outline cannot be used: "few" (under 3 corners), "flat" (no surface), "crossing" (it crosses itself), or ""."""
    points = [(float(x), float(y)) for x, y in points]
    if len(points) < 3:
        return "few"
    count = len(points)
    for i in range(count):
        for j in range(i + 1, count):
            if j == i + 1 or (i == 0 and j == count - 1):
                continue                                             # edges that share a corner
            if segments_cross(points[i], points[(i + 1) % count], points[j], points[(j + 1) % count]):
                return "crossing"
    if polygon_area(points) <= MIN_AREA:
        return "flat"
    return ""


class RemovalLog(object):
    """What the operator removed from the façade list, in groups, so that Undo brings the last group back."""

    def __init__(self):
        self._groups = []

    def push(self, letters):
        letters = list(letters)
        if letters:
            self._groups.append(letters)

    def pop(self):
        return self._groups.pop() if self._groups else []

    def can_undo(self):
        return bool(self._groups)

    def clear(self):
        del self._groups[:]


def array_to_qimage(array):
    """QImage (a copy, it owns its data) of a numpy uint8 array H x W x 3 (RGB), H x W x 4 (RGBA) or H x W (grey)."""
    import numpy as np
    data = np.asarray(array)
    if data.dtype != np.uint8:
        data = np.clip(data, 0, 255).astype(np.uint8)
    if data.ndim == 2:
        data = np.stack([data, data, data], axis=-1)
    data = np.ascontiguousarray(data)
    height, width, depth = data.shape[0], data.shape[1], data.shape[2]
    fmt = QtGui.QImage.Format_RGBA8888 if depth == 4 else QtGui.QImage.Format_RGB888
    return QtGui.QImage(data.data, width, height, width * depth, fmt).copy()


def event_pos(event):
    """Position of a mouse or wheel event in its widget, as a QPointF, Qt5 and Qt6."""
    getter = getattr(event, "position", None)
    if getter is not None:
        try:
            return QtCore.QPointF(getter())
        except Exception:
            pass
    return QtCore.QPointF(event.pos())


def _is_button(event, button):
    return event.button() == button


# --------------------------------------------------------------------------------------------------
# Drawing items
# --------------------------------------------------------------------------------------------------

class _Badge(QtWidgets.QGraphicsItem):
    """The round letter label of a façade. It ignores the view's zoom (always the same size on screen) and sits `offset` pixels
    from its anchor point."""

    RADIUS = 12

    def __init__(self, letter, colour):
        super(_Badge, self).__init__()
        self.letter = letter
        self.colour = QtGui.QColor(colour)
        self.offset = QtCore.QPointF(0, 0)
        self.hot = False
        self.setFlag(QtWidgets.QGraphicsItem.ItemIgnoresTransformations, True)

    def boundingRect(self):
        r = self.RADIUS + 4
        return QtCore.QRectF(self.offset.x() - r - 6, self.offset.y() - r, 2 * r + 12, 2 * r)

    def paint(self, painter, _option, _widget=None):
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        centre = self.offset
        radius = self.RADIUS + (2 if self.hot else 0)
        painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255) if self.hot else QtGui.QColor(0, 0, 0, 170), 2))
        painter.setBrush(self.colour)
        painter.drawEllipse(centre, radius, radius)
        font = QtGui.QFont(painter.font())
        font.setBold(True)
        font.setPointSizeF(max(7.0, font.pointSizeF() * (0.9 if len(self.letter) == 1 else 0.7)))
        painter.setFont(font)
        painter.setPen(QtGui.QColor(readable_on(self.colour.name())))
        painter.drawText(QtCore.QRectF(centre.x() - radius, centre.y() - radius, 2 * radius, 2 * radius), QtCore.Qt.AlignCenter,
                         self.letter)


class _FacadeMark(object):
    """The three scene items of one façade: dark halo, coloured line, letter badge."""

    def __init__(self, scene, facade, frame):
        self.letter = facade.letter
        self.colour = facade.colour
        (x0, y0), (x1, y1) = facade.polyline_xy()
        self.a = QtCore.QPointF(*frame.to_pixel(x0, y0))
        self.b = QtCore.QPointF(*frame.to_pixel(x1, y1))
        self.halo = QtWidgets.QGraphicsLineItem(QtCore.QLineF(self.a, self.b))
        self.line = QtWidgets.QGraphicsLineItem(QtCore.QLineF(self.a, self.b))
        self.badge = _Badge(facade.letter, facade.colour)
        mid = QtCore.QPointF((self.a.x() + self.b.x()) / 2.0, (self.a.y() + self.b.y()) / 2.0)
        self.badge.setPos(mid)
        # the badge stands on the side the wall faces (its normal), so the operator sees which side is the front
        nx, ny = getattr(facade, "nx", 0.0), getattr(facade, "ny", 0.0)
        norm = math.hypot(nx, ny)
        if norm > 1e-9:
            self.badge.offset = QtCore.QPointF(nx / norm * 22.0, -ny / norm * 22.0)
        for item, z in ((self.halo, 10), (self.line, 11), (self.badge, 12)):
            item.setZValue(z)
            scene.addItem(item)
        self.style(False, False)

    def remove(self, scene):
        for item in (self.halo, self.line, self.badge):
            scene.removeItem(item)

    def style(self, selected, hover):
        halo = QtGui.QPen(QtGui.QColor(255, 255, 255, 235) if selected else QtGui.QColor(0, 0, 0, 150), 11 if selected else 8)
        line = QtGui.QPen(QtGui.QColor(self.colour), 6 if (selected or hover) else 4)
        for pen in (halo, line):
            pen.setCosmetic(True)
            pen.setCapStyle(QtCore.Qt.RoundCap)
        self.halo.setPen(halo)
        self.line.setPen(line)
        self.badge.hot = bool(selected or hover)
        self.badge.update()


class OrthoCanvas(QtWidgets.QGraphicsView):
    """The orthomosaic picture. Wheel = zoom at the cursor, middle or right button drag = pan (left drag too when nothing is being drawn).

    Search area: while `set_draw_enabled(True)` and no outline is closed, each click adds a corner; a double click, or a click on
    the first corner, closes the outline; Esc drops the outline being drawn; Delete clears it so it can be drawn again.
    Façades (`set_facades`) are drawn as coloured lines with their letter; click, hover and selection are reported by signals.

    Signals: `polygon_changed(list or None)` corners in metres of the extent, `drawing_changed(bool)`, `facade_clicked(letter,
    ctrl, shift)` ("" when the click hit nothing), `facade_hovered(letter)`, `message(text)`."""

    polygon_changed = QtCore.Signal(object)
    drawing_changed = QtCore.Signal(bool)
    facade_clicked = QtCore.Signal(str, bool, bool)
    facade_hovered = QtCore.Signal(str)
    message = QtCore.Signal(str)
    corners_changed = QtCore.Signal(int)
    command = QtCore.Signal(str)                       # "delete" or "undo": keys pressed on the picture when no outline is drawn

    def __init__(self, parent=None):
        super(OrthoCanvas, self).__init__(parent)
        self._scene = QtWidgets.QGraphicsScene(self)
        self.setScene(self._scene)
        self.setObjectName("facadeCanvas")
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setBackgroundBrush(QtGui.QColor(theme.GROUND_0))
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setRenderHint(QtGui.QPainter.Antialiasing, True)
        self.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QtWidgets.QGraphicsView.NoAnchor)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setMinimumSize(320, 240)
        self._image_item = None
        self._frame = None
        self._size = (0, 0)
        self._fit_scale = 1.0
        self._user_zoomed = False
        self._hint = ""
        self._empty = tr("facade.ui.canvas_loading")
        self._last_count = 0
        self._draw_enabled = False
        self._points = []                              # corners of the outline, in scene (picture pixel) coordinates
        self._closed = False
        self._cursor = None                            # scene position of the cursor while drawing
        self._poly_items = []
        self._marks = {}
        self._selected = set()
        self._hover = ""
        self._press = None                             # (view pos, button, moved) while a button is down
        self._panning = False
        self._skip_release = False
        self._build_zoom_buttons()

    # -- the picture ------------------------------------------------------------------------------

    def set_picture(self, rgb, extent):
        """Show the picture `rgb` (numpy H x W x 3, or a QImage) whose corners are `extent` = (left, bottom, right, top) metres."""
        image = rgb if isinstance(rgb, QtGui.QImage) else array_to_qimage(rgb)
        width, height = image.width(), image.height()
        if self._image_item is not None:
            self._scene.removeItem(self._image_item)
        self._image_item = self._scene.addPixmap(QtGui.QPixmap.fromImage(image))
        self._image_item.setTransformationMode(QtCore.Qt.SmoothTransformation)
        self._image_item.setZValue(0)
        self._size = (width, height)
        self._frame = PictureFrame(width, height, extent)
        margin = max(width, height)
        self._scene.setSceneRect(QtCore.QRectF(-margin, -margin, width + 2 * margin, height + 2 * margin))
        self._user_zoomed = False
        self.fit_all()
        self.viewport().update()

    def has_picture(self):
        return self._image_item is not None

    def frame(self):
        return self._frame

    def set_hint(self, text):
        """Short help written on the picture (bottom centre); empty text hides it."""
        self._hint = text or ""
        self.viewport().update()

    def set_empty_text(self, text):
        """What the empty canvas says (loading, or why there is no picture)."""
        self._empty = text or ""
        self.viewport().update()

    def drawForeground(self, painter, _rect):
        painter.save()
        painter.resetTransform()
        area = QtCore.QRectF(self.viewport().rect())
        if not self._image_item:
            self._paint_box(painter, area, self._empty, center=True)
        elif self._hint:
            self._paint_box(painter, area, self._hint, center=False)
        painter.restore()

    def _paint_box(self, painter, area, text, center):
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        font = QtGui.QFont(self.font())
        font.setPointSizeF(font.pointSizeF() * 1.1)
        painter.setFont(font)
        width = min(area.width() - 24, 560)
        flags = QtCore.Qt.AlignCenter | QtCore.Qt.TextWordWrap
        text_rect = painter.fontMetrics().boundingRect(QtCore.QRect(0, 0, int(width - 28), 2000), flags, text)
        box = QtCore.QRectF(0, 0, text_rect.width() + 28, text_rect.height() + 20)
        if center:
            box.moveCenter(area.center())
        else:
            box.moveCenter(QtCore.QPointF(area.center().x(), area.bottom() - box.height() / 2.0 - 14))
        painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 40), 1))
        painter.setBrush(QtGui.QColor(20, 20, 20, 215))
        painter.drawRoundedRect(box, 10, 10)
        painter.setPen(QtGui.QColor(theme.FG_0))
        painter.drawText(box.adjusted(14, 10, -14, -10), flags, text)

    # -- zoom and pan -----------------------------------------------------------------------------

    def _build_zoom_buttons(self):
        self._buttons = []
        for text, tip, handler in (("+", "facade.ui.zoom_in", lambda: self.zoom_by(ZOOM_STEP)),
                                   ("−", "facade.ui.zoom_out", lambda: self.zoom_by(1.0 / ZOOM_STEP)),
                                   ("⤢", "facade.ui.zoom_fit", self.fit_all)):
            button = QtWidgets.QToolButton(self)
            button.setObjectName("aeroZoomButton")
            button.setText(text)
            button.setToolTip(tr(tip))
            button.setCursor(QtCore.Qt.PointingHandCursor)
            button.setFocusPolicy(QtCore.Qt.NoFocus)
            button.clicked.connect(lambda *_a, h=handler: h())
            self._buttons.append(button)

    def _place_buttons(self):
        size = max(30, self._buttons[0].sizeHint().height())
        x = self.width() - size - 10
        for index, button in enumerate(self._buttons):
            button.setFixedSize(size, size)
            button.move(x, 10 + index * (size + 6))
            button.raise_()

    def resizeEvent(self, event):
        super(OrthoCanvas, self).resizeEvent(event)
        if self._image_item is not None and not self._user_zoomed:
            self.fit_all()
        self._place_buttons()

    def showEvent(self, event):
        super(OrthoCanvas, self).showEvent(event)
        self._place_buttons()
        if self._image_item is not None and not self._user_zoomed:
            self.fit_all()

    def scale_now(self):
        return self.transform().m11()

    def fit_all(self):
        if self._image_item is None:
            return
        self.fitInView(self._image_item, QtCore.Qt.KeepAspectRatio)
        self._fit_scale = self.scale_now()
        self._user_zoomed = False

    def zoom_by(self, factor, anchor=None):
        if self._image_item is None:
            return
        current = self.scale_now()
        target = max(self._fit_scale * 0.5, min(current * factor, 14.0))
        factor = target / current if current else 1.0
        if abs(factor - 1.0) < 1e-6:
            return
        self._user_zoomed = True
        if anchor is None:
            self.setTransformationAnchor(QtWidgets.QGraphicsView.AnchorViewCenter)
            self.scale(factor, factor)
            self.setTransformationAnchor(QtWidgets.QGraphicsView.AnchorUnderMouse)
        else:
            self.scale(factor, factor)

    def wheelEvent(self, event):
        steps = event.angleDelta().y() / 120.0
        if steps:
            self.zoom_by(ZOOM_STEP ** steps, anchor=True)
        event.accept()

    def zoom_to_world(self, rect, pad=0.25):
        """Zoom on `rect` = (x0, y0, x1, y1) in metres, with `pad` (fraction) room around it."""
        if self._frame is None:
            return
        x0, y0, x1, y1 = rect
        a = self._frame.to_pixel(min(x0, x1), max(y0, y1))
        b = self._frame.to_pixel(max(x0, x1), min(y0, y1))
        target = QtCore.QRectF(QtCore.QPointF(*a), QtCore.QPointF(*b)).normalized()
        grow = max(target.width(), target.height(), 20.0) * pad
        target.adjust(-grow, -grow, grow, grow)
        self._user_zoomed = True
        self.fitInView(target, QtCore.Qt.KeepAspectRatio)

    def zoom_to_facades(self, letters):
        points = []
        for letter in letters:
            mark = self._marks.get(letter)
            if mark is not None:
                points.extend([mark.a, mark.b])
        if not points or self._frame is None:
            return
        xs, ys = [p.x() for p in points], [p.y() for p in points]
        grow = 30.0
        rect = QtCore.QRectF(min(xs) - grow, min(ys) - grow, max(xs) - min(xs) + 2 * grow, max(ys) - min(ys) + 2 * grow)
        self._user_zoomed = True
        self.fitInView(rect, QtCore.Qt.KeepAspectRatio)

    # -- the search area (polygon) ----------------------------------------------------------------

    def set_draw_enabled(self, enabled):
        self._draw_enabled = bool(enabled)
        self.setCursor(QtCore.Qt.CrossCursor if self._draw_enabled and not self._closed else QtCore.Qt.ArrowCursor)
        self.drawing_changed.emit(self.is_drawing())
        self._refresh_polygon()

    def draw_enabled(self):
        return self._draw_enabled

    def is_drawing(self):
        """True while corners are being placed (the tool is on and the outline is not closed)."""
        return self._draw_enabled and not self._closed

    def is_closed(self):
        return self._closed

    def corner_count(self):
        return len(self._points)

    def polygon(self):
        """Corners of the closed outline in metres of the extent, or None while there is none."""
        if not self._closed or self._frame is None:
            return None
        return [self._frame.to_world(p.x(), p.y()) for p in self._points]

    def polygon_problem(self):
        polygon = self.polygon()
        return polygon_problem(polygon) if polygon is not None else "few"

    def set_polygon(self, world_points):
        """Show a closed outline given in metres (None or [] removes it)."""
        self._points = []
        self._closed = False
        if world_points and self._frame is not None:
            self._points = [QtCore.QPointF(*self._frame.to_pixel(x, y)) for x, y in world_points]
            self._closed = len(self._points) >= 3
        self._cursor = None
        self._refresh_polygon()
        self.drawing_changed.emit(self.is_drawing())

    def clear_polygon(self):
        """Delete: remove the outline (or the one being drawn) so a new one can be drawn."""
        had = bool(self._points)
        self._points = []
        self._closed = False
        self._cursor = None
        self._refresh_polygon()
        self.setCursor(QtCore.Qt.CrossCursor if self._draw_enabled else QtCore.Qt.ArrowCursor)
        self.drawing_changed.emit(self.is_drawing())
        if had:
            self.polygon_changed.emit(None)

    def cancel_drawing(self):
        """Esc: drop the corners placed so far (a closed outline stays)."""
        if self._closed or not self._points:
            return False
        self._points = []
        self._cursor = None
        self._refresh_polygon()
        return True

    def _close_polygon(self):
        if self._closed or len(self._points) < 3:
            return
        self._closed = True
        self._cursor = None
        self.setCursor(QtCore.Qt.ArrowCursor)
        self._refresh_polygon()
        self.drawing_changed.emit(False)
        self.polygon_changed.emit(self.polygon())
        problem = self.polygon_problem()
        if problem:
            self.message.emit(tr(POLY_KEYS[problem]))

    def _to_view(self, scene_point):
        return QtCore.QPointF(self.mapFromScene(scene_point))

    def _near_first(self, view_pos):
        if len(self._points) < 3:
            return False
        first = self._to_view(self._points[0])
        return math.hypot(first.x() - view_pos.x(), first.y() - view_pos.y()) <= CLOSE_PX

    def _refresh_polygon(self):
        if len(self._points) != self._last_count:
            self._last_count = len(self._points)
            self.corners_changed.emit(self._last_count)
        for item in self._poly_items:
            self._scene.removeItem(item)
        self._poly_items = []
        if not self._points:
            return
        bad = self._closed and bool(self.polygon_problem())
        colour = QtGui.QColor(theme.ORANGE_BRIGHT if bad else theme.GREEN_BRIGHT)
        path = QtGui.QPainterPath(self._points[0])
        for point in self._points[1:]:
            path.lineTo(point)
        if self._closed:
            path.closeSubpath()
        elif self._cursor is not None:
            path.lineTo(self._cursor)
        pen = QtGui.QPen(colour, 3)
        pen.setCosmetic(True)
        pen.setJoinStyle(QtCore.Qt.RoundJoin)
        shape = QtWidgets.QGraphicsPathItem(path)
        shape.setPen(pen)
        if self._closed:
            fill = QtGui.QColor(colour)
            fill.setAlpha(46)
            shape.setBrush(fill)
        shape.setZValue(5)
        self._scene.addItem(shape)
        self._poly_items.append(shape)
        if not self._closed:
            for index, point in enumerate(self._points):
                dot = QtWidgets.QGraphicsEllipseItem(-5, -5, 10, 10)
                big = index == 0 and len(self._points) >= 3
                if big:
                    dot.setRect(-8, -8, 16, 16)
                dot.setFlag(QtWidgets.QGraphicsItem.ItemIgnoresTransformations, True)
                dot.setPos(point)
                dot.setBrush(QtGui.QColor(255, 255, 255) if big else colour)
                dot.setPen(QtGui.QPen(colour, 2))
                dot.setZValue(6)
                self._scene.addItem(dot)
                self._poly_items.append(dot)

    # -- the façades ------------------------------------------------------------------------------

    def set_facades(self, facades):
        """Draw `facades` (objects with letter, colour, polyline_xy(), nx, ny); the old ones are removed. A hover or selection on a
        letter that is still there is kept."""
        for mark in self._marks.values():
            mark.remove(self._scene)
        self._marks = {}
        if self._frame is not None:
            for facade in facades:
                self._marks[facade.letter] = _FacadeMark(self._scene, facade, self._frame)
        self._selected = set(letter for letter in self._selected if letter in self._marks)
        if self._hover not in self._marks:
            self._hover = ""
        self._restyle()

    def letters(self):
        return sorted(self._marks)

    def set_selected(self, letters):
        self._selected = set(letter for letter in letters if letter in self._marks)
        self._restyle()

    def selected(self):
        return sorted(self._selected)

    def set_hover(self, letter):
        letter = letter if letter in self._marks else ""
        if letter != self._hover:
            self._hover = letter
            self._restyle()

    def hover(self):
        return self._hover

    def _restyle(self):
        for letter, mark in self._marks.items():
            mark.style(letter in self._selected, letter == self._hover)

    def letter_at(self, view_pos):
        """Letter of the façade drawn under the viewport point `view_pos`, or ""."""
        best, best_distance = "", HIT_PX + 0.5
        point = (view_pos.x(), view_pos.y())
        for letter, mark in self._marks.items():
            a, b = self._to_view(mark.a), self._to_view(mark.b)
            distance = segment_distance(point, (a.x(), a.y()), (b.x(), b.y()))
            centre = self._to_view(mark.badge.pos())
            distance = min(distance, math.hypot(point[0] - centre.x() - mark.badge.offset.x(),
                                                point[1] - centre.y() - mark.badge.offset.y()) - 8)
            if distance < best_distance:
                best, best_distance = letter, distance
        return best

    def facade_view_point(self, letter):
        """Viewport position (QPoint) of the middle of a façade line: for tests and hints."""
        mark = self._marks.get(letter)
        if mark is None:
            return None
        middle = QtCore.QPointF((mark.a.x() + mark.b.x()) / 2.0, (mark.a.y() + mark.b.y()) / 2.0)
        return self.mapFromScene(middle)

    def scene_to_view(self, px, py):
        return self.mapFromScene(QtCore.QPointF(px, py))

    # -- mouse and keys ---------------------------------------------------------------------------

    def mousePressEvent(self, event):
        self.setFocus(QtCore.Qt.MouseFocusReason)
        pos = event_pos(event)
        self._press = (pos, event.button(), False)
        self._panning = False
        event.accept()

    def mouseMoveEvent(self, event):
        pos = event_pos(event)
        if self._press is not None:
            start, button, moved = self._press
            dx, dy = pos.x() - start.x(), pos.y() - start.y()
            can_pan = button in (QtCore.Qt.MiddleButton, QtCore.Qt.RightButton) or button == QtCore.Qt.LeftButton
            if can_pan and (moved or math.hypot(dx, dy) > DRAG_PX):
                if not moved:
                    self._press = (start, button, True)
                    self._press_last = start
                last = self._press_last
                self._press_last = pos
                self._pan(pos.x() - last.x(), pos.y() - last.y())
                self._panning = True
            event.accept()
            return
        if self.is_drawing() and self._frame is not None:
            self._cursor = self.mapToScene(pos.toPoint())
            self._refresh_polygon()
        elif self._marks:
            letter = self.letter_at(pos)
            if letter != self._hover:
                self.set_hover(letter)
                self.facade_hovered.emit(letter)
            self.viewport().setCursor(QtCore.Qt.PointingHandCursor if letter else
                                      (QtCore.Qt.CrossCursor if self.is_drawing() else QtCore.Qt.ArrowCursor))
        event.accept()

    def mouseReleaseEvent(self, event):
        press, self._press = self._press, None
        if press is None or self._skip_release:
            self._skip_release = False
            event.accept()
            return
        start, button, moved = press
        event.accept()
        if moved or self._panning:
            self._panning = False
            return
        if button != QtCore.Qt.LeftButton:
            return
        pos = event_pos(event)
        if self.is_drawing() and self._frame is not None:
            if self._near_first(pos):
                self._close_polygon()
                return
            scene_point = self.mapToScene(pos.toPoint())
            if self._points:
                last = self._to_view(self._points[-1])
                if math.hypot(last.x() - pos.x(), last.y() - pos.y()) < 3:
                    return
            self._points.append(scene_point)
            self._cursor = scene_point
            self._refresh_polygon()
            return
        if self._marks:
            ctrl, shift, _alt = modifiers(event)
            self.facade_clicked.emit(self.letter_at(pos), ctrl, shift)

    def mouseDoubleClickEvent(self, event):
        event.accept()
        if self.is_drawing() and event.button() == QtCore.Qt.LeftButton:
            self._skip_release = True
            self._close_polygon()
        else:
            super(OrthoCanvas, self).mouseDoubleClickEvent(event)

    def leaveEvent(self, event):
        super(OrthoCanvas, self).leaveEvent(event)
        if self._hover and self._press is None:
            self.set_hover("")
            self.facade_hovered.emit("")

    def keyPressEvent(self, event):
        key = event.key()
        if key == QtCore.Qt.Key_Escape and self.cancel_drawing():
            event.accept()
        elif key in (QtCore.Qt.Key_Delete, QtCore.Qt.Key_Backspace) and self._draw_enabled and self._points:
            self.clear_polygon()
            event.accept()
        elif key == QtCore.Qt.Key_Delete and not self._draw_enabled:
            self.command.emit("delete")
            event.accept()
        elif key == QtCore.Qt.Key_Z and (event.modifiers() & QtCore.Qt.ControlModifier) and not self._draw_enabled:
            self.command.emit("undo")
            event.accept()
        elif key in (QtCore.Qt.Key_Plus, QtCore.Qt.Key_Equal):
            self.zoom_by(ZOOM_STEP)
            event.accept()
        elif key == QtCore.Qt.Key_Minus:
            self.zoom_by(1.0 / ZOOM_STEP)
            event.accept()
        elif key == QtCore.Qt.Key_0:
            self.fit_all()
            event.accept()
        else:
            super(OrthoCanvas, self).keyPressEvent(event)

    def _pan(self, dx, dy):
        self._user_zoomed = True
        bar_x, bar_y = self.horizontalScrollBar(), self.verticalScrollBar()
        bar_x.setValue(int(bar_x.value() - dx))
        bar_y.setValue(int(bar_y.value() - dy))


# --------------------------------------------------------------------------------------------------
# The façade list
# --------------------------------------------------------------------------------------------------

LETTER_ROLE = QtCore.Qt.UserRole + 1
FACADE_ROLE = QtCore.Qt.UserRole + 2


class _FacadeDelegate(QtWidgets.QStyledItemDelegate):
    """One row: coloured disc with the letter, "Wall A" and the way it faces, then its length and height."""

    def sizeHint(self, option, _index):
        fm = QtGui.QFontMetrics(option.font)
        return QtCore.QSize(220, max(48, fm.height() * 2 + 22))

    def paint(self, painter, option, index):
        facade = index.data(FACADE_ROLE)
        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        if not option.state & QtWidgets.QStyle.State_Enabled:
            painter.setOpacity(0.55)
        rect = QtCore.QRectF(option.rect)
        selected = bool(option.state & QtWidgets.QStyle.State_Selected)
        hovered = bool(option.state & QtWidgets.QStyle.State_MouseOver)
        if selected:
            painter.setPen(QtGui.QPen(QtGui.QColor(theme.GREEN), 1))
            painter.setBrush(QtGui.QColor(30, 202, 89, 46))
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 6, 6)
        elif hovered:
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor(theme.GROUND_3))
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 6, 6)
        if facade is None:
            painter.restore()
            return
        disc = min(36.0, rect.height() - 10)
        disc_rect = QtCore.QRectF(rect.left() + 10, rect.center().y() - disc / 2.0, disc, disc)
        painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0, 120), 1))
        painter.setBrush(QtGui.QColor(facade.colour))
        painter.drawEllipse(disc_rect)
        font = QtGui.QFont(option.font)
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() * (1.15 if len(facade.letter) == 1 else 0.9))
        painter.setFont(font)
        painter.setPen(QtGui.QColor(readable_on(facade.colour)))
        painter.drawText(disc_rect, QtCore.Qt.AlignCenter, facade.letter)
        left = disc_rect.right() + 12
        width = int(rect.right() - left - 8)
        bold = QtGui.QFont(option.font)
        bold.setBold(True)
        painter.setFont(bold)
        fm = painter.fontMetrics()
        line = fm.height()
        top = rect.center().y() - line - 1
        painter.setPen(QtGui.QColor(theme.FG_0))
        name = tr("facade.ui.row_title", letter=facade.letter)
        painter.drawText(QtCore.QRectF(left, top, width, line), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                         fm.elidedText(name, QtCore.Qt.ElideRight, width))
        name_width = fm.horizontalAdvance(name)
        small = QtGui.QFont(option.font)
        small.setPointSizeF(max(7.0, small.pointSizeF() * 0.88))
        painter.setFont(small)
        painter.setPen(QtGui.QColor(theme.FG_2))
        fm_small = painter.fontMetrics()
        facing = tr("facade.ui.row_facing", direction=tr(dir_key(facade.facing)))
        room = width - name_width - 12
        if room > fm_small.horizontalAdvance(facing[:6]):
            painter.drawText(QtCore.QRectF(left + name_width + 12, top, room, line), QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
                             fm_small.elidedText(facing, QtCore.Qt.ElideRight, int(room)))
        painter.setFont(QtGui.QFont(option.font))
        painter.setPen(QtGui.QColor(theme.FG_1))
        fm = painter.fontMetrics()
        size = tr("facade.ui.row_size", length=metres(facade.length), height=metres(facade.height))
        painter.drawText(QtCore.QRectF(left, rect.center().y() + 2, width, line), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                         fm.elidedText(size, QtCore.Qt.ElideRight, width))
        painter.restore()


class FacadeList(QtWidgets.QWidget):
    """The façades of the building as a list. Click selects, Shift and Ctrl click select several, right-click or the Delete key removes the
    selected ones, Undo (button or Ctrl+Z) brings the last removal back. A removed façade keeps its letter and is flagged
    `removed = True`, so it is not processed.

    Signals: `selection_changed(list of letters)`, `changed()` (something was removed or restored), `activated(letter)` (double click)."""

    selection_changed = QtCore.Signal(object)
    changed = QtCore.Signal()
    activated = QtCore.Signal(str)

    def __init__(self, parent=None, allow_remove=True):
        super(FacadeList, self).__init__(parent)
        self._all = []
        self._log = RemovalLog()
        self._allow_remove = allow_remove
        self._quiet = False
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.PAD)
        self.count_label = ui.label("", "aeroSection")
        layout.addWidget(self.count_label)
        self.list = QtWidgets.QListWidget()
        self.list.setObjectName("facadeList")
        self.list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.list.setItemDelegate(_FacadeDelegate(self.list))
        self.list.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.list.setMouseTracking(True)
        self.list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.list.itemSelectionChanged.connect(self._on_selection)
        self.list.itemDoubleClicked.connect(lambda item: self.activated.emit(item.data(LETTER_ROLE)))
        self.list.setMinimumHeight(140)
        layout.addWidget(self.list, 1)
        self.empty = ui.label(tr("facade.ui.list_empty"), "facadeEmpty")
        layout.addWidget(self.empty)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(theme.PAD)
        self.remove_button = ui.button("facade.ui.remove", self.remove_selected, "aeroBtn", width=110)
        self.undo_button = ui.button("facade.ui.undo", self.undo, "aeroBtn", width=90)
        row.addWidget(self.remove_button)
        row.addWidget(self.undo_button)
        row.addStretch(1)
        layout.addLayout(row)
        self.hint = ui.label(tr("facade.ui.list_hint"), "aeroMeta")
        layout.addWidget(self.hint)
        if not allow_remove:
            self.remove_button.setVisible(False)
            self.undo_button.setVisible(False)
            self.hint.setVisible(False)
        self.remove_button.setToolTip(tr("facade.ui.remove_tip"))
        self.undo_button.setToolTip(tr("facade.ui.undo_tip"))
        undo = qt_shortcut(self, "Ctrl+Z", self.undo)
        self._shortcuts = [undo]
        self._refresh_state()

    # -- data ---------------------------------------------------------------------------------------

    def show_count(self, shown):
        self.count_label.setVisible(bool(shown))

    def set_facades(self, facades, keep_log=False):
        """Show `facades` (all of them, removed ones included: only the kept ones are listed)."""
        self._all = list(facades)
        if not keep_log:
            self._log.clear()
        self._rebuild([])

    def facades(self):
        return list(self._all)

    def kept(self):
        return [f for f in self._all if not f.removed]

    def removed(self):
        return [f for f in self._all if f.removed]

    def selected_letters(self):
        return [item.data(LETTER_ROLE) for item in self.list.selectedItems()]

    def select(self, letters):
        """Select exactly `letters` (no signal about it: the caller already knows)."""
        wanted = set(letters)
        self._quiet = True
        try:
            self.list.clearSelection()
            for row in range(self.list.count()):
                item = self.list.item(row)
                if item.data(LETTER_ROLE) in wanted:
                    item.setSelected(True)
        finally:
            self._quiet = False
        self._refresh_state()

    def can_undo(self):
        return self._log.can_undo()

    def _rebuild(self, keep_selected):
        self._quiet = True
        try:
            self.list.clear()
            for facade in self.kept():
                item = QtWidgets.QListWidgetItem()
                item.setData(LETTER_ROLE, facade.letter)
                item.setData(FACADE_ROLE, facade)
                item.setToolTip(tr("facade.ui.row_tip", letter=facade.letter, length=metres(facade.length), height=metres(facade.height),
                                   direction=tr(dir_key(facade.facing))))
                self.list.addItem(item)
                if facade.letter in keep_selected:
                    item.setSelected(True)
        finally:
            self._quiet = False
        self._refresh_state()

    def _refresh_state(self):
        count = len(self.kept())
        self.count_label.setText(tr("facade.ui.list_count", n=count))
        self.empty.setVisible(count == 0 and bool(self._all) or (count == 0 and not self._all))
        self.list.setVisible(count > 0)
        has_selection = bool(self.list.selectedItems())
        self.remove_button.setEnabled(has_selection)
        self.undo_button.setEnabled(self._log.can_undo())
        if has_selection:
            self.remove_button.setText(tr("facade.ui.remove_n", n=len(self.list.selectedItems())))
        else:
            self.remove_button.setText(tr("facade.ui.remove"))

    def _on_selection(self):
        self._refresh_state()
        if not self._quiet:
            self.selection_changed.emit(self.selected_letters())

    # -- removing and undoing ---------------------------------------------------------------------

    def remove_selected(self):
        if not self._allow_remove:
            return []
        letters = self.selected_letters()
        if not letters:
            return []
        for facade in self._all:
            if facade.letter in letters:
                facade.removed = True
        self._log.push(letters)
        self._rebuild([])
        self.selection_changed.emit([])
        self.changed.emit()
        return letters

    def undo(self):
        if not self._allow_remove:
            return []
        letters = self._log.pop()
        if not letters:
            return []
        for facade in self._all:
            if facade.letter in letters:
                facade.removed = False
        self._rebuild(letters)
        self.selection_changed.emit(self.selected_letters())
        self.changed.emit()
        return letters

    def _menu(self, point):
        item = self.list.itemAt(point)
        if item is not None and not item.isSelected():
            self.list.clearSelection()
            item.setSelected(True)
        menu = QtWidgets.QMenu(self)
        selected = self.selected_letters()
        if self._allow_remove:
            remove = menu.addAction(tr("facade.ui.remove_n", n=len(selected)) if selected else tr("facade.ui.remove"))
            remove.setEnabled(bool(selected))
            remove.triggered.connect(lambda *_a: self.remove_selected())
            undo = menu.addAction(tr("facade.ui.undo"))
            undo.setEnabled(self._log.can_undo())
            undo.triggered.connect(lambda *_a: self.undo())
        zoom = menu.addAction(tr("facade.ui.zoom_on"))
        zoom.setEnabled(bool(selected))
        zoom.triggered.connect(lambda *_a: self.activated.emit(selected[0]))
        self._menu_object = menu
        menu.popup(self.list.viewport().mapToGlobal(point))

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key_Delete:
            self.remove_selected()
            event.accept()
            return
        super(FacadeList, self).keyPressEvent(event)

    def eventFilter(self, watched, event):
        return super(FacadeList, self).eventFilter(watched, event)


# --------------------------------------------------------------------------------------------------
# Batch progress
# --------------------------------------------------------------------------------------------------

STATE_MARKS = {STATE_WAITING: ("…", theme.FG_3), STATE_RUNNING: ("▶", theme.GREEN_BRIGHT),
               STATE_DONE: ("✓", theme.GREEN_BRIGHT), STATE_FAILED: ("✕", theme.ORANGE_BRIGHT),
               STATE_CANCELLED: ("–", theme.FG_3)}


class _BatchRow(QtWidgets.QFrame):
    def __init__(self, letter, colour):
        super(_BatchRow, self).__init__()
        self.letter = letter
        self.state = STATE_WAITING
        self.setObjectName("facadeCard")
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(theme.PAD + 4)
        self.disc = QtWidgets.QLabel(letter)
        self.disc.setAlignment(QtCore.Qt.AlignCenter)
        self.disc.setMinimumSize(30, 30)
        self.disc.setStyleSheet("background: {}; color: {}; border-radius: 15px; font-weight: 700;".format(colour, readable_on(colour)))
        layout.addWidget(self.disc)
        self.name = ui.label(tr("facade.ui.batch_name", letter=letter), wrap=False)
        layout.addWidget(self.name)
        self.status = ui.label("", "aeroMeta", wrap=False)
        layout.addWidget(self.status, 1)
        self.mark = QtWidgets.QLabel("")
        self.mark.setMinimumWidth(24)
        self.mark.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(self.mark)
        self.set_state(STATE_WAITING)

    def set_state(self, state, text=""):
        self.state = state
        symbol, colour = STATE_MARKS[state]
        self.mark.setText(symbol)
        self.mark.setStyleSheet("color: {}; font-size: 13pt; font-weight: 700; background: transparent;".format(colour))
        self.status.setText(text or tr(STATE_KEYS[state]))
        self.status.setToolTip(text or "")
        self.name.setStyleSheet("font-weight: 700;" if state == STATE_RUNNING else "")


class BatchProgress(QtWidgets.QWidget):
    """What is being computed, one row per façade: waiting, running, done or failed (with the plain reason), an overall bar,
    the "please do not touch Metashape" banner while it runs, Cancel, and Retry for the failed ones.

    Signals: `cancel_requested()`, `retry_requested(list of letters)`."""

    cancel_requested = QtCore.Signal()
    retry_requested = QtCore.Signal(object)

    def __init__(self, parent=None, show_retry=True):
        super(BatchProgress, self).__init__(parent)
        self._show_retry = show_retry
        self._rows = {}
        self._order = []
        self._running = False
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.PAD + 4)
        self.banner = QtWidgets.QFrame()
        self.banner.setObjectName("facadeWait")
        banner = QtWidgets.QVBoxLayout(self.banner)
        banner.setContentsMargins(16, 12, 16, 12)
        banner.setSpacing(4)
        self.banner_title = ui.label(tr("facade.ui.wait_title"), "facadeWaitText")
        banner.addWidget(self.banner_title)
        self.banner_text = ui.label(tr("facade.ui.wait_text"), "aeroMeta")
        banner.addWidget(self.banner_text)
        self.banner.setVisible(False)
        layout.addWidget(self.banner)
        self.bar = QtWidgets.QProgressBar()
        self.bar.setObjectName("facadeBar")
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        layout.addWidget(self.bar)
        self.line = controls.ElidedLabel("aeroMeta")
        layout.addWidget(self.line)
        body = QtWidgets.QWidget()
        self.rows = QtWidgets.QVBoxLayout(body)
        self.rows.setContentsMargins(0, 0, theme.PAD, 0)
        self.rows.setSpacing(theme.PAD)
        self.rows.addStretch(1)
        self.scroll = controls.scroll_page(body)
        layout.addWidget(self.scroll, 1)
        buttons = QtWidgets.QHBoxLayout()
        buttons.setSpacing(theme.PAD)
        self.cancel_button = ui.big_button("facade.ui.cancel_job", self.cancel_requested.emit, "aeroBtn", width=130)
        self.retry_button = ui.big_button("facade.ui.retry", self._retry, "aeroGo", width=170)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.retry_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.cancel_button.setVisible(False)
        self.retry_button.setVisible(False)

    # -- rows ---------------------------------------------------------------------------------------

    def set_items(self, facades):
        """One waiting row per façade (objects with letter and colour)."""
        for row in self._rows.values():
            row.setParent(None)
            row.deleteLater()
        self._rows, self._order = {}, []
        for facade in facades:
            row = _BatchRow(facade.letter, facade.colour)
            self._rows[facade.letter] = row
            self._order.append(facade.letter)
            self.rows.insertWidget(self.rows.count() - 1, row)
        self.bar.setValue(0)
        self.line.setFullText("")
        self._sync_buttons()

    def set_state(self, letter, state, text=""):
        row = self._rows.get(letter)
        if row is not None:
            row.set_state(state, text)
        self._sync_buttons()

    def state_of(self, letter):
        row = self._rows.get(letter)
        return row.state if row is not None else None

    def states(self):
        return dict((letter, self._rows[letter].state) for letter in self._order)

    def failed(self):
        return [letter for letter in self._order if self._rows[letter].state == STATE_FAILED]

    def retryable(self):
        """What "Try again" would redo: the rows that failed or were cancelled."""
        return [letter for letter in self._order if self._rows[letter].state in (STATE_FAILED, STATE_CANCELLED)]

    def set_progress(self, fraction, text=""):
        self.bar.setValue(int(max(0.0, min(1.0, fraction)) * 1000))
        if text:
            self.line.setFullText(text)

    def set_running(self, running):
        """While running: the banner and Cancel show, Retry hides."""
        self._running = bool(running)
        self.banner.setVisible(self._running)
        self._sync_buttons()

    def is_running(self):
        return self._running

    def _sync_buttons(self):
        self.cancel_button.setVisible(self._running)
        self.retry_button.setVisible(self._show_retry and not self._running and bool(self.retryable()))

    def _retry(self):
        self.retry_requested.emit(self.retryable())


# --------------------------------------------------------------------------------------------------
# Thumbnails of the built orthophotos
# --------------------------------------------------------------------------------------------------

THUMB_SIZE = QtCore.QSize(168, 112)


class OrthoThumbnails(QtWidgets.QWidget):
    """The orthophotos of the façades, a small picture and a check box each: the checked ones are exported.

    Signal: `checks_changed()`."""

    checks_changed = QtCore.Signal()

    def __init__(self, parent=None):
        super(OrthoThumbnails, self).__init__(parent)
        self._facades = {}
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.PAD)
        self.list = QtWidgets.QListWidget()
        self.list.setObjectName("facadeThumbs")
        self.list.setIconSize(THUMB_SIZE)
        self.list.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.list.setSpacing(2)
        self.list.setMinimumHeight(180)
        self.list.itemChanged.connect(lambda _item: self.checks_changed.emit())
        layout.addWidget(self.list, 1)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(theme.PAD)
        row.addWidget(ui.button("facade.ui.select_all", lambda: self.set_all(True), "aeroBtn", width=110))
        row.addWidget(ui.button("facade.ui.select_none", lambda: self.set_all(False), "aeroBtn", width=110))
        row.addStretch(1)
        layout.addLayout(row)

    def _placeholder(self, text):
        pixmap = QtGui.QPixmap(THUMB_SIZE)
        pixmap.fill(QtGui.QColor(theme.GROUND_3))
        painter = QtGui.QPainter(pixmap)
        painter.setPen(QtGui.QColor(theme.FG_3))
        painter.drawText(pixmap.rect(), QtCore.Qt.AlignCenter, text)
        painter.end()
        return QtGui.QIcon(pixmap)

    def set_items(self, facades, checked=True):
        self.list.blockSignals(True)
        self.list.clear()
        self._facades = {}
        for facade in facades:
            item = QtWidgets.QListWidgetItem(self._placeholder("…"), tr("facade.ui.thumb_title", letter=facade.letter,
                                                                          length=metres(facade.length),
                                                                          height=metres(facade.height)))
            item.setData(LETTER_ROLE, facade.letter)
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.Checked if checked else QtCore.Qt.Unchecked)
            self.list.addItem(item)
            self._facades[facade.letter] = item
        self.list.blockSignals(False)
        self.checks_changed.emit()

    def set_thumbnail(self, letter, array):
        item = self._facades.get(letter)
        if item is None:
            return
        if array is None:
            item.setIcon(self._placeholder(tr("facade.ui.thumb_missing")))
            return
        image = array_to_qimage(array)
        scaled = QtGui.QPixmap.fromImage(image).scaled(THUMB_SIZE, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
        frame = QtGui.QPixmap(THUMB_SIZE)                              # every thumbnail the same size, the picture centred in it
        frame.fill(QtGui.QColor(theme.GROUND_3))
        painter = QtGui.QPainter(frame)
        painter.drawPixmap((THUMB_SIZE.width() - scaled.width()) // 2, (THUMB_SIZE.height() - scaled.height()) // 2, scaled)
        painter.end()
        item.setIcon(QtGui.QIcon(frame))

    def mark_failed(self, letter, text=""):
        item = self._facades.get(letter)
        if item is not None:
            item.setIcon(self._placeholder(tr("facade.ui.thumb_missing")))
            item.setCheckState(QtCore.Qt.Unchecked)
            item.setToolTip(text)

    def checked_letters(self):
        return [self.list.item(row).data(LETTER_ROLE) for row in range(self.list.count())
                if self.list.item(row).checkState() == QtCore.Qt.Checked]

    def set_checked(self, letters):
        wanted = set(letters)
        self.list.blockSignals(True)
        for row in range(self.list.count()):
            item = self.list.item(row)
            item.setCheckState(QtCore.Qt.Checked if item.data(LETTER_ROLE) in wanted else QtCore.Qt.Unchecked)
        self.list.blockSignals(False)
        self.checks_changed.emit()

    def set_all(self, checked):
        self.set_checked([self.list.item(row).data(LETTER_ROLE) for row in range(self.list.count())] if checked else [])

    def count(self):
        return self.list.count()

    def letters(self):
        return [self.list.item(row).data(LETTER_ROLE) for row in range(self.list.count())]


# Margin editor and method picker live in their own module; they are part of this module's public face.
from .widgets_margins import LinkBadge, MarginEditor, MarginSchematic, MethodOption, MethodPicker, NumberField  # noqa: E402,F401
