"""Fast Export : presets et liste des fichiers a ecrire (fonctions pures : ni Qt, ni Metashape).

Un preset dit, pour l'ortho et pour le MNT, le format du fichier et sa compression. La fenetre en tire la liste
des fichiers (`build_jobs`) : un nom court par fichier, dans le dossier Outputs, avec un numero de version si le
meme export existe deja. Le travail lui-meme (appel de Metashape) est dans msapi.export_raster.
"""

import collections
import os

from . import naming

ORTHO, DEM = "ortho", "dem"
KINDS = (ORTHO, DEM)

# Un fichier a ecrire.
# `pixel_m` sert au nom ; `native` : on garde la grille de Metashape (rien n'est rechantillonne).
Job = collections.namedtuple("Job", "kind name path pixel_m spec version native")

# Le MNT est toujours un GeoTIFF flottant compresse sans perte.
_DEM_SPEC = {"ext": "tif", "image": "tiff", "compression": "deflate", "tiled": True, "overviews": False,
             "world": False, "white": False, "alpha": False}

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
)

# Au-dela, un TIFF classique (4 Go) ne suffit plus : on demande un BigTIFF. Mesure sur la taille brute, sans
# compter la compression, pour ne jamais rater le seuil.
BIGTIFF_RAW_BYTES = 3.5e9


def preset(key):
    for item in PRESETS:
        if item["key"] == key:
            return item
    return PRESETS[0]


def spec_for(preset_key, kind):
    return dict(preset(preset_key)[kind])


def needs_bigtiff(width_px, height_px, bands=4, bytes_per_sample=1):
    """True si l'image brute depasse BIGTIFF_RAW_BYTES (ortho 4 bandes a 8 bits ; MNT 1 bande flottante)."""
    return float(width_px) * float(height_px) * bands * bytes_per_sample > BIGTIFF_RAW_BYTES


def aux_names(name, spec):
    """Fichiers que Metashape ecrit a cote du fichier principal : fichier monde et .prj d'un JPEG."""
    stem = os.path.splitext(name)[0]
    return [stem + ".jgw", stem + ".prj"] if spec.get("world") else []


def build_jobs(site, date, wanted, preset_key, folder, lang="fr", date_style="short", listing=None, native=()):
    """Liste des fichiers a ecrire.

    `wanted` : {"ortho": taille de pixel en metres, "dem": ...} pour ce qui est coche (les autres sont absents).
    `native` : les types dont on garde la grille native (pixel non modifie par l'utilisateur).
    L'ortho et le MNT d'un meme export partagent le meme numero de version : le plus petit pour lequel aucun
    des fichiers prevus n'existe deja dans `folder`.
    """
    kinds = [kind for kind in KINDS if wanted.get(kind)]
    if not kinds:
        return []

    def planned(version):
        names = []
        for kind in kinds:
            spec = spec_for(preset_key, kind)
            name = naming.build_name(site, date, wanted[kind], kind, spec["ext"], lang=lang, version=version,
                                     date_style=date_style)
            names.append(name)
            names.extend(aux_names(name, spec))
        return names

    version = naming.next_free_version(folder, planned, listing=listing)
    jobs = []
    for kind in kinds:
        spec = spec_for(preset_key, kind)
        name = naming.build_name(site, date, wanted[kind], kind, spec["ext"], lang=lang, version=version,
                                 date_style=date_style)
        jobs.append(Job(kind, name, os.path.join(folder, name), wanted[kind], spec, version, kind in native))
    return jobs
