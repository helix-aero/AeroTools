"""Demarrage et arret de Aero-Exports : le point d'entree appele par le menu Aero."""

from ... import i18n, journal, msapi, theme
from ...qt import QtGui

_STATE = {"dialog": None}


def install():
    """Ouvre (ou ramene devant) la fenetre Aero-Exports."""
    window = msapi.main_window()
    if window is None:
        msapi.message_box(i18n.t("tool.no_window"))
        return
    theme.load_fonts(QtGui)
    from . import dialog
    _STATE["dialog"] = dialog.show(window, _STATE["dialog"])
    journal.write("Aero-Exports ouvert")


def uninstall():
    """Ferme la fenetre (avant un rechargement du code), sauf si un export est en cours."""
    shown = _STATE.get("dialog")
    if shown is None:
        return
    try:
        shown.close()
        shown.deleteLater()
    except RuntimeError:
        pass
    _STATE["dialog"] = None
