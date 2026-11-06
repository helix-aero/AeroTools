"""Fast Export: an orthomosaic in degrees (WGS 84) next to a DEM in metres (CC42) in the same chunk."""

import pytest

from aerotools.tools.fastexport import pipeline, plan
from test_fastexport_cad import DAY, Env, fake_context


def context(tmp_path, **kw):
    ctx = fake_context(tmp_path, Env(tmp_path), "full", **kw)
    ctx.info.update({"crs_ortho": "wgs84", "crs_dem": "cc42", "geographic_ortho": True, "geographic_dem": False,
                     "box_ortho": (8.74, 41.91, 8.75, 41.92), "box_dem": (2175805.0, 1206705.0, 2175894.0, 1206803.0)})
    return ctx


def jobs(tmp_path, kinds):
    return plan.build_jobs("EDF", DAY, dict.fromkeys(kinds, 0.5), "full", str(tmp_path), lang="en", version=1)


def test_each_box_is_converted_from_the_system_of_its_own_raster(tmp_path):
    seen = []
    ctx = context(tmp_path, crs="L93")
    ctx.transform = lambda box, source, target: seen.append((box, source)) or (1.0, 2.0, 3.0, 4.0)
    pipeline.run(ctx, jobs(tmp_path, ["ortho", "dem"]), lambda f, t: None)
    assert [source for _box, source in seen] == ["wgs84", "cc42"]


def test_a_pixel_in_centimetres_on_a_raster_in_degrees_is_refused_with_a_clear_message(tmp_path):
    with pytest.raises(pipeline.Failure, match="geographic"):
        pipeline.run(context(tmp_path), jobs(tmp_path, ["ortho"]), lambda f, t: None)


def test_the_dem_in_metres_is_not_refused_without_a_target_system(tmp_path):
    pipeline.run(context(tmp_path), jobs(tmp_path, ["dem"]), lambda f, t: None)


def test_a_box_that_converts_to_nan_is_a_failure_not_a_crash(tmp_path):
    ctx = context(tmp_path, crs="L93")
    ctx.transform = lambda box, source, target: (float("nan"), 2.0, 3.0, 4.0)
    with pytest.raises(pipeline.Failure, match="converted"):
        pipeline.run(ctx, jobs(tmp_path, ["dem"]), lambda f, t: None)


def test_the_failing_file_is_named_not_the_first_one(tmp_path):
    """Ortho fine, DEM refused: the error line must name the DEM file."""
    ctx = context(tmp_path, crs="L93")
    ctx.info["geographic_ortho"] = False
    all_jobs = jobs(tmp_path, ["ortho", "dem"])
    real_export = ctx.export

    def export(chunk, kind, spec, path, *args, **kw):
        if kind == "dem":
            return False, "Empty raster"
        return real_export(chunk, kind, spec, path, *args, **kw)

    ctx.export = export
    ctx.transform = lambda box, source, target: (1.0, 2.0, 3.0, 4.0)
    with pytest.raises(pipeline.Failure, match="Empty raster"):
        pipeline.run(ctx, all_jobs, lambda f, t: None)
    assert ctx.failed == [j for j in all_jobs if j.kind == "dem"][0].name
    assert ctx.failed != all_jobs[0].name


@pytest.mark.parametrize("lon,lat,expected", [(8.75, 41.92, 3942), (2.35, 48.85, 3948), (-1.5, 47.2, 3947)])
def test_the_best_projected_system_for_a_place_in_france(lon, lat, expected):
    from aerotools.tools.fastexport import projdb
    if not projdb.available():
        pytest.skip("proj.db not available")
    assert projdb.best_projected(lon, lat) == expected


def test_the_best_projected_system_skips_feet_and_needs_a_place():
    from aerotools.tools.fastexport import projdb
    if not projdb.available():
        pytest.skip("proj.db not available")
    assert projdb.best_projected(None, None) is None
    name = dict(projdb.candidates(-74.0, 40.7))[projdb.best_projected(-74.0, 40.7)]
    assert "ft" not in name
