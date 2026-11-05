"""Fast Export : detection des grilles de geoide, dossier Geoid d'AeroTools, grilles de la zone."""
import os
import types

from aerotools import msapi
from aerotools.tools.fastexport import crs

CORSICA = (9.0, 42.0)
PARIS = (2.3, 48.8)


def touch(folder, name, size=10):
    os.makedirs(str(folder), exist_ok=True)
    path = os.path.join(str(folder), name)
    with open(path, "wb") as fh:
        fh.write(b"x" * size)
    return path


def test_own_dir_is_a_geoid_folder_in_the_aerotools_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert crs.own_dir() == os.path.join(str(tmp_path), "HELIX", "AeroTools", "Geoid")
    assert crs.search_dirs()[0] == crs.own_dir()


def test_a_rac09_grid_is_not_taken_for_rac23(tmp_path):
    touch(tmp_path, "fr_ign_RAC09.tif")
    touch(tmp_path, "fr_ign_RAC09-2.tif")
    assert crs.find_grid("RAC23", [str(tmp_path)]) == []
    assert [m["by"] for m in crs.find_grid("RAC09", [str(tmp_path)])] == ["copy", "name"]


def test_find_grid_accepts_the_exact_name_and_numbered_copies(tmp_path):
    touch(tmp_path, "fr_ign_RAF20.tif")
    touch(tmp_path, "fr_ign_RAF20-3.tif")
    touch(tmp_path, "fr_ign_RAF20-x.tif")
    found = crs.find_grid("RAF20", [str(tmp_path)])
    assert sorted(os.path.basename(m["path"]) for m in found) == ["fr_ign_RAF20-3.tif", "fr_ign_RAF20.tif"]


def test_zone_in_corsica_asks_for_the_corsican_grid_only(tmp_path):
    touch(tmp_path, "fr_ign_RAC09.tif")
    touch(tmp_path, "egm96-15.tif")
    assert [s["id"] for s in crs.missing_in_zone(*CORSICA, dirs=[str(tmp_path)])] == ["RAC23"]
    touch(tmp_path, "fr_ign_RAC23.tif")
    assert crs.missing_in_zone(*CORSICA, dirs=[str(tmp_path)]) == []


def test_zone_on_the_continent_asks_for_the_french_grids(tmp_path):
    assert [s["id"] for s in crs.missing_in_zone(*PARIS, dirs=[str(tmp_path)])] == ["RAF20", "RAF09", "EGM96"]


def test_zone_without_position_asks_for_nothing(tmp_path):
    assert crs.missing_in_zone(None, None, dirs=[str(tmp_path)]) == []


def test_register_geoids_gives_each_grid_once(tmp_path, ms):
    given = []
    ms.CoordinateSystem = types.SimpleNamespace(addGeoid=given.append)
    msapi._GEOIDS_GIVEN.clear()
    first, _skip = touch(tmp_path, "fr_ign_RAC23.tif"), touch(tmp_path, "notes.txt")
    assert msapi.register_geoids(str(tmp_path)) == [first]
    assert msapi.register_geoids(str(tmp_path)) == [first]
    assert given == [first]
    assert msapi.register_geoids(str(tmp_path / "absent")) == []


def test_register_geoids_skips_a_grid_metashape_refuses(tmp_path, ms):
    def refuse(path):
        raise RuntimeError("bad grid")

    ms.CoordinateSystem = types.SimpleNamespace(addGeoid=refuse)
    msapi._GEOIDS_GIVEN.clear()
    touch(tmp_path, "fr_ign_RAC23.tif")
    assert msapi.register_geoids(str(tmp_path)) == []


def test_the_running_metashape_install_geoids_are_searched(monkeypatch, tmp_path):
    monkeypatch.setattr(crs.sys, "executable", str(tmp_path / "Metashape_v22" / "python" / "python.exe"))
    dirs = [os.path.normcase(d) for d in crs.search_dirs()]
    assert os.path.normcase(str(tmp_path / "Metashape_v22" / "geoids")) in dirs
    assert not any("Metashape Pro" in d and "Program Files" in d for d in dirs)


class FakeVector(tuple):
    pass


def fake_transform(heights):
    def transform(point, source, target):
        return (point[0], point[1], heights[target])
    return transform


def test_geoid_applied_compares_the_height_with_and_without_the_grid(ms):
    ms.Vector = lambda values: FakeVector(values)
    ms.CoordinateSystem = lambda text: text
    ms.CoordinateSystem.transform = staticmethod(fake_transform({"with": -47.6, "flat": 0.0}))
    assert msapi.geoid_applied("with", "flat", (9.0, 42.0)) is True
    ms.CoordinateSystem.transform = staticmethod(fake_transform({"with": 0.0, "flat": 0.0}))
    assert msapi.geoid_applied("with", "flat", (9.0, 42.0)) is False


def test_geoid_applied_does_not_block_when_the_test_cannot_run(ms):
    ms.Vector = lambda values: FakeVector(values)

    def broken(*_args):
        raise RuntimeError("no")

    ms.CoordinateSystem = lambda text: text
    ms.CoordinateSystem.transform = staticmethod(broken)
    assert msapi.geoid_applied("a", "b", (9.0, 42.0)) is True
