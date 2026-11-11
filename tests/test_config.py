import json
import os

import pytest

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


# -- Settings migration: a renamed tool keeps the choices saved under its old section name ----------------------------

@pytest.fixture
def renamed(monkeypatch):
    """One renamed section: "alpha" used to be called "oldalpha"."""
    monkeypatch.setattr(config, "LEGACY_SECTIONS", {"alpha": "oldalpha"})


def _write_settings(data):
    os.makedirs(config.settings_dir(), exist_ok=True)
    with open(config.settings_path(), "w", encoding="utf-8") as handle:
        json.dump(data, handle)


def _read_settings():
    with open(config.settings_path(), encoding="utf-8") as handle:
        return json.load(handle)


def test_legacy_section_is_copied_to_the_new_name(local_appdata, renamed):
    old = {"mode": "a", "clip": False, "look": {"palette": "x"}}
    _write_settings({"oldalpha": old, "fastgcp": {"auto_filter": False}})
    assert config.get("alpha", "mode") == "a"
    assert config.get("alpha", "clip") is False
    assert config.get("alpha", "look") == {"palette": "x"}
    saved = _read_settings()
    assert saved["alpha"] == old                          # copied on first read and written back
    assert saved["oldalpha"] == old                       # the old data is not deleted
    assert saved["fastgcp"] == {"auto_filter": False}


def test_legacy_section_never_overwrites_an_existing_new_section(local_appdata, renamed):
    _write_settings({"oldalpha": {"mode": "a"}, "alpha": {"mode": "b"}})
    assert config.get("alpha", "mode") == "b"
    assert _read_settings()["oldalpha"] == {"mode": "a"}


def test_legacy_copy_is_independent_of_the_old_section(local_appdata, renamed):
    _write_settings({"oldalpha": {"mode": "a"}})
    assert config.put("alpha", "mode", "b") is True
    saved = _read_settings()
    assert saved["alpha"]["mode"] == "b"
    assert saved["oldalpha"]["mode"] == "a"


def test_no_legacy_section_means_no_new_section(local_appdata, renamed):
    _write_settings({"fastgcp": {"x": 1}})
    assert config.get("alpha", "mode") is None
    assert _read_settings() == {"fastgcp": {"x": 1}}


def test_aero_exports_keeps_the_choices_saved_under_its_first_name(local_appdata):
    _write_settings({"fastexport": {"cad_format": "dwg"}})
    assert config.get("aeroexports", "cad_format") == "dwg"


# -- a settings file that cannot be read must never be replaced by a one-key file --------------------------

def _seed(data):
    os.makedirs(config.settings_dir(), exist_ok=True)
    with open(config.settings_path(), "w", encoding="utf-8") as handle:
        json.dump(data, handle)


def _quick(monkeypatch):
    monkeypatch.setattr(config, "READ_PAUSE_S", 0.0)


def test_put_survives_a_file_that_is_unreadable_for_a_moment(local_appdata, monkeypatch):
    _quick(monkeypatch)
    _seed({"update": {"channel": "dev"}, "aigcp": {"python": "C:/py"}})
    real_open, refused = open, []

    def flaky(path, *args, **kwargs):
        if os.path.abspath(str(path)) == os.path.abspath(config.settings_path()) and "r" in (args[0] if args else kwargs.get("mode", "r")) \
                and len(refused) < 2:
            refused.append(path)
            raise PermissionError("held by the antivirus")
        return real_open(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr("builtins.open", flaky)
        assert config.put("fastgcp", "ai_auto_review", False) is True
    assert len(refused) == 2
    with open(config.settings_path(), encoding="utf-8") as handle:
        assert json.load(handle) == {"update": {"channel": "dev"}, "aigcp": {"python": "C:/py"}, "fastgcp": {"ai_auto_review": False}}


def test_put_does_not_write_over_a_file_that_stays_locked(local_appdata, monkeypatch):
    _quick(monkeypatch)
    _seed({"update": {"channel": "dev"}})
    before = open(config.settings_path(), "rb").read()
    real_open = open

    def locked(path, *args, **kwargs):
        if os.path.abspath(str(path)) == os.path.abspath(config.settings_path()) and "r" in (args[0] if args else kwargs.get("mode", "r")):
            raise PermissionError("held by OneDrive")
        return real_open(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr("builtins.open", locked)
        assert config.put("fastgcp", "ai_auto_review", False) is False          # not saved, and nothing lost
    assert open(config.settings_path(), "rb").read() == before
    assert os.listdir(config.settings_dir()) == ["settings.json"]


def test_put_on_a_corrupt_file_keeps_a_copy_before_replacing_it(local_appdata, monkeypatch):
    _quick(monkeypatch)
    os.makedirs(config.settings_dir())
    with open(config.settings_path(), "w", encoding="utf-8") as handle:
        handle.write('{"update": {"channel": "dev"}, "aigcp": ')            # cut in the middle
    assert config.put("fastgcp", "ai_auto_review", False) is True
    with open(config.settings_path(), encoding="utf-8") as handle:
        assert json.load(handle) == {"fastgcp": {"ai_auto_review": False}}
    copies = [n for n in os.listdir(config.settings_dir()) if n.endswith(".bak")]
    assert len(copies) == 1 and copies[0].startswith("settings.json.corrupt-")
    with open(os.path.join(config.settings_dir(), copies[0]), encoding="utf-8") as handle:
        assert handle.read() == '{"update": {"channel": "dev"}, "aigcp": '        # the old content is not lost


def test_a_second_corrupt_file_does_not_overwrite_the_first_copy(local_appdata, monkeypatch):
    _quick(monkeypatch)
    for text in ("{first", "{second"):
        os.makedirs(config.settings_dir(), exist_ok=True)
        with open(config.settings_path(), "w", encoding="utf-8") as handle:
            handle.write(text)
        assert config.put("a", "x", 1) is True
    texts = sorted(open(os.path.join(config.settings_dir(), n), encoding="utf-8").read()
                   for n in os.listdir(config.settings_dir()) if n.endswith(".bak"))
    assert texts == ["{first", "{second"]


def test_loading_a_corrupt_file_never_touches_it(local_appdata):
    os.makedirs(config.settings_dir())
    with open(config.settings_path(), "w", encoding="utf-8") as handle:
        handle.write("{not json")
    assert config.load_settings() == {} and config.get("a", "b", 3) == 3
    assert open(config.settings_path(), encoding="utf-8").read() == "{not json"
    assert os.listdir(config.settings_dir()) == ["settings.json"]


def test_two_threads_putting_at_once_lose_nothing(local_appdata):
    import threading
    errors = []

    def worker(number):
        try:
            for step in range(15):
                assert config.put("t%d" % number, "k%d" % step, step) is True
        except Exception as exc:                     # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    data = config.load_settings()
    assert not errors and all(len(data["t%d" % n]) == 15 for n in range(4))
    assert [n for n in os.listdir(config.settings_dir()) if n.endswith(".tmp")] == []
