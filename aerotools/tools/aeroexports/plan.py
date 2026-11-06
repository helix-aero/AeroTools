"""Aero-Exports : presets et liste des fichiers a ecrire (fonctions pures : ni Qt, ni Metashape).

Un preset dit, pour l'ortho et pour le MNT, le format du fichier et sa compression. La fenetre en tire la liste
des fichiers (`build_jobs`) : un nom court par fichier, dans le dossier Outputs, avec un numero de version si le
meme export existe deja. Le travail lui-meme (appel de Metashape) est dans msapi.export_raster ; la conversion en
JPEG 2000 / ECW et l'ecriture du DXF sont dans pipeline.py.

Presets :
    full / quick / jpeg   GeoTIFF pleine qualite, GeoTIFF compresse, JPEG + fichier monde (Metashape seul)
    jp2                   JPEG 2000 (GDAL ou Global Mapper)
    ecw                   ECW (Global Mapper)
    cad                   TOPO.dxf ou TOPO.dwg + 00_Assets\\ortho + 00_Assets\\DTM, images en JPEG 2000 ou ECW
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

# Un fichier a ecrire.
# `pixel_m` sert au nom ; `native` : on garde la grille de Metashape (rien n'est rechantillonne) ;
# `extra` : dict (CAD : chemin et extension de l'image de visualisation du MNT, calque, dossier).
Job = collections.namedtuple("Job", "kind name path pixel_m spec version native extra")
Job.__new__.__defaults__ = (None,)

# Le MNT est toujours un GeoTIFF flottant compresse sans perte.
_DEM_SPEC = {"ext": "tif", "image": "tiff", "compression": "deflate", "tiled": True, "overviews": False,
             "world": False, "white": False, "alpha": False}


def _raw_ortho(post, ext):
    """Ortho brute (GeoTIFF RVB sans perte, fond blanc) que GDAL / Global Mapper convertissent ensuite."""
    return {"ext": ext, "image": "tiff", "compression": "lzw", "quality": 90, "tiled": True, "overviews": False,
            "world": False, "white": True, "alpha": False, "post": post}


# `title` : cle de texte de la fenetre. `ortho` : reglages passes a msapi.export_raster.
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

# Au-dela, un TIFF classique (4 Go) ne suffit plus : on demande un BigTIFF. Mesure sur la taille brute, sans
# compter la compression, pour ne jamais rater le seuil.
BIGTIFF_RAW_BYTES = 3.5e9


def preset(key):
    for item in PRESETS:
        if item["key"] == key:
            return item
    return PRESETS[0]


def spec_for(preset_key, kind, cad=None):
    """Reglages d'un type de fichier. En CAD, l'extension suit le choix d'images (jp2 / ecw)."""
    spec = dict(preset(preset_key)[kind])
    if preset_key == CAD and kind == ORTHO:
        images = (cad or {}).get("images", "jp2")
        spec["ext"], spec["post"] = images, images
    return spec


def needs_bigtiff(width_px, height_px, bands=4, bytes_per_sample=1):
    """True si l'image brute depasse BIGTIFF_RAW_BYTES (ortho 4 bandes a 8 bits ; MNT 1 bande flottante)."""
    return float(width_px) * float(height_px) * bands * bytes_per_sample > BIGTIFF_RAW_BYTES


def aux_names(name, spec):
    """Fichiers que Metashape ecrit a cote du fichier principal : fichier monde et .prj d'un JPEG."""
    stem = os.path.splitext(name)[0]
    return [stem + ".jgw", stem + ".prj"] if spec.get("world") else []


def cad_dxf_name(version, fmt="dxf"):
    """TOPO.dxf, TOPO_v2.dxf..."""
    return "%s%s.%s" % (CAD_BASENAME, "_v%d" % version if version > 1 else "", fmt)


def listing_of(folder):
    """Chemins relatifs (barres obliques, minuscules) de tous les fichiers sous `folder` : pour next_free_version."""
    found = []
    for base, _dirs, names in os.walk(folder):
        for name in names:
            found.append(os.path.relpath(os.path.join(base, name), folder).replace("\\", "/").lower())
    return found


def build_jobs(site, date, wanted, preset_key, folder, lang="fr", date_style="short", listing=None, native=(),
               cad=None, version=None, dem_picture=False):
    """Liste des fichiers a ecrire.

    `wanted` : {"ortho": taille de pixel en metres, "dem": ...} pour ce qui est coche (les autres sont absents).
    `native` : les types dont on garde la grille native (pixel non modifie par l'utilisateur).
    `cad` : {"format": "dxf" | "dwg", "images": "jp2" | "ecw"} quand le preset est CAD.
    `dem_picture` : ecrire aussi l'image coloree du MNT (meme nom, extension .jp2 / .ecw) : presets JPEG 2000 et ECW seulement
    (avec un GeoTIFF, le nom serait celui du MNT lui-meme).
    `version` : numero impose (1 = ecraser) ; sinon le plus petit pour lequel aucun des fichiers prevus n'existe deja
    dans `folder` (l'ortho et le MNT d'un meme export partagent le meme numero).
    """
    kinds = [kind for kind in KINDS if wanted.get(kind)]
    if not kinds:
        return []
    is_cad = preset_key == CAD
    cad = dict({"format": "dxf", "images": "jp2"}, **(cad or {})) if is_cad else None

    def locate(kind, version_number):
        """(nom, chemin, extra) de ce que produit un type pour un numero de version."""
        spec = spec_for(preset_key, kind, cad)
        name = naming.build_name(site, date, wanted[kind], kind, spec["ext"], lang=lang, version=version_number,
                                 date_style=date_style)
        if not is_cad:
            if kind == DEM and dem_picture and preset_key in PICTURE_PRESETS:      # coloured picture next to the DEM
                ext = PICTURE_PRESETS[preset_key]
                view = naming.build_name(site, date, wanted[kind], kind, ext, lang=lang, version=version_number,
                                         date_style=date_style)
                return name, os.path.join(folder, name), {"layer_kind": "dem", "view_name": view,
                                                          "view_path": os.path.join(folder, view), "view_ext": ext}
            return name, os.path.join(folder, name), None
        where = os.path.join(folder, dxfout.ASSETS, dxfout.ORTHO_DIR if kind == ORTHO else dxfout.DEM_DIR)
        if kind == ORTHO:
            return name, os.path.join(where, name), {"layer_kind": "ortho"}
        tif = naming.build_name(site, date, wanted[kind], kind, "tif", lang=lang, version=version_number,
                                date_style=date_style)
        view = naming.build_name(site, date, wanted[kind], kind, cad["images"], lang=lang, version=version_number,
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
    """Chemins de tout ce qu'un export ecrit (fichiers principaux, annexes, DXF / DWG) : pour savoir ce qui existe deja."""
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
