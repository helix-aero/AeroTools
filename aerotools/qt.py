"""Acces a Qt et compatibilite PySide2 / PySide6.

Metashape 2.x embarque PySide2 (Qt5). Les versions plus recentes passent a
PySide6 (Qt6), ou quelques API de QMouseEvent changent de nom. Tout le code
d'AeroTools passe par ce module pour ne pas avoir a s'en soucier.
"""

try:
    from PySide2 import QtCore, QtGui, QtWidgets  # noqa: F401
    BINDING = "PySide2"
except ImportError:  # pragma: no cover - depend de la version de Metashape
    from PySide6 import QtCore, QtGui, QtWidgets  # noqa: F401
    BINDING = "PySide6"


# QShortcut est dans QtWidgets en Qt5 (PySide2) et dans QtGui en Qt6 (PySide6).
QShortcut = getattr(QtWidgets, "QShortcut", None) or getattr(QtGui, "QShortcut", None)


def app():
    return QtWidgets.QApplication.instance()


def event_global_pos(event):
    """Position globale d'un evenement souris, en QPoint, Qt5 comme Qt6."""
    getter = getattr(event, "globalPos", None)
    if getter is not None:
        try:
            return getter()
        except Exception:
            pass
    getter = getattr(event, "globalPosition", None)
    if getter is not None:
        try:
            return getter().toPoint()
        except Exception:
            pass
    return QtGui.QCursor.pos()


def _flag_is_set(mods, flag):
    """True si `flag` est dans le jeu de modificateurs `mods`.

    PySide2 : `int(mods) & int(flag)` (c'est la forme qui a toujours marche).
    PySide6 : les modificateurs sont des enum.Flag, `int()` peut lever ; on
    replie sur `.value`, puis sur l'operateur `&`, puis sur `in`.
    """
    try:
        return bool(int(mods) & int(flag))
    except Exception:
        pass
    try:
        return bool(mods.value & flag.value)
    except Exception:
        pass
    try:
        return bool(mods & flag)
    except Exception:
        pass
    try:
        return flag in mods
    except Exception:
        return False


def modifiers(event):
    """Jeu de modificateurs de l'evenement sous forme (ctrl, shift, alt)."""
    try:
        mods = event.modifiers()
    except Exception:
        return False, False, False
    return (
        _flag_is_set(mods, QtCore.Qt.ControlModifier),
        _flag_is_set(mods, QtCore.Qt.ShiftModifier),
        _flag_is_set(mods, QtCore.Qt.AltModifier),
    )


def cursor_pos():
    return QtGui.QCursor.pos()
