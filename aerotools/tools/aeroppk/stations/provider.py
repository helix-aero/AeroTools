"""What every network of base stations has in common: the file set of a station (`Product`), the probe of a server, the download.

A network (RGP, EPN, IGS, NOAA CORS, Geoscience Australia...) is a `Provider`: its metadata (name, frame, licence), its station
snapshot, and `products()`, the list of file sets that could cover a flight, best first. Nothing else in Aero-PPK knows which
network a station comes from: the basket, the map, the download and the solver only use this interface.
"""

import datetime
import json
import os
import re
import stat as stat_module
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .. import gnsstime
from . import net, station as _station

CACHE_NAME = "AeroPPK"
DOWNLOAD_WORKERS = 3                  # files of one station fetched at once: polite towards the archive, much faster than one by one
CACHE_CAP_BYTES = 4 * 1024 ** 3       # the download cache is trimmed (oldest files first) above this size
SETTLE_SECONDS = 26 * 3600            # a file of a UTC day may still grow until a couple of hours after the day ends


class Product(object):
    """One set of files that covers a time window for one station: observation files + navigation files."""

    def __init__(self, key, label, interval, version, obs, nav, nav_groups=None):
        self.key = key                   # "v3_1hz", "v2_1hz", "hourly_30s", "daily_30s"...
        self.label = label
        self.interval = interval         # seconds, None when only the file header tells
        self.version = version           # RINEX major version
        self.obs = obs                   # list of (url, file name)
        self.nav = nav                   # list of (url, file name), optional ones may be missing on the server
        self.nav_groups = nav_groups     # alternatives to `nav`: the first group with an existing file is used
        self.sizes = {}                  # url -> bytes when probed


def utc_slots(start_utc, end_utc, step_minutes):
    """Start times (UTC datetimes) of the `step_minutes` slots that overlap [start_utc, end_utc]."""
    step = datetime.timedelta(minutes=step_minutes)
    begin = start_utc.replace(second=0, microsecond=0)
    minute = (begin.minute // step_minutes) * step_minutes if step_minutes < 60 else 0
    begin = begin.replace(minute=minute)
    slots = []
    current = begin
    while current <= end_utc:
        slots.append(current)
        current += step
    return slots


def window_utc(start_gps, end_gps, margin_s):
    """The flight window padded by `margin_s` seconds on both sides, as UTC (the solver needs base data before the first photo)."""
    return (gnsstime.gps_to_utc(start_gps - datetime.timedelta(seconds=margin_s)),
            gnsstime.gps_to_utc(end_gps + datetime.timedelta(seconds=margin_s)))


RETRY_STATUS = (408, 429)


def _exists(url, opener):
    """(present, size). 404 and friends are "absent"; a timeout, a cut answer or a 5xx is retried once, then raised as NetError: the file
    may well exist, and dropping it would silently select a coarser product."""
    last = None
    for _attempt in (0, 1):
        try:
            return net.exists(url, opener=opener)
        except net.NetError as exc:
            if exc.status is not None and exc.status < 500 and exc.status not in RETRY_STATUS:
                return False, None
            last = exc
            if "not answering" in str(exc):
                break
            time.sleep(0.5)
    raise last


def probe_products(candidates, wanted_interval=None, opener=None, cancelled=None):
    """Which of the candidate products really exist on the server.

    Returns the products whose every observation file exists and that have at least one navigation file (GPS ephemerides are
    required); `nav` then lists the existing ones and `sizes` the byte counts. Order is kept (best first); with `wanted_interval`
    (seconds) products coarser than it are left out.
    """
    found = []
    for product in candidates:
        if cancelled is not None and cancelled():
            break
        if wanted_interval is not None and product.interval is not None and product.interval > wanted_interval:
            continue
        sizes, ok = {}, True
        for url, _name in product.obs:
            present, size = _exists(url, opener)
            if not present:
                ok = False
                break
            sizes[url] = size
        if not ok:
            continue
        navs = []
        for group in (product.nav_groups or [product.nav]):
            for url, name in group:
                present, size = _exists(url, opener)
                if present:
                    navs.append((url, name))
                    sizes[url] = size
            if navs:
                break
        if not navs:
            continue
        product.nav = navs
        product.nav_groups = None
        product.sizes = sizes
        found.append(product)
    return found


_LONG_STAMP = re.compile(r"_(\d{4})(\d{3})\d{4}_")
_SHORT_STAMP = re.compile(r"^\w{4}(\d{3})[0-9a-z]\.(\d{2})[a-z](?:\.\w+)?$", re.I)


def day_of_file(name):
    """UTC day (a datetime at 00:00) a RINEX archive name belongs to, from its long or short name; None when the name says nothing."""
    try:
        found = _LONG_STAMP.search(name)
        if found:
            return datetime.datetime(int(found.group(1)), 1, 1) + datetime.timedelta(days=int(found.group(2)) - 1)
        found = _SHORT_STAMP.match(name)
        if found:
            year = int(found.group(2))
            return datetime.datetime(2000 + year if year < 80 else 1900 + year, 1, 1) + datetime.timedelta(days=int(found.group(1)) - 1)
    except ValueError:
        pass
    return None


def archive_problem(path, name=None):
    """None when `path` looks like what its name says (gzip, Unix compress, RINEX or Compact RINEX), else a sentence for the log.

    A captive portal, a proxy or an error page often answers HTTP 200 with an HTML body: it must never be cached as RINEX."""
    name = (name or os.path.basename(path)).lower()
    try:
        with open(path, "rb") as handle:
            head = handle.read(160)
    except OSError as exc:
        return "unreadable (%s)" % exc
    if not head:
        return "empty"
    if name.endswith(".gz"):
        return None if head[:2] == b"\x1f\x8b" else "the server did not send a gzip file"
    if name.endswith(".z"):
        return None if head[:2] == b"\x1f\x9d" else "the server did not send a compressed file"
    if b"RINEX VERSION / TYPE" in head or b"COMPACT RINEX" in head:
        return None
    return "the server did not send a RINEX file"


def _cache_ok(target, name, size, now=None):
    """Whether the file already in the cache can be used. `size` = what the server announced for it (None when it did not say)."""
    try:
        have = os.path.getsize(target)
    except OSError:
        return False
    if have == 0 or archive_problem(target, name):
        return False
    if size:
        return have == size                       # a file grown on the server (today's broadcast file) or cut short is fetched again
    day = day_of_file(name)
    if day is not None:
        settled = (day + datetime.timedelta(seconds=SETTLE_SECONDS) - datetime.datetime(1970, 1, 1)).total_seconds()
        if os.path.getmtime(target) < settled:    # cached before its day was over: it may be incomplete and nobody can tell
            return False
    return True


_pruned = set()
_prune_lock = threading.Lock()
CACHE_MARKER = ".aeroppk-cache"       # written by the plugin when it creates its own cache; pruning needs it


def _own_cache_root():
    return os.path.join(cache_dir(), "rinex")


def _same_path(a, b):
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _is_link(path_or_entry):
    """True for a symbolic link, a junction or any other reparse point: pruning never goes through those."""
    try:
        st = os.lstat(path_or_entry if isinstance(path_or_entry, str) else path_or_entry.path)
    except OSError:
        return True
    if stat_module.S_ISLNK(st.st_mode):
        return True
    return bool(getattr(st, "st_file_attributes", 0) & 0x400)      # FILE_ATTRIBUTE_REPARSE_POINT (junctions)


def ensure_cache_marker(root=None):
    """Create the plugin's own cache folder and the marker file that says it is ours. Only the plugin's cache gets one."""
    own = _own_cache_root()
    root = root or own
    if not _same_path(root, own):
        return False
    try:
        os.makedirs(own, exist_ok=True)
        marker = os.path.join(own, CACHE_MARKER)
        if not os.path.isfile(marker):
            with open(marker, "w") as fh:
                fh.write("Aero-PPK download cache. Files in this folder may be deleted to keep it small." + chr(10))
        return True
    except OSError:
        return False


def _walk_files(root):
    """Regular files under `root`, never entering a link or a junction (and never listing one as a file)."""
    stack = [root]
    while stack:
        folder = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for entry in entries:
            if _is_link(entry):
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(entry.path)
                elif entry.is_file(follow_symlinks=False):
                    yield entry.path
            except OSError:
                continue


def prune_cache(root=None, max_bytes=None, part_age=3600.0, now=None):
    """Housekeeping of the download cache: abandoned `.part` files go, then the oldest files while the folder is over the cap.

    Only the plugin's own cache folder (`<settings>/AeroPPK/rinex`) that carries the plugin's marker file is ever pruned; any other
    root raises ValueError and nothing is touched. Links and junctions are never followed."""
    own = _own_cache_root()
    root = root or own
    if not _same_path(root, own):
        raise ValueError("refusing to prune %r: it is not the Aero-PPK cache" % (root,))
    if _is_link(own) or not os.path.isfile(os.path.join(own, CACHE_MARKER)):
        raise ValueError("refusing to prune %r: no Aero-PPK cache marker" % (root,))
    now = now if now is not None else time.time()
    files, total = [], 0
    for path in _walk_files(own):
        name = os.path.basename(path)
        if name == CACHE_MARKER:
            continue
        try:
            st = os.lstat(path)
        except OSError:
            continue
        if name.endswith(".part") and now - st.st_mtime > part_age:
            try:
                os.remove(path)
            except OSError:
                pass
            continue
        files.append((st.st_mtime, st.st_size, path))
        total += st.st_size
    cap = CACHE_CAP_BYTES if max_bytes is None else max_bytes
    for _mtime, size, path in sorted(files):
        if total <= cap:
            break
        try:
            os.remove(path)
            total -= size
        except OSError:
            pass
    return total


def _prune_once(folder):
    """Housekeeping of the plugin's own cache `folder` belongs to (`<cache>/rinex/<network>/<station>`), once per run."""
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(folder)))
        own = _own_cache_root()
        if not _same_path(root, own):                      # a folder given by the caller outside the plugin's cache is none of our business
            return
        key = os.path.normcase(os.path.realpath(own))
        with _prune_lock:
            if key in _pruned:
                return
            _pruned.add(key)
        ensure_cache_marker(own)
        prune_cache(own)
    except Exception:                                 # housekeeping never blocks a download
        pass


def fetch_product(provider_id, station, product, folder=None, progress=None, cancelled=None, opener=None, workers=DOWNLOAD_WORKERS):
    """Download the files of a product that are not already cached. Returns ([obs paths], [nav paths]) of downloaded archives.

    A cached file is used only when it looks like what its name says, has the size the server announced and, when the server did not
    announce one, is old enough to be final. Otherwise it is downloaded again. A downloaded file that is not an archive (a captive portal
    page) is deleted and refused. Up to `workers` files are fetched at once; `progress(i, n, done, total)` is the overall progress,
    (i + done / total) / n."""
    folder = folder or os.path.join(cache_dir(), "rinex", provider_id, station.code)
    _prune_once(folder)
    todo = [(u, n, True) for u, n in product.obs] + [(u, n, False) for u, n in product.nav]
    fractions = [0.0] * len(todo)
    lock = threading.Lock()
    failed = threading.Event()
    gone = lambda: failed.is_set() or (cancelled is not None and cancelled())

    def report(index, done, total):
        if progress is None:
            return
        with lock:
            fractions[index] = (done / float(total)) if total else 0.0
            value = sum(fractions)
        progress(int(value), len(todo), value - int(value), 1.0)

    def one(index):
        url, name, _is_obs = todo[index]
        target = os.path.join(folder, name)
        if _cache_ok(target, name, product.sizes.get(url)):
            report(index, 1, 1)
            return target
        if os.path.exists(target):
            os.remove(target)
        net.download(url, target, progress=lambda d, t: report(index, d, t), cancelled=gone, opener=opener,
                     validate=lambda path, name=name: archive_problem(path, name))
        report(index, 1, 1)
        return target

    paths = [None] * len(todo)
    if len(todo) <= 1 or workers <= 1:
        for index in range(len(todo)):
            paths[index] = one(index)
    else:
        with ThreadPoolExecutor(max_workers=min(workers, len(todo))) as pool:
            futures = [pool.submit(one, index) for index in range(len(todo))]
            errors = []
            for index, future in enumerate(futures):
                try:
                    paths[index] = future.result()
                except BaseException as exc:          # the first real failure is reported; the others stop
                    failed.set()
                    errors.append(exc)
            if errors:
                real = [e for e in errors if not str(e).startswith("Download stopped")]
                raise (real or errors)[0]
    obs_paths = [p for p, item in zip(paths, todo) if item[2]]
    nav_paths = [p for p, item in zip(paths, todo) if not item[2]]
    return obs_paths, nav_paths


def cache_dir():
    from .... import config
    return os.path.join(config.settings_dir(), CACHE_NAME)


class Provider(object):
    """A network of base stations. Subclasses (or instances of `archive.Rinex3Provider`) give `products`."""

    id = ""
    name = ""                 # long name, for the report
    short = ""                # a few letters, for rows and the map
    color = "#888888"         # marker colour on the map
    licence = ""              # licence and credit line, kept in the report
    licence_short = ""        # a few words, for a report that mixes several networks
    site = ""                 # server shown in the report
    frame_group = ""          # frames of one group are mixed without a warning (RGF93 v2b and ETRF2000 are both ETRS89)
    frame_label = ""          # what the positions are expressed in
    frame_short = ""
    snapshot = ""             # file name of the station snapshot, next to this module
    snapshot_source = ""

    def __init__(self):
        self._stations = None
        self._date = ""

    # -- catalogue --------------------------------------------------------------------------------

    def snapshot_path(self):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), self.snapshot)

    def station_from_dict(self, item):
        return _station.from_dict(item, self.id, self.frame_group)

    def load_snapshot(self):
        with open(self.snapshot_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return [self.station_from_dict(item) for item in data["stations"]], data.get("date", "")

    def load_catalog(self):
        """(stations, date text). Read once per run."""
        if self._stations is None:
            self._stations, self._date = self.load_snapshot()
        return self._stations, self._date

    # -- files ------------------------------------------------------------------------------------

    def products(self, station, start_gps, end_gps, margin_s=60.0):
        raise NotImplementedError

    def probe(self, station, start_gps, end_gps, wanted_interval=None, opener=None, cancelled=None):
        return probe_products(self.products(station, start_gps, end_gps), wanted_interval, opener, cancelled)

    def fetch(self, station, product, folder=None, progress=None, cancelled=None, opener=None):
        return fetch_product(self.id, station, product, folder, progress, cancelled, opener)

    def info(self):
        """What the report and the window say about the network."""
        return {"id": self.id, "name": self.name, "short": self.short, "licence": self.licence, "licence_short": self.licence_short,
                "url": self.site, "frame": self.frame_label}
