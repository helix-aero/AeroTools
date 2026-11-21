"""Aero-PPK orbit levels: file names of the open servers, level selection, cache, errors. A fake opener, no network.

The names were checked against the real servers on 08/11/2026 (docs/AEROPPK.md, "Precise orbits"); here they are pinned.
"""
import datetime
import threading
import urllib.error

import pytest

from aerotools import i18n
from aerotools.lang import en
from aerotools.tools.aeroppk.stations import orbits

D = datetime.date
TODAY = D(2026, 11, 8)


class Answer(object):
    def __init__(self, size=100000, kind="application/x-gzip"):
        self.headers = {"Content-Length": str(size) if size is not None else None, "Content-Type": kind}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class Server(object):
    """Fake opener. `present` maps a URL to (size, content type); `rules` may answer a host with a status or an exception."""

    def __init__(self, present=(), rules=None):
        self.present = dict((u, (100000, "application/x-gzip")) if isinstance(u, str) else u for u in present)
        self.rules = rules or {}
        self.seen = []
        self.timeouts = []
        self.methods = []

    def __call__(self, request, timeout=None):
        url = request.full_url
        self.seen.append(url)
        self.timeouts.append(timeout)
        self.methods.append(request.get_method())
        host = url.split("/")[2]
        rule = self.rules.get(host)
        if isinstance(rule, Exception):
            raise rule
        if isinstance(rule, int):
            raise urllib.error.HTTPError(url, rule, "x", {}, None)
        if url in self.present:
            size, kind = self.present[url]
            return Answer(size, kind)
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)


def urls(level, day, source=None):
    return [u for s, u in orbits.candidates(level, day) if source is None or s == source]


@pytest.fixture(autouse=True)
def fresh_cache():
    orbits.clear_cache()
    yield
    orbits.clear_cache()


# ---------------------------------------------------------------------------------------------------------------------
# File names
# ---------------------------------------------------------------------------------------------------------------------

def test_gps_week_and_day_of_week():
    assert orbits.week_dow(D(1980, 1, 6)) == (0, 0)
    assert orbits.week_dow(D(2026, 11, 7)) == (2443, 6)
    assert orbits.week_dow(D(2022, 11, 27)) == (2238, 0)
    assert orbits.week_dow(D(2022, 6, 11)) == (2213, 6)


def test_final_names_of_a_recent_day():
    day = D(2026, 10, 18)                                         # week 2441, day of year 291
    assert urls(orbits.FINAL, day) == [
        "https://navigation-office.esa.int/products/gnss-products/2441/ESA0OPSFIN_20262910000_01D_05M_ORB.SP3.gz",
        "https://www.aiub.unibe.ch/download/CODE/2026/COD0OPSFIN_20262910000_01D_05M_ORB.SP3.gz",
        "https://isdc-data.gfz.de/gnss/products/final/w2441/GFZ0OPSFIN_20262910000_01D_15M_ORB.SP3.gz",
        "https://igs.bkg.bund.de/root_ftp/IGS/products/2441/IGS0OPSFIN_20262910000_01D_15M_ORB.SP3.gz",
    ]


def test_rapid_names_of_yesterday():
    day = D(2026, 11, 7)                                          # week 2443, day of year 311
    assert urls(orbits.RAPID, day) == [
        "https://navigation-office.esa.int/products/gnss-products/2443/ESA0OPSRAP_20263110000_01D_05M_ORB.SP3.gz",
        "https://www.aiub.unibe.ch/download/CODE/COD0OPSRAP_20263110000_01D_05M_ORB.SP3",
        "https://isdc-data.gfz.de/gnss/products/rapid/w2443/GFZ0OPSRAP_20263110000_01D_05M_ORB.SP3.gz",
        "https://igs.bkg.bund.de/root_ftp/IGS/products/2443/IGS0OPSRAP_20263110000_01D_15M_ORB.SP3.gz",
    ]


def test_old_days_use_the_gps_week_names():
    got = urls(orbits.FINAL, D(2022, 6, 11))                      # week 2213, day 6: before the long names (27/11/2022)
    assert "https://www.aiub.unibe.ch/download/CODE/2022/COD22136.EPH.Z" in got
    assert "https://navigation-office.esa.int/products/gnss-products/2213/esa22136.sp3.Z" in got
    assert any("GFZ0OPSFIN_20221620000_01D_15M_ORB.SP3.gz" in u for u in got)       # GFZ has long names for old days too
    assert not any("ESA0OPSFIN" in u or "IGS0OPSFIN" in u for u in got)
    assert any(u.endswith("COD22376.EPH.Z") for u in urls(orbits.FINAL, D(2022, 11, 26)))
    assert any("COD0OPSFIN_20223310000" in u for u in urls(orbits.FINAL, D(2022, 11, 27)))   # first day with the long names


def test_the_year_of_a_day_picks_the_folder_of_code():
    assert "/CODE/2025/COD0OPSFIN_20250010000" in urls(orbits.FINAL, D(2025, 1, 1), "CODE")[0]
    assert urls(orbits.FINAL, D(2024, 12, 31), "CODE")[0].endswith("COD0OPSFIN_20243660000_01D_05M_ORB.SP3.gz")


def test_no_candidate_needs_an_account():
    for level in (orbits.FINAL, orbits.RAPID):
        for day in (D(2026, 11, 7), D(2020, 1, 1)):
            for _source, url in orbits.candidates(level, day):
                assert url.startswith("https://")
                assert "cddis" not in url and "earthdata" not in url


# ---------------------------------------------------------------------------------------------------------------------
# Level selection
# ---------------------------------------------------------------------------------------------------------------------

def test_final_beats_rapid_and_stops_there():
    day = D(2026, 10, 18)
    server = Server([urls(orbits.FINAL, day, "GFZ")[0], urls(orbits.RAPID, day, "ESA")[0]])
    got = orbits.level_of_day(day, opener=server, today=TODAY)
    assert (got.level, got.source, got.sure) == (orbits.FINAL, "GFZ", True)
    assert all("RAP" not in u for u in server.seen)                                  # no rapid request once the final orbit is found
    assert server.seen == urls(orbits.FINAL, day)[:3]                                  # in order: ESA, CODE, then GFZ answered


def test_rapid_when_only_rapid_exists():
    day = D(2026, 11, 7)
    server = Server([urls(orbits.RAPID, day, "CODE")[0]])
    got = orbits.level_of_day(day, opener=server, today=TODAY)
    assert (got.level, got.source, got.sure) == (orbits.RAPID, "CODE", True)
    assert not any("FIN" in u for u in server.seen)                                  # yesterday: no final orbit can exist, not asked


def test_broadcast_when_nothing_is_published():
    day = D(2026, 11, 7)
    server = Server()
    got = orbits.level_of_day(day, opener=server, today=TODAY)
    assert (got.level, got.source, got.sure) == (orbits.BROADCAST, "", True)
    assert len(server.seen) == 4                                                       # the four rapid candidates


def test_nothing_is_asked_for_a_day_that_is_not_over():
    server = Server()
    assert orbits.level_of_day(TODAY, opener=server, today=TODAY).level == orbits.BROADCAST
    assert orbits.level_of_day(D(2026, 12, 1), opener=server, today=TODAY).level == orbits.BROADCAST
    assert server.seen == []


def test_final_is_asked_from_four_days_on():
    server = Server()
    orbits.level_of_day(D(2026, 11, 4), opener=server, today=TODAY)
    assert any("FIN" in u for u in server.seen)
    orbits.clear_cache()
    server = Server()
    orbits.level_of_day(D(2026, 11, 5), opener=server, today=TODAY)
    assert not any("FIN" in u for u in server.seen)


def test_an_old_day_found_through_the_legacy_name():
    day = D(2019, 3, 2)
    server = Server(["https://www.aiub.unibe.ch/download/CODE/2019/COD20426.EPH.Z"])
    got = orbits.level_of_day(day, opener=server, today=TODAY)
    assert (got.level, got.source) == (orbits.FINAL, "CODE")


def test_only_head_requests_with_a_user_agent_and_a_short_timeout():
    server = Server()
    orbits.level_of_day(D(2026, 11, 7), opener=server, today=TODAY)
    assert set(server.methods) == {"HEAD"}
    assert all(0 < t <= orbits.TIMEOUT for t in server.timeouts)


def test_a_login_page_or_a_stub_is_not_an_orbit_file():
    day = D(2026, 11, 7)
    url = urls(orbits.RAPID, day, "ESA")[0]
    assert orbits.level_of_day(day, opener=Server([(url, (50000, "text/html; charset=utf-8"))]), today=TODAY).level == orbits.BROADCAST
    orbits.clear_cache()
    assert orbits.level_of_day(day, opener=Server([(url, (900, "application/x-gzip"))]), today=TODAY).level == orbits.BROADCAST
    orbits.clear_cache()
    assert orbits.level_of_day(day, opener=Server([(url, (None, "application/octet-stream"))]), today=TODAY).level == orbits.RAPID   # size unknown


def test_a_server_that_says_forbidden_is_not_a_no_but_the_next_one_is_tried():
    day = D(2026, 11, 7)
    server = Server([urls(orbits.RAPID, day, "GFZ")[0]], rules={"navigation-office.esa.int": 403})
    got = orbits.level_of_day(day, opener=server, today=TODAY)
    assert (got.level, got.source) == (orbits.RAPID, "GFZ")
    orbits.clear_cache()
    nothing = orbits.level_of_day(day, opener=Server(rules={"navigation-office.esa.int": 503}), today=TODAY)
    assert (nothing.level, nothing.sure) == (orbits.BROADCAST, False)


def test_days_between_and_the_worst_day_of_a_flight():
    assert orbits.days_between(datetime.datetime(2026, 11, 6, 23, 50), datetime.datetime(2026, 11, 7, 0, 20)) == (D(2026, 11, 6), D(2026, 11, 7))
    assert orbits.days_between(D(2026, 11, 6), D(2026, 11, 6)) == (D(2026, 11, 6),)
    assert orbits.days_between(D(2026, 11, 7), D(2026, 11, 6)) == (D(2026, 11, 7),)
    final = urls(orbits.FINAL, D(2026, 10, 18), "ESA")[0]
    rapid = urls(orbits.RAPID, D(2026, 10, 19), "ESA")[0]
    server = Server([final, rapid])
    got = orbits.level_of_days(orbits.days_between(D(2026, 10, 18), D(2026, 10, 19)), opener=server, today=TODAY)
    assert got.level == orbits.RAPID                                                   # one day with final, the next with rapid only
    assert orbits.level_of_days([], opener=server, today=TODAY).level == orbits.BROADCAST


def test_precise_available_hook():
    day = D(2026, 11, 7)
    assert orbits.precise_available([day], opener=Server([urls(orbits.RAPID, day, "ESA")[0]]), today=TODAY)
    orbits.clear_cache()
    assert not orbits.precise_available([day], opener=Server(), today=TODAY)


# ---------------------------------------------------------------------------------------------------------------------
# Errors: never raise
# ---------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("error", [urllib.error.URLError("no route"), OSError("down"), TimeoutError("slow"), ConnectionResetError("reset"),
                                   ValueError("strange"), RuntimeError("worse")])
def test_any_failure_gives_broadcast(error):
    server = Server(rules={"navigation-office.esa.int": error, "www.aiub.unibe.ch": error, "isdc-data.gfz.de": error, "igs.bkg.bund.de": error})
    got = orbits.level_of_day(D(2026, 10, 18), opener=server, today=TODAY)
    assert (got.level, got.source, got.sure) == (orbits.BROADCAST, "", False)


def test_a_dead_host_is_asked_once_not_once_per_file():
    day = D(2026, 10, 18)
    server = Server(rules={"www.aiub.unibe.ch": urllib.error.URLError("dns")})
    orbits.level_of_day(day, opener=server, today=TODAY)
    assert sum(1 for u in server.seen if "aiub" in u) == 1                              # final and rapid names share the host


def test_offline_costs_at_most_one_request_per_host():
    err = urllib.error.URLError("offline")
    server = Server(rules=dict((h, err) for h in ("navigation-office.esa.int", "www.aiub.unibe.ch", "isdc-data.gfz.de", "igs.bkg.bund.de")))
    orbits.level_of_day(D(2026, 10, 18), opener=server, today=TODAY)
    assert len(server.seen) == 4


def test_a_broken_input_does_not_raise():
    assert orbits.level_of_day(None, opener=Server(), today=TODAY).level == orbits.BROADCAST
    assert orbits.level_of_day("2026-11-07", opener=Server(), today=TODAY).level == orbits.BROADCAST
    assert orbits.level_of_days(None, opener=Server(), today=TODAY).level == orbits.BROADCAST


def test_the_budget_stops_the_probe():
    old = orbits.BUDGET
    orbits.BUDGET = 0.0
    try:
        server = Server()
        got = orbits.level_of_day(D(2026, 10, 18), opener=server, today=TODAY)
    finally:
        orbits.BUDGET = old
    assert server.seen == [] and (got.level, got.sure) == (orbits.BROADCAST, False)


def test_a_datetime_is_taken_as_its_day():
    day = D(2026, 11, 7)
    server = Server([urls(orbits.RAPID, day, "ESA")[0]])
    assert orbits.level_of_day(datetime.datetime(2026, 11, 7, 14, 30), opener=server, today=TODAY).level == orbits.RAPID


# ---------------------------------------------------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------------------------------------------------

class Clock(object):
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_the_second_question_for_a_day_is_not_asked():
    day = D(2026, 11, 7)
    server = Server([urls(orbits.RAPID, day, "ESA")[0]])
    first = orbits.level_of_day(day, opener=server, today=TODAY)
    count = len(server.seen)
    again = orbits.level_of_day(day, opener=Server(), today=TODAY)                     # a server that would say "nothing": not reached
    assert again == first and count == 1 and len(server.seen) == count


def test_final_is_kept_for_the_session_the_rest_expires():
    clock = Clock()
    cache = orbits.Cache(clock)
    final_day, rapid_day = D(2026, 10, 18), D(2026, 11, 7)
    server = Server([urls(orbits.FINAL, final_day, "ESA")[0], urls(orbits.RAPID, rapid_day, "ESA")[0]])
    orbits.level_of_day(final_day, opener=server, today=TODAY, cache=cache)
    orbits.level_of_day(rapid_day, opener=server, today=TODAY, cache=cache)
    clock.now += orbits.KEEP_S - 1
    assert cache.get(rapid_day) is not None and cache.get(final_day) is not None
    clock.now += 2
    assert cache.get(rapid_day) is None                                                # rapid may become final: ask again
    assert cache.get(final_day) is not None
    clock.now += 10 ** 7
    assert cache.get(final_day) is not None


def test_an_answer_made_with_a_network_error_is_asked_again_soon():
    clock = Clock()
    cache = orbits.Cache(clock)
    day = D(2026, 11, 7)
    bad = Server(rules={"navigation-office.esa.int": OSError("x"), "www.aiub.unibe.ch": OSError("x"), "isdc-data.gfz.de": OSError("x"),
                        "igs.bkg.bund.de": OSError("x")})
    assert orbits.level_of_day(day, opener=bad, today=TODAY, cache=cache).sure is False
    assert cache.get(day) is not None
    clock.now += orbits.KEEP_BAD_S + 1
    assert cache.get(day) is None
    good = Server([urls(orbits.RAPID, day, "ESA")[0]])
    assert orbits.level_of_day(day, opener=good, today=TODAY, cache=cache).level == orbits.RAPID     # back online


def test_the_cache_is_safe_with_threads():
    day = D(2026, 11, 7)
    server = Server([urls(orbits.RAPID, day, "ESA")[0], urls(orbits.RAPID, D(2026, 11, 6), "ESA")[0]])
    results, errors = [], []

    def work(n):
        try:
            results.append(orbits.level_of_days([day, D(2026, 11, 6)], opener=server, today=TODAY).level)
        except Exception as exc:                                                        # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and results == [orbits.RAPID] * 12


# ---------------------------------------------------------------------------------------------------------------------
# Words
# ---------------------------------------------------------------------------------------------------------------------

def test_words_for_every_state_exist_in_the_catalog():
    answers = [None, orbits.Answer(), orbits.Answer(sure=False), orbits.Answer(orbits.RAPID, "ESA"), orbits.Answer(orbits.FINAL, "CODE")]
    for answer in answers:
        for key, _fields in orbits.words(answer):
            assert key in en.TEXTS, key


def test_words_say_the_source_and_that_the_solution_does_not_use_it():
    text = "\n".join(i18n.t(k, **f) for k, f in orbits.words(orbits.Answer(orbits.FINAL, "CODE")))
    assert "Final" in text and "CODE" in text and "broadcast" in text
    text = "\n".join(i18n.t(k, **f) for k, f in orbits.words(orbits.Answer(orbits.RAPID, "GFZ")))
    assert "Rapid" in text and "GFZ" in text and "{" not in text
    assert [k for k, _ in orbits.words(orbits.Answer(sure=False))] == ["aeroppk.orbit.offline"]
    assert i18n.t("aeroppk.orbit.offline") == "Broadcast orbits only."
