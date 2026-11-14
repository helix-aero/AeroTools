"""Aero-Exports: several outputs of one DEM in one run (heights + picture), and the JPEG preset keeping the DEM a GeoTIFF."""

import os

from aerotools.tools.aeroexports import pipeline, plan
from test_aeroexports_cad import DAY, Env, fake_context
from test_aeroexports_look_pipeline import spy


def run(tmp_path, monkeypatch, preset, wanted, **kw):
    spy(monkeypatch, [])
    jobs = plan.build_jobs("ClientA", DAY, wanted, preset, str(tmp_path), version=1, **kw)
    ctx = fake_context(tmp_path, Env(tmp_path), preset, tools={"gdal": "G"})
    result = pipeline.run(ctx, jobs, lambda f, t: None)
    return jobs, sorted(os.path.basename(p) for p, _s in result.files), sorted(os.listdir(tmp_path))


def test_jpeg_preset_with_only_the_dem_writes_a_geotiff_of_heights_by_design(tmp_path, monkeypatch):
    _jobs, written, on_disk = run(tmp_path, monkeypatch, "jpeg", {"dem": 0.5})
    assert written == ["ClientA_300704_50cm_dtm.tif"] and on_disk == written


def test_ortho_dem_and_dem_picture_in_one_run_with_distinct_names(tmp_path, monkeypatch):
    jobs, written, on_disk = run(tmp_path, monkeypatch, "jpeg", {"ortho": 0.5, "dem": 0.5}, dem_picture=True)
    assert {"ClientA_300704_50cm_ortho.jpg", "ClientA_300704_50cm_dtm.tif", "ClientA_300704_50cm_dtm.jpg",
            "ClientA_300704_50cm_dtm.jgw"} <= set(written)
    assert "ClientA_300704_50cm_dtm.tif" in on_disk and "ClientA_300704_50cm_dtm.jpg" in on_disk
    assert not [n for n in on_disk if n.endswith("_view.tif")]
    assert len(set(on_disk)) == len(on_disk)
    assert not [n for n in on_disk if n.startswith(pipeline.TMP_NAME)]
    assert len(plan.planned_files(jobs, "jpeg")) == len(set(plan.planned_files(jobs, "jpeg")))


def test_picture_only_writes_no_heights(tmp_path, monkeypatch):
    jobs, written, on_disk = run(tmp_path, monkeypatch, "full", {"dem": 0.5}, dem_picture=True, dem_values=False)
    assert written == ["ClientA_300704_50cm_dtm_view.tif"] and on_disk == written
    assert plan.planned_files(jobs, "full") == [str(tmp_path / "ClientA_300704_50cm_dtm_view.tif")]


def test_second_run_picks_a_new_version_instead_of_overwriting(tmp_path, monkeypatch):
    run(tmp_path, monkeypatch, "full", {"dem": 0.5}, dem_picture=True)
    jobs = plan.build_jobs("ClientA", DAY, {"dem": 0.5}, "full", str(tmp_path), version=None, dem_picture=True)
    assert jobs[0].version == 2 and jobs[0].extra["view_name"] == "ClientA_300704_50cm_v2_dtm_view.tif"
    only_picture = plan.build_jobs("ClientA", DAY, {"dem": 0.5}, "full", str(tmp_path), version=None, dem_picture=True,
                                   dem_values=False)
    assert only_picture[0].version == 2                    # the picture of v1 exists: no overwrite
