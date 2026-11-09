"""A tool that needs no other pack imports no other tool: the packs declared standalone really are.

Static scan of every module under aerotools/tools/<tool>/ for a tool whose pack has an empty `requires`: relative and absolute
imports (also the ones inside functions), `from .. import other`, and import_module / __import__ calls with a literal name. Code two
tools both need belongs to the core (aerotools/*.py). Nothing is allow-listed. A tool that does require another pack is checked
against its declaration by test_packs.py.
"""

import ast
import os

import pytest

from aerotools import config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(ROOT, "aerotools", "tools")
STANDALONE = [entry["id"] for entry in config.PACKS if not entry["requires"]]


def _modules(tool_id):
    for base, dirs, names in os.walk(os.path.join(TOOLS_DIR, tool_id)):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in sorted(names):
            if name.endswith(".py"):
                yield os.path.join(base, name)


def _package_parts(path):
    """["aerotools", "tools", "<tool>", ...] of the package that holds a module."""
    return os.path.relpath(os.path.dirname(path), ROOT).split(os.sep)


def _targets_in(source, package):
    """Dotted names (as tuples) of everything a module imports, resolved against the package that holds it."""
    tree = ast.parse(source)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            base = package[:len(package) - (node.level - 1)] if node.level else []
            names = node.module.split(".") if node.module else []
            if names:
                found.append(tuple(base + names))
            found.extend(tuple(base + names + [alias.name]) for alias in node.names)
        elif isinstance(node, ast.Import):
            found.extend(tuple(alias.name.split(".")) for alias in node.names)
        elif isinstance(node, ast.Call):
            callee = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if callee in ("import_module", "__import__") and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    found.append(tuple(arg.value.split(".")))
    return found


def _targets(path):
    with open(path, encoding="utf-8") as handle:
        return _targets_in(handle.read(), _package_parts(path))


def _other_tool(target, tool_id):
    """The other tool a dotted name points into, or None."""
    if target[:2] == ("aerotools", "tools") and len(target) > 2 and target[2] != tool_id:
        return target[2]
    return None


def test_at_least_one_tool_is_declared_standalone():
    assert STANDALONE, "no pack with an empty `requires`"


@pytest.mark.parametrize("tool_id", STANDALONE)
def test_a_standalone_tool_imports_no_other_tool(tool_id):
    problems = []
    for path in _modules(tool_id):
        for target in _targets(path):
            other = _other_tool(target, tool_id)
            if other:
                problems.append("{} imports tools.{}".format(os.path.relpath(path, ROOT).replace(os.sep, "/"), other))
    assert not problems, "\n".join(sorted(set(problems)))




def test_the_scan_sees_the_imports_it_is_meant_to_find():
    source = (
        "import importlib\n"
        "from ..other import thing\n"
        "from .. import other\n"
        "from aerotools.tools.other import thing as again\n"
        "def lazy():\n"
        "    importlib.import_module('aerotools.tools.other.deep')\n"
        "    return __import__('aerotools.tools.other')\n"
    )
    found = _targets_in(source, ["aerotools", "tools", "mine"])
    pointing = [target for target in found if _other_tool(target, "mine") == "other"]
    assert len(pointing) == 7, found
    assert _other_tool(("aerotools", "tools", "mine", "x"), "mine") is None
    assert _other_tool(("aerotools", "i18n"), "mine") is None
