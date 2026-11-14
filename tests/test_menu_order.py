"""The Aero menu keeps the tools first, even for a tool installed while Metashape runs."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")   # the real Qt: aerotools.qt is a stub under the tests

from aerotools import msapi


def test_a_tool_added_late_goes_up_with_the_other_tools(monkeypatch):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = QtWidgets.QMainWindow()
    menu = window.menuBar().addMenu("Aero")
    for text in ("First tool", None, "Install", "Update", None, "About", "Late tool"):
        menu.addSeparator() if text is None else menu.addAction(text)
    monkeypatch.setattr(msapi, "main_window", lambda: window)

    assert msapi.arrange_menu("Aero", [["First tool", "Late tool"], ["Install", "Update"], ["About"]])

    shown = [a.text() or "-" for a in menu.actions()]
    assert shown == ["First tool", "Late tool", "-", "Install", "Update", "-", "About"]
    assert app is not None


def test_a_missing_menu_is_not_an_error(monkeypatch):
    monkeypatch.setattr(msapi, "main_window", lambda: None)
    assert msapi.arrange_menu("Aero", [["x"]]) is False


# The "Aero" menu of Metashape has no Qt parent: Shiboken deletes it with its last Python wrapper, and it leaves the menu bar.

class _Action:
    """A menu-bar action that records the `.menu()` calls."""
    def __init__(self, text, menu=None):
        self._text, self._menu, self.menu_calls = text, menu, 0

    def text(self):
        return self._text

    def menu(self):
        self.menu_calls += 1
        return self._menu


class _Window:
    def __init__(self, actions):
        self._actions = actions

    def menuBar(self):
        return self

    def actions(self):
        return self._actions


@pytest.fixture
def pins(monkeypatch):
    import builtins
    monkeypatch.delattr(builtins, msapi.MENUS_FLAG, raising=False)
    monkeypatch.delattr(builtins, msapi.ACTIONS_FLAG, raising=False)
    yield
    for flag in (msapi.MENUS_FLAG, msapi.ACTIONS_FLAG):
        if hasattr(builtins, flag):
            delattr(builtins, flag)


def test_the_lookup_only_asks_the_matching_action_for_its_menu(monkeypatch, pins):
    aero = object()
    others = [_Action("File", object()), _Action("PhotoGCP", object())]
    target = _Action("&Aero", aero)
    monkeypatch.setattr(msapi, "main_window", lambda: _Window(others + [target]))

    assert msapi.find_menu("Aero") is aero
    assert [a.menu_calls for a in others] == [0, 0]
    assert target.menu_calls == 1
    assert msapi.find_menu("Missing") is None
    assert [a.menu_calls for a in others] == [0, 0]


def test_the_action_is_pinned_with_its_menu_and_the_pin_is_reused(monkeypatch, pins):
    import builtins
    aero = _Menu()
    target = _Action("Aero", aero)
    monkeypatch.setattr(msapi, "main_window", lambda: _Window([target]))

    first = msapi.find_menu("Aero")
    assert getattr(builtins, msapi.ACTIONS_FLAG)["Aero"] is target     # the parent wrapper lives as long as the menu pin
    assert msapi.find_menu("Aero") is first
    assert getattr(builtins, msapi.MENUS_FLAG)["Aero"] is first

    monkeypatch.setattr(msapi, "main_window", lambda: _Window([]))
    assert msapi.find_menu("Aero") is None             # the menu left: both pins go
    assert "Aero" not in getattr(builtins, msapi.MENUS_FLAG)
    assert "Aero" not in getattr(builtins, msapi.ACTIONS_FLAG)


def test_the_menu_is_kept_on_builtins_after_the_lookup_helpers(monkeypatch, pins):
    import builtins
    aero = _Menu()
    monkeypatch.setattr(msapi, "main_window", lambda: _Window([_Action("Aero", aero)]))

    msapi.arrange_menu("Aero", [["x"]])
    assert getattr(builtins, msapi.MENUS_FLAG)["Aero"] is aero


class _Menu:
    def actions(self):
        return []

    def removeAction(self, action):
        pass


def test_a_parentless_menu_survives_the_lookup(monkeypatch, pins):
    import builtins
    import gc
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = QtWidgets.QMainWindow()
    menu = QtWidgets.QMenu("Aero")
    window.menuBar().addMenu(menu)
    menu.setParent(None)                              # as Metashape's addMenuItem leaves it
    monkeypatch.setattr(msapi, "main_window", lambda: window)
    alive = []
    menu.destroyed.connect(lambda *_: alive.append(False))

    msapi.find_menu("Aero")
    del menu                                          # the wrapper the caller held; only the pin is left
    gc.collect()
    assert not alive
    assert getattr(builtins, msapi.MENUS_FLAG)["Aero"].title() == "Aero"
    assert [a.text() for a in window.menuBar().actions()] == ["Aero"]


def test_the_menu_returned_is_valid_after_the_function_returns_and_gc(monkeypatch, pins):
    """Shiboken makes the wrapper of QAction.menu() a child of the action wrapper: released with the loop variable, it took the menu along."""
    import gc
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = QtWidgets.QMainWindow()
    menu = QtWidgets.QMenu("Aero")
    window.menuBar().addMenu(menu)
    menu.setParent(None)
    monkeypatch.setattr(msapi, "main_window", lambda: window)

    found = msapi.find_menu("Aero")
    del menu                                           # Metashape's own wrapper goes: the pinned one must carry the menu
    gc.collect()
    assert msapi.is_valid(found)
    assert found.title() == "Aero"
    assert [a.text() for a in window.menuBar().actions()] == ["Aero"]
