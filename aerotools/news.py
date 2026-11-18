"""News: a short note shown at Metashape startup, published without a plugin release.

Source
------
`news.json`, next to `manifest.json` in the public repository, one per channel (the same base address as the updater: dev and main can carry
different news). The file is NOT listed in the manifest: editing it never triggers an update and needs no manifest, and it is fetched fresh at
every start (no-cache headers and a cache-busting query on https). Format: docs/NEWS.md.

    {"items": [{"id": "2026-11-14-050", "date": "2026-11-14", "title": "...", "body": "...", "url": "https://...",
                "channels": ["main", "dev"], "min_version": "0.5.0", "max_version": "0.5.9"}]}

Behaviour
---------
Startup (`schedule_startup`, called by the bootstrap once the menu is installed): a few seconds after the launch, when Metashape is idle,
the file is fetched in a worker thread (short timeout), and the popup opens only if there are items not seen yet and the "Notifications"
setting is on. Offline, a bad file or no item means nothing at all (one journal line at most): nothing ever blocks or slows the startup.
`Aero > News` shows the current items on demand, seen or not, even when the notifications are off.

Settings (section `news` of settings.json): `enabled` (default on; the Install plugins window and the popup's checkbox set it) and `seen` (ids
of the items already shown, newest last, kept to the last SEEN_KEEP).
"""

import builtins
import json
import time
import urllib.parse
import urllib.request

from . import config, journal, updater, workers

SECTION = "news"
NEWS_FILE = "news.json"
TIMEOUT = 5                                  # seconds: the popup is a courtesy, never worth waiting for
STARTUP_DELAY_MS = 8000                      # after the menu is installed: Metashape is idle by then
POLL_MS = 250
POLL_MAX_MS = 20000                          # a fetch that is not back by then is dropped
MAX_SHOWN = 3                                # items in the popup (no scroll bar); the rest is behind the "More news" link
MAX_ITEMS = 50                               # items read from the file
MAX_TITLE = 100
MAX_BODY = 400
SEEN_KEEP = 200
MORE_URL = "https://docs.helix.aero"         # "More news" in the popup
USER_AGENT = "AeroTools-news"
_STARTED_FLAG = "_AEROTOOLS_NEWS_STARTED"    # on builtins: reload_all and install_menu(force) never schedule a second check
_CURRENT = "_AEROTOOLS_NEWS_DIALOG"          # the open popup (a window without a Qt parent would be collected)


# --------------------------------------------------------------------------
# Reading the file
# --------------------------------------------------------------------------

def url(plugin=None):
    """Address of news.json: the base of the manifest of the followed channel."""
    plugin = plugin or updater.plugins()[0]
    return updater.base_url(plugin) + "/" + NEWS_FILE


def _text(value, limit):
    return value.strip()[:limit] if isinstance(value, str) else ""


def _link(value):
    """A link is https only (a news file must not open anything else on the workstation)."""
    value = value.strip() if isinstance(value, str) else ""
    return value if urllib.parse.urlsplit(value).scheme == "https" and len(value) <= 500 else ""


def _in_range(version, low, high):
    here = updater._version_tuple(version)
    if isinstance(low, str) and low.strip() and here < updater._version_tuple(low):
        return False
    if isinstance(high, str) and high.strip() and here > updater._version_tuple(high):
        return False
    return True


def parse(raw, channel=None, version=None):
    """The items of a news.json for this channel and this version, newest first. Never raises: an unreadable file or a bad item gives
    nothing (the good items of a file with one bad item are kept). `raw` is bytes or text."""
    channel = channel if channel is not None else updater.channel()
    if version is None:
        from . import __version__ as version
    try:
        data = json.loads(raw.decode("utf-8-sig") if isinstance(raw, (bytes, bytearray)) else raw)
        entries = data["items"]
        if not isinstance(entries, list):
            return []
    except Exception:
        return []
    items, ids = [], set()
    for entry in entries[:MAX_ITEMS]:
        if not isinstance(entry, dict):
            continue
        item_id, title = _text(entry.get("id"), 80), _text(entry.get("title"), MAX_TITLE)
        if not item_id or not title or item_id in ids:
            continue
        channels = entry.get("channels")
        if isinstance(channels, list) and channels and channel not in channels:
            continue
        if not _in_range(version, entry.get("min_version"), entry.get("max_version")):
            continue
        ids.add(item_id)
        items.append({"id": item_id, "date": _text(entry.get("date"), 10), "title": title, "body": _text(entry.get("body"), MAX_BODY),
                      "url": _link(entry.get("url"))})
    items.sort(key=lambda item: (item["date"], item["id"]), reverse=True)
    return items


def _open(address, timeout):
    """Bytes of an address, asked fresh. One attempt only (the updater retries; the news does not). Replaced in the tests."""
    request = urllib.request.Request(address, headers=dict(updater.NO_CACHE, **{"User-Agent": USER_AGENT}))
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(1 << 20)


def fetch(plugin=None, timeout=TIMEOUT):
    """The current items (a list, possibly empty), or None when the file could not be fetched. Never raises, never shows anything."""
    try:
        address = url(plugin)
        updater._check_scheme(address)
        if urllib.parse.urlsplit(address).scheme == "https":
            address += "?t={}".format(int(time.time()))            # a cache in between must not hold a new item back
        return parse(_open(address, timeout))
    except Exception as exc:
        journal.write("news not loaded: {!r}".format(exc))
        return None


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

def enabled():
    return config.get(SECTION, "enabled", True) is not False


def set_enabled(value):
    return config.put(SECTION, "enabled", bool(value))


def seen():
    value = config.get(SECTION, "seen", [])
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def mark_seen(ids):
    """Remember that these items were shown. Returns True if the write succeeded."""
    known = seen()
    for item_id in ids:
        if item_id not in known:
            known.append(item_id)
    return config.put(SECTION, "seen", known[-SEEN_KEEP:])


def unseen(items):
    done = set(seen())
    return [item for item in items if item["id"] not in done]


# --------------------------------------------------------------------------
# Showing
# --------------------------------------------------------------------------

def _window():
    try:
        from . import msapi
        return msapi.main_window()
    except Exception:
        return None


def schedule_startup(delay_ms=STARTUP_DELAY_MS):
    """Plans the startup check, once per Metashape session. Returns True if it was planned now."""
    if getattr(builtins, _STARTED_FLAG, False):
        return False
    setattr(builtins, _STARTED_FLAG, True)
    try:
        from .qt import QtCore
        QtCore.QTimer.singleShot(delay_ms, _startup_slot)
        return True
    except Exception as exc:
        journal.write("news check not planned: {!r}".format(exc))
        return False


def _startup_slot():
    """What the timer calls. Looks the module up again: after `reload_all()` the new code runs, not the purged one."""
    try:
        import importlib
        importlib.import_module("aerotools.news").check_startup()
    except Exception as exc:
        journal.write("news check failed: {!r}".format(exc))


def check_startup():
    """Fetch in the background and show the unseen items. Returns True if a fetch was started."""
    if not enabled():
        return False
    return _run_fetch(lambda items: _show_unseen(items))


def show_now():
    """Aero > News: the current items on demand, seen or not, even when the notifications are off."""
    return _run_fetch(lambda items: _open_dialog(items, on_demand=True), always=True)


def _run_fetch(done, always=False):
    """Starts the worker thread and polls it from the Qt thread (the window is only ever touched there)."""
    try:
        from .qt import QtCore
        worker = workers.Worker(lambda _w: fetch())
        worker.start()
        deadline = [POLL_MAX_MS]

        def poll():
            if not worker.finished:
                deadline[0] -= POLL_MS
                if deadline[0] <= 0:
                    worker.abandon()
                    if always:
                        _guarded(done, None)
                    return
                QtCore.QTimer.singleShot(POLL_MS, poll)
                return
            result = worker.result
            _guarded(done, result if isinstance(result, list) else None)

        QtCore.QTimer.singleShot(POLL_MS, poll)
        return True
    except Exception as exc:
        journal.write("news not started: {!r}".format(exc))
        return False


def _guarded(done, items):
    try:
        done(items)
    except Exception as exc:
        journal.write("news window failed: {!r}".format(exc))


def _show_unseen(items):
    """Startup: only what was not shown yet, and only if the notifications are still on when the answer comes back."""
    if not items or not enabled():
        return False
    fresh = unseen(items)
    if not fresh:
        return False
    return _open_dialog(fresh, on_demand=False)


def _open_dialog(items, on_demand):
    from . import news_dialog
    old = getattr(builtins, _CURRENT, None)
    if old is not None:
        try:
            old.close()
        except Exception:
            pass
    dialog = news_dialog.NewsDialog(items, on_demand=on_demand, parent=_window())
    setattr(builtins, _CURRENT, dialog)
    dialog.show()
    return True

