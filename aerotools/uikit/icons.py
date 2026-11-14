"""Vector icons: every glyph of the kit is a few strokes drawn with QPainter, so it is sharp at any scale and needs no image file.

`paint(painter, name, rect, color)` draws into a rectangle; `icon(name, color, size)` makes a QIcon for a button.
Names: info, check, cross, warn, error, success, chevron_right, chevron_down, chevron_left, chevron_up, folder, search, plus, minus,
refresh, inbox, dot, arrow_right, gear, undo, redo, export, play, fit, spinner (a 100 degree arc, `angle` turns it),
mark (the HELIX AERO tool mark). Colours are given by the caller (tokens read when drawing), nothing is fixed here.
"""

from ..qt import QtCore, QtGui
from . import tokens

def _color(value):
    return value if isinstance(value, QtGui.QColor) else QtGui.QColor(value)


def _pen(color, width):
    pen = QtGui.QPen(_color(color))
    pen.setWidthF(width)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    pen.setJoinStyle(QtCore.Qt.RoundJoin)
    return pen


class _Box(object):
    """Maps unit coordinates (0..1) into a square inside `rect`."""

    def __init__(self, rect):
        side = min(rect.width(), rect.height())
        self.x = rect.x() + (rect.width() - side) / 2.0
        self.y = rect.y() + (rect.height() - side) / 2.0
        self.side = side

    def pt(self, u, v):
        return QtCore.QPointF(self.x + u * self.side, self.y + v * self.side)

    def rect(self, u, v, w, h):
        return QtCore.QRectF(self.x + u * self.side, self.y + v * self.side, w * self.side, h * self.side)

    def line(self, p, a, b):
        p.drawLine(self.pt(*a), self.pt(*b))

    def poly(self, p, *points):
        p.drawPolyline([self.pt(*pt) for pt in points])


def _ring(p, box, color, width, fill=None):
    p.setPen(_pen(color, width))
    p.setBrush(_color(fill) if fill else QtCore.Qt.NoBrush)
    p.drawEllipse(box.rect(0.06, 0.06, 0.88, 0.88))


def paint(p, name, rect, color, width=None, angle=0, background=None):
    """Draw icon `name` in `rect` (QRectF) with `color`; `width` is the stroke in px (default: a 12th of the size)."""
    rect = QtCore.QRectF(rect)
    box = _Box(rect)
    w = width if width else max(1.25, box.side / 12.0)
    p.save()
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    p.setBrush(QtCore.Qt.NoBrush)
    p.setPen(_pen(color, w))
    if name == "check":
        box.poly(p, (0.2, 0.54), (0.42, 0.74), (0.8, 0.28))
    elif name == "cross":
        box.line(p, (0.25, 0.25), (0.75, 0.75))
        box.line(p, (0.75, 0.25), (0.25, 0.75))
    elif name == "plus":
        box.line(p, (0.5, 0.2), (0.5, 0.8))
        box.line(p, (0.2, 0.5), (0.8, 0.5))
    elif name == "minus":
        box.line(p, (0.2, 0.5), (0.8, 0.5))
    elif name == "chevron_right":
        box.poly(p, (0.38, 0.22), (0.66, 0.5), (0.38, 0.78))
    elif name == "chevron_left":
        box.poly(p, (0.62, 0.22), (0.34, 0.5), (0.62, 0.78))
    elif name == "chevron_down":
        box.poly(p, (0.22, 0.38), (0.5, 0.66), (0.78, 0.38))
    elif name == "chevron_up":
        box.poly(p, (0.22, 0.62), (0.5, 0.34), (0.78, 0.62))
    elif name == "arrow_right":
        box.line(p, (0.18, 0.5), (0.82, 0.5))
        box.poly(p, (0.55, 0.23), (0.82, 0.5), (0.55, 0.77))
    elif name == "info":
        _ring(p, box, color, w)
        box.line(p, (0.5, 0.45), (0.5, 0.72))
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(_color(color))
        p.drawEllipse(box.pt(0.5, 0.29), w * 0.7, w * 0.7)
    elif name == "warn":
        box.poly(p, (0.5, 0.1), (0.94, 0.86), (0.06, 0.86), (0.5, 0.1))
        box.line(p, (0.5, 0.38), (0.5, 0.6))
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(_color(color))
        p.drawEllipse(box.pt(0.5, 0.73), w * 0.7, w * 0.7)
    elif name == "error":
        _ring(p, box, color, w)
        box.line(p, (0.35, 0.35), (0.65, 0.65))
        box.line(p, (0.65, 0.35), (0.35, 0.65))
    elif name == "success":
        _ring(p, box, color, w)
        box.poly(p, (0.3, 0.52), (0.45, 0.66), (0.71, 0.36))
    elif name == "folder":
        path = QtGui.QPainterPath()
        path.moveTo(box.pt(0.08, 0.26))
        path.lineTo(box.pt(0.38, 0.26))
        path.lineTo(box.pt(0.48, 0.36))
        path.lineTo(box.pt(0.92, 0.36))
        path.lineTo(box.pt(0.92, 0.80))
        path.lineTo(box.pt(0.08, 0.80))
        path.closeSubpath()
        p.drawPath(path)
    elif name == "search":
        p.drawEllipse(box.rect(0.12, 0.12, 0.5, 0.5))
        box.line(p, (0.58, 0.58), (0.86, 0.86))
    elif name == "refresh":
        p.drawArc(box.rect(0.15, 0.15, 0.7, 0.7), 40 * 16, 270 * 16)
        box.poly(p, (0.72, 0.1), (0.8, 0.28), (0.62, 0.3))
    elif name == "inbox":
        box.poly(p, (0.1, 0.55), (0.22, 0.2), (0.78, 0.2), (0.9, 0.55), (0.9, 0.8), (0.1, 0.8), (0.1, 0.55))
        box.poly(p, (0.1, 0.55), (0.36, 0.55), (0.42, 0.66), (0.58, 0.66), (0.64, 0.55), (0.9, 0.55))
    elif name == "gear":
        import math
        center = box.pt(0.5, 0.5)
        p.drawEllipse(center, box.side * 0.17, box.side * 0.17)
        p.drawEllipse(center, box.side * 0.31, box.side * 0.31)
        for k in range(8):
            a = math.radians(k * 45)
            p.drawLine(QtCore.QPointF(center.x() + math.cos(a) * box.side * 0.31, center.y() + math.sin(a) * box.side * 0.31),
                       QtCore.QPointF(center.x() + math.cos(a) * box.side * 0.43, center.y() + math.sin(a) * box.side * 0.43))
    elif name in ("undo", "redo"):
        sign = 1.0 if name == "undo" else -1.0
        def at(u, v):
            return box.pt(u if sign > 0 else 1.0 - u, v)
        path = QtGui.QPainterPath()
        path.moveTo(at(0.22, 0.4))
        path.cubicTo(at(0.5, 0.26), at(0.86, 0.34), at(0.82, 0.74))
        p.drawPath(path)
        p.drawPolyline([at(0.4, 0.16), at(0.2, 0.4), at(0.46, 0.58)])
    elif name == "export":
        box.poly(p, (0.2, 0.56), (0.2, 0.84), (0.8, 0.84), (0.8, 0.56))
        box.line(p, (0.5, 0.64), (0.5, 0.14))
        box.poly(p, (0.3, 0.32), (0.5, 0.14), (0.7, 0.32))
    elif name == "play":
        path = QtGui.QPainterPath()
        path.moveTo(box.pt(0.3, 0.2))
        path.lineTo(box.pt(0.8, 0.5))
        path.lineTo(box.pt(0.3, 0.8))
        path.closeSubpath()
        p.setBrush(_color(color))
        p.drawPath(path)
    elif name == "fit":
        for u, v, du, dv in ((0.14, 0.14, 1, 1), (0.86, 0.14, -1, 1), (0.14, 0.86, 1, -1), (0.86, 0.86, -1, -1)):
            box.poly(p, (u + du * 0.24, v), (u, v), (u, v + dv * 0.24))
    elif name == "dot":
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(_color(color))
        p.drawEllipse(box.pt(0.5, 0.5), box.side * 0.14, box.side * 0.14)
    elif name == "spinner":
        p.setPen(_pen(background or tokens.BORDER, w))
        p.drawEllipse(box.rect(0.1, 0.1, 0.8, 0.8))
        p.setPen(_pen(color, w * 1.2))
        p.drawArc(box.rect(0.1, 0.1, 0.8, 0.8), int(angle * 16), 100 * 16)
    elif name == "mark":
        _paint_mark(p, box)
    elif name.startswith("glyph_"):
        _glyph(p, box, name[6:], color, width if width else max(1.25, box.side / 13.0))
    elif name.startswith("tool_"):
        _tile(p, box)
        inner = box.side * 0.2
        _glyph(p, _Box(QtCore.QRectF(box.x + inner, box.y + inner, box.side - 2 * inner, box.side - 2 * inner)), name[5:],
               tokens.ON_OVERLAY, max(1.2, box.side * 0.075))
    p.restore()


TOOLS = (
    "gcp",
    "space",
    "exports",
    "suite",
)
TOOL_NAMES = tuple("tool_" + k for k in TOOLS)       # header version: the rounded gradient tile with the glyph in white
GLYPH_NAMES = tuple("glyph_" + k for k in TOOLS)     # glyph alone, one colour, for lists and buttons
NAMES = ("info", "check", "cross", "warn", "error", "success", "chevron_right", "chevron_down", "chevron_left", "chevron_up",
         "folder", "search", "plus", "minus", "refresh", "inbox", "dot", "arrow_right", "gear", "undo", "redo", "export", "play", "fit",
         "spinner", "mark") + TOOL_NAMES + GLYPH_NAMES


def _tile(p, box):
    """The rounded gradient tile of the tool marks (same as the suite mark's)."""
    gradient = QtGui.QLinearGradient(box.pt(0, 1), box.pt(1, 0))
    gradient.setColorAt(0.0, QtGui.QColor(tokens.LOGO_0))
    gradient.setColorAt(0.5, QtGui.QColor(tokens.LOGO_1))
    gradient.setColorAt(1.0, QtGui.QColor(tokens.LOGO_2))
    p.setPen(QtCore.Qt.NoPen)
    p.setBrush(gradient)
    p.drawRoundedRect(box.rect(0, 0, 1, 1), box.side * 0.24, box.side * 0.24)


def _glyph(p, box, key, color, w):
    """The nine tool glyphs on a 24 px grid: one stroke weight, round caps and joins, one colour."""
    def g(x, y):
        return box.pt(x / 24.0, y / 24.0)

    def line(x1, y1, x2, y2):
        p.drawLine(g(x1, y1), g(x2, y2))

    def poly(*pts):
        p.drawPolyline([g(x, y) for x, y in pts])

    def ring(x, y, r):
        p.drawEllipse(g(x, y), box.side * r / 24.0, box.side * r / 24.0)

    def dot(x, y, r):
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(_color(color))
        ring(x, y, r)
        p.setBrush(QtCore.Qt.NoBrush)
        p.setPen(_pen(color, w))

    def arc(x, y, rw, rh, start, span):
        p.drawArc(QtCore.QRectF(g(x - rw, y - rh), g(x + rw, y + rh)), int(start * 16), int(span * 16))

    p.setBrush(QtCore.Qt.NoBrush)
    p.setPen(_pen(color, w))
    if key == "suite":                     # the three orbits of the HELIX AERO mark
        for inset, start in ((0.12, 20), (0.26, 120), (0.4, 220)):
            p.drawArc(box.rect(inset, inset, 1 - 2 * inset, 1 - 2 * inset), start * 16, 250 * 16)
        dot(12, 12, 1.6)
    elif key == "gcp":
        ring(12, 12, 6.5)
        line(12, 2.5, 12, 8)
        line(12, 16, 12, 21.5)
        line(2.5, 12, 8, 12)
        line(16, 12, 21.5, 12)
        dot(12, 12, 1.7)
    elif key == "space":
        arc(10, 6, 6.5, 2.4, 0, 360)
        line(3.5, 6, 3.5, 15)
        line(16.5, 6, 16.5, 15)
        arc(10, 15, 6.5, 2.4, 180, 180)
        line(14, 14, 21, 21)
        poly((21, 15), (21, 21), (15, 21))
    elif key == "exports":
        poly((3.5, 15), (3.5, 20.5), (20.5, 20.5), (20.5, 15))
        poly((8, 15), (8, 3.5), (14, 3.5), (17, 6.5), (17, 15))
        poly((14, 3.5), (14, 6.5), (17, 6.5))
        line(10.5, 10.5, 14.5, 10.5)


def _paint_mark(p, box):
    """The tool mark: a rounded square in the logo gradient with three orbit arcs."""
    gradient = QtGui.QLinearGradient(box.pt(0, 1), box.pt(1, 0))
    gradient.setColorAt(0.0, QtGui.QColor(tokens.LOGO_0))
    gradient.setColorAt(0.5, QtGui.QColor(tokens.LOGO_1))
    gradient.setColorAt(1.0, QtGui.QColor(tokens.LOGO_2))
    p.setPen(QtCore.Qt.NoPen)
    p.setBrush(gradient)
    p.drawRoundedRect(box.rect(0, 0, 1, 1), box.side * 0.24, box.side * 0.24)
    p.setBrush(QtCore.Qt.NoBrush)
    width = max(1.2, box.side / 12.0)
    for index, (inset, start) in enumerate(((0.22, 20), (0.34, 120), (0.46, 220))):
        ink = QtGui.QColor(tokens.ON_OVERLAY)
        ink.setAlpha(255 if index == 0 else 215 - index * 40)
        p.setPen(_pen(ink, width))
        p.drawArc(box.rect(inset, inset, 1 - 2 * inset, 1 - 2 * inset), start * 16, 250 * 16)
    p.setPen(QtCore.Qt.NoPen)
    p.setBrush(QtGui.QColor(tokens.TEXT_STRONG))
    p.drawEllipse(box.pt(0.5, 0.5), box.side * 0.07, box.side * 0.07)


def pixmap(name, color, size, dpr=2.0, **kwargs):
    """A QPixmap (size x size logical px, rendered at `dpr`) of an icon."""
    image = QtGui.QPixmap(int(size * dpr), int(size * dpr))
    image.setDevicePixelRatio(dpr)
    image.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(image)
    paint(p, name, QtCore.QRectF(0, 0, size, size), color, **kwargs)
    p.end()
    return image


def icon(name, color, disabled_color=None, size=16):
    """A QIcon for a button: normal and (optionally) disabled colour."""
    result = QtGui.QIcon()
    result.addPixmap(pixmap(name, color, size), QtGui.QIcon.Normal)
    if disabled_color:
        result.addPixmap(pixmap(name, disabled_color, size), QtGui.QIcon.Disabled)
    return result


# --------------------------------------------------------------------------------------------------
# The AERO wordmark (the logotype with the open O), as vector paths in a 43.8667 x 10.2 box
# --------------------------------------------------------------------------------------------------

WORDMARK_BOX = (0.0, -0.1, 43.8667, 10.2)
WORDMARK_PATHS = (
    "M 0 10 L 4 0 L 5 0 L 9 10 L 8 10 L 4.5 1.25 L 1 10 Z",
    "M 12.5556 0 H 19.5556 V 0.92 H 12.5556 Z M 12.5556 4.44 H 18.5556 V 5.36 H 12.5556 Z M 12.5556 9.08 H 19.5556 V 10 H 12.5556 Z",
    "M 23.1111 0 H 27.2551 A 2.68 2.68 0 0 1 28.188 5.1924 L 30.1111 10 H 29.1111 L 26.8871 4.44 H 27.2551 "
    "A 1.68 1.76 0 0 0 28.9351 2.68 A 1.68 1.76 0 0 0 27.2551 0.92 H 23.1111 Z",
    "M 33.6667 7.1 V 2.4 A 2.5 2.5 0 0 1 36.1667 -0.1 H 41.3667 A 2.5 2.5 0 0 1 43.8667 2.4 V 7.6 A 2.5 2.5 0 0 1 41.3667 10.1 "
    "H 36.6667 V 9.18 H 41.3667 A 1.5 1.58 0 0 0 42.8667 7.6 V 2.4 A 1.5 1.58 0 0 0 41.3667 0.82 H 36.1667 A 1.5 1.58 0 0 0 34.6667 2.4 V 7.1 Z",
)
_wordmark_cache = {}


def _arc_to(path, x0, y0, rx, ry, phi, large, sweep, x, y):
    """SVG endpoint arc (angle `phi` is 0 in the wordmark) appended to a QPainterPath as an exact elliptical arc."""
    import math
    if rx == 0 or ry == 0 or (x0 == x and y0 == y):
        path.lineTo(x, y)
        return
    dx, dy = (x0 - x) / 2.0, (y0 - y) / 2.0
    lam = (dx * dx) / (rx * rx) + (dy * dy) / (ry * ry)
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = rx * rx * ry * ry - rx * rx * dy * dy - ry * ry * dx * dx
    den = rx * rx * dy * dy + ry * ry * dx * dx
    coef = math.sqrt(max(0.0, num / den)) * (-1 if large == sweep else 1)
    cxp, cyp = coef * rx * dy / ry, -coef * ry * dx / rx
    cx, cy = cxp + (x0 + x) / 2.0, cyp + (y0 + y) / 2.0
    start = math.degrees(math.atan2((y0 - cy) / ry, (x0 - cx) / rx))
    end = math.degrees(math.atan2((y - cy) / ry, (x - cx) / rx))
    delta = end - start
    if sweep and delta < 0:
        delta += 360.0
    elif not sweep and delta > 0:
        delta -= 360.0
    # SVG angles grow clockwise on screen (y down); Qt's arcTo angles grow counter-clockwise
    path.arcTo(QtCore.QRectF(cx - rx, cy - ry, 2 * rx, 2 * ry), -start, -delta)


def _parse_path(d):
    path = QtGui.QPainterPath()
    path.setFillRule(QtCore.Qt.WindingFill)
    tokens_ = d.replace(",", " ").split()
    i, x, y, start = 0, 0.0, 0.0, (0.0, 0.0)
    cmd = ""
    while i < len(tokens_):
        if tokens_[i].isalpha():
            cmd = tokens_[i]
            i += 1
            if cmd == "Z":
                path.closeSubpath()
                x, y = start
            continue
        if cmd == "M":
            x, y = float(tokens_[i]), float(tokens_[i + 1])
            i += 2
            path.moveTo(x, y)
            start, cmd = (x, y), "L"
        elif cmd == "L":
            x, y = float(tokens_[i]), float(tokens_[i + 1])
            i += 2
            path.lineTo(x, y)
        elif cmd == "H":
            x = float(tokens_[i])
            i += 1
            path.lineTo(x, y)
        elif cmd == "V":
            y = float(tokens_[i])
            i += 1
            path.lineTo(x, y)
        elif cmd == "A":
            rx, ry, phi, large, sweep, nx, ny = [float(v) for v in tokens_[i:i + 7]]
            i += 7
            _arc_to(path, x, y, rx, ry, phi, int(large), int(sweep), nx, ny)
            x, y = nx, ny
        else:
            raise ValueError("unsupported path command " + cmd)
    return path


def wordmark_path():
    """The four paths of the AERO wordmark as one QPainterPath in its own units (cached)."""
    if "path" not in _wordmark_cache:
        total = QtGui.QPainterPath()
        total.setFillRule(QtCore.Qt.WindingFill)
        for d in WORDMARK_PATHS:
            total.addPath(_parse_path(d))
        _wordmark_cache["path"] = total
    return _wordmark_cache["path"]


def wordmark_width(height):
    """Width of the wordmark for a given height."""
    return height * WORDMARK_BOX[2] / WORDMARK_BOX[3]


def paint_wordmark(p, rect):
    """Draw the wordmark in `rect` (it keeps its proportions, right-aligned): white on a dark skin, the logo gradient on a light one."""
    rect = QtCore.QRectF(rect)
    x0, y0, w, h = WORDMARK_BOX
    scale = min(rect.width() / w, rect.height() / h)
    ox = rect.right() - w * scale
    oy = rect.top() + (rect.height() - h * scale) / 2.0
    p.save()
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    p.translate(ox, oy)
    p.scale(scale, scale)
    p.translate(-x0, -y0)
    if tokens.DARK:
        brush = QtGui.QBrush(QtGui.QColor(tokens.ON_OVERLAY))
    else:
        gradient = QtGui.QLinearGradient(QtCore.QPointF(0, 10), QtCore.QPointF(w, 0))
        gradient.setColorAt(0.0, QtGui.QColor(tokens.LOGO_0))
        gradient.setColorAt(0.5, QtGui.QColor(tokens.LOGO_1))
        gradient.setColorAt(1.0, QtGui.QColor(tokens.LOGO_2))
        brush = QtGui.QBrush(gradient)
    p.setPen(QtCore.Qt.NoPen)
    p.setBrush(brush)
    p.drawPath(wordmark_path())
    p.restore()
