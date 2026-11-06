"""Aero-Facade UI: small helpers shared by the widgets and the screens (texts, numbers, colours, shortcuts)."""

from ... import i18n
from ...qt import QtCore, QtGui


def tr(key, **fields):
    """Text of the interface for a full key (the whole key, with its prefix)."""
    return i18n.t(key, **fields)


def metres(value, decimals=1):
    """A length in metres written with the decimal mark of the interface language."""
    return i18n.fmt_number(value, decimals)


def readable_on(colour):
    """Black or white, whichever reads best on the colour `colour` (a "#rrggbb" string)."""
    colour = colour.lstrip("#")
    try:
        red, green, blue = [int(colour[i:i + 2], 16) for i in (0, 2, 4)]
    except ValueError:
        return "#000000"
    return "#000000" if 0.299 * red + 0.587 * green + 0.114 * blue > 150 else "#ffffff"


def qt_shortcut(widget, sequence, handler):
    """A keyboard shortcut that works while `widget` or one of its children has the focus (None when Qt has no QShortcut)."""
    from ...qt import QShortcut
    if QShortcut is None:
        return None
    shortcut = QShortcut(QtGui.QKeySequence(sequence), widget)
    shortcut.setContext(QtCore.Qt.WidgetWithChildrenShortcut)
    shortcut.activated.connect(handler)
    return shortcut


DIRECTION_KEYS = {
    "N": "facade.ui.dir_N", "NE": "facade.ui.dir_NE", "E": "facade.ui.dir_E", "SE": "facade.ui.dir_SE",
    "S": "facade.ui.dir_S", "SW": "facade.ui.dir_SW", "W": "facade.ui.dir_W", "NW": "facade.ui.dir_NW",
}
POLY_KEYS = {"few": "facade.ui.poly_few", "flat": "facade.ui.poly_flat", "crossing": "facade.ui.poly_crossing"}
STATE_KEYS = {"waiting": "facade.ui.state_waiting", "running": "facade.ui.state_running", "done": "facade.ui.state_done",
              "failed": "facade.ui.state_failed", "cancelled": "facade.ui.state_cancelled"}
SIDE_KEYS = {"left": "facade.ui.side_left", "right": "facade.ui.side_right", "top": "facade.ui.side_top",
             "bottom": "facade.ui.side_bottom"}
LINK_KEYS = {("lr", True): "facade.ui.link_on_lr", ("lr", False): "facade.ui.link_off_lr",
             ("tb", True): "facade.ui.link_on_tb", ("tb", False): "facade.ui.link_off_tb"}
FORMAT_KEYS = {"png": ("facade.ui.format_png", "facade.ui.format_png_text", "facade.ui.format_png_tip"),
               "jpg": ("facade.ui.format_jpg", "facade.ui.format_jpg_text", "facade.ui.format_jpg_tip"),
               "tif": ("facade.ui.format_tif", "facade.ui.format_tif_text", "facade.ui.format_tif_tip"),
               "jp2": ("facade.ui.format_jp2", "facade.ui.format_jp2_text", "facade.ui.format_jp2_tip"),
               "ecw": ("facade.ui.format_ecw", "facade.ui.format_ecw_text", "facade.ui.format_ecw_tip")}
FORMAT_NEED_KEYS = {"jp2": "facade.ui.format_need_jp2", "ecw": "facade.ui.format_need_ecw"}
SHEET_KEYS = ("facade.ui.sheet_title", "facade.ui.sheet_on", "facade.ui.sheet_on_tip", "facade.ui.sheet_dxf", "facade.ui.sheet_dwg",
              "facade.ui.sheet_dxf_tip", "facade.ui.sheet_dwg_tip", "facade.ui.sheet_need_dwg", "facade.ui.sheet_pictures",
              "facade.ui.sheet_pictures_tip", "facade.ui.sheet_titles", "facade.ui.sheet_lang_fr", "facade.ui.sheet_lang_en",
              "facade.ui.sheet_z", "facade.ui.sheet_z_tip", "facade.ui.sheet_z_none", "facade.ui.sheet_relative",
              "facade.ui.height_not_covering", "facade.ui.height_ellipsoid_note", "facade.ui.names_sheet")


def dir_key(facing):
    """Full text key of a compass name."""
    return DIRECTION_KEYS.get(facing, DIRECTION_KEYS["N"])
