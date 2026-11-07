"""install/install_core.ps1: the installer copies the core only and writes the launcher (needs Windows PowerShell)."""

import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "install", "install_core.ps1")
POWERSHELL = shutil.which("powershell")

pytestmark = pytest.mark.skipif(os.name != "nt" or not POWERSHELL, reason="needs Windows PowerShell")


def run(local, *args):
    env = dict(os.environ, LOCALAPPDATA=str(local))
    return subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", SCRIPT, "-Copy", *args],
                          capture_output=True, text=True, env=env, timeout=120)


def test_the_manifest_lists_the_packs_for_the_installer():
    with open(os.path.join(ROOT, "manifest.json"), encoding="utf-8") as handle:
        manifest = json.load(handle)
    from aerotools import config
    assert [pack["id"] for pack in manifest["packs"]] == [pack["id"] for pack in config.PACKS]
    assert all(pack["paths"] for pack in manifest["packs"])


def test_check_before_installing_reports_a_missing_menu(tmp_path):
    result = run(tmp_path, "-Check")
    assert result.returncode == 2 and "NOT installed" in result.stdout


def test_the_installer_copies_the_core_only_and_writes_the_launcher(tmp_path):
    result = run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    app = tmp_path / "HELIX" / "AeroToolsApp"
    assert (app / "aerotools" / "bootstrap.py").is_file() and (app / "manifest.json").is_file()
    assert (app / "install" / "install_all.ps1").is_file()                    # the AI installer travels with the core
    assert (app / "aerotools" / "tools" / "__init__.py").is_file()
    for owned in ("aerotools/tools/aerofacade", "aerotools/tools/fastgcp", "aerotools/vendor", "models"):
        assert not (app / owned).exists(), owned
    launcher = tmp_path / "Agisoft" / "Metashape Pro" / "scripts" / "aerotools_launcher.py"
    assert str(app) in launcher.read_text(encoding="utf-8")


def test_running_it_again_changes_nothing_and_leaves_installed_tools_alone(tmp_path):
    run(tmp_path)
    app = tmp_path / "HELIX" / "AeroToolsApp"
    tool = app / "aerotools" / "tools" / "fastspace"
    tool.mkdir()
    (tool / "tool.py").write_text("# installed later")
    again = run(tmp_path)
    assert again.returncode == 0 and "already installed" in again.stdout
    assert (tool / "tool.py").read_text() == "# installed later"
    assert run(tmp_path, "-Check").returncode == 0


def test_a_folder_without_the_manifest_is_refused_with_a_clear_message(tmp_path):
    bare = tmp_path / "bare"
    (bare / "aerotools").mkdir(parents=True)
    (bare / "aerotools" / "bootstrap.py").write_text("# x")
    (bare / "install").mkdir()
    shutil.copyfile(os.path.join(ROOT, "install", "aerotools_launcher.py.tpl"), str(bare / "install" / "aerotools_launcher.py.tpl"))
    env = dict(os.environ, LOCALAPPDATA=str(tmp_path / "local"))
    result = subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", SCRIPT, "-Root", str(bare), "-Copy"],
                            capture_output=True, text=True, env=env, timeout=120)
    assert result.returncode == 1 and "manifest.json not found" in result.stdout
