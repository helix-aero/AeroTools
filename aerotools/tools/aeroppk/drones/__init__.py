"""Drone readers. One class per drone family; add yours to READERS (see base.py)."""

from .dji_m3e import DjiM3E

READERS = [DjiM3E()]


def reader_for(folder, preferred=None):
    """The first reader that recognises `folder` (the preferred id first), or None."""
    ordered = sorted(READERS, key=lambda r: 0 if r.id == preferred else 1)
    for reader in ordered:
        if reader.accepts(folder):
            return reader
    return None


def reader_by_id(reader_id):
    for reader in READERS:
        if reader.id == reader_id:
            return reader
    return None
