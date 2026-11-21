"""Start and stop of Aero-PPK: the entry point called by the Aero menu."""

from ... import i18n, journal, msapi, theme, uikit
from ...qt import QtGui

_STATE = {"dialog": None}


def install():
    """Open (or bring to the front) the Aero-PPK window."""
    window = msapi.main_window()
    if window is None:
        uikit.inform(None, i18n.t("aeroppk.title"), i18n.t("tool.no_window"), "warning")
        return
    theme.load_fonts(QtGui)
    from . import dialog
    _STATE["dialog"] = dialog.PpkDialog.open(window)
    journal.write("Aero-PPK opened")


def uninstall():
    """Close the window (before a code reload), unless a calculation is running."""
    shown = _STATE.get("dialog")
    if shown is None:
        return
    try:
        shown.close()
        shown.deleteLater()
    except RuntimeError:
        pass
    _STATE["dialog"] = None
