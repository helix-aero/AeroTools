"""Fast Export : systemes d'altitude calcules par AeroTools (grilles de geoide lues et appliquees ici, pas par Metashape).

Pourquoi : Metashape 2.2.1 choisit sa grille par le nom du datum et non par le fichier (« RAC23 » et « RAC09 » donnent la
meme valeur, 23 cm d'ecart avec la vraie grille RAC23 en Corse), ignore RAC23 avec un repere RGF93 v2b, et refuse EGM96 avec un
repere francais. Ici : Metashape exporte le MNT en hauteurs ellipsoidales (ce qu'il fait bien), puis on retranche l'ondulation
du geoide lue dans notre dossier `Geoid` (`H = h - N`).

Grilles : GeoTIFF « GTG » de PROJ-data (Float32, une bande `geoid_undulation`, Deflate + predicteur flottant), noeuds regulierement
espaces en longitude / latitude, interpolation bilineaire comme PROJ. Valeurs de reference : voir tests/test_fastexport_vertical.py.
"""

import hashlib
import math
import os

import numpy as np

from . import crs, tiffio

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
# Quelle grille pour quel repere
# --------------------------------------------------------------------------------------------------
# RGF93 v1 : EPSG 2154 (Lambert-93), 3942-3950 (CC42-CC50), 4171. RGF93 v2b : 9794, 9842-9850, 9782.
# Une grille est faite pour un repere : RAF09 / RAF18b / RAC09 partent de RGF93 v1 (EPSG 4965), RAF20 / RAC23 de RGF93 v2b
# (EPSG 9781). Le choix d'une altitude n'est propose qu'avec le repere qui lui correspond.
V1_EPSG = frozenset([2154, 4171] + list(range(3942, 3951)))
V2B_EPSG = frozenset([9794, 9782] + list(range(9842, 9851)))
GRID_FRAME = {4965: "v1", 9781: "v2b"}


def horizontal_frame(epsg):
    """"v1", "v2b", ou None (UTM, WGS 84... : ni l'un ni l'autre)."""
    epsg = int(epsg)
    return "v1" if epsg in V1_EPSG else "v2b" if epsg in V2B_EPSG else None


def compatible_verticals(epsg, lon=None, lat=None):
    """Systemes d'altitude proposables avec ce repere horizontal, a la position (lon, lat) : liste de fiches du catalogue.

    Les grilles francaises du bon repere qui couvrent le lieu (la plus recente d'abord pour v2b ; pour v1, RAC09 / RAF09 : c'est
    ce que contiennent les chantiers en v1), puis EGM96 (mondial, valable partout), puis l'ellipsoide. Sans repere connu
    (UTM, WGS 84), on propose les grilles recentes (v2b)."""
    frame = horizontal_frame(epsg) or "v2b"
    out = []
    for vid in crs.EXPORT_VERTICALS:
        spec = crs.vertical_by_id(vid)
        if vid in ("ellipsoid", "EGM96"):
            continue
        if GRID_FRAME.get(spec.get("horz_epsg")) == frame and (lon is None or lat is None or crs.covers(spec, lon, lat)):
            out.append(spec)
    out.sort(key=lambda spec: spec["id"])
    out.append(crs.vertical_by_id("EGM96"))
    out.append(crs.vertical_by_id("ellipsoid"))
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


def correct_dem(src, dst, grid, to_lonlat, progress=None, samples=5):
    """Ecrit dans `dst` le MNT `src` (hauteurs ellipsoidales) passe en altitudes : `H = h - N`.

    `to_lonlat(x, y)` : (longitude, latitude) en degres d'un point du MNT (dans le repere du MNT). N est calcule sur un semis de
    points (un tous les ~100 m) puis interpole bilineairement a chaque pixel. Controle : `samples` pixels sont recalcules
    directement (sans semis) ; un ecart de plus de 5 mm annule tout (ValueError). Renvoie {"min": N min, "max": N max,
    "check_mm": ecart maximal du controle en mm}. Leve ValueError, et laisse `dst` absent, si la grille ne couvre pas tout le MNT."""
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
    n_field = np.empty((rows, cols), dtype=np.float64)
    for r in range(rows):
        for c in range(cols):
            lon, lat = to_lonlat(float(lx[c]), float(ly[r]))
            n_field[r, c] = float(grid.undulation(lon, lat))
    if not np.all(np.isfinite(n_field)):
        raise ValueError("la grille ne couvre pas tout le MNT")

    rng = np.random.RandomState(7)
    picks = {}
    for _ in range(samples):
        row, col = int(rng.randint(0, height)), int(rng.randint(0, width))
        picks[(row, col)] = None
    state = {"done": 0, "worst": 0.0}
    total = max(1, len(tiff.offsets))

    def change(top, left, block):
        h = min(block.shape[0], height - top)
        w = min(block.shape[1], width - left)
        u = (left + np.arange(w) + 0.5) * px / (width * px) * (cols - 1)
        v = (top + np.arange(h) + 0.5) * py / (height * py) * (rows - 1)
        n = _bilinear(n_field, u[np.newaxis, :], v[:, np.newaxis])
        part = block[:h, :w].astype(np.float64)
        valid = np.isfinite(part)
        if nodata is not None:
            valid &= part != nodata
        out = block.copy()
        out[:h, :w] = np.where(valid, part - n, part).astype(np.float32)
        for (row, col) in picks:
            if top <= row < top + h and left <= col < left + w:
                lon, lat = to_lonlat(x0 + (col + 0.5) * px, y0 - (row + 0.5) * py)
                direct = float(grid.undulation(lon, lat))
                state["worst"] = max(state["worst"], abs(direct - float(n[row - top, col - left])))
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
    return {"min": float(n_field.min()), "max": float(n_field.max()), "check_mm": state["worst"] * 1000.0}
