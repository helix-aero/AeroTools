"""The basket of the base station (step 2): what is in it, what the row is doing. Pure logic, no Qt.

A click on a station of the map puts it in the basket, a right click in the basket takes it out. The basket holds one station (a new
choice replaces the old one).
Each row goes through `PENDING -> RUNNING -> DONE` (or `ERROR`) while the files are fetched. The calculation uses the bases that are
`DONE`, nearest first.
"""

from .stations import catalog

PENDING, RUNNING, DONE, ERROR = "pending", "running", "done", "error"
RADIUS_STEPS = (5, 10, 20, 30, 50, 100, 150, 200, 300)          # km, the stops of the search slider
DEFAULT_RADIUS = 50                  # single base: the stations around are listed and shown on the map
MARGINS_H = (1, 2)                   # hours of data fetched before and after the flight
DEFAULT_MARGIN_H = 1


def radius_from_slider(index):
    """Slider position -> kilometres (clamped)."""
    return RADIUS_STEPS[max(0, min(len(RADIUS_STEPS) - 1, int(index)))]


def slider_from_radius(km):
    """Kilometres -> the nearest stop of the slider."""
    return min(range(len(RADIUS_STEPS)), key=lambda i: abs(RADIUS_STEPS[i] - float(km)))


class Item(object):
    """One row of the basket."""

    def __init__(self, station):
        self.station = station
        self.state = PENDING
        self.error = ""              # sentence for the tooltip of the error mark
        self.info = {}               # rate (s), systems (letters), receiver, antenna, size (bytes), product label
        self.base = None             # pipeline.BaseData once downloaded

    @property
    def code(self):
        return self.station.code

    @property
    def distance_m(self):
        return self.station.distance_m or 0.0

    @property
    def network(self):
        """The network of the station: a provider of `stations.catalog`, or None."""
        return catalog.provider_of(self.station)


class Basket(object):
    def __init__(self, limit=1):
        self.items = []
        self.limit = limit           # 1: a new station replaces the one in the basket


    def __len__(self):
        return len(self.items)

    def codes(self):
        return [i.code for i in self.items]

    def get(self, code):
        for item in self.items:
            if item.code == code:
                return item
        return None

    def add(self, station):
        """Put a station in. False when it is already there or the basket is full (with a limit of one, the choice replaces the old one)."""
        if self.get(station.code) is not None:
            return False
        if self.limit == 1:
            self.items = [Item(station)]
            return True
        if len(self.items) >= self.limit:
            return False
        self.items.append(Item(station))
        return True

    def clear(self):
        self.items = []


    def remove(self, code):
        item = self.get(code)
        if item is None:
            return False
        self.items.remove(item)
        return True

    def toggle(self, station):
        """What a click on the map does. Returns True when the station is in the basket afterwards."""
        if self.get(station.code) is not None:
            self.remove(station.code)
            return False
        return self.add(station)

    def clear_results(self):
        """Forget what was downloaded (the flight or the margin changed): every row waits again."""
        for item in self.items:
            item.state, item.error, item.base = PENDING, "", None

    def todo(self):
        return [i for i in self.items if i.state in (PENDING, ERROR)]

    def done(self):
        return [i for i in self.items if i.state == DONE and i.base is not None]

    def used(self):
        """The bases the calculation takes: those downloaded, nearest first."""
        return sorted(self.done(), key=lambda i: i.distance_m)[:self.limit]

    def planned(self):
        """The rows the calculation will use once everything is downloaded: the ones that did not fail."""
        return sorted((i for i in self.items if i.state != ERROR), key=lambda i: i.distance_m)[:self.limit]

    def frames_mixed(self):
        """True when the bases the calculation will take are not in the same reference frame (see `stations.catalog`)."""
        return catalog.frames_mixed([i.station for i in self.planned()])

    def frames_text(self, short=False):
        return catalog.frames_text([i.station for i in self.planned()], short)

    def busy(self):
        return any(i.state == RUNNING for i in self.items)

    def can_continue(self):
        """Compute can start: a base is ready or can be downloaded, and nothing runs (the download is part of Compute)."""
        return bool(self.done() or self.todo()) and not self.busy()


def margin_seconds(hours):
    return 3600.0 * float(hours if hours in MARGINS_H else DEFAULT_MARGIN_H)


def rate_text(interval):
    """Data rate of a station in hertz as short text: 1 s -> '1 Hz', 30 s -> '1/30 Hz', None -> ''."""
    if not interval:
        return ""
    if interval <= 1.0:
        return "%g Hz" % round(1.0 / interval, 3)
    return "1/%g Hz" % interval
