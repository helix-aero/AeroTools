"""A switch of channel, an update or a removal changes the installed tools: the Aero menu follows right away, and a tool the user did not
choose is never installed on the way.

The channels ship different tools and each has its own config.py: the code of main does not know a tool that only dev ships. The tools here
are made up (`one`, `two` in both channels, `devonly` in dev); the "code of main" is a config that does not declare `devonly`, which is what
released 0.5.0 was against a dev channel with one more tool. Real Qt (offscreen) for the menu, files on disk for the installation."""

import builtins
import importlib
import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")
from test_menu_guard import Bar, bar  # noqa: F401  (the `bar` fixture)

from aerotools import bootstrap, config, updater
from test_packs import maker, write

FLAGS = (bootstrap._LABELS_FLAG, bootstrap._SYNC_FLAG)


def _pack(pack_id):
    return {"id": pack_id, "paths": ["aerotools/tools/{}/".format(pack_id)], "marker": "aerotools/tools/{}/tool.py".format(pack_id),
            "requires": []}


def _tool(pack_id):
    return {"id": pack_id, "menu": "Aero-" + pack_id, "title": pack_id, "subtitle": "x", "shortcut": None, "ready": True, "reopen": False}


MAIN_IDS = ("one", "two")
DEV_IDS = MAIN_IDS + ("devonly",)
FILES = {
    "aerotools/__init__.py": '__version__ = "9.9.9"\n',
    "aerotools/core.py": "print('core')\n",
    "aerotools/tools/one/tool.py": "print('one')\n",
    "aerotools/tools/two/tool.py": "print('two')\n",
    "aerotools/tools/devonly/tool.py": "print('devonly')\n",
    "aerotools/tools/devonly/more.py": "print('more')\n",
}
DEV_ONLY = [name for name in FILES if "/devonly/" in name]
MAIN_FILES = {name: data for name, data in FILES.items() if name not in DEV_ONLY}
DEV_FILES = dict(FILES, **{"aerotools/core.py": "print('core v2')\n"})       # the dev channel: one more tool, and a newer core


def declare(monkeypatch, ids):
    """The config.py of a channel: it declares only the tools that channel ships."""
    monkeypatch.setattr(config, "PACKS", [_pack(i) for i in ids])
    monkeypatch.setattr(config, "TOOLS", [_tool(i) for i in ids])


class Channel(object):
    """A published repository (file://) and an installation, on disk."""

    def __init__(self, root, monkeypatch):
        self.remote, self.local = root / "remote", root / "local"
        self.remote.mkdir()
        self.local.mkdir()
        self.plugin = {"id": "aerotools", "title": "T", "repo": "x/y", "root": str(self.local)}
        monkeypatch.setattr(updater, "plugins", lambda: [self.plugin])
        config.put(updater.SECTION, "base_url", self.remote.as_uri())

    def publish(self, files):
        shutil.rmtree(str(self.remote))
        self.remote.mkdir()
        for name, data in files.items():
            write(self.remote, name, data)
        maker.write(str(self.remote))

    def disk(self, name):
        return (self.local / os.path.join(*name.split("/"))).exists()

    def entries(self, bar_):
        """The Aero entries of the menu that the user sees (a hidden one is not shown)."""
        for action in bar_.window.menuBar().actions():
            if action.text() == "Aero":
                return [a.text() for a in action.menu().actions() if a.text().startswith("Aero-") and a.isVisible()]
        return []


@pytest.fixture
def channels(tmp_path, monkeypatch, bar):  # noqa: F811
    for flag in FLAGS:
        if hasattr(builtins, flag):
            delattr(builtins, flag)
    yield Channel(tmp_path, monkeypatch)
    for flag in FLAGS:
        if hasattr(builtins, flag):
            delattr(builtins, flag)


def main_installation(channels, monkeypatch):
    """What the owner had: the main channel, tools one and two installed, the menu built by the code of main."""
    declare(monkeypatch, MAIN_IDS)
    channels.publish(MAIN_FILES)
    for pack_id in MAIN_IDS:
        assert updater.install_pack(channels.plugin, pack_id)["ok"]
    assert bootstrap.install_menu() is True


def left_by_the_old_code(channels, monkeypatch):
    """The owner's disk after released 0.5.0 followed dev: the tool's files, installed as core files, no entry in the menu."""
    main_installation(channels, monkeypatch)
    channels.publish(DEV_FILES)
    for name in DEV_ONLY:
        write(channels.local, name, FILES[name])
    write(channels.local, "aerotools/core.py", DEV_FILES["aerotools/core.py"])
    shutil.copyfile(str(channels.remote / "manifest.json"), str(channels.local / "manifest.json"))
    declare(monkeypatch, DEV_IDS)                                                  # the new config.py arrived with that update


def test_the_owner_of_a_file_of_a_tool_this_code_does_not_know_is_not_the_core(monkeypatch):
    declare(monkeypatch, MAIN_IDS)
    assert config.pack_of("aerotools/tools/devonly/tool.py") == config.CORE          # what made the update install it as core
    assert config.owner_of("aerotools/tools/devonly/tool.py") == "?devonly"
    assert config.owner_of("aerotools/tools/one/tool.py") == "one"
    assert config.owner_of("aerotools/tools/__init__.py") == config.CORE
    assert config.owner_of("aerotools/core.py") == config.CORE
    declare(monkeypatch, DEV_IDS)
    assert config.owner_of("aerotools\\tools\\devonly\\tool.py") == "devonly"


def test_switching_to_a_channel_with_another_tool_does_not_install_it(channels, monkeypatch, bar):  # noqa: F811
    main_installation(channels, monkeypatch)
    assert channels.entries(bar) == ["Aero-one", "Aero-two"]
    channels.publish(DEV_FILES)
    # The code of main runs the update: it does not know the tool.
    state = updater.check(channels.plugin)
    assert [c["path"] for c in state["changes"]] == ["aerotools/core.py"]
    result = updater.apply(channels.plugin)
    assert result["ok"] and result["core_changed"]
    assert not any(channels.disk(name) for name in DEV_ONLY)
    assert updater.verify(channels.plugin)[1] == []
    # The new code (it declares the tool) finds it "not installed": installing it is the user's choice.
    declare(monkeypatch, DEV_IDS)
    assert "devonly" not in updater.installed_packs(channels.plugin)
    assert [s["installed"] for s in updater.pack_status(channels.plugin) if s["id"] == "devonly"] == [False]
    bootstrap.sync_menu()
    assert "Aero-devonly" not in channels.entries(bar)
    done = updater.install_pack(channels.plugin, "devonly")
    assert done["ok"] and done["packs"] == ["devonly"]
    bootstrap.sync_menu()
    assert channels.entries(bar).count("Aero-devonly") == 1


def test_a_tool_found_on_disk_is_recorded_as_installed_and_gets_its_entry_at_the_next_tick(channels, monkeypatch, bar):  # noqa: F811
    left_by_the_old_code(channels, monkeypatch)
    assert "devonly" in updater.installed_packs(channels.plugin)
    assert updater.tool_installed("devonly")
    assert "Aero-devonly" not in channels.entries(bar)                             # what the owner saw: installed, no entry
    assert updater.check(channels.plugin)["state"] == "current" and updater.verify(channels.plugin)[1] == []
    bootstrap.guard_tick()                                                         # the menu guard runs every second
    assert channels.entries(bar).count("Aero-devonly") == 1
    bootstrap.guard_tick()
    bar.qapp.processEvents()
    assert channels.entries(bar) == ["Aero-one", "Aero-two", "Aero-devonly"]       # never twice


def test_the_reload_after_an_update_adds_the_entry_at_once(channels, monkeypatch, bar):  # noqa: F811
    left_by_the_old_code(channels, monkeypatch)
    monkeypatch.setattr(bootstrap, "purge_modules", lambda: None)
    monkeypatch.setattr(importlib, "import_module", lambda name, *a, **k: bootstrap)
    assert bootstrap.reload_all() is True
    assert channels.entries(bar).count("Aero-devonly") == 1


def test_going_back_to_a_channel_without_the_tool_removes_it_and_hides_its_entry(channels, monkeypatch, bar):  # noqa: F811
    left_by_the_old_code(channels, monkeypatch)
    bootstrap.guard_tick()
    assert "Aero-devonly" in channels.entries(bar)
    # Back to main: the dev code runs the update, and the remote has no more of the tool.
    channels.publish(MAIN_FILES)
    result = updater.apply(channels.plugin)
    assert result["ok"]
    assert not any(channels.disk(name) for name in DEV_ONLY)
    assert not channels.disk("aerotools/tools/devonly")                           # the folder goes too
    assert not updater.tool_installed("devonly")
    declare(monkeypatch, MAIN_IDS)                                                 # the config.py of main arrived with it
    assert bootstrap.sync_menu() is True
    assert channels.entries(bar) == ["Aero-one", "Aero-two"]
    assert bootstrap.sync_menu() is False                                          # nothing left to do
    assert updater.check(channels.plugin)["state"] == "current" and updater.verify(channels.plugin)[1] == []
    # The entry is hidden, not lost: the tool coming back shows it again, once.
    declare(monkeypatch, DEV_IDS)
    channels.publish(DEV_FILES)
    assert updater.install_pack(channels.plugin, "devonly")["ok"]
    bootstrap.sync_menu()
    assert channels.entries(bar).count("Aero-devonly") == 1


def test_removing_a_tool_takes_its_entry_out_of_the_menu(channels, monkeypatch, bar):  # noqa: F811
    left_by_the_old_code(channels, monkeypatch)
    bootstrap.sync_menu()
    assert "Aero-devonly" in channels.entries(bar)
    assert updater.remove_pack(channels.plugin, "devonly")["ok"]
    bootstrap.sync_menu()
    assert "Aero-devonly" not in channels.entries(bar)
    assert "Aero-one" in channels.entries(bar)


def test_the_menu_is_left_alone_while_nothing_changes(channels, monkeypatch, bar):  # noqa: F811
    main_installation(channels, monkeypatch)
    bootstrap.sync_menu()
    calls = []
    monkeypatch.setattr(bootstrap, "add_tool_menu", lambda *a, **k: calls.append(a))
    bootstrap.sync_menu()
    bootstrap.sync_menu()
    assert calls == []
