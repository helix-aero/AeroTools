"""Uninstall: what is removed, what is kept, and that a wrong folder is never touched."""

import os

import pytest

from aerotools import config, uninstall


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A throw-away LOCALAPPDATA and user profile, with everything AeroTools can leave on a computer."""
    local, profile = tmp_path / "local", tmp_path / "profile"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("USERPROFILE", str(profile))
    monkeypatch.setenv("HOME", str(profile))
    scripts = local / "Agisoft" / "Metashape Pro" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "aerotools_launcher.py").write_text("# launcher")
    (scripts / "aerotools_launcher.py.bak").write_text("# old")
    (scripts / "other_script.py").write_text("# someone else's script")
    settings = local / "HELIX" / "AeroTools"
    settings.mkdir(parents=True)
    (settings / "settings.json").write_text("{}")
    env = profile / "AIGCP" / "_env" / "Scripts"
    env.mkdir(parents=True)
    (env / "python.exe").write_bytes(b"x" * 100)
    (profile / "AIGCP" / "models.txt").write_text("not the environment")
    return tmp_path


def test_items_list_what_exists_with_sizes(home):
    found = {entry["id"]: entry for entry in uninstall.items()}
    assert [entry["id"] for entry in uninstall.items()] == ["menu", "settings", "env"]
    assert len(found["menu"]["paths"]) == 2
    assert found["env"]["bytes"] == 100


def test_the_menu_is_always_removed_and_the_rest_is_kept_by_default(home):
    result = uninstall.remove([])
    assert result == {"removed": ["menu"], "errors": {}}
    scripts = home / "local" / "Agisoft" / "Metashape Pro" / "scripts"
    assert not (scripts / "aerotools_launcher.py").exists()
    assert not (scripts / "aerotools_launcher.py.bak").exists()
    assert (scripts / "other_script.py").exists()
    assert (home / "local" / "HELIX" / "AeroTools" / "settings.json").exists()
    assert (home / "profile" / "AIGCP" / "_env").exists()


def test_settings_and_environment_are_removed_when_asked_and_nothing_else(home):
    result = uninstall.remove(["settings", "env"])
    assert result["removed"] == ["menu", "settings", "env"] and not result["errors"]
    assert not (home / "local" / "HELIX" / "AeroTools").exists()
    assert not (home / "profile" / "AIGCP" / "_env").exists()
    assert (home / "profile" / "AIGCP" / "models.txt").exists()
    assert (home / "local" / "HELIX").exists()


def test_running_it_twice_is_harmless(home):
    uninstall.remove(["settings", "env"])
    again = uninstall.remove(["settings", "env"])
    assert again["errors"] == {} and [e["paths"] for e in uninstall.items()] == [[], [], []]


def test_a_folder_with_an_unexpected_name_is_never_deleted(home, monkeypatch):
    elsewhere = home / "profile" / "Documents"
    elsewhere.mkdir()
    (elsewhere / "report.docx").write_text("precious")
    monkeypatch.setattr(config, "settings_dir", lambda: str(elsewhere))
    result = uninstall.remove(["settings"])
    assert "settings" in result["errors"] and "settings" not in result["removed"]
    assert (elsewhere / "report.docx").exists()


def test_one_failure_does_not_stop_the_other_items(home, monkeypatch):
    real = uninstall.shutil.rmtree

    def refuse(path, *args, **kwargs):
        if os.path.basename(path) == "AeroTools":
            raise PermissionError("in use")
        return real(path, *args, **kwargs)

    monkeypatch.setattr(uninstall.shutil, "rmtree", refuse)
    result = uninstall.remove(["settings", "env"])
    assert list(result["errors"]) == ["settings"]
    assert result["removed"] == ["menu", "env"]
    assert not (home / "profile" / "AIGCP" / "_env").exists()


def test_progress_is_reported_before_each_item(home):
    seen = []
    uninstall.remove(["env"], seen.append)
    assert seen == ["menu", "env"]
