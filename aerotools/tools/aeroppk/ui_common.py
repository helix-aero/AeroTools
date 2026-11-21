"""Shared bits of the Aero-PPK screens: the screen base class and a few small helpers. Qt only. Everything visual comes from the UI kit."""

from ... import i18n, uikit
from ...qt import QtCore, QtWidgets
from ...uikit import tokens
from ...uikit.controls import CappedStack


LABEL_W = 104                                   # label column of a form in a narrow card (design px): narrower than a full-width form


def narrow_labels(form, width=LABEL_W):
    """A form in a narrow card: a shorter label column (the kit's is made for a full-width form)."""
    for row in form.rows:
        row.label.setFixedWidth(uikit.px(width))
        row.error.setContentsMargins(uikit.px(width + tokens.S), 0, 0, 0)


def tr(key, **fields):
    return i18n.t(key, **fields)


def plural(key, count, **fields):
    """Text of `key.one` when `count` is 1 and of `key.other` otherwise (`n` is the count)."""
    return i18n.tn(key + ".one", key + ".other", count, **fields)


def clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())


def glyph_kind(kind):
    """The kit name of a status mark (this tool says stop and note)."""
    return {"stop": "error", "note": "info"}.get(kind, kind)


def glyph(kind="pending"):
    return uikit.Glyph(glyph_kind(kind))


def card(spacing=tokens.M):
    """A framed area (frame, layout)."""
    frame = QtWidgets.QFrame()
    frame.setProperty("ui", "card")
    frame.setAttribute(QtCore.Qt.WA_StyledBackground, True)
    layout = uikit.vbox((tokens.M,) * 4, spacing, parent=frame)
    return frame, layout


def value(text="", tone=None, mode="right", kind="body"):
    """The value of a form row: one line, centred on the row like its label."""
    widget = uikit.ElidedLabel(text, kind, tone, mode=mode, fit=True)
    widget.setFixedHeight(uikit.px(tokens.CONTROL_H))
    widget.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
    return widget


class OneAtATimeStack(CappedStack):
    """The kit's stack that counts only the page on show, with one more guarantee: the page that goes is hidden BEFORE the new one is shown.
    The kit shows the pages in the order they were added, so going back from a later page showed the earlier one first and the later one still
    counted for a moment: the window asked for the height of both pages and grew, and stayed that tall."""

    def setCurrentIndex(self, index):
        if 0 <= index < len(self._pages):
            for number, page in enumerate(self._pages):
                if number != index:
                    page.setVisible(False)
        super(OneAtATimeStack, self).setCurrentIndex(index)


def one_line(table):
    """A `DataTable` (or an `EmptyState`) whose empty-state texts stay on one line: a wrapped text makes the whole window a
    height-for-width layout, which the window sizes with the wrong rule (it grows past the screen)."""
    state = getattr(table, "empty", table)
    for text in (state.title, state.text):
        text.setWordWrap(False)
    return table


def heading(text, info=""):
    """A small heading row with an optional "i" (a layout: add buttons at the right with `addWidget`)."""
    row = uikit.hbox(spacing=tokens.S)
    row.addWidget(uikit.label(text, "strong", wrap=False))
    if info:
        row.addWidget(uikit.InfoButton(info))
    row.addStretch(1)
    return row


def fmt_time(moment, with_date=True):
    if moment is None:
        return "-"
    return moment.strftime("%Y-%m-%d %H:%M:%S" if with_date else "%H:%M:%S")


def fmt_duration(seconds):
    if seconds is None:
        return "-"
    seconds = int(round(seconds))
    return "%d:%02d" % (seconds // 60, seconds % 60) if seconds < 3600 else "%d:%02d:%02d" % (seconds // 3600, seconds % 3600 // 60, seconds % 60)


class Screen(uikit.StepScreen):
    """One screen of the wizard (a kit `StepScreen`). The window calls `enter()` when it appears and `leave()` when it goes away,
    `can_leave(forward)` may refuse, and the methods below say what the footer buttons do."""

    step = 0
    name_key = ""

    def __init__(self, wizard):
        super(Screen, self).__init__(wizard)
        self.wizard = wizard
        self.state = wizard.state
        self.content.setSpacing(uikit.px(tokens.L))

    def next_enabled(self):
        return self.state.can_next(self.step)

    def next_text(self):
        return tr("ui.next")

    def show_next(self):
        return True

    def show_back(self):
        return self.step > 0

    def busy(self):
        return False

    def next_step(self):
        return self.step + 1

    def prev_step(self):
        return self.step - 1

    def hint(self):
        """A short line for the footer (why Next is grey), or ''."""
        return ""
