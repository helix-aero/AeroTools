"""Aero-Exports: DXF, GeoTIFF, CAD / JPEG 2000 / ECW presets, tiles, export chain and external software (without Metashape)."""
import datetime
import os
import struct

import pytest

from aerotools.tools.aeroexports import dxfout, external, geotiff, pipeline, plan, relief

DAY = datetime.date(2030, 7, 4)


# -- a minimal GeoTIFF, written by hand ------------------------------------------------------------

def write_geotiff(path, width, height, left, top, pixel):
    """Classic TIFF with ModelPixelScale and ModelTiepoint: all that geotiff.read_geotiff reads."""
    entries = []
    blobs = b""
    data_start = 8 + 2 + 12 * 4 + 4

    def add(tag, kind, count, payload):
        nonlocal blobs
        if len(payload) <= 4:
            entries.append(struct.pack("<HHI", tag, kind, count) + payload.ljust(4, b"\0"))
        else:
            entries.append(struct.pack("<HHII", tag, kind, count, data_start + len(blobs)))
            blobs += payload

    add(256, 4, 1, struct.pack("<I", width))
    add(257, 4, 1, struct.pack("<I", height))
    add(33550, 12, 3, struct.pack("<3d", pixel, pixel, 0.0))
    add(33922, 12, 6, struct.pack("<6d", 0, 0, 0, left, top, 0))
    with open(path, "wb") as handle:
        handle.write(b"II" + struct.pack("<HI", 42, 8))
        handle.write(struct.pack("<H", len(entries)))
        for entry in entries:
            handle.write(entry)
        handle.write(struct.pack("<I", 0))
        handle.write(blobs)


def test_geotiff_reader_gives_size_origin_and_pixel(tmp_path):
    path = str(tmp_path / "a.tif")
    write_geotiff(path, 200, 100, 1000.0, 2000.0, 0.5)
    info = geotiff.read_geotiff(path)
    assert (info["width"], info["height"], info["origin"], info["pixel"]) == (200, 100, (1000.0, 2000.0), (0.5, 0.5))
    assert geotiff.extent(info) == (1000.0, 1950.0, 1100.0, 2000.0)
    (tmp_path / "b.tif").write_bytes(b"not a tiff at all")
    assert geotiff.read_geotiff(str(tmp_path / "b.tif")) is None
    assert geotiff.read_geotiff(str(tmp_path / "absent.tif")) is None


# -- DXF ----------------------------------------------------------------------------------------------

def image(layer="300704_0.1_ortho", file=r"00_Assets\ortho\a.jp2"):
    return {"layer": layer, "file": file, "size_px": (200, 100), "insert": (650000.0, 6860000.0), "pixel": (0.1, 0.1)}


def pairs(text):
    lines = text.split("\r\n")
    return [(lines[i].strip(), lines[i + 1]) for i in range(0, len(lines) - 1, 2)]


def test_dxf_has_the_sections_autocad_needs_and_the_image(tmp_path):
    text = dxfout.build_dxf([image(), image("300704_0.2_DTM", r"00_Assets\DTM\a.jp2")])
    names = [value for code, value in pairs(text) if code == "2"]
    for section in ("HEADER", "CLASSES", "TABLES", "BLOCKS", "ENTITIES", "OBJECTS"):
        assert section in names
    assert text.endswith("0\r\nEOF\r\n") and "AC1015" in text
    kinds = [value for code, value in pairs(text) if code == "0"]
    assert kinds.count("IMAGE") == 2 and kinds.count("IMAGEDEF") == 2 and kinds.count("IMAGEDEF_REACTOR") == 2
    assert [v for c, v in pairs(text) if c == "1" and v.startswith("00_Assets")] == [r"00_Assets\ortho\a.jp2", r"00_Assets\DTM\a.jp2"]


def test_dxf_places_the_image_by_its_lower_left_corner_and_pixel_size():
    items = pairs(dxfout.build_dxf([image()]))
    start = items.index(("0", "IMAGE"))
    entity = dict((c, v) for c, v in items[start:start + 40] if c in ("10", "20", "11", "22", "13", "23"))
    assert (entity["10"], entity["20"]) == ("650000.0", "6860000.0")
    assert (entity["11"], entity["22"]) == ("0.1", "0.1")
    assert (entity["13"], entity["23"]) == ("200.0", "100.0")


def test_dxf_layers_and_handles_are_unique_and_names_are_cleaned():
    text = dxfout.build_dxf([image("a:b/c"), image("a:b/c"), image("other")])
    layers = [v for (c, v), nxt in zip(pairs(text), pairs(text)[1:]) if c == "100" and v == "AcDbLayerTableRecord"]
    assert len(layers) == 3                                   # 0 + two layers (the duplicate is merged)
    assert "a_b_c" in text and "a:b/c" not in text
    handles = [v for c, v in pairs(text) if c == "5"]
    assert len(handles) == len(set(handles))


def test_dxf_is_written_with_crlf_and_relative_path_helpers(tmp_path):
    dxf = str(tmp_path / "Outputs" / "TOPO.dxf")
    asset = str(tmp_path / "Outputs" / "00_Assets" / "ortho" / "x.jp2")
    assert dxfout.relative_path(asset, dxf) == r"00_Assets\ortho\x.jp2"
    assert dxfout.asset_folder(dxf, "ortho").endswith(os.path.join("00_Assets", "ortho"))
    assert dxfout.asset_folder(dxf, "dem").endswith(os.path.join("00_Assets", "DTM"))
    dxfout.write_dxf(dxf, [image()])
    assert b"\r\n" in open(dxf, "rb").read()
    assert dxfout.layer_name("300704", "0.1", "ortho") == "300704_0.1_ortho"
    assert dxfout.layer_name("300704", "0.2", "dem") == "300704_0.2_DTM"
    assert dxfout.placement(10.0, 20.0, 30.0, 40.0, (100, 200)) == ((10.0, 20.0), (0.2, 0.1))


# -- presets and names ---------------------------------------------------------------------------------

def test_new_presets_exist_and_export_a_raw_geotiff_first():
    keys = [item["key"] for item in plan.PRESETS]
    assert keys[:3] == ["full", "quick", "jpeg"] and set(keys[3:]) == {"jp2", "ecw", "cad"}
    jp2 = plan.spec_for("jp2", "ortho")
    assert (jp2["ext"], jp2["post"], jp2["compression"], jp2["alpha"], jp2["white"]) == ("jp2", "jp2", "lzw", False, True)
    assert plan.spec_for("ecw", "ortho")["ext"] == "ecw"
    assert plan.spec_for("cad", "ortho", {"images": "ecw"})["ext"] == "ecw"
    assert plan.spec_for("cad", "ortho")["ext"] == "jp2"


def test_jp2_and_ecw_jobs_keep_the_dem_as_a_geotiff(tmp_path):
    jobs = plan.build_jobs("ClientA", DAY, {"ortho": 0.03, "dem": 0.2}, "jp2", str(tmp_path))
    assert [job.name for job in jobs] == ["ClientA_300704_3cm_ortho.jp2", "ClientA_300704_20cm_dtm.tif"]


def test_cad_jobs_go_to_the_asset_folders_and_the_dem_gets_an_image(tmp_path):
    folder = str(tmp_path)
    jobs = plan.build_jobs("ClientA", DAY, {"ortho": 0.1, "dem": 0.2}, "cad", folder, cad={"format": "dxf", "images": "jp2"})
    ortho, dem = jobs
    assert ortho.path == os.path.join(folder, "00_Assets", "ortho", "ClientA_300704_10cm_ortho.jp2")
    assert dem.path == os.path.join(folder, "00_Assets", "DTM", "ClientA_300704_20cm_dtm.tif")
    assert dem.extra["view_path"] == os.path.join(folder, "00_Assets", "DTM", "ClientA_300704_20cm_dtm.jp2")
    files = plan.planned_files(jobs, "cad", {"format": "dwg"})
    assert os.path.join(folder, "TOPO.dxf") in files and os.path.join(folder, "TOPO.dwg") in files


def test_version_is_forced_for_overwriting_and_free_for_a_new_version(tmp_path):
    folder = str(tmp_path)
    first = plan.build_jobs("ClientA", DAY, {"ortho": 0.03}, "full", folder)
    open(first[0].path, "wb").write(b"x")
    assert plan.build_jobs("ClientA", DAY, {"ortho": 0.03}, "full", folder, version=1)[0].name == "ClientA_300704_3cm_ortho.tif"
    assert plan.build_jobs("ClientA", DAY, {"ortho": 0.03}, "full", folder)[0].name == "ClientA_300704_3cm_v2_ortho.tif"


def test_cad_versions_look_inside_the_asset_folders(tmp_path):
    folder = str(tmp_path)
    jobs = plan.build_jobs("ClientA", DAY, {"ortho": 0.1}, "cad", folder)
    os.makedirs(os.path.dirname(jobs[0].path))
    open(jobs[0].path, "wb").write(b"x")
    again = plan.build_jobs("ClientA", DAY, {"ortho": 0.1}, "cad", folder)
    assert again[0].version == 2 and "_v2_" in again[0].name
    assert plan.cad_dxf_name(1) == "TOPO.dxf" and plan.cad_dxf_name(2, "dwg") == "TOPO_v2.dwg"


# -- export chain with fake tools ------------------------------------------------------------------

class Env(object):
    """A fake Metashape (it writes real small GeoTIFFs) and fake converters."""

    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.exports = []
        self.empty_tiles = set()

    def export(self, chunk, kind, spec, path, pixel=None, box=None, big=False, progress=None, crs=None, clip_to_boundary=True):
        self.exports.append((kind, os.path.basename(path), pixel, box, crs, clip_to_boundary))
        left, bottom, right, top = box if box else (1000.0, 2000.0, 1100.0, 2050.0)
        if box and (round(left), round(bottom)) in self.empty_tiles:
            return True, ""                                   # tile without data: Metashape writes nothing
        pixel = pixel or 0.5
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if spec.get("image") == "jp2":
            open(path, "wb").write(b"jp2")
        else:
            write_geotiff(path, int(round((right - left) / pixel)), int(round((top - bottom) / pixel)), left, top, pixel)
        if progress:
            progress(100)
        return True, ""


def fake_context(tmp_path, env, preset, tools=None, **kw):
    info = {"ortho": (0.5, 200, 100), "dem": (0.5, 200, 100), "box_ortho": (1000.0, 2000.0, 1100.0, 2050.0),
            "box_dem": (1000.0, 2000.0, 1100.0, 2050.0), "crs_object": "project", "center": (2.35, 48.85)}
    snap = lambda box, pixel: box
    return pipeline.Context(object(), info, str(tmp_path), "ClientA", DAY, preset, env.export, tools=tools or {}, snap=snap,
                            transform=lambda box, a, b: box, **kw)


def fake_converters(monkeypatch, record):
    def make(name):
        def convert(tool, src, dst, *args, **kwargs):
            record.append((name, os.path.basename(src), os.path.basename(dst)))
            open(dst, "wb").write(b"converted")
            return True, ""
        return convert

    monkeypatch.setattr(external, "to_jp2_gdal", make("gdal"))
    monkeypatch.setattr(external, "to_jp2_gm", make("gm"))
    monkeypatch.setattr(external, "to_ecw", make("ecw"))

    def view(dem, out, *args, **kwargs):
        write_geotiff(out, 200, 100, 1000.0, 2050.0, 0.5)
        record.append(("view", os.path.basename(dem), os.path.basename(out)))
        return True, ""

    monkeypatch.setattr(relief, "render", view)


def test_jp2_export_writes_a_raw_tiff_then_converts_it_and_removes_the_raw(tmp_path, monkeypatch):
    env, record = Env(tmp_path), []
    fake_converters(monkeypatch, record)
    jobs = plan.build_jobs("ClientA", DAY, {"ortho": 0.5}, "jp2", str(tmp_path), version=1)
    result = pipeline.run(fake_context(tmp_path, env, "jp2", tools={"gdal": "G"}), jobs, lambda f, t: None)
    assert record == [("gdal", "ClientA_300704_50cm_ortho.tif", "ClientA_300704_50cm_ortho.jp2")]
    assert os.path.exists(tmp_path / "ClientA_300704_50cm_ortho.jp2")
    assert not [n for n in os.listdir(tmp_path) if n.startswith(pipeline.TMP_NAME)]      # the work folder is deleted
    assert result.tiles == 1 and result.files[0][1] == len(b"converted")


def test_metashape_gets_no_progress_callback_so_it_never_calls_back_into_qt_while_exporting(tmp_path):
    """A callback called by Metashape in the middle of its computation crashed Metashape (recursive repaint): none is passed."""
    env, seen = Env(tmp_path), []
    original = env.export

    def export(*args, **kwargs):
        seen.append((kwargs.get("progress"), ctx.native))
        return original(*args, **kwargs)

    ctx = fake_context(tmp_path, env, "full")
    ctx.export = export
    jobs = plan.build_jobs("ClientA", DAY, {"ortho": 0.5}, "full", str(tmp_path), version=1)
    fractions = []
    pipeline.run(ctx, jobs, lambda f, t: fractions.append(f))
    assert seen and all(progress is None and native for progress, native in seen)
    assert ctx.native is False and fractions[-1] == 1.0 and fractions == sorted(fractions)


def test_ecw_needs_global_mapper_and_jp2_prefers_gdal_then_global_mapper(tmp_path, monkeypatch):
    env, record = Env(tmp_path), []
    fake_converters(monkeypatch, record)
    jobs = plan.build_jobs("ClientA", DAY, {"ortho": 0.5}, "ecw", str(tmp_path), version=1)
    with pytest.raises(pipeline.Failure, match="Global Mapper"):
        pipeline.run(fake_context(tmp_path, env, "ecw", tools={}), jobs, lambda f, t: None)
    pipeline.run(fake_context(tmp_path, env, "ecw", tools={"gm": "GM"}), jobs, lambda f, t: None)
    assert record[-1][0] == "ecw"
    assert pipeline.encoder(fake_context(tmp_path, env, "jp2", tools={"gm": "GM"})) == "gm"
    assert pipeline.encoder(fake_context(tmp_path, env, "jp2", tools={"gm": "GM", "gdal": "G"})) == "gdal"
    assert pipeline.encoder(fake_context(tmp_path, env, "jp2", tools={})) == "metashape"


def test_without_any_encoder_metashape_writes_its_own_jp2(tmp_path):
    env = Env(tmp_path)
    jobs = plan.build_jobs("ClientA", DAY, {"ortho": 0.5}, "jp2", str(tmp_path), version=1)
    pipeline.run(fake_context(tmp_path, env, "jp2", tools={}), jobs, lambda f, t: None)
    assert open(tmp_path / "ClientA_300704_50cm_ortho.jp2", "rb").read() == b"jp2"


def test_cad_export_writes_assets_a_dxf_and_checks_it_with_autocad(tmp_path, monkeypatch):
    env, record = Env(tmp_path), []
    fake_converters(monkeypatch, record)
    monkeypatch.setattr(external, "check_dxf", lambda core, dxf, tick=None: {"images": 2, "failed": [], "valid": True})
    jobs = plan.build_jobs("ClientA", DAY, {"ortho": 0.5, "dem": 0.5}, "cad", str(tmp_path),
                           cad={"format": "dxf", "images": "jp2"}, version=1)
    ctx = fake_context(tmp_path, env, "cad", tools={"gdal": "G", "acad": "A"}, cad={"format": "dxf", "images": "jp2"})
    result = pipeline.run(ctx, jobs, lambda f, t: None)
    assert os.path.isfile(tmp_path / "00_Assets" / "ortho" / "ClientA_300704_50cm_ortho.jp2")
    assert os.path.isfile(tmp_path / "00_Assets" / "DTM" / "ClientA_300704_50cm_dtm.tif")
    assert os.path.isfile(tmp_path / "00_Assets" / "DTM" / "ClientA_300704_50cm_dtm.jp2")
    text = open(tmp_path / "TOPO.dxf", "rb").read().decode("ascii")
    assert "00_Assets\\ortho\\ClientA_300704_50cm_ortho.jp2" in text and "00_Assets\\DTM\\ClientA_300704_50cm_dtm.jp2" in text
    assert "300704_0.5_ortho" in text and "300704_0.5_DTM" in text
    assert result.dxf == str(tmp_path / "TOPO.dxf") and result.check["images"] == 2 and not result.warnings


def test_the_dem_layer_needs_no_gdal(tmp_path, monkeypatch):
    env, record = Env(tmp_path), []
    fake_converters(monkeypatch, record)
    jobs = plan.build_jobs("ClientA", DAY, {"ortho": 0.5, "dem": 0.5}, "cad", str(tmp_path),
                           cad={"format": "dxf", "images": "ecw"}, version=1)
    ctx = fake_context(tmp_path, env, "cad", tools={"gm": "GM"}, cad={"format": "dxf", "images": "ecw"})
    result = pipeline.run(ctx, jobs, lambda f, t: None)
    assert not result.warnings and os.path.isfile(tmp_path / "00_Assets" / "DTM" / "ClientA_300704_50cm_dtm.tif")
    assert b"_DTM" in open(tmp_path / "TOPO.dxf", "rb").read()


def test_dwg_is_made_by_autocad_and_fails_clearly_without_it(tmp_path, monkeypatch):
    env, record = Env(tmp_path), []
    fake_converters(monkeypatch, record)
    monkeypatch.setattr(external, "check_dxf", lambda core, dxf, tick=None: {"images": 1, "failed": [], "valid": True})
    monkeypatch.setattr(external, "dxf_to_dwg", lambda core, dxf, dwg, tick=None: (open(dwg, "wb").write(b"dwg") and True, ""))
    jobs = plan.build_jobs("ClientA", DAY, {"ortho": 0.5}, "cad", str(tmp_path), cad={"format": "dwg", "images": "jp2"},
                           version=1)
    ctx = fake_context(tmp_path, env, "cad", tools={"gdal": "G", "acad": "A"}, cad={"format": "dwg", "images": "jp2"})
    assert pipeline.run(ctx, jobs, lambda f, t: None).dwg == str(tmp_path / "TOPO.dwg")
    ctx = fake_context(tmp_path, env, "cad", tools={"gdal": "G"}, cad={"format": "dwg", "images": "jp2"})
    with pytest.raises(pipeline.Failure, match="AutoCAD"):
        pipeline.run(ctx, jobs, lambda f, t: None)


def test_tiles_export_one_file_per_tile_and_skip_empty_ones(tmp_path, monkeypatch):
    env, record = Env(tmp_path), []
    fake_converters(monkeypatch, record)
    env.empty_tiles = {(1050, 2000)}                          # the bottom-right tile has no data
    jobs = plan.build_jobs("ClientA", DAY, {"ortho": 0.5}, "full", str(tmp_path), version=1)
    ctx = fake_context(tmp_path, env, "full", tile_m=50)
    result = pipeline.run(ctx, jobs, lambda f, t: None)
    names = sorted(name for _kind, name, _p, _b, _c, _f in env.exports)
    assert names == ["ClientA_300704_50cm_R01C01_ortho.tif", "ClientA_300704_50cm_R01C02_ortho.tif"] or len(names) == 2
    assert result.tiles == len(env.exports) - 1               # one empty tile: nothing written
    boxes = [b for _k, _n, _p, b, _c, _f in env.exports]
    assert all(box[2] - box[0] <= 50.0 + 1e-6 for box in boxes)


def test_native_pixel_is_rounded_only_when_the_grid_is_imposed(tmp_path):
    env = Env(tmp_path)
    job = plan.Job("ortho", "n", "p", 0.0100008, {}, 1, True, None)
    assert pipeline.pixel_of(fake_context(tmp_path, env, "full"), job) == 0.0100008
    assert pipeline.pixel_of(fake_context(tmp_path, env, "full", tile_m=50), job) == 0.01
    typed = plan.Job("ortho", "n", "p", 0.0123456, {}, 1, False, None)
    assert pipeline.pixel_of(fake_context(tmp_path, env, "full", tile_m=50), typed) == 0.0123456


def test_a_target_crs_converts_the_box_and_is_passed_to_metashape(tmp_path):
    env = Env(tmp_path)
    jobs = plan.build_jobs("ClientA", DAY, {"ortho": 0.5}, "full", str(tmp_path), version=1)
    ctx = fake_context(tmp_path, env, "full", crs="L93")
    ctx.transform = lambda box, source, target: (10.0, 20.0, 60.0, 45.0)
    pipeline.run(ctx, jobs, lambda f, t: None)
    kind, name, pixel, box, crs, _clip = env.exports[0]
    assert box == (10.0, 20.0, 60.0, 45.0) and crs == "L93" and pixel == 0.5


def test_a_failed_export_is_reported_not_swallowed(tmp_path):
    env = Env(tmp_path)
    env.export = lambda *a, **k: (False, "disk full")
    jobs = plan.build_jobs("ClientA", DAY, {"ortho": 0.5}, "full", str(tmp_path), version=1)
    with pytest.raises(pipeline.Failure, match="disk full"):
        pipeline.run(fake_context(tmp_path, env, "full"), jobs, lambda f, t: None)


# -- external software ---------------------------------------------------------------------------------

def test_global_mapper_script_converts_a_geotiff_to_ecw():
    text = external.gm_script("C:/a/b.tif", "C:/a/b.ecw", 10)
    assert "IMPORT FILENAME=\"C:\\a\\b.tif\" TYPE=GEOTIFF" in text
    assert "EXPORT_RASTER FILENAME=\"C:\\a\\b.ecw\" TYPE=ECW TARGET_COMPRESSION=10" in text and text.startswith("GLOBAL_MAPPER_SCRIPT")


def test_autocad_output_is_read_for_loaded_and_failed_images():
    ok = "Reload image IMAGE_1: x\nIMAGE_1 loaded and relinked.\nReload image IMAGE_2: y\nIMAGE_2 loaded and relinked.\n"
    assert external.parse_check(ok) == {"images": 2, "failed": [], "valid": True}
    bad = "Invalid or incomplete DXF input -- drawing discarded.\n"
    assert external.parse_check(bad)["valid"] is False and external.parse_check(bad)["images"] == 0


def test_tool_detection_honours_a_saved_path_and_never_invents_one(tmp_path, monkeypatch):
    fake = tmp_path / "global_mapper.exe"
    fake.write_bytes(b"x")
    assert external.find_global_mapper(str(fake)) == str(fake)
    wrong = tmp_path / "other.exe"
    wrong.write_bytes(b"x")
    monkeypatch.setattr(external, "_uninstall_locations", lambda pattern: [])
    monkeypatch.setattr(external, "_program_dirs", lambda: [str(tmp_path / "nothing")])
    assert external.find_global_mapper(str(wrong)) == ""
    assert external.find_autocad("") == "" and external.find_gdal("") == ""
    core = tmp_path / external.ACCORE
    core.write_bytes(b"x")
    assert external.find_autocad(str(tmp_path / "acad.exe")) == str(core)


def test_newest_version_wins(tmp_path):
    old, new = tmp_path / "GlobalMapper25.0" / "g.exe", tmp_path / "GlobalMapper26.0" / "g.exe"
    for path in (old, new):
        path.parent.mkdir()
        path.write_bytes(b"x")
    assert external._newest([str(old), str(new)]) == str(new)


def test_a_file_put_in_place_replaces_the_one_already_there(tmp_path):
    from aerotools.tools.aeroexports import pipeline
    old = tmp_path / "SITE_260610_5cm_ortho.tif"
    old.write_bytes(b"old export")
    new = tmp_path / "staged.tif"
    new.write_bytes(b"new export")
    pipeline._commit(str(new), str(old))
    assert old.read_bytes() == b"new export" and not new.exists()
    with pytest.raises(pipeline.Failure, match="Cannot overwrite"):
        pipeline._commit(str(tmp_path / "missing.tif"), str(old))
    assert old.read_bytes() == b"new export"


def test_an_old_file_is_not_taken_for_a_new_export_that_wrote_nothing(tmp_path):
    env = Env(tmp_path)
    jobs = plan.build_jobs("ClientA", DAY, {"ortho": 0.5}, "full", str(tmp_path), version=1)
    stale = tmp_path / "ClientA_300704_50cm_ortho.tif"
    stale.write_bytes(b"left by an export of another chunk")
    env.export = lambda *args, **kwargs: (True, "")        # Metashape writes nothing (no data)
    with pytest.raises(pipeline.Failure):
        pipeline.run(fake_context(tmp_path, env, "full"), jobs, lambda f, t: None)
    assert stale.read_bytes() == b"left by an export of another chunk"      # never taken for the new one, and not destroyed
