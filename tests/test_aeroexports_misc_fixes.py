"""Aero-Exports: small fixes (degrees read from the file, holes kept without the nodata tag, GDAL output not lost, stale work folders,
GDAL on the PATH, feet systems, progress index)."""

import os
import time

import numpy as np
import pytest

from aerotools.tools.aeroexports import external, pipeline, preview, projdb, relief, tiffio, vertical
from test_aeroexports_geoid import RAC23, X0, Y0, PIXEL, fake_lonlat, make_dem
from test_aeroexports_look import hill_dem, read_rgb


def test_a_millimetre_dem_in_metres_is_shaded_and_a_degree_dem_is_not(tmp_path):
    metres, degrees = str(tmp_path / "m.tif"), str(tmp_path / "d.tif")
    hill_dem(metres, pixel=0.005)                               # 5 mm pixel (close range), projected CRS
    hill_dem(degrees, pixel=0.00001, geographic=True)
    assert not relief.is_geographic(tiffio.Tiff(metres), 0.005) and relief.is_geographic(tiffio.Tiff(degrees), 0.00001)
    assert not preview.Preview.from_tiff(metres).geographic and preview.Preview.from_tiff(degrees).geographic
    sun, bare = str(tmp_path / "sun.tif"), str(tmp_path / "bare.tif")
    assert relief.render(metres, sun)[0] and relief.render(metres, bare, look=relief.looks.DEFAULT._replace(shading="none"))[0]
    assert not np.array_equal(read_rgb(sun), read_rgb(bare))              # the 5 mm DEM in metres is shaded, as a metric DEM must be


def test_a_geotiff_without_the_model_type_key_keeps_the_old_pixel_guess():
    class Bare(object):
        def ints(self, tag, default=None):
            return default
    assert relief.is_geographic(Bare(), 0.00001) and not relief.is_geographic(Bare(), 0.5)


def test_holes_stay_holes_when_the_dem_has_no_usable_nodata_tag(tmp_path):
    height = np.full((90, 120), 100.0, dtype=np.float32)
    height[10:20, 10:20] = -32767.0
    src, dst = str(tmp_path / "dem.tif"), str(tmp_path / "alt.tif")
    make_dem(src, height, X0, Y0, PIXEL, nodata=-1.0)           # the tag says -1, the holes are -32767 (a DEM that went through another tool)
    vertical.correct_dem(src, dst, vertical.read_grid(RAC23), fake_lonlat)
    out = tiffio.Tiff(dst).read_all()
    assert (out[10:20, 10:20] == -32767.0).all() and abs(float(out[50, 50]) - 100.0) > 40.0


def test_gdal_that_fills_its_output_does_not_block_and_its_reason_is_reported(tmp_path):
    chatty = tmp_path / "gdal_translate.exe"
    chatty.write_text("")                                       # the file only has to exist; Popen is replaced below
    import subprocess
    import sys
    script = tmp_path / "noisy.py"
    script.write_text("import sys\nsys.stdout.write('x' * 3000000)\nsys.stderr.write('ERROR 4: no JP2OpenJPEG driver\n')\nsys.exit(1)\n")
    real = subprocess.Popen
    calls = []

    def popen(command, **kw):
        calls.append(kw)
        return real([sys.executable, str(script)], **kw)

    old = external.subprocess.Popen
    external.subprocess.Popen = popen
    try:
        ok, message = external.to_jp2_gdal(str(tmp_path), str(tmp_path / "a.tif"), str(tmp_path / "a.jp2"))
    finally:
        external.subprocess.Popen = old
    assert not ok and "no JP2OpenJPEG driver" in message
    assert calls[0]["stdout"] is not subprocess.PIPE and calls[0]["stderr"] is subprocess.STDOUT


def test_x86_program_files_is_not_version_86_and_gdal_on_the_path_is_found(tmp_path, monkeypatch):
    assert external._version_key(r"C:\Program Files (x86)\QGIS 3.28\bin\g.exe") < external._version_key(r"C:\Program Files\QGIS 3.34\bin\g.exe")
    bin_dir = tmp_path / "conda" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "gdal_translate.exe").write_text("")
    monkeypatch.setattr(external, "_program_dirs", lambda: [])
    monkeypatch.setattr(external.shutil, "which", lambda name: str(bin_dir / "gdal_translate.exe"))
    assert external.find_gdal("") == str(bin_dir)


def test_stale_work_folders_are_swept_and_recent_ones_are_left(tmp_path):
    old, recent = tmp_path / (pipeline.TMP_NAME + "abc"), tmp_path / (pipeline.TMP_NAME + "xyz")
    part = tmp_path / (pipeline.TMP_NAME + "_TOPO.dwg")
    keep = tmp_path / "deliverable.tif"
    for item in (old, recent):
        item.mkdir()
        (item / "big.tif").write_text("x")
        (item / pipeline.WORK_MARKER).write_text("x")
    foreign = tmp_path / (pipeline.TMP_NAME + "mine")
    foreign.mkdir()
    (foreign / "notes.txt").write_text("x")
    part.write_text("x")
    keep.write_text("x")
    long_ago = time.time() - 3 * 24 * 3600
    for item in (old, part, foreign):
        os.utime(str(item), (long_ago, long_ago))
    assert pipeline.sweep_stale(str(tmp_path)) == 2
    assert not old.exists() and not part.exists() and recent.exists() and keep.exists() and (foreign / "notes.txt").exists()


def test_a_run_sweeps_the_stale_folders_of_an_earlier_crash(tmp_path):
    from test_aeroexports_cad import DAY, Env, fake_context
    from aerotools.tools.aeroexports import plan
    old = tmp_path / (pipeline.TMP_NAME + "crashed")
    old.mkdir()
    (old / pipeline.WORK_MARKER).write_text("x")
    long_ago = time.time() - 3 * 24 * 3600
    os.utime(str(old), (long_ago, long_ago))
    jobs = plan.build_jobs("ClientA", DAY, {"ortho": 0.5}, "full", str(tmp_path), version=1)
    pipeline.run(fake_context(tmp_path, Env(tmp_path), "full"), jobs, lambda f, t: None)
    assert not old.exists()


def test_projected_systems_in_feet_are_not_offered_and_are_recognised():
    if not projdb.available():
        pytest.skip("PROJ database missing")
    assert projdb.is_metric(2154) and projdb.is_metric(32632) and not projdb.is_metric(2277) and not projdb.is_metric(6527)
    assert projdb.is_metric(99999999)                              # unknown: nothing is blocked
    texas = [code for code, _name in projdb.candidates(text="Texas Central")]
    assert 2277 not in texas and 32632 in [code for code, _ in projdb.candidates(text="32632")]
