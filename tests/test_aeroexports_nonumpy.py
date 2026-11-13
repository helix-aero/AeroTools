"""Aero-Exports on a Metashape without numpy: every module loads, the exports that do not need numpy still run, and the ones that do
fail with a readable message (the window disables them; see tests_qt/run_qt_aeroexports_nonumpy.py for the real window)."""

import importlib
import os
import subprocess
import sys

import pytest

from test_aeroexports_cad import DAY, Env             # imported while numpy is still there; neither uses it

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PACKAGES = ("aerotools.tools.aeroexports", "aerotools.projtool")


def context(pipeline, tmp_path, preset):
    info = {"ortho": (0.5, 200, 100), "dem": (0.5, 200, 100), "box_ortho": (1000.0, 2000.0, 1100.0, 2050.0),
            "box_dem": (1000.0, 2000.0, 1100.0, 2050.0), "crs_object": "project", "center": (2.35, 48.85)}
    return pipeline.Context(object(), info, str(tmp_path), "ClientA", DAY, preset, Env(tmp_path).export, tools={},
                            snap=lambda box, pixel: box, transform=lambda box, a, b: box)


@pytest.fixture
def no_numpy():
    """`import numpy` raises ImportError, and the modules of the tool are imported afresh (restored afterwards)."""
    real = sys.modules.get("numpy")
    saved = {name: module for name, module in sys.modules.items() if name.startswith(PACKAGES)}
    for name in saved:
        del sys.modules[name]
    sys.modules["numpy"] = None
    try:
        yield
    finally:
        for name in [n for n in sys.modules if n.startswith(PACKAGES)]:
            del sys.modules[name]
        sys.modules.update(saved)
        if real is None:
            del sys.modules["numpy"]
        else:
            sys.modules["numpy"] = real                       # the real one is back for the other tests


def test_every_module_of_the_window_and_the_pipeline_imports_without_numpy(no_numpy):
    dialog = importlib.import_module("aerotools.tools.aeroexports.dialog")
    pipeline = importlib.import_module("aerotools.tools.aeroexports.pipeline")
    importlib.import_module("aerotools.tools.aeroexports.tool")
    assert dialog.HAS_NUMPY is False and pipeline.Failure


def test_the_numpy_modules_load_only_when_used(no_numpy):
    pipeline = importlib.import_module("aerotools.tools.aeroexports.pipeline")
    for name in ("relief", "footprint", "vertical"):
        with pytest.raises(ImportError):
            getattr(getattr(pipeline, name), "anything")


def test_a_plain_geotiff_export_runs_without_numpy(no_numpy, tmp_path):
    pipeline = importlib.import_module("aerotools.tools.aeroexports.pipeline")
    plan = importlib.import_module("aerotools.tools.aeroexports.plan")
    jobs = plan.build_jobs("ClientA", DAY, {"ortho": 0.5, "dem": 0.5}, "full", str(tmp_path), version=1)
    result = pipeline.run(context(pipeline, tmp_path, "full"), jobs, lambda f, t: None)
    assert sorted(os.path.basename(p) for p, _s in result.files) == ["ClientA_300704_50cm_dtm.tif", "ClientA_300704_50cm_ortho.tif"]


def test_a_jpeg_export_runs_without_numpy(no_numpy, tmp_path):
    pipeline = importlib.import_module("aerotools.tools.aeroexports.pipeline")
    plan = importlib.import_module("aerotools.tools.aeroexports.plan")
    jobs = plan.build_jobs("ClientA", DAY, {"ortho": 0.5}, "jpeg", str(tmp_path), version=1)
    result = pipeline.run(context(pipeline, tmp_path, "jpeg"), jobs, lambda f, t: None)
    assert [os.path.basename(p) for p, _s in result.files][0] == "ClientA_300704_50cm_ortho.jpg"


def test_what_needs_numpy_fails_with_a_readable_message_and_leaves_nothing(no_numpy, tmp_path):
    pipeline = importlib.import_module("aerotools.tools.aeroexports.pipeline")
    plan = importlib.import_module("aerotools.tools.aeroexports.plan")
    jobs = plan.build_jobs("ClientA", DAY, {"dem": 0.5}, "full", str(tmp_path), version=1, dem_picture=True)
    with pytest.raises(pipeline.Failure) as caught:
        pipeline.run(context(pipeline, tmp_path, "full"), jobs, lambda f, t: None)
    assert "numpy" in str(caught.value)
    assert os.listdir(str(tmp_path)) == []


def test_the_real_window_opens_without_numpy():
    """In a fresh Python with the real Qt (skipped where there is none): the window opens and disables what needs numpy."""
    probe = subprocess.run([sys.executable, "-c", "import PySide6"], capture_output=True)
    probe2 = subprocess.run([sys.executable, "-c", "import PySide2"], capture_output=True)
    if probe.returncode and probe2.returncode:
        pytest.skip("no Qt in this Python")
    done = subprocess.run([sys.executable, "-B", os.path.join(ROOT, "tests_qt", "run_qt_aeroexports_nonumpy.py")],
                          capture_output=True, text=True, timeout=300)
    assert done.returncode == 0 and "FAIL" not in done.stdout, done.stdout + done.stderr
