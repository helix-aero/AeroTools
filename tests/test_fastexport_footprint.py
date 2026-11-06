"""Fast Export : emprise reelle des donnees et polygone de decoupe (clip) des images du DXF."""

import struct

import numpy as np
import pytest

from aerotools.tools.fastexport import dxfout, footprint


def diamond(size=100):
    """Un losange (ortho tournee) dans un carre : les coins sont vides."""
    rows, cols = np.mgrid[0:size, 0:size]
    middle = (size - 1) / 2.0
    return (np.abs(rows - middle) + np.abs(cols - middle)) <= size / 2.0 - 2


def image_info(width=1000, height=1000, pixel=0.1):
    return {"width": width, "height": height, "origin": (500.0, 300.0 + height * pixel), "pixel": (pixel, pixel)}


def polygon_area(points):
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:] + points[:1]))) / 2.0


def test_the_outline_of_a_diamond_has_few_vertices_and_follows_the_data():
    mask = diamond()
    path = footprint.outline(mask)
    assert 4 <= len(path) <= 40
    assert path[0][1] <= path[1][1] <= path[2][1]                                   # descend le long du bord gauche
    area = polygon_area(path)
    assert mask.sum() * 0.95 < area < mask.sum() * 1.15                             # a peu pres l'aire des donnees


def test_an_empty_mask_has_no_outline_and_a_full_one_needs_no_clip():
    assert footprint.outline(np.zeros((10, 10), dtype=bool)) == []
    full = np.ones((50, 50), dtype=bool)
    extent = (500.0, 300.0, 600.0, 400.0)
    assert footprint.clip_polygon(full, extent, image_info()) == []
    assert footprint.clip_polygon(np.zeros((10, 10), dtype=bool), extent, image_info()) == []


def test_the_clip_polygon_is_closed_in_image_pixels_and_never_cuts_the_data():
    mask = diamond()
    extent = (500.0, 300.0, 600.0, 400.0)                                          # 100 m, image de 1000 px a 0,1 m
    clip = footprint.clip_polygon(mask, extent, image_info())
    assert clip[0] == clip[-1] and len(clip) >= 5
    xs, ys = [p[0] for p in clip], [p[1] for p in clip]
    assert -0.5 <= min(xs) and max(xs) <= 999.5 and -0.5 <= min(ys) and max(ys) <= 999.5
    # le centre du losange est dedans, les coins de l'image sont dehors
    assert _inside(clip, 499.5, 499.5) and not _inside(clip, 5.0, 5.0) and not _inside(clip, 990.0, 990.0)
    # un pixel de donnees a la limite du losange reste dans le polygone (marge d'un pixel du masque)
    rows, cols = np.nonzero(mask)
    for r, c in list(zip(rows, cols))[::37]:
        x_px, y_px = (c + 0.5) * 10 - 0.5, (100 - (r + 0.5)) * 10 - 0.5
        assert _inside(clip, x_px, y_px)


def _inside(poly, x, y):
    inside = False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def rgba_tiff(path, alpha):
    """TIFF RVBA 8 bits sans compression, en bandes, comme le petit export de masque de Metashape."""
    height, width = alpha.shape
    pixels = np.zeros((height, width, 4), dtype=np.uint8)
    pixels[:, :, 3] = np.where(alpha, 255, 0)
    data = pixels.tobytes()
    entries = [(256, 4, 1, width), (257, 4, 1, height), (259, 3, 1, 1), (262, 3, 1, 2), (273, 4, 1, 8), (277, 3, 1, 4),
               (278, 4, 1, height), (279, 4, 1, len(data))]
    bits = struct.pack("<4H", 8, 8, 8, 8)
    ifd = 8 + len(data) + (len(data) % 2)
    table = 2 + 12 * (len(entries) + 1) + 4
    rows = [struct.pack("<HHII", tag, kind, count, value) if kind == 4 else struct.pack("<HHIHH", tag, kind, count, value, 0)
            for tag, kind, count, value in entries]
    rows.append(struct.pack("<HHII", 258, 3, 4, ifd + table))
    rows.sort(key=lambda item: struct.unpack("<H", item[:2])[0])
    with open(path, "wb") as out:
        out.write(b"II" + struct.pack("<HI", 42, ifd) + data + b"\0" * (len(data) % 2))
        out.write(struct.pack("<H", len(rows)) + b"".join(rows) + struct.pack("<I", 0) + bits)


def test_the_alpha_band_of_a_small_export_becomes_the_mask(tmp_path):
    mask = diamond(60)
    path = str(tmp_path / "mask.tif")
    rgba_tiff(path, mask)
    assert np.array_equal(footprint.alpha_mask(path), mask)


def test_the_dxf_writes_a_polygon_clip_only_when_there_is_one():
    base = {"layer": "260704_0.1_ortho", "file": "00_Assets\\a.jp2", "size_px": (1000, 1000), "insert": (500.0, 300.0),
            "pixel": (0.1, 0.1)}
    unix = lambda text: "\n".join(text.splitlines()) + "\n"                          # le DXF est ecrit en CRLF
    plain = unix(dxfout.build_dxf([base]))
    clipped = unix(dxfout.build_dxf([dict(base, clip=[(100.0, 100.0), (900.0, 100.0), (500.0, 900.0), (100.0, 100.0)])]))
    assert "\n71\n1\n" in plain and "\n280\n0\n" in plain and "\n70\n3\n" in plain
    assert "\n71\n2\n" in clipped and "\n91\n4\n" in clipped and "\n280\n1\n" in clipped and "\n70\n7\n" in clipped
    lines = clipped.split("\n")
    entity = lines[lines.index("ENTITIES"):lines.index("OBJECTS")]
    codes = entity[1::2]
    assert codes.count("14") == 4 and codes.count("24") == 4


def test_an_open_clip_polygon_is_closed_because_autocad_rejects_the_whole_dxf_otherwise():
    base = {"layer": "l", "file": "a.jp2", "size_px": (1000, 1000), "insert": (0.0, 0.0), "pixel": (0.1, 0.1)}
    open_polygon = [(100.0, 100.0), (900.0, 100.0), (500.0, 900.0)]
    text = "\n".join(dxfout.build_dxf([dict(base, clip=open_polygon)]).splitlines())
    assert "\n91\n4\n" in text                                                          # three vertices + the first one again
    assert dxfout.closed_polygon(open_polygon + [open_polygon[0]]) == open_polygon + [open_polygon[0]]
    assert dxfout.closed_polygon([(1.0, 1.0), (2.0, 2.0)]) is None and dxfout.closed_polygon(None) is None
    plain = "\n".join(dxfout.build_dxf([dict(base, clip=[(1.0, 1.0), (2.0, 2.0)])]).splitlines())
    assert "\n71\n1\n" in plain                                                         # a degenerate outline is no clip
