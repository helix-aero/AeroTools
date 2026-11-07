"""Packs: the tools are installed one by one, a pack only imports what it requires, the core never imports a tool."""

import ast
import importlib.util
import os
import shutil

import pytest

from aerotools import config, updater

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = os.path.join(ROOT, "aerotools")


# --------------------------------------------------------------------------
# The declaration
# --------------------------------------------------------------------------


def test_every_tool_has_a_pack_with_its_marker_on_disk():
    assert {entry["id"] for entry in config.TOOLS} == {entry["id"] for entry in config.PACKS}
    for entry in config.PACKS:
        assert os.path.isfile(os.path.join(ROOT, *entry["marker"].split("/"))), entry["id"]
        assert config.pack_of(entry["marker"]) == entry["id"]
        assert set(entry["requires"]) <= {other["id"] for other in config.PACKS}


def test_requirements_form_no_loop():
    for entry in config.PACKS:
        assert config.pack_closure([entry["id"]])[-1] == entry["id"]


def test_pack_of_uses_the_longest_prefix_and_defaults_to_core():
    assert config.pack_of("aerotools/tools/aerol3/tool.py") == "aerol3"
    assert config.pack_of("aerotools/vendor/proj/bin/proj.exe") == "aeroexports"
    assert config.pack_of("models/checker.onnx") == "fastgcp"
    assert config.pack_of("aerotools/updater.py") == config.CORE
    assert config.pack_of("install/install_all.ps1") == config.CORE
    assert config.pack_of("aerotools\\tools\\fastspace\\tool.py") == "fastspace"


def test_closure_puts_requirements_first_and_dependents_are_found():
    assert config.pack_closure(["aerol3"]) == ["aeroexports", "aerofacade", "aerol3"]
    assert config.pack_closure(["fastgcp", "fastgcp"]) == ["fastgcp"]
    assert set(config.dependents("aeroexports")) == {"aerofacade", "aerol3"}
    assert config.dependents("aerol3") == []


# --------------------------------------------------------------------------
# The imports respect the packs
# --------------------------------------------------------------------------

def _imports(path, package_parts):
    """Top-level package names imported by a module: ("tools", "aerofacade") for `from ..aerofacade import x` in a tool."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level:
            base = list(package_parts[:len(package_parts) - (node.level - 1)])
            names = (node.module.split(".") if node.module else [])
            if names:
                found.append(tuple(base + names))
            else:
                found.extend(tuple(base + [alias.name]) for alias in node.names)
        elif isinstance(node, ast.Import):
            found.extend(tuple(alias.name.split(".")) for alias in node.names)
    return found


def _modules():
    for base, dirs, names in os.walk(PACKAGE):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", "vendor")]
        for name in names:
            if name.endswith(".py"):
                path = os.path.join(base, name)
                relative = os.path.relpath(path, ROOT).replace(os.sep, "/")
                package = relative.split("/")[:-1]                       # ["aerotools", "tools", "aerofacade"]
                yield path, relative, package


def test_a_tool_only_imports_its_own_pack_the_core_and_what_it_requires():
    problems = []
    for path, relative, package in _modules():
        owner = config.pack_of(relative)
        if owner == config.CORE:
            continue
        allowed = set(config.pack_closure([owner]))
        for target in _imports(path, package):
            if target[:2] == ("aerotools", "tools") and len(target) > 2:
                other = target[2]
                if other not in allowed:
                    problems.append("{} imports tools.{} but {} does not require it".format(relative, other, owner))
    assert not problems, "\n".join(problems)


def test_the_core_never_imports_a_tool():
    problems = []
    for path, relative, package in _modules():
        if config.pack_of(relative) != config.CORE:
            continue
        for target in _imports(path, package):
            if target[:2] == ("aerotools", "tools"):
                problems.append("{} imports {}".format(relative, ".".join(target)))
    assert not problems, "\n".join(problems)


# --------------------------------------------------------------------------
# Installing and removing, against a published repository (file://)
# --------------------------------------------------------------------------

def _maker():
    spec = importlib.util.spec_from_file_location("make_manifest", os.path.join(ROOT, "install", "make_manifest.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


maker = _maker()

FILES = {
    "aerotools/__init__.py": '__version__ = "9.9.9"\n',
    "aerotools/core.py": "print('core')\n",
    "aerotools/tools/aeroexports/tool.py": "print('exports')\n",
    "aerotools/tools/aeroexports/dem.py": "print('dem')\n",
    "aerotools/vendor/proj/bin/proj.exe": b"\x00\x01" * 2000,
    "aerotools/tools/aerofacade/tool.py": "print('facade')\n",
    "aerotools/tools/aerol3/tool.py": "print('l3')\n",
    "aerotools/tools/fastgcp/tool.py": "print('gcp')\n",
    "models/checker.onnx": b"\x02" * 3000,
}


def write(root, name, data):
    path = os.path.join(str(root), *name.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data if isinstance(data, bytes) else data.encode("utf-8"))


@pytest.fixture
def world(tmp_path, local_appdata):
    """A repository with every tool, and a minimal installation: the core and the manifest, no tool."""
    remote, local = tmp_path / "remote", tmp_path / "local"
    remote.mkdir()
    local.mkdir()
    for name, data in FILES.items():
        write(remote, name, data)
    maker.write(str(remote))
    for name, data in FILES.items():
        if config.pack_of(name) == config.CORE:
            write(local, name, data)
    shutil.copyfile(str(remote / "manifest.json"), str(local / "manifest.json"))
    config.put(updater.SECTION, "base_url", remote.as_uri())
    plugin = {"id": "aerotools", "title": "T", "repo": "x/y", "root": str(local)}
    return {"remote": remote, "local": local, "plugin": plugin}


def test_a_minimal_installation_has_only_the_core_and_is_up_to_date(world):
    plugin = world["plugin"]
    assert updater.installed_packs(plugin) == {config.CORE}
    result = updater.check(plugin)
    assert result["state"] == "current" and result["changes"] == []
    assert updater.verify(plugin)[1] == []


def test_installing_a_tool_brings_what_it_requires_and_nothing_else(world):
    plugin, local = world["plugin"], world["local"]
    result = updater.install_pack(plugin, "aerofacade")
    assert result["ok"] and result["packs"] == ["aeroexports", "aerofacade"]
    assert (local / "aerotools" / "tools" / "aerofacade" / "tool.py").is_file()
    assert (local / "aerotools" / "tools" / "aeroexports" / "dem.py").is_file()
    assert (local / "aerotools" / "vendor" / "proj" / "bin" / "proj.exe").is_file()
    assert not (local / "aerotools" / "tools" / "fastgcp").exists()
    assert not (local / "models").exists()
    assert updater.installed_packs(plugin) == {config.CORE, "aeroexports", "aerofacade"}
    assert updater.check(plugin)["state"] == "current"
    assert updater.verify(plugin)[1] == []


def test_installing_twice_changes_nothing(world):
    updater.install_pack(world["plugin"], "fastgcp")
    again = updater.install_pack(world["plugin"], "fastgcp")
    assert again["ok"] and again["updated"] == 0 and again["packs"] == []


def test_a_damaged_download_leaves_the_tool_not_installed(world):
    plugin, local = world["plugin"], world["local"]
    write(world["remote"], "aerotools/tools/fastgcp/tool.py", "print('other')\n")        # the manifest announces something else
    result = updater.install_pack(plugin, "fastgcp")
    assert not result["ok"] and result["packs"] == []
    assert not (local / "aerotools" / "tools" / "fastgcp").exists()
    assert updater.installed_packs(plugin) == {config.CORE}


def test_an_update_only_concerns_the_installed_tools(world):
    plugin, local = world["plugin"], world["local"]
    updater.install_pack(plugin, "fastgcp")
    write(world["remote"], "aerotools/tools/fastgcp/tool.py", "print('gcp v2')\n")
    write(world["remote"], "aerotools/tools/aerol3/tool.py", "print('l3 v2')\n")
    maker.write(str(world["remote"]))
    result = updater.check(plugin)
    assert result["state"] == "update"
    assert [change["path"] for change in result["changes"]] == ["aerotools/tools/fastgcp/tool.py"]
    assert updater.apply(plugin)["updated"] == 1
    assert not (local / "aerotools" / "tools" / "aerol3").exists()


def test_removing_a_tool_is_refused_while_another_one_needs_it(world):
    plugin = world["plugin"]
    updater.install_pack(plugin, "aerofacade")
    refused = updater.remove_pack(plugin, "aeroexports")
    assert not refused["ok"] and refused["needed_by"] == ["aerofacade"]
    assert updater.is_pack_installed(plugin, "aeroexports")


def test_removing_a_tool_deletes_its_files_and_folders_but_not_the_core(world):
    plugin, local = world["plugin"], world["local"]
    updater.install_pack(plugin, "aerofacade")
    pycache = local / "aerotools" / "tools" / "aerofacade" / "__pycache__"
    pycache.mkdir()
    (pycache / "tool.cpython-39.pyc").write_bytes(b"x")
    assert updater.remove_pack(plugin, "aerofacade")["ok"]
    assert updater.remove_pack(plugin, "aeroexports")["ok"]
    assert not (local / "aerotools" / "tools" / "aerofacade").exists()
    assert not (local / "aerotools" / "tools" / "aeroexports").exists()
    assert not (local / "aerotools" / "vendor").exists()
    assert (local / "aerotools" / "core.py").is_file() and (local / "manifest.json").is_file()
    assert updater.installed_packs(plugin) == {config.CORE}


def test_a_file_the_user_added_to_a_tool_folder_survives_its_removal(world):
    plugin, local = world["plugin"], world["local"]
    updater.install_pack(plugin, "fastgcp")
    mine = local / "models" / "my_model.onnx"
    mine.write_bytes(b"trained by me")
    assert updater.remove_pack(plugin, "fastgcp")["ok"]
    assert mine.read_bytes() == b"trained by me"
    assert not (local / "models" / "checker.onnx").exists()


def test_a_development_copy_has_every_tool_and_refuses_install_and_removal(world):
    plugin, local = world["plugin"], world["local"]
    (local / ".git").mkdir()
    assert updater.installed_packs(plugin) == {config.CORE} | {entry["id"] for entry in config.PACKS}
    assert not updater.install_pack(plugin, "fastgcp")["ok"]
    assert not updater.remove_pack(plugin, "fastgcp")["ok"]


def test_pack_status_says_what_is_installed_what_it_weighs_and_what_is_missing(world):
    plugin = world["plugin"]
    updater.install_pack(plugin, "aeroexports")
    remote, _raw = updater.remote_manifest(plugin)
    status = {item["id"]: item for item in updater.pack_status(plugin, remote)}
    assert status["aeroexports"]["installed"] and status["aeroexports"]["needs"] == []
    assert not status["aerol3"]["installed"] and status["aerol3"]["needs"] == ["aerofacade"]
    assert status["aeroexports"]["bytes"] >= 4000 and status["fastgcp"]["bytes"] >= 3000
    assert status["aeroexports"]["update"] == 0


def test_an_unknown_tool_is_refused(world):
    assert not updater.install_pack(world["plugin"], "nothing")["ok"]
    assert not updater.remove_pack(world["plugin"], "nothing")["ok"]
