"""Startup news (aerotools/news.py): parsing and filters, the seen ids, the Notifications setting, a silent failure, the address, the menu."""

import builtins
import json
import os
import sys
import time

import pytest

from aerotools import bootstrap, config, journal, news, updater

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def doc(*items):
    return json.dumps({"items": list(items)})


def item(item_id, date="2026-11-14", title="Title", **more):
    return dict({"id": item_id, "date": date, "title": title}, **more)


# -- parsing ---------------------------------------------------------------------------------------

def test_a_good_file_gives_its_items_newest_first():
    raw = doc(item("a", "2026-01-01"), item("c", "2026-11-14", body="Text", url="https://helix.aero"), item("b", "2026-05-05"))
    items = news.parse(raw, "main", "0.5.0")
    assert [i["id"] for i in items] == ["c", "b", "a"]
    assert items[0]["body"] == "Text" and items[0]["url"] == "https://helix.aero" and items[1]["url"] == ""


@pytest.mark.parametrize("raw", [b"", b"   ", b"not json", b"[]", b"{}", b'{"items": 3}', b'{"items": null}', b"\xff\xfe\x00", None, 12])
def test_a_bad_or_empty_file_gives_nothing_and_never_raises(raw):
    assert news.parse(raw, "main", "0.5.0") == []


def test_a_file_with_a_bom_is_read():
    assert len(news.parse(b"\xef\xbb\xbf" + doc(item("a")).encode(), "main", "0.5.0")) == 1


def test_bad_items_are_skipped_and_the_good_ones_kept():
    raw = doc(item("ok"), "text", 3, {"id": "x"}, {"title": "no id"}, item("ok"), item("", title="empty id"), {"id": "n", "title": 5})
    assert [i["id"] for i in news.parse(raw, "main", "0.5.0")] == ["ok"]


def test_the_channel_filter():
    raw = doc(item("all"), item("main-only", channels=["main"]), item("dev-only", channels=["dev"]), item("both", channels=["main", "dev"]),
              item("any", channels=[]))
    assert sorted(i["id"] for i in news.parse(raw, "main", "0.5.0")) == ["all", "any", "both", "main-only"]
    assert sorted(i["id"] for i in news.parse(raw, "dev", "0.5.0")) == ["all", "any", "both", "dev-only"]


def test_the_version_filter_includes_both_ends():
    raw = doc(item("old", max_version="0.4.9"), item("from", min_version="0.5.0"), item("until", max_version="0.5.0"),
              item("future", min_version="0.6"), item("window", min_version="0.4.1", max_version="0.5.9"), item("blank", min_version=""))
    assert sorted(i["id"] for i in news.parse(raw, "main", "0.5.0")) == ["blank", "from", "until", "window"]
    assert sorted(i["id"] for i in news.parse(raw, "main", "0.10.0")) == ["blank", "from", "future"]      # 0.10 is after 0.9, not before 0.5


def test_a_link_that_is_not_https_is_dropped_and_texts_are_cut():
    raw = doc(item("a", url="http://x.test"), item("b", url="javascript:alert(1)"), item("c", title="T" * 300, body="B" * 900))
    by_id = {i["id"]: i for i in news.parse(raw, "main", "0.5.0")}
    assert by_id["a"]["url"] == "" and by_id["b"]["url"] == ""
    assert len(by_id["c"]["title"]) == news.MAX_TITLE and len(by_id["c"]["body"]) == news.MAX_BODY


def test_the_example_file_of_the_repository_is_valid_and_shows_nothing():
    with open(os.path.join(ROOT, "news.json"), "rb") as handle:
        raw = handle.read()
    assert json.loads(raw)["items"]
    assert news.parse(raw, "main", "0.5.0") == [] and news.parse(raw, "dev", "9.9.9") == []


# -- seen ids and the setting ------------------------------------------------------------------------

def test_seen_ids_and_the_unseen_list():
    items = news.parse(doc(item("a"), item("b", "2026-01-01")), "main", "0.5.0")
    assert news.seen() == [] and [i["id"] for i in news.unseen(items)] == ["a", "b"]
    assert news.mark_seen(["a"]) is True
    assert [i["id"] for i in news.unseen(items)] == ["b"]
    news.mark_seen(["a", "b"])
    assert news.seen() == ["a", "b"] and news.unseen(items) == []


def test_the_seen_list_is_bounded_and_survives_a_bad_setting():
    news.mark_seen(["id%d" % n for n in range(news.SEEN_KEEP + 20)])
    assert len(news.seen()) == news.SEEN_KEEP and news.seen()[-1] == "id%d" % (news.SEEN_KEEP + 19)
    config.put(news.SECTION, "seen", "garbage")
    assert news.seen() == []


def test_notifications_are_on_by_default_and_the_setting_is_kept():
    assert news.enabled() is True
    assert news.set_enabled(False) is True and news.enabled() is False
    assert config.get(news.SECTION, "enabled") is False
    news.set_enabled(True)
    assert news.enabled() is True


# -- fetch: fresh, short, silent -----------------------------------------------------------------------

def test_the_address_is_next_to_the_manifest_and_follows_the_channel(monkeypatch):
    plugin = updater.plugins()[0]
    monkeypatch.setattr(updater, "channel", lambda: "dev")
    assert news.url(plugin) == updater.file_url(plugin, "manifest.json").replace("manifest.json", "news.json")
    assert news.url(plugin).endswith("/dev/news.json")
    monkeypatch.setattr(updater, "channel", lambda: "main")
    assert news.url(plugin).endswith("/main/news.json")
    config.put(updater.SECTION, "base_url", "file:///mirror/")
    assert news.url(plugin) == "file:///mirror/news.json"


def test_fetch_asks_fresh_with_a_short_timeout(monkeypatch):
    asked = []

    def opener(address, timeout):
        asked.append((address, timeout))
        return doc(item("a")).encode()

    monkeypatch.setattr(news, "_open", opener)
    assert [i["id"] for i in news.fetch()] == ["a"]
    address, timeout = asked[0]
    assert address.startswith("https://") and "/news.json?t=" in address                  # cache-busting query
    assert timeout == news.TIMEOUT == 5
    assert updater.NO_CACHE["Cache-Control"] == "no-cache"


def test_a_failed_fetch_is_silent_and_leaves_one_journal_line(monkeypatch):
    lines = []
    monkeypatch.setattr(journal, "write", lambda message, tag="AeroTools": lines.append(message))
    for failure in (OSError("offline"), TimeoutError("slow"), ValueError("x")):
        def broken(address, timeout, failure=failure):
            raise failure
        monkeypatch.setattr(news, "_open", broken)
        assert news.fetch() is None
    assert len(lines) == 3 and all(line.startswith("news not loaded") for line in lines)
    monkeypatch.setattr(news, "_open", lambda address, timeout: b"<html>not json</html>")
    assert news.fetch() == []                                                           # bad content = no news, not an error


def test_an_address_that_is_not_https_is_refused(monkeypatch):
    config.put(updater.SECTION, "base_url", "http://example.test/x")
    monkeypatch.setattr(news, "_open", lambda address, timeout: doc(item("a")).encode())
    assert news.fetch() is None


def test_the_news_file_is_not_in_the_manifest():
    sys.path.insert(0, os.path.join(ROOT, "install"))
    try:
        import make_manifest
    finally:
        sys.path.pop(0)
    assert "news.json" not in make_manifest.listing()
    with open(os.path.join(ROOT, "manifest.json"), encoding="utf-8") as handle:
        assert "news.json" not in json.load(handle)["files"]
    assert news.NEWS_FILE != updater.MANIFEST


# -- showing ---------------------------------------------------------------------------------------------

@pytest.fixture
def shown(monkeypatch):
    """The popups that would open (the window itself is checked in tests_qt/run_qt_news_about.py with real Qt)."""
    calls = []
    monkeypatch.setattr(news, "_open_dialog", lambda items, on_demand: calls.append((items, on_demand)) or True)
    return calls


def test_notifications_off_means_no_check_and_no_popup(shown, monkeypatch):
    started = []
    monkeypatch.setattr(news, "_run_fetch", lambda *a, **k: started.append(1) or True)
    news.set_enabled(False)
    assert news.check_startup() is False and started == []
    items = news.parse(doc(item("a")), "main", "0.5.0")
    assert news._show_unseen(items) is False and shown == []                            # also when it was turned off while fetching


def test_the_startup_popup_lists_only_the_unseen_items(shown):
    items = news.parse(doc(item("a"), item("b", "2026-01-01")), "main", "0.5.0")
    news.mark_seen(["a"])
    assert news._show_unseen(items) is True
    assert [i["id"] for i in shown[0][0]] == ["b"] and shown[0][1] is False
    news.mark_seen(["b"])
    assert news._show_unseen(items) is False and len(shown) == 1
    assert news._show_unseen(None) is False and news._show_unseen([]) is False


def test_startup_is_planned_once_per_session(monkeypatch):
    if hasattr(builtins, "_AEROTOOLS_NEWS_STARTED"):
        delattr(builtins, "_AEROTOOLS_NEWS_STARTED")
    planned = []
    import aerotools.qt as qt
    monkeypatch.setattr(qt.QtCore, "QTimer", type("T", (), {"singleShot": staticmethod(lambda ms, fn: planned.append((ms, fn)))}), raising=False)
    try:
        assert news.schedule_startup() is True and news.schedule_startup() is False
        assert planned[0][0] == news.STARTUP_DELAY_MS >= 3000                           # a few seconds after the launch
    finally:
        delattr(builtins, "_AEROTOOLS_NEWS_STARTED")


def test_the_worker_result_reaches_the_window_from_the_qt_thread(monkeypatch):
    """fetch runs in a Worker thread; the callback is polled from a timer (here driven by hand) and gets the list, or None."""
    import aerotools.qt as qt
    pending = []
    monkeypatch.setattr(qt.QtCore, "QTimer", type("T", (), {"singleShot": staticmethod(lambda ms, fn: pending.append(fn))}), raising=False)
    monkeypatch.setattr(news, "fetch", lambda: [{"id": "a"}])
    got = []
    assert news._run_fetch(got.append) is True
    for _ in range(300):
        if got:
            break
        if pending:
            pending.pop(0)()
        time.sleep(0.01)
    assert got == [[{"id": "a"}]]


def test_a_window_that_fails_is_a_journal_line_not_an_error(monkeypatch):
    lines = []
    monkeypatch.setattr(journal, "write", lambda message, tag="AeroTools": lines.append(message))
    news._guarded(lambda items: 1 / 0, [])
    assert lines and lines[0].startswith("news window failed")


# -- the menu ----------------------------------------------------------------------------------------------

def test_the_news_entry_is_in_the_maintenance_group_before_about(ms, clean_bootstrap_flags):
    assert bootstrap.install_menu() is True
    labels = [label for kind, label in ms.app.menu_order if kind == "item"]
    assert labels.index("Aero/News") == labels.index("Aero/Uninstall Aero...") + 1 == labels.index("Aero/About") - 1


def test_the_menu_entry_shows_the_news_even_when_notifications_are_off(monkeypatch):
    news.set_enabled(False)
    calls = []
    monkeypatch.setattr(news, "_run_fetch", lambda done, always=False: calls.append(always) or True)
    bootstrap.show_news()
    assert calls == [True]
