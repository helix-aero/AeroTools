"""Tests de naming.py : pytest tests/test_aeroexports_naming.py"""
import datetime
import os

import pytest

from aerotools.tools.aeroexports import naming


def test_build_name_ortho_mnt_fr_en():
    d = datetime.date(2026, 7, 4)
    assert naming.build_name("CITADELLE", d, 0.03, "ortho", "tif") == "CITADELLE_260704_3cm_ortho.tif"
    assert naming.build_name("CITADELLE", d, 0.05, "dem", ".TIF") == "CITADELLE_260704_5cm_mnt.tif"
    assert naming.build_name("CITADELLE", d, 0.05, "dem", "tif", lang="en") == "CITADELLE_260704_5cm_dtm.tif"


def test_kind_always_last_even_with_version_and_tile():
    d = datetime.date(2026, 7, 4)
    n = naming.build_name("EDF", d, 0.03, "ortho", "ecw", version=2, tile="R02C05")
    assert n == "EDF_260704_3cm_v2_R02C05_ortho.ecw"
    n = naming.build_name("EDF", d, 0.03, "dem", "tif", version=2, tile="R02C05", lang="en")
    assert n.endswith("_dtm.tif")


def test_ortho_and_dem_tiles_pair_by_prefix():
    d = datetime.date(2026, 7, 4)
    a = naming.build_name("EDF", d, 0.03, "ortho", "tif", tile="E2179_N1208")
    b = naming.build_name("EDF", d, 0.03, "dem", "tif", tile="E2179_N1208")
    assert a.replace("_ortho.tif", "") == b.replace("_mnt.tif", "")


@pytest.mark.parametrize("pixel,expected", [
    (0.03, "3cm"), (0.030000001, "3cm"), (0.05, "5cm"), (0.1, "10cm"), (0.5, "50cm"), (1.0, "1m"), (2.0, "2m"),
    (0.008, "8mm"), (0.0312871, "31mm"), (0.0067924, "7mm"), (0.0025, "3mm"), (0.0004, "400um"),
])
def test_format_resolution(pixel, expected):
    assert naming.format_resolution(pixel) == expected


def test_format_resolution_invalid():
    with pytest.raises(ValueError):
        naming.format_resolution(0)


def test_date_styles():
    d = datetime.date(2026, 7, 4)
    assert naming.format_date(d) == "260704"
    assert naming.format_date(d, "long") == "20260704"
    assert naming.format_date(d, "iso") == "2026-07-04"


def test_flight_date_mode_and_fallback():
    exif = ["2026:07:04 09:00:00"] * 50 + ["2026:07:05 00:10:00"] * 3 + ["1970:01:01 00:00:00", "", None, "n'importe quoi"]
    assert naming.flight_date(exif) == (datetime.date(2026, 7, 4), "exif")
    today = datetime.date(2026, 11, 5)
    assert naming.flight_date([], today=today) == (today, "today")
    assert naming.flight_date(["garbage"], today=today)[1] == "today"


def test_flight_date_tie_takes_earliest():
    exif = ["2026:07:04 10:00:00", "2026:07:05 10:00:00"]
    assert naming.flight_date(exif)[0] == datetime.date(2026, 7, 4)


def test_sanitize_windows_rules():
    assert naming.sanitize_token("Zone nord / Pôle \"A\"?") == "Zone_nord_Pole_A"
    assert naming.sanitize_token("CON") == "CON_"
    assert naming.sanitize_token("  ..site.. ") == "site"
    assert naming.sanitize_token("A" * 40, max_len=10) == "A" * 10
    for bad in ["a<b", "a:b", "x.", "x ", "NUL.tif", "", "a|b"]:
        assert not naming.is_valid_windows_name(bad)
    assert naming.is_valid_windows_name("CITADELLE_260704_3cm_ortho.tif")


def test_build_name_never_produces_forbidden_chars():
    n = naming.build_name('A/B\\C:D*E?F"G<H>I|J', datetime.date(2026, 1, 2), 0.02, "ortho", "tif")
    assert naming.is_valid_windows_name(n)


@pytest.mark.parametrize("psx,expected", [
    (r"F:\1_Photogrammetry\Pompeani\Citadelle\Metashape\CITADELLE_Pompeani.psx", "CITADELLE"),
    (r"F:\Bouygues\EDF\2026-07-04.psx", "EDF"),
    (r"F:\Clients\Munster\Metashape\projet.psx", "Munster"),
    (r"F:\x\Metashape\Cavica Gamvert final.psx", "Cavica_Gamvert"),
    (r"F:\Metashape\Outputs\260704.psx", "SITE"),
])
def test_guess_site(psx, expected):
    assert naming.guess_site(psx) == expected


def test_guess_site_max_len():
    assert len(naming.guess_site(r"C:\a\Un_tres_long_nom_de_chantier_vraiment_long.psx", max_len=12)) <= 12


def test_next_free_version_shared_by_ortho_and_dem():
    d = datetime.date(2026, 7, 4)

    def planned(v):
        return [naming.build_name("EDF", d, 0.03, "ortho", e, version=v) for e in ("tif", "tfw", "prj")] + \
               [naming.build_name("EDF", d, 0.03, "dem", "tif", version=v)]

    assert naming.next_free_version("x", planned, listing=[]) == 1
    assert naming.next_free_version("x", planned, listing=["EDF_260704_3cm_mnt.tif"]) == 2
    present = ["EDF_260704_3cm_ortho.tif", "EDF_260704_3cm_v2_mnt.tif", "autre.txt"]
    assert naming.next_free_version("x", planned, listing=present) == 3
    assert naming.next_free_version("x", planned, listing=["EDF_260704_3CM_MNT.TIF"]) == 2     # casse Windows


def test_check_path_length(tmp_path):
    long_name = "a" * 250 + ".tif"
    assert naming.check_path_length(str(tmp_path), [long_name, "ok.tif"]) == [os.path.join(str(tmp_path), long_name)]


def test_outputs_folder_next_to_psx():
    assert naming.outputs_folder(r"C:\p\site\a.psx") == os.path.join(os.path.abspath(r"C:\p\site"), "Outputs")


def test_stem_of_handles_double_extension():
    assert naming.stem_of("a_ortho.tif.aux.xml") == "a_ortho"
    assert naming.stem_of("a_ortho.tif") == "a_ortho"
