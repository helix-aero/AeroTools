"""FastGCP Qt panel: a dock at the right of Metashape.

Main view: title, Undo button, Play / Pause with the state of the tool, current marker (two counters),
search, marker table (ID, X / Y / Z errors, number of projections, sortable columns).
The footer carries the status, the Export menu and the HELIX credit (site link and
GitHub). The gear opens the Settings window (settings_dialog.py): options,
log, AI models and retraining.

The photo filter, the synchronisation with the Reference pane and the
move to the next photo are automatic: the panel does not offer them.

Rules of this module:
  - no direct call to Metashape: everything goes through `msapi`;
  - no hard-coded style: colours, margins and objectName come from `theme`;
  - no help sentence, and no tooltip other than the two counter pills
    and the GitHub icon of the credit;
  - no hard-coded visible text: everything goes through `i18n.t(...)`;
  - never an exception towards the caller: a marker reference becomes
    invalid (RuntimeError) as soon as the chunk changes, the panel must
    survive and reread the list.
"""

import functools
import math
import os
import re
import time

from ... import config, i18n, journal, msapi, svgpath, theme
from ...qt import QtCore, QtGui, QtWidgets
from . import aigcp, history, qc

SITE_URL = "https://helix.aero"
SITE_NAME = "helix.aero"
GITHUB_URL = "https://github.com/helix-aero/AeroTools"
SETTLE_MS = (500, 2000)               # rereads of the table after a bulk modification
HEADER_BUTTON = 26                    # round buttons at the top: Undo, Redo, settings
HEADER_ICON = 14
LOGO_SCALE = 2                        # the logo is drawn at double resolution
LOGO_WIDTH = 96                       # displayed width of the AERO logo (the kit asks for 64 px at least)
LOGO_FILE = "helix-aero-AERO-degrade-512px.png"
LABEL_MAX = 10                        # maximum length of the current marker name in its card

# Lines visible in the unfolded log.
LOG_VISIBLE_LINES = 6

# Minimum width of the dock: the marker table needs room.
MIN_PANEL_WIDTH = 420

# Same height for all the buttons and lists of the panel; Run is a small button.
CONTROL_HEIGHT = 30
RUN_WIDTH = 124
RUN_HEIGHT = 40
RUN_ICON = 14
QC_WIDTH = 140
ADJUST_WIDTH = 96
EXPORT_WIDTH = 112
CANCEL_WIDTH = 84
PROGRESS_HEIGHT = 22                  # detection bar (text inside) and Cancel button at its right

# Text of each check box of the Settings card (option key: text key).
CHECK_TEXTS = {
    "collect_dataset": "panel.save_picks",
    "ai_auto_review": "aigcp.auto_review",
}

# Text of each choice of the AI detection drop-downs (literal text key,
# so that the unused-texts test sees it).
AIGCP_CHOICE_TEXTS = {
    ("type", "checker"): "aigcp.type_checker",
    ("type", "spray"): "aigcp.type_spray",
    ("scope", "marker"): "aigcp.scope_marker",
    ("scope", "all"): "aigcp.scope_all",
    ("ignore", "both"): "aigcp.ignore_both",
    ("ignore", "green"): "aigcp.ignore_green",
    ("ignore", "blue"): "aigcp.ignore_blue",
    ("ignore", "none"): "aigcp.ignore_none",
}

# Minimum height of the marker table (it takes all the rest of the dock).
LIST_MIN_HEIGHT = 120

# Table columns: (title text key, sort key, width in pixels;
# 0 = stretches). The title is read with `i18n.t`, see `column_titles()`.
COLUMNS = (
    ("panel.col.id", "id", 0),
    ("panel.col.x", "x", 76),
    ("panel.col.y", "y", 76),
    ("panel.col.z", "z", 76),
    ("panel.col.proj", "proj", 54),
)
HEADER_HEIGHT = 30
HEADER_FONT_PX = 12
NO_VALUE = "–"

# Entries of the Export menu: (key, text key). The functions are in report.py.
EXPORTS = (
    ("pdf", "panel.export.pdf"),
    ("csv", "panel.export.csv"),
)

# Maximum width of the Export button text: beyond it, it is elided.
EXPORT_TEXT_MAX_PX = 150

# Qt ignores `letter-spacing` in style sheets: it is applied on the
# font of the titles (in pixels, like the theme value).
EYEBROW_SPACING = 1.4

# A click on a new item triggers currentItemChanged then itemClicked:
# the same marker is only handled once within this window.
PICK_DEBOUNCE_S = 0.2

DOT_SIZE = 10    # diameter of the list dot, in pixels
ROW_HEIGHT = 28  # height of a list row
_DOT_ICONS = {}
_ICONS = {}

# The icons are painted at twice the size then marked as such:
# sharp on a normal screen and on a high-density screen.
ICON_SCALE = 2

ROLE_MARKER = QtCore.Qt.UserRole
ROLE_LABEL = QtCore.Qt.UserRole + 1


# ----------------------------------------------------------------------
# Table: formats and sorts (no Qt, testable on their own)
# ----------------------------------------------------------------------

def format_error(value):
    """Error in metres, short format (0.012). Without a value: a dash."""
    if value is None:
        return NO_VALUE
    try:
        number = float(value)
    except (TypeError, ValueError):
        return NO_VALUE
    if number != number or number in (float("inf"), float("-inf")):
        return NO_VALUE
    magnitude = abs(number)
    if magnitude >= 1000:
        text = "{:.1f}".format(number)
    elif magnitude >= 100:
        text = "{:.2f}".format(number)
    else:
        text = "{:.3f}".format(number)
    # -0.000 tells nothing: it is written 0.000.
    return text[1:] if text.startswith("-") and float(text) == 0 else text


def natural_key(label):
    """Natural sort key: GCP2 before GCP10, case-insensitive."""
    parts = re.split(r"(\d+)", str(label).lower())
    return tuple((0, int(part)) if part.isdigit() else (1, part) for part in parts if part)


def sort_rows(rows, key, descending=False):
    """Sort the table rows. `key`: "id", "x", "y", "z", "proj" or None.

    None (or an unknown key) keeps the project order. The ID is sorted in natural
    order; the errors and the number of projections are sorted by absolute
    value, rows without a value always stay at the end, whatever the
    direction. On a tie, the project order is kept.
    """
    rows = list(rows)
    if key == "id":
        return sorted(rows, key=lambda row: natural_key(row["label"]), reverse=descending)
    if key not in ("x", "y", "z", "proj"):
        return rows
    valued = [row for row in rows if row.get(key) is not None]
    missing = [row for row in rows if row.get(key) is None]
    valued.sort(key=lambda row: abs(row[key]), reverse=descending)
    return valued + missing


def _qcolor(css):
    """QColor from a theme colour, including the rgba(r, g, b, a) format."""
    if css.startswith("rgba"):
        numbers = [float(part) for part in re.findall(r"[\d.]+", css)]
        red, green, blue = [int(n) for n in numbers[:3]]
        return QtGui.QColor(red, green, blue, int(round(numbers[3] * 255)))
    return QtGui.QColor(css)


def _text_width(metrics, text):
    """Width of a text in pixels (Qt 5.11+ and Qt 6)."""
    if hasattr(metrics, "horizontalAdvance"):
        return metrics.horizontalAdvance(text)
    return metrics.width(text)


def column_titles():
    """Table titles."""
    return [i18n.t(key) for key, _sort, _width in COLUMNS]


def _repolish(widget):
    """Apply the style sheet again after an objectName change."""
    try:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()
    except RuntimeError:
        pass


def _eyebrow(text):
    """Theme title (spaced capitals), elided if it is too long.

    Same objectName as `theme.eyebrow`, with the letter spacing that the
    QSS cannot do.
    """
    label = _ElidedLabel("aeroEyebrow")
    font = label.font()
    font.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, EYEBROW_SPACING)
    label.setFont(font)
    label.set_full_text(text.upper())
    return label


# ----------------------------------------------------------------------
# Painted icons (QPainter -> QPixmap -> QIcon), never a unicode character
# ----------------------------------------------------------------------

def _paint(width, height, draw):
    """Transparent `width` x `height` pixmap (logical px), drawn by `draw(painter)`."""
    pixmap = QtGui.QPixmap(width * ICON_SCALE, height * ICON_SCALE)
    pixmap.fill(QtCore.Qt.transparent)
    pixmap.setDevicePixelRatio(ICON_SCALE)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    try:
        draw(painter)
    finally:
        painter.end()
    return pixmap


def _pen(color, width):
    pen = QtGui.QPen(QtGui.QColor(color), width)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    pen.setJoinStyle(QtCore.Qt.RoundJoin)
    return pen


def _cached(key, build):
    pixmap = _ICONS.get(key)
    if pixmap is None:
        pixmap = build()
        _ICONS[key] = pixmap
    return pixmap


# Round Play / Pause button: a single icon, no text.
PLAY_BUTTON = 36
PLAY_ICON = 16


def _play_pixmap(running, color):
    def build():
        def draw(painter):
            painter.setBrush(QtGui.QColor(color))
            if running:
                # PAUSE: two bars.
                painter.setPen(QtCore.Qt.NoPen)
                painter.drawRoundedRect(QtCore.QRectF(3.2, 2.2, 3.2, 11.6), 1.2, 1.2)
                painter.drawRoundedRect(QtCore.QRectF(9.6, 2.2, 3.2, 11.6), 1.2, 1.2)
            else:
                # PLAY: triangle, shifted to the right to look centred.
                painter.setPen(_pen(color, 1.4))
                painter.drawPolygon(QtGui.QPolygonF([
                    QtCore.QPointF(4.6, 2.6),
                    QtCore.QPointF(13.0, 8.0),
                    QtCore.QPointF(4.6, 13.4),
                ]))
        return _paint(PLAY_ICON, PLAY_ICON, draw)
    return _cached(("play", running, color), build)


def _play_icon(running):
    """PAUSE when the tool runs (on the green), PLAY when it is stopped."""
    color = theme.GREEN_ON if running else theme.FG_1
    return QtGui.QIcon(_play_pixmap(running, color))


def _run_pixmap(color):
    """Filled triangle pointing to the right (the Run button)."""
    def build():
        def draw(painter):
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor(color))
            painter.drawPolygon(QtGui.QPolygonF([
                QtCore.QPointF(3.4, 1.8),
                QtCore.QPointF(13.0, 8.0),
                QtCore.QPointF(3.4, 14.2),
            ]))
        return _paint(PLAY_ICON, PLAY_ICON, draw)
    return _cached(("run", color), build)


def _run_icon():
    icon = QtGui.QIcon()
    icon.addPixmap(_run_pixmap(theme.GREEN_ON), QtGui.QIcon.Normal)
    icon.addPixmap(_run_pixmap(theme.FG_4), QtGui.QIcon.Disabled)
    return icon


STATE_DOT = 10


def _state_dot_pixmap(running):
    """Small round dot: green if the tool runs, grey otherwise."""
    color = theme.GREEN if running else theme.FG_2

    def build():
        def draw(painter):
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor(color))
            painter.drawEllipse(QtCore.QRectF(0.5, 0.5, STATE_DOT - 1.0, STATE_DOT - 1.0))
        return _paint(STATE_DOT, STATE_DOT, draw)
    return _cached(("state", running, color), build)


COUNTER_ICON = 14


def _counter_pixmap(kind, color):
    def build():
        def draw(painter):
            size = float(COUNTER_ICON)
            if kind == "target":
                painter.setPen(_pen(color, 1.4))
                painter.setBrush(QtCore.Qt.NoBrush)
                painter.drawEllipse(QtCore.QPointF(size / 2, size / 2), 5.4, 5.4)
                painter.setPen(QtCore.Qt.NoPen)
                painter.setBrush(QtGui.QColor(color))
                painter.drawEllipse(QtCore.QPointF(size / 2, size / 2), 1.9, 1.9)
            else:
                painter.setPen(_pen(color, 1.3))
                painter.setBrush(QtCore.Qt.NoBrush)
                painter.drawRoundedRect(QtCore.QRectF(1.2, 2.2, 11.6, 9.6), 2.2, 2.2)
                relief = QtGui.QPainterPath()
                relief.moveTo(2.8, 10.2)
                relief.lineTo(5.6, 6.6)
                relief.lineTo(7.6, 8.8)
                relief.lineTo(9.0, 7.4)
                relief.lineTo(11.2, 10.2)
                painter.drawPath(relief)
                painter.setPen(QtCore.Qt.NoPen)
                painter.setBrush(QtGui.QColor(color))
                painter.drawEllipse(QtCore.QPointF(9.6, 5.0), 0.95, 0.95)
        return _paint(COUNTER_ICON, COUNTER_ICON, draw)
    return _cached(("counter", kind, color), build)


GEAR_ICON = 18
GITHUB_ICON = 16

# GitHub mark (Octicons mark-github, 16 x 16), painted without a file or QtSvg.
GITHUB_PATH = (
    "M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49"
    "-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82"
    ".72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15"
    "-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04"
    " 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25"
    ".54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"
)


def _github_pixmap(color):
    def build():
        def draw(painter):
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor(color))
            painter.drawPath(svgpath.parse(GITHUB_PATH))
        return _paint(GITHUB_ICON, GITHUB_ICON, draw)
    return _cached(("github", color), build)


def short_label(label, limit=LABEL_MAX):
    """Marker name truncated to `limit` characters, followed by three dots."""
    label = label or ""
    return label if len(label) <= limit else label[:limit] + "..."


def _gear_pixmap(color):
    def build():
        def draw(painter):
            size = float(GEAR_ICON)
            centre = size / 2
            outer, inner, teeth = 8.2, 6.0, 8
            path = QtGui.QPainterPath()
            path.setFillRule(QtCore.Qt.OddEvenFill)
            points = []
            step = math.pi * 2 / teeth
            half_tooth = step * 0.22
            half_gap = step * 0.28
            for i in range(teeth):
                mid = i * step - math.pi / 2
                for angle, radius in (
                    (mid - half_gap - half_tooth, inner),
                    (mid - half_tooth, outer),
                    (mid + half_tooth, outer),
                    (mid + half_gap + half_tooth, inner),
                ):
                    points.append(QtCore.QPointF(
                        centre + radius * math.cos(angle),
                        centre + radius * math.sin(angle),
                    ))
            path.addPolygon(QtGui.QPolygonF(points + [points[0]]))
            path.addEllipse(QtCore.QPointF(centre, centre), 2.6, 2.6)
            painter.setPen(_pen(color, 0.8))
            painter.setBrush(QtGui.QColor(color))
            painter.drawPath(path)
        return _paint(GEAR_ICON, GEAR_ICON, draw)
    return _cached(("gear", color), build)


UNDO_ICON = 18


def _undo_pixmap(color, opacity=1.0):
    """Curved arrow going back (undo)."""
    def build():
        def draw(painter):
            painter.setOpacity(opacity)
            painter.setPen(_pen(color, 1.6))
            painter.setBrush(QtCore.Qt.NoBrush)
            path = QtGui.QPainterPath()
            path.moveTo(4.6, 6.4)
            path.lineTo(10.2, 6.4)
            path.cubicTo(13.6, 6.4, 15.4, 8.4, 15.4, 10.9)
            path.cubicTo(15.4, 13.4, 13.4, 15.2, 10.6, 15.2)
            path.lineTo(6.4, 15.2)
            painter.drawPath(path)
            head = QtGui.QPainterPath()
            head.moveTo(7.6, 3.2)
            head.lineTo(4.0, 6.4)
            head.lineTo(7.6, 9.6)
            painter.drawPath(head)
        return _paint(UNDO_ICON, UNDO_ICON, draw)
    return _cached(("undo", color, opacity), build)


def _undo_icon(mirror=False):
    """Icon of the Undo button (or Redo, mirrored), with its greyed version."""
    def pick(pixmap):
        return pixmap.transformed(QtGui.QTransform().scale(-1, 1)) if mirror else pixmap

    icon = QtGui.QIcon()
    icon.addPixmap(pick(_undo_pixmap(theme.FG_2)), QtGui.QIcon.Normal)
    icon.addPixmap(pick(_undo_pixmap(theme.FG_3, 0.45)), QtGui.QIcon.Disabled)
    return icon


EXPORT_ICON = 16


def _export_pixmap(color):
    """Arrow going down into a tray (export)."""
    def build():
        def draw(painter):
            painter.setPen(_pen(color, 1.5))
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.drawLine(QtCore.QPointF(8.0, 2.0), QtCore.QPointF(8.0, 9.6))
            head = QtGui.QPainterPath()
            head.moveTo(4.8, 6.6)
            head.lineTo(8.0, 9.8)
            head.lineTo(11.2, 6.6)
            painter.drawPath(head)
            tray = QtGui.QPainterPath()
            tray.moveTo(2.6, 10.4)
            tray.lineTo(2.6, 13.4)
            tray.lineTo(13.4, 13.4)
            tray.lineTo(13.4, 10.4)
            painter.drawPath(tray)
        return _paint(EXPORT_ICON, EXPORT_ICON, draw)
    return _cached(("export", color), build)


def _dot_icon(done):
    """Painted round dot: green if the marker is marked, grey otherwise.

    It is drawn instead of using the characters ● ○: their presence depends
    on the font and they do not take the theme colour in a QSS list.
    """
    icon = _DOT_ICONS.get(done)
    if icon is not None:
        return icon
    size = DOT_SIZE + 2
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    if done:
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(theme.GREEN))
    else:
        painter.setPen(QtGui.QPen(QtGui.QColor(theme.FG_3), 1.4))
        painter.setBrush(QtCore.Qt.NoBrush)
    painter.drawEllipse(1, 1, DOT_SIZE - 1, DOT_SIZE - 1)
    painter.end()
    icon = QtGui.QIcon(pixmap)
    _DOT_ICONS[done] = icon
    return icon


# ----------------------------------------------------------------------
# Small widgets
# ----------------------------------------------------------------------

class _LinkFrame(QtWidgets.QFrame):
    """Clickable frame: a click opens the address in the browser."""

    def __init__(self, url, parent=None):
        super(_LinkFrame, self).__init__(parent)
        self._url = url
        self.setCursor(QtCore.Qt.PointingHandCursor)

    def mousePressEvent(self, event):
        try:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(self._url))
        except Exception as exc:
            journal.write("FastGCP: banner link: {!r}".format(exc))
        super(_LinkFrame, self).mousePressEvent(event)


class _ElidedLabel(QtWidgets.QLabel):
    """Single-line label, truncated by "…" instead of widening the dock."""

    def __init__(self, object_name, parent=None):
        super(_ElidedLabel, self).__init__(parent)
        self.setObjectName(object_name)
        self._full = ""
        self.setMinimumWidth(0)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred
        )

    def set_full_text(self, text):
        self._full = text or ""
        self._elide()

    def _elide(self):
        width = self.width()
        if width <= 0:
            shown = self._full
        else:
            shown = self.fontMetrics().elidedText(
                self._full, QtCore.Qt.ElideRight, width
            )
        if shown != self.text():
            QtWidgets.QLabel.setText(self, shown)

    def resizeEvent(self, event):
        super(_ElidedLabel, self).resizeEvent(event)
        self._elide()

    def changeEvent(self, event):
        super(_ElidedLabel, self).changeEvent(event)
        if event.type() in (QtCore.QEvent.FontChange, QtCore.QEvent.StyleChange):
            self._elide()


class _Switch(QtWidgets.QPushButton):
    """Round ON / OFF switch: green track and dot on the right when the tool runs, grey otherwise."""

    WIDTH, HEIGHT = 46, 26

    def __init__(self, parent=None):
        super(_Switch, self).__init__(parent)
        self._on = True
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setCursor(QtCore.Qt.PointingHandCursor)

    def set_on(self, on):
        self._on = bool(on)
        self.update()

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        width, height = float(self.width()), float(self.height())
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(theme.GREEN if self._on else theme.GROUND_4))
        painter.drawRoundedRect(QtCore.QRectF(0, 0, width, height), height / 2.0, height / 2.0)
        knob = height - 6.0
        left = width - knob - 3.0 if self._on else 3.0
        painter.setBrush(QtGui.QColor("#ffffff" if self._on else theme.FG_2))
        painter.drawEllipse(QtCore.QRectF(left, 3.0, knob, knob))
        painter.end()


class _ElidedButton(QtWidgets.QPushButton):
    """Full-width button whose text is truncated by an ellipsis if it does not fit."""

    def __init__(self, text="", parent=None):
        super(_ElidedButton, self).__init__(parent)
        self._full = ""
        self.setMinimumWidth(0)
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        self.set_full_text(text)

    def set_full_text(self, text):
        self._full = text or ""
        self._elide()

    def _elide(self):
        room = self.width() - 28
        shown = self._full
        if room > 0:
            shown = self.fontMetrics().elidedText(self._full, QtCore.Qt.ElideRight, room)
        if shown != self.text():
            QtWidgets.QPushButton.setText(self, shown)

    def resizeEvent(self, event):
        super(_ElidedButton, self).resizeEvent(event)
        self._elide()

    def changeEvent(self, event):
        super(_ElidedButton, self).changeEvent(event)
        if event.type() in (QtCore.QEvent.FontChange, QtCore.QEvent.StyleChange):
            self._elide()


class _ElidedCheck(QtWidgets.QCheckBox):
    """Check box whose text is truncated by an ellipsis if it does not fit."""

    INDICATOR_ROOM = 14 + 8 + 6   # box, space, margin

    def __init__(self, text="", parent=None):
        super(_ElidedCheck, self).__init__(parent)
        self._full = ""
        self.setMinimumWidth(0)
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        self.set_full_text(text)

    def set_full_text(self, text):
        self._full = text or ""
        self._elide()

    def _elide(self):
        room = self.width() - self.INDICATOR_ROOM
        shown = self._full
        if room > 0:
            shown = self.fontMetrics().elidedText(self._full, QtCore.Qt.ElideRight, room)
        if shown != self.text():
            QtWidgets.QCheckBox.setText(self, shown)

    def resizeEvent(self, event):
        super(_ElidedCheck, self).resizeEvent(event)
        self._elide()

    def changeEvent(self, event):
        super(_ElidedCheck, self).changeEvent(event)
        if event.type() in (QtCore.QEvent.FontChange, QtCore.QEvent.StyleChange):
            self._elide()


class _GridHeader(QtWidgets.QHeaderView):
    """Table header, painted by hand: frame background, discreet title,
    rule below, sort arrow drawn in the theme colours (the QSS cannot
    paint the arrow without an external image)."""

    PAD_X = 7
    ARROW_W = 7
    ARROW_H = 4
    GAP = 4

    def __init__(self, parent=None):
        super(_GridHeader, self).__init__(QtCore.Qt.Horizontal, parent)
        self.setSectionsClickable(True)
        self.setSectionsMovable(False)
        self.setHighlightSections(False)
        self.setSortIndicatorShown(True)
        self.setCursor(QtCore.Qt.PointingHandCursor)

    def sizeHint(self):
        base = super(_GridHeader, self).sizeHint()
        return QtCore.QSize(base.width(), HEADER_HEIGHT)

    def paintSection(self, painter, rect, logical_index):
        if not rect.isValid():
            return
        painter.save()
        try:
            self._paint(painter, rect, logical_index)
        finally:
            painter.restore()

    def _paint(self, painter, rect, index):
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        # Rule under the header.
        painter.setPen(QtGui.QPen(_qcolor(theme.BORDER_SOFT), 1))
        painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())

        model = self.model()
        text = ""
        if model is not None:
            text = model.headerData(index, QtCore.Qt.Horizontal) or ""
        sorted_here = self.sortIndicatorSection() == index
        color = _qcolor(theme.FG_0 if sorted_here else theme.FG_1)
        font = QtGui.QFont(self.font())
        font.setPixelSize(HEADER_FONT_PX)
        font.setWeight(QtGui.QFont.DemiBold)
        painter.setFont(font)
        metrics = QtGui.QFontMetrics(font)

        right_aligned = index != 0
        room = rect.width() - self.PAD_X - 3
        if sorted_here:
            room -= self.ARROW_W + self.GAP
        shown = metrics.elidedText(str(text), QtCore.Qt.ElideRight, max(room, 0))
        width = _text_width(metrics, shown)
        arrow_total = (self.ARROW_W + self.GAP) if sorted_here else 0
        if right_aligned:
            text_x = rect.right() - self.PAD_X - width + 1
            arrow_x = text_x - self.GAP - self.ARROW_W
        else:
            text_x = rect.left() + self.PAD_X + 1
            arrow_x = text_x + width + self.GAP
        baseline = int(round(
            rect.top() + (rect.height() - 1 + metrics.ascent() - metrics.descent()) / 2.0
        ))
        painter.setPen(color)
        painter.drawText(int(text_x), baseline, shown)

        if sorted_here and arrow_total:
            middle = (rect.height() - 1) / 2.0 + rect.top()
            up = self.sortIndicatorOrder() == QtCore.Qt.AscendingOrder
            half_h = self.ARROW_H / 2.0
            top = middle - half_h
            bottom = middle + half_h
            if up:
                points = [(arrow_x, bottom), (arrow_x + self.ARROW_W, bottom),
                          (arrow_x + self.ARROW_W / 2.0, top)]
            else:
                points = [(arrow_x, top), (arrow_x + self.ARROW_W, top),
                          (arrow_x + self.ARROW_W / 2.0, bottom)]
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(color)
            painter.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(x, y) for x, y in points]))


class _NumberDelegate(QtWidgets.QStyledItemDelegate):
    """Value columns: right-aligned text, tabular digits.

    The theme fonts have no fixed-width digits (the 1 is narrower)
    and Qt 5 cannot enable the tnum feature: each digit is therefore
    placed in a cell as wide as the widest one, which aligns the
    decimals from one row to the next. The background, the selection and the hover stay
    those of the style (hence of the QSS)."""

    PAD_RIGHT = 8

    def paint(self, painter, option, index):
        opt = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text = opt.text
        opt.text = ""
        widget = opt.widget
        style = widget.style() if widget is not None else QtWidgets.QApplication.style()
        style.drawControl(QtWidgets.QStyle.CE_ItemViewItem, opt, painter, widget)

        selected = bool(opt.state & QtWidgets.QStyle.State_Selected)
        brush = index.data(QtCore.Qt.ForegroundRole)
        muted = False
        warn = False
        if brush is not None:
            try:
                name = brush.color().name().lower()
                muted = name == _qcolor(theme.FG_3).name().lower()
                warn = name == _qcolor(theme.OFF_TEXT).name().lower()
            except Exception:
                muted = warn = False
        if warn:
            color = _qcolor(theme.OFF_TEXT)
        elif selected:
            color = _qcolor(theme.FG_1 if muted else theme.FG_0)
        else:
            color = _qcolor(theme.FG_3 if muted else theme.FG_1)

        painter.save()
        try:
            painter.setFont(opt.font)
            painter.setPen(color)
            metrics = QtGui.QFontMetrics(opt.font)
            cell = max(_text_width(metrics, digit) for digit in "0123456789")
            baseline = int(round(
                opt.rect.top() + (opt.rect.height() + metrics.ascent() - metrics.descent()) / 2.0
            ))
            x = opt.rect.right() - self.PAD_RIGHT + 1
            for char in reversed(text):
                natural = _text_width(metrics, char)
                advance = cell if char.isdigit() else natural
                x -= advance
                painter.drawText(int(x + (advance - natural) / 2.0), baseline, char)
        finally:
            painter.restore()


class _Counter(QtWidgets.QFrame):
    """Counter pill: a small painted icon and a number, no word.

    The only part of the panel that carries a tooltip. The children let
    the mouse through: hovering the icon or the number amounts to hovering the
    whole pill.
    """

    def __init__(self, kind, tip, parent=None):
        super(_Counter, self).__init__(parent)
        self.setObjectName("aeroCounter")
        self._kind = kind
        self.set_tip(tip)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(8, 3, 10, 3)
        layout.setSpacing(5)
        self._icon = QtWidgets.QLabel()
        self._icon.setFixedSize(COUNTER_ICON, COUNTER_ICON)
        self._number = QtWidgets.QLabel("0")
        self._number.setObjectName("aeroCounterNum")
        for child in (self._icon, self._number):
            child.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        layout.addWidget(self._icon)
        layout.addWidget(self._number)
        self.setSizePolicy(QtWidgets.QSizePolicy.Maximum, QtWidgets.QSizePolicy.Fixed)
        self.apply_theme()

    def apply_theme(self):
        """Icon in the colours of the active palette, tooltip in the theme style."""
        self._icon.setPixmap(_counter_pixmap(self._kind, theme.FG_1))
        self.setStyleSheet(theme.tooltip_stylesheet())

    def set_number(self, number):
        self._number.setText(str(int(number)))

    def set_tip(self, tip):
        self.setToolTip(tip)


class FastGcpPanel(QtWidgets.QDockWidget):
    """FastGCP dock. Public contract: see the methods without an underscore."""

    # The journal lines may come from another thread: a signal puts them
    # back in the interface thread.
    _line_added = QtCore.Signal(str)

    def __init__(self, state, parent=None):
        super(FastGcpPanel, self).__init__("Aero-GCP", parent)
        self._state = state
        self._journal_cb = None
        self._log_open = False
        self._running = True
        self._last_pick = (None, 0.0)
        self._checks = {}
        self._aigcp_combos = {}
        self._review_dialog = None
        # Table sort: (key, descending) or None = project order.
        self._sort = None
        self._signature = None            # snapshot of the table, to see what Metashape changed
        self.poll_cost = 0.0
        # Chunk name displayed after the progress (set by tool.py).
        self._chunk_name = ""

        self.setObjectName("AeroFastGcpDock")
        # The custom header acts as the bar: the native bar disappears.
        self.setTitleBarWidget(QtWidgets.QWidget())
        self._root = self._build_root()
        self.setWidget(self._root)

        self._load_options()
        self._refresh_icons()
        self._subscribe_journal()
        history.subscribe(self._on_history_changed)
        self._update_undo_button()
        self.update_active_card()
        self.set_status("")

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_root(self):
        root = QtWidgets.QWidget()
        root.setObjectName("aeroPanel")
        root.setMinimumWidth(MIN_PANEL_WIDTH)
        root.setStyleSheet(theme.stylesheet())

        outer = QtWidgets.QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QtWidgets.QScrollArea()
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)

        body = QtWidgets.QWidget()
        scroll.setWidget(body)
        # QScrollArea paints its content: the panel background is left visible.
        body.setAutoFillBackground(False)
        scroll.viewport().setAutoFillBackground(False)

        column = QtWidgets.QVBoxLayout(body)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self._build_banner())
        column.addWidget(self._build_header())
        column.addWidget(theme.gradient_rule(QtWidgets))

        content = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(content)
        layout.setContentsMargins(theme.PAD_L, theme.PAD_L, theme.PAD_L, theme.PAD_L)
        layout.setSpacing(theme.PAD)
        layout.addLayout(self._build_play())
        layout.addWidget(self._build_aigcp_card())
        layout.addWidget(self._build_active_card())
        layout.addWidget(self._build_search())
        layout.addWidget(self._build_list(), 1)
        self._build_more()                # opened in the Settings window, never in the column
        layout.addLayout(self._build_footer())
        layout.addLayout(self._build_credit())
        column.addWidget(content, 1)
        return root

    def _build_banner(self):
        """At the very top: green rule, HELIX logo (white on the grey) and a presentation line.

        The whole banner is a link to the site.
        """
        holder = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(holder)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        top = QtWidgets.QFrame()
        top.setObjectName("aeroBannerTop")
        top.setFixedHeight(3)
        outer.addWidget(top)

        banner = _LinkFrame(SITE_URL)
        banner.setObjectName("aeroBanner")
        row = QtWidgets.QHBoxLayout(banner)
        row.setContentsMargins(theme.PAD_L, theme.PAD, theme.PAD_L, theme.PAD)
        row.setSpacing(theme.PAD_L)
        logo = QtWidgets.QLabel()
        pixmap = QtGui.QPixmap(os.path.join(config.ROOT, "assets", "logo", LOGO_FILE))
        if pixmap.isNull():
            logo.setText("AERO")
            logo.setObjectName("aeroTitle")
        else:
            # AERO text logo, gradient version of the kit approved on 2026-10-31.
            pixmap = pixmap.scaledToWidth(LOGO_WIDTH * LOGO_SCALE, QtCore.Qt.SmoothTransformation)
            pixmap.setDevicePixelRatio(LOGO_SCALE)
            logo.setPixmap(pixmap)
        logo.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        row.addWidget(logo, 0)
        self.ad = _ElidedLabel("aeroMeta")
        self.ad.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        self.ad.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.ad.set_full_text(i18n.t("panel.ad", site=SITE_NAME))
        row.addWidget(self.ad, 1)
        outer.addWidget(banner)
        return holder

    def _build_header(self):
        """Title on the left, on the right: Undo, Redo, settings (gear)."""
        header = QtWidgets.QWidget()
        header.setObjectName("aeroHeader")
        layout = QtWidgets.QHBoxLayout(header)
        layout.setContentsMargins(theme.PAD_L, theme.PAD + theme.PAD_S, theme.PAD_L, theme.PAD)
        layout.setSpacing(theme.PAD)
        title = QtWidgets.QLabel("Aero-GCP")
        title.setObjectName("aeroTitle")
        layout.addWidget(title, 1)

        self.undo_button = self._round_button(self._on_undo_clicked)
        self.redo_button = self._round_button(self._on_redo_clicked)
        layout.addWidget(self.undo_button, 0)
        layout.addWidget(self.redo_button, 0)

        self.more_button = self._round_button(self._open_settings)
        self.more_button.setEnabled(True)
        layout.addWidget(self.more_button, 0)
        return header

    @staticmethod
    def _round_button(handler):
        """Small round button of the header banner (Undo, Redo, settings)."""
        button = QtWidgets.QPushButton()
        button.setObjectName("aeroGhost")
        button.setProperty("round", True)
        button.setCursor(QtCore.Qt.PointingHandCursor)
        button.setFixedSize(HEADER_BUTTON, HEADER_BUTTON)
        button.setIconSize(QtCore.QSize(HEADER_ICON, HEADER_ICON))
        button.setEnabled(False)
        button.clicked.connect(handler)
        return button

    def _build_play(self):
        """Round Play / Pause button, then dot and state text."""
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.PAD + theme.PAD_S)

        self.toggle = _Switch()
        self.toggle.clicked.connect(self._on_toggle_clicked)
        row.addWidget(self.toggle, 0)

        # The state dot is replaced by the switch; it stays for the existing callers.
        self.state_dot = QtWidgets.QLabel()
        self.state_dot.setFixedSize(STATE_DOT, STATE_DOT)
        self.state_dot.setVisible(False)
        row.addWidget(self.state_dot, 0, QtCore.Qt.AlignVCenter)

        self.state_text = _ElidedLabel("aeroState")
        self.state_text.set_full_text(i18n.t("panel.state_on"))
        row.addWidget(self.state_text, 1)
        return row

    def _build_aigcp_card(self):
        """AI detection, at the top: target type, scope (active marker or all), run."""
        card = QtWidgets.QFrame()
        card.setObjectName("aeroCardAccent")
        layout = QtWidgets.QVBoxLayout(card)
        pad = theme.PAD + theme.PAD_S
        layout.setContentsMargins(pad, pad, pad, pad)
        layout.setSpacing(theme.PAD)
        self._eyebrow_aigcp = _ElidedLabel("aeroAccentTitle")
        self._eyebrow_aigcp.set_full_text(i18n.t("panel.aigcp").upper())
        layout.addWidget(self._eyebrow_aigcp)

        actions = QtWidgets.QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(theme.PAD)
        self.aigcp_button = self._tool_button("aigcp.run", self._on_aigcp_run)
        self.aigcp_button.setObjectName("aeroRun")
        self.aigcp_button.setFixedSize(RUN_WIDTH, RUN_HEIGHT)
        self.aigcp_button.setIcon(_run_icon())
        self.aigcp_button.setIconSize(QtCore.QSize(RUN_ICON, RUN_ICON))
        self.aigcp_button.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        self.aigcp_clear_button = self._tool_button("aigcp.clear", self._on_aigcp_clear)
        self.aigcp_review_button = self._tool_button("aigcp.review", self._on_aigcp_review)
        actions.addWidget(self.aigcp_button, 0)
        actions.addWidget(self.aigcp_clear_button, 1, QtCore.Qt.AlignVCenter)
        actions.addWidget(self.aigcp_review_button, 1, QtCore.Qt.AlignVCenter)
        layout.addLayout(actions)

        # Two rows of lists: type and scope, then the photos to leave alone.
        choices = QtWidgets.QGridLayout()
        choices.setContentsMargins(0, 0, 0, 0)
        choices.setHorizontalSpacing(theme.PAD)
        choices.setVerticalSpacing(theme.PAD)
        choices.addWidget(self._build_combo("type", aigcp.TYPES), 0, 0)
        choices.addWidget(self._build_combo("scope", aigcp.SCOPES), 0, 1)
        choices.addWidget(self._build_combo("ignore", aigcp.IGNORES), 1, 0, 1, 2)
        choices.setColumnStretch(0, 2)
        choices.setColumnStretch(1, 3)
        layout.addLayout(choices)

        # Progress bar with its text inside, and Cancel at the right; visible during a computation.
        self.aigcp_bar_row = QtWidgets.QWidget()
        bar_layout = QtWidgets.QHBoxLayout(self.aigcp_bar_row)
        bar_layout.setContentsMargins(0, 0, 0, 0)
        bar_layout.setSpacing(theme.PAD)
        self.aigcp_progress = QtWidgets.QProgressBar()
        self.aigcp_progress.setObjectName("aeroProgressText")
        self.aigcp_progress.setTextVisible(True)
        self.aigcp_progress.setAlignment(QtCore.Qt.AlignCenter)
        self.aigcp_progress.setRange(0, 100)
        bar_layout.addWidget(self.aigcp_progress, 1)
        self.aigcp_cancel_button = self._tool_button("aigcp.cancel", self._on_aigcp_cancel)
        self.aigcp_cancel_button.setFixedSize(CANCEL_WIDTH, PROGRESS_HEIGHT)
        self.aigcp_cancel_button.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        bar_layout.addWidget(self.aigcp_cancel_button, 0)
        self.aigcp_bar_row.setVisible(False)
        layout.addWidget(self.aigcp_bar_row)
        self._set_aigcp_texts()
        return card

    def _build_combo(self, key, values):
        """Drop-down list of an AI detection choice; the texts are set by `_set_aigcp_texts`."""
        combo = QtWidgets.QComboBox()
        combo.setObjectName("aeroCombo")
        combo.setCursor(QtCore.Qt.PointingHandCursor)
        combo.setFocusPolicy(QtCore.Qt.NoFocus)
        combo.setFixedHeight(CONTROL_HEIGHT)
        combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLength)
        combo.setMinimumContentsLength(4)
        for value in values:
            combo.addItem("", value)
        combo.activated.connect(functools.partial(self._on_aigcp_combo, key))
        self._aigcp_combos[key] = combo
        return combo

    def _build_active_card(self):
        card = QtWidgets.QFrame()
        card.setObjectName("aeroCardActive")
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(theme.PAD + theme.PAD_S, theme.PAD_S + 2, theme.PAD + theme.PAD_S, theme.PAD_S + 2)
        layout.setSpacing(theme.PAD_S)

        # A single line: title, identifier (truncated to 10 characters), counters.
        line = QtWidgets.QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(theme.PAD)
        self._eyebrow_active = _eyebrow(i18n.t("panel.active_marker"))
        self._eyebrow_active.setSizePolicy(QtWidgets.QSizePolicy.Maximum, QtWidgets.QSizePolicy.Preferred)
        line.addWidget(self._eyebrow_active, 0)
        self.active_value = _ElidedLabel("aeroValueEmpty")
        self.active_value.set_full_text("—")
        line.addWidget(self.active_value, 1)

        # The pills keep their place when they are hidden: the card
        # does not change height from one marker to the next.
        self.counters = QtWidgets.QWidget()
        policy = self.counters.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.counters.setSizePolicy(policy)
        row = QtWidgets.QHBoxLayout(self.counters)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.PAD)
        self.count_pointed = _Counter("target", i18n.t("panel.tip_pointed"))
        self.count_photos = _Counter("image", i18n.t("panel.tip_seen"))
        row.addWidget(self.count_pointed)
        row.addWidget(self.count_photos)
        line.addWidget(self.counters, 0)
        layout.addLayout(line)
        return card

    def _build_search(self):
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText(i18n.t("panel.search"))
        self.search.setClearButtonEnabled(False)
        self.search.textChanged.connect(self._on_search)
        return self.search

    def _build_list(self):
        """Marker table: ID, X / Y / Z errors, number of projections."""
        box = QtWidgets.QFrame()
        box.setObjectName("aeroGridBox")
        box.setMinimumHeight(LIST_MIN_HEIGHT)
        box.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding
        )
        inner = QtWidgets.QVBoxLayout(box)
        # The square corners of the grid stay clear of the rounded frame.
        inner.setContentsMargins(3, 3, 3, 3)
        inner.setSpacing(0)

        grid = QtWidgets.QTreeWidget()
        grid.setObjectName("aeroGrid")
        self._header = _GridHeader(grid)
        grid.setHeader(self._header)
        grid.setColumnCount(len(COLUMNS))
        grid.setHeaderLabels(column_titles())
        grid.setRootIsDecorated(False)
        grid.setIndentation(0)
        grid.setItemsExpandable(False)
        grid.setUniformRowHeights(True)
        grid.setAllColumnsShowFocus(True)
        grid.setSortingEnabled(False)
        grid.setTextElideMode(QtCore.Qt.ElideRight)
        grid.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        grid.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        # Shift+click / Ctrl+click: several markers for the AI detection. The last
        # one clicked stays the current marker.
        grid.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        grid.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        grid.setIconSize(QtCore.QSize(DOT_SIZE + 2, DOT_SIZE + 2))
        grid.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding
        )

        header = self._header
        # QTreeView.setHeader resets these settings: they are set again afterwards.
        header.setSectionsClickable(True)
        header.setSectionsMovable(False)
        header.setSortIndicatorShown(True)
        header.setStretchLastSection(False)
        for column, (_title, _key, width) in enumerate(COLUMNS):
            if width:
                header.setSectionResizeMode(column, QtWidgets.QHeaderView.Fixed)
                header.resizeSection(column, width)
            else:
                header.setSectionResizeMode(column, QtWidgets.QHeaderView.Stretch)
        header.setSortIndicator(-1, QtCore.Qt.AscendingOrder)
        header.sectionClicked.connect(self._on_header_clicked)
        self._number_delegate = _NumberDelegate(grid)
        for column in range(1, len(COLUMNS)):
            grid.setItemDelegateForColumn(column, self._number_delegate)

        grid.currentItemChanged.connect(self._on_current_changed)
        grid.itemClicked.connect(self._on_item_clicked)
        grid.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        grid.customContextMenuRequested.connect(self._on_grid_menu)
        inner.addWidget(grid)
        self.grid = grid
        # Former name of the widget, kept for the existing callers.
        self.list = grid
        return box

    def _tool_button(self, text_key, handler):
        button = _ElidedButton(i18n.t(text_key))
        button.setObjectName("aeroGhost")
        button.setProperty("pill", True)
        button.setCursor(QtCore.Qt.PointingHandCursor)
        button.setFixedHeight(CONTROL_HEIGHT)
        button.clicked.connect(handler)
        return button

    def _build_more(self):
        """"More" card: recording of the markings and log. Folded at the start."""
        self.more = QtWidgets.QFrame()
        self.more.setObjectName("aeroCard")
        layout = QtWidgets.QVBoxLayout(self.more)
        pad = theme.PAD + theme.PAD_S
        layout.setContentsMargins(pad, pad, pad, pad)
        layout.setSpacing(theme.PAD_S)

        layout.addWidget(self._check("ai_auto_review", i18n.t(CHECK_TEXTS["ai_auto_review"])))
        layout.addWidget(self._check("collect_dataset", i18n.t(CHECK_TEXTS["collect_dataset"])))

        self.log_button = _ElidedButton(i18n.t("panel.log"))
        self.log_button.setCursor(QtCore.Qt.PointingHandCursor)
        self.log_button.clicked.connect(self._on_log_button)
        layout.addSpacing(theme.PAD_S)
        layout.addWidget(self.log_button)

        self.log = QtWidgets.QPlainTextEdit()
        self.log.setObjectName("aeroLog")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(journal.MAX_LINES)
        self.log.setLineWrapMode(QtWidgets.QPlainTextEdit.WidgetWidth)
        self.log.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.log.setVisible(False)
        layout.addWidget(self.log)

        return self.more

    def _check(self, key, text):
        box = _ElidedCheck(text)
        self._checks[key] = box
        box.toggled.connect(functools.partial(self._on_check, key))
        return box

    def _build_footer(self):
        """One row: residual check (green outline) on the left, Export (solid green) on the right."""
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.PAD)
        self.qc_button = self._tool_button("panel.qc", self._open_qc)
        self.qc_button.setObjectName("aeroYes")
        self.qc_button.setFixedWidth(QC_WIDTH)
        self.qc_button.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        row.addWidget(self.qc_button, 0)
        self.adjust_button = self._tool_button("panel.adjust", self._open_adjust)
        self.adjust_button.setObjectName("aeroYes")
        self.adjust_button.setFixedWidth(ADJUST_WIDTH)
        self.adjust_button.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        row.addWidget(self.adjust_button, 0)
        row.addStretch(1)

        # The status ("7/18 marked") is no longer displayed; the widget stays for the existing callers.
        self.status = _ElidedLabel("aeroStatus")
        self.status.setVisible(False)
        row.addWidget(self.status, 0)

        self.export_button = QtWidgets.QPushButton()
        self._set_export_text()
        self.export_button.setObjectName("aeroRun")
        self.export_button.setProperty("pill", True)
        self.export_button.setCursor(QtCore.Qt.PointingHandCursor)
        self.export_button.setFixedSize(EXPORT_WIDTH, CONTROL_HEIGHT)
        self.export_button.setIconSize(QtCore.QSize(EXPORT_ICON, EXPORT_ICON))
        self.export_button.clicked.connect(self._show_export_menu)
        self.export_menu = QtWidgets.QMenu(self.export_button)
        self._export_actions = {}
        for kind, text_key in EXPORTS:
            action = self.export_menu.addAction(i18n.t(text_key))
            action.triggered.connect(functools.partial(self._on_export, kind))
            self._export_actions[kind] = action
        row.addWidget(self.export_button, 0)

        return row

    def _build_credit(self):
        """At the very bottom: "Tool created by helix.aero" (link) and the GitHub icon (link)."""
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.PAD_S)
        self.credit = QtWidgets.QLabel()
        self.credit.setObjectName("aeroMeta")
        self.credit.setTextFormat(QtCore.Qt.RichText)
        self.credit.setOpenExternalLinks(True)
        self.credit.setCursor(QtCore.Qt.PointingHandCursor)
        self._set_credit_text()
        row.addWidget(self.credit, 0)
        self.github_button = QtWidgets.QPushButton()
        self.github_button.setObjectName("aeroGhost")
        self.github_button.setProperty("round", True)
        self.github_button.setCursor(QtCore.Qt.PointingHandCursor)
        self.github_button.setFixedSize(26, 26)
        self.github_button.setIconSize(QtCore.QSize(GITHUB_ICON, GITHUB_ICON))
        self.github_button.setToolTip(i18n.t("panel.github_tip"))
        self.github_button.clicked.connect(self._open_github)
        row.addWidget(self.github_button, 0)
        row.addStretch(1)
        return row

    def _set_credit_text(self):
        link = '<a href="{url}" style="color:{color};text-decoration:none">{name}</a>'.format(
            url=SITE_URL, color=theme.GREEN, name=SITE_NAME)
        self.credit.setText(i18n.t("panel.credit", site=link))

    def _open_github(self, _checked=False):
        try:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(GITHUB_URL))
        except Exception as exc:
            journal.write("FastGCP: GitHub link: {!r}".format(exc))

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------

    def _refresh_icons(self):
        """Repaint all the icons in the colours of the active palette."""
        try:
            self._apply_toggle(self._running)
            self.more_button.setIcon(QtGui.QIcon(_gear_pixmap(theme.FG_2)))
            self.github_button.setIcon(QtGui.QIcon(_github_pixmap(theme.FG_2)))
            self.undo_button.setIcon(_undo_icon())
            self.redo_button.setIcon(_undo_icon(mirror=True))
            self.export_button.setIcon(QtGui.QIcon(_export_pixmap(theme.GREEN_ON)))
            self.export_menu.setStyleSheet(theme.menu_stylesheet())
            self._paint_placeholder()
            self.count_pointed.apply_theme()
            self.count_photos.apply_theme()
        except RuntimeError:
            pass

    def _paint_placeholder(self):
        """Search placeholder text in FG_2 (the QSS leaves it too pale
        on some Qt versions)."""
        palette = self.search.palette()
        role = getattr(QtGui.QPalette, "PlaceholderText", None)
        if role is None:
            return
        palette.setColor(role, _qcolor(theme.FG_2))
        self.search.setPalette(palette)

    def _set_export_text(self):
        """Text of the Export button, elided if it is too long."""
        text = i18n.t("panel.export")
        metrics = self.export_button.fontMetrics()
        self.export_button.setText(
            metrics.elidedText(text, QtCore.Qt.ElideRight, EXPORT_TEXT_MAX_PX))

    def _set_aigcp_texts(self):
        """Texts of the AI Detection card, displayed choices, button state."""
        self._eyebrow_aigcp.set_full_text(i18n.t("panel.aigcp").upper())
        for key, combo in self._aigcp_combos.items():
            for index in range(combo.count()):
                combo.setItemText(index, i18n.t(AIGCP_CHOICE_TEXTS[(key, combo.itemData(index))]))
        self.aigcp_button.set_full_text(i18n.t("aigcp.run"))
        self.aigcp_clear_button.set_full_text(i18n.t("aigcp.clear"))
        self.aigcp_review_button.set_full_text(i18n.t("aigcp.review"))
        self.aigcp_cancel_button.set_full_text(i18n.t("aigcp.cancel"))
        self.update_aigcp()

    # ------------------------------------------------------------------
    # Options
    # ------------------------------------------------------------------

    def _load_options(self):
        """Set the widgets from `state` without triggering the signals."""
        state = self._state
        if state is None:
            return
        try:
            self._apply_toggle(bool(state.enabled))

            for key, box in self._checks.items():
                box.blockSignals(True)
                box.setChecked(bool(state.option(key)))
                box.blockSignals(False)

            self._set_log_open(bool(state.option("show_log")), persist=False)
        except Exception as exc:
            journal.write("FastGCP: reading the settings: {!r}".format(exc))

    def _set_option(self, key, value):
        state = self._state
        if state is None:
            return
        try:
            state.set_option(key, value)
        except Exception as exc:
            journal.write("FastGCP: setting {} not saved: {!r}".format(key, exc))

    def _on_check(self, key, checked):
        self._set_option(key, bool(checked))
        journal.write("Setting {}: {}".format(key, "yes" if checked else "no"))

    # ------------------------------------------------------------------
    # Play / Pause
    # ------------------------------------------------------------------

    def _apply_toggle(self, running):
        """Align the button, dot and state text with `running`."""
        self._running = bool(running)
        self.toggle.set_on(running)
        self.state_dot.setPixmap(_state_dot_pixmap(running))
        self.state_text.set_full_text(
            i18n.t("panel.state_on" if running else "panel.state_off"))
        self.state_text.setObjectName("aeroState" if running else "aeroStateOff")
        _repolish(self.state_text)
        self._update_undo_button()

    def _on_toggle_clicked(self):
        state = self._state
        if state is None:
            return
        running = not bool(state.enabled)
        state.enabled = running
        self._apply_toggle(running)
        # The Ctrl+Z shortcut (tool.py) follows the state of the tool.
        history.notify()
        journal.write("FastGCP {}".format("active" if running else "paused"))

    # ------------------------------------------------------------------
    # Settings window (the gear)
    # ------------------------------------------------------------------

    def _open_settings(self, _checked=False):
        """Open the Settings window (a single instance, reused)."""
        try:
            from . import settings_dialog
            self._settings_dialog = settings_dialog.show(self, getattr(self, "_settings_dialog", None))
            if self._log_open:
                self._fit_log_height()
                self._scroll_log_to_end()
        except Exception as exc:
            journal.write("FastGCP: Settings window: {!r}".format(exc))

    # ------------------------------------------------------------------
    # Current marker
    # ------------------------------------------------------------------

    def update_active_card(self):
        """Reread the current marker from `state` and redraw the card."""
        state = self._state
        if state is None:
            return
        try:
            label = state.active_label()
        except Exception:
            label = None

        if label:
            wanted = "aeroValue"
            self.active_value.set_full_text(short_label(label))
            self.active_value.setToolTip(label if label != short_label(label) else "")
            self.count_pointed.set_number(self._pointed_count(state.active_marker))
            self.count_photos.set_number(getattr(state, "last_photo_count", 0) or 0)
            self.counters.setVisible(True)
        else:
            wanted = "aeroValueEmpty"
            self.active_value.set_full_text("—")
            self.counters.setVisible(False)

        if self.active_value.objectName() != wanted:
            self.active_value.setObjectName(wanted)
            _repolish(self.active_value)
        self._refresh_row(label)

    @staticmethod
    def _pointed_count(marker):
        if marker is None:
            return 0
        try:
            return int(msapi.projection_count(marker))
        except Exception:
            return 0

    def set_active_marker_by_label(self, label, from_sync=False):
        """Make the marker of this name the current one (selection of the Reference pane)."""
        state = self._state
        if state is None:
            return False
        try:
            marker = self._find_marker(label)
            if marker is None:
                journal.write("Marker not found in the chunk: {}".format(label))
                return False
            state.active_marker = marker
            self._select_row(label)
            self.update_active_card()
            journal.write("Current marker: {}{}".format(
                label, " (Reference selection)" if from_sync else ""
            ))
            if not from_sync:
                self._filter_photos(marker)
            return True
        except Exception as exc:
            journal.write("FastGCP: marker {} not activated: {!r}".format(label, exc))
            return False

    @staticmethod
    def _find_marker(label):
        chunk = msapi.chunk()
        if chunk is None:
            return None
        for marker in msapi.markers(chunk):
            if msapi.marker_label(marker) == label:
                return marker
        return None

    def _select_row(self, label):
        """Select the row without triggering _pick (no loop)."""
        self.grid.blockSignals(True)
        try:
            item = self._item_for(label)
            if item is None:
                self.grid.clearSelection()
            else:
                self.grid.setCurrentItem(item)
                self.grid.scrollToItem(item)
        finally:
            self.grid.blockSignals(False)

    def _item_for(self, label):
        if not label:
            return None
        for row in range(self.grid.topLevelItemCount()):
            item = self.grid.topLevelItem(row)
            if item.data(0, ROLE_LABEL) == label:
                return item
        return None

    def _refresh_row(self, label):
        """Update the marker row (dot, projections, errors)."""
        item = self._item_for(label)
        if item is None:
            return
        marker = self._marker_of(item)
        if marker is not None:
            self._style_item(item, marker, label)

    def refresh_errors(self):
        """After a marking or an undo: errors and projections of the current marker.

        The errors of a marker only depend on its own projections:
        a single row changes. If the table is sorted on a value
        column, everything is reread so that the order stays right.
        """
        state = self._state
        if state is None:
            return
        try:
            if self._sort is not None and self._sort[0] != "id":
                self.refresh_markers()
            else:
                self._refresh_row(state.active_label())
            self._update_undo_button()
        except Exception as exc:
            journal.write("FastGCP: errors not refreshed: {!r}".format(exc))

    def refresh_rows(self, labels=None, settle=False):
        """Reread the rows of these markers (X/Y/Z errors, projections); None = the whole table.

        After a validation or a refinement, several markers changed: reread them
        all. `settle=True`: Metashape recomputes the marker positions afterwards, so
        the rows are reread twice more, a little later. Never raises.
        """
        state = self._state
        if state is None:
            return
        if settle:
            for delay in SETTLE_MS:
                QtCore.QTimer.singleShot(delay, functools.partial(self.refresh_rows, labels))
        try:
            if labels is None or (self._sort is not None and self._sort[0] != "id"):
                self.refresh_markers()
            else:
                for label in set(labels):
                    self._refresh_row(label)
                self._update_undo_button()
        except Exception as exc:
            journal.write("FastGCP: rows not refreshed: {!r}".format(exc))

    def _filter_photos(self, marker):
        state = self._state
        if state is None:
            return
        try:
            # Metashape recentres on its selected marker when the photo
            # changes: it is given the same marker as FastGCP.
            if msapi.select_marker(msapi.chunk(), marker):
                state.last_selected_label = msapi.marker_label(marker)
        except Exception as exc:
            journal.write("FastGCP: Reference selection: {!r}".format(exc))
        try:
            from . import photos
            photos.filter_for_marker(state, marker, force=True)
        except Exception as exc:
            journal.write("FastGCP: photos filter: {!r}".format(exc))
        self.update_active_card()

    # ------------------------------------------------------------------
    # Marker list
    # ------------------------------------------------------------------

    def poll_rows(self):
        """Reread the table if it changed in Metashape: marker added or removed, projection placed
        or removed elsewhere, errors recomputed. Compares a light snapshot with the previous one.

        Called by the tick, every two seconds. Never raises. `poll_cost` (seconds) says
        how much the read costs: the tick spaces out the rereads if the project is big.
        """
        if self._state is None:
            return
        try:
            chunk = msapi.chunk()
            if chunk is None:
                self._signature = None
                return
            start = time.time()
            signature = []
            for marker in msapi.markers(chunk):
                label = msapi.marker_label(marker)
                if label:
                    row = self._row_data(chunk, marker, label)
                    signature.append((label, row["proj"], row["x"], row["y"], row["z"]))
            self.poll_cost = time.time() - start
            previous, self._signature = self._signature, signature
            if previous is not None and previous != signature:
                bar = self.grid.verticalScrollBar()
                position = bar.value()
                self.refresh_markers()
                bar.setValue(position)             # the table is not made to jump while it is being read
        except Exception as exc:
            journal.write("FastGCP: table follow-up: {!r}".format(exc))

    def refresh_markers(self):
        """Reread the markers of the current chunk. Never raises."""
        if self._state is None:
            return
        try:
            self._rebuild_list()
        except Exception as exc:
            journal.write("FastGCP: marker reread: {!r}".format(exc))
            try:
                self.set_status(i18n.t("panel.status.reread_failed"))
            except Exception:
                pass

    def _rebuild_list(self):
        chunk = msapi.chunk()
        needle = self.search.text().strip().lower()
        active = self._state.active_label()
        total = 0
        rows = []

        if chunk is not None:
            everything = msapi.markers(chunk)
            total = len(everything)
            for marker in everything:
                label = msapi.marker_label(marker)
                if not label:
                    continue
                if needle and needle not in label.lower():
                    continue
                rows.append(self._row_data(chunk, marker, label))
        key, descending = self._sort if self._sort is not None else (None, False)
        rows = sort_rows(rows, key, descending)
        current = None
        chosen = set(self.selected_labels())

        self.grid.blockSignals(True)
        self.grid.setUpdatesEnabled(False)
        try:
            self.grid.clear()
            for row in rows:
                item = self._make_item(row)
                self.grid.addTopLevelItem(item)
                if row["label"] == active:
                    current = item
                if row["label"] in chosen:
                    item.setSelected(True)
            if current is not None:
                self.grid.setCurrentItem(current, 0, QtCore.QItemSelectionModel.NoUpdate)
                self.grid.scrollToItem(current)
                if len(chosen) < 2:
                    current.setSelected(True)
        finally:
            self.grid.setUpdatesEnabled(True)
            self.grid.blockSignals(False)

        # The status only gives the number: "14", or "3/14" during a search.
        if chunk is None:
            self.set_status("\u2014")
        elif needle:
            self.set_status("{}/{}".format(len(rows), total))
        else:
            # Progress: markers with at least 3 photos marked.
            done, count = qc.progress(rows)
            if self._chunk_name:
                self.set_status(i18n.t("panel.status.progress_chunk", done=done,
                                       total=count, name=self._chunk_name))
            else:
                self.set_status(i18n.t("panel.status.progress", done=done, total=count))
        self.update_active_card()

    @staticmethod
    def _row_data(chunk, marker, label):
        """Values of a row: name, projections, errors in metres (or None)."""
        errors = msapi.marker_errors(chunk, marker)
        return {
            "label": label,
            "marker": marker,
            "proj": FastGcpPanel._pointed_count(marker),
            "x": errors.get("x"),
            "y": errors.get("y"),
            "z": errors.get("z"),
        }

    def _make_item(self, row):
        item = QtWidgets.QTreeWidgetItem()
        item.setData(0, ROLE_MARKER, row["marker"])
        item.setData(0, ROLE_LABEL, row["label"])
        item.setSizeHint(0, QtCore.QSize(0, ROW_HEIGHT))
        self._fill_item(item, row)
        return item

    def _fill_item(self, item, row):
        item.setText(0, row["label"])
        item.setIcon(0, _dot_icon(row["proj"] > 0))
        id_font = QtGui.QFont(self.grid.font())
        id_font.setWeight(QtGui.QFont.Medium)
        item.setFont(0, id_font)
        item.setForeground(0, QtGui.QBrush(_qcolor(theme.FG_0)))
        muted = QtGui.QBrush(_qcolor(theme.FG_3))
        normal = QtGui.QBrush(_qcolor(theme.FG_1))
        warn = QtGui.QBrush(_qcolor(theme.OFF_TEXT))
        for column, (_title, key, _width) in enumerate(COLUMNS):
            if column == 0:
                continue
            value = row.get(key)
            if key == "proj":
                item.setText(column, str(int(value)))
                # Fewer than 3 photos marked: to be completed, in the alert colour.
                item.setForeground(column, warn if qc.is_few(row) else normal)
            else:
                item.setText(column, format_error(value))
                item.setForeground(column, muted if value is None else normal)

    def _style_item(self, item, marker, label):
        chunk = msapi.chunk()
        self._fill_item(item, self._row_data(chunk, marker, label))

    @staticmethod
    def _marker_of(item):
        """Marker of a row, or None if the reference became invalid."""
        try:
            marker = item.data(0, ROLE_MARKER)
            if marker is None:
                return None
            marker.label  # raises RuntimeError if the marker no longer exists
            return marker
        except Exception:
            return None

    # -- right click: delete the projections or the chosen markers ---------

    def _on_grid_menu(self, pos):
        item = self.grid.itemAt(pos)
        if item is not None and not item.isSelected():
            self.grid.clearSelection()
            item.setSelected(True)
        labels = self.selected_labels()
        if not labels:
            return
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet(theme.menu_stylesheet())
        count = len(labels)
        for key, handler in (("panel.remove_projections", self._remove_projections),
                             ("panel.remove_markers", self._remove_markers)):
            action = menu.addAction(i18n.t(key, n=count))
            action.triggered.connect(lambda *_args, h=handler, names=labels: h(names))
        run = getattr(menu, "exec_", None) or getattr(menu, "exec")
        run(self.grid.viewport().mapToGlobal(pos))

    def _confirm(self, text_key, labels):
        from ... import dialogs
        shown = ", ".join(labels[:5]) + ("\u2026" if len(labels) > 5 else "")
        return dialogs.ask(self, i18n.t("panel.remove_title"), i18n.t(text_key, n=len(labels), names=shown))

    def _markers_named(self, labels):
        chunk = msapi.chunk()
        wanted = set(labels)
        if chunk is None:
            return None, []
        return chunk, [m for m in msapi.markers(chunk) if msapi.marker_label(m) in wanted]

    def _remove_projections(self, labels):
        state = self._state
        if state is None or not self._confirm("panel.remove_projections_confirm", labels):
            return
        from . import review_dialog
        busy = review_dialog.BusyDialog(i18n.t("panel.busy_projections", n=len(labels)), self)
        busy.open()
        try:
            _chunk, found = self._markers_named(labels)
            total = 0
            for index, marker in enumerate(found):
                busy.step(index, len(found))
                total += msapi.remove_all_projections(marker)
            aigcp.forget_markers(state, labels)
            self.refresh_rows(labels, settle=True)
            self.update_active_card()
            self.set_status(i18n.t("panel.removed_projections", n=total, k=len(found)))
            journal.write("FastGCP: {} projection(s) removed on {} marker(s)".format(total, len(found)))
        except Exception as exc:
            journal.write("FastGCP: removal of the projections: {!r}".format(exc))
        finally:
            busy.close()

    def _remove_markers(self, labels):
        state = self._state
        if state is None or not self._confirm("panel.remove_markers_confirm", labels):
            return
        try:
            chunk, found = self._markers_named(labels)
            gone = []
            for marker in found:
                label = msapi.marker_label(marker)
                if msapi.remove_marker(chunk, marker)[0]:
                    gone.append(label)
            state.active_label()          # resets the current marker if it was just deleted
            aigcp.forget_markers(state, gone)
            self.refresh_markers()
            self.update_active_card()
            self.set_status(i18n.t("panel.removed_markers", n=len(gone)))
            journal.write("FastGCP: {} marker(s) removed: {}".format(len(gone), ", ".join(gone)))
        except Exception as exc:
            journal.write("FastGCP: removal of the markers: {!r}".format(exc))

    def _on_search(self, _text):
        self.refresh_markers()

    def _on_current_changed(self, current, _previous):
        if current is not None:
            self._pick(current)

    def _on_item_clicked(self, item, _column=0):
        if item is not None:
            self._pick(item)

    # -- sort by clicking a header ---------------------------------------

    def sort_state(self):
        """(key, descending) of the current sort, or None for the project order."""
        return self._sort

    def set_sort(self, key, descending=False):
        """Set the sort (COLUMNS key, or None) and reread the table."""
        valid = [column_key for _title, column_key, _width in COLUMNS]
        if key not in valid:
            key = None
        self._sort = (key, bool(descending)) if key is not None else None
        header = self._header
        header.blockSignals(True)
        try:
            if self._sort is None:
                header.setSortIndicator(-1, QtCore.Qt.AscendingOrder)
            else:
                header.setSortIndicator(
                    valid.index(key),
                    QtCore.Qt.DescendingOrder if descending else QtCore.Qt.AscendingOrder,
                )
        finally:
            header.blockSignals(False)
        header.viewport().update()
        self.refresh_markers()

    def _on_header_clicked(self, column):
        """Successive clicks on a header: ascending, descending, project order."""
        try:
            key = COLUMNS[column][1]
        except IndexError:
            return
        if self._sort is None or self._sort[0] != key:
            self.set_sort(key, False)
        elif not self._sort[1]:
            self.set_sort(key, True)
        else:
            self.set_sort(None)

    def _pick(self, item):
        """A click (or an arrow) in the list: current marker + filter."""
        state = self._state
        if state is None:
            return
        try:
            label = item.data(0, ROLE_LABEL)
            now = time.time()
            last_label, last_time = self._last_pick
            if label == last_label and now - last_time < PICK_DEBOUNCE_S:
                return
            self._last_pick = (label, now)

            marker = self._marker_of(item)
            if marker is None:
                self.set_status(i18n.t("panel.status.list_reread"))
                self.refresh_markers()
                return
            state.active_marker = marker
            self.update_active_card()
            journal.write("Current marker: {}".format(label))
            self._filter_photos(marker)
        except Exception as exc:
            journal.write("FastGCP: marker choice: {!r}".format(exc))

    # ------------------------------------------------------------------
    # Undo
    # ------------------------------------------------------------------

    def _on_history_changed(self):
        try:
            self._update_undo_button()
        except RuntimeError:
            history.unsubscribe(self._on_history_changed)

    def _update_undo_button(self):
        """Undo / Redo: active only when the tool runs and there is something to undo / redo."""
        state = self._state
        running = state is not None and bool(state.enabled)
        self.undo_button.setEnabled(bool(running and history.can_undo()))
        self.redo_button.setEnabled(bool(running and history.can_redo()))

    def _on_redo_clicked(self):
        state = self._state
        if state is None:
            return
        try:
            from . import marking
            marking.redo_last(state)
        except Exception as exc:
            journal.write("FastGCP: redo: {!r}".format(exc))
        self._update_undo_button()

    def _on_undo_clicked(self):
        state = self._state
        if state is None:
            return
        try:
            from . import marking
            marking.undo_last(state)
        except Exception as exc:
            journal.write("FastGCP: undo: {!r}".format(exc))
        self._update_undo_button()

    # ------------------------------------------------------------------
    # Residual check and markers to watch
    # ------------------------------------------------------------------

    def _all_rows(self):
        """Rows of all the markers of the chunk (not only those of the search)."""
        chunk = msapi.chunk()
        rows = []
        for marker in msapi.markers(chunk):
            label = msapi.marker_label(marker)
            if label:
                rows.append(self._row_data(chunk, marker, label))
        return rows

    def _qc_data(self):
        """What the Residual check window shows: all the markers, the control and check error."""
        rows = self._all_rows()
        enabled = {row["label"]: msapi.marker_enabled(row["marker"]) for row in rows}
        items = qc.table(rows, enabled)
        return {"items": items, "groups": qc.group_rms(items), "limit": qc.residual_limit(rows)}

    def _open_qc(self, _checked=False):
        """"Residual check" window: all the markers and their state (see qc_dialog.py)."""
        try:
            from . import qc_dialog
            self._qc_dialog = qc_dialog.show(self, self._qc_data, self._on_qc_open, self._open_diagnostic,
                                             getattr(self, "_qc_dialog", None))
        except Exception as exc:
            journal.write("FastGCP: residual check: {!r}".format(exc))

    def _on_qc_open(self, item):
        """Double-click in the window: the marker, and its worst photo when it is a high residual."""
        self._on_qc_pick(item["label"], item["status"] == qc.RESIDUAL)

    def _adjust_collect(self):
        """Read of the markers for "Adjust" (main thread): points, ticked boxes, PPK or not."""
        chk = msapi.chunk()
        points, why = msapi.marker_frame_points(chk)
        used, total = msapi.cameras_georeferenced(chk)
        return {"points": points, "why": why, "ppk": bool(total) and used >= 0.5 * total,
                "enabled": {p["label"]: bool(msapi.marker_enabled(p["marker"])) for p in points}}

    def _adjust_apply(self, labels):
        """Tick these markers as check points and untick the others. Returns (number ticked, error)."""
        try:
            chk = msapi.chunk()
            points, why = msapi.marker_frame_points(chk)
            if not points:
                return 0, why
            wanted, before = set(labels), []
            count = 0
            for point in points:
                before.append("{}={}".format(point["label"], msapi.marker_enabled(point["marker"])))
                on = point["label"] in wanted
                if msapi.set_marker_enabled(point["marker"], on):
                    count += 1 if on else 0
            msapi.refresh()
            journal.write("FastGCP: check markers ticked: {} (before: {})".format(
                ", ".join(sorted(wanted)), ", ".join(before)))
            return count, ""
        except Exception as exc:
            journal.write("FastGCP: ticking the check markers: {!r}".format(exc))
            return 0, repr(exc)

    def _open_adjust(self, _checked=False):
        """"Adjust" window: which markers to tick as check points (see gcp_select_dialog.py)."""
        try:
            from . import gcp_select_dialog
            self._adjust_dialog = gcp_select_dialog.show(self, self._adjust_collect, self._adjust_apply,
                                                         getattr(self, "_adjust_dialog", None))
        except Exception as exc:
            journal.write("FastGCP: adjustment of the check markers: {!r}".format(exc))

    def _open_diagnostic(self, _checked=False):
        """Window of the full marking diagnostic (see diagnostic.py)."""
        try:
            from . import diagnostic, diagnostic_dialog

            def compute():
                return diagnostic.analyze(diagnostic.collect(msapi.chunk()), self._all_rows())

            self._diag_dialog = diagnostic_dialog.show(
                self, compute, self._on_diag_open, getattr(self, "_diag_dialog", None))
        except Exception as exc:
            journal.write("FastGCP: marking diagnostic: {!r}".format(exc))

    def _on_diag_open(self, item):
        """A diagnostic item: current marker, then the photo concerned if it is known."""
        try:
            label, camera = item.get("label"), item.get("camera")
            if label and not self.set_active_marker_by_label(label):
                return
            if camera is None:
                return
            ok, _message = msapi.open_photo(camera)
            marker = self._find_marker(label) if label else None
            if ok and marker is not None:
                from . import photos
                photos.zoom_on_marker(self._state, marker, camera, force=True)
        except Exception as exc:
            journal.write("FastGCP: opening from the diagnostic: {!r}".format(exc))

    def _on_qc_pick(self, label, open_worst, _checked=False):
        """A click in the menu: current marker; for a residual, opens the worst photo."""
        try:
            if not self.set_active_marker_by_label(label) or not open_worst:
                return
            state = self._state
            marker = self._find_marker(label)
            worst = msapi.worst_projection(marker) if marker is not None else None
            if worst is None:
                return
            ok, _message = msapi.open_photo(worst[0])
            if ok:
                from . import photos
                photos.zoom_on_marker(state, marker, worst[0], force=True)
                self.set_status(i18n.t("qc.opened", label=label))
        except Exception as exc:
            journal.write("FastGCP: choice in the check: {!r}".format(exc))

    # ------------------------------------------------------------------
    # AI detection
    # ------------------------------------------------------------------

    def update_aigcp(self):
        """Show the stored choices; grey out Run during a computation; progress bar."""
        state = self._state
        chosen = {"type": aigcp.selected_type(), "scope": aigcp.selected_scope(),
                  "ignore": aigcp.selected_ignore()}
        for key, combo in self._aigcp_combos.items():
            try:
                index = combo.findData(chosen[key])
                if index >= 0 and combo.currentIndex() != index:
                    combo.blockSignals(True)
                    combo.setCurrentIndex(index)
                    combo.blockSignals(False)
            except RuntimeError:
                pass
        running = state is not None and aigcp.busy(state)
        proposals = state is not None and bool(getattr(state, "aigcp_applied", None))
        self.aigcp_button.setEnabled(not running)
        self.aigcp_clear_button.setEnabled(proposals and not running)
        self.aigcp_review_button.setEnabled(state is not None)       # always clickable: without a detection, it says so
        # Bar: full width and back and forth while the runner loads its model,
        # then in proportion to the photos processed.
        progress = aigcp.progress_of(state) if running else None
        if progress is not None and progress[1] > 0:
            self.aigcp_progress.setRange(0, progress[1])
            self.aigcp_progress.setValue(min(progress[0], progress[1]))
        else:
            self.aigcp_progress.setRange(0, 100)         # empty range = the text would not be displayed
            self.aigcp_progress.setValue(0)
        self.aigcp_progress.setFormat(aigcp.bar_text(state) if running else "")
        self.aigcp_bar_row.setVisible(bool(running))

    def _on_aigcp_combo(self, key, index):
        combo = self._aigcp_combos[key]
        aigcp.set_choice(key, combo.itemData(index))
        self.update_aigcp()

    def _on_aigcp_review(self, _checked=False):
        self.open_review()

    def show_ai_setup(self, text, target=None):
        """Explain what is missing for the AI detection. With `target` (an engine the installer can
        install), the box offers to install it right away; without, it says what to do."""
        try:
            from ... import dialogs
            if target is None:
                dialogs.inform(self, i18n.t("aigcp.setup_title"), text)
                return
            if dialogs.ask(self, i18n.t("aigcp.setup_title"), text):
                from . import install_ui
                install_ui.run(self, target, confirm=False)
                self.update_aigcp()
        except Exception as exc:
            journal.write("FastGCP: AI setup message: {!r}".format(exc))

    def open_review(self):
        """Open the review window of the points of the last detection. Never raises."""
        try:
            from . import review_dialog
            self._review_dialog = review_dialog.show(self._state, self, self._review_dialog)
        except Exception as exc:
            journal.write("FastGCP: AI points review: {!r}".format(exc))

    def close_review(self):
        try:
            from . import review_dialog
            review_dialog.close(self._review_dialog)
        except Exception as exc:
            journal.write("FastGCP: closing the AI review: {!r}".format(exc))
        self._review_dialog = None

    def selected_labels(self):
        """Labels of the selected rows (Shift+click), in table order."""
        out = []
        for row in range(self.grid.topLevelItemCount()):
            item = self.grid.topLevelItem(row)
            if item.isSelected():
                out.append(item.data(0, ROLE_LABEL))
        return out

    def _on_aigcp_run(self, _checked=False):
        try:
            aigcp.start(self._state, labels=self.selected_labels())
        except Exception as exc:
            journal.write("FastGCP: AI detection: {!r}".format(exc))
        self.update_aigcp()

    def _on_aigcp_cancel(self, _checked=False):
        try:
            aigcp.abort(self._state)
        except Exception as exc:
            journal.write("FastGCP: cancelling the detection: {!r}".format(exc))
        self.update_aigcp()

    def _on_aigcp_clear(self, _checked=False):
        try:
            aigcp.clear(self._state)
        except Exception as exc:
            journal.write("FastGCP: clearing the proposals: {!r}".format(exc))
        self.update_aigcp()

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def _show_export_menu(self):
        """Open the menu above the button, aligned on its right edge."""
        button = self.export_button
        menu = self.export_menu
        size = menu.sizeHint()
        origin = button.mapToGlobal(
            QtCore.QPoint(button.width() - size.width(), -size.height() - theme.PAD_S)
        )
        menu.popup(origin)

    def _on_export(self, kind, _checked=False):
        """Run the requested export. report.py is imported here, at click time."""
        name = str(kind).upper()
        try:
            from . import report
            function = getattr(report, "export_" + kind)
        except Exception as exc:
            self.set_status(i18n.t("panel.status.export_unavailable"))
            journal.write("Export {} unavailable: {!r}".format(name, exc))
            return
        journal.write("Export: {}".format(name))
        try:
            function(self._state, self)
        except Exception as exc:
            self.set_status(i18n.t("panel.status.export_failed"))
            journal.write("Export {} failed: {!r}".format(name, exc))

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def set_chunk_name(self, name):
        """Name of the current chunk, used in the status at the next refresh."""
        self._chunk_name = str(name or "")

    def set_status(self, text):
        """Status line of the panel footer."""
        try:
            self.status.set_full_text(text or "")
        except RuntimeError:
            pass

    # ------------------------------------------------------------------
    # Journal
    # ------------------------------------------------------------------

    def _subscribe_journal(self):
        self._line_added.connect(self._append_log)
        self._journal_cb = self._on_journal_line
        lines = journal.lines()
        if lines:
            self.log.setPlainText("\n".join(lines))
            self._scroll_log_to_end()
        journal.subscribe(self._journal_cb)

    def _on_journal_line(self, line):
        self._line_added.emit(line)

    def _append_log(self, line):
        try:
            self.log.appendPlainText(line)
        except RuntimeError:
            pass

    def _scroll_log_to_end(self):
        bar = self.log.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _fit_log_height(self):
        self.log.ensurePolished()
        metrics = self.log.fontMetrics()
        frame = self.log.frameWidth() * 2
        margin = int(self.log.document().documentMargin() * 2)
        self.log.setFixedHeight(metrics.lineSpacing() * LOG_VISIBLE_LINES + frame + margin)

    def _on_log_button(self):
        self._set_log_open(not self._log_open, persist=True)

    def _set_log_open(self, opened, persist):
        self._log_open = bool(opened)
        if self._log_open:
            self._fit_log_height()
        self.log.setVisible(self._log_open)
        if self._log_open:
            self._scroll_log_to_end()
        if persist:
            self._set_option("show_log", self._log_open)

    # ------------------------------------------------------------------
    # Closing
    # ------------------------------------------------------------------

    def detach(self):
        """Cut the links with the rest of the program. Never raises."""
        try:
            self.close_review()
        except Exception:
            pass
        try:
            if self._journal_cb is not None:
                journal.unsubscribe(self._journal_cb)
        except Exception:
            pass
        self._journal_cb = None
        try:
            history.unsubscribe(self._on_history_changed)
        except Exception:
            pass
        self._state = None
