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
