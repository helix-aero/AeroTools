"""Aero-PPK, fixes of the audit: merged RINEX, the download cache and the network layer, the frame declared when positions are written,
the antenna of an own base, the time system of a RINEX file, the leap seconds of a report, and the missing tests of the risky paths.

No network (fake openers), no Metashape (a fake chunk), no RTKLIB."""
import datetime
import gzip
import http.client
import io
import os
import threading
import types
import urllib.error

import pytest

from aerotools.tools.aeroppk import antex, frames, gnsstime, ownbase, pipeline, report, rinex, rinexprep, rtklib, runner, unpack
from aerotools.tools.aeroppk.stations import catalog, net, provider, station

import aeroppk_lzw
import test_aeroppk_engine_profiles as engine
import test_aeroppk_wizard_logic as wl

REAL_PRUNE = provider.prune_cache
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data", "aeroppk_nets")
EARLY = os.path.join(DATA, "aubg_0845_excerpt.rnx")
LATE = os.path.join(DATA, "aubg_0900_excerpt.rnx")


@pytest.fixture(autouse=True)
def quiet_network(monkeypatch):
    """No sleep between retries, a fresh circuit breaker, no housekeeping of the real cache."""
    monkeypatch.setattr(net.time, "sleep", lambda s: None)
    monkeypatch.setattr(provider.time, "sleep", lambda s: None)
    monkeypatch.setattr(provider, "prune_cache", lambda *a, **k: 0)
    net.reset_breaker()
    yield
    net.reset_breaker()


# ---------------------------------------------------------------------------------------------------------------------
# 1. A merged RINEX file knows where it really ends
# ---------------------------------------------------------------------------------------------------------------------

def with_last_obs(src, dst):
    """A copy of a RINEX file whose header says where it ends (what Trimble, Leica and teqc files do)."""
    last = rinex.last_epoch(src, rinex.read_obs_header(src))
    line = ("%6d%6d%6d%6d%6d%13.7f     GPS         TIME OF LAST OBS\n" % (last.year, last.month, last.day, last.hour, last.minute,
                                                                           last.second)).encode()
    with open(src, "rb") as handle:
        raw = handle.read()
    at = raw.index(b"END OF HEADER")
    start = raw.rfind(b"\n", 0, at) + 1
    with open(dst, "wb") as out:
        out.write(raw[:start] + line + raw[start:])
    return str(dst), last


def test_a_merged_file_ends_where_its_last_piece_ends(tmp_path):
    first, first_last = with_last_obs(EARLY, tmp_path / "a.rnx")
    second, second_last = with_last_obs(LATE, tmp_path / "b.rnx")
    assert rinex.read_obs_info(first).last == first_last < second_last
    merged = rinexprep.merge_obs([first, second], str(tmp_path / "merged.rnx"))
    info = rinex.read_obs_info(merged)
    assert info.last == second_last and info.first == rinex.read_obs_header(first).first          # not the end of the first piece
    assert not [l for l in rinexprep._read_header(merged) if l[60:].strip() == b"TIME OF LAST OBS"]


def test_a_merge_of_pieces_that_do_not_agree_on_the_interval_drops_it(tmp_path):
    def interval(src, dst, seconds):
        raw = open(src, "rb").read()
        at = raw.index(b"END OF HEADER")
        start = raw.rfind(b"\n", 0, at) + 1
        open(dst, "wb").write(raw[:start] + ("%10.3f".ljust(60) % seconds).encode() + b"INTERVAL\n" + raw[start:])
        return str(dst)
    one, other = interval(EARLY, tmp_path / "a.rnx", 1.0), interval(LATE, tmp_path / "b.rnx", 15.0)
    assert rinex.read_obs_header(rinexprep.merge_obs([one, one], str(tmp_path / "same.rnx"))).interval == 1.0
    assert rinex.read_obs_header(rinexprep.merge_obs([one, other], str(tmp_path / "mixed.rnx"))).interval is None


def test_the_merge_streams_the_pieces_and_keeps_the_bodies_in_order(tmp_path):
    header, body = rinexprep._split_v3(EARLY)
    assert list(rinexprep._iter_body(EARLY)) == body and rinexprep._read_header(EARLY) == header
    assert hasattr(rinexprep._iter_body(EARLY), "__next__")                                    # a generator: nothing is held in memory
    out = rinexprep.merge_obs([EARLY, EARLY, EARLY], str(tmp_path / "three.rnx"))
    assert open(out, "rb").read() == b"".join(header) + 3 * b"".join(body)
    types_ = rinexprep.types_v3(header)
    assert list(rinexprep._remap_lines(iter(body), types_, dict(types_))) == body
    other = rinexprep.types_v3(rinexprep._split_v3(LATE)[0])
    assert rinexprep._remap_body(list(body), types_, other) == list(rinexprep._remap_lines(iter(body), types_, other))


def base_text(first_hour, last_hour, name):
    return wl.OBS.replace("2030    01     1    10    00", "2030    01     1    %02d    00" % first_hour).replace(
        "2030    01     1    13    00", "2030    01     1    %02d    00" % last_hour)


def test_own_base_pieces_are_recognised_by_where_the_last_one_ends(tmp_path):
    """Two files of one station, the flight in the second one: it used to be refused as 'no overlap'."""
    a = wl.write(tmp_path, "a.obs", base_text(10, 11, "a") + "> 2030 01 01 10 59 59.0000000  0  0\n")
    b = wl.write(tmp_path, "b.obs", base_text(11, 13, "b") + "> 2030 01 01 12 59 59.0000000  0  0\n")
    flight = (datetime.datetime(2030, 1, 1, 12, 30), datetime.datetime(2030, 1, 1, 12, 40))
    nav = wl.write(tmp_path, "n.nav", wl.NAV)
    rec = ownbase.recognise([a, b, nav], str(tmp_path / "work"), None, flight, ["G"], False)
    assert rec.obs_info.last == datetime.datetime(2030, 1, 1, 12, 59, 59) and [i.key for i in rec.issues] == []


def test_a_file_without_first_epoch_does_not_stop_the_recognition(tmp_path):
    without = wl.OBS.replace("  2030    01     1    10    00    0.0000000     GPS         TIME OF FIRST OBS\n", "")
    a = wl.write(tmp_path, "a.obs", without)
    b = wl.write(tmp_path, "b.obs", base_text(11, 13, "b"))
    rec = ownbase.recognise([a, b, wl.write(tmp_path, "n.nav", wl.NAV)], str(tmp_path / "work"), None, wl.FLIGHT, ["G"], False)
    assert rec.names["obs"] == ["b.obs", "a.obs"]                                              # the one with a first epoch comes first


def test_two_compressed_files_of_the_same_name_do_not_overwrite_each_other(tmp_path):
    folders = []
    for name, src in (("one", EARLY), ("two", LATE)):
        folder = tmp_path / name
        folder.mkdir()
        with open(src, "rb") as raw, gzip.open(str(folder / "base.rnx.gz"), "wb") as packed:
            packed.write(raw.read())
        folders.append(str(folder / "base.rnx.gz"))
    nav = wl.write(tmp_path, "n.nav", wl.NAV)
    rec = ownbase.recognise(folders + [nav], str(tmp_path / "work"), None, None, ["G"], False)
    assert rec.obs is not None
    body = open(rec.obs, "rb").read()
    assert body.count(b"> 2026 11 04 08 45") == 1 and body.count(b"> 2026 11 04 09 00") == 1         # both pieces, each once


# ---------------------------------------------------------------------------------------------------------------------
# 2. The download cache
# ---------------------------------------------------------------------------------------------------------------------

GZ = b"\x1f\x8b\x08\x00" + b"x" * 60
PORTAL = b"<html><body>Please log in to the guest wifi</body></html>"


class Answer(object):
    def __init__(self, body, length=True, cut_at=None):
        self.io = io.BytesIO(body)
        self.headers = {"Content-Length": str(len(body))} if length is True else ({"Content-Length": str(length)} if length else {})
        self.cut_at = cut_at

    def read(self, size=-1):
        if self.cut_at is not None and self.io.tell() >= self.cut_at:
            raise http.client.IncompleteRead(b"", 10)
        return self.io.read(size if self.cut_at is None else min(size, self.cut_at - self.io.tell()))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Server(object):
    """A fake opener: `routes` maps a URL to what it answers, in order (the last answer repeats): bytes, an int (HTTP error), an exception,
    an Answer or a callable. Every request is recorded."""

    def __init__(self, routes):
        self.routes, self.calls, self.lock = dict((k, list(v) if isinstance(v, list) else [v]) for k, v in routes.items()), [], threading.Lock()

    def __call__(self, request, timeout=None):
        url = request.full_url
        with self.lock:
            self.calls.append((request.get_method(), url))
            queue = self.routes.get(url, [404])
            item = queue.pop(0) if len(queue) > 1 else queue[0]
        if callable(item) and not isinstance(item, Answer):
            item = item()
        if isinstance(item, int):
            raise urllib.error.HTTPError(url, item, "x", {}, None)
        if isinstance(item, BaseException):
            raise item
        return item if isinstance(item, Answer) else Answer(item)

    def count(self, method=None, url=None):
        return len([c for c in self.calls if (method is None or c[0] == method) and (url is None or c[1] == url)])


def test_download_retries_a_chunked_answer_that_is_cut(tmp_path):
    server = Server({"http://h/f.gz": [Answer(GZ, cut_at=10), Answer(GZ)]})
    target = str(tmp_path / "f.gz")
    assert net.download("http://h/f.gz", target, opener=server) == len(GZ)
    assert open(target, "rb").read() == GZ and server.count("GET") == 2 and not os.path.exists(target + ".part")


def test_download_gives_up_after_the_retries_with_a_net_error_and_no_part_file(tmp_path):
    server = Server({"http://h/f.gz": Answer(GZ, cut_at=10)})
    with pytest.raises(net.NetError):
        net.download("http://h/f.gz", str(tmp_path / "f.gz"), opener=server)
    assert server.count("GET") == 3 and os.listdir(str(tmp_path)) == []


def test_download_notices_a_body_shorter_than_announced(tmp_path):
    server = Server({"http://h/f.gz": [Answer(GZ, length=len(GZ) + 5), Answer(GZ)]})
    assert net.download("http://h/f.gz", str(tmp_path / "f.gz"), opener=server) == len(GZ) and server.count("GET") == 2
    bad = Server({"http://h/g.gz": Answer(GZ, length=len(GZ) + 5)})
    with pytest.raises(net.NetError, match="incomplete"):
        net.download("http://h/g.gz", str(tmp_path / "g.gz"), opener=bad)
    assert not os.path.exists(str(tmp_path / "g.gz"))


def test_download_stops_when_cancelled_and_does_not_retry(tmp_path):
    server = Server({"http://h/f.gz": GZ})
    with pytest.raises(net.NetError, match="stopped"):
        net.download("http://h/f.gz", str(tmp_path / "f.gz"), cancelled=lambda: True, opener=server)
    assert server.count("GET") == 1 and os.listdir(str(tmp_path)) == []


def test_download_does_not_retry_a_missing_file_but_retries_a_busy_server(tmp_path):
    missing = Server({"http://h/f.gz": 404})
    with pytest.raises(net.NetError) as caught:
        net.download("http://h/f.gz", str(tmp_path / "f.gz"), opener=missing)
    assert caught.value.status == 404 and missing.count() == 1
    busy = Server({"http://h/f.gz": [503, GZ]})
    assert net.download("http://h/f.gz", str(tmp_path / "f.gz"), opener=busy) == len(GZ) and busy.count() == 2


def test_download_refuses_what_the_validator_refuses_and_leaves_nothing(tmp_path):
    server = Server({"http://h/f.crx.gz": PORTAL})
    with pytest.raises(net.NetError, match="gzip"):
        net.download("http://h/f.crx.gz", str(tmp_path / "f.crx.gz"), opener=server, validate=lambda p: provider.archive_problem(p, "f.crx.gz"))
    assert server.count("GET") == 1 and os.listdir(str(tmp_path)) == []                        # no retry: a portal does not get better


def test_http_client_errors_are_net_errors_in_every_call():
    boom = lambda request, timeout=None: (_ for _ in ()).throw(http.client.BadStatusLine("x"))
    for call in (lambda: net.exists("http://h/a", opener=boom), lambda: net.read_range("http://h/a", opener=boom)):
        with pytest.raises(net.NetError):
            call()
        net.reset_breaker()


def test_exists_tells_a_missing_file_from_a_server_that_is_unwell():
    server = Server({"http://h/a": Answer(b"", length=1234), "http://h/b": 404, "http://h/c": 403, "http://h/d": 503,
                     "http://h/e": 410, "http://h/f": TimeoutError("slow")})
    assert net.exists("http://h/a", opener=server) == (True, 1234)
    assert net.exists("http://h/b", opener=server) == (False, None) and net.exists("http://h/c", opener=server) == (False, None)
    assert net.exists("http://h/e", opener=server) == (False, None)
    with pytest.raises(net.NetError) as caught:
        net.exists("http://h/d", opener=server)
    assert caught.value.status == 503
    with pytest.raises(net.NetError) as caught:
        net.exists("http://h/f", opener=server)
    assert caught.value.status is None


def test_an_offline_host_is_dropped_after_two_failures_and_comes_back_later(monkeypatch):
    clock = [1000.0]
    breaker = net._Breaker(clock=lambda: clock[0])
    monkeypatch.setattr(net, "BREAKER", breaker)
    calls = []

    def down(request, timeout=None):
        calls.append(request.full_url)
        raise TimeoutError("timed out")
    for _ in range(2):
        with pytest.raises(net.NetError):
            net.exists("http://dead.example/a", opener=down)
    assert len(calls) == 2
    for number in range(20):                                              # every other file of that host fails at once, nothing is asked
        with pytest.raises(net.NetError, match="not answering"):
            net.exists("http://dead.example/f%d" % number, opener=down)
    assert len(calls) == 2
    with pytest.raises(net.NetError):                                      # another host is not affected
        net.exists("http://alive.example/a", opener=down)
    assert len(calls) == 3
    clock[0] += net.BREAKER_PAUSE + 1
    server = Server({"http://dead.example/a": Answer(b"", length=5)})
    assert net.exists("http://dead.example/a", opener=server) == (True, 5)         # one request is let through, and the host is back
    assert net.exists("http://dead.example/b", opener=Server({"http://dead.example/b": 404})) == (False, None)


def test_an_http_answer_never_trips_the_breaker():
    server = Server({"http://h/a": 503})
    for _ in range(6):
        with pytest.raises(net.NetError):
            net.exists("http://h/a", opener=server)
    assert server.count() == 6


def gz_product(count=1, nav=1, sizes=True):
    obs = [("http://h/o%d.crx.gz" % i, "o%d.crx.gz" % i) for i in range(count)]
    navs = [("http://h/n%d.rnx.gz" % i, "n%d.rnx.gz" % i) for i in range(nav)]
    product = provider.Product("x", "x", 1.0, 3, obs, navs)
    if sizes:
        product.sizes = dict((u, len(GZ)) for u, _ in obs + navs)
    return product


STATION = types.SimpleNamespace(code="ALIC")


def test_a_captive_portal_page_is_never_cached_as_rinex(tmp_path):
    product = gz_product(1, 1, sizes=False)
    server = Server({"http://h/o0.crx.gz": PORTAL, "http://h/n0.rnx.gz": GZ})
    with pytest.raises(net.NetError, match="gzip"):
        provider.fetch_product("igs", STATION, product, str(tmp_path / "c"), opener=server)
    assert not os.path.exists(str(tmp_path / "c" / "o0.crx.gz"))


def test_an_html_file_already_in_the_cache_is_replaced(tmp_path):
    folder = tmp_path / "c"
    folder.mkdir()
    (folder / "o0.crx.gz").write_bytes(PORTAL)                           # cached by an older version, or by a bad network
    (folder / "n0.rnx.gz").write_bytes(GZ)
    server = Server({"http://h/o0.crx.gz": GZ})
    obs, nav = provider.fetch_product("igs", STATION, gz_product(), str(folder), opener=server)
    assert open(obs[0], "rb").read() == GZ and server.count("GET") == 1 and server.count("GET", "http://h/n0.rnx.gz") == 0


def test_a_cached_file_of_another_size_than_the_server_announces_is_fetched_again(tmp_path):
    folder = tmp_path / "c"
    folder.mkdir()
    (folder / "o0.crx.gz").write_bytes(GZ[:30])                           # a file served while it was still being written
    (folder / "n0.rnx.gz").write_bytes(GZ)
    server = Server({"http://h/o0.crx.gz": GZ})
    provider.fetch_product("igs", STATION, gz_product(), str(folder), opener=server)
    assert (folder / "o0.crx.gz").read_bytes() == GZ and server.count("GET") == 1


def test_a_complete_cached_file_is_not_downloaded_again(tmp_path):
    folder = tmp_path / "c"
    folder.mkdir()
    for name in ("o0.crx.gz", "n0.rnx.gz"):
        (folder / name).write_bytes(GZ)
    server = Server({})
    provider.fetch_product("igs", STATION, gz_product(), str(folder), opener=server)
    assert server.calls == []


def test_the_broadcast_file_of_a_day_that_is_not_over_is_fetched_again_when_nobody_can_say_it_is_complete(tmp_path, monkeypatch):
    name = "BRDC00IGS_R_20263160000_01D_MN.rnx.gz"
    path = tmp_path / name
    path.write_bytes(GZ)
    day = datetime.datetime(2026, 11, 12)
    assert provider.day_of_file(name) == day and provider.day_of_file("crte350i.23d.Z") == datetime.datetime(2023, 12, 16)
    assert provider.day_of_file("whatever.gz") is None
    day_s = (day - datetime.datetime(1970, 1, 1)).total_seconds()
    for mtime, expected in ((day_s + 3600.0, False), (day_s + 2 * 86400.0, True)):                # cached in the day / two days later
        os.utime(str(path), (mtime, mtime))
        assert provider._cache_ok(str(path), name, None) is expected
    assert provider._cache_ok(str(path), name, len(GZ)) is True                                  # unless the server says it has not grown
    assert provider._cache_ok(str(path), name, len(GZ) + 100) is False


def test_the_archive_check_knows_the_formats_of_the_servers(tmp_path):
    def check(name, content):
        path = tmp_path / "t"
        path.write_bytes(content)
        return provider.archive_problem(str(path), name)
    assert check("a.crx.gz", GZ) is None and check("a.d.Z", b"\x1f\x9dxx") is None
    assert check("a.crx", b"3.0           COMPACT RINEX FORMAT                    CRINEX VERS   / TYPE\n") is None
    assert check("a.rnx", b"     3.04           OBSERVATION DATA    M                   RINEX VERSION / TYPE\n") is None
    for name in ("a.crx.gz", "a.d.Z", "a.rnx"):
        assert check(name, PORTAL) and check(name, b"")


def test_files_are_fetched_in_parallel_and_come_back_in_order(tmp_path):
    product = gz_product(6, 2)
    seen, lock = [0, 0], threading.Lock()

    def slow():
        with lock:
            seen[0] += 1
            seen[1] = max(seen[1], seen[0])
        threading.Event().wait(0.05)
        with lock:
            seen[0] -= 1
        return GZ
    server = Server(dict((u, slow) for u, _ in product.obs + product.nav))
    progress = []
    obs, nav = provider.fetch_product("igs", STATION, product, str(tmp_path / "c"), opener=server, progress=lambda *a: progress.append(a))
    assert [os.path.basename(p) for p in obs] == ["o%d.crx.gz" % i for i in range(6)] and [os.path.basename(p) for p in nav] == ["n0.rnx.gz", "n1.rnx.gz"]
    assert 1 < seen[1] <= provider.DOWNLOAD_WORKERS and server.count("GET") == 8
    values = [(i + d / t) / n for i, n, d, t in progress]
    assert values[-1] == pytest.approx(1.0) and max(values) <= 1.0 + 1e-9


def test_a_failure_among_parallel_downloads_is_reported_as_itself(tmp_path):
    product = gz_product(4, 1)
    routes = dict((u, GZ) for u, _ in product.obs + product.nav)
    routes["http://h/o2.crx.gz"] = 404
    with pytest.raises(net.NetError) as caught:
        provider.fetch_product("igs", STATION, product, str(tmp_path / "c"), opener=Server(routes))
    assert caught.value.status == 404 and "stopped" not in str(caught.value)


def test_the_cache_is_trimmed_oldest_first_and_abandoned_part_files_go(tmp_path, monkeypatch):
    monkeypatch.setattr(provider, "cache_dir", lambda: str(tmp_path))
    assert provider.ensure_cache_marker()
    root = tmp_path / "rinex" / "igs" / "ALIC"
    root.mkdir(parents=True)
    for k in range(4):
        path = root / ("f%d.gz" % k)
        path.write_bytes(b"x" * 1000)
        os.utime(str(path), (1000.0 + k, 1000.0 + k))
    (root / "old.gz.part").write_bytes(b"x" * 1000)
    os.utime(str(root / "old.gz.part"), (1000.0, 1000.0))
    (root / "fresh.gz.part").write_bytes(b"x" * 1000)
    kept = REAL_PRUNE(None, max_bytes=2500, now=1000.0 + 7200.0)
    left = sorted(os.listdir(str(root)))
    assert left == ["f3.gz", "fresh.gz.part"] and kept == 2000                               # the old .part and the oldest files went, the new .part counts


# ---------------------------------------------------------------------------------------------------------------------
# 5. A server that is unwell does not silently change the product
# ---------------------------------------------------------------------------------------------------------------------

def two_products():
    fine = provider.Product("v3_1hz", "1 s", 1.0, 3, [("http://h/fine_%d" % i, "fine_%d" % i) for i in range(3)], [("http://h/nav", "nav")])
    coarse = provider.Product("daily_30s", "30 s", 30.0, 3, [("http://h/daily", "daily")], [("http://h/nav", "nav")])
    return fine, coarse


def test_a_503_on_one_slot_of_the_fine_product_is_an_error_not_a_missing_product():
    fine, coarse = two_products()
    server = Server({"http://h/fine_0": Answer(b"", length=10), "http://h/fine_1": 503, "http://h/fine_2": Answer(b"", length=10),
                     "http://h/daily": Answer(b"", length=10), "http://h/nav": Answer(b"", length=10)})
    with pytest.raises(net.NetError):
        provider.probe_products([fine, coarse], opener=server)
    assert server.count(url="http://h/fine_1") == 2                                          # asked twice
    assert server.count(url="http://h/daily") == 0                                           # and never fell back to the coarse product


def test_one_503_that_goes_away_on_the_second_try_changes_nothing():
    fine, coarse = two_products()
    server = Server({"http://h/fine_0": Answer(b"", length=10), "http://h/fine_1": [503, Answer(b"", length=10)],
                     "http://h/fine_2": Answer(b"", length=10), "http://h/daily": Answer(b"", length=10), "http://h/nav": Answer(b"", length=10)})
    found = provider.probe_products([fine, coarse], opener=server)
    assert [p.key for p in found] == ["v3_1hz", "daily_30s"]


def test_a_missing_file_is_still_a_missing_product():
    fine, coarse = two_products()
    server = Server({"http://h/fine_0": 404, "http://h/daily": Answer(b"", length=10), "http://h/nav": Answer(b"", length=10)})
    assert [p.key for p in provider.probe_products([fine, coarse], opener=server)] == ["daily_30s"]


# ---------------------------------------------------------------------------------------------------------------------
# 6. The 1 Hz product of the flight beats a coarser one of the wide window
# ---------------------------------------------------------------------------------------------------------------------

class FakeProvider(object):
    def __init__(self, wide, narrow):
        self.wide, self.narrow, self.asked = wide, narrow, []

    def probe(self, station, start, end, **kwargs):
        self.asked.append((start, end))
        return list(self.wide if len(self.asked) == 1 else self.narrow)


def product(key, interval):
    return types.SimpleNamespace(key=key, interval=interval)


WINDOW = pipeline.Window(datetime.datetime(2026, 11, 11, 10, 0), datetime.datetime(2026, 11, 11, 10, 30))


def test_the_narrow_window_is_tried_when_the_wide_one_only_has_a_coarse_product():
    fake = FakeProvider([product("daily_30s", 30.0)], [product("v3_1hz", 1.0)])
    found = runner.find_products(fake, None, WINDOW, 1)
    assert [p.key for p in found] == ["v3_1hz"] and len(fake.asked) == 2
    assert fake.asked[1] == (WINDOW.start, WINDOW.end) and fake.asked[0][0] < WINDOW.start


def test_the_wide_product_stays_when_the_narrow_one_is_not_finer():
    fake = FakeProvider([product("daily_30s", 30.0)], [product("hourly_30s", 30.0)])
    assert [p.key for p in runner.find_products(fake, None, WINDOW, 1)] == ["daily_30s"]
    fake = FakeProvider([product("daily_30s", 30.0)], [])
    assert [p.key for p in runner.find_products(fake, None, WINDOW, 1)] == ["daily_30s"]


def test_a_fine_wide_product_is_not_questioned_and_nothing_wide_still_tries_the_narrow_window():
    fake = FakeProvider([product("v3_1hz", 1.0)], [product("other", 1.0)])
    assert [p.key for p in runner.find_products(fake, None, WINDOW, 1)] == ["v3_1hz"] and len(fake.asked) == 1
    fake = FakeProvider([], [product("v3_1hz", 1.0)])
    assert [p.key for p in runner.find_products(fake, None, WINDOW, 1)] == ["v3_1hz"]
    fake = FakeProvider([product("daily_30s", 30.0)], [product("v3_1hz", 1.0)])
    assert [p.key for p in runner.find_products(fake, None, WINDOW, 0)] == ["daily_30s"] and len(fake.asked) == 1      # no margin: no second window


def test_a_survey_that_already_found_the_files_is_not_asked_again(monkeypatch, tmp_path):
    fake = FakeProvider([], [])
    monkeypatch.setattr(runner.catalog, "provider_of", lambda s: fake)
    seen = []
    monkeypatch.setattr(runner.pipeline, "prepare_base", lambda spec, *a, **k: seen.append(spec.product) or "base")
    s = types.SimpleNamespace(code="X", distance_m=1000.0)
    assert runner.fetch_base(s, WINDOW, 1, {}, str(tmp_path), products=[product("v3_1hz", 1.0)]) == "base"
    assert fake.asked == [] and seen[0].key == "v3_1hz"
    fake.wide, fake.narrow = [], [product("v3_1hz", 1.0)]
    runner.fetch_base(s, WINDOW, 1, {}, str(tmp_path), products=[product("daily_30s", 30.0)])          # coarse: the servers are asked again
    assert len(fake.asked) == 2 and seen[1].key == "v3_1hz"


# ---------------------------------------------------------------------------------------------------------------------
# 7. Unix compress
# ---------------------------------------------------------------------------------------------------------------------

def sample_text(size=60000):
    import random
    rng = random.Random(7)
    lines = ["G%02d  %14.3f 7 %14.3f 7 %10.3f   %8.3f" % (i % 32, 21000000 + rng.random() * 5e6, 1e8 + rng.random() * 9e7,
                                                         rng.random() * 3000 - 1500, 30 + rng.random() * 20) for i in range(size // 70)]
    return ("\n".join(lines) + "\n").encode()


@pytest.mark.parametrize("bits, clear_every", [(16, None), (12, None), (9, None), (16, 400)])
def test_the_z_decoder_inverts_an_encoder_whatever_the_code_size_and_the_clear_codes(bits, clear_every):
    data = sample_text()
    assert unpack.lzw_decompress(aeroppk_lzw.z_compress(data, bits, clear_every)) == data


def test_the_z_decoder_on_odd_inputs():
    for data in (b"a", b"aa", b"abababababababab", bytes(range(256)) * 3, b"\x00" * 5000):
        assert unpack.lzw_decompress(aeroppk_lzw.z_compress(data)) == data
    with pytest.raises(unpack.UnpackError):
        unpack.lzw_decompress(b"\x1f\x9d\x90\xff\xff\xff\xff")


def test_an_unpacked_file_is_reused_until_its_source_changes(tmp_path, monkeypatch):
    source = tmp_path / "in" / "base.rnx.gz"
    source.parent.mkdir()
    with gzip.open(str(source), "wb") as handle:
        handle.write(b"first")
    out = str(tmp_path / "work")
    path = unpack.unpack(str(source), out)
    assert open(path, "rb").read() == b"first" and os.path.basename(path) == "base.rnx"
    monkeypatch.setattr(unpack.gzip, "open", lambda *a, **k: (_ for _ in ()).throw(AssertionError("unpacked again")))
    assert unpack.unpack(str(source), out) == path                                              # no decoding the second time
    monkeypatch.undo()
    with gzip.open(str(source), "wb") as handle:
        handle.write(b"second, longer")
    assert open(unpack.unpack(str(source), out), "rb").read() == b"second, longer"
    assert source.exists()                                                                      # the cache is never touched


# ---------------------------------------------------------------------------------------------------------------------
# 3. The frame declared when the positions are written
# ---------------------------------------------------------------------------------------------------------------------

def station_of(provider_id):
    return station.Station("S", "S00XXX", "S", 45.0, 5.0, 300.0, None, None, "", "", provider=provider_id)


ETRS_CHUNKS = ("RGF93 v1 / Lambert-93 (EPSG::2154)", "ETRS89 / UTM zone 32N (EPSG::25832)", "ETRS89 (EPSG::4258)")
WGS_CHUNKS = ("WGS 84 / UTM zone 31N (EPSG::32631)", "WGS 84 (EPSG::4326)")
NAD_CHUNKS = ("NAD83(2011) / UTM zone 10N (EPSG::6339)", "NAD83 / California zone 5 (ftUS)")


@pytest.mark.parametrize("network", ["rgp", "epn"])
def test_european_networks_declare_etrs89(network):
    for crs in ETRS_CHUNKS:
        decided = frames.source_frame([station_of(network)], crs)
        assert decided.ok and decided.source == "EPSG::4258" and decided.retry is None and decided.checked


def test_noaa_cors_declares_nad83_2011_and_never_etrs89():
    for crs in NAD_CHUNKS:
        decided = frames.source_frame([station_of("noaa")], crs)
        assert decided.ok and decided.source == "EPSG::6318" and "NAD83" in decided.label
    assert not frames.source_frame([station_of("noaa")], ETRS_CHUNKS[0]).ok


@pytest.mark.parametrize("network", ["igs", "ga"])
def test_igs_and_geoscience_australia_declare_itrf_and_only_into_a_global_chunk(network):
    for crs in WGS_CHUNKS:
        decided = frames.source_frame([station_of(network)], crs)
        assert decided.ok and decided.source == "EPSG::9989" and decided.retry == "EPSG::4326" and "IGS20" in decided.label
    for crs in ETRS_CHUNKS + NAD_CHUNKS:
        decided = frames.source_frame([station_of(network)], crs)
        assert decided.problem == "mismatch" and decided.fields["crs"] == crs


def test_a_plate_fixed_base_is_refused_into_a_global_chunk_it_was_converted_wrongly_before():
    """The French project in WGS 84 with an RGP base: Metashape has no epoch, it would write ETRS89 numbers as WGS 84, 0.9 m away."""
    for network, crs in (("rgp", WGS_CHUNKS[0]), ("epn", WGS_CHUNKS[1]), ("noaa", WGS_CHUNKS[0])):
        decided = frames.source_frame([station_of(network)], crs)
        assert decided.problem == "mismatch" and not decided.ok and decided.source is None


def test_a_chunk_whose_datum_the_name_does_not_tell_is_allowed_but_flagged():
    decided = frames.source_frame([station_of("rgp")], "Swiss CH1903+ / LV95 (EPSG::2056)")
    assert decided.ok and decided.source == "EPSG::4258" and decided.checked is False
    assert frames.chunk_family("Swiss CH1903+ / LV95") is None and frames.chunk_family("") == "" and frames.chunk_family(None) == ""


def test_a_chunk_without_a_coordinate_system_is_refused():
    assert frames.source_frame([station_of("rgp")], "").problem == "local"


def test_bases_of_different_frames_are_not_written():
    decided = frames.source_frame([station_of("rgp"), station_of("igs")], WGS_CHUNKS[0])
    assert decided.problem == "mixed" and "RGF93" in decided.fields["frames"] and "IGS20" in decided.fields["frames"]
    assert frames.source_frame([station_of("rgp"), station_of("epn")], ETRS_CHUNKS[0]).ok            # RGF93 and ETRF2000 are both ETRS89
    assert frames.source_frame([], ETRS_CHUNKS[0]).problem == "none"


def test_an_own_base_declares_what_the_user_said_and_nothing_when_nothing_was_said():
    own = types.SimpleNamespace(provider="own", code="OWN")
    assert frames.source_frame([own], ETRS_CHUNKS[0]).problem == "own"
    assert frames.source_frame([own], ETRS_CHUNKS[0], "").problem == "own"
    assert frames.source_frame([own], ETRS_CHUNKS[0], "nonsense").problem == "own"
    assert frames.source_frame([own], ETRS_CHUNKS[0], "etrs89").source == "EPSG::4258"
    assert frames.source_frame([own], NAD_CHUNKS[0], "nad83").source == "EPSG::6318"
    assert frames.source_frame([own], WGS_CHUNKS[0], "itrf").source == "EPSG::4326"
    assert frames.source_frame([own], WGS_CHUNKS[0], "etrs89").problem == "mismatch"                  # what was declared does not fit the chunk


@pytest.mark.parametrize("network", ["rgp", "epn", "igs", "noaa", "own"])
def test_simple_mode_writes_any_single_frame_into_a_wgs84_chunk_as_it_is(network):
    base = types.SimpleNamespace(provider="own", code="OWN") if network == "own" else station_of(network)
    for crs in WGS_CHUNKS:
        decided = frames.simple_frame([base], crs)
        assert decided.ok and decided.source == "EPSG::4326" and decided.retry is None


def test_simple_mode_sends_any_other_chunk_to_the_advanced_mode():
    for crs in ETRS_CHUNKS + NAD_CHUNKS + ("Swiss CH1903+ / LV95 (EPSG::2056)", ""):
        decided = frames.simple_frame([station_of("rgp")], crs)
        assert decided.problem == "simple_only" and decided.fields["crs"]
    assert frames.source_frame([station_of("rgp")], ETRS_CHUNKS[0]).ok                              # what Advanced then does


def test_simple_mode_refuses_bases_of_different_frames_too():
    assert frames.simple_frame([station_of("rgp"), station_of("igs")], WGS_CHUNKS[1]).problem == "mixed"
    assert frames.simple_frame([station_of("rgp"), station_of("epn")], WGS_CHUNKS[1]).ok
    assert frames.simple_frame([], WGS_CHUNKS[1]).problem == "none"


def test_every_frame_of_a_network_has_a_declaration():
    from aerotools.tools.aeroppk.stations import epn, ga, igs, noaa, rgp
    groups = set(p.frame_group for p in (epn.PROVIDER, ga.PROVIDER, igs.PROVIDER, noaa.PROVIDER, rgp.PROVIDER))
    assert groups <= set(frames.NETWORK_FRAMES)


def test_the_positions_are_written_with_the_declared_system_and_the_retry_only_when_nothing_was_written():
    calls = []

    def setter(chk, items, source=None):
        calls.append(source)
        return {"done": 0, "missing": [], "failed": [i["path"] for i in items]} if source == "EPSG::9989" else {"done": len(items), "missing": [], "failed": []}
    items = [{"path": "a"}, {"path": "b"}]
    igs_frame = frames.source_frame([station_of("igs")], WGS_CHUNKS[0])
    assert frames.write_positions(None, items, igs_frame, setter)["done"] == 2 and calls == ["EPSG::9989", "EPSG::4326"]
    del calls[:]
    rgp_frame = frames.source_frame([station_of("rgp")], ETRS_CHUNKS[0])
    frames.write_positions(None, items, rgp_frame, lambda c, i, source=None: calls.append(source) or {"done": 0, "missing": [], "failed": ["a", "b"]})
    assert calls == ["EPSG::4258"]                                                                    # no retry where none is defined


def test_results_screen_takes_the_frame_from_the_bases_that_count(monkeypatch):
    from aerotools.tools.aeroppk import screen_export
    monkeypatch.setattr(screen_export.msapi, "chunk_crs_name", lambda chk: WGS_CHUNKS[0])
    run = lambda code, provider_id: types.SimpleNamespace(base=types.SimpleNamespace(name=code, spec=types.SimpleNamespace(
        station=types.SimpleNamespace(code=code, provider=provider_id))))
    result = types.SimpleNamespace(runs=[run("IGSA", "igs"), run("RGPA", "rgp")], excluded=["RGPA"])
    screen = types.SimpleNamespace(state=types.SimpleNamespace(results=[result], own_frame=""))
    frame = screen_export.ExportScreen._frame
    assert frame(screen, object()).source == "EPSG::4326"                                           # Simple: WGS 84, as it is
    assert frame(screen, object(), True).source == "EPSG::9989"                                     # the RGP base was left out by the user
    result.excluded = []
    assert frame(screen, object()).problem == "mixed"
    assert frame(screen, object(), True).problem == "mixed"
    own = types.SimpleNamespace(runs=[run("OWN", "own")], excluded=[])
    screen = types.SimpleNamespace(state=types.SimpleNamespace(results=[own], own_frame="itrf"))
    assert frame(screen, object(), True).source == "EPSG::4326"
    monkeypatch.setattr(screen_export.msapi, "chunk_crs_name", lambda chk: ETRS_CHUNKS[0])
    assert frame(screen, object()).problem == "simple_only"                                         # Lambert 93: only Advanced writes


# ---------------------------------------------------------------------------------------------------------------------
# 14. The antenna calibration is not applied twice to a typed phase centre
# ---------------------------------------------------------------------------------------------------------------------

def run_with_antex(monkeypatch, tmp_path, provider_id):
    atx = tmp_path / "t.atx"
    atx.write_text(
        "     1.4            M                                       ANTEX VERSION / SYST\n"
        "                                                            END OF HEADER       \n"
        "ANT             NONE                                        TYPE / SERIAL NO    \n"
        "                                                            END OF ANTENNA      \n", encoding="ascii")
    rig = engine._Rig(monkeypatch, {"GR": rtklib.Q_FIX, "G": rtklib.Q_FIX})
    seen = []
    original = rig._run
    monkeypatch.setattr(rtklib, "run_rnx2rtkp", lambda rover, base, navs, opts, out, **k: seen.append((opts.base_antenna, opts.antex_file)) or original(
        rover, base, navs, opts, out, **k))
    events = engine._events()
    info = types.SimpleNamespace(systems="GR", antenna_hen=(0.0, 0.0, 0.0), antenna="ANT", first=None, last=None)
    spec = types.SimpleNamespace(station=types.SimpleNamespace(code="B", provider=provider_id), name="B")
    base = pipeline.BaseData(spec, "base.obs", [], info, (4600000.0, 700000.0, 4350000.0))
    window = pipeline.Window(datetime.datetime(2026, 5, 19, 11, 0), datetime.datetime(2026, 5, 19, 12, 0))
    pipeline.run_base(types.SimpleNamespace(obs="r.obs", nav=""), events, window, base, rtklib.Options(antex_file=str(atx)),
                      types.SimpleNamespace(systems="GR", first=None, last=None), {"rnx2rtkp": "x"}, str(tmp_path))
    return seen, base


def test_a_network_base_gets_its_antenna_calibration_and_an_own_base_does_not(monkeypatch, tmp_path):
    seen, base = run_with_antex(monkeypatch, tmp_path, "igs")
    assert seen and all(model == "ANT NONE" for model, _file in seen)
    seen, base = run_with_antex(monkeypatch, tmp_path, "own")
    assert seen and all(model == "" for model, _file in seen)                                    # nothing for RTKLIB to add on top of the phase centre


# ---------------------------------------------------------------------------------------------------------------------
# 9. ANTEX files are parsed once
# ---------------------------------------------------------------------------------------------------------------------

def test_two_antex_files_that_alternate_are_each_parsed_once(tmp_path, monkeypatch):
    parsed = []
    real = antex._read_models
    monkeypatch.setattr(antex, "_read_models", lambda path: parsed.append(path) or real(path))
    antex._CACHE.clear()
    a, b = tmp_path / "igs.atx", tmp_path / "ngs.atx"
    for path, model in ((a, "AAA"), (b, "BBB")):
        path.write_text("%-20s%-20s                    TYPE / SERIAL NO    \n" % (model, "NONE"), encoding="ascii")
    for _ in range(4):
        assert antex.choose([str(a), str(b)], "BBB") == (str(b), "BBB NONE")
        assert antex.choose([str(a), str(b)], "AAA") == (str(a), "AAA NONE")
    assert sorted(parsed) == sorted([str(a), str(b)])
    a.write_text("%-20s%-20s                    TYPE / SERIAL NO    \n" % ("CCC", "NONE"), encoding="ascii")        # a new version of one file: read again
    assert antex.choose([str(a)], "CCC")[1] == "CCC NONE" and parsed.count(str(a)) == 2


def test_the_phase_centre_of_an_antenna_is_read_once_per_file_version(tmp_path, monkeypatch):
    path = tmp_path / "t.atx"
    path.write_text(
        "TRM55971.00     NONE                                        TYPE / SERIAL NO    \n"
        "   G01                                                      START OF FREQUENCY  \n"
        "      0.40      0.20    123.40                              NORTH / EAST / UP   \n"
        "                                                            END OF ANTENNA      \n", encoding="ascii")
    calls = []
    real = antex._phase_centre_up
    monkeypatch.setattr(antex, "_phase_centre_up", lambda p, a: calls.append(a) or real(p, a))
    for _ in range(3):
        assert antex.phase_centre_up(str(path), "TRM55971.00     NONE") == pytest.approx(0.1234)
    assert len(calls) == 1 and antex.phase_centre_up(str(tmp_path / "none.atx"), "TRM55971.00") is None


# ---------------------------------------------------------------------------------------------------------------------
# 18. The time system of a RINEX file
# ---------------------------------------------------------------------------------------------------------------------

def obs_with_time_system(tmp_path, system, first="  2026    11     4     8    45    0.0000000", last="  2026    11     4     9     0    0.0000000"):
    text = wl.OBS.replace("  2030    01     1    10    00    0.0000000     GPS         TIME OF FIRST OBS",
                          "%s     %-3s         TIME OF FIRST OBS" % (first, system)).replace(
        "  2030    01     1    13    00    0.0000000     GPS         TIME OF LAST OBS", "%s     %-3s         TIME OF LAST OBS" % (last, system))
    return wl.write(tmp_path, "t_%s.obs" % system, text)


def test_times_written_in_utc_or_beidou_time_are_read_as_gps_time(tmp_path):
    gps = rinex.read_obs_header(obs_with_time_system(tmp_path, "GPS"))
    glo = rinex.read_obs_header(obs_with_time_system(tmp_path, "GLO"))
    bdt = rinex.read_obs_header(obs_with_time_system(tmp_path, "BDT"))
    gal = rinex.read_obs_header(obs_with_time_system(tmp_path, "GAL"))
    assert gps.first == datetime.datetime(2026, 11, 4, 8, 45, 0) == gal.first
    assert glo.first == gps.first + datetime.timedelta(seconds=18) and glo.last == gps.last + datetime.timedelta(seconds=18)
    assert bdt.first == gps.first + datetime.timedelta(seconds=14)
    assert glo.time_system == "GLO"
    assert rinex.covers(glo, datetime.datetime(2026, 11, 4, 8, 45, 30), datetime.datetime(2026, 11, 4, 8, 50)) is True
    assert rinex.covers(glo, datetime.datetime(2026, 11, 4, 8, 45, 10), datetime.datetime(2026, 11, 4, 8, 50)) is False        # before it starts
    assert rinex.to_gps_time(None, "GLO") is None


# ---------------------------------------------------------------------------------------------------------------------
# Missing tests of the risky paths
# ---------------------------------------------------------------------------------------------------------------------

def test_slots_and_windows_on_exact_boundaries_and_around_midnight():
    t = datetime.datetime
    assert provider.utc_slots(t(2026, 11, 4, 9, 15), t(2026, 11, 4, 9, 30), 15) == [t(2026, 11, 4, 9, 15), t(2026, 11, 4, 9, 30)]
    assert provider.utc_slots(t(2026, 11, 4, 9, 15, 1), t(2026, 11, 4, 9, 29, 59), 15) == [t(2026, 11, 4, 9, 15)]
    assert provider.utc_slots(t(2026, 11, 4, 9, 0), t(2026, 11, 4, 9, 0), 60) == [t(2026, 11, 4, 9, 0)]
    over = provider.utc_slots(t(2026, 11, 4, 23, 50), t(2026, 11, 5, 0, 10), 60)
    assert over == [t(2026, 11, 4, 23, 0), t(2026, 11, 5, 0, 0)]
    gps_start = t(2026, 11, 4, 23, 59, 50) + datetime.timedelta(seconds=18)                      # 10 s before midnight UTC, in GPS time
    start, end = provider.window_utc(gps_start, gps_start + datetime.timedelta(seconds=30), 60.0)
    assert start == t(2026, 11, 4, 23, 58, 50) and end == t(2026, 11, 5, 0, 1, 20)
    days = provider.utc_slots(start.replace(hour=0, minute=0), end, 1440)
    assert days == [t(2026, 11, 4), t(2026, 11, 5)]


def test_leap_seconds_around_the_last_one():
    d = datetime.date
    assert gnsstime.leap_seconds(d(2016, 12, 31)) == 17 and gnsstime.leap_seconds(d(2017, 1, 1)) == 18
    assert gnsstime.leap_seconds(datetime.datetime(2016, 12, 31, 23, 59, 59)) == 17 and gnsstime.leap_seconds(d(2026, 11, 12)) == 18
    t = datetime.datetime
    assert gnsstime.gps_to_utc(t(2017, 1, 1, 0, 0, 18)) == t(2017, 1, 1, 0, 0, 0)
    assert gnsstime.gps_to_utc(t(2017, 1, 1, 0, 0, 16)) == t(2016, 12, 31, 23, 59, 59)
    assert gnsstime.gps_to_utc(t(2016, 12, 31, 12, 0, 17)) == t(2016, 12, 31, 12, 0, 0)


def test_the_report_prints_the_leap_seconds_of_the_flight_not_of_the_day_it_was_written():
    old_flight = {"flights": [{"start_utc": datetime.datetime(2016, 12, 20, 10, 0)}], "generated": datetime.datetime(2026, 11, 12)}
    assert report._leap_offset(old_flight) == 17
    assert report._leap_offset({"flights": [{"start_utc": datetime.datetime(2026, 5, 1)}], "generated": datetime.datetime(2026, 11, 12)}) == 18
    assert report._leap_offset({"flights": [], "generated": datetime.datetime(2016, 1, 1)}) == 17
    assert report._leap_offset({}) == gnsstime.leap_seconds(datetime.date.today())


def test_rtklib_never_waits_on_an_output_pipe_it_does_not_read():
    import inspect
    source = inspect.getsource(rtklib.run_rnx2rtkp)
    assert "stdout=subprocess.DEVNULL" in source and "stdout=subprocess.PIPE" not in source
