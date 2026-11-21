"""A small Unix `compress` (.Z) encoder for the tests: the repository has one real .Z file only, this one makes bigger or odd ones.

It follows compress(1) 4.x: block mode, codes packed in groups of `n_bits` bytes, a partial group padded when the code size grows."""


def z_compress(data, maxbits=16, clear_every=None, block_mode=True):
    maxmax = 1 << maxbits
    out = bytearray(b"\x1f\x9d")
    out.append((0x80 if block_mode else 0) | maxbits)
    first = 257 if block_mode else 256
    state = {"n": 9, "max": 511, "free": first}
    group = []

    def emit(code, clear=False):
        group.append(code)
        if len(group) == 8:
            flush_padded()
        if clear:
            flush_padded()
            state["n"], state["max"] = 9, 511
        elif state["free"] > state["max"]:
            flush_padded()
            state["n"] += 1
            state["max"] = (1 << state["n"]) - 1 if state["n"] < maxbits else maxmax

    def flush_padded():
        if group:
            bits = 0
            for i, code in enumerate(group):
                bits |= code << (i * state["n"])
            out.extend(bits.to_bytes(state["n"], "little"))
            del group[:]

    if not data:
        return bytes(out)
    table = {}
    ent = data[0]
    emitted = 0
    for c in data[1:]:
        key = (ent << 8) | c
        code = table.get(key)
        if code is not None:
            ent = code
            continue
        emit(ent)
        emitted += 1
        if state["free"] < maxmax:
            table[key] = state["free"]
            state["free"] += 1
        if block_mode and clear_every and emitted % clear_every == 0:
            table.clear()
            state["free"] = first
            emit(256, clear=True)
        ent = c
    emit(ent)
    # the last group is written as short as possible
    if group:
        bits = 0
        for i, code in enumerate(group):
            bits |= code << (i * state["n"])
        out.extend(bits.to_bytes((len(group) * state["n"] + 7) // 8, "little"))
        del group[:]
    return bytes(out)
