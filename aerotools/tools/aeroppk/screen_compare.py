"""The Compare window of the Aero-PPK results: the solutions stored in this window and the current one, one row each, and what two of them
differ by over the photos they share, with one plain sentence. Not modal. The stored solutions are a plain list on the wizard
(`wizard.stored`): they live in memory only, as long as the window."""

from ... import i18n, uikit
from ...qt import QtCore, QtWidgets
from ...uikit import shell, style, tokens
from . import solutions
from .ui_common import tr
from .widgets import KeyValues


def stored_list(wizard):
    """The list of stored solutions of the wizard (made when the wizard does not have it yet)."""
    stored = getattr(wizard, "stored", None)
    if stored is None:
        stored = wizard.stored = []
    return stored


def current_solution(wizard):
    """The results on show now as a solution, named "<name> (current)"."""
    sol = solutions.snapshot(wizard.state)
    sol.name = tr("aeroppk.sol.current", name=sol.name)
    return sol


def differs(wizard, current=None):
    """True when at least one stored solution is not the one on show now."""
    if not wizard.state.results:
        return False
    stored = stored_list(wizard)
    if not stored:
        return False
    current = current or solutions.snapshot(wizard.state)
    return any(not solutions.same(sol, current) for sol in stored)


def precision_text(sol):
    h, v = sol.figures.get("h_median"), sol.figures.get("v_median")
    if h is None or v is None:
        return "-"
    return tr("aeroppk.sol.precision", h=i18n.fmt_number(100.0 * h, 1), v=i18n.fmt_number(100.0 * v, 1))


def sentence(diff):
    """The one plain sentence about a pair: how close they are for 95 % of the photos, or that they share nothing."""
    within = solutions.agree_within(diff)
    if within is None:
        return tr("aeroppk.sol.no_common")
    return tr("aeroppk.sol.agree", x=i18n.fmt_number(100.0 * within, 1), pct=95)


class CompareDialog(QtWidgets.QDialog):
    """`on_change()` is called when a stored solution is removed (the Result screen looks again at its Compare button)."""

    def __init__(self, wizard, on_change=None):
        super(CompareDialog, self).__init__(wizard)
        self.wizard = wizard
        self.on_change = on_change
        self.setWindowTitle(tr("aeroppk.sol.title"))
        self.setModal(False)
        self.items = []                          # stored solutions, then the current one
        self._silent = False
        area = shell.screen_area(wizard)
        self.resize(min(uikit.px(760), area.width() - uikit.px(tokens.SCREEN_MARGIN)),
                    min(uikit.px(520), area.height() - uikit.px(tokens.SCREEN_MARGIN + tokens.XL)))
        outer = uikit.vbox(parent=self)
        self.root = QtWidgets.QWidget()
        self.root.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        style.apply(self.root)
        outer.addWidget(self.root)
        lay = uikit.vbox((tokens.XL,) * 4, tokens.L, parent=self.root)
        lay.addWidget(uikit.label(tr("aeroppk.sol.title"), "lead", wrap=False))
        self.table = uikit.DataTable([uikit.Column("name", tr("aeroppk.sol.c_name"), stretch=True),
                                      uikit.Column("reliable", tr("aeroppk.sol.c_reliable"), align="right"),
                                      uikit.Column("precision", tr("aeroppk.sol.c_precision"), align="right")],       # (as wide as the header and the figures)
                                     sortable=False, empty_title=tr("aeroppk.sol.empty"))
        self.table.setMinimumHeight(uikit.px(tokens.CONTROL_H * 4))
        self.table.selectionChanged.connect(self._compare)
        lay.addWidget(self.table, 1)
        pick = uikit.hbox(spacing=tokens.S)
        pick.addWidget(uikit.label(tr("aeroppk.sol.second"), "small", "muted", wrap=False))
        self.other = uikit.ChoiceBox()
        self.other.activated.connect(self._compare)
        pick.addWidget(self.other, 1)
        self.remove_button = uikit.Button(tr("aeroppk.sol.remove"), "secondary", handler=self._remove)
        self.remove_button.setToolTip(tr("aeroppk.sol.remove_tip"))
        pick.addWidget(self.remove_button)
        lay.addLayout(pick)
        self.facts = KeyValues()
        for key in ("photos", "h", "v", "over"):
            self.facts.add_row(tr("aeroppk.sol.d_" + key), "-", key)
        lay.addWidget(self.facts)
        self.sentence = uikit.label("", "strong", wrap=False)
        lay.addWidget(self.sentence)
        foot = uikit.hbox(spacing=tokens.S)
        foot.addStretch(1)
        foot.addWidget(uikit.Button(tr("ui.close"), "secondary", handler=self.close))
        lay.addLayout(foot)

    def show_all(self):
        """Fill the window from the stored solutions and the current results, and bring it to the front."""
        self.refresh()
        self.show()
        self.raise_()
        self.activateWindow()

    def refresh(self, keep=None):
        stored = list(stored_list(self.wizard))
        self.items = stored + ([current_solution(self.wizard)] if self.wizard.state.results else [])
        self._silent = True
        try:
            self.table.set_rows([[s.name, "%s %%" % i18n.fmt_number(s.reliable_pct(), 0), precision_text(s)] for s in self.items])
            self.other.set_choices([(i, s.name) for i, s in enumerate(self.items)])
            first = keep if keep is not None and 0 <= keep < len(self.items) else 0
            if self.items:
                self.table.select_row(first)
                self.other.setCurrentIndex(len(self.items) - 1 if first != len(self.items) - 1 else max(0, len(self.items) - 2))
        finally:
            self._silent = False
        self._compare()

    def _selected(self):
        rows = self.table.selected_rows()
        return rows[0] if rows else None

    def _compare(self, *_args):
        if self._silent:
            return
        first = self._selected()
        second = self.other.currentData()
        self.remove_button.setEnabled(first is not None and first < len(stored_list(self.wizard)))
        if first is None or second is None or first == second or first >= len(self.items) or second >= len(self.items):
            for key in self.facts.values:
                self.facts.set_text(key, "-")
            self.sentence.setText(tr("aeroppk.sol.same") if len(self.items) > 1 else "")
            return
        diff = solutions.compare(self.items[first], self.items[second])
        cm = lambda v: i18n.fmt_number(100.0 * v, 1)
        self.facts.set_text("photos", "%d / %d" % (diff["n"], diff["common"]))
        for key in ("h", "v"):
            if diff["n"]:
                self.facts.set_text(key, tr("aeroppk.sol.d_line", median=cm(diff[key + "_median"]), p95=cm(diff[key + "_p95"]),
                                            max=cm(diff[key + "_max"])))
            else:
                self.facts.set_text(key, "-")
        self.facts.set_text("over", str(diff["over"]) if diff["n"] else "-")
        self.sentence.setText(sentence(diff))

    def _remove(self):
        first = self._selected()
        stored = stored_list(self.wizard)
        if first is None or first >= len(stored):
            return
        del stored[first]
        self.refresh(first - 1 if first else 0)
        if self.on_change:
            self.on_change()
