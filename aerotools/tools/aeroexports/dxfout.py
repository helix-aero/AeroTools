"""Aero-Exports: DXF that references the orthomosaics and DEMs through georeferenced IMAGE entities (no dependency).

Why a DXF and not a GeoTIFF: plain AutoCAD does not always read the georeferencing of a GeoTIFF or an ECW. An IMAGE
entity carries its world coordinates in the drawing itself (insertion point + size of one pixel), so the image
lands at the right place and scale whatever the Autodesk product.

The image is not embedded: the DXF points to the file with a RELATIVE path (the folder can be moved as a whole):

    TOPO.dxf
    00_Assets\\ortho\\SITE_260704_3cm_ortho.jp2
    00_Assets\\DTM\\SITE_260704_20cm_dtm.jp2

The DXF is written by hand (R2000, structure taken from ezdxf's output and then checked in AutoCAD 2023 with
accoreconsole): neither ezdxf nor AutoCAD is needed to write it. Python 3.9, no dependency.
"""

import os

ASSETS = "00_Assets"
ORTHO_DIR = "ortho"
DEM_DIR = "DTM"
CONTINUOUS = "Continuous"

# 0x10.. : handles of the drawing objects; the images take others above them.
H_ROOT_DICT, H_IMAGE_DICT = "A", "B"
H_PLOT_DICT, H_PLOT_NORMAL = "12", "13"      # "Normal" plot style: AutoCAD requires it on every layer
H_MODEL_RECORD, H_PAPER_RECORD = "1F", "1B"
H_LTYPE, H_STYLE, H_APPID, H_LAYER0 = "14", "15", "16", "17"
FIRST_FREE = 0x100


def relative_path(asset_path, dxf_path):
    """Path of the image seen from the DXF's folder, with backslashes (Windows / AutoCAD)."""
    base = os.path.dirname(os.path.abspath(dxf_path))
    return os.path.relpath(os.path.abspath(asset_path), base).replace("/", "\\")


def asset_folder(dxf_path, kind):
    """Image folder of a DXF: `00_Assets\\ortho` or `00_Assets\\DTM`."""
    return os.path.join(os.path.dirname(os.path.abspath(dxf_path)), ASSETS, ORTHO_DIR if kind == "ortho" else DEM_DIR)


def layer_name(date_text, pixel_text, kind):
    """Layer of an image: "260704_0.1_ortho" or "260704_0.2_DTM" (date, pixel in metres, kind)."""
    tail = "ortho" if kind == "ortho" else "DTM"
    return "_".join(part for part in (date_text, pixel_text, tail) if part)


def _clean_layer(name):
    """A DXF layer name cannot contain < > / \\ \" : ; ? * | = `."""
    bad = '<>/\\":;?*|=`'
    cleaned = "".join("_" if char in bad else char for char in name).strip()
    return cleaned[:255] or "0"


class _Writer(object):
    def __init__(self):
        self.lines = []

    def add(self, code, value):
        self.lines.append(str(code))
        self.lines.append(str(value))

    def text(self):
        return "\r\n".join(self.lines) + "\r\n"


def _number(value):
    return repr(float(value))


def closed_polygon(points):
    """The clip polygon with its first vertex repeated at the end (or None). AutoCAD 2023 refuses the whole DXF ("Xdata wasn't
    read", drawing discarded) when the outline of an IMAGE clip is not closed: measured with accoreconsole."""
    if not points or len(points) < 3:
        return None
    points = [tuple(point) for point in points]
    return points if points[0] == points[-1] else points + [points[0]]


def text_value(value):
    """A TEXT string as the ASCII file holds it: a letter outside ASCII becomes the DXF escape "U+00C7" form (nothing is lost, no "?")."""
    out = "".join(char if ord(char) < 128 else "\\U+%04X" % ord(char) for char in str(value))
    return out.replace("\r", " ").replace("\n", " ")


TEXT_ALIGN = {"left": 0, "center": 1, "right": 2}


def _layer_style(spec):
    """(AutoCAD colour index, lineweight in hundredths of a millimetre) of a layer given as an int or a (colour, lineweight) pair."""
    if isinstance(spec, (tuple, list)):
        return int(spec[0]), int(spec[1])
    return int(spec), -3


def _write_entity(w, handle, item):
    """One LINE, LWPOLYLINE or TEXT of the model space (R2000 subclass markers, as AutoCAD writes them)."""
    kind = item["type"]
    w.add(0, kind); w.add(5, handle); w.add(330, H_MODEL_RECORD); w.add(100, "AcDbEntity"); w.add(8, _clean_layer(item["layer"]))
    if kind == "LINE":
        (x1, y1), (x2, y2) = item["p1"], item["p2"]
        w.add(100, "AcDbLine")
        w.add(10, _number(x1)); w.add(20, _number(y1)); w.add(30, 0.0)
        w.add(11, _number(x2)); w.add(21, _number(y2)); w.add(31, 0.0)
    elif kind == "LWPOLYLINE":
        points = list(item["points"])
        w.add(100, "AcDbPolyline"); w.add(90, len(points)); w.add(70, 1 if item.get("closed") else 0)
        for x, y in points:
            w.add(10, _number(x)); w.add(20, _number(y))
    elif kind == "TEXT":
        x, y = item["pos"]
        align = TEXT_ALIGN.get(item.get("align", "left"), 0)
        w.add(100, "AcDbText")
        w.add(10, _number(x)); w.add(20, _number(y)); w.add(30, 0.0)
        w.add(40, _number(item["height"])); w.add(1, text_value(item["text"]))
        if align:
            w.add(72, align)
            w.add(11, _number(x)); w.add(21, _number(y)); w.add(31, 0.0)
        w.add(100, "AcDbText"); w.add(73, 0)
    else:
        raise ValueError("unknown DXF entity: %r" % (kind,))


def build_dxf(images, units_m=True, entities=None, layers=None, extents=None, font="txt"):
    """Text of an R2000 DXF.

    `images`: list of dicts {"layer", "file" (path as written in the DXF), "size_px": (width, height),
    "insert": (x, y) bottom-left corner in drawing units, "pixel": (width of a pixel, height of a pixel),
    "clip" (optional): closed polygon [(x, y), ...] in image pixels, from its TOP-left corner, y pointing down (see footprint.py)}.

    Optional, for drawings that are more than images (Aero-Facade's sheet), all written AFTER the images (so on top of them):
    `entities`: list of dicts {"type": "LINE", "layer", "p1": (x, y), "p2": (x, y)} | {"type": "LWPOLYLINE", "layer", "points": [(x, y)...],
    "closed": bool} | {"type": "TEXT", "layer", "pos": (x, y), "height", "text", "align": "left" | "center" | "right"} (the text sits on its
    baseline, `pos` is the anchor of the chosen alignment). `layers`: {name: colour index, or (colour index, lineweight)}, the colour of a
    layer (7 when not given). `extents`: (xmin, ymin, xmax, ymax), written in the header ($EXTMIN / $EXTMAX). `font`: the SHX font of the
    "Standard" text style ("txt" squares the capital O: Aero-Facade asks for "simplex").
    """
    w = _Writer()
    entities = list(entities or [])
    styles = {_clean_layer(name): _layer_style(spec) for name, spec in (layers or {}).items()}
    layers = []
    for item in list(images) + entities:
        name = _clean_layer(item["layer"])
        if name not in layers and name != "0":
            layers.append(name)
    for name in styles:
        if name not in layers and name != "0":
            layers.append(name)
    handle = [FIRST_FREE]

    def new_handle():
        handle[0] += 1
        return "%X" % handle[0]

    layer_handles = {name: new_handle() for name in layers}
    defs = [(new_handle(), new_handle(), new_handle(), item) for item in images]   # (entity, definition, reactor)
    entity_handles = [new_handle() for _item in entities]

    w.add(0, "SECTION"); w.add(2, "HEADER")
    w.add(9, "$ACADVER"); w.add(1, "AC1015")
    w.add(9, "$HANDSEED"); w.add(5, "%X" % (handle[0] + 1))
    if extents:
        w.add(9, "$EXTMIN"); w.add(10, _number(extents[0])); w.add(20, _number(extents[1])); w.add(30, 0.0)
        w.add(9, "$EXTMAX"); w.add(10, _number(extents[2])); w.add(20, _number(extents[3])); w.add(30, 0.0)
    w.add(9, "$INSUNITS"); w.add(70, 6 if units_m else 0)
    w.add(9, "$MEASUREMENT"); w.add(70, 1)
    w.add(0, "ENDSEC")

    w.add(0, "SECTION"); w.add(2, "CLASSES")
    for dxf_name, cpp, vendor, flags, entity in (
            ("IMAGE", "AcDbRasterImage", "ISM", 127, 1), ("IMAGEDEF", "AcDbRasterImageDef", "ISM", 0, 0),
            ("IMAGEDEF_REACTOR", "AcDbRasterImageDefReactor", "ISM", 1, 0),
            ("ACDBDICTIONARYWDFLT", "AcDbDictionaryWithDefault", "ObjectDBX Classes", 0, 0),
            ("ACDBPLACEHOLDER", "AcDbPlaceHolder", "ObjectDBX Classes", 0, 0)):
        w.add(0, "CLASS"); w.add(1, dxf_name); w.add(2, cpp); w.add(3, vendor); w.add(90, flags)
        w.add(280, 0); w.add(281, entity)
    w.add(0, "ENDSEC")

    w.add(0, "SECTION"); w.add(2, "TABLES")
    for name, h in (("VPORT", "8"), ):
        w.add(0, "TABLE"); w.add(2, name); w.add(5, h); w.add(330, 0); w.add(100, "AcDbSymbolTable"); w.add(70, 0)
        w.add(0, "ENDTAB")
    w.add(0, "TABLE"); w.add(2, "LTYPE"); w.add(5, "5"); w.add(330, 0); w.add(100, "AcDbSymbolTable"); w.add(70, 3)
    for name, h, desc in (("ByBlock", "10", ""), ("ByLayer", "11", ""), (CONTINUOUS, H_LTYPE, "Solid line")):
        w.add(0, "LTYPE"); w.add(5, h); w.add(330, "5"); w.add(100, "AcDbSymbolTableRecord")
        w.add(100, "AcDbLinetypeTableRecord"); w.add(2, name); w.add(70, 0); w.add(3, desc)
        w.add(72, 65); w.add(73, 0); w.add(40, 0.0)
    w.add(0, "ENDTAB")
    w.add(0, "TABLE"); w.add(2, "LAYER"); w.add(5, "2"); w.add(330, 0); w.add(100, "AcDbSymbolTable")
    w.add(70, 1 + len(layers))
    for name, h in [("0", H_LAYER0)] + [(name, layer_handles[name]) for name in layers]:
        w.add(0, "LAYER"); w.add(5, h); w.add(330, "2"); w.add(100, "AcDbSymbolTableRecord")
        color, weight = styles.get(name, (7, -3))
        w.add(100, "AcDbLayerTableRecord"); w.add(2, name); w.add(70, 0); w.add(62, color); w.add(6, CONTINUOUS)
        w.add(370, weight); w.add(390, H_PLOT_NORMAL)
    w.add(0, "ENDTAB")
    w.add(0, "TABLE"); w.add(2, "STYLE"); w.add(5, "3"); w.add(330, 0); w.add(100, "AcDbSymbolTable"); w.add(70, 1)
    w.add(0, "STYLE"); w.add(5, H_STYLE); w.add(330, "3"); w.add(100, "AcDbSymbolTableRecord")
    w.add(100, "AcDbTextStyleTableRecord"); w.add(2, "Standard"); w.add(70, 0); w.add(40, 0.0); w.add(41, 1.0)
    w.add(50, 0.0); w.add(71, 0); w.add(42, 2.5); w.add(3, font); w.add(4, "")
    w.add(0, "ENDTAB")
    for name, h in (("VIEW", "6"), ("UCS", "7")):
        w.add(0, "TABLE"); w.add(2, name); w.add(5, h); w.add(330, 0); w.add(100, "AcDbSymbolTable"); w.add(70, 0)
        w.add(0, "ENDTAB")
    w.add(0, "TABLE"); w.add(2, "APPID"); w.add(5, "9"); w.add(330, 0); w.add(100, "AcDbSymbolTable"); w.add(70, 1)
    w.add(0, "APPID"); w.add(5, H_APPID); w.add(330, "9"); w.add(100, "AcDbSymbolTableRecord")
    w.add(100, "AcDbRegAppTableRecord"); w.add(2, "ACAD"); w.add(70, 0)
    w.add(0, "ENDTAB")
    w.add(0, "TABLE"); w.add(2, "DIMSTYLE"); w.add(5, "4"); w.add(330, 0); w.add(100, "AcDbSymbolTable"); w.add(70, 0)
    w.add(100, "AcDbDimStyleTable")
    w.add(0, "ENDTAB")
    w.add(0, "TABLE"); w.add(2, "BLOCK_RECORD"); w.add(5, "1"); w.add(330, 0); w.add(100, "AcDbSymbolTable"); w.add(70, 2)
    for name, h in (("*Model_Space", H_MODEL_RECORD), ("*Paper_Space", H_PAPER_RECORD)):
        w.add(0, "BLOCK_RECORD"); w.add(5, h); w.add(330, "1"); w.add(100, "AcDbSymbolTableRecord")
        w.add(100, "AcDbBlockTableRecord"); w.add(2, name); w.add(70, 0); w.add(280, 1); w.add(281, 0)
    w.add(0, "ENDTAB")
    w.add(0, "ENDSEC")

    w.add(0, "SECTION"); w.add(2, "BLOCKS")
    for name, record, h_block, h_end in (("*Model_Space", H_MODEL_RECORD, "20", "21"),
                                         ("*Paper_Space", H_PAPER_RECORD, "22", "23")):
        w.add(0, "BLOCK"); w.add(5, h_block); w.add(330, record); w.add(100, "AcDbEntity"); w.add(8, "0")
        w.add(100, "AcDbBlockBegin"); w.add(2, name); w.add(70, 0); w.add(10, 0.0); w.add(20, 0.0); w.add(30, 0.0)
        w.add(3, name); w.add(1, "")
        w.add(0, "ENDBLK"); w.add(5, h_end); w.add(330, record); w.add(100, "AcDbEntity"); w.add(8, "0")
        w.add(100, "AcDbBlockEnd")
    w.add(0, "ENDSEC")

    w.add(0, "SECTION"); w.add(2, "ENTITIES")
    for entity, definition, reactor, item in defs:
        width, height = item["size_px"]
        px, py = item["pixel"]
        x, y = item["insert"]
        # Optional 3D placement (Aero-Facade: a picture standing in a vertical plane): "insert_z", and the vectors of one pixel
        # along the image's columns ("u_vec") and up the image ("v_vec"). Without them the image lies flat, north up.
        z = item.get("insert_z", 0.0)
        ux, uy, uz = item.get("u_vec", (px, 0.0, 0.0))
        vx, vy, vz = item.get("v_vec", (0.0, py, 0.0))
        w.add(0, "IMAGE"); w.add(5, entity); w.add(330, H_MODEL_RECORD); w.add(100, "AcDbEntity")
        w.add(8, _clean_layer(item["layer"])); w.add(100, "AcDbRasterImage"); w.add(90, 0)
        w.add(10, _number(x)); w.add(20, _number(y)); w.add(30, _number(z) if z else 0.0)
        w.add(11, _number(ux)); w.add(21, _number(uy)); w.add(31, _number(uz) if uz else 0.0)
        w.add(12, _number(vx)); w.add(22, _number(vy)); w.add(32, _number(vz) if vz else 0.0)
        w.add(13, _number(width)); w.add(23, _number(height))
        clip = closed_polygon(item.get("clip"))
        w.add(340, definition); w.add(70, 7 if clip else 3); w.add(280, 1 if clip else 0)
        w.add(281, 50); w.add(282, 50); w.add(283, 0)
        if clip:                                        # polygonal clip (vertices in pixels, closed polygon)
            w.add(360, reactor); w.add(71, 2); w.add(91, len(clip))
            for x_clip, y_clip in clip:
                w.add(14, _number(x_clip)); w.add(24, _number(y_clip))
        else:                                           # rectangular clip: the whole image
            w.add(360, reactor); w.add(71, 1); w.add(91, 2)
            w.add(14, -0.5); w.add(24, -0.5); w.add(14, _number(width - 0.5)); w.add(24, _number(height - 0.5))
    for entity, item in zip(entity_handles, entities):
        _write_entity(w, entity, item)
    w.add(0, "ENDSEC")

    w.add(0, "SECTION"); w.add(2, "OBJECTS")
    w.add(0, "DICTIONARY"); w.add(5, H_ROOT_DICT); w.add(330, 0); w.add(100, "AcDbDictionary"); w.add(281, 1)
    w.add(3, "ACAD_IMAGE_DICT"); w.add(350, H_IMAGE_DICT)
    w.add(3, "ACAD_PLOTSTYLENAME"); w.add(350, H_PLOT_DICT)
    w.add(0, "ACDBDICTIONARYWDFLT"); w.add(5, H_PLOT_DICT); w.add(330, H_ROOT_DICT); w.add(100, "AcDbDictionary"); w.add(281, 1)
    w.add(3, "Normal"); w.add(350, H_PLOT_NORMAL); w.add(100, "AcDbDictionaryWithDefault"); w.add(340, H_PLOT_NORMAL)
    w.add(0, "ACDBPLACEHOLDER"); w.add(5, H_PLOT_NORMAL); w.add(330, H_PLOT_DICT)
    w.add(0, "DICTIONARY"); w.add(5, H_IMAGE_DICT); w.add(330, H_ROOT_DICT); w.add(100, "AcDbDictionary"); w.add(281, 1)
    for index, (entity, definition, reactor, item) in enumerate(defs, 1):
        w.add(3, "IMAGE_%d" % index); w.add(350, definition)
    for entity, definition, reactor, item in defs:
        width, height = item["size_px"]
        w.add(0, "IMAGEDEF"); w.add(5, definition)
        w.add(102, "{ACAD_REACTORS"); w.add(330, H_IMAGE_DICT); w.add(330, reactor); w.add(102, "}")
        w.add(330, H_IMAGE_DICT); w.add(100, "AcDbRasterImageDef"); w.add(90, 0); w.add(1, item["file"])
        w.add(10, _number(width)); w.add(20, _number(height)); w.add(11, 1.0); w.add(21, 1.0)
        w.add(280, 1); w.add(281, 0)
        w.add(0, "IMAGEDEF_REACTOR"); w.add(5, reactor); w.add(330, entity); w.add(100, "AcDbRasterImageDefReactor")
        w.add(90, 2); w.add(330, entity)
    w.add(0, "ENDSEC")
    w.add(0, "EOF")
    return w.text()


def write_dxf(path, images, units_m=True, entities=None, layers=None, extents=None, font="txt"):
    """Write the DXF (ASCII, CRLF) and return its path."""
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="ascii", errors="replace", newline="") as handle:
        handle.write(build_dxf(images, units_m, entities, layers, extents, font))
    return path


def placement(left, bottom, right, top, size_px):
    """(bottom-left insertion, pixel) of a north-up image whose extent is (left, bottom, right, top)."""

    width, height = size_px
    return (left, bottom), ((right - left) / float(width), (top - bottom) / float(height))
