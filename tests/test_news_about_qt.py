"""Runs the real-Qt harness of the News window and the About window (tests_qt/run_qt_news_about.py) in a clean interpreter, on the three skins.
Skipped when no Qt binding is installed. The pytest session itself runs with a fake Qt, hence the subprocess."""

import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARNESS = os.path.join(ROOT, "tests_qt", "run_qt_news_about.py")


def _has_qt():
    return any(subprocess.run([sys.executable, "-I", "-c", "import " + binding], capture_output=True).returncode == 0
               for binding in ("PySide2", "PySide6"))


@pytest.mark.skipif(not os.path.isfile(HARNESS), reason="tests_qt is not part of this tree")
def test_news_and_about_windows_offscreen():
    if not _has_qt():
        pytest.skip("no Qt binding installed")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONDONTWRITEBYTECODE="1")
    done = subprocess.run([sys.executable, "-B", HARNESS], capture_output=True, text=True, timeout=240, env=env)
    failed = [line for line in done.stdout.splitlines() if line.startswith("FAIL")]
    assert done.returncode == 0 and not failed, "\n".join(failed) + "\n" + done.stderr[-1500:]
    assert "0 checks failed" in done.stdout
