"""Aero-Exports: the "look" of the DEM image (palette, shading, colour range), shared by the preview window and the export.

The look is a small immutable value (`Look`). It is saved in the tool's settings (section `aeroexports`, key `dem_look`),
so what the user sees in the preview is exactly what `relief.render` paints into the DXF/DWG DEM image. No numpy and no Qt here.

Palettes are data: `palettes.json` next to this file, plus an optional `palettes.json` in the AeroTools settings folder
(same format, same ids replace the built-in ones). A broken file is ignored, never fatal.
"""

import collections
import json
import os

from ... import config

SECTION = "aeroexports"
KEY = "dem_look"
FILE = "palettes.json"

NONE, SUN, LIGHTS4, SLOPE = "none", "sun", "lights4", "slope"
SHADINGS = (NONE, SUN, LIGHTS4, SLOPE)
AUTO, FULL, CUSTOM = "auto", "full", "custom"
STRETCHES = (AUTO, FULL, CUSTOM)
DEFAULT_PALETTE = "metashape"
ALTITUDE_RANGE = (5.0, 85.0)             # sun height above the horizon, degrees

# Metashape's own DEM palette read from the legend of its Ortho tab: (percent of the range, R, G, B). Last-resort copy
# of the "metashape" entry of palettes.json, and the reference the tests compare it to.
METASHAPE_RAMP = ((0, 47, 55, 218), (10, 48, 118, 219), (20, 52, 191, 221), (30, 50, 222, 184), (40, 51, 222, 120),
                  (50, 51, 222, 50), (60, 120, 223, 49), (70, 181, 222, 48), (80, 220, 194, 49), (90, 222, 119, 52),
                  (100, 225, 53, 56))

Look = collections.namedtuple("Look", "palette shading azimuth altitude strength stretch low high")
# Same picture as before the preview existed: Metashape palette, sun from the north-west at 45 degrees, strength 0.8,
# colours spread between the lowest and the highest height.
DEFAULT = Look(DEFAULT_PALETTE, SUN, 315.0, 45.0, 0.8, FULL, None, None)


def _number(value, default, low=None, high=None):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number or number in (float("inf"), float("-inf")):
        return default
    if low is not None:
        number = max(low, number)
    if high is not None:
        number = min(high, number)
    return number


def _optional(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def normalized(source):
    """A valid `Look` from a Look or a dict (saved settings, possibly damaged or from an older version)."""
    data = source._asdict() if isinstance(source, Look) else (source if isinstance(source, dict) else {})
    palette = data.get("palette")
    if not isinstance(palette, str) or not palette:
        palette = DEFAULT.palette
    shading = data.get("shading") if data.get("shading") in SHADINGS else DEFAULT.shading
    stretch = data.get("stretch") if data.get("stretch") in STRETCHES else DEFAULT.stretch
    low, high = _optional(data.get("low")), _optional(data.get("high"))
    if stretch == CUSTOM and (low is None or high is None or high <= low):
        stretch, low, high = FULL, None, None                       # a custom range needs two values, low under high
    if stretch != CUSTOM:
        low = high = None
    return Look(palette, shading, _number(data.get("azimuth"), DEFAULT.azimuth) % 360.0,
                _number(data.get("altitude"), DEFAULT.altitude, *ALTITUDE_RANGE),
                _number(data.get("strength"), DEFAULT.strength, 0.0, 1.0), stretch, low, high)


def to_dict(look):
    return normalized(look)._asdict()


def load():
    """The saved look, or the default one."""
    return normalized(config.get(SECTION, KEY))


def save(look):
    return config.put(SECTION, KEY, to_dict(look))


def is_default(look):
    return normalized(look) == DEFAULT


# --------------------------------------------------------------------------------------------------
# Palettes
# --------------------------------------------------------------------------------------------------

def _builtin():
    return {"id": DEFAULT_PALETTE, "label": {"en": "Metashape", "fr": "Metashape"},
            "stops": [(pct / 100.0, (r, g, b)) for pct, r, g, b in METASHAPE_RAMP]}


def _clean(entry):
    """A palette entry made valid, or None: an id, at least two stops with increasing positions in 0..1 and colours in 0..255."""
    try:
        ident = str(entry["id"]).strip()
        stops = [(float(position), tuple(int(c) for c in rgb)) for position, rgb in entry["stops"]]
    except (KeyError, TypeError, ValueError):
        return None
    if not ident or len(stops) < 2 or any(len(rgb) != 3 or min(rgb) < 0 or max(rgb) > 255 for _p, rgb in stops):
        return None
    if any(not 0.0 <= p <= 1.0 for p, _rgb in stops) or any(b[0] <= a[0] for a, b in zip(stops, stops[1:])):
        return None
    label = entry.get("label")
    return {"id": ident, "label": label if isinstance(label, dict) else {}, "stops": stops}


def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return [item for item in (_clean(entry) for entry in data["palettes"]) if item]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def user_file():
    return os.path.join(config.settings_dir(), FILE)


def palettes():
    """{id: palette} in display order: the package file, then the user's file (an id found twice keeps the user's version).
    Always holds the Metashape palette."""
    found = collections.OrderedDict()
    for path in (os.path.join(os.path.dirname(os.path.abspath(__file__)), FILE), user_file()):
        for item in _read(path):
            found[item["id"]] = item
    if DEFAULT_PALETTE not in found:
        found[DEFAULT_PALETTE] = _builtin()
        found.move_to_end(DEFAULT_PALETTE, last=False)
    return found


def palette(ident):
    """The palette called `ident`; the Metashape one when it does not exist (a saved look can outlive a removed palette)."""
    found = palettes()
    return found.get(ident) or found[DEFAULT_PALETTE]


def label(entry, lang):
    """Display name of a palette in a language (English, then its id, when it has no name there)."""
    names = entry.get("label") or {}
    return names.get(lang) or names.get("en") or entry["id"]
