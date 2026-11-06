"""The panel table follows Metashape: reread at regular intervals, less often if the read is slow."""

from aerotools.tools.fastgcp import tool


class _Panel(object):
    def __init__(self, cost=0.0):
        self.calls = 0
        self.poll_cost = cost

    def poll_rows(self):
        self.calls += 1


class _State(object):
    def __init__(self, panel):
        self.panel = panel


def test_the_table_is_compared_every_few_ticks():
    state = _State(_Panel())
    for _ in range(tool.POLL_TICKS * 3):
        tool._poll_table(state)
    assert state.panel.calls == 3


def test_a_slow_read_is_spaced_out():
    state = _State(_Panel(cost=tool.SLOW_POLL_S * 2))
    for _ in range(tool.POLL_TICKS * 5):
        tool._poll_table(state)
    assert state.panel.calls == 1


def test_a_panel_without_polling_is_left_alone():
    state = _State(object())
    tool._poll_table(state)
