"""Fast Export : decoupage en dalles (fonctions pures, sans Qt, sans Metashape, sans rasterio).

Toute l'arithmetique des coordonnees se fait en fractions exactes (``fractions.Fraction`` construites
depuis le texte decimal : 0.03 vaut exactement 3/100), jamais en flottants : une dalle de 300 m a 3 cm
fait EXACTEMENT 10 000 pixels et son coin bas-gauche est un multiple exact de 3 cm. Les flottants
n'apparaissent qu'en sortie (``float(...)``), pour le fichier ou l'API Metashape.

Vocabulaire
    cellule   case de la grille (xmin, ymin, xmax, ymax), toujours entiere, alignee sur le pixel
    dalle     ce qu'on exporte : la cellule, rognee a l'emprise des donnees (edge='crop') ou entiere
              (edge='full'), agrandie du recouvrement
    ligne R   1 = la plus au nord ; colonne C : 1 = la plus a l'ouest (convention image)

Deux logiques de nom (voir tile_name) : 'rc' (R02C05) et 'xy' (E2179_N1208, coin bas-gauche en km,
sinon en metres).

Python 3.9, aucune dependance.
"""

import math
from collections import namedtuple
from decimal import Decimal
from fractions import Fraction

# Limites DURES documentees (par cote de dalle, en pixels, ou en octets) et recommandations HELIX.
# Les recommandations ne sont pas des limites : ce sont des seuils d'avertissement a valider sur le terrain.
FORMAT_LIMITS = {
    "jpeg": {"max_side_px": 65500, "source": "libjpeg : JPEG_MAX_DIMENSION = 65500"},
    "tiff": {"max_file_bytes": 4 * 1024 ** 3, "source": "TIFF classique : offsets 32 bits (4 Gio) ; au-dela BigTIFF"},
    "png": {"max_side_px": 2 ** 31 - 1, "source": "specification PNG"},
    "jp2": {"max_side_px": 2 ** 32 - 1, "source": "JPEG 2000 : tailles 32 bits"},
    "ecw": {"recommended_max_px": 30000, "source": "recommandation (ECW : pas de limite dure trouvee ; AutoCAD charge mal au-dela)"},
}

Tile = namedtuple("Tile", "row col ix iy cell bounds width_px height_px partial overlap_px")
Tile.__doc__ = ("row/col : numerotation 1-based (R1 = nord, C1 = ouest) ; ix/iy : indice absolu de la cellule dans la grille "
                "d'origine (iy vers le nord) ; cell : (xmin,ymin,xmax,ymax) Fractions ; bounds : emprise exportee ; "
                "partial : la dalle est plus petite que la cellule (bord) ; overlap_px : recouvrement ajoute.")


# ---------------------------------------------------------------------------
# Fractions exactes
# ---------------------------------------------------------------------------

def to_fraction(value):
    """Convertit un nombre ou un texte en Fraction exacte via sa forme decimale (0.03 -> 3/100)."""
    if isinstance(value, Fraction):
        return value
    if isinstance(value, float):
        return Fraction(Decimal(repr(value)))
    return Fraction(Decimal(str(value)))


def _lcm(a, b):
    return a * b // math.gcd(a, b)


def lcm_fraction(values):
    """Plus petit commun multiple de fractions positives : lcm(3/100, 1/10) = 3/10."""
    fr = [to_fraction(v) for v in values]
    if not fr or any(f <= 0 for f in fr):
        raise ValueError("tailles de pixel invalides : %r" % (values,))
    num = 1
    den = 0
    for f in fr:
        num = _lcm(num, f.numerator)
        den = math.gcd(den, f.denominator)
    return Fraction(num, den)


def floor_to(value, step):
    """Plus grand multiple de `step` <= value (exact)."""
    value, step = to_fraction(value), to_fraction(step)
    return math.floor(value / step) * step


def ceil_to(value, step):
    value, step = to_fraction(value), to_fraction(step)
    return math.ceil(value / step) * step


def is_multiple(value, step):
    return (to_fraction(value) / to_fraction(step)).denominator == 1


# ---------------------------------------------------------------------------
# Pas de grille
# ---------------------------------------------------------------------------

def snap_tile_size(tile_m, pixel_sizes):
    """Taille de dalle (m) arrondie au multiple le plus proche du ppcm des tailles de pixel.

    `pixel_sizes` : toutes les tailles de pixel qui partageront la grille (ortho ET MNT), pour que
    la meme dalle tombe pile sur les pixels de chacun. Renvoie (Fraction, exact) ou `exact` dit si la
    taille demandee etait deja valable. Ex. : (300, [0.03, 0.10]) -> (300, True) ; (250, [0.03]) -> (249.99, False).
    """
    unit = lcm_fraction(pixel_sizes)
    wanted = to_fraction(tile_m)
    if wanted <= 0:
        raise ValueError("taille de dalle invalide : %r" % (tile_m,))
    n = max(1, int((wanted / unit) + Fraction(1, 2)))     # arrondi au plus proche (demi vers le haut)
    return n * unit, (n * unit == wanted)


def nice_tile_sizes(pixel_sizes, around_m, count=5):
    """Quelques tailles de dalle 'rondes en metres' compatibles avec les pixels, autour de `around_m`.

    Une taille ronde = nombre entier de metres ET multiple du ppcm des pixels. Sert a remplir la liste
    deroulante de l'interface (ex. a 3 cm : 150, 300, 600 m ; a 5 cm : 100, 200, 250 m...).
    """
    unit = lcm_fraction(pixel_sizes)
    found = []
    lo, hi = max(1, int(around_m / 4)), int(around_m * 4) + 1
    for meters in range(lo, hi + 1):
        if is_multiple(meters, unit):
            found.append(meters)
    found.sort(key=lambda m: abs(m - around_m))
    return sorted(found[:count])


def snap_origin(x, y, round_to_m, pixel_sizes):
    """Coin bas-gauche de la grille, arrondi AU-DESSOUS au plus petit pas qui est a la fois un multiple de
    `round_to_m` ET un multiple des pixels (ppcm). Ex. : 100 m et 3 cm -> pas de 300 m (100 m seul ne tombe
    pas sur la grille de 3 cm : 100 / 0.03 n'est pas entier). Renvoie (ox, oy) Fractions."""
    unit = lcm_fraction(pixel_sizes)
    step = lcm_fraction([to_fraction(round_to_m), unit])
    return floor_to(x, step), floor_to(y, step)


# ---------------------------------------------------------------------------
# Grille
# ---------------------------------------------------------------------------

def tile_grid(bounds, tile_w_m, tile_h_m=None, pixel_sizes=(0.03,), origin=None, round_to_m=None,
              overlap_m=0, edge="crop", footprint=None, order="rows_north_first", max_tiles=100000):
    """Calcule les dalles couvrant `bounds` = (xmin, ymin, xmax, ymax) (coordonnees du SCR de sortie, m).

    pixel_sizes : tailles de pixel partageant la grille (ortho + MNT) ; la premiere sert aux comptes `*_px`.
    origin      : (x, y) coin bas-gauche de la grille ; sinon arrondi a `round_to_m` (defaut : la taille de dalle).
    overlap_m   : recouvrement ajoute de chaque cote, arrondi au pixel superieur.
    edge        : 'crop' (dalle de bord rognee a l'emprise, alignee sur le pixel) ou 'full' (cellule entiere).
    footprint   : fonction (xmin, ymin, xmax, ymax) -> bool ; False = dalle vide, ignoree (ex. emprise des donnees).
    order       : 'rows_north_first' (R1C1 en haut a gauche, par lignes) ou 'cols_west_first'.
    Renvoie une liste de `Tile`, ordre garanti. Leve ValueError si la taille de dalle n'est pas un multiple exact
    des pixels (utiliser snap_tile_size) ou s'il y aurait plus de `max_tiles` dalles.
    """
    if edge not in ("crop", "full"):
        raise ValueError("edge : 'crop' ou 'full'")
    px_list = [to_fraction(p) for p in pixel_sizes]
    unit = lcm_fraction(px_list)
    pix = px_list[0]
    tw = to_fraction(tile_w_m)
    th = to_fraction(tile_h_m if tile_h_m is not None else tile_w_m)
    for label, size in (("largeur", tw), ("hauteur", th)):
        if size <= 0 or not is_multiple(size, unit):
            raise ValueError("%s de dalle %s m : pas un multiple exact de %s m (ppcm des pixels). Voir snap_tile_size."
                             % (label, float(size), float(unit)))
    xmin, ymin, xmax, ymax = [to_fraction(v) for v in bounds]
    if xmax <= xmin or ymax <= ymin:
        raise ValueError("emprise vide : %r" % (bounds,))
    if origin is None:
        step = to_fraction(round_to_m) if round_to_m else min(tw, th)
        ox, oy = snap_origin(xmin, ymin, step, px_list)
    else:
        ox, oy = to_fraction(origin[0]), to_fraction(origin[1])
        if not (is_multiple(ox, unit) and is_multiple(oy, unit)):
            raise ValueError("origine (%s, %s) pas alignee sur les pixels (multiple de %s m)" % (float(ox), float(oy), float(unit)))
    if ox > xmin or oy > ymin:
        raise ValueError("l'origine de la grille doit etre en bas a gauche de l'emprise des donnees")
    ov = to_fraction(overlap_m)
    ov = ceil_to(ov, pix) if ov > 0 else Fraction(0)

    n_cols = math.ceil((xmax - ox) / tw)
    n_rows = math.ceil((ymax - oy) / th)
    if n_cols * n_rows > max_tiles:
        raise ValueError("%d dalles : trop (max %d), augmenter la taille de dalle" % (n_cols * n_rows, max_tiles))

    cells = []
    for iy in range(n_rows):
        for ix in range(n_cols):
            cell = (ox + ix * tw, oy + iy * th, ox + (ix + 1) * tw, oy + (iy + 1) * th)
            if edge == "crop":
                b = (max(cell[0], floor_to(xmin, pix)), max(cell[1], floor_to(ymin, pix)),
                     min(cell[2], ceil_to(xmax, pix)), min(cell[3], ceil_to(ymax, pix)))
            else:
                b = cell
            if b[2] <= b[0] or b[3] <= b[1]:
                continue
            if footprint is not None and not footprint(*[float(v) for v in b]):
                continue
            partial = (b != cell)
            if ov > 0:
                b = (max(b[0] - ov, floor_to(xmin, pix)) if edge == "crop" else b[0] - ov,
                     max(b[1] - ov, floor_to(ymin, pix)) if edge == "crop" else b[1] - ov,
                     min(b[2] + ov, ceil_to(xmax, pix)) if edge == "crop" else b[2] + ov,
                     min(b[3] + ov, ceil_to(ymax, pix)) if edge == "crop" else b[3] + ov)
            w_px = int((b[2] - b[0]) / pix)
            h_px = int((b[3] - b[1]) / pix)
            cells.append((ix, iy, cell, b, w_px, h_px, partial, int(ov / pix)))

    tiles = []
    for ix, iy, cell, b, w_px, h_px, partial, ov_px in cells:
        tiles.append(Tile(row=n_rows - iy, col=ix + 1, ix=ix, iy=iy, cell=cell, bounds=b, width_px=w_px,
                          height_px=h_px, partial=partial, overlap_px=ov_px))
    if order == "rows_north_first":
        tiles.sort(key=lambda t: (t.row, t.col))
    elif order == "cols_west_first":
        tiles.sort(key=lambda t: (t.col, t.row))
    else:
        raise ValueError("order : 'rows_north_first' ou 'cols_west_first'")
    return tiles


def grid_origin(tiles):
    """Coin bas-gauche de la grille d'apres les dalles (Fractions) : cellule ix=0, iy=0 deduite."""
    t = tiles[0]
    tw = t.cell[2] - t.cell[0]
    th = t.cell[3] - t.cell[1]
    return t.cell[0] - t.ix * tw, t.cell[1] - t.iy * th


# ---------------------------------------------------------------------------
# Noms de dalles
# ---------------------------------------------------------------------------

def _best_unit_m(tile, ox, oy):
    """Plus grande unite parmi 1000, 100, 10, 1 m qui divise la cellule et l'origine (sinon 1 m)."""
    tw = tile.cell[2] - tile.cell[0]
    th = tile.cell[3] - tile.cell[1]
    for unit in (1000, 100, 10, 1):
        if all(is_multiple(v, unit) for v in (tw, th, ox, oy)):
            return unit
    return 1


def tile_name(tile, scheme="rc", n_rows=None, n_cols=None, unit_m=None, origin=None):
    """Jeton de nom d'une dalle.

    'rc' : R02C05 (largeur = 2 chiffres, 3 si plus de 99 lignes / colonnes). Court, mais depend de l'emprise :
           si le chantier grandit a l'ouest, C01 change de place.
    'xy' : E2179_N1208 = coin bas-gauche de la CELLULE (pas de la dalle rognee) en km si la grille est au km
           (unite 1000), sinon en metres entiers (E2179500_N1208000) ; stable d'une date a l'autre tant que
           la grille ne change pas, donc a preferer pour un suivi mensuel du meme site. Si la grille n'est pas en
           metres entiers (pixel exotique), l'unite tombe a 1 m et le nom arrondit : verifier les collisions
           (tile_names verifie).
    """
    if scheme == "rc":
        width = 3 if max(n_rows or tile.row, n_cols or tile.col) > 99 else 2
        return "R%0*dC%0*d" % (width, tile.row, width, tile.col)
    if scheme == "xy":
        ox, oy = origin if origin is not None else (Fraction(0), Fraction(0))
        unit = unit_m or _best_unit_m(tile, ox, oy)
        e = math.floor(tile.cell[0] / unit)
        n = math.floor(tile.cell[1] / unit)
        return "E%d_N%d" % (e, n)
    raise ValueError("scheme : 'rc' ou 'xy'")


def tile_names(tiles, scheme="rc"):
    """Noms de toutes les dalles ; leve ValueError en cas de collision. Renvoie {(row, col): jeton}."""
    if not tiles:
        return {}
    n_rows = max(t.row for t in tiles)
    n_cols = max(t.col for t in tiles)
    ox, oy = grid_origin(tiles)
    unit = _best_unit_m(tiles[0], ox, oy) if scheme == "xy" else None
    listed = [((t.row, t.col), tile_name(t, scheme, n_rows, n_cols, unit, (ox, oy))) for t in tiles]
    if len(set(name for _key, name in listed)) != len(listed):
        raise ValueError("collision de noms de dalles (schema %s) : changer de schema ou d'unite" % scheme)
    return dict(listed)


# ---------------------------------------------------------------------------
# Avertissements de taille
# ---------------------------------------------------------------------------

def tile_size_warnings(width_px, height_px, bands, bytes_per_sample, fmt):
    """Liste de textes d'avertissement (cles courtes) pour une dalle de cette taille et ce format."""
    out = []
    raw = width_px * height_px * bands * bytes_per_sample
    limits = FORMAT_LIMITS.get(fmt, {})
    side = limits.get("max_side_px")
    if side and max(width_px, height_px) > side:
        out.append(("side_limit", "%s : %d px > %d px par cote (%s)" % (fmt, max(width_px, height_px), side, limits["source"])))
    cap = limits.get("max_file_bytes")
    if cap and raw > cap:
        out.append(("bigtiff", "%s : %.1f Gio non compresses, risque de depasser %d Gio : BigTIFF ou dalles plus petites"
                    % (fmt, raw / 1024 ** 3, cap // 1024 ** 3)))
    rec = limits.get("recommended_max_px")
    if rec and max(width_px, height_px) > rec:
        out.append(("recommended", "%s : %d px > %d px recommandes par cote" % (fmt, max(width_px, height_px), rec)))
    return out


def estimate_grid(bounds, tile_m, pixel_m):
    """(colonnes, lignes, pixels par dalle) pour un apercu rapide dans l'interface."""
    xmin, ymin, xmax, ymax = [to_fraction(v) for v in bounds]
    tw = to_fraction(tile_m)
    return math.ceil((xmax - xmin) / tw), math.ceil((ymax - ymin) / tw), int(tw / to_fraction(pixel_m))
