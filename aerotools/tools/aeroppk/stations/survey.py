"""What a station can offer for a given flight window: which files exist, how finely sampled, which satellite systems.

One `Offer` per station. `survey` asks the server (HEAD requests + one 32 KB range read), so it is slow-ish and must run in a
worker thread. Nothing is downloaded beyond that.
"""

from concurrent.futures import ThreadPoolExecutor

from . import catalog, inspect as head, net, rgp


class Offer(object):
    def __init__(self, station):
        self.station = station
        self.products = []          # rgp.Product, best first
        self.info = {}              # parse_head() of the best product (systems, receiver, antenna, interval)
        self.error = ""
        self.problem = ""           # key of a reason the files cannot be used (see inspect.problem)

    @property
    def best(self):
        return self.products[0] if self.products else None

    @property
    def interval(self):
        return self.best.interval if self.best else None

    @property
    def systems(self):
        return self.info.get("systems", [])

    def available(self):
        return self.best is not None


def survey_station(station, window, opener=None, cancelled=None):
    offer = Offer(station)
    try:
        provider = catalog.provider_of(station) or rgp.PROVIDER
        offer.products = provider.probe(station, window.start, window.end, opener=opener, cancelled=cancelled)
        if offer.best is not None:
            offer.info = head.inspect_product(offer.best, opener=opener)
            offer.problem = head.problem(offer.info)
            if offer.problem:                                           # the files exist but cannot be decoded: not a station to offer
                offer.products = []
                return offer
            if offer.best.interval is None and offer.info.get("interval"):
                offer.best.interval = offer.info["interval"]            # a network whose files all have their own rate
    except net.NetError as exc:
        offer.error = str(exc)
    return offer


def survey(stations, window, progress=None, cancelled=None, workers=6, opener=None):
    """Offers for every station (same order). `progress(done, total)` is called as the answers come in."""
    results = [None] * len(stations)
    done = [0]

    def work(index):
        if cancelled is not None and cancelled():
            return
        results[index] = survey_station(stations[index], window, opener=opener, cancelled=cancelled)
        done[0] += 1
        if progress:
            progress(done[0], len(stations))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(work, range(len(stations))))
    return [r if r is not None else Offer(s) for r, s in zip(results, stations)]
