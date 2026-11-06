"""Aero-Facade drawing sheet: the pure layout of all the facade pictures on ONE drawing (no Metashape, no Qt, no files).

What the sheet looks like (drawing units = metres, 1:1, `$INSUNITS` 6):

* ONE row, left to right, the facades ordered by the compass bearing of the direction they face (N, NE, E, SE, S, SW, W, NW), a constant
  gap between two pictures. The first picture's left edge is at X = 0. A single row (not a grid) so that every picture shares the same
  Y scale and one set of level lines serves all of them.
* Y is the HEIGHT: each picture stands at the height of its bottom edge (`SheetFacade.bottom`, already in the sheet's Y system: relative
  to a common zero, or an altitude of a vertical datum). Whoever measures reads the height straight from the Y coordinate.
* Each picture has a thin frame and a title under it ("FACADE NORD" + a smaller detail line). Duplicated names get the facade letter.
* Dressing, all on layers a user can freeze: horizontal level lines at round values (0.5 / 1 / 2 / 5 ... m, chosen from the total height),
  labelled at both ends, a graduated vertical axis on the left, short ticks on both edges of each frame. The level lines are cut around
  the pictures and the titles: they never run through a picture.
* A small note under the sheet says which height system the Y values are in.

`build_sheet` returns a `Sheet`: the IMAGE records for `aeroexports.dxfout`, its TEXT / LINE / LWPOLYLINE entities, the layers and the
extents. Python 3.9, standard library only.
"""

import math
from dataclasses import dataclass, field

from . import sheet_labels as labels

FONT = "simplex"                  # SHX font of the drawing text (the default "txt" draws the capital O as a square)
LAYER_LEVELS = "FACADE_LEVELS"
LAYER_TITLES = "FACADE_TITLES"
LAYER_FRAMES = "FACADE_FRAMES"
LEVEL_STEPS = (0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0)
MAX_LEVELS = 20                  # a step is chosen so that the total height holds at most this many level lines
MIN_GAP = 2.0                    # metres between two pictures (at least)
GAP_FRACTION = 0.05              # ... or this fraction of the mean picture width
TEXT_WIDTH = 0.65                # width of a character, in text heights (a safe over-estimate for the SHX font): used to cut the lines
ACI_LEVELS = 8                   # AutoCAD colour index of the level layer (grey)
ACI_PLAIN = 7
LINEWEIGHT_THIN = 9              # hundredths of a millimetre


def image_layer(letter):
    return "FACADE_%s" % letter


@dataclass
class SheetFacade:
    """One picture to place. `bottom` is the Y of its bottom edge in the sheet's system; `file` is the path as written in the DXF."""
    letter: str
    azimuth: float
    size_px: tuple
    pixel: float
    bottom: float
    file: str = ""

    @property
    def width(self):
        return self.size_px[0] * self.pixel

    @property
    def height(self):
        return self.size_px[1] * self.pixel

    @property
    def top(self):
        return self.bottom + self.height


@dataclass
class Sheet:
    images: list = field(default_factory=list)      # dicts for dxfout.build_dxf
    entities: list = field(default_factory=list)    # dicts for dxfout.build_dxf(entities=...)
    layers: dict = field(default_factory=dict)      # layer name -> (aci colour, lineweight)
    extents: tuple = (0.0, 0.0, 0.0, 0.0)           # (xmin, ymin, xmax, ymax) of everything
    names: dict = field(default_factory=dict)       # letter -> title (first line)
    order: list = field(default_factory=list)       # letters, left to right
    x_of: dict = field(default_factory=dict)        # letter -> X of the left edge
    levels: list = field(default_factory=list)      # the Y values of the level lines
    step: float = 1.0
    gap: float = 0.0
    text_height: float = 0.0

    def count(self, kind, layer=None):
        return sum(1 for e in self.entities if e["type"] == kind and (layer is None or e["layer"] == layer))


# --------------------------------------------------------------------------------------------------
# Names and order
# --------------------------------------------------------------------------------------------------

def compass(azimuth):
    """Code of the 45 degree sector that holds `azimuth` (degrees, clockwise from north): "N", "NE", "E", ..."""
    return labels.COMPASS[int(((float(azimuth) % 360.0) + 22.5) // 45.0) % 8]


def sector_key(azimuth):
    """Sort key: clockwise from the start of the north sector, so a wall facing 357 degrees comes first like one facing 3."""
    return (float(azimuth) % 360.0 + 22.5) % 360.0


def ordered(facades):
    """The facades left to right: by bearing in steps of 5 degrees from the start of the north sector (walls that face the same way within
    5 degrees keep the order of their letters, whatever the noise of the detection), then by letter."""
    return sorted(facades, key=lambda f: (int(sector_key(f.azimuth) // 5.0), f.letter))


def names(facades, language=labels.DEFAULT_LANGUAGE):
    """{letter: title} ("FACADE NORD"). Two facades with the same name both get their letter: "FACADE NORD (A)", "FACADE NORD (B)"."""
    words = labels.table(language)
    base = {f.letter: words["title"].format(direction=words["dir"][compass(f.azimuth)]) for f in facades}
    count = {}
    for title in base.values():
        count[title] = count.get(title, 0) + 1
    return {letter: ("%s (%s)" % (title, letter) if count[title] > 1 else title) for letter, title in base.items()}


def detail_lines(facade, language=labels.DEFAULT_LANGUAGE):
    """The smaller lines under a title: azimuth and size in metres, then the pixel size in centimetres."""
    azimuth = int(round(float(facade.azimuth) % 360.0)) % 360
    words = labels.table(language)
    return [words["detail"].format(azimuth=azimuth, width="%.1f" % facade.width, height="%.1f" % facade.height),
            words["pixel"].format(pixel="%g" % round(facade.pixel * 100.0, 2))]


def detail_line(facade, language=labels.DEFAULT_LANGUAGE):
    """The detail lines on one line (used when the picture is wide enough to hold it)."""
    return " - ".join(detail_lines(facade, language))


# --------------------------------------------------------------------------------------------------
# Levels
# --------------------------------------------------------------------------------------------------

def level_step(span):
    """Spacing of the level lines for a total height `span` (metres): the smallest of 0.5, 1, 2, 5, 10 ... m that gives at most MAX_LEVELS lines."""
    for step in LEVEL_STEPS:
        if span / step <= MAX_LEVELS:
            return step
    return LEVEL_STEPS[-1] * math.ceil(span / LEVEL_STEPS[-1] / MAX_LEVELS)


def minor_step(step):
    """Spacing of the small graduations of the axis (five or four per level)."""
    return step / 4.0 if step == 2.0 else step / 5.0


def level_values(low, high, step):
    """The multiples of `step` from the one at or under `low` to the one at or over `high`."""
    first = int(math.floor(low / step + 1e-9))
    last = int(math.ceil(high / step - 1e-9))
    return [round(k * step, 6) for k in range(first, last + 1)]


def level_text(value, mode, datum_short=""):
    """Label of a level: "+3.00" (relative), "NGF-IGN69 123.45" (altitude with a datum name). Never "-0.00"."""
    value = 0.0 if abs(value) < 5e-7 else value
    if mode == "altitude":
        return ("%s %.2f" % (datum_short, value)).strip()
    return "%.2f" % value if value == 0 else "%+.2f" % value


def _cut(x0, x1, blocks):
    """Pieces of the segment [x0, x1] that are outside the (a, b) intervals of `blocks`."""
    pieces = [(x0, x1)]
    for a, b in sorted(blocks):
        out = []
        for p, q in pieces:
            if b <= p or a >= q:
                out.append((p, q))
                continue
            if a > p:
                out.append((p, a))
            if b < q:
                out.append((b, q))
        pieces = out
    return pieces


# --------------------------------------------------------------------------------------------------
# The sheet
# --------------------------------------------------------------------------------------------------

def _nice(value):
    """A text height rounded to two significant figures."""
    if value <= 0:
        return 0.1
    digits = 1 - int(math.floor(math.log10(value)))
    return round(value, digits)


def build_sheet(facades, language=labels.DEFAULT_LANGUAGE, mode="relative", datum_short="", datum_note="", site="", date=""):
    """Lay the facades out on one sheet.

    `facades`: list of `SheetFacade`. `mode`: "relative" (Y = height above the common zero) or "altitude" (Y = altitude of a vertical
    datum, named by `datum_short` for the level labels and `datum_note` for the note under the sheet)."""
    words = labels.table(language)
    items = ordered(list(facades))
    if not items:
        raise ValueError("no facade to place")
    sheet = Sheet()
    widths = [f.width for f in items]
    gap = max(MIN_GAP, GAP_FRACTION * sum(widths) / len(widths))
    gap = round(gap, 1)
    xs, cursor = [], 0.0
    for f in items:
        xs.append(cursor)
        cursor += f.width + gap
    x_end = xs[-1] + items[-1].width + gap                # right end of the level lines
    total_width = xs[-1] + items[-1].width
    lh = _nice(min(1.0, max(0.12, total_width / 300.0)))      # height of the small labels
    th, sh = round(2.2 * lh, 3), round(1.1 * lh, 3)           # title and detail line
    ymin = min(f.bottom for f in items)
    ymax = max(f.top for f in items)
    step = level_step(ymax - ymin)
    levels = level_values(ymin, ymax, step)
    y_low, y_high = levels[0], levels[-1]
    x_axis = -gap
    titles = names(items, language)
    sheet.names, sheet.order, sheet.levels, sheet.step, sheet.gap, sheet.text_height = titles, [f.letter for f in items], levels, step, gap, lh
    sheet.x_of = {f.letter: x for f, x in zip(items, xs)}

    layers = {LAYER_LEVELS: (ACI_LEVELS, LINEWEIGHT_THIN), LAYER_FRAMES: (ACI_PLAIN, -3), LAYER_TITLES: (ACI_PLAIN, -3)}
    ents = []

    def line(layer, p, q):
        ents.append({"type": "LINE", "layer": layer, "p1": (round(p[0], 4), round(p[1], 4)), "p2": (round(q[0], 4), round(q[1], 4))})

    def text(layer, pos, height, value, align="left"):
        ents.append({"type": "TEXT", "layer": layer, "pos": (round(pos[0], 4), round(pos[1], 4)), "height": height, "text": value, "align": align})

    # titles first: their boxes are needed to cut the level lines
    boxes = []                                            # (x0, y0, x1, y1) of each title block
    title_rows = []
    for f, x in zip(items, xs):
        first, one = titles[f.letter], detail_line(f, language)
        # one detail line when it fits under its picture (the gap is shared with the neighbour), else one line each
        rows = [one] if len(one) * sh * TEXT_WIDTH <= f.width + 0.8 * gap else detail_lines(f, language)
        y1 = f.bottom - 0.5 * th - th
        ys = [y1 - 0.4 * th - sh - k * 1.4 * sh for k in range(len(rows))]
        half = max([len(first) * th * TEXT_WIDTH] + [len(r) * sh * TEXT_WIDTH for r in rows]) / 2.0 + lh
        cx = x + f.width / 2.0
        boxes.append((cx - half, ys[-1] - 0.3 * sh, cx + half, y1 + 1.1 * th))
        title_rows.append((cx, y1, ys, first, rows))

    # level lines: from the axis to the right end, never through a picture or a title
    for y in levels:
        blocks = [(x, x + f.width) for f, x in zip(items, xs) if f.bottom - 1e-9 <= y <= f.top + 1e-9]
        blocks += [(b[0], b[2]) for b in boxes if b[1] <= y <= b[3]]
        for p, q in _cut(x_axis, x_end, blocks):
            if q - p > 0.2 * lh:
                line(LAYER_LEVELS, (p, y), (q, y))
        label = level_text(y, mode, datum_short)
        text(LAYER_LEVELS, (x_axis - 1.2 * lh, y - lh / 2.0), lh, label, "right")
        text(LAYER_LEVELS, (x_end + 0.5 * lh, y - lh / 2.0), lh, label, "left")

    # the axis, with a small graduation between the labelled levels
    line(LAYER_LEVELS, (x_axis, y_low), (x_axis, y_high))
    for y in levels:
        line(LAYER_LEVELS, (x_axis - 0.9 * lh, y), (x_axis, y))
    small = minor_step(step)
    k0, k1 = int(math.floor(y_low / small + 1e-9)), int(math.ceil(y_high / small - 1e-9))
    for k in range(k0, k1 + 1):
        y = k * small
        if abs(y / step - round(y / step)) > 1e-6:
            line(LAYER_LEVELS, (x_axis - 0.45 * lh, y), (x_axis, y))
    text(LAYER_LEVELS, (x_axis, y_high + 1.4 * lh), lh, words["axis_altitude" if mode == "altitude" else "axis_relative"], "right")

    # frames, and ticks on both edges of each frame
    tick = 1.5 * lh
    for f, x in zip(items, xs):
        ents.append({"type": "LWPOLYLINE", "layer": LAYER_FRAMES, "closed": True,
                     "points": [(round(x, 4), round(f.bottom, 4)), (round(x + f.width, 4), round(f.bottom, 4)),
                                (round(x + f.width, 4), round(f.top, 4)), (round(x, 4), round(f.top, 4))]})
        for y in levels:
            if f.bottom + 1e-9 < y < f.top - 1e-9:
                line(LAYER_LEVELS, (x, y), (x + tick, y))
                line(LAYER_LEVELS, (x + f.width - tick, y), (x + f.width, y))

    # titles
    lowest = ymin
    for cx, y1, ys, first, rows in title_rows:
        text(LAYER_TITLES, (cx, y1), th, first, "center")
        for y, row in zip(ys, rows):
            text(LAYER_TITLES, (cx, y), sh, row, "center")
        lowest = min(lowest, ys[-1])

    # the note under the sheet
    y_note = lowest - 2.5 * lh
    note = words["note_altitude"].format(datum=datum_note or datum_short) if mode == "altitude" else words["note_relative"]
    tail = " %s %s" % (site, date) if (site or date) else ""
    units = words["note_units"].format(site=tail.rstrip(), y=words["y_altitude" if mode == "altitude" else "y_relative"])
    text(LAYER_TITLES, (0.0, y_note), 1.2 * lh, note)
    text(LAYER_TITLES, (0.0, y_note - 2.0 * lh), lh, units)

    sheet.entities = ents
    sheet.layers = layers
    for f in items:
        layer = image_layer(f.letter)
        layers[layer] = (ACI_PLAIN, -3)
        sheet.images.append({"layer": layer, "file": f.file, "size_px": tuple(f.size_px), "insert": (sheet.x_of[f.letter], f.bottom),
                             "pixel": (f.pixel, f.pixel)})
    right = x_end + 0.5 * lh + max(len(level_text(v, mode, datum_short)) for v in levels) * lh * TEXT_WIDTH
    left = x_axis - 1.2 * lh - max(len(level_text(v, mode, datum_short)) for v in levels) * lh * TEXT_WIDTH
    sheet.extents = (round(left, 3), round(y_note - 2.5 * lh, 3), round(max(right, 0.0), 3), round(y_high + 3.0 * lh, 3))
    return sheet
