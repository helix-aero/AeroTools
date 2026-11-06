import pytest

from aerotools import journal


@pytest.fixture(autouse=True)
def _clean():
    journal.clear()
    saved = list(journal._subscribers)
    del journal._subscribers[:]
    yield
    journal.clear()
    journal._subscribers[:] = saved


def test_circular_buffer():
    for i in range(journal.MAX_LINES + 25):
        journal.write("ligne {}".format(i))
    lines = journal.lines()
    assert len(lines) == journal.MAX_LINES
    assert lines[-1].endswith("ligne {}".format(journal.MAX_LINES + 24))
    assert lines[0].endswith("ligne 25")


def test_lines_returns_a_copy():
    journal.write("a")
    journal.lines().append("intrus")
    assert len(journal.lines()) == 1


def test_subscribe_is_idempotent_and_receives():
    got = []
    journal.subscribe(got.append)
    journal.subscribe(got.append)
    journal.write("bonjour")
    assert len(got) == 1 and got[0].endswith("bonjour")
    journal.unsubscribe(got.append)
    journal.write("encore")
    assert len(got) == 1


def test_failing_subscriber_is_dropped_others_still_called():
    good = []

    def bad(_line):
        raise RuntimeError("panneau detruit")

    journal.subscribe(bad)
    journal.subscribe(good.append)
    journal.write("un")
    assert bad not in journal._subscribers
    assert len(good) == 1
    journal.write("deux")
    assert len(good) == 2
    assert len(journal.lines()) == 2


def test_lines_are_also_written_to_a_log_file_that_rotates(tmp_path, monkeypatch):
    path = tmp_path / "aerotools.log"
    monkeypatch.setattr(journal, "log_path", lambda: str(path))
    journal.write("first line")
    assert "first line" in path.read_text(encoding="utf-8")
    monkeypatch.setattr(journal, "LOG_MAX_BYTES", 10)
    journal.write("second line")
    assert (tmp_path / "aerotools.log.old").exists() and "second line" in path.read_text(encoding="utf-8")
