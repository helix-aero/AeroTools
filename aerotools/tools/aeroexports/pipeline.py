"""Aero-Exports: what happens when "Export" is clicked (no Qt: the window gives it its callbacks).

For each file of the list (plan.Job):
  1. Metashape writes a GeoTIFF (exportRaster), in one piece or tile by tile;
  2. if the wanted format is JPEG 2000 or ECW, GDAL or Global Mapper converts that GeoTIFF;
  3. in CAD mode, a DXF (TOPO.dxf) places all the images at the right spot, then AutoCAD turns it into a DWG and checks that
     the images reload.

Nothing is assumed about the machine: the tools come from `ctx.tools` (see external.find_*). A tile without data
writes no file: it is simply skipped.
"""

import math
import os
import shutil
import tempfile

from ... import projtool
from . import crs, dxfout, external, geotiff, look as looks, naming, plan, tiling


class _Needs(object):
    """A module that needs numpy, imported on first use: the exports that do not need it (plain GeoTIFF, JPEG, JP2, ECW) run on a
    Metashape without numpy. Using it there raises ImportError, which `run_job` turns into a readable Failure."""

    def __init__(self, name):
        self._name = name

    def __getattr__(self, attr):
        import importlib
        return getattr(importlib.import_module("." + self._name, __package__), attr)


footprint, relief, vertical = _Needs("footprint"), _Needs("relief"), _Needs("vertical")

TMP_NAME = ".aeroexports_tmp"
WORK_MARKER = ".aeroexports_work"       # written in every work folder this module creates: a folder without it is never swept


class Failure(Exception):
    """Readable failure: the message is shown as it is."""


class Context(object):
    """Everything an export needs; the window fills it in, the tests replace it with fakes."""

    def __init__(self, chunk, info, folder, site, date, preset, export, tools=None, crs=None, tile_m=None, cad=None,
                 date_style="short", snap=None, transform=None, tick=None, vertical=None, proj=None, epsg=None,
                 look=None, clip_boundary=True, log=None):
        self.chunk, self.info, self.folder = chunk, info, folder
        self.clip_boundary = bool(clip_boundary)         # cut every Metashape export to the chunk's boundary shapes (same value everywhere)
        self.log = log or (lambda text: None)            # one line of journal
        self.site, self.date, self.preset, self.cad = site, date, preset, cad or {}
        self.export = export                              # msapi.export_raster or a fake
        self.tools = tools or {}                          # {"gm": path, "acad": accoreconsole path, "gdal": folder}
        self.crs, self.tile_m = crs, tile_m
        self.date_style = date_style
        self.snap, self.transform = snap, transform
        self.tick = tick or (lambda: None)
        self.native = False
        self.vertical, self.proj, self.epsg = vertical, proj, epsg   # height grid (id), PROJ engine, EPSG of the output CRS
        self.look = looks.normalized(look if look is not None else looks.DEFAULT)   # palette / shading of the DEM image
        self.all_jobs = None                              # every file of the export (set by `run`): tiles are numbered on one grid
        self.failed = ""                                  # name of the file being written: the one that failed if `run` raises


class Result(object):
    def __init__(self):
        self.files = []                                   # (path, bytes) of everything written
        self.warnings = []
        self.dxf = self.dwg = None
        self.check = None                                 # AutoCAD's {"images": n, "failed": [...], "valid": bool}
        self.tiles = 0


# --------------------------------------------------------------------------------------------------
# One file
# --------------------------------------------------------------------------------------------------

def encoder(ctx):
    """Who writes the JPEG 2000: "gdal", "gm", or "metashape" (its own JPEG 2000, heavier, no external tool)."""
    if ctx.tools.get("gdal"):
        return "gdal"
    if ctx.tools.get("gm"):
        return "gm"
    return "metashape"


def _convert(ctx, post, src, dst, work=None):
    """GeoTIFF -> JPEG 2000 / ECW. The converter writes in a new folder (`work`, or next to `dst`) and the result replaces
    `dst` only if it worked: a conversion that fails (licence, disk full) keeps the previous file of that name. Raises Failure."""
    part_dir = tempfile.mkdtemp(prefix="conv", dir=work or os.path.dirname(os.path.abspath(dst)))
    part = os.path.join(part_dir, os.path.basename(dst))
    try:
        _convert_to(ctx, post, src, part)
        _commit(part, dst)
    finally:
        shutil.rmtree(part_dir, ignore_errors=True)


def _convert_to(ctx, post, src, dst):
    """The conversion itself, to the file `dst` (which does not exist yet). Raises Failure."""
    if post == "ecw":
        if not ctx.tools.get("gm"):
            raise Failure("ECW: Global Mapper not found")
        ok, message = external.to_ecw(ctx.tools["gm"], src, dst, tick=ctx.tick)
    else:
        which = encoder(ctx)
        if which == "gdal":
            ok, message = external.to_jp2_gdal(ctx.tools["gdal"], src, dst, tick=ctx.tick)
            if not ok and ctx.tools.get("gm"):
                ok, message = external.to_jp2_gm(ctx.tools["gm"], src, dst, tick=ctx.tick)
        elif which == "gm":
            ok, message = external.to_jp2_gm(ctx.tools["gm"], src, dst, tick=ctx.tick)
        else:
            raise Failure("JPEG 2000: no external encoder")
    for path in (src, dst):                               # GDAL sometimes leaves an .aux.xml (statistics): useless here
        _drop_sidecar(path)
    if not ok:
        raise Failure(message)


def _drop_sidecar(path):
    try:
        if os.path.isfile(path + ".aux.xml"):
            os.remove(path + ".aux.xml")
    except OSError:
        pass


def pixel_of(ctx, job):
    """Pixel size used when the export imposes a grid (CRS change, tiles). Metashape's native pixel
    (1.0008 cm) is then rounded to a tenth of a millimetre (1 cm): without this the tiles do not fall on whole pixels."""
    return round(float(job.pixel_m), 4) if (ctx.crs is not None or ctx.tile_m) and job.native else float(job.pixel_m)


def _grid(ctx, job):
    """(pixel, extent) to give Metashape for this file; (None, None) = its native grid."""
    kind = job.kind
    native = job.native and ctx.crs is None and not ctx.tile_m
    box = ctx.info.get("box_" + kind)
    if native or not box:
        return None, None
    if ctx.crs is None and ctx.info.get("geographic_" + kind):
        # An orthomosaic / DEM in degrees: a pixel in centimetres makes no sense, Metashape returns "Empty raster".
        raise Failure("The {} is in a geographic system (degrees). Pick a projected system in \"Export in\", "
                      "for example RGF93 v1 / CC42.".format("orthomosaic" if kind == "ortho" else "DEM"))
    if ctx.crs is not None:
        # The extent is in the CRS of THIS raster (the orthomosaic can be in degrees and the DEM in metres).
        source = ctx.info.get("crs_" + kind) or ctx.info.get("crs_object")
        moved = ctx.transform(box, source, ctx.crs)
        if moved is None:
            reason = getattr(ctx.transform, "last_error", "")
            raise Failure("Cannot convert the extent to the chosen CRS" + (" (%s)" % reason if reason else ""))
        if not all(math.isfinite(value) for value in moved):
            raise Failure("The area of the {} could not be converted to the chosen system.".format(
                "orthomosaic" if kind == "ortho" else "DEM"))
        box = moved
    pixel = pixel_of(ctx, job)
    return pixel, ctx.snap(box, pixel)


def _frame(ctx, job):
    """The grid every file of this export is cut on: (tile size, absolute index of the first column, of the last row, number of
    columns, number of rows). One size (valid for the pixels of ALL the files) and one origin (the union of their extents), so a
    tile of the orthomosaic and the tile of the DTM that carry the same R..C.. cover the same ground."""
    peers = [peer for peer in (getattr(ctx, "all_jobs", None) or [job]) if peer.kind in plan.KINDS]
    pixels, boxes = [], []
    for peer in peers:
        _pixel, box = _grid(ctx, peer)
        if box:
            pixels.append(pixel_of(ctx, peer))
            boxes.append(box)
    size, _exact = tiling.snap_tile_size(ctx.tile_m, pixels)
    left, bottom = min(b[0] for b in boxes), min(b[1] for b in boxes)
    right, top = max(b[2] for b in boxes), max(b[3] for b in boxes)
    first_x = int(tiling.floor_to(left, size) / size)
    first_y = int(tiling.floor_to(bottom, size) / size)
    last_y = int(tiling.ceil_to(top, size) / size)          # exclusive
    last_x = int(tiling.ceil_to(right, size) / size)
    return size, first_x, first_y, last_x - first_x, last_y - first_y


def _tiles(ctx, job, box):
    """List of (name token or None, floating extent): a single tile if tiling is not requested. The numbers R..C.. count from the
    common frame of all the files (`_frame`), not from the extent of this one raster."""
    if not ctx.tile_m or not box:
        return [(None, box)]
    pixel = pixel_of(ctx, job)
    size, first_x, first_y, n_cols, n_rows = _frame(ctx, job)
    tiles = tiling.tile_grid(box, size, pixel_sizes=(pixel,), edge="crop")
    origin_x, origin_y = tiling.grid_origin(tiles)
    shift_x, shift_y = int(origin_x / size) - first_x, int(origin_y / size) - first_y
    out = []
    for tile in tiles:
        shared = tile._replace(row=n_rows - (tile.iy + shift_y), col=tile.ix + shift_x + 1)
        out.append((tiling.tile_name(shared, "rc", n_rows, n_cols), tuple(float(v) for v in tile.bounds)))
    return out


def _named(ctx, job, kind, ext, token):
    return naming.build_name(ctx.site, ctx.date, job.pixel_m, kind, ext, version=job.version, tile=token,
                             date_style=ctx.date_style)


def _view_named(ctx, job, ext, token):
    """Name of the picture of a DEM tile: the DEM's own name in the picture format (a GeoTIFF picture gets "_view")."""
    return plan.picture_name(_named(ctx, job, plan.DEM, "tif", token), ext)


def exported_size(ctx, job, pixel, box):
    """(width, height) in pixels of the raster Metashape is asked for: the extent over the pixel when the grid is imposed (finer pixel,
    other CRS, a tile), else the native size. None when unknown."""
    if pixel and box:
        return (int(math.ceil((box[2] - box[0]) / float(pixel) - 1e-6)), int(math.ceil((box[3] - box[1]) / float(pixel) - 1e-6)))
    found = ctx.info.get(job.kind)
    return (found[1], found[2]) if found else None


def _big(ctx, job, pixel=None, box=None):
    """BigTIFF (more than 4 GB of raw samples) decided on the size that is exported, not on the native one."""
    size = exported_size(ctx, job, pixel, box)
    if not size:
        return False
    return plan.needs_bigtiff(size[0], size[1], 4 if job.kind == plan.ORTHO else 1, 1 if job.kind == plan.ORTHO else 4)


def _record(ctx, raw, layer, final):
    """Placement of an image in the DXF, read from the raw GeoTIFF."""
    info = geotiff.read_geotiff(raw)
    if info is None:
        raise Failure("Unreadable georeferencing: %s" % os.path.basename(raw))
    left, bottom, right, top = geotiff.extent(info)
    return {"layer": layer, "path": final, "size_px": (info["width"], info["height"]), "insert": (left, bottom),
            "pixel": (info["pixel"][0], info["pixel"][1])}


def _commit(staged, final):
    """Put a finished file at its final place. The file already there (the previous deliverable of the same name, which the
    user agreed to overwrite) is replaced in one step, so it is never lost when the export fails before this point.
    `staged` is in the work folder, on the same volume. Raises Failure (the old file is then untouched)."""
    try:
        os.replace(staged, final)
    except OSError as exc:
        raise Failure("Cannot overwrite %s: %s" % (os.path.basename(final), exc))


def size_problems(ctx, jobs):
    """Readable reasons why a file of the list cannot be written as asked (libjpeg refuses an image more than 65 500 px on a side: the
    check of tiling.tile_size_warnings, run on the planned grid, before Metashape works for minutes). [] when all is fine."""
    problems = []
    for job in jobs:
        picture = job.kind == plan.DEM and (job.extra or {}).get("view_ext") == "jpg"
        if (job.spec.get("image") != "jpeg" and not picture) or job.spec.get("post") or ctx.preset == plan.CAD:
            continue
        pixel, box = _grid(ctx, job)
        for token, tile_box in (_tiles(ctx, job, box) if box else [(None, None)]):
            size = exported_size(ctx, job, pixel, tile_box or box)
            if not size:
                continue
            for key, text in tiling.tile_size_warnings(size[0], size[1], 3, 1, "jpeg"):
                if key == "side_limit":
                    problems.append("%s: %s. Cut it into tiles or choose GeoTIFF." % (job.name, text))
                    break
            else:
                continue
            break
    return problems


def run_job(ctx, job, work, report, result, images):
    """Write one file of the list (or all its tiles). `report(fraction)`: progress of this file."""
    spec = job.spec
    post = spec.get("post")
    is_cad = ctx.preset == plan.CAD
    pixel, box = _grid(ctx, job)
    tiles = _tiles(ctx, job, box)
    folder = os.path.dirname(job.path)
    os.makedirs(folder, exist_ok=True)
    extra = job.extra or {}
    if job.kind == plan.DEM and extra.get("view_path"):
        os.makedirs(os.path.dirname(extra["view_path"]), exist_ok=True)
    date_text = naming.format_date(ctx.date, ctx.date_style)
    pixel_text = "%g" % round(float(job.pixel_m), 4)
    skipped = []                                          # final names of the tiles without data
    for index, (token, tile_box) in enumerate(tiles):
        def sub(percent, index=index):
            report((index + max(0.0, min(100.0, float(percent))) / 100.0) / len(tiles))

        direct_ext = spec["ext"] if not post else None
        raw_spec = dict(spec, ext="tif") if post else dict(spec)
        if is_cad and job.kind == plan.DEM:
            final = os.path.join(folder, _named(ctx, job, job.kind, "tif", token))
        elif post:
            final = os.path.join(folder, _named(ctx, job, job.kind, spec["ext"], token))
        else:
            final = os.path.join(folder, _named(ctx, job, job.kind, direct_ext, token))
        metashape_jp2 = post in ("jp2", "cad") and spec["ext"] == "jp2" and encoder(ctx) == "metashape"
        if metashape_jp2:
            raw_spec = dict(spec, image="jp2", ext="jp2")
        # `direct`: the file Metashape writes is the deliverable itself. It is written in the work folder and only moved to its place
        # once everything that follows (heights, pictures) has worked: a failure then leaves nothing under the final name (a DEM
        # still in ellipsoidal heights, say), and the previous deliverable of the same name is not touched.
        direct = not post or metashape_jp2
        if direct:
            raw = os.path.join(work, os.path.basename(final))
        else:
            raw = os.path.join(work, os.path.splitext(os.path.basename(final))[0] + ".tif")
        # No progress callback: Metashape would call it in the middle of its computation (maybe from another thread) and touching
        # a Qt bar at that moment crashes Metashape ("Recursive repaint detected"). The bar moves between files.
        # The work folder is new: no file left by an earlier export (maybe of another chunk) can pass for this one.
        ctx.native = True
        try:
            ok, error = ctx.export(ctx.chunk, job.kind, raw_spec, raw, pixel, tile_box, big=_big(ctx, job, pixel, tile_box), progress=None,
                                   crs=ctx.crs, clip_to_boundary=ctx.clip_boundary)
        finally:
            ctx.native = False
        sub(100)
        if not ok:
            raise Failure(error or "Metashape did not write the file")
        if not os.path.isfile(raw):
            if len(tiles) > 1:
                skipped.append(final)
                continue                                  # tile without data: nothing to write
            raise Failure("Metashape did not write the file")
        result.tiles += 1
        if job.kind == plan.DEM and ctx.vertical:
            try:
                _to_altitude(ctx, raw, sub)
            except Failure as exc:
                ctx.log("{} not written: {} (nothing was left in the Outputs folder)".format(os.path.basename(final), exc))
                raise
        if post and not metashape_jp2:
            _convert(ctx, spec["post"] if post != "cad" else spec["ext"], raw, final, work)
        position = len(result.files)
        if is_cad and job.kind == plan.ORTHO:
            if direct:
                raise Failure("Image without a source GeoTIFF: cannot place it")
            record = _record(ctx, raw, dxfout.layer_name(date_text, pixel_text, "ortho"), final)
            _clip_ortho(ctx, raw, work, record, result)
            images.append(record)
        if job.kind == plan.DEM and extra.get("view_path"):       # CAD, or the coloured picture asked next to a JP2 / ECW export
            _dem_image(ctx, job, raw, token, work, date_text, pixel_text, result, images if is_cad else None)
        values = plan.values_wanted(job)                      # False: only the coloured picture of the DEM was asked
        if direct and values:
            _commit(raw, final)
            for name in plan.aux_names(os.path.basename(final), spec):      # .jgw / .prj of a JPEG: written next to the file
                side = os.path.join(work, name)
                if os.path.isfile(side):
                    _commit(side, os.path.join(folder, name))
        else:
            os.remove(raw)
        # the files of this tile, listed before the picture of the DEM (written by _dem_image) as they always were
        written = []
        if values:
            written.append((final, os.path.getsize(final)))
            for name in plan.aux_names(os.path.basename(final), spec):
                side = os.path.join(folder, name)
                if os.path.isfile(side):
                    written.append((side, os.path.getsize(side)))
        result.files[position:position] = written
    for name in skipped:                                  # the export went through: a tile of an older export that has no data now
        if os.path.isfile(name):                          # would be an orphan next to the new ones
            try:
                os.remove(name)
                ctx.log("{}: no data this time, the older tile was removed".format(os.path.basename(name)))
            except OSError:
                ctx.log("{}: no data this time, the older tile could not be removed".format(os.path.basename(name)))


def _to_altitude(ctx, raw, sub):
    """Ellipsoidal heights of the DEM -> altitudes of the chosen grid (our Geoid folder). PROJ computes, our reader checks.
    Raises Failure."""
    engine = ctx.proj
    if engine is None or not engine.available():
        raise Failure("Altitude: PROJ not found (folder aerotools/vendor/proj)")
    try:
        grid = vertical.load_grid(ctx.vertical)
        spec = crs.vertical_by_id(ctx.vertical)
        tmp = raw + ".alt"
        vertical.correct_dem(raw, tmp, grid, lambda xs, ys: engine.lonlat(ctx.epsg, xs, ys),
                             reference=lambda lons, lats: -engine.vertical_shift(spec["file"], lons, lats),
                             progress=lambda part: (sub(100.0 * part), ctx.tick()))
        os.replace(tmp, raw)
    except (ValueError, OSError, projtool.ProjError) as exc:
        raise Failure("Altitude %s: %s" % (ctx.vertical, exc))


MASK_SPEC = {"ext": "tif", "image": "tiff", "compression": "none", "tiled": False, "overviews": False, "world": False,
             "white": False, "alpha": True}          # small uncompressed RGBA export: only the alpha is of use to us


def _clip_warning(result):
    if "clip" not in result.warnings:
        result.warnings.append("clip")


def _clip_ortho(ctx, raw, work, record, result):
    """Clip polygon of the orthomosaic (real extent of the data) if the option is ticked. A failure does not stop the export."""
    if not ctx.cad.get("clip"):
        return
    try:
        info = geotiff.read_geotiff(raw)
        left, bottom, right, top = geotiff.extent(info)
        cell = max(max(right - left, top - bottom) / float(footprint.TARGET_PX), info["pixel"][0])   # never finer than the image
        mask_path = os.path.join(work, "mask_%s.tif" % os.path.splitext(os.path.basename(raw))[0])
        ok, error = ctx.export(ctx.chunk, plan.ORTHO, MASK_SPEC, mask_path, cell, (left, bottom, right, top), big=False,
                               progress=None, crs=ctx.crs, clip_to_boundary=ctx.clip_boundary)
        if not ok or not os.path.isfile(mask_path):
            raise ValueError(error or "no mask")
        mask_info = geotiff.read_geotiff(mask_path)
        record["clip"] = footprint.clip_polygon(footprint.alpha_mask(mask_path), geotiff.extent(mask_info), info) or None
        os.remove(mask_path)
    except (ValueError, OSError, TypeError, ImportError):
        _clip_warning(result)


def _clip_dem(ctx, dem_tif, view_tif, record, result):
    """Clip polygon of the DEM image: the pixels that have a height."""
    if not ctx.cad.get("clip"):
        return
    try:
        mask, step = footprint.dem_mask(dem_tif)
        ctx.log("DEM footprint: {:.1%} of the pixels have data (clip_to_boundary={})".format(float(mask.mean()), ctx.clip_boundary))
        dem = geotiff.read_geotiff(dem_tif)
        left, top = dem["origin"]
        extent = (left, top - mask.shape[0] * step * dem["pixel"][1], left + mask.shape[1] * step * dem["pixel"][0], top)
        record["clip"] = footprint.clip_polygon(mask, extent, geotiff.read_geotiff(view_tif)) or None
    except (ValueError, OSError, TypeError, ImportError):
        _clip_warning(result)


def _look_for(ctx, result):
    """Look of the DEM image. A custom colour range is written in the heights of the project, which the export may have changed
    (height system): then the full range is used, and the report says so."""
    if ctx.vertical and ctx.look.stretch == looks.CUSTOM:
        if "stretch" not in result.warnings:
            result.warnings.append("stretch")
        return ctx.look._replace(stretch=looks.FULL, low=None, high=None)
    return ctx.look


def _dem_image(ctx, job, dem_tif, token, work, date_text, pixel_text, result, images):
    """Display picture of the DEM (coloured relief): for the DXF (`images`: list of the images to place), or alone next to the DEM
    (`images` is None)."""
    extra = job.extra
    view_tif = os.path.join(work, os.path.splitext(os.path.basename(dem_tif))[0] + "_view.tif")
    view_final = os.path.join(os.path.dirname(extra["view_path"]), _view_named(ctx, job, extra["view_ext"], token))
    if extra["view_ext"] == "jpg":                        # JPEG + world file, written by relief itself (no external tool)
        view_jpg = os.path.splitext(view_tif)[0] + ".jpg"
        ok, message = relief.render(dem_tif, view_jpg, look=_look_for(ctx, result), tick=ctx.tick, jpeg=True)
        _drop_sidecar(dem_tif)
        if not ok:
            raise Failure(message)
        _commit(view_jpg, view_final)
        result.files.append((view_final, os.path.getsize(view_final)))
        world = os.path.splitext(view_final)[0] + ".jgw"
        _commit(os.path.splitext(view_jpg)[0] + ".jgw", world)
        result.files.append((world, os.path.getsize(world)))
        return
    ok, message = relief.render(dem_tif, view_tif, look=_look_for(ctx, result), tick=ctx.tick)
    _drop_sidecar(dem_tif)
    if not ok:
        raise Failure(message)
    # the coloured relief is an RGB GeoTIFF: it follows the same path as the orthomosaic (GDAL first, or Global Mapper)
    if extra["view_ext"] == "tif":                        # the picture is the RGB GeoTIFF itself: no converter
        _commit(view_tif, view_final)
        result.files.append((view_final, os.path.getsize(view_final)))
        return
    _convert(ctx, extra["view_ext"], view_tif, view_final, work)
    result.files.append((view_final, os.path.getsize(view_final)))
    if images is not None:
        record = _record(ctx, view_tif, dxfout.layer_name(date_text, pixel_text, "dem"), view_final)
        _clip_dem(ctx, dem_tif, view_tif, record, result)
        images.append(record)
    os.remove(view_tif)


# --------------------------------------------------------------------------------------------------
# The whole export
# --------------------------------------------------------------------------------------------------

def run(ctx, jobs, progress):
    """Write everything. `progress(fraction 0..1, text)`. Returns a Result; raises Failure."""
    result = Result()
    if not jobs:
        return result
    ctx.all_jobs = list(jobs)                             # the tiles of every file are numbered on one common grid
    problems = size_problems(ctx, jobs)
    if problems:
        raise Failure(problems[0])
    os.makedirs(ctx.folder, exist_ok=True)
    sweep_stale(ctx.folder)
    work = tempfile.mkdtemp(prefix=TMP_NAME, dir=ctx.folder)
    try:
        with open(os.path.join(work, WORK_MARKER), "w") as handle:
            handle.write("work folder of an Aero-Exports run")
    except OSError:
        pass
    images = []
    total = len(jobs)
    try:
        for index, job in enumerate(jobs):
            ctx.failed = job.name
            progress(index / float(total), job.name)
            try:
                run_job(ctx, job, work, lambda part, index=index: progress((index + part) / float(total), job.name), result,
                        images)
            except ImportError as exc:
                raise Failure("This export needs numpy, which this Metashape does not include (%s)" % exc)
        progress(1.0, "")
        if ctx.preset == plan.CAD:
            _write_cad(ctx, jobs, images, result, work)
        ctx.failed = ""
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return result


STALE_SECONDS = 24 * 3600


def _is_link(path):
    try:
        st = os.lstat(path)
    except OSError:
        return True
    return os.path.islink(path) or bool(getattr(st, "st_file_attributes", 0) & 0x400)


def sweep_stale(folder, now=None):
    """Remove the work folders (and half-written DWG) that an export killed in the middle left in the Outputs folder: names that start
    with TMP_NAME and not touched for a day. Never a recent one (another export may be running). Returns the number removed."""
    import time
    now = time.time() if now is None else now
    removed = 0
    try:
        names = os.listdir(folder)
    except OSError:
        return 0
    for name in names:
        path = os.path.join(folder, name)
        if not name.startswith(TMP_NAME):
            continue
        try:
            if _is_link(path) or now - os.path.getmtime(path) < STALE_SECONDS:
                continue                                      # a link or junction is never followed
            if os.path.isdir(path):
                if not os.path.isfile(os.path.join(path, WORK_MARKER)):
                    continue                                  # not made by us: a user's folder that happens to start the same
                shutil.rmtree(path)
            else:
                os.remove(path)
            removed += 1
        except OSError:
            continue
    return removed


def _write_cad(ctx, jobs, images, result, work):
    """TOPO.dxf (and TOPO.dwg): all the images in place, relative paths."""
    if not images:
        raise Failure("No image to place in the drawing")
    version = jobs[0].version
    dxf = os.path.join(ctx.folder, plan.cad_dxf_name(version, "dxf"))
    ctx.failed = os.path.basename(dxf)
    entries = []
    for item in images:
        entries.append({"layer": item["layer"], "file": dxfout.relative_path(item["path"], dxf), "size_px": item["size_px"],
                        "insert": item["insert"], "pixel": item["pixel"], "clip": item.get("clip")})
    staged = dxfout.write_dxf(os.path.join(work, os.path.basename(dxf)), entries)      # the relative paths are those of the final folder
    _commit(staged, dxf)
    result.dxf = dxf
    result.files.append((dxf, os.path.getsize(dxf)))
    core = ctx.tools.get("acad")
    if core:
        result.check = external.check_dxf(core, dxf, tick=ctx.tick)
    if ctx.cad.get("format") == "dwg":
        if not core:
            raise Failure("DWG: AutoCAD not found")
        dwg = os.path.join(ctx.folder, plan.cad_dxf_name(version, "dwg"))
        ctx.failed = os.path.basename(dwg)
        # AutoCAD writes under another name, in the SAME folder (it stores the image paths relative to the drawing's folder, which
        # must stay the final one); the previous DWG stays until the new one is complete.
        part = os.path.join(ctx.folder, TMP_NAME + "_" + os.path.basename(dwg))
        try:
            ok, message = external.dxf_to_dwg(core, dxf, part, tick=ctx.tick)
            if not ok:
                raise Failure(message)
            _commit(part, dwg)
        finally:
            if os.path.isfile(part):
                os.remove(part)
        result.dwg = dwg
        result.files.append((dwg, os.path.getsize(dwg)))
