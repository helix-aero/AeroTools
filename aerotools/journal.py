"""Journal d'AeroTools : tampon circulaire + abonnes.

Le journal sert aux deux sorties : la console de Metashape (print) et la zone
depliable du panneau. Les panneaux s'abonnent, se desabonnent a la fermeture,
et une exception chez un abonne n'empeche jamais d'ecrire la ligne.
"""

import time

MAX_LINES = 300

_lines = []
_subscribers = []


def write(message, tag="AeroTools"):
    """Ajoute une ligne horodatee au journal et la pousse aux abonnes."""
    line = "{} {}".format(time.strftime("%H:%M:%S"), message)
    _lines.append(line)
    if len(_lines) > MAX_LINES:
        del _lines[: len(_lines) - MAX_LINES]
    print("[{}] {}".format(tag, message))
    for callback in list(_subscribers):
        try:
            callback(line)
        except Exception:
            unsubscribe(callback)
    return line


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
