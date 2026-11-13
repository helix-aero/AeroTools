"""AeroTools updater: fresh requests, bounded retries, clean staging, disk space, no downgrade, recovery after a crash, safe names."""

import io
import json
import os
import shutil
import urllib.error

import pytest

from aerotools import config, updater
from test_updater import install_old, publish, write


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    """Retries pause for seconds: record the pauses instead of sleeping."""
    pauses = []
    monkeypatch.setattr(updater, "_sleep", pauses.append)
    return pauses


@pytest.fixture
def world(tmp_path, local_appdata):
    remote = tmp_path / "remote"
    local = tmp_path / "local"
    remote.mkdir()
    local.mkdir()
    publish(remote, {
        "aerotools/a.py": "print('a2')\n",
        "aerotools/b.py": "print('b')\n",
        "install/run.bat": "@echo off\r\necho hi\r\n",
        "assets/sample.bin": b"\x00\x01\x02" * 1000,
    })
    config.put(updater.SECTION, "base_url", remote.as_uri())
    plugin = {"id": "aerotools", "title": "T", "repo": "x/y", "root": str(local)}
    return {"remote": remote, "local": local, "plugin": plugin}


def stage_folders(plugin):
    base = os.path.join(config.settings_dir(), "updates", plugin["id"])
    return sorted(os.listdir(base)) if os.path.isdir(base) else []


class FakeServer(object):
    """Stands in for urllib.request.urlopen on an https address: serves the files of `remote`, can fail first."""

    def __init__(self, remote, monkeypatch):
        self.remote = str(remote)
        self.failures = {}              # file name -> list of exceptions to raise, one per request
        self.requests = []
        monkeypatch.setattr(updater.urllib.request, "urlopen", self.urlopen)

    def urlopen(self, request, timeout=None):
        url = request.full_url
        name = urllib.request.url2pathname(url.split("/repo/", 1)[1]) if "/repo/" in url else url
        name = urllib.parse.unquote(name).replace("\\", "/")
        self.requests.append((name, dict((k.lower(), v) for k, v in request.header_items())))
        queue = self.failures.get(name)
        if queue:
            raise queue.pop(0)
        with open(os.path.join(self.remote, *name.split("/")), "rb") as handle:
            return _Body(handle.read())


class _Body(object):
    def __init__(self, data):
        self.data = io.BytesIO(data)

    def read(self, count=-1):
        return self.data.read(count)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


import urllib.parse  # noqa: E402
import urllib.request  # noqa: E402


@pytest.fixture
def server(world, monkeypatch):
    config.put(updater.SECTION, "base_url", "https://files.test/repo")
    return FakeServer(world["remote"], monkeypatch)


# --------------------------------------------------------------------------
# Fresh requests and retries
# --------------------------------------------------------------------------

def test_every_request_asks_for_a_fresh_file(world, server):
    install_old(world)
    assert updater.apply(world["plugin"])["ok"]
    assert server.requests
    for name, headers in server.requests:
        assert headers.get("cache-control") == "no-cache", name        # the manifest already had it, the files did not
    assert any(name == "aerotools/a.py" for name, _headers in server.requests)


def test_a_transient_error_is_retried_with_a_pause_and_the_update_goes_through(world, server, no_waiting):
    install_old(world)
    server.failures["aerotools/a.py"] = [urllib.error.URLError("timed out"), urllib.error.HTTPError("u", 503, "busy", {}, None)]
    seen = []
    result = updater.apply(world["plugin"], progress=lambda done, total, text: seen.append((done, total)))
    assert result["ok"], result
    assert no_waiting == [1.0, 3.0]
    assert all(done <= total for done, total in seen) and seen[-1][0] == seen[-1][1]   # a failed attempt is taken back from the progress
    assert (world["local"] / "aerotools" / "a.py").read_text() == "print('a2')\n"


def test_retries_are_bounded(world, server, no_waiting):
    install_old(world)
    server.failures["aerotools/a.py"] = [urllib.error.URLError("down")] * 10
    result = updater.apply(world["plugin"])
    assert result["ok"] is False and "download of aerotools/a.py failed" in result["error"]
    assert sum(1 for name, _h in server.requests if name == "aerotools/a.py") == updater.RETRIES
    assert (world["local"] / "aerotools" / "a.py").read_text() == "print('a1')\n"


def test_a_missing_file_is_not_retried(world, server, no_waiting):
    install_old(world)
    server.failures["aerotools/a.py"] = [urllib.error.HTTPError("u", 404, "not found", {}, None)]
    result = updater.apply(world["plugin"])
    assert result["ok"] is False and no_waiting == []


def test_the_manifest_request_is_retried_too(world, server, no_waiting):
    server.failures["manifest.json"] = [ConnectionResetError("reset")]
    remote, _raw = updater.remote_manifest(world["plugin"])
    assert remote["version"] == "9.9.9" and no_waiting == [1.0]


def test_a_stale_copy_served_once_is_fetched_again(world, server, no_waiting, monkeypatch):
    """The CDN still serves the previous file for a few minutes: the digest does not match, the second request does."""
    install_old(world)
    good = open(os.path.join(str(world["remote"]), "aerotools", "a.py"), "rb").read()
    calls = {"n": 0}
    real = server.urlopen

    def urlopen(request, timeout=None):
        if request.full_url.endswith("aerotools/a.py"):
            calls["n"] += 1
            if calls["n"] == 1:
                return _Body(b"print('previous release')\n")
        return real(request, timeout)

    monkeypatch.setattr(updater.urllib.request, "urlopen", urlopen)
    result = updater.apply(world["plugin"])
    assert result["ok"] and calls["n"] == 2 and no_waiting == [1.0]
    assert (world["local"] / "aerotools" / "a.py").read_bytes() == good


def test_a_file_that_never_matches_says_the_server_may_still_be_updating(world):
    install_old(world)
    write(world["remote"], "aerotools/a.py", "print('corrupted')\n")
    result = updater.apply(world["plugin"])
    assert result["ok"] is False and "does not match the published list" in result["error"] and "try again" in result["error"]


# --------------------------------------------------------------------------
# Staging folder and disk space
# --------------------------------------------------------------------------

def test_the_downloaded_copy_is_removed_after_a_successful_update_and_the_backups_stay(world):
    install_old(world)
    assert updater.apply(world["plugin"])["ok"]
    folders = stage_folders(world["plugin"])
    assert len(folders) == 1
    stage = os.path.join(config.settings_dir(), "updates", "aerotools", folders[0])
    assert not os.path.exists(os.path.join(stage, "new"))
    assert os.path.isfile(os.path.join(stage, "old", "aerotools", "a.py"))


@pytest.mark.parametrize("how", ["corrupt", "cancel", "replace"])
def test_a_failed_update_leaves_no_staging_folder(world, monkeypatch, how):
    install_old(world)
    kwargs = {}
    if how == "corrupt":
        write(world["remote"], "aerotools/a.py", "print('corrupted')\n")
    elif how == "cancel":
        kwargs["cancelled"] = lambda: True
    else:
        monkeypatch.setattr(updater, "_install_file", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    assert updater.apply(world["plugin"], **kwargs)["ok"] is False
    assert stage_folders(world["plugin"]) == []
    assert (world["local"] / "aerotools" / "a.py").read_text() == "print('a1')\n"


def test_repeated_updates_keep_only_the_last_backups(world):
    install_old(world)
    for index in range(5):
        write(world["remote"], "aerotools/b.py", "print('b{}')\n".format(index))
        publish(world["remote"], {})
        assert updater.apply(world["plugin"])["ok"]
    assert len(stage_folders(world["plugin"])) <= updater.KEEP_BACKUPS


def test_not_enough_disk_space_is_refused_before_downloading(world, monkeypatch):
    install_old(world)
    monkeypatch.setattr(updater.shutil, "disk_usage", lambda path: shutil._ntuple_diskusage(10 ** 9, 10 ** 9 - 1000, 1000))
    result = updater.apply(world["plugin"])
    assert result["ok"] is False and "not enough free disk space" in result["error"]
    assert stage_folders(world["plugin"]) == []
    assert (world["local"] / "aerotools" / "a.py").read_text() == "print('a1')\n"


def test_enough_disk_space_lets_the_update_through(world, monkeypatch):
    install_old(world)
    monkeypatch.setattr(updater.shutil, "disk_usage", lambda path: shutil._ntuple_diskusage(10 ** 12, 0, 10 ** 12))
    assert updater.apply(world["plugin"])["ok"]


# --------------------------------------------------------------------------
# Not older than what is installed
# --------------------------------------------------------------------------

def rewrite_local_manifest(world, **fields):
    path = world["local"] / "manifest.json"
    data = json.loads(path.read_text())
    data.update(fields)
    path.write_text(json.dumps(data))


def test_an_older_manifest_than_the_installed_one_is_refused(world):
    install_old(world)
    rewrite_local_manifest(world, built="2999-01-01T00:00:00", version="9.9.9")
    result = updater.apply(world["plugin"])
    assert result["ok"] is False and "older than the installed one" in result["error"]
    assert (world["local"] / "aerotools" / "a.py").read_text() == "print('a1')\n"


def test_the_version_counts_when_the_dates_are_missing():
    assert updater.is_older({"version": "1.2.0"}, {"version": "1.10.0"})
    assert not updater.is_older({"version": "1.10.0"}, {"version": "1.2.0"})
    assert not updater.is_older({"version": "1.0"}, None)
    assert not updater.is_older({}, {"built": "2026-01-01T00:00:00"})


def test_switching_the_branch_on_purpose_allows_one_older_update(world):
    install_old(world)
    rewrite_local_manifest(world, built="2999-01-01T00:00:00")
    assert updater.set_channel("main")
    assert updater.apply(world["plugin"])["ok"]
    rewrite_local_manifest(world, built="2999-01-01T00:00:00")
    assert updater.apply(world["plugin"])["ok"] is False                  # the permission was used up by the first update


# --------------------------------------------------------------------------
# Core files changed: the caller must reload
# --------------------------------------------------------------------------

def test_a_core_file_in_the_update_is_reported_and_a_pack_only_update_is_not(world, monkeypatch):
    pack = {"id": "madeup", "paths": ["aerotools/tools/madeup/"], "marker": "aerotools/tools/madeup/tool.py", "requires": []}
    monkeypatch.setattr(config, "PACKS", list(config.PACKS) + [pack])
    install_old(world)
    assert updater.apply(world["plugin"])["core_changed"] is True
    publish(world["remote"], {"aerotools/tools/madeup/tool.py": "x = 1\n"})
    plugin = world["plugin"]
    result = updater.install_pack(plugin, "madeup")
    assert result["ok"], result
    assert result["core_changed"] is False                                   # only the files of the pack were new
    publish(world["remote"], {"aerotools/a.py": "print('a3')\n"})
    result = updater.apply(plugin)
    assert result["core_changed"] is True and result["updated"] >= 1


# --------------------------------------------------------------------------
# An update that was interrupted is undone at the next start
# --------------------------------------------------------------------------

def test_an_interrupted_update_is_undone_by_recover(world, monkeypatch):
    install_old(world)
    local = world["local"]
    real = updater._install_file
    calls = {"n": 0}

    def dies(source, destination, name):
        calls["n"] += 1
        if calls["n"] == 3:
            raise SystemExit("power cut")                                  # not an Exception: nothing in apply() catches it
        return real(source, destination, name)

    monkeypatch.setattr(updater, "_install_file", dies)
    with pytest.raises(SystemExit):
        updater.apply(world["plugin"])
    replaced = [n for n in ("aerotools/a.py", "aerotools/b.py", "install/run.bat", "assets/sample.bin") if (local / n).exists()]
    assert (local / "aerotools" / "a.py").read_text() != "print('a1')\n" or "assets/sample.bin" in replaced   # half way
    monkeypatch.setattr(updater, "_install_file", real)
    assert stage_folders(world["plugin"])                                  # the backups were kept for the recovery
    assert updater.recover(world["plugin"]) > 0
    assert (local / "aerotools" / "a.py").read_text() == "print('a1')\n"
    assert not (local / "assets" / "sample.bin").exists()                  # a file the update added is removed again
    assert (local / "aerotools" / "old.py").exists()
    assert json.loads((local / "manifest.json").read_text())["files"]["aerotools/old.py"]
    assert updater.recover(world["plugin"]) == 0                           # done once


def test_recover_ignores_an_update_that_went_through_and_other_folders(world):
    install_old(world)
    assert updater.apply(world["plugin"])["ok"]
    assert updater.recover(world["plugin"]) == 0
    assert updater.recover(dict(world["plugin"], root=str(world["remote"]))) == 0
    assert (world["local"] / "aerotools" / "a.py").read_text() == "print('a2')\n"


def test_recover_never_runs_an_older_journal_over_a_later_successful_update(world, monkeypatch):
    install_old(world)
    real = updater._install_file
    count = {"n": 0}

    def dies(source, destination, name):
        count["n"] += 1
        if count["n"] == 2:
            raise SystemExit("killed")
        return real(source, destination, name)

    monkeypatch.setattr(updater, "_install_file", dies)
    with pytest.raises(SystemExit):
        updater.apply(world["plugin"])
    monkeypatch.setattr(updater, "_install_file", real)
    assert updater.apply(world["plugin"])["ok"]                            # a later update completes
    assert updater.recover(world["plugin"]) == 0
    assert (world["local"] / "aerotools" / "a.py").read_text() == "print('a2')\n"


# --------------------------------------------------------------------------
# Names, addresses, channel
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["aerotools/con.py", "aerotools/AUX.txt", "nul", "aerotools/lpt1.log", "aerotools/a.py.", "aerotools/a ",
                                  "aerotools//b.py"])
def test_names_windows_reads_as_something_else_are_refused(name):
    with pytest.raises(updater.UpdateError):
        updater._safe_relative(name)


@pytest.mark.parametrize("name", ["aerotools/console.py", "CLICK HERE TO INSTALL.bat", "aerotools/vendor/proj/share/proj/proj.db",
                                  "aerotools/auxiliary.py", "assets/fonts/Rubik-Regular.ttf", "README.md"])
def test_ordinary_names_pass(name):
    assert updater._safe_relative(name) == name


def test_the_published_manifest_names_are_all_acceptable():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data = json.load(open(os.path.join(root, "manifest.json"), encoding="utf-8"))
    for name in data["files"]:
        updater._safe_relative(name)


def test_plain_http_updates_are_refused_except_to_this_computer(world, monkeypatch):
    config.put(updater.SECTION, "base_url", "http://mirror.example/repo")
    result = updater.check(world["plugin"])
    assert result["state"] == "error" and "only https" in result["message"]
    config.put(updater.SECTION, "base_url", "http://localhost:9/repo")
    monkeypatch.setattr(updater.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(ConnectionRefusedError("nobody")))
    result = updater.check(world["plugin"])
    assert "only https" not in result["message"]                           # refused later, by the connection itself


def test_the_channel_follows_the_hint_of_the_installer_unless_the_settings_say_otherwise(local_appdata, tmp_path, monkeypatch):
    folder = tmp_path / "app"
    folder.mkdir()
    (folder / "channel.txt").write_text("dev\n", encoding="utf-8")
    monkeypatch.setattr(config, "ROOT", str(folder))
    assert updater.channel() == "dev"
    config.put(updater.SECTION, "channel", "main")
    assert updater.channel() == "main"


def test_without_a_hint_or_a_setting_the_channel_is_main(local_appdata, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT", str(tmp_path))
    assert updater.channel() == "main"


# --------------------------------------------------------------------------
# The digests of the installed files are remembered
# --------------------------------------------------------------------------

def count_hashing(monkeypatch):
    counted = []
    real = updater.digest_file

    def counting(path, name=None):
        counted.append(path)
        return real(path, name)

    monkeypatch.setattr(updater, "digest_file", counting)
    return counted


def old_file(path, data):
    with open(path, "wb") as handle:
        handle.write(data)
    os.utime(path, (1_600_000_000, 1_600_000_000))


def test_the_second_look_at_an_installed_pack_does_not_read_its_files_again(world, monkeypatch):
    install_old(world)
    for name in ("aerotools/a.py", "aerotools/b.py", "install/run.bat", "aerotools/__init__.py"):
        path = str(world["local"] / name)
        old_file(path, open(path, "rb").read())
    plugin = world["plugin"]
    remote, _raw = updater.remote_manifest(plugin)
    counted = count_hashing(monkeypatch)
    updater.compare(plugin, remote, updater.local_manifest(plugin))
    first = len(counted)
    updater.compare(plugin, remote, updater.local_manifest(plugin))
    assert first == 4 and len(counted) == first


def test_a_file_that_changed_is_hashed_again(world, monkeypatch):
    install_old(world)
    path = str(world["local"] / "aerotools" / "b.py")
    old_file(path, b"print('b')\n")
    plugin = world["plugin"]
    remote, _raw = updater.remote_manifest(plugin)
    local = updater.local_manifest(plugin)
    assert "aerotools/b.py" not in {c["path"] for c in updater.compare(plugin, remote, local)}
    old_file(path, b"print('X')\n")                                        # same size, other content, other mtime
    os.utime(path, (1_600_000_500, 1_600_000_500))
    assert "aerotools/b.py" in {c["path"] for c in updater.compare(plugin, remote, local)}


def test_a_file_written_a_moment_ago_is_never_taken_from_the_memory(world):
    install_old(world)
    path = str(world["local"] / "aerotools" / "b.py")
    plugin = world["plugin"]
    remote, _raw = updater.remote_manifest(plugin)
    local = updater.local_manifest(plugin)
    write(world["local"], "aerotools/b.py", "print('b')\n")
    updater.compare(plugin, remote, local)
    write(world["local"], "aerotools/b.py", "print('X')\n")                 # same size, within the same clock tick
    assert "aerotools/b.py" in {c["path"] for c in updater.compare(plugin, remote, local)}


def test_verify_always_reads_the_disk(world, monkeypatch):
    install_old(world)
    assert updater.apply(world["plugin"])["ok"]
    counted = count_hashing(monkeypatch)
    updater.verify(world["plugin"])
    assert len(counted) >= 4


def test_recover_leaves_alone_an_update_another_metashape_is_running(world, monkeypatch):
    import subprocess
    install_old(world)
    real = updater._install_file
    count = {"n": 0}

    def dies(source, destination, name):
        count["n"] += 1
        if count["n"] == 2:
            raise SystemExit("killed")
        return real(source, destination, name)

    monkeypatch.setattr(updater, "_install_file", dies)
    with pytest.raises(SystemExit):
        updater.apply(world["plugin"])
    monkeypatch.setattr(updater, "_install_file", real)
    base = os.path.join(config.settings_dir(), "updates", "aerotools")
    stage = os.path.join(base, os.listdir(base)[0])
    other = subprocess.Popen(["ping", "-n", "60", "127.0.0.1"] if os.name == "nt" else ["sleep", "60"], stdout=subprocess.DEVNULL)
    try:
        data = json.load(open(os.path.join(stage, updater.JOURNAL)))
        data["pid"] = other.pid                                           # written by a process that is still running
        json.dump(data, open(os.path.join(stage, updater.JOURNAL), "w"))
        assert updater.recover(world["plugin"]) == 0
    finally:
        other.kill()
        other.wait()
    assert updater.recover(world["plugin"]) > 0                           # once it is gone, the update is undone


def test_process_alive_tells_a_running_process_from_a_gone_one():
    import subprocess
    assert updater._process_alive(os.getpid()) is False                   # our own journal is ours to undo
    assert updater._process_alive(None) is False and updater._process_alive("x") is False and updater._process_alive(0) is False
    other = subprocess.Popen(["ping", "-n", "60", "127.0.0.1"] if os.name == "nt" else ["sleep", "60"], stdout=subprocess.DEVNULL)
    try:
        assert updater._process_alive(other.pid) is True
    finally:
        other.kill()
        other.wait()
    assert updater._process_alive(other.pid) is False
