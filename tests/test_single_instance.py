"""The Aero menu opens Aero-GCP once: a second click only brings the open panel to the front."""

from aerotools import bootstrap, msapi
from aerotools.tools.fastgcp import session as session_mod, tool


class _Panel(object):
    def __init__(self, alive=True):
        self.alive = alive
        self.shown = 0
        self.raised = 0

    def objectName(self):
        if not self.alive:
            raise RuntimeError("Internal C++ object already deleted.")
        return session_mod.DOCK_NAME

    def show(self):
        self.shown += 1

    def raise_(self):
        self.raised += 1


class _Window(object):
    def findChildren(self, *_args):
        raise AssertionError("an open panel must not trigger a search for another one")


def test_a_second_launch_brings_the_open_panel_forward(monkeypatch):
    state = session_mod.current()
    panel = _Panel()
    monkeypatch.setattr(state, "panel", panel, raising=False)
    monkeypatch.setattr(msapi, "main_window", lambda: _Window())
    monkeypatch.setattr(tool, "uninstall", lambda: (_ for _ in ()).throw(AssertionError("closed and rebuilt")))

    tool.install()
    tool.install()

    assert state.panel is panel
    assert (panel.shown, panel.raised) == (2, 2)


def test_a_deleted_panel_does_not_count_as_open():
    assert tool._alive(_Panel())
    assert not tool._alive(_Panel(alive=False))
    assert not tool._alive(None)


def test_a_panel_closed_by_the_user_is_not_reopened_by_a_reload(monkeypatch):
    import builtins
    monkeypatch.setattr(builtins, bootstrap._LAST_TOOL_FLAG, "fastgcp", raising=False)

    bootstrap.forget_last("anothertool")
    assert getattr(builtins, bootstrap._LAST_TOOL_FLAG) == "fastgcp"

    bootstrap.forget_last("fastgcp")
    assert getattr(builtins, bootstrap._LAST_TOOL_FLAG) is None
