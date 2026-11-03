"""Autotest Qt du rapport PDF de FastGCP : genere un vrai PDF, hors Metashape.

Lancement (aucun pytest requis) :
    python -B tests_qt\\run_qt_report.py

Fonctionne avec le Python embarque de Metashape (PySide2) comme avec un Python
equipe de PySide6. Un faux module `Metashape` est injecte avant l'import
d'AeroTools : 30 marqueurs fictifs (dont quelques-uns sans erreur et quelques-uns
tres ecartes), 9 cameras dont 3 ont une vraie image JPEG, une fausse vue photo.

Variables d'environnement :
  AEROTOOLS_REPORT_OUT   dossier de sortie des PDF (sinon un dossier temporaire)
  AEROTOOLS_SELFTEST_VERBOSE=1   garde le journal

Sortie : une ligne PASS / FAIL par cas, code de sortie non nul au moindre FAIL.
"""

import datetime
import os
import random
import re
import subprocess
import sys
import tempfile
import traceback
import types

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="aerotools_qt_report_")
OUT = os.environ.get("AEROTOOLS_REPORT_OUT") or tempfile.mkdtemp(prefix="aerotools_report_")
os.makedirs(OUT, exist_ok=True)


def _prepare_qt_environment():
    """DLL de Metashape si on tourne dans son Python, plateforme hors ecran si possible."""
    metashape_dir = os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))
    has_qt = os.path.exists(os.path.join(metashape_dir, "Qt5Core.dll"))
    offscreen = True
    if has_qt:
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(metashape_dir)
        os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(metashape_dir, "plugins"))
        offscreen = os.path.exists(
            os.path.join(metashape_dir, "plugins", "platforms", "qoffscreen.dll"))
    if offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    return "offscreen" if offscreen else "native"


PLATFORM = _prepare_qt_environment()


# --------------------------------------------------------------------------
# Faux Metashape
# --------------------------------------------------------------------------

class Vector(object):
    def __init__(self, *args):
        values = list(args[0]) if len(args) == 1 else list(args)
        values = (values + [0.0, 0.0, 0.0])[:3]
        self.x, self.y, self.z = [float(v) for v in values]
        self.truth = {}

    def __getitem__(self, index):
        return (self.x, self.y, self.z)[index]


class Projection(object):
    def __init__(self, coord=None, pinned=False):
        self.coord = coord
        self.pinned = pinned


class _Reference(object):
    location = None


class Marker(object):
    Projection = Projection

    def __init__(self, label, position=None):
        self.label = label
        self.position = position
        self.projections = {}
        self.reference = _Reference()
        self.selected = False


class _Sensor(object):
    def __init__(self, width, height):
        self.width, self.height = width, height


class _Photo(object):
    def __init__(self, path):
        self.path = path


class Camera(object):
    def __init__(self, key, path):
        self.key = key
        self.label = "IMG_{:04d}".format(key)
        self.enabled = True
        self.sensor = _Sensor(4000, 3000)
        self.photo = _Photo(path)
        self.transform = object()

    def project(self, point):
        uv = point.truth.get(self.key)
        return Vector(uv[0], uv[1], 0) if uv else None


class _ChunkTransform(object):
    scale = 1.0
    matrix = None


class Chunk(object):
    def __init__(self, cameras, markers):
        self.cameras = list(cameras)
        self.markers = list(markers)
        self.transform = _ChunkTransform()
        self.crs = None
        self.key = 1
        self.label = "Chunk 1 - Levé éolien"


class PhotoView(object):
    def __init__(self, width, height):
        self.camera = None
        self.width = width
        self.height = height
        self.scale = 0.2
        self.center = Vector(2000.0, 1500.0)

    def open(self, camera):
        self.camera = camera


class FakeApp(object):
    version = "2.2.1-report"

    def __init__(self):
        self.document = types.SimpleNamespace(chunk=None, path="")
        self.photo_view = None

    def update(self):
        pass

    def messageBox(self, text):
        print(text)


FAKE = types.ModuleType("Metashape")
FAKE.Vector = Vector
FAKE.Marker = Marker
FAKE.Camera = Camera
FAKE.Chunk = Chunk
FAKE.app = FakeApp()
sys.modules["Metashape"] = FAKE


from aerotools import i18n, journal, msapi  # noqa: E402
from aerotools.qt import BINDING, QtCore, QtGui, QtWidgets  # noqa: E402
from aerotools.tools.fastgcp import report  # noqa: E402

if not os.environ.get("AEROTOOLS_SELFTEST_VERBOSE"):
    journal.print = lambda *args, **kwargs: None

FAILURES = []


def check(name, condition, detail=""):
    print("{} {}{}".format("PASS" if condition else "FAIL", name,
                           "" if condition else " : " + str(detail)))
    if not condition:
        FAILURES.append(name)


# --------------------------------------------------------------------------
# Donnees fictives
# --------------------------------------------------------------------------

N_CAMERAS = 9
N_MARKERS = 30
REAL_IMAGES = 3
BIG = {6: (0.162, -0.118, 0.205), 18: (-0.121, 0.094, -0.088), 11: (0.074, 0.081, 0.052)}
NO_ERROR = {24, 25, 26}      # marqueurs jamais triangules : pas d'erreur, pas de projection


def make_photo(path, seed):
    """Photo fictive 4000 x 3000 : champ, chemin, cible quadrillee a (2000, 1500)."""
    rnd = random.Random(seed)
    image = QtGui.QImage(4000, 3000, QtGui.QImage.Format_RGB32)
    painter = QtGui.QPainter(image)
    gradient = QtGui.QLinearGradient(0, 0, 0, 3000)
    gradient.setColorAt(0.0, QtGui.QColor("#7d9a55"))
    gradient.setColorAt(1.0, QtGui.QColor("#a89a6a"))
    painter.fillRect(0, 0, 4000, 3000, QtGui.QBrush(gradient))
    painter.setPen(QtCore.Qt.NoPen)
    for _ in range(900):
        painter.setBrush(QtGui.QColor(rnd.randint(70, 150), rnd.randint(100, 170),
                                      rnd.randint(40, 100), 90))
        painter.drawEllipse(rnd.randint(0, 4000), rnd.randint(0, 3000),
                            rnd.randint(30, 160), rnd.randint(20, 100))
    painter.setBrush(QtGui.QColor("#d9d2bd"))
    painter.drawRect(0, 1380, 4000, 220)
    for cx, cy in ((2000, 1500), (1300, 800), (2800, 2100)):
        for i in range(2):
            for j in range(2):
                painter.setBrush(QtGui.QColor("#101010" if (i + j) % 2 == 0 else "#f4f4f4"))
                painter.drawRect(cx - 50 + i * 50, cy - 50 + j * 50, 50, 50)
    painter.end()
    image.save(path, "JPG", 88)


def build_world(folder):
    rnd = random.Random(11)
    cameras = []
    for key in range(1, N_CAMERAS + 1):
        path = os.path.join(folder, "img_{}.jpg".format(key))
        if key <= REAL_IMAGES:
            make_photo(path, key)
        else:
            path = os.path.join(folder, "absente_{}.jpg".format(key))
        cameras.append(Camera(key, path))
    markers = []
    for index in range(N_MARKERS):
        label = "GCP{:02d}".format(index + 1)
        if index == 8:
            label += "_PILE_PONT_RD117_AMONT_RIVE_GAUCHE"
        marker = Marker(label)
        position = Vector(100.0 + index, 200.0, 5.0)
        marker.position = position
        if index not in NO_ERROR:
            n_proj = rnd.choice([2, 3, 3, 4, 4, 5, 5, 6, 7, 8, 9]) if index != 4 else 1
            if index == 18:
                n_proj = 2
            for cam in cameras[:n_proj]:
                u = 2000.0 + rnd.uniform(-300, 300)
                v = 1500.0 + rnd.uniform(-200, 200)
                if cam.key == 1:
                    u, v = 2000.0 + 8.0, 1500.0 - 6.0
                marker.projections[cam] = Projection(Vector(u, v), True)
                gap = 10.0 if index in BIG else 1.5
                position.truth[cam.key] = (u + rnd.uniform(-gap, gap) + (7 if index in BIG else 0),
                                           v + rnd.uniform(-gap, gap) - (5 if index in BIG else 0))
        marker.reference.location = Vector(500000.0 + index * 10.0, 4700000.0 + index * 3.0, 120.0)
        markers.append(marker)
    return Chunk(cameras, markers), markers


def fake_errors_factory(markers):
    rnd = random.Random(5)
    table = {}
    for index, marker in enumerate(markers):
        if index in NO_ERROR:
            continue
        if index in BIG:
            x, y, z = BIG[index]
        else:
            x, y, z = rnd.gauss(0, 0.02), rnd.gauss(0, 0.02), rnd.gauss(0, 0.035)
        table[id(marker)] = {"x": x, "y": y, "z": z,
                             "total": (x * x + y * y + z * z) ** 0.5}

    def fake(_chk, marker):
        return table.get(id(marker), {"x": None, "y": None, "z": None, "total": None})
    return fake


class PhotoWidget(QtWidgets.QWidget):
    def __init__(self, image_path, parent=None):
        super(PhotoWidget, self).__init__(parent)
        self.resize(1000, 640)
        self.image = QtGui.QImage(image_path).scaled(
            1000, 750, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.drawImage(0, -55, self.image)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setPen(QtGui.QPen(QtGui.QColor("#1eca59"), 2))
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.drawEllipse(QtCore.QPointF(502, 320), 9, 9)
        painter.end()


def make_stand_in_panel(markers):
    """Faux panneau (titre, liste, bouton) quand le vrai panneau ne se construit pas."""
    widget = QtWidgets.QWidget()
    widget.setStyleSheet("background: #f6f6f4; color: #111214;")
    layout = QtWidgets.QVBoxLayout(widget)
    layout.addWidget(QtWidgets.QLabel("FastGCP"))
    listing = QtWidgets.QListWidget()
    for marker in markers[:20]:
        listing.addItem("{}    {} projections".format(marker.label, len(marker.projections)))
    layout.addWidget(listing)
    layout.addWidget(QtWidgets.QPushButton("Exporter"))
    widget.resize(330, 780)
    widget.show()
    QtWidgets.QApplication.processEvents()
    return widget


# --------------------------------------------------------------------------
# Verifications d'un PDF
# --------------------------------------------------------------------------

def read_pdf(path):
    with open(path, "rb") as handle:
        return handle.read()


def pdf_pages(raw):
    return len(re.findall(rb"/Type\s*/Page(?![s\w])", raw))


def check_pdf(name, path, pages=None):
    ok = os.path.isfile(path) and os.path.getsize(path) > 0
    check("{} : fichier non vide".format(name), ok, path)
    if not ok:
        return None
    raw = read_pdf(path)
    check("{} : en-tete %PDF".format(name), raw[:5] == b"%PDF-", raw[:8])
    count = pdf_pages(raw)
    if pages is not None:
        check("{} : {} pages".format(name, pages), count == pages, "trouve {}".format(count))
    check("{} : polices TrueType embarquees".format(name), b"/FontFile2" in raw)
    check("{} : pas de reste .part".format(name), not os.path.exists(path + ".part"))
    return raw


def pdf_title(raw):
    """Titre du document (champ /Title), en UTF-16 avec BOM ou en hexadecimal."""
    match = re.search(rb"/Title\s*\((.*?)(?<!\\)\)", raw, re.S)
    if match:
        data = match.group(1)
        if data[:2] == b"\xfe\xff":
            return data[2:].decode("utf-16-be", "replace")
        return data.decode("latin-1", "replace")
    match = re.search(rb"/Title\s*<([0-9A-Fa-f\s]+)>", raw)
    if match:
        data = bytes.fromhex(re.sub(rb"\s+", b"", match.group(1)).decode("ascii"))
        if data[:2] == b"\xfe\xff":
            return data[2:].decode("utf-16-be", "replace")
        return data.decode("latin-1", "replace")
    return ""


_EXTRACT_SCRIPT = (
    "import sys\n"
    "try:\n"
    "    import fitz\n"
    "    d = fitz.open(sys.argv[1])\n"
    "    text = '\\n'.join(p.get_text() for p in d)\n"
    "except ImportError:\n"
    "    import pypdf\n"
    "    text = '\\n'.join(p.extract_text() for p in pypdf.PdfReader(sys.argv[1]).pages)\n"
    "sys.stdout.buffer.write(text.encode('utf-8'))\n"
)


def pdf_text(path):
    """Texte des pages d'un PDF, ou None si aucun extracteur n'est disponible.

    Essaie PyMuPDF puis pypdf dans ce Python, puis dans le `python` du PATH
    (le Python embarque de Metashape n'a ni l'un ni l'autre). Les titres du
    rapport sont peints en contours : seul le texte courant est extrait.
    """
    try:
        import fitz
        return "\n".join(page.get_text() for page in fitz.open(path))
    except Exception:
        pass
    try:
        import pypdf
        return "\n".join(page.extract_text() for page in pypdf.PdfReader(path).pages)
    except Exception:
        pass
    for exe in ("python", "py"):
        try:
            done = subprocess.run([exe, "-c", _EXTRACT_SCRIPT, path],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        except Exception:
            continue
        if done.returncode == 0 and done.stdout:
            return done.stdout.decode("utf-8", "replace")
    return None


def ensure_fonts():
    """Charge Segoe UI depuis le dossier Windows si la plateforme (offscreen) n'a aucune police."""
    try:
        families = set(QtGui.QFontDatabase.families())
    except Exception:
        families = set(QtGui.QFontDatabase().families())
    if "Segoe UI" in families:
        return
    folder = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    for name in ("segoeui", "segoeuib", "seguisb", "seguisym"):
        path = os.path.join(folder, name + ".ttf")
        if os.path.isfile(path):
            QtGui.QFontDatabase.addApplicationFont(path)


def squash(text):
    """Texte sur une ligne, espaces normalises."""
    return re.sub(r"\s+", " ", text or "")


# --------------------------------------------------------------------------
# Cas
# --------------------------------------------------------------------------

def main():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    print("liaison Qt : {} ; plateforme : {} ; Python {}".format(
        BINDING, PLATFORM, sys.version.split()[0]))
    print("sortie : {}".format(OUT))

    i18n.set_lang("fr")
    ensure_fonts()
    work = tempfile.mkdtemp(prefix="aerotools_photos_")
    chunk, markers = build_world(work)
    FAKE.app.document.chunk = chunk
    FAKE.app.photo_view = PhotoView(1000, 640)
    FAKE.app.photo_view.camera = chunk.cameras[0]
    msapi.marker_errors = fake_errors_factory(markers)

    photo_widget = PhotoWidget(chunk.cameras[0].photo.path)
    msapi.photo_view_widgets = lambda: [photo_widget]

    state = types.SimpleNamespace(panel=None, active_marker=markers[6])
    try:
        from aerotools.tools.fastgcp import session as session_mod
        from aerotools.tools.fastgcp.panel import FastGcpPanel
        real = session_mod.Session()
        panel = FastGcpPanel(real, None)
        panel.resize(330, 780)
        panel.show()
        panel.refresh_markers()
        app.processEvents()
        real.panel = panel
        real.active_marker = markers[6]
        state = real
    except Exception:
        traceback.print_exc()
        print("panneau reel indisponible : un widget simple le remplace")
        state.panel = make_stand_in_panel(markers)
    app.processEvents()

    data = report.collect_data(chunk)
    stats = data["stats"]
    check("collect_data : 30 marqueurs", stats["n_markers"] == N_MARKERS, stats["n_markers"])
    check("collect_data : pointes = 27", stats["n_pointed"] == N_MARKERS - len(NO_ERROR),
          stats["n_pointed"])
    check("collect_data : pire marqueur GCP07", stats["worst_label"] == "GCP07",
          stats["worst_label"])

    black = QtGui.QImage(300, 200, QtGui.QImage.Format_RGB32)
    black.fill(QtGui.QColor("black"))
    check("looks_blank : image noire", report.looks_blank(black))
    check("looks_blank : image nulle", report.looks_blank(QtGui.QImage()))
    check("looks_blank : photo", not report.looks_blank(QtGui.QImage(chunk.cameras[0].photo.path)))

    # 1. PDF complet avec captures
    images = report.collect_images(state, data)
    check("captures : vue photo", images.get("photo") is not None)
    check("captures : 2 vignettes", len(images.get("crops") or []) == 2,
          len(images.get("crops") or []))
    check("captures : panneau", images.get("panel") is not None)
    full = os.path.join(OUT, "rapport_complet_{}.pdf".format(BINDING))
    pages = report.build_pdf(data, full, images)
    check("build_pdf renvoie 3 pages", pages == 3, pages)
    check_pdf("PDF complet", full, 3)

    # 2. Sans captures : la page 3 disparait, sans trou
    bare = os.path.join(OUT, "rapport_sans_captures_{}.pdf".format(BINDING))
    check("build_pdf sans captures : 2 pages", report.build_pdf(data, bare, {}) == 2)
    check_pdf("PDF sans captures", bare, 2)

    # 3. Captures partielles : seulement le panneau
    partial = os.path.join(OUT, "rapport_panneau_seul_{}.pdf".format(BINDING))
    only_panel = {"panel": images["panel"], "panel_caption": "Panneau FastGCP"}
    check("build_pdf panneau seul : 3 pages", report.build_pdf(data, partial, only_panel) == 3)
    check_pdf("PDF panneau seul", partial, 3)

    # 4. Beaucoup de marqueurs : le tableau passe sur plusieurs pages
    many_chunk, many_markers = build_world(work)
    extra = []
    for index in range(100):
        marker = Marker("GCP{:03d}".format(index + 100))
        marker.projections = dict(list(many_markers[index % N_MARKERS].projections.items()))
        marker.position = many_markers[index % N_MARKERS].position
        extra.append(marker)
    many_chunk.markers = list(many_markers) + extra
    errors_a = fake_errors_factory(many_markers)

    def errors_many(chk, marker):
        if id(marker) in {id(m) for m in many_markers}:
            return errors_a(chk, marker)
        base = many_markers[int(marker.label[3:]) % N_MARKERS]
        return errors_a(chk, base)
    msapi.marker_errors = errors_many
    FAKE.app.document.chunk = many_chunk
    many_data = report.collect_data(many_chunk)
    many_pdf = os.path.join(OUT, "rapport_130_marqueurs_{}.pdf".format(BINDING))
    expected = report.page_count(many_data, images)
    got = report.build_pdf(many_data, many_pdf, images)
    check("130 marqueurs : pages annoncees = pages ecrites", got == expected, (got, expected))
    check_pdf("PDF 130 marqueurs", many_pdf, expected)
    check("130 marqueurs : plus de 3 pages", expected > 3, expected)
    FAKE.app.document.chunk = chunk
    msapi.marker_errors = fake_errors_factory(markers)

    # 5. Cas degeneres : aucun marqueur, aucune erreur
    empty = os.path.join(OUT, "rapport_vide_{}.pdf".format(BINDING))
    empty_data = report.collect_data(Chunk([], []))
    report.build_pdf(empty_data, empty, {})
    check_pdf("PDF sans marqueur", empty, 2)
    msapi.marker_errors = lambda _c, _m: {"x": None, "y": None, "z": None, "total": None}
    noerr = os.path.join(OUT, "rapport_sans_erreur_{}.pdf".format(BINDING))
    noerr_data = report.collect_data(chunk)
    report.build_pdf(noerr_data, noerr, {})
    check_pdf("PDF sans aucune erreur", noerr, 2)
    msapi.marker_errors = fake_errors_factory(markers)

    # 6. export_pdf de bout en bout (dialogues remplaces)
    target = os.path.join(OUT, "export_bout_en_bout_{}.pdf".format(BINDING))
    messages = []
    report.ask_save_path = lambda parent, caption, default, flt: target
    report.show_message = lambda parent, title, text, warning=False: messages.append((title, text))
    report.offer_open = lambda parent, title, path: messages.append(("ouvrir", path)) or False
    journal.clear()
    result = report.export_pdf(state)
    check("export_pdf renvoie le chemin", result == target, result)
    check_pdf("export_pdf", target, 3)
    check("export_pdf : une ligne de journal", any("Rapport PDF écrit" in line
                                                   for line in journal.lines()))
    check("export_pdf : propose d'ouvrir", ("ouvrir", target) in messages, messages)

    # 7. Annulation : None, rien d'ecrit, pas de message
    messages[:] = []
    report.ask_save_path = lambda *args: ""
    check("export_pdf annule : None", report.export_pdf(state) is None)
    check("export_pdf annule : silencieux", not messages, messages)

    # 8. Echec d'ecriture : None, message lisible, pas d'exception
    bad = os.path.join(OUT, "dossier_absent", "x.pdf")
    report.ask_save_path = lambda *args: bad
    messages[:] = []
    journal.clear()
    check("export_pdf echec : None", report.export_pdf(state) is None)
    check("export_pdf echec : message", any("Export PDF impossible" in m[0] for m in messages),
          messages)
    check("export_pdf echec : journal", any("Rapport" in line for line in journal.lines()))

    # 9. CSV de bout en bout
    csv_path = os.path.join(OUT, "tableau_{}.csv".format(BINDING))
    report.ask_save_path = lambda *args: csv_path
    messages[:] = []
    check("export_csv renvoie le chemin", report.export_csv(state) == csv_path)
    with open(csv_path, "rb") as handle:
        raw = handle.read()
    check("export_csv : BOM UTF-8", raw[:3] == b"\xef\xbb\xbf")
    lines = raw.decode("utf-8-sig").split("\r\n")
    check("export_csv : 30 lignes + en-tete", len([l for l in lines if l]) == N_MARKERS + 1,
          len(lines))
    check("export_csv : separateur point-virgule", lines[0].startswith("ID;Erreur X (m);"),
          lines[0])
    check("export_csv : virgule decimale", re.search(r"GCP07;0,162;-0,118;0,205;", lines[7])
          is not None, lines[7])

    # 10. Aucun marqueur : message, None
    FAKE.app.document.chunk = Chunk([], [])
    messages[:] = []
    report.ask_save_path = lambda *args: target
    check("export_pdf sans marqueur : None", report.export_pdf(state) is None)
    check("export_pdf sans marqueur : message", bool(messages))
    FAKE.app.document.chunk = chunk

    # 11. Langue : le rapport lit la langue au moment de l'export
    FAKE.app.document.chunk = chunk
    msapi.marker_errors = fake_errors_factory(markers)
    today = datetime.date.today()
    stamp = today.strftime("%Y-%m-%d")
    cases = {
        "en": {
            "title": "Report", "absent_title": "Rapport",
            "present": ["Page 1 of 3", "Total error", "Error X (m)", "Mean", "Median",
                        "in meters", "current marker", "0.287", stamp],
            "absent": ["Erreur totale", "Moyenne", "Page 1 / 3", "0,287", "Rapport"],
            "pdf_name": "{}_GCP_report_Chunk_1_-_Leve_eolien.pdf".format(stamp),
            "csv_name": "{}_GCP_table_Chunk_1_-_Leve_eolien.csv".format(stamp),
            "csv_head": "ID,Error X (m),Error Y (m),Error Z (m),Total error (m),Projections,"
                        "Reference X,Reference Y,Reference Z",
            "csv_row": re.compile(r"^GCP07,0\.162,-0\.118,0\.205,"),
            "dialog_title": "PDF export failed",
            "filter": "PDF files",
        },
        "fr": {
            "title": "Rapport", "absent_title": "Report",
            "present": ["Page 1 / 3", "Erreur totale", "Erreur X (m)", "Moyenne",
                        "Médiane", "en mètres", "marqueur courant", "0,287",
                        today.strftime("%d/%m/%Y")],
            "absent": ["Total error", "Mean", "Page 1 of 3", "0.287"],
            "pdf_name": "{}_Rapport_GCP_Chunk_1_-_Leve_eolien.pdf".format(stamp),
            "csv_name": "{}_Tableau_GCP_Chunk_1_-_Leve_eolien.csv".format(stamp),
            "csv_head": "ID;Erreur X (m);Erreur Y (m);Erreur Z (m);Erreur totale (m);Projections;"
                        "X référence;Y référence;Z référence",
            "csv_row": re.compile(r"^GCP07;0,162;-0,118;0,205;"),
            "dialog_title": "Export PDF impossible",
            "filter": "Fichiers PDF",
        },
    }
    for code in ("en", "fr"):
        expect = cases[code]
        i18n.set_lang(code)
        tag = code.upper()

        # PDF construit directement : contenu dans la langue
        lang_data = report.collect_data(chunk)
        lang_images = report.collect_images(state, lang_data)
        lang_pdf = os.path.join(OUT, "rapport_langue_{}_{}.pdf".format(code, BINDING))
        count = report.build_pdf(lang_data, lang_pdf, lang_images)
        check("langue {} : build_pdf renvoie 3 pages".format(tag), count == 3, count)
        raw = check_pdf("PDF {}".format(tag), lang_pdf, 3)
        title = pdf_title(raw) if raw else ""
        check("langue {} : titre du document contient {!r}".format(tag, expect["title"]),
              expect["title"] in title and expect["absent_title"] not in title, title)
        text = pdf_text(lang_pdf)
        if text is None:
            print("SKIP langue {} : texte du PDF non verifie (ni PyMuPDF ni pypdf)".format(tag))
        else:
            flat = squash(text)
            for needle in expect["present"]:
                check("langue {} : le PDF contient {!r}".format(tag, needle), needle in flat)
            for needle in expect["absent"]:
                check("langue {} : le PDF ne contient pas {!r}".format(tag, needle),
                      needle not in flat)

        # CSV par le vrai point d'entree
        seen = {}
        messages[:] = []
        csv_lang = os.path.join(OUT, "tableau_langue_{}_{}.csv".format(code, BINDING))

        def ask_csv(parent, caption, default, flt, _seen=seen, _path=csv_lang):
            _seen.update(default=default, caption=caption, filter=flt)
            return _path
        report.ask_save_path = ask_csv
        check("langue {} : export_csv renvoie le chemin".format(tag),
              report.export_csv(state) == csv_lang)
        check("langue {} : nom du CSV propose".format(tag),
              os.path.basename(seen["default"]) == expect["csv_name"], seen["default"])
        with open(csv_lang, "rb") as handle:
            raw_csv = handle.read()
        check("langue {} : CSV en UTF-8 avec BOM".format(tag), raw_csv[:3] == b"\xef\xbb\xbf")
        rows = raw_csv.decode("utf-8-sig").split("\r\n")
        check("langue {} : en-tete du CSV".format(tag), rows[0] == expect["csv_head"], rows[0])
        check("langue {} : ligne GCP07 du CSV".format(tag),
              expect["csv_row"].search(rows[7]) is not None, rows[7])
        body = "".join(rows[1:])
        if code == "en":
            check("langue EN : aucun point-virgule ni virgule decimale dans le CSV",
                  ";" not in body and re.search(r"\d,\d{3}(,|$)", body) is None, rows[7])
        else:
            check("langue FR : aucun point decimal dans le CSV",
                  re.search(r"\d\.\d", body) is None, rows[7])

        # PDF par le vrai point d'entree : nom propose, boite d'enregistrement, journal
        pdf_target = os.path.join(OUT, "export_langue_{}_{}.pdf".format(code, BINDING))

        def ask_pdf(parent, caption, default, flt, _seen=seen, _path=pdf_target):
            _seen.update(default=default, caption=caption, filter=flt)
            return _path
        report.ask_save_path = ask_pdf
        journal.clear()
        check("langue {} : export_pdf renvoie le chemin".format(tag),
              report.export_pdf(state) == pdf_target)
        check("langue {} : nom du PDF propose".format(tag),
              os.path.basename(seen["default"]) == expect["pdf_name"], seen["default"])
        check("langue {} : boite d'enregistrement dans la langue".format(tag),
              seen["caption"] == i18n.t("dlg.save_pdf")
              and seen["filter"].startswith(expect["filter"]), (seen["caption"], seen["filter"]))
        check("langue {} : proposition d'ouvrir le fichier".format(tag),
              ("ouvrir", pdf_target) in messages, messages)
        check("langue {} : le journal reste en francais".format(tag),
              any("Rapport PDF écrit" in line for line in journal.lines()))
        messages[:] = []
        report.ask_save_path = lambda *args: os.path.join(OUT, "dossier_absent", "x.pdf")
        report.export_pdf(state)
        check("langue {} : message d'echec dans la langue".format(tag),
              any(m[0] == expect["dialog_title"] for m in messages), messages)
    i18n.set_lang("fr")

    print("-" * 60)
    print("liaison Qt : {} ; fichiers dans {}".format(BINDING, OUT))
    if FAILURES:
        print("{} cas en echec".format(len(FAILURES)))
        return 1
    print("tous les cas passent")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception:
        traceback.print_exc()
        code = 1
    sys.exit(code)
