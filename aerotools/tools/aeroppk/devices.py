"""The device (drone) of step 1: what the photos say about it, what its antenna is, what the user may add. Pure logic, no Qt.

The photo positions are those of the camera; the PPK result is the position of the GNSS antenna, and the vector from the antenna to
the camera (the lever arm) is added to get the camera position back. A reader of `drones/` knows the lever arm of its drone (the
M3E writes it in the events file). For a device whose antenna is NOT known the user may type the height of the antenna above the
camera. Only the vertical part is offered: the horizontal part has to be rotated with the heading of every photo, which the tool does
not have, and on a mapping drone it is a few centimetres at most.
"""

import copy
import struct

from . import drones

OTHER = "other"
MAX_Z = 2.0                  # metres: a larger antenna height above the camera is a typing mistake


class Choice(object):
    def __init__(self, ident, name, reader=None):
        self.id, self.name, self.reader = ident, name, reader

    @property
    def antenna_known(self):
        return self.reader is not None and bool(getattr(self.reader, "antenna_known", False))


def choices():
    """The readers of `drones/`, then the entry for any other device."""
    out = [Choice(r.id, r.name, r) for r in drones.READERS]
    out.append(Choice(OTHER, "", None))
    return out


def choice_by_id(ident):
    for choice in choices():
        if choice.id == ident:
            return choice
    return choices()[-1]


# ---------------------------------------------------------------------------------------------------------------------
# What a photo says: make and model in the EXIF block (a few bytes of the JPEG header)
# ---------------------------------------------------------------------------------------------------------------------

def read_make_model(path):
    """(make, model) written in the EXIF of a JPEG, ('', '') when absent or unreadable. Reads the header only."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(131072)
    except OSError:
        return "", ""
    return parse_make_model(head)


def parse_make_model(data):
    if data[:2] != b"\xff\xd8":
        return "", ""
    pos = 2
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            break
        marker = data[pos + 1]
        (size,) = struct.unpack(">H", data[pos + 2:pos + 4])
        if marker == 0xE1 and data[pos + 4:pos + 10] == b"Exif\x00\x00":
            return _tiff_make_model(data[pos + 10:pos + 2 + size])
        if marker in (0xDA, 0xD9):
            break
        pos += 2 + size
    return "", ""


def _tiff_make_model(tiff):
    if len(tiff) < 8 or tiff[:2] not in (b"II", b"MM"):
        return "", ""
    order = "<" if tiff[:2] == b"II" else ">"
    try:
        (offset,) = struct.unpack_from(order + "I", tiff, 4)
        (count,) = struct.unpack_from(order + "H", tiff, offset)
        found = {}
        for i in range(min(count, 200)):
            base = offset + 2 + 12 * i
            tag, typ, cnt = struct.unpack_from(order + "HHI", tiff, base)
            if tag in (0x010F, 0x0110) and typ == 2:
                if cnt <= 4:
                    raw = tiff[base + 8:base + 8 + cnt]
                else:
                    (at,) = struct.unpack_from(order + "I", tiff, base + 8)
                    raw = tiff[at:at + cnt]
                found[tag] = raw.split(b"\x00")[0].decode("latin-1", "replace").strip()
        return found.get(0x010F, ""), found.get(0x0110, "")
    except (struct.error, IndexError):
        return "", ""


def detect(photos, folder=""):
    """Id of the reader that fits these photos, or '' when none does. EXIF model first, then the file names of the folder."""
    models = []
    for path in list(photos)[:3]:
        make, model = read_make_model(path)
        if make or model:
            models.append(model.strip().lower())
    for reader in drones.READERS:
        wanted = [m.lower() for m in getattr(reader, "exif_models", ())]
        if wanted and any(text in wanted for text in models):
            return reader.id
    if folder:
        found = drones.reader_for(folder)
        if found is not None:
            return found.id
    return ""


# ---------------------------------------------------------------------------------------------------------------------
# Antenna offset typed by the user
# ---------------------------------------------------------------------------------------------------------------------

def parse_antenna_z(text):
    """`0.085`, `8,5` -> metres, None when empty or not a sensible number (0 to MAX_Z)."""
    try:
        value = float((text or "").strip().replace(",", "."))
    except ValueError:
        return None
    return value if 0.0 <= value <= MAX_Z else None


def with_antenna_height(events, z_up):
    """Copies of the events whose lever arm is longer by `z_up` metres (the antenna sits `z_up` above the camera, so the camera is
    `z_up` further down from it)."""
    out = []
    for event in events:
        clone = copy.copy(event)
        n, e, d = event.offset_ned
        clone.offset_ned = (n, e, d + float(z_up))
        out.append(clone)
    return out
