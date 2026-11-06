"""History of the FastGCP markings, so that they can be undone.

A stack of limited depth (MAX_DEPTH). Each entry keeps what is needed to
undo a placement: the marker, the photo, and the projection that the marker
had on this photo BEFORE (x, y, pinned) or None if it had none.

`marking.place_at` records the entry before writing to Metashape;
`marking.undo_last` pops it and restores. This module does not touch Metashape: it
only keeps references and notifies its subscribers (the panel's Undo
button, the Ctrl+Z shortcut) when the stack changes.

A marker or photo reference becomes invalid (RuntimeError) when the chunk
changes: the stack is then emptied (Session.forget_chunk) and any entry that
became unreadable is ignored, never an exception.
"""

MAX_DEPTH = 100

_STACK = []
_REDO = []            # markings undone by Undo, which Redo can place again
_LISTENERS = []


class Entry(object):
    """A placed marking: what is needed to undo it."""

    __slots__ = ("marker", "camera", "previous", "chunk_key", "label", "camera_name")

    def __init__(self, marker, camera, previous, chunk_key=None, label="", camera_name=""):
        self.marker = marker
        self.camera = camera
        self.previous = previous
        self.chunk_key = chunk_key
        self.label = label
        self.camera_name = camera_name

    def is_valid(self):
        """False if the marker or the photo no longer exists. Never raises."""
        try:
            self.marker.label
            self.camera.key
            return True
        except Exception:
            return False


def _clean_previous(previous):
    """(x, y, pinned) as floats, or None. An unreadable value is None."""
    if previous is None:
        return None
    try:
        x, y, pinned = previous
        return (float(x), float(y), bool(pinned))
    except Exception:
        return None


def push(marker, camera, previous, chunk_key=None, label="", camera_name="", keep_redo=False):
    """Push a marking. Returns the entry, or None if it is unusable.

    A new marking empties the Redo stack, except when it is Redo itself (`keep_redo`).
    """
    if marker is None or camera is None:
        return None
    entry = Entry(marker, camera, _clean_previous(previous), chunk_key, label, camera_name)
    if not keep_redo:
        del _REDO[:]
    _STACK.append(entry)
    if len(_STACK) > MAX_DEPTH:
        del _STACK[: len(_STACK) - MAX_DEPTH]
    notify()
    return entry


def pop():
    """Pop and return the last entry, or None if the stack is empty."""
    if not _STACK:
        return None
    entry = _STACK.pop()
    notify()
    return entry


def discard(entry):
    """Remove `entry` if it is the last one (write refused after the push)."""
    if entry is not None and _STACK and _STACK[-1] is entry:
        _STACK.pop()
        notify()


def push_redo(marker, camera, after, chunk_key=None, label="", camera_name=""):
    """Keep what Undo has just erased: `after` is the projection to place again (or None: none)."""
    if marker is None or camera is None:
        return None
    entry = Entry(marker, camera, _clean_previous(after), chunk_key, label, camera_name)
    _REDO.append(entry)
    if len(_REDO) > MAX_DEPTH:
        del _REDO[: len(_REDO) - MAX_DEPTH]
    notify()
    return entry


def pop_redo():
    """Pop the last undone marking, or None."""
    if not _REDO:
        return None
    entry = _REDO.pop()
    notify()
    return entry


def can_redo():
    return bool(_REDO)


def peek():
    return _STACK[-1] if _STACK else None


def size():
    return len(_STACK)


def can_undo():
    return bool(_STACK)


def clear():
    if _STACK or _REDO:
        del _STACK[:]
        del _REDO[:]
        notify()


# --------------------------------------------------------------------------
# Subscribers
# --------------------------------------------------------------------------

def subscribe(callback):
    if callback not in _LISTENERS:
        _LISTENERS.append(callback)


def unsubscribe(callback):
    if callback in _LISTENERS:
        _LISTENERS.remove(callback)


def notify():
    """Notify the subscribers. An error in one of them does not affect the others."""
    for callback in list(_LISTENERS):
        try:
            callback()
        except Exception:
            unsubscribe(callback)
