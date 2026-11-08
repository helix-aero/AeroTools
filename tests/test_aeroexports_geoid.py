"""Aero-Exports: geoid grids (our folder, state, zone), TIFF reading, application of the altitudes to a DTM."""
import hashlib
import os
import shutil
import struct

import numpy as np
import pytest

from aerotools.tools.aeroexports import crs, projdb, projtool, tiffio, vertical

HERE = os.path.dirname(os.path.abspath(__file__))
RAC23 = os.path.join(HERE, "data", "fr_ign_RAC23.tif")
CORSICA = (9.0, 42.0)
PARIS = (2.3, 48.8)

# Altitude of an ellipsoidal height of 0 m, computed with PROJ 9.8.1 + our RAC23 file (RGF93 v2b -> NGF-IGN78).
RAC23_PROJ = [((8.77, 41.39), -46.5833), ((9.0, 42.0), -49.2143), ((9.2, 42.5), -49.5548), ((8.6, 42.2), -47.6401)]


def touch(folder, name, content=b"x"):
    os.makedirs(str(folder), exist_ok=True)
    path = os.path.join(str(folder), name)
    with open(path, "wb") as fh:
        fh.write(content)
    return path


# -- folder, state -------------------------------------------------------------------------------

RAC23_ID, RAC09_ID = "fr_ign_RAC23", "fr_ign_RAC09"


def test_own_dir_is_a_geoid_folder_in_the_aerotools_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert crs.own_dir() == os.path.join(str(tmp_path), "HELIX", "AeroTools", "Geoid")


def test_the_official_file_is_ok_and_a_different_one_is_stale(tmp_path):
    spec = crs.vertical_by_id(RAC23_ID)
    shutil.copy(RAC23, str(tmp_path / spec["file"]))
    assert hashlib.sha256(open(RAC23, "rb").read()).hexdigest() == spec["sha256"]       # the test file is the official one
    assert crs.grid_state(RAC23_ID, str(tmp_path))[0] == "ok"
    touch(tmp_path, spec["file"], b"something else")
    assert crs.grid_state(RAC23_ID, str(tmp_path))[0] == "stale"
    assert crs.grid_state(RAC09_ID, str(tmp_path))[0] == "missing"


def test_a_rac09_file_never_stands_for_rac23(tmp_path):
    touch(tmp_path, "fr_ign_RAC09.tif")
    touch(tmp_path, "fr_ign_RAC09-2.tif")
    assert crs.grid_state(RAC23_ID, str(tmp_path))[0] == "missing"


def zone(epsg, place, folder):
    return [(spec["id"], state) for spec, state in crs.zone_grids(epsg, place[0], place[1], folder=str(folder))]


def test_zone_asks_for_the_small_grids_of_the_project_frame(tmp_path):
    assert zone(3942, CORSICA, tmp_path) == [(RAC09_ID, "missing"), ("us_nga_egm96_15", "missing")]    # v1: RAC09, not RAC23; EGM2008 (80 MB) not here
    assert zone(9842, CORSICA, tmp_path) == [(RAC23_ID, "missing"), ("us_nga_egm96_15", "missing")]
    assert [i for i, _s in zone(2154, PARIS, tmp_path)][:1] != []


def test_zone_lists_an_outdated_grid_and_skips_a_good_one(tmp_path):
    spec = crs.vertical_by_id(RAC23_ID)
    shutil.copy(RAC23, str(tmp_path / spec["file"]))
    assert zone(9842, CORSICA, tmp_path) == [("us_nga_egm96_15", "missing")]
    touch(tmp_path, spec["file"], b"damaged")
    assert (RAC23_ID, "stale") in zone(9842, CORSICA, tmp_path)


def test_zone_without_position_or_frame_asks_for_nothing(tmp_path):
    assert crs.zone_grids(3942, None, None, folder=str(tmp_path)) == []
    assert crs.zone_grids(None, 9.0, 42.0, folder=str(tmp_path)) == []


# -- which grid for which system, worldwide (rule read from PROJ: see projdb.py) ----------------------

def ids(epsg, place=CORSICA):
    return [spec["id"] for spec in vertical.compatible_verticals(epsg, *place)]


def test_a_v1_system_gets_the_v1_grid_and_a_v2b_system_the_v2b_one():
    assert ids(3942)[0] == RAC09_ID and RAC23_ID not in ids(3942)                # plain CC42: RAC09, not RAC23
    assert ids(9842)[0] == RAC23_ID and RAC09_ID not in ids(9842)
    assert "fr_ign_RAF09" in ids(2154, PARIS) and "fr_ign_RAF20" not in ids(2154, PARIS)
    assert "fr_ign_RAF20" in ids(9794, PARIS) and "fr_ign_RAF09" not in ids(9794, PARIS)


def test_every_list_ends_with_the_ellipsoid_and_offers_the_world_grids_when_the_frame_is_modern():
    for epsg, place in ((3942, CORSICA), (32632, CORSICA), (25832, PARIS)):
        listing = ids(epsg, place)
        assert listing[-1] == "ellipsoid" and "us_nga_egm96_15" in listing


def test_other_countries_get_their_own_grids():
    assert any(i.startswith("ch_swisstopo_chgeo2004") for i in ids(25832, (6.14, 46.2)))        # Switzerland, ETRS89 UTM
    assert "us_noaa_g2018u0" in ids(6347, (-74.0, 40.7))                                        # United States, NAD83(2011)
    assert any(i.startswith("au_ga_AUSGeoid2020") for i in ids(7856, (151.2, -33.9)))           # Australia, GDA2020


def test_a_local_datum_only_gets_the_ellipsoidal_height():
    assert ids(2056, (6.14, 46.2)) == ["ellipsoid"]                                              # CH1903+ / LV95 : datum local
    assert ids(27572, PARIS) == ["ellipsoid"]                                                    # NTF : datum local


def test_the_list_never_offers_a_grid_that_does_not_cover_the_project():
    assert "us_noaa_g2018u0" not in ids(6347, CORSICA)
    assert RAC23_ID not in ids(9842, (2.3, 48.8))


def test_frames_and_xy_candidates_come_from_the_proj_database():
    assert projdb.frame_of(3942)["family"] == "global" and projdb.frame_of(2056)["family"] != "global"
    assert projdb.geographic_epsg(3942) == 4171 and projdb.geographic_epsg(9842) == 9782 and projdb.geographic_epsg(32632) == 4326
    corsica = [code for code, _name in projdb.candidates(*CORSICA, limit=12)]
    assert {3942, 9842, 2154, 32632}.issubset(set(corsica)) and corsica.index(3942) < corsica.index(32632)
    assert [code for code, _n in projdb.candidates(*CORSICA, text="lambert-93")][:1] == [2154]
    assert projdb.candidates(None, None, text="3942") == [(3942, "RGF93 v1 / CC42")]
    assert projdb.candidates(None, None) == []


# -- grid reading (values from PROJ) ----------------------------------------------------------------------

def test_the_reader_matches_proj_on_rac23():
    grid = vertical.read_grid(RAC23)
    for (lon, lat), expected in RAC23_PROJ:
        assert -float(grid.undulation(lon, lat)) == pytest.approx(expected, abs=0.0005)


def test_the_catalog_footprint_is_the_node_footprint_of_the_file():
    grid = vertical.read_grid(RAC23)
    assert crs.vertical_by_id(RAC23_ID)["bbox"] == pytest.approx(tuple(round(v, 4) for v in grid.bounds()), abs=0.0002)


def test_outside_the_grid_there_is_no_value():
    grid = vertical.read_grid(RAC23)
    assert np.isnan(float(grid.undulation(2.3, 48.8)))
    values = grid.undulation(np.array([9.0, 2.3]), np.array([42.0, 48.8]))
    assert np.isfinite(values[0]) and np.isnan(values[1])


def test_the_tiff_reader_refuses_what_it_does_not_know(tmp_path):
    path = touch(tmp_path, "x.tif", b"not a tiff at all")
    with pytest.raises(ValueError):
        tiffio.Tiff(path)


# -- a DTM (hand-written TIFF, like Metashape's: Deflate tiles, georeferencing, nodata) ----------------

def make_dem(path, array, x0, y0, pixel, nodata=-32767.0, tile=16):
    """Float32 GeoTIFF with Deflate tiles, PixelIsArea, like Metashape's."""
    import zlib
    height, width = array.shape
    across, down = -(-width // tile), -(-height // tile)
    blocks = []
    for ty in range(down):
        for tx in range(across):
            block = np.full((tile, tile), nodata, dtype="<f4")
            part = array[ty * tile:(ty + 1) * tile, tx * tile:(tx + 1) * tile]
            block[:part.shape[0], :part.shape[1]] = part
            blocks.append(zlib.compress(block.tobytes()))
    data = b"".join(blocks)
    offsets, cursor = [], 8
    for item in blocks:
        offsets.append(cursor)
        cursor += len(item)
    values = {256: (4, [width]), 257: (4, [height]), 258: (3, [32]), 259: (3, [8]), 262: (3, [1]), 277: (3, [1]),
              322: (4, [tile]), 323: (4, [tile]), 324: (4, offsets), 325: (4, [len(b) for b in blocks]), 339: (3, [3]),
              33550: (12, [pixel, pixel, 0.0]), 33922: (12, [0.0, 0.0, 0.0, x0, y0, 0.0]),
              34735: (3, [1, 1, 0, 3, 1024, 0, 1, 1, 1025, 0, 1, 1, 3072, 0, 1, 3942])}
    codes = {3: "H", 4: "I", 12: "d"}
    entries, extra = [], b""
    ifd = cursor + (cursor % 2)
    table = 2 + 12 * (len(values) + 1) + 4
    for tag in sorted(list(values) + [42113]):
        if tag == 42113:
            raw = (repr(nodata) + "\0").encode()
            kind, count = 2, len(raw)
        else:
            kind, items = values[tag]
            raw = struct.pack("<" + codes[kind] * len(items), *items)
            count = len(items)
        if len(raw) <= 4:
            slot = raw.ljust(4, b"\0")
        else:
            slot = struct.pack("<I", ifd + table + len(extra))
            extra += raw + (b"\0" if len(raw) % 2 else b"")
        entries.append(struct.pack("<HHI", tag, kind, count) + slot)
    with open(path, "wb") as out:
        out.write(b"II" + struct.pack("<HI", 42, ifd) + data + b"\0" * (cursor % 2))
        out.write(struct.pack("<H", len(entries)) + b"".join(entries) + struct.pack("<I", 0) + extra)


X0, Y0, PIXEL = 1000.0, 2400.0, 0.5            # top-left corner and pixel of a 120 x 90 px DTM (60 x 45 m)


def fake_lonlat(x, y):
    """Test CRS: 1 m = 1e-5 degree, placed in Corsica (covered by the RAC23 grid)."""
    return 8.8 + (x - 1000.0) * 1e-5, 41.4 + (y - 2000.0) * 1e-5


def test_correct_dem_subtracts_the_grid_undulation(tmp_path):
    height = np.full((90, 120), 100.0, dtype=np.float32)
    height[10:20, 10:20] = -32767.0                           # holes
    src, dst = str(tmp_path / "dem.tif"), str(tmp_path / "alt.tif")
    make_dem(src, height, X0, Y0, PIXEL)
    grid = vertical.read_grid(RAC23)
    stats = vertical.correct_dem(src, dst, grid, fake_lonlat)
    out = tiffio.Tiff(dst).read_all()
    assert out.shape == height.shape
    rows, cols = np.mgrid[0:90, 0:120]
    lon, lat = fake_lonlat(X0 + (cols + 0.5) * PIXEL, Y0 - (rows + 0.5) * PIXEL)
    expected = 100.0 - grid.undulation(lon, lat)
    good = height != -32767.0
    assert np.abs(out[good] - expected[good]).max() < 0.005
    assert (out[~good] == -32767.0).all()                     # the "no data" value stays as it is
    assert 46.0 < stats["min"] <= stats["max"] < 48.0 and stats["check_mm"] < 5.0
    assert not os.path.exists(dst + ".part")


def test_correct_dem_keeps_the_georeferencing(tmp_path):
    src, dst = str(tmp_path / "dem.tif"), str(tmp_path / "alt.tif")
    make_dem(src, np.full((90, 120), 5.0, dtype=np.float32), X0, Y0, PIXEL)
    vertical.correct_dem(src, dst, vertical.read_grid(RAC23), fake_lonlat)
    before, after = tiffio.Tiff(src), tiffio.Tiff(dst)
    for tag in (tiffio.T_SCALE, tiffio.T_TIEPOINT, tiffio.T_GEOKEYS, tiffio.T_GDAL_NODATA):
        assert before.tags[tag][2] == after.tags[tag][2]


def test_correct_dem_refuses_a_dem_outside_the_grid_and_writes_nothing(tmp_path):
    src, dst = str(tmp_path / "dem.tif"), str(tmp_path / "alt.tif")
    make_dem(src, np.full((90, 120), 5.0, dtype=np.float32), X0, Y0, PIXEL)
    with pytest.raises(ValueError, match="does not cover"):
        vertical.correct_dem(src, dst, vertical.read_grid(RAC23),
                              lambda x, y: (np.full(np.shape(x), 2.3), np.full(np.shape(x), 48.8)))
    assert not os.path.exists(dst) and not os.path.exists(dst + ".part")


def test_the_lattice_is_never_bigger_than_the_cap():
    cols, rows = vertical._lattice(10 ** 7, 10 ** 7, 0.01, 0.01)
    assert cols == rows == vertical.MAX_LATTICE
    assert vertical._lattice(10, 10, 0.5, 0.5) == (2, 2)


# -- embedded PROJ 9 ---------------------------------------------------------------------------------

DATA = os.path.join(HERE, "data")
needs_proj = pytest.mark.skipif(os.name != "nt" or not projtool.Proj().available(), reason="Windows PROJ binaries missing")


def engine():
    return projtool.Proj(geoid_dir=DATA)


@needs_proj
def test_proj_is_the_embedded_9_series():
    assert "Rel. 9." in engine().version()


@needs_proj
def test_proj_vertical_shift_matches_the_reference_values():
    lons = np.array([p[0][0] for p in RAC23_PROJ])
    lats = np.array([p[0][1] for p in RAC23_PROJ])
    shift = engine().vertical_shift("fr_ign_RAC23.tif", lons, lats)
    assert shift == pytest.approx([p[1] for p in RAC23_PROJ], abs=0.0002)


@needs_proj
def test_proj_shift_is_nan_outside_the_grid():
    shift = engine().vertical_shift("fr_ign_RAC23.tif", np.array([9.0, 2.3]), np.array([42.0, 48.8]))
    assert np.isfinite(shift[0]) and np.isnan(shift[1])


@needs_proj
def test_proj_converts_projected_points_to_longitude_latitude():
    lon, lat = engine().lonlat(3942, np.array([2200000.0]), np.array([1200000.0]))
    assert (float(lon[0]), float(lat[0])) == pytest.approx((9.0254967, 41.8415304), abs=1e-6)


@needs_proj
def test_proj_and_our_reader_agree_on_the_whole_lattice_of_a_dem(tmp_path):
    src, dst = str(tmp_path / "dem.tif"), str(tmp_path / "alt.tif")
    make_dem(src, np.full((90, 120), 100.0, dtype=np.float32), X0, Y0, PIXEL)
    proj = engine()
    stats = vertical.correct_dem(src, dst, vertical.read_grid(RAC23), lambda xs, ys: (fake_lonlat(np.asarray(xs), np.asarray(ys))),
                                 reference=lambda lon, lat: -proj.vertical_shift("fr_ign_RAC23.tif", lon, lat))
    assert stats["proj_mm"] < 1.0


def test_a_reference_that_disagrees_with_us_stops_the_export(tmp_path):
    src, dst = str(tmp_path / "dem.tif"), str(tmp_path / "alt.tif")
    make_dem(src, np.full((90, 120), 100.0, dtype=np.float32), X0, Y0, PIXEL)
    grid = vertical.read_grid(RAC23)
    with pytest.raises(ValueError, match="differ"):
        vertical.correct_dem(src, dst, grid, fake_lonlat, reference=lambda lon, lat: grid.undulation(lon, lat) + 0.05)
    assert not os.path.exists(dst)


def test_proj_output_is_parsed_by_batch_and_failures_become_nan():
    def fake(exe, args, text):
        assert len(text.splitlines()) == 3
        return "\n".join(["1 2 3 0", "4 5 inf 0", "7 8 9 0"]) + "\n"

    proj = projtool.Proj(vendor=os.path.join(HERE, "no_proj"), runner=fake)
    shift = proj.vertical_shift("x.tif", np.array([1.0, 2.0, 3.0]), np.array([1.0, 2.0, 3.0]))
    assert shift[0] == 3.0 and np.isnan(shift[1]) and shift[2] == 9.0
    with pytest.raises(projtool.ProjError):
        projtool.Proj(runner=lambda *a: "1 2 3 0\n").vertical_shift("x.tif", np.array([1.0, 2.0]), np.array([1.0, 2.0]))


def test_geographic_frame_of_a_projected_system():
    assert [projtool.geographic_epsg(e) for e in (2154, 3942, 9794, 9842, 32632, 25832)] == [4171, 4171, 9782, 9782, 4326, 4258]


def test_depth_surfaces_are_never_offered_as_heights():
    flipped = [g["id"] for g in projdb.grids() if g["depth"]]
    assert "dk_sdfi_dklat_2022" in flipped and len(flipped) == 7
    assert not set(flipped) & set(ids(25832, (10.2, 56.1)))                    # Denmark, ETRS89 UTM


def test_every_grid_of_the_catalog_is_a_height_offset_with_its_sha256():
    for grid in projdb.grids():
        assert len(grid["sha256"]) == 64 and grid["size"] > 0 and grid["url"].startswith("https://cdn.proj.org/")
        assert grid["bbox"][0] <= grid["bbox"][2] and grid["bbox"][1] < grid["bbox"][3]
