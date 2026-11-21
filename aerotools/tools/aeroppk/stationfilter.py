"""Choosing stations: filters and the automatic choice. Pure functions, tested without Qt."""

SYSTEM_NAMES = (("G", "GPS"), ("R", "GLONASS"), ("E", "Galileo"), ("C", "BeiDou"))


def matches(offer, sampling_1s_only=False, required_systems=""):
    """Does a surveyed offer satisfy the user's filters? A station without data never matches."""
    if offer is None or not offer.available():
        return False
    if sampling_1s_only and (offer.interval is None or offer.interval > 1.0):
        return False
    have = set(offer.systems)
    return all(letter in have for letter in required_systems)


def auto_pick(offers, count, one_s_only=False):
    """The nearest stations that have data, preferring 1 s data and a spread of directions is not tried: the nearest win.

    Returns station codes. Offers with data every second come first, then coarser ones (not with `one_s_only`), each group nearest first.
    """
    usable = [o for o in offers if matches(o, one_s_only)]
    usable.sort(key=lambda o: (0 if (o.interval or 99) <= 1.0 else 1, o.station.distance_m or 0.0))
    return [o.station.code for o in usable[:count]]


def candidates(stations, offers, one_s_only=False):
    """The stations of a list (nearest first) that may still be a choice: not asked yet, asked and unreachable (unknown), or with data
    (every second when `one_s_only`). A station known to have no data that day is out."""
    out = []
    for station in stations:
        offer = offers.get(station.code)
        if offer is None or (not offer.available() and offer.error):
            out.append(station)
        elif matches(offer, one_s_only):
            out.append(station)
    return out


def next_to_ask(stations, offers, one_s_only, wanted):
    """The stations to ask the servers about next, nearest first: none once `wanted` of those that fit are known (a farther station
    cannot beat them), else every station not asked yet."""
    known = [s for s in stations if s.code in offers and matches(offers[s.code], one_s_only) and (offers[s.code].interval or 99) <= 1.0]
    if len(known) >= wanted:
        return []
    return [s for s in stations if s.code not in offers]


def data_text(offer):
    """(rate seconds, kind key) of the best product, or None when the station has nothing that day."""
    if offer is None or not offer.available():
        return None
    return offer.interval, ("fine" if (offer.interval or 99) <= 1.0 else "coarse")


def enabled_networks(choice, counts):
    """The networks whose stations are shown. `choice` is the list the user ticked (None = nothing chosen yet), `counts` maps a network
    id to the number of its stations within the search radius. By default every network that has a station there is on; when none
    has, every network stays on so that the list is not empty for a reason the user cannot see."""
    if choice is not None:
        return [pid for pid in counts if pid in choice]
    covering = [pid for pid, n in counts.items() if n > 0]
    return covering or list(counts)
