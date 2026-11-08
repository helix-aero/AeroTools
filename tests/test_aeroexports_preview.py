"""Aero-Exports: the DEM preview model (small DEM, painting, legend) and where its data comes from."""

import os
import time

import numpy as np
import pytest

from aerotools.tools.aeroexports import look as looks
from aerotools.tools.aeroexports import preview, relief, tiffio
from test_aeroexports_geoid import make_dem
from test_aeroexports_look import hill_dem, read_rgb


def brute_force_mean(z, k, nodata=-32767.0):
    height, width = z.shape
    out = np.full((-(-height // k), -(-width // k)), np.nan)
    for r in range(out.shape[0]):
        for c in range(out.shape[1]):
            part = z[r * k:(r + 1) * k, c * k:(c + 1) * k]
            good = part[part != nodata]
            if good.size:
                out[r, c] = good.mean()
    return out


@pytest.mark.parametrize("k", [1, 2, 4, 3, 5])                                        # 16-pixel tiles: 3 and 5 take the general path
def test_reducing_a_dem_averages_the_valid_pixels_of_each_square(tmp_path, k):
    rows, cols = np.mgrid[0:50, 0:70]
    z = (3.0 + 0.1 * cols + np.sin(rows / 3.0)).astype("<f4")
    z[10:20, 30:45] = -32767.0                                                         # a hole
    z[:, -4:] = -32767.0
    path = str(tmp_path / "dem.tif")
    make_dem(path, z, 500.0, 900.0, 0.25)
    grid, px, py = preview.reduce(tiffio.Tiff(path), k)
    expected = brute_force_mean(z, k)
    assert grid.shape == expected.shape and (px, py) == (0.25 * k, 0.25 * k)
    assert np.array_equal(np.isnan(grid), np.isnan(expected))
    assert np.allclose(grid[~np.isnan(grid)], expected[~np.isnan(expected)], atol=1e-4)


def test_the_reduction_factor_keeps_the_preview_near_a_thousand_pixels():
    assert preview.reduction_factor(800, 600) == 1 and preview.reduction_factor(1250, 10) == 1
    assert preview.reduction_factor(8150, 8770) == 8                                   # a typical large ortho: about 1100 px
    assert preview.reduction_factor(20000, 20000) == 16
    for size in (500, 2500, 7777, 31000):
        assert 500 < size / preview.reduction_factor(size, size) <= 1250 or size < 1250


def make_preview(tmp_path, **kw):
    path = str(tmp_path / "dem.tif")
    z = hill_dem(path, nodata_rows=6, **kw)
    return preview.Preview.from_tiff(path), z, path


def test_the_preview_is_painted_with_the_same_picture_as_the_export(tmp_path):
    model, z, path = make_preview(tmp_path)
    for look in (looks.DEFAULT, looks.DEFAULT._replace(palette="viridis", shading="lights4", strength=0.6),
                 looks.DEFAULT._replace(shading="slope", stretch="custom", low=2.0, high=4.0)):
        out = str(tmp_path / "view.tif")
        assert relief.render(path, out, look=look)[0]
        exported, shown = read_rgb(out), model.render(look, void="white")
        assert shown.shape[:2] == exported.shape[:2] and (shown[:, :, 3] == 255).all()
        assert np.abs(shown[:, :, :3].astype(int) - exported.astype(int)).max() <= 1   # float32 against float64: one level at most


def test_no_data_is_transparent_in_the_preview_when_the_drawing_hides_it_and_white_otherwise(tmp_path):
    model, _z, _path = make_preview(tmp_path)
    clear, white = model.render(looks.DEFAULT, "transparent"), model.render(looks.DEFAULT, "white")
    assert (clear[:6, :, 3] == 0).all() and (clear[6:, :, 3] == 255).all()
    assert (white[:6, :, :3] == 255).all() and (white[:, :, 3] == 255).all()


def test_the_legend_has_five_labels_between_the_exact_ends(tmp_path):
    model, z, _path = make_preview(tmp_path)
    low, high = float(z[6:].min()), float(z.max())
    legend = model.legend(looks.DEFAULT)
    assert (legend["low"], legend["high"]) == pytest.approx((low, high))
    assert [f for f, _t in legend["ticks"]] == [0.0, 0.25, 0.5, 0.75, 1.0]
    assert legend["ticks"][0][1] == "%.2f" % low and legend["ticks"][-1][1] == "%.2f" % high
    custom = model.legend(looks.DEFAULT._replace(stretch="custom", low=1.0, high=5.0))
    assert [t for _f, t in custom["ticks"]] == ["1.00", "2.00", "3.00", "4.00", "5.00"]
    assert preview.nice_decimals(1000) == 0 and preview.nice_decimals(60) == 1 and preview.nice_decimals(5) == 2
    assert preview.nice_decimals(0.2) == 3
    bar = model.colour_bar(looks.DEFAULT, 64)
    assert bar.shape == (64, 3) and tuple(bar[0]) == (225, 53, 56) and tuple(bar[-1]) == (47, 55, 218)   # red on top, blue below


def test_metashapes_own_min_and_max_are_used_only_when_they_fit_the_data(tmp_path):
    model, z, path = make_preview(tmp_path)
    low, high = float(z[6:].min()), float(z.max())
    trusted = preview.Preview.from_tiff(path, exact=(low - 0.5, high + 0.5))
    assert trusted.span() == (low - 0.5, high + 0.5)                                  # the real peak may be missing from the small DEM
    for bad in (None, (5, 5), (high + 10, high + 20), (low, low + 0.01), ("x", "y"), (float("nan"), 3), "oops", (1,)):
        assert preview.Preview.from_tiff(path, exact=bad).span() == pytest.approx((low, high))


def test_automatic_range_ignores_a_spike_and_custom_range_is_used_as_typed(tmp_path):
    path = str(tmp_path / "dem.tif")
    z = hill_dem(path)
    z[0, 0] = 300.0
    make_dem(path, z, 1000.0, 2400.0, 0.5)
    model = preview.Preview.from_tiff(path)
    assert model.colour_range(looks.DEFAULT)[1] == pytest.approx(300.0)
    assert model.colour_range(looks.DEFAULT._replace(stretch="auto"))[1] < 20
    assert model.colour_range(looks.DEFAULT._replace(stretch="custom", low=2, high=3)) == (2.0, 3.0)


def test_a_geographic_dem_is_painted_without_shading_like_the_export(tmp_path):
    model, _z, _path = make_preview(tmp_path, pixel=0.00001)
    assert model.geographic
    sun, bare = model.render(looks.DEFAULT, "white"), model.render(looks.DEFAULT._replace(shading="none"), "white")
    assert np.array_equal(sun, bare)


def test_a_dem_without_any_data_is_not_valid(tmp_path):
    path = str(tmp_path / "empty.tif")
    make_dem(path, np.full((20, 20), -32767.0, dtype="<f4"), 0.0, 0.0, 0.5)
    assert not preview.Preview.from_tiff(path).valid


def test_painting_the_preview_is_fast_enough_for_a_slider(tmp_path):
    rows, cols = np.mgrid[0:800, 0:800]
    grid = (5 + 3 * np.sin(rows / 40.0) * np.cos(cols / 33.0)).astype(np.float32)
    model = preview.Preview(grid, 0.1, 0.1)
    model.render(looks.DEFAULT)                                                        # warm-up
    for look in (looks.DEFAULT, looks.DEFAULT._replace(shading="lights4")):
        started = time.perf_counter()
        model.render(look)
        assert time.perf_counter() - started < 1.0                                     # measured 0.05 to 0.15 s; the bound only catches a regression to seconds


# -- where the small DEM comes from -------------------------------------------------------------------

def test_an_exported_dem_is_reused_only_when_it_is_not_older_than_the_project(tmp_path):
    psx, dem = tmp_path / "site.psx", tmp_path / "dem.tif"
    psx.write_text("x")
    dem.write_text("x")
    os.utime(psx, (1000, 1000))
    os.utime(dem, (2000, 2000))
    assert preview.usable_export([str(tmp_path / "none.tif"), str(dem)], str(psx)) == str(dem)
    os.utime(psx, (3000, 3000))                                                        # project saved after the export: DEM maybe rebuilt
    assert preview.usable_export([str(dem)], str(psx)) is None
    assert preview.usable_export([str(dem)], None) == str(dem) and preview.usable_export([], str(psx)) is None


def test_the_small_dem_is_asked_from_metashape_without_a_progress_callback(tmp_path):
    seen, flags = [], []

    def export(chunk, kind, spec, path, pixel, box, big=False, progress=None, crs=None, clip_to_boundary=True):
        flags.append(clip_to_boundary)
        seen.append((kind, spec, pixel, box, progress, crs, big))
        make_dem(path, np.full((10, 10), 3.0, dtype="<f4"), box[0], box[3], pixel)
        return True, ""

    path = preview.fetch_small(export, object(), (1000.0, 2000.0, 1500.0, 2200.0), str(tmp_path))
    assert flags == [True]                                  # the boundary clip is always passed, Metashape's default when not asked
    preview.fetch_small(export, object(), (1000.0, 2000.0, 1500.0, 2200.0), str(tmp_path), clip=False)
    assert flags == [True, False]
    kind, spec, pixel, box, progress, crs, big = seen[0]
    assert kind == "dem" and progress is None and crs is None and not big             # a Qt callback inside Metashape's export crashed it
    assert pixel == pytest.approx(500.0 / preview.TARGET) and box == (1000.0, 2000.0, 1500.0, 2200.0)
    assert spec["compression"] == "deflate" and os.path.isfile(path)
    assert preview.Preview.from_tiff(path).valid


def test_a_failed_or_empty_small_export_raises_a_clear_error(tmp_path):
    with pytest.raises(ValueError, match="boom"):
        preview.fetch_small(lambda *a, **k: (False, "boom"), object(), (0, 0, 10, 10), str(tmp_path))
    with pytest.raises(ValueError):
        preview.fetch_small(lambda *a, **k: (True, ""), object(), (0, 0, 10, 10), str(tmp_path))     # says OK but wrote nothing
    with pytest.raises(ValueError):
        preview.fetch_small(lambda *a, **k: (True, ""), object(), None, str(tmp_path))


# -- an export left on disk is reused for the preview only if it is THIS chunk's DEM ---------------------------------------

def test_an_export_of_another_chunk_is_not_reused_for_the_preview(tmp_path):
    june = tmp_path / "SITE_260610_20cm_dtm.tif"          # what the July chunk would be called too if the date were the same
    make_dem(str(june), np.full((40, 30), 5.0, dtype="<f4"), 900.0, 920.0, 0.2)       # 30 x 40 px at 0.2 m: x 900..906, y 912..920
    native_here = (0.2, 30, 40)
    box_here = (900.0, 912.0, 906.0, 920.0)
    assert preview.usable_export([str(june)], None, box_here, native_here) == str(june)
    # the chunk now shown has another extent / size / pixel: the file on disk is not its DEM
    assert preview.usable_export([str(june)], None, (100.0, 200.0, 106.0, 208.0), native_here) is None
    assert preview.usable_export([str(june)], None, box_here, (0.2, 31, 40)) is None
    assert preview.usable_export([str(june)], None, box_here, (0.1, 30, 40)) is None
    assert preview.usable_export([str(tmp_path / "missing.tif")], None, box_here, native_here) is None


def test_an_unreadable_export_is_not_reused_for_the_preview(tmp_path):
    bad = tmp_path / "SITE_260610_20cm_dtm.tif"
    bad.write_text("not a tiff")
    assert preview.usable_export([str(bad)], None, (0.0, 0.0, 6.0, 8.0), (0.2, 30, 40)) is None
