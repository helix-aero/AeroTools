"""Aero-Exports: presets and list of the files to write (pure functions: no Qt, no Metashape).

A preset says, for the orthomosaic and for the DTM, the file format and its compression. The window derives the list
of files from it (`build_jobs`): one short name per file, in the Outputs folder, with a version number if the
same export already exists. The work itself (calling Metashape) is in msapi.export_raster; the conversion to
JPEG 2000 / ECW and the writing of the DXF are in pipeline.py.

Presets:
    full / quick / jpeg   full-quality GeoTIFF, compressed GeoTIFF, JPEG + world file (Metashape alone)
    jp2                   JPEG 2000 (GDAL or Global Mapper)
    ecw                   ECW (Global Mapper)
    cad                   TOPO.dxf or TOPO.dwg + 00_Assets\\ortho + 00_Assets\\DTM, images in JPEG 2000 or ECW
"""

import collections
import os
import re

from . import dxfout, naming

ORTHO, DEM = "ortho", "dem"
KINDS = (ORTHO, DEM)
CAD = "cad"
CAD_FORMATS = ("dxf", "dwg")
CAD_IMAGES = ("jp2", "ecw")
CAD_BASENAME = "TOPO"
# Presets that can add a coloured DEM picture, and its extension. JPEG 2000 / ECW follow the preset; with the others the picture is an
# RGB GeoTIFF (named ..._dtm_view.tif so that it never takes the name of the heights); with the JPEG + world file preset it is a JPEG
# (..._dtm.jpg + .jgw: no external tool needed, and a different extension from the heights ..._dtm.tif).
PICTURE_PRESETS = {"full": "tif", "quick": "tif", "jpeg": "jpg", "jp2": "jp2", "ecw": "ecw"}
VIEW_SUFFIX = "_view"

# A file to write.
# `pixel_m` goes into the name; `native`: Metashape's grid is kept (nothing is resampled);
# `extra`: dict (CAD: path and extension of the DTM display picture, layer, folder).
Job = collections.namedtuple("Job", "kind name path pixel_m spec version native extra")
Job.__new__.__defaults__ = (None,)

# The DTM is always a losslessly compressed floating-point GeoTIFF.
_DEM_SPEC = {"ext": "tif", "image": "tiff", "compression": "deflate", "tiled": True, "overviews": False,
             "world": False, "white": False, "alpha": False}


def _raw_ortho(post, ext):
    """Raw orthomosaic (lossless RGB GeoTIFF, white background) that GDAL / Global Mapper then convert."""
    return {"ext": ext, "image": "tiff", "compression": "lzw", "quality": 90, "tiled": True, "overviews": False,
            "world": False, "white": True, "alpha": False, "post": post}


# `title`: text key of the window. `ortho`: settings passed to msapi.export_raster.
PRESETS = (
    {"key": "full", "title": "export.preset_full", "tip": "export.preset_full_tip",
     "ortho": {"ext": "tif", "image": "tiff", "compression": "lzw", "quality": 90, "tiled": True,
               "overviews": False, "world": False, "white": False, "alpha": True},
     "dem": _DEM_SPEC},
    {"key": "quick", "title": "export.preset_quick", "tip": "export.preset_quick_tip",
     "ortho": {"ext": "tif", "image": "tiff", "compression": "jpeg", "quality": 85, "tiled": True,
               "overviews": True, "world": False, "white": True, "alpha": False},
     "dem": _DEM_SPEC},
    {"key": "jpeg", "title": "export.preset_jpeg", "tip": "export.preset_jpeg_tip",
     "ortho": {"ext": "jpg", "image": "jpeg", "compression": "jpeg", "quality": 90, "tiled": False,
               "overviews": False, "world": True, "white": True, "alpha": False},
     "dem": _DEM_SPEC},
    {"key": "jp2", "title": "export.preset_jp2", "tip": "export.preset_jp2_tip",
     "ortho": _raw_ortho("jp2", "jp2"), "dem": _DEM_SPEC, "needs": "jp2"},
    {"key": "ecw", "title": "export.preset_ecw", "tip": "export.preset_ecw_tip",
     "ortho": _raw_ortho("ecw", "ecw"), "dem": _DEM_SPEC, "needs": "ecw"},
    {"key": CAD, "title": "export.preset_cad", "tip": "export.preset_cad_tip",
     "ortho": _raw_ortho("cad", "jp2"), "dem": _DEM_SPEC, "needs": "cad"},
)

# Beyond this, a classic TIFF (4 GB) is no longer enough: a BigTIFF is requested. Measured on the raw size, without
# counting compression, so the threshold is never missed.
BIGTIFF_RAW_BYTES = 3.5e9


def preset(key):
    for item in PRESETS:
        if item["key"] == key:
            return item
    return PRESETS[0]


def spec_for(preset_key, kind, cad=None):
    """Settings of a file type. In CAD, the extension follows the image choice (jp2 / ecw)."""
    spec = dict(preset(preset_key)[kind])
    if preset_key == CAD and kind == ORTHO:
        images = (cad or {}).get("images", "jp2")
        spec["ext"], spec["post"] = images, images
    return spec


def needs_bigtiff(width_px, height_px, bands=4, bytes_per_sample=1):
    """True if the raw image exceeds BIGTIFF_RAW_BYTES (orthomosaic 4 bands of 8 bits; DTM 1 floating-point band)."""
    return float(width_px) * float(height_px) * bands * bytes_per_sample > BIGTIFF_RAW_BYTES


def picture_name(name, ext):
    """Name of the coloured picture of a DEM file `name` (...dtm.tif) in the format `ext`. A GeoTIFF picture gets "_view" so that it
    does not collide with the heights themselves (the other formats have another extension)."""
    stem = os.path.splitext(name)[0]
    return stem + (VIEW_SUFFIX if ext == "tif" else "") + "." + ext


def values_wanted(job):
    """False when only the coloured picture of the DEM is wanted (the heights themselves are not written)."""
    return not (job.extra or {}).get("no_values")


def aux_names(name, spec):
    """Files that Metashape writes next to the main file: world file and .prj of a JPEG."""
    stem = os.path.splitext(name)[0]
    return [stem + ".jgw", stem + ".prj"] if spec.get("world") else []


def view_aux_names(extra):
    """Side files of the coloured DEM picture: the world file of a JPEG picture (the picture is never a source for a .prj)."""
    if (extra or {}).get("view_ext") == "jpg" and extra.get("view_name"):
        return [os.path.splitext(extra["view_name"])[0] + ".jgw"]
    return []


def cad_dxf_name(version, fmt="dxf"):
    """TOPO.dxf, TOPO_v2.dxf..."""
    return "%s%s.%s" % (CAD_BASENAME, "_v%d" % version if version > 1 else "", fmt)


_TILED = re.compile(r"^(.*)_R\d{2,3}C\d{2,3}_(ortho|dtm)(\.[A-Za-z0-9]+)$", re.I)


def untiled(name):
    """Name of the whole-image file that a tile belongs to: SITE_260704_3cm_R01C02_ortho.tif -> SITE_260704_3cm_ortho.tif.
    A name that is not a tile comes back as it is. Tiles carry their token just before the type (see naming.py)."""
    match = _TILED.match(name)
    return match.group(1) + "_" + match.group(2) + match.group(3) if match else name


def with_tiles(names):
    """`names` plus the whole-image name of each tile among them: what a folder listing looks like when the tiles of an export
    count as that export (tiled export: its files are the tiles, not the whole-image name)."""
    out = list(names)
    for name in names:
        head, tail = os.path.split(name)
        whole = untiled(tail)
        if whole != tail:
            out.append(head + "/" + whole if head else whole)
    return out


def listing_of(folder):
    """Relative paths (forward slashes, lower case) of all the files under `folder`: for next_free_version."""
    found = []
    for base, _dirs, names in os.walk(folder):
        for name in names:
            found.append(os.path.relpath(os.path.join(base, name), folder).replace("\\", "/").lower())
    return found


def build_jobs(site, date, wanted, preset_key, folder, date_style="short", listing=None, native=(),
               cad=None, version=None, dem_picture=False, tiled=False, dem_values=True):
    """List of the files to write.

    `wanted`: {"ortho": pixel size in metres, "dem": ...} for what is ticked (the others are absent).
    `native`: the types whose native grid is kept (pixel not changed by the user).
    `cad`: {"format": "dxf" | "dwg", "images": "jp2" | "ecw"} when the preset is CAD.
    `dem_picture`: also write the coloured picture of the DTM (same name, extension .jp2 / .ecw / .jpg + .jgw; a GeoTIFF
    picture is named ..._dtm_view.tif so as not to take the name of the heights).
    `dem_values`: False = the heights (GeoTIFF of the DTM) are not kept, only the picture (needs `dem_picture`).
    `version`: imposed number (1 = overwrite); otherwise the smallest one for which none of the planned files already exists
    in `folder` (the orthomosaic and the DTM of one export share the same number).
    `tiled`: the export is cut into tiles; the tiles already in the folder (..._R01C02_ortho.tif) then count as the files of that
    export, so a version already delivered as tiles is not taken for a free one.
    """
    kinds = [kind for kind in KINDS if wanted.get(kind)]
    if not kinds:
        return []
    is_cad = preset_key == CAD
    cad = dict({"format": "dxf", "images": "jp2"}, **(cad or {})) if is_cad else None

    def locate(kind, version_number):
        """(name, path, extra) of what one type produces for a version number."""
        spec = spec_for(preset_key, kind, cad)
        name = naming.build_name(site, date, wanted[kind], kind, spec["ext"], version=version_number,
                                 date_style=date_style)
        if not is_cad:
            if kind == DEM and dem_picture and preset_key in PICTURE_PRESETS:      # coloured picture next to the DEM
                ext = PICTURE_PRESETS[preset_key]
                view = picture_name(name, ext)
                return name, os.path.join(folder, name), {"layer_kind": "dem", "view_name": view,
                                                          "view_path": os.path.join(folder, view), "view_ext": ext,
                                                          "no_values": not dem_values}
            return name, os.path.join(folder, name), None
        where = os.path.join(folder, dxfout.ASSETS, dxfout.ORTHO_DIR if kind == ORTHO else dxfout.DEM_DIR)
        if kind == ORTHO:
            return name, os.path.join(where, name), {"layer_kind": "ortho"}
        tif = naming.build_name(site, date, wanted[kind], kind, "tif", version=version_number,
                                date_style=date_style)
        view = naming.build_name(site, date, wanted[kind], kind, cad["images"], version=version_number,
                                 date_style=date_style)
        return tif, os.path.join(where, tif), {"layer_kind": "dem", "view_name": view, "view_path": os.path.join(where, view),
                                               "view_ext": cad["images"]}

    def planned(version_number):
        names = []
        for kind in kinds:
            name, path, extra = locate(kind, version_number)
            if not (extra and extra.get("no_values")):
                names.append(os.path.relpath(path, folder).replace("\\", "/") if is_cad else name)
            if extra and extra.get("view_path"):
                names.append(os.path.relpath(extra["view_path"], folder).replace("\\", "/"))
                names.extend(os.path.relpath(os.path.join(os.path.dirname(extra["view_path"]), n), folder).replace("\\", "/")
                             for n in view_aux_names(extra))
            names.extend(aux_names(name, spec_for(preset_key, kind, cad)))
        if is_cad:
            names.append(cad_dxf_name(version_number, "dxf"))
            if cad["format"] == "dwg":
                names.append(cad_dxf_name(version_number, "dwg"))
        return names

    if version is None:
        found = listing if listing is not None else (
            listing_of(folder) if (is_cad or tiled) and os.path.isdir(folder) else None)
        if tiled and found is not None:
            found = with_tiles(found)
        version = naming.next_free_version(folder, planned, listing=found)
    jobs = []
    for kind in kinds:
        spec = spec_for(preset_key, kind, cad)
        name, path, extra = locate(kind, version)
        jobs.append(Job(kind, name, path, wanted[kind], spec, version, kind in native, extra))
    return jobs


def existing_files(jobs, preset_key, cad=None, tiled=False):
    """What an export of `jobs` would overwrite: the planned files that exist, plus, when the export is `tiled`, the tiles of the
    same names already on disk (they are not in `planned_files`, whose names are those of the whole images)."""
    found = [path for path in planned_files(jobs, preset_key, cad) if os.path.exists(path)]
    if tiled:
        for path in planned_files(jobs, preset_key, cad):
            folder, name = os.path.split(path)
            try:
                listed = os.listdir(folder)
            except OSError:
                continue
            found.extend(os.path.join(folder, other) for other in sorted(listed)
                         if untiled(other) != other and untiled(other).lower() == name.lower())
    return found


def planned_files(jobs, preset_key, cad=None):
    """Paths of everything an export writes (main files, side files, DXF / DWG): to know what already exists."""
    paths = []
    for job in jobs:
        if values_wanted(job):
            paths.append(job.path)
            paths.extend(os.path.join(os.path.dirname(job.path), n) for n in aux_names(job.name, job.spec))
        if job.extra and job.extra.get("view_path"):
            paths.append(job.extra["view_path"])
            paths.extend(os.path.join(os.path.dirname(job.extra["view_path"]), n) for n in view_aux_names(job.extra))
    if preset_key == CAD and jobs:
        folder = os.path.dirname(os.path.dirname(os.path.dirname(jobs[0].path)))
        fmt = (cad or {}).get("format", "dxf")
        paths.append(os.path.join(folder, cad_dxf_name(jobs[0].version, "dxf")))
        if fmt == "dwg":
            paths.append(os.path.join(folder, cad_dxf_name(jobs[0].version, "dwg")))
    return paths
