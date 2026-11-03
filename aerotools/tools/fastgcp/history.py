"""Historique des pointages de FastGCP, pour pouvoir les annuler.

Une pile de profondeur limitee (MAX_DEPTH). Chaque entree garde ce qu'il faut
pour defaire une pose : le marqueur, la photo, et la projection que le marqueur
avait sur cette photo AVANT (x, y, pinned) ou None s'il n'en avait pas.

`marking.place_at` enregistre l'entree avant d'ecrire dans Metashape ;
`marking.undo_last` depile et restaure. Ce module ne touche pas a Metashape : il
ne fait que garder des references et prevenir ceux qui s'abonnent (bouton
Annuler du panneau, raccourci Ctrl+Z) quand la pile change.

Une reference de marqueur ou de photo devient invalide (RuntimeError) quand on
change de chunk : la pile est alors videe (Session.forget_chunk) et toute entree
devenue illisible est ignoree, jamais une exception.
"""

MAX_DEPTH = 100

_STACK = []
_LISTENERS = []


class Entry(object):
    """Un pointage pose : de quoi le defaire."""

    __slots__ = ("marker", "camera", "previous", "chunk_key", "label", "camera_name")

    def __init__(self, marker, camera, previous, chunk_key=None, label="", camera_name=""):
        self.marker = marker
        self.camera = camera
        self.previous = previous
        self.chunk_key = chunk_key
        self.label = label
        self.camera_name = camera_name

    def is_valid(self):
        """False si le marqueur ou la photo n'existent plus. Ne leve jamais."""
        try:
            self.marker.label
            self.camera.key
            return True
        except Exception:
            return False


def _clean_previous(previous):
    """(x, y, pinned) en flottants, ou None. Une valeur illisible vaut None."""
    if previous is None:
        return None
    try:
        x, y, pinned = previous
        return (float(x), float(y), bool(pinned))
    except Exception:
        return None


def push(marker, camera, previous, chunk_key=None, label="", camera_name=""):
    """Empile un pointage. Renvoie l'entree, ou None si elle est inutilisable."""
    if marker is None or camera is None:
        return None
    entry = Entry(marker, camera, _clean_previous(previous), chunk_key, label, camera_name)
    _STACK.append(entry)
    if len(_STACK) > MAX_DEPTH:
        del _STACK[: len(_STACK) - MAX_DEPTH]
    notify()
    return entry


def pop():
    """Depile et renvoie la derniere entree, ou None si la pile est vide."""
    if not _STACK:
        return None
    entry = _STACK.pop()
    notify()
    return entry


def discard(entry):
    """Retire `entry` si c'est la derniere (ecriture refusee apres l'empilage)."""
    if entry is not None and _STACK and _STACK[-1] is entry:
        _STACK.pop()
        notify()


def peek():
    return _STACK[-1] if _STACK else None


def size():
    return len(_STACK)


def can_undo():
    return bool(_STACK)


def clear():
    if _STACK:
        del _STACK[:]
        notify()


# --------------------------------------------------------------------------
# Abonnes
# --------------------------------------------------------------------------

def subscribe(callback):
    if callback not in _LISTENERS:
        _LISTENERS.append(callback)


def unsubscribe(callback):
    if callback in _LISTENERS:
        _LISTENERS.remove(callback)


def notify():
    """Previent les abonnes. Une erreur chez l'un d'eux ne gene pas les autres."""
    for callback in list(_LISTENERS):
        try:
            callback()
        except Exception:
            unsubscribe(callback)
