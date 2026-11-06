"""Start and stop of Aero-Exports: the entry point called by the Aero menu."""

from ... import i18n, journal, msapi, theme
from ...qt import QtGui

_STATE = {"dialog": None}


def install():
    """Open the Aero-Exports window (or bring it to the front)."""
    window = msapi.main_window()
    if window is None:
        msapi.message_box(i18n.t("tool.no_window"))
        return
    theme.load_fonts(QtGui)
    from . import dialog
    _STATE["dialog"] = dialog.show(window, _STATE["dialog"])
    journal.write("Aero-Exports opened")


def uninstall():
    """Close the window (before a code reload), unless an export is running."""

    shown = _STATE.get("dialog")
    if shown is None:
        return
    try:
        shown.close()
        shown.deleteLater()
    except RuntimeError:
        pass
    _STATE["dialog"] = None
