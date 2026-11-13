"""Runs the real-Qt harness of the UI kit (tests_qt/run_qt_uikit.py) in a clean interpreter: behaviour of every component, long strings,
100 / 125 / 150 % scaling. Skipped when no Qt binding is installed. The pytest session itself runs with a fake Qt, hence the subprocess."""

import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARNESS = os.path.join(ROOT, "tests_qt", "run_qt_uikit.py")


def _has_qt():
    for binding in ("PySide2", "PySide6"):
        if subprocess.run([sys.executable, "-I", "-c", "import " + binding], capture_output=True).returncode == 0:
            return True
    return False


@pytest.mark.skipif(not os.path.isfile(HARNESS), reason="tests_qt is not part of this tree")
def test_uikit_harness_passes_offscreen():
    if not _has_qt():
        pytest.skip("no Qt binding installed")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONDONTWRITEBYTECODE="1")
    done = subprocess.run([sys.executable, "-B", HARNESS, "--no-shots"], capture_output=True, text=True, timeout=240, env=env)
    failed = [line for line in done.stdout.splitlines() if line.startswith("FAIL")]
    assert done.returncode == 0 and not failed, "\n".join(failed) + "\n" + done.stderr[-1500:]
    assert "0 checks failed" in done.stdout
