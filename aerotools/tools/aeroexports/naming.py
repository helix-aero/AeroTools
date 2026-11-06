"""Aero-Exports : noms de fichiers de sortie (fonctions pures, sans Qt ni Metashape).

Regle : ``<site>_<date>_<resolution>[_v<N>][_<tuile>]_<type>.<ext>``

    CITADELLE_260704_3cm_ortho.tif        ortho, 3 cm
    CITADELLE_260704_5cm_mnt.tif          MNT   (_dtm si l'interface est en anglais)
    CITADELLE_260704_3cm_v2_ortho.ecw     deuxieme export le meme jour
    CITADELLE_260704_3cm_R02C05_ortho.tif dalle ligne 2, colonne 5 (voir tiling.py)

Le type (_ortho / _mnt) est TOUJOURS la fin du nom : une dalle, une version ou un doublon ne le
decale jamais, et on retrouve tout ce qui est ortho avec ``*_ortho.*``.

Python 3.9, aucune dependance.
"""

import datetime
import os
import re
import unicodedata
from collections import Counter

KINDS = {
    "fr": {"ortho": "ortho", "dem": "mnt"},
    "en": {"ortho": "ortho", "dem": "dtm"},
}

# Fichiers qui accompagnent un export et qui comptent pour detecter un doublon.
AUX_EXTS = (".tfw", ".wld", ".jgw", ".prj", ".aux.xml", ".ovr", ".xml", ".dxf", ".dwg", ".ers", ".txt", ".csv")
KNOWN_EXTS = (".tif", ".tiff", ".ecw", ".jp2", ".jpg", ".jpeg", ".png", ".dxf", ".dwg", ".xyz", ".asc")

WINDOWS_FORBIDDEN = '<>:"/\\|?*'
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {"COM%d" % i for i in range(1, 10)} | {"LPT%d" % i for i in range(1, 10)}
GENERIC_FOLDERS = {
    "metashape", "photogrammetry", "photogrammetrie", "photos", "projet", "projets", "project", "projects", "drone",
    "outputs", "output", "export", "exports", "livrable", "livrables", "1_livrable", "ortho", "mnt", "dtm", "dem",
    "data", "donnees", "travail", "work", "psx", "agisoft",
}
GENERIC_TOKENS = {
    "ortho", "orthomosaic", "orthophoto", "mnt", "dtm", "dem", "mns", "final", "finale", "copy", "copie", "test",
    "tmp", "temp", "new", "nouveau", "backup", "bak", "psx", "metashape", "project", "projet", "untitled",
}
MAX_PATH_SAFE = 240   # marge sous les 260 caracteres de Windows (le chemin complet, dossier compris)


# ---------------------------------------------------------------------------
# Nettoyage
# ---------------------------------------------------------------------------

def strip_accents(text):
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def sanitize_token(text, max_len=None):
    """Transforme un texte libre en jeton de nom de fichier : ASCII, lettres / chiffres / tiret / souligne.

    Les espaces et ponctuations deviennent un souligne unique ; jamais de caractere interdit sous Windows,
    jamais de point ou d'espace final, jamais de nom reserve (CON, PRN, COM1...).
    """
    text = strip_accents(str(text or "")).replace("ß", "ss").replace("œ", "oe")
    text = re.sub(r"[^A-Za-z0-9\-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_-")
    if max_len and len(text) > max_len:
        text = text[:max_len].rstrip("_-")
    if text.upper() in WINDOWS_RESERVED:
        text = text + "_"
    return text


def is_valid_windows_name(name):
    """True si `name` (nom de fichier sans dossier) est acceptable sous Windows."""
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
    """Propose un nom de site court a partir du .psx (puis du dossier parent si le nom est generique).

    CITADELLE_Pompeani.psx                      -> CITADELLE
    2026-07-04.psx dans ...\\Bouygues\\EDF\\     -> EDF
    Le resultat n'est qu'une proposition : l'utilisateur peut la corriger dans la fenetre.
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
    """'2026:07:04 09:12:33' (ou ISO) -> datetime.date, ou None."""
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
    """Date de vol : le jour le plus frequent parmi les dates EXIF des photos (robuste aux photos aberrantes).

    Sans date exploitable : `today` (ou la date du jour). Renvoie (date, source) avec source 'exif' ou 'today'.
    """
    days = [d for d in (parse_exif_date(x) for x in (exif_dates or [])) if d and d.year >= 2000]
    if days:
        best = Counter(days).most_common()
        top = max(count for _day, count in best)
        return min(day for day, count in best if count == top), "exif"
    return (today or datetime.date.today()), "today"


def format_date(date, style="short"):
    """short = YYMMDD (le plus court), long = YYYYMMDD, iso = YYYY-MM-DD."""
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
    """Arrondi 'commercial' (2.5 -> 3), pas l'arrondi bancaire de round()."""
    return int(value + 0.5)


def format_resolution(pixel_m):
    """Taille de pixel en metres -> texte court : 0.03 -> '3cm', 0.008 -> '8mm', 2.0 -> '2m'.

    La resolution du NOM est arrondie (0.0312871 donne '31mm'), celle du FICHIER reste celle demandee.
    Regles : >= 1 m entier -> '2m' ; multiple entier de cm -> '5cm' ; multiple entier de mm -> '8mm' ;
    sinon on arrondit au mm le plus proche (jamais de virgule dans un nom de fichier).
    """
    if pixel_m is None or pixel_m <= 0:
        raise ValueError("taille de pixel invalide : %r" % (pixel_m,))
    cm = pixel_m * 100.0
    mm = pixel_m * 1000.0
    if pixel_m >= 1.0 and abs(pixel_m - round(pixel_m)) < 1e-9:
        return "%dm" % round(pixel_m)
    if abs(cm - round(cm)) < 0.02 and round(cm) >= 1:     # 0.0300 ou 0.03000001 -> 3cm
        return "%dcm" % round(cm)
    if _half_up(mm) >= 1:
        return "%dmm" % _half_up(mm)
    return "%dum" % max(1, _half_up(pixel_m * 1e6))


# ---------------------------------------------------------------------------
# Construction du nom
# ---------------------------------------------------------------------------

def kind_label(kind, lang="fr"):
    """'ortho' / 'dem' -> suffixe : ortho, mnt (fr) ou dtm (en)."""
    return KINDS.get(lang, KINDS["fr"])[kind]


def build_name(site, date, pixel_m, kind, ext, lang="fr", version=1, tile=None, date_style="short"):
    """Nom de fichier complet (sans dossier).

    `kind` : 'ortho' ou 'dem' ; `ext` avec ou sans point ; `version` > 1 ajoute _v<N> ;
    `tile` : jeton de dalle deja formate (ex. 'R02C05', voir tiling.tile_name).
    """
    if kind not in ("ortho", "dem"):
        raise ValueError("kind doit etre 'ortho' ou 'dem' : %r" % (kind,))
    parts = [sanitize_token(site) or "SITE", format_date(date, date_style), format_resolution(pixel_m)]
    if version and int(version) > 1:
        parts.append("v%d" % int(version))
    if tile:
        parts.append(sanitize_token(tile))
    parts.append(kind_label(kind, lang))
    ext = ext if ext.startswith(".") else "." + ext
    name = "_".join(parts) + ext.lower()
    if not is_valid_windows_name(name):
        raise ValueError("nom invalide sous Windows : %r" % (name,))
    return name


def stem_of(name):
    """Nom sans extension (gere .aux.xml)."""
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
    """Plus petit N >= 1 tel qu'aucun fichier prevu (avec _v<N>) n'existe deja dans `folder`.

    `planned_names` : fonction version -> liste de noms (tous types, toutes extensions, y compris
    fichiers auxiliaires), pour que l'ortho et le MNT d'un meme export partagent le meme _vN.
    `listing` : liste de noms deja presents (tests) ; sinon os.listdir.
    """
    existing = set(n.lower() for n in (listing if listing is not None else (os.listdir(folder) if os.path.isdir(folder) else [])))
    for version in range(1, 1000):
        if not any(n.lower() in existing for n in planned_names(version)):
            return version
    raise RuntimeError("plus de 999 versions du meme export")


def check_path_length(folder, names):
    """Liste les chemins complets trop longs pour Windows (> MAX_PATH_SAFE)."""
    return [os.path.join(folder, n) for n in names if len(os.path.join(os.path.abspath(folder), n)) > MAX_PATH_SAFE]


def outputs_folder(psx_path):
    """Dossier de sortie : ``Outputs`` a cote du .psx (non cree ici)."""
    return os.path.join(os.path.dirname(os.path.abspath(psx_path)), "Outputs")
