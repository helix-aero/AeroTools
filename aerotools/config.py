"""Chemins, registre des outils et reglages persistants d'AeroTools.

Les reglages sont ranges par poste (LOCALAPPDATA) et non sur le serveur :
chaque PC garde ses propres cases cochees, le code reste commun.
"""

import json
import os

APP_NAME = "AeroTools"
VENDOR = "HELIX AERO"

# Racine du depot : le dossier qui contient le paquet « aerotools ».
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ASSETS = os.path.join(ROOT, "assets")
FONTS = os.path.join(ASSETS, "fonts")

# Registre des outils du menu Aero.
#   id        : identifiant technique, sert aussi de nom de sous-paquet
#   menu      : libelle du menu Metashape (ASCII : l'API passe par Qt en latin-1
#               sur certaines versions, les accents y sont risques). Pour un outil
#               pas pret, bootstrap y ajoute (a venir) dans la langue du poste.
#   titre     : nom de l'outil (le meme dans toutes les langues)
#   sous_titre: cle de texte (aerotools/lang/*.py) de la description de l'outil
#   raccourci : raccourci clavier Metashape, ou None
#   pret      : False => l'entree de menu affiche « a venir » au lieu de charger
TOOLS = [
    {
        "id": "fastgcp",
        "menu": "FastGCP",
        "titre": "FastGCP",
        "sous_titre": "tool.fastgcp.subtitle",
        "raccourci": "F8",
        "pret": True,
    },
    # La detection IA (ex-AIGCP) est dans FastGCP : plus d'entree de menu a part.
]


def tool(tool_id):
    """Fiche d'un outil du registre, ou None."""
    for entry in TOOLS:
        if entry["id"] == tool_id:
            return entry
    return None


# --------------------------------------------------------------------------
# Reglages par poste
# --------------------------------------------------------------------------

def settings_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "HELIX", APP_NAME)


def settings_path():
    return os.path.join(settings_dir(), "settings.json")


def load_settings():
    """Reglages du poste. Jamais d'exception : un fichier casse = reglages vides."""
    try:
        with open(settings_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_settings(data):
    """Ecrit les reglages. Renvoie True si l'ecriture a abouti."""
    try:
        os.makedirs(settings_dir(), exist_ok=True)
        tmp = settings_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
        os.replace(tmp, settings_path())
        return True
    except Exception:
        return False


def section(name):
    """Section de reglages sous forme de dict. Une section corrompue = vide."""
    value = load_settings().get(name, {})
    return value if isinstance(value, dict) else {}


def get(section_name, key, default=None):
    return section(section_name).get(key, default)


def put(section_name, key, value):
    data = load_settings()
    current = data.get(section_name)
    if not isinstance(current, dict):
        current = {}
        data[section_name] = current
    current[key] = value
    return save_settings(data)
