"""Langue de l'interface et des rapports d'AeroTools.

Aucune dependance Qt : ce module sert aussi bien au panneau qu'au rapport, au
menu et aux tests. La langue choisie est gardee par poste (reglage `ui.langue`).

Principe
--------
  - `LANGUES` liste les langues proposees, dans l'ordre d'affichage. Chaque
    langue a un catalogue `aerotools/lang/<code>.py` (voir fr.py) qui definit
    `TEXTES` (cle -> texte) et `FORMATS` (decimale, date, delimiteur CSV).
  - Les catalogues sont charges a la demande, au premier besoin.
  - `t(cle, **champs)` ne leve jamais : texte absent de la langue courante =
    texte francais, puis la cle elle-meme ; champ manquant = laisse tel quel.
  - Ajouter une langue : un fichier `lang/<code>.py` et une ligne dans LANGUES.

Les appels a `t()` se font au moment d'afficher, jamais a l'import : la langue
peut changer pendant que Metashape tourne (`set_lang` previent les abonnes).
"""

import datetime
import importlib
import locale
import math
import warnings

from . import config

# (code, nom dans la langue elle-meme). L'ordre est celui du selecteur.
LANGUES = [
    ("fr", "Français"),
    ("en", "English"),
]

# Langue de repli quand un texte manque, et langue par defaut du systeme francais.
REPLI = "fr"
# Langue prise quand ni le reglage ni le systeme ne designent une langue connue.
DEFAUT = "en"

SECTION = "ui"
CLE = "langue"

_current = None
_subscribers = []
_catalogs = {}


# --------------------------------------------------------------------------
# Codes de langue
# --------------------------------------------------------------------------

def codes():
    """Codes des langues proposees, dans l'ordre du selecteur."""
    return [code for code, _name in LANGUES]


def native_name(code):
    """Nom d'une langue dans cette langue (par exemple English), ou le code."""
    for known, name in LANGUES:
        if known == code:
            return name
    return str(code)


def _clean(value):
    """Code connu depuis "fr", "FR", "fr_FR", "fr-CA", ou None."""
    try:
        text = str(value or "").strip().lower().replace("-", "_")
    except Exception:
        return None
    head = text.split("_", 1)[0]
    return head if head in codes() else None


def _raw_prefix(value):
    """Code court d'une langue quelconque ("es_ES" -> "es").

    Un nom de langue complet ("Francais", "English") est reconnu aussi : on
    ne sait pas sous quelle forme Metashape donne sa langue.
    """
    try:
        text = str(value or "").strip().lower().replace("-", "_")
    except Exception:
        return ""
    for code, name in LANGUES:
        if text == name.lower():
            return code
    return text.split("_", 1)[0]


# --------------------------------------------------------------------------
# Langue courante
# --------------------------------------------------------------------------

def _metashape_language():
    """Langue de l'interface de Metashape, ou None si l'API ne la donne pas."""
    import Metashape  # present seulement dans Metashape

    settings = Metashape.app.settings
    value = getattr(settings, "language", None)
    if value:
        return value
    reader = getattr(settings, "value", None)
    if callable(reader):
        return reader("main/language")
    return None


def _system_language():
    """Langue du systeme (Windows : langue d'affichage de l'utilisateur)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        found = locale.getdefaultlocale()[0]
    return found


def detect_default():
    """Langue au premier lancement : Metashape, puis le systeme.

    Une langue prise en charge est reprise telle quelle (le francais donne le
    francais). Toute autre langue, ou aucune information, donne l'anglais.
    """
    for probe in (_metashape_language, _system_language):
        try:
            prefix = _raw_prefix(probe())
        except Exception:
            prefix = ""
        if not prefix:
            continue
        return prefix if prefix in codes() else DEFAUT
    return DEFAUT


def _initial():
    """Reglage du poste s'il existe et est connu, sinon langue detectee."""
    try:
        saved = _clean(config.get(SECTION, CLE))
    except Exception:
        saved = None
    return saved or detect_default()


def lang():
    """Code de la langue courante (lue une fois, puis gardee)."""
    global _current
    if _current is None:
        _current = _initial()
    return _current


def set_lang(code, persist=True):
    """Change la langue, l'enregistre pour ce poste et previent les abonnes.

    Renvoie False (sans rien changer) si `code` n'est pas une langue connue.
    Les abonnes ne sont prevenus que si la langue change vraiment.
    """
    global _current
    clean = _clean(code)
    if clean is None:
        return False
    changed = clean != lang()
    _current = clean
    if persist:
        try:
            config.put(SECTION, CLE, clean)
        except Exception:
            pass
    if changed:
        _notify()
    return True


def reset():
    """Oublie la langue gardee : la prochaine lecture repart du reglage. Pour les tests."""
    global _current
    _current = None


# --------------------------------------------------------------------------
# Abonnes
# --------------------------------------------------------------------------

def subscribe(callback):
    """`callback()` est appele apres chaque changement de langue."""
    if callback not in _subscribers:
        _subscribers.append(callback)


def unsubscribe(callback):
    if callback in _subscribers:
        _subscribers.remove(callback)


def _notify():
    for callback in list(_subscribers):
        try:
            callback()
        except RuntimeError:
            # Objet Qt deja detruit : plus personne a prevenir.
            unsubscribe(callback)
        except Exception as exc:
            _report(exc)


def _report(exc):
    try:
        from . import journal
        journal.write("langue : abonne en erreur : {!r}".format(exc))
    except Exception:
        pass


# --------------------------------------------------------------------------
# Catalogues
# --------------------------------------------------------------------------

def _load(code):
    """(textes, formats) d'une langue connue. Vides si le catalogue est illisible."""
    cached = _catalogs.get(code)
    if cached is not None:
        return cached
    texts, formats = {}, {}
    if code in codes():
        try:
            module = importlib.import_module("." + code, __package__ + ".lang")
            texts = dict(getattr(module, "TEXTES", {}) or {})
            formats = dict(getattr(module, "FORMATS", {}) or {})
        except Exception as exc:
            _report(exc)
    _catalogs[code] = (texts, formats)
    return _catalogs[code]


def texts(code):
    """Dictionnaire cle -> texte d'une langue (copie)."""
    return dict(_load(code)[0])


def formats(code):
    """Formats propres a une langue (copie)."""
    return dict(_load(code)[1])


class _Open(dict):
    """Champs de formatage : un champ absent reste ecrit "{nom}"."""

    def __missing__(self, name):
        return "{" + str(name) + "}"


def _format(text, fields):
    if not fields:
        return text
    try:
        return text.format_map(_Open(fields))
    except Exception:
        return text


def t(key, **fields):
    """Texte de `key` dans la langue courante. Ne leve jamais.

    Ordre de repli : langue courante, francais, la cle elle-meme.
    `fields` remplit les champs `{nom}` du texte ; un champ non fourni reste tel quel.
    """
    code = lang()
    text = _load(code)[0].get(key)
    if text is None and code != REPLI:
        text = _load(REPLI)[0].get(key)
    if text is None:
        text = str(key)
    return _format(text, fields)


def has(key, code=None):
    """True si `key` existe dans le catalogue de `code` (langue courante par defaut)."""
    return key in _load(code or lang())[0]


# --------------------------------------------------------------------------
# Formats
# --------------------------------------------------------------------------

def _fmt(name):
    """Valeur d'un format dans la langue courante, puis en francais."""
    code = lang()
    value = _load(code)[1].get(name)
    if value is None and code != REPLI:
        value = _load(REPLI)[1].get(name)
    return value


def decimal_separator():
    return _fmt("decimal") or "."


def csv_delimiter():
    """Delimiteur des fichiers CSV : point-virgule en francais (Excel), virgule en anglais."""
    return _fmt("csv_delimiter") or ","


def fmt_number(value, decimals=3):
    """Nombre avec `decimals` decimales, sans separateur de milliers.

    Virgule decimale en francais, point en anglais. Un moins devant un zero
    (-0.000) est retire. Valeur absente, illisible, NaN ou infinie : chaine vide.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if math.isnan(number) or math.isinf(number):
        return ""
    try:
        places = max(0, int(decimals))
    except (TypeError, ValueError):
        places = 3
    text = "{:.{d}f}".format(number, d=places)
    if text.startswith("-") and float(text) == 0.0:
        text = text[1:]
    separator = decimal_separator()
    return text if separator == "." else text.replace(".", separator)


def fmt_date(value):
    """Date selon la langue : JJ/MM/AAAA en francais, AAAA-MM-JJ en anglais.

    Accepte une date ou une date et heure ; autre chose est rendu tel quel.
    """
    pattern = _fmt("date") or "%Y-%m-%d"
    if isinstance(value, (datetime.date, datetime.datetime)):
        try:
            return value.strftime(pattern)
        except Exception:
            pass
    return str(value)
