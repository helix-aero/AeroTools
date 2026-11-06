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
