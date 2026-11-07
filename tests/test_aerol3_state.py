"""Aero-L3: the wizard's state (button rules, saved choices) and the fake backend. No Qt, no Metashape."""

import datetime
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import aerol3_synth as synth  # noqa: E402
from aerotools import config  # noqa: E402
from aerotools.tools.aerol3 import checks, scan, state as S  # noqa: E402
from aerotools.tools.aerol3 import backend  # noqa: E402
from aerotools.tools.aerol3.backend import Cancelled  # noqa: E402
from aerotools.tools.aerol3.fake_backend import FakeBackend  # noqa: E402


@pytest.fixture
def mission(tmp_path):
    synth.write_flight(tmp_path, "DJI_202610020844_009_TESTSITE-FLIGHT01", shots=8, heading=120.0, group="B")
    synth.write_flight(tmp_path, "DJI_202610020910_011_TESTSITE-FLIGHT02", shots=8, heading=300.0, group="A", start=synth.GPS0 + datetime.timedelta(minutes=30))
    synth.write_flight(tmp_path, "DJI_202610021054_016_TESTSITE-TINY", shots=2, start=synth.GPS0 + datetime.timedelta(hours=2))
    return scan.scan_folder(str(tmp_path))


def test_the_buttons_follow_the_state(mission):
    state = S.WizardState()
    assert not state.can_next(S.PHOTOS) and not state.can_next(S.RUN)
    state.set_scan(mission)
    assert state.can_next(S.PHOTOS) and state.can_next(S.CHECKS) and state.can_next(S.PROJECT)
    assert [f.name.split("-")[-1] for f in state.selected_flights()] == ["FLIGHT01", "FLIGHT02"]               # the tiny flight starts unticked
    for key in list(state.selection):
        state.set_selected(key, False)
    assert state.analysis is None and not state.can_next(S.PHOTOS)


def test_the_chunk_name_is_proposed_and_a_typed_name_is_kept(mission):
    state = S.WizardState()
    state.set_scan(mission)
    assert state.options.chunk_name == "L3_TESTSITE_261002"
    state.options.chunk_name = "Mine"
    state.refresh_analysis()
    assert state.options.chunk_name == "Mine"
    state.options.chunk_name = "  "
    assert not state.can_next(S.PROJECT)


def test_a_blocking_finding_closes_next(mission):
    state = S.WizardState()
    state.set_scan(mission)
    state.set_option("include_unpaired", True)
    assert state.can_next(S.CHECKS)
    for flight in state.scan.flights:
        for rec in flight.photos:
            rec.serial = rec.serial + ("-X" if flight.name.endswith("LIGHT02") else "")        # a second unit
    for key in state.selection:
        state.selection[key] = True
    state.refresh_analysis()
    assert state.analysis.has("mixed_units") and not state.can_next(S.CHECKS)


def test_steps_to_run_adds_the_alignment_when_a_later_step_needs_it():
    state = S.WizardState()
    assert state.steps_to_run() == []                                      # nothing ticked: the chunk only
    state.options.steps = {"align": False, "repair": False, "optimize": True, "dem": False, "ortho": False}
    assert state.steps_to_run() == ["align", "optimize"]
    state.options.steps = {"align": False, "repair": False, "optimize": False, "dem": False, "ortho": False}
    state.options.quick_test = True
    assert state.steps_to_run() == ["quick_test"]
    state.options.quick_test = False
    assert state.steps_to_run() == []
    state.options.steps = {"align": True, "repair": True, "optimize": True, "dem": True, "ortho": True}
    assert state.steps_to_run() == ["align", "repair", "optimize", "dem", "ortho"]


def test_ticking_a_step_of_the_chain_ticks_what_it_needs():
    state = S.WizardState()
    state.set_step("dem", True)
    assert state.options.steps["align"] and state.options.steps["repair"] and state.steps_to_run() == ["align", "repair", "dem"]
    state.set_step("align", False)
    assert state.steps_to_run() == [] and not state.options.steps["dem"]
    state.set_step("align", True)
    assert state.steps_to_run() == ["align", "repair"]


def test_the_progress_bar_is_shared_out_between_the_steps():
    spans = backend.step_spans(["align", "repair", "ortho"])
    assert [k for k, _lo, _hi in spans] == ["create", "verify", "align", "repair", "ortho"]
    assert spans[0][1] == 0.0 and abs(spans[-1][2] - 1.0) < 1e-9 and all(a[2] == b[1] for a, b in zip(spans, spans[1:]))


def test_choices_are_saved_and_loaded_without_the_name(mission):
    state = S.WizardState()
    state.set_scan(mission)
    state.root = "D:\\flights"
    state.options.accuracy_fixed = 0.03
    state.options.frame = "etrs89"
    state.options.steps["align"] = True
    S.save_prefs(state)
    saved = config.section(S.SECTION)
    assert saved["last_folder"] == "D:\\flights" and "chunk_name" not in saved["options"]
    other = S.WizardState()
    S.load_prefs(other)
    assert other.root == "D:\\flights" and other.options.accuracy_fixed == 0.03 and other.options.frame == "etrs89"
    assert other.options.steps["align"] is True and other.options.chunk_name == ""


def test_saving_keeps_the_other_tools_settings(mission):
    config.save_settings({"aeroexports": {"cad_format": "dxf"}, "aerol3": {"last_folder": "x", "junk": 1}})
    state = S.WizardState()
    state.root = "E:\\y"
    S.save_prefs(state)
    data = config.load_settings()
    assert data["aeroexports"] == {"cad_format": "dxf"} and data["aerol3"]["last_folder"] == "E:\\y"


def test_damaged_saved_choices_give_the_defaults():
    config.save_settings({"aerol3": {"last_folder": 5, "options": {"accuracy_fixed": "x", "frame": "mars"}}})
    state = S.WizardState()
    S.load_prefs(state)
    assert state.root == "" and state.options.accuracy_fixed == 0.05 and state.options.frame == "wgs84"
    config.save_settings({"aerol3": "junk"})
    S.load_prefs(S.WizardState())


def test_the_fake_backend_runs_the_steps_in_order_and_can_cancel(mission):
    state = S.WizardState()
    state.set_scan(mission)
    plan = state.analysis.plan
    backend = FakeBackend(flipped=2)
    seen = []
    result = backend.run(plan, ["align", "repair", "optimize"], lambda f, t: seen.append(f), lambda: False)
    assert [s.key for s in result.steps] == ["create", "verify", "align", "repair", "optimize"] and result.ok
    assert result.alignment.repaired == 2 and seen == sorted(seen)
    stop = FakeBackend().run(plan, ["align"], None, lambda: True)
    assert stop.cancelled and not stop.ok and stop.steps == []
    failing = FakeBackend(fail="align").run(plan, ["align", "repair"], None, None)
    assert not failing.ok and [s.key for s in failing.steps] == ["create", "verify", "align"]


def test_the_tool_is_registered_in_the_aero_menu():
    entry = config.tool("aerol3")
    assert entry["menu"] == "Aero-L3" and entry["ready"] is True and entry["reopen"] is False
    from aerotools import i18n
    assert i18n.has(entry["subtitle"])
