"""Every font shipped in assets/fonts comes with its licence text (the SIL Open Font License asks for it) and is named in assets/fonts/NOTICE.txt (a file every channel ships)."""

import glob
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONTS = os.path.join(ROOT, "assets", "fonts")
NOTICES = os.path.join(FONTS, "NOTICE.txt")
FONT_FILES = sorted(glob.glob(os.path.join(FONTS, "*.ttf")) + glob.glob(os.path.join(FONTS, "*.otf")))


def family_of(path):
    return re.split(r"[-_.]", os.path.basename(path))[0]


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


@pytest.mark.parametrize("font", FONT_FILES, ids=[os.path.basename(f) for f in FONT_FILES])
def test_each_font_has_its_licence_text(font):
    licence = os.path.join(FONTS, "OFL-%s.txt" % family_of(font))
    assert os.path.isfile(licence), "no licence text for " + os.path.basename(font)
    text = read(licence)
    assert text.startswith("Copyright ") and "SIL OPEN FONT LICENSE Version 1.1" in text
    assert "PERMISSION & CONDITIONS" in text and "DISCLAIMER" in text      # the whole text, not a pointer to it


@pytest.mark.parametrize("font", FONT_FILES, ids=[os.path.basename(f) for f in FONT_FILES])
def test_the_copyright_line_is_the_one_of_the_font_file(font):
    from_font = None
    try:
        from fontTools.ttLib import TTFont
    except ImportError:
        pytest.skip("fontTools not installed")
    for record in TTFont(font)["name"].names:
        if record.nameID == 0 and record.platformID == 3:
            from_font = record.toUnicode()
    first = read(os.path.join(FONTS, "OFL-%s.txt" % family_of(font))).splitlines()[0]
    assert first.strip() == from_font.strip()


def test_the_fonts_notice_names_each_font_and_its_licence_file():
    text = read(NOTICES)
    for font in FONT_FILES:
        assert family_of(font) in text and "OFL-%s.txt" % family_of(font) in text


# --- THIRD_PARTY.md: the page lists what this copy contains, no more and no less --------------------------------------

PAGE = os.path.join(ROOT, "THIRD_PARTY.md")
PROJ_BIN = os.path.join(ROOT, "aerotools", "vendor", "proj", "bin")
PROJ_LICENCES = os.path.join(ROOT, "aerotools", "vendor", "proj", "licenses")
HAS_PROJ = os.path.isdir(PROJ_BIN)
MODELS = sorted(glob.glob(os.path.join(ROOT, "models", "*")))


def test_the_page_exists_and_is_in_the_manifest_files():
    assert os.path.isfile(PAGE)
    sys.path.insert(0, os.path.join(ROOT, "install"))
    import make_manifest
    assert "THIRD_PARTY.md" in make_manifest.TOP_FILES


def test_the_page_names_the_fonts_the_icon_the_logos_and_the_models():
    text = read(PAGE)
    for font in FONT_FILES:
        assert family_of(font) in text
    assert "assets/fonts/NOTICE.txt" in text and "Octicons" in text and "assets/logo/" in text
    for model in MODELS:
        assert "models/" + os.path.basename(model) in text, "models/{} is not named".format(os.path.basename(model))
    assert "AGPL-3.0" in text and "Ultralytics" in text


def test_the_models_folder_has_its_own_notice_naming_each_model():
    notice = os.path.join(ROOT, "models", "NOTICE.txt")
    text = read(notice)
    for model in MODELS:
        name = os.path.basename(model)
        if name != "NOTICE.txt":
            assert name in text, name + " is not named in models/NOTICE.txt"
    assert "AGPL-3.0" in text and "Ultralytics" in text
    sys.path.insert(0, os.path.join(ROOT, "install"))
    import make_manifest
    assert make_manifest.wanted("models/NOTICE.txt")


def test_the_page_speaks_of_proj_only_where_proj_is_shipped():
    text = read(PAGE)
    assert ("PROJ" in text) == HAS_PROJ
    if HAS_PROJ:
        assert os.path.isfile(os.path.join(ROOT, "tests", "data", "fr_ign_RAC23.tif")) == ("fr_ign_RAC23" in text)


@pytest.mark.skipif(not HAS_PROJ, reason="the PROJ engine is not in this channel")
class TestProjLibraries:
    def test_each_program_and_library_is_named_in_the_page(self):
        text = read(PAGE)
        for name in sorted(os.listdir(PROJ_BIN)):
            assert "`{}`".format(name) in text, name + " is not listed in THIRD_PARTY.md"

    def test_each_licence_file_is_linked_from_the_page_and_each_link_exists(self):
        text = read(PAGE)
        linked = set(re.findall(r"\(aerotools/vendor/proj/licenses/([^)]+)\)", text))
        on_disk = set(os.listdir(PROJ_LICENCES))
        assert linked == on_disk, sorted(linked ^ on_disk)

    def test_the_licence_texts_are_whole_texts(self):
        for name in sorted(os.listdir(PROJ_LICENCES)):
            body = read(os.path.join(PROJ_LICENCES, name))
            assert len(body.strip()) > 80, name
        assert "Apache License" in read(os.path.join(PROJ_LICENCES, "OpenSSL.txt"))
        assert "Apache License" in read(os.path.join(PROJ_LICENCES, "lerc.txt"))
        assert "Permission is hereby granted" in read(os.path.join(PROJ_LICENCES, "PROJ.txt"))
        assert "Permission is hereby granted" in read(os.path.join(PROJ_LICENCES, "brotli.txt"))
        assert "Redistribution and use" in read(os.path.join(PROJ_LICENCES, "libwebp.txt"))

    def test_the_versions_stated_for_the_files_that_say_theirs(self):
        text = read(PAGE)
        for dll, marker in (("libcrypto-3-x64.dll", b"3.0.20"), ("libcurl.dll", b"8.20.0"), ("tiff.dll", b"4.7.1"),
                            ("zlib.dll", b"1.3.2"), ("zstd.dll", b"1.5.7"), ("proj_9.dll", b"PROJ 9.8.1")):
            with open(os.path.join(PROJ_BIN, dll), "rb") as handle:
                assert marker in handle.read(), "{} is not the version the page states".format(dll)
            assert marker.decode().replace("PROJ ", "") in text
