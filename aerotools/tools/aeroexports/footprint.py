"""Aero-Exports: real extent of the data of an orthomosaic or DEM, so that the DXF clips the white around it.

The DXF can carry a clip polygon on each image: AutoCAD then shows only the inside, the image file is not
modified. This module builds that polygon:
  - a "there is data here" mask at low resolution (alpha of a small orthomosaic export by Metashape, or valid pixels
    of the DEM);
  - its outline: for each row, from the first to the last data pixel (a simple polygon), simplified;
  - the conversion into pixels of the final image, in the DXF's convention.
Pure module (numpy): no dependency on Metashape.
"""

import zlib

import numpy as np

from . import relief, tiffio

T_PLANAR = 284
DECODERS = {1: lambda payload: payload, 8: zlib.decompress, 32946: zlib.decompress, 5: tiffio.lzw_decode}   # TIFF compression codes

TARGET_PX = 800                  # the mask is about 800 pixels on its long side
TOLERANCE_PX = 1.2               # outline simplification: 1 mask pixel at most


def alpha_mask(path):
    """Mask (rows x columns, True = data) of the alpha band of an 8-bit RGBA GeoTIFF (strips or tiles; uncompressed, Deflate or LZW)."""
    tiff = tiffio.Tiff(path, strict=False)
    samples = tiff.ints(tiffio.T_SAMPLES, [1])[0]
    bits = tiff.ints(tiffio.T_BITS, [8])
    if (any(b != 8 for b in bits) or samples < 4 or tiff.ints(T_PLANAR, [1])[0] != 1
            or tiff.compression not in DECODERS or tiff.predictor not in (1, 2)):
        raise ValueError("alpha: interleaved 8-bit RGBA expected (uncompressed, Deflate or LZW)")
    alpha = np.zeros((tiff.height, tiff.width), dtype=np.uint8)
    with open(path, "rb") as handle:
        for index, (offset, count) in enumerate(zip(tiff.offsets, tiff.counts)):
            handle.seek(offset)
            payload = handle.read(count)
            if tiff.tiled:
                row, col = divmod(index, tiff.across)
                top, left, rows, cols = row * tiff.block_h, col * tiff.block_w, tiff.block_h, tiff.block_w
            else:
                top, left, rows, cols = index * tiff.block_h, 0, min(tiff.block_h, tiff.height - index * tiff.block_h), tiff.width
            if top >= tiff.height or left >= tiff.width:
                continue
            try:
                data = np.frombuffer(DECODERS[tiff.compression](payload), dtype=np.uint8)
            except zlib.error:
                raise ValueError("alpha: damaged Deflate block")
            if data.size < rows * cols * samples:
                raise ValueError("alpha: block too short")
            block = data[:rows * cols * samples].reshape(rows, cols, samples)
            if tiff.predictor == 2:                                  # each sample is stored as the difference to its left neighbour
                block = np.cumsum(block, axis=1, dtype=np.uint8)
            part = block[:tiff.height - top, :tiff.width - left, 3]
            alpha[top:top + part.shape[0], left:left + part.shape[1]] = part
    return alpha > 0


def dem_mask(path, target=TARGET_PX):
    """Mask of the valid pixels of a DEM, reduced to at most `target` pixels on its long side."""
    tiff = tiffio.Tiff(path)
    nodata = relief.declared_nodata(tiff)
    step = max(1, int(round(max(tiff.width, tiff.height) / float(target))))
    mask = np.zeros((-(-tiff.height // step), -(-tiff.width // step)), dtype=bool)
    for top, left, block in tiff.blocks():
        good = ~relief.nodata_mask(block, nodata)
        rows, cols = np.nonzero(good)
        inside = (top + rows < tiff.height) & (left + cols < tiff.width)       # a tile on the edge can reach past the image
        rows, cols = rows[inside], cols[inside]
        np.logical_or.at(mask, ((top + rows) // step, (left + cols) // step), True)
    return mask, step


def outline(mask):
    """Outline of the mask: [(column, row), ...] on pixel edges (column 3 runs from 3 to 4), or [] without data.

    For each row, from the left edge of the first data pixel to the right edge of the last. A horizontal hole or notch
    is therefore filled (the white that remains is inside the extent)."""
    rows = np.nonzero(mask.any(axis=1))[0]
    if rows.size == 0:
        return []
    first = mask.argmax(axis=1)
    last = mask.shape[1] - mask[:, ::-1].argmax(axis=1)
    # left edge going down, then right edge going up
    left_chain = [(float(first[r]), float(r)) for r in rows] + [(float(first[rows[-1]]), float(rows[-1] + 1))]
    right_chain = [(float(last[rows[-1]]), float(rows[-1] + 1))] + [(float(last[r]), float(r)) for r in rows[::-1]]
    right_chain.append((float(last[rows[0]]), float(rows[0])))
    return _simplify(left_chain, TOLERANCE_PX) + _simplify(right_chain, TOLERANCE_PX)


def _simplify(points, tolerance):
    """Douglas-Peucker (iterative): keeps the points that deviate by more than `tolerance` from the neighbouring line."""
    if len(points) < 3:
        return list(points)
    pts = np.array(points, dtype=float)
    keep = np.zeros(len(pts), dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        p, q = pts[a], pts[b]
        chord = q - p
        length = float(np.hypot(*chord))
        inner = pts[a + 1:b]
        if length == 0.0:
            dist = np.hypot(inner[:, 0] - p[0], inner[:, 1] - p[1])
        else:
            dist = np.abs(chord[0] * (inner[:, 1] - p[1]) - chord[1] * (inner[:, 0] - p[0])) / length
        k = int(dist.argmax())
        if dist[k] > tolerance:
            keep[a + 1 + k] = True
            stack.extend(((a, a + 1 + k), (a + 1 + k, b)))
    return [tuple(point) for point in pts[keep]]


def _grow(mask):
    """Mask grown by one pixel in every direction: the polygon never trims data at the limit."""
    padded = np.pad(mask, 1)
    out = np.zeros_like(mask)
    for dr in (0, 1, 2):
        for dc in (0, 1, 2):
            out |= padded[dr:dr + mask.shape[0], dc:dc + mask.shape[1]]
    return out


def clip_polygon(mask, mask_extent, image_info):
    """Clip polygon of an image, in the coordinates of its IMAGE entity in the DXF.

    `mask_extent`: (left, bottom, right, top) of the mask in drawing units. `image_info`: {"width", "height", "origin",
    "pixel"} of the final image (geotiff.read_geotiff). The vertices are in image pixels, from its TOP-left corner
    (x to the right, y DOWN, like the rows of an image), the centre of the first pixel being (0, 0): the convention of the
    image space of the IMAGE entity in AutoCAD (checked in AutoCAD: measured from the bottom, the polygon appears flipped).
    The polygon is closed (last vertex = first).
    Returns [] if there is no data or if the polygon covers the whole image (nothing to clip)."""

    path = outline(_grow(mask))
    if len(path) < 3:
        return []
    left, bottom, right, top = mask_extent
    cell_x = (right - left) / float(mask.shape[1])
    cell_y = (top - bottom) / float(mask.shape[0])
    il, it = image_info["origin"]
    px, py = image_info["pixel"]
    width, height = image_info["width"], image_info["height"]
    out = []
    for col, row in path:
        x = left + col * cell_x
        y = top - row * cell_y
        # column from the left edge, row from the TOP edge of the image (y down)
        out.append((min(max((x - il) / px, 0.0), float(width)) - 0.5, min(max((it - y) / py, 0.0), float(height)) - 0.5))
    area_polygon = abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(out, out[1:] + out[:1]))) / 2.0
    if area_polygon >= 0.995 * width * height:
        return []
    return out + [out[0]]
