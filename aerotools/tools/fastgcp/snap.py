"""Recalage du clic sur le centre de la cible.

Apres un clic, on regarde une petite zone de la photo (une trentaine de pixels
autour du clic) et on cherche le croisement d'un damier 2x2 : quatre carres
dont les diagonales opposees ont la meme couleur. Si ce croisement est a moins
de `max_shift` pixels du clic, le point est pose dessus. Sinon, ou si rien de
net ne ressort (croix, marque a la craie, photo floue), le clic reste tel quel.

Meme methode que la chaine PhotoPN : on projette les deux couleurs de la cible
sur l'axe qui les separe (jaune/noir, blanc/noir...), puis on prend le meilleur
croisement, pondere par une gaussienne centree sur le clic pour ne jamais sauter
sur un autre damier voisin. Python pur, sans numpy : la zone est minuscule.

Desactive par defaut (reglage fastgcp.recalage_max_px, voir session.py) : sur
CITADELLE_Pompeani, les pointages manuels etaient plus justes que le recalage.

Convention de Metashape : l'origine est le coin haut-gauche du premier pixel,
donc un croisement entre deux pixels a des coordonnees entieres.
"""

import math

from ... import journal, msapi

MAX_SHIFT_PX = 3.5
SEARCH_RADIUS = 4
HALVES = (6, 8, 10)
MIN_SCORE = 10.0
SIGMA = 3.5
# Marge de lecture autour du clic : rayon de recherche + plus grand carre + 1
# pixel pour l'interpolation sous-pixel.
MARGIN = SEARCH_RADIUS + max(HALVES) + 2


def _principal_axis(rows):
    """Axe de couleur le plus contraste de la zone (vecteur unitaire de 3)."""
    count = 0
    sums = [0.0, 0.0, 0.0]
    for row in rows:
        for rgb in row:
            for k in range(3):
                sums[k] += rgb[k]
            count += 1
    if count == 0:
        return None
    mean = [s / count for s in sums]
    cov = [[0.0] * 3 for _ in range(3)]
    for row in rows:
        for rgb in row:
            d = (rgb[0] - mean[0], rgb[1] - mean[1], rgb[2] - mean[2])
            for i in range(3):
                for j in range(i, 3):
                    cov[i][j] += d[i] * d[j]
    for i in range(3):
        for j in range(i):
            cov[i][j] = cov[j][i]
    vec = [1.0, 1.0, 1.0]
    for _ in range(30):
        nxt = [sum(cov[i][j] * vec[j] for j in range(3)) for i in range(3)]
        norm = math.sqrt(sum(v * v for v in nxt))
        if norm < 1e-6:
            return None
        vec = [v / norm for v in nxt]
    return vec


def signal(rows):
    """Zone convertie en une seule valeur par pixel (axe des deux couleurs)."""
    axis = _principal_axis(rows)
    if axis is None:
        axis = (0.299, 0.587, 0.114)
    return [[rgb[0] * axis[0] + rgb[1] * axis[1] + rgb[2] * axis[2] for rgb in row]
            for row in rows]


def _integral(grid):
    height = len(grid)
    width = len(grid[0]) if height else 0
    ii = [[0.0] * (width + 1) for _ in range(height + 1)]
    for y in range(height):
        run = 0.0
        row = grid[y]
        above = ii[y]
        out = ii[y + 1]
        for x in range(width):
            run += row[x]
            out[x + 1] = above[x + 1] + run
    return ii


def _box(ii, x0, y0, x1, y1):
    return ii[y1][x1] - ii[y0][x1] - ii[y1][x0] + ii[y0][x0]


def _saddle(ii, width, height, x, y, half):
    """Contraste damier 2x2 autour du croisement (x, y), ou None si hors zone."""
    if x < half or y < half or x + half > width or y + half > height:
        return None
    area = float(half * half)
    nw = _box(ii, x - half, y - half, x, y) / area
    ne = _box(ii, x, y - half, x + half, y) / area
    sw = _box(ii, x - half, y, x, y + half) / area
    se = _box(ii, x, y, x + half, y + half) / area
    contrast = abs(0.5 * (nw + se) - 0.5 * (ne + sw))
    cohesion = 0.35 * (abs(nw - se) + abs(ne - sw))
    return contrast - cohesion


def _quad(minus, centre, plus):
    d = minus - 2.0 * centre + plus
    if abs(d) < 1e-6:
        return 0.0
    return max(-0.5, min(0.5, 0.5 * (minus - plus) / d))


def find_crossing(rows, cx, cy, radius=SEARCH_RADIUS, halves=HALVES,
                  min_score=MIN_SCORE, sigma=SIGMA):
    """Meilleur croisement de damier pres de (cx, cy), dans le repere de `rows`.

    `rows` : liste de lignes de pixels (r, g, b). Renvoie (x, y, score) ou None.
    """
    if not rows or not rows[0]:
        return None
    grid = signal(rows)
    height, width = len(grid), len(grid[0])
    ii = _integral(grid)
    two_s2 = 2.0 * sigma * sigma
    best = None
    best_score = min_score
    best_half = halves[0]
    ix, iy = int(round(cx)), int(round(cy))
    for half in halves:
        for y in range(iy - radius, iy + radius + 1):
            for x in range(ix - radius, ix + radius + 1):
                raw = _saddle(ii, width, height, x, y, half)
                if raw is None:
                    continue
                score = raw * math.exp(-((x - cx) ** 2 + (y - cy) ** 2) / two_s2)
                if score > best_score:
                    best_score, best, best_half = score, (x, y), half
    if best is None:
        return None
    bx, by = best
    around = [_saddle(ii, width, height, bx + dx, by + dy, best_half)
              for dx, dy in ((0, 0), (-1, 0), (1, 0), (0, -1), (0, 1))]
    if any(v is None for v in around):
        return float(bx), float(by), best_score
    centre, xm, xp, ym, yp = around
    return bx + _quad(xm, centre, xp), by + _quad(ym, centre, yp), best_score


def refine_in_rows(rows, origin_x, origin_y, x, y, max_shift=MAX_SHIFT_PX):
    """Recale (x, y) sur le croisement trouve dans `rows` (zone dont le pixel
    haut-gauche est (origin_x, origin_y) dans l'image).

    Renvoie (x, y, decalage_en_px) si le croisement est assez pres, sinon None.
    """
    hit = find_crossing(rows, x - origin_x, y - origin_y)
    if hit is None:
        return None
    nx, ny = hit[0] + origin_x, hit[1] + origin_y
    shift = math.hypot(nx - x, ny - y)
    if shift > max_shift:
        return None
    return nx, ny, shift


def read_rows(image_path, x, y, expected_size):
    """Lit la zone autour de (x, y) dans le fichier image, via Qt.

    Renvoie (rows, origin_x, origin_y) ou (None, raison). On ne lit que la zone
    quand le format le permet (JPEG), pas la photo entiere.
    """
    from ...qt import QtCore, QtGui

    reader = QtGui.QImageReader(image_path)
    try:
        reader.setAutoTransform(False)
    except Exception:
        pass
    size = reader.size()
    width, height = size.width(), size.height()
    if width <= 0 or height <= 0:
        return None, "fichier image illisible"
    if (width, height) != tuple(expected_size):
        return None, "taille du fichier {}x{} differente du capteur {}x{}".format(
            width, height, expected_size[0], expected_size[1])
    x0 = max(0, int(round(x)) - MARGIN)
    y0 = max(0, int(round(y)) - MARGIN)
    x1 = min(width, int(round(x)) + MARGIN + 1)
    y1 = min(height, int(round(y)) + MARGIN + 1)
    if x1 - x0 < 2 * max(HALVES) or y1 - y0 < 2 * max(HALVES):
        return None, "trop pres du bord de l'image"
    reader.setClipRect(QtCore.QRect(x0, y0, x1 - x0, y1 - y0))
    image = reader.read()
    if image.isNull():
        return None, "lecture de la zone impossible"
    image = image.convertToFormat(QtGui.QImage.Format_RGB32)
    rows = []
    for j in range(image.height()):
        row = []
        for i in range(image.width()):
            pixel = image.pixel(i, j)
            row.append(((pixel >> 16) & 255, (pixel >> 8) & 255, pixel & 255))
        rows.append(row)
    return rows, x0, y0


def snap_click(camera, x, y, max_shift=MAX_SHIFT_PX):
    """Recale le clic (x, y) fait sur `camera`. Ne leve jamais.

    Renvoie (x, y, decalage) si le point a ete deplace, ou (None, raison).
    """
    try:
        if max_shift <= 0:
            return None, "recalage coupe"
        path = msapi.camera_image_path(camera)
        if not path:
            return None, "chemin de l'image inconnu"
        size = msapi.camera_size(camera)
        rows = read_rows(path, x, y, size)
        if rows[0] is None:
            return None, rows[1]
        zone, origin_x, origin_y = rows
        result = refine_in_rows(zone, origin_x, origin_y, x, y, max_shift)
        if result is None:
            return None, "pas de croisement net a moins de {:g} px".format(max_shift)
        return result
    except Exception as exc:
        try:
            journal.write("recalage : {!r}".format(exc))
        except Exception:
            pass
        return None, "erreur ({!r})".format(exc)
