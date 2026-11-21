"""Qt self test of the Aero-PPK report (PDF and CSV): builds real files, outside Metashape.

Run it with Metashape's own Python, without starting Metashape (no pytest needed):

    "C:\\Program Files\\Agisoft\\Metashape_v22\\python\\python.exe" -B tests_qt\\run_qt_aeroppk_report.py

It builds the PDF from `reportdata.sample()` and from a bigger generated dictionary (3 flights, 320 photos, 3 bases
with one suspect, float and unsolved photos) in English and French, checks the page count, the file header, the text
of the PDF when a text extractor is available, the CSV, and that no content overflows a page. It also paints every
page on a PNG image (same drawing code), so the layout can be looked at.

Environment variables:
  AEROPPK_REPORT_OUT   output folder (default: a temporary folder, printed at the start)
  AEROPPK_PNG_SCALE    pixels per point of the PNG pages (default 1.6)

Output: one PASS / FAIL line per check, a non-zero exit code on the first failure.
"""

import datetime
import math
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
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="aerotools_qt_aeroppk_")
OUT = os.environ.get("AEROPPK_REPORT_OUT") or tempfile.mkdtemp(prefix="aeroppk_report_")
os.makedirs(OUT, exist_ok=True)
PNG_SCALE = float(os.environ.get("AEROPPK_PNG_SCALE", "1.6"))


def _prepare_qt_environment():
    """Metashape DLLs when running in its Python, off screen platform when available."""
    metashape_dir = os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))
    has_qt = os.path.exists(os.path.join(metashape_dir, "Qt5Core.dll"))
    offscreen = True
    if has_qt:
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(metashape_dir)
        os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(metashape_dir, "plugins"))
        offscreen = os.path.exists(os.path.join(metashape_dir, "plugins", "platforms", "qoffscreen.dll"))
    if offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    return "offscreen" if offscreen else "native"


PLATFORM = _prepare_qt_environment()

# A minimal Metashape stand-in: the report only needs the package to import.
FAKE = types.ModuleType("Metashape")
FAKE.app = types.SimpleNamespace(document=types.SimpleNamespace(chunk=None, path=""), version="2.2.1-test",
                                 photo_view=None, update=lambda: None, messageBox=print)
for _name in ("Vector", "Camera", "Marker", "Chunk"):
    setattr(FAKE, _name, type(_name, (object,), {}))
sys.modules["Metashape"] = FAKE

from aerotools import i18n  # noqa: E402
from aerotools.qt import BINDING, QtGui, QtWidgets  # noqa: E402
from aerotools.tools.aeroppk import report, reportdata  # noqa: E402

FAILURES = []


def check(name, condition, detail=""):
    print("{} {}{}".format("PASS" if condition else "FAIL", name, "" if condition else " : " + str(detail)))
    if not condition:
        FAILURES.append(name)


# --------------------------------------------------------------------------
# A bigger dictionary
# --------------------------------------------------------------------------

def _base(code, name, distance_km, interval, systems, fix_pct, suspect, bearing, lat0, lon0, extra=None):
    d = distance_km / 111.0
    lat = lat0 + d * math.cos(math.radians(bearing))
    lon = lon0 + d * math.sin(math.radians(bearing)) / math.cos(math.radians(lat0))
    base = {"code": code, "name": name, "distance_km": distance_km, "baseline_km": distance_km * 1.01, "interval_s": interval,
            "systems": systems, "systems_used": systems[:3], "product": "{} s, RINEX 3, hourly files".format(int(interval)),
            "files": ["{}{:03d}{}.24o.gz".format(code.lower(), 311, chr(97 + h)) for h in range(2 + int(distance_km) % 4)],
            "fix_pct": fix_pct, "float": 3, "used": 100, "dropped": 30 if suspect else 2,
            "bias_n": -0.026 if suspect else 0.002, "bias_e": 0.004 if suspect else -0.001, "bias_u": 0.048 if suspect else 0.006,
            "rms_h": 0.027 if suspect else 0.004, "suspect": suspect, "lat": lat, "lon": lon, "h": 450.0,
            "receiver": "LEICA GR50", "antenna": "LEIAR25.R4 NONE", "profile": "all", "alt_profile": "gps_glo"}
    base.update(extra or {})
    return base


def make_flight(rng, index, n, origin, with_onboard=True, bad_every=17):
    lat0, lon0 = origin
    start = datetime.datetime(2026, 11, 3 + index, 9, 14, 0)
    rows, track = [], []
    side = int(math.sqrt(n)) + 1
    for i in range(n):
        line, col = divmod(i, side)
        col = col if line % 2 == 0 else side - 1 - col
        east, north = col * 12.0, line * 25.0
        lat = lat0 + (north - 150.0) / 111000.0
        lon = lon0 + (east - 140.0) / (111000.0 * math.cos(math.radians(lat0)))
        float_photo = (i % bad_every == 5)
        none_photo = (i % 53 == 9)
        single = (i % 29 == 3)
        q = 0 if none_photo else (2 if float_photo else 1)
        sd = 0.011 + rng.random() * 0.008
        sdu = 0.021 + rng.random() * 0.012
        flags, level = [], 0
        if none_photo:
            flags, level = ["no_solution"], 2
            lat = lon = float("nan")
        elif float_photo:
            flags, level = ["float"] + (["spread"] if i % 2 else []), 1
            sd, sdu = 0.14 + rng.random() * 0.1, 0.3 + rng.random() * 0.2
        elif single:
            flags = ["single_base"]
        used = [] if none_photo else (["STNA"] if single else ["STNA", "STNB", "STNC"][: 2 + (i % 2)])
        spread = None if none_photo else (0.0 if len(used) < 2 else abs(rng.gauss(0.012, 0.008)) + (0.06 if float_photo else 0.0))
        has_delta = with_onboard and not none_photo
        rows.append({
            "index": i + 1, "name": "IMG_{:04d}.JPG".format(i + 1),
            "time_utc": start + datetime.timedelta(seconds=2 * i, microseconds=300000), "lat": lat, "lon": lon,
            "h": 612.0 + 0.04 * i + rng.gauss(0, 0.01), "sdn": sd * 0.9, "sde": sd * 0.8, "sdu": sdu, "q": q, "level": level,
            "flags": flags, "used": used, "spread": spread,
            "dn": rng.gauss(0.012, 0.008) if has_delta else None, "de": rng.gauss(-0.008, 0.006) if has_delta else None,
            "du": rng.gauss(-0.05, 0.015) if has_delta else None, "onboard_q": 50 if has_delta else None})
        if not none_photo:
            track.append((lat, lon, q))
    counts = {"photos": n, "fix": sum(1 for r in rows if r["q"] == 1), "float": sum(1 for r in rows if r["q"] == 2),
              "none": sum(1 for r in rows if r["q"] == 0), "ok": sum(1 for r in rows if r["level"] == 0),
              "warn": sum(1 for r in rows if r["level"] == 1), "bad": sum(1 for r in rows if r["level"] == 2)}
    counts["solved"] = counts["photos"] - counts["none"]
    counts["fix_pct"] = 100.0 * counts["fix"] / max(n, 1)
    counts.update({"suspect_bases": ["STNB"], "sd_h_median": 0.015, "sd_h_max": 0.2, "sd_u_median": 0.026, "sd_u_max": 0.46,
                   "multi_pct": 90.0, "spread_median": 0.012, "spread_max": 0.09})
    onboard = ({"rtk_photos": n - counts["none"], "gross_median_m": 0.06, "gross_error": False, "bias_n": 0.012, "bias_e": -0.008,
                "bias_u": -0.05, "p95_n": 0.016, "p95_e": 0.012, "p95_u": 0.03, "rms_h": 0.01}
               if with_onboard else {"rtk_photos": 0, "gross_median_m": 0.4, "gross_error": False})
    bases = [_base("STNA", "Station A, IGN", 0.5, 1.0, "GRES", 99.0, False, 200, lat0, lon0),
             _base("STNB", "Station B, IGN", 12.4, 30.0, "GR", 18.0, True, 70, lat0, lon0),
             _base("STNC", "Station C, IGN", 28.7, 30.0, "GREC", 88.0, False, 340, lat0, lon0)]
    return {"name": "FLIGHT_{:03d}_zone_{}".format(index + 1, index + 1),
            "drone": "DJI Mavic 3 Enterprise", "folder": "flight{}".format(index), "photos": n, "events": n,
            "start_utc": start, "end_utc": start + datetime.timedelta(seconds=2 * n), "duration_s": 2.0 * n,
            "files": [{"key": "photos", "present": True, "level": "blocking", "detail": str(n)},
                      {"key": "events", "present": True, "level": "blocking", "detail": "x_Timestamp.MRK"},
                      {"key": "rover_obs", "present": True, "level": "blocking", "detail": "x_PPKOBS.obs"},
                      {"key": "rover_nav", "present": index != 1, "level": "advised", "detail": ""},
                      {"key": "raw", "present": index == 0, "level": "optional", "detail": "x_PPKRAW.bin" if index == 0 else ""}],
            "bases": bases, "quality": counts, "onboard": onboard, "photos_rows": rows, "track": track[:: max(1, n // 120)],
            "center": (lat0, lon0), "warnings": [("warning", "aeroppk.warn.suspect_base", {"code": "STNB"})] if index == 0 else [],
            "profile_conflicts": 0, "step": 1}


def big_data(flights=3, photos=(140, 120, 60)):
    rng = random.Random(7)
    items = [make_flight(rng, i, photos[i % len(photos)], (45.30 + 0.01 * i, 5.15 + 0.005 * i), with_onboard=(i != 1)) for i in range(flights)]
    tot = {key: sum(f["quality"][key] for f in items) for key in ("photos", "fix", "float", "none", "warn", "bad")}
    tot["fix_pct"] = 100.0 * tot["fix"] / max(tot["photos"], 1)
    return {"title": "Wind farm - West site n°2", "generated": datetime.datetime(2026, 11, 6, 10, 0, 0),
            "tool": {"name": "Aero-PPK", "version": "0.2.0"},
            "engine": {"name": "RTKLIB-EX (demo5)", "version": "RTKLIB ver.EX 2.5.1", "licence": "BSD 2-clause",
                       "options": [("frequency", "l1+l2"), ("systems", "GREC"), ("elevation_mask", "10 deg"), ("ambiguity", "fix and hold"),
                                   ("ratio", "3"), ("dynamics", "off"), ("solution_type", "combined"), ("iono", "estimate"),
                                   ("tropo", "estimate")]},
            "frame": {"label": "RGF93 v2b (ETRS89)"},
            "source": {"name": "RGP (IGN)", "licence": "Licence Ouverte Etalab 2.0", "url": "https://rgpdata.ign.fr"},
            "flights": items, "totals": tot,
            "warnings": [("warning", "aeroppk.warn.suspect_base", {"code": "STNB"}), ("info", "aeroppk.warn.unknown_demo_key", {})]}


def edge_data():
    """Awkward but possible input: nothing solved, missing files, no bases, no photos, unknown flags and keys, many warnings."""
    rng = random.Random(3)
    bad = make_flight(rng, 0, 30, (45.0, 3.0), with_onboard=False)
    bad["bases"] = []
    bad["files"][1]["present"] = False
    bad["files"].append({"key": "strange_file", "present": False, "level": "advised", "detail": ""})
    bad["onboard"] = {"rtk_photos": 0, "gross_median_m": 55.0, "gross_error": True}
    for r in bad["photos_rows"][:8]:
        r.update(q=0, level=2, flags=["no_solution", "never_seen_flag"], used=[], lat=float("nan"), lon=float("nan"))
    bad["track"] = []
    empty = make_flight(rng, 1, 0, (45.0, 3.0))
    empty["photos_rows"], empty["track"], empty["center"], empty["bases"], empty["files"], empty["quality"] = [], [], None, [], [], {}
    lonely = make_flight(rng, 2, 12, (45.0, 3.0))
    lonely["bases"] = lonely["bases"][:1]
    lonely["track"] = []
    data = big_data(3, (30, 0, 12))
    data["flights"] = [bad, empty, lonely]
    data["totals"] = {}
    data["warnings"] = [("error", "aeroppk.warn.error_%d" % i, {}) for i in range(14)] + [("warning", "aeroppk.warn.suspect_base", {"code": "STNB"})]
    data["source"] = {"name": "Some network", "licence": "Open licence", "url": "https://example.org/data"}
    data["title"] = "A very long project name " * 4
    return data


def good_data():
    data = big_data(1, (40,))
    f = data["flights"][0]
    for r in f["photos_rows"]:
        r.update(q=1, level=0, flags=[], used=["STNA", "STNC"], spread=0.01, sdn=0.01, sde=0.01, sdu=0.02)
    f["bases"] = [b for b in f["bases"] if not b["suspect"]]
    f["warnings"] = []
    f["quality"].update(photos=40, fix=40, float=0, none=0, ok=40, warn=0, bad=0, fix_pct=100.0)
    data["totals"] = {"photos": 40, "fix": 40, "float": 0, "none": 0, "warn": 0, "bad": 0, "fix_pct": 100.0}
    data["warnings"] = []
    return data


# --------------------------------------------------------------------------
# PDF checks
# --------------------------------------------------------------------------

def pdf_pages(raw):
    return len(re.findall(rb"/Type\s*/Page(?![s\w])", raw))


_EXTRACT_SCRIPT = (
    "import sys\n"
    "try:\n"
    "    import fitz\n"
    "    text = '\\n'.join(p.get_text() for p in fitz.open(sys.argv[1]))\n"
    "except ImportError:\n"
    "    import pypdf\n"
    "    text = '\\n'.join(p.extract_text() for p in pypdf.PdfReader(sys.argv[1]).pages)\n"
    "sys.stdout.buffer.write(text.encode('utf-8'))\n"
)


def pdf_text(path):
    """Text of the PDF pages, or None when no extractor exists (PyMuPDF or pypdf, here or in the `python` of the PATH)."""
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
    candidates = [os.environ.get("AEROPPK_PDF_PYTHON"), "python", "py"]
    for exe in candidates:
        if not exe:
            continue
        try:
            done = subprocess.run([exe, "-c", _EXTRACT_SCRIPT, path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        except Exception:
            continue
        if done.returncode == 0 and done.stdout:
            return done.stdout.decode("utf-8", "replace")
    return None


def squash(text):
    return re.sub(r"\s+", " ", text or "")


def ensure_fonts():
    """Load Segoe UI from the Windows folder when the off screen platform has no font at all."""
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


def build_case(label, data, code, expected_pages=None, texts=()):
    tag = "{} {}".format(label, code.upper())
    path = os.path.join(OUT, "{}_{}.pdf".format(label, code))
    count = report.build_pdf(data, path)
    check("{} : build_pdf returns page_count".format(tag), count == report.page_count(data), (count, report.page_count(data)))
    if expected_pages is not None:
        check("{} : {} pages".format(tag, expected_pages), count == expected_pages, count)
    ok = os.path.isfile(path) and os.path.getsize(path) > 2000
    check("{} : file written and not empty".format(tag), ok, path)
    raw = open(path, "rb").read() if ok else b""
    check("{} : %PDF header".format(tag), raw[:5] == b"%PDF-", raw[:8])
    check("{} : page objects match".format(tag), pdf_pages(raw) == count, pdf_pages(raw))
    check("{} : no .part left".format(tag), not os.path.exists(path + ".part"))
    check("{} : nothing overflows a page".format(tag), not report.LAST_OVERFLOWS, report.LAST_OVERFLOWS)
    text = pdf_text(path)
    if text is None:
        print("SKIP {} : PDF text not checked (no PyMuPDF or pypdf found)".format(tag))
    else:
        flat = squash(text)
        for needle in texts:
            check("{} : the PDF contains {!r}".format(tag, needle), needle in flat)
    # the same pages as images
    images = report.render_pages(data, PNG_SCALE)
    check("{} : one PNG per page".format(tag), len(images) == count, len(images))
    for number, image in images:
        target = os.path.join(OUT, "{}_{}_p{:02d}.png".format(label, code, number))
        image.save(target, "PNG")
    check("{} : rendering overflows nothing".format(tag), not report.LAST_OVERFLOWS, report.LAST_OVERFLOWS)
    return path


def csv_checks(label, data, code):
    tag = "{} {}".format(label, code.upper())
    path = os.path.join(OUT, "{}_{}.csv".format(label, code))
    report.write_csv(data, path)
    raw = open(path, "rb").read()
    check("{} CSV : UTF-8 with BOM".format(tag), raw[:3] == b"\xef\xbb\xbf")
    rows = raw.decode("utf-8-sig").split("\r\n")
    rows = [r for r in rows if r]
    n_photos = sum(len(f["photos_rows"]) for f in data["flights"])
    check("{} CSV : one row per photo plus the header".format(tag), len(rows) == n_photos + 1, (len(rows), n_photos))
    delim = i18n.csv_delimiter()
    expected_head = i18n.t("aeroppk.report.csv_flight")
    check("{} CSV : header in the language".format(tag), rows[0].startswith(expected_head + delim), rows[0])
    import csv as csv_module
    parsed = list(csv_module.reader(rows, delimiter=delim))
    check("{} CSV : every row has {} columns".format(tag, len(parsed[0])), all(len(r) == len(parsed[0]) for r in parsed), "")
    number = re.compile(r"^-?\d+{}\d+$".format(re.escape(i18n.decimal_separator())))
    good = [r for r in parsed[1:] if r[3]]
    check("{} CSV : lat, lon, h and sigmas use the {} decimal separator".format(tag, "point"),
          all(number.match(r[i]) for r in good for i in (3, 4, 5, 6, 7, 8)), good[0][3:9])
    check("{} CSV : time carries the same decimal separator".format(tag),
          all(re.match(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d" + re.escape(i18n.decimal_separator()) + r"\d{3}$", r[2]) for r in parsed[1:]), parsed[1][2])
    check("{} CSV : quality and level are plain codes".format(tag),
          all(r[9] in ("fix", "float", "none") and r[10] in ("ok", "warn", "bad") for r in parsed[1:]), "")


def main():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    print("Qt binding: {} ; platform: {} ; Python {}".format(BINDING, PLATFORM, sys.version.split()[0]))
    print("output: {}".format(OUT))
    ensure_fonts()

    small = reportdata.sample()
    big = big_data()
    n_big = sum(len(f["photos_rows"]) for f in big["flights"])
    check("generated data has 300+ photos", n_big >= 300, n_big)
    plan = report.page_plan(big)
    check("page plan of the big report has the expected shape", [p[0] for p in plan].count("imported") == 3
          and [p[0] for p in plan].count("quality") == 3 and plan[0] == ("cover",) and plan[-1] == ("method", 3), plan[:3])

    for code in ("en",):
        t = {"aeroppk.report.footer_page": i18n.t("aeroppk.report.footer_page")}
        # titles are painted as outlines: only body text is searched
        present_small = ["RGP (IGN)", "https://rgpdata.ign.fr", "Station A, IGN", "FLIGHT_DEMO_001", "RGF93 v2b (ETRS89)",
                         t["aeroppk.report.footer_page"].format(page=1, total=report.page_count(small))]
        build_case("sample", small, code, expected_pages=1 + 5 * 1 + 1 + 3, texts=present_small + ["PPK (post-processed kinematic)", "processing aid", "Present"])
        present_big = ["RGP (IGN)", "Licence Ouverte Etalab 2.0", "Station B, IGN",
                       t["aeroppk.report.footer_page"].format(page=3, total=report.page_count(big))]
        build_case("big", big, code, texts=present_big)
        csv_checks("sample", small, code)
        csv_checks("big", big, code)
        build_case("edge", edge_data(), code)
        build_case("good", good_data(), code, expected_pages=1 + 5 + 2 + 3, texts=["Status: good", "processing aid", "Valid"])
    # the report without the table of every photo: fewer pages, the note about the CSV, still no overflow
    good = good_data()
    good["table"] = False
    good["exports"] = [("pdf", "r.pdf"), ("csv", "r.csv")]
    with_table = report.page_count(good_data())
    build_case("notable", good, "en", expected_pages=with_table - len(report.table_plan([40])),
               texts=["The table of all photos is not in this report.", "The CSV of positions has every photo.", "r.csv"])
    print("PNG pages are in {}".format(OUT))
    if FAILURES:
        print("{} FAILED: {}".format(len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception:
        traceback.print_exc()
        code = 1
    sys.exit(code)
