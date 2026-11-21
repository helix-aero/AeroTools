"""Aero-PPK base station networks, checked live: a real observation file and a real navigation file of one station of each network.

    python tests_qt\\run_qt_aeroppk_networks_live.py [network ids ...] [--days-back N]

NOT run by the test suite (it needs the network, takes minutes and downloads a few tens of MB). For every network (RGP, EPN, IGS, NOAA CORS,
Geoscience Australia, or the ones named) it picks a sample site, takes the nearest station of the snapshot that has data, then does what the
window does: the survey (what exists, the first bytes of the file), the download of the files around a short flight window with one hour of
margin, the unpacking with the embedded crx2rnx, the preparation (`rinex`, `rinexprep`), and a read of the result by the embedded convbin.
It prints PASS / FAIL lines, and a table at the end that docs/AEROPPK.md quotes. Downloads go to a temporary folder, the real settings are
never touched. Exit code 1 when a network fails.
"""

import datetime
import os
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import _isolation  # noqa: E402

GUARD = _isolation.Guard("aeroppk_nets_")

import numpy as np  # noqa: E402

from aerotools import config  # noqa: E402

GUARD.pin(config)
from aerotools.tools.aeroppk import gnsstime, pipeline, rtklib, runner  # noqa: E402
from aerotools.tools.aeroppk.stations import catalog, station as stn, survey  # noqa: E402

# a neutral sample site per network (latitude, longitude): somewhere the network has stations
SITES = {"rgp": (46.0, 4.5), "epn": (48.1, 11.6), "igs": (48.0, 10.0), "noaa": (39.0, -105.0), "ga": (-33.9, 151.2)}
FAILURES = []
TABLE = []


def check(name, condition, detail=""):
    print("{} {}{}".format("PASS" if condition else "FAIL", name, "  [{}]".format(detail) if detail else ""))
    if not condition:
        FAILURES.append(name)


def convbin_reads(programs, path, out_dir):
    """Does the embedded convbin read this RINEX file? Returns (ok, detail). It converts it to RINEX 3.04 in `out_dir`."""
    exe = programs.get("convbin")
    if not exe:
        return False, "convbin not found"
    target = os.path.join(out_dir, "convbin_check.obs")
    flags = 0x08000000 if os.name == "nt" else 0
    done = subprocess.run([exe, "-r", "rinex", "-v", "3.04", "-o", target, path], cwd=out_dir, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, creationflags=flags, timeout=900)
    text = (done.stdout + done.stderr).decode("ascii", "replace")
    ok = os.path.isfile(target) and os.path.getsize(target) > 1000
    counts = [part for part in text.split() if part.startswith("O=")]            # the epoch count convbin ends with
    return ok, "%s epochs read, %d bytes written" % (counts[-1][2:] if counts else "?", os.path.getsize(target) if os.path.isfile(target) else 0)


def run_network(provider, days_back, programs, work_root):
    site = SITES[provider.id]
    print("\n== %s  (%s, %s)" % (provider.short, provider.name, provider.frame_label))
    stations, date = provider.load_catalog()
    check("%s: the snapshot lists stations" % provider.id, len(stations) > 50, "%d, snapshot %s" % (len(stations), date))
    day = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).replace(hour=10, minute=0, second=0, microsecond=0) - datetime.timedelta(days=days_back)
    window = pipeline.Window(gnsstime.utc_to_gps(day), gnsstime.utc_to_gps(day + datetime.timedelta(minutes=25)))
    near = stn.nearby([s for s in stations if s.usable], site[0], site[1], 600, day.date())
    check("%s: stations around the sample site" % provider.id, len(near) > 0, "%d within 600 km" % len(near))
    offer = None
    for station in near[:12]:
        offer = survey.survey_station(station, runner.widened(window, 1))
        print("  survey %-6s %6.1f km  %s" % (station.code, station.distance_m / 1000.0,
                                               ", ".join("%s %s" % (p.key, len(p.obs)) for p in offer.products) or offer.error or "no data"))
        if offer.available():
            break
    if offer is None or not offer.available():
        check("%s: a station with data for %s" % (provider.id, day.date()), False)
        TABLE.append((provider.short, "-", "no data", "", "", "", ""))
        return
    station, best = offer.station, offer.best
    check("%s: survey of %s" % (provider.id, station.code), offer.available() and bool(offer.info.get("systems")),
          "%s, %s, systems %s, receiver %s" % (best.key, best.label, "".join(offer.systems), offer.info.get("receiver")))
    started = time.time()
    try:
        base = runner.fetch_base(station, window, 1, programs, work_root)
    except pipeline.PipelineError as exc:
        check("%s: download and preparation of %s" % (provider.id, station.code), False, str(exc))
        TABLE.append((provider.short, station.code, "failed", str(exc)[:60], "", "", ""))
        return
    seconds = time.time() - started
    info = base.info
    check("%s: files downloaded and unpacked" % provider.id, os.path.isfile(base.obs) and len(base.navs) >= 1,
          "%s + %d navigation file(s), %.0f s" % (os.path.basename(base.obs), len(base.navs), seconds))
    check("%s: the observation file is RINEX that the tool reads" % provider.id,
          info.version >= 2.0 and info.interval is not None and "G" in info.systems,
          "RINEX %.2f, %s s, %s, %s / %s" % (info.version, info.interval, "".join(info.systems), info.receiver, info.antenna))
    covers = info.first is not None and info.last is not None and info.first <= window.start and info.last >= window.end
    check("%s: the data cover the flight window" % provider.id, covers, "%s to %s GPS" % (info.first, info.last))
    check("%s: a navigation file has GPS ephemerides" % provider.id, any(_has_gps_nav(p) for p in base.navs),
          ", ".join(os.path.basename(p) for p in base.navs))
    shift = None
    if info.approx_xyz is not None:
        shift = float(np.linalg.norm(np.array(info.approx_xyz) - np.array(base.xyz)))
        print("  header position vs snapshot position (at the date): %.3f m" % shift)
    ok, detail = convbin_reads(programs, base.obs, os.path.dirname(base.obs))
    check("%s: convbin reads the file" % provider.id, ok, detail)
    TABLE.append((provider.short, station.code, best.key, "%s s" % ("%g" % info.interval), "".join(info.systems),
                  "%.0f s" % seconds, "-" if shift is None else "%.2f m" % shift))


def _has_gps_nav(path):
    try:
        with open(path, "rb") as handle:
            head = handle.read(200000).decode("ascii", "replace")
    except OSError:
        return False
    lines = head.splitlines()
    if not lines or "RINEX VERSION / TYPE" not in lines[0]:
        return False
    version = float(lines[0][:9])
    kind = lines[0][40:41]
    if version < 3.0:
        return kind in ("N", " ")                   # RINEX 2 `n` files are GPS, `g` files GLONASS (the file name decides)
    return kind in ("G", "M")


def main(argv):
    days_back = 4
    ids = []
    args = list(argv)
    while args:
        item = args.pop(0)
        if item == "--days-back":
            days_back = int(args.pop(0))
        else:
            ids.append(item)
    providers = [catalog.get(i) for i in (ids or [p.id for p in catalog.PROVIDERS])]
    programs = rtklib.find_programs()
    check("embedded programs found", bool(programs.get("crx2rnx")) and bool(programs.get("convbin")), str(sorted(programs)))
    work_root = tempfile.mkdtemp(prefix="aeroppk_nets_work_")
    for provider in providers:
        try:
            run_network(provider, days_back, programs, work_root)
        except Exception as exc:                                   # a failure of one network must not hide the others
            check("%s: ran without an exception" % provider.id, False, "%s: %s" % (type(exc).__name__, exc))
    print("\n%-6s %-6s %-14s %-8s %-8s %-8s %s" % ("net", "code", "product", "rate", "systems", "time", "header vs snapshot"))
    for row in TABLE:
        print("%-6s %-6s %-14s %-8s %-8s %-8s %s" % row)
    problem = GUARD.check()
    check("real settings untouched", problem is None, str(problem))
    print("downloads in", work_root)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
