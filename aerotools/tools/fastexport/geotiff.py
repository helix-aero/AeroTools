"""Lecture minimale du georeferencement d'un GeoTIFF (TIFF classique ou BigTIFF), sans GDAL : stdlib seulement.

Sert a placer l'image dans le DXF a partir du fichier lui-meme (taille en pixels, coin haut-gauche, taille du pixel)
et non d'apres ce qu'on croit avoir demande. Metashape ecrit ModelPixelScale (33550) et ModelTiepoint (33922).
"""

import struct

TAG_WIDTH, TAG_HEIGHT, TAG_SCALE, TAG_TIEPOINT = 256, 257, 33550, 33922
_TYPES = {1: ("B", 1), 2: ("c", 1), 3: ("H", 2), 4: ("I", 4), 5: ("II", 8), 6: ("b", 1), 8: ("h", 2), 9: ("i", 4),
          11: ("f", 4), 12: ("d", 8), 16: ("Q", 8), 17: ("q", 8), 18: ("Q", 8)}


def read_geotiff(path):
    """{"width", "height", "origin": (x, y) coin haut-gauche, "pixel": (px, py)} ou None si le fichier n'est pas un GeoTIFF lisible."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(16)
            if head[:2] == b"II":
                end = "<"
            elif head[:2] == b"MM":
                end = ">"
            else:
                return None
            magic = struct.unpack(end + "H", head[2:4])[0]
            if magic == 42:
                offset = struct.unpack(end + "I", head[4:8])[0]
                big = False
            elif magic == 43:
                offset = struct.unpack(end + "Q", head[8:16])[0]
                big = True
            else:
                return None
            handle.seek(offset)
            count = struct.unpack(end + ("Q" if big else "H"), handle.read(8 if big else 2))[0]
            entry_size = 20 if big else 12
            tags = {}
            raw = handle.read(entry_size * count)
            for index in range(count):
                entry = raw[index * entry_size:(index + 1) * entry_size]
                if big:
                    tag, kind, number = struct.unpack(end + "HHQ", entry[:12])
                    slot = entry[12:20]
                else:
                    tag, kind, number = struct.unpack(end + "HHI", entry[:8])
                    slot = entry[8:12]
                if tag not in (TAG_WIDTH, TAG_HEIGHT, TAG_SCALE, TAG_TIEPOINT) or kind not in _TYPES:
                    continue
                code, size = _TYPES[kind]
                width = 2 if kind == 5 else 1
                total = size * number
                limit = 8 if big else 4
                if total <= limit:
                    data = slot[:total]
                else:
                    pointer = struct.unpack(end + ("Q" if big else "I"), slot[:limit])[0]
                    here = handle.tell()
                    handle.seek(pointer)
                    data = handle.read(total)
                    handle.seek(here)
                tags[tag] = struct.unpack(end + code * (number * width), data) if kind != 5 else None
            width_px = tags.get(TAG_WIDTH, (0,))[0]
            height_px = tags.get(TAG_HEIGHT, (0,))[0]
            scale, tie = tags.get(TAG_SCALE), tags.get(TAG_TIEPOINT)
            if not (width_px and height_px and scale and tie and len(tie) >= 6):
                return None
            i, j, _k, x, y, _z = tie[:6]
            return {"width": int(width_px), "height": int(height_px),
                    "origin": (x - i * scale[0], y + j * scale[1]), "pixel": (scale[0], scale[1])}
    except (OSError, struct.error):
        return None


def extent(info):
    """(gauche, bas, droite, haut) d'un resultat de read_geotiff."""
    left, top = info["origin"]
    return left, top - info["height"] * info["pixel"][1], left + info["width"] * info["pixel"][0], top
