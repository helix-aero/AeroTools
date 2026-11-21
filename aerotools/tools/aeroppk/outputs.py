"""What Aero-PPK writes (step 3 asks for it before anything is computed, step 4 writes it). Pure logic, no Qt.

Files go to the `PPK` folder inside the `Outputs` folder next to the Metashape project, the folder the other Aero tools use
(`aerotools.outputdir`). A file that already exists is never overwritten: a new one gets `_v2`, `_v3`...
The photo positions are always in the frame of the base stations, heights are ellipsoidal (see docs/AEROPPK.md).
"""

import datetime
import os
from xml.sax.saxutils import escape

from ... import i18n, outputdir

RESULT_FOLDER = "PPK"
PHOTO_TABLE_LIMIT = 500                  # above this many photos the table of every photo is not in the report unless the user asks for it
FILE_KINDS = ("pdf", "csv", "kml", "txt")
EXTENSIONS = {"pdf": ".pdf", "csv": ".csv", "kml": ".kml", "txt": ".txt"}


class OutputOptions(object):
    """The choices of step 3. The EXIF rewrite is off unless asked for, and nothing here applies it: it is a separate, confirmed step."""

    def __init__(self, pdf=True, csv=True, kml=False, txt=False, exif=False):
        self.pdf, self.csv, self.kml, self.txt, self.exif = bool(pdf), bool(csv), bool(kml), bool(txt), bool(exif)

    def kinds(self):
        """The ticked file kinds, in a fixed order."""
        return [k for k in FILE_KINDS if getattr(self, k)]

    def to_dict(self):
        return {"pdf": self.pdf, "csv": self.csv, "kml": self.kml, "txt": self.txt, "exif": self.exif}

    @classmethod
    def from_dict(cls, data):
        data = data if isinstance(data, dict) else {}
        return cls(data.get("pdf", True), data.get("csv", True), data.get("kml", False), data.get("txt", False), False)


def default_include_table(photos):
    """The box "Include the table of all photos" of the export is ticked for a project of `PHOTO_TABLE_LIMIT` photos or fewer."""
    return int(photos) <= PHOTO_TABLE_LIMIT


def table_warning(photos):
    """True when the report with the table would be long enough to deserve the grey line about its pages."""
    return int(photos) > PHOTO_TABLE_LIMIT


def output_folder(project_path="", flight_folder=""):
    """`<Outputs>/PPK`. `Outputs` is the folder of the suite next to the saved project; without a project it is next to the flight folder."""
    if project_path:
        base = outputdir.outputs_folder(project_path)
    elif flight_folder:
        base = os.path.join(os.path.dirname(os.path.abspath(flight_folder.rstrip("/\\"))), outputdir.OUTPUTS)
    else:
        base = os.path.join(os.path.expanduser("~"), outputdir.OUTPUTS)
    return os.path.join(base, RESULT_FOLDER)


def unique_path(folder, name):
    """`folder/name`, or `name_v2`, `name_v3`... when it exists."""
    stem, ext = os.path.splitext(name)
    path = os.path.join(folder, name)
    version = 2
    while os.path.exists(path):
        path = os.path.join(folder, "%s_v%d%s" % (stem, version, ext))
        version += 1
    return path


def base_file_name(data, kind, today=None):
    """`YYYY-MM-DD_PPK_<kind>_<project>.<ext>`: the date of the calculation, the name of the project."""
    generated = data.get("generated")
    day = today or (generated.date() if isinstance(generated, datetime.datetime) else generated) or datetime.date.today()
    title = "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(data.get("title") or "Project")).strip("_") or "Project"
    labels = {"pdf": "report", "csv": "positions", "kml": "positions", "txt": "positions"}
    return "%s_PPK_%s_%s%s" % (day.strftime("%Y-%m-%d"), labels[kind], title, EXTENSIONS[kind])


# ---------------------------------------------------------------------------------------------------------------------
# KML and text
# ---------------------------------------------------------------------------------------------------------------------

LEVEL_STYLES = (("ok", "ff59ca1e"), ("warn", "ff2a9bff"), ("bad", "ff1052ff"))       # KML colours are aabbggrr


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def photo_rows(data):
    """(flight name, row) of every photo that has a position."""
    out = []
    for flight in data.get("flights") or []:
        for row in flight.get("photos_rows") or []:
            if _finite(row.get("lat")) is not None and _finite(row.get("lon")) is not None:
                out.append((flight.get("name") or "", row))
    return out


def _quality(q):
    return {1: "fixed", 2: "float"}.get(int(q or 0), "none")


def kml_text(data):
    """A KML document with one point per photo, coloured by its level. Heights are ellipsoidal (said in the description)."""
    frame = (data.get("frame") or {}).get("label") or ""
    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<kml xmlns="http://www.opengis.net/kml/2.2">', "<Document>",
           "<name>%s</name>" % escape(str(data.get("title") or "PPK")),
           "<description>%s</description>" % escape(i18n.t("aeroppk.out.kml_note", frame=frame))]
    for level, color in LEVEL_STYLES:
        out.append('<Style id="%s"><IconStyle><color>%s</color><scale>0.7</scale><Icon><href>http://maps.google.com/mapfiles/kml/shapes/'
                   'placemark_circle.png</href></Icon></IconStyle><LabelStyle><scale>0</scale></LabelStyle></Style>' % (level, color))
    for flight, row in photo_rows(data):
        level = ("ok", "warn", "bad")[max(0, min(2, int(row.get("level") or 0)))]
        sd_h = (_finite(row.get("sdn")) or 0.0, _finite(row.get("sde")) or 0.0)
        desc = "%s | %s | %.1f cm" % (flight, _quality(row.get("q")), 100.0 * (sd_h[0] ** 2 + sd_h[1] ** 2) ** 0.5)
        h = _finite(row.get("h")) or 0.0
        out.append("<Placemark><name>%s</name><description>%s</description><styleUrl>#%s</styleUrl><Point><altitudeMode>absolute</altitudeMode>"
                   "<coordinates>%.9f,%.9f,%.4f</coordinates></Point></Placemark>" % (
                       escape(str(row.get("name") or row.get("index"))), escape(desc), level, row["lon"], row["lat"], h))
    out += ["</Document>", "</kml>", ""]
    return "\n".join(out)


TXT_COLUMNS = ("name", "latitude", "longitude", "height", "sd_h", "sd_v", "solution", "level")


def txt_text(data):
    """A plain text table, tab separated, with a few comment lines first (`#`): frame, bases, strategy."""
    frame = (data.get("frame") or {}).get("label") or ""
    lines = ["# " + i18n.t("aeroppk.out.txt_head", frame=frame), "# " + "\t".join(TXT_COLUMNS)]
    for flight, row in photo_rows(data):
        sd_h = ((_finite(row.get("sdn")) or 0.0) ** 2 + (_finite(row.get("sde")) or 0.0) ** 2) ** 0.5
        lines.append("\t".join([str(row.get("name") or row.get("index")), "%.9f" % row["lat"], "%.9f" % row["lon"],
                                "%.4f" % (_finite(row.get("h")) or 0.0), "%.4f" % sd_h, "%.4f" % (_finite(row.get("sdu")) or 0.0),
                                _quality(row.get("q")), ("ok", "check", "bad")[max(0, min(2, int(row.get("level") or 0)))]]))
    lines.append("")
    return "\n".join(lines)


def export(data, options, folder, build_pdf=None, write_csv=None, today=None):
    """Write the ticked files into `folder` (created). Returns the list of paths written.

    `build_pdf(data, path)` and `write_csv(data, path)` are the writers of report.py (they need Qt, so they are passed in)."""
    os.makedirs(folder, exist_ok=True)
    paths = [(kind, unique_path(folder, base_file_name(data, kind, today))) for kind in options.kinds()]
    data = dict(data, exports=[(kind, os.path.basename(path)) for kind, path in paths])          # the report lists the files written with it
    written = []
    for kind, path in paths:
        if kind == "pdf":
            if build_pdf is None:
                continue
            build_pdf(data, path)
        elif kind == "csv":
            if write_csv is None:
                continue
            write_csv(data, path)
        elif kind == "kml":
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(kml_text(data))
        else:
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(txt_text(data))
        written.append(path)
    return written
