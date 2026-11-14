"""Texts of the AeroTools interface and reports.

No Qt dependency: this module serves the panel, the reports, the menu and the tests alike.

Principle
---------
  - `aerotools/lang/en.py` is the one catalog. It defines `TEXTS` (key -> text) and `FORMATS`
    (decimal separator, date pattern, CSV delimiter).
  - `t(key, **fields)` never raises: a key missing from the catalog gives the key itself, and a
    missing field is left as it is.
  - Call `t()` when something is displayed, never at import time.
  - Plurals: write `{n} {n|file|files}` in a text; `t(key, n=1)` gives "1 file", `n=2` gives "2 files" (zero is plural).
    For a text assembled in code, `plural(n, "file", "files")` and `tn(key_one, key_other, n)` do the same.
"""

import datetime
import math
import re

from .lang import en

_texts = dict(en.TEXTS)
_formats = dict(en.FORMATS)


def texts():
    """Dictionary key -> text of the catalog (a copy)."""
    return dict(_texts)


def formats():
    """Number, date and CSV formats (a copy)."""
    return dict(_formats)


class _Open(dict):
    """Format fields: a missing field stays written "{name}"."""

    def __missing__(self, name):
        return "{" + str(name) + "}"


_PLURAL = re.compile(r"\{(\w+)\|([^{}|]*)\|([^{}|]*)\}")


def plural(n, one, other):
    """`one` when the count is exactly 1, else `other` (0 is plural, as in English)."""
    try:
        return one if float(n) == 1.0 else other
    except (TypeError, ValueError):
        return other


def _plurals(text, fields):
    def pick(match):
        name = match.group(1)
        if name not in fields:
            return match.group(0)
        return plural(fields[name], match.group(2), match.group(3))
    return _PLURAL.sub(pick, text)


def _format(text, fields):
    if not fields:
        return text
    try:
        return _plurals(text, fields).format_map(_Open(fields))
    except Exception:
        return text


def t(key, **fields):
    """Text of `key`. Never raises.

    `fields` fills the `{name}` fields of the text; a field that is not given stays as it is.
    A key missing from the catalog gives the key itself.
    """
    text = _texts.get(key)
    if text is None:
        text = str(key)
    return _format(text, fields)


def tn(key_one, key_other, count, **fields):
    """Text of `key_one` when `count` is 1, else of `key_other` (for texts that differ by more than a word). `{n}` is the count unless
    the call gives another `n`."""
    fields.setdefault("n", count)
    return t(key_one if plural(count, True, False) else key_other, **fields)


def has(key):
    """True if `key` exists in the catalog."""
    return key in _texts


# --------------------------------------------------------------------------
# Formats
# --------------------------------------------------------------------------

def decimal_separator():
    return _formats.get("decimal") or "."


def csv_delimiter():
    """Delimiter of the CSV files."""
    return _formats.get("csv_delimiter") or ","


def fmt_number(value, decimals=3):
    """Number with `decimals` decimals, no thousands separator.

    A minus sign in front of a zero (-0.000) is removed. A missing, unreadable, NaN or infinite
    value gives an empty string.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if math.isnan(number) or math.isinf(number):
        return ""
    try:
        places = max(0, int(decimals))
    except (TypeError, ValueError):
        places = 3
    text = "{:.{d}f}".format(number, d=places)
    if text.startswith("-") and float(text) == 0.0:
        text = text[1:]
    separator = decimal_separator()
    return text if separator == "." else text.replace(".", separator)


def fmt_date(value):
    """Date as YYYY-MM-DD.

    Accepts a date or a date and time; anything else is returned as it is.
    """
    pattern = _formats.get("date") or "%Y-%m-%d"
    if isinstance(value, (datetime.date, datetime.datetime)):
        try:
            return value.strftime(pattern)
        except Exception:
            pass
    return str(value)
