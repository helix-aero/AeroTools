"""Aero-Exports: height systems computed by AeroTools (geoid grids read and applied here, not by Metashape).

Why: Metashape 2.2.1 chooses its grid by the datum name and not by the file ("RAC23" and "RAC09" give the
same value, 23 cm off the true RAC23 grid in Corsica), ignores RAC23 with an RGF93 v2b frame, and refuses EGM96 with a
French frame. Here: Metashape exports the DTM in ellipsoidal heights (which it does well), then the geoid undulation
read from our `Geoid` folder is subtracted (`H = h - N`).

Grids: PROJ-data "GTG" GeoTIFF (Float32, one `geoid_undulation` band, Deflate + floating-point predictor), nodes regularly
spaced in longitude / latitude, bilinear interpolation like PROJ. Reference values: see tests/test_aeroexports_vertical.py.
"""

import hashlib
import math
import os

import numpy as np

from . import crs, projdb, tiffio

NODATA_LIMIT = -1000.0          # an undulation smaller than this is a "no data" value
LATTICE_STEP_M = 100.0          # spacing of the lattice of points where the undulation of a DTM is computed (it varies by a few cm per km)
MAX_LATTICE = 400


class Grid(object):
    """An undulation grid: `values[row, column]`, row 0 at the north; nodes at (lon0 + col * dlon, lat0 - row * dlat)."""

    def __init__(self, values, lon0, lat0, dlon, dlat):
        self.values, self.lon0, self.lat0, self.dlon, self.dlat = values, lon0, lat0, dlon, dlat
        self.rows, self.cols = values.shape

    def bounds(self):
        """(min lon, min lat, max lon, max lat) of the nodes."""
        return (self.lon0, self.lat0 - (self.rows - 1) * self.dlat, self.lon0 + (self.cols - 1) * self.dlon, self.lat0)

    def undulation(self, lon, lat):
        """N (metres) at the points (arrays or numbers); NaN outside the grid or on a node without data."""
        lon = np.asarray(lon, dtype=np.float64)
        lat = np.asarray(lat, dtype=np.float64)
        fx = (lon - self.lon0) / self.dlon
        fy = (self.lat0 - lat) / self.dlat
        inside = (fx >= 0) & (fx <= self.cols - 1) & (fy >= 0) & (fy <= self.rows - 1)
        fx = np.clip(fx, 0, self.cols - 1)
        fy = np.clip(fy, 0, self.rows - 1)
        x0 = np.minimum(np.floor(fx).astype(int), self.cols - 2)
        y0 = np.minimum(np.floor(fy).astype(int), self.rows - 2)
        tx, ty = fx - x0, fy - y0
        v = self.values
        corners = (v[y0, x0], v[y0, x0 + 1], v[y0 + 1, x0], v[y0 + 1, x0 + 1])
        bad = np.zeros(np.shape(fx), dtype=bool)
        for item in corners:
            bad |= ~np.isfinite(item) | (item < NODATA_LIMIT)
        value = (corners[0] * (1 - tx) * (1 - ty) + corners[1] * tx * (1 - ty) + corners[2] * (1 - tx) * ty + corners[3] * tx * ty)
        return np.where(inside & ~bad, value, np.nan).astype(np.float64)


def read_grid(path):
    """Grid of a GTG file, or ValueError. The georeferencing comes from the file: no assumed table."""
    tiff = tiffio.Tiff(path)
    scale = tiff.ints(tiffio.T_SCALE)
    tie = tiff.ints(tiffio.T_TIEPOINT)
    if not scale or not tie or len(tie) < 6:
        raise ValueError("grid without georeferencing")
    if _raster_type(tiff) != RASTER_PIXEL_IS_POINT:
        raise ValueError("\"PixelIsArea\" grid: node frame not verified")
    values = tiff.read_all()
    i, j, _k, x, y, _z = tie[:6]
    dlon, dlat = scale[0], scale[1]
    # PixelIsPoint: the tie point is the node (0, 0) itself. Checked against PROJ 9.8: 0.0 mm difference on 4 points of RAC23.
    return Grid(values, x - i * dlon, y + j * dlat, dlon, dlat)


RASTER_PIXEL_IS_POINT = 2


def _raster_type(tiff):
    """Value of the GeoTIFF key GTRasterTypeGeoKey (1 = PixelIsArea, 2 = PixelIsPoint); 1 if absent."""
    keys = tiff.ints(tiffio.T_GEOKEYS, [])
    for index in range(4, len(keys) - 3, 4):
        if keys[index] == 1025 and keys[index + 1] == 0:
            return keys[index + 3]
    return 1


# --------------------------------------------------------------------------------------------------
# Which grid for which frame (the rule comes from PROJ: see projdb.py)
# --------------------------------------------------------------------------------------------------

def compatible_verticals(epsg, lon=None, lat=None, folder=None):
    """Altitudes that can be offered with this horizontal CRS at position (lon, lat), worldwide: records of grids of the same frame
    (the most local first), then the ellipsoid. `folder`: if a grid is already there and does not cover the centre of the project
    (rectangular extent but data only over the country), it is left out."""
    out = []
    for spec in projdb.verticals_for(epsg, lon, lat):
        if folder and lon is not None and lat is not None and crs.grid_state(spec["id"], folder)[0] == "ok":
            try:
                if not np.isfinite(float(load_grid(spec["id"], folder).undulation(lon, lat))):
                    continue
            except ValueError:
                pass
        out.append(spec)
    out.append(crs.vertical_by_id(projdb.ELLIPSOID_ID))
    return out


# --------------------------------------------------------------------------------------------------
# Grid of an identifier, correction of a DTM
# --------------------------------------------------------------------------------------------------

_GRID_CACHE = {}


def load_grid(vertical_id, folder=None):
    """Grid of the requested altitude, read from our folder (verified by its fingerprint); ValueError otherwise."""
    state, path = crs.grid_state(vertical_id, folder)
    if state != "ok":
        raise ValueError("grid %s: %s" % (vertical_id, state))
    stamp = (path, os.path.getsize(path), os.path.getmtime(path))
    if stamp not in _GRID_CACHE:
        _GRID_CACHE.clear()
        _GRID_CACHE[stamp] = read_grid(path)
    return _GRID_CACHE[stamp]


def _lattice(width, height, px, py):
    """Number of points (columns, rows) of the lattice that covers a raster."""
    cols = int(min(MAX_LATTICE, max(2, math.ceil(width * px / LATTICE_STEP_M) + 1)))
    rows = int(min(MAX_LATTICE, max(2, math.ceil(height * py / LATTICE_STEP_M) + 1)))
    return cols, rows


def _bilinear(field, fx, fy):
    """Bilinear interpolation of `field` (rows x columns) at the fractional positions (fy, fx), broadcastable 2D arrays."""
    rows, cols = field.shape
    x0 = np.minimum(np.floor(fx).astype(int), cols - 2)
    y0 = np.minimum(np.floor(fy).astype(int), rows - 2)
    tx, ty = fx - x0, fy - y0
    return (field[y0, x0] * (1 - tx) * (1 - ty) + field[y0, x0 + 1] * tx * (1 - ty)
            + field[y0 + 1, x0] * (1 - tx) * ty + field[y0 + 1, x0 + 1] * tx * ty)


def correct_dem(src, dst, grid, lonlat, reference=None, progress=None, samples=5):
    """Write to `dst` the DTM `src` (ellipsoidal heights) converted to altitudes: `H = h - N`.

    `lonlat(xs, ys)`: (longitudes, latitudes) in degrees of points of the DTM (arrays, in the DTM's frame). N is computed on a
    lattice of points (one every ~100 m) then interpolated bilinearly at each pixel.
    * `grid`: our reading of the grid. `reference(lons, lats)`: N given by PROJ (authority); if supplied, it is the one that
      applies and it must agree with our computation to within 1 cm over the whole lattice (otherwise ValueError, nothing is written).
    * Check: `samples` pixels are recomputed directly (without the lattice); more than 5 mm of difference cancels everything (ValueError).
    Returns {"min": min N, "max": max N, "check_mm": check difference, "proj_mm": PROJ / ours difference or None}. Raises ValueError, and
    leaves `dst` absent, if the grid does not cover the whole DTM."""
    tiff = tiffio.Tiff(src)
    if _raster_type(tiff) == RASTER_PIXEL_IS_POINT:
        raise ValueError("\"PixelIsPoint\" DTM not supported")
    scale, tie = tiff.ints(tiffio.T_SCALE), tiff.ints(tiffio.T_TIEPOINT)
    if not scale or not tie:
        raise ValueError("DTM without georeferencing")
    px, py = float(scale[0]), float(scale[1])
    x0, y0 = tie[3] - tie[0] * px, tie[4] + tie[1] * py                 # top-left corner
    width, height = tiff.width, tiff.height
    nodata_text = tiff.text(tiffio.T_GDAL_NODATA).strip()
    nodata = float(nodata_text) if nodata_text else None

    cols, rows = _lattice(width, height, px, py)
    lx = x0 + np.linspace(0.0, width * px, cols)
    ly = y0 - np.linspace(0.0, height * py, rows)
    grid_x, grid_y = np.meshgrid(lx, ly)
    lon, lat = lonlat(grid_x.ravel(), grid_y.ravel())
    n_ours = grid.undulation(lon, lat).reshape(rows, cols)
    n_field, proj_mm = n_ours, None
    if reference is not None:
        n_ref = np.asarray(reference(lon, lat), dtype=np.float64).reshape(rows, cols)
        if not np.all(np.isfinite(n_ref)):
            raise ValueError("the grid does not cover the whole DTM")
        if np.all(np.isfinite(n_ours)):
            proj_mm = float(np.abs(n_ref - n_ours).max()) * 1000.0
            if proj_mm > 10.0:
                raise ValueError("PROJ and AeroTools differ by %.1f mm on the grid" % proj_mm)
        n_field = n_ref
    if not np.all(np.isfinite(n_field)):
        raise ValueError("the grid does not cover the whole DTM")

    rng = np.random.RandomState(7)
    picks = [(int(rng.randint(0, height)), int(rng.randint(0, width))) for _ in range(samples)]
    pick_lon, pick_lat = lonlat(np.array([x0 + (c + 0.5) * px for _r, c in picks]), np.array([y0 - (r + 0.5) * py for r, _c in picks]))
    pick_n = dict(zip(picks, grid.undulation(pick_lon, pick_lat)))
    state = {"done": 0, "worst": 0.0}
    total = max(1, len(tiff.offsets))

    def change(top, left, block):
        h = min(block.shape[0], height - top)
        w = min(block.shape[1], width - left)
        u = (left + np.arange(w) + 0.5) / float(width) * (cols - 1)
        v = (top + np.arange(h) + 0.5) / float(height) * (rows - 1)
        n = _bilinear(n_field, u[np.newaxis, :], v[:, np.newaxis])
        part = block[:h, :w].astype(np.float64)
        valid = np.isfinite(part)
        if nodata is not None:
            valid &= part != nodata
        out = block.copy()
        out[:h, :w] = np.where(valid, part - n, part).astype(np.float32)
        for (row, col), direct in pick_n.items():
            if top <= row < top + h and left <= col < left + w:
                state["worst"] = max(state["worst"], abs(float(direct) - float(n[row - top, col - left])))
        state["done"] += 1
        if progress:
            progress(state["done"] / float(total))
        return out

    tmp = dst + ".part"
    try:
        tiff.rewrite(tmp, change)
        if state["worst"] > 0.005:
            raise ValueError("correction check: %.1f mm of difference" % (state["worst"] * 1000.0))
        os.replace(tmp, dst)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return {"min": float(n_field.min()), "max": float(n_field.max()), "check_mm": state["worst"] * 1000.0, "proj_mm": proj_mm}
