"""The time axis of step 2: the flight, the window asked of the base servers, and what each base covers. Pure logic, no Qt.

One row per dataset on a horizontal axis with hour ticks (the owner's wish: see at a glance whether a base covers the flight).
Times are UTC datetimes (the files are in GPS time, converted by the caller with `gnsstime.gps_to_utc`).
"""

import datetime

FLIGHT, BASE, PLANNED = "flight", "base", "planned"


class Row(object):
    """One bar: `kind` is FLIGHT, BASE (data in hand) or PLANNED (what will be asked, nothing downloaded yet)."""

    def __init__(self, key, label, start, end, kind, ok=True):
        self.key, self.label, self.start, self.end, self.kind = key, label, start, end, kind
        self.ok = ok                       # False: the bar does not cover the flight (drawn in the warning colour)


def covers(start, end, flight):
    return start is not None and end is not None and start <= flight[0] and end >= flight[1]


def build_rows(flight_span, margin_hours, bases, flight_label=""):
    """Rows for the axis. `flight_span` = (start, end) UTC, `bases` = list of (code, first, last) where `first` / `last` are None while
    nothing is downloaded (the row then shows the window that will be asked)."""
    rows = [Row("flight", flight_label, flight_span[0], flight_span[1], FLIGHT)]
    pad = datetime.timedelta(hours=float(margin_hours))
    for code, first, last in bases:
        if first is None or last is None:
            rows.append(Row(code, code, flight_span[0] - pad, flight_span[1] + pad, PLANNED))
        else:
            rows.append(Row(code, code, first, last, BASE, covers(first, last, flight_span)))
    return rows


def axis_range(rows, margin_hours):
    """(start, end) of the axis: all the bars and the download window, rounded out to whole hours."""
    flight = next((r for r in rows if r.kind == FLIGHT), None)
    spans = [(r.start, r.end) for r in rows]
    if flight is not None:
        pad = datetime.timedelta(hours=float(margin_hours))
        spans.append((flight.start - pad, flight.end + pad))
    start = min(s for s, _e in spans)
    end = max(e for _s, e in spans)
    start = start.replace(minute=0, second=0, microsecond=0)
    if end.minute or end.second or end.microsecond:
        end = end.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(hours=1)
    return start, end


def ticks(start, end, max_ticks=8):
    """Hour marks (datetimes) between `start` and `end`, every 1, 2, 3, 6 or 12 hours so there are at most `max_ticks`."""
    hours = max(1, int(round((end - start).total_seconds() / 3600.0)))
    step = next((s for s in (1, 2, 3, 6, 12, 24) if hours / s <= max_ticks), 24)
    out, current = [], start
    while current <= end:
        out.append(current)
        current += datetime.timedelta(hours=step)
    return out


def fraction(moment, start, end):
    """Position of `moment` on the axis, 0 to 1 (clamped)."""
    span = (end - start).total_seconds()
    if span <= 0:
        return 0.0
    return max(0.0, min(1.0, (moment - start).total_seconds() / span))
