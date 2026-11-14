"""Aero-Exports: presets, list of files, export call (without Metashape or Qt)."""
import datetime
import os
import types

import pytest

from aerotools import msapi
from aerotools.tools.aeroexports import plan

DAY = datetime.date(2030, 7, 4)


def test_default_preset_is_lossless_geotiff():
    ortho = plan.spec_for("full", "ortho")
    assert (ortho["ext"], ortho["image"], ortho["compression"], ortho["alpha"]) == ("tif", "tiff", "lzw", True)
    assert plan.spec_for("nope", "dem") == plan.spec_for("full", "dem")          # unknown key: the first preset


def test_every_preset_keeps_the_dem_lossless_and_never_jpeg_compresses_it():
    for item in plan.PRESETS:
        dem = item["dem"]
        assert dem["image"] == "tiff" and dem["compression"] in ("deflate", "lzw", "none")
        assert dem["world"] is False


def test_quick_view_is_jpeg_compressed_on_white_without_alpha():
    ortho = plan.spec_for("quick", "ortho")
    assert ortho["compression"] == "jpeg" and ortho["white"] and not ortho["alpha"]


def test_build_jobs_names_ortho_and_dem_in_the_outputs_folder(tmp_path):
    jobs = plan.build_jobs("SITE_A", DAY, {"ortho": 0.03, "dem": 0.1}, "full", str(tmp_path))
    assert [job.name for job in jobs] == ["SITE_A_300704_3cm_ortho.tif", "SITE_A_300704_10cm_dtm.tif"]
    assert jobs[0].path == str(tmp_path / "SITE_A_300704_3cm_ortho.tif")
    assert [job.kind for job in jobs] == ["ortho", "dem"] and {job.version for job in jobs} == {1}


def test_build_jobs_name_the_dem_dtm(tmp_path):
    jobs = plan.build_jobs("ClientA", DAY, {"dem": 0.05}, "full", str(tmp_path))
    assert [job.name for job in jobs] == ["ClientA_300704_5cm_dtm.tif"]


def test_build_jobs_only_lists_what_is_wanted(tmp_path):
    assert plan.build_jobs("ClientA", DAY, {}, "full", str(tmp_path)) == []
    assert plan.build_jobs("ClientA", DAY, {"ortho": 0}, "full", str(tmp_path)) == []
    assert [job.kind for job in plan.build_jobs("ClientA", DAY, {"ortho": 0.03}, "full", str(tmp_path))] == ["ortho"]


def test_a_second_export_gets_a_version_shared_by_ortho_and_dem(tmp_path):
    (tmp_path / "ClientA_300704_3cm_ortho.tif").write_text("x")           # only the orthomosaic already exists
    jobs = plan.build_jobs("ClientA", DAY, {"ortho": 0.03, "dem": 0.1}, "full", str(tmp_path))
    assert [job.name for job in jobs] == ["ClientA_300704_3cm_v2_ortho.tif", "ClientA_300704_10cm_v2_dtm.tif"]
    assert {job.version for job in jobs} == {2}


def test_the_world_file_of_a_jpeg_counts_as_an_existing_file(tmp_path):
    (tmp_path / "ClientA_300704_3cm_ortho.jgw").write_text("0")           # an orphan .jgw is enough to change the version
    jobs = plan.build_jobs("ClientA", DAY, {"ortho": 0.03}, "jpeg", str(tmp_path))
    assert jobs[0].name == "ClientA_300704_3cm_v2_ortho.jpg"


def test_aux_files_of_a_jpeg_are_the_world_file_and_the_prj():
    assert plan.aux_names("ClientA_300704_3cm_ortho.jpg", plan.spec_for("jpeg", "ortho")) == [
        "ClientA_300704_3cm_ortho.jgw", "ClientA_300704_3cm_ortho.prj"]
    assert plan.aux_names("ClientA_300704_3cm_ortho.tif", plan.spec_for("full", "ortho")) == []


def test_native_jobs_are_flagged(tmp_path):
    jobs = plan.build_jobs("ClientA", DAY, {"ortho": 0.0313, "dem": 0.1}, "full", str(tmp_path), native={"ortho"})
    assert [(job.kind, job.native) for job in jobs] == [("ortho", True), ("dem", False)]


def test_bigtiff_threshold():
    assert not plan.needs_bigtiff(10000, 10000)                      # 400 Mo
    assert plan.needs_bigtiff(30000, 30000)                          # 3.6 GB raw in RGBA
    assert plan.needs_bigtiff(30000, 30000, bands=1, bytes_per_sample=4)


# -- call of Metashape (fake module) --------------------------------------------------------------

class FakeCompression(object):
    TiffCompressionNone, TiffCompressionLZW, TiffCompressionJPEG = 0, 1, 2
    TiffCompressionPackbits, TiffCompressionDeflate = 3, 4


class FakeChunk(object):
    def __init__(self, error=None):
        self.calls, self.error = [], error

    def exportRaster(self, **kwargs):
        if self.error:
            raise self.error
        self.calls.append(kwargs)


@pytest.fixture
def meta(ms):
    ms.ImageCompression = FakeCompression
    ms.ElevationData, ms.OrthomosaicData = "dem-data", "ortho-data"
    ms.ImageFormatJPEG, ms.ImageFormatTIFF = "jpeg", "tiff"
    ms.Vector = lambda values: types.SimpleNamespace(x=values[0], y=values[1])
    ms.BBox = lambda low, high: types.SimpleNamespace(min=low, max=high)
    return ms


BOX = (650000.52, 6860120.37, 650101.49, 6860000.1)          # left, bottom, right, top (the order does not matter here)


def test_export_raster_passes_the_preset_to_metashape(meta):
    chunk = FakeChunk()
    spec = plan.spec_for("quick", "ortho")
    ok, error = msapi.export_raster(chunk, "ortho", spec, "C:/x/a.tif", 0.03, BOX, big=True, progress=print)
    assert ok and error == ""
    call = chunk.calls[0]
    assert call["source_data"] == "ortho-data" and call["image_format"] == "tiff"
    assert call["resolution_x"] == call["resolution_y"] == 0.03 and "resolution" not in call
    assert call["white_background"] is True and call["save_alpha"] is False
    compression = call["image_compression"]
    assert compression.tiff_compression == FakeCompression.TiffCompressionJPEG and compression.jpeg_quality == 85
    assert compression.tiff_big is True and compression.tiff_tiled is True and compression.tiff_overviews is True
    assert call["progress"] is print


def test_export_raster_with_a_pixel_snaps_the_region_to_the_pixel_grid(meta):
    chunk = FakeChunk()
    msapi.export_raster(chunk, "ortho", plan.spec_for("full", "ortho"), "C:/x/a.tif", 0.5, (1.1, 2.2, 9.9, 7.1))
    region = chunk.calls[0]["region"]
    assert (region.min.x, region.min.y, region.max.x, region.max.y) == (1.0, 2.0, 10.0, 7.5)


def test_snapped_box_is_exact_on_already_aligned_edges():
    assert msapi.snapped_box((1.0, 2.0, 10.0, 7.5), 0.5) == (1.0, 2.0, 10.0, 7.5)
    left, bottom, right, top = msapi.snapped_box((650000.52, 6860000.37, 650101.49, 6860120.37), 0.03)
    assert abs(left / 0.03 - round(left / 0.03)) < 1e-6 and abs(top / 0.03 - round(top / 0.03)) < 1e-6
    assert left <= 650000.52 and right >= 650101.49 and bottom <= 6860000.37 and top >= 6860120.37


def test_export_raster_without_a_pixel_keeps_the_metashape_grid(meta):
    chunk = FakeChunk()
    msapi.export_raster(chunk, "dem", plan.spec_for("full", "dem"), "C:/x/a.tif")
    call = chunk.calls[0]
    assert call["source_data"] == "dem-data"
    assert not {"resolution", "resolution_x", "resolution_y", "region"} & set(call)
    assert call["image_compression"].tiff_compression == FakeCompression.TiffCompressionDeflate
    assert "progress" not in call


def test_export_raster_always_says_whether_to_clip_to_the_boundary_shapes(meta):
    chunk = FakeChunk()
    msapi.export_raster(chunk, "dem", plan.spec_for("full", "dem"), "C:/x/a.tif")
    msapi.export_raster(chunk, "dem", plan.spec_for("full", "dem"), "C:/x/b.tif", clip_to_boundary=False)
    msapi.export_raster(chunk, "ortho", plan.spec_for("full", "ortho"), "C:/x/c.tif", 0.5, (1.0, 2.0, 9.0, 7.0), clip_to_boundary=True)
    assert [call["clip_to_boundary"] for call in chunk.calls] == [True, False, True]


def test_export_raster_jpeg_asks_for_a_world_file(meta):
    chunk = FakeChunk()
    msapi.export_raster(chunk, "ortho", plan.spec_for("jpeg", "ortho"), "C:/x/a.jpg")
    call = chunk.calls[0]
    assert call["image_format"] == "jpeg" and call["save_world"] is True


def test_export_raster_reports_a_failure_instead_of_raising(meta):
    ok, error = msapi.export_raster(FakeChunk(RuntimeError("disk full")), "ortho", plan.spec_for("full", "ortho"),
                                    "C:/x/a.tif")
    assert not ok and "disk full" in error


def test_raster_info_reads_what_exists(meta):
    ortho = types.SimpleNamespace(resolution=0.03, width=100, height=50,
                                  projection=types.SimpleNamespace(crs=types.SimpleNamespace(name="RGF93 / Lambert-93")))
    photo = lambda text: types.SimpleNamespace(photo=types.SimpleNamespace(meta={"Exif/DateTimeOriginal": text}))
    ortho.left, ortho.bottom, ortho.right, ortho.top = 1.0, 2.0, 3.0, 4.0
    chunk = types.SimpleNamespace(orthomosaic=ortho, elevation=None, crs=None,
                                  cameras=[photo("2030:07:04 09:00:00"), types.SimpleNamespace(photo=None)])
    info = msapi.raster_info(chunk)
    assert info["ortho"] == (0.03, 100, 50) and info["dem"] is None
    assert info["box_ortho"] == (1.0, 2.0, 3.0, 4.0) and info["box_dem"] is None
    assert info["crs"] == "RGF93 / Lambert-93" and info["dates"] == ["2030:07:04 09:00:00"]


def test_raster_info_without_a_chunk_is_empty(meta, monkeypatch):
    monkeypatch.setattr(msapi, "chunk", lambda: None)
    assert msapi.raster_info() == {"ortho": None, "dem": None, "box_ortho": None, "box_dem": None, "crs": "",
                                   "dates": [], "crs_object": None, "crs_ortho": None, "crs_dem": None, "center": None,
                                   "dem_default": None, "ortho_default": None, "n_dems": 0, "n_orthos": 0, "boundaries": None}


def _raster(key, width, height, resolution):
    return types.SimpleNamespace(key=key, label="r%d" % key, width=width, height=height, resolution=resolution,
                                 left=0.0, bottom=0.0, right=1.0, top=1.0, projection=types.SimpleNamespace(crs=None))


def _shape(kind):
    return types.SimpleNamespace(boundary_type="Metashape.Shape.BoundaryType." + kind)


def test_raster_info_names_the_default_rasters_and_counts_the_others_and_the_boundary_shapes(meta):
    default = _raster(3, 5200, 6400, 0.0125)
    chunk = types.SimpleNamespace(elevation=default, elevations=[_raster(1, 10, 10, 1.0), default, _raster(5, 10, 10, 1.0)],
                                  orthomosaic=_raster(0, 2019, 2395, 0.05), orthomosaics=[_raster(0, 2019, 2395, 0.05), _raster(1, 9, 9, 0.01)],
                                  shapes=[_shape("NoBoundary"), _shape("OuterBoundary"), _shape("InnerBoundary")], crs=None, cameras=[])
    info = msapi.raster_info(chunk)
    assert info["dem_default"] == {"key": 3, "label": "r3", "width": 5200, "height": 6400, "resolution": 0.0125}
    assert info["ortho_default"]["key"] == 0 and info["n_dems"] == 3 and info["n_orthos"] == 2
    assert info["boundaries"] == 2                                                  # NoBoundary shapes do not cut anything
    chunk.shapes = []
    assert msapi.raster_info(chunk)["boundaries"] == 0
    del chunk.shapes
    assert msapi.raster_info(chunk)["boundaries"] is None                           # unreadable: unknown, not zero


def test_raster_signature_changes_with_the_chunk_the_default_rasters_and_the_shapes(meta):
    def make(key, dem_key=3, shapes=1):
        return types.SimpleNamespace(key=key, elevation=_raster(dem_key, 100, 100, 0.1), orthomosaic=_raster(0, 50, 50, 0.2),
                                     shapes=[_shape("OuterBoundary")] * shapes)
    base = msapi.raster_signature(make(1))
    assert base == msapi.raster_signature(make(1)) and msapi.raster_signature(None) is None
    assert msapi.raster_signature(make(2)) != base                                  # another chunk
    assert msapi.raster_signature(make(1, dem_key=4)) != base                       # another default DEM (double-click in the Workspace)
    assert msapi.raster_signature(make(1, shapes=2)) != base                        # a shape was added


# -- CRS change: extent ---------------------------------------------------------------------

COMPOUND = ('COMPD_CS["RGF93 v1 / CC42 + RAC09",PROJCS["RGF93 v1 / CC42",GEOGCS["RGF93 v1",DATUM["D",SPHEROID["GRS 1980",6378137,298.257]]],'
            'PROJECTION["Lambert_Conformal_Conic_2SP"],UNIT["metre",1]],VERT_CS["RAC09",VERT_DATUM["IGN78",2005]]]')


class FakeCrs(object):
    def __init__(self, wkt, ok=True):
        self.wkt, self.ok = wkt, ok


def test_horizontal_part_cuts_the_vertical_system_out(meta):
    meta.CoordinateSystem = FakeCrs
    flat = msapi.horizontal_part(FakeCrs(COMPOUND))
    assert flat.wkt.startswith('PROJCS["RGF93 v1 / CC42"') and flat.wkt.endswith("UNIT[\"metre\",1]]") and "VERT_CS" not in flat.wkt
    plain = FakeCrs('PROJCS["x"]')
    assert msapi.horizontal_part(plain) is plain


def test_transform_box_falls_back_to_the_horizontal_part_when_the_geoid_fails(meta):
    meta.CoordinateSystem = FakeCrs
    meta.Vector = lambda values: tuple(values)
    calls = []

    def transform(point, source, target):
        calls.append((source.wkt.startswith("COMPD_CS"), target.wkt.startswith("COMPD_CS")))
        if source.wkt.startswith("COMPD_CS") or target.wkt.startswith("COMPD_CS"):
            raise RuntimeError("geoid grid not found")
        return (point[0] + 1.0, point[1] + 2.0, 0.0)

    meta.CoordinateSystem.transform = staticmethod(transform)
    box = msapi.transform_box((0.0, 0.0, 10.0, 5.0), FakeCrs(COMPOUND), FakeCrs(COMPOUND.replace("RAC09", "RAC23")))
    assert box == (1.0, 2.0, 11.0, 7.0)
    assert msapi.transform_box.last_error == "geoid grid not found"


def test_transform_box_gives_up_with_a_reason(meta):
    meta.CoordinateSystem = FakeCrs
    meta.Vector = lambda values: tuple(values)

    def refuse(point, source, target):
        raise RuntimeError("no path")

    meta.CoordinateSystem.transform = staticmethod(refuse)
    assert msapi.transform_box((0.0, 0.0, 1.0, 1.0), FakeCrs('PROJCS["a"]'), FakeCrs('PROJCS["b"]')) is None
    assert msapi.transform_box.last_error == "no path"


def test_every_kit_name_the_export_windows_ask_for_exists():
    """The windows are built from the UI kit: a name they ask `uikit` for must exist there (a revert once removed a shared widget and
    the window no longer started). The names are read from the sources."""
    import re
    root = os.path.join(os.path.dirname(__file__), "..", "aerotools")
    kit = open(os.path.join(root, "uikit", "__init__.py"), encoding="utf-8").read()
    known = set(re.findall(r'"(\w+)": "\w+"', kit))
    wanted = set()
    for name in ("dialog.py", "previewwin.py", "widgets.py"):
        source = open(os.path.join(root, "tools", "aeroexports", name), encoding="utf-8").read()
        wanted |= set(re.findall(r"(?<![\w.])uikit\.([A-Za-z_]\w*)", source))
    missing = [name for name in sorted(wanted) if name not in known]
    assert missing == []


# -- tiled exports and the "already exists" guard --------------------------------------------------------

def _touch_all(folder, names):
    os.makedirs(folder, exist_ok=True)
    for name in names:
        open(os.path.join(folder, name), "wb").write(b"x")


def test_tiles_already_delivered_are_found_by_the_overwrite_guard(tmp_path):
    folder = str(tmp_path)
    _touch_all(folder, ["ClientA_300704_50cm_R01C01_ortho.tif", "ClientA_300704_50cm_R01C02_ortho.tif",
                        "ClientA_300704_50cm_R01C01_dtm.tif"])
    jobs = plan.build_jobs("ClientA", DAY, {"ortho": 0.5}, "full", folder, version=1)
    assert plan.existing_files(jobs, "full") == []                                      # without tiling: other names, nothing to overwrite
    found = plan.existing_files(jobs, "full", tiled=True)
    assert [os.path.basename(p) for p in found] == ["ClientA_300704_50cm_R01C01_ortho.tif",
                                                    "ClientA_300704_50cm_R01C02_ortho.tif"]


def test_a_new_tiled_version_does_not_reuse_a_version_delivered_as_tiles(tmp_path):
    folder = str(tmp_path)
    _touch_all(folder, ["ClientA_300704_50cm_R01C01_ortho.tif"])
    assert plan.build_jobs("ClientA", DAY, {"ortho": 0.5}, "full", folder, tiled=True)[0].name == "ClientA_300704_50cm_v2_ortho.tif"
    assert plan.build_jobs("ClientA", DAY, {"ortho": 0.5}, "full", folder)[0].name == "ClientA_300704_50cm_ortho.tif"
    _touch_all(folder, ["ClientA_300704_50cm_v2_R02C03_ortho.tif"])
    assert plan.build_jobs("ClientA", DAY, {"ortho": 0.5}, "full", folder, tiled=True)[0].version == 3


def test_tiles_of_a_cad_export_are_seen_in_the_asset_folders(tmp_path):
    folder = str(tmp_path)
    _touch_all(os.path.join(folder, "00_Assets", "ortho"), ["ClientA_300704_10cm_R01C01_ortho.jp2"])
    jobs = plan.build_jobs("ClientA", DAY, {"ortho": 0.1}, "cad", folder, tiled=True)
    assert jobs[0].version == 2
    first = plan.build_jobs("ClientA", DAY, {"ortho": 0.1}, "cad", folder, version=1)
    assert [os.path.basename(p) for p in plan.existing_files(first, "cad", tiled=True)] == ["ClientA_300704_10cm_R01C01_ortho.jp2"]


def test_untiled_only_changes_real_tile_names():
    assert plan.untiled("ClientA_300704_50cm_R01C02_ortho.tif") == "ClientA_300704_50cm_ortho.tif"
    assert plan.untiled("ClientA_300704_50cm_v2_R101C007_dtm.tif") == "ClientA_300704_50cm_v2_dtm.tif"
    assert plan.untiled("ClientA_300704_50cm_ortho.tif") == "ClientA_300704_50cm_ortho.tif"
    assert plan.untiled("R01C02_notes.txt") == "R01C02_notes.txt"
