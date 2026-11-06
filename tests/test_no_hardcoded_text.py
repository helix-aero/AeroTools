"""Guard: the text in the Python sources is plain English.

Every string literal of every module under aerotools/ (the vendored packages excluded, the
text catalog in lang/ included) is checked for accented letters, guillemets and common
words that are not English. Docstrings, log / journal lines and messages are covered like any other literal.

The only exceptions are the recognition word lists in ALLOWED_RECOGNITION_LISTS: words the code
looks for in real-world data (folder or file names, for example) are data, not prose. Each exception
names a file and the variable the list is assigned to, and a test checks that the entry still exists.

The detector itself is tested at the bottom, on small known snippets.
"""

import ast
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = os.path.join(ROOT, "aerotools")

# Folders that are not scanned: third-party code and bytecode caches.
EXCLUDED_DIRS = {"vendor", "__pycache__"}

# (path relative to the repository root, name of the variable the list is assigned to). The strings
# inside such an assignment are lists of words that recognise real-world data; they are not scanned.
ALLOWED_RECOGNITION_LISTS = {
    ("aerotools/tools/aeroexports/naming.py", "GENERIC_TOKENS"),
}

# Accented letters and guillemets.
ACCENTS = re.compile("[\u00e0\u00e2\u00e4\u00e7\u00e9\u00e8\u00ea\u00eb\u00ee\u00ef\u00f4\u00f6\u00f9\u00fb\u00fc\u00ff\u0153\u00e6"
                     "\u00c0\u00c2\u00c4\u00c7\u00c9\u00c8\u00ca\u00cb\u00ce\u00cf\u00d4\u00d6\u00d9\u00db\u00dc\u0178\u0152\u00c6\u00ab\u00bb]")

# Unaccented non-English words that are rare in code and in English text.
FOREIGN_WORDS = re.compile(
    r"\b(aucune?|avec|dans|pour|sont|sans(?!-serif)|cette|ces|leurs?|donc|mais|chaque|tous|toutes|"
    r"fichiers?|dossiers?|erreurs?|reglages?|valeurs?|nombre|lignes|jamais|deja|apres|puis|aussi|"
    r"comme|depuis|vers|lors|selon|sinon|alors|autres?|meme|seulement|encore|toujours|"
    r"premiere|derniere|ancienne?|nouveau|nouvelle|poste|outils?|langue|cles?|renvoie|appele|"
    r"champs|doit|peut|faut|pas|trop|tres|oui|ecrit|ecrire|ouvre|ouvrir|ferme|fermer|donne|"
    r"sert|etre|avoir|faire|vrai|rien|notre|votre|nos|vos|"
    r"exporter|exporte|marqueurs?|pointage|pointe|annuler|annule|enregistrer|enregistre|"
    r"disponible|indisponible|actif|inactif|relue|relecture|chargement|charger|"
    r"demarrage|demarre|fenetre|sans nom|a venir|rechercher)\b",
    re.IGNORECASE,
)

# Technical constants that look like text without being text (font names).
IGNORED_LITERALS = {"Segoe UI", "Helvetica Neue", "Arial", "DejaVu Sans"}

# Single characters are not text: they are transliteration or stripping tables (an "oe" ligature, a guillemet).
MIN_LITERAL_LENGTH = 2


def source_files():
    """Every .py file under aerotools/, vendored code excluded, as repository-relative paths."""
    found = []
    for folder, dirs, names in os.walk(PACKAGE):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_DIRS)
        for name in sorted(names):
            if name.endswith(".py"):
                found.append(os.path.relpath(os.path.join(folder, name), ROOT).replace(os.sep, "/"))
    return found


def _assigned_names(node):
    """Names a statement assigns to (`NAME = ...`, `NAME: type = ...`), or an empty list."""
    if isinstance(node, ast.Assign):
        return [t.id for t in node.targets if isinstance(t, ast.Name)]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id]
    return []


def foreign_literals(source, relpath="x.py"):
    """[(line, text)] of the string literals that are not plain English, sorted by line."""
    tree = ast.parse(source, relpath)
    allowed_names = {name for path, name in ALLOWED_RECOGNITION_LISTS if path == relpath}
    skipped = set()
    for node in ast.walk(tree):
        if allowed_names.intersection(_assigned_names(node)):
            for child in ast.walk(node):
                skipped.add(id(child))
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if id(node) in skipped or node.value in IGNORED_LITERALS or len(node.value) < MIN_LITERAL_LENGTH:
            continue
        if ACCENTS.search(node.value) or FOREIGN_WORDS.search(node.value):
            found.append((node.lineno, node.value))
    return sorted(found)


# --------------------------------------------------------------------------
# The sources
# --------------------------------------------------------------------------

SOURCES = source_files()


def test_the_scan_covers_the_whole_package_but_not_the_vendored_code():
    assert "aerotools/msapi.py" in SOURCES
    assert "aerotools/lang/en.py" in SOURCES
    assert "aerotools/tools/fastgcp/panel.py" in SOURCES
    assert not [p for p in SOURCES if p.startswith("aerotools/vendor/")]


@pytest.mark.parametrize("relpath", SOURCES)
def test_the_sources_hold_plain_english_text(relpath):
    with open(os.path.join(ROOT, *relpath.split("/")), encoding="utf-8") as handle:
        source = handle.read()
    found = foreign_literals(source, relpath)
    assert found == [], "text that is not plain English in string literals: {}".format(found)


@pytest.mark.parametrize("relpath, name", sorted(ALLOWED_RECOGNITION_LISTS))
def test_each_allowed_recognition_list_still_exists(relpath, name):
    with open(os.path.join(ROOT, *relpath.split("/")), encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), relpath)
    assert any(name in _assigned_names(node) for node in ast.walk(tree)), "{} no longer assigns {}".format(relpath, name)


# --------------------------------------------------------------------------
# The detector itself (the strings below are sample input for the detector)
# --------------------------------------------------------------------------

def test_detector_flags_accented_letters_guillemets_and_known_foreign_words():
    source = (
        'def f(panel):\n'
        '    panel.set_status("Rien \u00e0 annuler")\n'            # sample input: accented letter
        '    label.setText("Exporter")\n'                     # sample input: foreign word
        '    msg = "Chunk \u00ab {} \u00bb".format(name)\n'             # sample input: guillemets
        '    box.setToolTip("photos du marqueur")\n'          # sample input: foreign word
    )
    texts = [text for _line, text in foreign_literals(source)]
    assert texts == ["Rien \u00e0 annuler", "Exporter", "Chunk \u00ab {} \u00bb", "photos du marqueur"]


def test_detector_also_checks_docstrings_journal_lines_and_print():
    source = (
        '"""Module docstring avec \u00e9."""\n'                    # sample input
        'def f(state):\n'
        '    """Docstring avec des fichiers."""\n'            # sample input
        '    journal.write("Pointage ignor\u00e9 : {}".format(1))\n'  # sample input
        '    print("[AeroTools] charg\u00e9")\n'                   # sample input
    )
    assert [line for line, _text in foreign_literals(source)] == [1, 3, 4, 5]


def test_detector_checks_the_literal_parts_of_f_strings():
    source = 'def f(n):\n    return f"{n} fichiers"\n'     # sample input
    assert [line for line, _text in foreign_literals(source)] == [2]


def test_detector_leaves_english_text_and_neutral_constants_alone():
    source = (
        'def f(state):\n'
        '    """Open the file and write the error."""\n'
        '    journal.write("Update finished: {} file(s), non-finite value, plus the latest changes")\n'
        '    return {"x": 1, "kind": "pdf", "font": "Segoe UI", "dash": "\u2013", "impossible": 1}\n'
    )
    assert foreign_literals(source) == []


def test_detector_ignores_sans_serif_and_single_characters():
    source = (
        'FONT = "the sans-serif font"\n'
        'def f(text):\n'
        '    return text.replace("\u0153", "oe").replace("\u00ab", "")\n'
    )
    assert foreign_literals(source) == []


def test_detector_skips_a_listed_recognition_list_only(monkeypatch):
    source = (
        'STOP_WORDS = ("livrable", "dossier", "export")\n'
        'OTHER = ("dossier",)\n'
        'def f():\n'
        '    return STOP_WORDS\n'
    )
    assert [line for line, _text in foreign_literals(source, "listed.py")] == [1, 2]
    monkeypatch.setitem(globals(), "ALLOWED_RECOGNITION_LISTS", {("listed.py", "STOP_WORDS")})
    assert [line for line, _text in foreign_literals(source, "listed.py")] == [2]
    assert [line for line, _text in foreign_literals(source, "other.py")] == [1, 2]
