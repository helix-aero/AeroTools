"""Fast Export : image du MNT en relief colore, comme l'onglet Ortho de Metashape (palette + ombrage), sans GDAL.

Un module pur (numpy, `tiffio`) : la meme fonction peut servir a l'image du DXF et a un apercu. Le MNT est lu par blocs de
lignes (avec une ligne de recouvrement pour l'ombrage), l'image RVB est ecrite en Deflate avec le georeferencement du MNT.
"""

import struct
import zlib

import numpy as np

from . import tiffio

# Palette par defaut du MNT de Metashape, relevee sur la legende de l'onglet Ortho (CITADELLE, 1,53 m a 14,94 m, echelle
# lineaire entre le minimum et le maximum) : (pourcentage de l'amplitude, R, V, B). Bas = bleu, haut = rouge.
METASHAPE_RAMP = ((0, 47, 55, 218), (10, 48, 118, 219), (20, 52, 191, 221), (30, 50, 222, 184), (40, 51, 222, 120),
                  (50, 51, 222, 50), (60, 120, 223, 49), (70, 181, 222, 48), (80, 220, 194, 49), (90, 222, 119, 52),
                  (100, 225, 53, 56))
NODATA_LIMIT = -9000.0           # un MNT plus bas que ca est « pas de donnee » (Metashape ecrit -32767)
WHITE = 255
AZIMUTH, ALTITUDE = 315.0, 45.0  # lumiere du nord-ouest, a 45 degres (comme gdaldem hillshade)
SHADE_STRENGTH = 0.8             # 0 = pas d'ombrage ; 1 = ombrage complet
CHUNK = 1024                     # lignes traitees d'un coup
GEO_TAGS = (tiffio.T_SCALE, tiffio.T_TIEPOINT, tiffio.T_GEOKEYS, 34736, 34737)


def lookup_table(ramp=METASHAPE_RAMP):
    """Table de 256 couleurs (256 x 3, uint8) interpolee lineairement entre les points de la palette."""
    stops = np.array([item[0] for item in ramp], dtype=np.float64) / 100.0 * 255.0
    colors = np.array([item[1:] for item in ramp], dtype=np.float64)
    index = np.arange(256, dtype=np.float64)
    return np.stack([np.interp(index, stops, colors[:, channel]) for channel in range(3)], axis=1).round().astype(np.uint8)


def hillshade(z, pixel_x, pixel_y, strength=SHADE_STRENGTH, azimuth=AZIMUTH, altitude=ALTITUDE):
    """Facteur d'eclairement (1 = terrain plat) de la partie interieure de `z` (formule de Horn, comme gdaldem).

    `z` : tableau float (lignes x colonnes) ; le resultat a 2 lignes et 2 colonnes de moins."""
    a, b, c = z[:-2, :-2], z[:-2, 1:-1], z[:-2, 2:]
    d, f = z[1:-1, :-2], z[1:-1, 2:]
    g, h, i = z[2:, :-2], z[2:, 1:-1], z[2:, 2:]
    west_east = ((a + 2 * d + g) - (c + 2 * f + i)) / (8.0 * pixel_x)
    south_north = ((g + 2 * h + i) - (a + 2 * b + c)) / (8.0 * pixel_y)
    steep = west_east ** 2 + south_north ** 2
    aspect = np.arctan2(south_north, west_east)
    lift = np.radians(altitude)
    light = (np.sin(lift) - np.cos(lift) * np.sqrt(steep) * np.cos(aspect - np.radians(azimuth))) / np.sqrt(1.0 + steep)
    flat = np.sin(lift)
    return 1.0 + strength * (light / flat - 1.0)


def _nodata_mask(z, nodata=None):
    """Pixels sans donnee : non finis, tres bas, ou egaux a la valeur « pas de donnee » declaree dans le fichier."""
    bad = ~np.isfinite(z) | (z < NODATA_LIMIT)
    return bad if nodata is None else bad | (z == nodata)


def declared_nodata(tiff):
    """Valeur « pas de donnee » ecrite dans le TIFF (balise GDAL), ou None."""
    try:
        return float(tiff.text(tiffio.T_GDAL_NODATA).strip())
    except ValueError:
        return None


def value_range(path):
    """(minimum, maximum) des hauteurs valides du MNT, ou None s'il n'y en a aucune."""
    low, high = None, None
    tiff = tiffio.Tiff(path)
    nodata = declared_nodata(tiff)
    for _top, _left, block in tiff.blocks():
        good = block[~_nodata_mask(block, nodata)]
        if good.size:
            low = float(good.min()) if low is None else min(low, float(good.min()))
            high = float(good.max()) if high is None else max(high, float(good.max()))
    return None if low is None else (low, high)


def colorize(z, low, high, table, shade=None, nodata=None):
    """Image RVB (lignes x colonnes x 3, uint8) : couleur selon la hauteur, ombrage multiplie, blanc sans donnee."""
    span = max(high - low, 1e-9)
    index = np.clip(np.rint((z - low) / span * 255.0), 0, 255).astype(np.int64)
    missing = _nodata_mask(z, nodata)
    rgb = table[np.where(missing, 0, index)].astype(np.float32)
    if shade is not None:
        rgb *= shade[:, :, None].astype(np.float32)
    out = np.clip(np.rint(rgb), 0, 255).astype(np.uint8)
    out[missing] = WHITE
    return out


def render(dem_path, out_path, ramp=METASHAPE_RAMP, strength=SHADE_STRENGTH, tick=None, level=6):
    """MNT GeoTIFF float32 -> GeoTIFF RVB en relief colore. Renvoie (ok, message)."""
    try:
        tiff = tiffio.Tiff(dem_path)
        span = value_range(dem_path)
        if span is None:
            return False, "MNT sans donnee"
        low, high = span
        scale = tiff.ints(tiffio.T_SCALE)
        pixel_x, pixel_y = (abs(scale[0]), abs(scale[1])) if scale else (1.0, 1.0)
        geographic = pixel_x < 0.01          # degres : pas de pente en metres, donc pas d'ombrage
        z = tiff.read_all()
        nodata = declared_nodata(tiff)
        table = lookup_table(ramp)
        compressed = []
        for top in range(0, tiff.height, CHUNK):
            bottom = min(tiff.height, top + CHUNK)
            part = z[top:bottom]
            shade = None
            if not geographic and strength > 0:
                lo, hi = max(0, top - 1), min(tiff.height, bottom + 1)
                filled = np.where(_nodata_mask(z[lo:hi], nodata), low, z[lo:hi]).astype(np.float64)
                padded = np.pad(filled, 1, mode="edge")
                shade = hillshade(padded, pixel_x, pixel_y, strength)[top - lo:top - lo + (bottom - top)]
            image = colorize(part, low, high, table, shade, nodata)
            compressed.append(zlib.compress(np.ascontiguousarray(image).tobytes(), level))
            if tick is not None:
                tick()
        _write_rgb(out_path, tiff, compressed)
    except (ValueError, OSError) as exc:
        return False, "Relief colore : %s" % exc
    return True, ""


def _write_rgb(path, source, compressed):
    """TIFF RVB 8 bits en bandes Deflate ; le georeferencement du MNT est recopie, comme avec gdaldem."""
    end = "<"
    tags = {
        tiffio.T_WIDTH: (4, 1, struct.pack(end + "I", source.width)),
        tiffio.T_HEIGHT: (4, 1, struct.pack(end + "I", source.height)),
        tiffio.T_BITS: (3, 3, struct.pack(end + "HHH", 8, 8, 8)),
        262: (3, 1, struct.pack(end + "H", 2)),                                      # photometrique : RVB
        tiffio.T_SAMPLES: (3, 1, struct.pack(end + "H", 3)),
        tiffio.T_ROWS_PER_STRIP: (4, 1, struct.pack(end + "I", CHUNK)),
        284: (3, 1, struct.pack(end + "H", 1)),                                      # donnees entrelacees
    }
    if source.end == end:
        tags.update({tag: source.tags[tag] for tag in GEO_TAGS if tag in source.tags})
    writer = tiffio.Tiff.__new__(tiffio.Tiff)         # on ne se sert que de son ecriture de balises (voir tiffio.Tiff._write)
    writer.tags, writer.tiled = tags, False
    writer._write(path, compressed, sum(len(item) for item in compressed) > tiffio.CLASSIC_LIMIT)
