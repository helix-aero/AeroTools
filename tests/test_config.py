import json
import os

from aerotools import config


def test_settings_roundtrip(local_appdata):
    assert config.load_settings() == {}
    assert config.put("fastgcp", "auto_filter", False) is True
    assert config.get("fastgcp", "auto_filter") is False
    assert config.get("fastgcp", "unknown", 42) == 42
    expected = os.path.join(str(local_appdata), "HELIX", "AeroTools", "settings.json")
    assert config.settings_path() == expected
    with open(expected, encoding="utf-8") as handle:
        assert json.load(handle) == {"fastgcp": {"auto_filter": False}}


def test_put_keeps_other_sections(local_appdata):
    config.put("a", "x", 1)
    config.put("b", "y", 2)
    assert config.load_settings() == {"a": {"x": 1}, "b": {"y": 2}}


def test_corrupted_file_gives_empty_settings(local_appdata):
    os.makedirs(config.settings_dir())
    with open(config.settings_path(), "w", encoding="utf-8") as handle:
        handle.write("{not json")
    assert config.load_settings() == {}
    assert config.get("fastgcp", "x", "default") == "default"


def test_non_dict_json_gives_empty_settings(local_appdata):
    os.makedirs(config.settings_dir())
    with open(config.settings_path(), "w", encoding="utf-8") as handle:
        json.dump([1, 2, 3], handle)
    assert config.load_settings() == {}


def test_section_of_wrong_type_does_not_raise(local_appdata):
    os.makedirs(config.settings_dir())
    with open(config.settings_path(), "w", encoding="utf-8") as handle:
        json.dump({"fastgcp": 3}, handle)
    assert config.get("fastgcp", "x", "default") == "default"


def test_registry():
    assert config.tool("fastgcp")["ready"] is True
    assert config.tool("aigcp") is None            # AI detection lives inside FastGCP: no separate entry
    assert config.tool("no-such-tool") is None
    ids = [entry["id"] for entry in config.TOOLS]
    assert len(ids) == len(set(ids))


# -- Settings migration: the Aero-Exports section used to be called "fastexport" ---------------------------------------

def _write_settings(data):
    os.makedirs(config.settings_dir(), exist_ok=True)
    with open(config.settings_path(), "w", encoding="utf-8") as handle:
        json.dump(data, handle)


def _read_settings():
    with open(config.settings_path(), encoding="utf-8") as handle:
        return json.load(handle)


def test_legacy_fastexport_section_is_copied_to_aeroexports(local_appdata):
    old = {"cad_format": "dwg", "cad_images": "tif", "cad_clip": False, "dem_look": {"palette": "x"}}
    _write_settings({"fastexport": old, "fastgcp": {"auto_filter": False}})
    assert config.get("aeroexports", "cad_format") == "dwg"
    assert config.get("aeroexports", "cad_clip") is False
    assert config.get("aeroexports", "dem_look") == {"palette": "x"}
    saved = _read_settings()
    assert saved["aeroexports"] == old                    # copied on first read and written back
    assert saved["fastexport"] == old                     # the old data is not deleted
    assert saved["fastgcp"] == {"auto_filter": False}


def test_legacy_section_never_overwrites_an_existing_aeroexports_section(local_appdata):
    _write_settings({"fastexport": {"cad_format": "dwg"}, "aeroexports": {"cad_format": "dxf"}})
    assert config.get("aeroexports", "cad_format") == "dxf"
    assert _read_settings()["fastexport"] == {"cad_format": "dwg"}


def test_legacy_copy_is_independent_of_the_old_section(local_appdata):
    _write_settings({"fastexport": {"cad_format": "dwg"}})
    assert config.put("aeroexports", "cad_format", "dxf") is True
    saved = _read_settings()
    assert saved["aeroexports"]["cad_format"] == "dxf"
    assert saved["fastexport"]["cad_format"] == "dwg"


def test_no_legacy_section_means_no_aeroexports_section(local_appdata):
    _write_settings({"fastgcp": {"x": 1}})
    assert config.get("aeroexports", "cad_format") is None
    assert _read_settings() == {"fastgcp": {"x": 1}}
