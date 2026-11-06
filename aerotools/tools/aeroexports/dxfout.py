"""Aero-Exports : DXF qui reference les orthos et les MNT par des entites IMAGE georeferencees (sans dependance).

Pourquoi un DXF et pas un GeoTIFF : AutoCAD « simple » ne lit pas toujours le georef d'un GeoTIFF ou d'un ECW. Une
entite IMAGE a ses coordonnees monde dans le dessin lui-meme (point d'insertion + taille d'un pixel), donc l'image se
pose au bon endroit et a la bonne echelle quel que soit le produit Autodesk.

L'image n'est pas integree : le DXF pointe vers le fichier avec un chemin RELATIF (le dossier se deplace d'un bloc) :

    TOPO.dxf
    00_Assets\\ortho\\SITE_260704_3cm_ortho.jp2
    00_Assets\\DTM\\SITE_260704_20cm_dtm.jp2

Le DXF est ecrit a la main (R2000, structure relevee sur la sortie d'ezdxf puis verifiee dans AutoCAD 2023 par
accoreconsole) : ni ezdxf ni AutoCAD ne sont necessaires pour l'ecrire. Python 3.9, sans dependance.
"""

import os

ASSETS = "00_Assets"
ORTHO_DIR = "ortho"
DEM_DIR = "DTM"
CONTINUOUS = "Continuous"

# 0x10.. : poignees des objets du dessin ; les images en prennent d'autres au-dessus.
H_ROOT_DICT, H_IMAGE_DICT = "A", "B"
H_PLOT_DICT, H_PLOT_NORMAL = "12", "13"      # style de trace « Normal » : AutoCAD l'exige sur chaque calque
H_MODEL_RECORD, H_PAPER_RECORD = "1F", "1B"
H_LTYPE, H_STYLE, H_APPID, H_LAYER0 = "14", "15", "16", "17"
FIRST_FREE = 0x100


def relative_path(asset_path, dxf_path):
    """Chemin de l'image vu depuis le dossier du DXF, avec des antislashs (Windows / AutoCAD)."""
    base = os.path.dirname(os.path.abspath(dxf_path))
    return os.path.relpath(os.path.abspath(asset_path), base).replace("/", "\\")


def asset_folder(dxf_path, kind):
    """Dossier des images d'un DXF : `00_Assets\\ortho` ou `00_Assets\\DTM`."""
    return os.path.join(os.path.dirname(os.path.abspath(dxf_path)), ASSETS, ORTHO_DIR if kind == "ortho" else DEM_DIR)


def layer_name(date_text, pixel_text, kind):
    """Calque d'une image : « 260704_0.1_ortho » ou « 260704_0.2_MNT » (date, pixel en metres, nature)."""
    tail = "ortho" if kind == "ortho" else "MNT"
    return "_".join(part for part in (date_text, pixel_text, tail) if part)


def _clean_layer(name):
    """Un nom de calque DXF ne contient pas < > / \\ \" : ; ? * | = `."""
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


def build_dxf(images, units_m=True):
    """Texte d'un DXF R2000.

    `images` : liste de dicts {"layer", "file" (chemin tel qu'ecrit dans le DXF), "size_px": (largeur, hauteur),
    "insert": (x, y) coin bas-gauche en unites du dessin, "pixel": (largeur d'un pixel, hauteur d'un pixel),
    "clip" (facultatif) : polygone ferme [(x, y), ...] en pixels de l'image, depuis son coin bas-gauche (voir footprint.py)}.
    """
    w = _Writer()
    layers = []
    for item in images:
        name = _clean_layer(item["layer"])
        if name not in layers and name != "0":
            layers.append(name)
    handle = [FIRST_FREE]

    def new_handle():
        handle[0] += 1
        return "%X" % handle[0]

    layer_handles = {name: new_handle() for name in layers}
    defs = [(new_handle(), new_handle(), new_handle(), item) for item in images]   # (entite, definition, reactor)

    w.add(0, "SECTION"); w.add(2, "HEADER")
    w.add(9, "$ACADVER"); w.add(1, "AC1015")
    w.add(9, "$HANDSEED"); w.add(5, "%X" % (handle[0] + 1))
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
        w.add(100, "AcDbLayerTableRecord"); w.add(2, name); w.add(70, 0); w.add(62, 7); w.add(6, CONTINUOUS)
        w.add(370, -3); w.add(390, H_PLOT_NORMAL)
    w.add(0, "ENDTAB")
    w.add(0, "TABLE"); w.add(2, "STYLE"); w.add(5, "3"); w.add(330, 0); w.add(100, "AcDbSymbolTable"); w.add(70, 1)
    w.add(0, "STYLE"); w.add(5, H_STYLE); w.add(330, "3"); w.add(100, "AcDbSymbolTableRecord")
    w.add(100, "AcDbTextStyleTableRecord"); w.add(2, "Standard"); w.add(70, 0); w.add(40, 0.0); w.add(41, 1.0)
    w.add(50, 0.0); w.add(71, 0); w.add(42, 2.5); w.add(3, "txt"); w.add(4, "")
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
        w.add(0, "IMAGE"); w.add(5, entity); w.add(330, H_MODEL_RECORD); w.add(100, "AcDbEntity")
        w.add(8, _clean_layer(item["layer"])); w.add(100, "AcDbRasterImage"); w.add(90, 0)
        w.add(10, _number(x)); w.add(20, _number(y)); w.add(30, 0.0)
        w.add(11, _number(px)); w.add(21, 0.0); w.add(31, 0.0)
        w.add(12, 0.0); w.add(22, _number(py)); w.add(32, 0.0)
        w.add(13, _number(width)); w.add(23, _number(height))
        clip = closed_polygon(item.get("clip"))
        w.add(340, definition); w.add(70, 7 if clip else 3); w.add(280, 1 if clip else 0)
        w.add(281, 50); w.add(282, 50); w.add(283, 0)
        if clip:                                        # decoupe polygonale (sommets en pixels, polygone ferme)
            w.add(360, reactor); w.add(71, 2); w.add(91, len(clip))
            for x_clip, y_clip in clip:
                w.add(14, _number(x_clip)); w.add(24, _number(y_clip))
        else:                                           # decoupe rectangulaire : l'image entiere
            w.add(360, reactor); w.add(71, 1); w.add(91, 2)
            w.add(14, -0.5); w.add(24, -0.5); w.add(14, _number(width - 0.5)); w.add(24, _number(height - 0.5))
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


def write_dxf(path, images, units_m=True):
    """Ecrit le DXF (ASCII, CRLF) et renvoie son chemin."""
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="ascii", errors="replace", newline="") as handle:
        handle.write(build_dxf(images, units_m))
    return path


def placement(left, bottom, right, top, size_px):
    """(insertion bas-gauche, pixel) d'une image nord-en-haut dont l'emprise est (gauche, bas, droite, haut)."""
    width, height = size_px
    return (left, bottom), ((right - left) / float(width), (top - bottom) / float(height))
