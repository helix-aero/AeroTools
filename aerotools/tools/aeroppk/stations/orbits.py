"""Which satellite orbits exist for a flight day: broadcast only, rapid precise orbits, or final precise orbits.

Only a look at open servers (one HEAD request per candidate file, no account, nothing is downloaded and the solver does not use the
answer). `level_of_days` never raises: any network trouble gives "broadcast". Answers are kept for the session; run it in a worker thread.
"""

import datetime
import threading
import time
import urllib.error
import urllib.request

from . import net

BROADCAST, RAPID, FINAL = "broadcast", "rapid", "final"
RANK = {BROADCAST: 0, RAPID: 1, FINAL: 2}

TIMEOUT = 6.0                       # one request
BUDGET = 25.0                       # all the requests of one day
MIN_SIZE = 20000                    # a precise orbit file is never smaller (a 15 min GPS-only day is 90 KB); a login page is
FINAL_MIN_AGE = 4                   # days: the earliest final orbits seen (ESA) came 3.65 days after the end of their day
KEEP_S = 1800.0                     # an answer below "final" can improve: ask again after this long
KEEP_BAD_S = 120.0                  # an answer made with a network error: ask again soon
LONG_NAMES = datetime.date(2022, 11, 27)    # GPS week 2238: the files carry the long RINEX-style names from here
GPS_EPOCH = datetime.date(1980, 1, 6)

ESA = "https://navigation-office.esa.int/products/gnss-products/%d/"
GFZ = "https://isdc-data.gfz.de/gnss/products/%s/w%d/"
CODE = "https://www.aiub.unibe.ch/download/CODE/"
BKG = "https://igs.bkg.bund.de/root_ftp/IGS/products/%d/"


def week_dow(day):
    """(GPS week, day of week 0 = Sunday) of a date."""
    days = (day - GPS_EPOCH).days
    return days // 7, days % 7


def _long(center, kind, day, step, ext="SP3.gz"):
    return "%s0OPS%s_%d%03d0000_01D_%s_ORB.%s" % (center, kind, day.year, day.timetuple().tm_yday, step, ext)


def candidates(level, day):
    """[(source name, URL)] of the files that would prove `level` for `day`, most likely first.

    Verified on 08/11/2026: ESA, CODE, GFZ and the BKG mirror of the IGS answer a plain HEAD without login. The final orbits of the IGS
    itself are on the BKG mirror only up to GPS week 2431 (later ones are at CDDIS, which needs an account, and on FTP mirrors)."""
    week, dow = week_dow(day)
    out = []
    if level == FINAL:
        if day >= LONG_NAMES:
            out.append(("ESA", ESA % week + _long("ESA", "FIN", day, "05M")))
            out.append(("CODE", "%s%d/%s" % (CODE, day.year, _long("COD", "FIN", day, "05M"))))
        out.append(("GFZ", GFZ % ("final", week) + _long("GFZ", "FIN", day, "15M")))
        if day >= LONG_NAMES:
            out.append(("IGS", BKG % week + _long("IGS", "FIN", day, "15M")))
        else:
            out.append(("CODE", "%s%d/COD%d%d.EPH.Z" % (CODE, day.year, week, dow)))
            out.append(("ESA", ESA % week + "esa%d%d.sp3.Z" % (week, dow)))
    elif level == RAPID:
        out.append(("ESA", ESA % week + _long("ESA", "RAP", day, "05M")))
        out.append(("CODE", CODE + _long("COD", "RAP", day, "05M", "SP3")))
        out.append(("GFZ", GFZ % ("rapid", week) + _long("GFZ", "RAP", day, "05M")))
        out.append(("IGS", BKG % week + _long("IGS", "RAP", day, "15M")))
    return out


class Answer(object):
    """`level` is one of BROADCAST, RAPID, FINAL; `source` the server that proved it ("" for broadcast); `sure` is False when a network
    error left some candidate unchecked, so that the level may be higher than shown."""

    def __init__(self, level=BROADCAST, source="", sure=True):
        self.level, self.source, self.sure = level, source, sure

    def __repr__(self):
        return "Answer(%s, %r, sure=%s)" % (self.level, self.source, self.sure)

    def __eq__(self, other):
        return isinstance(other, Answer) and (self.level, self.source, self.sure) == (other.level, other.source, other.sure)

    def __ne__(self, other):
        return not self == other

    __hash__ = None


def _head(url, timeout, opener):
    """(status, size, content type) of a HEAD request. Raises OSError / URLError when the server cannot be reached."""
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": net.USER_AGENT})
    try:
        with (opener or urllib.request.urlopen)(request, timeout=timeout) as answer:
            size = answer.headers.get("Content-Length")
            return 200, int(size) if size and size.isdigit() else None, (answer.headers.get("Content-Type") or "").lower()
    except urllib.error.HTTPError as exc:
        return exc.code, None, ""


def _is_file(status, size, kind):
    """A 200 that is a data file and not a login or error page that a server answers with 200."""
    return status == 200 and "html" not in kind and (size is None or size >= MIN_SIZE)


def _probe(level, day, opener, deadline, dead):
    """(source or "", sure). `dead` collects the hosts that did not answer, so that their other files are not tried."""
    sure = True
    for source, url in candidates(level, day):
        host = url.split("/")[2]
        left = deadline - time.monotonic()
        if host in dead or left <= 0:
            sure = False
            continue
        try:
            status, size, kind = _head(url, min(TIMEOUT, left), opener)
        except Exception:                                   # no network, DNS, timeout, TLS: never an error for the caller
            dead.add(host)
            sure = False
            continue
        if _is_file(status, size, kind):
            return source, True
        if status not in (404, 410, 200):                   # 401, 403, 5xx: the server did not say "not there"
            sure = False
    return "", sure


class Cache(object):
    """Answers per day for the session, thread safe. Time comes from `clock` so that a test can move it."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._items = {}

    def get(self, day):
        with self._lock:
            entry = self._items.get(day)
            if entry is None:
                return None
            answer, until = entry
            if until is not None and self._clock() >= until:
                del self._items[day]
                return None
            return answer

    def put(self, day, answer):
        if answer.level == FINAL and answer.sure:
            until = None
        else:
            until = self._clock() + (KEEP_S if answer.sure else KEEP_BAD_S)
        with self._lock:
            self._items[day] = (answer, until)

    def clear(self):
        with self._lock:
            self._items.clear()


_cache = Cache()


def clear_cache():
    _cache.clear()


def level_of_day(day, opener=None, today=None, cache=None):
    """The best `Answer` for one UTC date (`datetime.date`). Never raises."""
    cache = cache or _cache
    try:
        if isinstance(day, datetime.datetime):
            day = day.date()
        found = cache.get(day)
        if found is not None:
            return found
        today = today or datetime.datetime.now(datetime.timezone.utc).date()
        answer = _look(day, opener, today)
        cache.put(day, answer)
        return answer
    except Exception:
        return Answer()


def _look(day, opener, today):
    age = (today - day).days
    if age < 1:                                             # a day is not over: no rapid orbit exists yet
        return Answer()
    deadline = time.monotonic() + BUDGET
    dead = set()
    unsure = False
    if age >= FINAL_MIN_AGE:
        source, sure = _probe(FINAL, day, opener, deadline, dead)
        if source:
            return Answer(FINAL, source)
        unsure = not sure
    source, sure = _probe(RAPID, day, opener, deadline, dead)
    if source:
        return Answer(RAPID, source, sure=not unsure)
    return Answer(BROADCAST, "", sure=sure and not unsure)


def days_between(first, last):
    """The dates from `first` to `last` (datetimes or dates, UTC) as a tuple: two when a flight crosses midnight."""
    first = first.date() if isinstance(first, datetime.datetime) else first
    last = last.date() if isinstance(last, datetime.datetime) else last
    return tuple(first + datetime.timedelta(days=n) for n in range(max(0, (last - first).days) + 1))


def words(answer):
    """[(text key, fields)] that tell what `answer` means, for the tooltip; None stands for "still looking"."""
    if answer is None:
        return [("aeroppk.orbit.checking", {})]
    if answer.level == BROADCAST:
        return [("aeroppk.orbit.broadcast" if answer.sure else "aeroppk.orbit.offline", {})]
    return [("aeroppk.orbit." + answer.level, {"source": answer.source}), ("aeroppk.orbit.note", {})]


def level_of_days(days, opener=None, today=None, cache=None):
    """The `Answer` that holds for every day of a flight that crosses midnight: the lowest level of its days."""
    worst = None
    try:
        for day in sorted(set(days)):
            answer = level_of_day(day, opener=opener, today=today, cache=cache)
            if worst is None or RANK[answer.level] < RANK[worst.level]:
                worst = answer
    except Exception:
        return Answer()
    return worst if worst is not None else Answer()


def precise_available(days, opener=None, today=None, cache=None):
    """True when rapid or final precise orbits exist for every day. A hook for the day the solver can use them; nothing uses it yet."""
    return RANK[level_of_days(days, opener=opener, today=today, cache=cache).level] > 0
