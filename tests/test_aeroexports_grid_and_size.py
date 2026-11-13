"""Aero-Exports: BigTIFF decided on the exported size, tiles of the orthomosaic and the DTM numbered on one common grid, and the
65 500 px side limit of JPEG checked before Metashape starts."""

import os
import re

import pytest

from aerotools.tools.aeroexports import pipeline, plan
from test_aeroexports_cad import DAY, Env, fake_context


def run(tmp_path, preset, wanted, info=None, **kw):
    env = Env(tmp_path)
    ctx = fake_context(tmp_path, env, preset, **kw)
    if info:
        ctx.info.update(info)
    jobs = plan.build_jobs("ClientA", DAY, wanted, preset, str(tmp_path), version=1)
    return env, ctx, jobs


# ---- 4: BigTIFF ------------------------------------------------------------------------------------------------

def test_bigtiff_follows_the_size_that_is_exported_not_the_native_one(tmp_path):
    big_flags = []

    def export(chunk, kind, spec, path, pixel=None, box=None, big=False, progress=None, crs=None, clip_to_boundary=True):
        big_flags.append(big)
        return False, "stop here"

    # native 28 000 x 28 000 (3.1 GB raw RGBA: classic TIFF is enough), but 2 cm asked over the same ground is 70 000 px a side
    info = {"ortho": (0.05, 28000, 28000), "box_ortho": (0.0, 0.0, 1400.0, 1400.0)}
    env = Env(tmp_path)
    ctx = fake_context(tmp_path, env, "full")
    ctx.info.update(info)
    ctx.export = export
    jobs = plan.build_jobs("ClientA", DAY, {"ortho": 0.02}, "full", str(tmp_path), version=1)
    with pytest.raises(pipeline.Failure):
        pipeline.run(ctx, jobs, lambda f, t: None)
    assert big_flags == [True]
    big_flags.clear()
    jobs = plan.build_jobs("ClientA", DAY, {"ortho": 0.05}, "full", str(tmp_path), version=1, native={"ortho"})
    with pytest.raises(pipeline.Failure):
        pipeline.run(ctx, jobs, lambda f, t: None)
    assert big_flags == [False]                       # native pixel: the native size (3.1 GB) stays classic


def test_exported_size_uses_the_planned_grid_when_there_is_one():
    ctx = type("C", (), {"info": {"ortho": (0.05, 100, 100)}})()
    job = type("J", (), {"kind": "ortho"})()
    assert pipeline.exported_size(ctx, job, 0.01, (0.0, 0.0, 10.0, 5.0)) == (1000, 500)
    assert pipeline.exported_size(ctx, job, None, None) == (100, 100)
    assert pipeline.exported_size(type("C", (), {"info": {}})(), job, None, None) is None


# ---- 5: tiles on one grid --------------------------------------------------------------------------------------

def tiles_of(env):
    """{kind: {token: box}} of the tile exports a fake Metashape received."""
    out = {}
    for kind, name, pixel, box, _crs, _clip in env.exports:
        match = re.search(r"_(R\d+C\d+)_", name)
        if match:
            out.setdefault(kind, {})[match.group(1)] = box
    return out


def test_ortho_and_dem_tiles_with_the_same_name_cover_the_same_ground(tmp_path):
    # the DEM starts one 50 m cell further south-west than the orthomosaic: counted from each own extent, R01C01 would differ
    info = {"ortho": (0.5, 400, 400), "dem": (0.5, 500, 500), "box_ortho": (1010.0, 2010.0, 1210.0, 2210.0),
            "box_dem": (990.0, 1990.0, 1240.0, 2240.0)}
    env, ctx, jobs = run(tmp_path, "full", {"ortho": 0.5, "dem": 0.5}, info, tile_m=50)
    pipeline.run(ctx, jobs, lambda f, t: None)
    tiles = tiles_of(env)
    shared = set(tiles["ortho"]) & set(tiles["dem"])
    assert shared
    for token in shared:
        o, d = tiles["ortho"][token], tiles["dem"][token]
        # same cell: the lower-left corners fall in the same 50 m cell (an edge tile is cropped to its own data)
        assert (o[0] // 50, o[1] // 50) == (d[0] // 50, d[1] // 50), (token, o, d)
    assert len(tiles["dem"]) > len(tiles["ortho"])
    first_ortho = min(tiles["ortho"], key=lambda t: (int(t[1:3]), int(t[4:])))
    assert tiles["ortho"][first_ortho][0] >= tiles["dem"]["R%02dC%02d" % (int(first_ortho[1:3]), int(first_ortho[4:]))][0]


def test_a_single_raster_keeps_its_own_numbering(tmp_path):
    info = {"dem": (0.5, 500, 500), "box_dem": (990.0, 1990.0, 1240.0, 2240.0)}
    env, ctx, jobs = run(tmp_path, "full", {"dem": 0.5}, info, tile_m=50)
    pipeline.run(ctx, jobs, lambda f, t: None)
    names = sorted(tiles_of(env)["dem"])
    assert names[0] == "R01C01" and names[-1] == "R06C06"


# ---- 34: JPEG side limit ---------------------------------------------------------------------------------------

def test_a_jpeg_longer_than_65500_px_is_refused_before_metashape_starts(tmp_path):
    info = {"ortho": (0.01, 70000, 30000), "box_ortho": (0.0, 0.0, 700.0, 300.0)}
    env, ctx, jobs = run(tmp_path, "jpeg", {"ortho": 0.01}, info)
    with pytest.raises(pipeline.Failure) as caught:
        pipeline.run(ctx, jobs, lambda f, t: None)
    assert "65500" in str(caught.value) and "tiles" in str(caught.value) and env.exports == []
    assert os.listdir(str(tmp_path)) == []


def test_tiles_under_the_limit_and_other_formats_pass_the_check(tmp_path):
    info = {"ortho": (0.01, 70000, 30000), "box_ortho": (0.0, 0.0, 700.0, 300.0)}
    env, ctx, jobs = run(tmp_path, "jpeg", {"ortho": 0.01}, info, tile_m=300)
    assert pipeline.size_problems(ctx, jobs) == []                      # 30 000 px tiles
    env, ctx, jobs = run(tmp_path, "full", {"ortho": 0.01}, info)
    assert pipeline.size_problems(ctx, jobs) == []                      # GeoTIFF has no such limit


def test_a_dem_picture_as_jpeg_has_the_same_65500_px_guard_and_the_heights_do_not(tmp_path):
    info = {"dem": (0.01, 70000, 30000), "box_dem": (0.0, 0.0, 700.0, 300.0)}
    env = Env(tmp_path)
    ctx = fake_context(tmp_path, env, "jpeg")
    ctx.info.update(info)
    with_picture = plan.build_jobs("ClientA", DAY, {"dem": 0.01}, "jpeg", str(tmp_path), version=1, dem_picture=True)
    problems = pipeline.size_problems(ctx, with_picture)
    assert len(problems) == 1 and "65500" in problems[0] and "tiles" in problems[0]
    plain = plan.build_jobs("ClientA", DAY, {"dem": 0.01}, "jpeg", str(tmp_path), version=1)
    assert pipeline.size_problems(ctx, plain) == []                     # the heights are a GeoTIFF
