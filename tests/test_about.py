"""The About entry: the window itself is built and checked with real Qt in tests_qt/run_qt_news_about.py (run by test_news_about_qt.py);
here the menu entry, its fallback and the links that must never come back."""

import os
import sys
import types

from aerotools import bootstrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OLD_GROUP = "240884" + "1503199237"      # written in two parts: this file must not hold the id either
COMMUNITY = "https://www.facebook.com/groups/1852563252425052"
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules"}


def test_the_old_community_group_is_nowhere_in_the_tree():
    found = []
    for base, dirs, names in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in names:
            path = os.path.join(base, name)
            try:
                with open(path, "rb") as handle:
                    data = handle.read()
            except OSError:
                continue
            if OLD_GROUP.encode() in data:
                found.append(os.path.relpath(path, ROOT))
    assert found == []


def test_the_about_window_names_the_three_links():
    with open(os.path.join(ROOT, "aerotools", "about_dialog.py"), encoding="utf-8") as handle:
        source = handle.read()
    for address in ("https://helix.aero\"", "https://docs.helix.aero\"", COMMUNITY + "\""):
        assert address in source
    assert OLD_GROUP not in source


def test_about_opens_the_window(ms, monkeypatch):
    opened = []
    fake = types.ModuleType("aerotools.about_dialog")
    fake.show = lambda parent=None: opened.append(parent)
    monkeypatch.setitem(sys.modules, "aerotools.about_dialog", fake)
    monkeypatch.setattr(sys.modules["aerotools"], "about_dialog", fake, raising=False)
    bootstrap.about()
    assert len(opened) == 1 and ms.app.messages == []


def test_about_falls_back_to_a_message_when_the_window_cannot_open(ms, monkeypatch):
    fake = types.ModuleType("aerotools.about_dialog")

    def broken(parent=None):
        raise RuntimeError("no window")

    fake.show = broken
    monkeypatch.setitem(sys.modules, "aerotools.about_dialog", fake)
    monkeypatch.setattr(sys.modules["aerotools"], "about_dialog", fake, raising=False)
    bootstrap.about()
    assert len(ms.app.messages) == 1 and "About window could not open" in ms.app.messages[0] and "no window" in ms.app.messages[0]
