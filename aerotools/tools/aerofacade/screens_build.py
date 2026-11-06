"""Aero-Facade UI: screen 5 (build the surface under each wall). Screens 6 and 7 (orthophotos, export) are in `screens_export.py`.

The long steps run one facade at a time, so that each wall has its own row with its own result: `batch_worker` is the function that
runs on the worker thread. It only writes plain dictionaries (`items`, `extras`); the screens read them with the wizard's timer.
"""

from ... import theme, ui
from ...qt import QtWidgets
from . import state as S
from .backend import Cancelled, Progress
from .common import tr
from .screens_base import Screen, heading, right_panel
from .tasks import plain_error
from .widgets import STATE_CANCELLED, STATE_DONE, STATE_FAILED, STATE_RUNNING, STATE_WAITING, BatchProgress


def batch_worker(facades, call, items, extras=None, after=None):
    """Function for `Task`: run `call(facade, progress)` -> (ok, message) on each facade in turn.

    `items[letter] = (state, text)` is updated as it goes. Cancelling marks the rest "cancelled" and stops; an exception on one
    facade is that facade's failure, the others still run. `after(facade, extras)` runs after a success (thumbnails)."""
    facades = list(facades)

    def work(progress, cancelled):
        count = max(1, len(facades))
        results = {}
        for index, facade in enumerate(facades):
            if cancelled():
                for rest in facades[index:]:
                    items[rest.letter] = (STATE_CANCELLED, "")
                break
            letter = facade.letter
            items[letter] = (STATE_RUNNING, "")
            sub = Progress(progress, cancelled, index / float(count), (index + 1) / float(count))
            try:
                ok, message = call(facade, sub)
            except Cancelled:
                for rest in facades[index:]:
                    items[rest.letter] = (STATE_CANCELLED, "")
                break
            except Exception as exc:                          # one wall failing must not stop the others
                ok, message = False, plain_error(exc)
            if ok and after is not None:
                try:
                    after(facade, extras)
                except Exception:
                    pass                                      # a missing thumbnail is not a failure
            results[letter] = (bool(ok), message or "")
            items[letter] = (STATE_DONE if ok else STATE_FAILED, "" if ok else (message or tr("facade.ui.failed_default")))
            progress((index + 1) / float(count), "")
        return results

    return work


def apply_items(widget, items, shown):
    """Copy the worker's `items` into the rows of a BatchProgress; `shown` remembers what is already displayed."""
    for letter, entry in list(items.items()):
        if shown.get(letter) != entry:
            shown[letter] = entry
            widget.set_state(letter, entry[0], entry[1] if entry[0] == STATE_FAILED else "")


class BuildScreen(Screen):
    """Screen 5: the surfaces are built one wall after the other, with a row per wall, a banner and a Cancel button."""

    step = S.BUILD
    name_key = "facade.ui.step_build"

    def __init__(self, wizard):
        super(BuildScreen, self).__init__(wizard)
        self._running = False
        self._shown = {}
        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(theme.PAD_L)
        area, col = right_panel(width_hint=340)
        col.addWidget(heading(5, "facade.ui.title_build"))
        self.intro = ui.label("")
        col.addWidget(self.intro)
        card = QtWidgets.QFrame()
        card.setObjectName("facadeNote")
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.addWidget(ui.label(tr("facade.ui.build_why")))
        col.addWidget(card)
        self.build_button = ui.big_button("facade.ui.build", self.start_build, "aeroGo")
        col.addWidget(self.build_button)
        self.note = ui.label("", "aeroMeta")
        col.addWidget(self.note)
        col.addStretch(1)
        outer.addWidget(area, 2)
        self.batch = BatchProgress(show_retry=False)          # the Build button on the left does the same, once
        outer.addWidget(self.batch, 3)
        self.batch.cancel_requested.connect(self.cancel_job)
        self.batch.retry_requested.connect(self._retry)

    # -- wizard hooks -----------------------------------------------------------------------------

    def enter(self):
        st = self.state
        facades = st.active()
        self.batch.set_items(facades)
        self._shown = {}
        for facade in facades:
            ok, message = st.dem_state.get(facade.letter, (None, ""))
            if ok:
                self.batch.set_state(facade.letter, STATE_DONE, tr("facade.ui.already_built"))
            elif ok is False:
                self.batch.set_state(facade.letter, STATE_FAILED, message or tr("facade.ui.failed_default"))
        self.intro.setText(tr("facade.ui.build_intro", n=len(facades)))
        self._refresh()

    def busy(self):
        return self._running

    def next_text(self):
        return tr("facade.ui.next_ortho")

    def _todo(self, letters=None):
        st = self.state
        return [f for f in st.active() if (letters is None or f.letter in letters) and not st.dem_state.get(f.letter, (False, ""))[0]]

    def _refresh(self):
        todo = self._todo()
        done = len(self.state.dem_ok_letters())
        self.build_button.setVisible(not self._running and bool(todo))
        failed_before = any(self.state.dem_state.get(f.letter, (None, ""))[0] is False for f in self.state.active())
        self.build_button.setText(tr("facade.ui.build_again") if failed_before else tr("facade.ui.build_n", n=len(todo)))
        if self._running:
            self.note.setText(tr("facade.ui.build_running"))
        elif todo:
            self.note.setText(tr("facade.ui.build_left", n=len(todo)) if done else "")
        elif done:
            self.note.setText(tr("facade.ui.build_all_done"))
        else:
            self.note.setText("")
        self.note.setVisible(bool(self.note.text()))
        self.wizard.refresh_buttons()

    # -- the build --------------------------------------------------------------------------------

    def start_build(self, letters=None):
        if self._running:
            return False
        todo = self._todo(letters)
        if not todo:
            return False
        st = self.state
        backend = self.wizard.backend
        margins, method = st.margins_map(), st.method
        items, shown = {}, self._shown
        for facade in todo:
            self.batch.set_state(facade.letter, STATE_WAITING)
            shown.pop(facade.letter, None)

        def call(facade, sub):
            result = backend.build_dems([facade], margins, method, sub, sub.cancelled)
            entry = result.get(facade.letter)
            if entry is None:                                 # a backend leaves a wall out only when the job was cancelled: our
                raise Cancelled()                             # Cancel, or the Cancel button of Metashape's own progress window
            return bool(entry[0]), entry[1]

        work = batch_worker(todo, call, items)
        self._running = True
        self.batch.set_running(True)
        self.batch.set_progress(0.0, tr("facade.ui.build_start"))
        started = self.wizard.run_job(work, self._on_progress, self._on_done, "build", items=items)
        if not started:
            self._running = False
            self.batch.set_running(False)
        self._refresh()
        return started

    def _retry(self, letters):
        self.start_build(set(letters))

    def _on_progress(self, task):
        apply_items(self.batch, task.items, self._shown)
        self.batch.set_progress(task.fraction, task.text)

    def _on_done(self, task):
        self._running = False
        self.batch.set_running(False)
        apply_items(self.batch, task.items, self._shown)
        st = self.state
        if task.error is not None:
            self.note.setText(tr("facade.ui.job_failed", reason=plain_error(task.error)))
            self.note.setVisible(True)
        else:
            for letter, entry in (task.result or {}).items():
                st.dem_state[letter] = entry
        self.batch.set_progress(1.0 if not task.was_cancelled and task.error is None else task.fraction,
                                tr("facade.ui.state_cancelled") if task.was_cancelled else "")
        self._refresh()
        if task.was_cancelled and task.error is None:
            self.note.setText(tr("facade.ui.build_cancelled"))
            self.note.setVisible(True)
        everything = bool(st.active()) and len(st.dem_ok_letters()) == len(st.active())
        if everything and not task.was_cancelled and task.error is None:
            choice = self.wizard.ask(tr("facade.ui.build_done_title"), tr("facade.ui.build_done_text", n=len(st.active())),
                                     (("exit", "facade.ui.exit"), ("continue", "facade.ui.continue_ortho")))
            if choice == "continue":
                self.wizard.go(S.PICTURES)
            elif choice == "exit":
                self.wizard.close()
