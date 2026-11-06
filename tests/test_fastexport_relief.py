"""Fast Export : image du MNT en relief colore (palette de Metashape + ombrage), sans GDAL."""

import numpy as np
import pytest

from aerotools.tools.fastexport import relief
from test_fastexport_geoid import make_dem


def test_the_palette_runs_from_blue_to_red_like_metashape():
    table = relief.lookup_table()
    assert table.shape == (256, 3)
    assert tuple(table[0]) == (47, 55, 218) and tuple(table[255]) == (225, 53, 56)
    assert table[128][1] > table[128][0] and table[128][1] > table[128][2]            # le milieu est vert
    assert table[0][2] > table[0][0] and table[255][0] > table[255][2]                # bas bleu, haut rouge


def test_flat_ground_is_not_shaded_and_a_slope_is_lit_or_dark_by_its_side():
    flat = relief.hillshade(np.full((5, 5), 10.0), 1.0, 1.0)
    assert flat == pytest.approx(np.ones((3, 3)))
    ramp = np.tile(np.arange(5, dtype=float), (5, 1))                                 # monte vers l'est (versant tourne a l'ouest)
    up_east = relief.hillshade(ramp, 1.0, 1.0)[1, 1]
    up_west = relief.hillshade(ramp[:, ::-1], 1.0, 1.0)[1, 1]
    assert up_east > 1.0 > up_west                                                    # lumiere du nord-ouest : un versant tourne a l'ouest est eclaire


def test_nodata_pixels_are_white_and_the_declared_nodata_value_is_honoured():
    z = np.array([[0.0, 5.0, -32767.0, 10.0]], dtype=np.float32)
    table = relief.lookup_table()
    image = relief.colorize(z, 0.0, 10.0, table)
    assert tuple(image[0, 2]) == (255, 255, 255) and tuple(image[0, 0]) == tuple(table[0])
    assert tuple(relief.colorize(z, 0.0, 10.0, table, nodata=0.0)[0, 0]) == (255, 255, 255)


def test_render_writes_a_georeferenced_rgb_geotiff_from_a_metashape_style_dem(tmp_path):
    height, width = 40, 60
    rows, cols = np.mgrid[0:height, 0:width]
    z = (2.0 + 0.05 * cols + 0.5 * np.sin(rows / 4.0)).astype("<f4")
    z[:5, :] = -32767.0                                                               # une bande sans donnee
    dem, out = str(tmp_path / "dem.tif"), str(tmp_path / "view.tif")
    make_dem(dem, z, 1000.0, 2400.0, 0.5)
    ok, message = relief.render(dem, out)
    assert ok, message
    assert relief.value_range(dem) == pytest.approx((float(z[5:].min()), float(z.max())))
    data = open(out, "rb").read()
    assert data[:2] == b"II" and len(data) > 100
    # relu avec le lecteur de TIFF : 3 plans de 8 bits, georeferencement du MNT recopie
    import struct
    end = "<"
    ifd = struct.unpack(end + "I", data[4:8])[0]
    count = struct.unpack(end + "H", data[ifd:ifd + 2])[0]
    tags = {struct.unpack(end + "H", data[ifd + 2 + 12 * i:ifd + 4 + 12 * i])[0] for i in range(count)}
    assert {256, 257, 258, 262, 277, 33550, 33922, 34735} <= tags


def test_render_refuses_a_dem_without_any_data(tmp_path):
    dem = str(tmp_path / "empty.tif")
    make_dem(dem, np.full((10, 10), -32767.0, dtype="<f4"), 0.0, 0.0, 0.5)
    ok, message = relief.render(dem, str(tmp_path / "view.tif"))
    assert not ok and "without data" in message
