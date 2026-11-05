import builtins
import importlib
import sys

import pytest

from aerotools import bootstrap, config


@pytest.fixture
def boot(ms, clean_bootstrap_flags):
    # purge_modules() vide sys.modules : on le restaure pour les autres fichiers.
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
    assert "Aero/Recharger AeroTools" in labels
    assert "Aero/Verifier les mises a jour..." in labels
    assert "Aero/FastGCP" in labels


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
        raise AssertionError("import inattendu : " + name)

    cfg = sys.modules["aerotools.config"]
    future = {"id": "futur", "menu": "Futur", "titre": "Futur", "sous_titre": "tool.fastgcp.subtitle",
              "raccourci": None, "pret": False}
    monkeypatch.setattr(cfg, "TOOLS", list(cfg.TOOLS) + [future])
    monkeypatch.setattr(importlib, "import_module", forbidden)
    assert boot.launch("futur") is False
    assert len(ms.app.messages) == 1
    assert "Futur" in ms.app.messages[0]


def test_launch_unknown_tool(boot, ms):
    assert boot.launch("nexiste-pas") is False
    assert "inconnu" in ms.app.messages[0]


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


def test_launch_survives_failing_tool(boot, ms, monkeypatch):
    class Broken(object):
        @staticmethod
        def install():
            raise RuntimeError("panne")

    monkeypatch.setattr(importlib, "import_module", lambda name, *a, **k: Broken)
    assert boot.launch("fastgcp") is False
    assert "n'a pas demarre" in ms.app.messages[0]


def test_menu_callback_goes_through_launch(boot, ms, monkeypatch):
    seen = []
    monkeypatch.setattr(boot, "launch", lambda tool_id, reload=False: seen.append(tool_id))
    callback = boot._make_launcher("fastgcp")
    callback()
    assert seen == ["fastgcp"]
    assert callback.__name__ == "aero_launch_fastgcp"
