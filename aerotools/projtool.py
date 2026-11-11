"""PROJ 9 (binaries embedded in aerotools/vendor/proj) for coordinate and altitude conversions, shared by the tools.

PROJ is the reference for geodetic conversions (QGIS, GDAL, IGN): the computation is left to it, without going through pyproj (no tie
to the version of Metashape's Python). `cs2cs` (CRS change) and `cct` (a given operation, here the vertical shift
of a grid) are called as subprocesses, with a single call per batch of points.

* PROJ_DATA = embedded EPSG database + our `Geoid` folder: PROJ only reads our grids, never those of Metashape or of the workstation;
* PROJ_NETWORK=OFF: no hidden download.
* The choice of the grid is ours (guided list): the `vgridshift` operation of the chosen grid is imposed, PROJ is not left to
  pick one (in the v1 frame in Corsica, its automatic choice takes RAC23, not RAC09: 23 cm of difference on a surveyed point).
"""

import os
import sqlite3
import subprocess

import numpy as np

VENDOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor", "proj")
CHUNK = 100000                       # points per call
NO_WINDOW = 0x08000000               # CREATE_NO_WINDOW (Windows)


# Recognition list: lower-case PROJ datum names (proper names, kept as PROJ writes them) of the modern geocentric frames.
MODERN_GLOBAL = ("reseau geodesique francais 1993", "european terrestrial reference", "etrs89", "etrf", "world geodetic system 1984",
                 "wgs 84", "international terrestrial reference", "itrf", "sirgas", "geocentric datum of australia 2020", "gda2020",
                 "national spatial reference system 2011", "nad83(2011)", "new zealand geodetic datum 2000", "nzgd2000",
                 "japanese geodetic datum 2011", "jgd2011")

_db_cache = {}


class ProjError(Exception):
    """Readable failure of a PROJ call."""


def proj_db():
    """Read-only connection to the embedded proj.db (EPSG, IGNF and others)."""
    path = os.path.join(VENDOR, "share", "proj", "proj.db")
    if path not in _db_cache:
        _db_cache[path] = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True, check_same_thread=False)
    return _db_cache[path]


def datum_family(name):
    """"global" for the equivalent modern geocentric frames, otherwise the datum name itself (no equivalence)."""
    low = (name or "").lower()
    return "global" if any(word in low for word in MODERN_GLOBAL) else low


def frame_of(code):
    """{"code2d", "datum", "name", "family"} of the frame of an EPSG CRS (projected or geographic), or None."""
    code = str(code).replace("EPSG:", "").replace("EPSG::", "")
    row = proj_db().execute(
        "select g.code, g.datum_code, d.name from projected_crs p join geodetic_crs g on g.auth_name=p.geodetic_crs_auth_name "
        "and g.code=p.geodetic_crs_code join geodetic_datum d on d.auth_name=g.datum_auth_name and d.code=g.datum_code "
        "where p.auth_name='EPSG' and p.code=?", (code,)).fetchone()
    if row is None:
        row = proj_db().execute("select g.code, g.datum_code, d.name from geodetic_crs g join geodetic_datum d on d.auth_name="
                                "g.datum_auth_name and d.code=g.datum_code where g.auth_name='EPSG' and g.code=?", (code,)).fetchone()
    if row is None:
        return None
    return {"code2d": row[0], "datum": row[1], "name": row[2], "family": datum_family(row[2])}


def geographic_epsg(horizontal_epsg):
    """Code of the 2D geographic CRS of the frame of a projected CRS (the one in which the grids are queried); 4326 if unknown."""
    frame = frame_of(horizontal_epsg) if os.path.isfile(os.path.join(VENDOR, "share", "proj", "proj.db")) else None
    try:
        return int(frame["code2d"]) if frame else 4326
    except (TypeError, ValueError):
        return 4326


class Proj(object):
    """Calls to PROJ. `runner(exe, args, text)` -> text output, replaceable (tests)."""

    def __init__(self, vendor=None, geoid_dir=None, runner=None):
        self.vendor = vendor or VENDOR
        self.geoid_dir = geoid_dir
        self._runner = runner or self._run

    def exe(self, name):
        return os.path.join(self.vendor, "bin", name + (".exe" if os.name == "nt" else ""))

    def available(self):
        return os.path.isfile(self.exe("cs2cs")) and os.path.isfile(self.exe("cct")) \
            and os.path.isfile(os.path.join(self.vendor, "share", "proj", "proj.db"))

    def _env(self):
        env = dict(os.environ)
        folders = [os.path.join(self.vendor, "share", "proj")]
        if self.geoid_dir:
            folders.append(self.geoid_dir)
        env["PROJ_DATA"] = os.pathsep.join(folders)
        env["PROJ_LIB"] = env["PROJ_DATA"]
        env["PROJ_NETWORK"] = "OFF"
        return env

    def _run(self, exe, args, text):
        try:
            done = subprocess.run([exe] + list(args), input=text.encode("ascii"), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  env=self._env(), creationflags=NO_WINDOW if os.name == "nt" else 0, timeout=900)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ProjError("PROJ does not start: %s" % exc)
        if done.returncode != 0:
            raise ProjError(done.stderr.decode("utf-8", "replace").strip()[-300:] or "PROJ failed")
        return done.stdout.decode("ascii", "replace")

    def version(self):
        try:
            done = subprocess.run([self.exe("cs2cs")], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self._env(),
                                  creationflags=NO_WINDOW if os.name == "nt" else 0, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return ""
        text = (done.stderr + done.stdout).decode("ascii", "replace").strip().splitlines()
        return text[0] if text else ""

    def _columns(self, name, args, rows, width):
        """Run `name` on `rows` (n x k array) in batches; returns an (n, width) array of floats (NaN where PROJ gives up)."""
        rows = np.asarray(rows, dtype=np.float64)
        out = np.full((len(rows), width), np.nan)
        for start in range(0, len(rows), CHUNK):
            part = rows[start:start + CHUNK]
            text = "".join(" ".join("%.9f" % v for v in line) + "\n" for line in part)
            records = []
            for line in self._runner(self.exe(name), args, text).splitlines():
                line = line.strip()
                if not line or line.startswith("("):
                    continue                                  # empty line, or continuation of a point's error message
                records.append(None if line.startswith("#") else line)       # "# Record n TRANSFORMATION ERROR": point without a value
            if len(records) != len(part):
                raise ProjError("PROJ returned %d lines for %d points" % (len(records), len(part)))
            for index, line in enumerate(records):
                words = line.split() if line else []
                if len(words) >= width:
                    values = []
                    for word in words[:width]:
                        try:
                            values.append(float(word))
                        except ValueError:
                            values.append(np.nan)
                    out[start + index] = [v if abs(v) < 1e15 else np.nan for v in values]
        return out

    def lonlat(self, epsg, xs, ys):
        """(longitudes, latitudes) in degrees of the points (xs, ys) of CRS `epsg`, in its geographic frame."""
        xs, ys = np.asarray(xs, dtype=np.float64).ravel(), np.asarray(ys, dtype=np.float64).ravel()
        rows = np.column_stack([xs, ys, np.zeros(len(xs))])
        out = self._columns("cs2cs", ["-d", "9", "EPSG:%d" % int(epsg), "EPSG:%d" % geographic_epsg(epsg)], rows, 3)
        return out[:, 1], out[:, 0]                      # an EPSG geographic CRS comes out as (latitude, longitude)

    def vertical_shift(self, grid_file, lons, lats):
        """Shift (metres) to add to an ellipsoidal height to get the altitude, at the given points: `-N`. NaN outside the grid."""
        lons, lats = np.asarray(lons, dtype=np.float64).ravel(), np.asarray(lats, dtype=np.float64).ravel()
        rows = np.column_stack([lons, lats, np.zeros(len(lons)), np.zeros(len(lons))])
        args = ["-d", "6", "+proj=pipeline", "+step", "+inv", "+proj=vgridshift", "+grids=%s" % grid_file, "+multiplier=1"]
        return self._columns("cct", args, rows, 4)[:, 2]
