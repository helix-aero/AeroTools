"""Fast Export: the look of the DEM picture (palette, shading, colour range): settings, palettes as data, shared render."""

import json
import os
import struct
import zlib

import numpy as np
import pytest

from aerotools import config
from aerotools.tools.fastexport import look as looks
from aerotools.tools.fastexport import relief, tiffio
from test_fastexport_geoid import make_dem


# -- the look value and its settings -----------------------------------------------------------------

def test_the_default_look_is_metashapes_palette_with_a_north_west_sun():
    d = looks.DEFAULT
    assert (d.palette, d.shading, d.azimuth, d.altitude, d.strength, d.stretch) == ("metashape", "sun", 315.0, 45.0, 0.8, "full")
    assert looks.is_default(d) and looks.load() == d                                  # nothing saved yet: the default look


def test_a_look_survives_the_settings_file_and_comes_back_identical():
    mine = looks.Look("viridis", "lights4", 120.0, 30.0, 0.5, "custom", 1.5, 9.25)
    assert looks.save(mine)
    assert looks.load() == mine
    assert config.get("fastexport", "dem_look")["palette"] == "viridis"               # in the tool's own section, plain JSON
    assert not looks.is_default(mine)


def test_damaged_settings_never_break_the_look():
    for bad in (None, "text", 12, [], {"palette": 3, "shading": "disco", "azimuth": "x", "altitude": 999, "strength": -4,
                                        "stretch": "weird"}):
        got = looks.normalized(bad)
        assert got.shading in looks.SHADINGS and got.stretch in looks.STRETCHES and 0 <= got.strength <= 1
        assert looks.ALTITUDE_RANGE[0] <= got.altitude <= looks.ALTITUDE_RANGE[1] and 0 <= got.azimuth < 360
    config.put("fastexport", "dem_look", "garbage")
    assert looks.load() == looks.DEFAULT
    assert looks.normalized({"azimuth": 725}).azimuth == 5.0 and looks.normalized({"azimuth": -90}).azimuth == 270.0


def test_a_custom_range_needs_two_values_in_order_else_the_full_range_is_used():
    for low, high in ((None, 5), (3, None), (5, 5), (6, 5), ("a", "b")):
        got = looks.normalized({"stretch": "custom", "low": low, "high": high})
        assert got.stretch == "full" and got.low is None and got.high is None
    ok = looks.normalized({"stretch": "custom", "low": 2.5, "high": 10})
    assert (ok.stretch, ok.low, ok.high) == ("custom", 2.5, 10.0)
    assert looks.normalized({"stretch": "auto", "low": 1, "high": 2}).low is None     # the numbers only mean something when custom


# -- palettes as data ---------------------------------------------------------------------------------

def test_the_package_ships_the_six_palettes_asked_for_and_all_are_valid():
    found = looks.palettes()
    assert {"metashape", "relief", "rainbow", "gray", "viridis", "terrain"} <= set(found)
    assert list(found)[0] == "metashape"                                              # the default comes first
    for entry in found.values():
        positions = [p for p, _rgb in entry["stops"]]
        assert positions == sorted(positions) and 0 <= positions[0] and positions[-1] <= 1 and len(positions) >= 2
        assert all(0 <= c <= 255 for _p, rgb in entry["stops"] for c in rgb)
        assert looks.label(entry, "fr") and looks.label(entry, "en")
        table = relief.table_for(entry)
        assert table.shape == (256, 3) and table.dtype == np.uint8


def test_the_metashape_palette_in_the_json_file_is_the_one_the_export_always_used():
    assert np.array_equal(relief.table_for(looks.palette("metashape")), relief.lookup_table(relief.METASHAPE_RAMP))
    assert np.array_equal(relief.table_for(looks._builtin()), relief.lookup_table(looks.METASHAPE_RAMP))


def test_a_palette_file_in_the_settings_folder_adds_or_replaces_palettes(tmp_path):
    os.makedirs(config.settings_dir(), exist_ok=True)
    mine = {"schema": 1, "palettes": [
        {"id": "lava", "label": {"en": "Lava"}, "stops": [[0, [0, 0, 0]], [1, [255, 60, 0]]]},
        {"id": "gray", "label": {"en": "My grey"}, "stops": [[0, [0, 0, 0]], [1, [255, 255, 255]]]},
        {"id": "broken", "stops": [[0, [0, 0, 0]]]},                                  # one stop: refused
        {"id": "worse", "stops": [[0, [0, 0, 0]], [0, [1, 1, 1]]]},                   # positions not increasing: refused
        {"id": "dark", "stops": [[0, [0, 0, 300]], [1, [1, 1, 1]]]}]}                 # colour out of range: refused
    with open(looks.user_file(), "w", encoding="utf-8") as handle:
        json.dump(mine, handle)
    found = looks.palettes()
    assert "lava" in found and looks.label(found["lava"], "fr") == "Lava"            # no French name: falls back to English
    assert looks.label(found["gray"], "en") == "My grey"                              # same id: the user's version wins
    assert not {"broken", "worse", "dark"} & set(found) and "metashape" in found


def test_a_broken_palette_file_is_ignored_and_the_metashape_palette_always_exists():
    os.makedirs(config.settings_dir(), exist_ok=True)
    open(looks.user_file(), "w").write("{ not json")
    assert "metashape" in looks.palettes() and len(looks.palettes()) >= 6
    assert looks.palette("no-such-palette")["id"] == "metashape"                      # a saved look can outlive a removed palette


# -- the render shared by the preview and the export --------------------------------------------------

def hill_dem(path, nodata_rows=0, shape=(60, 80), pixel=0.5):
    rows, cols = np.mgrid[0:shape[0], 0:shape[1]]
    z = (2.0 + 0.05 * cols + 0.8 * np.sin(rows / 5.0) + 0.5 * np.cos(cols / 7.0)).astype("<f4")
    if nodata_rows:
        z[:nodata_rows, :] = -32767.0
    make_dem(path, z, 1000.0, 2400.0, pixel)
    return z


def read_rgb(path):
    """Pixels of an RGB TIFF written by relief.render (Deflate strips)."""
    tiff = tiffio.Tiff(path, strict=False)
    rows = []
    with open(path, "rb") as handle:
        for offset, count in zip(tiff.offsets, tiff.counts):
            handle.seek(offset)
            rows.append(np.frombuffer(zlib.decompress(handle.read(count)), dtype=np.uint8).reshape(-1, tiff.width, 3))
    return np.concatenate(rows)[:tiff.height]


def legacy_picture(z, nodata=-32767.0):
    """The picture the export drew before looks existed (Metashape palette, Horn sun 315/45, strength 0.8, min to max, white void)."""
    bad = ~np.isfinite(z) | (z < -9000.0) | (z == nodata)
    low, high = float(z[~bad].min()), float(z[~bad].max())
    filled = np.pad(np.where(bad, low, z).astype(np.float64), 1, mode="edge")
    shade = relief.hillshade(filled, 0.5, 0.5, 0.8)
    return relief.colorize(z, low, high, relief.lookup_table(), shade, nodata)


def test_the_default_look_paints_exactly_what_the_export_painted_before(tmp_path):
    dem, out = str(tmp_path / "dem.tif"), str(tmp_path / "view.tif")
    z = hill_dem(dem, nodata_rows=4)
    ok, message = relief.render(dem, out)
    assert ok, message
    picture, legacy = read_rgb(out), legacy_picture(z)
    bad = ~np.isfinite(z) | (z < -9000.0)
    touching = np.zeros_like(bad)                                  # data pixels next to no data: the slope there no longer sees a cliff
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            touching |= np.roll(np.roll(bad, dr, axis=0), dc, axis=1)
    assert np.array_equal(picture[~touching], legacy[~touching])   # everywhere else the picture is the one made before looks existed
    assert np.array_equal(picture[bad], legacy[bad])               # and no data stays white
    ok, _ = relief.render(dem, str(tmp_path / "again.tif"), look=looks.DEFAULT)
    assert ok and open(out, "rb").read() == open(str(tmp_path / "again.tif"), "rb").read()


def test_each_shading_style_gives_its_own_picture_and_colours_only_has_no_shade(tmp_path):
    dem = str(tmp_path / "dem.tif")
    z = hill_dem(dem)
    pictures = {}
    for shading in looks.SHADINGS:
        out = str(tmp_path / (shading + ".tif"))
        assert relief.render(dem, out, look=looks.DEFAULT._replace(shading=shading))[0]
        pictures[shading] = read_rgb(out)
    assert len({picture.tobytes() for picture in pictures.values()}) == 4
    table = relief.lookup_table()
    low, high = float(z.min()), float(z.max())
    flat = relief.colorize(z, low, high, table)
    assert np.array_equal(pictures["none"], flat)                                     # no shading: the bare palette
    assert pictures["slope"].astype(int).sum() < pictures["none"].astype(int).sum()   # slope only darkens


def test_strength_zero_means_no_shading_whatever_the_style(tmp_path):
    dem = str(tmp_path / "dem.tif")
    hill_dem(dem)
    base = str(tmp_path / "none.tif")
    relief.render(dem, base, look=looks.DEFAULT._replace(shading="none"))
    for shading in ("sun", "lights4", "slope"):
        out = str(tmp_path / (shading + "0.tif"))
        relief.render(dem, out, look=looks.DEFAULT._replace(shading=shading, strength=0.0))
        assert open(out, "rb").read() == open(base, "rb").read()


def test_the_sun_moves_the_light_and_four_lights_do_not_depend_on_a_single_side():
    ramp = np.tile(np.arange(9, dtype=float), (9, 1))                                 # climbs to the east: the slope faces west
    mirror = ramp[:, ::-1].copy()                                                     # climbs to the west: the slope faces east
    sun_nw = looks.DEFAULT
    assert relief.shade_factor(ramp, 1.0, 1.0, sun_nw)[3, 3] > 1.0 > relief.shade_factor(mirror, 1.0, 1.0, sun_nw)[3, 3]
    sun_se = sun_nw._replace(azimuth=135.0)                                           # the sun moved to the other side
    assert relief.shade_factor(mirror, 1.0, 1.0, sun_se)[3, 3] > 1.0 > relief.shade_factor(ramp, 1.0, 1.0, sun_se)[3, 3]
    four = looks.DEFAULT._replace(shading="lights4")
    a = relief.shade_factor(ramp, 1.0, 1.0, four)[3, 3]
    b = relief.shade_factor(mirror, 1.0, 1.0, four)[3, 3]
    assert a == pytest.approx(b)                                                      # same slope seen from the other side: same light
    assert relief.shade_factor(np.full((5, 5), 7.0), 1.0, 1.0, four) == pytest.approx(np.ones((3, 3)))


def test_the_colour_range_can_be_full_automatic_or_typed_by_the_user(tmp_path):
    dem = str(tmp_path / "dem.tif")
    z = hill_dem(dem)
    z[0, 0] = 400.0                                                                    # one spike
    make_dem(dem, z, 1000.0, 2400.0, 0.5)
    span = relief.value_range(dem)
    assert span[1] == pytest.approx(400.0)
    auto = relief.percentile_range(dem, span)
    assert auto[1] < 20 and span[0] - 1e-6 <= auto[0] < auto[1]                      # the spike no longer stretches the scale
    assert relief.choose_range(looks.DEFAULT, span) == span
    assert relief.choose_range(looks.DEFAULT._replace(stretch="auto"), span, lambda: auto) == auto
    custom = looks.DEFAULT._replace(stretch="custom", low=1.0, high=3.0)
    assert relief.choose_range(custom, span) == (1.0, 3.0)
    assert relief.choose_range(custom._replace(high=0.5), span) == span               # nonsense custom range: the full one
    full_png, auto_png = str(tmp_path / "f.tif"), str(tmp_path / "a.tif")
    relief.render(dem, full_png, look=looks.DEFAULT._replace(shading="none"))
    relief.render(dem, auto_png, look=looks.DEFAULT._replace(shading="none", stretch="auto"))
    assert open(full_png, "rb").read() != open(auto_png, "rb").read()


def test_a_custom_range_clips_the_colours_at_both_ends():
    z = np.array([[0.0, 5.0, 10.0, 20.0]], dtype=np.float32)
    table = relief.lookup_table()
    image = relief.colorize(z, 5.0, 10.0, table)
    assert tuple(image[0, 0]) == tuple(table[0]) == tuple(image[0, 1])                 # at or under the low end: the lowest colour
    assert tuple(image[0, 3]) == tuple(table[255]) == tuple(image[0, 2])


def test_the_void_colour_is_chosen_by_the_caller_and_nan_is_void_without_warnings():
    z = np.array([[1.0, np.nan, 3.0]], dtype=np.float32)
    table = relief.lookup_table()
    with np.errstate(all="raise"):
        white = relief.colorize(z, 1.0, 3.0, table)
        black = relief.colorize(z, 1.0, 3.0, table, void=(0, 0, 0))
    assert tuple(white[0, 1]) == (255, 255, 255) and tuple(black[0, 1]) == (0, 0, 0)
    assert tuple(white[0, 0]) == tuple(black[0, 0]) == tuple(table[0])


def test_a_geographic_dem_has_no_shading_but_is_still_coloured(tmp_path):
    dem, out = str(tmp_path / "dem.tif"), str(tmp_path / "view.tif")
    z = hill_dem(dem, pixel=0.00001)                                                   # degrees
    assert relief.render(dem, out)[0]
    flat = relief.colorize(z, float(z.min()), float(z.max()), relief.lookup_table())
    assert np.array_equal(read_rgb(out), flat)


def test_no_dark_line_along_the_border_of_the_data(tmp_path):
    """Filling the no-data pixels with the lowest height made a cliff at every border: a dark line on the sunny side."""
    z = np.full((40, 40), 10.0, dtype=np.float32)                   # flat ground: nothing should be shaded
    z[:, :10] = -32767.0
    filled = relief.fill_for_shading(z, z < -9000.0, 10.0)
    shade = relief.shade_factor(np.pad(filled, 1, mode="edge"), 0.5, 0.5, looks.DEFAULT)
    assert np.allclose(shade[:, 8:], 1.0, atol=1e-6)
    cliff = relief.shade_factor(np.pad(np.where(z < -9000.0, 10.0 - 5.0, z).astype(np.float64), 1, mode="edge"), 0.5, 0.5, looks.DEFAULT)
    assert abs(cliff[:, 9:11] - 1.0).max() > 0.05                    # what the old filling did
