"""Aero-Exports: output file names (pure functions, no Qt, no Metashape).

Rule: ``<site>_<date>_<resolution>[_v<N>][_<tile>]_<type>.<ext>``

    SITEA_260704_3cm_ortho.tif        orthomosaic, 3 cm
    SITEA_260704_5cm_dtm.tif          DTM
    SITEA_260704_3cm_v2_ortho.ecw     second export on the same day
    SITEA_260704_3cm_R02C05_ortho.tif tile at row 2, column 5 (see tiling.py)

The type (_ortho / _dtm) is ALWAYS the end of the name: a tile, a version or a duplicate never
shifts it, and everything that is an orthomosaic is found with ``*_ortho.*``.

Python 3.9, no dependency.
"""

import datetime
import os
import re
import unicodedata
from collections import Counter

KINDS = {"ortho": "ortho", "dem": "dtm"}

# Files that accompany an export and count when detecting a duplicate.
AUX_EXTS = (".tfw", ".wld", ".jgw", ".prj", ".aux.xml", ".ovr", ".xml", ".dxf", ".dwg", ".ers", ".txt", ".csv")
KNOWN_EXTS = (".tif", ".tiff", ".ecw", ".jp2", ".jpg", ".jpeg", ".png", ".dxf", ".dwg", ".xyz", ".asc")

WINDOWS_FORBIDDEN = '<>:"/\\|?*'
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {"COM%d" % i for i in range(1, 10)} | {"LPT%d" % i for i in range(1, 10)}
# Recognition lists, not prose: generic folder names, as found in real project trees, that say nothing about
# the site; guess_site() skips them and looks higher up the path.
GENERIC_FOLDERS = {
    "metashape", "photogrammetry", "photogrammetrie", "photos", "projet", "projets", "project", "projects", "drone",
    "outputs", "output", "export", "exports", "livrable", "livrables", "1_livrable", "ortho", "mnt", "dtm", "dem",
    "data", "donnees", "travail", "work", "psx", "agisoft",
}
# Recognition list, not prose: generic words, as found in real project and file names, dropped from a file
# name when deriving the site name.
GENERIC_TOKENS = {
    "ortho", "orthomosaic", "orthophoto", "mnt", "dtm", "dem", "mns", "final", "finale", "copy", "copie", "test",
    "tmp", "temp", "new", "nouveau", "backup", "bak", "psx", "metashape", "project", "projet", "untitled",
}
MAX_PATH_SAFE = 240   # margin under Windows' 260 characters (the full path, folder included)


# ---------------------------------------------------------------------------
# Cleaning
# ---------------------------------------------------------------------------

def strip_accents(text):
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def sanitize_token(text, max_len=None):
    """Turn free text into a file name token: ASCII, letters / digits / hyphen / underscore.

    Spaces and punctuation become a single underscore; never a character forbidden on Windows,
    never a trailing dot or space, never a reserved name (CON, PRN, COM1...).
    """
    text = strip_accents(str(text or "")).replace("\u00df", "ss").replace("\u0153", "oe")
    text = re.sub(r"[^A-Za-z0-9\-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_-")
    if max_len and len(text) > max_len:
        text = text[:max_len].rstrip("_-")
    if text.upper() in WINDOWS_RESERVED:
        text = text + "_"
    return text


def is_valid_windows_name(name):
    """True if `name` (file name without folder) is acceptable on Windows."""
    if not name or name != name.rstrip(" ."):
        return False
    if any(ch in WINDOWS_FORBIDDEN or ord(ch) < 32 for ch in name):
        return False
    return name.split(".")[0].upper() not in WINDOWS_RESERVED


# ---------------------------------------------------------------------------
# Site
# ---------------------------------------------------------------------------

_DATE_LIKE = re.compile(r"^(?:\d{6}|\d{8}|\d{4}-\d{2}-\d{2}|\d{2}-\d{2}-\d{4}|v\d+|\d{1,2})$", re.I)


def guess_site(psx_path, max_len=16):
    """Propose a short site name from the .psx (then from the parent folder if the name is generic).

    SITEA_ProjectName.psx                       -> SITEA
    2026-07-04.psx in ...\\ClientA\\SiteA\\       -> SiteA
    The result is only a proposal: the user can correct it in the window.
    """
    stem = os.path.splitext(os.path.basename(psx_path or ""))[0]
    folder_names = []
    parent = os.path.dirname(os.path.abspath(psx_path)) if psx_path else ""
    while parent and len(folder_names) < 4:
        name = os.path.basename(parent)
        if not name:
            break
        folder_names.append(name)
        up = os.path.dirname(parent)
        if up == parent:
            break
        parent = up

    def tokens_of(text):
        raw = [t for t in re.split(r"[\s_]+", strip_accents(text)) if t]
        return [t for t in raw if not _DATE_LIKE.match(t) and t.lower() not in GENERIC_TOKENS]

    for candidate in [stem] + [n for n in folder_names if n.lower() not in GENERIC_FOLDERS]:
        tokens = tokens_of(candidate)
        while tokens and len(sanitize_token("_".join(tokens))) > max_len:
            tokens.pop()
        if tokens:
            return sanitize_token("_".join(tokens), max_len)
    return "SITE"


# ---------------------------------------------------------------------------
# Date
# ---------------------------------------------------------------------------

def parse_exif_date(text):
    """'2026:07:04 09:12:33' (or ISO) -> datetime.date, or None."""
    if not text:
        return None
    match = re.match(r"\s*(\d{4})[:\-/](\d{2})[:\-/](\d{2})", str(text))
    if not match:
        return None
    try:
        return datetime.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def flight_date(exif_dates, today=None):
    """Flight date: the most frequent day among the EXIF dates of the photos (robust to stray photos).

    Without a usable date: `today` (or the current date). Returns (date, source) with source 'exif' or 'today'.
    """
    days = [d for d in (parse_exif_date(x) for x in (exif_dates or [])) if d and d.year >= 2000]
    if days:
        best = Counter(days).most_common()
        top = max(count for _day, count in best)
        return min(day for day, count in best if count == top), "exif"
    return (today or datetime.date.today()), "today"


def format_date(date, style="short"):
    """short = YYMMDD (the shortest), long = YYYYMMDD, iso = YYYY-MM-DD."""
    if isinstance(date, str):
        date = parse_exif_date(date) or datetime.date.today()
    if style == "long":
        return date.strftime("%Y%m%d")
    if style == "iso":
        return date.strftime("%Y-%m-%d")
    return date.strftime("%y%m%d")


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def _half_up(value):
    """Round half up (2.5 -> 3), not the banker's rounding of round()."""
    return int(value + 0.5)


def format_resolution(pixel_m):
    """Pixel size in metres -> short text: 0.03 -> '3cm', 0.008 -> '8mm', 2.0 -> '2m'.

    The resolution in the NAME is rounded (0.0312871 gives '31mm'), the one of the FILE stays the one requested.
    Rules: whole metres >= 1 m -> '2m'; whole centimetres -> '5cm'; whole millimetres -> '8mm';
    otherwise round to the nearest mm (never a decimal separator in a file name).
    """
    if pixel_m is None or pixel_m <= 0:
        raise ValueError("invalid pixel size: %r" % (pixel_m,))
    cm = pixel_m * 100.0
    mm = pixel_m * 1000.0
    if pixel_m >= 1.0 and abs(pixel_m - round(pixel_m)) < 1e-9:
        return "%dm" % round(pixel_m)
    if abs(cm - round(cm)) < 0.02 and round(cm) >= 1:     # 0.0300 or 0.03000001 -> 3cm
        return "%dcm" % round(cm)
    if _half_up(mm) >= 1:
        return "%dmm" % _half_up(mm)
    return "%dum" % max(1, _half_up(pixel_m * 1e6))


# ---------------------------------------------------------------------------
# Building the name
# ---------------------------------------------------------------------------

def kind_label(kind):
    """'ortho' / 'dem' -> file name suffix: ortho or dtm."""
    return KINDS[kind]


def build_name(site, date, pixel_m, kind, ext, version=1, tile=None, date_style="short"):
    """Complete file name (without folder).

    `kind`: 'ortho' or 'dem'; `ext` with or without the dot; `version` > 1 adds _v<N>;
    `tile`: already formatted tile token (e.g. 'R02C05', see tiling.tile_name).
    """
    if kind not in ("ortho", "dem"):
        raise ValueError("kind must be 'ortho' or 'dem': %r" % (kind,))
    parts = [sanitize_token(site) or "SITE", format_date(date, date_style), format_resolution(pixel_m)]
    if version and int(version) > 1:
        parts.append("v%d" % int(version))
    if tile:
        parts.append(sanitize_token(tile))
    parts.append(kind_label(kind))
    ext = ext if ext.startswith(".") else "." + ext
    name = "_".join(parts) + ext.lower()
    if not is_valid_windows_name(name):
        raise ValueError("invalid name on Windows: %r" % (name,))
    return name


def stem_of(name):
    """Name without extension (handles .aux.xml)."""
    exts = sorted(AUX_EXTS + KNOWN_EXTS, key=len, reverse=True)
    changed = True
    while changed:
        changed = False
        for ext in exts:
            if name.lower().endswith(ext) and len(name) > len(ext):
                name = name[: -len(ext)]
                changed = True
                break
    return name


def next_free_version(folder, planned_names, listing=None):
    """Smallest N >= 1 such that no planned file (with _v<N>) already exists in `folder`.

    `planned_names`: function version -> list of names (all types, all extensions, auxiliary
    files included), so that the orthomosaic and the DTM of one export share the same _vN.
    `listing`: list of names already present (tests); otherwise os.listdir.
    """
    existing = set(n.lower() for n in (listing if listing is not None else (os.listdir(folder) if os.path.isdir(folder) else [])))
    for version in range(1, 1000):
        if not any(n.lower() in existing for n in planned_names(version)):
            return version
    raise RuntimeError("more than 999 versions of the same export")


def check_path_length(folder, names):
    """List the full paths that are too long for Windows (> MAX_PATH_SAFE)."""
    return [os.path.join(folder, n) for n in names if len(os.path.join(os.path.abspath(folder), n)) > MAX_PATH_SAFE]


def outputs_folder(psx_path):
    """Output folder: ``Outputs`` next to the .psx (not created here)."""
    return os.path.join(os.path.dirname(os.path.abspath(psx_path)), "Outputs")
