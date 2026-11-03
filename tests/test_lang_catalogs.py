"""Catalogues de langue : memes cles, memes champs, et toutes les cles utilisees existent."""

import ast
import os
import string

import pytest

from aerotools import config, i18n

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = os.path.join(ROOT, "aerotools")


def fields_of(text):
    """Noms des champs `{nom}` d'un texte."""
    return sorted(name for _lit, name, _spec, _conv in string.Formatter().parse(text)
                  if name is not None)


@pytest.fixture(params=i18n.codes())
def code(request):
    return request.param


def test_every_listed_language_has_a_catalog_module(code):
    assert i18n.texts(code), "le catalogue {} est vide ou illisible".format(code)
    assert i18n.formats(code)


def test_french_and_english_have_exactly_the_same_keys():
    fr, en = i18n.texts("fr"), i18n.texts("en")
    assert sorted(set(fr) - set(en)) == [], "cles absentes de en.py"
    assert sorted(set(en) - set(fr)) == [], "cles absentes de fr.py"


def test_every_language_has_the_keys_of_the_reference():
    reference = set(i18n.texts("fr"))
    for known in i18n.codes():
        assert set(i18n.texts(known)) == reference, known


def test_french_and_english_use_the_same_format_fields():
    fr, en = i18n.texts("fr"), i18n.texts("en")
    differences = {key: (fields_of(fr[key]), fields_of(en[key]))
                   for key in fr if key in en and fields_of(fr[key]) != fields_of(en[key])}
    assert differences == {}


def test_every_language_uses_the_same_format_fields_as_the_reference():
    reference = i18n.texts("fr")
    for known in i18n.codes():
        texts = i18n.texts(known)
        for key, text in texts.items():
            assert fields_of(text) == fields_of(reference[key]), (known, key)


def test_formats_have_the_same_keys_in_every_language():
    reference = set(i18n.formats("fr"))
    assert reference == {"decimal", "date", "csv_delimiter"}
    for known in i18n.codes():
        assert set(i18n.formats(known)) == reference, known


def test_texts_are_plain_non_empty_strings(code):
    for key, text in i18n.texts(code).items():
        assert isinstance(key, str) and key
        assert isinstance(text, str) and text.strip(), (code, key)
        assert text == text.strip() or key == "boot.about", (code, key)


def test_no_semicolon_in_written_texts(code):
    # Regle de style du projet : pas de point-virgule dans les textes rediges.
    for key, text in i18n.texts(code).items():
        assert ";" not in text, (code, key)


def test_no_non_breaking_space_in_texts(code):
    for key, text in i18n.texts(code).items():
        assert "\u00a0" not in text and "\u202f" not in text, (code, key)


def test_english_texts_are_not_copies_of_french_ones():
    # Un texte identique est permis (ID, Minimum, Metashape...), mais pas la
    # plupart : un catalogue anglais rempli de francais serait un oubli.
    fr, en = i18n.texts("fr"), i18n.texts("en")
    same = [key for key in fr if fr[key] == en[key]]
    assert len(same) <= len(fr) * 0.12, same


def test_tool_registry_texts_exist_in_every_language(code):
    for entry in config.TOOLS:
        assert entry["sous_titre"] in i18n.texts(code), (code, entry["id"])


# --------------------------------------------------------------------------
# Les cles utilisees dans le code existent
# --------------------------------------------------------------------------

def _python_files():
    for folder, dirs, files in os.walk(PACKAGE):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(folder, name)


def _literal_keys():
    """Cles passees en texte litteral a `i18n.t(...)` ou `t(...)` dans le code."""
    found = {}
    for path in _python_files():
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            named = (isinstance(func, ast.Attribute) and func.attr == "t"
                     and isinstance(func.value, ast.Name) and func.value.id == "i18n")
            if named and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, str):
                found.setdefault(node.args[0].value, []).append(os.path.relpath(path, ROOT))
    return found


def test_the_code_asks_for_keys_that_exist():
    keys = _literal_keys()
    assert len(keys) > 40
    missing = {key: where for key, where in keys.items() if key not in i18n.texts("fr")}
    assert missing == {}


def test_keys_stored_in_tables_exist():
    from aerotools.tools.fastgcp import panel

    for key, _sort, _width in panel.COLUMNS:
        assert key in i18n.texts("fr")
    for _kind, key in panel.EXPORTS:
        assert key in i18n.texts("fr")
    for kind in ("pdf", "csv"):
        assert "err." + kind in i18n.texts("fr")


def test_most_catalog_keys_are_used_somewhere():
    """Une cle jamais lue est probablement un reste : on en tolere quelques-unes
    (cles composees au moment de l'appel)."""
    source = ""
    for path in _python_files():
        if os.sep + "lang" + os.sep in path or path.endswith("i18n.py"):
            continue
        with open(path, encoding="utf-8") as handle:
            source += handle.read()
    unused = [key for key in i18n.texts("fr") if '"{}"'.format(key) not in source]
    assert len(unused) <= 6, unused
