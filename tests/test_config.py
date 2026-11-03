import json
import os

from aerotools import config


def test_settings_roundtrip(local_appdata):
    assert config.load_settings() == {}
    assert config.put("fastgcp", "auto_filter", False) is True
    assert config.get("fastgcp", "auto_filter") is False
    assert config.get("fastgcp", "inconnu", 42) == 42
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
        handle.write("{pas du json")
    assert config.load_settings() == {}
    assert config.get("fastgcp", "x", "defaut") == "defaut"


def test_non_dict_json_gives_empty_settings(local_appdata):
    os.makedirs(config.settings_dir())
    with open(config.settings_path(), "w", encoding="utf-8") as handle:
        json.dump([1, 2, 3], handle)
    assert config.load_settings() == {}


def test_section_of_wrong_type_does_not_raise(local_appdata):
    os.makedirs(config.settings_dir())
    with open(config.settings_path(), "w", encoding="utf-8") as handle:
        json.dump({"fastgcp": 3}, handle)
    assert config.get("fastgcp", "x", "defaut") == "defaut"


def test_registry():
    assert config.tool("fastgcp")["pret"] is True
    assert config.tool("aigcp")["pret"] is False
    assert config.tool("nexiste-pas") is None
    ids = [entry["id"] for entry in config.TOOLS]
    assert len(ids) == len(set(ids))
