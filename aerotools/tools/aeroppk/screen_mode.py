"""Step 3 of the Aero-PPK wizard: where the base comes from. Three large cards (two in the public build), one is picked. The Base step
then shows the page of that mode."""

from ... import uikit
from ...qt import QtCore, QtGui, QtWidgets
from ...uikit import metrics, tokens
from . import state as S
from .ui_common import Screen, tr

# (mode, title, sentence, who it is for). The keys are spelled out so that the public build drops the texts of the private card with it.
CARDS = [(S.MODE_SINGLE, "aeroppk.mode.single", "aeroppk.mode.single_text", "aeroppk.mode.single_for"),
         (S.MODE_OWN, "aeroppk.mode.own", "aeroppk.mode.own_text", "aeroppk.mode.own_for")]


class ModeCard(uikit.ChoiceCard):
    """A choice card with a small tag at its top right (inside the card, so that it takes no room above it and the cards stay level)."""

    def __init__(self, title, text, value, tag=""):
        super(ModeCard, self).__init__(title, text, value=value)
        self.tag = tag
        if tag:
            self.setAccessibleDescription(tag)

    def paintEvent(self, event):
        super(ModeCard, self).paintEvent(event)
        if not self.tag:
            return
        p = QtGui.QPainter(self)
        p.setFont(metrics.font("caption", strong=True))
        p.setPen(QtGui.QColor(tokens.SUCCESS))
        line = QtCore.QRectF(0, uikit.px(tokens.S + 2), self.width() - uikit.px(tokens.M), QtGui.QFontMetrics(metrics.font("body", strong=True)).height())
        p.drawText(line, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, self.tag)
        p.end()


class ModeScreen(Screen):
    step = S.MODE
    name_key = "aeroppk.step_mode"

    def __init__(self, wizard):
        super(ModeScreen, self).__init__(wizard)
        self.cards = {}
        self.group = uikit.ChoiceGroup(self)
        self.content.addWidget(uikit.label(tr("aeroppk.mode.question"), "strong", wrap=False))
        row = uikit.hbox(spacing=tokens.L)
        columns = [self._column(mode, title, text, who) for mode, title, text, who in CARDS]
        height = max(card.heightForWidth(uikit.px(240)) for card in self.cards.values()) + uikit.px(tokens.S)   # room for three lines: the same for all
        for card in self.cards.values():
            card.setFixedHeight(height)
            card.setSizePolicy(card.sizePolicy().horizontalPolicy(), QtWidgets.QSizePolicy.Fixed)
        for column in columns:
            row.addLayout(column, 1)
        self.content.addLayout(row)
        self.content.addStretch(1)
        self.group.changed.connect(self._picked)
        self.group.set_value(self.state.mode)

    def _column(self, mode, title, text, who):
        """One card and, under it, who it is for. The recommended card carries its tag inside."""
        column = uikit.vbox(spacing=tokens.S)
        card = ModeCard(tr(title), tr(text), mode, tr("aeroppk.mode.recommended") if mode == S.MODE_SINGLE else "")
        self.group.add(card)
        self.cards[mode] = card
        column.addWidget(card)
        column.addWidget(uikit.label(tr(who), "small", "muted", wrap=False))
        column.addStretch(1)
        return column

    def _picked(self, mode):
        self.state.set_mode(mode)
        self.wizard.refresh_buttons()

    def enter(self):
        self.group.set_value(self.state.mode)
        self.wizard.refresh_buttons()

    def select(self, mode):
        """Pick a mode as a click would (the tests call this)."""
        card = self.cards.get(mode)
        if card is not None:
            card.click()

    def next_enabled(self):
        return True
