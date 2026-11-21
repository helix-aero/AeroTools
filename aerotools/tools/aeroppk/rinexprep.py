"""Turn downloaded archives into plain RINEX files RTKLIB can read: unpack, merge the pieces, check what we got."""

import os

from . import rinex, unpack


class PrepError(Exception):
    pass


OBS_TYPES_LABEL = b"SYS / # / OBS TYPES"
CRLF, LF = bytes((13, 10)), bytes((10,))


def _split_v3(path):
    """(header lines, body lines) of a RINEX 3 observation file, as bytes lines."""
    header, body = [], []
    in_header = True
    with open(path, "rb") as handle:
        for raw in handle:
            if in_header:
                header.append(raw)
                if raw[60:73] == b"END OF HEADER":
                    in_header = False
            else:
                body.append(raw)
    return header, body


def _read_header(path):
    """Header lines of a RINEX file (up to END OF HEADER), as bytes lines. The body is not read."""
    header = []
    with open(path, "rb") as handle:
        for raw in handle:
            header.append(raw)
            if raw[60:73] == b"END OF HEADER":
                break
    return header


def _iter_body(path):
    """The lines after END OF HEADER of a RINEX file, one at a time (nothing is kept)."""
    with open(path, "rb") as handle:
        for raw in handle:
            if raw[60:73] == b"END OF HEADER":
                break
        for raw in handle:
            yield raw


def _label_of(line):
    return line[60:].strip()


def _types_signature(lines, version):
    label = OBS_TYPES_LABEL if version >= 3.0 else b"# / TYPES OF OBSERV"
    return [l[:60].rstrip() for l in lines if l[60:].strip() == label]


def types_v3(header):
    """{system letter: [observation codes]} of a RINEX 3 header, codes in the order of the file."""
    out, current = {}, None
    for line in header:
        if line[60:].strip() != OBS_TYPES_LABEL:
            continue
        text = line[:60].decode("ascii", "replace")
        if text[0].strip():
            current = text[0]
            out[current] = text[7:].split()
        elif current is not None:
            out[current].extend(text[7:].split())
    return out


def _types_lines(types):
    """Header lines `SYS / # / OBS TYPES` for the given {system: codes}."""
    lines = []
    for system, codes in types.items():
        for start in range(0, len(codes), 13):
            head = "%s  %3d" % (system, len(codes)) if start == 0 else " " * 6
            text = head + "".join(" " + code.ljust(3) for code in codes[start:start + 13])
            lines.append((text.ljust(60) + OBS_TYPES_LABEL.decode("ascii")).encode("ascii") + b"\n")
    return lines


def _with_types(header, types):
    """The header with its `SYS / # / OBS TYPES` lines replaced by those of `types` (at the place of the first one)."""
    out, written = [], False
    for line in header:
        if line[60:].strip() == OBS_TYPES_LABEL:
            if not written:
                out.extend(_types_lines(types))
                written = True
            continue
        out.append(line)
    return out


def _union_types(all_types):
    """The observation codes of several pieces, the first piece's order first, then what the next ones add."""
    union = {}
    for types in all_types:
        for system, codes in types.items():
            known = union.setdefault(system, [])
            known.extend(code for code in codes if code not in known)
    return union


def _remap_lines(lines, source, target):
    """Generator behind `_remap_body`: the lines of a RINEX 3 body (any iterable of bytes lines), rewritten one at a time."""
    if source == target:
        yield from lines
        return
    index = dict((system.encode("ascii"), dict((code, i) for i, code in enumerate(codes))) for system, codes in source.items())
    blank = b" " * 16
    iterator = iter(lines)
    for line in iterator:
        yield line
        if not line.startswith(b">"):
            continue
        try:
            flag, count = int(line[31:32] or b"0"), int(line[32:35])
        except ValueError:
            continue
        for _ in range(count):
            record = next(iterator, None)
            if record is None:
                break
            system = record[:1]
            if flag > 1 or system not in index:
                yield record
                continue
            ending = CRLF if record.endswith(CRLF) else LF
            text = record.rstrip(CRLF)
            fields = dict((code, text[3 + 16 * i:3 + 16 * (i + 1)].ljust(16)) for code, i in index[system].items())
            row = b"".join(fields.get(code, blank) for code in target[system.decode("ascii")])
            yield (text[:3] + row).rstrip() + ending


def _remap_body(body, source, target):
    """Rewrite the observation records of a RINEX 3 body so that each satellite line follows the code order `target` (per system).

    A code that the piece does not have is left blank. Event records (epoch flag above 1) are copied as they are."""
    if source == target:
        return body
    return list(_remap_lines(body, source, target))


STALE_LABELS = (b"TIME OF LAST OBS",)


def _merged_header(header, drop):
    """The first piece's header without the lines that only describe that piece (`drop`: labels)."""
    return [line for line in header if _label_of(line) not in drop]


def merge_obs(paths, out_path):
    """Concatenate observation files of the same station and format (consecutive time pieces) into one RINEX file.

    The header of the first file is kept, except `TIME OF LAST OBS` (it would describe the first piece only: the reader then finds the real
    last epoch in the data) and `INTERVAL` when the pieces disagree; the data of the others follow. RINEX 3 pieces that record the same
    observation codes in a different order, or some more codes (a receiver set up again between two files), are rewritten on the union of
    the codes, because RTKLIB reads the columns of every record with the header of the first piece. RINEX 2 pieces must agree.
    The pieces are streamed: only their headers are held in memory.
    """
    if not paths:
        raise PrepError("no observation file")
    if len(paths) == 1:
        return paths[0]
    headers = [_read_header(path) for path in paths]
    first_header = headers[0]
    version = rinex.read_obs_header(paths[0]).version
    signature = _types_signature(first_header, version)
    if version < 3.0:
        for path, header in zip(paths[1:], headers[1:]):
            if _types_signature(header, version) != signature:
                raise PrepError("the base files do not record the same observation types (%s)" % os.path.basename(path))
    all_types = [types_v3(header) for header in headers] if version >= 3.0 else None
    target = _union_types(all_types) if all_types else None
    drop = set(STALE_LABELS)
    if len(set(_interval_of(header) for header in headers)) > 1:
        drop.add(b"INTERVAL")
    head = _merged_header(first_header, drop)
    with open(out_path, "wb") as out:
        out.writelines(_with_types(head, target) if target and target != all_types[0] else head)
        for k, path in enumerate(paths):
            body = _iter_body(path)
            out.writelines(_remap_lines(body, all_types[k], target) if target else body)
    return out_path


def _interval_of(header):
    for line in header:
        if _label_of(line) == b"INTERVAL":
            return line[:10].strip()
    return None


def prepare_archives(obs_archives, nav_archives, work_dir, crx2rnx):
    """Unpack every archive; merge the observation pieces. Returns (obs_path, [nav_paths])."""
    plain_obs = [unpack.unpack(p, work_dir, crx2rnx=crx2rnx) for p in obs_archives]
    plain_nav = [unpack.unpack(p, work_dir, crx2rnx=crx2rnx) for p in nav_archives]
    merged = merge_obs(plain_obs, os.path.join(work_dir, "merged_" + os.path.basename(plain_obs[0]))) if len(plain_obs) > 1 \
        else plain_obs[0]
    return merged, plain_nav
