"""Drawing kit of the PDF reports: A4 page geometry, palette, fonts, the vector canvas, the cards and the PDF writer.

Shared by the tools that write a report (same palette, fonts, header band, footer, cards and table style). Needs Qt (QPdfWriter and
QPainter) and nothing else; the pages themselves are laid out by each tool.

Rendering choices: everything is painted as vector graphics. The variable fonts Rubik and Manrope do not keep their weights in a Qt PDF
(Qt5 only exposes the default instance, Qt6 embeds it without its variations): titles and key figures are painted as outlines
(QPainterPath), the weight coming from the font if Qt provides it, otherwise from a light outline stroke. Running text stays real,
selectable text, in Segoe UI (fallback: the sans-serif font of the machine).
"""

import math
import os
import re
import unicodedata

from . import i18n, theme
from .qt import QtCore, QtGui

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

DPI = 300
PT_TO_DEVICE = DPI / 72.0           # the painter works in points (1/72 inch)
MARGIN = 40.0
TEXT_SCALE = 10                     # text is drawn at 10x then scaled down
A4_W, A4_H = 595.28, 841.89         # points
TABLE_TOP = 78.0                    # first table row, under the page title
TABLE_HEAD_H = 24.0
SITE_URL = "https://helix.aero"
SITE_TEXT = "helix.aero"

# Report palette: always light, whatever the theme of the panel.
INK = "#111214"
INK_2 = "#2b2e33"
MUTED = "#5d6067"
FAINT = "#8c8e96"
PAPER = "#f6f6f4"
CARD = "#ffffff"
LINE = "#dcdcd6"
GRID = "#ebebe6"
WARN_BG = "#fde3d8"
WARN_TEXT = "#b83504"
GREEN = theme.GREEN
GREEN_DEEP = theme.GREEN_DEEP
ORANGE = theme.ORANGE

EM_DASH = "—"
MINUS = "−"
DOT = "·"


# --------------------------------------------------------------------------
# Text and file names
# --------------------------------------------------------------------------

def typo(text):
    """Typographic minus sign for running text."""
    return MINUS + text[1:] if text and text[0] == "-" else text



def safe_name(text, default="Chunk"):
    """Safe file name: ASCII, no spaces or special characters."""
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("._-")
    text = re.sub(r"_+", "_", text)
    return text[:60] or default



# --------------------------------------------------------------------------
# Drawing tools
# --------------------------------------------------------------------------

def font_families():
    try:
        return set(QtGui.QFontDatabase.families())
    except Exception:
        pass
    try:
        return set(QtGui.QFontDatabase().families())
    except Exception:
        return set()



def font_styles(family):
    try:
        return list(QtGui.QFontDatabase.styles(family))
    except Exception:
        pass
    try:
        return list(QtGui.QFontDatabase().styles(family))
    except Exception:
        return []



class Style(object):
    """Fonts of the report. One instance per PDF."""

    # Weights: 0 normal, 1 medium, 2 semi-bold, 3 bold.
    FAUX_STROKE = (0.010, 0.020, 0.032, 0.044)   # x size, if Qt only has one weight

    def __init__(self):
        try:
            title_family, _text_family = theme.load_fonts(QtGui)
        except Exception:
            title_family = "Segoe UI"
        self.display_family = title_family
        self.real_weights = len(font_styles("Rubik")) > 1
        families = font_families()
        self.body_family = "Segoe UI"
        for name in ("Segoe UI", "Helvetica Neue", "Arial", "DejaVu Sans"):
            if name in families:
                self.body_family = name
                break
        self._cache = {}

    def body_font(self, px, bold=False, italic=False, strike=False):
        font = QtGui.QFont(self.body_family)
        font.setPixelSize(max(1, int(round(px * TEXT_SCALE))))
        font.setBold(bool(bold))
        font.setItalic(bool(italic))
        font.setStrikeOut(bool(strike))
        font.setHintingPreference(QtGui.QFont.PreferNoHinting)
        return font

    def display_font(self, px, weight):
        """(font, stroke width in points)."""
        font = QtGui.QFont(self.display_family)
        font.setPixelSize(max(1, int(round(px * TEXT_SCALE))))
        font.setHintingPreference(QtGui.QFont.PreferNoHinting)
        weight = max(0, min(3, int(weight)))
        if self.real_weights:
            # Real Rubik: stay one step below the names to stay close to the Qt5 rendering.
            font.setWeight((QtGui.QFont.Normal, QtGui.QFont.Medium,
                            QtGui.QFont.Medium, QtGui.QFont.DemiBold)[weight])
            return font, 0.0
        font.setWeight(QtGui.QFont.Normal)
        return font, px * self.FAUX_STROKE[weight]

    def advance(self, text, px, kind, weight, italic=False):
        key = (text, round(px, 2), kind, weight, bool(italic))
        value = self._cache.get(key)
        if value is None:
            if kind == "display":
                font, _stroke = self.display_font(px, weight)
            else:
                font = self.body_font(px, weight >= 2, italic)
            metrics = QtGui.QFontMetricsF(font)
            getter = getattr(metrics, "horizontalAdvance", None) or metrics.width
            value = getter(text) / float(TEXT_SCALE)
            self._cache[key] = value
        return value



def qcolor(color, alpha=1.0):
    result = QtGui.QColor(color)
    if alpha < 1.0:
        result.setAlphaF(alpha)
    return result



def nice_top(value, divisions=4):
    """(top, step) of an axis that starts at 0 and covers `value`."""
    if value is None or value <= 0:
        return 1.0, 0.25
    raw = value / float(divisions)
    magnitude = 10 ** math.floor(math.log10(raw))
    step = magnitude * 10
    for factor in (1, 2, 2.5, 5, 10):
        if factor * magnitude >= raw - 1e-12:
            step = factor * magnitude
            break
    top = step * math.ceil(value / step - 1e-9)
    return top, step



def tick_decimals(step):
    for decimals in range(0, 5):
        if abs(step * 10 ** decimals - round(step * 10 ** decimals)) < 1e-9:
            return decimals
    return 4



class Canvas(object):
    """A QPainter and the drawing primitives of the report (units: points)."""

    def __init__(self, painter, style):
        self.p = painter
        self.st = style

    # -- text -------------------------------------------------------------

    def width(self, text, px, kind="body", weight=1, spacing=0.0, italic=False):
        text = str(text)
        if spacing:
            return sum(self.st.advance(ch, px, kind, weight, italic) + spacing for ch in text)
        return self.st.advance(text, px, kind, weight, italic)

    def text(self, x, y, text, px, color=INK, kind="body", weight=1, align="l",
             alpha=1.0, spacing=0.0, italic=False, strike=False):
        """Writes `text` with the baseline at `y`. Returns the width. `italic` and `strike` (struck through) apply to the body font."""
        text = str(text)
        if not text:
            return 0.0
        width = self.width(text, px, kind, weight, spacing, italic)
        if align == "r":
            x -= width
        elif align == "c":
            x -= width / 2.0
        if spacing:
            cursor = x
            for ch in text:
                self._draw(cursor, y, ch, px, color, kind, weight, alpha, italic, strike)
                cursor += self.st.advance(ch, px, kind, weight, italic) + spacing
        else:
            self._draw(x, y, text, px, color, kind, weight, alpha, italic, strike)
        return width

    def _draw(self, x, y, text, px, color, kind, weight, alpha, italic=False, strike=False):
        p = self.p
        col = qcolor(color, alpha)
        shrink = 1.0 / TEXT_SCALE
        if kind == "display":
            font, stroke = self.st.display_font(px, weight)
            path = QtGui.QPainterPath()
            path.addText(QtCore.QPointF(0.0, 0.0), font, text)
            transform = QtGui.QTransform()
            transform.translate(x, y)
            transform.scale(shrink, shrink)
            path = transform.map(path)
            if stroke > 0:
                pen = QtGui.QPen(col, stroke)
                pen.setJoinStyle(QtCore.Qt.RoundJoin)
                p.setPen(pen)
            else:
                p.setPen(QtCore.Qt.NoPen)
            p.setBrush(col)
            p.drawPath(path)
            return
        p.save()
        p.translate(x, y)
        p.scale(shrink, shrink)
        p.setFont(self.st.body_font(px, weight >= 2, italic, strike))
        p.setPen(col)
        p.drawText(QtCore.QPointF(0.0, 0.0), text)
        p.restore()

    def link(self, x, y, text, px, url, kind="body", weight=1, align="l"):
        """Makes the text already written with the baseline at `y` clickable (PDF link).

        An invisible QTextDocument carries the anchor: Qt turns it into a link annotation. The
        area follows the system font, within a few points of the font of the written text.
        """
        text = str(text)
        width = self.width(text, px, kind, weight)
        if align == "r":
            x -= width
        elif align == "c":
            x -= width / 2.0
        font = QtGui.QFont(self.st.body_family)
        font.setPixelSize(max(1, int(round(px))))
        doc = QtGui.QTextDocument()
        doc.setDefaultFont(font)
        doc.setDocumentMargin(0)
        doc.setHtml('<a href="{}">{}</a>'.format(url, text))
        p = self.p
        p.save()
        p.translate(x, y - QtGui.QFontMetricsF(font).ascent())
        p.setOpacity(0.0)
        doc.drawContents(p)
        p.restore()

    def fit(self, text, px, max_width, kind="body", weight=1):
        """Shortens `text` with an ellipsis to fit in `max_width`."""
        text = str(text)
        if self.width(text, px, kind, weight) <= max_width:
            return text
        while len(text) > 1 and self.width(text + "…", px, kind, weight) > max_width:
            text = text[:-1]
        return text.rstrip() + "…"

    def wrap(self, text, px, max_width, kind="body", weight=1, max_lines=3):
        """Splits `text` into lines of width `max_width` (at most `max_lines`)."""
        lines, current = [], ""
        for word in str(text).split():
            trial = (current + " " + word).strip()
            if current and self.width(trial, px, kind, weight) > max_width:
                lines.append(current)
                current = word
            else:
                current = trial
        if current:
            lines.append(current)
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = self.fit(lines[-1] + " …", px, max_width, kind, weight)
        return lines

    # -- shapes -----------------------------------------------------------

    def rect(self, x, y, w, h, fill=None, line=None, radius=0.0, line_w=0.6, alpha=1.0):
        p = self.p
        p.setPen(QtGui.QPen(qcolor(line), line_w) if line else QtCore.Qt.NoPen)
        p.setBrush(qcolor(fill, alpha) if fill else QtCore.Qt.NoBrush)
        if radius:
            p.drawRoundedRect(QtCore.QRectF(x, y, w, h), radius, radius)
        else:
            p.drawRect(QtCore.QRectF(x, y, w, h))

    def card(self, x, y, w, h, radius=7.0):
        self.rect(x, y, w, h, fill=CARD, line=LINE, radius=radius)

    def line(self, x1, y1, x2, y2, color=LINE, width=0.6, dash=None, alpha=1.0):
        pen = QtGui.QPen(qcolor(color, alpha), width)
        if dash:
            pen.setDashPattern([float(d) for d in dash])
        pen.setCapStyle(QtCore.Qt.FlatCap)
        self.p.setPen(pen)
        self.p.drawLine(QtCore.QPointF(x1, y1), QtCore.QPointF(x2, y2))

    def circle(self, cx, cy, r, fill=None, line=None, line_w=0.6, alpha=1.0, dash=None):
        p = self.p
        if line:
            pen = QtGui.QPen(qcolor(line), line_w)
            if dash:
                pen.setDashPattern([float(d) for d in dash])
            p.setPen(pen)
        else:
            p.setPen(QtCore.Qt.NoPen)
        p.setBrush(qcolor(fill, alpha) if fill else QtCore.Qt.NoBrush)
        p.drawEllipse(QtCore.QPointF(cx, cy), r, r)

    def image(self, image, x, y, w, h):
        self.p.drawImage(QtCore.QRectF(x, y, w, h), image)



# --------------------------------------------------------------------------
# Page elements
# --------------------------------------------------------------------------

def gradient_brush(x0, y0, x1, y1):
    gradient = QtGui.QLinearGradient(x0, y0, x1, y1)
    gradient.setColorAt(0.0, QtGui.QColor(theme.LOGO_0))
    gradient.setColorAt(0.5, QtGui.QColor(theme.LOGO_1))
    gradient.setColorAt(1.0, QtGui.QColor(theme.LOGO_2))
    return QtGui.QBrush(gradient)



def rule(cv, W, height=3.0):
    """Logo gradient rule at the top of the page."""
    cv.p.setPen(QtCore.Qt.NoPen)
    cv.p.setBrush(gradient_brush(0, height, W, 0))
    cv.p.drawRect(QtCore.QRectF(0, 0, W, height))



def kpi_card(cv, x, y, w, h, label, value, sub=None, accent=GREEN):
    cv.card(x, y, w, h)
    cv.rect(x, y + 12, 3, h - 24, fill=accent, radius=1.5)
    cv.text(x + 14, y + 19, label.upper(), 6.8, MUTED, "body", 2, spacing=1.0)
    cv.text(x + 14, y + 44, value, 20, INK, "display", 2)
    if sub:
        cv.text(x + 14, y + 58, cv.fit(sub, 7.8, w - 26), 7.8, MUTED)



def card_title(cv, x, y, title, subtitle=None):
    cv.text(x + 14, y + 22, title, 11.5, INK, "display", 2)
    if subtitle:
        cv.text(x + 14, y + 35, subtitle, 7.6, MUTED)



def empty_note(cv, x, y, w, h, text=None):
    cv.text(x + w / 2.0, y + h / 2.0 + 3, text or i18n.t("report.empty"), 8.5, FAINT, align="c")



# --------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------

def new_writer(path, title, creator):
    writer = QtGui.QPdfWriter(path)
    try:
        writer.setPageSize(QtGui.QPageSize(QtGui.QPageSize.A4))
    except Exception:
        writer.setPageSize(QtGui.QPagedPaintDevice.A4)
    try:
        writer.setPageMargins(QtCore.QMarginsF(0.0, 0.0, 0.0, 0.0))
    except Exception:
        pass
    writer.setResolution(DPI)
    writer.setTitle(title)
    writer.setCreator(creator)
    return writer



def remove(path):
    try:
        os.remove(path)
    except OSError:
        pass

