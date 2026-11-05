"""Mise a jour d'AeroTools : comparaison, telechargement verifie, retour arriere, manifeste a jour."""

import importlib.util
import json
import os

import pytest

from aerotools import config, updater

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_maker():
    spec = importlib.util.spec_from_file_location("make_manifest", os.path.join(ROOT, "install", "make_manifest.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


maker = load_maker()


def write(root, name, data):
    path = os.path.join(str(root), *name.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data if isinstance(data, bytes) else data.encode("utf-8"))
    return path


def publish(root, files):
    """Ecrit `files` et son manifeste dans `root` (le « depot »), renvoie le manifeste."""
    for name, data in files.items():
        write(root, name, data)
    write(root, "aerotools/__init__.py", '__version__ = "9.9.9"\n')
    manifest = maker.write(str(root))
    return manifest


@pytest.fixture
def world(tmp_path, local_appdata, monkeypatch):
    """Un depot publie (file://) et une installation locale d'une version plus ancienne."""
    remote = tmp_path / "remote"
    local = tmp_path / "local"
    remote.mkdir()
    local.mkdir()
    publish(remote, {
        "aerotools/a.py": "print('a2')\n",
        "aerotools/b.py": "print('b')\n",
        "install/run.bat": "@echo off\r\necho hi\r\n",
        "models/damier.onnx": b"\x00\x01\x02" * 1000,
    })
    config.put(updater.SECTION, "base_url", remote.as_uri())
    plugin = {"id": "aerotools", "title": "T", "repo": "x/y", "root": str(local)}
    return {"remote": remote, "local": local, "plugin": plugin}


def install_old(world):
    """Etat local : a.py ancien, b.py a jour, run.bat a jour en CRLF, onnx absent, un fichier obsolete."""
    local = world["local"]
    write(local, "aerotools/a.py", "print('a1')\n")
    write(local, "aerotools/b.py", "print('b')\r\n")        # CRLF local : meme empreinte que le LF distant
    write(local, "install/run.bat", "@echo off\r\necho hi\r\n")
    write(local, "aerotools/old.py", "print('gone')\n")
    write(local, "aerotools/__init__.py", '__version__ = "1.0.0"\n')
    previous = maker.build(str(local))
    previous["files"]["aerotools/old.py"] = {"sha256": "0" * 64, "size": 1}
    with open(str(local / "manifest.json"), "w") as handle:
        json.dump(previous, handle)


def test_compare_lists_adds_replaces_deletes_and_ignores_line_endings(world):
    install_old(world)
    plugin = world["plugin"]
    remote, _raw = updater.remote_manifest(plugin)
    changes = {c["path"]: c["action"] for c in updater.compare(plugin, remote, updater.local_manifest(plugin))}
    assert changes["aerotools/a.py"] == "replace"
    assert changes["models/damier.onnx"] == "add"
    assert changes["aerotools/old.py"] == "delete"
    assert "aerotools/b.py" not in changes and "install/run.bat" not in changes


def test_check_reports_size_and_state(world):
    install_old(world)
    result = updater.check(world["plugin"])
    assert result["state"] == "update"
    assert result["bytes"] >= 3000 and result["remote"]["version"] == "9.9.9"


def test_apply_installs_verifies_and_records_the_date(world):
    install_old(world)
    plugin, local = world["plugin"], world["local"]
    seen = []
    result = updater.apply(plugin, progress=lambda done, total, text: seen.append((done, total)))
    assert result == {"ok": True, "updated": 4, "error": ""}
    assert (local / "aerotools" / "a.py").read_text() == "print('a2')\n"
    assert (local / "models" / "damier.onnx").stat().st_size == 3000
    assert not (local / "aerotools" / "old.py").exists()
    assert seen and seen[-1][0] == seen[-1][1]                         # la progression va jusqu'au bout
    count, bad = updater.verify(plugin)
    assert count == 5 and bad == []
    assert updater.check(plugin)["state"] == "current"
    info = updater.installed_info(plugin)
    assert info["version"] == "9.9.9" and info["updated"]
    assert (local / "install" / "run.bat").read_bytes() == b"@echo off\r\necho hi\r\n"     # CRLF gardees


def test_a_second_apply_does_nothing(world):
    install_old(world)
    updater.apply(world["plugin"])
    assert updater.apply(world["plugin"]) == {"ok": True, "updated": 0, "error": ""}


def test_a_corrupted_download_touches_nothing(world):
    install_old(world)
    local = world["local"]
    write(world["remote"], "aerotools/a.py", "print('corrompu')\n")          # le manifeste annonce autre chose
    before = (local / "aerotools" / "a.py").read_text()
    result = updater.apply(world["plugin"])
    assert result["ok"] is False and "abime" in result["error"]
    assert (local / "aerotools" / "a.py").read_text() == before
    assert not (local / "models" / "damier.onnx").exists()


def test_a_failure_during_replacement_restores_the_old_files(world, monkeypatch):
    install_old(world)
    local = world["local"]
    real = updater._install_file
    calls = {"n": 0}

    def flaky(source, destination, name):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disque plein")
        return real(source, destination, name)

    monkeypatch.setattr(updater, "_install_file", flaky)
    result = updater.apply(world["plugin"])
    assert result["ok"] is False and "disque plein" in result["error"]
    assert (local / "aerotools" / "a.py").read_text() == "print('a1')\n"
    assert (local / "aerotools" / "old.py").exists()
    assert not (local / "models" / "damier.onnx").exists()
    assert json.loads((local / "manifest.json").read_text())["files"]["aerotools/old.py"]     # manifeste d'avant


def test_cancel_before_replacement_leaves_the_install_alone(world):
    install_old(world)
    local = world["local"]
    result = updater.apply(world["plugin"], cancelled=lambda: True)
    assert result["ok"] is False
    assert (local / "aerotools" / "a.py").read_text() == "print('a1')\n"


def test_a_git_copy_is_never_updated(world):
    install_old(world)
    os.makedirs(os.path.join(str(world["local"]), ".git"))
    assert updater.check(world["plugin"])["state"] == "dev"
    result = updater.apply(world["plugin"])
    assert result["ok"] is False and "git" in result["error"]
    assert (world["local"] / "aerotools" / "a.py").read_text() == "print('a1')\n"


@pytest.mark.parametrize("name", ["../evil.py", "/abs.py", "C:/evil.py", "a/../../evil.py"])
def test_manifest_paths_cannot_leave_the_install_folder(name):
    with pytest.raises(updater.UpdateError):
        updater._check_manifest({"files": {name: {"sha256": "0" * 64, "size": 1}}})


def test_unreachable_server_is_reported_not_raised(local_appdata, tmp_path):
    config.put(updater.SECTION, "base_url", (tmp_path / "nothing-here").as_uri())
    result = updater.check({"id": "x", "title": "X", "repo": "a/b", "root": str(tmp_path)})
    assert result["state"] == "error" and result["message"]


def test_default_url_follows_the_channel(local_appdata):
    plugin = {"id": "x", "title": "X", "repo": "helix-aero/AeroTools", "root": "."}
    assert updater.base_url(plugin) == "https://raw.githubusercontent.com/helix-aero/AeroTools/main"
    config.put(updater.SECTION, "channel", "dev")
    assert updater.file_url(plugin, "CLICK HERE TO INSTALL.bat").endswith("/dev/CLICK%20HERE%20TO%20INSTALL.bat")


def test_set_channel_switches_between_stable_and_test(local_appdata):
    plugin = {"id": "x", "title": "X", "repo": "helix-aero/AeroTools", "root": "."}
    assert updater.channel() == "main"
    assert updater.set_channel("dev")
    assert updater.channel() == "dev" and updater.base_url(plugin).endswith("/AeroTools/dev")
    assert updater.set_channel("main")
    assert updater.base_url(plugin).endswith("/AeroTools/main")


def test_format_size():
    assert updater.format_size(512) == "512 B"
    assert updater.format_size(18 * 1024 * 1024 + 400000) == "18.4 MB"


def test_the_published_manifest_is_up_to_date():
    """Echoue quand on a modifie un fichier publie sans relancer install/make_manifest.py."""
    on_disk = updater.read_manifest(os.path.join(ROOT, "manifest.json"))
    now = maker.build(ROOT)
    assert on_disk["version"] == now["version"]
    stale = sorted(name for name in set(on_disk["files"]) | set(now["files"])
                   if on_disk["files"].get(name) != now["files"].get(name))
    assert stale == [], "lance : python install/make_manifest.py   (fichiers : {})".format(stale[:8])
