"""Aero-Exports : systemes d'altitude calcules par AeroTools (grilles de geoide lues et appliquees ici, pas par Metashape).

Pourquoi : Metashape 2.2.1 choisit sa grille par le nom du datum et non par le fichier (« RAC23 » et « RAC09 » donnent la
meme valeur, 23 cm d'ecart avec la vraie grille RAC23 en Corse), ignore RAC23 avec un repere RGF93 v2b, et refuse EGM96 avec un
repere francais. Ici : Metashape exporte le MNT en hauteurs ellipsoidales (ce qu'il fait bien), puis on retranche l'ondulation
du geoide lue dans notre dossier `Geoid` (`H = h - N`).

Grilles : GeoTIFF « GTG » de PROJ-data (Float32, une bande `geoid_undulation`, Deflate + predicteur flottant), noeuds regulierement
espaces en longitude / latitude, interpolation bilineaire comme PROJ. Valeurs de reference : voir tests/test_aeroexports_vertical.py.
"""

import hashlib
import math
import os

import numpy as np

from . import crs, projdb, tiffio

NODATA_LIMIT = -1000.0          # une ondulation plus petite que ca est une valeur « pas de donnee »
LATTICE_STEP_M = 100.0          # pas du semis de points ou l'on calcule l'ondulation d'un MNT (elle varie de quelques cm par km)
MAX_LATTICE = 400


class Grid(object):
    """Une grille d'ondulation : `values[ligne, colonne]`, ligne 0 au nord ; noeuds en (lon0 + col * dlon, lat0 - ligne * dlat)."""

    def __init__(self, values, lon0, lat0, dlon, dlat):
        self.values, self.lon0, self.lat0, self.dlon, self.dlat = values, lon0, lat0, dlon, dlat
        self.rows, self.cols = values.shape

    def bounds(self):
        """(lon min, lat min, lon max, lat max) des noeuds."""
        return (self.lon0, self.lat0 - (self.rows - 1) * self.dlat, self.lon0 + (self.cols - 1) * self.dlon, self.lat0)

    def undulation(self, lon, lat):
        """N (metres) aux points (tableaux ou nombres) ; NaN hors de la grille ou sur un noeud sans donnee."""
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
    """Grid d'un fichier GTG, ou ValueError. Le georeferencement vient du fichier : pas de table supposee."""
    tiff = tiffio.Tiff(path)
    scale = tiff.ints(tiffio.T_SCALE)
    tie = tiff.ints(tiffio.T_TIEPOINT)
    if not scale or not tie or len(tie) < 6:
        raise ValueError("grille sans georeferencement")
    if _raster_type(tiff) != RASTER_PIXEL_IS_POINT:
        raise ValueError("grille « PixelIsArea » : repere des noeuds non verifie")
    values = tiff.read_all()
    i, j, _k, x, y, _z = tie[:6]
    dlon, dlat = scale[0], scale[1]
    # PixelIsPoint : le point de calage est le noeud (0, 0) lui-meme. Verifie contre PROJ 9.8 : ecart 0,0 mm sur 4 points de RAC23.
    return Grid(values, x - i * dlon, y + j * dlat, dlon, dlat)


RASTER_PIXEL_IS_POINT = 2


def _raster_type(tiff):
    """Valeur de la clef GeoTIFF GTRasterTypeGeoKey (1 = PixelIsArea, 2 = PixelIsPoint) ; 1 si absente."""
    keys = tiff.ints(tiffio.T_GEOKEYS, [])
    for index in range(4, len(keys) - 3, 4):
        if keys[index] == 1025 and keys[index + 1] == 0:
            return keys[index + 3]
    return 1


# --------------------------------------------------------------------------------------------------
# Quelle grille pour quel repere (la regle vient de PROJ : voir projdb.py)
# --------------------------------------------------------------------------------------------------

def compatible_verticals(epsg, lon=None, lat=None, folder=None):
    """Altitudes proposables avec ce SCR horizontal a la position (lon, lat), dans le monde entier : fiches de grilles du meme repere
    (la plus locale d'abord), puis l'ellipsoide. `folder` : si une grille est deja la et qu'elle ne couvre pas le centre du projet
    (emprise rectangulaire mais donnees seulement sur le pays), on l'ecarte."""
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
# Grille d'un identifiant, correction d'un MNT
# --------------------------------------------------------------------------------------------------

_GRID_CACHE = {}


def load_grid(vertical_id, folder=None):
    """Grid de l'altitude demandee, lue dans notre dossier (verifiee par son empreinte) ; ValueError sinon."""
    state, path = crs.grid_state(vertical_id, folder)
    if state != "ok":
        raise ValueError("grille %s : %s" % (vertical_id, state))
    stamp = (path, os.path.getsize(path), os.path.getmtime(path))
    if stamp not in _GRID_CACHE:
        _GRID_CACHE.clear()
        _GRID_CACHE[stamp] = read_grid(path)
    return _GRID_CACHE[stamp]


def _lattice(width, height, px, py):
    """Nombre de points (colonnes, lignes) du semis qui couvre un raster."""
    cols = int(min(MAX_LATTICE, max(2, math.ceil(width * px / LATTICE_STEP_M) + 1)))
    rows = int(min(MAX_LATTICE, max(2, math.ceil(height * py / LATTICE_STEP_M) + 1)))
    return cols, rows


def _bilinear(field, fx, fy):
    """Interpolation bilineaire de `field` (lignes x colonnes) aux positions fractionnaires (fy, fx), tableaux 2D broadcastables."""
    rows, cols = field.shape
    x0 = np.minimum(np.floor(fx).astype(int), cols - 2)
    y0 = np.minimum(np.floor(fy).astype(int), rows - 2)
    tx, ty = fx - x0, fy - y0
    return (field[y0, x0] * (1 - tx) * (1 - ty) + field[y0, x0 + 1] * tx * (1 - ty)
            + field[y0 + 1, x0] * (1 - tx) * ty + field[y0 + 1, x0 + 1] * tx * ty)


def correct_dem(src, dst, grid, lonlat, reference=None, progress=None, samples=5):
    """Ecrit dans `dst` le MNT `src` (hauteurs ellipsoidales) passe en altitudes : `H = h - N`.

    `lonlat(xs, ys)` : (longitudes, latitudes) en degres de points du MNT (tableaux, dans le repere du MNT). N est calcule sur un
    semis de points (un tous les ~100 m) puis interpole bilineairement a chaque pixel.
    * `grid` : notre lecture de la grille. `reference(lons, lats)` : N donne par PROJ (autorite) ; s'il est fourni, c'est lui qui
      s'applique et on exige qu'il concorde avec notre calcul a 1 cm pres sur tout le semis (sinon ValueError, rien n'est ecrit).
    * Controle : `samples` pixels sont recalcules directement (sans semis) ; plus de 5 mm d'ecart annule tout (ValueError).
    Renvoie {"min": N min, "max": N max, "check_mm": ecart du controle, "proj_mm": ecart PROJ / nous ou None}. Leve ValueError, et
    laisse `dst` absent, si la grille ne couvre pas tout le MNT."""
    tiff = tiffio.Tiff(src)
    if _raster_type(tiff) == RASTER_PIXEL_IS_POINT:
        raise ValueError("MNT « PixelIsPoint » non pris en charge")
    scale, tie = tiff.ints(tiffio.T_SCALE), tiff.ints(tiffio.T_TIEPOINT)
    if not scale or not tie:
        raise ValueError("MNT sans georeferencement")
    px, py = float(scale[0]), float(scale[1])
    x0, y0 = tie[3] - tie[0] * px, tie[4] + tie[1] * py                 # coin haut-gauche
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
            raise ValueError("la grille ne couvre pas tout le MNT")
        if np.all(np.isfinite(n_ours)):
            proj_mm = float(np.abs(n_ref - n_ours).max()) * 1000.0
            if proj_mm > 10.0:
                raise ValueError("PROJ et AeroTools divergent de %.1f mm sur la grille" % proj_mm)
        n_field = n_ref
    if not np.all(np.isfinite(n_field)):
        raise ValueError("la grille ne couvre pas tout le MNT")

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
            raise ValueError("controle de la correction : %.1f mm d'ecart" % (state["worst"] * 1000.0))
        os.replace(tmp, dst)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return {"min": float(n_field.min()), "max": float(n_field.max()), "check_mm": state["worst"] * 1000.0, "proj_mm": proj_mm}
