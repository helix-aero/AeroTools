"""DataTable: the one table skin of the suite. Sortable by clicking a header, cells cut with an ellipsis, rows never edited,
and a built-in empty / loading / error state in place of the grid when there is nothing to show.

Beyond text it has: a checkbox column (tickable rows, a header box that ticks all), a status-icon column, per-cell tone and weight,
and disabled rows. The header, the checkboxes and the icons are drawn here from the tokens at paint time, so any skin reaches them.
"""

from ..qt import QtCore, QtGui, QtWidgets
from . import icons, metrics, style, tokens
from .blocks import EmptyState, _glyph_look
from .metrics import px

Qt = QtCore.Qt
SORT_ROLE = Qt.UserRole + 1
CHECK_ROLE = Qt.UserRole + 2
KIND_ROLE = Qt.UserRole + 3
TONE_ROLE = Qt.UserRole + 4
TONES = ("success", "warning", "danger", "info", "muted", "faint", "accent", "strong")


def cell(text="", sort=None, tone=None, bold=False, icon=None, tip=None):
    """A cell with more than text: `tone` (success, warning, danger, info, muted, faint, accent, strong), `bold`, an `icon` kind for a
    status column (ok, warn, error, info, pending, skipped, running), a tooltip. Use it in place of a plain text in a row."""
    return {"text": text, "sort": sort, "tone": tone, "bold": bold, "icon": icon, "tip": tip}


def tone_color(tone):
    """The colour of a tone name, read from the tokens now (a '#rrggbb' passes through)."""
    t = tokens
    table = {"success": t.SUCCESS, "warning": t.WARNING, "danger": t.DANGER, "info": t.INFO, "muted": t.TEXT_MUTED, "faint": t.TEXT_DISABLED,
             "accent": t.ACCENT, "strong": t.TEXT_STRONG}
    return table.get(tone, tone)


class Column(object):
    """One column: `key` (how rows name it), `title`, `align` ('left' or 'right'), `width` (design px, None = fits its content),
    `stretch` (takes the remaining width), `kind` ('text' sorts as text, 'number' sorts by the number given as the cell's sort value,
    'check' a tick box per row (no text), 'status' a status icon (the cell is an icon kind))."""

    def __init__(self, key, title, align="left", width=None, stretch=False, kind="text"):
        self.key, self.title, self.align, self.width, self.stretch, self.kind = key, title, align, width, stretch, kind
        if kind in ("check", "status") and width is None:
            self.width = 40 if kind == "check" else 44


def draw_check(painter, rect, state, enabled=True, hover=False):
    """The kit's checkbox (state: True, False or 'partial'), drawn in `rect` (QRectF). Clearly visible on every skin."""
    painter.save()
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    side = float(px(16))
    box = QtCore.QRectF(rect.center().x() - side / 2.0, rect.center().y() - side / 2.0, side, side)
    radius = min(float(px(4)), float(px(tokens.RADIUS)) or 0.0) if tokens.RADIUS else 0.0
    on = state is True or state == "partial"
    if on and enabled:
        painter.setPen(QtGui.QPen(QtGui.QColor(tokens.ACCENT), 1.5))
        painter.setBrush(QtGui.QColor(tokens.ACCENT_HOVER if hover else tokens.ACCENT))
    elif on:
        painter.setPen(QtGui.QPen(QtGui.QColor(tokens.BORDER), 1.5))
        painter.setBrush(QtGui.QColor(tokens.SURFACE_PRESSED))
    else:
        painter.setPen(QtGui.QPen(QtGui.QColor((tokens.TEXT if hover else tokens.TEXT_MUTED) if enabled else tokens.BORDER), 1.5))
        painter.setBrush(QtGui.QColor(tokens.SURFACE if enabled else tokens.BG))
    painter.drawRoundedRect(box.adjusted(0.75, 0.75, -0.75, -0.75), radius, radius)
    mark = tokens.ON_ACCENT if enabled else tokens.TEXT_DISABLED
    if state is True:
        icons.paint(painter, "check", box.adjusted(2, 2, -2, -2), mark, width=max(1.8, side / 8.0))
    elif state == "partial":
        icons.paint(painter, "minus", box.adjusted(2, 2, -2, -2), mark, width=max(1.8, side / 8.0))
    painter.restore()


class _Item(QtWidgets.QTableWidgetItem):
    def __lt__(self, other):
        a, b = self.data(SORT_ROLE), other.data(SORT_ROLE)
        try:
            return a < b
        except TypeError:
            return str(a) < str(b)


class _Delegate(QtWidgets.QStyledItemDelegate):
    """Draws the check and status columns (and leaves text columns to the style sheet)."""

    def __init__(self, owner):
        super(_Delegate, self).__init__(owner.table)
        self.owner = owner

    def initStyleOption(self, option, index):
        super(_Delegate, self).initStyleOption(option, index)
        tone = index.data(TONE_ROLE)
        if tone and (index.flags() & Qt.ItemIsEnabled):                     # the colour of a tone is read now: a skin switch reaches it
            colour = QtGui.QColor(tone_color(tone))
            for group in (QtGui.QPalette.Active, QtGui.QPalette.Inactive):
                option.palette.setColor(group, QtGui.QPalette.Text, colour)
                option.palette.setColor(group, QtGui.QPalette.HighlightedText, colour)

    def paint(self, painter, option, index):
        kind = self.owner.columns[index.column()].kind
        if kind not in ("check", "status"):
            super(_Delegate, self).paint(painter, option, index)
            return
        opt = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        opt.icon = QtGui.QIcon()
        opt.features &= ~QtWidgets.QStyleOptionViewItem.HasCheckIndicator
        widget = option.widget
        style_ = widget.style() if widget is not None else QtWidgets.QApplication.style()
        style_.drawControl(QtWidgets.QStyle.CE_ItemViewItem, opt, painter, widget)
        enabled = bool(index.flags() & Qt.ItemIsEnabled)
        rect = QtCore.QRectF(option.rect)
        if kind == "check":
            hover = bool(option.state & QtWidgets.QStyle.State_MouseOver)
            draw_check(painter, rect, bool(index.data(CHECK_ROLE)), enabled, hover and enabled)
        else:
            name, color = _glyph_look(str(index.data(KIND_ROLE) or "pending"))
            side = float(px(tokens.ICON))
            box = QtCore.QRectF(rect.center().x() - side / 2.0, rect.center().y() - side / 2.0, side, side)
            icons.paint(painter, name, box, color if enabled else tokens.TEXT_DISABLED, background=tokens.BORDER_STRONG)

    def editorEvent(self, event, model, option, index):
        if self.owner.columns[index.column()].kind == "check" and (index.flags() & Qt.ItemIsEnabled):
            if event.type() == QtCore.QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                self.owner._toggle_visual_row(index.row())
                return True
            if event.type() in (QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonDblClick):
                return True
        return super(_Delegate, self).editorEvent(event, model, option, index)


class _Header(QtWidgets.QHeaderView):
    """The header, drawn here: skin fill, a chevron next to the text of the sorted column (never over it, on either alignment), a
    tick box that ticks all rows in a check column."""

    owner = None

    def _column(self, index):
        return self.owner.columns[index] if self.owner is not None and 0 <= index < len(self.owner.columns) else None

    def _font(self):
        font = metrics.font("small", strong=True, family="label")
        if tokens.LABEL_CASE == "upper":
            font.setCapitalization(QtGui.QFont.AllUppercase)
        if tokens.LABEL_SPACING > 0:
            font.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, tokens.LABEL_SPACING * 0.6 * metrics.font_px("small"))
        return font

    def _room(self):
        return px(12) + px(tokens.XS)

    def sectionSizeFromContents(self, logicalIndex):
        size = super(_Header, self).sectionSizeFromContents(logicalIndex)
        column = self._column(logicalIndex)
        if column is None or column.kind in ("check", "status"):
            return size
        text = self.model().headerData(logicalIndex, Qt.Horizontal, Qt.DisplayRole) or ""
        fm = QtGui.QFontMetrics(self._font())
        wide = fm.horizontalAdvance(str(text).upper() if tokens.LABEL_CASE == "upper" else str(text)) + 2 * px(tokens.S)
        if self.isSortIndicatorShown():
            wide += self._room()
        return QtCore.QSize(max(size.width(), wide), size.height())

    def paintSection(self, painter, rect, index):
        column = self._column(index)
        painter.save()
        full = QtGui.QPainterPath()
        radius = float(px(tokens.RADIUS_L) - 1)
        bounds = QtCore.QRectF(0, 0, self.viewport().width(), rect.height())
        full.addRoundedRect(bounds.adjusted(0, 0, 0, radius), radius, radius)
        painter.setClipPath(full)
        painter.fillRect(rect, QtGui.QColor(tokens.TABLE_HEADER))
        painter.setClipping(False)
        painter.setPen(QtGui.QPen(QtGui.QColor(tokens.BORDER_STRONG), 1))
        painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())
        if column is not None and column.kind == "check":
            state = self.owner._check_state()
            draw_check(painter, QtCore.QRectF(rect), state, self.owner._has_enabled_rows(), False)
            painter.restore()
            return
        text = self.model().headerData(index, Qt.Horizontal, Qt.DisplayRole) or ""
        right = column is not None and column.align == "right"
        sorted_here = self.isSortIndicatorShown() and self.sortIndicatorSection() == index and column is not None and column.kind != "status"
        painter.setFont(self._font())
        painter.setPen(QtGui.QColor(tokens.TEXT_MUTED))
        fm = painter.fontMetrics()
        shown = str(text).upper() if tokens.LABEL_CASE == "upper" else str(text)
        pad = px(tokens.S)
        room = self._room() if sorted_here else 0
        avail = max(px(8), rect.width() - 2 * pad - room)
        status = column is not None and column.kind == "status"
        elided = fm.elidedText(shown, Qt.ElideRight, avail)
        if status and elided != shown:
            elided = ""                                  # an icon column's title that does not fit is left out, not cut
        tw = fm.horizontalAdvance(elided)
        if status:
            x_text = rect.left() + (rect.width() - tw) / 2.0
        elif right:
            x_text = rect.right() - pad - tw
        else:
            x_text = rect.left() + pad
        painter.drawText(QtCore.QRectF(x_text, rect.top(), tw + 1, rect.height()), Qt.AlignVCenter | Qt.AlignLeft, elided)
        if sorted_here:
            side = px(12)
            name = "chevron_up" if self.sortIndicatorOrder() == Qt.AscendingOrder else "chevron_down"
            x = x_text - side - px(tokens.XS) if right else x_text + tw + px(tokens.XS)
            x = max(rect.left() + 1, min(x, rect.right() - side - 1))
            icons.paint(painter, name, QtCore.QRectF(x, rect.top() + (rect.height() - side) / 2.0, side, side), tokens.TEXT_MUTED)
        painter.restore()

    def mousePressEvent(self, event):
        index = self.logicalIndexAt(event.pos())
        column = self._column(index)
        if column is not None and column.kind == "check" and event.button() == Qt.LeftButton:
            self.owner._toggle_all()
            event.accept()
            return
        super(_Header, self).mousePressEvent(event)


class _Table(QtWidgets.QTableWidget):
    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Space and self.owner is not None and self.owner._check_column() is not None:
            self.owner._toggle_selected()
            event.accept()
            return
        super(_Table, self).keyPressEvent(event)

    owner = None


class DataTable(QtWidgets.QWidget):
    """A table of rows. `columns` is a list of `Column`; a row is a list (or dict by column key) of cells; a cell is a text,
    `(text, sort_value)` or a `cell(...)`. `set_rows(rows)` replaces everything. Empty / loading / error: `set_empty`, `set_loading`,
    `set_error`. Ticks: `checked_rows()`, `set_checked(row, on)`, `check_all(on)`, `checkedChanged`. `set_row_enabled(row, False)` greys a row
    out (it cannot be selected or ticked)."""
    selectionChanged = QtCore.Signal()
    rowActivated = QtCore.Signal(int)
    checkedChanged = QtCore.Signal()

    def __init__(self, columns, selection="single", sortable=True, empty_title="", empty_text="", parent=None):
        super(DataTable, self).__init__(parent)
        self.columns = list(columns)
        self._rows = []
        self._base_min_h = px(tokens.CONTROL_H) * 4
        self._stack = QtWidgets.QStackedLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self.table = _Table(0, len(self.columns))
        self.table.owner = self
        self.table.setProperty("ui", "table")
        head = _Header(Qt.Horizontal, self.table)
        head.owner = self
        self.table.setHorizontalHeader(head)
        self.table.setItemDelegate(_Delegate(self))
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode({"none": QtWidgets.QAbstractItemView.NoSelection, "extended": QtWidgets.QAbstractItemView.ExtendedSelection}
                                    .get(selection, QtWidgets.QAbstractItemView.SingleSelection))
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.ElideRight)
        self.table.setMouseTracking(True)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(px(tokens.CONTROL_H))
        self.table.verticalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Fixed)
        header = self.table.horizontalHeader()
        header.setFixedHeight(px(tokens.CONTROL_H))
        header.setHighlightSections(False)
        header.setSortIndicatorShown(sortable)
        header.setSectionsClickable(True)
        header.setStretchLastSection(False)
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.table.setFocusPolicy(Qt.StrongFocus)
        self.table.setHorizontalHeaderLabels([c.title for c in self.columns])
        for index, column in enumerate(self.columns):
            self.table.horizontalHeaderItem(index).setTextAlignment((Qt.AlignRight if column.align == "right" else Qt.AlignLeft) | Qt.AlignVCenter)
        for index, column in enumerate(self.columns):
            if column.width:
                header.setSectionResizeMode(index, QtWidgets.QHeaderView.Fixed)
                self.table.setColumnWidth(index, px(column.width))
            elif column.stretch:
                header.setSectionResizeMode(index, QtWidgets.QHeaderView.Stretch)
            else:
                header.setSectionResizeMode(index, QtWidgets.QHeaderView.ResizeToContents)
        if not any(c.stretch for c in self.columns) and self.columns:
            last = len(self.columns) - 1
            if self.columns[last].kind == "text" or self.columns[last].kind == "number":
                header.setSectionResizeMode(last, QtWidgets.QHeaderView.Stretch)
        self.table.setSortingEnabled(bool(sortable))
        self._sortable = bool(sortable)
        if self._check_column() is not None:
            header.setSortIndicatorShown(sortable)
        self.table.itemSelectionChanged.connect(self.selectionChanged.emit)
        self.table.cellDoubleClicked.connect(lambda row, _c: self.rowActivated.emit(row))
        self.empty = EmptyState(empty_title, empty_text)
        self.empty.set_card(True)
        self._stack.addWidget(self.table)
        self._stack.addWidget(self.empty)
        self._empty_args = (empty_title, empty_text, "empty", None)
        self._stack.setCurrentWidget(self.empty if (empty_title or empty_text) else self.table)
        self._guard = False
        self._saved_min = None

    def sizeHint(self):
        return QtCore.QSize(px(520), px(tokens.CONTROL_H) * 7)

    def minimumSizeHint(self):
        return QtCore.QSize(px(280), max(self._base_min_h, self._empty_need()))

    def _empty_need(self):
        """Height the empty / loading / error panel needs at the present width (its text wraps; it must never be clipped)."""
        if self._stack.currentWidget() is not self.empty or self.width() <= 0:
            return 0
        return self.empty.heightForWidth(self.width()) if self.empty.hasHeightForWidth() else self.empty.sizeHint().height()

    def resizeEvent(self, event):
        super(DataTable, self).resizeEvent(event)
        self._pin_empty_height()

    def _pin_empty_height(self):
        """While the empty panel shows, the table is at least as tall as that panel's wrapped text needs; afterwards the minimum a tool
        set (if any) comes back."""
        if self._guard:
            return
        self._guard = True
        try:
            need = self._empty_need()
            if need > 0:
                if self._saved_min is None:
                    self._saved_min = self.minimumHeight()
                if self.minimumHeight() != max(self._saved_min, need):
                    self.setMinimumHeight(max(self._saved_min, need))
            elif self._saved_min is not None:
                self.setMinimumHeight(self._saved_min)
                self._saved_min = None
        finally:
            self._guard = False

    # -- content ----------------------------------------------------------------------------------

    def _check_column(self):
        for index, column in enumerate(self.columns):
            if column.kind == "check":
                return index
        return None

    def set_rows(self, rows, checked=(), disabled=()):
        """Replace the rows. Shows the empty state when `rows` is empty and one was set. `checked` / `disabled`: source rows to start ticked / greyed."""
        self._rows = [list(r.get(c.key, "") for c in self.columns) if isinstance(r, dict) else list(r) for r in rows]
        sorting = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self._rows))
        base_font = self.table.font()
        for r, cells in enumerate(self._rows):
            off = r in set(disabled)
            for c, column in enumerate(self.columns):
                value = cells[c] if c < len(cells) else ""
                spec = value if isinstance(value, dict) else None
                if spec is not None:
                    text, key = spec.get("text", ""), spec.get("sort")
                    key = text if key is None else key
                else:
                    text, key = value if isinstance(value, tuple) else (value, value)
                item = _Item("" if column.kind in ("check", "status") else str(text))
                if column.kind == "check":
                    key = 1 if r in set(checked) else 0
                    item.setData(CHECK_ROLE, r in set(checked))
                elif column.kind == "status":
                    kind = (spec or {}).get("icon") or str(text or "pending")
                    item.setData(KIND_ROLE, kind)
                    key = kind
                item.setData(SORT_ROLE, key if column.kind == "number" or isinstance(key, (int, float)) else str(key).lower())
                item.setData(Qt.UserRole, r)
                item.setTextAlignment((Qt.AlignRight if column.align == "right" else Qt.AlignLeft) | Qt.AlignVCenter)
                tip = (spec or {}).get("tip") or (str(text) if len(str(text)) > 28 else "")
                if tip:
                    item.setToolTip(str(tip))
                if spec is not None:
                    if spec.get("tone"):
                        item.setData(TONE_ROLE, spec["tone"])
                    if spec.get("bold"):
                        font = QtGui.QFont(base_font)
                        font.setBold(True)
                        item.setFont(font)
                if off:
                    item.setFlags(item.flags() & ~(Qt.ItemIsEnabled | Qt.ItemIsSelectable))
                self.table.setItem(r, c, item)
        self.table.setSortingEnabled(sorting)
        self._show_rows()
        self.table.horizontalHeader().viewport().update()

    def _show_rows(self):
        has = bool(self._rows)
        title, text, kind, action = self._empty_args
        if has or not (title or text):
            self._stack.setCurrentWidget(self.table)
        else:
            self.empty.set_state(title, text, kind, action)
            self._stack.setCurrentWidget(self.empty)
        self._pin_empty_height()
        self.updateGeometry()

    def clear(self):
        self.set_rows([])

    def row_count(self):
        return len(self._rows)

    def row_data(self, row):
        """The cells of source row `row` (as given to `set_rows`), whatever the sort order."""
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def _visual(self, source_row):
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item is not None and item.data(Qt.UserRole) == source_row:
                return r
        return -1

    def selected_rows(self):
        """Source indexes of the selected rows."""
        seen = []
        for index in self.table.selectionModel().selectedRows():
            item = self.table.item(index.row(), 0)
            if item is not None:
                seen.append(item.data(Qt.UserRole))
        return sorted(seen)

    def select_row(self, source_row):
        visual = self._visual(source_row)
        if visual >= 0:
            self.table.selectRow(visual)

    def sort_by(self, key, descending=False):
        for index, column in enumerate(self.columns):
            if column.key == key:
                self.table.sortItems(index, Qt.DescendingOrder if descending else Qt.AscendingOrder)

    # -- ticks and disabled rows -------------------------------------------------------------------

    def is_checked(self, source_row):
        column = self._check_column()
        visual = self._visual(source_row)
        if column is None or visual < 0:
            return False
        return bool(self.table.item(visual, column).data(CHECK_ROLE))

    def checked_rows(self):
        """Source indexes of the ticked rows."""
        return [r for r in range(len(self._rows)) if self.is_checked(r)]

    def set_checked(self, source_row, on=True):
        column = self._check_column()
        visual = self._visual(source_row)
        if column is None or visual < 0 or not self.is_row_enabled(source_row):
            return
        item = self.table.item(visual, column)
        if bool(item.data(CHECK_ROLE)) != bool(on):
            item.setData(CHECK_ROLE, bool(on))
            item.setData(SORT_ROLE, 1 if on else 0)
            self.table.viewport().update()
            self.table.horizontalHeader().viewport().update()
            self.checkedChanged.emit()

    def check_all(self, on=True):
        """Tick (or untick) every enabled row."""
        for r in range(len(self._rows)):
            self.set_checked(r, on)

    def _toggle_visual_row(self, visual):
        item = self.table.item(visual, 0)
        if item is not None:
            source = item.data(Qt.UserRole)
            self.set_checked(source, not self.is_checked(source))

    def _toggle_selected(self):
        rows = self.selected_rows()
        if rows:
            target = not all(self.is_checked(r) for r in rows)
            for r in rows:
                self.set_checked(r, target)

    def _enabled_rows(self):
        return [r for r in range(len(self._rows)) if self.is_row_enabled(r)]

    def _has_enabled_rows(self):
        return bool(self._enabled_rows())

    def _check_state(self):
        rows = self._enabled_rows()
        ticked = [r for r in rows if self.is_checked(r)]
        if not ticked:
            return False
        return True if len(ticked) == len(rows) else "partial"

    def _toggle_all(self):
        self.check_all(self._check_state() is not True)

    def set_row_enabled(self, source_row, enabled=True):
        visual = self._visual(source_row)
        if visual < 0:
            return
        for c in range(len(self.columns)):
            item = self.table.item(visual, c)
            if item is not None:
                flags = item.flags()
                item.setFlags(flags | (Qt.ItemIsEnabled | Qt.ItemIsSelectable) if enabled else flags & ~(Qt.ItemIsEnabled | Qt.ItemIsSelectable))
        self.table.viewport().update()

    def is_row_enabled(self, source_row):
        visual = self._visual(source_row)
        if visual < 0:
            return False
        item = self.table.item(visual, 0)
        return item is not None and bool(item.flags() & Qt.ItemIsEnabled)

    # -- states -----------------------------------------------------------------------------------

    def set_empty(self, title, text="", action=None):
        """What to show when there are no rows."""
        self._empty_args = (title, text, "empty", action)
        self._show_rows()

    def set_loading(self, text=""):
        self.empty.set_state(text, "", "loading")
        self._stack.setCurrentWidget(self.empty)
        self._pin_empty_height()

    def set_error(self, title, text="", action=None):
        self.empty.set_state(title, text, "error", action)
        self._stack.setCurrentWidget(self.empty)
        self._pin_empty_height()

    def state(self):
        """'table' or 'empty' (the empty / loading / error panel is showing)."""
        return "table" if self._stack.currentWidget() is self.table else "empty"
