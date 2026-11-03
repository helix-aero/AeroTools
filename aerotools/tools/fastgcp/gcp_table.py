"""Lecture d'un fichier texte de GCP et attribution automatique des colonnes.

Sans Qt ni Metashape. Accepte un CSV, un TXT ou tout fichier texte en colonnes :
  - encodage : UTF-8 (avec ou sans BOM), sinon Windows-1252 ;
  - separateur : point-virgule, tabulation, virgule ou espaces ;
  - decimale : point ou virgule (« 1234,56 ») ;
  - lignes ignorees : vides et commentaires (#, //) ;
  - premiere ligne : en-tetes si elle ne contient aucun nombre.

`guess_columns` propose quelle colonne est le nom, X (est / longitude), Y (nord /
latitude) et Z (altitude), d'apres les en-tetes, sinon d'apres la forme des
donnees. L'utilisateur peut tout corriger : ce n'est qu'une proposition.
"""

import math
import re
import unicodedata

DELIMITERS = (";", "\t", ",", " ")
COMMENT_PREFIXES = ("#", "//")

NAME_WORDS = ("name", "nom", "id", "label", "point", "pt", "pts", "gcp", "marker",
              "marqueur", "numero", "num", "no", "designation", "code", "ref")
X_WORDS = ("x", "e", "east", "easting", "est", "lon", "long", "longitude", "lng")
Y_WORDS = ("y", "n", "north", "northing", "nord", "lat", "latitude")
Z_WORDS = ("z", "h", "alt", "altitude", "elevation", "elev", "hauteur", "height",
           "ngf", "cote")
FIELDS = ("name", "x", "y", "z")
_WORDS = {"name": NAME_WORDS, "x": X_WORDS, "y": Y_WORDS, "z": Z_WORDS}

# Part minimale de cellules numeriques pour qu'une colonne soit « de nombres ».
NUMERIC_SHARE = 0.8
SAMPLE_LINES = 40


# --------------------------------------------------------------------------
# Lecture
# --------------------------------------------------------------------------

def decode(data):
    """Texte d'un fichier lu en octets : UTF-8 (BOM compris), sinon Windows-1252."""
    if isinstance(data, str):
        return data
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def read_file(path):
    with open(path, "rb") as handle:
        return decode(handle.read())


def clean_lines(text):
    """[(numero de ligne, texte)] sans lignes vides ni commentaires."""
    out = []
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith(COMMENT_PREFIXES):
            continue
        out.append((number, stripped))
    return out


def split_line(line, delimiter):
    """Cellules d'une ligne. Espace : une suite d'espaces compte pour un seul."""
    if delimiter == " ":
        cells = line.split()
    else:
        cells = [cell for cell in _split_quoted(line, delimiter)]
    return [cell.strip().strip('"').strip() for cell in cells]


def _split_quoted(line, delimiter):
    cells, current, quoted = [], [], False
    for char in line:
        if char == '"':
            quoted = not quoted
            current.append(char)
        elif char == delimiter and not quoted:
            cells.append("".join(current))
            current = []
        else:
            current.append(char)
    cells.append("".join(current))
    return cells


def _commas_are_decimals(line):
    """Vrai pour « GCP1 1234,5 5678,9 12,3 » : des espaces dans les morceaux
    coupes a la virgule disent que la virgule est une decimale, pas un separateur."""
    return sum(1 for cell in split_line(line, ",") if " " in cell) >= 2


def detect_delimiter(lines):
    """Separateur le plus coherent sur les premieres lignes.

    Un separateur est retenu s'il donne au moins 3 colonnes (2 a defaut) et le
    meme nombre de colonnes sur presque toutes les lignes. Le point-virgule et
    la tabulation passent avant la virgule, qui sert aussi de decimale.
    """
    sample = [text for _number, text in lines[:SAMPLE_LINES]]
    if not sample:
        return ";"
    best = None
    for minimum in (3, 2):
        for delimiter in DELIMITERS:
            if delimiter == "," and _commas_are_decimals(sample[0]):
                continue
            counts = [len(split_line(text, delimiter)) for text in sample]
            modal = max(set(counts), key=counts.count)
            share = counts.count(modal) / float(len(counts))
            if modal >= minimum and share >= 0.8:
                score = (share, modal)
                if best is None or score > best[0]:
                    best = (score, delimiter)
                break
        if best is not None:
            return best[1]
    return ";"


def parse_number(text):
    """Nombre lu dans une cellule (decimale point ou virgule), ou None."""
    if text is None:
        return None
    value = str(text).strip().strip('"').replace(" ", "").replace(" ", "")
    if not value:
        return None
    if "," in value and "." in value:
        # Le dernier separateur est la decimale, l'autre separe les milliers.
        if value.rfind(",") > value.rfind("."):
            value = value.replace(".", "").replace(",", ".")
        else:
            value = value.replace(",", "")
    elif value.count(",") == 1:
        value = value.replace(",", ".")
    if not re.fullmatch(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?", value):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


class Table(object):
    """Fichier lu : lignes de cellules, avec leur numero de ligne dans le fichier."""

    def __init__(self, rows, delimiter, has_header):
        self.rows = rows            # [(numero de ligne, [cellules])]
        self.delimiter = delimiter
        self.has_header = has_header

    @property
    def header(self):
        return self.rows[0][1] if self.has_header and self.rows else []

    @property
    def body(self):
        return self.rows[1:] if self.has_header else self.rows

    @property
    def width(self):
        return max((len(cells) for _n, cells in self.rows), default=0)


def detect_header(rows):
    """Vrai si la premiere ligne n'a aucun nombre alors que les suivantes en ont."""
    if len(rows) < 2:
        return False
    first_numbers = sum(1 for cell in rows[0][1] if parse_number(cell) is not None)
    if first_numbers:
        return False
    later = rows[1:SAMPLE_LINES]
    return any(sum(1 for cell in cells if parse_number(cell) is not None) >= 2
               for _n, cells in later)


def parse_text(text, delimiter=None, has_header=None):
    """Lit un texte. `delimiter` et `has_header` None : detection automatique."""
    lines = clean_lines(text)
    if delimiter is None:
        delimiter = detect_delimiter(lines)
    rows = [(number, split_line(line, delimiter)) for number, line in lines]
    if has_header is None:
        has_header = detect_header(rows)
    return Table(rows, delimiter, bool(has_header))


# --------------------------------------------------------------------------
# Attribution des colonnes
# --------------------------------------------------------------------------

def _token(title):
    """Premier mot d'un en-tete, en minuscules sans accents : « X (m) » -> « x »."""
    text = unicodedata.normalize("NFKD", str(title))
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    match = re.match(r"[a-z]+", text.strip())
    return match.group(0) if match else ""


def _numeric_share(table, column):
    cells = [cells[column] for _n, cells in table.body[:SAMPLE_LINES] if column < len(cells)]
    if not cells:
        return 0.0
    return sum(1 for c in cells if parse_number(c) is not None) / float(len(cells))


def guess_columns(table):
    """Colonne (indice, ou None) de chaque champ : {"name", "x", "y", "z"}."""
    width = table.width
    mapping = {field: None for field in FIELDS}
    taken = set()

    if table.has_header:
        for column, title in enumerate(table.header):
            word = _token(title)
            for field in FIELDS:
                if mapping[field] is None and word in _WORDS[field]:
                    mapping[field] = column
                    taken.add(column)
                    break

    numeric = [c for c in range(width) if _numeric_share(table, c) >= NUMERIC_SHARE]
    if mapping["name"] is None:
        text_columns = [c for c in range(width)
                        if c not in taken and c not in numeric]
        if text_columns:
            mapping["name"] = text_columns[0]
            taken.add(text_columns[0])
        elif numeric and mapping["x"] is None and width >= 4:
            # Numero de point purement numerique : 4 colonnes de nombres, la premiere est le nom.
            mapping["name"] = numeric[0]
            taken.add(numeric[0])
    free = [c for c in numeric if c not in taken]
    for field in ("x", "y", "z"):
        if mapping[field] is None and free:
            mapping[field] = free.pop(0)
            taken.add(mapping[field])
    return mapping


def column_choices(table):
    """Libelles des colonnes pour une liste deroulante : « 1 · GCP1 », « 2 · X »..."""
    out = []
    first = table.body[0][1] if table.body else []
    for column in range(table.width):
        if table.has_header and column < len(table.header) and table.header[column]:
            sample = table.header[column]
        elif column < len(first):
            sample = first[column]
        else:
            sample = ""
        out.append("{} · {}".format(column + 1, sample).rstrip(" ·"))
    return out


# --------------------------------------------------------------------------
# Enregistrements
# --------------------------------------------------------------------------

def build_records(table, mapping):
    """Points lus : ([{"label", "x", "y", "z", "line"}], [(ligne, motif)]).

    Il faut au minimum les colonnes X et Y. Sans colonne nom, les points sont
    nommes GCP1, GCP2... Sans colonne Z, `z` vaut None. Un nom repete garde la
    derniere ligne. Les motifs de rejet sont des codes courts : « x », « y »,
    « z », « name », « columns » (voir `reject_text` dans gcp_import).
    """
    records, rejected = [], []
    if mapping.get("x") is None or mapping.get("y") is None:
        return records, [(0, "columns")]
    seen = {}
    for number, cells in table.body:
        def cell(field):
            column = mapping.get(field)
            if column is None or column >= len(cells):
                return None
            return cells[column]

        x, y = parse_number(cell("x")), parse_number(cell("y"))
        if x is None:
            rejected.append((number, "x"))
            continue
        if y is None:
            rejected.append((number, "y"))
            continue
        z = parse_number(cell("z"))
        if mapping.get("z") is not None and z is None:
            rejected.append((number, "z"))
            continue
        if mapping.get("name") is None:
            label = "GCP{}".format(len(records) + 1)
        else:
            label = (cell("name") or "").strip()
            if not label:
                rejected.append((number, "name"))
                continue
        record = {"label": label, "x": x, "y": y, "z": z, "line": number}
        if label in seen:
            records[seen[label]] = record
        else:
            seen[label] = len(records)
            records.append(record)
    return records, rejected


def looks_geographic(records):
    """Vrai si les valeurs ressemblent a des degres (|X| <= 180 et |Y| <= 90)."""
    if not records:
        return False
    return all(abs(r["x"]) <= 180 and abs(r["y"]) <= 90 for r in records)
