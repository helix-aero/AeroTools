"""Rapport PDF et export CSV des GCP du chunk courant (FastGCP).

Trois couches, separees pour pouvoir tester la premiere sans Qt :

  collect_data(chunk)        lit le chunk et calcule les statistiques (pur Python)
  collect_images(state, d)   prend les captures d'ecran (Qt, dans Metashape)
  build_pdf(data, path, ..)  peint le PDF avec QPdfWriter et QPainter

`export_pdf(state)` et `export_csv(state)` sont les points d'entree du panneau :
ils demandent le fichier, ecrivent, journalisent, et ne levent jamais.

Choix de rendu :
  - A4 a 300 dpi, tout est peint en vectoriel (graphiques compris) ;
  - les polices variables Rubik et Manrope ne gardent pas leurs graisses dans
    un PDF Qt (Qt5 n'expose que l'instance par defaut, Qt6 l'embarque sans ses
    variations). Les titres et les chiffres cles sont donc peints en contours
    (QPainterPath), la graisse venant de la police si Qt la fournit, sinon d'un
    leger trait de contour. Le texte courant (tableau, legendes) reste du vrai
    texte, selectionnable, en Segoe UI (repli : police sans empattement du poste) ;
  - jamais de dependance hors Qt.
"""

import csv
import datetime
import math
import os
import re
import unicodedata

from ... import i18n, journal, msapi, theme
from ...qt import QtCore, QtGui, QtWidgets
from . import review

# --------------------------------------------------------------------------
# Constantes
# --------------------------------------------------------------------------

DPI = 300
PT_TO_DEVICE = DPI / 72.0           # le peintre travaille en points (1/72 pouce)
MARGIN = 40.0
BAR_LIMIT = 20                      # barres du graphique des erreurs
THRESHOLD_FACTOR = 2.0              # mise en evidence : valeur > 2 x RMS
MIN_FOR_THRESHOLD = 3               # pas de seuil avec moins de 3 mesures
CROP_W, CROP_H = 330, 220           # vignette autour de la projection, en pixels image
CROP_ZOOM = 3                       # agrandissement de la vignette
ROW_H = 15.0                        # hauteur d'une ligne du tableau
TEXT_SCALE = 10                     # le texte est dessine a 10x puis reduit

# Palette du rapport : toujours claire, quel que soit le theme du panneau.
INK = "#111214"
INK_2 = "#2b2e33"
MUTED = "#5d6067"
FAINT = "#8c8e96"
PAPER = "#f6f6f4"
CARD = "#ffffff"
LINE = "#dcdcd6"
GRID = "#ebebe6"
WARN_BG = "#fde3d8"
WARN_TEXT = "#b83504"
GREEN = theme.GREEN
GREEN_DEEP = theme.GREEN_DEEP
ORANGE = theme.ORANGE

EM_DASH = "—"
MINUS = "−"
DOT = "·"


# --------------------------------------------------------------------------
# Formats
# --------------------------------------------------------------------------

def fmt_num(value, decimals=3):
    """Nombre dans la langue du rapport (virgule en francais, point en anglais).

    Tiret cadratin si la valeur est absente ou illisible. La langue est lue a
    chaque appel : celle qui est choisie au moment de l'export.
    """
    return i18n.fmt_number(value, decimals) or EM_DASH


def fmt_m(value, decimals=3):
    """« 0,123 m » ou un tiret."""
    text = fmt_num(value, decimals)
    return text if text == EM_DASH else text + " m"


def _typo(text):
    """Moins typographique pour le texte courant."""
    return MINUS + text[1:] if text and text[0] == "-" else text


def safe_name(text, default="Chunk"):
    """Nom de fichier sur : ASCII, sans espaces ni caracteres speciaux."""
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("._-")
    text = re.sub(r"_+", "_", text)
    return text[:60] or default


def suggested_filename(chunk_label, ext=".pdf", today=None, kind=None):
    """`AAAA-MM-JJ_Rapport_GCP_<chunk>.pdf`, ou `YYYY-MM-DD_GCP_report_<chunk>.pdf` en anglais.

    `kind` : mot du nom (par defaut, celui du rapport dans la langue choisie).
    """
    day = today or datetime.date.today()
    if kind is None:
        kind = i18n.t("file.report")
    return "{}_{}_{}{}".format(day.strftime("%Y-%m-%d"), kind, safe_name(chunk_label), ext)


def project_path():
    """Chemin du projet .psx ouvert, ou chaine vide."""
    try:
        return str(getattr(msapi.document(), "path", "") or "")
    except Exception:
        return ""


def project_directory():
    """Dossier du projet .psx s'il existe, sinon None."""
    path = project_path()
    if path:
        folder = os.path.dirname(path)
        if folder and os.path.isdir(folder):
            return folder
    return None


def default_directory():
    """Dossier propose a l'enregistrement : projet, sinon Documents, sinon base."""
    folder = project_directory()
    if folder:
        return folder
    try:
        docs = QtCore.QStandardPaths.writableLocation(
            QtCore.QStandardPaths.DocumentsLocation)
        if docs and os.path.isdir(docs):
            return docs
    except Exception:
        pass
    return os.path.expanduser("~")


# --------------------------------------------------------------------------
# Donnees (sans Qt)
# --------------------------------------------------------------------------

def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) or math.isinf(number) else number


def _fallback_errors(chk, marker):
    """Erreurs d'un marqueur si msapi.marker_errors n'existe pas (encore).

    Methode de l'exemple officiel de l'API Metashape : estimee moins reference,
    ramenee dans le repere local (est, nord, haut) au point du marqueur.
    """
    try:
        reference = marker.reference.location
        position = marker.position
        if reference is None or position is None:
            return None
        crs, matrix = chk.crs, chk.transform.matrix
        if crs is None or matrix is None:
            return None
        world = matrix.mulp(position)
        estimated = crs.unproject(crs.project(world))
        delta = estimated - crs.unproject(reference)
        local = crs.localframe(world).mulv(delta)
        x, y, z = local[0], local[1], local[2]
        return {"x": x, "y": y, "z": z, "total": math.sqrt(x * x + y * y + z * z)}
    except Exception:
        return None


def _marker_errors(chk, marker):
    """{"x","y","z","total"} en metres, valeurs None si inconnues. Ne leve pas."""
    reader = getattr(msapi, "marker_errors", None)
    try:
        raw = reader(chk, marker) if reader is not None else _fallback_errors(chk, marker)
    except Exception:
        raw = None
    raw = raw or {}
    out = {key: _finite(raw.get(key)) for key in ("x", "y", "z", "total")}
    if out["total"] is None and None not in (out["x"], out["y"], out["z"]):
        out["total"] = math.sqrt(out["x"] ** 2 + out["y"] ** 2 + out["z"] ** 2)
    return out


def _reference_xyz(marker):
    """Coordonnees de reference (x, y, z) du marqueur, ou None."""
    try:
        location = marker.reference.location
        if location is None:
            return None
        try:
            values = (location.x, location.y, location.z)
        except AttributeError:
            values = (location[0], location[1], location[2])
        values = tuple(_finite(v) for v in values)
        return None if None in values else values
    except Exception:
        return None


def _rms(values):
    values = [v for v in values if v is not None]
    if not values:
        return None
    return math.sqrt(sum(v * v for v in values) / len(values))


def _median(values):
    ordered = sorted(values)
    if not ordered:
        return None
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _meta():
    path = project_path()
    return {
        "project": os.path.basename(path) if path else i18n.t("report.no_project"),
        "project_path": path,
        "metashape": msapi.app_version(),
    }


def collect_data(chunk, today=None):
    """Lit les marqueurs du chunk. Pur Python, aucun appel Qt.

    Renvoie {"chunk": nom, "date": date, "meta": {...}, "markers": [...],
    "stats": {...}}. Un marqueur : label, x, y, z, total (m, ou None),
    projections (int), pointed (bool), ref ((x, y, z) ou None).
    """
    markers = []
    for marker in msapi.markers(chunk):
        errors = _marker_errors(chunk, marker)
        try:
            count = int(msapi.projection_count(marker))
        except Exception:
            count = 0
        markers.append({
            "label": msapi.marker_label(marker),
            "x": errors["x"], "y": errors["y"], "z": errors["z"],
            "total": errors["total"],
            "projections": count,
            "pointed": count > 0,
            "ref": _reference_xyz(marker),
        })

    totals = [m["total"] for m in markers if m["total"] is not None]
    rms = {axis: _rms([m[axis] for m in markers]) for axis in ("x", "y", "z", "total")}
    rms_xy = None
    if rms["x"] is not None and rms["y"] is not None:
        rms_xy = math.sqrt(rms["x"] ** 2 + rms["y"] ** 2)

    worst = None
    for m in markers:
        if m["total"] is not None and (worst is None or m["total"] > worst["total"]):
            worst = m
    counts = [m["projections"] for m in markers]

    def threshold(value):
        if value is None or len(totals) < MIN_FOR_THRESHOLD or value <= 0:
            return None
        return THRESHOLD_FACTOR * value

    stats = {
        "n_markers": len(markers),
        "n_pointed": sum(1 for m in markers if m["pointed"]),
        "n_with_error": len(totals),
        "rms_x": rms["x"], "rms_y": rms["y"], "rms_z": rms["z"],
        "rms_xy": rms_xy, "rms_total": rms["total"],
        "worst_label": worst["label"] if worst else None,
        "worst_total": worst["total"] if worst else None,
        "total_projections": sum(counts),
        "mean_projections": (sum(counts) / float(len(counts))) if counts else None,
        "median_projections": _median(counts),
        "min_projections": min(counts) if counts else None,
        "max_projections": max(counts) if counts else None,
        "n_low_projections": sum(1 for c in counts if 0 < c < 3),
        "threshold_x": threshold(rms["x"]), "threshold_y": threshold(rms["y"]),
        "threshold_z": threshold(rms["z"]), "threshold_total": threshold(rms["total"]),
    }
    return {
        "chunk": msapi.chunk_label(chunk),
        "date": today or datetime.date.today(),
        "meta": _meta(),
        "markers": markers,
        "stats": stats,
    }


def over_threshold(value, threshold):
    """True si |value| depasse le seuil (seuil None : jamais)."""
    return value is not None and threshold is not None and abs(value) > threshold


def projection_bins(counts, max_bins=12):
    """Regroupe les comptes de projections pour l'histogramme.

    Renvoie [(libelle, effectif, contient_zero)]. Un groupe par valeur tant que
    le maximum est petit, sinon des groupes de largeur egale.
    """
    if not counts:
        return []
    top = max(counts)
    width = max(1, int(math.ceil((top + 1) / float(max_bins))))
    bins = []
    start = 0
    while start <= top:
        end = start + width - 1
        n = sum(1 for c in counts if start <= c <= end)
        label = str(start) if width == 1 else "{}-{}".format(start, min(end, top))
        bins.append((label, n, start == 0))
        start += width
    return bins


# --------------------------------------------------------------------------
# CSV
# --------------------------------------------------------------------------

def csv_header(with_reference):
    head = [i18n.t(key) for key in ("csv.id", "csv.x", "csv.y", "csv.z",
                                    "csv.total", "csv.projections")]
    if with_reference:
        head += [i18n.t(key) for key in ("csv.ref_x", "csv.ref_y", "csv.ref_z")]
    return head


def _csv_num(value):
    """Nombre du CSV : vide si inconnu (Excel lit mieux une cellule vide qu'un tiret)."""
    return "" if value is None else fmt_num(value)


def csv_rows(data):
    """Lignes du CSV (listes de textes), en-tete compris."""
    markers = data["markers"]
    with_ref = any(m.get("ref") for m in markers)
    rows = [csv_header(with_ref)]
    for m in markers:
        row = [m["label"], _csv_num(m["x"]), _csv_num(m["y"]), _csv_num(m["z"]),
               _csv_num(m["total"]), str(m["projections"])]
        if with_ref:
            ref = m.get("ref")
            if ref:
                geographic = max(abs(ref[0]), abs(ref[1])) <= 360.0
                row += [fmt_num(ref[0], 8 if geographic else 3),
                        fmt_num(ref[1], 8 if geographic else 3), fmt_num(ref[2], 3)]
            else:
                row += ["", "", ""]
        rows.append(row)
    return rows


def write_csv(data, path):
    """Ecrit le CSV, UTF-8 avec BOM (Excel), dans la langue choisie.

    Francais : point-virgule et virgule decimale. Anglais : virgule et point.
    """
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle, delimiter=i18n.csv_delimiter(), lineterminator="\r\n")
        for row in csv_rows(data):
            writer.writerow(row)
    return path


# --------------------------------------------------------------------------
# Outils de dessin
# --------------------------------------------------------------------------

def _font_families():
    try:
        return set(QtGui.QFontDatabase.families())
    except Exception:
        pass
    try:
        return set(QtGui.QFontDatabase().families())
    except Exception:
        return set()


def _font_styles(family):
    try:
        return list(QtGui.QFontDatabase.styles(family))
    except Exception:
        pass
    try:
        return list(QtGui.QFontDatabase().styles(family))
    except Exception:
        return []


class _Style(object):
    """Polices du rapport. Une instance par PDF."""

    # Graisses : 0 normal, 1 moyen, 2 demi-gras, 3 gras.
    FAUX_STROKE = (0.010, 0.020, 0.032, 0.044)   # x taille, si Qt n'a qu'une graisse

    def __init__(self):
        try:
            title_family, _text_family = theme.load_fonts(QtGui)
        except Exception:
            title_family = "Segoe UI"
        self.display_family = title_family
        self.real_weights = len(_font_styles("Rubik")) > 1
        families = _font_families()
        self.body_family = "Segoe UI"
        for name in ("Segoe UI", "Helvetica Neue", "Arial", "DejaVu Sans"):
            if name in families:
                self.body_family = name
                break
        self._cache = {}

    def body_font(self, px, bold=False):
        font = QtGui.QFont(self.body_family)
        font.setPixelSize(max(1, int(round(px * TEXT_SCALE))))
        font.setBold(bool(bold))
        font.setHintingPreference(QtGui.QFont.PreferNoHinting)
        return font

    def display_font(self, px, weight):
        """(police, epaisseur de trait en points)."""
        font = QtGui.QFont(self.display_family)
        font.setPixelSize(max(1, int(round(px * TEXT_SCALE))))
        font.setHintingPreference(QtGui.QFont.PreferNoHinting)
        weight = max(0, min(3, int(weight)))
        if self.real_weights:
            # Rubik reel : on reste un cran sous les noms pour rester proche du rendu Qt5.
            font.setWeight((QtGui.QFont.Normal, QtGui.QFont.Medium,
                            QtGui.QFont.Medium, QtGui.QFont.DemiBold)[weight])
            return font, 0.0
        font.setWeight(QtGui.QFont.Normal)
        return font, px * self.FAUX_STROKE[weight]

    def advance(self, text, px, kind, weight):
        key = (text, round(px, 2), kind, weight)
        value = self._cache.get(key)
        if value is None:
            if kind == "display":
                font, _stroke = self.display_font(px, weight)
            else:
                font = self.body_font(px, weight >= 2)
            metrics = QtGui.QFontMetricsF(font)
            getter = getattr(metrics, "horizontalAdvance", None) or metrics.width
            value = getter(text) / float(TEXT_SCALE)
            self._cache[key] = value
        return value


def _qcolor(color, alpha=1.0):
    result = QtGui.QColor(color)
    if alpha < 1.0:
        result.setAlphaF(alpha)
    return result


def _nice_top(value, divisions=4):
    """(sommet, pas) d'un axe qui part de 0 et couvre `value`."""
    if value is None or value <= 0:
        return 1.0, 0.25
    raw = value / float(divisions)
    magnitude = 10 ** math.floor(math.log10(raw))
    step = magnitude * 10
    for factor in (1, 2, 2.5, 5, 10):
        if factor * magnitude >= raw - 1e-12:
            step = factor * magnitude
            break
    top = step * math.ceil(value / step - 1e-9)
    return top, step


def _tick_decimals(step):
    for decimals in range(0, 5):
        if abs(step * 10 ** decimals - round(step * 10 ** decimals)) < 1e-9:
            return decimals
    return 4


class _Canvas(object):
    """Un QPainter et les primitives du rapport (unites : points)."""

    def __init__(self, painter, style):
        self.p = painter
        self.st = style

    # -- texte ------------------------------------------------------------

    def width(self, text, px, kind="body", weight=1, spacing=0.0):
        text = str(text)
        if spacing:
            return sum(self.st.advance(ch, px, kind, weight) + spacing for ch in text)
        return self.st.advance(text, px, kind, weight)

    def text(self, x, y, text, px, color=INK, kind="body", weight=1, align="l",
             alpha=1.0, spacing=0.0):
        """Ecrit `text` avec la ligne de base en `y`. Renvoie la largeur."""
        text = str(text)
        if not text:
            return 0.0
        width = self.width(text, px, kind, weight, spacing)
        if align == "r":
            x -= width
        elif align == "c":
            x -= width / 2.0
        if spacing:
            cursor = x
            for ch in text:
                self._draw(cursor, y, ch, px, color, kind, weight, alpha)
                cursor += self.st.advance(ch, px, kind, weight) + spacing
        else:
            self._draw(x, y, text, px, color, kind, weight, alpha)
        return width

    def _draw(self, x, y, text, px, color, kind, weight, alpha):
        p = self.p
        col = _qcolor(color, alpha)
        shrink = 1.0 / TEXT_SCALE
        if kind == "display":
            font, stroke = self.st.display_font(px, weight)
            path = QtGui.QPainterPath()
            path.addText(QtCore.QPointF(0.0, 0.0), font, text)
            transform = QtGui.QTransform()
            transform.translate(x, y)
            transform.scale(shrink, shrink)
            path = transform.map(path)
            if stroke > 0:
                pen = QtGui.QPen(col, stroke)
                pen.setJoinStyle(QtCore.Qt.RoundJoin)
                p.setPen(pen)
            else:
                p.setPen(QtCore.Qt.NoPen)
            p.setBrush(col)
            p.drawPath(path)
            return
        p.save()
        p.translate(x, y)
        p.scale(shrink, shrink)
        p.setFont(self.st.body_font(px, weight >= 2))
        p.setPen(col)
        p.drawText(QtCore.QPointF(0.0, 0.0), text)
        p.restore()

    def fit(self, text, px, max_width, kind="body", weight=1):
        """Raccourcit `text` avec une ellipse pour tenir dans `max_width`."""
        text = str(text)
        if self.width(text, px, kind, weight) <= max_width:
            return text
        while len(text) > 1 and self.width(text + "…", px, kind, weight) > max_width:
            text = text[:-1]
        return text.rstrip() + "…"

    def wrap(self, text, px, max_width, kind="body", weight=1, max_lines=3):
        """Coupe `text` en lignes de largeur `max_width` (au plus `max_lines`)."""
        lines, current = [], ""
        for word in str(text).split():
            trial = (current + " " + word).strip()
            if current and self.width(trial, px, kind, weight) > max_width:
                lines.append(current)
                current = word
            else:
                current = trial
        if current:
            lines.append(current)
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = self.fit(lines[-1] + " …", px, max_width, kind, weight)
        return lines

    # -- formes -----------------------------------------------------------

    def rect(self, x, y, w, h, fill=None, line=None, radius=0.0, line_w=0.6, alpha=1.0):
        p = self.p
        p.setPen(QtGui.QPen(_qcolor(line), line_w) if line else QtCore.Qt.NoPen)
        p.setBrush(_qcolor(fill, alpha) if fill else QtCore.Qt.NoBrush)
        if radius:
            p.drawRoundedRect(QtCore.QRectF(x, y, w, h), radius, radius)
        else:
            p.drawRect(QtCore.QRectF(x, y, w, h))

    def card(self, x, y, w, h, radius=7.0):
        self.rect(x, y, w, h, fill=CARD, line=LINE, radius=radius)

    def line(self, x1, y1, x2, y2, color=LINE, width=0.6, dash=None, alpha=1.0):
        pen = QtGui.QPen(_qcolor(color, alpha), width)
        if dash:
            pen.setDashPattern([float(d) for d in dash])
        pen.setCapStyle(QtCore.Qt.FlatCap)
        self.p.setPen(pen)
        self.p.drawLine(QtCore.QPointF(x1, y1), QtCore.QPointF(x2, y2))

    def circle(self, cx, cy, r, fill=None, line=None, line_w=0.6, alpha=1.0, dash=None):
        p = self.p
        if line:
            pen = QtGui.QPen(_qcolor(line), line_w)
            if dash:
                pen.setDashPattern([float(d) for d in dash])
            p.setPen(pen)
        else:
            p.setPen(QtCore.Qt.NoPen)
        p.setBrush(_qcolor(fill, alpha) if fill else QtCore.Qt.NoBrush)
        p.drawEllipse(QtCore.QPointF(cx, cy), r, r)

    def image(self, image, x, y, w, h):
        self.p.drawImage(QtCore.QRectF(x, y, w, h), image)


# --------------------------------------------------------------------------
# Elements de page
# --------------------------------------------------------------------------

def _fmt_date(day):
    return i18n.fmt_date(day)


def _footer(cv, W, H, page, total):
    y = H - 34.0
    cv.line(MARGIN, y, W - MARGIN, y, LINE, 0.6)
    cv.text(MARGIN, y + 15.0, "HELIX AERO {} AeroTools FastGCP".format(DOT), 7.5, MUTED)
    cv.text(W - MARGIN, y + 15.0, i18n.t("report.footer.page", page=page, total=total),
            7.5, MUTED, align="r")


def _page_background(cv, W, H):
    cv.rect(0, 0, W, H, fill=PAPER)


def _gradient_brush(x0, y0, x1, y1):
    gradient = QtGui.QLinearGradient(x0, y0, x1, y1)
    gradient.setColorAt(0.0, QtGui.QColor(theme.LOGO_0))
    gradient.setColorAt(0.5, QtGui.QColor(theme.LOGO_1))
    gradient.setColorAt(1.0, QtGui.QColor(theme.LOGO_2))
    return QtGui.QBrush(gradient)


def _rule(cv, W, height=3.0):
    """Filet du degrade du logo en haut de page."""
    cv.p.setPen(QtCore.Qt.NoPen)
    cv.p.setBrush(_gradient_brush(0, height, W, 0))
    cv.p.drawRect(QtCore.QRectF(0, 0, W, height))


def _page_title(cv, W, data, title, subtitle=None):
    _rule(cv, W)
    cv.text(MARGIN, 52.0, title, 17, INK, "display", 2)
    right = "{}  {}  {}".format(data["chunk"], DOT, _fmt_date(data["date"]))
    cv.text(W - MARGIN, 52.0, cv.fit(right, 8.5, 260), 8.5, MUTED, align="r")
    if subtitle:
        cv.text(MARGIN, 66.0, subtitle, 8.5, MUTED)


def _draw_band(cv, W, data):
    """Bandeau titre de la page 1."""
    height = 132.0
    cv.p.setPen(QtCore.Qt.NoPen)
    cv.p.setBrush(_gradient_brush(0, height, W, 0))
    cv.p.drawRect(QtCore.QRectF(0, 0, W, height))
    cv.text(MARGIN, 36.0, "HELIX AERO", 9, "#ffffff", "display", 2, spacing=1.8)
    cv.text(W - MARGIN, 36.0, "AeroTools {} FastGCP".format(DOT), 9, "#ffffff", "display", 1,
            align="r", alpha=0.85)
    cv.text(MARGIN, 76.0, i18n.t("report.title"), 29, "#ffffff", "display", 2)
    meta = data["meta"]
    version = meta.get("metashape") or EM_DASH
    cells = [
        (i18n.t("report.band.project").upper(), meta.get("project") or EM_DASH),
        (i18n.t("report.band.chunk").upper(), data["chunk"] or EM_DASH),
        (i18n.t("report.band.date").upper(), _fmt_date(data["date"])),
        (i18n.t("report.band.metashape").upper(), version),
    ]
    inner = W - 2 * MARGIN
    weights = (0.34, 0.34, 0.16, 0.16)
    x = MARGIN
    for (label, value), share in zip(cells, weights):
        cell_w = inner * share
        cv.text(x, 104.0, label, 6.8, "#ffffff", "body", 2, alpha=0.72, spacing=1.2)
        cv.text(x, 119.0, cv.fit(value, 10.5, cell_w - 10), 10.5, "#ffffff", "body", 2)
        x += cell_w


def _kpi_card(cv, x, y, w, h, label, value, sub=None, accent=GREEN):
    cv.card(x, y, w, h)
    cv.rect(x, y + 12, 3, h - 24, fill=accent, radius=1.5)
    cv.text(x + 14, y + 19, label.upper(), 6.8, MUTED, "body", 2, spacing=1.0)
    cv.text(x + 14, y + 44, value, 20, INK, "display", 2)
    if sub:
        cv.text(x + 14, y + 58, cv.fit(sub, 7.8, w - 26), 7.8, MUTED)


def _draw_kpis(cv, x, y, w, data):
    s = data["stats"]
    gap, h = 10.0, 68.0
    cw = (w - 2 * gap) / 3.0
    pct = ""
    if s["n_markers"]:
        pct = i18n.t("report.kpi.pointed_sub",
                     pct=int(round(100.0 * s["n_pointed"] / s["n_markers"])))
    mean = s["mean_projections"]
    xy = None
    if s["rms_x"] is not None and s["rms_y"] is not None:
        xy = "X {}  {}  Y {}".format(fmt_m(s["rms_x"]), DOT, fmt_m(s["rms_y"]))
    z_sub = (i18n.t("report.kpi.rms_z_sub", value=fmt_m(s["rms_total"]))
             if s["rms_total"] is not None else None)
    worst = s["worst_label"]
    worst_hot = (s["worst_total"] is not None and s["threshold_total"] is not None
                 and s["worst_total"] > s["threshold_total"])
    cards = [
        (i18n.t("report.kpi.markers"), str(s["n_markers"]),
         i18n.t("report.kpi.markers_sub"), GREEN),
        (i18n.t("report.kpi.pointed"), "{} / {}".format(s["n_pointed"], s["n_markers"]),
         pct or None, GREEN),
        (i18n.t("report.kpi.projections"), str(s["total_projections"]),
         i18n.t("report.kpi.projections_sub", mean=fmt_num(mean, 1))
         if mean is not None else None, GREEN),
        (i18n.t("report.kpi.rms_xy"), fmt_m(s["rms_xy"]), xy, GREEN),
        (i18n.t("report.kpi.rms_z"), fmt_m(s["rms_z"]), z_sub, GREEN),
        (i18n.t("report.kpi.worst"), fmt_m(s["worst_total"]), worst,
         ORANGE if worst_hot else GREEN),
    ]
    for index, (label, value, sub, accent) in enumerate(cards):
        col, row = index % 3, index // 3
        _kpi_card(cv, x + col * (cw + gap), y + row * (h + gap), cw, h, label, value, sub, accent)
    return 2 * h + gap


def _card_title(cv, x, y, title, subtitle=None):
    cv.text(x + 14, y + 22, title, 11.5, INK, "display", 2)
    if subtitle:
        cv.text(x + 14, y + 35, subtitle, 7.6, MUTED)


def _empty_note(cv, x, y, w, h, text=None):
    cv.text(x + w / 2.0, y + h / 2.0 + 3, text or i18n.t("report.empty"), 8.5, FAINT, align="c")


def _bar_chart(cv, x, y, w, h, data):
    """Barres horizontales : erreur totale par marqueur, tri decroissant."""
    s = data["stats"]
    cv.card(x, y, w, h)
    valid = [m for m in data["markers"] if m["total"] is not None]
    valid.sort(key=lambda m: -m["total"])
    shown = valid[:BAR_LIMIT]
    sub = i18n.t("report.bar.sub")
    if len(valid) > len(shown):
        sub = i18n.t("report.bar.sub_top", shown=len(shown), total=len(valid))
    _card_title(cv, x, y, i18n.t("report.bar.title"), sub)
    if not shown:
        _empty_note(cv, x, y, w, h)
        return
    label_w, value_w = 76.0, 40.0
    left = x + 14 + label_w
    right = x + w - 14 - value_w
    top_y = y + 54.0
    bottom_reserve = 44.0
    row_h = min(17.0, (h - 54.0 - bottom_reserve) / float(len(shown)))
    bar_h = row_h * 0.56
    peak = max(shown[0]["total"], s["threshold_total"] or 0.0, s["rms_total"] or 0.0)
    top, step = _nice_top(peak, 4)
    decimals = _tick_decimals(step)
    scale = (right - left) / top
    plot_bottom = top_y + row_h * len(shown)
    n_ticks = int(round(top / step))
    for i in range(n_ticks + 1):
        gx = left + i * step * scale
        cv.line(gx, top_y - 3, gx, plot_bottom + 1, GRID if i else LINE, 0.5)
        cv.text(gx, plot_bottom + 11, fmt_num(i * step, decimals), 6.6, FAINT, align="c")
    thr = s["threshold_total"]
    rms = s["rms_total"]
    if rms:
        rx = left + rms * scale
        cv.line(rx, top_y - 3, rx, plot_bottom + 1, INK_2, 0.8, dash=(3, 2.5))
    for i, m in enumerate(shown):
        cy = top_y + i * row_h + row_h / 2.0
        hot = over_threshold(m["total"], thr)
        cv.text(x + 14, cy + 2.6, cv.fit(m["label"], 7.8, label_w - 6), 7.8, INK_2)
        bw = max(1.2, m["total"] * scale)
        cv.rect(left, cy - bar_h / 2.0, bw, bar_h, fill=ORANGE if hot else GREEN,
                radius=min(2.0, bar_h / 2.0))
        value = fmt_num(m["total"])
        vw = cv.width(value, 7.2)
        cv.rect(left + bw + 2.0, cy - 5.2, vw + 4.0, 10.4, fill=CARD)
        cv.text(left + bw + 4, cy + 2.6, value, 7.2, WARN_TEXT if hot else MUTED)
    ly = y + h - 14
    lx = x + 14
    cv.rect(lx, ly - 6, 8, 8, fill=GREEN, radius=2)
    lx += 12
    lx += cv.text(lx, ly + 1, i18n.t("report.bar.legend_total"), 7.2, MUTED) + 12
    if thr:
        cv.rect(lx, ly - 6, 8, 8, fill=ORANGE, radius=2)
        lx += 12
        lx += cv.text(lx, ly + 1, i18n.t("report.bar.legend_over"), 7.2, MUTED) + 12
    if rms:
        cv.line(lx, ly - 2, lx + 10, ly - 2, INK_2, 0.8, dash=(3, 2.5))
        lx += 14
        cv.text(lx, ly + 1, i18n.t("report.bar.legend_rms", value=fmt_m(rms)), 7.2, MUTED)


def _scatter_chart(cv, x, y, w, h, data):
    """Dispersion en plan : erreur X en abscisse, erreur Y en ordonnee."""
    s = data["stats"]
    cv.card(x, y, w, h)
    _card_title(cv, x, y, i18n.t("report.scatter.title"), i18n.t("report.scatter.sub"))
    pts = [m for m in data["markers"] if m["x"] is not None and m["y"] is not None]
    if not pts:
        _empty_note(cv, x, y, w, h)
        return
    left_pad = 26.0
    side = w - 14 - left_pad - 14
    px0, py0 = x + left_pad + 14, y + 48.0
    extent = max([abs(m["x"]) for m in pts] + [abs(m["y"]) for m in pts]
                 + [s["rms_xy"] or 0.0, 0.005])
    top, step = _nice_top(extent * 1.08, 2)
    decimals = _tick_decimals(step)
    k = side / (2.0 * top)
    ox, oy = px0 + side / 2.0, py0 + side / 2.0
    cv.rect(px0, py0, side, side, fill="#fbfbf9", line=LINE, radius=2)
    n = int(round(top / step))
    for i in range(-n, n + 1):
        gx, gy = ox + i * step * k, oy - i * step * k
        if i:
            cv.line(gx, py0, gx, py0 + side, GRID, 0.5)
            cv.line(px0, gy, px0 + side, gy, GRID, 0.5)
        if n <= 2 or i % 2 == 0:
            cv.text(gx, py0 + side + 10, _typo(fmt_num(i * step, decimals)), 6.4, FAINT, align="c")
            cv.text(px0 - 4, gy + 2.2, _typo(fmt_num(i * step, decimals)), 6.4, FAINT, align="r")
    cv.line(ox, py0, ox, py0 + side, FAINT, 0.7)
    cv.line(px0, oy, px0 + side, oy, FAINT, 0.7)
    rms = s["rms_xy"]
    if rms:
        cv.circle(ox, oy, rms * k, fill=GREEN, alpha=0.10)
        cv.circle(ox, oy, rms * k, line=GREEN_DEEP, line_w=0.9, dash=(3, 2.2))
    thr = s["threshold_total"]
    for m in pts:
        hot = over_threshold(m["total"], thr)
        cv.circle(ox + m["x"] * k, oy - m["y"] * k, 3.0, fill=ORANGE if hot else GREEN,
                  line="#ffffff", line_w=0.8)
    far = sorted(pts, key=lambda m: -(m["x"] ** 2 + m["y"] ** 2))[:3]
    for m in far:
        cx, cy = ox + m["x"] * k, oy - m["y"] * k
        label = cv.fit(m["label"], 6.8, 60)
        if cx > ox + side * 0.2:
            cv.text(cx - 5, cy - 4.5, label, 6.8, INK_2, align="r")
        else:
            cv.text(cx + 5, cy - 4.5, label, 6.8, INK_2)
    ly = y + h - 12
    if rms:
        cv.circle(x + 20, ly - 2.2, 3.4, line=GREEN_DEEP, line_w=0.9, dash=(2, 1.6))
        cv.text(x + 28, ly, i18n.t("report.scatter.legend_rms", value=fmt_m(rms)), 7.2, MUTED)
    cv.text(x + w - 14, ly, i18n.t("report.scatter.unit"), 7.2, FAINT, align="r")


def _axis_rms_chart(cv, x, y, w, h, data):
    s = data["stats"]
    cv.card(x, y, w, h)
    _card_title(cv, x, y, i18n.t("report.axis.title"))
    rows = [("X", s["rms_x"]), ("Y", s["rms_y"]), ("Z", s["rms_z"]),
            (i18n.t("report.axis.xy"), s["rms_xy"]),
            (i18n.t("report.axis.total"), s["rms_total"])]
    if all(v is None for _l, v in rows):
        _empty_note(cv, x, y + 10, w, h - 10)
        return
    peak = max(v for _l, v in rows if v is not None) or 1.0
    top, _step = _nice_top(peak, 2)
    left = x + 14 + 30
    right = x + w - 14 - 46
    row_h = 18.0
    for i, (label, value) in enumerate(rows):
        cy = y + 50 + i * row_h
        cv.text(x + 14, cy + 2.6, label, 7.8, INK_2)
        if value is None:
            cv.text(right + 4, cy + 2.6, EM_DASH, 7.4, FAINT)
            continue
        bw = max(1.2, value / top * (right - left))
        bar = i >= 3
        cv.rect(left, cy - 3.6, bw, 7.2, fill=GREEN_DEEP if bar else GREEN, radius=2)
        cv.text(left + bw + 4, cy + 2.6, fmt_num(value), 7.4, MUTED)


def _note_card(cv, x, y, w, h, data):
    s = data["stats"]
    cv.card(x, y, w, h)
    if s["n_with_error"] == 0:
        lines = [i18n.t("report.note.none_1"), i18n.t("report.note.none_2")]
    else:
        if s["threshold_total"]:
            second = i18n.t("report.note.threshold", value=fmt_m(s["threshold_total"]))
        else:
            second = i18n.t("report.note.no_threshold")
        lines = [i18n.t("report.note.errors"), second]
    cv.text(x + 14, y + 20, i18n.t("report.note.title").upper(), 6.8, MUTED, "body", 2,
            spacing=1.0)
    for i, line in enumerate(lines):
        cv.text(x + 14, y + 34 + i * 11.5, line, 8, INK_2)


def _page_summary(cv, W, H, data, page, total):
    _page_background(cv, W, H)
    _draw_band(cv, W, data)
    inner = W - 2 * MARGIN
    y = 150.0
    y += _draw_kpis(cv, MARGIN, y, inner, data) + 14.0
    right_w = 208.0
    gap = 10.0
    left_w = inner - right_w - gap
    scatter_h = right_w + 52.0
    axis_h = 148.0
    block_h = scatter_h + gap + axis_h
    _bar_chart(cv, MARGIN, y, left_w, block_h, data)
    _scatter_chart(cv, MARGIN + left_w + gap, y, right_w, scatter_h, data)
    _axis_rms_chart(cv, MARGIN + left_w + gap, y + scatter_h + gap, right_w, axis_h, data)
    y += block_h + gap
    _note_card(cv, MARGIN, y, inner, 52.0, data)
    _footer(cv, W, H, page, total)


# --------------------------------------------------------------------------
# Tableau
# --------------------------------------------------------------------------

TABLE_TOP = 78.0
TABLE_HEAD_H = 24.0
HIST_H = 176.0


def _table_plan(n_rows, H):
    """Repartition des lignes sur les pages : (pages_de_lignes, histo_avec_derniere).

    `pages_de_lignes` est une liste de nombres de lignes. L'histogramme se met
    sous la derniere table s'il y a la place, sinon il a sa page.
    """
    bottom = H - 46.0
    per_page = int((bottom - TABLE_TOP - TABLE_HEAD_H - 8.0) // ROW_H)
    # Sous la table : 22 pt de legende, puis l'histogramme, avant le filet du pied.
    with_hist = int((H - 48.0 - TABLE_TOP - TABLE_HEAD_H - 8.0 - 22.0 - HIST_H) // ROW_H)
    n_rows = max(n_rows, 0)
    chunks = []
    left = n_rows
    while left > per_page:
        chunks.append(per_page)
        left -= per_page
    if left > with_hist:
        chunks.append(left)
        return chunks, False
    chunks.append(left)
    return chunks, True


def _draw_table(cv, x, y, w, rows, stats, first_index):
    n = len(rows)
    height = TABLE_HEAD_H + n * ROW_H + 8.0
    cv.card(x, y, w, height)
    pad = 14.0
    right = x + w - pad
    col_w = [66.0, 66.0, 66.0, 70.0, 44.0]          # X, Y, Z, Total, Proj.
    cols = []
    edge = right
    for width in reversed(col_w):
        cols.append(edge)
        edge -= width
    cols.reverse()                                    # bords droits des 5 colonnes
    heads = [i18n.t(key) for key in ("report.table.x", "report.table.y", "report.table.z",
                                     "report.table.total", "report.table.proj")]
    cv.rect(x + 0.3, y + 0.3, w - 0.6, TABLE_HEAD_H, fill="#ecece8", radius=6.5)
    cv.rect(x + 0.3, y + TABLE_HEAD_H - 8, w - 0.6, 8.6, fill="#ecece8")
    cv.text(x + pad, y + 15.5, i18n.t("report.table.id"), 6.9, MUTED, "body", 2, spacing=0.8)
    for edge, head in zip(cols, heads):
        cv.text(edge, y + 15.5, head, 7.0, MUTED, "body", 2, align="r")
    thresholds = (stats["threshold_x"], stats["threshold_y"], stats["threshold_z"],
                  stats["threshold_total"])
    id_w = (cols[0] - col_w[0]) - (x + pad) - 6
    for i, m in enumerate(rows):
        ry = y + TABLE_HEAD_H + i * ROW_H
        if (first_index + i) % 2 == 1:
            cv.rect(x + 0.3, ry, w - 0.6, ROW_H, fill=PAPER)
        base = ry + ROW_H - 4.6
        muted = not m["pointed"]
        cv.text(x + pad, base, cv.fit(m["label"], 8.2, id_w), 8.2, FAINT if muted else INK, "body", 1)
        values = (m["x"], m["y"], m["z"], m["total"])
        for edge, value, thr in zip(cols[:4], values, thresholds):
            text = _typo(fmt_num(value))
            if over_threshold(value, thr):
                tw = cv.width(text, 8.2)
                cv.rect(edge - tw - 4, ry + 1.8, tw + 8, ROW_H - 3.6, fill=WARN_BG, radius=3)
                cv.text(edge, base, text, 8.2, WARN_TEXT, "body", 2, align="r")
            else:
                cv.text(edge, base, text, 8.2, FAINT if value is None else INK_2, align="r")
        cv.text(cols[4], base, str(m["projections"]), 8.2,
                WARN_TEXT if muted else INK_2, "body", 2 if muted else 1, align="r")
    return height


def _histogram_card(cv, x, y, w, h, data):
    s = data["stats"]
    counts = [m["projections"] for m in data["markers"]]
    cv.card(x, y, w, h)
    _card_title(cv, x, y, i18n.t("report.hist.title"), i18n.t("report.hist.sub"))
    bins = projection_bins(counts)
    if not bins:
        _empty_note(cv, x, y, w, h)
        return
    chart_w = w - 14 - 14 - 160.0
    left, base = x + 14 + 8, y + h - 30.0
    top_y = y + 56.0
    peak = max(n for _l, n, _z in bins) or 1
    top, step = _nice_top(float(peak), 3)
    top = max(top, 1.0)
    step = max(1.0, round(step))
    unit = (base - top_y) / top
    n_ticks = int(top // step)
    for i in range(n_ticks + 1):
        gy = base - i * step * unit
        cv.line(left - 4, gy, left + chart_w, gy, GRID if i else LINE, 0.5)
        cv.text(left - 7, gy + 2.2, str(int(i * step)), 6.4, FAINT, align="r")
    slot = chart_w / float(len(bins))
    bar_w = min(26.0, slot * 0.62)
    for i, (label, n, zero) in enumerate(bins):
        cx = left + slot * (i + 0.5)
        bh = n * unit
        if n:
            cv.rect(cx - bar_w / 2.0, base - bh, bar_w, bh, fill=ORANGE if zero else GREEN,
                    radius=min(2.0, bar_w / 3.0))
            cv.text(cx, base - bh - 3.5, str(n), 7.2, MUTED, align="c")
        cv.text(cx, base + 11, label, 7.2, MUTED, align="c")
    cv.text(left + chart_w / 2.0, base + 23, i18n.t("report.hist.axis"), 7.0, FAINT, align="c")
    sx = x + w - 14 - 140.0
    cv.line(sx - 12, y + 56, sx - 12, y + h - 22, LINE, 0.6)
    items = [
        (i18n.t("report.hist.mean"), fmt_num(s["mean_projections"], 1)),
        (i18n.t("report.hist.median"), fmt_num(s["median_projections"], 1)),
        (i18n.t("report.hist.min"), str(s["min_projections"])),
        (i18n.t("report.hist.max"), str(s["max_projections"])),
        (i18n.t("report.hist.low"), str(s["n_low_projections"])),
        (i18n.t("report.hist.none"), str(s["n_markers"] - s["n_pointed"])),
    ]
    for i, (label, value) in enumerate(items):
        ry = y + 66 + i * 17.0
        cv.text(sx, ry, label, 7.8, MUTED)
        cv.text(sx + 140, ry, value, 8.2, INK, "body", 2, align="r")
        if i < len(items) - 1:
            cv.line(sx, ry + 6.5, sx + 140, ry + 6.5, GRID, 0.5)


def _draw_table_pages(draw_page, cv, W, H, data, first_page_no, total):
    """Peint les pages du tableau. `draw_page()` ouvre une nouvelle page."""
    plan, hist_inline = _table_plan(len(data["markers"]), H)
    inner = W - 2 * MARGIN
    markers = data["markers"]
    index = 0
    page_no = first_page_no
    last_index = len(plan) - 1
    for number, n in enumerate(plan):
        if number or page_no > first_page_no:
            draw_page()
        _page_background(cv, W, H)
        if number == 0:
            subtitle = i18n.t("report.table.sub", n=len(markers))
        elif len(plan) > 1:
            subtitle = i18n.t("report.table.sub_page", page=number + 1, pages=len(plan))
        else:
            subtitle = i18n.t("report.table.sub_next")
        _page_title(cv, W, data, i18n.t("report.table.title"), subtitle)
        rows = markers[index:index + n]
        height = _draw_table(cv, MARGIN, TABLE_TOP, inner, rows, data["stats"], index)
        index += n
        if number == last_index:
            if rows:
                cv.text(MARGIN, TABLE_TOP + height + 11, _table_legend(data["stats"]), 7.2, MUTED)
            if hist_inline:
                top = max(TABLE_TOP + height + 22.0, TABLE_TOP + TABLE_HEAD_H + 8.0)
                _histogram_card(cv, MARGIN, top, inner, HIST_H, data)
        _footer(cv, W, H, page_no, total)
        page_no += 1
    if not hist_inline:
        draw_page()
        _page_background(cv, W, H)
        _page_title(cv, W, data, i18n.t("report.hist.page_title"))
        _histogram_card(cv, MARGIN, TABLE_TOP, inner, HIST_H, data)
        _footer(cv, W, H, page_no, total)
        page_no += 1
    return page_no


def _table_legend(stats):
    if stats["threshold_total"]:
        return i18n.t("report.table.legend")
    return i18n.t("report.table.legend_plain")


def _table_pages_count(n_rows, H):
    plan, hist_inline = _table_plan(n_rows, H)
    return len(plan) + (0 if hist_inline else 1)


# --------------------------------------------------------------------------
# Captures
# --------------------------------------------------------------------------

def _fit_box(iw, ih, bw, bh):
    if iw <= 0 or ih <= 0 or bw <= 0 or bh <= 0:
        return 0.0, 0.0
    scale = min(bw / float(iw), bh / float(ih))
    return iw * scale, ih * scale


CARD_PAD = 8.0
CAPTION_LINE = 10.5


def _caption_height(lines):
    return len(lines) * CAPTION_LINE + 8.0 if lines else 0.0


def _capture_height(image, width, lines, max_h):
    """Hauteur naturelle d'une carte de capture de largeur `width`, plafonnee a `max_h`."""
    caption_h = _caption_height(lines)
    _iw, ih = _fit_box(image.width(), image.height(), width - 2 * CARD_PAD,
                       max_h - 2 * CARD_PAD - caption_h)
    return ih + 2 * CARD_PAD + caption_h


def _capture_card(cv, x, y, w, h, image, lines):
    """Carte avec l'image centree en haut et sa legende en dessous. Renvoie h."""
    cv.card(x, y, w, h)
    caption_h = _caption_height(lines)
    iw, ih = _fit_box(image.width(), image.height(), w - 2 * CARD_PAD,
                      h - 2 * CARD_PAD - caption_h)
    ix = x + (w - iw) / 2.0
    iy = y + CARD_PAD
    cv.image(image, ix, iy, iw, ih)
    cv.rect(ix, iy, iw, ih, line=LINE, line_w=0.5)
    base = y + CARD_PAD + ih + 12.0
    for i, line in enumerate(lines):
        cv.text(x + CARD_PAD, base + i * CAPTION_LINE, line, 7.6,
                INK_2 if i == 0 else MUTED, "body", 2 if i == 0 else 1)
    return h


def _caption_lines(cv, text, width, max_lines=3):
    if isinstance(text, (list, tuple)):
        return [cv.fit(str(t), 7.6, width) for t in text][:max_lines]
    return cv.wrap(text, 7.6, width, max_lines=max_lines)


def _has_images(images):
    if not images:
        return False
    return bool(images.get("photo") is not None or images.get("crops")
                or images.get("panel") is not None)


def _page_captures(cv, W, H, data, images, page, total):
    crops = [c for c in (images.get("crops") or []) if c and c.get("image") is not None][:2]
    photo = images.get("photo")
    panel = images.get("panel")
    subtitle = i18n.t("report.shots.sub")
    if crops:
        subtitle = i18n.t("report.shots.sub_crops")
    _page_background(cv, W, H)
    _page_title(cv, W, data, i18n.t("report.shots.title"), subtitle)
    inner = W - 2 * MARGIN
    gap = 12.0
    y = TABLE_TOP + 4.0
    bottom = H - 50.0

    if photo is not None:
        lines = _caption_lines(cv, images.get("photo_caption") or i18n.t("report.shots.photo"),
                               inner - 2 * CARD_PAD, 2)
        reserve = (gap + 250.0) if (crops or panel is not None) else 0.0
        max_h = min(300.0, bottom - y - reserve)
        h = _capture_height(photo, inner, lines, max_h)
        _capture_card(cv, MARGIN, y, inner, h, photo, lines)
        y += h + gap

    remaining = bottom - y
    if remaining < 80 or (not crops and panel is None):
        _footer(cv, W, H, page, total)
        return

    panel_w = 0.0
    panel_lines = []
    if panel is not None:
        panel_lines = _caption_lines(cv, images.get("panel_caption") or i18n.t("report.shots.panel"),
                                     190.0, 1)
        box_h = remaining - 2 * CARD_PAD - _caption_height(panel_lines)
        panel_w = min(210.0, box_h * panel.width() / float(panel.height()) + 2 * CARD_PAD)
        if not crops:
            panel_h = min(remaining, 470.0)
            box_h = panel_h - 2 * CARD_PAD - _caption_height(panel_lines)
            panel_w = min(inner, box_h * panel.width() / float(panel.height()) + 2 * CARD_PAD)
            _capture_card(cv, MARGIN + (inner - panel_w) / 2.0, y, panel_w, panel_h, panel,
                          panel_lines)
            _footer(cv, W, H, page, total)
            return
        panel_w = max(panel_w, 120.0)

    col_w = inner - (panel_w + gap if panel is not None else 0.0)
    per_crop = (remaining - gap * (len(crops) - 1)) / float(len(crops))
    cy = y
    for crop in crops:
        lines = _caption_lines(cv, crop.get("caption") or "", col_w - 2 * CARD_PAD, 3)
        h = _capture_height(crop["image"], col_w, lines, per_crop)
        _capture_card(cv, MARGIN, cy, col_w, h, crop["image"], lines)
        cy += h + gap
    if panel is not None:
        _capture_card(cv, MARGIN + col_w + gap, y, panel_w, remaining, panel, panel_lines)
    _footer(cv, W, H, page, total)


# --------------------------------------------------------------------------
# Photos pointees par l'IA : une vignette par photo, croix rouge et indice de confiance
# --------------------------------------------------------------------------

AI_CELL = 58.0                      # cote d'une vignette, en points
AI_GAP = 7.0
AI_CAPTION_H = 11.0
AI_HEAD_H = 42.0                    # titre du marqueur + deux lignes d'informations
AI_THUMB_PX = 160                   # cote de la vignette peinte, en pixels
AI_FALLBACK_PX = 96                 # region lue dans la photo quand le recadrage n'existe plus
AI_RED = "#ff4a2e"
A4_W, A4_H = 595.28, 841.89


def _ai_columns(width):
    return max(1, int((width - 2 * MARGIN + AI_GAP) // (AI_CELL + AI_GAP)))


def _ai_plan(sections, width=A4_W, height=A4_H):
    """Pages des photos pointees par l'IA, sans rien dessiner.

    `sections` : [{"label": str, "items": [...]}]. Renvoie une liste de pages ; une page est
    une liste de ("head", section, suite) ou ("row", [items]). Un marqueur qui ne tient pas
    sur la page continue sur la suivante, avec son titre repete.
    """
    cols = _ai_columns(width)
    row_h = AI_CELL + AI_CAPTION_H + AI_GAP
    top = TABLE_TOP + 4.0
    bottom = height - 50.0
    pages, y = [[]], top
    for section in sections:
        items, i, continued = section["items"], 0, False
        while i < len(items):
            if y + AI_HEAD_H + row_h > bottom and pages[-1]:
                pages.append([])
                y = top
            pages[-1].append(("head", section, continued))
            y += AI_HEAD_H
            while i < len(items) and y + row_h <= bottom:
                pages[-1].append(("row", items[i:i + cols]))
                y += row_h
                i += cols
            if i < len(items):
                pages.append([])
                y = top
                continued = True
            else:
                y += 8.0
    return pages if pages != [[]] else []


def _page_ai(cv, W, H, data, plan, page, total):
    _page_background(cv, W, H)
    _page_title(cv, W, data, i18n.t("report.ai.title"), i18n.t("report.ai.sub"))
    y = TABLE_TOP + 4.0
    for entry in plan:
        if entry[0] == "head":
            section, continued = entry[1], entry[2]
            title = i18n.t("report.ai.marker", label=section["label"], n=len(section["items"]))
            if continued:
                title = "{} {}".format(title, i18n.t("report.ai.cont"))
            cv.text(MARGIN, y + 12.0, title, 10.5, INK, "body", 2)
            cv.line(MARGIN, y + 17.0, W - MARGIN, y + 17.0, LINE, 0.6)
            lines = [] if continued else cv.wrap(" {} ".format(DOT).join(ai_info_parts(section.get("info") or {})),
                                                 7.6, W - 2 * MARGIN, max_lines=2)
            for k, line in enumerate(lines):
                cv.text(MARGIN, y + 28.0 + k * CAPTION_LINE, line, 7.6, MUTED)
            y += AI_HEAD_H
            continue
        for k, item in enumerate(entry[1]):
            x = MARGIN + k * (AI_CELL + AI_GAP)
            cv.image(item["image"], x, y, AI_CELL, AI_CELL)
            cv.rect(x, y, AI_CELL, AI_CELL, line=item["ring"], line_w=0.7)
            cv.text(x + AI_CELL / 2.0, y + AI_CELL + 9.0, item["caption"], 7.2, item["color"],
                    "body", 1, "c")
        y += AI_CELL + AI_CAPTION_H + AI_GAP
    _footer(cv, W, H, page, total)


def _median_of(values):
    values = sorted(values)
    if not values:
        return None
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2.0


def ai_info_parts(info):
    """Phrases courtes decrivant un marqueur dans le rapport : erreur, pointage, confiance, GCP.

    `info` : dict de ai_sections (cles absentes ou None = on n'en parle pas). Sans Qt.
    """
    parts = []
    if info.get("total") is not None:
        parts.append(i18n.t("report.ai.info_error", total=fmt_num(info["total"], 3),
                            x=fmt_num(info.get("x"), 3), y=fmt_num(info.get("y"), 3),
                            z=fmt_num(info.get("z"), 3)))
    if info.get("median_px") is not None:
        parts.append(i18n.t("report.ai.info_pointing", median=fmt_num(info["median_px"], 1),
                            max=fmt_num(info["max_px"], 1)))
    if info.get("conf_mean") is not None:
        parts.append(i18n.t("report.ai.info_conf", mean=fmt_num(info["conf_mean"], 2),
                            min=fmt_num(info["conf_min"], 2)))
    if info.get("projections"):
        parts.append(i18n.t("report.ai.info_projections", n=info["projections"]))
    if info.get("gcp") is True:
        text = i18n.t("report.ai.info_gcp_yes")
    elif info.get("gcp") is False:
        text = i18n.t("report.ai.info_gcp_no")
    else:
        text = None
    if text:
        if info.get("accuracy") is not None:
            text = "{} ({})".format(text, i18n.t("report.ai.info_accuracy", acc=fmt_num(info["accuracy"], 3)))
        parts.append(text)
    return parts


def _ai_mark(image, cx, cy, color):
    """Croix a quatre branches (vide au centre, halo blanc) sur une vignette de AI_THUMB_PX."""
    marked = image.convertToFormat(QtGui.QImage.Format_RGB32)
    painter = QtGui.QPainter(marked)
    try:
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        arm, gap = marked.width() / 5.0, marked.width() / 28.0
        for tone, width in (("#ffffff", 4.2), (color, 2.2)):
            painter.setPen(QtGui.QPen(QtGui.QColor(tone), width))
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                painter.drawLine(QtCore.QPointF(cx + dx * gap, cy + dy * gap),
                                 QtCore.QPointF(cx + dx * arm, cy + dy * arm))
    finally:
        painter.end()
    return marked


def _ai_thumb(entry):
    """Vignette carree d'une proposition, croix sur son point ; None si la photo est illisible.

    On reprend le recadrage ecrit par la detection quand il existe encore ; sinon on lit
    la region dans la photo d'origine.
    """
    x, y = entry["x"], entry["y"]
    if review.has_crop(entry) and os.path.isfile(entry["big"]):
        source = QtGui.QImage(entry["big"])
        if not source.isNull():
            left, top, side = review.source_rect(entry, 1.0, source.width())
            rect = QtCore.QRect(int(round(left)), int(round(top)), int(round(side)), int(round(side)))
            image = source.copy(rect).scaled(AI_THUMB_PX, AI_THUMB_PX, QtCore.Qt.IgnoreAspectRatio,
                                             QtCore.Qt.SmoothTransformation)
            cx, cy = review.to_view(entry, x, y, AI_THUMB_PX, 1.0)
            return _ai_mark(image, cx, cy, AI_RED)
    region = _read_region(entry["image_path"], x, y, AI_FALLBACK_PX, AI_FALLBACK_PX,
                          msapi.camera_size(entry["camera"]))
    if region is None:
        return None
    image, lx, ly, _scale = region
    factor = AI_THUMB_PX / float(image.width())
    image = image.scaled(AI_THUMB_PX, int(round(image.height() * factor)), QtCore.Qt.IgnoreAspectRatio,
                         QtCore.Qt.SmoothTransformation)
    return _ai_mark(image, lx * factor, ly * factor, AI_RED)


def _natural(label):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(label))]


def _ai_still_there(entry):
    """La proposition est-elle toujours dans le projet, dans l'etat que le journal lui donne ?

    Gardee : projection non posee. Validee ou affinee : projection posee a la main (un
    Ctrl+Z ou un « Effacer » apres coup la fait sortir du rapport).
    """
    projection = msapi.get_projection(entry["marker"], entry["camera"])
    if projection is None:
        return False
    return not projection[2] if entry["state"] == review.KEPT else bool(projection[2])


def _ai_info(entries, data):
    """Informations d'un marqueur pour l'en-tete de sa section (voir ai_info_parts).

    L'erreur et le nombre de projections viennent de `data` (collect_data) ; la precision de
    pointage est l'ecart, en pixels, entre chaque point retenu et la reprojection du point 3D
    du marqueur ; la confiance est celle de l'IA sur les points retenus.
    """
    marker = entries[0]["marker"]
    info = {}
    row = next((m for m in (data or {}).get("markers", []) if m["label"] == entries[0]["label"]), None)
    if row is not None:
        info.update(total=row["total"], x=row["x"], y=row["y"], z=row["z"], projections=row["projections"])
    chk = msapi.chunk()
    gaps = []
    for entry in entries:
        estimate = msapi.marker_estimate(chk, marker, entry["camera"])
        if estimate is not None:
            gaps.append(math.hypot(entry["x"] - estimate[0], entry["y"] - estimate[1]))
    if gaps:
        info.update(median_px=_median_of(gaps), max_px=max(gaps))
    confs = [e["conf"] for e in entries if e["state"] != review.REFINED]
    if confs:
        info.update(conf_mean=sum(confs) / len(confs), conf_min=min(confs))
    info["gcp"], info["accuracy"] = msapi.marker_reference_info(marker, chk)
    return info


def ai_sections(state, data=None):
    """Vignettes des photos pointees par l'IA, par marqueur : [{"label", "items": [...], "info": {...}}].

    Seules comptent les propositions gardees, validees ou affinees, et seulement si leur
    projection existe encore dans le projet. Une photo illisible est simplement absente.
    """
    grouped, kept = {}, {}
    for entry in getattr(state, "aigcp_log", None) or []:
        if entry["state"] not in (review.KEPT, review.ACCEPTED, review.REFINED):
            continue
        try:
            if not _ai_still_there(entry):
                continue
            image = _ai_thumb(entry)
        except Exception as exc:
            journal.write("Rapport : vignette IA ignorée : {!r}".format(exc))
            continue
        if image is None:
            continue
        manual = entry["state"] == review.REFINED
        kept.setdefault(entry["label"], []).append(entry)
        grouped.setdefault(entry["label"], []).append({
            "image": image, "camera": entry["camera_label"],
            "caption": i18n.t("report.ai.manual") if manual else fmt_num(entry["conf"], 2),
            "color": GREEN_DEEP if manual else MUTED,
            "ring": GREEN if manual else LINE,
        })
    sections = []
    for label in sorted(grouped, key=_natural):
        try:
            info = _ai_info(kept[label], data)
        except Exception as exc:
            journal.write("Rapport : informations de {} ignorées : {!r}".format(label, exc))
            info = {}
        sections.append({"label": label, "info": info,
                         "items": sorted(grouped[label], key=lambda it: it["camera"])})
    return sections


# --------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------

def _new_writer(path, title):
    writer = QtGui.QPdfWriter(path)
    try:
        writer.setPageSize(QtGui.QPageSize(QtGui.QPageSize.A4))
    except Exception:
        writer.setPageSize(QtGui.QPagedPaintDevice.A4)
    try:
        writer.setPageMargins(QtCore.QMarginsF(0.0, 0.0, 0.0, 0.0))
    except Exception:
        pass
    writer.setResolution(DPI)
    writer.setTitle(title)
    writer.setCreator("AeroTools FastGCP (HELIX AERO)")
    return writer


def page_count(data, images=None):
    """Nombre de pages que build_pdf produira (A4 : 842 pt de haut)."""
    ai = len(_ai_plan((images or {}).get("ai_picks") or []))
    return 1 + _table_pages_count(len(data["markers"]), 841.89) + (1 if _has_images(images) else 0) + ai


def build_pdf(data, path, images=None):
    """Peint le rapport dans `path` et renvoie le nombre de pages.

    `images` (toutes optionnelles) : {"photo": QImage, "photo_caption": str,
    "crops": [{"image": QImage, "caption": str}], "panel": QImage,
    "panel_caption": str, "ai_picks": [{"label": str, "items": [...]}]} (voir ai_sections). Leve une exception si le fichier ne peut pas etre
    ecrit : l'appelant (export_pdf) la transforme en message.
    """
    if QtWidgets.QApplication.instance() is None:
        raise RuntimeError(i18n.t("err.no_qt"))
    images = images or {}
    total = page_count(data, images)
    title = i18n.t("report.doc_title", chunk=data.get("chunk") or "").strip()
    tmp = path + ".part"
    writer = _new_writer(tmp, title)
    painter = QtGui.QPainter(writer)
    if not painter.isActive():
        _remove(tmp)
        raise IOError(i18n.t("err.cannot_write", path=path))
    try:
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        painter.scale(PT_TO_DEVICE, PT_TO_DEVICE)
        W = writer.width() / PT_TO_DEVICE
        H = writer.height() / PT_TO_DEVICE
        cv = _Canvas(painter, _Style())
        _page_summary(cv, W, H, data, 1, total)
        writer.newPage()
        next_no = _draw_table_pages(writer.newPage, cv, W, H, data, 2, total)
        pages = next_no - 1
        if _has_images(images):
            writer.newPage()
            _page_captures(cv, W, H, data, images, pages + 1, total)
            pages += 1
        for plan in _ai_plan(images.get("ai_picks") or [], W, H):
            writer.newPage()
            _page_ai(cv, W, H, data, plan, pages + 1, total)
            pages += 1
    except Exception:
        painter.end()
        _remove(tmp)
        raise
    painter.end()
    try:
        os.replace(tmp, path)
    except OSError:
        _remove(tmp)
        raise
    return pages


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


# --------------------------------------------------------------------------
# Captures d'ecran (Qt, dans Metashape)
# --------------------------------------------------------------------------

def _to_image(obj):
    """QImage depuis un QPixmap ou un QImage, None si vide."""
    try:
        image = obj.toImage() if hasattr(obj, "toImage") else obj
        return None if image is None or image.isNull() else image
    except Exception:
        return None


def looks_blank(image, grid=48):
    """True si l'image est vide ou n'a qu'une ou deux couleurs (rendu GL rate)."""
    try:
        if image is None or image.isNull():
            return True
        rgb = image.convertToFormat(QtGui.QImage.Format_RGB32)
        w, h = rgb.width(), rgb.height()
        if w < 8 or h < 8:
            return True
        seen = set()
        for j in range(grid):
            for i in range(grid):
                seen.add(int(rgb.pixel(int((i + 0.5) * w / grid), int((j + 0.5) * h / grid))))
                if len(seen) > 2:
                    return False
        return True
    except Exception:
        return True


def _grab_widget(widget):
    try:
        return _to_image(widget.grab())
    except Exception:
        return None


def _grab_screen_region(widget):
    """Plan B : pixels reellement affiches a l'ecran pour ce widget (rendu GL)."""
    try:
        screen = QtGui.QGuiApplication.primaryScreen()
        window = widget.window()
        origin = widget.mapTo(window, QtCore.QPoint(0, 0))
        pixmap = screen.grabWindow(int(window.winId()), origin.x(), origin.y(),
                                   widget.width(), widget.height())
        return _to_image(pixmap)
    except Exception:
        return None


def _grab_photo_view():
    """Capture de la vue photo ouverte, ou None."""
    for widget in msapi.photo_view_widgets():
        for grabber in (_grab_widget, _grab_screen_region):
            image = grabber(widget)
            if image is not None and not looks_blank(image):
                return image
    return None


def _read_region(path, cx, cy, width, height, sensor):
    """Region `width` x `height` (pixels) centree sur (cx, cy) dans le fichier image.

    (cx, cy) est en pixels du capteur Metashape. Renvoie (QImage, cx_local,
    cy_local, echelle fichier/capteur) ou None si le fichier est illisible ou
    n'a pas le meme format que le capteur.
    """
    if not path or not os.path.isfile(path):
        return None
    reader = QtGui.QImageReader(path)
    size = reader.size()
    if not size.isValid():
        full = QtGui.QImage(path)
        if full.isNull():
            return None
        size = full.size()
    iw, ih = size.width(), size.height()
    sw, sh = sensor
    scale = 1.0
    if sw and sh:
        if abs(iw / float(ih) - sw / float(sh)) > 0.02:
            return None
        scale = iw / float(sw)
    fx, fy = cx * scale, cy * scale
    rw, rh = int(min(width, iw)), int(min(height, ih))
    x0 = int(max(0, min(iw - rw, round(fx - rw / 2.0))))
    y0 = int(max(0, min(ih - rh, round(fy - rh / 2.0))))
    rect = QtCore.QRect(x0, y0, rw, rh)
    reader = QtGui.QImageReader(path)
    reader.setClipRect(rect)
    image = reader.read()
    if image.isNull() or image.width() != rw or image.height() != rh:
        image = QtGui.QImage(path).copy(rect)
    if image.isNull():
        return None
    return image, fx - x0, fy - y0, scale


def _annotate(image, zoom, posed, estimated):
    """Agrandit la vignette, dessine le point pose (cercle) et l'estime (croix)."""
    if zoom != 1:
        image = image.scaled(image.width() * zoom, image.height() * zoom,
                             QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
    big = image.convertToFormat(QtGui.QImage.Format_RGB32)
    painter = QtGui.QPainter(big)
    try:
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        unit = max(1.0, big.width() / 150.0)
        px, py = posed[0] * zoom, posed[1] * zoom
        if estimated is not None:
            ex, ey = estimated[0] * zoom, estimated[1] * zoom
            if math.hypot(ex - px, ey - py) > 4 * unit:
                for color, width in (("#ffffff", 1.8 * unit), (ORANGE, 0.8 * unit)):
                    painter.setPen(QtGui.QPen(QtGui.QColor(color), width))
                    painter.drawLine(QtCore.QPointF(px, py), QtCore.QPointF(ex, ey))
            arm = 4.2 * unit
            for color, width in (("#ffffff", 2.0 * unit), (ORANGE, 0.9 * unit)):
                painter.setPen(QtGui.QPen(QtGui.QColor(color), width))
                painter.drawLine(QtCore.QPointF(ex - arm, ey - arm), QtCore.QPointF(ex + arm, ey + arm))
                painter.drawLine(QtCore.QPointF(ex - arm, ey + arm), QtCore.QPointF(ex + arm, ey - arm))
        painter.setBrush(QtCore.Qt.NoBrush)
        for color, width in (("#ffffff", 2.0 * unit), (GREEN, 1.0 * unit)):
            painter.setPen(QtGui.QPen(QtGui.QColor(color), width))
            painter.drawEllipse(QtCore.QPointF(px, py), 6.5 * unit, 6.5 * unit)
    finally:
        painter.end()
    return big


def _vec_xy(vec):
    try:
        return float(vec.x), float(vec.y)
    except AttributeError:
        return float(vec[0]), float(vec[1])


def _marker_views(marker):
    """[(ecart_px ou None, camera, (u, v) pose, (u, v) estime ou None)], ecart decroissant."""
    views = []
    try:
        position = marker.position
    except Exception:
        position = None
    for cam in msapi.projection_cameras(marker, without_proposals=True):
        try:
            posed = _vec_xy(marker.projections[cam].coord)
        except Exception:
            continue
        estimated, gap = None, None
        if position is not None:
            try:
                uv = cam.project(position)
                if uv is not None:
                    estimated = _vec_xy(uv)
                    gap = math.hypot(estimated[0] - posed[0], estimated[1] - posed[1])
            except Exception:
                estimated, gap = None, None
        views.append((gap, cam, posed, estimated))
    views.sort(key=lambda v: -(v[0] if v[0] is not None else -1.0))
    return views


def _crop_for_marker(marker, entry):
    """Vignette annotee et sa legende pour un marqueur, ou None."""
    for gap, cam, posed, estimated in _marker_views(marker):
        region = _read_region(msapi.camera_image_path(cam), posed[0], posed[1],
                              CROP_W, CROP_H, msapi.camera_size(cam))
        if region is None:
            continue
        image, lx, ly, scale = region
        est_local = None
        if estimated is not None:
            est_local = (lx + (estimated[0] - posed[0]) * scale,
                         ly + (estimated[1] - posed[1]) * scale)
        big = _annotate(image, CROP_ZOOM, (lx, ly), est_local)
        first = entry["label"]
        if entry["total"] is not None:
            first = i18n.t("report.shots.crop_error", label=entry["label"],
                           value=fmt_m(entry["total"]))
        second = msapi.camera_label(cam)
        if gap is not None:
            second = i18n.t("report.shots.crop_gap", camera=second, gap=fmt_num(gap, 1))
        return {"image": big, "caption": [first, second]}
    return None


def _pick_crops(chk, data, limit=2):
    """Vignettes des marqueurs les plus ecartes, sinon les moins pointes."""
    by_label = {}
    for marker in msapi.markers(chk):
        by_label.setdefault(msapi.marker_label(marker), marker)
    pointed = [m for m in data["markers"] if m["pointed"]]
    with_error = [m for m in pointed if m["total"] is not None]
    if with_error:
        ranked = sorted(with_error, key=lambda m: -m["total"])
    else:
        ranked = sorted(pointed, key=lambda m: m["projections"])
    crops = []
    for entry in ranked:
        marker = by_label.get(entry["label"])
        if marker is None:
            continue
        try:
            crop = _crop_for_marker(marker, entry)
        except Exception as exc:
            journal.write("Rapport : vignette de {} ignorée : {!r}".format(entry["label"], exc))
            continue
        if crop is not None:
            crops.append(crop)
        if len(crops) >= limit:
            break
    return crops


def _photo_fallback(state):
    """Plan B de la vue photo : recadrage large de l'image ouverte autour du marqueur courant."""
    marker = getattr(state, "active_marker", None)
    cam = msapi.current_camera()
    if marker is None or cam is None:
        return None, ""
    try:
        posed = _vec_xy(marker.projections[cam].coord)
    except Exception:
        return None, ""
    region = _read_region(msapi.camera_image_path(cam), posed[0], posed[1], 1500, 1000,
                          msapi.camera_size(cam))
    if region is None:
        return None, ""
    image, lx, ly, _scale = region
    big = _annotate(image, 1, (lx, ly), None)
    return big, i18n.t("report.shots.fallback", camera=msapi.camera_label(cam),
                       marker=msapi.marker_label(marker))


def collect_images(state, data):
    """Captures pour la page 3. Chaque capture ratee est simplement absente."""
    images = {}
    chk = msapi.chunk()
    try:
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.processEvents()
    except Exception:
        pass

    try:
        photo = _grab_photo_view()
        caption = ""
        if photo is not None:
            cam = msapi.current_camera()
            if cam is not None:
                caption = i18n.t("report.shots.photo_cam", camera=msapi.camera_label(cam))
            else:
                caption = i18n.t("report.shots.photo")
            marker = getattr(state, "active_marker", None)
            if marker is not None:
                try:
                    caption = i18n.t("report.shots.photo_marker", caption=caption,
                                     marker=msapi.marker_label(marker))
                except Exception:
                    pass
        else:
            photo, caption = _photo_fallback(state)
        if photo is not None:
            images["photo"], images["photo_caption"] = photo, caption
    except Exception as exc:
        journal.write("Rapport : capture de la vue photo ignorée : {!r}".format(exc))

    try:
        crops = _pick_crops(chk, data)
        if crops:
            images["crops"] = crops
    except Exception as exc:
        journal.write("Rapport : vignettes ignorées : {!r}".format(exc))

    try:
        picks = ai_sections(state, data)
        if picks:
            images["ai_picks"] = picks
    except Exception as exc:
        journal.write("Rapport : photos pointées par l'IA ignorées : {!r}".format(exc))

    try:
        panel = getattr(state, "panel", None)
        if panel is not None:
            image = _grab_widget(panel)
            if image is not None and not looks_blank(image):
                images["panel"] = image
                images["panel_caption"] = i18n.t("report.shots.panel")
    except Exception as exc:
        journal.write("Rapport : capture du panneau ignorée : {!r}".format(exc))
    return images


# --------------------------------------------------------------------------
# Dialogues (isoles pour pouvoir les remplacer dans les tests)
# --------------------------------------------------------------------------

def ask_save_path(parent, caption, default_path, name_filter):
    """Chemin choisi par l'utilisateur, ou chaine vide."""
    result = QtWidgets.QFileDialog.getSaveFileName(parent, caption, default_path, name_filter)
    path = result[0] if isinstance(result, (tuple, list)) else result
    return str(path or "")


def _exec(box):
    runner = getattr(box, "exec_", None) or getattr(box, "exec")
    return runner()


def show_message(parent, title, text, warning=False):
    try:
        box = QtWidgets.QMessageBox(
            QtWidgets.QMessageBox.Warning if warning else QtWidgets.QMessageBox.Information,
            title, text, QtWidgets.QMessageBox.Ok, parent)
        _exec(box)
    except Exception:
        msapi.message_box("{} : {}".format(title, text))


def offer_open(parent, title, path):
    """Petite boite avec un bouton pour ouvrir le fichier. Renvoie True si ouvert."""
    try:
        box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Information, title,
                                    i18n.t("dlg.saved", path=path),
                                    QtWidgets.QMessageBox.NoButton, parent)
        open_button = box.addButton(i18n.t("dlg.open"), QtWidgets.QMessageBox.AcceptRole)
        box.addButton(i18n.t("dlg.close"), QtWidgets.QMessageBox.RejectRole)
        _exec(box)
        if box.clickedButton() is open_button:
            return open_file(path)
    except Exception as exc:
        journal.write("Rapport : ouverture proposée impossible : {!r}".format(exc))
    return False


def open_file(path):
    try:
        return bool(QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path)))
    except Exception:
        return False


def _busy(on):
    try:
        if on:
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        else:
            QtWidgets.QApplication.restoreOverrideCursor()
    except Exception:
        pass


def _status(state, text):
    panel = getattr(state, "panel", None)
    setter = getattr(panel, "set_status", None)
    if setter is not None:
        try:
            setter(text)
        except Exception:
            pass


def _explain(exc):
    if isinstance(exc, PermissionError):
        return i18n.t("err.denied")
    return str(exc) or exc.__class__.__name__


def _fail(state, parent, kind, exc):
    """Echec d'un export (`kind` : "pdf" ou "csv") : journal, statut, message.

    Le journal technique reste en francais quelle que soit la langue choisie.
    """
    journal.write("Rapport : Export {} impossible : {!r}".format(str(kind).upper(), exc))
    what = i18n.t("err." + kind)
    _status(state, what)
    show_message(parent, what, _explain(exc), warning=True)


def _prepare(state, parent, ext, caption, name_filter, kind):
    """Chunk, donnees et chemin choisi. Renvoie (data, path) ou (None, None)."""
    chk = msapi.chunk()
    if chk is None:
        show_message(parent, i18n.t("dlg.title"), i18n.t("dlg.no_chunk"), warning=True)
        return None, None
    data = collect_data(chk)
    if not data["markers"]:
        journal.write("Rapport : aucun marqueur dans le chunk {}".format(data["chunk"]))
        show_message(parent, i18n.t("dlg.title"), i18n.t("dlg.no_markers"), warning=True)
        return None, None
    default = os.path.join(default_directory(), suggested_filename(data["chunk"], ext, kind=kind))
    path = ask_save_path(parent, caption, default, name_filter)
    if not path:
        return None, None
    if not path.lower().endswith(ext):
        path += ext
    return data, path


# --------------------------------------------------------------------------
# Points d'entree du panneau
# --------------------------------------------------------------------------

def export_pdf(state, parent=None):
    """Demande le fichier, ecrit le rapport PDF. Renvoie le chemin, ou None.

    Ne leve jamais : une erreur va au journal et dans un message lisible.
    """
    try:
        data, path = _prepare(state, parent, ".pdf", i18n.t("dlg.save_pdf"),
                              i18n.t("dlg.filter_pdf"), i18n.t("file.report"))
        if data is None:
            return None
        _busy(True)
        try:
            images = collect_images(state, data)
            pages = build_pdf(data, path, images)
        finally:
            _busy(False)
        journal.write("Rapport PDF écrit : {} ({} pages, {} marqueurs)".format(
            path, pages, data["stats"]["n_markers"]))
        done = i18n.t("dlg.pdf_done")
        _status(state, done)
        offer_open(parent, done, path)
        return path
    except Exception as exc:
        _fail(state, parent, "pdf", exc)
        return None


def export_csv(state, parent=None):
    """Meme logique pour le CSV du tableau (Excel). Renvoie le chemin, ou None."""
    try:
        data, path = _prepare(state, parent, ".csv", i18n.t("dlg.save_csv"),
                              i18n.t("dlg.filter_csv"), i18n.t("file.table"))
        if data is None:
            return None
        write_csv(data, path)
        journal.write("Tableau CSV écrit : {} ({} marqueurs)".format(
            path, data["stats"]["n_markers"]))
        done = i18n.t("dlg.csv_done")
        _status(state, done)
        offer_open(parent, done, path)
        return path
    except Exception as exc:
        _fail(state, parent, "csv", exc)
        return None
