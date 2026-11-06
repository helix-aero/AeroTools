"""Aero-Exports: model of the DEM preview (numpy only, no Qt, no Metashape).

A `Preview` holds a small version of the DEM (about 1000 pixels on the long side) and paints it with a `look.Look` through
the same functions as the export (`relief`): the picture in the preview window is the picture that lands in the DXF.
Painting the whole preview takes 30 to 60 ms, so the window can repaint while a slider moves, without a thread.

The small DEM comes from a low-resolution export by Metashape (`fetch_small`), or from a DEM already exported next to the
project (`usable_export`). It is shrunk by averaging the valid pixels of each square of `k` x `k` pixels (`reduce`).
"""

import math
import os

import numpy as np

from . import look as looks
from . import plan, relief, tiffio

TARGET = 1000                     # pixels on the long side of the preview
SAMPLE = 400000                   # values used for the automatic colour range
TRUST = 0.01                      # Metashape's own min / max are used when the small DEM stays within this part of them


def reduction_factor(width, height, target=TARGET):
    """Smallest power of two `k` so that the long side divided by `k` is at most 1.25 x `target`."""
    k = 1
    while max(width, height) / float(k) > target * 1.25:
        k *= 2
    return k


def reduce(tiff, k):
    """(grid, pixel_x, pixel_y): the DEM of an open `tiffio.Tiff` averaged over squares of `k` x `k` pixels. Valid pixels only;
    NaN where a square has none. Tiles are summed as they are read, the whole DEM is never in memory."""
    nodata = relief.declared_nodata(tiff)
    height, width = tiff.height, tiff.width
    out_h, out_w = -(-height // k), -(-width // k)
    total = np.zeros((out_h, out_w), dtype=np.float64)
    count = np.zeros((out_h, out_w), dtype=np.int64)
    for top, left, block in tiff.blocks():
        rows, cols = min(block.shape[0], height - top), min(block.shape[1], width - left)
        if rows <= 0 or cols <= 0:
            continue
        block = block[:rows, :cols]
        valid = ~relief.nodata_mask(block, nodata)
        if top % k == 0 and left % k == 0:
            pad_h, pad_w = -(-rows // k) * k, -(-cols // k) * k
            values = np.zeros((pad_h, pad_w), dtype=np.float64)
            values[:rows, :cols] = np.where(valid, block, 0.0)
            seen = np.zeros((pad_h, pad_w), dtype=np.int64)
            seen[:rows, :cols] = valid
            r0, c0 = top // k, left // k
            total[r0:r0 + pad_h // k, c0:c0 + pad_w // k] += values.reshape(pad_h // k, k, pad_w // k, k).sum(axis=(1, 3))
            count[r0:r0 + pad_h // k, c0:c0 + pad_w // k] += seen.reshape(pad_h // k, k, pad_w // k, k).sum(axis=(1, 3))
        else:                                    # a block that does not start on a square (long thin strips): slower, general
            cell = ((top + np.arange(rows)) // k)[:, None] * out_w + ((left + np.arange(cols)) // k)[None, :]
            index = cell[valid]
            total += np.bincount(index, weights=block[valid].astype(np.float64), minlength=out_h * out_w).reshape(out_h, out_w)
            count += np.bincount(index, minlength=out_h * out_w).reshape(out_h, out_w)
    with np.errstate(invalid="ignore", divide="ignore"):
        grid = np.where(count > 0, total / np.maximum(count, 1), np.nan).astype(np.float32)
    pixel_x, pixel_y = relief.pixel_size(tiff)
    return grid, pixel_x * k, pixel_y * k


def nice_decimals(span):
    """Decimals that tell the five labels of a legend apart."""
    step = abs(span) / 4.0
    return 0 if step >= 100 else 1 if step >= 10 else 2 if step >= 0.1 else 3


class Preview(object):
    """A small DEM and everything needed to paint it: `grid` (NaN = no data), the pixel size (metres, or degrees), and `exact`,
    the exact (minimum, maximum) of the full DEM when Metashape gave it."""

    def __init__(self, grid, pixel_x, pixel_y, exact=None):
        self.grid = np.asarray(grid, dtype=np.float32)
        self.pixel_x, self.pixel_y = float(pixel_x), float(pixel_y)
        self.void = relief.nodata_mask(self.grid)
        good = self.grid[~self.void]
        self.grid_range = (float(good.min()), float(good.max())) if good.size else None
        self.exact = self._checked(exact)
        self._sample = None

    @classmethod
    def from_tiff(cls, path, exact=None, target=TARGET):
        tiff = tiffio.Tiff(path)
        grid, px, py = reduce(tiff, reduction_factor(tiff.width, tiff.height, target))
        return cls(grid, px, py, exact)

    def _checked(self, exact):
        """Metashape's minimum and maximum, when they are sane and bracket the small DEM; otherwise the small DEM's own."""
        try:
            low, high = float(exact[0]), float(exact[1])
        except (TypeError, ValueError, IndexError):
            return None
        if not (math.isfinite(low) and math.isfinite(high)) or high <= low or self.grid_range is None:
            return None
        slack = TRUST * (high - low)
        if self.grid_range[0] < low - slack or self.grid_range[1] > high + slack:
            return None
        return low, high

    @property
    def valid(self):
        return self.grid_range is not None

    @property
    def geographic(self):
        """Pixels in degrees: slopes cannot be computed, so there is no shading (same rule as the export)."""
        return self.pixel_x < 0.01

    def span(self):
        """(minimum, maximum) height: the exact one of the full DEM if known, else the small DEM's."""
        return self.exact or self.grid_range

    def _percentile(self):
        if self._sample is None:
            values = self.grid[~self.void]
            self._sample = values[::max(1, values.size // SAMPLE)]
        low, high = np.percentile(self._sample, relief.AUTO_PERCENT)
        return (float(low), float(high)) if high > low else self.span()

    def colour_range(self, look):
        """(low, high) the colours are spread between, for a look."""
        return relief.choose_range(looks.normalized(look), self.span(), self._percentile)

    def render(self, look, void="transparent"):
        """RGBA picture (rows x columns x 4, uint8). `void`: "transparent" (no data is not drawn: AutoCAD hides it with the
        clip option) or "white" (the export paints it white)."""
        look = looks.normalized(look)
        low, high = self.colour_range(look)
        shade = None if self.geographic else relief.shade_block(self.grid, None, low, self.pixel_x, self.pixel_y, look,
                                                                 dtype=np.float32)
        rgb = relief.colorize(self.grid, low, high, relief.table_for(looks.palette(look.palette)), shade, None)
        alpha = np.full(self.grid.shape, 255, dtype=np.uint8)
        if void == "transparent":
            alpha[self.void] = 0
        return np.dstack([rgb, alpha])

    def legend(self, look, count=5):
        """What the colour bar shows: {"low", "high", "ticks": [(fraction 0..1 from the bottom, text), ...]}. The two ends are the
        exact colour range (for the full range: Metashape's exact minimum and maximum)."""
        low, high = self.colour_range(look)
        decimals = nice_decimals(high - low)
        ticks = []
        for index in range(count):
            fraction = index / float(count - 1)
            ticks.append((fraction, "{:.{d}f}".format(low + (high - low) * fraction, d=decimals)))
        return {"low": low, "high": high, "ticks": ticks}

    def colour_bar(self, look, length=256):
        """The palette as a column of `length` colours, highest first (rows x 3, uint8), for the legend."""
        table = relief.table_for(looks.palette(looks.normalized(look).palette))
        index = np.rint(np.linspace(255, 0, length)).astype(np.int64)
        return table[index]


# --------------------------------------------------------------------------------------------------
# Where the small DEM comes from
# --------------------------------------------------------------------------------------------------

def belongs_to(path, box, native):
    """True when the GeoTIFF `path` is the DEM of the chunk described by `box` (left, bottom, right, top) and `native`
    (pixel size in metres, width, height): same pixel, same size in pixels, same extent (within two pixels). Two chunks of
    the same site can have the same file name pattern, so the file itself is checked, not only its name and date."""
    from . import geotiff
    info = geotiff.read_geotiff(path)
    if info is None:
        return False
    pixel, width, height = native
    if info["width"] != width or info["height"] != height:
        return False
    if abs(info["pixel"][0] - pixel) > pixel * 1e-3 or abs(info["pixel"][1] - pixel) > pixel * 1e-3:
        return False
    tolerance = 2.0 * max(info["pixel"])
    return all(abs(a - b) <= tolerance for a, b in zip(geotiff.extent(info), box))


def usable_export(paths, psx=None, box=None, native=None):
    """First DEM file of `paths` that exists, is not older than the project file, and (when `box` and `native` are given, as
    they are for a chunk) really is this chunk's DEM on Metashape's own grid (see `belongs_to`), or None."""
    try:
        limit = os.path.getmtime(psx) if psx and os.path.isfile(psx) else None
    except OSError:
        limit = None
    for path in paths:
        try:
            if not os.path.isfile(path) or (limit is not None and os.path.getmtime(path) < limit):
                continue
            if box and native and not belongs_to(path, box, native):
                continue
            return path
        except OSError:
            continue
    return None


def fetch_small(export, chunk, box, work, target=TARGET, clip=True):
    """Ask Metashape (`export` is msapi.export_raster or a fake) for the DEM at about `target` pixels on the long side, in
    the project's coordinate system, into the folder `work`. Returns the file path. Raises ValueError.

    No progress callback is given: Metashape would call it in the middle of its computation, and touching a Qt widget at that
    moment crashes Metashape."""
    if not box:
        raise ValueError("no DEM extent")
    pixel = max(box[2] - box[0], box[3] - box[1]) / float(target)
    path = os.path.join(work, "preview_dem.tif")
    ok, error = export(chunk, plan.DEM, plan.spec_for("full", plan.DEM), path, pixel, box, big=False, progress=None, crs=None,
                     clip_to_boundary=clip)
    if not ok or not os.path.isfile(path):
        raise ValueError(error or "Metashape wrote no file")
    return path
