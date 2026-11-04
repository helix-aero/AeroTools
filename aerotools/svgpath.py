"""Chemin SVG -> QPainterPath, pour peindre une petite icone sans fichier ni QtSvg.

Commandes prises en charge : M m L l H h V v C c A a Z z (avec les repetitions
implicites des coordonnees). Les arcs sont convertis en courbes de Bezier.
"""

import math

from .qt import QtGui

_ARGS = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "A": 7, "Z": 0}


class _Scanner(object):
    def __init__(self, text):
        self.text, self.pos = text, 0

    def _skip(self):
        while self.pos < len(self.text) and self.text[self.pos] in " ,\t\r\n":
            self.pos += 1

    def peek(self):
        self._skip()
        return self.text[self.pos] if self.pos < len(self.text) else ""

    def command(self):
        char = self.peek()
        if char and char.isalpha():
            self.pos += 1
            return char
        return None

    def has_number(self):
        char = self.peek()
        return bool(char) and (char.isdigit() or char in "+-.")

    def number(self):
        self._skip()
        start = self.pos
        if self.pos < len(self.text) and self.text[self.pos] in "+-":
            self.pos += 1
        seen_dot = False
        while self.pos < len(self.text):
            char = self.text[self.pos]
            if char.isdigit():
                self.pos += 1
            elif char == "." and not seen_dot:
                seen_dot = True
                self.pos += 1
            else:
                break
        return float(self.text[start:self.pos])

    def flag(self):
        """Un drapeau d'arc : un seul chiffre (0 ou 1), parfois colle au suivant."""
        self._skip()
        char = self.text[self.pos]
        self.pos += 1
        return char == "1"


def _arc(path, x0, y0, rx, ry, rotation, large, sweep, x, y):
    """Ajoute l'arc elliptique SVG de (x0, y0) a (x, y) sous forme de courbes de Bezier."""
    if rx == 0 or ry == 0 or (x0 == x and y0 == y):
        path.lineTo(x, y)
        return
    rx, ry = abs(rx), abs(ry)
    phi = math.radians(rotation)
    cos_p, sin_p = math.cos(phi), math.sin(phi)
    dx, dy = (x0 - x) / 2.0, (y0 - y) / 2.0
    x1 = cos_p * dx + sin_p * dy
    y1 = -sin_p * dx + cos_p * dy
    scale = x1 * x1 / (rx * rx) + y1 * y1 / (ry * ry)
    if scale > 1:
        root = math.sqrt(scale)
        rx, ry = rx * root, ry * root
    num = rx * rx * ry * ry - rx * rx * y1 * y1 - ry * ry * x1 * x1
    den = rx * rx * y1 * y1 + ry * ry * x1 * x1
    coef = math.sqrt(max(0.0, num / den)) * (-1.0 if large == sweep else 1.0)
    cx1 = coef * rx * y1 / ry
    cy1 = -coef * ry * x1 / rx
    cx = cos_p * cx1 - sin_p * cy1 + (x0 + x) / 2.0
    cy = sin_p * cx1 + cos_p * cy1 + (y0 + y) / 2.0

    def angle(ux, uy, vx, vy):
        return math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)

    start = angle(1, 0, (x1 - cx1) / rx, (y1 - cy1) / ry)
    delta = angle((x1 - cx1) / rx, (y1 - cy1) / ry, (-x1 - cx1) / rx, (-y1 - cy1) / ry)
    if not sweep and delta > 0:
        delta -= 2 * math.pi
    elif sweep and delta < 0:
        delta += 2 * math.pi
    steps = max(1, int(math.ceil(abs(delta) / (math.pi / 2) - 1e-9)))
    step = delta / steps
    k = 4.0 / 3.0 * math.tan(step / 4.0)

    def point(u, v):
        return cx + rx * cos_p * u - ry * sin_p * v, cy + rx * sin_p * u + ry * cos_p * v

    for i in range(steps):
        a1 = start + i * step
        a2 = a1 + step
        c1 = point(math.cos(a1) - k * math.sin(a1), math.sin(a1) + k * math.cos(a1))
        c2 = point(math.cos(a2) + k * math.sin(a2), math.sin(a2) - k * math.cos(a2))
        end = point(math.cos(a2), math.sin(a2))
        path.cubicTo(c1[0], c1[1], c2[0], c2[1], end[0], end[1])


def parse(data):
    """QPainterPath des donnees `d` d'un chemin SVG."""
    path = QtGui.QPainterPath()
    scan = _Scanner(data)
    x = y = start_x = start_y = 0.0
    command = None
    while scan.peek():
        word = scan.command()
        if word is not None:
            command = word
        elif command is None or not scan.has_number():
            break
        upper = command.upper()
        relative = command != upper
        if upper == "Z":
            path.closeSubpath()
            x, y = start_x, start_y
            continue
        args = [scan.flag() if upper == "A" and i in (3, 4) else scan.number()
                for i in range(_ARGS[upper])]
        if upper == "M":
            nx, ny = args
            x, y = (x + nx, y + ny) if relative else (nx, ny)
            path.moveTo(x, y)
            start_x, start_y = x, y
            command = "l" if relative else "L"       # les paires suivantes sont des lignes
        elif upper == "L":
            nx, ny = args
            x, y = (x + nx, y + ny) if relative else (nx, ny)
            path.lineTo(x, y)
        elif upper == "H":
            x = x + args[0] if relative else args[0]
            path.lineTo(x, y)
        elif upper == "V":
            y = y + args[0] if relative else args[0]
            path.lineTo(x, y)
        elif upper == "C":
            pts = list(args)
            if relative:
                pts = [pts[i] + (x if i % 2 == 0 else y) for i in range(6)]
            path.cubicTo(*pts)
            x, y = pts[4], pts[5]
        elif upper == "A":
            rx, ry, rotation, large, sweep, nx, ny = args
            if relative:
                nx, ny = x + nx, y + ny
            _arc(path, x, y, rx, ry, rotation, large, sweep, nx, ny)
            x, y = nx, ny
    return path
