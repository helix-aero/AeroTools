"""Two small painted views of the Aero-PPK window: the time axis of step 2 and the plan of the photo positions of step 4. Qt only; the
rules (rows, ticks, positions) are in `timeline.py` and `mapmath.py`."""

import math

from ...qt import QtCore, QtGui, QtWidgets
from ...uikit import metrics, tokens
from ...uikit.metrics import px
from . import timeline


def _color(value, alpha=None):
    color = QtGui.QColor(value)
    if alpha is not None:
        color.setAlpha(alpha)
    return color


class TimelineView(QtWidgets.QWidget):
    """One row per dataset on a horizontal time axis (UTC, hour marks): the flight, then each base. The download window around the
    flight is shaded; a base bar is green when it covers the flight and orange when it does not; a base not downloaded yet shows the
    window that will be asked as a dashed outline. `rowClicked(key)` is sent when a row is clicked."""

    rowClicked = QtCore.Signal(str)

    def __init__(self, parent=None):
        super(TimelineView, self).__init__(parent)
        self._rows = []
        self._margin = 1
        self._selected = ""
        self._range = None
        self.setMouseTracking(False)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

    def set_rows(self, rows, margin_hours):
        self._rows, self._margin = list(rows), margin_hours
        self._range = timeline.axis_range(self._rows, margin_hours) if self._rows else None
        self.updateGeometry()
        self.update()

    def set_selected(self, key):
        self._selected = key
        self.update()

    def rows(self):
        return list(self._rows)

    def _metrics(self):
        return QtGui.QFontMetrics(metrics.font("small"))

    def _row_h(self):
        return px(tokens.XL - tokens.XS)

    def _label_w(self):
        fm = self._metrics()
        names = [r.label for r in self._rows] or [""]
        return max(px(tokens.XXL), max(fm.horizontalAdvance(n) for n in names) + px(tokens.M))

    def sizeHint(self):
        return QtCore.QSize(px(tokens.POPOVER_W), self._row_h() * max(1, len(self._rows)) + self._metrics().height() + px(tokens.M + 2))

    def minimumSizeHint(self):
        return QtCore.QSize(px(tokens.POPOVER_W // 2), self.sizeHint().height())

    def _plot_rect(self):
        left = self._label_w()
        return QtCore.QRectF(left, 2, max(10, self.width() - left - px(tokens.XL)), self._row_h() * max(1, len(self._rows)))

    def mousePressEvent(self, event):
        pos = event.pos() if hasattr(event, "pos") else event.position().toPoint()
        row = int(pos.y() // self._row_h())
        if 0 <= row < len(self._rows) and self._rows[row].label:
            self.rowClicked.emit(self._rows[row].key)
        super(TimelineView, self).mousePressEvent(event)

    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        if not self._rows or self._range is None:
            p.end()
            return
        start, end = self._range
        plot = self._plot_rect()
        rh = self._row_h()
        flight = next((r for r in self._rows if r.kind == timeline.FLIGHT), None)

        def x_of(moment):
            return plot.left() + plot.width() * timeline.fraction(moment, start, end)

        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(_color(tokens.SURFACE))
        p.drawRoundedRect(plot, px(tokens.RADIUS), px(tokens.RADIUS))
        if flight is not None:                                          # download window
            import datetime
            pad = datetime.timedelta(hours=float(self._margin))
            a, b = x_of(flight.start - pad), x_of(flight.end + pad)
            p.setBrush(_color(tokens.ACCENT, 36))
            p.drawRect(QtCore.QRectF(a, plot.top(), max(1.0, b - a), plot.height()))
        p.setFont(metrics.font("caption"))
        tick_h = QtGui.QFontMetrics(metrics.font("caption")).height()
        for tick in timeline.ticks(start, end):
            x = x_of(tick)
            p.setPen(QtGui.QPen(_color(tokens.BORDER), 1))
            p.drawLine(QtCore.QPointF(x, plot.top()), QtCore.QPointF(x, plot.bottom()))
            p.setPen(_color(tokens.TEXT_MUTED))
            p.drawText(QtCore.QRectF(x - px(tokens.XL), plot.bottom() + 1, px(2 * tokens.XL), tick_h + 2), QtCore.Qt.AlignCenter,
                       tick.strftime("%H:%M"))
        small = metrics.font("small")
        gap = px(tokens.XS)
        for index, row in enumerate(self._rows):
            top = plot.top() + index * rh
            bar = QtCore.QRectF(x_of(row.start), top + gap, max(3.0, x_of(row.end) - x_of(row.start)), rh - 2 * gap)
            if row.label:
                p.setFont(small)
                p.setPen(_color(tokens.TEXT_STRONG if row.key == self._selected else tokens.TEXT))
                p.drawText(QtCore.QRectF(2, top, self._label_w() - px(tokens.S), rh), QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight, row.label)
            if row.kind == timeline.FLIGHT:
                p.setPen(QtCore.Qt.NoPen)
                p.setBrush(_color(tokens.TEXT))
            elif row.kind == timeline.PLANNED:
                pen = QtGui.QPen(_color(tokens.TEXT_DISABLED), 1.2, QtCore.Qt.DashLine)
                p.setPen(pen)
                p.setBrush(QtCore.Qt.NoBrush)
            else:
                p.setPen(QtCore.Qt.NoPen)
                p.setBrush(_color(tokens.ACCENT if row.ok else tokens.WARNING))
            p.drawRoundedRect(bar, gap, gap)
            if row.key == self._selected and row.kind != timeline.FLIGHT:
                p.setBrush(QtCore.Qt.NoBrush)
                p.setPen(QtGui.QPen(_color(tokens.TEXT_STRONG), 1.6))
                p.drawRoundedRect(bar.adjusted(-1, -1, 1, 1), gap, gap)
        p.end()


class PlanView(QtWidgets.QWidget):
    """Plan of the photo positions (north up, equal scale). While the calculation runs the photos light up in the order of the flight;
    when it ends each one takes the colour of its level (green good, orange to check, red not reliable, grey no position)."""

    def __init__(self, parent=None):
        super(PlanView, self).__init__(parent)
        self._points = []              # (lat, lon)
        self._lit = 0
        self._levels = None            # per photo: 0 good, 1 check, 2 bad, 3 none
        self.setMinimumSize(px(tokens.POPOVER_W * 2 // 3), px(tokens.CONTROL_H * 4))
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

    def set_points(self, points):
        self._points = [(float(a), float(b)) for a, b in points if math.isfinite(a) and math.isfinite(b)]
        self._lit, self._levels = 0, None
        self.update()

    def set_lit(self, count):
        self._lit = max(0, min(len(self._points), int(count)))
        self.update()

    def set_levels(self, levels):
        self._levels = list(levels) if levels is not None else None
        self.update()

    def levels(self):
        return self._levels

    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(_color(tokens.SURFACE))
        p.drawRoundedRect(QtCore.QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), px(tokens.RADIUS_L), px(tokens.RADIUS_L))
        pts = self._points
        if not pts:
            p.end()
            return
        lat0 = sum(a for a, _b in pts) / len(pts)
        lon0 = sum(b for _a, b in pts) / len(pts)
        k = math.cos(math.radians(lat0))
        xy = [((b - lon0) * k, (a - lat0)) for a, b in pts]
        xs, ys = [x for x, _y in xy], [y for _x, y in xy]
        span = max(max(xs) - min(xs), max(ys) - min(ys), 1e-7)
        margin = float(px(tokens.L))
        scale = min(self.width() - 2 * margin, self.height() - 2 * margin) / span
        cx, cy = (max(xs) + min(xs)) / 2.0, (max(ys) + min(ys)) / 2.0
        colors = (tokens.ACCENT, tokens.TEXT, tokens.TEXT_MUTED, tokens.TEXT_DISABLED)
        radius = max(2.0, min(4.5, 90.0 / math.sqrt(len(pts))))
        for index, (x, y) in enumerate(xy):
            sx = self.width() / 2.0 + (x - cx) * scale
            sy = self.height() / 2.0 - (y - cy) * scale
            if self._levels is not None:
                color = colors[max(0, min(3, int(self._levels[index] if index < len(self._levels) else 3)))]
            elif index < self._lit:
                color = tokens.TEXT_STRONG
            else:
                color = tokens.BORDER
            p.setBrush(_color(color))
            p.setPen(QtCore.Qt.NoPen)
            p.drawEllipse(QtCore.QPointF(sx, sy), radius, radius)
        p.end()
