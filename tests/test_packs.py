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


def _pack(pack_id, paths, requires=()):
    return {"id": pack_id, "paths": list(paths), "marker": paths[0] + "tool.py", "requires": list(requires)}


# A made-up registry: the rules below hold whatever tools the repository has.
#   alpha owns aerotools/vendor/, delta owns the library folder inside it (the longest prefix wins)
#   beta needs alpha, gamma needs beta, delta needs alpha and omega, omega owns the models
FAKE_PACKS = [
    _pack("alpha", ["aerotools/tools/alpha/", "aerotools/vendor/"]),
    _pack("beta", ["aerotools/tools/beta/"], ["alpha"]),
    _pack("gamma", ["aerotools/tools/gamma/"], ["beta"]),
    _pack("delta", ["aerotools/tools/delta/", "aerotools/vendor/lib/"], ["alpha", "omega"]),
    _pack("omega", ["aerotools/tools/omega/", "models/"]),
]


@pytest.fixture
def fake_packs(monkeypatch):
    monkeypatch.setattr(config, "PACKS", [dict(entry) for entry in FAKE_PACKS])


def test_pack_of_uses_the_longest_prefix_and_defaults_to_core(fake_packs):
    assert config.pack_of("aerotools/tools/gamma/tool.py") == "gamma"
    assert config.pack_of("aerotools/vendor/proj/bin/proj.exe") == "alpha"
    assert config.pack_of("aerotools/vendor/lib/engine.exe") == "delta"
    assert config.pack_of("aerotools/tools/delta/pipeline.py") == "delta"
    assert config.pack_of("models/checker.onnx") == "omega"
    assert config.pack_of("aerotools/updater.py") == config.CORE
    assert config.pack_of("install/install_all.ps1") == config.CORE
    assert config.pack_of("aerotools\\tools\\beta\\tool.py") == "beta"


def test_closure_puts_requirements_first_and_dependents_are_found(fake_packs):
    assert config.pack_closure(["gamma"]) == ["alpha", "beta", "gamma"]
    assert config.pack_closure(["omega", "omega"]) == ["omega"]
    assert config.pack_closure(["delta"]) == ["alpha", "omega", "delta"]
    assert set(config.dependents("alpha")) == {"beta", "gamma", "delta"}
    assert config.dependents("omega") == ["delta"]
    assert config.dependents("gamma") == []


# --------------------------------------------------------------------------
# The imports respect the packs
# --------------------------------------------------------------------------

def _imports(path, package_parts):
    """Top-level package names imported by a module: ("tools", "beta") for `from ..beta import x` in a tool."""
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
                package = relative.split("/")[:-1]                       # ["aerotools", "tools", "beta"]
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
    "aerotools/tools/alpha/tool.py": "print('alpha')\n",
    "aerotools/tools/alpha/data.py": "print('data')\n",
    "aerotools/vendor/proj/bin/proj.exe": b"\x00\x01" * 2000,
    "aerotools/tools/beta/tool.py": "print('beta')\n",
    "aerotools/tools/gamma/tool.py": "print('gamma')\n",
    "aerotools/tools/omega/tool.py": "print('omega')\n",
    "models/checker.onnx": b"\x02" * 3000,
}


def write(root, name, data):
    path = os.path.join(str(root), *name.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data if isinstance(data, bytes) else data.encode("utf-8"))


@pytest.fixture
def world(tmp_path, local_appdata, fake_packs):
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
    result = updater.install_pack(plugin, "beta")
    assert result["ok"] and result["packs"] == ["alpha", "beta"]
    assert (local / "aerotools" / "tools" / "beta" / "tool.py").is_file()
    assert (local / "aerotools" / "tools" / "alpha" / "data.py").is_file()
    assert (local / "aerotools" / "vendor" / "proj" / "bin" / "proj.exe").is_file()
    assert not (local / "aerotools" / "tools" / "omega").exists()
    assert not (local / "models").exists()
    assert updater.installed_packs(plugin) == {config.CORE, "alpha", "beta"}
    assert updater.check(plugin)["state"] == "current"
    assert updater.verify(plugin)[1] == []


def test_installing_twice_changes_nothing(world):
    updater.install_pack(world["plugin"], "omega")
    again = updater.install_pack(world["plugin"], "omega")
    assert again["ok"] and again["updated"] == 0 and again["packs"] == []


def test_a_damaged_download_leaves_the_tool_not_installed(world):
    plugin, local = world["plugin"], world["local"]
    write(world["remote"], "aerotools/tools/omega/tool.py", "print('other')\n")        # the manifest announces something else
    result = updater.install_pack(plugin, "omega")
    assert not result["ok"] and result["packs"] == []
    assert not (local / "aerotools" / "tools" / "omega").exists()
    assert updater.installed_packs(plugin) == {config.CORE}


def test_an_update_only_concerns_the_installed_tools(world):
    plugin, local = world["plugin"], world["local"]
    updater.install_pack(plugin, "omega")
    write(world["remote"], "aerotools/tools/omega/tool.py", "print('omega v2')\n")
    write(world["remote"], "aerotools/tools/gamma/tool.py", "print('gamma v2')\n")
    maker.write(str(world["remote"]))
    result = updater.check(plugin)
    assert result["state"] == "update"
    assert [change["path"] for change in result["changes"]] == ["aerotools/tools/omega/tool.py"]
    assert updater.apply(plugin)["updated"] == 1
    assert not (local / "aerotools" / "tools" / "gamma").exists()


def test_removing_a_tool_is_refused_while_another_one_needs_it(world):
    plugin = world["plugin"]
    updater.install_pack(plugin, "beta")
    refused = updater.remove_pack(plugin, "alpha")
    assert not refused["ok"] and refused["needed_by"] == ["beta"]
    assert updater.is_pack_installed(plugin, "alpha")


def test_removing_a_tool_deletes_its_files_and_folders_but_not_the_core(world):
    plugin, local = world["plugin"], world["local"]
    updater.install_pack(plugin, "beta")
    pycache = local / "aerotools" / "tools" / "beta" / "__pycache__"
    pycache.mkdir()
    (pycache / "tool.cpython-39.pyc").write_bytes(b"x")
    assert updater.remove_pack(plugin, "beta")["ok"]
    assert updater.remove_pack(plugin, "alpha")["ok"]
    assert not (local / "aerotools" / "tools" / "beta").exists()
    assert not (local / "aerotools" / "tools" / "alpha").exists()
    assert not (local / "aerotools" / "vendor").exists()
    assert (local / "aerotools" / "core.py").is_file() and (local / "manifest.json").is_file()
    assert updater.installed_packs(plugin) == {config.CORE}


def test_a_file_the_user_added_to_a_tool_folder_survives_its_removal(world):
    plugin, local = world["plugin"], world["local"]
    updater.install_pack(plugin, "omega")
    mine = local / "models" / "my_model.onnx"
    mine.write_bytes(b"trained by me")
    assert updater.remove_pack(plugin, "omega")["ok"]
    assert mine.read_bytes() == b"trained by me"
    assert not (local / "models" / "checker.onnx").exists()


def test_a_development_copy_has_every_tool_and_refuses_install_and_removal(world):
    plugin, local = world["plugin"], world["local"]
    (local / ".git").mkdir()
    assert updater.installed_packs(plugin) == {config.CORE} | {entry["id"] for entry in config.PACKS}
    assert not updater.install_pack(plugin, "omega")["ok"]
    assert not updater.remove_pack(plugin, "omega")["ok"]


def test_pack_status_says_what_is_installed_what_it_weighs_and_what_is_missing(world):
    plugin = world["plugin"]
    updater.install_pack(plugin, "alpha")
    remote, _raw = updater.remote_manifest(plugin)
    status = {item["id"]: item for item in updater.pack_status(plugin, remote)}
    assert status["alpha"]["installed"] and status["alpha"]["needs"] == []
    assert not status["gamma"]["installed"] and status["gamma"]["needs"] == ["beta"]
    assert status["alpha"]["bytes"] >= 4000 and status["omega"]["bytes"] >= 3000
    assert status["alpha"]["update"] == 0


def test_an_unknown_tool_is_refused(world):
    assert not updater.install_pack(world["plugin"], "nothing")["ok"]
    assert not updater.remove_pack(world["plugin"], "nothing")["ok"]
