"""Fast Export : grilles de geoide (notre dossier, etat, zone), lecture TIFF, application des altitudes a un MNT."""
import hashlib
import os
import shutil
import struct

import numpy as np
import pytest

from aerotools.tools.fastexport import crs, tiffio, vertical

HERE = os.path.dirname(os.path.abspath(__file__))
RAC23 = os.path.join(HERE, "data", "fr_ign_RAC23.tif")
CORSICA = (9.0, 42.0)
PARIS = (2.3, 48.8)

# Altitude d'une hauteur ellipsoidale de 0 m, calculee avec PROJ 9.8.1 + notre fichier RAC23 (RGF93 v2b -> NGF-IGN78).
RAC23_PROJ = [((8.77, 41.39), -46.5833), ((9.0, 42.0), -49.2143), ((9.2, 42.5), -49.5548), ((8.55, 41.9), -46.8131)]


def touch(folder, name, content=b"x"):
    os.makedirs(str(folder), exist_ok=True)
    path = os.path.join(str(folder), name)
    with open(path, "wb") as fh:
        fh.write(content)
    return path


# -- dossier, etat -------------------------------------------------------------------------------

def test_own_dir_is_a_geoid_folder_in_the_aerotools_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert crs.own_dir() == os.path.join(str(tmp_path), "HELIX", "AeroTools", "Geoid")


def test_the_official_file_is_ok_and_a_different_one_is_stale(tmp_path, monkeypatch):
    spec = crs.vertical_by_id("RAC23")
    shutil.copy(RAC23, str(tmp_path / spec["file"]))
    assert hashlib.sha256(open(RAC23, "rb").read()).hexdigest() == spec["sha256"]       # le fichier de test est l'officiel
    assert crs.grid_state("RAC23", str(tmp_path))[0] == "ok"
    touch(tmp_path, spec["file"], b"autre chose")
    assert crs.grid_state("RAC23", str(tmp_path))[0] == "stale"
    assert crs.grid_state("RAC09", str(tmp_path))[0] == "missing"


def test_a_rac09_file_never_stands_for_rac23(tmp_path):
    touch(tmp_path, "fr_ign_RAC09.tif")
    touch(tmp_path, "fr_ign_RAC09-2.tif")
    assert crs.grid_state("RAC23", str(tmp_path))[0] == "missing"


def test_zone_in_corsica_asks_for_the_corsican_grids_and_the_world_one(tmp_path):
    ids = [spec["id"] for spec, _state in crs.zone_grids(*CORSICA, folder=str(tmp_path))]
    assert ids == ["RAC23", "RAC09", "EGM96"]
    assert all(state == "missing" for _spec, state in crs.zone_grids(*CORSICA, folder=str(tmp_path)))


def test_zone_on_the_continent_asks_for_the_french_grids(tmp_path):
    assert [spec["id"] for spec, _s in crs.zone_grids(*PARIS, folder=str(tmp_path))] == ["RAF20", "RAF09", "EGM96"]


def test_zone_lists_an_outdated_grid_and_skips_a_good_one(tmp_path):
    spec = crs.vertical_by_id("RAC23")
    shutil.copy(RAC23, str(tmp_path / spec["file"]))
    assert [s["id"] for s, _state in crs.zone_grids(*CORSICA, folder=str(tmp_path))] == ["RAC09", "EGM96"]
    touch(tmp_path, spec["file"], b"abime")
    assert ("RAC23", "stale") in [(s["id"], state) for s, state in crs.zone_grids(*CORSICA, folder=str(tmp_path))]


def test_zone_without_position_asks_for_nothing(tmp_path):
    assert crs.zone_grids(None, None, folder=str(tmp_path)) == []


# -- quelle grille pour quel repere ----------------------------------------------------------------

def ids(epsg, place=CORSICA):
    return [spec["id"] for spec in vertical.compatible_verticals(epsg, *place)]


def test_a_v1_system_gets_the_v1_grids():
    assert ids(3942) == ["RAC09", "EGM96", "ellipsoid"]                  # CC42 de base : RAC09, pas RAC23
    assert ids(2154) == ["RAC09", "EGM96", "ellipsoid"]
    assert ids(2154, PARIS) == ["RAF09", "EGM96", "ellipsoid"]


def test_a_v2b_system_gets_the_v2b_grids():
    assert ids(9842) == ["RAC23", "EGM96", "ellipsoid"]
    assert ids(9794, PARIS) == ["RAF20", "EGM96", "ellipsoid"]


def test_a_system_without_frame_gets_the_recent_grids():
    assert ids(32632) == ["RAC23", "EGM96", "ellipsoid"]
    assert vertical.horizontal_frame(32632) is None
    assert (vertical.horizontal_frame(3942), vertical.horizontal_frame(9842), vertical.horizontal_frame(4171)) == ("v1", "v2b", "v1")


def test_every_catalog_system_has_a_frame_that_matches_its_name():
    for item in crs.CRS_LIST:
        name = item["en"]
        frame = vertical.horizontal_frame(item["epsg"])
        if "v2b" in name:
            assert frame == "v2b", name
        elif "RGF93 v1" in name:
            assert frame == "v1", name


# -- lecture de grille (valeurs de PROJ) -----------------------------------------------------------------

def test_the_reader_matches_proj_on_rac23():
    grid = vertical.read_grid(RAC23)
    for (lon, lat), expected in RAC23_PROJ:
        assert -float(grid.undulation(lon, lat)) == pytest.approx(expected, abs=0.0005)


def test_the_catalog_footprint_is_the_node_footprint_of_the_file():
    grid = vertical.read_grid(RAC23)
    assert crs.vertical_by_id("RAC23")["bbox"] == pytest.approx(tuple(round(v, 4) for v in grid.bounds()), abs=0.0002)


def test_outside_the_grid_there_is_no_value():
    grid = vertical.read_grid(RAC23)
    assert np.isnan(float(grid.undulation(2.3, 48.8)))
    values = grid.undulation(np.array([9.0, 2.3]), np.array([42.0, 48.8]))
    assert np.isfinite(values[0]) and np.isnan(values[1])


def test_the_tiff_reader_refuses_what_it_does_not_know(tmp_path):
    path = touch(tmp_path, "x.tif", b"not a tiff at all")
    with pytest.raises(ValueError):
        tiffio.Tiff(path)


# -- un MNT (TIFF ecrit a la main, comme Metashape : tuiles Deflate, georeferencement, nodata) ------------

def make_dem(path, array, x0, y0, pixel, nodata=-32767.0, tile=16):
    """GeoTIFF float32 a tuiles Deflate, PixelIsArea, comme celui de Metashape."""
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


X0, Y0, PIXEL = 1000.0, 2400.0, 0.5            # coin haut-gauche et pixel d'un MNT de 120 x 90 px (60 x 45 m)


def fake_lonlat(x, y):
    """SCR de test : 1 m = 1e-5 degre, pose en Corse (la grille RAC23 couvre)."""
    return 8.8 + (x - 1000.0) * 1e-5, 41.4 + (y - 2000.0) * 1e-5


def test_correct_dem_subtracts_the_grid_undulation(tmp_path):
    height = np.full((90, 120), 100.0, dtype=np.float32)
    height[10:20, 10:20] = -32767.0                           # trous
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
    assert (out[~good] == -32767.0).all()                     # le « pas de donnee » reste tel quel
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
    with pytest.raises(ValueError, match="ne couvre pas"):
        vertical.correct_dem(src, dst, vertical.read_grid(RAC23), lambda x, y: (2.3, 48.8))
    assert not os.path.exists(dst) and not os.path.exists(dst + ".part")


def test_the_lattice_is_never_bigger_than_the_cap():
    cols, rows = vertical._lattice(10 ** 7, 10 ** 7, 0.01, 0.01)
    assert cols == rows == vertical.MAX_LATTICE
    assert vertical._lattice(10, 10, 0.5, 0.5) == (2, 2)
