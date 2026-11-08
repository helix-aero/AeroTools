"""Aero-Exports: tiling (pure functions, no Qt, no Metashape, no rasterio).

All coordinate arithmetic is done with exact fractions (``fractions.Fraction`` built
from the decimal text: 0.03 is exactly 3/100), never with floats: a 300 m tile at 3 cm
is EXACTLY 10,000 pixels and its bottom-left corner is an exact multiple of 3 cm. Floats
only appear on output (``float(...)``), for the file or the Metashape API.

Vocabulary
    cell      grid square (xmin, ymin, xmax, ymax), always whole, aligned on the pixel
    tile      what is exported: the cell, cropped to the data extent (edge='crop') or whole
              (edge='full'), enlarged by the overlap
    row R     1 = the northernmost; column C: 1 = the westernmost (image convention)

Two naming schemes (see tile_name): 'rc' (R02C05) and 'xy' (E2179_N1208, bottom-left corner in km,
otherwise in metres).

Python 3.9, no dependency.
"""

import math
from collections import namedtuple
from decimal import Decimal
from fractions import Fraction

# Documented HARD limits (per tile side, in pixels, or in bytes) and HELIX recommendations.
# The recommendations are not limits: they are warning thresholds to be validated in the field.
FORMAT_LIMITS = {
    "jpeg": {"max_side_px": 65500, "source": "libjpeg : JPEG_MAX_DIMENSION = 65500"},
    "tiff": {"max_file_bytes": 4 * 1024 ** 3, "source": "classic TIFF: 32-bit offsets (4 GiB); beyond that BigTIFF"},
    "png": {"max_side_px": 2 ** 31 - 1, "source": "PNG specification"},
    "jp2": {"max_side_px": 2 ** 32 - 1, "source": "JPEG 2000: 32-bit sizes"},
    "ecw": {"recommended_max_px": 30000, "source": "recommendation (ECW: no hard limit found; AutoCAD copes badly beyond it)"},
}

Tile = namedtuple("Tile", "row col ix iy cell bounds width_px height_px partial overlap_px")
Tile.__doc__ = ("row/col: 1-based numbering (R1 = north, C1 = west); ix/iy: absolute index of the cell in the original grid "
                "(iy towards the north); cell: (xmin,ymin,xmax,ymax) Fractions; bounds: exported extent; "
                "partial: the tile is smaller than the cell (edge); overlap_px: overlap added.")


# ---------------------------------------------------------------------------
# Exact fractions
# ---------------------------------------------------------------------------

def to_fraction(value):
    """Convert a number or a text into an exact Fraction through its decimal form (0.03 -> 3/100)."""
    if isinstance(value, Fraction):
        return value
    if isinstance(value, float):
        return Fraction(Decimal(repr(value)))
    return Fraction(Decimal(str(value)))


def _lcm(a, b):
    return a * b // math.gcd(a, b)


def lcm_fraction(values):
    """Least common multiple of positive fractions: lcm(3/100, 1/10) = 3/10."""
    fr = [to_fraction(v) for v in values]
    if not fr or any(f <= 0 for f in fr):
        raise ValueError("invalid pixel sizes: %r" % (values,))
    num = 1
    den = 0
    for f in fr:
        num = _lcm(num, f.numerator)
        den = math.gcd(den, f.denominator)
    return Fraction(num, den)


def floor_to(value, step):
    """Largest multiple of `step` <= value (exact)."""
    value, step = to_fraction(value), to_fraction(step)
    return math.floor(value / step) * step


def ceil_to(value, step):
    value, step = to_fraction(value), to_fraction(step)
    return math.ceil(value / step) * step


def is_multiple(value, step):
    return (to_fraction(value) / to_fraction(step)).denominator == 1


# ---------------------------------------------------------------------------
# Grid step
# ---------------------------------------------------------------------------

def snap_tile_size(tile_m, pixel_sizes):
    """Tile size (m) rounded to the nearest multiple of the lcm of the pixel sizes.

    `pixel_sizes`: all the pixel sizes that will share the grid (orthomosaic AND DTM), so that
    the same tile falls exactly on the pixels of each. Returns (Fraction, exact) where `exact` tells whether the
    requested size was already valid. E.g.: (300, [0.03, 0.10]) -> (300, True); (250, [0.03]) -> (249.99, False).
    """
    unit = lcm_fraction(pixel_sizes)
    wanted = to_fraction(tile_m)
    if wanted <= 0:
        raise ValueError("invalid tile size: %r" % (tile_m,))
    n = max(1, int((wanted / unit) + Fraction(1, 2)))     # round to nearest (half up)
    return n * unit, (n * unit == wanted)


def nice_tile_sizes(pixel_sizes, around_m, count=5):
    """A few tile sizes 'round in metres' compatible with the pixels, around `around_m`.

    A round size = a whole number of metres AND a multiple of the lcm of the pixels. Used to fill the drop-down
    list of the interface (e.g. at 3 cm: 150, 300, 600 m; at 5 cm: 100, 200, 250 m...).
    """
    unit = lcm_fraction(pixel_sizes)
    found = []
    lo, hi = max(1, int(around_m / 4)), int(around_m * 4) + 1
    for meters in range(lo, hi + 1):
        if is_multiple(meters, unit):
            found.append(meters)
    found.sort(key=lambda m: abs(m - around_m))
    return sorted(found[:count])


def snap_origin(x, y, round_to_m, pixel_sizes):
    """Bottom-left corner of the grid, rounded DOWN to the smallest step that is both a multiple of
    `round_to_m` AND a multiple of the pixels (lcm). E.g.: 100 m and 3 cm -> 300 m step (100 m alone does not
    fall on the 3 cm grid: 100 / 0.03 is not an integer). Returns (ox, oy) Fractions."""
    unit = lcm_fraction(pixel_sizes)
    step = lcm_fraction([to_fraction(round_to_m), unit])
    return floor_to(x, step), floor_to(y, step)


# ---------------------------------------------------------------------------
# Grid
# ---------------------------------------------------------------------------

def tile_grid(bounds, tile_w_m, tile_h_m=None, pixel_sizes=(0.03,), origin=None, round_to_m=None,
              overlap_m=0, edge="crop", footprint=None, order="rows_north_first", max_tiles=100000):
    """Compute the tiles covering `bounds` = (xmin, ymin, xmax, ymax) (coordinates of the output CRS, m).

    pixel_sizes : pixel sizes sharing the grid (orthomosaic + DTM); the first one is used for the `*_px` counts.
    origin      : (x, y) bottom-left corner of the grid; otherwise rounded to `round_to_m` (default: the tile size).
    overlap_m   : overlap added on each side, rounded up to the pixel.
    edge        : 'crop' (edge tile cropped to the extent, aligned on the pixel) or 'full' (whole cell).
    footprint   : function (xmin, ymin, xmax, ymax) -> bool; False = empty tile, skipped (e.g. data extent).
    order       : 'rows_north_first' (R1C1 at top left, by rows) or 'cols_west_first'.
    Returns a list of `Tile`, order guaranteed. Raises ValueError if the tile size is not an exact multiple
    of the pixels (use snap_tile_size) or if there would be more than `max_tiles` tiles.
    """
    if edge not in ("crop", "full"):
        raise ValueError("edge: 'crop' or 'full'")
    px_list = [to_fraction(p) for p in pixel_sizes]
    unit = lcm_fraction(px_list)
    pix = px_list[0]
    tw = to_fraction(tile_w_m)
    th = to_fraction(tile_h_m if tile_h_m is not None else tile_w_m)
    for label, size in (("width", tw), ("height", th)):
        if size <= 0 or not is_multiple(size, unit):
            raise ValueError("tile %s %s m: not an exact multiple of %s m (lcm of the pixels). See snap_tile_size."
                             % (label, float(size), float(unit)))
    xmin, ymin, xmax, ymax = [to_fraction(v) for v in bounds]
    if xmax <= xmin or ymax <= ymin:
        raise ValueError("empty extent: %r" % (bounds,))
    if origin is None:
        step = to_fraction(round_to_m) if round_to_m else min(tw, th)
        ox, oy = snap_origin(xmin, ymin, step, px_list)
    else:
        ox, oy = to_fraction(origin[0]), to_fraction(origin[1])
        if not (is_multiple(ox, unit) and is_multiple(oy, unit)):
            raise ValueError("origin (%s, %s) not aligned on the pixels (multiple of %s m)" % (float(ox), float(oy), float(unit)))
    if ox > xmin or oy > ymin:
        raise ValueError("the grid origin must be at the bottom left of the data extent")
    ov = to_fraction(overlap_m)
    ov = ceil_to(ov, pix) if ov > 0 else Fraction(0)

    n_cols = math.ceil((xmax - ox) / tw)
    n_rows = math.ceil((ymax - oy) / th)
    if n_cols * n_rows > max_tiles:
        raise ValueError("%d tiles: too many (max %d), increase the tile size" % (n_cols * n_rows, max_tiles))

    cells = []
    for iy in range(n_rows):
        for ix in range(n_cols):
            cell = (ox + ix * tw, oy + iy * th, ox + (ix + 1) * tw, oy + (iy + 1) * th)
            if edge == "crop":
                b = (max(cell[0], floor_to(xmin, pix)), max(cell[1], floor_to(ymin, pix)),
                     min(cell[2], ceil_to(xmax, pix)), min(cell[3], ceil_to(ymax, pix)))
            else:
                b = cell
            if b[2] <= b[0] or b[3] <= b[1]:
                continue
            if footprint is not None and not footprint(*[float(v) for v in b]):
                continue
            partial = (b != cell)
            if ov > 0:
                b = (max(b[0] - ov, floor_to(xmin, pix)) if edge == "crop" else b[0] - ov,
                     max(b[1] - ov, floor_to(ymin, pix)) if edge == "crop" else b[1] - ov,
                     min(b[2] + ov, ceil_to(xmax, pix)) if edge == "crop" else b[2] + ov,
                     min(b[3] + ov, ceil_to(ymax, pix)) if edge == "crop" else b[3] + ov)
            w_px = int((b[2] - b[0]) / pix)
            h_px = int((b[3] - b[1]) / pix)
            cells.append((ix, iy, cell, b, w_px, h_px, partial, int(ov / pix)))

    tiles = []
    for ix, iy, cell, b, w_px, h_px, partial, ov_px in cells:
        tiles.append(Tile(row=n_rows - iy, col=ix + 1, ix=ix, iy=iy, cell=cell, bounds=b, width_px=w_px,
                          height_px=h_px, partial=partial, overlap_px=ov_px))
    if order == "rows_north_first":
        tiles.sort(key=lambda t: (t.row, t.col))
    elif order == "cols_west_first":
        tiles.sort(key=lambda t: (t.col, t.row))
    else:
        raise ValueError("order: 'rows_north_first' or 'cols_west_first'")
    return tiles


def grid_origin(tiles):
    """Bottom-left corner of the grid from the tiles (Fractions): cell ix=0, iy=0 deduced."""
    t = tiles[0]
    tw = t.cell[2] - t.cell[0]
    th = t.cell[3] - t.cell[1]
    return t.cell[0] - t.ix * tw, t.cell[1] - t.iy * th


# ---------------------------------------------------------------------------
# Tile names
# ---------------------------------------------------------------------------

def _best_unit_m(tile, ox, oy):
    """Largest unit among 1000, 100, 10, 1 m that divides the cell and the origin (otherwise 1 m)."""
    tw = tile.cell[2] - tile.cell[0]
    th = tile.cell[3] - tile.cell[1]
    for unit in (1000, 100, 10, 1):
        if all(is_multiple(v, unit) for v in (tw, th, ox, oy)):
            return unit
    return 1


def tile_name(tile, scheme="rc", n_rows=None, n_cols=None, unit_m=None, origin=None):
    """Name token of a tile.

    'rc' : R02C05 (width = 2 digits, 3 if more than 99 rows / columns). Short, but depends on the extent:
           if the site grows to the west, C01 changes place.
    'xy' : E2179_N1208 = bottom-left corner of the CELL (not of the cropped tile) in km if the grid is on the km
           (unit 1000), otherwise in whole metres (E2179500_N1208000); stable from one date to the next as long as
           the grid does not change, hence preferable for a monthly follow-up of the same site. If the grid is not in
           whole metres (exotic pixel), the unit falls to 1 m and the name rounds: check collisions
           (tile_names checks).
    """
    if scheme == "rc":
        width = 3 if max(n_rows or tile.row, n_cols or tile.col) > 99 else 2
        return "R%0*dC%0*d" % (width, tile.row, width, tile.col)
    if scheme == "xy":
        ox, oy = origin if origin is not None else (Fraction(0), Fraction(0))
        unit = unit_m or _best_unit_m(tile, ox, oy)
        e = math.floor(tile.cell[0] / unit)
        n = math.floor(tile.cell[1] / unit)
        return "E%d_N%d" % (e, n)
    raise ValueError("scheme: 'rc' or 'xy'")


def tile_names(tiles, scheme="rc"):
    """Names of all the tiles; raises ValueError on a collision. Returns {(row, col): token}."""
    if not tiles:
        return {}
    n_rows = max(t.row for t in tiles)
    n_cols = max(t.col for t in tiles)
    ox, oy = grid_origin(tiles)
    unit = _best_unit_m(tiles[0], ox, oy) if scheme == "xy" else None
    listed = [((t.row, t.col), tile_name(t, scheme, n_rows, n_cols, unit, (ox, oy))) for t in tiles]
    if len(set(name for _key, name in listed)) != len(listed):
        raise ValueError("tile name collision (scheme %s): change the scheme or the unit" % scheme)
    return dict(listed)


# ---------------------------------------------------------------------------
# Size warnings
# ---------------------------------------------------------------------------

def tile_size_warnings(width_px, height_px, bands, bytes_per_sample, fmt):
    """List of warning texts (short keys) for a tile of this size and format."""
    out = []
    raw = width_px * height_px * bands * bytes_per_sample
    limits = FORMAT_LIMITS.get(fmt, {})
    side = limits.get("max_side_px")
    if side and max(width_px, height_px) > side:
        out.append(("side_limit", "%s: %d px > %d px per side (%s)" % (fmt, max(width_px, height_px), side, limits["source"])))
    cap = limits.get("max_file_bytes")
    if cap and raw > cap:
        out.append(("bigtiff", "%s: %.1f GiB uncompressed, may exceed %d GiB: BigTIFF or smaller tiles"
                    % (fmt, raw / 1024 ** 3, cap // 1024 ** 3)))
    rec = limits.get("recommended_max_px")
    if rec and max(width_px, height_px) > rec:
        out.append(("recommended", "%s: %d px > %d px recommended per side" % (fmt, max(width_px, height_px), rec)))
    return out


def estimate_grid(bounds, tile_m, pixel_m):
    """(columns, rows, pixels per tile) for a quick preview in the interface."""
    xmin, ymin, xmax, ymax = [to_fraction(v) for v in bounds]
    tw = to_fraction(tile_m)
    return math.ceil((xmax - xmin) / tw), math.ceil((ymax - ymin) / tw), int(tw / to_fraction(pixel_m))
