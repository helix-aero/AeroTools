"""Fast Export : catalogue OFFICIEL des SCR et des geoides, etat des grilles dans notre dossier, telechargement verifie.

Sans dependance (pyproj / rasterio ne servent qu'aux tests). Tourne dans le Python de Metashape (3.9), telechargement en
``urllib``. Les grilles sont rangees dans notre dossier `Geoid` (`own_dir`) et lues par `vertical.py` : on ne regarde jamais
dans les dossiers de Metashape.

Sources de verite
    * Codes EPSG : registre EPSG v12.029 (2025-10-02) lu via la base de pyproj.
    * Grilles : index PROJ-data https://cdn.proj.org/files.geojson (taille + sha256 de chaque fichier) ;
      les grilles IGN d'origine (RAF20.tac, RAC23.mnt...) ne sont lisibles ni par Metashape ni par PROJ :
      PROJ-data les republie au format GTG (GeoTIFF), « derived from work by France IGN ».
"""

import hashlib
import os
import tempfile
import urllib.parse
import urllib.request

ALLOWED_HOSTS = ("cdn.proj.org", "www.ign.fr", "geodesie.ign.fr", "www.agisoft.com")
MAX_DOWNLOAD_BYTES = 120 * 1024 * 1024

# --------------------------------------------------------------------------------------------------
# SCR horizontaux
# --------------------------------------------------------------------------------------------------
# kind : proj (projete) / geo (geographique). family : pour regrouper dans la liste deroulante.
# `v2b` : le repere RGF93 v2b (grilles RAF20 / RAC23) ; les codes EPSG v1 (2154, 3942...) restent les plus courants.
CRS_LIST = [
    # --- France metropolitaine + Corse
    {"epsg": 2154, "fr": "RGF93 v1 / Lambert-93", "en": "RGF93 v1 / Lambert-93", "kind": "proj", "family": "fr", "default": True,
     "area": "France metropolitaine et Corse", "bbox": (-9.86, 41.15, 10.38, 51.56)},
    {"epsg": 9794, "fr": "RGF93 v2b / Lambert-93", "en": "RGF93 v2b / Lambert-93", "kind": "proj", "family": "fr",
     "area": "France metropolitaine et Corse", "bbox": (-9.86, 41.15, 10.38, 51.56)},
    {"epsg": 3942, "fr": "RGF93 v1 / CC42", "en": "RGF93 v1 / CC42", "kind": "proj", "family": "cc",
     "area": "sud du 43e parallele et Corse", "bbox": (-1.06, 41.31, 9.63, 43.07)},
    {"epsg": 3943, "fr": "RGF93 v1 / CC43", "en": "RGF93 v1 / CC43", "kind": "proj", "family": "cc", "area": "France metropolitaine, 42,33-44,01 N", "bbox": (-1.79, 42.33, 7.65, 44.01)},
    {"epsg": 3944, "fr": "RGF93 v1 / CC44", "en": "RGF93 v1 / CC44", "kind": "proj", "family": "cc", "area": "France metropolitaine, 42,92-45,0 N", "bbox": (-1.79, 42.92, 7.71, 45.0)},
    {"epsg": 3945, "fr": "RGF93 v1 / CC45", "en": "RGF93 v1 / CC45", "kind": "proj", "family": "cc", "area": "France metropolitaine, 44,0-46,0 N", "bbox": (-1.46, 44.0, 7.71, 46.0)},
    {"epsg": 3946, "fr": "RGF93 v1 / CC46", "en": "RGF93 v1 / CC46", "kind": "proj", "family": "cc", "area": "France metropolitaine, 45,0-47,0 N", "bbox": (-2.21, 45.0, 7.16, 47.0)},
    {"epsg": 3947, "fr": "RGF93 v1 / CC47", "en": "RGF93 v1 / CC47", "kind": "proj", "family": "cc", "area": "France metropolitaine, 46,0-48,0 N", "bbox": (-4.77, 46.0, 7.63, 48.0)},
    {"epsg": 3948, "fr": "RGF93 v1 / CC48", "en": "RGF93 v1 / CC48", "kind": "proj", "family": "cc", "area": "France metropolitaine, 47,0-49,0 N", "bbox": (-4.87, 47.0, 8.23, 49.0)},
    {"epsg": 3949, "fr": "RGF93 v1 / CC49", "en": "RGF93 v1 / CC49", "kind": "proj", "family": "cc", "area": "France metropolitaine, 48,0-50,0 N", "bbox": (-4.87, 48.0, 8.23, 50.0)},
    {"epsg": 3950, "fr": "RGF93 v1 / CC50", "en": "RGF93 v1 / CC50", "kind": "proj", "family": "cc", "area": "nord du 49e parallele", "bbox": (-2.03, 49.0, 8.08, 51.14)},
    {"epsg": 9842, "fr": "RGF93 v2b / CC42", "en": "RGF93 v2b / CC42", "kind": "proj", "family": "cc2b", "area": "sud du 43e parallele et Corse", "bbox": (-1.06, 41.31, 9.63, 43.07)},
    {"epsg": 9843, "fr": "RGF93 v2b / CC43", "en": "RGF93 v2b / CC43", "kind": "proj", "family": "cc2b", "area": "France metropolitaine, 42,33-44,01 N", "bbox": (-1.79, 42.33, 7.65, 44.01)},
    {"epsg": 9844, "fr": "RGF93 v2b / CC44", "en": "RGF93 v2b / CC44", "kind": "proj", "family": "cc2b", "area": "France metropolitaine, 42,92-45,0 N", "bbox": (-1.79, 42.92, 7.71, 45.0)},
    {"epsg": 9845, "fr": "RGF93 v2b / CC45", "en": "RGF93 v2b / CC45", "kind": "proj", "family": "cc2b", "area": "France metropolitaine, 44,0-46,0 N", "bbox": (-1.46, 44.0, 7.71, 46.0)},
    {"epsg": 9846, "fr": "RGF93 v2b / CC46", "en": "RGF93 v2b / CC46", "kind": "proj", "family": "cc2b", "area": "France metropolitaine, 45,0-47,0 N", "bbox": (-2.21, 45.0, 7.16, 47.0)},
    {"epsg": 9847, "fr": "RGF93 v2b / CC47", "en": "RGF93 v2b / CC47", "kind": "proj", "family": "cc2b", "area": "France metropolitaine, 46,0-48,0 N", "bbox": (-4.77, 46.0, 7.63, 48.0)},
    {"epsg": 9848, "fr": "RGF93 v2b / CC48", "en": "RGF93 v2b / CC48", "kind": "proj", "family": "cc2b", "area": "France metropolitaine, 47,0-49,0 N", "bbox": (-4.87, 47.0, 8.23, 49.0)},
    {"epsg": 9849, "fr": "RGF93 v2b / CC49", "en": "RGF93 v2b / CC49", "kind": "proj", "family": "cc2b", "area": "France metropolitaine, 48,0-50,0 N", "bbox": (-4.87, 48.0, 8.23, 50.0)},
    {"epsg": 9850, "fr": "RGF93 v2b / CC50", "en": "RGF93 v2b / CC50", "kind": "proj", "family": "cc2b", "area": "nord du 49e parallele", "bbox": (-2.03, 49.0, 8.08, 51.14)},
    {"epsg": 32632, "fr": "WGS 84 / UTM 32N", "en": "WGS 84 / UTM zone 32N", "kind": "proj", "family": "utm", "area": "6-12 E (Corse, est de la France)", "bbox": (6.0, 0.0, 12.0, 84.0)},
    {"epsg": 32631, "fr": "WGS 84 / UTM 31N", "en": "WGS 84 / UTM zone 31N", "kind": "proj", "family": "utm", "area": "0-6 E (France de l'ouest)", "bbox": (0.0, 0.0, 6.0, 84.0)},
    {"epsg": 25832, "fr": "ETRS89 / UTM 32N", "en": "ETRS89 / UTM zone 32N", "kind": "proj", "family": "utm", "area": "6-12 E, Europe", "bbox": (6.0, 36.53, 12.01, 84.01)},
    {"epsg": 25831, "fr": "ETRS89 / UTM 31N", "en": "ETRS89 / UTM zone 31N", "kind": "proj", "family": "utm", "area": "0-6 E, Europe", "bbox": (0.0, 37.0, 6.01, 82.45)},
    {"epsg": 4171, "fr": "RGF93 v1 (geographique)", "en": "RGF93 v1 (geographic)", "kind": "geo", "family": "geo",
     "area": "France metropolitaine et Corse", "bbox": (-9.86, 41.15, 10.38, 51.56)},
    {"epsg": 9782, "fr": "RGF93 v2b (geographique)", "en": "RGF93 v2b (geographic)", "kind": "geo", "family": "geo",
     "area": "France metropolitaine et Corse", "bbox": (-9.86, 41.15, 10.38, 51.56)},
    {"epsg": 4326, "fr": "WGS 84 (lat/lon)", "en": "WGS 84 (lat/lon)", "kind": "geo", "family": "geo", "area": "Monde", "bbox": (-180, -90, 180, 90)},
    # --- Outre-mer (RGAF09, RGFG95, RGR92, RGM04)
    {"epsg": 5490, "fr": "RGAF09 / UTM 20N (Antilles)", "en": "RGAF09 / UTM zone 20N (Antilles)", "kind": "proj", "family": "dom", "area": "Guadeloupe, Martinique", "bbox": (-63.66, 14.08, -60.0, 18.32)},
    {"epsg": 2972, "fr": "RGFG95 / UTM 22N (Guyane)", "en": "RGFG95 / UTM zone 22N (Guiana)", "kind": "proj", "family": "dom", "area": "Guyane", "bbox": (-54.0, 2.17, -49.45, 8.88)},
    {"epsg": 2975, "fr": "RGR92 / UTM 40S (Reunion)", "en": "RGR92 / UTM zone 40S (Reunion)", "kind": "proj", "family": "dom", "area": "La Reunion", "bbox": (54.0, -24.72, 58.24, -18.28)},
    {"epsg": 4471, "fr": "RGM04 / UTM 38S (Mayotte)", "en": "RGM04 / UTM zone 38S (Mayotte)", "kind": "proj", "family": "dom", "area": "Mayotte", "bbox": (43.68, -14.49, 46.7, -11.33)},
]

# --------------------------------------------------------------------------------------------------
# Systemes altimetriques et grilles
# --------------------------------------------------------------------------------------------------
# `vert_epsg` : EPSG du systeme vertical ; `horz_epsg` : repere 3D auquel la grille est rattachee.
# `file` : nom du fichier GTG (identique chez PROJ-data et dans le dossier geoids de Metashape).
# `size`, `sha256` : ceux de cdn.proj.org (index files.geojson du 2026-11-05). `bbox` : emprise des noeuds de la grille (lon/lat, relevee dans le fichier).
VERTICALS = [
    {"id": "ellipsoid", "fr": "Hauteur ellipsoidale (aucun geoide)", "en": "Ellipsoidal height (no geoid)", "vert_epsg": None,
     "file": None, "area": "partout", "bbox": (-180, -90, 180, 90)},
    {"id": "RAF20", "fr": "NGF-IGN69 (RAF20, RGF93 v2b)", "en": "NGF-IGN69 (RAF20, RGF93 v2b)", "vert_epsg": 5720, "horz_epsg": 9781,
     "file": "fr_ign_RAF20.tif", "area": "France metropolitaine continentale (PAS la Corse)", "bbox": (-5.5, 42.0, 8.5, 51.5),
     "size": 342920, "sha256": "dc0cc2a38f0ea1029fe72cca3b5b7ed6dfe7e1db2a8d8482b7326ce3d6f25605",
     "url": "https://cdn.proj.org/fr_ign_RAF20.tif", "official": "https://www.ign.fr/publications-de-l-ign/Geodesie/RAF20.tac",
     "note": "Grille IGN de reference depuis 2021 ; RGF93 v2b -> IGN69.", "recommended": True},
    {"id": "RAF18b", "fr": "NGF-IGN69 (RAF18b, ancien)", "en": "NGF-IGN69 (RAF18b, legacy)", "vert_epsg": 5720, "horz_epsg": 4965,
     "file": "fr_ign_RAF18b.tif", "area": "France metropolitaine continentale", "bbox": (-5.5, 42.0, 8.5, 51.5),
     "size": 341314, "sha256": "59522db6682a42dd0fa25edd7b6b18c92ae3224324809051d80ab960a76b67e9",
     "url": "https://cdn.proj.org/fr_ign_RAF18b.tif", "official": "sur demande a geodesie@ign.fr (archivee)",
     "note": "Pour relivrer un chantier ancien calcule en RAF18b."},
    {"id": "RAF09", "fr": "NGF-IGN69 (RAF09, RGF93 v1)", "en": "NGF-IGN69 (RAF09, RGF93 v1)", "vert_epsg": 5720, "horz_epsg": 4965,
     "file": "fr_ign_RAF09.tif", "area": "France metropolitaine continentale", "bbox": (-5.5, 42.0, 8.5, 51.5),
     "size": 344998, "sha256": "7396144b7fe903e42f0f28a4f55bf1e01e2947c8cb134af7f13a403d64db9927",
     "url": "https://cdn.proj.org/fr_ign_RAF09.tif", "official": "https://www.ign.fr/publications-de-l-ign/Geodesie/RAF09.mnt",
     "note": "Ancienne grille, encore dans certaines commandes."},
    {"id": "RAC23", "fr": "NGF-IGN78 Corse (RAC23, RGF93 v2b)", "en": "NGF-IGN78 Corsica (RAC23, RGF93 v2b)", "vert_epsg": 5721, "horz_epsg": 9781,
     "file": "fr_ign_RAC23.tif", "area": "Corse", "bbox": (8.3833, 41.2, 9.75, 43.25),
     "size": 9162, "sha256": "075c061558c091f0e188256d62523e95b00deb90e2436550581f041b60811bab",
     "url": "https://cdn.proj.org/fr_ign_RAC23.tif", "official": "https://www.ign.fr/publications-de-l-ign/Geodesie/RAC23.mnt",
     "note": "Grille IGN de reference pour la Corse depuis l'automne 2023 ; RGF93 v2b -> IGN78.", "recommended": True},
    {"id": "RAC09", "fr": "NGF-IGN78 Corse (RAC09, RGF93 v1)", "en": "NGF-IGN78 Corsica (RAC09, RGF93 v1)", "vert_epsg": 5721, "horz_epsg": 4965,
     "file": "fr_ign_RAC09.tif", "area": "Corse", "bbox": (8.4167, 41.2, 9.7167, 43.2),
     "size": 8597, "sha256": "a1519cc41df3e1a6dbd8135782cca0cd6359f0cb5ec5e52cb531e4f567e59cdd",
     "url": "https://cdn.proj.org/fr_ign_RAC09.tif", "official": "sur demande a geodesie@ign.fr (archivee)",
     "note": "Ancienne grille corse ; utilisee par beaucoup de chantiers deja livres (ex. DTM_HADI_rac09)."},
    {"id": "EGM2008", "fr": "EGM2008 (mondial, 2,5')", "en": "EGM2008 (global, 2.5')", "vert_epsg": 3855, "horz_epsg": 4979,
     "file": "us_nga_egm08_25.tif", "area": "Monde", "bbox": (-180, -90, 180, 90),
     "size": 80585622, "sha256": "4191d471eefebf24091b56dbc604353cb3b8cf8cc70e448bb9ae56a272bef17a",
     "url": "https://cdn.proj.org/us_nga_egm08_25.tif", "official": "https://www.agisoft.com/downloads/geoids/",
     "note": "Fichier PROJ-data de 80 Mo ; Agisoft propose aussi sa version (2,5' et 1'). Nom de fichier different de celui d'Agisoft : verifier le doublon."},
    {"id": "EGM96", "fr": "EGM96 (mondial, 15')", "en": "EGM96 (global, 15')", "vert_epsg": 5773, "horz_epsg": 4979,
     "file": "us_nga_egm96_15.tif", "area": "Monde", "bbox": (-180, -90, 180, 90),
     "size": 2710815, "sha256": "db493027562c9b004d7220fa881f5603adada4e1c5029b933fa7de4547b0e78d",
     "url": "https://cdn.proj.org/us_nga_egm96_15.tif", "official": "installe avec Metashape (egm96-15.tif)",
     "note": "Deja fourni par Metashape sous le nom egm96-15.tif.", "alt_files": ["egm96-15.tif"]},
]

# Grilles des DOM (PROJ-data, source IGN) : proposees seulement si le projet est hors metropole.
VERTICALS_DOM = [
    {"id": "RAGTBT2016", "fr": "Guadeloupe 1988 (RAGTBT2016)", "en": "Guadeloupe 1988 (RAGTBT2016)", "vert_epsg": 5757, "horz_epsg": 5488,
     "file": "fr_ign_RAGTBT2016.tif", "area": "Guadeloupe (Grande-Terre, Basse-Terre)", "size": 33374,
     "sha256": "5984c1565e9a65b88d5608c7bd3cdbf39f7f13c84b453e1984feab56384d1baa", "url": "https://cdn.proj.org/fr_ign_RAGTBT2016.tif"},
    {"id": "RAMART2016", "fr": "Martinique 1987 (RAMART2016)", "en": "Martinique 1987 (RAMART2016)", "vert_epsg": 5756, "horz_epsg": 5488,
     "file": "fr_ign_RAMART2016.tif", "area": "Martinique", "size": 23884,
     "sha256": "350a9cd98b5ab68566870c99c698490630ac9d221c34e7595c75afaf4c435243", "url": "https://cdn.proj.org/fr_ign_RAMART2016.tif"},
    {"id": "ggguy15", "fr": "NGG1977 Guyane (ggguy15)", "en": "NGG1977 French Guiana (ggguy15)", "vert_epsg": 5755, "horz_epsg": 4967,
     "file": "fr_ign_ggguy15.tif", "area": "Guyane", "size": 114493,
     "sha256": "577d18e74ae6f15ee66db245deb9c9837cab0ab460c28bbc40e0f0dbb7a83c49", "url": "https://cdn.proj.org/fr_ign_ggguy15.tif"},
    {"id": "RAR07", "fr": "Reunion 1989 (RAR07)", "en": "Reunion 1989 (RAR07)", "vert_epsg": 5758, "horz_epsg": 4971,
     "file": "fr_ign_RAR07_bl.tif", "area": "La Reunion", "size": 10378,
     "sha256": "730aea12ee8642561d5c3bf26ffc023222e349bfcdf6ee676f999a875531d4a4", "url": "https://cdn.proj.org/fr_ign_RAR07_bl.tif"},
]


# Altitudes proposees a l'export (les grilles sont lues et appliquees par AeroTools : voir vertical.py).
EXPORT_VERTICALS = ("RAF20", "RAF09", "RAC23", "RAC09", "EGM96", "ellipsoid")


def crs_by_epsg(epsg):
    for item in CRS_LIST:
        if item["epsg"] == epsg:
            return item
    return None


def vertical_by_id(vid):
    for item in VERTICALS + VERTICALS_DOM:
        if item["id"] == vid:
            return item
    return None


def label(item, lang="fr"):
    return item.get(lang) or item["fr"]


def crs_choices(lon=None, lat=None, lang="fr", projected_only=True):
    """Liste (epsg, libelle) a proposer. Avec lon/lat (centre du projet, degres) : les SCR dont l'emprise contient le point
    d'abord (Lambert-93 en tete, puis la CC du lieu, UTM), puis le reste. Jamais de SCR hors catalogue.
    Les SCR geographiques (degres) sont ecartes par defaut : le pixel d'un export se donne en metres."""
    items = [c for c in CRS_LIST if not projected_only or c["kind"] != "geo"]
    if lon is not None and lat is not None:
        def inside(c):
            b = c["bbox"]
            return b[0] <= lon <= b[2] and b[1] <= lat <= b[3]
        order = {"fr": 0, "cc": 1, "utm": 2, "geo": 3, "cc2b": 4, "dom": 5}
        items.sort(key=lambda c: (not inside(c), order.get(c["family"], 9), c["epsg"]))
    return [(c["epsg"], label(c, lang)) for c in items]


# --------------------------------------------------------------------------------------------------
# Geoides adaptes a un lieu
# --------------------------------------------------------------------------------------------------

def covers(item, lon, lat):
    b = item.get("bbox")
    return bool(b) and b[0] <= lon <= b[2] and b[1] <= lat <= b[3]


def own_dir():
    """Dossier « Geoid » d'AeroTools (par poste) : les grilles telechargees par la fenetre y sont rangees et lues. Rien n'est
    cherche dans les dossiers de Metashape."""
    from ... import config
    return os.path.join(config.settings_dir(), "Geoid")


_SHA_CACHE = {}


def grid_state(vertical_id, folder=None):
    """("ok" | "stale" | "missing", chemin) de la grille dans notre dossier. « stale » : le fichier est la mais son empreinte
    (SHA-256) n'est pas celle de la grille officielle du catalogue : copie abimee, ancienne ou modifiee, a retelecharger."""
    spec = vertical_by_id(vertical_id)
    if spec is None or not spec.get("file"):
        raise KeyError(vertical_id)
    path = os.path.join(folder or own_dir(), spec["file"])
    if not os.path.isfile(path):
        return "missing", path
    stamp = (path, os.path.getsize(path), os.path.getmtime(path))
    if stamp not in _SHA_CACHE:
        _SHA_CACHE[stamp] = sha256_file(path)
    return ("ok" if _SHA_CACHE[stamp] == spec.get("sha256") else "stale"), path


def zone_grids(lon, lat, folder=None):
    """[(fiche, etat)] des grilles de l'export qui couvrent (lon, lat), se telechargent, et manquent ou sont perimees. Sans position :
    rien (on ne sait pas ce qui sert). EGM96, mondiale, est toujours demandee quand la position est connue."""
    if lon is None or lat is None:
        return []
    out = []
    for vid in EXPORT_VERTICALS:
        spec = vertical_by_id(vid)
        if spec.get("file") and spec.get("url") and covers(spec, lon, lat):
            state = grid_state(vid, folder)[0]
            if state != "ok":
                out.append((spec, state))
    return out


def check_geoid_presence(vertical_id, folder=None, lon=None, lat=None):
    """Diagnostic pour l'interface : {'status': 'ok'|'stale'|'missing'|'not_covering'|'none', 'spec': fiche, 'path': chemin}."""
    spec = vertical_by_id(vertical_id)
    if spec is None:
        raise KeyError(vertical_id)
    if spec.get("file") is None:
        return {"status": "none", "spec": spec, "path": None}
    if lon is not None and lat is not None and spec.get("bbox") and not covers(spec, lon, lat):
        return {"status": "not_covering", "spec": spec, "path": None}
    state, path = grid_state(vertical_id, folder)
    return {"status": state, "spec": spec, "path": path}


# --------------------------------------------------------------------------------------------------
# Telechargement verifie
# --------------------------------------------------------------------------------------------------

def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def _check_url(url):
    parts = urllib.parse.urlparse(url)
    if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS:
        raise ValueError("source non autorisee : %s (seulement HTTPS vers %s)" % (url, ", ".join(ALLOWED_HOSTS)))


def download_geoid(vertical_id, dest_dir, overwrite=False, progress=None, timeout=60, opener=None):
    """Telecharge la grille du catalogue dans `dest_dir` ; verifie taille ET sha256 AVANT de la poser.

    * HTTPS uniquement, hotes de ALLOWED_HOSTS, au plus MAX_DOWNLOAD_BYTES ;
    * ecrit dans un fichier temporaire du meme dossier, renomme seulement si le hash est bon (atomique) ;
    * ne remplace jamais un fichier existant sauf `overwrite=True` ;
    * ne s'execute rien : c'est une donnee. Renvoie le chemin final. Leve ValueError / OSError.
    `progress(octets_recus, octets_total)` optionnel ; `opener` (tests) remplace urllib.request.urlopen.
    """
    spec = vertical_by_id(vertical_id)
    if spec is None or not spec.get("url"):
        raise ValueError("pas de source officielle telechargeable pour %r" % (vertical_id,))
    _check_url(spec["url"])
    if spec["size"] > MAX_DOWNLOAD_BYTES:
        raise ValueError("fichier trop gros (%d octets)" % spec["size"])
    os.makedirs(dest_dir, exist_ok=True)
    final = os.path.join(dest_dir, spec["file"])
    if os.path.exists(final) and not overwrite:
        raise FileExistsError(final)
    fd, tmp = tempfile.mkstemp(prefix=spec["file"] + ".", suffix=".part", dir=dest_dir)
    os.close(fd)
    try:
        request = urllib.request.Request(spec["url"], headers={"User-Agent": "HELIX-FastExport/0.1"})
        with (opener or urllib.request.urlopen)(request, timeout=timeout) as resp, open(tmp, "wb") as out:
            total = int(resp.headers.get("Content-Length") or spec["size"])
            got = 0
            while True:
                block = resp.read(1 << 16)
                if not block:
                    break
                got += len(block)
                if got > MAX_DOWNLOAD_BYTES:
                    raise ValueError("telechargement trop gros")
                out.write(block)
                if progress:
                    progress(got, total)
        if os.path.getsize(tmp) != spec["size"]:
            raise ValueError("taille recue %d, attendue %d" % (os.path.getsize(tmp), spec["size"]))
        digest = sha256_file(tmp)
        if digest != spec["sha256"]:
            raise ValueError("sha256 inattendu : %s (attendu %s)" % (digest, spec["sha256"]))
        os.replace(tmp, final)
        return final
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
