"""GPS time helpers: week / time of week, calendar dates, GPS - UTC leap seconds.

Pure Python, no Qt, no Metashape.

Why it matters for PPK: the DJI `.MRK` file gives the exposure time as GPS week + seconds of week, and RINEX files are
written in GPS time (observations) or UTC (some navigation headers). GPS time does not have leap seconds, UTC does:
mixing them silently shifts every photo by 18 seconds (about 180 m at 10 m/s). Everything in this package keeps GPS time
as a plain `datetime` (naive, "GPS calendar time") and only converts to UTC at the edges, with `gps_to_utc`.
"""

import datetime

GPS_EPOCH = datetime.datetime(1980, 1, 6)
WEEK = 604800.0
DAY = 86400.0

# UTC dates at which GPS - UTC grew by one second (first day with the new value). Source: IERS Bulletin C list of leap
# seconds, GPS - UTC = TAI - UTC - 19 s. 18 s since 2017-01-01. If a new leap second is ever added, append it here
# (a test fails on purpose when the table is shorter than the date being converted, see LAST_KNOWN).
LEAP_STARTS = [
    (datetime.date(1981, 7, 1), 1), (datetime.date(1982, 7, 1), 2), (datetime.date(1983, 7, 1), 3),
    (datetime.date(1985, 7, 1), 4), (datetime.date(1988, 1, 1), 5), (datetime.date(1990, 1, 1), 6),
    (datetime.date(1991, 1, 1), 7), (datetime.date(1992, 7, 1), 8), (datetime.date(1993, 7, 1), 9),
    (datetime.date(1994, 7, 1), 10), (datetime.date(1996, 1, 1), 11), (datetime.date(1997, 7, 1), 12),
    (datetime.date(1999, 1, 1), 13), (datetime.date(2006, 1, 1), 14), (datetime.date(2009, 1, 1), 15),
    (datetime.date(2012, 7, 1), 16), (datetime.date(2015, 7, 1), 17), (datetime.date(2017, 1, 1), 18),
]
LAST_KNOWN = datetime.date(2017, 1, 1)


def leap_seconds(day):
    """GPS - UTC in seconds on the UTC calendar day `day` (a date or datetime)."""
    if isinstance(day, datetime.datetime):
        day = day.date()
    value = 0
    for start, seconds in LEAP_STARTS:
        if day >= start:
            value = seconds
    return value


def gps_to_utc(gps_time):
    """GPS calendar time (naive datetime) -> UTC (naive datetime)."""
    guess = gps_time - datetime.timedelta(seconds=leap_seconds(gps_time))
    return gps_time - datetime.timedelta(seconds=leap_seconds(guess))


def utc_to_gps(utc_time):
    return utc_time + datetime.timedelta(seconds=leap_seconds(utc_time))


def from_week_tow(week, tow):
    """GPS week + seconds of week -> GPS calendar time. `week` is the full week number (no rollover)."""
    return GPS_EPOCH + datetime.timedelta(seconds=int(week) * WEEK + float(tow))


def to_week_tow(gps_time):
    seconds = (gps_time - GPS_EPOCH).total_seconds()
    week = int(seconds // WEEK)
    return week, seconds - week * WEEK


def to_seconds(gps_time):
    """Seconds since the GPS epoch (float): the single time axis used by the solvers and the interpolation."""
    return (gps_time - GPS_EPOCH).total_seconds()


def from_seconds(seconds):
    return GPS_EPOCH + datetime.timedelta(seconds=float(seconds))


def day_of_year(day):
    return day.timetuple().tm_yday


def year_fraction(gps_time):
    """Decimal year, for epoch dependent frame transformations (e.g. 2023.96)."""
    start = datetime.datetime(gps_time.year, 1, 1)
    end = datetime.datetime(gps_time.year + 1, 1, 1)
    return gps_time.year + (gps_time - start).total_seconds() / (end - start).total_seconds()


def parse_rinex_time(text):
    """`  2023    12    14     9    17   30.0000000` (RINEX 3 header) or `23 12 14  9 17 30.0000000` -> datetime."""
    words = text.split()
    year = int(words[0])
    if year < 80:
        year += 2000
    elif year < 100:
        year += 1900
    seconds = float(words[5])
    whole = int(seconds)
    return datetime.datetime(year, int(words[1]), int(words[2]), int(words[3]), int(words[4]), whole) + \
        datetime.timedelta(seconds=seconds - whole)
