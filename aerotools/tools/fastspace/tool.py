"""Start and stop of Fast Space: the entry point called by the Aero menu."""

from ... import i18n, journal, msapi, theme
from ...qt import QtGui

_STATE = {"dialog": None}


def install():
    """Open the Fast Space window (or bring it to the front)."""
    window = msapi.main_window()
    if window is None:
        msapi.message_box(i18n.t("tool.no_window"))
        return
    theme.load_fonts(QtGui)
    from . import dialog
    _STATE["dialog"] = dialog.show(window, _STATE["dialog"])
    journal.write("Aero-Space opened")


def uninstall():
    """Close the window (before a code reload), unless a batch is running."""

    shown = _STATE.get("dialog")
    if shown is None:
        return
    try:
        if shown.batch_running():
            # A batch is running (Reload during Lighten): closing is refused, so the window is neither deleted nor forgotten.
            journal.write("Aero-Space: reload while a batch is running: the window is kept until it ends")
            return False
        shown.close()
        shown.deleteLater()
    except RuntimeError:
        pass
    _STATE["dialog"] = None
    return True
