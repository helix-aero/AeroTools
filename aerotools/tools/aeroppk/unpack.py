"""Unpack what GNSS archives serve: `.gz`, Unix `compress` (`.Z`) and Compact RINEX / Hatanaka (`.crx`, `.d`).

The RGP keeps its RINEX 2 files (all the older days) as Hatanaka compressed with Unix compress, e.g. `crte350i.23d.Z`. Python has
no decoder for `.Z`, so a small pure Python one is included (LZW as written by compress(1), 9 to 16 bits). Hatanaka needs
`crx2rnx` (shipped with RTKLIB, see aerotools/vendor/rtklib).
"""

import gzip
import json
import os
import shutil
import subprocess

NO_WINDOW = 0x08000000


class UnpackError(Exception):
    pass


def lzw_decompress(data):
    """Decode the bytes of a Unix compress (`.Z`) file."""
    if len(data) < 3 or data[0] != 0x1F or data[1] != 0x9D:
        raise UnpackError("not a .Z file")
    maxbits = data[2] & 0x1F
    block_mode = bool(data[2] & 0x80)
    if maxbits < 9 or maxbits > 16:
        raise UnpackError("unsupported .Z code size")
    limit = 1 << maxbits
    table = [bytes([i]) for i in range(256)] + ([b""] if block_mode else [])   # 256 = the CLEAR code, never a string
    first = 257 if block_mode else 256
    free_ent = first                  # == len(table) while the table grows
    n_bits = 9
    maxcode = 511
    out = []
    emit = out.append
    old = None
    prev = b""                        # the string of `old`
    pos = 3
    size = len(data)
    from_bytes = int.from_bytes
    while pos < size:
        group = data[pos:pos + n_bits]
        pos += n_bits
        mask = (1 << n_bits) - 1
        acc = from_bytes(group, "little")
        restart = False
        for offset in range(0, len(group) * 8 - (n_bits - 1), n_bits):
            if free_ent > maxcode:
                break
            code = (acc >> offset) & mask
            if old is None:
                if code >= 256:
                    raise UnpackError("corrupt .Z stream (first code)")
                old = code
                prev = table[code]
                emit(prev)
                continue
            if code == 256 and block_mode:
                del table[256:]
                table.append(b"")
                free_ent = 257
                old = None
                restart = True
                break
            if code < free_ent:
                entry = table[code]
            elif code == free_ent:
                entry = prev + prev[:1]
            else:
                raise UnpackError("corrupt .Z stream")
            emit(entry)
            if free_ent < limit:
                table.append(prev + entry[:1])
                free_ent += 1
            old = code
            prev = entry
        if restart:
            n_bits = 9
            maxcode = 511
        elif free_ent > maxcode:
            n_bits += 1
            maxcode = (1 << n_bits) - 1 if n_bits < maxbits else limit
    return b"".join(out)


MAX_CRINEX = 3.0                     # newest Compact RINEX the embedded crx2rnx decodes (checked: 3.1 files are refused by it)


def crinex_version(first_line):
    """Version written at the top of a Compact RINEX file (`3.0   COMPACT RINEX FORMAT ...`), or None."""
    try:
        return float(first_line[:9])
    except (ValueError, TypeError):
        return None


def hatanaka_output_name(name):
    """crte350i.23d -> crte350i.23o ; xxx.crx -> xxx.rnx"""
    low = name.lower()
    if low.endswith(".crx"):
        return name[:-4] + ".rnx"
    stem, dot, ext = name.rpartition(".")
    if dot and len(ext) == 3 and ext[2].lower() == "d" and ext[:2].isdigit():
        return stem + "." + ext[:2] + "o"
    return name + ".rnx"


def _source_key(path):
    stat = os.stat(path)
    return "%d:%d" % (stat.st_size, stat.st_mtime_ns)


def _cached_result(marker, key, out_dir):
    """The unpacked file recorded next to `out_dir` for the same source (same size and time), or None."""
    try:
        with open(marker, "r", encoding="utf-8") as handle:
            record = json.load(handle)
        result = os.path.join(out_dir, record["result"])
        if record["key"] == key and os.path.isfile(result) and os.path.getsize(result) > 0:
            return result
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def unpack(path, out_dir, crx2rnx=None):
    """Return the path of a plain RINEX file made from `path` (written in `out_dir`; the source is untouched).

    A file already unpacked from the same source (same size and modification time) is reused: a base is prepared again at every session."""
    os.makedirs(out_dir, exist_ok=True)
    name = os.path.basename(path)
    marker = os.path.join(out_dir, name + ".unpacked")
    try:
        key = _source_key(path)
    except OSError as exc:
        raise UnpackError("cannot unpack %s: %s" % (name, exc))
    cached = _cached_result(marker, key, out_dir)
    if cached:
        return cached
    work = os.path.join(out_dir, name)
    same = os.path.abspath(path) == os.path.abspath(work)
    current, scratch = path, False                  # `scratch`: `current` is a copy of ours that can be deleted
    try:
        low = name.lower()
        if low.endswith(".gz"):
            target = work[:-3]
            with gzip.open(path, "rb") as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            current, scratch = target, True
        elif low.endswith(".z"):
            target = work[:-2]
            with open(path, "rb") as src:
                payload = lzw_decompress(src.read())
            with open(target, "wb") as dst:
                dst.write(payload)
            del payload
            current, scratch = target, True
        elif not same:
            shutil.copyfile(path, work)
            current, scratch = work, True
        base = os.path.basename(current)
        with open(current, "rb") as handle:
            first_line = handle.read(80)
        if b"COMPACT RINEX FORMAT" in first_line:
            if not crx2rnx or not os.path.isfile(crx2rnx):
                raise UnpackError("crx2rnx (Compact RINEX decoder) is missing")
            version = crinex_version(first_line)
            if version is not None and version > MAX_CRINEX:
                raise UnpackError("Compact RINEX %.1f is newer than the decoder of Aero-PPK reads (up to %.1f)" % (version, MAX_CRINEX))
            if not scratch:                         # the decoder works in place: give it a copy
                copy = os.path.join(out_dir, base)
                shutil.copyfile(current, copy)
                current, scratch = copy, True
            target = os.path.join(out_dir, hatanaka_output_name(base))
            if os.path.exists(target):
                os.remove(target)
            flags = NO_WINDOW if os.name == "nt" else 0
            done = subprocess.run([crx2rnx, "-f", current], cwd=out_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  creationflags=flags, timeout=600)
            if done.returncode != 0 or not os.path.isfile(target):
                raise UnpackError("crx2rnx failed: " + done.stderr.decode("ascii", "replace")[-200:])
            if os.path.abspath(current) != os.path.abspath(target):
                os.remove(current)
            current = target
        try:
            with open(marker, "w", encoding="utf-8") as handle:
                json.dump({"key": key, "result": os.path.basename(current)}, handle)
        except OSError:
            pass
        return current
    except (OSError, EOFError, gzip.BadGzipFile) as exc:
        raise UnpackError("cannot unpack %s: %s" % (name, exc))
