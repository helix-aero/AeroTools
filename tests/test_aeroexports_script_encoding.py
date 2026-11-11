"""Aero-Exports: the scripts given to Global Mapper and AutoCAD keep accented folder names (Etang, Saint-Etienne...)."""

import os

import pytest

from aerotools.tools.aeroexports import dxfout, external

ACCENTED = "Étang de Biguglia ôç"


class Capture(object):
    """Stands in for subprocess.Popen: keeps the bytes of the script it was given, then 'runs' for a moment."""

    def __init__(self, sink):
        self.sink = sink

    def __call__(self, command, **kwargs):
        script = command[command.index("/s") + 1] if "/s" in command else command[1]
        self.sink.append(open(script, "rb").read())
        return type("Done", (), {"poll": lambda self: 0, "returncode": 0, "kill": lambda self: None})()


def test_the_global_mapper_script_is_utf8_with_the_accents(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(external.subprocess, "Popen", Capture(seen))
    src = str(tmp_path / ACCENTED / "in.tif")
    dst = str(tmp_path / ACCENTED / "out.ecw")
    external._run_gm("GM", external.gm_script(src, dst), dst)
    text = seen[0].decode("utf-8")                              # a strict decode: it IS UTF-8
    assert "?" not in text
    assert text.count(ACCENTED) == 2 and "\\" in text           # both paths, backslashes as before


def test_the_autocad_script_keeps_the_accents_in_the_windows_code_page(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(external.subprocess, "Popen", Capture(seen))
    dwg = str(tmp_path / ACCENTED / "TOPO.dwg")
    external._run_core("CORE", str(tmp_path / "TOPO.dxf"), external.acad_script_dwg(dwg))
    codec = external.acad_script_encoding(external.acad_script_dwg(dwg))
    assert codec in ("mbcs", "cp1252")                          # these accents are in the ANSI page: no BOM, as AutoCAD always read
    assert ACCENTED in seen[0].decode(codec) and not seen[0].startswith(b"\xef\xbb\xbf")


def test_a_path_outside_the_ansi_page_goes_out_as_utf8_with_a_bom():
    text = external.acad_script_dwg("C:/Données/Ж中/TOPO.dwg")          # Cyrillic and Chinese letters: in no Western page
    assert external.acad_script_encoding(text) == "utf-8-sig"
    assert text.encode("utf-8-sig").decode("utf-8-sig") == text


def test_a_plain_ascii_script_is_written_exactly_as_before():
    text = external.acad_script_check()
    assert text.encode(external.acad_script_encoding(text)) == text.encode("ascii")
    assert external.GM_SCRIPT_ENCODING == "utf-8" and "x".encode(external.GM_SCRIPT_ENCODING) == b"x"


# -- the real programs, on an accented folder (slow, needs them installed: AEROTOOLS_LIVE_TOOLS=1) --------------

live = pytest.mark.skipif(not os.environ.get("AEROTOOLS_LIVE_TOOLS"), reason="set AEROTOOLS_LIVE_TOOLS=1 to run the real tools")


@live
def test_live_autocad_saves_a_dwg_in_an_accented_folder(tmp_path):
    core = external.find_autocad("")
    if not core:
        pytest.skip("AutoCAD not installed")
    folder = tmp_path / ACCENTED
    folder.mkdir()
    dxf = dxfout.write_dxf(str(folder / "TOPO.dxf"), [])
    ok, message = external.dxf_to_dwg(core, dxf, str(folder / "TOPO.dwg"))
    assert ok, message


@live
def test_live_global_mapper_writes_in_an_accented_folder(tmp_path):
    import subprocess
    gm, gdal = external.find_global_mapper(""), external.find_gdal("")
    if not (gm and gdal):
        pytest.skip("Global Mapper or GDAL not installed")
    folder = tmp_path / ACCENTED
    folder.mkdir()
    grid = folder / "g.asc"                              # a small real raster: Global Mapper refuses the minimal test TIFFs
    header = ["ncols 20", "nrows 10", "xllcorner 900000", "yllcorner 6500000", "cellsize 1", "NODATA_value -9999"]
    rows = [" ".join(str((r * 7 + c * 3) % 255) for c in range(20)) for r in range(10)]
    grid.write_text(chr(10).join(header + rows) + chr(10))
    src = str(folder / "in.tif")
    subprocess.run([os.path.join(gdal, "gdal_translate.exe"), "-q", "-a_srs", "EPSG:2154", "-ot", "Byte", str(grid), src],
                   check=True, env=external.gdal_env(gdal))
    ok, message = external.to_ecw(gm, src, str(folder / "out.ecw"))
    assert ok, message
