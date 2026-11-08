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

from . import dxfout, naming

ORTHO, DEM = "ortho", "dem"
KINDS = (ORTHO, DEM)
CAD = "cad"
CAD_FORMATS = ("dxf", "dwg")
CAD_IMAGES = ("jp2", "ecw")
CAD_BASENAME = "TOPO"
PICTURE_PRESETS = {"jp2": "jp2", "ecw": "ecw"}      # presets that can add a coloured DEM picture, and its extension

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


def aux_names(name, spec):
    """Files that Metashape writes next to the main file: world file and .prj of a JPEG."""
    stem = os.path.splitext(name)[0]
    return [stem + ".jgw", stem + ".prj"] if spec.get("world") else []


def cad_dxf_name(version, fmt="dxf"):
    """TOPO.dxf, TOPO_v2.dxf..."""
    return "%s%s.%s" % (CAD_BASENAME, "_v%d" % version if version > 1 else "", fmt)


def listing_of(folder):
    """Relative paths (forward slashes, lower case) of all the files under `folder`: for next_free_version."""
    found = []
    for base, _dirs, names in os.walk(folder):
        for name in names:
            found.append(os.path.relpath(os.path.join(base, name), folder).replace("\\", "/").lower())
    return found


def build_jobs(site, date, wanted, preset_key, folder, date_style="short", listing=None, native=(),
               cad=None, version=None, dem_picture=False):
    """List of the files to write.

    `wanted`: {"ortho": pixel size in metres, "dem": ...} for what is ticked (the others are absent).
    `native`: the types whose native grid is kept (pixel not changed by the user).
    `cad`: {"format": "dxf" | "dwg", "images": "jp2" | "ecw"} when the preset is CAD.
    `dem_picture`: also write the coloured picture of the DTM (same name, extension .jp2 / .ecw): JPEG 2000 and ECW presets only
    (with a GeoTIFF, the name would be that of the DTM itself).
    `version`: imposed number (1 = overwrite); otherwise the smallest one for which none of the planned files already exists
    in `folder` (the orthomosaic and the DTM of one export share the same number).
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
                view = naming.build_name(site, date, wanted[kind], kind, ext, version=version_number,
                                         date_style=date_style)
                return name, os.path.join(folder, name), {"layer_kind": "dem", "view_name": view,
                                                          "view_path": os.path.join(folder, view), "view_ext": ext}
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
            names.append(os.path.relpath(path, folder).replace("\\", "/") if is_cad else name)
            if extra and extra.get("view_path"):
                names.append(os.path.relpath(extra["view_path"], folder).replace("\\", "/"))
            names.extend(aux_names(name, spec_for(preset_key, kind, cad)))
        if is_cad:
            names.append(cad_dxf_name(version_number, "dxf"))
            if cad["format"] == "dwg":
                names.append(cad_dxf_name(version_number, "dwg"))
        return names

    if version is None:
        version = naming.next_free_version(folder, planned, listing=listing if listing is not None else
                                           (listing_of(folder) if is_cad and os.path.isdir(folder) else None))
    jobs = []
    for kind in kinds:
        spec = spec_for(preset_key, kind, cad)
        name, path, extra = locate(kind, version)
        jobs.append(Job(kind, name, path, wanted[kind], spec, version, kind in native, extra))
    return jobs


def planned_files(jobs, preset_key, cad=None):
    """Paths of everything an export writes (main files, side files, DXF / DWG): to know what already exists."""
    paths = []
    for job in jobs:
        paths.append(job.path)
        paths.extend(os.path.join(os.path.dirname(job.path), n) for n in aux_names(job.name, job.spec))
        if job.extra and job.extra.get("view_path"):
            paths.append(job.extra["view_path"])
    if preset_key == CAD and jobs:
        folder = os.path.dirname(os.path.dirname(os.path.dirname(jobs[0].path)))
        fmt = (cad or {}).get("format", "dxf")
        paths.append(os.path.join(folder, cad_dxf_name(jobs[0].version, "dxf")))
        if fmt == "dwg":
            paths.append(os.path.join(folder, cad_dxf_name(jobs[0].version, "dwg")))
    return paths
