"""AeroTools journal: ring buffer + subscribers.

The journal feeds two outputs: the Metashape console (print) and the collapsible
area of the panel. Panels subscribe, unsubscribe on close,
and an exception in a subscriber never prevents the line from being written.
"""

import os
import sys
import time

MAX_LINES = 300
LOG_NAME = "aerotools.log"
LOG_MAX_BYTES = 1 << 20

_lines = []
_subscribers = []
_file = {"path": None, "size": 0}            # the log file in use and its size, known without asking the disk for each line


def write(message, tag="AeroTools"):
    """Append a timestamped line to the journal and push it to the subscribers."""
    line = "{} {}".format(time.strftime("%H:%M:%S"), message)
    _lines.append(line)
    if len(_lines) > MAX_LINES:
        del _lines[: len(_lines) - MAX_LINES]
    _print("[{}] {}".format(tag, message))
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


def _print(text):
    """The console line; Metashape may have no stdout at all (None), and a closed or odd console must never stop a tool."""
    try:
        if sys.stdout is not None:
            print(text)
    except Exception:
        pass


def _to_file(text):
    """Append a line to the file; never raises. Beyond 1 MB, the old file becomes .old.

    The folder and the size are looked at once per file, then followed in memory: a chatty tool (zoom) writes many lines a second."""
    data = (text + os.linesep).encode("utf-8", errors="replace")
    for _attempt in (1, 2):
        try:
            path = log_path()
            if _file["path"] != path:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                _file["path"], _file["size"] = path, (os.path.getsize(path) if os.path.isfile(path) else 0)
            if _file["size"] > LOG_MAX_BYTES:
                os.replace(path, path + ".old")
                _file["size"] = 0
            with open(path, "ab") as handle:
                handle.write(data)
            _file["size"] += len(data)
            return
        except Exception:
            _file["path"] = None              # something changed under us (folder removed, file locked): look again, once


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
