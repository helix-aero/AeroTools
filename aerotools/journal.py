"""AeroTools journal: ring buffer + subscribers.

The journal feeds two outputs: the Metashape console (print) and the collapsible
area of the panel. Panels subscribe, unsubscribe on close,
and an exception in a subscriber never prevents the line from being written.
"""

import os
import time

MAX_LINES = 300
LOG_NAME = "aerotools.log"
LOG_MAX_BYTES = 1 << 20

_lines = []
_subscribers = []


def write(message, tag="AeroTools"):
    """Append a timestamped line to the journal and push it to the subscribers."""
    line = "{} {}".format(time.strftime("%H:%M:%S"), message)
    _lines.append(line)
    if len(_lines) > MAX_LINES:
        del _lines[: len(_lines) - MAX_LINES]
    print("[{}] {}".format(tag, message))
    _to_file("{} [{}] {}".format(time.strftime("%Y-%m-%d %H:%M:%S"), tag, message))
    for callback in list(_subscribers):
        try:
            callback(line)
        except Exception:
            unsubscribe(callback)
    return line


def log_path():
    """aerotools.log, next to the settings: the same trace as the console, readable afterwards (support, diagnostics)."""
    from . import config
    return os.path.join(config.settings_dir(), LOG_NAME)


def _to_file(text):
    """Append a line to the file; never raises. Beyond 1 MB, the old file becomes .old."""
    try:
        path = log_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.isfile(path) and os.path.getsize(path) > LOG_MAX_BYTES:
            os.replace(path, path + ".old")
        with open(path, "a", encoding="utf-8", errors="replace") as handle:
            handle.write(text + "\n")
    except Exception:
        pass


def lines():
    return list(_lines)


def subscribe(callback):
    if callback not in _subscribers:
        _subscribers.append(callback)


def unsubscribe(callback):
    if callback in _subscribers:
        _subscribers.remove(callback)


def clear():
    del _lines[:]
