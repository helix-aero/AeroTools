"""The lines written to the Log (the window the README asks users to paste) use the names of the tools as the menu shows them.
The old names stay in nothing a user can read."""
import ast
import glob
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OLD_NAMES = re.compile(r"fast[-_ ]?(gcp|space|export)", re.IGNORECASE)


def journal_texts():
    """(file, line, text) of every journal.write(...) whose text holds a literal part."""
    found = []
    for path in sorted(glob.glob(os.path.join(ROOT, "aerotools", "**", "*.py"), recursive=True)):
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), path)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "write"):
                continue
            owner = node.func.value
            if not (isinstance(owner, ast.Name) and owner.id == "journal") or not node.args:
                continue
            for part in ast.walk(node.args[0]):
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    found.append((os.path.relpath(path, ROOT), node.lineno, part.value))
    return found


def test_the_scan_sees_the_journal_lines():
    assert len(journal_texts()) > 30


def test_no_journal_line_uses_an_old_tool_name():
    bad = ["{}:{} {!r}".format(path, line, text) for path, line, text in journal_texts() if OLD_NAMES.search(text)]
    assert not bad, "old tool name in the Log:\n" + "\n".join(bad)

