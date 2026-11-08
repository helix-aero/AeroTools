"""Qt self-test of the FastGCP PDF report: generates a real PDF, outside Metashape.

Run (no pytest required):
    python -B tests_qt\\run_qt_report.py

Works with the Python embedded in Metashape (PySide2) as well as with a Python
equipped with PySide6. A fake `Metashape` module is injected before AeroTools is
imported: 30 fake markers (a few without error and a few with large errors),
9 cameras of which 3 have a real JPEG image, a fake photo view.

Environment variables:
  AEROTOOLS_REPORT_OUT   output folder of the PDFs (otherwise a temporary folder)
  AEROTOOLS_SELFTEST_VERBOSE=1   keeps the journal

Output: one PASS / FAIL line per case, non-zero exit code on any FAIL.
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
sys.path.insert(0, HERE)
import _isolation  # noqa: E402
GUARD = _isolation.Guard("aerotools_qt_report_")                      # settings of the machine stay untouched
OUT = os.environ.get("AEROTOOLS_REPORT_OUT") or tempfile.mkdtemp(prefix="aerotools_report_")
os.makedirs(OUT, exist_ok=True)


def _prepare_qt_environment():
    """Metashape DLLs if running in its Python, offscreen platform if possible."""
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
# Fake Metashape
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
        self.label = "Chunk 1 - Survey Z\u00fcrich"


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


from aerotools import config, i18n, journal, msapi  # noqa: E402
GUARD.pin(config)
from aerotools.qt import BINDING, QtCore, QtGui, QtWidgets  # noqa: E402
from aerotools.tools.fastgcp import report  # noqa: E402

if not os.environ.get("AEROTOOLS_SELFTEST_VERBOSE"):
    journal.print = lambda *args, **kwargs: None

FAILURES = []


def check(name, condition, detail=""):
    print("{} {}{}".format("PASS" if condition else "FAIL", name,
                           "" if condition else ": " + str(detail)))
    if not condition:
        FAILURES.append(name)


# --------------------------------------------------------------------------
# Fake data
# --------------------------------------------------------------------------

N_CAMERAS = 9
N_MARKERS = 30
REAL_IMAGES = 3
BIG = {6: (0.162, -0.118, 0.205), 18: (-0.121, 0.094, -0.088), 11: (0.074, 0.081, 0.052)}
NO_ERROR = {24, 25, 26}      # markers never triangulated: no error, no projection


def make_photo(path, seed):
    """Fake 4000 x 3000 photo: field, path, checkered target at (2000, 1500)."""
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
            path = os.path.join(folder, "missing_{}.jpg".format(key))
        cameras.append(Camera(key, path))
    markers = []
    for index in range(N_MARKERS):
        label = "GCP{:02d}".format(index + 1)
        if index == 8:
            label += "_LONG_LABEL_TEST_POINT_UPSTREAM_LEFT_SIDE"
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
    """Fake panel (title, list, button) when the real panel cannot be built."""
    widget = QtWidgets.QWidget()
    widget.setStyleSheet("background: #f6f6f4; color: #111214;")
    layout = QtWidgets.QVBoxLayout(widget)
    layout.addWidget(QtWidgets.QLabel("FastGCP"))
    listing = QtWidgets.QListWidget()
    for marker in markers[:20]:
        listing.addItem("{}    {} projections".format(marker.label, len(marker.projections)))
    layout.addWidget(listing)
    layout.addWidget(QtWidgets.QPushButton("Export"))
    widget.resize(330, 780)
    widget.show()
    QtWidgets.QApplication.processEvents()
    return widget


# --------------------------------------------------------------------------
# PDF checks
# --------------------------------------------------------------------------

def read_pdf(path):
    with open(path, "rb") as handle:
        return handle.read()


def pdf_pages(raw):
    return len(re.findall(rb"/Type\s*/Page(?![s\w])", raw))


def check_pdf(name, path, pages=None):
    ok = os.path.isfile(path) and os.path.getsize(path) > 0
    check("{}: non-empty file".format(name), ok, path)
    if not ok:
        return None
    raw = read_pdf(path)
    check("{}: %PDF header".format(name), raw[:5] == b"%PDF-", raw[:8])
    count = pdf_pages(raw)
    if pages is not None:
        check("{}: {} pages".format(name, pages), count == pages, "found {}".format(count))
    check("{}: TrueType fonts embedded".format(name), b"/FontFile2" in raw)
    check("{}: no .part file left".format(name), not os.path.exists(path + ".part"))
    return raw


def pdf_title(raw):
    """Document title (/Title field), as UTF-16 with BOM or as hexadecimal."""
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
    """Text of the pages of a PDF, or None if no extractor is available.

    Tries PyMuPDF then pypdf in this Python, then in the `python` of the PATH
    (the Python embedded in Metashape has neither). The titles of the
    report are painted as outlines: only the running text is extracted.
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
    """Loads Segoe UI from the Windows folder if the (offscreen) platform has no font."""
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
    """Text on one line, whitespace normalised."""
    return re.sub(r"\s+", " ", text or "")


# --------------------------------------------------------------------------
# Cases
# --------------------------------------------------------------------------

def main():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    print("Qt binding: {}; platform: {}; Python {}".format(
        BINDING, PLATFORM, sys.version.split()[0]))
    print("output: {}".format(OUT))

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
        print("real panel unavailable: a simple widget replaces it")
        state.panel = make_stand_in_panel(markers)
    app.processEvents()

    data = report.collect_data(chunk)
    stats = data["stats"]
    check("collect_data: 30 markers", stats["n_markers"] == N_MARKERS, stats["n_markers"])
    check("collect_data: pointed = 27", stats["n_pointed"] == N_MARKERS - len(NO_ERROR),
          stats["n_pointed"])
    check("collect_data: worst marker GCP07", stats["worst_label"] == "GCP07",
          stats["worst_label"])

    black = QtGui.QImage(300, 200, QtGui.QImage.Format_RGB32)
    black.fill(QtGui.QColor("black"))
    check("looks_blank: black image", report.looks_blank(black))
    check("looks_blank: null image", report.looks_blank(QtGui.QImage()))
    check("looks_blank: photo", not report.looks_blank(QtGui.QImage(chunk.cameras[0].photo.path)))

    # 1. Full PDF with screenshots
    images = report.collect_images(state, data)
    check("screenshots: photo view", images.get("photo") is not None)
    check("screenshots: 2 thumbnails", len(images.get("crops") or []) == 2,
          len(images.get("crops") or []))
    check("screenshots: panel", images.get("panel") is not None)
    full = os.path.join(OUT, "report_full_{}.pdf".format(BINDING))
    pages = report.build_pdf(data, full, images)
    check("build_pdf returns 3 pages", pages == 3, pages)
    check_pdf("Full PDF", full, 3)

    # 2. Without screenshots: page 3 disappears, without a gap
    bare = os.path.join(OUT, "report_no_screenshots_{}.pdf".format(BINDING))
    check("build_pdf without screenshots: 2 pages", report.build_pdf(data, bare, {}) == 2)
    check_pdf("PDF without screenshots", bare, 2)

    # 3. Partial screenshots: only the panel
    partial = os.path.join(OUT, "report_panel_only_{}.pdf".format(BINDING))
    only_panel = {"panel": images["panel"], "panel_caption": "FastGCP panel"}
    check("build_pdf panel only: 3 pages", report.build_pdf(data, partial, only_panel) == 3)
    check_pdf("PDF panel only", partial, 3)

    # 4. Many markers: the table runs over several pages
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
    many_pdf = os.path.join(OUT, "report_130_markers_{}.pdf".format(BINDING))
    expected = report.page_count(many_data, images)
    got = report.build_pdf(many_data, many_pdf, images)
    check("130 markers: announced pages = written pages", got == expected, (got, expected))
    check_pdf("PDF 130 markers", many_pdf, expected)
    check("130 markers: more than 3 pages", expected > 3, expected)
    FAKE.app.document.chunk = chunk
    msapi.marker_errors = fake_errors_factory(markers)

    # 5. Degenerate cases: no marker, no error
    empty = os.path.join(OUT, "report_empty_{}.pdf".format(BINDING))
    empty_data = report.collect_data(Chunk([], []))
    report.build_pdf(empty_data, empty, {})
    check_pdf("PDF without markers", empty, 2)
    msapi.marker_errors = lambda _c, _m: {"x": None, "y": None, "z": None, "total": None}
    noerr = os.path.join(OUT, "report_no_error_{}.pdf".format(BINDING))
    noerr_data = report.collect_data(chunk)
    report.build_pdf(noerr_data, noerr, {})
    check_pdf("PDF without any error", noerr, 2)
    msapi.marker_errors = fake_errors_factory(markers)

    # 6. export_pdf end to end (dialogs replaced)
    target = os.path.join(OUT, "export_end_to_end_{}.pdf".format(BINDING))
    messages = []
    report.ask_save_path = lambda parent, caption, default, flt: target
    report.show_message = lambda parent, title, text, warning=False: messages.append((title, text))
    report.offer_open = lambda parent, title, path: messages.append(("open", path)) or False
    journal.clear()
    result = report.export_pdf(state)
    check("export_pdf returns the path", result == target, result)
    check_pdf("export_pdf", target, 3)
    check("export_pdf: a journal line", any("PDF report written" in line
                                            for line in journal.lines()))
    check("export_pdf: offers to open the file", ("open", target) in messages, messages)

    # 7. Cancellation: None, nothing written, no message
    messages[:] = []
    report.ask_save_path = lambda *args: ""
    check("export_pdf cancelled: None", report.export_pdf(state) is None)
    check("export_pdf cancelled: silent", not messages, messages)

    # 8. Write failure: None, readable message, no exception
    bad = os.path.join(OUT, "missing_folder", "x.pdf")
    report.ask_save_path = lambda *args: bad
    messages[:] = []
    journal.clear()
    check("export_pdf failure: None", report.export_pdf(state) is None)
    check("export_pdf failure: message", any("PDF export failed" in m[0] for m in messages),
          messages)
    check("export_pdf failure: journal", any("Report" in line for line in journal.lines()))

    # 9. CSV end to end
    csv_path = os.path.join(OUT, "table_{}.csv".format(BINDING))
    report.ask_save_path = lambda *args: csv_path
    messages[:] = []
    check("export_csv returns the path", report.export_csv(state) == csv_path)
    with open(csv_path, "rb") as handle:
        raw = handle.read()
    check("export_csv: UTF-8 BOM", raw[:3] == b"\xef\xbb\xbf")
    lines = raw.decode("utf-8-sig").split("\r\n")
    check("export_csv: 30 rows + header", len([l for l in lines if l]) == N_MARKERS + 1,
          len(lines))
    check("export_csv: comma delimiter", lines[0].startswith("ID,Error X (m),"),
          lines[0])
    check("export_csv: decimal point", re.search(r"GCP07,0\.162,-0\.118,0\.205,", lines[7])
          is not None, lines[7])

    # 10. No marker: message, None
    FAKE.app.document.chunk = Chunk([], [])
    messages[:] = []
    report.ask_save_path = lambda *args: target
    check("export_pdf without markers: None", report.export_pdf(state) is None)
    check("export_pdf without markers: message", bool(messages))
    FAKE.app.document.chunk = chunk

    # 11. Report formats: texts, dates, numbers, CSV delimiter, proposed file names
    FAKE.app.document.chunk = chunk
    msapi.marker_errors = fake_errors_factory(markers)
    stamp = datetime.date.today().strftime("%Y-%m-%d")
    present = ["Page 1 of 3", "Total error", "Error X (m)", "Mean", "Median",
               "in meters", "current marker", "0.287", stamp]
    absent = ["0,287"]
    pdf_name = "{}_GCP_report_Chunk_1_-_Survey_Zurich.pdf".format(stamp)
    csv_name = "{}_GCP_table_Chunk_1_-_Survey_Zurich.csv".format(stamp)
    csv_head = ("ID,Error X (m),Error Y (m),Error Z (m),Total error (m),Projections,"
                "Reference X,Reference Y,Reference Z")

    # PDF built directly: content
    fmt_data = report.collect_data(chunk)
    fmt_images = report.collect_images(state, fmt_data)
    fmt_pdf = os.path.join(OUT, "report_formats_{}.pdf".format(BINDING))
    count = report.build_pdf(fmt_data, fmt_pdf, fmt_images)
    check("formats: build_pdf returns 3 pages", count == 3, count)
    raw = check_pdf("PDF formats", fmt_pdf, 3)
    title = pdf_title(raw) if raw else ""
    check("formats: document title contains 'Report'", "Report" in title, title)
    text = pdf_text(fmt_pdf)
    if text is None:
        print("SKIP formats: PDF text not checked (neither PyMuPDF nor pypdf)")
    else:
        flat = squash(text)
        for needle in present:
            check("formats: the PDF contains {!r}".format(needle), needle in flat)
        for needle in absent:
            check("formats: the PDF does not contain {!r}".format(needle), needle not in flat)

    # CSV through the real entry point
    seen = {}
    messages[:] = []
    csv_fmt = os.path.join(OUT, "table_formats_{}.csv".format(BINDING))

    def ask_csv(parent, caption, default, flt, _seen=seen, _path=csv_fmt):
        _seen.update(default=default, caption=caption, filter=flt)
        return _path
    report.ask_save_path = ask_csv
    check("formats: export_csv returns the path", report.export_csv(state) == csv_fmt)
    check("formats: proposed CSV name", os.path.basename(seen["default"]) == csv_name, seen["default"])
    with open(csv_fmt, "rb") as handle:
        raw_csv = handle.read()
    check("formats: CSV in UTF-8 with BOM", raw_csv[:3] == b"\xef\xbb\xbf")
    rows = raw_csv.decode("utf-8-sig").split("\r\n")
    check("formats: CSV header", rows[0] == csv_head, rows[0])
    check("formats: CSV row GCP07", re.search(r"^GCP07,0\.162,-0\.118,0\.205,", rows[7]) is not None, rows[7])
    body = "".join(rows[1:])
    check("formats: no semicolon or decimal comma in the CSV",
          ";" not in body and re.search(r"\d,\d{3}(,|$)", body) is None, rows[7])

    # PDF through the real entry point: proposed name, save dialog, journal
    pdf_target = os.path.join(OUT, "export_formats_{}.pdf".format(BINDING))

    def ask_pdf(parent, caption, default, flt, _seen=seen, _path=pdf_target):
        _seen.update(default=default, caption=caption, filter=flt)
        return _path
    report.ask_save_path = ask_pdf
    journal.clear()
    check("formats: export_pdf returns the path", report.export_pdf(state) == pdf_target)
    check("formats: proposed PDF name", os.path.basename(seen["default"]) == pdf_name, seen["default"])
    check("formats: save dialog texts",
          seen["caption"] == i18n.t("dlg.save_pdf") and seen["filter"].startswith("PDF files"),
          (seen["caption"], seen["filter"]))
    check("formats: offer to open the file", ("open", pdf_target) in messages, messages)
    check("formats: journal line", any("PDF report written" in line for line in journal.lines()))
    messages[:] = []
    report.ask_save_path = lambda *args: os.path.join(OUT, "missing_folder", "x.pdf")
    report.export_pdf(state)
    check("formats: failure message", any(m[0] == "PDF export failed" for m in messages), messages)

    print("-" * 60)
    print("Qt binding: {}; files in {}".format(BINDING, OUT))
    problem = GUARD.check()
    if problem:
        FAILURES.append("real settings: " + problem)
        print(problem)
    if FAILURES:
        print("{} failing cases".format(len(FAILURES)))
        return 1
    print("all cases pass")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception:
        traceback.print_exc()
        code = 1
    sys.exit(code)
