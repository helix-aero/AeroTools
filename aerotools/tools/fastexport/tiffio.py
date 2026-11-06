"""Fast Export : lecture et reecriture d'un TIFF float32 a une bande (classique ou BigTIFF), sans GDAL.

Ne sert qu'a deux choses : lire une grille de geoide (`vertical.py`) et corriger les hauteurs d'un MNT ecrit par Metashape.
Compressions lues : aucune, Deflate (avec ou sans predicteur flottant). Le reste leve ValueError : on prefere refuser
un fichier inconnu que le corrompre.
"""

import struct
import zlib

import numpy as np

T_WIDTH, T_HEIGHT, T_BITS, T_COMPRESSION = 256, 257, 258, 259
T_STRIP_OFFSETS, T_SAMPLES, T_ROWS_PER_STRIP, T_STRIP_COUNTS = 273, 277, 278, 279
T_PREDICTOR, T_TILE_W, T_TILE_H, T_TILE_OFFSETS, T_TILE_COUNTS, T_FORMAT = 317, 322, 323, 324, 325, 339
T_SCALE, T_TIEPOINT, T_GDAL_NODATA, T_GDAL_METADATA, T_GEOKEYS = 33550, 33922, 42113, 42112, 34735
OFFSET_TAGS = (T_STRIP_OFFSETS, T_STRIP_COUNTS, T_TILE_OFFSETS, T_TILE_COUNTS, T_COMPRESSION, T_PREDICTOR)
TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 16: 8, 17: 8, 18: 8}
TYPE_CODE = {1: "B", 3: "H", 4: "I", 6: "b", 8: "h", 9: "i", 11: "f", 12: "d", 16: "Q", 17: "q"}
CLASSIC_LIMIT = 3900 * 1024 * 1024               # au-dela : BigTIFF


def lzw_decode(data):
    """Bytes of a TIFF LZW block (codes of 9 to 12 bits, most significant bit first, "early change"). Slow (pure Python): meant for
    the small pictures Metashape writes for a mask, not for a whole ortho. Raises ValueError on a damaged block."""
    table = [bytes((i,)) for i in range(256)] + [b"", b""]
    out = bytearray()
    bits, buf, count, previous = 9, 0, 0, None
    for byte in data:
        buf = (buf << 8) | byte
        count += 8
        while count >= bits:
            code = (buf >> (count - bits)) & ((1 << bits) - 1)
            count -= bits
            buf &= (1 << count) - 1
            if code == 257:
                return bytes(out)
            if code == 256:
                del table[258:]
                bits, previous = 9, None
                continue
            if previous is None:
                if code >= 256:
                    raise ValueError("LZW : bloc abime")
                entry = table[code]
            else:
                if code > len(table):
                    raise ValueError("LZW : bloc abime")
                entry = table[code] if code < len(table) else previous + previous[:1]
                if len(table) < 4096:
                    table.append(previous + entry[:1])
            out += entry
            previous = entry
            size = len(table)
            bits = 9 if size < 511 else 10 if size < 1023 else 11 if size < 2047 else 12
    return bytes(out)


class Tiff(object):
    """Premiere image d'un TIFF : ses balises (octets bruts) et ses blocs (tuiles ou bandes)."""

    def __init__(self, path, strict=True):
        """`strict=False` : ne verifie pas que c'est un float32 a une bande (les balises et les positions des blocs seules)."""
        self.path = path
        with open(path, "rb") as handle:
            head = handle.read(16)
            self.end = {b"II": "<", b"MM": ">"}.get(head[:2])
            if self.end is None:
                raise ValueError("pas un TIFF")
            magic = struct.unpack(self.end + "H", head[2:4])[0]
            if magic not in (42, 43):
                raise ValueError("pas un TIFF")
            self.big = magic == 43
            offset = struct.unpack(self.end + ("Q" if self.big else "I"), head[8:16] if self.big else head[4:8])[0]
            handle.seek(offset)
            count = struct.unpack(self.end + ("Q" if self.big else "H"), handle.read(8 if self.big else 2))[0]
            size = 20 if self.big else 12
            raw = handle.read(size * count)
            limit = 8 if self.big else 4
            self.tags = {}                         # balise -> (type, nombre, octets)
            for index in range(count):
                entry = raw[index * size:(index + 1) * size]
                if self.big:
                    tag, kind, number = struct.unpack(self.end + "HHQ", entry[:12])
                    slot = entry[12:20]
                else:
                    tag, kind, number = struct.unpack(self.end + "HHI", entry[:8])
                    slot = entry[8:12]
                total = TYPE_SIZE.get(kind, 1) * number
                if total <= limit:
                    data = slot[:total]
                else:
                    pointer = struct.unpack(self.end + ("Q" if self.big else "I"), slot[:limit])[0]
                    here = handle.tell()
                    handle.seek(pointer)
                    data = handle.read(total)
                    handle.seek(here)
                self.tags[tag] = (kind, number, data)
        self.width = self.ints(T_WIDTH)[0]
        self.height = self.ints(T_HEIGHT)[0]
        self.compression = self.ints(T_COMPRESSION, [1])[0]
        self.predictor = self.ints(T_PREDICTOR, [1])[0]
        if strict:
            if self.ints(T_BITS, [0])[0] != 32 or self.ints(T_FORMAT, [1])[0] != 3 or self.ints(T_SAMPLES, [1])[0] != 1:
                raise ValueError("seul le float32 a une bande est lu")
            if self.compression not in (1, 8, 32946) or self.predictor not in (1, 3):
                raise ValueError("compression %d / predicteur %d non pris en charge" % (self.compression, self.predictor))
        self.tiled = T_TILE_W in self.tags
        if self.tiled:
            self.block_w, self.block_h = self.ints(T_TILE_W)[0], self.ints(T_TILE_H)[0]
            self.offsets, self.counts = self.ints(T_TILE_OFFSETS), self.ints(T_TILE_COUNTS)
        else:
            self.block_w, self.block_h = self.width, self.ints(T_ROWS_PER_STRIP, [self.height])[0]
            self.offsets, self.counts = self.ints(T_STRIP_OFFSETS), self.ints(T_STRIP_COUNTS)
        self.across = -(-self.width // self.block_w)

    def ints(self, tag, default=None):
        """Valeurs numeriques d'une balise, ou `default`."""
        found = self.tags.get(tag)
        if found is None:
            return default
        kind, number, data = found
        return list(struct.unpack(self.end + TYPE_CODE[kind] * number, data))

    def text(self, tag):
        found = self.tags.get(tag)
        return found[2].decode("latin-1").rstrip("\0") if found else ""

    # -- decodage --------------------------------------------------------------------------

    def _decode(self, payload, width, height):
        data = payload if self.compression == 1 else zlib.decompress(payload)
        buf = np.frombuffer(data, dtype=np.uint8)
        if buf.size < width * height * 4:
            raise ValueError("bloc trop court")
        buf = buf[:width * height * 4]
        if self.predictor == 3:
            rows = np.cumsum(buf.reshape(height, width * 4), axis=1, dtype=np.uint8)
            planes = rows.reshape(height, 4, width)            # plan 0 = octet de poids fort
            if self.end == "<":
                planes = planes[:, ::-1, :]
            buf = np.ascontiguousarray(planes.transpose(0, 2, 1)).reshape(-1)
        return np.frombuffer(buf.tobytes(), dtype=np.dtype(self.end + "f4")).reshape(height, width).astype(np.float32)

    def blocks(self):
        """(ligne, colonne, tableau) de chaque bloc, tableau complet (tuile entiere, bande entiere)."""
        with open(self.path, "rb") as handle:
            for index, (offset, count) in enumerate(zip(self.offsets, self.counts)):
                handle.seek(offset)
                payload = handle.read(count)
                if self.tiled:
                    row, col = divmod(index, self.across)
                    top, left = row * self.block_h, col * self.block_w
                    yield top, left, self._decode(payload, self.block_w, self.block_h)
                else:
                    top = index * self.block_h
                    yield top, 0, self._decode(payload, self.width, min(self.block_h, self.height - top))

    def read_all(self):
        """L'image entiere (pour une grille : quelques Ko)."""
        out = np.empty((self.height, self.width), dtype=np.float32)
        for top, left, block in self.blocks():
            part = block[:self.height - top, :self.width - left]
            out[top:top + part.shape[0], left:left + part.shape[1]] = part
        return out

    # -- reecriture ------------------------------------------------------------------------

    def rewrite(self, dest, change, level=6):
        """Ecrit une copie de la premiere image dans `dest`, chaque bloc passant par `change(ligne, colonne, tableau)`.

        Les balises de georeferencement sont recopiees telles quelles ; la copie est en Deflate sans predicteur, sans
        apercus (les apercus d'origine ne correspondraient plus aux valeurs corrigees). Octets little-endian seulement."""
        if self.end != "<":
            raise ValueError("reecriture : TIFF little-endian seulement")
        compressed = []
        for top, left, block in self.blocks():
            new = np.ascontiguousarray(change(top, left, block), dtype="<f4")
            compressed.append(zlib.compress(new.tobytes(), level))
        big = sum(len(item) for item in compressed) > CLASSIC_LIMIT
        self._write(dest, compressed, big)

    def _write(self, dest, compressed, big):
        end = "<"
        word = "Q" if big else "I"
        limit = 8 if big else 4
        head_size = 16 if big else 8
        offsets, cursor = [], head_size
        for item in compressed:
            offsets.append(cursor)
            cursor += len(item)
        kind_off = 16 if big else 4
        tags = {tag: value for tag, value in self.tags.items() if tag not in OFFSET_TAGS}
        counts = [len(item) for item in compressed]
        tags[T_COMPRESSION] = (3, 1, struct.pack(end + "H", 8))
        names = (T_TILE_OFFSETS, T_TILE_COUNTS) if self.tiled else (T_STRIP_OFFSETS, T_STRIP_COUNTS)
        for tag, values in zip(names, (offsets, counts)):
            tags[tag] = (kind_off, len(values), struct.pack(end + (("Q" if big else "I") * len(values)), *values))
        extra, entries = bytearray(), []
        pad = b"\0" if cursor % 2 else b""          # une IFD commence a une adresse paire
        ifd_start = cursor + len(pad)
        entry_size = 20 if big else 12
        table_size = (8 if big else 2) + entry_size * len(tags) + (8 if big else 4)
        extra_base = ifd_start + table_size
        for tag in sorted(tags):
            kind, number, data = tags[tag]
            if len(data) <= limit:
                slot = data.ljust(limit, b"\0")
            else:
                slot = struct.pack(end + word, extra_base + len(extra))
                extra += data + (b"\0" if len(data) % 2 else b"")
            entries.append(struct.pack(end + ("HHQ" if big else "HHI"), tag, kind, number) + slot)
        with open(dest, "wb") as out:
            if big:
                out.write(b"II" + struct.pack(end + "HHHQ", 43, 8, 0, ifd_start))
            else:
                out.write(b"II" + struct.pack(end + "HI", 42, ifd_start))
            for item in compressed:
                out.write(item)
            out.write(pad)
            out.write(struct.pack(end + ("Q" if big else "H"), len(entries)))
            out.write(b"".join(entries))
            out.write(struct.pack(end + word, 0))
            out.write(bytes(extra))
