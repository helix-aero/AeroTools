"""Fast Export : emprise reelle des donnees d'une ortho ou d'un MNT, pour que le DXF decoupe le blanc autour.

Le DXF peut porter un polygone de decoupe (clip) sur chaque image : AutoCAD n'affiche alors que l'interieur, le fichier
image n'est pas modifie. Ce module fabrique ce polygone :
  - un masque « il y a des donnees ici » a faible resolution (alpha d'un petit export de l'ortho par Metashape, ou pixels
    valides du MNT) ;
  - son contour : pour chaque ligne, du premier au dernier pixel de donnees (un polygone simple), simplifie ;
  - la conversion dans les pixels de l'image finale, au format du DXF.
Module pur (numpy) : aucune dependance a Metashape.
"""

import zlib

import numpy as np

from . import relief, tiffio

T_PLANAR = 284
DECODERS = {1: lambda payload: payload, 8: zlib.decompress, 32946: zlib.decompress, 5: tiffio.lzw_decode}   # TIFF compression codes

TARGET_PX = 800                  # le masque a environ 800 pixels sur son grand cote
TOLERANCE_PX = 1.2               # simplification du contour : 1 pixel de masque au plus


def alpha_mask(path):
    """Masque (lignes x colonnes, True = donnees) de la bande alpha d'un GeoTIFF RVBA 8 bits (bandes ou tuiles ; sans compression, Deflate ou LZW)."""
    tiff = tiffio.Tiff(path, strict=False)
    samples = tiff.ints(tiffio.T_SAMPLES, [1])[0]
    bits = tiff.ints(tiffio.T_BITS, [8])
    if (any(b != 8 for b in bits) or samples < 4 or tiff.ints(T_PLANAR, [1])[0] != 1
            or tiff.compression not in DECODERS or tiff.predictor not in (1, 2)):
        raise ValueError("alpha : RVBA 8 bits entrelace (sans compression, Deflate ou LZW) attendu")
    alpha = np.zeros((tiff.height, tiff.width), dtype=np.uint8)
    with open(path, "rb") as handle:
        for index, (offset, count) in enumerate(zip(tiff.offsets, tiff.counts)):
            handle.seek(offset)
            payload = handle.read(count)
            if tiff.tiled:
                row, col = divmod(index, tiff.across)
                top, left, rows, cols = row * tiff.block_h, col * tiff.block_w, tiff.block_h, tiff.block_w
            else:
                top, left, rows, cols = index * tiff.block_h, 0, min(tiff.block_h, tiff.height - index * tiff.block_h), tiff.width
            if top >= tiff.height or left >= tiff.width:
                continue
            try:
                data = np.frombuffer(DECODERS[tiff.compression](payload), dtype=np.uint8)
            except zlib.error:
                raise ValueError("alpha : bloc Deflate abime")
            if data.size < rows * cols * samples:
                raise ValueError("alpha : bloc trop court")
            block = data[:rows * cols * samples].reshape(rows, cols, samples)
            if tiff.predictor == 2:                                  # each sample is stored as the difference to its left neighbour
                block = np.cumsum(block, axis=1, dtype=np.uint8)
            part = block[:tiff.height - top, :tiff.width - left, 3]
            alpha[top:top + part.shape[0], left:left + part.shape[1]] = part
    return alpha > 0


def dem_mask(path, target=TARGET_PX):
    """Masque des pixels valides d'un MNT, reduit pour avoir au plus `target` pixels sur son grand cote."""
    tiff = tiffio.Tiff(path)
    nodata = relief.declared_nodata(tiff)
    step = max(1, int(round(max(tiff.width, tiff.height) / float(target))))
    mask = np.zeros((-(-tiff.height // step), -(-tiff.width // step)), dtype=bool)
    for top, left, block in tiff.blocks():
        good = ~relief._nodata_mask(block, nodata)
        rows, cols = np.nonzero(good)
        np.logical_or.at(mask, ((top + rows) // step, (left + cols) // step), True)
    return mask, step


def outline(mask):
    """Contour du masque : [(colonne, ligne), ...] en bords de pixels (la colonne 3 va de 3 a 4), ou [] sans donnee.

    Pour chaque ligne, du bord gauche du premier pixel de donnees au bord droit du dernier. Un trou ou une encoche horizontale
    est donc comblee (le blanc qui reste est a l'interieur de l'emprise)."""
    rows = np.nonzero(mask.any(axis=1))[0]
    if rows.size == 0:
        return []
    first = mask.argmax(axis=1)
    last = mask.shape[1] - mask[:, ::-1].argmax(axis=1)
    # bord gauche vers le bas, puis bord droit vers le haut
    left_chain = [(float(first[r]), float(r)) for r in rows] + [(float(first[rows[-1]]), float(rows[-1] + 1))]
    right_chain = [(float(last[rows[-1]]), float(rows[-1] + 1))] + [(float(last[r]), float(r)) for r in rows[::-1]]
    right_chain.append((float(last[rows[0]]), float(rows[0])))
    return _simplify(left_chain, TOLERANCE_PX) + _simplify(right_chain, TOLERANCE_PX)


def _simplify(points, tolerance):
    """Douglas-Peucker (iteratif) : garde les points qui s'ecartent de plus de `tolerance` de la droite voisine."""
    if len(points) < 3:
        return list(points)
    pts = np.array(points, dtype=float)
    keep = np.zeros(len(pts), dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        p, q = pts[a], pts[b]
        chord = q - p
        length = float(np.hypot(*chord))
        inner = pts[a + 1:b]
        if length == 0.0:
            dist = np.hypot(inner[:, 0] - p[0], inner[:, 1] - p[1])
        else:
            dist = np.abs(chord[0] * (inner[:, 1] - p[1]) - chord[1] * (inner[:, 0] - p[0])) / length
        k = int(dist.argmax())
        if dist[k] > tolerance:
            keep[a + 1 + k] = True
            stack.extend(((a, a + 1 + k), (a + 1 + k, b)))
    return [tuple(point) for point in pts[keep]]


def _grow(mask):
    """Masque agrandi d'un pixel dans toutes les directions : le polygone ne rogne jamais une donnee a la limite."""
    padded = np.pad(mask, 1)
    out = np.zeros_like(mask)
    for dr in (0, 1, 2):
        for dc in (0, 1, 2):
            out |= padded[dr:dr + mask.shape[0], dc:dc + mask.shape[1]]
    return out


def clip_polygon(mask, mask_extent, image_info):
    """Polygone de decoupe d'une image, dans les coordonnees de son entite IMAGE du DXF.

    `mask_extent` : (gauche, bas, droite, haut) du masque en unites du dessin. `image_info` : {"width", "height", "origin",
    "pixel"} de l'image finale (geotiff.read_geotiff). Les sommets sont en pixels de l'image, depuis son coin bas-gauche, le
    centre du premier pixel valant (0, 0) : la convention de l'entite IMAGE. Le polygone est ferme (dernier sommet = premier).
    Renvoie [] s'il n'y a pas de donnees ou si le polygone couvre toute l'image (rien a decouper)."""
    path = outline(_grow(mask))
    if len(path) < 3:
        return []
    left, bottom, right, top = mask_extent
    cell_x = (right - left) / float(mask.shape[1])
    cell_y = (top - bottom) / float(mask.shape[0])
    il, it = image_info["origin"]
    px, py = image_info["pixel"]
    ib = it - image_info["height"] * py
    width, height = image_info["width"], image_info["height"]
    out = []
    for col, row in path:
        x = left + col * cell_x
        y = top - row * cell_y
        out.append((min(max((x - il) / px, 0.0), float(width)) - 0.5, min(max((y - ib) / py, 0.0), float(height)) - 0.5))
    area_polygon = abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(out, out[1:] + out[:1]))) / 2.0
    if area_polygon >= 0.995 * width * height:
        return []
    return out + [out[0]]
