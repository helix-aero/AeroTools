"""Fast Export : presets, liste des fichiers, appel d'export (sans Metashape ni Qt)."""
import datetime
import types

import pytest

from aerotools import msapi
from aerotools.tools.fastexport import plan

DAY = datetime.date(2026, 7, 4)


def test_default_preset_is_lossless_geotiff():
    ortho = plan.spec_for("full", "ortho")
    assert (ortho["ext"], ortho["image"], ortho["compression"], ortho["alpha"]) == ("tif", "tiff", "lzw", True)
    assert plan.spec_for("nope", "dem") == plan.spec_for("full", "dem")          # clef inconnue : le premier preset


def test_every_preset_keeps_the_dem_lossless_and_never_jpeg_compresses_it():
    for item in plan.PRESETS:
        dem = item["dem"]
        assert dem["image"] == "tiff" and dem["compression"] in ("deflate", "lzw", "none")
        assert dem["world"] is False


def test_quick_view_is_jpeg_compressed_on_white_without_alpha():
    ortho = plan.spec_for("quick", "ortho")
    assert ortho["compression"] == "jpeg" and ortho["white"] and not ortho["alpha"]


def test_build_jobs_names_ortho_and_dem_in_the_outputs_folder(tmp_path):
    jobs = plan.build_jobs("CITADELLE", DAY, {"ortho": 0.03, "dem": 0.1}, "full", str(tmp_path))
    assert [job.name for job in jobs] == ["CITADELLE_260704_3cm_ortho.tif", "CITADELLE_260704_10cm_mnt.tif"]
    assert jobs[0].path == str(tmp_path / "CITADELLE_260704_3cm_ortho.tif")
    assert [job.kind for job in jobs] == ["ortho", "dem"] and {job.version for job in jobs} == {1}


def test_build_jobs_english_names_use_dtm(tmp_path):
    jobs = plan.build_jobs("EDF", DAY, {"dem": 0.05}, "full", str(tmp_path), lang="en")
    assert [job.name for job in jobs] == ["EDF_260704_5cm_dtm.tif"]


def test_build_jobs_only_lists_what_is_wanted(tmp_path):
    assert plan.build_jobs("EDF", DAY, {}, "full", str(tmp_path)) == []
    assert plan.build_jobs("EDF", DAY, {"ortho": 0}, "full", str(tmp_path)) == []
    assert [job.kind for job in plan.build_jobs("EDF", DAY, {"ortho": 0.03}, "full", str(tmp_path))] == ["ortho"]


def test_a_second_export_gets_a_version_shared_by_ortho_and_dem(tmp_path):
    (tmp_path / "EDF_260704_3cm_ortho.tif").write_text("x")           # seul l'ortho existe deja
    jobs = plan.build_jobs("EDF", DAY, {"ortho": 0.03, "dem": 0.1}, "full", str(tmp_path))
    assert [job.name for job in jobs] == ["EDF_260704_3cm_v2_ortho.tif", "EDF_260704_10cm_v2_mnt.tif"]
    assert {job.version for job in jobs} == {2}


def test_the_world_file_of_a_jpeg_counts_as_an_existing_file(tmp_path):
    (tmp_path / "EDF_260704_3cm_ortho.jgw").write_text("0")           # un .jgw orphelin suffit a changer de version
    jobs = plan.build_jobs("EDF", DAY, {"ortho": 0.03}, "jpeg", str(tmp_path))
    assert jobs[0].name == "EDF_260704_3cm_v2_ortho.jpg"


def test_aux_files_of_a_jpeg_are_the_world_file_and_the_prj():
    assert plan.aux_names("EDF_260704_3cm_ortho.jpg", plan.spec_for("jpeg", "ortho")) == [
        "EDF_260704_3cm_ortho.jgw", "EDF_260704_3cm_ortho.prj"]
    assert plan.aux_names("EDF_260704_3cm_ortho.tif", plan.spec_for("full", "ortho")) == []


def test_native_jobs_are_flagged(tmp_path):
    jobs = plan.build_jobs("EDF", DAY, {"ortho": 0.0313, "dem": 0.1}, "full", str(tmp_path), native={"ortho"})
    assert [(job.kind, job.native) for job in jobs] == [("ortho", True), ("dem", False)]


def test_bigtiff_threshold():
    assert not plan.needs_bigtiff(10000, 10000)                      # 400 Mo
    assert plan.needs_bigtiff(30000, 30000)                          # 3,6 Go brut en RGBA
    assert plan.needs_bigtiff(30000, 30000, bands=1, bytes_per_sample=4)


# -- appel de Metashape (faux module) --------------------------------------------------------------

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


BOX = (2175791.52, 1206800.37, 2175892.49, 1206680.1)          # gauche, bas, droite, haut (peu importe l'ordre ici)


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
    left, bottom, right, top = msapi.snapped_box((2175791.52, 1206680.37, 2175892.49, 1206800.37), 0.03)
    assert abs(left / 0.03 - round(left / 0.03)) < 1e-6 and abs(top / 0.03 - round(top / 0.03)) < 1e-6
    assert left <= 2175791.52 and right >= 2175892.49 and bottom <= 1206680.37 and top >= 1206800.37


def test_export_raster_without_a_pixel_keeps_the_metashape_grid(meta):
    chunk = FakeChunk()
    msapi.export_raster(chunk, "dem", plan.spec_for("full", "dem"), "C:/x/a.tif")
    call = chunk.calls[0]
    assert call["source_data"] == "dem-data"
    assert not {"resolution", "resolution_x", "resolution_y", "region"} & set(call)
    assert call["image_compression"].tiff_compression == FakeCompression.TiffCompressionDeflate
    assert "progress" not in call


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
                                  projection=types.SimpleNamespace(crs=types.SimpleNamespace(name="RGF93 / CC42")))
    photo = lambda text: types.SimpleNamespace(photo=types.SimpleNamespace(meta={"Exif/DateTimeOriginal": text}))
    ortho.left, ortho.bottom, ortho.right, ortho.top = 1.0, 2.0, 3.0, 4.0
    chunk = types.SimpleNamespace(orthomosaic=ortho, elevation=None, crs=None,
                                  cameras=[photo("2026:07:04 09:00:00"), types.SimpleNamespace(photo=None)])
    info = msapi.raster_info(chunk)
    assert info["ortho"] == (0.03, 100, 50) and info["dem"] is None
    assert info["box_ortho"] == (1.0, 2.0, 3.0, 4.0) and info["box_dem"] is None
    assert info["crs"] == "RGF93 / CC42" and info["dates"] == ["2026:07:04 09:00:00"]


def test_raster_info_without_a_chunk_is_empty(meta, monkeypatch):
    monkeypatch.setattr(msapi, "chunk", lambda: None)
    assert msapi.raster_info() == {"ortho": None, "dem": None, "box_ortho": None, "box_dem": None, "crs": "",
                                   "dates": [], "crs_object": None, "center": None}


# -- changement de SCR : emprise ---------------------------------------------------------------------

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


def test_the_crs_list_has_no_geographic_system_by_default():
    from aerotools.tools.fastexport import crs
    shown = [epsg for epsg, _label in crs.crs_choices(9.0, 42.0, "en")]
    assert 2154 in shown and 4326 not in shown and 9782 not in shown
    assert 4326 in [epsg for epsg, _label in crs.crs_choices(projected_only=False)]
