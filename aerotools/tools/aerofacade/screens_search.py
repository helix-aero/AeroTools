"""Aero-Facade UI: screen 1 (draw the search area, run the detection) and screen 2 (check the detected facades)."""

from ... import theme, ui
from ...qt import QtCore, QtWidgets
from ..aeroexports import controls
from . import state as S
from .common import POLY_KEYS, metres, tr
from .screens_base import Screen, canvas_frame, heading, right_panel, two_columns
from .tasks import plain_error
from .widgets import FacadeList, polygon_area


class SearchScreen(Screen):
    """Screen 1: the picture of the orthomosaic, a polygon tool, and the Run button that starts the detection."""

    step = S.SEARCH
    name_key = "facade.ui.step_search"

    def __init__(self, wizard):
        super(SearchScreen, self).__init__(wizard)
        self._running = False
        self._error = ""
        self.frame, self.frame_layout = canvas_frame()
        area, col = right_panel()
        col.addWidget(heading(1, "facade.ui.title_search"))
        col.addWidget(ui.label(tr("facade.ui.search_intro")))
        card = QtWidgets.QFrame()
        card.setObjectName("facadeNote")
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(theme.PAD)
        for text in (tr("facade.ui.search_step1"), tr("facade.ui.search_step2"), tr("facade.ui.search_step3")):
            card_layout.addWidget(ui.label(text))
        col.addWidget(card)
        self.status = ui.label("", "aeroMeta")
        col.addWidget(self.status)
        self.draw_again = ui.big_button("facade.ui.draw_again", self._draw_again, "aeroBtn", width=150)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.draw_again)
        row.addStretch(1)
        col.addLayout(row)
        self.run_button = ui.big_button("facade.ui.run", self.start_detection, "aeroGo")
        col.addWidget(self.run_button)
        self.run_note = ui.label("", "aeroMeta")
        col.addWidget(self.run_note)
        self.box = QtWidgets.QFrame()
        self.box.setObjectName("facadeCard")
        box = QtWidgets.QVBoxLayout(self.box)
        box.setContentsMargins(14, 12, 14, 12)
        box.setSpacing(theme.PAD)
        self.progress_text = controls.ElidedLabel()
        box.addWidget(self.progress_text)
        self.bar = QtWidgets.QProgressBar()
        self.bar.setObjectName("facadeBar")
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        box.addWidget(self.bar)
        self.cancel_button = ui.big_button("facade.ui.cancel_job", self.cancel_job, "aeroBtn", width=130)
        box.addWidget(self.cancel_button, 0, QtCore.Qt.AlignLeft)
        self.box.setVisible(False)
        col.addWidget(self.box)
        self.error = ui.label("", "aeroWarn")
        self.error.setVisible(False)
        col.addWidget(self.error)
        col.addStretch(1)
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(two_columns(self.frame, area, 5, 3))
        canvas = wizard.canvas
        canvas.polygon_changed.connect(self._on_polygon)
        canvas.corners_changed.connect(lambda _n: self.refresh())
        canvas.drawing_changed.connect(lambda _d: self.refresh())
        canvas.message.connect(self._on_message)

    # -- wizard hooks -----------------------------------------------------------------------------

    def enter(self):
        canvas = self.wizard.canvas
        self.frame_layout.addWidget(canvas)
        canvas.show()
        canvas.set_facades([])
        canvas.set_selected([])
        canvas.set_draw_enabled(canvas.has_picture() and not self._running)
        if self.state.polygon and not canvas.is_closed():
            canvas.set_polygon(self.state.polygon)
        self.refresh()

    def busy(self):
        return self._running

    def picture_ready(self):
        """The wizard has put the orthomosaic in the canvas: drawing may start."""
        self.wizard.canvas.set_draw_enabled(not self._running)
        self.refresh()

    # -- the outline ------------------------------------------------------------------------------

    def _on_polygon(self, points):
        canvas = self.wizard.canvas
        self.state.set_polygon(points, canvas.polygon_problem() if points else "few")
        self._error = ""
        self.refresh()
        self.wizard.refresh_buttons()

    def _on_message(self, text):
        self._error = text
        self.refresh()

    def _draw_again(self):
        if self._running:
            return
        self.wizard.canvas.clear_polygon()
        self.state.set_polygon(None)
        self._error = ""
        self.wizard.canvas.set_draw_enabled(self.wizard.canvas.has_picture())
        self.refresh()
        self.wizard.refresh_buttons()
        self.wizard.canvas.setFocus()

    def refresh(self):
        canvas = self.wizard.canvas
        has_picture = canvas.has_picture()
        st = self.state
        if self._running:
            text = tr("facade.ui.outline_running")
        elif not has_picture:
            text = ""
        elif st.polygon and not st.polygon_problem:
            text = tr("facade.ui.outline_ok", area=metres(polygon_area(st.polygon), 0))
        elif st.polygon:
            text = tr(POLY_KEYS[st.polygon_problem])
        elif canvas.corner_count():
            text = tr("facade.ui.outline_drawing", n=canvas.corner_count())
        else:
            text = tr("facade.ui.outline_none")
        self.status.setText(text)
        self.run_button.setEnabled(has_picture and st.can_run() and not self._running)
        note = ""
        if has_picture and not self._running and not st.can_run():
            note = tr("facade.ui.run_disabled")
        self.run_note.setText(note)
        self.run_note.setVisible(bool(note))
        self.draw_again.setEnabled(has_picture and not self._running and bool(st.polygon or canvas.corner_count()))
        if self._error and not self._running:
            self.error.setText(self._error)
        self.error.setVisible(bool(self._error) and not self._running)
        if has_picture:
            if self._running:
                canvas.set_hint("")
            elif st.polygon and not st.polygon_problem:
                canvas.set_hint(tr("facade.ui.hint_ready"))
            elif st.polygon:
                canvas.set_hint(tr("facade.ui.hint_bad"))
            elif canvas.corner_count() >= 3:
                canvas.set_hint(tr("facade.ui.hint_close"))
            elif canvas.corner_count():
                canvas.set_hint(tr("facade.ui.hint_more"))
            else:
                canvas.set_hint(tr("facade.ui.hint_draw"))
        self.box.setVisible(self._running)
        self.run_button.setVisible(not self._running)
        self.draw_again.setVisible(not self._running)

    # -- the detection ----------------------------------------------------------------------------

    def start_detection(self):
        if self._running or not self.state.can_run():
            return False
        polygon = list(self.state.polygon)
        backend = self.wizard.backend
        self._running = True
        self._error = ""
        self.bar.setValue(0)
        self.progress_text.setFullText(tr("facade.ui.detect_start"))
        self.wizard.canvas.set_draw_enabled(False)
        self.refresh()
        started = self.wizard.run_job(lambda progress, cancelled: backend.detect(polygon, progress, cancelled),
                                      self._on_progress, lambda task: self._on_done(task, polygon), "detect")
        if not started:
            self._running = False
            self.refresh()
        self.wizard.refresh_buttons()
        return started

    def _on_progress(self, task):
        self.bar.setValue(int(task.fraction * 1000))
        if task.text:
            self.progress_text.setFullText(task.text)

    def _on_done(self, task, polygon):
        self._running = False
        canvas = self.wizard.canvas
        canvas.set_draw_enabled(True)
        if task.error is not None:
            self._error = tr("facade.ui.detect_failed", reason=plain_error(task.error))
        elif task.was_cancelled:
            self._error = tr("facade.ui.detect_cancelled")
        elif not task.result:
            self._error = tr("facade.ui.detect_none")
        else:
            self._error = ""
            self.state.set_facades(task.result, polygon_key=tuple(polygon))
            self.refresh()
            self.wizard.refresh_buttons()
            self.wizard.go(S.FACADES)
            return
        self.refresh()
        self.wizard.refresh_buttons()


class FacadesScreen(Screen):
    """Screen 2: the detected facades on the picture and in a list; remove the ones that are not wanted."""

    step = S.FACADES
    name_key = "facade.ui.step_facades"

    def __init__(self, wizard):
        super(FacadesScreen, self).__init__(wizard)
        self._version = None
        self.frame, self.frame_layout = canvas_frame()
        area, col = right_panel()
        col.addWidget(heading(2, "facade.ui.title_facades"))
        col.addWidget(ui.label(tr("facade.ui.facades_intro")))
        self.list = FacadeList()
        self.list.setMinimumHeight(260)
        col.addWidget(self.list, 1)
        self.empty_note = ui.label(tr("facade.ui.facades_none_left"), "aeroWarn")
        self.empty_note.setVisible(False)
        col.addWidget(self.empty_note)
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(two_columns(self.frame, area, 5, 3))
        self.list.selection_changed.connect(self._from_list)
        self.list.activated.connect(lambda letter: self.wizard.canvas.zoom_to_facades([letter]))
        self.list.changed.connect(self._changed)
        canvas = wizard.canvas
        canvas.facade_clicked.connect(self._from_canvas)
        canvas.command.connect(self._command)

    def enter(self):
        canvas = self.wizard.canvas
        self.frame_layout.addWidget(canvas)
        canvas.show()
        canvas.set_draw_enabled(False)
        same = self._version == self.state.version
        self._version = self.state.version
        self.list.set_facades(self.state.facades, keep_log=same)
        canvas.set_facades(self.list.kept())
        canvas.set_selected([])
        canvas.set_hint(tr("facade.ui.hint_facades"))
        canvas.fit_all()
        self._after_change()

    def _command(self, name):
        if self.wizard.current_step() != S.FACADES:
            return
        if name == "delete":
            self.list.remove_selected()
        elif name == "undo":
            self.list.undo()

    def _from_list(self, letters):
        canvas = self.wizard.canvas
        canvas.set_selected(letters)
        if letters:
            canvas.zoom_to_facades(letters)

    def _from_canvas(self, letter, ctrl, shift):
        if self.wizard.current_step() != S.FACADES:
            return
        current = set(self.list.selected_letters())
        if not letter:
            new = current if (ctrl or shift) else set()
        elif ctrl:
            new = current ^ set([letter])
        elif shift:
            new = current | set([letter])
        else:
            new = set([letter])
        self.list.select(new)
        self.wizard.canvas.set_selected(new)
        for row in range(self.list.list.count()):
            item = self.list.list.item(row)
            if item.isSelected():
                self.list.list.scrollToItem(item)
                break

    def _changed(self):
        self.state.invalidate_builds()
        canvas = self.wizard.canvas
        canvas.set_facades(self.list.kept())
        canvas.set_selected(self.list.selected_letters())
        self._after_change()

    def _after_change(self):
        none_left = not self.state.active()
        self.empty_note.setVisible(none_left and bool(self.state.facades))
        self.wizard.refresh_buttons()

    def leave(self, forward):
        self.wizard.canvas.set_selected([])
        return True
