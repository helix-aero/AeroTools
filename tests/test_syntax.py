"""All the package code must parse as Python 3.9 (Metashape's embedded Python)."""

import ast
import os

import pytest

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "aerotools")
FILES = [
    os.path.join(folder, name)
    for folder, _dirs, names in os.walk(PKG)
    if "__pycache__" not in folder
    for name in names
    if name.endswith(".py")
]


def test_files_found():
    assert FILES


@pytest.mark.parametrize("path", FILES, ids=[os.path.relpath(p, PKG) for p in FILES])
def test_parses_as_python_39(path):
    with open(path, "rb") as handle:
        source = handle.read()
    ast.parse(source, filename=path, feature_version=(3, 9))
