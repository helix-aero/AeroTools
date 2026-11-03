"""Le modele de lanceur : dossier absent = une ligne, aucune exception."""

import ast
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(HERE, "install", "aerotools_launcher.py.tpl")


def _render(root):
    with open(TPL, encoding="utf-8") as handle:
        text = handle.read()
    assert text.count("@@AEROTOOLS_ROOT@@") == 1
    return text.replace("@@AEROTOOLS_ROOT@@", root)


def test_missing_root_prints_one_line(tmp_path, capsys):
    exec(compile(_render(str(tmp_path / "absent")), "launcher", "exec"), {})
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1 and out[0].startswith("[AeroTools]")


def test_template_is_python39_and_has_no_pyside_import():
    source = _render(r"\\fileserver\share\AeroTools")
    ast.parse(source, feature_version=(3, 9))
    assert "PySide" not in source


def test_launcher_installs_menu(ms, clean_bootstrap_flags):
    before = list(sys.path)
    exec(compile(_render(HERE), "launcher", "exec"), {})
    assert any(label == "Aero/Recharger AeroTools" for label, _cb, _sc in ms.app.menu_items)
    assert HERE in sys.path
    sys.path[:] = before
