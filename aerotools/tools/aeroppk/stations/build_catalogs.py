"""Make the station snapshots of the networks from their own public coordinate files (needs the network; run by hand when refreshing).

    python -m aerotools.tools.aeroppk.stations.build_catalogs [igs] [epn] [noaa] [ga]

Each snapshot is a JSON file next to this module (`igs_stations.json`...) with the date, the source, the licence and the stations. The
sources are the networks' own solutions, never a third-party compilation:

  igs   IGc20 (positions and velocities in IGS20 of the IGS reference stations) + the open solution numbers of `soln_IGc20.snx`,
        https://files.igs.org/pub/station/coord/IGc20/, kept when the station has a daily file on the BKG mirror;
  epn   EUREF reference frame solution in ETRF2000, https://epncb.oma.be/pub/product/referenceframe/ (latest release), kept when the
        station has a daily file on the BKG mirror and a solution that is current;
  noaa  one NGS coordinate page per station, NAD 83 (2011) epoch 2010.0, https://noaa-cors-pds.s3.amazonaws.com/coord/coord_20/;
  ga    two APREF weekly SINEX solutions (positions in IGS20, velocities from their difference), Australian stations that have a
        daily file in the GA bucket.
"""

import datetime
import json
import os
import re
import sys
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor

from . import epn, ga, igs, net, noaa, sources
from .station import Station

HERE = os.path.dirname(os.path.abspath(__file__))


def fetch(url, length=None, timeout=120):
    request = urllib.request.Request(url, headers={"User-Agent": net.USER_AGENT})
    if length:
        request.add_header("Range", "bytes=0-%d" % (length - 1))
    with urllib.request.urlopen(request, timeout=timeout) as answer:
        return answer.read(length) if length else answer.read()


def fetch_text(url, **kwargs):
    return fetch(url, **kwargs).decode("utf-8", "replace")


def write(provider, stations, source, frame):
    items = [s.to_dict() for s in sorted(stations, key=lambda s: s.code)]
    payload = {"date": datetime.date.today().isoformat(), "source": source, "license": provider.licence, "frame": frame, "stations": items}
    path = provider.snapshot_path()
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
    print("%s: %d stations -> %s" % (provider.id, len(items), path))


def recent_day(days_back=4):
    return datetime.date.today() - datetime.timedelta(days=days_back)


def day_listing(base_url, day, kind_regex):
    """{long id: source letter} of a directory page of the BKG mirror."""
    url = "%s/%04d/%03d/" % (base_url, day.year, day.timetuple().tm_yday)
    return sources.parse_listing(fetch_text(url), kind_regex)


def make_station(code, long_id, name, xyz, vel, epoch, provider, country="", tag="R"):
    lat, lon, h = sources.to_geographic(xyz)
    return Station(code, long_id, name, lat, lon, h, None, None, "", country, provider=provider.id, frame=provider.frame_group,
                   xyz=xyz, vel=vel, epoch=epoch, tag=tag)


def build_igs():
    base = igs.ROOT + "/obs"
    day = recent_day()
    listing = day_listing(base, day, r"_01D_30S_MO\.crx\.gz")
    ssc = fetch_text("https://files.igs.org/pub/station/coord/IGc20/IGc20.ssc").splitlines()
    soln = fetch_text("https://files.igs.org/pub/station/coord/IGc20/soln_IGc20.snx").splitlines()
    estimates = sources.parse_sinex_estimates(ssc)
    names = sources.parse_sinex_sites(ssc)
    open_soln = sources.parse_open_solutions(soln)
    by_code = {}
    for long_id, tag in listing.items():
        by_code.setdefault(long_id[:4], (long_id, tag))
    stations = []
    for code, (long_id, tag) in sorted(by_code.items()):
        key = (code, open_soln.get(code, -1))
        item = estimates.get(key)
        if item is None or item["vel"] is None:
            continue
        stations.append(make_station(code, long_id, names.get(code, {}).get("name", ""), item["pos"], item["vel"], item["epoch"],
                                     igs.PROVIDER, long_id[6:9], tag))
    write(igs.PROVIDER, stations, igs.PROVIDER.snapshot_source, igs.PROVIDER.frame_label)


def build_epn():
    base = epn.ROOT + "/obs"
    day = recent_day()
    listing = day_listing(base, day, r"_01D_30S_MO\.crx\.gz")
    page = fetch_text("https://epncb.oma.be/pub/product/referenceframe/latest/")
    match = re.search(r'href="([^"]*EUR0OPSSNX_[^"]*_SOL_ETRF2000\.SSC)"', page)
    url = epn.PROVIDER.snapshot_source if not match else "https://epncb.oma.be/pub/product/referenceframe/latest/" + match.group(1)
    ssc = fetch_text(url).splitlines()
    short = fetch_text(url.replace("_ETRF2000.SSC", "_ETRF2000_short.SSC")).splitlines()
    names = sources.parse_epn_names(fetch_text(igs.ROOT.replace("/IGS", "/EUREF") + "/station/general/euref_20.snx").splitlines())
    last_year = None
    for line in ssc[:6]:
        found = re.search(r"GPSWEEKS \[ (\d+) - (\d+) \]", line)
        if found:
            last_year = 1980.0 + (int(found.group(2)) * 7 + 5) / 365.25 + 0.0
    solutions = sources.parse_epn_ssc(ssc)
    for code, items in sources.parse_epn_ssc(short).items():
        solutions.setdefault(code, items)
    latest = sources.latest_epn_solutions(solutions, last_year or 2026.0, 0.5)
    by_code = {}
    for long_id, tag in listing.items():
        by_code.setdefault(long_id[:4], (long_id, tag))
    stations = []
    for code, item in sorted(latest.items()):
        if code not in by_code:
            continue
        long_id, tag = by_code[code]
        stations.append(make_station(code, long_id, names.get(code, ""), item["pos"], item["vel"], item["epoch"], epn.PROVIDER,
                                     long_id[6:9], tag))
    write(epn.PROVIDER, stations, url, epn.PROVIDER.frame_label)


def build_noaa():
    day = recent_day()
    doy = day.timetuple().tm_yday
    listing = fetch_text("%s/%04d/%03d/%04d.%03d.files.list" % (noaa.ROOT, day.year, doy, day.year, doy), timeout=300)
    codes = sources.parse_noaa_stations(listing, day.year, doy)
    base = "https://noaa-cors-pds.s3.amazonaws.com/coord/coord_20/%s_20.coord.txt"

    def one(code):
        try:
            return sources.parse_noaa_coord(fetch_text(base % code, timeout=60))
        except OSError:
            return None
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(one, codes))
    stations = []
    for item in results:
        if item is None:
            continue
        code = item["code"].upper()                        # one spelling for every network; the file names are lower case (noaa.py)
        stations.append(Station(code, code + "00USA", item["name"], item["lat"], item["lon"], item["h"], None, None, "",
                                item["region"], provider=noaa.PROVIDER.id, frame=noaa.PROVIDER.frame_group, xyz=item["xyz"],
                                epoch=item["epoch"]))
    write(noaa.PROVIDER, stations, noaa.PROVIDER.snapshot_source, noaa.PROVIDER.frame_label)


def s3_keys(bucket_url, prefix):
    """Every key under a prefix of a public bucket (ListObjectsV2, 1000 per page)."""
    keys, token = [], None
    while True:
        url = "%s/?list-type=2&prefix=%s" % (bucket_url, prefix) + ("&continuation-token=" + urllib.request.quote(token, safe="") if token else "")
        text = fetch_text(url)
        keys += re.findall(r"<Key>([^<]+)</Key>", text)
        found = re.search(r"<NextContinuationToken>([^<]+)</NextContinuationToken>", text)
        if not found:
            return keys
        token = found.group(1)


def sinex_head(url):
    """The beginning of a gzipped SINEX file up to the end of its estimates (the covariance matrix after it is huge and not needed)."""
    for length in (4000000, 12000000, 40000000):
        stream = zlib.decompressobj(16 + zlib.MAX_WBITS)
        try:
            text = stream.decompress(fetch(url, length=length, timeout=300)).decode("ascii", "replace")
        except zlib.error:
            continue
        if "-SOLUTION/ESTIMATE" in text:
            return text.splitlines()
    raise OSError("the estimates of %s were not found" % url)


def last_positions(estimates):
    """{code: {"pos", "epoch"}} of the highest solution number of each station of a SINEX estimate table."""
    out, best = {}, {}
    for (code, soln), value in estimates.items():
        if soln >= best.get(code, -1):
            best[code] = soln
            out[code] = {"pos": value["pos"], "epoch": value["epoch"]}
    return out


def read_snapshot(provider):
    """{code: station dict} of a snapshot already made, {} when there is none."""
    try:
        with open(provider.snapshot_path(), "r", encoding="utf-8") as handle:
            return dict((item["code"], item) for item in json.load(handle)["stations"] if item.get("xyz") and item.get("vel"))
    except (OSError, ValueError):
        return {}


def build_ga():
    base = "https://ga-gnss-products-v1.s3.amazonaws.com/public/%d/AUS%d7.SNX.gz"
    week_now, week_before = 2440, 2336
    lines = sinex_head(base % (week_now, week_now))
    second = last_positions(sources.parse_sinex_estimates(lines))
    first = last_positions(sources.parse_sinex_estimates(sinex_head(base % (week_before, week_before))))
    day = recent_day()
    keys = s3_keys("https://ga-gnss-data-rinex-v1.s3.amazonaws.com", "public/daily/%04d/%03d/" % (day.year, day.timetuple().tm_yday))
    listing = sources.parse_listing("\n".join(keys), r"_01D_30S_MO\.crx\.gz")
    wanted = dict((long_id[:4], (long_id, tag)) for long_id, tag in listing.items() if long_id.endswith("AUS") and long_id[:4] in second)
    velocities = sources.velocities_from_two_solutions(first, dict((c, second[c]) for c in wanted))
    core = read_snapshot(igs.PROVIDER)
    site_text = "\n".join(sources.read_block(lines, "SITE/ID"))
    stations = []
    for code, (long_id, tag) in sorted(wanted.items()):
        state = re.search(long_id + r"\s+([A-Z]{2,3})\b", site_text)
        name = state.group(1) if state else ""
        if code in core:                                            # an IGS reference station: its own solution is better than a week
            station = core[code]
            stations.append(make_station(code, long_id, name, station["xyz"], station["vel"], station["epoch"], ga.PROVIDER, "AUS", tag))
        elif code in velocities:
            stations.append(make_station(code, long_id, name, second[code]["pos"], velocities[code], second[code]["epoch"], ga.PROVIDER,
                                         "AUS", tag))
    write(ga.PROVIDER, stations, ga.PROVIDER.snapshot_source, ga.PROVIDER.frame_label)


BUILDERS = {"igs": build_igs, "epn": build_epn, "noaa": build_noaa, "ga": build_ga}


def main(argv):
    wanted = argv or sorted(BUILDERS)
    for name in wanted:
        if name not in BUILDERS:
            print("unknown network: %s (known: %s)" % (name, ", ".join(sorted(BUILDERS))))
            return 2
    for name in wanted:
        BUILDERS[name]()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
