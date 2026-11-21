"""Optional antenna phase centre corrections of the base station (IGS ANTEX file).

The base antenna model is written in the header of the RINEX file of the station (`ANT # / TYPE`). The IGS publishes the
calibrations of the antennas used by the permanent stations in `igs20.atx` (free, https://files.igs.org/pub/station/general/).
RTKLIB can apply them to the base antenna. The antenna of the drone is not in that file (no calibration is published for it), so
the rover side stays uncorrected: this option fixes half of the problem, which is why it is off by default (see docs/AEROPPK.md).

The file is large (about 60 MB), so it is never shipped: `ensure()` downloads it once into the cache folder when the user asks.

A second, optional file can be added next to `igs20.atx`: `ngs20.atx` of the US National Geodetic Survey (https://www.ngs.noaa.gov/ANTCAL/),
which also holds the antennas of the NOAA CORS stations. It is downloaded the same way (`ensure(source="ngs20")`) and consulted for an antenna
that `igs20.atx` does not have (`choose`).
"""

import os

from . import rinex
from .stations import net, rgp

URL = "https://files.igs.org/pub/station/general/igs20.atx"
FILE_NAME = "igs20.atx"
# the calibration files that can be downloaded: id -> (address, file name in the cache)
SOURCES = {"igs20": (URL, FILE_NAME), "ngs20": ("https://www.ngs.noaa.gov/ANTCAL/LoadFile?file=ngs20.atx", "ngs20.atx")}
DEFAULT_SOURCE = "igs20"


def cache_path(source=DEFAULT_SOURCE):
    return os.path.join(rgp.cache_dir(), "antex", SOURCES[source][1])


def normalise(antenna):
    """`TRM55971.00     NONE` -> ("TRM55971.00", "NONE"): the model and the radome ("NONE" when absent)."""
    words = (antenna or "").split()
    if not words:
        return "", ""
    return words[0], (words[1] if len(words) > 1 else "NONE")


_CACHE = {}


def available_models(path):
    """Receiver antennas of an ANTEX file, read once per file version (see `_read_models`)."""
    try:
        key = (path, os.path.getmtime(path), os.path.getsize(path))
    except OSError:
        return set()
    if key not in _CACHE:
        for old in [k for k in _CACHE if k[0] == path]:          # an older version of the same file goes; other files stay (two files alternate)
            del _CACHE[old]
        _CACHE[key] = _read_models(path)
    return _CACHE[key]


def _read_models(path):
    """Set of (model, radome) of the receiver antennas of an ANTEX file (satellite entries, which have a PRN, are skipped)."""
    found = set()
    try:
        with open(path, "r", encoding="ascii", errors="replace") as handle:
            for line in handle:
                if line[60:80].startswith("TYPE / SERIAL NO"):
                    model, radome = normalise(line[0:20])
                    serial = line[20:40].strip()
                    if model and not (serial[:1] in ("G", "R", "E", "C", "J", "I", "S") and serial[1:3].isdigit()):
                        found.add((model, radome))
    except OSError:
        return set()
    return found


def lookup(models, antenna):
    """The `ANT # / TYPE` string to give RTKLIB, or None when the file has no calibration for it.

    The exact model with its radome is preferred, then the same model without radome (`NONE`), which is what the IGS does
    for a station whose radome was not calibrated.
    """
    model, radome = normalise(antenna)
    if not model:
        return None
    if (model, radome) in models:
        return "%s %s" % (model, radome)
    if (model, "NONE") in models:
        return "%s NONE" % model
    return None


def ensure(progress=None, cancelled=None, opener=None, source=DEFAULT_SOURCE):
    """Path of the ANTEX file of `source` (`igs20` or `ngs20`), downloaded the first time. Raises net.NetError when it cannot be downloaded."""
    path = cache_path() if source == DEFAULT_SOURCE else cache_path(source)
    if os.path.isfile(path) and os.path.getsize(path) > 1000000:
        return path
    net.download(SOURCES[source][0], path, progress=progress, cancelled=cancelled, opener=opener)
    return path


def choose(paths, antenna):
    """(file, `ANT # / TYPE` string) of the first file of `paths` that holds a calibration of `antenna`, else (None, None)."""
    for path in paths:
        if not path:
            continue
        known = lookup(available_models(path), antenna)
        if known:
            return path, known
    return None, None


def header_antenna(obs_path):
    """The antenna string of a RINEX observation file (base side)."""
    return rinex.read_obs_info(obs_path).antenna


_UP_CACHE = {}


def phase_centre_up(path, antenna):
    """`_phase_centre_up` remembered per file version and antenna (the file is 60 MB of text)."""
    try:
        key = (path, os.path.getmtime(path), os.path.getsize(path), antenna)
    except OSError:
        return None
    if key not in _UP_CACHE:
        _UP_CACHE[key] = _phase_centre_up(path, antenna)
    return _UP_CACHE[key]


def _phase_centre_up(path, antenna):
    """Height of the L1 phase centre above the antenna reference point (metres) for `antenna` (`ANT # / TYPE` text) in an ANTEX file, or None.

    Only the vertical part of the GPS L1 offset is read (`NORTH / EAST / UP` of the first G01 block, millimetres in the file). The exact
    model with its radome is looked for first, then the same model without radome."""
    model, radome = normalise(antenna)
    if not model or not os.path.isfile(path):
        return None
    wanted = [(model, radome), (model, "NONE")]
    try:
        with open(path, "r", encoding="ascii", errors="replace") as handle:
            current, in_l1 = None, False
            found = {}
            for line in handle:
                label = line[60:80]
                if label.startswith("TYPE / SERIAL NO"):
                    mdl, rad = normalise(line[0:20])
                    serial = line[20:40].strip()
                    current = (mdl, rad) if mdl and not (serial[:1] in ("G", "R", "E", "C", "J", "I", "S") and serial[1:3].isdigit()) else None
                    in_l1 = False
                elif current in wanted and label.startswith("START OF FREQUENCY"):
                    in_l1 = line[3:6] == "G01"
                elif current in wanted and in_l1 and label.startswith("NORTH / EAST / UP"):
                    try:
                        found.setdefault(current, float(line[0:30].split()[2]) / 1000.0)
                    except (ValueError, IndexError):
                        pass
                    in_l1 = False
                elif label.startswith("END OF ANTENNA"):
                    if wanted[0] in found:
                        break
                    current, in_l1 = None, False
            for key in wanted:
                if key in found:
                    return found[key]
    except OSError:
        return None
    return None
