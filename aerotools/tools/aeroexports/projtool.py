"""Aero-Exports : PROJ 9 (binaires embarques dans aerotools/vendor/proj) pour les conversions de coordonnees et d'altitude.

PROJ est la reference des conversions geodesiques (QGIS, GDAL, IGN) : on lui confie le calcul, sans passer par pyproj (pas de lien
avec la version de Python de Metashape). On appelle `cs2cs` (changement de SCR) et `cct` (operation donnee, ici le decalage
vertical d'une grille) en sous-processus, avec un seul appel par lot de points.

* PROJ_DATA = base EPSG embarquee + notre dossier `Geoid` : PROJ ne lit que nos grilles, jamais celles de Metashape ou du poste ;
* PROJ_NETWORK=OFF : aucun telechargement cache.
* Le choix de la grille est le notre (liste guidee) : on impose l'operation `vgridshift` de la grille choisie, on ne laisse pas
  PROJ en choisir une (en repere v1 en Corse, son choix automatique prend RAC23, pas RAC09 : 23 cm d'ecart sur un point releve).
"""

import os
import subprocess

import numpy as np

VENDOR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "vendor", "proj"))
CHUNK = 100000                       # points par appel
NO_WINDOW = 0x08000000               # CREATE_NO_WINDOW (Windows)


class ProjError(Exception):
    """Echec lisible d'un appel PROJ."""


def geographic_epsg(horizontal_epsg):
    """SCR geographique du meme repere que `horizontal_epsg` (d'apres la base de PROJ) : celui dans lequel on interroge les grilles."""
    from . import projdb
    return projdb.geographic_epsg(horizontal_epsg)


class Proj(object):
    """Appels a PROJ. `runner(exe, args, text)` -> sortie texte, remplacable (tests)."""

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
            raise ProjError("PROJ ne demarre pas : %s" % exc)
        if done.returncode != 0:
            raise ProjError(done.stderr.decode("utf-8", "replace").strip()[-300:] or "PROJ a echoue")
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
        """Lance `name` sur `rows` (tableau n x k) par lots ; renvoie un tableau (n, width) de float (NaN si PROJ renonce)."""
        rows = np.asarray(rows, dtype=np.float64)
        out = np.full((len(rows), width), np.nan)
        for start in range(0, len(rows), CHUNK):
            part = rows[start:start + CHUNK]
            text = "".join(" ".join("%.9f" % v for v in line) + "\n" for line in part)
            records = []
            for line in self._runner(self.exe(name), args, text).splitlines():
                line = line.strip()
                if not line or line.startswith("("):
                    continue                                  # ligne vide, ou suite du message d'erreur d'un point
                records.append(None if line.startswith("#") else line)       # « # Record n TRANSFORMATION ERROR » : point sans valeur
            if len(records) != len(part):
                raise ProjError("PROJ a rendu %d lignes pour %d points" % (len(records), len(part)))
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
        """(longitudes, latitudes) en degres de points (xs, ys) du SCR `epsg`, dans son repere geographique."""
        xs, ys = np.asarray(xs, dtype=np.float64).ravel(), np.asarray(ys, dtype=np.float64).ravel()
        rows = np.column_stack([xs, ys, np.zeros(len(xs))])
        out = self._columns("cs2cs", ["-d", "9", "EPSG:%d" % int(epsg), "EPSG:%d" % geographic_epsg(epsg)], rows, 3)
        return out[:, 1], out[:, 0]                      # un SCR geographique EPSG sort en (latitude, longitude)

    def vertical_shift(self, grid_file, lons, lats):
        """Decalage (metres) a ajouter a une hauteur ellipsoidale pour obtenir l'altitude, aux points donnes : `-N`. NaN hors grille."""
        lons, lats = np.asarray(lons, dtype=np.float64).ravel(), np.asarray(lats, dtype=np.float64).ravel()
        rows = np.column_stack([lons, lats, np.zeros(len(lons)), np.zeros(len(lons))])
        args = ["-d", "6", "+proj=pipeline", "+step", "+inv", "+proj=vgridshift", "+grids=%s" % grid_file, "+multiplier=1"]
        return self._columns("cct", args, rows, 4)[:, 2]
