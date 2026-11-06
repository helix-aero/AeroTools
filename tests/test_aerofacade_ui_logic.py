"""Aero-Facade UI: the parts of the wizard that need no display (geometry of the picture, outline check, removal log, rules that
enable the buttons, margins with their link badges, the worker thread, the texts). The windows themselves are tested by
tests_qt/run_qt_aerofacade.py."""

import ast
import os
import re
import time

import pytest

from aerotools import i18n
from aerotools.tools.aerofacade import state as S
from aerotools.tools.aerofacade.backend import Cancelled
from aerotools.tools.aerofacade.model import Facade, Method
from aerotools.tools.aerofacade.tasks import Task, plain_error
from aerotools.tools.aerofacade import widgets as W

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = os.path.join(ROOT, "aerotools", "tools", "aerofacade")


def make_facades(n=3):
    return [Facade.from_frame(chr(65 + i), i, (10.0 * i, 0.0, 0.0), (1.0, 0.0), 12.0, 6.0) for i in range(n)]


# -- picture frame and outline --------------------------------------------------------------------

def test_picture_frame_round_trip():
    frame = W.PictureFrame(200, 100, (-10.0, -5.0, 30.0, 15.0))
    assert frame.to_world(0, 0) == (-10.0, 15.0)
    assert frame.to_world(200, 100) == (30.0, -5.0)
    x, y = frame.to_world(50, 25)
    assert frame.to_pixel(x, y) == pytest.approx((50, 25))
    assert frame.metres_per_pixel == pytest.approx(0.2)


@pytest.mark.parametrize("points,expected", [
    ([(0, 0), (1, 0)], "few"),
    ([(0, 0), (1, 1), (2, 2)], "flat"),
    ([(0, 0), (4, 0), (4, 4), (0, 4)], ""),
    ([(0, 0), (4, 4), (4, 0), (0, 4)], "crossing"),
    ([(0, 0), (4, 0), (4, 2), (2, 2), (2, 4), (0, 4)], ""),          # an L shape is fine
])
def test_polygon_problem(points, expected):
    assert W.polygon_problem(points) == expected


def test_segment_distance():
    assert W.segment_distance((5, 3), (0, 0), (10, 0)) == pytest.approx(3.0)
    assert W.segment_distance((-4, 3), (0, 0), (10, 0)) == pytest.approx(5.0)
    assert W.segment_distance((1, 1), (2, 2), (2, 2)) == pytest.approx(2 ** 0.5)


def test_readable_on_picks_black_or_white():
    assert W.readable_on("#ffe119") == "#000000"
    assert W.readable_on("#4363d8") == "#ffffff"


def test_removal_log_undoes_the_last_group_first():
    log = W.RemovalLog()
    assert not log.can_undo() and log.pop() == []
    log.push(["A", "B"])
    log.push([])
    log.push(["C"])
    assert log.pop() == ["C"] and log.pop() == ["A", "B"] and not log.can_undo()


# -- the rules of the wizard ---------------------------------------------------------------------

def test_run_needs_a_valid_closed_outline():
    st = S.WizardState()
    assert not st.can_run()
    st.set_polygon([(0, 0), (4, 0), (4, 4), (0, 4)], "")
    assert st.can_run()
    st.set_polygon([(0, 0), (4, 4), (4, 0), (0, 4)], "crossing")
    assert not st.can_run()
    st.set_polygon(None)
    assert not st.can_run()


def test_next_after_detection_only_while_the_outline_is_the_one_used():
    st = S.WizardState()
    square = [(0, 0), (4, 0), (4, 4), (0, 4)]
    st.set_polygon(square, "")
    assert not st.can_next(S.SEARCH)
    st.set_facades(make_facades())
    assert st.can_next(S.SEARCH)
    st.set_polygon([(0, 0), (5, 0), (5, 5), (0, 5)], "")
    assert not st.can_next(S.SEARCH)


def test_removed_facades_are_not_active_and_the_last_one_gates_next():
    st = S.WizardState()
    st.set_facades(make_facades(2))
    st.facades[0].removed = True
    assert st.letters() == ["B"] and st.can_next(S.FACADES)
    st.facades[1].removed = True
    assert st.letters() == [] and not st.can_next(S.FACADES)


def test_linked_margins_follow_each_other_and_unlinked_ones_do_not():
    st = S.WizardState()
    st.set_facades(make_facades(2))
    st.apply_margin(["A", "B"], "left", 2.5)
    assert st.margins_of("A").right == 2.5 and st.margins_of("B").right == 2.5
    st.apply_link(["A"], "lr", False)
    st.apply_margin(["A"], "right", 4.0)
    assert st.margins_of("A").left == 2.5 and st.margins_of("A").right == 4.0
    assert st.margins_of("B").right == 2.5


def test_margins_of_one_facade_do_not_change_the_others():
    st = S.WizardState()
    st.set_facades(make_facades(3))
    st.apply_margin(["B"], "top", 3.0)
    assert [st.margins_of(l).top for l in "ABC"] == [1.0, 3.0, 1.0]
    mapping = st.margins_map()
    assert mapping["B"].top == 3.0 and mapping["A"] is not st.margins_of("A")      # copies


def test_a_change_makes_the_built_results_stale():
    st = S.WizardState()
    st.set_facades(make_facades(2))
    st.method = Method.DEPTH_MAPS.value
    st.dem_state = {"A": (True, ""), "B": (True, "")}
    st.ortho_state = {"A": (True, "")}
    before = st.signature()
    st.apply_margin(["A"], "depth", 3.0)
    assert st.dem_state == {} and st.ortho_state == {} and st.ortho_key is None
    assert st.signature() != before


def test_next_from_the_method_screen_needs_an_available_method():
    st = S.WizardState()
    st.set_facades(make_facades(1))
    st.availability = S.Availability.from_dict({"tie_points": (True, ""), "depth_maps": (False, "x"), "dense_cloud": (False, "y")})
    st.method = "depth_maps"
    assert not st.can_next(S.METHOD)
    st.method = "tie_points"
    assert st.can_next(S.METHOD)
    st.method = ""
    assert not st.can_next(S.METHOD)


def test_next_from_the_build_screen_needs_one_surface():
    st = S.WizardState()
    st.set_facades(make_facades(2))
    assert not st.can_next(S.BUILD)
    st.dem_state = {"A": (False, "no"), "B": (True, "")}
    assert st.can_next(S.BUILD) and st.dem_ok_letters() == ["B"]


def test_the_wizard_has_seven_steps_and_the_pictures_step_comes_before_the_export_settings():
    assert S.STEP_KEYS == ("search", "facades", "margins", "method", "build", "pictures", "export")
    assert (S.BUILD, S.PICTURES, S.EXPORT) == (4, 5, 6) and S.STEP_KEYS[S.PICTURES] == "pictures" and S.STEP_KEYS[S.EXPORT] == "export"


def test_next_from_the_pictures_screen_needs_one_ticked_picture_and_the_export_screen_is_last():
    st = S.WizardState()
    st.set_facades(make_facades(3))
    assert not st.can_next(S.PICTURES) and st.chosen_letters() == []
    st.ortho_state = {"A": (True, ""), "B": (False, "no"), "C": (True, "")}
    assert st.chosen_letters() == ["A", "C"] and st.can_next(S.PICTURES)       # nothing ticked yet: every built picture counts
    st.picture_choice = {"C", "B"}
    assert st.chosen_letters() == ["C"]                                         # B was ticked but failed: it is not exported
    st.picture_choice = set()
    assert st.chosen_letters() == [] and not st.can_next(S.PICTURES)
    assert not st.can_next(S.EXPORT)


def test_a_new_build_ticks_everything_again():
    st = S.WizardState()
    st.set_facades(make_facades(2))
    st.ortho_state = {"A": (True, ""), "B": (True, "")}
    st.picture_choice = {"A"}
    st.apply_margin(["A", "B"], "left", 2.0)                                    # other margins: new pictures
    assert st.picture_choice is None and st.ortho_state == {}
    st.ortho_state = {"A": (True, ""), "B": (True, "")}
    assert st.chosen_letters() == ["A", "B"]
    st.picture_choice = {"B"}
    st.set_facades(make_facades(2))                                              # a new detection
    assert st.picture_choice is None


def test_preferences_round_trip(tmp_path, monkeypatch):
    from aerotools import config
    monkeypatch.setattr(config, "settings_dir", lambda: str(tmp_path))
    st = S.WizardState()
    st.default_margins.set_left(1.7)
    st.method = "tie_points"
    st.pixel_cm = 2.5
    st.apply_all = False
    assert S.save_prefs(st, fmt="jpg", dxf=True)
    other = S.WizardState()
    saved = S.load_prefs(other)
    assert other.default_margins.left == 1.7 and other.method == "tie_points" and other.pixel_cm == 2.5 and other.apply_all is False
    assert saved["fmt"] == "jpg" and saved["dxf"] is True


def test_a_broken_preference_is_ignored(tmp_path, monkeypatch):
    from aerotools import config
    monkeypatch.setattr(config, "settings_dir", lambda: str(tmp_path))
    config.put(S.SECTION, "method", "teleport")
    config.put(S.SECTION, "pixel_cm", "abc")
    config.put(S.SECTION, "margins", "nonsense")
    st = S.WizardState()
    S.load_prefs(st)
    assert st.method == "" and st.pixel_cm == S.DEFAULT_PIXEL_CM and st.default_margins.left == 1.0


# -- worker thread --------------------------------------------------------------------------------

def test_task_runs_off_the_calling_thread_and_reports_plain_state():
    import threading
    seen = {}

    def work(progress, cancelled):
        seen["thread"] = threading.current_thread()
        progress(0.5, "halfway")
        return 42
    task = Task(work).start()
    task.wait(5)
    assert task.done and task.result == 42 and task.error is None and not task.was_cancelled
    assert seen["thread"] is not threading.current_thread()
    assert task.fraction == 0.5 and task.text == "halfway"


def test_task_cancel_is_seen_by_the_worker():
    def work(progress, cancelled):
        while not cancelled():
            time.sleep(0.005)
        raise Cancelled()
    task = Task(work).start()
    time.sleep(0.05)
    assert not task.done
    task.cancel()
    task.wait(5)
    assert task.done and task.was_cancelled and task.error is None


def test_task_keeps_the_error_and_the_traceback():
    def work(progress, cancelled):
        raise RuntimeError("no tie points")
    task = Task(work).start()
    task.wait(5)
    assert task.done and isinstance(task.error, RuntimeError) and "no tie points" in task.trace
    assert plain_error(task.error) == "no tie points"


def test_task_can_run_on_the_calling_thread():
    task = Task(lambda progress, cancelled: "x").start(threaded=False)
    assert task.done and task.result == "x"


def test_batch_worker_goes_on_after_a_failure_and_marks_the_rest_cancelled():
    from aerotools.tools.aerofacade.screens_build import batch_worker
    facades = make_facades(4)
    items = {}

    def call(facade, sub):
        if facade.letter == "B":
            raise RuntimeError("bad region")
        if facade.letter == "C":
            raise Cancelled()
        return True, "ok"
    task = Task(batch_worker(facades, call, items), items=items).start()
    task.wait(5)
    assert items["A"][0] == "done"
    assert items["B"] == ("failed", "bad region")
    assert items["C"][0] == "cancelled" and items["D"][0] == "cancelled"
    assert task.result == {"A": (True, "ok"), "B": (False, "bad region")}


# -- texts ---------------------------------------------------------------------------------------

def _ui_keys_in_code():
    found = set()
    for name in os.listdir(PACKAGE):
        if name.endswith(".py"):
            with open(os.path.join(PACKAGE, name), encoding="utf-8") as handle:
                found.update(re.findall(r'"(facade\.ui\.[a-z_0-9A-Z]+)"', handle.read()))
    return found


def test_every_ui_text_the_code_asks_for_exists_in_english():
    texts = i18n.texts()
    missing = sorted(k for k in _ui_keys_in_code() if k not in texts)
    assert missing == []


def test_every_ui_text_is_used_by_the_code():
    keys = _ui_keys_in_code()
    unused = sorted(k for k in i18n.texts() if k.startswith("facade.ui.") and k not in keys)
    assert unused == []


def _package_trees():
    for name in sorted(os.listdir(PACKAGE)):
        if name.endswith(".py"):
            with open(os.path.join(PACKAGE, name), encoding="utf-8") as handle:
                yield name, ast.parse(handle.read(), name)


def test_every_text_key_written_in_the_package_exists_in_the_catalog():
    """A key that is not in the catalog would show on screen as raw text: no string that looks like a key may be missing."""
    key_like = re.compile(r"^facade\.[A-Za-z0-9_.]+$")
    missing = []
    for name, tree in _package_trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and key_like.match(node.value):
                if not i18n.has(node.value):
                    missing.append((name, node.lineno, node.value))
    assert missing == []


def test_every_translation_call_gets_a_key_that_exists_or_a_variable():
    """`tr("...")` / `i18n.t("...")` with a literal must name a catalog key; a key built by string arithmetic is refused
    (it cannot be checked, and it is how a raw key ends up on screen)."""
    problems = []
    for name, tree in _package_trees():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            called = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            if called not in ("tr", "t"):
                continue
            if called == "t" and not (isinstance(func, ast.Attribute) and getattr(func.value, "id", "") == "i18n"):
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                if not i18n.has(first.value):
                    problems.append((name, node.lineno, first.value))
            elif isinstance(first, (ast.BinOp, ast.JoinedStr)) or (
                    isinstance(first, ast.Call) and getattr(first.func, "attr", "") == "format"):
                problems.append((name, node.lineno, "key built at run time"))
    assert problems == []
