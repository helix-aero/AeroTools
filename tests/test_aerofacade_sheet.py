"""Aero-Facade drawing sheet: naming, order, layout, levels, entities (sheet.py), the DXF writer's new entities (dxfout.py), the heights and the
files (sheetout.py). No Metashape, no Qt; AutoCAD, GDAL and Global Mapper are replaced by fakes."""
import math
import os
import re
import shutil

import numpy as np
import pytest

from aerotools.tools.aerofacade import sheet, sheet_labels, sheetout
from aerotools.tools.aerofacade.sheet import SheetFacade
from aerotools.tools.aeroexports import crs, dxfout, external, geotiff, projtool

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
RAC23_ID = "fr_ign_RAC23"
needs_proj = pytest.mark.skipif(not projtool.Proj().available(), reason="PROJ not embedded")


def fac(letter, azimuth, width=60.0, height=20.0, bottom=0.0, pixel=0.05, file=None):
    return SheetFacade(letter=letter, azimuth=azimuth, size_px=(int(round(width / pixel)), int(round(height / pixel))), pixel=pixel,
                       bottom=bottom, file=file or r"00_Assets\ortho\x_facade_%s.png" % letter)


def pairs(text):
    lines = text.split("\r\n")
    return [(lines[i].strip(), lines[i + 1]) for i in range(0, len(lines) - 1, 2)]


# -- names, order -----------------------------------------------------------------------------------

@pytest.mark.parametrize("azimuth,code", [(0, "N"), (22, "N"), (23, "NE"), (45, "NE"), (90, "E"), (135, "SE"), (180, "S"), (202, "S"),
                                          (203, "SW"), (270, "W"), (315, "NW"), (337, "NW"), (338, "N"), (359.9, "N"), (-90, "W"), (720, "N")])
def test_compass_sectors(azimuth, code):
    assert sheet.compass(azimuth) == code


def test_titles_use_the_cardinal_point_of_the_outward_normal_in_both_languages():
    items = [fac("A", 180), fac("B", 0), fac("C", 90), fac("D", 270), fac("E", 225)]
    fr = sheet.names(items, "fr")
    assert fr == {"A": "FACADE SUD", "B": "FACADE NORD", "C": "FACADE EST", "D": "FACADE OUEST", "E": "FACADE SUD-OUEST"}
    en = sheet.names(items, "en")
    assert en["A"] == "SOUTH FACADE" and en["E"] == "SOUTH-WEST FACADE"
    assert sheet.names(items, "xx") == fr                      # an unknown language gives the default (French)


def test_a_repeated_name_gets_the_facade_letter_and_a_unique_one_does_not():
    names = sheet.names([fac("A", 180), fac("B", 175), fac("C", 0)], "fr")
    assert names == {"A": "FACADE SUD (A)", "B": "FACADE SUD (B)", "C": "FACADE NORD"}


def test_order_is_clockwise_from_north_and_a_wall_just_west_of_north_comes_first():
    items = [fac("A", 180), fac("B", 270), fac("C", 357), fac("D", 90), fac("E", 45), fac("F", 5)]
    order = [f.letter for f in sheet.ordered(items)]
    assert order[:2] == ["C", "F"]                  # 357 then 5: both north, the one nearer to the start of the sector first
    assert order[2:] == ["E", "D", "A", "B"]
    same = [fac("C", 175.4), fac("A", 174.6), fac("B", 176.0)]
    assert [f.letter for f in sheet.ordered(same)] == ["A", "B", "C"]          # the same way within 5 degrees: letters, not detection noise


# -- levels ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("span,step", [(3, 0.5), (10, 0.5), (11, 1.0), (20, 1.0), (21, 2.0), (40, 2.0), (50, 5.0), (100, 5.0), (150, 10.0)])
def test_level_step_grows_with_the_total_height(span, step):
    assert sheet.level_step(span) == step


def test_level_values_are_round_and_cover_the_range():
    assert sheet.level_values(0.3, 5.2, 1.0) == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    assert sheet.level_values(91.5, 111.5, 2.0)[0] == 90.0 and sheet.level_values(91.5, 111.5, 2.0)[-1] == 112.0
    assert sheet.level_values(0.0, 4.0, 2.0) == [0.0, 2.0, 4.0]


def test_level_labels():
    assert sheet.level_text(3.0, "relative") == "+3.00"
    assert sheet.level_text(0.0, "relative") == "0.00"
    assert sheet.level_text(-1.5, "relative") == "-1.50"
    assert sheet.level_text(123.45, "altitude", "NGF-IGN69") == "NGF-IGN69 123.45"
    assert sheet.level_text(-1e-9, "relative") == "0.00"


# -- the sheet ---------------------------------------------------------------------------------------

def make(items=None, **kw):
    items = items or [fac("A", 180, 66, 22, 3.0), fac("B", 0, 67, 17, 5.0), fac("C", 90, 12, 17, 4.5), fac("D", 270, 12.5, 24, 2.0)]
    return items, sheet.build_sheet(items, **kw)


def test_one_row_left_to_right_first_picture_at_x_zero_with_a_constant_gap():
    items, s = make()
    assert s.order == ["B", "C", "A", "D"]                           # N, E, S, W
    widths = {f.letter: f.width for f in items}
    assert s.x_of["B"] == 0.0
    assert s.gap == max(2.0, round(0.05 * sum(widths.values()) / 4, 1))
    x = 0.0
    for letter in s.order:
        assert s.x_of[letter] == pytest.approx(x)
        x += widths[letter] + s.gap
    assert [im["insert"][0] for im in s.images] == [s.x_of[l] for l in s.order]


def test_pictures_stand_at_the_height_of_their_bottom_edge_at_one_to_one():
    items, s = make()
    by_layer = {im["layer"]: im for im in s.images}
    for f in items:
        im = by_layer["FACADE_%s" % f.letter]
        assert im["insert"][1] == f.bottom
        assert im["pixel"] == (f.pixel, f.pixel) and im["size_px"] == f.size_px
        assert im["file"] == f.file


def test_the_gap_is_at_least_two_metres_and_five_percent_of_the_mean_width():
    _, s = make([fac("A", 0, 10, 5), fac("B", 90, 12, 5)])
    assert s.gap == 2.0
    _, s = make([fac("A", 0, 200, 5), fac("B", 90, 100, 5)])
    assert s.gap == 7.5


def test_entity_counts_and_layers():
    items, s = make()
    assert len(s.images) == 4
    assert s.count("LWPOLYLINE", sheet.LAYER_FRAMES) == 4 and s.count("LWPOLYLINE") == 4
    titles = [e for e in s.entities if e["type"] == "TEXT" and e["layer"] == sheet.LAYER_TITLES]
    assert {"FACADE NORD", "FACADE EST", "FACADE SUD", "FACADE OUEST"} <= {t["text"] for t in titles}
    assert len([t for t in titles if t["text"].startswith("FACADE")]) == 4
    assert set(s.layers) == {sheet.LAYER_LEVELS, sheet.LAYER_FRAMES, sheet.LAYER_TITLES, "FACADE_A", "FACADE_B", "FACADE_C", "FACADE_D"}
    assert all(e["layer"] in s.layers for e in s.entities)
    labels = [e for e in s.entities if e["type"] == "TEXT" and e["layer"] == sheet.LAYER_LEVELS]
    assert sum(1 for e in labels if e["align"] == "left") == len(s.levels) == sum(1 for e in labels if e["align"] == "right") - 1  # +1: axis title


def test_level_lines_never_cross_a_picture_or_a_title():
    items, s = make()
    rects = [(s.x_of[f.letter], f.bottom, s.x_of[f.letter] + f.width, f.top) for f in items]
    drawn = 0
    for e in s.entities:
        if e["type"] != "LINE" or e["p1"][1] != e["p2"][1] or abs(e["p1"][0] - e["p2"][0]) < 3.0:
            continue
        y, a, b = e["p1"][1], min(e["p1"][0], e["p2"][0]), max(e["p1"][0], e["p2"][0])
        drawn += 1
        for x0, y0, x1, y1 in rects:
            assert not (y0 < y < y1 and a < x1 and b > x0), "a level line runs through a picture"
    assert drawn >= 1
    pieces = {}
    for e in s.entities:
        if e["type"] == "LINE" and e["p1"][1] == e["p2"][1] and e["p1"][1] in s.levels and abs(e["p1"][0] - e["p2"][0]) > 1.0:
            pieces[e["p1"][1]] = pieces.get(e["p1"][1], 0) + 1
    assert max(pieces.values()) > 1                                      # a level inside the pictures' height is cut in pieces, not dropped
    for lvl in s.levels:                                                 # every level is drawn somewhere
        assert any(e["type"] == "LINE" and e["p1"][1] == lvl == e["p2"][1] for e in s.entities)


def test_ticks_sit_on_both_edges_of_each_frame_inside_its_height():
    items, s = make([fac("A", 180, 60, 20, 0.0)])
    f = items[0]
    left = [e for e in s.entities if e["type"] == "LINE" and e["p1"][0] == 0.0 and e["p2"][0] > 0.0 and e["p1"][1] == e["p2"][1]]
    right = [e for e in s.entities if e["type"] == "LINE" and e["p2"][0] == f.width and e["p1"][0] < f.width and e["p1"][1] == e["p2"][1]]
    assert len(left) == len(right) == len([v for v in s.levels if 0.0 < v < 20.0])


def test_relative_and_altitude_notes_and_labels():
    _, rel = make(mode="relative")
    texts = [e["text"] for e in rel.entities if e["type"] == "TEXT"]
    assert "+2.00" in texts and any(t.startswith("Hauteurs relatives") for t in texts)
    assert any("Y = hauteur" in t for t in texts)
    items = [fac("A", 180, 66, 22, 91.5), fac("B", 0, 67, 17, 93.6)]
    s = sheet.build_sheet(items, "en", "altitude", "NGF-IGN78", "NGF-IGN78 / RAC09, geoid grid fr_ign_RAC09.tif", "CARRO", "261106")
    texts = [e["text"] for e in s.entities if e["type"] == "TEXT"]
    assert "NGF-IGN78 92.00" in texts
    assert "Altimetry: NGF-IGN78 / RAC09, geoid grid fr_ign_RAC09.tif" in texts
    assert "NORTH FACADE" in texts and "SOUTH FACADE" in texts
    assert s.levels[0] == 90.0 and s.levels[-1] >= 112.0


def test_a_narrow_picture_puts_its_detail_on_two_lines():
    items, s = make([fac("A", 180, 66, 22), fac("B", 90, 3, 10)])
    texts = [e["text"] for e in s.entities if e["type"] == "TEXT"]
    assert "pixel 5 cm" in texts and any(t.startswith("azimut 90%%d - 3.0 x 10.0 m") and "pixel" not in t for t in texts)
    assert any(t.startswith("azimut 180%%d") and "pixel 5 cm" in t for t in texts)       # the wide one keeps one line


def test_all_the_drawing_text_is_ascii_in_both_languages():
    for language in ("fr", "en"):
        for mode in ("relative", "altitude"):
            _, s = make(language=language, mode=mode, datum_short="NGF-IGN69", datum_note="NGF-IGN69 / RAF20", site="CARRO", date="261106")
            for e in s.entities:
                if e["type"] == "TEXT":
                    e["text"].encode("ascii")
    for table in sheet_labels.TABLE.values():
        for value in list(table.values()) + list(table["dir"].values()):
            if isinstance(value, str):
                value.encode("ascii")


def test_extents_hold_everything_and_there_is_no_facade_to_lay_out_is_an_error():
    items, s = make()
    xmin, ymin, xmax, ymax = s.extents
    assert xmin < 0 < s.x_of["D"] < xmax
    assert ymin < min(f.bottom for f in items) and ymax > max(f.top for f in items)
    with pytest.raises(ValueError):
        sheet.build_sheet([])


# -- dxfout: the new entities ------------------------------------------------------------------------

def test_dxf_writes_text_lines_and_polylines_with_layers_and_unique_handles():
    items, s = make()
    text = dxfout.build_dxf(s.images, True, s.entities, s.layers, s.extents)
    p = pairs(text)
    kinds = [v for c, v in p if c == "0"]
    assert kinds.count("IMAGE") == 4 and kinds.count("TEXT") == s.count("TEXT") and kinds.count("LINE") == s.count("LINE")
    assert kinds.count("LWPOLYLINE") == 4
    assert text.endswith("0\r\nEOF\r\n") and "\n" not in text.replace("\r\n", "")
    text.encode("ascii")
    at = [i for i, (c, v) in enumerate(p) if v == "$HANDSEED"][0] + 1          # the pair right after $HANDSEED holds the next free handle
    handles = [v for i, (c, v) in enumerate(p) if c == "5" and i != at]
    assert len(handles) == len(set(handles))
    assert int(p[at][1], 16) > max(int(h, 16) for h in handles)
    assert ("9", "$EXTMIN") in p and ("9", "$EXTMAX") in p
    layer_names = [v for c, v in p if c == "2" and v.startswith("FACADE_")]
    assert sheet.LAYER_LEVELS in layer_names and "FACADE_A" in layer_names


def test_dxf_layer_colour_and_lineweight_and_text_alignment():
    ents = [{"type": "TEXT", "layer": "T", "pos": (5.0, 6.0), "height": 0.5, "text": "abc", "align": "center"},
            {"type": "TEXT", "layer": "T", "pos": (5.0, 6.0), "height": 0.5, "text": "xyz"}]
    p = pairs(dxfout.build_dxf([], True, ents, {"T": (8, 9)}))
    i = [k for k, (c, v) in enumerate(p) if (c, v) == ("2", "T")][0]
    record = dict(p[i:i + 8])
    assert record["62"] == "8" and record["370"] == "9"
    texts = [k for k, (c, v) in enumerate(p) if (c, v) == ("0", "TEXT")]
    first, second = dict(p[texts[0]:texts[0] + 14]), dict(p[texts[1]:texts[1] + 14])
    assert first["72"] == "1" and first["11"] == "5.0" and "72" not in second


def test_dxf_non_ascii_text_becomes_an_escape_and_the_degree_code_is_kept():
    assert dxfout.text_value("FAÇADE 90%%d") == "FA\\U+00C7ADE 90%%d"
    text = dxfout.build_dxf([], True, [{"type": "TEXT", "layer": "T", "pos": (0, 0), "height": 1, "text": "FAÇADE"}])
    text.encode("ascii")
    assert "FA\\U+00C7ADE" in text


def test_dxf_without_entities_is_what_it_was_for_the_images():
    img = {"layer": "L", "file": "a.png", "size_px": (10, 10), "insert": (0.0, 0.0), "pixel": (1.0, 1.0)}
    text = dxfout.build_dxf([img])
    assert dxfout.build_dxf([img], True, None, None, None) == text
    assert "$EXTMIN" not in text and "TEXT" not in text and "LWPOLYLINE" not in text


def test_dxf_unknown_entity_is_refused():
    with pytest.raises(ValueError):
        dxfout.build_dxf([], True, [{"type": "CIRCLE", "layer": "L"}])


# -- sheetout: options, heights ----------------------------------------------------------------------

def test_options_defaults_and_dwg_implies_dxf():
    assert sheetout.normalise(None) == {"dxf": False, "dwg": False, "z": False, "vertical": None, "titles": "fr"}
    assert sheetout.normalise({"dwg": True})["dxf"] is True
    assert sheetout.normalise({"dxf": True, "vertical": "x", "titles": "EN"}) == {"dxf": True, "dwg": False, "z": False, "vertical": None, "titles": "en"}
    assert sheetout.normalise({"z": True, "vertical": "x", "titles": "de"})["vertical"] == "x"
    assert sheetout.normalise({"z": True, "titles": "de"})["titles"] == "fr"
    assert not sheetout.wanted({}) and sheetout.wanted({"dxf": True}) and sheetout.wanted({"dwg": True})


def test_missing_tools_are_named_with_a_reason():
    assert sheetout.tools_for_formats({"gm": "g", "acad": "a", "gdal": "d"}, "ecw", {"dwg": True}) == []
    missing = dict(sheetout.tools_for_formats({}, "ecw", {"dwg": True}))
    assert set(missing) == {"ecw", "dwg"} and "Global Mapper" in missing["ecw"] and "AutoCAD" in missing["dwg"]
    assert [k for k, _ in sheetout.tools_for_formats({}, "jp2", None)] == ["jp2"]
    assert sheetout.tools_for_formats({"gm": "g"}, "jp2", None) == []          # Global Mapper writes JPEG 2000 too
    assert sheetout.tools_for_formats({}, "png", {"dxf": True}) == []


def test_relative_heights_use_the_lowest_bottom_edge_as_the_common_zero():
    inputs = [sheetout.HeightInput("A", 61.8), sheetout.HeightInput("B", 63.9), sheetout.HeightInput("C", 60.8)]
    h = sheetout.sheet_heights(inputs, {"dxf": True})
    assert h["mode"] == "relative" and h["zero"] == 60.8
    assert h["y"] == pytest.approx({"A": 1.0, "B": 3.1, "C": 0.0})
    assert min(h["y"].values()) == 0.0


def test_altitude_mode_needs_a_height_system():
    with pytest.raises(sheetout.SheetError, match="Pick a height system"):
        sheetout.sheet_heights([sheetout.HeightInput("A", 100.0)], {"z": True})
    with pytest.raises(sheetout.SheetError):
        sheetout.sheet_heights([], {"dxf": True})


def test_ellipsoid_means_no_undulation():
    h = sheetout.sheet_heights([sheetout.HeightInput("A", 100.0, 1.0, 2.0)], {"z": True, "vertical": "ellipsoid"}, epsg=3942)
    assert h["y"] == {"A": 100.0} and h["mode"] == "altitude" and h["short"] == "ELL"
    assert "ellipsoidal" in h["note"] and "EPSG:3942" in h["note"]


@needs_proj
def test_altitude_is_the_ellipsoidal_height_minus_the_undulation_checked_against_proj(tmp_path):
    shutil.copy(os.path.join(DATA, "fr_ign_RAC23.tif"), str(tmp_path / "fr_ign_RAC23.tif"))
    engine = projtool.Proj(geoid_dir=str(tmp_path))
    inputs = [sheetout.HeightInput("A", 130.0, 2175840.0, 1206740.0), sheetout.HeightInput("B", 131.0, 2175900.0, 1206800.0)]
    h = sheetout.sheet_heights(inputs, {"z": True, "vertical": RAC23_ID}, epsg=3942, engine=engine, folder=str(tmp_path))
    n = h["undulation"]["A"]
    assert 40.0 < n < 55.0
    assert h["y"]["A"] == pytest.approx(130.0 - n) and h["y"]["B"] == pytest.approx(131.0 - h["undulation"]["B"])
    direct = -engine.vertical_shift("fr_ign_RAC23.tif", *engine.lonlat(3942, [2175840.0], [1206740.0]))
    assert n == pytest.approx(float(direct[0]), abs=1e-6)
    assert h["short"] and h["note"].endswith("(horizontal frame EPSG:3942)") and "fr_ign_RAC23.tif" in h["note"]


@needs_proj
def test_a_missing_or_foreign_grid_is_a_readable_error_and_nothing_else(tmp_path):
    engine = projtool.Proj(geoid_dir=str(tmp_path))
    corsica = [sheetout.HeightInput("A", 130.0, 2175840.0, 1206740.0)]
    with pytest.raises(sheetout.SheetError, match="not on this computer"):
        sheetout.sheet_heights(corsica, {"z": True, "vertical": RAC23_ID}, epsg=3942, engine=engine, folder=str(tmp_path))
    (tmp_path / "fr_ign_RAC23.tif").write_bytes(b"not the grid")
    with pytest.raises(sheetout.SheetError, match="differs from the official"):
        sheetout.sheet_heights(corsica, {"z": True, "vertical": RAC23_ID}, epsg=3942, engine=engine, folder=str(tmp_path))
    shutil.copy(os.path.join(DATA, "fr_ign_RAC23.tif"), str(tmp_path / "fr_ign_RAC23.tif"))
    paris = [sheetout.HeightInput("A", 130.0, 652000.0, 6862000.0)]
    with pytest.raises(sheetout.SheetError, match="does not cover"):
        sheetout.sheet_heights(paris, {"z": True, "vertical": RAC23_ID}, epsg=2154, engine=engine, folder=str(tmp_path))
    with pytest.raises(sheetout.SheetError, match="EPSG"):
        sheetout.sheet_heights(corsica, {"z": True, "vertical": RAC23_ID}, epsg=None, engine=engine, folder=str(tmp_path))
    with pytest.raises(sheetout.SheetError, match="Unknown"):
        sheetout.sheet_heights(corsica, {"z": True, "vertical": "no_such_grid"}, epsg=3942, engine=engine, folder=str(tmp_path))


def test_a_disagreement_between_proj_and_our_reader_stops_the_sheet(tmp_path):
    shutil.copy(os.path.join(DATA, "fr_ign_RAC23.tif"), str(tmp_path / "fr_ign_RAC23.tif"))

    class Engine(object):
        def available(self):
            return True

        def lonlat(self, epsg, xs, ys):
            return np.full(len(xs), 9.0), np.full(len(xs), 42.0)

        def vertical_shift(self, grid, lons, lats):
            return np.full(len(lons), -49.2143 + 0.05)          # 5 cm away from the grid

    with pytest.raises(sheetout.SheetError, match="disagree"):
        sheetout.undulations(RAC23_ID, 3942, [1.0], [2.0], Engine(), str(tmp_path))


def test_datum_names():
    spec = crs.vertical_by_id("fr_ign_RAC09")
    assert sheetout.datum_short(spec) == "NGF-IGN78"
    assert sheetout.datum_short(crs.vertical_by_id("ellipsoid")) == "ELL"
    assert sheetout.datum_note(spec, 3942) == "NGF-IGN78 / RAC09, geoid grid fr_ign_RAC09.tif (horizontal frame EPSG:3942)"


# -- sheetout: pictures and files --------------------------------------------------------------------

def test_a_picture_can_be_put_at_its_place_on_the_sheet(tmp_path):
    path = str(tmp_path / "a.tif")
    sheetout.write_geotiff_rgb(path, np.zeros((20, 40, 3), dtype=np.uint8), -1.0, 20.0, 0.05)
    info = geotiff.read_geotiff(path)
    assert (info["width"], info["height"], info["origin"], info["pixel"]) == (40, 20, (-1.0, 20.0), (0.05, 0.05))
    assert geotiff.set_origin(path, 123.5, 77.25)
    info = geotiff.read_geotiff(path)
    assert info["origin"] == (123.5, 77.25) and info["pixel"] == (0.05, 0.05)
    (tmp_path / "b.tif").write_bytes(b"not a tiff")
    assert geotiff.set_origin(str(tmp_path / "b.tif"), 1.0, 2.0) is False
    assert geotiff.set_origin(str(tmp_path / "absent.tif"), 1.0, 2.0) is False


def test_jp2_and_ecw_conversion_gets_a_copy_placed_on_the_sheet_and_leaves_the_original(tmp_path, monkeypatch):
    raw = str(tmp_path / "raw.tif")
    sheetout.write_geotiff_rgb(raw, np.zeros((10, 10, 3), dtype=np.uint8), 0.0, 10.0, 0.5)
    seen = {}

    def fake_gdal(gdal_dir, src, dst, quality=5, tick=None):
        seen["origin"] = geotiff.read_geotiff(src)["origin"]
        open(dst, "wb").write(b"jp2")
        return True, ""

    def fake_ecw(gm, src, dst, ratio=10, tick=None):
        seen["ecw_origin"] = geotiff.read_geotiff(src)["origin"]
        open(dst, "wb").write(b"ecw")
        return True, ""

    monkeypatch.setattr(external, "to_jp2_gdal", fake_gdal)
    monkeypatch.setattr(external, "to_ecw", fake_ecw)
    out = sheetout.convert_picture(raw, str(tmp_path / "sub" / "a.jp2"), "jp2", {"gdal": "G"}, origin=(100.0, 30.0))
    assert os.path.isfile(out) and seen["origin"] == (100.0, 30.0)
    assert geotiff.read_geotiff(raw)["origin"] == (0.0, 10.0)
    sheetout.convert_picture(raw, str(tmp_path / "a.ecw"), "ecw", {"gm": "M"}, origin=(5.0, 6.0))
    assert seen["ecw_origin"] == (5.0, 6.0)
    with pytest.raises(sheetout.SheetError, match="Global Mapper"):
        sheetout.convert_picture(raw, str(tmp_path / "b.ecw"), "ecw", {})
    with pytest.raises(sheetout.SheetError, match="encoder"):
        sheetout.convert_picture(raw, str(tmp_path / "b.jp2"), "jp2", {})


def entries_in(folder, pngs=True):
    out = []
    for letter, az, w, h in (("A", 180.0, 6.0, 3.0), ("B", 0.0, 7.0, 2.0)):
        path = sheetout.asset_path(folder, "SITE", "260101", letter, "png")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "wb").write(b"x")
        out.append({"letter": letter, "azimuth": az, "size_px": (int(w / 0.05), int(h / 0.05)), "pixel": 0.05, "image": path})
    return out


def test_the_sheet_files_use_relative_asset_paths_and_the_site_date_name(tmp_path, monkeypatch):
    entries = entries_in(str(tmp_path))
    heights = sheetout.sheet_heights([sheetout.HeightInput("A", 10.0), sheetout.HeightInput("B", 11.5)], {"dxf": True})
    result = sheetout.write_sheet(entries, str(tmp_path), "SITE", "260101", {"dxf": True}, heights, tools={})
    assert os.path.basename(result["dxf"]) == "SITE_260101_facades.dxf" and result["dwg"] is None and result["check"] is None
    text = open(result["dxf"], newline="").read()
    assert "00_Assets\\ortho\\SITE_260101_facade_A.png" in text and "00_Assets\\ortho\\SITE_260101_facade_B.png" in text
    assert str(tmp_path) not in text
    p = pairs(text)
    images = [i for i, (c, v) in enumerate(p) if (c, v) == ("0", "IMAGE")]
    assert len(images) == 2
    # B (north) first at X = 0, Y = 1.5 (relative to the lowest bottom edge: A = 0)
    first = dict(p[images[0]:images[0] + 30])
    assert first["10"] == "0.0" and first["20"] == "1.5"


def test_dwg_goes_through_autocad_and_a_missing_autocad_is_an_error_not_a_silent_skip(tmp_path, monkeypatch):
    entries = entries_in(str(tmp_path))
    heights = sheetout.sheet_heights([sheetout.HeightInput("A", 10.0), sheetout.HeightInput("B", 11.5)], {"dxf": True})
    with pytest.raises(sheetout.SheetError, match="AutoCAD"):
        sheetout.write_sheet(entries, str(tmp_path), "SITE", "260101", {"dwg": True}, heights, tools={})
    assert not [f for f in os.listdir(str(tmp_path)) if f.endswith((".dxf", ".dwg"))]          # nothing half written
    calls = []
    monkeypatch.setattr(external, "check_dxf", lambda core, dxf, tick=None: calls.append(("check", core)) or {"images": 2, "failed": [], "valid": True})

    def fake_dwg(core, dxf, dwg, tick=None, zoom_extents=False):
        calls.append(("dwg", zoom_extents))
        open(dwg, "wb").write(b"dwg")
        return True, ""

    monkeypatch.setattr(external, "dxf_to_dwg", fake_dwg)
    result = sheetout.write_sheet(entries, str(tmp_path), "SITE", "260101", {"dwg": True}, heights, tools={"acad": "A"})
    assert result["dwg"].endswith("SITE_260101_facades.dwg") and os.path.isfile(result["dwg"]) and os.path.isfile(result["dxf"])
    assert calls == [("check", "A"), ("dwg", True)] and result["warnings"] == []


def test_a_picture_autocad_cannot_load_is_a_warning(tmp_path, monkeypatch):
    entries = entries_in(str(tmp_path))
    heights = sheetout.sheet_heights([sheetout.HeightInput("A", 10.0), sheetout.HeightInput("B", 11.5)], {"dxf": True})
    monkeypatch.setattr(external, "check_dxf", lambda core, dxf, tick=None: {"images": 1, "failed": ["IMAGE_2"], "valid": True})
    result = sheetout.write_sheet(entries, str(tmp_path), "SITE", "260101", {"dxf": True}, heights, tools={"acad": "A"})
    assert result["warnings"] == ["AutoCAD loaded 1 of 2 pictures."]


def test_a_failed_dwg_conversion_is_reported(tmp_path, monkeypatch):
    entries = entries_in(str(tmp_path))
    heights = sheetout.sheet_heights([sheetout.HeightInput("A", 10.0), sheetout.HeightInput("B", 11.5)], {"dxf": True})
    monkeypatch.setattr(external, "check_dxf", lambda *a, **k: {"images": 2, "failed": [], "valid": True})
    monkeypatch.setattr(external, "dxf_to_dwg", lambda *a, **k: (False, "AutoCAD did not write the DWG"))
    with pytest.raises(sheetout.SheetError, match="did not write"):
        sheetout.write_sheet(entries, str(tmp_path), "SITE", "260101", {"dwg": True}, heights, tools={"acad": "A"})
