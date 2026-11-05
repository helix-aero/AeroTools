"""Fast Export : etat des grilles d'altitude dans notre dossier `Geoid`, telechargement verifie.

Le catalogue (systemes, grilles, tailles, SHA-256, urls) vient de `projdb.py` (base de PROJ et index PROJ-data) : plus de table ecrite a
la main, donc le monde entier. Sans dependance (urllib), tourne dans le Python de Metashape (3.9). Les grilles sont rangees dans
`own_dir()` et lues par PROJ et par `vertical.py` : on ne regarde jamais dans les dossiers de Metashape.
"""

import hashlib
import os
import tempfile
import urllib.parse
import urllib.request

from . import projdb

ALLOWED_HOSTS = ("cdn.proj.org",)
MAX_DOWNLOAD_BYTES = 120 * 1024 * 1024
ELLIPSOID = {"id": projdb.ELLIPSOID_ID, "file": None, "bbox": (-180.0, -90.0, 180.0, 90.0), "url": None,
             "fr": "Hauteur ellipsoïdale (aucune grille de géoïde)", "en": "Ellipsoidal height (no geoid grid)"}


# --------------------------------------------------------------------------------------------------
# Fiches
# --------------------------------------------------------------------------------------------------

def vertical_by_id(vid):
    """Fiche d'une grille d'altitude (id = nom du fichier sans extension) ou de l'ellipsoide ; None si inconnue."""
    if vid == projdb.ELLIPSOID_ID:
        return ELLIPSOID
    return projdb.grid_by_id(vid)


def label(item, lang="fr"):
    return item.get(lang) or item["fr"]


# --------------------------------------------------------------------------------------------------
# Geoides adaptes a un lieu
# --------------------------------------------------------------------------------------------------

def covers(item, lon, lat):
    return bool(item.get("bbox")) and projdb.inside(item["bbox"], lon, lat)


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


ZONE_MAX_SIZE = 5 * 1024 * 1024         # l'invite de la zone ne propose que les petites grilles ; les grosses se telechargent au choix


def zone_grids(project_epsg, lon, lat, folder=None):
    """[(fiche, etat)] des grilles du repere du projet qui couvrent (lon, lat), de moins de 5 Mo, absentes ou perimees. Sans repere ou
    sans position : rien (on ne sait pas ce qui sert)."""
    if lon is None or lat is None or not project_epsg or not projdb.available():
        return []
    out = []
    for spec in projdb.verticals_for(project_epsg, lon, lat, max_size=ZONE_MAX_SIZE):
        state = grid_state(spec["id"], folder)[0]
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
