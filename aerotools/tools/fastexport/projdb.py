"""Fast Export : la base de PROJ (proj.db, EPSG + IGNF + autres, lecture seule) et l'index des grilles de PROJ-data.

Repond a : « quels systemes de coordonnees proposer ici ? » et « quelles altitudes (grilles de geoide) vont avec celui-ci, ici ? »,
partout dans le monde, sans table ecrite a la main.

* Systemes XY : les CRS projetes EPSG non obsoletes dont l'emprise d'usage contient le projet, les plus precis d'abord, plus la zone
  UTM WGS 84 (et ETRS89 en Europe) ; recherche libre par nom ou par code.
* Altitudes : les grilles `VERTICAL_OFFSET_GEOGRAPHIC_TO_VERTICAL` de PROJ-data (index `files.geojson` embarque : url, taille, SHA-256,
  emprise, CRS source et cible) qui couvrent le lieu ET dont le repere source est le meme que celui du SCR choisi.
  Pourquoi cette condition : Metashape exporte des hauteurs ellipsoidales dans le repere du SCR choisi ; retrancher la grille d'un
  autre repere (une grille ETRS89 sur des hauteurs Bessel, par exemple) donnerait une altitude fausse de plusieurs metres. Repere
  « identique » = meme datum, ou meme famille geocentrique moderne (ETRS89, WGS 84, ITRF et leurs realisations nationales, RGF93,
  GDA2020, SIRGAS...), que PROJ lui-meme traite comme equivalentes a ~1 m pres en horizontal et sans effet sensible en hauteur.
  Un SCR a datum local (Bessel, Clarke, NAD27...) n'a que la hauteur ellipsoidale.
"""

import json
import math
import os
import re
import sqlite3

from . import projtool

DATA = os.path.join(projtool.VENDOR, "data", "files.geojson")
OPS = os.path.join(projtool.VENDOR, "data", "grid_ops.json")      # operations PROJ de chaque grille (releve avec projinfo 9.8.1)
GRID_TYPE = "VERTICAL_OFFSET_GEOGRAPHIC_TO_VERTICAL"
ELLIPSOID_ID = "ellipsoid"
MODERN_GLOBAL = ("reseau geodesique francais 1993", "european terrestrial reference", "etrs89", "etrf", "world geodetic system 1984",
                 "wgs 84", "international terrestrial reference", "itrf", "sirgas", "geocentric datum of australia 2020", "gda2020",
                 "national spatial reference system 2011", "nad83(2011)", "new zealand geodetic datum 2000", "nzgd2000",
                 "japanese geodetic datum 2011", "jgd2011")

_db_cache = {}
_grids_cache = {}


def _db():
    path = os.path.join(projtool.VENDOR, "share", "proj", "proj.db")
    if path not in _db_cache:
        _db_cache[path] = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True, check_same_thread=False)
    return _db_cache[path]


def available():
    return os.path.isfile(os.path.join(projtool.VENDOR, "share", "proj", "proj.db")) and os.path.isfile(DATA)


# --------------------------------------------------------------------------------------------------
# Reperes
# --------------------------------------------------------------------------------------------------

def datum_family(name):
    """"global" pour les reperes geocentriques modernes equivalents, sinon le nom du datum lui-meme (aucune equivalence)."""
    low = (name or "").lower()
    return "global" if any(word in low for word in MODERN_GLOBAL) else low


def frame_of(code):
    """{"code2d", "datum", "name", "family"} du repere d'un CRS EPSG (projete ou geographique), ou None."""
    code = str(code).replace("EPSG:", "").replace("EPSG::", "")
    row = _db().execute(
        "select g.code, g.datum_code, d.name from projected_crs p join geodetic_crs g on g.auth_name=p.geodetic_crs_auth_name "
        "and g.code=p.geodetic_crs_code join geodetic_datum d on d.auth_name=g.datum_auth_name and d.code=g.datum_code "
        "where p.auth_name='EPSG' and p.code=?", (code,)).fetchone()
    if row is None:
        row = _db().execute("select g.code, g.datum_code, d.name from geodetic_crs g join geodetic_datum d on d.auth_name="
                            "g.datum_auth_name and d.code=g.datum_code where g.auth_name='EPSG' and g.code=?", (code,)).fetchone()
    if row is None:
        return None
    return {"code2d": row[0], "datum": row[1], "name": row[2], "family": datum_family(row[2])}


def geographic_epsg(horizontal_epsg):
    """Code du CRS geographique 2D du repere d'un SCR projete (celui ou l'on interroge les grilles) ; 4326 si inconnu."""
    frame = frame_of(horizontal_epsg) if available() else None
    try:
        return int(frame["code2d"]) if frame else 4326
    except (TypeError, ValueError):
        return 4326


# --------------------------------------------------------------------------------------------------
# Grilles d'altitude
# --------------------------------------------------------------------------------------------------

def _bbox(geometry):
    points = []

    def walk(part):
        if part and isinstance(part[0], (int, float)):
            points.append(part)
        else:
            for item in part or []:
                walk(item)
    walk((geometry or {}).get("coordinates"))
    if not points:
        return None
    return (min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points))


def grids():
    """Toutes les grilles d'altitude de PROJ-data : liste de fiches {id, file, url, size, sha256, bbox, ...}."""
    if "all" not in _grids_cache:
        out = []
        with open(DATA, encoding="utf-8") as handle:
            features = json.load(handle)["features"]
        try:
            with open(OPS, encoding="utf-8") as handle:
                ops = json.load(handle)
        except (OSError, ValueError):
            ops = {}
        for feature in features:
            props = feature["properties"]
            if props.get("type") != GRID_TYPE or props.get("version_removed") or not props.get("source_crs_code"):
                continue
            box = _bbox(feature.get("geometry"))
            if box is None:
                continue
            stem = os.path.splitext(props["name"])[0]
            short = stem[len(props["source_id"]) + 1:] if stem.startswith(props["source_id"] + "_") else stem
            target = props.get("target_crs_name") or props.get("target_crs_code") or ""
            area = props.get("area_of_use") or props.get("source_country") or ""
            label = "%s · %s · %s (%s)" % (target, short, area, props.get("source_crs_name") or props["source_crs_code"])
            try:
                vert_epsg = int(str(props.get("target_crs_code", "")).split(":")[-1])
            except ValueError:
                vert_epsg = None
            out.append({"id": stem, "file": props["name"], "url": props["url"], "size": int(props["file_size"]),
                        "sha256": props["sha256sum"], "bbox": box, "area": area, "country": props.get("source_country", ""),
                        "source": str(props["source_crs_code"]), "target": target, "vert_epsg": vert_epsg, "short": short,
                        "fr": label, "en": label, "added": _version(props.get("version_added")),
                        "depth": bool(ops.get(props["name"], {}).get("flip"))})
        _grids_cache["all"] = out
        _grids_cache["by_id"] = {item["id"]: item for item in out}
    return _grids_cache["all"]


def _version(text):
    try:
        return tuple(int(part) for part in str(text or "0").split("."))
    except ValueError:
        return (0,)


def grid_by_id(grid_id):
    grids()
    return _grids_cache["by_id"].get(grid_id)


def inside(box, lon, lat):
    west, south, east, north = box
    if not (south <= lat <= north):
        return False
    return west <= lon <= east if west <= east else (lon >= west or lon <= east)


def area_of(box):
    west, south, east, north = box
    return max(0.0, (east - west) * (north - south))


def match(frame, grid):
    """"exact" (meme datum), "equivalent" (autre realisation du meme repere geocentrique moderne) ou None. Voir l'en-tete."""
    if frame is None:
        return None
    source = frame_of(grid["source"])
    if source is None:
        return None
    if source["datum"] == frame["datum"]:
        return "exact"
    if frame["family"] == "global" and source["family"] == "global":
        return "equivalent"
    return None


def verticals_for(horizontal_epsg, lon=None, lat=None, max_size=None):
    """Grilles d'altitude utilisables avec ce SCR a cet endroit : les plus locales d'abord (une grille nationale avant EGM).

    Une grille d'un autre datum equivalent (RAC23, faite pour RGF93 v2b, avec un SCR RGF93 v1) n'est proposee que si aucune grille du
    datum exact ne donne la meme altitude (RAC09 donne deja NGF-IGN78) : on ne melange pas deux realisations quand le choix existe.
    `max_size` (octets) : ecarte les gros fichiers. Les grilles dont le CRS vertical est une profondeur (axe vers le bas) sont ecartees."""
    frame = frame_of(horizontal_epsg)
    exact, equivalent = [], []
    for grid in grids():
        if lon is not None and lat is not None and not inside(grid["bbox"], lon, lat):
            continue
        if max_size is not None and grid["size"] > max_size:
            continue
        if grid["depth"]:
            continue                      # surface comptee vers le bas (profondeur) : PROJ inverse l'axe vertical, pas une altitude
        kind = match(frame, grid)
        if kind == "exact":
            exact.append(grid)
        elif kind == "equivalent":
            equivalent.append(grid)
    covered = set(item["vert_epsg"] for item in exact)
    out = exact + [item for item in equivalent if item["vert_epsg"] not in covered]
    out.sort(key=lambda item: (area_of(item["bbox"]), [-n for n in item["added"]], item["id"]))
    return out


# --------------------------------------------------------------------------------------------------
# Systemes XY
# --------------------------------------------------------------------------------------------------

_BASE_SQL = (
    "select p.code, p.name, e.west_lon, e.south_lat, e.east_lon, e.north_lat, d.name from projected_crs p "
    "join geodetic_crs g on g.auth_name=p.geodetic_crs_auth_name and g.code=p.geodetic_crs_code "
    "join geodetic_datum d on d.auth_name=g.datum_auth_name and d.code=g.datum_code "
    "join usage u on u.object_table_name='projected_crs' and u.object_auth_name=p.auth_name and u.object_code=p.code "
    "join extent e on e.auth_name=u.extent_auth_name and e.code=u.extent_code "
    "where p.auth_name='EPSG' and p.deprecated=0 and e.deprecated=0")


def utm_zone(lon):
    return int(math.floor((lon + 180.0) / 6.0)) % 60 + 1


def candidates(lon=None, lat=None, text=None, limit=40):
    """[(code, nom)] de CRS projetes EPSG : sans texte, ceux qui couvrent (lon, lat) du plus precis au plus large, puis les zones UTM
    WGS 84 / ETRS89 du lieu ; avec texte, ceux dont le nom contient le texte ou dont le code est le nombre saisi."""
    db = _db()
    text = (text or "").strip()
    if text:
        digits = text.replace("EPSG:", "").strip()
        rows = db.execute(_BASE_SQL + " and (p.name like ? or p.code = ?) group by p.code order by p.name limit ?",
                          ("%" + text + "%", digits if digits.isdigit() else "-", limit * 3)).fetchall()
        if lon is not None and lat is not None:
            rows.sort(key=lambda r: (not inside((r[2], r[3], r[4], r[5]), lon, lat), datum_family(r[6]) != "global", r[1]))
        return [(int(r[0]), r[1]) for r in rows[:limit]]
    if lon is None or lat is None:
        return []
    rows = db.execute(_BASE_SQL + " and e.south_lat<=? and e.north_lat>=?", (lat, lat)).fetchall()
    found = {}
    for code, name, west, south, east, north, datum in rows:
        if inside((west, south, east, north), lon, lat):
            size = area_of((west, south, east, north)) if west <= east else 1e9
            rank = (datum_family(datum) != "global", size)           # reperes modernes d'abord, puis du plus precis au plus large
            if code not in found or rank < found[code][0]:
                found[code] = (rank, name)
    ranked = sorted(found.items(), key=lambda item: (item[1][0], item[1][1]))
    zone = utm_zone(lon)
    pinned = [(32600 + zone if lat >= 0 else 32700 + zone), (25800 + zone if 28 <= zone <= 38 and lat >= 0 else None)]
    out, seen = [], set()
    for code, (_rank, name) in ranked[:max(1, limit - 2)]:
        out.append((int(code), name))
        seen.add(str(code))
    for code in pinned:
        if code is None or str(code) in seen:
            continue
        row = db.execute("select name from projected_crs where auth_name='EPSG' and code=? and deprecated=0", (str(code),)).fetchone()
        if row:
            out.append((code, row[0]))
    return out
