"""FastGCP Qt panel: a dock at the right of Metashape, built on the UI kit's `DockShell`.

Main view: title, Undo / Redo / settings buttons, on / off switch with the state of the tool, AI detection
(Run is the one primary button), current marker (two counters), search, marker table (ID, X / Y / Z errors,
number of projections, sortable columns; an empty state when there is no marker). The footer carries
Residual check, Adjust, the Export menu and the HELIX credit (site link and GitHub). The gear opens the
Settings window (settings_dialog.py): AI engine and model, training, options and log.

The photo filter, the synchronisation with the Reference pane and the
move to the next photo are automatic: the panel does not offer them.

Rules of this module:
  - no direct call to Metashape: everything goes through `msapi`;
  - no hard-coded style: colours, sizes and spacing come from the kit tokens, controls from `uikit`;
  - no help sentence on screen: the explanation is behind the "i" of the header;
  - no hard-coded visible text: everything goes through `i18n.t(...)`;
  - never an exception towards the caller: a marker reference becomes
    invalid (RuntimeError) as soon as the chunk changes, the panel must
    survive and reread the list.
"""

import functools
import math
import re
import time

from ... import i18n, journal, msapi, svgpath, uikit
from ...qt import QtCore, QtGui, QtWidgets
from ...uikit import tokens
from . import aigcp, history, qc
from .session import DOCK_NAME

SITE_URL = "https://helix.aero"
SITE_NAME = "helix.aero"
GITHUB_URL = "https://github.com/helix-aero/AeroTools"
SETTLE_MS = (500, 2000)               # rereads of the table after a bulk modification
HEADER_ICON = 14
LABEL_MAX = 10                        # maximum length of the current marker name in its card

# Lines visible in the unfolded log.
LOG_VISIBLE_LINES = 6

# Minimum width of the dock: the marker table needs room.
MIN_PANEL_WIDTH = 420

RUN_ICON = 14

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
LIST_MIN_HEIGHT = 96

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


PLAY_ICON = 16


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
    icon.addPixmap(_run_pixmap(tokens.ON_ACCENT), QtGui.QIcon.Normal)
    icon.addPixmap(_run_pixmap(tokens.BORDER_STRONG), QtGui.QIcon.Disabled)
    return icon


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
    icon.addPixmap(pick(_undo_pixmap(tokens.TEXT_MUTED)), QtGui.QIcon.Normal)
    icon.addPixmap(pick(_undo_pixmap(tokens.TEXT_DISABLED, 0.45)), QtGui.QIcon.Disabled)
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
        painter.setBrush(QtGui.QColor(tokens.ACCENT))
    else:
        painter.setPen(QtGui.QPen(QtGui.QColor(tokens.TEXT_DISABLED), 1.4))
        painter.setBrush(QtCore.Qt.NoBrush)
    painter.drawEllipse(1, 1, DOT_SIZE - 1, DOT_SIZE - 1)
    painter.end()
    icon = QtGui.QIcon(pixmap)
    _DOT_ICONS[done] = icon
    return icon


# ----------------------------------------------------------------------
# Small widgets
# ----------------------------------------------------------------------

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
        painter.setPen(QtGui.QPen(_qcolor(tokens.BORDER), 1))
        painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())

        model = self.model()
        text = ""
        if model is not None:
            text = model.headerData(index, QtCore.Qt.Horizontal) or ""
        sorted_here = self.sortIndicatorSection() == index
        color = _qcolor(tokens.TEXT_STRONG if sorted_here else tokens.TEXT)
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
                muted = name == _qcolor(tokens.TEXT_DISABLED).name().lower()
                warn = name == _qcolor(tokens.WARNING).name().lower()
            except Exception:
                muted = warn = False
        if warn:
            color = _qcolor(tokens.WARNING)
        elif selected:
            color = _qcolor(tokens.TEXT if muted else tokens.TEXT_STRONG)
        else:
            color = _qcolor(tokens.TEXT_DISABLED if muted else tokens.TEXT)

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

    The children let the mouse through: hovering the icon or the number amounts to hovering the
    whole pill.
    """

    def __init__(self, kind, tip, parent=None):
        super(_Counter, self).__init__(parent)
        self.setProperty("ui", "card")
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self._kind = kind
        self.set_tip(tip)
        layout = uikit.hbox((tokens.S, tokens.XS, tokens.S + 2, tokens.XS), tokens.XS + 1, parent=self)
        self._icon = QtWidgets.QLabel()
        self._icon.setFixedSize(uikit.px(COUNTER_ICON), uikit.px(COUNTER_ICON))
        self._number = uikit.label("0", "strong", wrap=False)
        for child in (self._icon, self._number):
            child.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        layout.addWidget(self._icon)
        layout.addWidget(self._number)
        self.setSizePolicy(QtWidgets.QSizePolicy.Maximum, QtWidgets.QSizePolicy.Fixed)
        self.apply_theme()

    def apply_theme(self):
        """Icon in the colours of the kit."""
        self._icon.setPixmap(_counter_pixmap(self._kind, tokens.TEXT))

    def set_number(self, number):
        self._number.setText(str(int(number)))

    def set_tip(self, tip):
        self.setToolTip(tip)


def _icon_button(tip, handler):
    """Quiet square button with a painted icon (Undo, Redo, settings, GitHub); the icon is set by `_refresh_icons`."""
    button = uikit.Button("", "quiet", handler=handler)
    button.setFixedWidth(uikit.px(tokens.CONTROL_H))
    button.setIconSize(QtCore.QSize(HEADER_ICON, HEADER_ICON))
    button.setToolTip(tip)
    button.setAccessibleName(tip)
    return button


class FastGcpPanel(uikit.DockShell):
    """FastGCP dock. Public contract: see the methods without an underscore."""

    # The journal lines may come from another thread: a signal puts them
    # back in the interface thread.
    _line_added = QtCore.Signal(str)

    def __init__(self, state, parent=None):
        super(FastGcpPanel, self).__init__(i18n.t("panel.title"), info=i18n.t("panel.info"), parent=parent,
                                           closable=True, mark="tool_gcp", min_width=MIN_PANEL_WIDTH)
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

        self.setObjectName(DOCK_NAME)
        self._build_content()

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

    def _build_content(self):
        """Header buttons, then the body of the dock (the shell owns the root, the banner and the margins)."""
        self.body.setSpacing(uikit.px(tokens.S))
        self._build_header()
        self.body.addLayout(self._build_play())
        self.body.addWidget(self._build_aigcp_card())
        self.body.addLayout(self._build_active_card())
        self.body.addWidget(self._build_search())
        self.body.addWidget(self._build_list(), 1)
        self._build_more()                # opened in the Settings window, never in the column
        self.body.addWidget(uikit.Divider())
        self.body.addLayout(self._build_footer())
        self.body.addLayout(self._build_credit())
        self.status.hide()                # the "7/18 marked" status is no longer displayed; the label stays for the callers

    def _on_close_clicked(self):
        """The cross closes Aero-GCP: marking, timer and shortcuts stop, the panel disappears."""
        from ... import bootstrap
        from . import tool
        journal.write("Aero-GCP closed")
        bootstrap.forget_last("fastgcp")
        tool.uninstall()

    def _build_header(self):
        """Right of the title: Undo, Redo, settings (gear), then the cross."""
        try:
            self.close_button.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.close_button.clicked.connect(self._on_close_clicked)
        self.close_button.setToolTip(i18n.t("panel.close_tip"))
        self.undo_button = _icon_button(i18n.t("panel.undo_tip"), self._on_undo_clicked)
        self.redo_button = _icon_button(i18n.t("panel.redo_tip"), self._on_redo_clicked)
        self.undo_button.setEnabled(False)
        self.redo_button.setEnabled(False)
        self.more_button = _icon_button(i18n.t("panel.settings_tip"), self._open_settings)
        right = self.header.right
        right.insertWidget(0, self.more_button)
        right.insertWidget(0, self.redo_button)
        right.insertWidget(0, self.undo_button)

    def _build_play(self):
        """On / off switch, then the state text."""
        row = uikit.hbox(spacing=tokens.M)
        self.toggle = uikit.Toggle("", True)
        self.toggle.setAccessibleName(i18n.t("panel.state_on"))
        self.toggle.clicked.connect(self._on_toggle_clicked)
        row.addWidget(self.toggle, 0)
        self.state_text = uikit.ElidedLabel(i18n.t("panel.state_on"), "lead")
        row.addWidget(self.state_text, 1)
        return row

    def _build_aigcp_card(self):
        """AI detection: target type, scope (active marker or all), run. Run is the one primary button of the view."""
        section = uikit.Section(i18n.t("panel.aigcp"))
        self._ai_section = section
        section.body.setSpacing(uikit.px(tokens.S))

        actions = uikit.hbox(spacing=tokens.S)
        self.aigcp_clear_button = uikit.Button("", "secondary", handler=self._on_aigcp_clear)
        self.aigcp_review_button = uikit.Button("", "secondary", handler=self._on_aigcp_review)
        self.aigcp_button = uikit.Button("", "primary", icon=None, handler=self._on_aigcp_run)
        self.aigcp_button.setIconSize(QtCore.QSize(RUN_ICON, RUN_ICON))
        actions.addWidget(self.aigcp_clear_button, 3)
        actions.addWidget(self.aigcp_review_button, 2)
        actions.addWidget(self.aigcp_button, 2)
        section.body.addLayout(actions)

        # Two rows of lists: type and scope, then the photos to leave alone.
        choices = QtWidgets.QGridLayout()
        choices.setContentsMargins(0, 0, 0, 0)
        choices.setHorizontalSpacing(uikit.px(tokens.S))
        choices.setVerticalSpacing(uikit.px(tokens.S))
        choices.addWidget(self._build_combo("type", aigcp.TYPES), 0, 0)
        choices.addWidget(self._build_combo("scope", aigcp.SCOPES), 0, 1)
        choices.addWidget(self._build_combo("ignore", aigcp.IGNORES), 1, 0, 1, 2)
        choices.setColumnStretch(0, 2)
        choices.setColumnStretch(1, 3)
        section.body.addLayout(choices)

        # Progress bar with its text, and Cancel at the right; visible during a computation.
        self.aigcp_bar_row = uikit.ProgressRow(cancellable=True, icon_cancel=True)
        self.aigcp_bar_row.cancelRequested.connect(self._on_aigcp_cancel)
        self.aigcp_bar_row.setVisible(False)
        section.body.addWidget(self.aigcp_bar_row)
        self._set_aigcp_texts()
        return section

    def _build_combo(self, key, values):
        """Drop-down list of an AI detection choice; the texts are set by `_set_aigcp_texts`."""
        combo = uikit.ChoiceBox()
        combo.setMinimumContentsLength(4)
        for value in values:
            combo.addItem("", value)
        combo.activated.connect(functools.partial(self._on_aigcp_combo, key))
        self._aigcp_combos[key] = combo
        return combo

    def _build_active_card(self):
        """A single line: title, identifier (truncated to 10 characters), counters."""
        line = uikit.hbox(spacing=tokens.M)
        self._eyebrow_active = uikit.ElidedLabel(i18n.t("panel.active_marker").upper(), "caption", fit=True)
        line.addWidget(self._eyebrow_active, 0)
        self.active_value = uikit.ElidedLabel("—", "title", "faint")
        line.addWidget(self.active_value, 1)

        # The pills keep their place when they are hidden: the line
        # does not change height from one marker to the next.
        self.counters = QtWidgets.QWidget()
        policy = self.counters.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.counters.setSizePolicy(policy)
        row = uikit.hbox(spacing=tokens.S, parent=self.counters)
        self.count_pointed = _Counter("target", i18n.t("panel.tip_pointed"))
        self.count_photos = _Counter("image", i18n.t("panel.tip_seen"))
        row.addWidget(self.count_pointed)
        row.addWidget(self.count_photos)
        line.addWidget(self.counters, 0)
        return line

    def _build_search(self):
        self.search = uikit.TextField()
        self.search.setPlaceholderText(i18n.t("panel.search"))
        self.search.setClearButtonEnabled(False)
        self.search.textChanged.connect(self._on_search)
        return self.search

    def _build_list(self):
        """Marker table: ID, X / Y / Z errors, number of projections; an empty state when the chunk has no marker."""
        box = QtWidgets.QFrame()
        box.setProperty("ui", "card")
        box.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        box.setMinimumHeight(uikit.px(LIST_MIN_HEIGHT))
        box.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        inner = uikit.vbox((3, 3, 3, 3), 0, parent=box)
        self._pages = QtWidgets.QStackedLayout()
        inner.addLayout(self._pages)

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
        self._pages.addWidget(grid)
        self.empty = uikit.EmptyState(i18n.t("panel.empty_title"), i18n.t("panel.empty_text"))
        self.empty.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Ignored)   # a short table must not grow the dock
        self._pages.addWidget(self.empty)
        self.grid = grid
        # Former name of the widget, kept for the existing callers.
        self.list = grid
        return box

    def _build_more(self):
        """Options card: recording of the markings and log. Shown in the Settings window."""
        self.more = QtWidgets.QWidget()
        layout = uikit.vbox(spacing=tokens.S, parent=self.more)

        layout.addWidget(self._check("ai_auto_review", i18n.t(CHECK_TEXTS["ai_auto_review"])))
        layout.addWidget(self._check("collect_dataset", i18n.t(CHECK_TEXTS["collect_dataset"])))

        self.log_disclosure = uikit.Disclosure(i18n.t("panel.log"))
        self.log_button = self.log_disclosure.head
        self.log_disclosure.toggled.connect(self._on_log_toggled)
        layout.addWidget(self.log_disclosure)

        self.log = QtWidgets.QPlainTextEdit()
        self.log.setObjectName("aeroLog")
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(journal.MAX_LINES)
        self.log.setLineWrapMode(QtWidgets.QPlainTextEdit.WidgetWidth)
        self.log.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.log_disclosure.add(self.log)

        return self.more

    def _check(self, key, text):
        box = uikit.Toggle(text)
        self._checks[key] = box
        box.toggled.connect(functools.partial(self._on_check, key))
        return box

    def _build_footer(self):
        """One row: residual check and adjust on the left, Export (a menu) on the right. All secondary: Run is the primary."""
        row = uikit.hbox(spacing=tokens.S)
        self.qc_button = uikit.Button(i18n.t("panel.qc"), "secondary", handler=self._open_qc)
        row.addWidget(self.qc_button, 0)
        self.adjust_button = uikit.Button(i18n.t("panel.adjust"), "secondary", handler=self._open_adjust)
        row.addWidget(self.adjust_button, 0)
        row.addStretch(1)

        self.export_button = uikit.Button("", "secondary", handler=self._show_export_menu)
        self.export_button.setIconSize(QtCore.QSize(EXPORT_ICON, EXPORT_ICON))
        self._set_export_text()
        self.export_menu = QtWidgets.QMenu(self.export_button)
        self._export_actions = {}
        for kind, text_key in EXPORTS:
            action = self.export_menu.addAction(i18n.t(text_key))
            action.triggered.connect(functools.partial(self._on_export, kind))
            self._export_actions[kind] = action
        row.addWidget(self.export_button, 0)
        return row

    def _build_credit(self):
        """At the very bottom: "Tool made by helix.aero" (link) and the GitHub icon (link)."""
        row = uikit.hbox(spacing=tokens.XS)
        self.credit = uikit.label("", "small", "muted", wrap=False)
        self.credit.setTextFormat(QtCore.Qt.RichText)
        self.credit.setOpenExternalLinks(True)
        self._set_credit_text()
        row.addWidget(self.credit, 0)
        self.github_button = _icon_button(i18n.t("panel.github_tip"), self._open_github)
        self.github_button.setFixedSize(uikit.px(tokens.CONTROL_H), uikit.px(tokens.CONTROL_H))
        row.addWidget(self.github_button, 0)
        row.addStretch(1)
        return row

    def _set_credit_text(self):
        link = '<a href="{url}" style="color:{color};text-decoration:none">{name}</a>'.format(
            url=SITE_URL, color=tokens.ACCENT, name=SITE_NAME)
        self.credit.setText(i18n.t("panel.credit", site=link))

    def _open_github(self, _checked=False):
        try:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(GITHUB_URL))
        except Exception as exc:
            journal.write("Aero-GCP: GitHub link: {!r}".format(exc))

    # ------------------------------------------------------------------
    # Icons
    # ------------------------------------------------------------------

    def refresh_skin(self):
        """A skin change while the dock is open (the theme button): the icons and the colours of the table rows are painted from the
        tokens when they are built, so build them again (live check: the light skin showed white row text on white)."""
        try:
            self._refresh_icons()
            self._signature = None
            self.refresh_markers()
            self.grid.viewport().update()
        except RuntimeError:
            pass

    def _refresh_icons(self):
        """Paint all the icons in the colours of the kit."""
        try:
            self._apply_toggle(self._running)
            self.more_button.setIcon(QtGui.QIcon(_gear_pixmap(tokens.TEXT)))
            self.github_button.setIcon(QtGui.QIcon(_github_pixmap(tokens.TEXT_MUTED)))
            self.undo_button.setIcon(_undo_icon())
            self.redo_button.setIcon(_undo_icon(mirror=True))
            self.export_button.setIcon(QtGui.QIcon(_export_pixmap(tokens.TEXT)))
            self.aigcp_button.setIcon(_run_icon())
            self.count_pointed.apply_theme()
            self.count_photos.apply_theme()
        except RuntimeError:
            pass

    def _set_export_text(self):
        """Text of the Export button, elided if it is too long."""
        text = i18n.t("panel.export")
        metrics = self.export_button.fontMetrics()
        self.export_button.setText(
            metrics.elidedText(text, QtCore.Qt.ElideRight, EXPORT_TEXT_MAX_PX))

    def _set_aigcp_texts(self):
        """Texts of the AI Detection card, displayed choices, button state."""
        self._ai_section.title.setText(i18n.t("panel.aigcp"))
        for key, combo in self._aigcp_combos.items():
            for index in range(combo.count()):
                combo.setItemText(index, i18n.t(AIGCP_CHOICE_TEXTS[(key, combo.itemData(index))]))
        self.aigcp_button.setText(i18n.t("aigcp.run"))
        self.aigcp_clear_button.setText(i18n.t("aigcp.clear"))
        self.aigcp_review_button.setText(i18n.t("aigcp.review"))
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
            journal.write("Aero-GCP: reading the settings: {!r}".format(exc))

    def _set_option(self, key, value):
        state = self._state
        if state is None:
            return
        try:
            state.set_option(key, value)
        except Exception as exc:
            journal.write("Aero-GCP: setting {} not saved: {!r}".format(key, exc))

    def _on_check(self, key, checked):
        self._set_option(key, bool(checked))
        journal.write("Setting {}: {}".format(key, "yes" if checked else "no"))

    # ------------------------------------------------------------------
    # Play / Pause
    # ------------------------------------------------------------------

    def _apply_toggle(self, running):
        """Align the button, dot and state text with `running`."""
        self._running = bool(running)
        self.toggle.setChecked(bool(running))
        self.toggle.setAccessibleName(i18n.t("panel.state_on" if running else "panel.state_off"))
        self.state_text.setFullText(
            i18n.t("panel.state_on" if running else "panel.state_off"))
        uikit.set_tone(self.state_text, None if running else "muted")
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
        journal.write("Aero-GCP {}".format("active" if running else "paused"))

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
            journal.write("Aero-GCP: Settings window: {!r}".format(exc))

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
            self.active_value.setFullText(short_label(label))
            uikit.set_tone(self.active_value, None)
            self.active_value.setToolTip(label if label != short_label(label) else "")
            self.count_pointed.set_number(self._pointed_count(state.active_marker))
            self.count_photos.set_number(getattr(state, "last_photo_count", 0) or 0)
            self.counters.setVisible(True)
        else:
            self.active_value.setFullText("—")
            uikit.set_tone(self.active_value, "faint")
            self.counters.setVisible(False)
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
            journal.write("Aero-GCP: marker {} not activated: {!r}".format(label, exc))
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
            journal.write("Aero-GCP: errors not refreshed: {!r}".format(exc))

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
            journal.write("Aero-GCP: rows not refreshed: {!r}".format(exc))

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
            journal.write("Aero-GCP: Reference selection: {!r}".format(exc))
        try:
            from . import photos
            photos.filter_for_marker(state, marker, force=True)
        except Exception as exc:
            journal.write("Aero-GCP: photos filter: {!r}".format(exc))
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
            signature = msapi.markers_signature(chunk)      # a few reads per marker: no error is computed just to compare
            self.poll_cost = time.time() - start
            previous, self._signature = self._signature, signature
            if previous is not None and previous != signature:
                bar = self.grid.verticalScrollBar()
                position = bar.value()
                self.refresh_markers()
                bar.setValue(position)             # the table is not made to jump while it is being read
        except Exception as exc:
            journal.write("Aero-GCP: table follow-up: {!r}".format(exc))

    def refresh_markers(self):
        """Reread the markers of the current chunk. Never raises."""
        if self._state is None:
            return
        try:
            self._rebuild_list()
        except Exception as exc:
            journal.write("Aero-GCP: marker reread: {!r}".format(exc))
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
        if rows:
            self._pages.setCurrentWidget(self.grid)
        else:
            if needle and total:
                self.empty.set_state(i18n.t("panel.empty_search"))
            else:
                self.empty.set_state(i18n.t("panel.empty_title"), i18n.t("panel.empty_text"))
            self._pages.setCurrentWidget(self.empty)

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
        item.setForeground(0, QtGui.QBrush(_qcolor(tokens.TEXT_STRONG)))
        muted = QtGui.QBrush(_qcolor(tokens.TEXT_DISABLED))
        normal = QtGui.QBrush(_qcolor(tokens.TEXT))
        warn = QtGui.QBrush(_qcolor(tokens.WARNING))
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
        menu = QtWidgets.QMenu(self.grid)
        count = len(labels)
        for key, handler in (("panel.remove_projections", self._remove_projections),
                             ("panel.remove_markers", self._remove_markers)):
            action = menu.addAction(i18n.t(key, n=count))
            action.triggered.connect(lambda *_args, h=handler, names=labels: h(names))
        run = getattr(menu, "exec_", None) or getattr(menu, "exec")
        run(self.grid.viewport().mapToGlobal(pos))

    def _confirm(self, text_key, labels):
        shown = ", ".join(labels[:5]) + ("\u2026" if len(labels) > 5 else "")
        return uikit.confirm(self, i18n.t("panel.remove_title"), i18n.t(text_key, n=len(labels), names=shown),
                             ok=i18n.t("panel.remove_title"), danger=True)

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
            journal.write("Aero-GCP: {} projection(s) removed on {} marker(s)".format(total, len(found)))
        except Exception as exc:
            journal.write("Aero-GCP: removal of the projections: {!r}".format(exc))
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
            journal.write("Aero-GCP: {} marker(s) removed: {}".format(len(gone), ", ".join(gone)))
        except Exception as exc:
            journal.write("Aero-GCP: removal of the markers: {!r}".format(exc))

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
            journal.write("Aero-GCP: marker choice: {!r}".format(exc))

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
            journal.write("Aero-GCP: redo: {!r}".format(exc))
        self._update_undo_button()

    def _on_undo_clicked(self):
        state = self._state
        if state is None:
            return
        try:
            from . import marking
            marking.undo_last(state)
        except Exception as exc:
            journal.write("Aero-GCP: undo: {!r}".format(exc))
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
            journal.write("Aero-GCP: residual check: {!r}".format(exc))

    def _on_qc_open(self, item):
        """Double-click in the window: the marker, and its worst photo when it is a high residual."""
        self._on_qc_pick(item["label"], item["status"] == qc.RESIDUAL)

    def _adjust_collect(self):
        """Read of the markers for "Adjust" (main thread): points, ticked boxes, PPK or not."""
        chk = msapi.chunk()
        points, why = msapi.marker_frame_points(chk)
        used, total = msapi.cameras_georeferenced(chk)
        return {"points": points, "why": why, "ppk": bool(total) and used >= 0.5 * total,
                "context": msapi.chunk_identity(chk),
                "enabled": {p["label"]: bool(msapi.marker_enabled(p["marker"])) for p in points}}

    def _adjust_apply(self, labels, context=None, analysed=None):
        """Tick these markers as control points and untick the other analysed ones. Returns (number ticked, error).

        `context`: chunk identity the result was computed for; another chunk or project: nothing is touched.
        `analysed`: labels that were in the computation; the markers outside it (too few photos, added since) keep their box."""
        try:
            chk = msapi.chunk()
            if context is not None and msapi.chunk_identity(chk) != context:
                return 0, i18n.t("gcp.stale_result")
            points, why = msapi.marker_frame_points(chk)
            if not points:
                return 0, why
            wanted, before = set(labels), []
            count = 0
            for point in points:
                if analysed is not None and point["label"] not in analysed and point["label"] not in wanted:
                    continue
                before.append("{}={}".format(point["label"], msapi.marker_enabled(point["marker"])))
                on = point["label"] in wanted
                if msapi.set_marker_enabled(point["marker"], on):
                    count += 1 if on else 0
            msapi.refresh()
            journal.write("Aero-GCP: check markers ticked: {} (before: {})".format(
                ", ".join(sorted(wanted)), ", ".join(before)))
            return count, ""
        except Exception as exc:
            journal.write("Aero-GCP: ticking the check markers: {!r}".format(exc))
            return 0, repr(exc)

    def _open_adjust(self, _checked=False):
        """"Adjust" window: which markers to tick as check points (see gcp_select_dialog.py)."""
        try:
            from . import gcp_select_dialog
            self._adjust_dialog = gcp_select_dialog.show(self, self._adjust_collect, self._adjust_apply,
                                                         getattr(self, "_adjust_dialog", None))
        except Exception as exc:
            journal.write("Aero-GCP: adjustment of the check markers: {!r}".format(exc))

    def _open_diagnostic(self, _checked=False):
        """Window of the full marking diagnostic (see diagnostic.py)."""
        try:
            from . import diagnostic, diagnostic_dialog

            def compute():
                return diagnostic.analyze(diagnostic.collect(msapi.chunk()), self._all_rows())

            self._diag_dialog = diagnostic_dialog.show(
                self, compute, self._on_diag_open, getattr(self, "_diag_dialog", None))
        except Exception as exc:
            journal.write("Aero-GCP: marking diagnostic: {!r}".format(exc))

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
            journal.write("Aero-GCP: opening from the diagnostic: {!r}".format(exc))

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
            journal.write("Aero-GCP: choice in the check: {!r}".format(exc))

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
            self.aigcp_bar_row.set_progress(min(progress[0], progress[1]) / float(progress[1]), aigcp.bar_text(state))
        elif running:
            self.aigcp_bar_row.set_indeterminate(aigcp.bar_text(state))
        else:
            self.aigcp_bar_row.reset()
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
            if target is None:
                uikit.inform(self, i18n.t("aigcp.setup_title"), text)
                return
            if uikit.confirm(self, i18n.t("aigcp.setup_title"), text, ok=i18n.t("settings.install")):
                from . import install_ui
                install_ui.run(self, target, confirm=False)
                self.update_aigcp()
        except Exception as exc:
            journal.write("Aero-GCP: AI setup message: {!r}".format(exc))

    def open_review(self):
        """Open the review window of the points of the last detection. Never raises."""
        try:
            from . import review_dialog
            self._review_dialog = review_dialog.show(self._state, self, self._review_dialog)
        except Exception as exc:
            journal.write("Aero-GCP: AI points review: {!r}".format(exc))

    def close_review(self):
        try:
            from . import review_dialog
            review_dialog.close(self._review_dialog)
        except Exception as exc:
            journal.write("Aero-GCP: closing the AI review: {!r}".format(exc))
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
            journal.write("Aero-GCP: AI detection: {!r}".format(exc))
        self.update_aigcp()

    def _on_aigcp_cancel(self, _checked=False):
        try:
            aigcp.abort(self._state)
        except Exception as exc:
            journal.write("Aero-GCP: cancelling the detection: {!r}".format(exc))
        self.update_aigcp()

    def _on_aigcp_clear(self, _checked=False):
        try:
            aigcp.clear(self._state)
        except Exception as exc:
            journal.write("Aero-GCP: clearing the proposals: {!r}".format(exc))
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
            QtCore.QPoint(button.width() - size.width(), -size.height() - tokens.XS)
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
            self.show_banner("warning", i18n.t("panel.status.export_unavailable"))
            journal.write("Export {} unavailable: {!r}".format(name, exc))
            return
        journal.write("Export: {}".format(name))
        try:
            function(self._state, self)
        except Exception as exc:
            self.set_status(i18n.t("panel.status.export_failed"))
            self.show_banner("danger", i18n.t("panel.status.export_failed"), details=repr(exc))
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
            self.status.setFullText(text or "")
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

    def _on_log_toggled(self, opened):
        self._set_log_open(opened, persist=True)

    def _set_log_open(self, opened, persist):
        self._log_open = bool(opened)
        if self._log_open:
            self._fit_log_height()
        if self.log_disclosure.is_open() != self._log_open:
            self.log_disclosure.blockSignals(True)
            self.log_disclosure.set_open(self._log_open)
            self.log_disclosure.blockSignals(False)
            self.log_disclosure.body.setVisible(self._log_open)
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
