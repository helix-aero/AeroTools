"""The networks Aero-PPK knows, and the one list of stations the map and the basket work with.

`PROVIDERS` is the order of preference: when the same physical station is offered by several enabled networks (BRUX is both IGS and
EPN), it is listed once, from the most regional network first, because a regional frame (ETRF2000, NAD 83) is what a local project
wants. Station codes are unique in the merged list (a clash between two different stations renames the later one `CODE~net`), so
the rest of the tool can keep using the code as a key.
"""

import copy

from .. import geo
from . import epn, ga, igs, noaa, rgp, station as _station

PROVIDERS = (rgp.PROVIDER, epn.PROVIDER, noaa.PROVIDER, ga.PROVIDER, igs.PROVIDER)
BY_ID = dict((p.id, p) for p in PROVIDERS)


def get(provider_id):
    """The provider of an id, or None (the user's own base is not a network)."""
    return BY_ID.get(provider_id)


def provider_of(station):
    return BY_ID.get(getattr(station, "provider", ""))


def short_of(station):
    p = provider_of(station)
    return p.short if p else ""


def frame_of(station):
    """(group, label) of the reference frame of a station; ("own", "") for the user's own base."""
    p = provider_of(station)
    return (p.frame_group, p.frame_label) if p else ("own", "")


def _same_station(a, b):
    return geo.distance_m(a.lat, a.lon, b.lat, b.lon) < 300.0


def merge(lists):
    """Merge station lists given in order of preference: a station found again in a later list (same code, less than 300 m apart) is
    dropped, a different station with the same code is renamed `CODE~net`."""
    out, by_code = [], {}
    for stations in lists:
        for station in stations:
            seen = by_code.get(station.code)
            if seen is None:
                by_code[station.code] = station
                out.append(station)
            elif _same_station(seen, station):
                continue
            else:
                station = copy.copy(station)                       # the list of the network is shared: never rename its station
                station.code = "%s~%s" % (station.code, station.provider)
                by_code[station.code] = station
                out.append(station)
    return out


def load(ids=None):
    """Every usable station of the networks `ids` (all when None), merged. The shipped snapshots are read once per run."""
    lists = []
    for provider in PROVIDERS:
        if ids is not None and provider.id not in ids:
            continue
        stations, _date = provider.load_catalog()
        lists.append([s for s in stations if s.usable])
    return merge(lists)


def counts(lat, lon, radius_km, day=None):
    """{network id: number of its stations within the radius around the site}. A station that two networks offer counts for the first one
    only (it is listed once, see `merge`), so a network whose every station there is also in a regional one counts zero."""
    out = dict((provider.id, 0) for provider in PROVIDERS)
    for station in _station.nearby(load(), lat, lon, radius_km, day):
        out[station.provider] += 1
    return out


def covering(lat, lon, radius_km, day=None):
    """Ids of the networks that have at least one station within the radius: what the window enables when nothing was chosen."""
    return [pid for pid, n in counts(lat, lon, radius_km, day).items() if n > 0]


def nearby(stations, lat, lon, radius_km, day=None):
    return _station.nearby(stations, lat, lon, radius_km, day)


def frame_groups(stations):
    """The distinct frame groups of some stations, in order of first use (the user's own base is `own`, never mixed with a network)."""
    out = []
    for station in stations:
        group = frame_of(station)[0]
        if group not in out:
            out.append(group)
    return out


def frames_mixed(stations):
    """True when the stations are expressed in different frames: averaging their results would mix frames."""
    groups = [g for g in frame_groups(stations) if g != "own"]
    return len(groups) > 1


def frames_text(stations, short=False):
    """The reference frame(s) of the stations as text: the label of each network once, joined by ` + `."""
    labels = []
    for station in stations:
        provider = provider_of(station)
        if provider is None:
            continue
        label = provider.frame_short if short else provider.frame_label
        if label not in labels:
            labels.append(label)
    return " + ".join(labels)


def networks_info(stations):
    """The networks the stations come from, for the report: list of dicts (id, name, short, licence, url, frame), each once."""
    out, seen = [], set()
    for station in stations:
        provider = provider_of(station)
        if provider is not None and provider.id not in seen:
            seen.add(provider.id)
            out.append(provider.info())
    return out


def source_of(stations):
    """The data source of a report: name, licence and address of the network (or of the networks, one line each when there are several)."""
    infos = networks_info(stations)
    if not infos:
        return {"name": rgp.NAME, "licence": rgp.LICENSE, "url": rgp.SERVER, "kind": "network", "networks": []}
    if len(infos) == 1:
        one = infos[0]
        return {"name": one["name"], "licence": one["licence"], "url": one["url"], "kind": "network", "networks": infos}
    return {"name": ", ".join(i["short"] for i in infos), "licence": "; ".join("%s: %s" % (i["short"], i["licence_short"]) for i in infos),
            "url": "; ".join(i["url"] for i in infos), "kind": "network", "networks": infos}
