"""The menu guard: it follows the Aero menu of Metashape's menu bar, brings it back if it leaves, and keeps the logo button on it.

The guard is a second line of defence (the menu is pinned with its action by msapi.find_menu): if the menu is replaced or leaves the bar anyway, it is pinned again or restored. Real Qt (offscreen), with a
fake `addMenuItem` that does what Metashape's does: a parentless QMenu per top-level title, filled in call order."""

import builtins
import gc
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")   # the real Qt: aerotools.qt is a stub under the tests
from PySide6 import QtCore, QtGui

from aerotools import bootstrap, config, journal, msapi

RESTORED = "the Aero menu had left the menu bar: restored"


class Bar(object):
    """A main window and the `addMenuItem` / `addMenuSeparator` of Metashape's application."""

    def __init__(self):
        self.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.window = QtWidgets.QMainWindow()
        self.journal = []
        self.held = []                                 # what keeps Metashape's first menu alive until it rebuilds it

    def _menu(self, title):
        for action in self.window.menuBar().actions():
            if action.text() == title:
                return action.menu()
        menu = QtWidgets.QMenu(title)                  # parentless, as Metashape leaves it
        self.window.menuBar().addMenu(menu)
        menu.setParent(None)
        self.held.append(menu)
        return menu

    def addMenuItem(self, label, callback, shortcut=None):
        title, _slash, name = label.partition("/")
        self._menu(title).addAction(name)

    def addMenuSeparator(self, title):
        self._menu(title).addSeparator()

    def menu_actions(self, title="Aero"):
        """Texts of the entries of the menu as the menu bar shows them now, or None when the menu is not in the bar."""
        for action in self.window.menuBar().actions():
            if action.text() == title:
                return [a.text() or "-" for a in action.menu().actions()]
        return None

    def rebuild(self, title="Aero"):
        """What Metashape does after a later startup script: a new menu object in the bar, the old one deleted."""
        for action in self.window.menuBar().actions():
            if action.text() == title:
                old = action.menu()
                new = QtWidgets.QMenu(title)
                for entry in old.actions():
                    if entry.isSeparator():
                        new.addSeparator()
                    else:
                        new.addAction(entry.text())
                self.window.menuBar().addMenu(new)     # its own action: the old menu's action goes with the old menu
                new.setParent(None)
                if old in self.held:
                    self.held.remove(old)
                old.deleteLater()
                self.qapp.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
                return new

    def remove(self, title="Aero"):
        for action in self.window.menuBar().actions():
            if action.text() == title:
                self.window.menuBar().removeAction(action)


FLAGS = ("_AEROTOOLS_MENU_INSTALLED", "_AEROTOOLS_MENU_TOOLS", "_AEROTOOLS_MENU_RETRIES", "_AEROTOOLS_MENU_GUARD",
         "_AEROTOOLS_MENU_RESTORES", msapi.MENUS_FLAG, msapi.ACTIONS_FLAG)


@pytest.fixture
def bar(ms, monkeypatch):
    monkeypatch.setattr(msapi, "QtCore", QtCore)
    monkeypatch.setattr(msapi, "QtWidgets", QtWidgets)
    import aerotools.qt as qt
    monkeypatch.setattr(qt, "QtCore", QtCore)
    monkeypatch.setattr(qt, "QtGui", QtGui)
    fake = Bar()
    ms.app.addMenuItem = fake.addMenuItem
    ms.app.addMenuSeparator = fake.addMenuSeparator
    monkeypatch.setattr(msapi, "main_window", lambda: fake.window)
    monkeypatch.setattr(journal, "write", lambda message, tag="AeroTools": fake.journal.append(message))
    for flag in FLAGS:
        if hasattr(builtins, flag):
            delattr(builtins, flag)
    yield fake
    fake.qapp.processEvents()                          # a first tick still pending runs now, not during the next test
    for timer in fake.window.findChildren(QtCore.QTimer):
        timer.stop()
    for flag in FLAGS:
        if hasattr(builtins, flag):
            delattr(builtins, flag)
    fake.window.deleteLater()


def _pinned():
    return getattr(builtins, msapi.MENUS_FLAG, {}).get("Aero")


def _corner(bar):
    return bar.window.menuBar().cornerWidget(QtCore.Qt.TopRightCorner)


def test_a_replaced_menu_is_pinned_again_by_the_guard(bar):
    bootstrap.install_menu()
    old = _pinned()
    new = bar.rebuild()
    assert not msapi.is_valid(old)                     # what was measured live: the pin outlived its C++ menu
    bootstrap.guard_tick()
    assert _pinned() is new and msapi.is_valid(_pinned())
    assert RESTORED not in bar.journal


def test_the_pinned_menu_survives_a_collection_after_the_guard(bar):
    bootstrap.install_menu()
    new = bar.rebuild()
    bootstrap.guard_tick()
    del new                                            # the caller's wrapper goes: the pin is all that is left
    gc.collect()
    assert bar.menu_actions() is not None
    assert msapi.find_menu("Aero") is _pinned()
    assert RESTORED not in bar.journal


def test_a_menu_that_left_the_bar_comes_back_once_with_one_journal_line(bar):
    bootstrap.install_menu()
    before = bar.menu_actions()
    bar.remove()
    bar.held.clear()
    assert bar.menu_actions() is None
    bootstrap.guard_tick()
    assert bar.menu_actions() == before
    assert bar.journal.count(RESTORED) == 1
    bootstrap.guard_tick()                             # it is there now: nothing more to say
    bootstrap.guard_tick()
    assert bar.journal.count(RESTORED) == 1
    assert bar.menu_actions() == before


def test_repeated_forced_installs_add_no_entry(bar):
    bootstrap.install_menu()
    before = bar.menu_actions()
    for _ in range(3):
        bootstrap.install_menu(force=True)
    assert bar.menu_actions() == before
    assert before.count("-") == 2
    assert before[-1] == "About"


def test_a_forced_install_keeps_the_menu_order(bar):
    bootstrap.install_menu()
    tools = [entry["menu"] for entry in config.TOOLS if entry["menu"] in bar.menu_actions()]
    bootstrap.install_menu(force=True)
    shown = bar.menu_actions()
    assert shown[:len(tools)] == tools
    assert shown[len(tools)] == "-"


def test_the_guard_timer_is_started_once_and_not_stacked(bar, monkeypatch):
    bootstrap.install_menu()
    bar.qapp.processEvents()                           # the zero-delay first tick
    timer = getattr(builtins, bootstrap._GUARD_FLAG)
    assert isinstance(timer, QtCore.QTimer) and timer.isActive() and timer.interval() == bootstrap.GUARD_MS
    bootstrap.install_menu(force=True)
    assert bootstrap.start_guard() is False
    monkeypatch.setattr(bootstrap, "purge_modules", lambda: None)
    assert bootstrap.reload_all() is True
    bar.qapp.processEvents()
    assert getattr(builtins, bootstrap._GUARD_FLAG) is timer
    ours = [t for t in bar.window.findChildren(QtCore.QTimer) if t.isActive() and t.interval() == bootstrap.GUARD_MS]
    assert ours == [timer]


def test_the_first_tick_runs_after_the_startup_scripts(bar):
    bootstrap.install_menu()
    new = bar.rebuild()                                # another launcher's addMenuItem, later in the same startup
    bar.qapp.processEvents()
    assert msapi.is_valid(_pinned())
    assert _pinned() is new


def test_the_logo_is_rebuilt_when_its_menu_is_gone(bar):
    bootstrap.install_menu()
    old_button = _corner(bar)
    assert old_button is not None
    old_button.setMenu(None)                           # the button that lost its menu is dead
    bootstrap.guard_tick()
    button = _corner(bar)
    assert button is not None and button.menu() is _pinned()
    assert button.objectName() == msapi.LOGO_BUTTON


def test_the_logo_follows_a_replaced_menu(bar):
    bootstrap.install_menu()
    new = bar.rebuild()
    bootstrap.guard_tick()
    assert _corner(bar).menu() is new


def test_the_guard_never_raises(bar, monkeypatch):
    bootstrap.install_menu()
    monkeypatch.setattr(msapi, "find_menu", lambda title: 1 / 0)
    assert bootstrap.guard_tick() is False


def test_a_menu_that_keeps_leaving_is_restored_a_few_times_only(bar):
    bootstrap.install_menu()
    for _ in range(bootstrap.GUARD_MAX_RESTORES + 3):
        bar.remove()
        bootstrap.guard_tick()
    assert bar.journal.count(RESTORED) == bootstrap.GUARD_MAX_RESTORES


def test_a_stale_pin_is_dropped_when_the_menu_is_gone(bar):
    bootstrap.install_menu()
    bar.remove()
    assert msapi.find_menu("Aero") is None
    assert _pinned() is None
