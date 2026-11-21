"""The GPS ionosphere (Klobuchar) parameters RTKLIB reads from the header of a navigation file, and what to do when none has them.

Measured (a 225 photo flight, station CAUR, a Geo++ conversion whose navigation files have no `ION ALPHA` line): without the parameters
RTKLIB stays float for the whole flight; with only the `ION ALPHA` line of the same day (or `GPSA` in RINEX 3) every photo is fixed. Zeros
do not work, values of another year (about 40 % smaller) do not work, values 20 % off still do. So the values must come from the day of
the flight. The parameters go into a small stub navigation file written in the run folder (a header and no ephemeris); a downloaded file
is never modified.
"""

import gzip
import os

from . import gnsstime
from .stations import provider as _provider
from .stations import igs, net, rgp

HEADER_LINES = 400                  # the broadcast file of the IGS has a long header: the GPS lines are not the first ones
WARNING = "no_ionosphere"


def _open(path):
    return gzip.open(path, "rb") if str(path).lower().endswith(".gz") else open(path, "rb")


def _numbers(text):
    """Four floats from the value part of a header line (fixed columns or blanks, `D` or `E` exponents), or None."""
    text = text.replace("D", "E").replace("d", "e")
    try:
        values = [float(v) for v in text.split()]
    except ValueError:
        return None
    return tuple(values) if len(values) == 4 else None


def klobuchar(path):
    """(alpha, beta) of the GPS ionosphere header lines of a navigation file (tuples of 4 floats), or None.

    Both RINEX forms are read: `ION ALPHA` / `ION BETA` (version 2) and `GPSA` / `GPSB` with `IONOSPHERIC CORR` (version 3). A file whose
    alpha is missing or all zero has none (RTKLIB needs alpha; beta alone does nothing). beta is None when only alpha is there."""
    alpha = beta = None
    try:
        with _open(path) as handle:
            for _ in range(HEADER_LINES):
                line = handle.readline().decode("latin-1")
                if not line or "END OF HEADER" in line[60:]:
                    break
                label = line[60:].strip()
                if label == "ION ALPHA":
                    alpha = alpha or _numbers(line[:60])
                elif label == "ION BETA":
                    beta = beta or _numbers(line[:60])
                elif label == "IONOSPHERIC CORR":
                    if line.startswith("GPSA"):
                        alpha = alpha or _numbers(line[4:60])
                    elif line.startswith("GPSB"):
                        beta = beta or _numbers(line[4:60])
    except (OSError, EOFError):
        return None
    if alpha is None or not any(alpha):
        return None
    return alpha, beta


def has_klobuchar(paths):
    return any(klobuchar(p) is not None for p in paths if p)


def first_klobuchar(paths):
    for path in paths:
        found = klobuchar(path) if path else None
        if found is not None:
            return found
    return None


def write_stub(path, alpha, beta=None):
    """A RINEX 2 navigation file with the two header lines and no ephemeris."""
    def line(values, label):
        return "  " + "".join("%12.4E" % v for v in values) + " " * 10 + label.ljust(20) + "\n"
    text = "%9.2f%11s%-20s%-20s%-20s\n" % (2.10, "", "N: GPS NAV DATA", "", "RINEX VERSION / TYPE")
    text += line(alpha, "ION ALPHA")
    if beta is not None:
        text += line(beta, "ION BETA")
    text += " " * 60 + "END OF HEADER".ljust(20) + "\n"
    with open(path, "w", encoding="ascii", newline="\n") as handle:
        handle.write(text)
    return path


def flight_days(window):
    """The UTC days (datetimes at 00:00) the flight window touches."""
    start, end = _provider.window_utc(window.start, window.end, 0.0)
    return _provider.utc_slots(start.replace(hour=0, minute=0), end, 1440)


def _is_nav_name(name):
    name = name.lower()
    return (name.endswith((".rnx", ".rnx.gz")) and "_mn" in name) or name.rsplit(".", 1)[-1][-1:] in ("n", "p")


def cached_navs(days, root=None):
    """Navigation files already unpacked for those days by earlier downloads, whatever the station (the work folders of the cache)."""
    root = root or os.path.join(rgp.cache_dir(), "work", "bases")
    wanted = set(d.date() for d in days)
    found = []
    try:
        folders = sorted(os.path.join(root, name) for name in os.listdir(root))
    except OSError:
        return found
    for folder in folders:
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        for name in names:
            day = _provider.day_of_file(name)
            if day is not None and day.date() in wanted and _is_nav_name(name):
                found.append(os.path.join(folder, name))
    return found


def fetch_broadcast(day, folder=None, cancelled=None, opener=None):
    """Download (cached) the worldwide broadcast file of the day from the IGS mirror: its path, or None when it cannot be had.

    It is only used for its header: the file is large, mixed, and never given to the solver."""
    url, name = igs.broadcast(day)
    folder = folder or os.path.join(rgp.cache_dir(), "rinex", "igs", "BRDC")
    target = os.path.join(folder, name)
    if _provider._cache_ok(target, name, None):
        return target
    try:
        net.download(url, target, cancelled=cancelled, opener=opener, retries=1, timeout=net.PROBE_TIMEOUT,
                     validate=lambda path: _provider.archive_problem(path, name))
    except net.NetError:
        return None
    return target


def stub_for(navs, window, work_dir, others=(), fetch=None, cache_root=None):
    """What to add to `navs` so that RTKLIB has the ionosphere parameters. Returns (stub path or None, warning key or None).

    Nothing is added when a file of `navs` already has them. Otherwise the parameters are taken, in this order, from (a) another
    navigation file of the run (`others`: the files of the other bases) or one in the download cache, for the day of the flight, then
    (b) `fetch(day)` = the broadcast file of that day, when given. When nothing is found the warning key comes back instead."""
    if has_klobuchar(navs):
        return None, None
    days = flight_days(window)
    stub = os.path.join(work_dir, "ionosphere_%04d%03d.nav" % (days[0].year, gnsstime.day_of_year(days[0])))
    values = klobuchar(stub)                                  # a stub made for this day by an earlier base
    if not values:
        values = first_klobuchar(list(others) + cached_navs(days, cache_root))
    if not values and fetch is not None:
        path = fetch(days[0])
        values = klobuchar(path) if path else None
    if not values:
        return None, WARNING
    os.makedirs(work_dir, exist_ok=True)
    return write_stub(stub, values[0], values[1]), None
