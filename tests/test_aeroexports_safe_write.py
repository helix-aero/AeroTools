"""Aero-Exports: an export that fails never leaves a wrong file under a deliverable name, and never destroys the previous one."""

import os

import pytest

from aerotools.tools.aeroexports import pipeline, plan, vertical
from test_aeroexports_cad import DAY, Env, fake_context

DEM_NAME = "ClientA_300704_50cm_dtm.tif"
ORTHO_NAME = "ClientA_300704_50cm_ortho.tif"


def jobs_for(tmp_path, kinds=("dem",), preset="full", **kw):
    return plan.build_jobs("ClientA", DAY, dict.fromkeys(kinds, 0.5), preset, str(tmp_path), version=1, **kw)


def tifs(tmp_path):
    return sorted(n for n in os.listdir(str(tmp_path)) if n.lower().endswith(".tif"))


def leftovers(tmp_path):
    return [n for n in os.listdir(str(tmp_path)) if n.startswith(pipeline.TMP_NAME)]


class Engine(object):
    """PROJ stand-in that is present."""

    def available(self):
        return True

    def lonlat(self, epsg, xs, ys):
        return xs, ys


# -- C1: a DEM still in ellipsoidal heights ----------------------------------------------------------

def test_a_failed_altitude_conversion_leaves_no_dem_in_the_outputs_folder(tmp_path):
    ctx = fake_context(tmp_path, Env(tmp_path), "full", vertical="fr_ign_RAC23", proj=None)        # PROJ missing
    with pytest.raises(pipeline.Failure, match="Altitude"):
        pipeline.run(ctx, jobs_for(tmp_path), lambda f, t: None)
    assert tifs(tmp_path) == [] and leftovers(tmp_path) == []


def test_a_grid_that_does_not_cover_the_dem_leaves_no_dem_either(tmp_path, monkeypatch):
    def refuse(*args, **kwargs):
        raise ValueError("the grid does not cover the DEM")

    monkeypatch.setattr(vertical, "load_grid", lambda grid_id: object())
    monkeypatch.setattr(vertical, "correct_dem", refuse)
    logged = []
    ctx = fake_context(tmp_path, Env(tmp_path), "full", vertical="fr_ign_RAC23", proj=Engine(), epsg=3942, log=logged.append)
    with pytest.raises(pipeline.Failure, match="does not cover"):
        pipeline.run(ctx, jobs_for(tmp_path), lambda f, t: None)
    assert tifs(tmp_path) == [] and leftovers(tmp_path) == []
    assert any(DEM_NAME in line and "not written" in line for line in logged)       # the journal says it


def test_cad_dem_after_a_failed_altitude_conversion_leaves_nothing_in_the_assets(tmp_path):
    ctx = fake_context(tmp_path, Env(tmp_path), "cad", vertical="fr_ign_RAC23", proj=None, cad={"format": "dxf", "images": "jp2"})
    jobs = plan.build_jobs("ClientA", DAY, {"dem": 0.5}, "cad", str(tmp_path), cad={"format": "dxf", "images": "jp2"}, version=1)
    with pytest.raises(pipeline.Failure, match="Altitude"):
        pipeline.run(ctx, jobs, lambda f, t: None)
    found = [n for base, _d, names in os.walk(str(tmp_path)) for n in names]
    assert found == []


def test_a_failed_altitude_conversion_keeps_the_previous_good_dem(tmp_path):
    (tmp_path / DEM_NAME).write_bytes(b"delivered last week, in altitudes")
    ctx = fake_context(tmp_path, Env(tmp_path), "full", vertical="fr_ign_RAC23", proj=None)
    with pytest.raises(pipeline.Failure):
        pipeline.run(ctx, jobs_for(tmp_path), lambda f, t: None)
    assert (tmp_path / DEM_NAME).read_bytes() == b"delivered last week, in altitudes"


# -- finding 9: the previous deliverable survives a failure -------------------------------------------

def test_a_failing_export_does_not_delete_the_previous_deliverable(tmp_path):
    previous = tmp_path / ORTHO_NAME
    previous.write_bytes(b"previous deliverable")
    env = Env(tmp_path)
    env.export = lambda *a, **k: (False, "disk full")
    with pytest.raises(pipeline.Failure, match="disk full"):
        pipeline.run(fake_context(tmp_path, env, "full"), jobs_for(tmp_path, ("ortho",)), lambda f, t: None)
    assert previous.read_bytes() == b"previous deliverable"


def test_an_export_that_writes_nothing_keeps_the_previous_deliverable(tmp_path):
    previous = tmp_path / ORTHO_NAME
    previous.write_bytes(b"left by an export of another chunk")
    env = Env(tmp_path)
    env.export = lambda *a, **k: (True, "")               # no data: no file
    with pytest.raises(pipeline.Failure):
        pipeline.run(fake_context(tmp_path, env, "full"), jobs_for(tmp_path, ("ortho",)), lambda f, t: None)
    assert previous.read_bytes() == b"left by an export of another chunk"       # and it never passed for the new one


def test_a_successful_export_replaces_the_previous_deliverable(tmp_path):
    previous = tmp_path / ORTHO_NAME
    previous.write_bytes(b"previous deliverable")
    result = pipeline.run(fake_context(tmp_path, Env(tmp_path), "full"), jobs_for(tmp_path, ("ortho",)), lambda f, t: None)
    assert previous.read_bytes() != b"previous deliverable" and previous.stat().st_size > 100
    assert result.files == [(str(previous), previous.stat().st_size)] and leftovers(tmp_path) == []


def test_a_failure_on_the_second_tile_keeps_the_previous_tiles(tmp_path):
    first = tmp_path / "ClientA_300704_50cm_R01C01_ortho.tif"
    first.write_bytes(b"old tile")
    env = Env(tmp_path)
    real, calls = env.export, []

    def export(chunk, kind, spec, path, *args, **kwargs):
        calls.append(path)
        if len(calls) == 2:
            return False, "network lost"
        return real(chunk, kind, spec, path, *args, **kwargs)

    env.export = export
    with pytest.raises(pipeline.Failure, match="network lost"):
        pipeline.run(fake_context(tmp_path, env, "full", tile_m=50), jobs_for(tmp_path, ("ortho",)), lambda f, t: None)
    assert first.read_bytes() != b"old tile"              # the first tile was rewritten, complete
    assert leftovers(tmp_path) == []


def test_an_older_tile_without_data_this_time_is_removed_only_when_the_export_succeeds(tmp_path):
    orphan = tmp_path / "ClientA_300704_50cm_R01C02_ortho.tif"
    orphan.write_bytes(b"old tile")
    env = Env(tmp_path)
    env.empty_tiles = {(1050, 2000)}                      # that tile has no data now
    pipeline.run(fake_context(tmp_path, env, "full", tile_m=50), jobs_for(tmp_path, ("ortho",)), lambda f, t: None)
    assert not orphan.exists()


def test_the_jpeg_world_file_follows_its_image(tmp_path):
    env = Env(tmp_path)
    real = env.export

    def export(chunk, kind, spec, path, *args, **kwargs):
        ok, error = real(chunk, kind, spec, path, *args, **kwargs)
        stem = os.path.splitext(path)[0]
        open(stem + ".jgw", "w").write("0.5\n0\n0\n-0.5\n1000\n2050\n")
        open(stem + ".prj", "w").write("PROJCS[]")
        return ok, error

    env.export = export
    result = pipeline.run(fake_context(tmp_path, env, "jpeg"), jobs_for(tmp_path, ("ortho",), preset="jpeg"), lambda f, t: None)
    names = sorted(os.listdir(str(tmp_path)))
    assert names == ["ClientA_300704_50cm_ortho.jgw", "ClientA_300704_50cm_ortho.jpg", "ClientA_300704_50cm_ortho.prj"]
    assert [os.path.basename(p) for p, _n in result.files] == ["ClientA_300704_50cm_ortho.jpg", "ClientA_300704_50cm_ortho.jgw",
                                                              "ClientA_300704_50cm_ortho.prj"]


# -- finding 9, second half: conversions (JPEG 2000 / ECW), DXF, DWG -------------------------------------

def test_a_failed_conversion_keeps_the_previous_jp2(tmp_path, monkeypatch):
    from aerotools.tools.aeroexports import external

    def failing_converter(tool, src, dst, *args, **kwargs):
        if os.path.exists(dst):
            os.remove(dst)                                # what the real converters do before they start
        return False, "GDAL: JPEG 2000 failed"

    monkeypatch.setattr(external, "to_jp2_gdal", failing_converter)
    previous = tmp_path / "ClientA_300704_50cm_ortho.jp2"
    previous.write_bytes(b"delivered last week")
    jobs = jobs_for(tmp_path, ("ortho",), preset="jp2")
    with pytest.raises(pipeline.Failure, match="JPEG 2000 failed"):
        pipeline.run(fake_context(tmp_path, Env(tmp_path), "jp2", tools={"gdal": "G"}), jobs, lambda f, t: None)
    assert previous.read_bytes() == b"delivered last week" and leftovers(tmp_path) == []
    assert tifs(tmp_path) == []


def test_a_failed_ecw_conversion_keeps_the_previous_ecw(tmp_path, monkeypatch):
    from aerotools.tools.aeroexports import external

    def failing_gm(gm, src, dst, *args, **kwargs):
        if os.path.exists(dst):
            os.remove(dst)
        return False, "Global Mapper did not write the file (licence or export refused?)"

    monkeypatch.setattr(external, "to_ecw", failing_gm)
    previous = tmp_path / "ClientA_300704_50cm_ortho.ecw"
    previous.write_bytes(b"delivered last week")
    with pytest.raises(pipeline.Failure, match="Global Mapper"):
        pipeline.run(fake_context(tmp_path, Env(tmp_path), "ecw", tools={"gm": "GM"}), jobs_for(tmp_path, ("ortho",), preset="ecw"),
                     lambda f, t: None)
    assert previous.read_bytes() == b"delivered last week"


def cad_context(tmp_path, monkeypatch, fmt="dwg", **kw):
    from aerotools.tools.aeroexports import external, relief
    from test_aeroexports_cad import fake_converters
    fake_converters(monkeypatch, [])
    monkeypatch.setattr(external, "check_dxf", lambda core, dxf, tick=None: {"images": 1, "failed": [], "valid": True})
    jobs = plan.build_jobs("ClientA", DAY, {"ortho": 0.5}, "cad", str(tmp_path), cad={"format": fmt, "images": "jp2"}, version=1)
    ctx = fake_context(tmp_path, Env(tmp_path), "cad", tools={"gdal": "G", "acad": "A"}, cad={"format": fmt, "images": "jp2"}, **kw)
    return ctx, jobs


def test_a_failed_dwg_keeps_the_previous_dwg(tmp_path, monkeypatch):
    from aerotools.tools.aeroexports import external

    def failing_autocad(core, dxf, dwg, tick=None, zoom_extents=False):
        if os.path.exists(dwg):
            os.remove(dwg)
        return False, "AutoCAD did not write the DWG"

    monkeypatch.setattr(external, "dxf_to_dwg", failing_autocad)
    (tmp_path / "TOPO.dwg").write_bytes(b"delivered last week")
    ctx, jobs = cad_context(tmp_path, monkeypatch)
    with pytest.raises(pipeline.Failure, match="did not write the DWG"):
        pipeline.run(ctx, jobs, lambda f, t: None)
    assert (tmp_path / "TOPO.dwg").read_bytes() == b"delivered last week"
    assert [n for n in os.listdir(str(tmp_path)) if n.startswith(pipeline.TMP_NAME)] == []


def test_the_dwg_is_written_beside_its_final_place_so_the_image_paths_stay_relative_to_it(tmp_path, monkeypatch):
    from aerotools.tools.aeroexports import external
    seen = []

    def autocad(core, dxf, dwg, tick=None, zoom_extents=False):
        seen.append(dwg)
        open(dwg, "wb").write(b"dwg")
        return True, ""

    monkeypatch.setattr(external, "dxf_to_dwg", autocad)
    ctx, jobs = cad_context(tmp_path, monkeypatch)
    result = pipeline.run(ctx, jobs, lambda f, t: None)
    assert os.path.dirname(seen[0]) == str(tmp_path) and seen[0] != str(tmp_path / "TOPO.dwg")
    assert (tmp_path / "TOPO.dwg").read_bytes() == b"dwg" and result.dwg == str(tmp_path / "TOPO.dwg")
    assert not [n for n in os.listdir(str(tmp_path)) if n.startswith(pipeline.TMP_NAME)]


def test_a_dxf_that_cannot_be_written_keeps_the_previous_dxf(tmp_path, monkeypatch):
    from aerotools.tools.aeroexports import dxfout

    def broken_write(path, *args, **kwargs):
        open(path, "w").write("half a file")
        raise OSError("disk full")

    monkeypatch.setattr(dxfout, "write_dxf", broken_write)
    (tmp_path / "TOPO.dxf").write_bytes(b"delivered last week")
    ctx, jobs = cad_context(tmp_path, monkeypatch, fmt="dxf")
    with pytest.raises(OSError, match="disk full"):
        pipeline.run(ctx, jobs, lambda f, t: None)
    assert (tmp_path / "TOPO.dxf").read_bytes() == b"delivered last week"
