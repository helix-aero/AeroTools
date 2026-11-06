"""Aero-Exports: the DXF drawing clips each image to its real data (option "Crop white background")."""

import os

import numpy as np

from aerotools.tools.aeroexports import footprint, pipeline, plan
from test_aeroexports_cad import DAY, Env, fake_context, fake_converters


def diamond(size=40):
    rows, cols = np.mgrid[0:size, 0:size]
    middle = (size - 1) / 2.0
    return (np.abs(rows - middle) + np.abs(cols - middle)) <= size / 2.0 - 2


def export(tmp_path, monkeypatch, clip, mask_error=None, **context):
    env = Env(tmp_path)
    fake_converters(monkeypatch, [])

    def alpha(path):
        if mask_error:
            raise ValueError(mask_error)
        return diamond()

    monkeypatch.setattr(footprint, "alpha_mask", alpha)
    monkeypatch.setattr(footprint, "dem_mask", lambda path, target=800: (diamond(), 1))
    cad = {"format": "dxf", "images": "jp2", "clip": clip}
    jobs = plan.build_jobs("EDF", DAY, {"ortho": 0.5, "dem": 0.5}, "cad", str(tmp_path), cad=cad, version=1)
    result = pipeline.run(fake_context(tmp_path, env, "cad", tools={"gdal": "G"}, cad=cad, **context), jobs, lambda f, t: None)
    text = open(tmp_path / "TOPO.dxf", "rb").read().decode("ascii")
    return result, "\n".join(text.splitlines()), env


def test_each_image_is_clipped_to_its_real_data_when_the_option_is_on(tmp_path, monkeypatch):
    result, text, env = export(tmp_path, monkeypatch, True)
    assert text.count("\n71\n2\n") == 2 and "\n70\n7\n" in text and not result.warnings
    masks = [item for item in env.exports if item[0] == "ortho" and item[1].startswith("mask_")]
    assert len(masks) == 1 and masks[0][2] >= 0.5                                 # small export of the orthomosaic: never finer than the image


def test_images_stay_whole_when_the_option_is_off(tmp_path, monkeypatch):
    result, text, env = export(tmp_path, monkeypatch, False)
    assert "\n71\n2\n" not in text and text.count("\n71\n1\n") == 2
    assert not [item for item in env.exports if item[1].startswith("mask_")]       # no mask export


def test_a_failed_mask_only_warns_and_the_drawing_is_still_written(tmp_path, monkeypatch):
    result, text, _env = export(tmp_path, monkeypatch, True, mask_error="no mask")
    assert "clip" in result.warnings and os.path.isfile(tmp_path / "TOPO.dxf")
    assert text.count("\n71\n2\n") == 1                                           # the DEM, on the other hand, is clipped


def test_the_boundary_clip_setting_reaches_every_metashape_export_including_the_mask(tmp_path, monkeypatch):
    for value in (True, False):
        folder = tmp_path / str(value)
        folder.mkdir()
        _result, _text, env = export(folder, monkeypatch, True, clip_boundary=value)
        assert len(env.exports) == 3                                               # ortho, mask of the ortho, DEM
        assert [item[5] for item in env.exports] == [value] * 3
    folder = tmp_path / "default"
    folder.mkdir()
    _result, _text, env = export(folder, monkeypatch, True)
    assert [item[5] for item in env.exports] == [True] * 3                          # Metashape's own default, passed explicitly


def test_the_dem_footprint_is_written_to_the_journal(tmp_path, monkeypatch):
    lines = []
    _result, _text, _env = export(tmp_path, monkeypatch, True, clip_boundary=False, log=lines.append)
    assert len(lines) == 1 and "DEM footprint" in lines[0] and "clip_to_boundary=False" in lines[0] and "%" in lines[0]
