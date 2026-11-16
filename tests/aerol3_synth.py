"""Synthetic Zenmuse L3 data for the Aero-L3 tests: tiny JPEG headers (EXIF + XMP, no pixels), MRK files and whole flight folders.

The numbers are invented (a straight flight line near 48.8 N, 2.3 E, made-up calibration values) but the structure is the one of the real
files: two lenses per shot (`..._LV.JPG`, `..._RV.JPG`), DJI `drone-dji:` tags, camera angles in two patterns
(one per flight direction), GPS time in `UTCAtExposure`.
"""

import datetime
import math
import os
import struct

LV_DEWARP = "2025-01-01;5000.000000000000,5000.000000000000,10.000000000000,20.000000000000,0.100000000000,-0.400000000000,-0.000011000000,-0.000022000000,0.500000000000"
RV_DEWARP = "2025-01-01;5010.000000000000,5010.000000000000,-12.000000000000,5.000000000000,0.110000000000,-0.410000000000,-0.000033000000,0.000044000000,0.510000000000"
SERIAL_L = "SERIAL-LEFT-001"
SERIAL_R = "SERIAL-RIGHT-001"


# --------------------------------------------------------------------------------------------------
# JPEG header
# --------------------------------------------------------------------------------------------------

def _tiff(ifd0, exif=None, gps=None, order="<"):
    """TIFF structure bytes. Entries are (tag, type, value): type 2 = ASCII (str), 3 = SHORT (int), 5 = RATIONAL (list of floats)."""
    exif = exif or []
    gps = gps or []
    entries0 = list(ifd0)
    if exif:
        entries0.append((0x8769, 4, 0))
    if gps:
        entries0.append((0x8825, 4, 0))
    blocks = {}

    def encode(entries, base):
        """(ifd bytes, data bytes) for an IFD that will sit at `base`; values that do not fit in 4 bytes go after the table."""
        entries = sorted(entries, key=lambda e: e[0])
        table_size = 2 + 12 * len(entries) + 4
        data = b""
        table = struct.pack(order + "H", len(entries))
        for tag, typ, value in entries:
            if typ == 2:
                raw = value.encode("latin-1") + b"\x00"
                count = len(raw)
            elif typ == 3:
                raw = struct.pack(order + "H", value)
                count = 1
            elif typ == 4:
                raw = struct.pack(order + "I", value)
                count = 1
            elif typ == 5:
                values = value if isinstance(value, (list, tuple)) else [value]
                raw = b"".join(struct.pack(order + "II", int(round(v * 1000000)), 1000000) for v in values)
                count = len(values)
            else:
                raise ValueError(typ)
            if len(raw) <= 4:
                inline = raw + b"\x00" * (4 - len(raw))
            else:
                inline = struct.pack(order + "I", base + table_size + len(data))
                data += raw + (b"\x00" if len(raw) % 2 else b"")
            table += struct.pack(order + "HHI", tag, typ, count) + inline
        table += struct.pack(order + "I", 0)
        return table + data

    header = (b"II" if order == "<" else b"MM") + struct.pack(order + "HI", 42, 8)
    ifd0_len = len(encode([(t, ty, v) if t not in (0x8769, 0x8825) else (t, ty, 0) for t, ty, v in entries0], 8))
    offset = 8 + ifd0_len
    exif_bytes = encode(exif, offset) if exif else b""
    offset2 = offset + len(exif_bytes)
    gps_bytes = encode(gps, offset2) if gps else b""
    fixed = []
    for tag, typ, value in entries0:
        if tag == 0x8769:
            value = offset
        elif tag == 0x8825:
            value = offset2
        fixed.append((tag, typ, value))
    return header + encode(fixed, 8) + exif_bytes + gps_bytes


def _dms(value):
    value = abs(value)
    d = int(value)
    m = int((value - d) * 60)
    s = (value - d - m / 60.0) * 3600
    return [d, m, s]


def xmp_packet(tags, make="DJI", model="L3"):
    """XMP packet with `drone-dji:` attributes from a dict of tag name -> value."""
    body = "\n".join('   drone-dji:{}="{}"'.format(k, v) for k, v in tags.items())
    return ('<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?><x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            '<rdf:Description rdf:about="DJI Meta Data" xmlns:tiff="http://ns.adobe.com/tiff/1.0/" xmlns:drone-dji="http://www.dji.com/drone-dji/1.0/" '
            'tiff:Make="{}" tiff:Model="{}"\n{}>\n</rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end="w"?>').format(make, model, body).encode("utf-8")


def make_jpeg(width=6144, height=4096, orientation=1, make="DJI", model="L3", tags=None, lat=None, lon=None, alt=None, order="<",
              datetime_original="2030:01:01 12:05:53", focal=14.45, focal35=28, with_xmp=True, padding=0):
    """Bytes of a JPEG that has a header and no pixel data."""
    ifd0 = [(0x010F, 2, make), (0x0110, 2, model)]
    if orientation is not None:
        ifd0.append((0x0112, 3, orientation))
    exif = [(0x9003, 2, datetime_original), (0x920A, 5, focal), (0xA405, 3, focal35)]
    gps = []
    if lat is not None and lon is not None:
        gps = [(1, 2, "N" if lat >= 0 else "S"), (2, 5, _dms(lat)), (3, 2, "E" if lon >= 0 else "W"), (4, 5, _dms(lon))]
        if alt is not None:
            gps += [(5, 3, 0), (6, 5, [alt])]
    tiff = _tiff(ifd0, exif, gps, order)
    out = b"\xff\xd8"
    out += b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    app1 = b"Exif\x00\x00" + tiff
    out += b"\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1
    if padding:
        out += b"\xff\xe2" + struct.pack(">H", padding + 2) + b"\x00" * padding        # like the MPF block that sits before the XMP in real files
    if with_xmp:
        packet = b"http://ns.adobe.com/xap/1.0/\x00" + xmp_packet(tags or {}, make, model)
        out += b"\xff\xe1" + struct.pack(">H", len(packet) + 2) + packet
    out += b"\xff\xc0" + struct.pack(">H", 11) + struct.pack(">BHHB", 8, height, width, 1) + b"\x01\x11\x00"
    out += b"\xff\xda" + struct.pack(">H", 8) + b"\x01\x01\x00\x00\x3f\x00" + b"\xff\xd9"
    return out


# --------------------------------------------------------------------------------------------------
# Flights
# --------------------------------------------------------------------------------------------------

def norm(angle):
    return (angle + 180.0) % 360.0 - 180.0


def photo_tags(lens, heading, group, lat, lon, alt, gps_time, rtk=50, serial=None, dewarp=None, nadir=True, camera_angles=True, flight_yaw=None):
    """The XMP tags of one photo of a nadir mapping flight. `group` 'A' or 'B' is the pattern of camera angles of the two flight directions."""
    gimbal_yaw = norm(heading + 180.0)
    if group == "B":
        cam_yaw, cam_roll = norm(gimbal_yaw + 90.0), 90.0
        pitch = -67.5 if lens == "L" else -112.5
    else:
        cam_yaw, cam_roll = norm(gimbal_yaw - 90.0), -90.0
        pitch = -112.5 if lens == "L" else -67.5
    tags = {
        "Version": "1.6", "ImageSource": "LEFT_CAMERA" if lens == "L" else "RIGHT_CAMERA", "GpsStatus": "RTK", "AltitudeType": "RtkAlt",
        "GpsLatitude": "%+.9f" % lat, "GpsLongitude": "%+.9f" % lon, "AbsoluteAltitude": "%+.3f" % alt, "RelativeAltitude": "%+.3f" % (alt - 50.0),
        "GimbalRollDegree": "+180.00", "GimbalYawDegree": "%+.2f" % gimbal_yaw, "GimbalPitchDegree": "-90.00",
        "FlightYawDegree": "%+.2f" % norm(heading if flight_yaw is None else flight_yaw), "FlightPitchDegree": "+0.50", "FlightRollDegree": "+1.00",
        "ProductName": "L3", "DroneModel": "M400", "RtkFlag": str(rtk), "RtkStdLat": "0.00122", "RtkStdLon": "0.00134", "RtkStdHgt": "0.00295",
        "DewarpFlag": "0", "DewarpData": dewarp or (LV_DEWARP if lens == "L" else RV_DEWARP),
        "CalibratedFocalLength": "4986.197266", "CalibratedOpticalCenterX": "3072.000000", "CalibratedOpticalCenterY": "2048.000000",
        "UTCAtExposure": gps_time.strftime("%Y-%m-%dT%H:%M:%S.%f"),
        "CameraSerialNumber": serial or (SERIAL_L if lens == "L" else SERIAL_R),
    }
    if camera_angles:
        tags.update({"CameraYawDegree": "%+.2f" % cam_yaw, "CameraPitchDegree": "%+.2f" % pitch, "CameraRollDegree": "%+.2f" % cam_roll})
    return tags


def destination(lat, lon, heading, metres):
    """Position `metres` away along a compass `heading` (small distances: flat Earth is enough for a test)."""
    dn = metres * math.cos(math.radians(heading))
    de = metres * math.sin(math.radians(heading))
    return lat + dn / 111320.0, lon + de / (111320.0 * math.cos(math.radians(lat)))


GPS0 = datetime.datetime(2030, 1, 1, 11, 6, 12, 442935)          # a GPS time, as `UTCAtExposure` holds it


def write_flight(folder, name="DJI_203001011200_009_TESTSITE-FLIGHT01", shots=8, heading=120.0, group="B", lat=48.8, lon=2.3, alt=200.0,
                 start=GPS0, step_s=2.0, speed=10.0, rtk=50, with_mrk=True, with_lidar=True, lens_folders=False, tags_for=None, files_for=None,
                 first_index=3, sorted_in=None):
    """Writes a flight folder under `folder` and returns the list of (path, tags) of its photos.

    `tags_for(i, lens, tags) -> tags` can change the tags of a photo, `files_for(i, lens, kwargs) -> kwargs` the arguments of `make_jpeg`
    (a rotated copy, a different size...). `lens_folders` writes the photos in `LV` / `RV` sub-folders."""
    root = os.path.join(str(folder), name)
    os.makedirs(root, exist_ok=True)
    photos = []
    mrk_lines = []
    for i in range(shots):
        t = start + datetime.timedelta(seconds=step_s * i)
        plat, plon = destination(lat, lon, heading, speed * step_s * i)
        for lens, offset_ms in (("L", 0.0), ("R", 2.7)):
            moment = t + datetime.timedelta(milliseconds=offset_ms)
            tags = photo_tags(lens, heading, group, plat, plon, alt + 0.01 * i, moment, rtk=rtk)
            if tags_for is not None:
                tags = tags_for(i, lens, dict(tags)) or tags
            kwargs = dict(tags=tags, lat=plat, lon=plon, alt=alt)
            if files_for is not None:
                kwargs = files_for(i, lens, kwargs) or kwargs
            stem = "DJI_{}_{:04d}".format(moment.strftime("%Y%m%d%H%M%S"), first_index + i)
            fname = "{}_{}V.JPG".format(stem, lens)
            target = os.path.join(root, ("LV" if lens == "L" else "RV")) if lens_folders else root
            os.makedirs(target, exist_ok=True)
            path = os.path.join(target, fname)
            with open(path, "wb") as handle:
                handle.write(make_jpeg(**kwargs))
            photos.append((path, tags))
            week = (moment - datetime.datetime(1980, 1, 6)).days // 7
            tow = (moment - datetime.datetime(1980, 1, 6)).total_seconds() - week * 604800
            mrk_lines.append("{:04d}_{}V\t{:.6f}\t[{}]\t{},N\t{},E\t{},V\t{:.8f},Lat\t{:.8f},Lon\t{:.3f},Ellh\t0.001221, 0.001338, 0.002954\t{},Q\n".format(
                first_index + i, lens, tow, week, 107, 713, 217, plat, plon, alt + 0.01 * i, rtk))
    if with_mrk:
        with open(os.path.join(root, name + "_Timestamp.MRK"), "w") as handle:
            handle.writelines(mrk_lines)
    if with_lidar:
        for ext in (".LDR", ".IMU", ".RPOS"):
            with open(os.path.join(root, "DJI_20300101120552_0002_L" + ext), "wb") as handle:
                handle.write(b"x")
    return photos
