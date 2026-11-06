"""Aero-Exports: state of the height grids in our `Geoid` folder, verified download.

The catalogue (systems, grids, sizes, SHA-256, URLs) comes from `projdb.py` (PROJ database and PROJ-data index): no hand-written
table any more, so it covers the whole world. No dependency (urllib), runs in Metashape's Python (3.9). The grids are stored in
`own_dir()` and read by PROJ and by `vertical.py`: Metashape's own folders are never searched.
"""

import hashlib
import os
import tempfile
import urllib.parse
import urllib.request

from . import projdb

ALLOWED_HOSTS = ("cdn.proj.org",)
MAX_DOWNLOAD_BYTES = 120 * 1024 * 1024
ELLIPSOID = {"id": projdb.ELLIPSOID_ID, "file": None, "bbox": (-180.0, -90.0, 180.0, 90.0), "url": None,
             "label": "Ellipsoidal height (no geoid grid)"}


# --------------------------------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------------------------------

def vertical_by_id(vid):
    """Record of a height grid (id = file name without extension) or of the ellipsoid; None if unknown."""
    if vid == projdb.ELLIPSOID_ID:
        return ELLIPSOID
    return projdb.grid_by_id(vid)


def label(item):
    return item["label"]


# --------------------------------------------------------------------------------------------------
# Geoids suited to a location
# --------------------------------------------------------------------------------------------------

def covers(item, lon, lat):
    return bool(item.get("bbox")) and projdb.inside(item["bbox"], lon, lat)


def own_dir():
    """AeroTools' "Geoid" folder (per workstation): the grids downloaded by the window are stored and read there. Nothing is
    searched in Metashape's folders."""
    from ... import config
    return os.path.join(config.settings_dir(), "Geoid")


_SHA_CACHE = {}


def grid_state(vertical_id, folder=None):
    """("ok" | "stale" | "missing", path) of the grid in our folder. "stale": the file is there but its fingerprint
    (SHA-256) is not that of the official grid in the catalogue: damaged, old or modified copy, to be downloaded again."""

    spec = vertical_by_id(vertical_id)
    if spec is None or not spec.get("file"):
        raise KeyError(vertical_id)
    path = os.path.join(folder or own_dir(), spec["file"])
    if not os.path.isfile(path):
        return "missing", path
    stamp = (path, os.path.getsize(path), os.path.getmtime(path))
    if stamp not in _SHA_CACHE:
        _SHA_CACHE[stamp] = sha256_file(path)
    return ("ok" if _SHA_CACHE[stamp] == spec.get("sha256") else "stale"), path


ZONE_MAX_SIZE = 5 * 1024 * 1024         # the zone prompt only offers small grids; large ones are downloaded on demand


def zone_grids(project_epsg, lon, lat, folder=None):
    """[(record, state)] of the project CRS grids that cover (lon, lat), under 5 MB, missing or stale. Without a CRS or
    a position: nothing (we cannot tell what is useful)."""
    if lon is None or lat is None or not project_epsg or not projdb.available():
        return []
    out = []
    for spec in projdb.verticals_for(project_epsg, lon, lat, max_size=ZONE_MAX_SIZE):
        state = grid_state(spec["id"], folder)[0]
        if state != "ok":
            out.append((spec, state))
    return out


def check_geoid_presence(vertical_id, folder=None, lon=None, lat=None):
    """Diagnosis for the interface: {'status': 'ok'|'stale'|'missing'|'not_covering'|'none', 'spec': record, 'path': path}."""
    spec = vertical_by_id(vertical_id)
    if spec is None:
        raise KeyError(vertical_id)
    if spec.get("file") is None:
        return {"status": "none", "spec": spec, "path": None}
    if lon is not None and lat is not None and spec.get("bbox") and not covers(spec, lon, lat):
        return {"status": "not_covering", "spec": spec, "path": None}
    state, path = grid_state(vertical_id, folder)
    return {"status": state, "spec": spec, "path": path}


# --------------------------------------------------------------------------------------------------
# Verified download
# --------------------------------------------------------------------------------------------------

def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def _check_url(url):
    parts = urllib.parse.urlparse(url)
    if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS:
        raise ValueError("source not allowed: %s (HTTPS to %s only)" % (url, ", ".join(ALLOWED_HOSTS)))


def download_geoid(vertical_id, dest_dir, overwrite=False, progress=None, timeout=60, opener=None):
    """Download the catalogue grid into `dest_dir`; checks size AND sha256 BEFORE putting it in place.

    * HTTPS only, hosts from ALLOWED_HOSTS, at most MAX_DOWNLOAD_BYTES;
    * writes to a temporary file in the same folder, renamed only if the hash is correct (atomic);
    * never replaces an existing file unless `overwrite=True`;
    * nothing is executed: it is data. Returns the final path. Raises ValueError / OSError.
    `progress(bytes_received, bytes_total)` optional; `opener` (tests) replaces urllib.request.urlopen.
    """
    spec = vertical_by_id(vertical_id)
    if spec is None or not spec.get("url"):
        raise ValueError("no official downloadable source for %r" % (vertical_id,))
    _check_url(spec["url"])
    if spec["size"] > MAX_DOWNLOAD_BYTES:
        raise ValueError("file too large (%d bytes)" % spec["size"])
    os.makedirs(dest_dir, exist_ok=True)
    final = os.path.join(dest_dir, spec["file"])
    if os.path.exists(final) and not overwrite:
        raise FileExistsError(final)
    fd, tmp = tempfile.mkstemp(prefix=spec["file"] + ".", suffix=".part", dir=dest_dir)
    os.close(fd)
    try:
        request = urllib.request.Request(spec["url"], headers={"User-Agent": "HELIX-AeroExports/0.1"})
        with (opener or urllib.request.urlopen)(request, timeout=timeout) as resp, open(tmp, "wb") as out:
            total = int(resp.headers.get("Content-Length") or spec["size"])
            got = 0
            while True:
                block = resp.read(1 << 16)
                if not block:
                    break
                got += len(block)
                if got > MAX_DOWNLOAD_BYTES:
                    raise ValueError("download too large")
                out.write(block)
                if progress:
                    progress(got, total)
        if os.path.getsize(tmp) != spec["size"]:
            raise ValueError("received size %d, expected %d" % (os.path.getsize(tmp), spec["size"]))
        digest = sha256_file(tmp)
        if digest != spec["sha256"]:
            raise ValueError("unexpected sha256: %s (expected %s)" % (digest, spec["sha256"]))

        os.replace(tmp, final)
        return final
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
