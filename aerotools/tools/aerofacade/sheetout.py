"""Aero-Facade drawing sheet: options, heights, pictures and the files (no Metashape, no Qt).

The two backends (Metashape and the fake one) prepare, for each facade, a picture file and the height of its bottom edge; everything else
is here so that it is written once and tested without Metashape:

* `normalise`: the `options["sheet"]` dict of the backend protocol, with its defaults;
* `sheet_heights`: the Y of every picture. Relative: height above the lowest bottom edge of all the pictures. Altitude: `H = h - N`, with
  `N` the undulation of the chosen vertical datum at the facade, read from the `Geoid` folder and checked against PROJ exactly like
  Aero-Exports does for a DEM (`vertical.correct_dem`), or no `N` for the ellipsoid. A missing grid is a readable `SheetError`;
* `convert_picture`: JPEG 2000 / ECW through the Aero-Exports tools (GDAL, Global Mapper), with the picture put at its place of the sheet;
* `write_sheet`: `<Site>_<date>_facades.dxf` (+ `.dwg` through AutoCAD), the pictures referenced by relative paths in `00_Assets\\ortho`.
"""

import os
import shutil
import struct
import tempfile
import types
from dataclasses import dataclass

import numpy as np

from ..aeroexports import crs, dxfout, external, geotiff, pipeline, projtool, vertical
from . import sheet as sheetmod
from . import sheet_labels

FORMATS = ("png", "jpg", "tif", "jp2", "ecw")
NATIVE = ("png", "jpg", "tif")           # written by Metashape itself; JPEG 2000 and ECW go through external tools
UNDULATION_CHECK_MM = 10.0               # PROJ and our own reading of the grid must agree to this (the same rule as a DEM)


class SheetError(Exception):
    """A readable reason why the sheet cannot be made; nothing is written for it."""


# --------------------------------------------------------------------------------------------------
# Options
# --------------------------------------------------------------------------------------------------

def normalise(raw):
    """`options["sheet"]` with its defaults: {"dxf", "dwg", "z", "vertical", "titles"}. A DWG is converted from the DXF, which is kept, so
    `dwg` implies `dxf`. `vertical` only counts in Z mode."""
    raw = raw if isinstance(raw, dict) else {}
    dwg = bool(raw.get("dwg"))
    z = bool(raw.get("z"))
    titles = str(raw.get("titles") or sheet_labels.DEFAULT_LANGUAGE).lower()
    return {"dxf": bool(raw.get("dxf")) or dwg, "dwg": dwg, "z": z, "vertical": (raw.get("vertical") or None) if z else None,
            "titles": titles if titles in sheet_labels.LANGUAGES else sheet_labels.DEFAULT_LANGUAGE}


def wanted(raw):
    """True when a sheet (DXF, DWG or both) was asked for."""
    opts = normalise(raw)
    return opts["dxf"] or opts["dwg"]


def sheet_stem(site, date):
    return "%s_%s_facades" % (site, date)


def picture_name(site, date, letter, ext):
    return "%s_%s_facade_%s.%s" % (site, date, letter, ext)


def asset_path(folder, site, date, letter, ext):
    """Where the sheet's picture of a facade lives: `<folder>\\00_Assets\\ortho\\<Site>_<date>_facade_<L>.<ext>`."""
    return os.path.join(folder, dxfout.ASSETS, dxfout.ORTHO_DIR, picture_name(site, date, letter, ext))


def tools_for_formats(tools, fmt, sheet_options):
    """[(what, reason)] that is missing for these choices (empty when everything needed was found). `tools`: {"gm", "acad", "gdal"} paths."""
    tools = tools or {}
    missing = []
    if str(fmt) == "ecw" and not tools.get("gm"):
        missing.append(("ecw", "ECW needs Global Mapper, which was not found."))
    if str(fmt) == "jp2" and not (tools.get("gdal") or tools.get("gm")):
        missing.append(("jp2", "JPEG 2000 needs GDAL (QGIS) or Global Mapper, which were not found."))
    if normalise(sheet_options)["dwg"] and not tools.get("acad"):
        missing.append(("dwg", "DWG needs AutoCAD, which was not found."))
    return missing


# --------------------------------------------------------------------------------------------------
# Heights
# --------------------------------------------------------------------------------------------------

@dataclass
class HeightInput:
    """Height of the bottom edge of a facade picture. `h`: in the relative mode any consistent height (the same system for all the
    facades); in the altitude mode the height in the output CRS, ELLIPSOIDAL as Metashape gives it. `x`, `y`: where the facade is, in the
    projected CRS `epsg` (only the altitude mode with a geoid grid reads them)."""
    letter: str
    h: float
    x: float = 0.0
    y: float = 0.0


def datum_short(spec):
    """Short name of a vertical datum for the level labels: "NGF-IGN69" for the "NGF-IGN69 height" of the catalogue; "ELL" for the ellipsoid."""
    if not spec or not spec.get("file"):
        return "ELL"
    name = str(spec.get("target") or spec.get("short") or spec.get("id"))
    return name.replace(" height", "").strip()


def datum_note(spec, epsg=None):
    """One line for the note under the sheet: which height system, which grid, in which horizontal frame."""
    frame = " (horizontal frame EPSG:%s)" % epsg if epsg else ""
    if not spec or not spec.get("file"):
        return "ellipsoidal heights, no geoid grid" + frame
    grid = str(spec.get("short") or spec.get("id"))
    return "%s / %s, geoid grid %s%s" % (datum_short(spec), grid, spec.get("file"), frame)


def undulations(vertical_id, epsg, xs, ys, engine=None, folder=None):
    """N (metres, array) of the vertical datum at the points (xs, ys) of the projected CRS `epsg`. Raises SheetError with a plain reason.

    The value that applies is PROJ's (the authority), our own reading of the grid must agree to UNDULATION_CHECK_MM, as for a DEM."""
    spec = crs.vertical_by_id(vertical_id)
    if spec is None:
        raise SheetError("Unknown height system: %s." % vertical_id)
    xs, ys = np.asarray(xs, dtype=np.float64).ravel(), np.asarray(ys, dtype=np.float64).ravel()
    if not spec.get("file"):
        return np.zeros(len(xs))
    if not epsg:
        raise SheetError("The project has no coordinate system with an EPSG code, so no height system can be applied. "
                         "Use relative heights.")
    engine = engine or projtool.Proj(geoid_dir=folder or crs.own_dir())
    if not engine.available():
        raise SheetError("PROJ was not found (folder aerotools/vendor/proj), so heights cannot be converted.")
    try:
        lon, lat = engine.lonlat(epsg, xs, ys)
    except projtool.ProjError as exc:
        raise SheetError("The position of the facade could not be converted: %s" % exc)
    if not (np.all(np.isfinite(lon)) and np.all(np.isfinite(lat))):
        raise SheetError("The position of the facade could not be converted to longitude and latitude.")
    found = crs.check_geoid_presence(vertical_id, folder, float(lon[0]), float(lat[0]))
    name = crs.label(spec)
    if found["status"] == "not_covering":
        raise SheetError("The grid %s does not cover this place. Pick another height system." % spec.get("short", vertical_id))
    if found["status"] == "missing":
        raise SheetError("The grid for %s is not on this computer. Download it on the export screen, then try again." % name)
    if found["status"] == "stale":
        raise SheetError("The %s grid on this computer differs from the official one (damaged or outdated). Download it again." % name)
    try:
        grid = vertical.load_grid(vertical_id, folder)
        ours = grid.undulation(lon, lat)
        reference = -engine.vertical_shift(spec["file"], lon, lat)
    except (ValueError, OSError, projtool.ProjError) as exc:
        raise SheetError("The height grid could not be read: %s" % exc)
    if not np.all(np.isfinite(reference)):
        raise SheetError("The grid %s does not cover the facade (no value there). Pick another height system." % spec.get("short", vertical_id))
    if np.all(np.isfinite(ours)) and float(np.abs(reference - ours).max()) * 1000.0 > UNDULATION_CHECK_MM:
        raise SheetError("PROJ and AeroTools disagree by more than %g mm on the grid %s: nothing was written." %
                         (UNDULATION_CHECK_MM, spec.get("short", vertical_id)))
    return reference


def sheet_heights(inputs, opts, epsg=None, engine=None, folder=None):
    """Y of each picture's bottom edge. `inputs`: list of HeightInput. Returns
    {"y": {letter: Y}, "mode": "relative" | "altitude", "short", "note", "undulation": {letter: N or None}, "zero": common zero or None}.
    Raises SheetError."""
    opts = normalise(opts)
    if not inputs:
        raise SheetError("No facade picture to place.")
    if not opts["z"]:
        zero = min(i.h for i in inputs)
        return {"y": {i.letter: i.h - zero for i in inputs}, "mode": "relative", "short": "", "note": "", "undulation": {}, "zero": zero}
    vid = opts["vertical"]
    if not vid:
        raise SheetError("Pick a height system for the altitudes, or untick the altitudes to use relative heights.")
    spec = crs.vertical_by_id(vid)
    n = undulations(vid, epsg, [i.x for i in inputs], [i.y for i in inputs], engine, folder)
    return {"y": {i.letter: i.h - float(v) for i, v in zip(inputs, n)}, "mode": "altitude", "short": datum_short(spec),
            "note": datum_note(spec, epsg), "undulation": {i.letter: float(v) for i, v in zip(inputs, n)}, "zero": None}


# --------------------------------------------------------------------------------------------------
# Pictures
# --------------------------------------------------------------------------------------------------

def write_geotiff_rgb(path, rgb, left, top, pixel):
    """Write an RGB uint8 array as an uncompressed GeoTIFF whose top-left corner is (left, top) and whose pixel is `pixel` metres. The frame is a
    plain metric one (projected model, metre): enough for GDAL and Global Mapper to read it, and meaningful only on the drawing sheet."""
    a = np.ascontiguousarray(rgb, dtype=np.uint8)
    h, w = a.shape[:2]
    data = a.tobytes()
    keys = [1, 1, 0, 3, 1024, 0, 1, 1, 1025, 0, 1, 1, 3076, 0, 1, 9001]
    blobs = [struct.pack("<3H", 8, 8, 8), struct.pack("<3d", pixel, pixel, 0.0), struct.pack("<6d", 0, 0, 0, left, top, 0.0),
             struct.pack("<%dH" % len(keys), *keys)]
    entries = [(256, 4, 1, w), (257, 4, 1, h), (258, 3, 3, ("blob", 0)), (259, 3, 1, 1), (262, 3, 1, 2), (273, 4, 1, ("data", 0)),
               (277, 3, 1, 3), (278, 4, 1, h), (279, 4, 1, len(data)), (33550, 12, 3, ("blob", 1)), (33922, 12, 6, ("blob", 2)),
               (34735, 3, len(keys), ("blob", 3))]
    entries.sort()
    table = 2 + 12 * len(entries) + 4
    cursor = 8 + table
    offsets = []
    for blob in blobs:
        offsets.append(cursor)
        cursor += len(blob)
    out = [struct.pack("<2sHI", b"II", 42, 8), struct.pack("<H", len(entries))]
    for tag, kind, count, value in entries:
        if isinstance(value, tuple):
            value = offsets[value[1]] if value[0] == "blob" else cursor
        inline = kind == 3 and count == 1                 # a single SHORT sits in the first two bytes of the value field
        out.append(struct.pack("<HHIHH", tag, kind, count, value, 0) if inline else struct.pack("<HHII", tag, kind, count, value))
    out.append(struct.pack("<I", 0))
    out.extend(blobs)
    out.append(data)
    with open(path, "wb") as handle:
        handle.write(b"".join(out))
    return path


def convert_picture(raw, dest, kind, tools, tick=None, origin=None):
    """GeoTIFF `raw` -> JPEG 2000 (`kind` "jp2") or ECW ("ecw") at `dest`, through the Aero-Exports tools (GDAL first for JPEG 2000, Global
    Mapper for ECW). `origin` = (left, top): the copy that is converted gets its top-left corner there (the picture's place on the sheet);
    `raw` itself is never changed. Raises SheetError."""
    ctx = types.SimpleNamespace(tools=dict(tools or {}), tick=tick or (lambda: None))
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    work = tempfile.mkdtemp(prefix="aerofacade_cv_")
    try:
        src = raw
        if origin is not None:
            src = os.path.join(work, os.path.basename(raw))
            shutil.copyfile(raw, src)
            if not geotiff.set_origin(src, origin[0], origin[1]):
                raise SheetError("The picture has no georeferencing that can be placed on the sheet.")
        try:
            pipeline._convert(ctx, "ecw" if kind == "ecw" else "jp2", src, dest)
        except pipeline.Failure as exc:
            raise SheetError(str(exc))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return dest


# --------------------------------------------------------------------------------------------------
# The files
# --------------------------------------------------------------------------------------------------

def write_sheet(entries, folder, site, date, opts, heights, tools=None, tick=None):
    """Write `<site>_<date>_facades.dxf` (and `.dwg`) in `folder`.

    `entries`: one dict per facade {"letter", "azimuth", "size_px", "pixel", "image": absolute path of the picture, already written}.
    `heights`: the result of `sheet_heights`. Returns {"dxf", "dwg" (or None), "check": AutoCAD's image check (or None), "sheet": Sheet,
    "warnings": [text]}. Raises SheetError."""
    opts = normalise(opts)
    tools = tools or {}
    if opts["dwg"] and not tools.get("acad"):
        raise SheetError("DWG needs AutoCAD, which was not found. The DXF can be written without it.")
    folder = os.path.abspath(folder)                      # AutoCAD runs in another working folder: it needs absolute paths
    os.makedirs(folder, exist_ok=True)
    dxf = os.path.join(folder, sheet_stem(site, date) + ".dxf")
    items = [sheetmod.SheetFacade(letter=e["letter"], azimuth=e["azimuth"], size_px=tuple(e["size_px"]), pixel=float(e["pixel"]),
                                  bottom=float(heights["y"][e["letter"]]), file=dxfout.relative_path(e["image"], dxf)) for e in entries]
    sheet = sheetmod.build_sheet(items, opts["titles"], heights["mode"], heights.get("short", ""), heights.get("note", ""), site, date)
    dxfout.write_dxf(dxf, sheet.images, True, sheet.entities, sheet.layers, sheet.extents, font=sheetmod.FONT)
    out = {"dxf": dxf, "dwg": None, "check": None, "sheet": sheet, "warnings": []}
    core = tools.get("acad")
    if core:
        out["check"] = external.check_dxf(core, dxf, tick=tick)
        if not out["check"]["valid"]:
            out["warnings"].append("AutoCAD could not open the DXF.")
        elif out["check"]["failed"] or out["check"]["images"] < len(items):
            out["warnings"].append("AutoCAD loaded %d of %d pictures." % (out["check"]["images"], len(items)))
    if opts["dwg"]:
        dwg = os.path.splitext(dxf)[0] + ".dwg"
        ok, message = external.dxf_to_dwg(core, dxf, dwg, tick=tick, zoom_extents=True)
        if not ok:
            raise SheetError(message)
        out["dwg"] = dwg
    return out


# --------------------------------------------------------------------------------------------------
# The whole export (shared by the Metashape adapter and the fake backend)
# --------------------------------------------------------------------------------------------------

def detect_tools():
    """{"gm", "acad", "gdal"}: the external programs found on this computer (the paths the user gave Aero-Exports are honoured)."""
    from ... import config
    section = "aeroexports"
    return {"gm": external.find_global_mapper(config.get(section, "path_gm") or ""),
            "acad": external.find_autocad(config.get(section, "path_acad") or ""),
            "gdal": external.find_gdal(config.get(section, "path_gdal") or "")}


class Hooks(object):
    """What `run_export` asks a backend for. The two backends implement it; nothing here knows Metashape."""

    def epsg(self):
        """EPSG code of the projected (or geographic) CRS the heights are read in, or None."""
        return None

    def info(self, facade):
        """{"size_px": (w, h), "pixel": metres, "azimuth": degrees, "h": height of the picture's bottom edge, "x", "y": its middle in `epsg`}.
        `h` is ellipsoidal in the CRS above (any consistent height system when only relative heights are asked). Raises when the picture is not built."""
        raise NotImplementedError

    def write_native(self, facade, path, ext, prog):
        """Write the picture as png / jpg / tif."""
        raise NotImplementedError

    def write_tif(self, facade, path, prog):
        """Write the picture as a GeoTIFF (the source of a JPEG 2000 or ECW)."""
        raise NotImplementedError

    def sidecars(self, facade, path, options, prog):
        """Georeferencing text and per-facade 3D DXF beside the loose picture `path`."""

    def message(self, facade, path):
        """Short text kept with a written picture."""
        return ""


def _write_targets(facade, ext, targets, hooks, tools, tick, prog):
    """Write one picture to every target [(path, origin or None)]: natively (png / jpg / tif, the other targets are copies) or through a
    GeoTIFF and the converters (JPEG 2000, ECW). A target with an `origin` is a picture of the sheet: its georeferencing says where it sits."""
    for path, _origin in targets:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    if ext in NATIVE:
        first = targets[0][0]
        hooks.write_native(facade, first, ext, prog)
        if not os.path.isfile(first):
            raise SheetError("No picture was written.")
        for path, _origin in targets[1:]:
            shutil.copyfile(first, path)
        if ext == "tif":
            for path, origin in targets:
                if origin is not None:
                    geotiff.set_origin(path, origin[0], origin[1])
        return
    work = tempfile.mkdtemp(prefix="aerofacade_raw_")
    try:
        raw = os.path.join(work, "%s.tif" % facade.letter)
        hooks.write_tif(facade, raw, prog)
        if not os.path.isfile(raw):
            raise SheetError("No picture was written.")
        for path, origin in targets:
            convert_picture(raw, path, ext, tools, tick, origin)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def run_export(facades, folder, fmt, options, site, date, hooks, prog, tick=None):
    """Write the pictures (one file per facade, as before), and the drawing sheet when `options["sheet"]` asks for it.

    The sheet's pictures live in `<folder>\\00_Assets\\ortho` (copies of the loose pictures, or the only ones when
    `options["pictures"]` is false). Heights are resolved BEFORE any file is written: a missing geoid grid stops the sheet with a plain
    message and nothing of it is written. Returns [(path, ok, message)]. `prog`: a backend.Progress."""
    from .backend import Cancelled
    options = options or {}
    ext = {"jpeg": "jpg", "tiff": "tif"}.get(str(fmt).lower().lstrip("."), str(fmt).lower().lstrip("."))
    if ext not in FORMATS:
        raise ValueError("unknown format: %s" % fmt)
    opts = normalise(options.get("sheet"))
    sheet_on = opts["dxf"] or opts["dwg"]
    pictures = bool(options.get("pictures", True)) or not sheet_on
    if "tools" in options:
        tools = options["tools"]
    else:                                                  # looked for only when a converter or AutoCAD is involved
        tools = detect_tools() if (ext not in NATIVE or sheet_on) else {}
    todo = [f for f in facades if not f.removed]
    out = []
    dxf_path = os.path.join(folder, sheet_stem(site, date) + ".dxf")
    missing = dict(tools_for_formats(tools, ext, opts))
    if ext in missing:
        return [(os.path.join(folder, picture_name(site, date, todo[0].letter if todo else "A", ext)), False, missing[ext])]
    if "dwg" in missing:                                  # said, not skipped: the DXF is still written
        out.append((os.path.splitext(dxf_path)[0] + ".dwg", False, missing["dwg"]))
        opts["dwg"] = False
    os.makedirs(folder, exist_ok=True)
    infos = {}
    for f in todo:
        try:
            infos[f.letter] = hooks.info(f)
        except Cancelled:
            return out
        except Exception as exc:
            out.append((os.path.join(folder, picture_name(site, date, f.letter, ext)), False, str(exc) or exc.__class__.__name__))
    todo = [f for f in todo if f.letter in infos]
    heights, origins = None, {}
    if sheet_on and todo:
        try:
            heights = sheet_heights([HeightInput(f.letter, infos[f.letter]["h"], infos[f.letter].get("x", 0.0), infos[f.letter].get("y", 0.0))
                                     for f in todo], opts, hooks.epsg())
            layout = sheetmod.build_sheet([sheetmod.SheetFacade(f.letter, infos[f.letter]["azimuth"], tuple(infos[f.letter]["size_px"]),
                                                                float(infos[f.letter]["pixel"]), heights["y"][f.letter]) for f in todo],
                                          opts["titles"], heights["mode"], heights["short"], heights["note"], site, date)
            for f in todo:
                size, pixel = infos[f.letter]["size_px"], float(infos[f.letter]["pixel"])
                origins[f.letter] = (layout.x_of[f.letter], heights["y"][f.letter] + size[1] * pixel)
        except SheetError as exc:
            out.append((dxf_path, False, str(exc)))
            sheet_on, heights = False, None
    entries = []
    count = max(1, len(todo))
    for index, f in enumerate(todo):
        sub = prog.sub(0.9 * index / count, 0.9 * (index + 1) / count)
        loose = os.path.join(folder, picture_name(site, date, f.letter, ext))
        asset = asset_path(folder, site, date, f.letter, ext)
        targets = ([(loose, None)] if pictures else []) + ([(asset, origins[f.letter])] if sheet_on else [])
        if not targets:
            continue
        try:
            _write_targets(f, ext, targets, hooks, tools, tick, sub)
            if pictures:
                hooks.sidecars(f, loose, options, sub)
                out.append((loose, True, hooks.message(f, loose)))
            if sheet_on:
                entries.append({"letter": f.letter, "azimuth": infos[f.letter]["azimuth"], "size_px": infos[f.letter]["size_px"],
                                "pixel": infos[f.letter]["pixel"], "image": asset})
        except Cancelled:
            return out
        except Exception as exc:
            out.append((targets[0][0], False, str(exc) or exc.__class__.__name__))
    if sheet_on and entries:
        try:
            try:
                prog(0.95, "Writing the drawing sheet")
            except Cancelled:
                return out
            kept = {e["letter"] for e in entries}
            result = write_sheet(entries, folder, site, date, opts, dict(heights, y={k: v for k, v in heights["y"].items() if k in kept}),
                                 tools, tick)
            out.append((result["dxf"], True, ""))
            if result["dwg"]:
                out.append((result["dwg"], True, ""))
            for warning in result["warnings"]:
                out.append((result["dxf"], False, warning))
        except SheetError as exc:
            out.append((dxf_path, False, str(exc)))
    return out
