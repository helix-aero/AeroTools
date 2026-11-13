"""The tool icons (one per shipped tool, plus the suite): names exist; the real-Qt script checks that each draws at 16 / 24 / 48 px and that
no two look alike (run in a clean interpreter, the pytest session itself uses a fake Qt)."""

import os
import subprocess
import sys

import pytest

from aerotools.uikit import icons

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "tests_qt", "run_qt_tool_icons.py")


def test_the_names_exist():
    assert icons.TOOLS[-1] == "suite" and len(set(icons.TOOLS)) == len(icons.TOOLS) >= 4
    for key in icons.TOOLS:
        assert "tool_" + key in icons.NAMES and "glyph_" + key in icons.NAMES


def test_icons_draw_and_differ_offscreen():
    if not os.path.isfile(SCRIPT):
        pytest.skip("tests_qt is not part of this tree")
    if all(subprocess.run([sys.executable, "-I", "-c", "import " + b], capture_output=True).returncode for b in ("PySide2", "PySide6")):
        pytest.skip("no Qt binding installed")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONDONTWRITEBYTECODE="1")
    done = subprocess.run([sys.executable, "-B", SCRIPT], capture_output=True, text=True, timeout=120, env=env)
    failed = [l for l in done.stdout.splitlines() if l.startswith("FAIL")]
    assert done.returncode == 0 and not failed, "\n".join(failed) + done.stderr[-800:]
