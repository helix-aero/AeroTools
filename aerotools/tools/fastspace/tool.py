"""Demarrage et arret de Fast Space : le point d'entree appele par le menu Aero."""

from ... import i18n, journal, msapi, theme
from ...qt import QtGui

_STATE = {"dialog": None}


def install():
    """Ouvre (ou ramene devant) la fenetre Fast Space."""
    window = msapi.main_window()
    if window is None:
        msapi.message_box(i18n.t("tool.no_window"))
        return
    theme.load_fonts(QtGui)
    from . import dialog
    _STATE["dialog"] = dialog.show(window, _STATE["dialog"])
    journal.write("Fast Space ouvert")


def uninstall():
    """Ferme la fenetre (avant un rechargement du code), sauf si un lot est en cours."""
    shown = _STATE.get("dialog")
    if shown is None:
        return
    try:
        shown.close()
        shown.deleteLater()
    except RuntimeError:
        pass
    _STATE["dialog"] = None
