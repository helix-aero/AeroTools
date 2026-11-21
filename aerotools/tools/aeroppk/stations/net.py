"""Polite HTTP for public GNSS archives: identified User-Agent, timeouts, retries, partial reads, progress, cancel.

No account, no cookie, no credential is ever sent. Only anonymous public servers are used (RGP data server).
"""

import http.client
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .... import __version__

USER_AGENT = "AeroTools-AeroPPK/%s (+https://github.com/helix-aero/AeroTools)" % __version__
TIMEOUT = 30
PROBE_TIMEOUT = 10                    # a HEAD or a 16 KB range read: a server that needs longer is not answering
CHUNK = 64 * 1024
BREAKER_FAILURES = 2                  # consecutive failures with no answer (timeout, refused, cut) after which a host is skipped...
BREAKER_PAUSE = 90.0                  # ...for this many seconds
ABSENT = (404, 410)                   # the file does not exist
NOT_FOR_US = (401, 403)               # a bucket that refuses what it does not have answers like this: also "not there"
WIRE_ERRORS = (urllib.error.URLError, OSError, http.client.HTTPException)


class NetError(Exception):
    """Network failure, with `status` set to the HTTP code when the server answered (404 = file does not exist)."""

    def __init__(self, message, status=None):
        super(NetError, self).__init__(message)
        self.status = status


class _Breaker(object):
    """Stops asking a host that does not answer: an offline machine must fail in seconds, not in one timeout per file.

    Only failures with no HTTP answer count (timeouts, refused connections, cut answers); a 404 or a 503 is an answer. Shared by all the
    threads of the run. A success resets the count; after `BREAKER_PAUSE` seconds one request is let through again."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._state = {}                       # host -> [consecutive failures, time it opened or None]

    def check(self, url):
        host = urllib.parse.urlsplit(url).netloc
        with self._lock:
            failures, opened = self._state.get(host, (0, None))
            if opened is not None:
                if self._clock() - opened < BREAKER_PAUSE:
                    raise NetError("%s is not answering (skipped for a moment)" % host)
                self._state[host] = [BREAKER_FAILURES - 1, None]          # let one request through: one more failure closes it again

    def success(self, url):
        with self._lock:
            self._state.pop(urllib.parse.urlsplit(url).netloc, None)

    def failure(self, url):
        host = urllib.parse.urlsplit(url).netloc
        with self._lock:
            state = self._state.setdefault(host, [0, None])
            state[0] += 1
            if state[0] >= BREAKER_FAILURES and state[1] is None:
                state[1] = self._clock()

    def reset(self):
        with self._lock:
            self._state.clear()


BREAKER = _Breaker()


def reset_breaker():
    BREAKER.reset()


def _guard(url, call):
    """`call()` with the breaker around it: a host that stays silent is dropped, an answer of any kind keeps it."""
    BREAKER.check(url)
    try:
        value = call()
    except urllib.error.HTTPError:
        BREAKER.success(url)
        raise
    except WIRE_ERRORS:
        BREAKER.failure(url)
        raise
    BREAKER.success(url)
    return value


def _open(url, headers=None, timeout=TIMEOUT, opener=None):
    request = urllib.request.Request(url, headers=dict({"User-Agent": USER_AGENT}, **(headers or {})))
    return (opener or urllib.request.urlopen)(request, timeout=timeout)


def exists(url, timeout=PROBE_TIMEOUT, opener=None):
    """(True, size or None) when the URL answers 200, (False, None) when 404 / 410 (or a bucket refusing a file it does not have: 401 / 403).

    Any other failure raises NetError (`status` = the HTTP code, None when the server did not answer): a timeout or a 503 does not say that
    the file is missing."""
    def ask():
        request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
        with (opener or urllib.request.urlopen)(request, timeout=timeout) as answer:
            size = answer.headers.get("Content-Length")
            return True, int(size) if size and size.isdigit() else None
    try:
        return _guard(url, ask)
    except urllib.error.HTTPError as exc:
        if exc.code in ABSENT or exc.code in NOT_FOR_US:
            return False, None
        raise NetError("%s answered HTTP %s" % (url, exc.code), exc.code)
    except WIRE_ERRORS as exc:
        raise NetError("cannot reach %s: %s" % (url, exc))


def read_range(url, length=16384, timeout=PROBE_TIMEOUT, opener=None):
    """First `length` bytes of a file (HTTP Range). Servers that ignore Range send everything: we stop after `length`."""
    def ask():
        with _open(url, {"Range": "bytes=0-%d" % (length - 1)}, timeout, opener) as answer:
            return answer.read(length)
    try:
        return _guard(url, ask)
    except urllib.error.HTTPError as exc:
        raise NetError("%s answered HTTP %s" % (url, exc.code), exc.code)
    except WIRE_ERRORS as exc:
        raise NetError("cannot reach %s: %s" % (url, exc))


def download(url, target, progress=None, cancelled=None, retries=2, timeout=TIMEOUT, opener=None, validate=None):
    """Download `url` to `target` (written to a temporary name first). Returns the byte count.

    `validate(path)` is called on the finished temporary file and returns None when it is good or a sentence when it is not (a captive
    portal or an error page served with HTTP 200): the file is then deleted and NetError raised, without retry."""
    folder = os.path.dirname(target)
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp = target + ".part"
    last = None
    for attempt in range(retries + 1):
        try:
            done = _fetch(url, tmp, progress, cancelled, timeout, opener, os.path.basename(target))
            if validate is not None:
                why = validate(tmp)
                if why:
                    raise NetError("%s: %s" % (os.path.basename(target), why), 200)
            os.replace(tmp, target)
            return done
        except urllib.error.HTTPError as exc:
            last = NetError("%s answered HTTP %s" % (url, exc.code), exc.code)
            if exc.code in ABSENT or exc.code in NOT_FOR_US:
                break
        except NetError as exc:
            last = exc
            if str(exc).startswith("Download stopped") or exc.status == 200 or "not answering" in str(exc):
                break
        except WIRE_ERRORS as exc:
            last = NetError("cannot reach %s: %s" % (url, exc))
        time.sleep(1.0 + attempt)
    try:
        if os.path.exists(tmp):
            os.remove(tmp)
    except OSError:
        pass
    raise last if last else NetError("download failed")


def _fetch(url, tmp, progress, cancelled, timeout, opener, name):
    """One attempt. Only the connection counts for the circuit breaker: a body cut half way is retried like any other failure."""
    answer = _guard(url, lambda: _open(url, None, timeout, opener))
    with answer:
        total = answer.headers.get("Content-Length")
        total = int(total) if total and total.isdigit() else 0
        done = 0
        with open(tmp, "wb") as handle:
            while True:
                if cancelled is not None and cancelled():
                    raise NetError("Download stopped.")
                block = answer.read(CHUNK)
                if not block:
                    break
                handle.write(block)
                done += len(block)
                if progress is not None:
                    progress(done, total)
    if total and done != total:
        raise NetError("incomplete download of %s" % name)
    return done
