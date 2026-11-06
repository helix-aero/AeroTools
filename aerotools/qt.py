"""Qt access and PySide2 / PySide6 compatibility.

Metashape 2.x ships PySide2 (Qt5). More recent versions move to
PySide6 (Qt6), where a few QMouseEvent APIs change name. All AeroTools code
goes through this module so it does not have to care.
"""

try:
    from PySide2 import QtCore, QtGui, QtWidgets  # noqa: F401
    BINDING = "PySide2"
except ImportError:  # pragma: no cover - depends on the Metashape version
    from PySide6 import QtCore, QtGui, QtWidgets  # noqa: F401
    BINDING = "PySide6"


# QShortcut is in QtWidgets in Qt5 (PySide2) and in QtGui in Qt6 (PySide6).
QShortcut = getattr(QtWidgets, "QShortcut", None) or getattr(QtGui, "QShortcut", None)


def app():
    return QtWidgets.QApplication.instance()


def event_global_pos(event):
    """Global position of a mouse event, as a QPoint, on Qt5 and Qt6."""
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
    """True if `flag` is in the modifier set `mods`.

    PySide2: `int(mods) & int(flag)` (the form that has always worked).
    PySide6: modifiers are enum.Flag, `int()` may raise; fall back
    to `.value`, then to the `&` operator, then to `in`.
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
    """The event's modifier set as (ctrl, shift, alt)."""
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
