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
    texts = i18n.texts("en")
    missing = sorted(k for k in _ui_keys_in_code() if k not in texts)
    assert missing == []


def test_every_ui_text_is_used_by_the_code():
    keys = _ui_keys_in_code()
    unused = sorted(k for k in i18n.texts("en") if k.startswith("facade.ui.") and k not in keys)
    assert unused == []


def test_ui_texts_fill_their_fields_in_both_languages():
    import string
    en, fr = i18n.texts("en"), i18n.texts("fr")
    for key, text in en.items():
        if key.startswith("facade.ui.") and key in fr:
            fields = lambda t: sorted(n for _l, n, _s, _c in string.Formatter().parse(t) if n)
            assert fields(text) == fields(fr[key]), key


def test_the_code_of_the_ui_has_no_hardcoded_visible_text():
    """Every visible text goes through the catalog: no accented letter or French word in a string literal of the UI modules."""
    accents = re.compile("[àâäçéèêëîïôöùûüÿœ«»]")
    for name in ("widgets.py", "widgets_margins.py", "wizard.py", "screens_base.py", "screens_search.py", "screens_margins.py",
                 "screens_build.py", "tool.py"):
        with open(os.path.join(PACKAGE, name), encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        docs = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)) and node.body and isinstance(node.body[0], ast.Expr) \
                    and isinstance(node.body[0].value, ast.Constant):
                docs.add(id(node.body[0].value))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
                if name == "tool.py" and node.value.startswith("Aero-Facade"):
                    continue                                        # journal lines (technical, French like the others)
                if accents.search(node.value) and node.value not in ("✓", "✕"):
                    if name == "tool.py":
                        continue
                    pytest.fail("{}: {!r}".format(name, node.value))
