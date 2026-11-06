"""Journal d'AeroTools : tampon circulaire + abonnes.

Le journal sert aux deux sorties : la console de Metashape (print) et la zone
depliable du panneau. Les panneaux s'abonnent, se desabonnent a la fermeture,
et une exception chez un abonne n'empeche jamais d'ecrire la ligne.
"""

import os
import time

MAX_LINES = 300
LOG_NAME = "aerotools.log"
LOG_MAX_BYTES = 1 << 20

_lines = []
_subscribers = []


def write(message, tag="AeroTools"):
    """Ajoute une ligne horodatee au journal et la pousse aux abonnes."""
    line = "{} {}".format(time.strftime("%H:%M:%S"), message)
    _lines.append(line)
    if len(_lines) > MAX_LINES:
        del _lines[: len(_lines) - MAX_LINES]
    print("[{}] {}".format(tag, message))
    _to_file("{} [{}] {}".format(time.strftime("%Y-%m-%d %H:%M:%S"), tag, message))
    for callback in list(_subscribers):
        try:
            callback(line)
        except Exception:
            unsubscribe(callback)
    return line


def log_path():
    """aerotools.log, a cote des reglages : la meme trace que la console, lisible apres coup (support, diagnostic)."""
    from . import config
    return os.path.join(config.settings_dir(), LOG_NAME)


def _to_file(text):
    """Ajoute une ligne au fichier ; jamais d'erreur. Au-dela de 1 Mo, l'ancien fichier devient .old."""
    try:
        path = log_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.isfile(path) and os.path.getsize(path) > LOG_MAX_BYTES:
            os.replace(path, path + ".old")
        with open(path, "a", encoding="utf-8", errors="replace") as handle:
            handle.write(text + "\n")
    except Exception:
        pass


def lines():
    return list(_lines)


def subscribe(callback):
    if callback not in _subscribers:
        _subscribers.append(callback)


def unsubscribe(callback):
    if callback in _subscribers:
        _subscribers.remove(callback)


def clear():
    del _lines[:]
