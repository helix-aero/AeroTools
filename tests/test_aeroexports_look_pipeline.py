"""Aero-Exports: the saved look reaches the DEM picture of the export, and the coloured DEM picture next to JPEG 2000 / ECW."""

import os

from aerotools.tools.aeroexports import external, look as looks, pipeline, plan, relief
from test_aeroexports_cad import DAY, Env, fake_context, write_geotiff


def spy(monkeypatch, record):
    """Fake converters and a fake relief.render that notes the look it was given."""
    def make(name):
        def convert(tool, src, dst, *args, **kwargs):
            record.append((name, os.path.basename(src), os.path.basename(dst)))
            open(dst, "wb").write(b"converted")
            return True, ""
        return convert

    monkeypatch.setattr(external, "to_jp2_gdal", make("gdal"))
    monkeypatch.setattr(external, "to_jp2_gm", make("gm"))
    monkeypatch.setattr(external, "to_ecw", make("ecw"))

    def view(dem, out, look=None, **kwargs):
        write_geotiff(out, 200, 100, 1000.0, 2050.0, 0.5)
        record.append(("look", look))
        return True, ""

    monkeypatch.setattr(relief, "render", view)


def looks_used(record):
    return [item[1] for item in record if item[0] == "look"]


MINE = looks.Look("viridis", "lights4", 90.0, 30.0, 0.4, "auto", None, None)


def cad_run(tmp_path, monkeypatch, record, **kw):
    spy(monkeypatch, record)
    cad = {"format": "dxf", "images": "jp2", "clip": False}
    jobs = plan.build_jobs("EDF", DAY, {"dem": 0.5}, "cad", str(tmp_path), lang="en", cad=cad, version=1)
    ctx = fake_context(tmp_path, Env(tmp_path), "cad", tools={"gdal": "G"}, cad=cad, **kw)
    return pipeline.run(ctx, jobs, lambda f, t: None)


def test_the_look_the_user_chose_is_what_paints_the_dem_image_of_the_drawing(tmp_path, monkeypatch):
    record = []
    cad_run(tmp_path, monkeypatch, record, look=MINE)
    assert looks_used(record) == [MINE]


def test_without_a_chosen_look_the_drawing_gets_the_default_one(tmp_path, monkeypatch):
    record = []
    cad_run(tmp_path, monkeypatch, record)
    assert looks_used(record) == [looks.DEFAULT]


def test_a_typed_colour_range_is_dropped_with_a_warning_when_the_heights_are_converted(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "_to_altitude", lambda ctx, raw, sub: None)          # the conversion itself is tested elsewhere
    record = []
    typed = looks.DEFAULT._replace(stretch="custom", low=2.0, high=9.0)
    result = cad_run(tmp_path, monkeypatch, record, look=typed, vertical="RAC09")
    used = looks_used(record)[0]
    assert used.stretch == "full" and used.low is None and used.palette == typed.palette
    assert result.warnings == ["stretch"]
    record.clear()
    result = cad_run(tmp_path, monkeypatch, record, look=typed)                        # same look, heights untouched: kept
    assert looks_used(record) == [typed] and result.warnings == []


def test_json_presets_can_add_a_coloured_dem_picture_next_to_the_dem(tmp_path):
    jobs = plan.build_jobs("EDF", DAY, {"ortho": 0.5, "dem": 0.5}, "jp2", str(tmp_path), lang="en", version=1, dem_picture=True)
    dem = [job for job in jobs if job.kind == "dem"][0]
    assert dem.name == "EDF_260704_50cm_dtm.tif" and dem.extra["view_path"].endswith("EDF_260704_50cm_dtm.jp2")
    assert dem.extra["view_path"] in plan.planned_files(jobs, "jp2")                   # counted as an existing file / in the names list
    assert [job for job in jobs if job.kind == "ortho"][0].extra is None
    ecw = plan.build_jobs("EDF", DAY, {"dem": 0.5}, "ecw", str(tmp_path), lang="en", version=1, dem_picture=True)[0]
    assert ecw.extra["view_ext"] == "ecw" and ecw.extra["view_path"].endswith("_dtm.ecw")


def test_geotiff_presets_ignore_the_picture_option_because_the_name_would_be_the_dems(tmp_path):
    for key in ("full", "quick", "jpeg"):
        job = plan.build_jobs("EDF", DAY, {"dem": 0.5}, key, str(tmp_path), lang="en", version=1, dem_picture=True)[0]
        assert job.extra is None
    off = plan.build_jobs("EDF", DAY, {"dem": 0.5}, "jp2", str(tmp_path), lang="en", version=1)[0]
    assert off.extra is None


def test_an_existing_picture_makes_the_next_export_a_new_version(tmp_path):
    jobs = plan.build_jobs("EDF", DAY, {"dem": 0.5}, "jp2", str(tmp_path), lang="en", version=None, dem_picture=True)
    assert jobs[0].version == 1
    (tmp_path / "EDF_260704_50cm_dtm.jp2").write_bytes(b"x")                           # only the picture exists
    again = plan.build_jobs("EDF", DAY, {"dem": 0.5}, "jp2", str(tmp_path), lang="en", version=None, dem_picture=True)
    assert again[0].version == 2 and again[0].name == "EDF_260704_50cm_v2_dtm.tif"


def test_the_export_writes_the_dem_and_its_coloured_picture_and_no_drawing(tmp_path, monkeypatch):
    record = []
    spy(monkeypatch, record)
    jobs = plan.build_jobs("EDF", DAY, {"dem": 0.5}, "jp2", str(tmp_path), lang="en", version=1, dem_picture=True)
    ctx = fake_context(tmp_path, Env(tmp_path), "jp2", tools={"gdal": "G"}, look=MINE)
    result = pipeline.run(ctx, jobs, lambda f, t: None)
    names = sorted(os.path.basename(path) for path, _size in result.files)
    assert names == ["EDF_260704_50cm_dtm.jp2", "EDF_260704_50cm_dtm.tif"]
    assert os.path.isfile(tmp_path / "EDF_260704_50cm_dtm.tif") and os.path.isfile(tmp_path / "EDF_260704_50cm_dtm.jp2")
    assert looks_used(record) == [MINE] and result.dxf is None
    assert [item for item in record if item[0] == "gdal"] == [("gdal", "EDF_260704_50cm_dtm_view.tif", "EDF_260704_50cm_dtm.jp2")]
    assert not [n for n in os.listdir(tmp_path) if n.startswith(pipeline.TMP_NAME) or n.endswith("_view.tif")]


def test_without_the_option_a_jp2_export_still_writes_only_the_dem(tmp_path, monkeypatch):
    record = []
    spy(monkeypatch, record)
    jobs = plan.build_jobs("EDF", DAY, {"dem": 0.5}, "jp2", str(tmp_path), lang="en", version=1)
    result = pipeline.run(fake_context(tmp_path, Env(tmp_path), "jp2", tools={"gdal": "G"}), jobs, lambda f, t: None)
    assert [os.path.basename(path) for path, _s in result.files] == ["EDF_260704_50cm_dtm.tif"] and not looks_used(record)
