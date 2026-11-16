"""Aero-L3: the pixel option reaches Metashape, a TypeError is not taken for an old API, MRK files that cannot be read are reported,
and an option that does not change the plan does not redo the analysis."""

import os

import pytest

from aerotools.tools.aerol3 import checks, mrk, msl3, plan, scan, state as S

from test_aerol3_msl3 import Chunk, run, world  # noqa: F401  (the fixture)
import aerol3_synth as synth

GOOD = "0003_LV\t216012.500000\t[2608]\t107,N\t713,E\t217,V\t48.85412345,Lat\t2.34912345,Lon\t95.412,Ellh\t0.001221, 0.001338, 0.002954\t34,Q\n"


# ---- pixel_cm ---------------------------------------------------------------------------------------------------

def test_the_pixel_chosen_by_the_operator_is_what_metashape_is_asked_for(world):
    world.plan.options.pixel_cm = 4.0
    run(world, steps=["align", "dem", "ortho"])
    calls = {c[0]: c[1] for c in world.doc.chunks[0].calls if c[0] in ("buildDem", "buildOrthomosaic")}
    assert calls["buildDem"]["resolution"] == pytest.approx(0.04) and calls["buildOrthomosaic"]["resolution"] == pytest.approx(0.04)


def test_no_valid_pixel_leaves_the_choice_to_metashape(world):
    world.plan.options.pixel_cm = 0
    run(world, steps=["align", "dem"])
    assert "resolution" not in [c[1] for c in world.doc.chunks[0].calls if c[0] == "buildDem"][0]


# ---- addPhotos flags --------------------------------------------------------------------------------------------

def test_a_type_error_that_is_not_about_the_flags_is_not_hidden(world, monkeypatch):
    calls = []

    def add(self, filenames=None, filegroups=None, layout=None, progress=None, **flags):
        calls.append(flags)
        raise TypeError("filegroups must be a list of strings")
    monkeypatch.setattr(Chunk, "addPhotos", add)
    result = run(world)
    assert not result.ok and "filegroups must be a list" in result.steps[0].text
    assert len(calls) == 1 and calls[0]["load_xmp_orientation"] is False       # not retried without the flags


def test_an_older_api_is_noted_when_it_refuses_the_flags(world, monkeypatch):
    original = Chunk.__init__

    def init(self, doc, key):
        original(self, doc, key)
        self.rejects_flags = True
    monkeypatch.setattr(Chunk, "__init__", init)
    assert run(world).ok and world.backend.xmp_flags_ignored is True


def test_the_signature_error_of_boost_python_counts_as_an_old_api():
    assert msl3._is_signature_error(TypeError("Python argument types in Chunk.addPhotos did not match C++ signature"))
    assert msl3._is_signature_error(TypeError("addPhotos() got an unexpected keyword argument 'load_reference'"))
    assert not msl3._is_signature_error(TypeError("'NoneType' object is not iterable"))


# ---- MRK --------------------------------------------------------------------------------------------------------

def test_a_byte_order_mark_does_not_drop_the_first_shot(tmp_path):
    path = tmp_path / "x_Timestamp.MRK"
    path.write_bytes(b"\xef\xbb\xbf" + (GOOD * 2).encode())
    shots, ignored, error = mrk.read_mrk(str(path))
    assert len(shots) == 2 and ignored == 0 and error == "" and shots[0].label == "0003_LV"


def test_lines_that_are_not_understood_are_counted_and_an_unreadable_file_is_reported(tmp_path):
    path = tmp_path / "x_Timestamp.MRK"
    path.write_text(GOOD + "0004_LV 1 2 3 another firmware\n\n" + GOOD)
    shots, ignored, error = mrk.read_mrk(str(path))
    assert len(shots) == 2 and ignored == 1
    shots, ignored, error = mrk.read_mrk(str(tmp_path / "missing.MRK"))
    assert shots == [] and error and mrk.parse_mrk(str(tmp_path / "missing.MRK")) == []


def test_a_flight_whose_mrk_could_not_be_read_gets_a_warning_not_a_silent_pass(tmp_path):
    synth.write_flight(tmp_path, shots=4)
    result = scan.scan_folder(str(tmp_path))
    assert not checks.analyse(result.flights).has("mrk_unreadable")
    for flight in result.flights:
        for path in flight.mrk_files:
            with open(path, "w") as handle:
                handle.write("garbage line\nmore garbage\n")
    result = scan.scan_folder(str(tmp_path))
    analysis = checks.analyse(result.flights)
    assert result.flights[0].mrk_ignored == 2 and analysis.has("mrk_unreadable") and not analysis.passed.count("mrk")


# ---- the analysis is not redone for an option that does not change it -------------------------------------------

def test_options_that_do_not_change_the_plan_do_not_redo_the_analysis(tmp_path, monkeypatch):
    synth.write_flight(tmp_path, shots=6)
    state = S.WizardState()
    state.set_scan(scan.scan_folder(str(tmp_path)))
    counted = []
    real = checks.analyse
    monkeypatch.setattr(checks, "analyse", lambda *a, **k: counted.append(1) or real(*a, **k))
    for key, value in (("pixel_cm", 3.0), ("quick_test", True), ("ortho_epsg", "2154"), ("frame", "etrs89"), ("chunk_name", "X")):
        state.set_option(key, value)
    assert counted == [] and state.options.pixel_cm == 3.0
    state.set_option("z_shift", 1.5)
    state.set_option("accuracy_fixed", 0.02)
    state.set_option("include_unpaired", True)
    assert len(counted) == 3
