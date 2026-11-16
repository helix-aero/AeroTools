"""Aero-L3: a small reader of what a DJI photo says about itself: JPEG size, a few EXIF tags and the XMP packet (`drone-dji:` tags).

Metashape's own reading of this payload is not trusted (angles, calibration, accuracy), so the tool reads the file header itself. Only the
header is read (segment by segment, the pixels are never decoded) so a whole flight is scanned in seconds. Pure Python 3.9.
"""

import re
import struct
from collections import namedtuple

MAX_SEGMENTS = 80
XMP_HEADER = b"http://ns.adobe.com/xap/1.0/\x00"
EXIF_HEADER = b"Exif\x00\x00"

JpegInfo = namedtuple("JpegInfo", "width height exif xmp")

_ATTR = re.compile(r'(drone-dji|tiff|xmp|exif|Camera):([A-Za-z0-9_]+)\s*=\s*"([^"]*)"')
_ELEM = re.compile(r"<(drone-dji):([A-Za-z0-9_]+)>([^<]*)</drone-dji:[A-Za-z0-9_]+>")

# (type id) -> (struct char, size)
_TYPES = {1: ("B", 1), 2: ("s", 1), 3: ("H", 2), 4: ("I", 4), 5: ("II", 8), 7: ("B", 1), 9: ("i", 4), 10: ("ii", 8)}


class JpegError(Exception):
    """The file is not a readable JPEG."""


# --------------------------------------------------------------------------------------------------
# EXIF (TIFF structure)
# --------------------------------------------------------------------------------------------------

def _read_ifd(tiff, offset, order):
    """{tag: (type, count, raw value bytes or an offset)} of one IFD; raises ValueError on a broken structure."""
    entries = {}
    if offset < 8 or offset + 2 > len(tiff):
        raise ValueError("bad IFD offset")
    (count,) = struct.unpack_from(order + "H", tiff, offset)
    if count > 512:
        raise ValueError("too many entries")
    for i in range(count):
        base = offset + 2 + 12 * i
        if base + 12 > len(tiff):
            break
        tag, typ, cnt = struct.unpack_from(order + "HHI", tiff, base)
        entries[tag] = (typ, cnt, tiff[base + 8:base + 12])
    return entries


def _values(tiff, order, entry):
    """Decoded values of one entry (list of numbers, tuples for rationals, or a str for ASCII)."""
    typ, cnt, raw = entry
    if typ not in _TYPES or cnt > 4096:
        return []
    fmt, size = _TYPES[typ]
    total = size * cnt
    if total <= 4:
        data = raw[:total]
    else:
        (offset,) = struct.unpack(order + "I", raw)
        if offset + total > len(tiff):
            return []
        data = tiff[offset:offset + total]
    if typ == 2:
        return data.split(b"\x00")[0].decode("latin-1", "replace").strip()
    if typ in (5, 10):
        out = []
        char = "I" if typ == 5 else "i"
        for i in range(cnt):
            num, den = struct.unpack_from(order + char + char, data, 8 * i)
            out.append(float(num) / den if den else 0.0)
        return out
    return list(struct.unpack(order + fmt * cnt, data)) if typ != 7 else list(data)


def parse_exif(tiff):
    """A dict of the few EXIF values the tool uses (missing ones are absent): make, model, software, orientation, datetime_original,
    focal_length, focal_length_35mm, gps_lat, gps_lon, gps_alt. Never raises: a damaged structure gives what was read so far."""
    out = {}
    if len(tiff) < 8 or tiff[:2] not in (b"II", b"MM"):
        return out
    order = "<" if tiff[:2] == b"II" else ">"
    try:
        (ifd0_offset,) = struct.unpack_from(order + "I", tiff, 4)
        ifd0 = _read_ifd(tiff, ifd0_offset, order)
    except (ValueError, struct.error):
        return out

    def ascii_tag(entries, tag, name):
        if tag in entries:
            text = _values(tiff, order, entries[tag])
            if isinstance(text, str) and text:
                out[name] = text

    def number_tag(entries, tag, name):
        if tag in entries:
            values = _values(tiff, order, entries[tag])
            if isinstance(values, list) and values:
                out[name] = values[0]

    ascii_tag(ifd0, 0x010F, "make")
    ascii_tag(ifd0, 0x0110, "model")
    ascii_tag(ifd0, 0x0131, "software")
    number_tag(ifd0, 0x0112, "orientation")
    try:
        if 0x8769 in ifd0:
            exif_entries = _read_ifd(tiff, struct.unpack(order + "I", ifd0[0x8769][2])[0], order)
            ascii_tag(exif_entries, 0x9003, "datetime_original")
            number_tag(exif_entries, 0x920A, "focal_length")
            number_tag(exif_entries, 0xA405, "focal_length_35mm")
        if 0x8825 in ifd0:
            gps = _read_ifd(tiff, struct.unpack(order + "I", ifd0[0x8825][2])[0], order)
            lat = _dms(tiff, order, gps, 2, 1)
            lon = _dms(tiff, order, gps, 4, 3)
            if lat is not None and lon is not None:
                out["gps_lat"], out["gps_lon"] = lat, lon
            if 6 in gps:
                alt = _values(tiff, order, gps[6])
                if isinstance(alt, list) and alt:
                    below = 5 in gps and gps[5][2][:1] == b"\x01"
                    out["gps_alt"] = -alt[0] if below else alt[0]
    except (ValueError, struct.error):
        pass
    return out


def _dms(tiff, order, gps, tag_value, tag_ref):
    if tag_value not in gps:
        return None
    values = _values(tiff, order, gps[tag_value])
    if not isinstance(values, list) or len(values) != 3:
        return None
    degrees = values[0] + values[1] / 60.0 + values[2] / 3600.0
    ref = _values(tiff, order, gps[tag_ref]) if tag_ref in gps else "N"
    return -degrees if isinstance(ref, str) and ref[:1] in ("S", "W") else degrees


# --------------------------------------------------------------------------------------------------
# XMP
# --------------------------------------------------------------------------------------------------

def parse_xmp(packet):
    """{'drone-dji:Name': 'value', 'tiff:Make': ...} of an XMP packet (attributes and simple elements)."""
    text = packet.decode("utf-8", "replace") if isinstance(packet, bytes) else packet
    out = {}
    for prefix, name, value in _ATTR.findall(text):
        out[prefix + ":" + name] = value
    for prefix, name, value in _ELEM.findall(text):
        out.setdefault(prefix + ":" + name, value.strip())
    return out


def number(xmp, name, default=None):
    """A `drone-dji:` value as a float ('+147.395' -> 147.395), or `default`."""
    value = xmp.get("drone-dji:" + name)
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError:
        return default


def text(xmp, name, default=""):
    value = xmp.get("drone-dji:" + name)
    return default if value is None else value


# --------------------------------------------------------------------------------------------------
# JPEG
# --------------------------------------------------------------------------------------------------

def parse_jpeg_stream(handle):
    """Reads the header of an open binary JPEG. Returns `JpegInfo(width, height, exif dict, xmp dict)`. Raises `JpegError`."""
    if handle.read(2) != b"\xff\xd8":
        raise JpegError("not a JPEG file")
    width = height = None
    exif, xmp = {}, {}
    for _ in range(MAX_SEGMENTS):
        head = handle.read(4)
        while len(head) == 4 and head[0] != 0xFF:               # resynchronise on garbage between segments
            head = head[1:] + handle.read(1)
        if len(head) < 4:
            break
        marker = head[1]
        while marker == 0xFF:                                   # fill bytes
            head = head[1:] + handle.read(1)
            if len(head) < 4:
                break
            marker = head[1]
        if len(head) < 4:
            break
        if marker == 0xDA or marker == 0xD9:                    # start of scan / end of image: the header is over
            break
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:           # markers without a length
            handle.seek(-2, 1)
            continue
        length = (head[2] << 8) | head[3]
        if length < 2:
            raise JpegError("damaged JPEG segment")
        body_length = length - 2
        if marker == 0xE1 and body_length > 8:
            body = handle.read(body_length)
            if body.startswith(EXIF_HEADER) and not exif:
                exif = parse_exif(body[6:])
            elif body.startswith(XMP_HEADER) and not xmp:
                xmp = parse_xmp(body[len(XMP_HEADER):])
        elif marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            body = handle.read(body_length)
            if len(body) >= 5:
                height, width = struct.unpack(">HH", body[1:5])
        else:
            handle.seek(body_length, 1)
        if width is not None and xmp and exif:
            break
    if width is None:
        raise JpegError("no image size found")
    return JpegInfo(width, height, exif, xmp)


def read_jpeg(path):
    """`JpegInfo` of a file on disk. Raises `JpegError` (or OSError when the file cannot be opened)."""
    with open(path, "rb") as handle:
        return parse_jpeg_stream(handle)
