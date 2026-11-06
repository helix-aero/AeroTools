"""Aero-Exports: DEM image in colours and shading, without GDAL. One pure module (numpy, `tiffio`, `look`) shared by the export
(the image placed in the DXF) and the preview window (`preview.py`): what the user sees is what the export paints.

The look (palette, shading style, sun, strength, colour range) is a `look.Look`. With the default look the picture is the one
Metashape's Ortho tab shows: its palette between the lowest and the highest height, times a Horn hillshade from the north-west
at 45 degrees (strength 0.8), white where there is no data. The DEM is read block by block (with one row of overlap for the
shading) and the RGB image is written in Deflate with the DEM's georeferencing.
"""

import struct
import zlib

import numpy as np

from . import look as looks
from . import tiffio

METASHAPE_RAMP = looks.METASHAPE_RAMP
NODATA_LIMIT = -9000.0           # a DEM lower than this is "no data" (Metashape writes -32767)
WHITE = 255
WHITE_RGB = (255, 255, 255)
AZIMUTH, ALTITUDE = looks.DEFAULT.azimuth, looks.DEFAULT.altitude
SHADE_STRENGTH = looks.DEFAULT.strength
SLOPE_FULL_DEG = 45.0            # slope shading: this slope and steeper get the darkest tone
CHUNK = 1024                     # rows processed at once
HIST_BINS = 2048                 # resolution of the automatic colour range (percentiles)
AUTO_PERCENT = (2.0, 98.0)       # automatic colour range: ignore the lowest and highest 2 %
GEO_TAGS = (tiffio.T_SCALE, tiffio.T_TIEPOINT, tiffio.T_GEOKEYS, 34736, 34737)


# --------------------------------------------------------------------------------------------------
# Palette
# --------------------------------------------------------------------------------------------------

def _table(stops, colors):
    index = np.arange(256, dtype=np.float64)
    return np.stack([np.interp(index, stops, colors[:, channel]) for channel in range(3)], axis=1).round().astype(np.uint8)


def lookup_table(ramp=METASHAPE_RAMP):
    """Table of 256 colours (256 x 3, uint8) interpolated linearly between the palette stops (percent, R, G, B)."""
    stops = np.array([item[0] for item in ramp], dtype=np.float64) / 100.0 * 255.0
    return _table(stops, np.array([item[1:] for item in ramp], dtype=np.float64))


def table_for(entry):
    """256 x 3 table of a palette of look.palettes() (stops: (position 0..1, (R, G, B)))."""
    stops = np.array([position for position, _rgb in entry["stops"]], dtype=np.float64) * 255.0
    return _table(stops, np.array([rgb for _position, rgb in entry["stops"]], dtype=np.float64))


# --------------------------------------------------------------------------------------------------
# Shading
# --------------------------------------------------------------------------------------------------

def slopes(z, pixel_x, pixel_y):
    """(west-east, south-north) slopes of the inside of `z` (Horn's formula, as gdaldem): 2 rows and 2 columns fewer."""
    a, b, c = z[:-2, :-2], z[:-2, 1:-1], z[:-2, 2:]
    d, f = z[1:-1, :-2], z[1:-1, 2:]
    g, h, i = z[2:, :-2], z[2:, 1:-1], z[2:, 2:]
    west_east = ((a + 2 * d + g) - (c + 2 * f + i)) / (8.0 * pixel_x)
    south_north = ((g + 2 * h + i) - (a + 2 * b + c)) / (8.0 * pixel_y)
    return west_east, south_north


def _lit(west_east, south_north, azimuths, altitude):
    """Light received relative to flat ground (1 = as flat ground), averaged over the lights `azimuths` (degrees from the north)."""
    steep = west_east ** 2 + south_north ** 2
    aspect = np.arctan2(south_north, west_east)
    lift = np.radians(altitude)
    root, norm = np.sqrt(steep), np.sqrt(1.0 + steep)
    total = 0.0
    for azimuth in azimuths:
        total = total + (np.sin(lift) - np.cos(lift) * root * np.cos(aspect - np.radians(azimuth))) / norm
    return total / len(azimuths) / np.sin(lift)


def hillshade(z, pixel_x, pixel_y, strength=SHADE_STRENGTH, azimuth=AZIMUTH, altitude=ALTITUDE):
    """Light factor (1 = flat ground) of the inside of `z`: a 2 rows / 2 columns smaller array."""
    west_east, south_north = slopes(z, pixel_x, pixel_y)
    return 1.0 + strength * (_lit(west_east, south_north, (azimuth,), altitude) - 1.0)


def shade_factor(z, pixel_x, pixel_y, look):
    """Brightness factor of the inside of `z` for a look (None for "colours only"). 1 = unchanged, under 1 = darker."""
    if look.shading == looks.NONE or look.strength <= 0:
        return None
    west_east, south_north = slopes(z, pixel_x, pixel_y)
    if look.shading == looks.SLOPE:
        degrees = np.degrees(np.arctan(np.sqrt(west_east ** 2 + south_north ** 2)))
        return 1.0 - look.strength * np.clip(degrees / SLOPE_FULL_DEG, 0.0, 1.0)
    if look.shading == looks.LIGHTS4:
        azimuths = tuple((look.azimuth + 90.0 * step) % 360.0 for step in range(4))
    else:
        azimuths = (look.azimuth,)
    return 1.0 + look.strength * (_lit(west_east, south_north, azimuths, look.altitude) - 1.0)


def shade_block(z, nodata, low, pixel_x, pixel_y, look, dtype=np.float64):
    """Brightness factor for every pixel of `z` (same shape), or None. Pixels without data take the lowest height first, and
    the edge is repeated, so the shading is defined up to the borders."""
    if look.shading == looks.NONE or look.strength <= 0:
        return None
    filled = fill_for_shading(z, nodata_mask(z, nodata), low, dtype)
    return shade_factor(np.pad(filled, 1, mode="edge"), pixel_x, pixel_y, look)


def fill_for_shading(z, void, low, dtype=np.float64):
    """Copy of `z` where each no-data pixel touching data takes the mean of its data neighbours, so the slope stops at the edge of
    the data instead of seeing a cliff down to `low` (that drew a dark line along every border). Other no-data pixels take `low`."""
    filled = np.where(void, 0, z).astype(dtype)
    if void.any() and not void.all():
        valid = ~void
        padded_z, padded_v = np.pad(filled, 1), np.pad(valid, 1)
        total = np.zeros_like(filled)
        count = np.zeros(filled.shape, dtype=np.int32)
        rows, cols = filled.shape
        for dr in (0, 1, 2):
            for dc in (0, 1, 2):
                near = padded_v[dr:dr + rows, dc:dc + cols]
                total += np.where(near, padded_z[dr:dr + rows, dc:dc + cols], 0)
                count += near
        grow = void & (count > 0)
        filled[grow] = total[grow] / count[grow]
        valid = valid | grow
        filled[~valid] = low
    elif void.all():
        filled[:] = low
    return filled


# --------------------------------------------------------------------------------------------------
# Colours
# --------------------------------------------------------------------------------------------------

def nodata_mask(z, nodata=None):
    """Pixels without data: not finite, very low, or equal to the "no data" value declared in the file."""
    bad = ~np.isfinite(z) | (z < NODATA_LIMIT)
    return bad if nodata is None else bad | (z == nodata)


_nodata_mask = nodata_mask                       # older name, still used by footprint.py


def declared_nodata(tiff):
    """The "no data" value written in the TIFF (GDAL tag), or None."""
    try:
        return float(tiff.text(tiffio.T_GDAL_NODATA).strip())
    except ValueError:
        return None


def value_range(path):
    """(minimum, maximum) of the valid heights of the DEM, or None when there are none."""
    low, high = None, None
    tiff = tiffio.Tiff(path)
    nodata = declared_nodata(tiff)
    for _top, _left, block in tiff.blocks():
        good = block[~nodata_mask(block, nodata)]
        if good.size:
            low = float(good.min()) if low is None else min(low, float(good.min()))
            high = float(good.max()) if high is None else max(high, float(good.max()))
    return None if low is None else (low, high)


def percentile_range(path, span, percents=AUTO_PERCENT, bins=HIST_BINS):
    """(low, high) heights under which `percents` of the valid pixels lie, from a histogram of `bins` classes between the
    minimum and the maximum `span` (precision: span / bins)."""
    low, high = span
    if high <= low:
        return span
    tiff = tiffio.Tiff(path)
    nodata = declared_nodata(tiff)
    counts = np.zeros(bins, dtype=np.int64)
    for _top, _left, block in tiff.blocks():
        good = block[~nodata_mask(block, nodata)]
        if good.size:
            counts += np.histogram(good, bins=bins, range=(low, high))[0]
    total = int(counts.sum())
    if total == 0:
        return span
    cumulative = np.cumsum(counts)
    edges = np.linspace(low, high, bins + 1)
    first = int(np.searchsorted(cumulative, total * percents[0] / 100.0, side="left"))
    last = int(np.searchsorted(cumulative, total * percents[1] / 100.0, side="left"))
    result = (float(edges[min(first, bins)]), float(edges[min(last + 1, bins)]))
    return result if result[1] > result[0] else span


def choose_range(look, span, percentile=None):
    """(low, high) colour range of a look. `span`: exact (minimum, maximum). `percentile`: function giving the automatic range,
    called only for "auto". A custom range that makes no sense falls back to the full one."""
    if look.stretch == looks.CUSTOM and look.low is not None and look.high is not None and look.high > look.low:
        return look.low, look.high
    if look.stretch == looks.AUTO and percentile is not None:
        return percentile()
    return span


def colorize(z, low, high, table, shade=None, nodata=None, void=WHITE_RGB):
    """RGB image (rows x columns x 3, uint8): colour by height, shading multiplied, `void` colour where there is no data."""
    span = max(high - low, 1e-9)
    missing = nodata_mask(z, nodata)
    index = np.clip(np.rint((np.where(missing, z.dtype.type(low), z) - low) / span * 255.0), 0, 255).astype(np.int64)
    rgb = table[np.where(missing, 0, index)].astype(np.float32)
    if shade is not None:
        rgb *= shade[:, :, None].astype(np.float32)
    out = np.clip(np.rint(rgb), 0, 255).astype(np.uint8)
    out[missing] = void
    return out


# --------------------------------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------------------------------

def pixel_size(tiff):
    scale = tiff.ints(tiffio.T_SCALE)
    return (abs(scale[0]), abs(scale[1])) if scale else (1.0, 1.0)


def render(dem_path, out_path, look=None, tick=None, level=6):
    """Float32 GeoTIFF DEM -> RGB GeoTIFF with the look (default: Metashape's). Returns (ok, message)."""
    try:
        look = looks.normalized(looks.DEFAULT if look is None else look)
        tiff = tiffio.Tiff(dem_path)
        span = value_range(dem_path)
        if span is None:
            return False, "DEM without data"
        low, high = choose_range(look, span, lambda: percentile_range(dem_path, span))
        pixel_x, pixel_y = pixel_size(tiff)
        geographic = pixel_x < 0.01          # degrees: no slope in metres, so no shading
        z = tiff.read_all()
        nodata = declared_nodata(tiff)
        table = table_for(looks.palette(look.palette))
        compressed = []
        for top in range(0, tiff.height, CHUNK):
            bottom = min(tiff.height, top + CHUNK)
            part = z[top:bottom]
            shade = None
            if not geographic:
                lo, hi = max(0, top - 1), min(tiff.height, bottom + 1)
                full = shade_block(z[lo:hi], nodata, low, pixel_x, pixel_y, look)
                shade = None if full is None else full[top - lo:top - lo + (bottom - top)]
            image = colorize(part, low, high, table, shade, nodata)
            compressed.append(zlib.compress(np.ascontiguousarray(image).tobytes(), level))
            if tick is not None:
                tick()
        _write_rgb(out_path, tiff, compressed)
    except (ValueError, OSError) as exc:
        return False, "Coloured relief: %s" % exc
    return True, ""


def _write_rgb(path, source, compressed):
    """8-bit RGB TIFF in Deflate strips; the DEM's georeferencing is copied, as with gdaldem."""
    end = "<"
    tags = {
        tiffio.T_WIDTH: (4, 1, struct.pack(end + "I", source.width)),
        tiffio.T_HEIGHT: (4, 1, struct.pack(end + "I", source.height)),
        tiffio.T_BITS: (3, 3, struct.pack(end + "HHH", 8, 8, 8)),
        262: (3, 1, struct.pack(end + "H", 2)),                                      # photometric: RGB
        tiffio.T_SAMPLES: (3, 1, struct.pack(end + "H", 3)),
        tiffio.T_ROWS_PER_STRIP: (4, 1, struct.pack(end + "I", CHUNK)),
        284: (3, 1, struct.pack(end + "H", 1)),                                      # interleaved samples
    }
    if source.end == end:
        tags.update({tag: source.tags[tag] for tag in GEO_TAGS if tag in source.tags})
    writer = tiffio.Tiff.__new__(tiffio.Tiff)         # only its tag writer is used (see tiffio.Tiff._write)
    writer.tags, writer.tiled = tags, False
    writer._write(path, compressed, sum(len(item) for item in compressed) > tiffio.CLASSIC_LIMIT)
