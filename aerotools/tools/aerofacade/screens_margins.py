"""Aero-Facade UI: screen 3 (the margins around each wall) and screen 4 (the method that builds the surface under each wall)."""

from ... import i18n, theme, ui
from ...qt import QtWidgets
from . import state as S
from .common import metres, tr
from . import methods
from .model import Method
from ..aeroexports import controls
from .screens_base import Screen, heading, right_panel
from .widgets import FacadeList, MarginEditor, MethodOption, MethodPicker

METHODS = tuple(Method)                                  # quality order, best first


class MarginsScreen(Screen):
    """Screen 3: left, the walls (to pick which ones the numbers apply to); right, the drawing and the numbers."""

    step = S.MARGINS
    name_key = "facade.ui.step_margins"

    def __init__(self, wizard):
        super(MarginsScreen, self).__init__(wizard)
        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(theme.PAD_L)
        left_area, left = right_panel(width_hint=300)
        left.addWidget(heading(3, "facade.ui.title_margins"))
        left.addWidget(ui.label(tr("facade.ui.margins_intro")))
        self.list = FacadeList(allow_remove=False)
        self.list.show_count(False)
        self.list.setMinimumHeight(220)
        left.addWidget(self.list, 1)
        outer.addWidget(left_area, 1)
        self.editor = MarginEditor()
        right_area, right = right_panel(width_hint=470)
        right.addWidget(self.editor)
        outer.addWidget(right_area, 3)
        self.list.selection_changed.connect(self._selection)
        self.editor.margin_edited.connect(self._edited)
        self.editor.link_toggled.connect(self._linked)
        self.editor.pixel_edited.connect(self._pixel)
        self.editor.apply_all_toggled.connect(self._apply_all)

    def enter(self):
        active = self.state.active()
        self.list.set_facades(active)
        self.editor.apply_all.blockSignals(True)
        self.editor.apply_all.setChecked(self.state.apply_all)
        self.editor.apply_all.blockSignals(False)
        self.editor.set_pixel_cm(self.state.pixel_cm)
        if active:
            self.list.select([active[0].letter])
        self._show_reference()
        self._sync_state()

    def leave(self, forward):
        S.save_prefs(self.state)
        return True

    # -- who the numbers apply to -----------------------------------------------------------------

    def targets(self):
        if self.state.apply_all:
            return self.state.letters()
        return [letter for letter in self.list.selected_letters() if self.state.facade(letter) is not None]

    def _reference(self):
        """The facade whose numbers the editor shows: the first picked one (any one, when the numbers apply to all)."""
        targets = self.targets() or self.state.letters()
        return self.state.facade(targets[0]) if targets else None

    def _show_reference(self):
        facade = self._reference()
        if facade is None:
            return
        self.editor.set_margins(self.state.margins_of(facade.letter))
        self.editor.set_wall_size(facade.length, facade.height)

    def _sync_state(self):
        on = self.state.apply_all
        self.list.list.setEnabled(not on)
        if on:
            self.list.select(self.state.letters())
        picked = self.targets()
        self.editor.setEnabled(bool(picked) or on)
        if on:
            self.editor.set_target_note(tr("facade.ui.margins_all_note", n=len(self.state.letters())))
        elif picked:
            self.editor.set_target_note(tr("facade.ui.margins_some_note", n=len(picked)))
        else:
            self.editor.set_target_note(tr("facade.ui.margins_pick_note"))
        self.wizard.refresh_buttons()

    def _selection(self, _letters):
        self._show_reference()
        self._sync_state()

    # -- edits ------------------------------------------------------------------------------------

    def _edited(self, name, value):
        self.state.apply_margin(self.targets(), name, value)
        self._show_reference()

    def _linked(self, pair, linked):
        self.state.apply_link(self.targets(), pair, linked)
        self._show_reference()

    def _pixel(self, cm):
        self.state.pixel_cm = float(cm)
        self.state.invalidate_builds()

    def _apply_all(self, on):
        self.state.apply_all = bool(on)
        if on:
            reference = self.editor.margins()
            self.state.set_all_margins(reference)
        self._sync_state()
        self._show_reference()


class MethodScreen(Screen):
    """Screen 4: how the surface under each wall is built, best first, the unavailable ones greyed with the reason."""

    step = S.METHOD
    name_key = "facade.ui.step_method"

    def __init__(self, wizard):
        super(MethodScreen, self).__init__(wizard)
        page = QtWidgets.QWidget()
        col = QtWidgets.QVBoxLayout(page)
        col.setContentsMargins(0, 0, theme.PAD, 0)
        col.setSpacing(theme.PAD + 4)
        col.addWidget(heading(4, "facade.ui.title_method"))
        col.addWidget(ui.label(tr("facade.ui.method_intro")))
        self.picker = MethodPicker()
        col.addWidget(self.picker)
        self.none_note = ui.label(tr("facade.ui.method_none"), "aeroWarn")
        self.none_note.setVisible(False)
        col.addWidget(self.none_note)
        self.long_note = ui.label("", "aeroWarn")
        self.long_note.setVisible(False)
        col.addWidget(self.long_note)
        self.summary = ui.label("", "aeroMeta")
        col.addWidget(self.summary)
        col.addStretch(1)
        self._long = {}
        area = controls.scroll_page(page)
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(area)
        self.picker.method_changed.connect(self._changed)

    def enter(self):
        try:
            self.state.availability = self.wizard.read_availability()
        except Exception:
            pass
        availability = self.state.availability
        active = self.state.active()
        self._existing = self._existing_surfaces()
        rows = methods.describe(availability, facades=active, margins=self.state.margins_map(), existing=self._existing)
        options = []
        self._long = {}
        for row in rows:
            reason = row["reason"]
            missing = methods.method_keys(row["key"])["reason"]
            if i18n.has(missing):
                reason = i18n.t(missing)
            options.append(MethodOption(row["key"], row["available"], reason, row["title"], row["explain"], row["quality"],
                                        row["time"], row["warning"], row["default"]))
            self._long[row["key"]] = row["long"]
        preferred = self.state.method if availability.ok(self.state.method or Method.TIE_POINTS) and self.state.method else None
        self.picker.set_options(options, preferred or next((o.key for o in options if o.default), None))
        self.none_note.setVisible(not availability.any_surface())
        self._changed(self.picker.selected())

    def _existing_surfaces(self):
        """{method key: letters whose surface is already in the chunk}: those cost no time."""
        found = {}
        for method in Method:
            try:
                have = self.wizard.backend.existing_dems(self.state.active(), self.state.margins_map(), method)
            except Exception:
                have = {}
            found[method.value] = set(letter for letter, yes in (have or {}).items() if yes)
        return found

    def _changed(self, key):
        self.state.method = key
        self.long_note.setText("")
        self.long_note.setVisible(False)
        count = len(self.state.active())
        if key:
            self.summary.setText(tr("facade.ui.method_summary", n=count, pixel=metres(self.state.pixel_cm, 1)))
            if self._long.get(key):
                low, high = self._range(key)
                self.long_note.setText(i18n.t("facade.method.long_notice", low=low, high=high))
                self.long_note.setVisible(True)
        else:
            self.summary.setText("")
        self.wizard.refresh_buttons()

    def _range(self, key):
        try:
            hint = methods.time_hint(Method.from_key(key), self.state.active(), self.state.margins_map(),
                                     getattr(self, "_existing", ()))
            return hint.params.get("low", 0), hint.params.get("high", 0)
        except Exception:
            return 0, 0

    def leave(self, forward):
        if forward:
            self.state.method = self.picker.selected()
            S.save_prefs(self.state)
        return True

    def next_step(self):
        """Screen 5 is skipped when every surface is already in the chunk."""
        try:
            existing = self.wizard.backend.existing_dems(self.state.active(), self.state.margins_map(), self.state.method)
        except Exception:
            existing = {}
        letters = self.state.letters()
        self.state.dem_state = dict((letter, (True, "")) for letter in letters if existing.get(letter))
        self.state.skipped_build = bool(letters) and all(existing.get(letter) for letter in letters)
        return S.PICTURES if self.state.skipped_build else S.BUILD
