import builtins
import importlib
import sys

import pytest

from aerotools import bootstrap, config


@pytest.fixture
def boot(ms, clean_bootstrap_flags):
    # purge_modules() empties sys.modules: restore it for the other files.
    saved = {k: v for k, v in sys.modules.items() if k.startswith("aerotools")}
    yield bootstrap
    for key in [k for k in sys.modules if k.startswith("aerotools")]:
        del sys.modules[key]
    sys.modules.update(saved)


def test_install_menu_is_idempotent(boot, ms):
    assert boot.install_menu() is True
    count = len(ms.app.menu_items)
    assert count == len(config.TOOLS) + 3
    assert boot.install_menu() is False
    assert len(ms.app.menu_items) == count
    labels = [item[0] for item in ms.app.menu_items]
    assert "Aero/Reload AeroTools" in labels
    assert "Aero/Check for updates..." in labels
    assert "Aero/Aero-GCP" in labels


def test_menu_groups_tools_upkeep_and_about_with_separators(boot, ms):
    boot.install_menu()
    order = ms.app.menu_order
    kinds = [kind for kind, _label in order]
    labels = [label for kind, label in order if kind == "item"]
    assert kinds.count("separator") == 2
    first, second = kinds.index("separator"), len(kinds) - 1 - kinds[::-1].index("separator")
    assert [label for kind, label in order[:first]] == ["Aero/" + entry["menu"] for entry in config.TOOLS]
    assert [label for _kind, label in order[first + 1:second]] == ["Aero/Check for updates...",
                                                                  "Aero/Reload AeroTools"]
    assert [label for _kind, label in order[second + 1:]] == ["Aero/About"]
    assert labels[0] == "Aero/Aero-GCP"


def test_menu_still_installs_when_separators_are_unavailable(boot, ms, monkeypatch):
    monkeypatch.delattr(type(ms.app), "addMenuSeparator")
    assert boot.install_menu() is True
    assert len(ms.app.menu_items) == len(config.TOOLS) + 3


def test_install_menu_force_registers_again(boot, ms):
    boot.install_menu()
    count = len(ms.app.menu_items)
    assert boot.install_menu(force=True) is True
    assert len(ms.app.menu_items) == 2 * count


def test_flag_survives_module_purge(boot, ms):
    boot.install_menu()
    boot.purge_modules()
    assert getattr(builtins, "_AEROTOOLS_MENU_INSTALLED") is True


def test_launch_not_ready_tool_loads_nothing(boot, ms, monkeypatch):
    def forbidden(name, *args, **kwargs):
        raise AssertionError("unexpected import: " + name)

    cfg = sys.modules["aerotools.config"]
    future = {"id": "future", "menu": "Future", "title": "Future", "subtitle": "tool.fastgcp.subtitle",
              "shortcut": None, "ready": False}
    monkeypatch.setattr(cfg, "TOOLS", list(cfg.TOOLS) + [future])
    monkeypatch.setattr(importlib, "import_module", forbidden)
    assert boot.launch("future") is False
    assert len(ms.app.messages) == 1
    assert "Future" in ms.app.messages[0]


def test_launch_unknown_tool(boot, ms):
    assert boot.launch("no-such-tool") is False
    assert "unknown tool" in ms.app.messages[0]


def test_launch_calls_install_of_ready_tool(boot, ms, monkeypatch):
    called = []

    class FakeTool(object):
        @staticmethod
        def install():
            called.append("install")

    monkeypatch.setattr(importlib, "import_module",
                        lambda name, *a, **k: FakeTool if name.endswith(".fastgcp.tool") else None)
    assert boot.launch("fastgcp") is True
    assert called == ["install"]
    assert getattr(builtins, "_AEROTOOLS_LAST_TOOL") == "fastgcp"


def test_one_off_window_is_not_reopened_by_reload(boot, ms, monkeypatch):
    opened = []

    class FakeTool(object):
        @staticmethod
        def install():
            opened.append("install")

    monkeypatch.setattr(importlib, "import_module", lambda name, *a, **k: FakeTool)
    assert boot.launch("fastgcp") is True
    assert boot.launch("fastspace") is True          # Aero-Space is a one-off window
    assert getattr(builtins, "_AEROTOOLS_LAST_TOOL") == "fastgcp"
    assert [entry["id"] for entry in config.TOOLS if not entry.get("reopen")] == ["fastspace", "aeroexports", "aerofacade", "aerol3"]


def test_reopens_only_panels(boot):
    assert boot.reopens("fastgcp") is True
    assert boot.reopens("fastspace") is False
    assert boot.reopens("no-such-tool") is False


def test_reload_ignores_a_stale_flag_left_by_an_old_version(boot, ms, monkeypatch):
    launched = []
    boot.root()
    monkeypatch.setattr(boot, "purge_modules", lambda: None)
    monkeypatch.setattr(importlib, "import_module", lambda name, *a, **k: boot)
    monkeypatch.setattr(boot, "launch", lambda tool_id, reload=False: launched.append(tool_id))
    setattr(builtins, "_AEROTOOLS_LAST_TOOL", "fastspace")           # what the old code left there
    assert boot.reload_all() is True
    assert launched == []
    setattr(builtins, "_AEROTOOLS_LAST_TOOL", "fastgcp")
    boot.reload_all()
    assert launched == ["fastgcp"]


def test_reload_without_a_panel_opens_nothing(boot, ms, monkeypatch):
    launched = []
    monkeypatch.setattr(boot, "purge_modules", lambda: None)
    monkeypatch.setattr(importlib, "import_module",
                        lambda name, *a, **k: type("M", (), {"launch": staticmethod(lambda t: launched.append(t))}))
    boot.launch  # the flag is empty: no tool has been opened
    assert not hasattr(builtins, "_AEROTOOLS_LAST_TOOL")
    assert boot.reload_all() is True
    assert launched == []


def test_launch_survives_failing_tool(boot, ms, monkeypatch):
    class Broken(object):
        @staticmethod
        def install():
            raise RuntimeError("breakdown")

    monkeypatch.setattr(importlib, "import_module", lambda name, *a, **k: Broken)
    assert boot.launch("fastgcp") is False
    assert "did not start" in ms.app.messages[0]


def test_menu_callback_goes_through_launch(boot, ms, monkeypatch):
    seen = []
    monkeypatch.setattr(boot, "launch", lambda tool_id, reload=False: seen.append(tool_id))
    callback = boot._make_launcher("fastgcp")
    callback()
    assert seen == ["fastgcp"]
    assert callback.__name__ == "aero_launch_fastgcp"
