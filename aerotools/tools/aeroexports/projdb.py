"""Aero-Exports: the PROJ database (proj.db, EPSG + IGNF + others, read-only) and the index of the PROJ-data grids.

Answers: "which coordinate systems to offer here?" and "which altitudes (geoid grids) go with this one, here?",
anywhere in the world, without a hand-written table.

* XY systems: the non-deprecated EPSG projected CRS whose area of use contains the project, the most precise first, plus the
  UTM WGS 84 zone (and ETRS89 in Europe); free search by name or by code.
* Altitudes: the `VERTICAL_OFFSET_GEOGRAPHIC_TO_VERTICAL` grids of PROJ-data (embedded `files.geojson` index: URL, size, SHA-256,
  extent, source and target CRS) that cover the place AND whose source frame is the same as that of the chosen CRS.
  Why this condition: Metashape exports ellipsoidal heights in the frame of the chosen CRS; subtracting the grid of another
  frame (an ETRS89 grid on Bessel heights, for example) would give an altitude wrong by several metres. "Same" frame
  = same datum, or same modern geocentric family (ETRS89, WGS 84, ITRF and their national realisations, RGF93,
  GDA2020, SIRGAS...), which PROJ itself treats as equivalent to ~1 m horizontally and with no noticeable effect on height.
  A CRS with a local datum (Bessel, Clarke, NAD27...) only has the ellipsoidal height.
"""

import json
import math
import os
import re

from ... import projtool

DATA = os.path.join(projtool.VENDOR, "data", "files.geojson")
OPS = os.path.join(projtool.VENDOR, "data", "grid_ops.json")      # PROJ operations of each grid (taken with projinfo 9.8.1)
GRID_TYPE = "VERTICAL_OFFSET_GEOGRAPHIC_TO_VERTICAL"
ELLIPSOID_ID = "ellipsoid"
_grids_cache = {}


def available():
    return os.path.isfile(os.path.join(projtool.VENDOR, "share", "proj", "proj.db")) and os.path.isfile(DATA)


# --------------------------------------------------------------------------------------------------
# Height grids
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
    """All the height grids of PROJ-data: list of records {id, file, url, size, sha256, bbox, ...}."""
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
                        "label": label, "added": _version(props.get("version_added")),
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
    """"exact" (same datum), "equivalent" (another realisation of the same modern geocentric frame) or None. See the header."""
    if frame is None:
        return None
    source = projtool.frame_of(grid["source"])
    if source is None:
        return None
    if source["datum"] == frame["datum"]:
        return "exact"
    if frame["family"] == "global" and source["family"] == "global":
        return "equivalent"
    return None


def verticals_for(horizontal_epsg, lon=None, lat=None, max_size=None):
    """Height grids usable with this CRS at this place: the most local first (a national grid before EGM), the most recent first
    for a comparable extent.

    A grid of another equivalent datum (RAC23, made for RGF93 v2b, with an RGF93 v1 CRS) is only offered if no grid of the
    exact datum gives the same altitude (RAC09 already gives NGF-IGN78): two realisations are not mixed when there is a choice.
    `max_size` (bytes): leaves out large files. Grids whose vertical CRS is a depth (axis pointing down) are left out."""
    frame = projtool.frame_of(horizontal_epsg)
    exact, equivalent = [], []
    for grid in grids():
        if lon is not None and lat is not None and not inside(grid["bbox"], lon, lat):
            continue
        if max_size is not None and grid["size"] > max_size:
            continue
        if grid["depth"]:
            continue                      # surface counted downwards (depth): PROJ flips the vertical axis, not an altitude
        kind = match(frame, grid)
        if kind == "exact":
            exact.append(grid)
        elif kind == "equivalent":
            equivalent.append(grid)
    covered = set(item["vert_epsg"] for item in exact)
    out = exact + [item for item in equivalent if item["vert_epsg"] not in covered]
    out.sort(key=lambda item: (round(math.log(max(area_of(item["bbox"]), 1e-6), 2)), [-n for n in item["added"]], item["id"]))
    return out


# --------------------------------------------------------------------------------------------------
# XY systems
# --------------------------------------------------------------------------------------------------

_BASE_SQL = (
    "select p.code, p.name, e.west_lon, e.south_lat, e.east_lon, e.north_lat, d.name from projected_crs p "
    "join geodetic_crs g on g.auth_name=p.geodetic_crs_auth_name and g.code=p.geodetic_crs_code "
    "join geodetic_datum d on d.auth_name=g.datum_auth_name and d.code=g.datum_code "
    "join usage u on u.object_table_name='projected_crs' and u.object_auth_name=p.auth_name and u.object_code=p.code "
    "join extent e on e.auth_name=u.extent_auth_name and e.code=u.extent_code "
    "where p.auth_name='EPSG' and p.deprecated=0 and e.deprecated=0 "
    # metres only: a pixel or a tile typed in metres would be read in feet by Metashape (and the DXF says metres)
    "and not exists (select 1 from axis a where a.coordinate_system_auth_name=p.coordinate_system_auth_name "
    "and a.coordinate_system_code=p.coordinate_system_code and a.uom_code<>9001)")


METRE = 9001


def is_metric(code):
    """False when the EPSG projected CRS has an axis in another unit than the metre (feet, US survey feet...); True when it is metric
    or unknown (then nothing is blocked)."""
    try:
        rows = projtool.proj_db().execute(
            "select a.uom_code from projected_crs p join axis a on a.coordinate_system_auth_name=p.coordinate_system_auth_name "
            "and a.coordinate_system_code=p.coordinate_system_code where p.auth_name='EPSG' and p.code=?",
            (str(code).replace("EPSG:", "").strip(),)).fetchall()
    except Exception:
        return True
    return all(int(row[0]) == METRE for row in rows)


def utm_zone(lon):
    return int(math.floor((lon + 180.0) / 6.0)) % 60 + 1


def candidates(lon=None, lat=None, text=None, limit=40):
    """[(code, name)] of EPSG projected CRS: without text, those that cover (lon, lat) from the most precise to the widest, then the
    UTM WGS 84 / ETRS89 zones of the place; with text, those whose name contains the text or whose code is the number typed."""
    db = projtool.proj_db()
    text = (text or "").strip()
    if text:
        digits = text.replace("EPSG:", "").strip()
        rows = db.execute(_BASE_SQL + " and (p.name like ? or p.code = ?) group by p.code order by p.name limit ?",
                          ("%" + text + "%", digits if digits.isdigit() else "-", limit * 3)).fetchall()
        if lon is not None and lat is not None:
            rows.sort(key=lambda r: (not inside((r[2], r[3], r[4], r[5]), lon, lat), projtool.datum_family(r[6]) != "global", r[1]))
        return [(int(r[0]), r[1]) for r in rows[:limit]]
    if lon is None or lat is None:
        return []
    rows = db.execute(_BASE_SQL + " and e.south_lat<=? and e.north_lat>=?", (lat, lat)).fetchall()
    found = {}
    for code, name, west, south, east, north, datum in rows:
        if inside((west, south, east, north), lon, lat):
            size = area_of((west, south, east, north)) if west <= east else 1e9
            rank = (projtool.datum_family(datum) != "global", size)           # modern frames first, then from the most precise to the widest
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


_UNSUITABLE = ("(ft", "ftUS", "(N-E)", "grid system", "(E-N)", "Albers", "EASE-Grid")


def best_projected(lon, lat):
    """EPSG code of a metre-based projected system that suits the place (the most precise national or regional one, a UTM zone
    when the place has nothing else), or None. Systems in feet, with swapped axes or meant for continents are passed over."""
    if lon is None or lat is None or not available():
        return None
    try:
        rows = candidates(lon, lat)
    except Exception:
        return None
    for code, name in rows:
        if not any(mark in name for mark in _UNSUITABLE):
            return int(code)
    return int(rows[0][0]) if rows else None
