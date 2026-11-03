"""Aucun texte visible ne doit rester en dur dans le code de l'interface.

Heuristique : on parcourt les litteraux de chaine des fichiers qui parlent a
l'utilisateur (panneau, rapport, menu, outil, annulation, photos) et on signale
ceux qui contiennent une lettre accentuee, des guillemets francais ou un mot
francais courant. Sont ignores :

  - les docstrings,
  - les arguments de `journal.write(...)`, de `print(...)` et de `_refuse(...)`
    (qui ne fait qu'ecrire au journal) : le journal technique et la console
    restent en francais, par choix (diagnostic interne),
  - les fonctions de la liste ALLOWED_FUNCTIONS, qui ne nourrissent que le journal,
  - les cles de dictionnaire (`d["erreur"]`, `d.get("erreur")`, `{"erreur": 1}`) :
    ce sont des noms de donnees, pas du texte affiche.

Le detecteur est lui-meme teste plus bas, sur des morceaux de code connus.
"""

import ast
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FASTGCP = os.path.join(ROOT, "aerotools", "tools", "fastgcp")

FILES = [
    os.path.join(FASTGCP, "panel.py"),
    os.path.join(FASTGCP, "report.py"),
    os.path.join(FASTGCP, "tool.py"),
    os.path.join(FASTGCP, "history.py"),
    os.path.join(FASTGCP, "marking.py"),
    os.path.join(FASTGCP, "photos.py"),
    os.path.join(ROOT, "aerotools", "bootstrap.py"),
]

# (nom de fichier, fonction) dont les textes ne vont que dans le journal technique.
ALLOWED_FUNCTIONS = {
    ("photos.py", "explain_diagnostic"),
}

# Lettres accentuees et guillemets francais.
ACCENTS = re.compile("[àâäçéèêëîïôöùûüÿœæÀÂÄÇÉÈÊËÎÏÔÖÙÛÜŸŒÆ«»]")

# Mots francais sans accent, rares dans du code ou de l'anglais.
FRENCH_WORDS = re.compile(
    r"\b(rapport|erreurs?|aucun|aucune|exporter|exporte|tableau|rechercher|"
    r"enregistrer|enregistre|marqueurs?|outil|pointage|pointe|annuler|annule|ouvrir|"
    r"fermer|fichier|dossier|impossible|indisponible|disponible|actif|inactif|"
    r"relue|relecture|chargement|charger|demarrage|demarre|fenetre|sans nom|"
    r"a venir|reglages?)\b",
    re.IGNORECASE,
)

# Constantes techniques qui ressemblent a du texte sans en etre.
IGNORED_LITERALS = {"Segoe UI", "Helvetica Neue", "Arial", "DejaVu Sans"}


# Fonctions dont les arguments ne vont que dans le journal technique ou la console.
JOURNAL_ONLY_CALLS = ("print", "_refuse")


def _is_journal_or_print(node):
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name) and func.id in JOURNAL_ONLY_CALLS:
        return True
    return (isinstance(func, ast.Attribute) and func.attr == "write"
            and isinstance(func.value, ast.Name) and func.value.id == "journal")


def _docstring_nodes(tree):
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docs.add(id(body[0].value))
    return docs


def suspicious_literals(source, filename="x.py"):
    """[(ligne, texte)] des litteraux qui ressemblent a du francais visible."""
    tree = ast.parse(source, filename)
    docs = _docstring_nodes(tree)
    base = os.path.basename(filename)
    skipped = set()

    for node in ast.walk(tree):
        if _is_journal_or_print(node):
            for child in ast.walk(node):
                skipped.add(id(child))
        # Cles de dictionnaire : noms de donnees.
        if isinstance(node, ast.Subscript):
            skipped.add(id(node.slice))
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if key is not None:
                    skipped.add(id(key))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)                 and node.func.attr in ("get", "setdefault", "pop") and node.args:
            skipped.add(id(node.args[0]))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and (base, node.name) in ALLOWED_FUNCTIONS:
            for child in ast.walk(node):
                skipped.add(id(child))

    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if id(node) in docs or id(node) in skipped:
            continue
        text = node.value
        if text in IGNORED_LITERALS:
            continue
        if ACCENTS.search(text) or FRENCH_WORDS.search(text):
            found.append((node.lineno, text))
    return sorted(found)


# --------------------------------------------------------------------------
# Le code de l'interface
# --------------------------------------------------------------------------

@pytest.mark.parametrize("path", FILES, ids=[os.path.basename(p) for p in FILES])
def test_no_visible_french_text_left_in_the_code(path):
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    found = suspicious_literals(source, path)
    assert found == [], "texte visible en dur (a passer par i18n.t) : {}".format(found)


# --------------------------------------------------------------------------
# Le detecteur lui-meme
# --------------------------------------------------------------------------

def test_detector_flags_accented_and_known_french_literals():
    source = (
        'def f(panel):\n'
        '    panel.set_status("Rien à annuler")\n'
        '    label.setText("Exporter")\n'
        '    msg = "Chunk « {} »".format(name)\n'
        '    box.setToolTip("photos du marqueur")\n'
    )
    texts = [text for _line, text in suspicious_literals(source)]
    assert texts == ["Rien à annuler", "Exporter", "Chunk « {} »", "photos du marqueur"]


def test_detector_ignores_journal_print_docstrings_and_neutral_text():
    source = (
        '"""Module : texte accentué permis ici."""\n'
        'def f(state):\n'
        '    """Docstring avec é."""\n'
        '    journal.write("Pointage ignoré : {}".format(1))\n'
        '    print("[AeroTools] chargé")\n'
        '    return {"x": 1, "kind": "pdf", "font": "Segoe UI", "dash": "–"}\n'
    )
    assert suspicious_literals(source) == []


def test_detector_keeps_looking_after_a_journal_call():
    source = (
        'def f():\n'
        '    journal.write("Export impossible")\n'
        '    status("Export impossible")\n'
    )
    assert [line for line, _t in suspicious_literals(source)] == [3]


def test_detector_allows_the_listed_journal_only_functions():
    source = (
        'def explain_diagnostic(diag):\n'
        '    return "aucune photo alignee dans ce chunk"\n'
        'def other():\n'
        '    return "aucune photo alignee"\n'
    )
    found = suspicious_literals(source, "photos.py")
    assert [line for line, _t in found] == [4]
