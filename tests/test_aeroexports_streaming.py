"""Aero-Exports: DEM handled block by block (bands, streamed rewrite, footprint without ufunc.at) gives what the whole-array
code gave."""

import zlib

import numpy as np
import pytest

from aerotools.tools.aeroexports import footprint, relief, tiffio
from test_aeroexports_geoid import make_dem


def dem_file(tmp_path, height=70, width=90, name="dem.tif", tile=16):
    rows, cols = np.mgrid[0:height, 0:width]
    z = (2.0 + 0.05 * cols + 0.5 * np.sin(rows / 4.0)).astype("<f4")
    z[:5, :] = -32767.0
    z[30:40, 50:60] = -32767.0
    path = str(tmp_path / name)
    make_dem(path, z, 1000.0, 2400.0, 0.5, tile=tile)
    return path, z


def test_row_bands_are_the_whole_image_in_order(tmp_path):
    path, z = dem_file(tmp_path)
    tiff = tiffio.Tiff(path)
    seen, expected_top = [], 0
    for top, band in tiff.row_bands():
        assert top == expected_top
        seen.append(band)
        expected_top += band.shape[0]
    assert np.array_equal(np.concatenate(seen), tiff.read_all()) and expected_top == tiff.height


def picture_of(tiff):
    out = []
    for offset, count in zip(tiff.offsets, tiff.counts):
        with open(tiff.path, "rb") as handle:
            handle.seek(offset)
            out.append(np.frombuffer(zlib.decompress(handle.read(count)), dtype=np.uint8).reshape(-1, tiff.width, 3))
    return np.concatenate(out)


def old_render(path, chunk):
    """The previous render: the whole DEM in memory, strips cut from it (the picture, as an array)."""
    tiff = tiffio.Tiff(path)
    low, high = relief.value_range(path)
    z, nodata = tiff.read_all(), relief.declared_nodata(tiff)
    table = relief.table_for(relief.looks.palette(relief.looks.DEFAULT.palette))
    parts = []
    for top in range(0, tiff.height, chunk):
        bottom = min(tiff.height, top + chunk)
        lo, hi = max(0, top - 1), min(tiff.height, bottom + 1)
        full = relief.shade_block(z[lo:hi], nodata, low, 0.5, 0.5, relief.looks.DEFAULT)
        parts.append(relief.colorize(z[top:bottom], low, high, table, full[top - lo:top - lo + (bottom - top)], nodata))
    return np.concatenate(parts)


@pytest.mark.parametrize("chunk", [1024, 7, 16, 33])
def test_render_in_strips_paints_the_same_picture_as_the_whole_dem_in_memory(tmp_path, monkeypatch, chunk):
    path, _z = dem_file(tmp_path)
    monkeypatch.setattr(relief, "CHUNK", chunk)
    ok, message = relief.render(path, str(tmp_path / "view.tif"))
    assert ok, message
    assert np.array_equal(picture_of(tiffio.Tiff(str(tmp_path / "view.tif"), strict=False)), old_render(path, chunk))


def old_rewrite(tiff, dest, change, level=6):
    """The previous algorithm: every compressed block in a list, then the file."""
    compressed = []
    for top, left, block in tiff.blocks():
        compressed.append(zlib.compress(np.ascontiguousarray(change(top, left, block), dtype="<f4").tobytes(), level))
    tiff._write(dest, compressed, sum(len(item) for item in compressed) > tiffio.CLASSIC_LIMIT)


def test_streamed_rewrite_is_byte_for_byte_the_old_file(tmp_path):
    path, _z = dem_file(tmp_path)
    tiff = tiffio.Tiff(path)
    change = lambda top, left, block: block - 1.5
    tiff.rewrite(str(tmp_path / "new.tif"), change)
    old_rewrite(tiff, str(tmp_path / "old.tif"), change)
    assert (tmp_path / "new.tif").read_bytes() == (tmp_path / "old.tif").read_bytes()
    assert np.allclose(tiffio.Tiff(str(tmp_path / "new.tif")).read_all(), tiff.read_all() - 1.5)


def test_rewrite_switches_to_bigtiff_exactly_when_the_blocks_pass_the_limit(tmp_path, monkeypatch):
    path, _z = dem_file(tmp_path)
    tiff = tiffio.Tiff(path)
    change = lambda top, left, block: block + 2.0
    size = len((tmp_path / "dem.tif").read_bytes())
    monkeypatch.setattr(tiffio, "CLASSIC_LIMIT", 3000)            # the blocks add up to more than this
    calls = []
    tiff.rewrite(str(tmp_path / "big.tif"), lambda t, l, b: calls.append(1) or change(t, l, b))
    old_rewrite(tiff, str(tmp_path / "oldbig.tif"), change)
    assert (tmp_path / "big.tif").read_bytes() == (tmp_path / "oldbig.tif").read_bytes()
    assert tiffio.Tiff(str(tmp_path / "big.tif")).big and np.allclose(tiffio.Tiff(str(tmp_path / "big.tif")).read_all(), tiff.read_all() + 2)
    monkeypatch.setattr(tiffio, "CLASSIC_LIMIT", 10 ** 9)
    tiff.rewrite(str(tmp_path / "small.tif"), change)
    assert not tiffio.Tiff(str(tmp_path / "small.tif")).big and size > 0


def old_dem_mask(path, target=footprint.TARGET_PX):
    tiff = tiffio.Tiff(path)
    nodata = relief.declared_nodata(tiff)
    step = max(1, int(round(max(tiff.width, tiff.height) / float(target))))
    mask = np.zeros((-(-tiff.height // step), -(-tiff.width // step)), dtype=bool)
    for top, left, block in tiff.blocks():
        good = ~relief.nodata_mask(block, nodata)
        rows, cols = np.nonzero(good)
        inside = (top + rows < tiff.height) & (left + cols < tiff.width)
        rows, cols = rows[inside], cols[inside]
        np.logical_or.at(mask, ((top + rows) // step, (left + cols) // step), True)
    return mask, step


@pytest.mark.parametrize("target", [800, 40, 11])
def test_dem_mask_without_ufunc_at_is_the_same_mask(tmp_path, target):
    path, _z = dem_file(tmp_path)
    new, step = footprint.dem_mask(path, target)
    old, old_step = old_dem_mask(path, target)
    assert step == old_step and np.array_equal(new, old)
