"""Look inside a base station file without downloading it: the first 32 KB are enough for the header and the first epoch.

Gives the user what the map filters need (sampling, constellations, receiver) before any big download. Works for RINEX 3
(`.crx.gz`, header lists the observation types per constellation) and RINEX 2 (`.d.Z`, constellations read from the satellite
list of the first epoch, which Compact RINEX writes in full).
"""

import re
import zlib

from .. import rinex as _rinex, unpack
from . import net

LENGTH = 32768
SAT = re.compile(r"([GRECJSI])(\d\d)")


def decode_head(name, data):
    """First bytes of an archive -> text (best effort: a truncated stream still gives its beginning)."""
    if name.lower().endswith(".gz"):
        stream = zlib.decompressobj(16 + zlib.MAX_WBITS)
        try:
            return stream.decompress(data).decode("ascii", "replace")
        except zlib.error:
            return ""
    if name.lower().endswith(".z"):
        try:
            return unpack.lzw_decompress(data).decode("ascii", "replace")
        except unpack.UnpackError:
            return ""
    return data.decode("ascii", "replace")


def parse_head(text):
    """Header text -> dict(systems, interval, receiver, antenna, version)."""
    info = {"systems": [], "interval": None, "receiver": "", "antenna": "", "version": None, "crinex": None}
    lines = text.splitlines()
    systems, in_data = set(), False
    epochs_seen = 0
    for line in lines:
        label = line[60:].strip() if len(line) > 60 else ""
        if label == "RINEX VERSION / TYPE" or "COMPACT RINEX FORMAT" in line[:60 + 20]:
            if "COMPACT RINEX FORMAT" in line[:60 + 20]:
                info["crinex"] = unpack.crinex_version(line)
            if label == "RINEX VERSION / TYPE":
                try:
                    info["version"] = float(line[0:9])
                except ValueError:
                    pass
            continue
        if label == "SYS / # / OBS TYPES" and line[0:1].strip():
            systems.add(line[0:1])
        elif label == "INTERVAL":
            try:
                info["interval"] = float(line[0:10])
            except ValueError:
                pass
        elif label == _rinex.RECEIVER_LABEL:
            info["receiver"] = line[20:40].strip()
        elif label == "ANT # / TYPE":
            info["antenna"] = line[20:40].strip()
        elif label == "END OF HEADER":
            in_data = True
        elif in_data and epochs_seen < 3 and (line.startswith("&") or line.startswith(">")):
            # the satellite list of an epoch: 32 columns after the epoch line start (RINEX 2 / Compact RINEX), 35 for RINEX 3
            for letter, _num in SAT.findall(line[32:] if line.startswith("&") else line[35:]):
                systems.add(letter)
            epochs_seen += 1
    info["systems"] = [s for s in "GRECJSI" if s in systems]
    return info


def problem(info):
    """A short key when the file cannot be used by this tool (`crinex`: a Compact RINEX newer than the decoder), else ''."""
    if (info.get("crinex") or 0.0) > unpack.MAX_CRINEX:
        return "crinex"
    return ""


def inspect_product(product, opener=None):
    """Read the head of the first observation file of a product. Returns the dict of parse_head plus "size"; {} on failure."""
    url, name = product.obs[0]
    try:
        data = net.read_range(url, LENGTH, opener=opener)
    except net.NetError:
        return {}
    info = parse_head(decode_head(name, data))
    info["size"] = sum(v for v in product.sizes.values() if v) if product.sizes else None
    return info
