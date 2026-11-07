"""Start and stop of Aero-L3: the entry point called by the Aero menu (Aero > Aero-L3).

`install()` / `uninstall()` are the names the launcher (`bootstrap.launch`) calls. Inside Metashape the window talks to the project through
`msl3.MetashapeL3Backend` (imported only here, lazily); the tests and the screenshots hand their own `Backend` (`FakeBackend`) to `open_window`.
"""

from ... import i18n, journal, msapi, theme
from ...qt import QtGui

_STATE = {"window": None}


def make_backend():
    """The Metashape `Backend`. Raises ImportError if the adapter cannot load. All its computing runs on the main thread (`threaded = False`)
    and calls `pump()` between two Metashape calls, which lets Qt work."""
    from . import msl3                                       # the only Metashape import of the whole package: lazy, here
    from ...qt import QtWidgets
    backend = msl3.MetashapeL3Backend(pump=QtWidgets.QApplication.processEvents)
    backend.threaded = False
    return backend


def open_window(backend, parent=None, existing=None):
    """Open (or bring to the front) the Aero-L3 window on `backend`. Returns the window, to be kept."""
    from . import wizard
    window = existing
    try:
        if window is not None:
            window.objectName()
            if window.backend is not backend:
                window.close()
                window.deleteLater()
                window = None
    except RuntimeError:
        window = None
    if window is None:
        window = wizard.L3Wizard(backend, parent)
        window.fit_to_fonts()
    window.show()
    window.raise_()
    window.activateWindow()
    return window


def install():
    """Open (or bring to the front) the Aero-L3 window."""
    parent = msapi.main_window()
    if parent is None:
        msapi.message_box(i18n.t("tool.no_window"))
        return
    shown = _STATE.get("window")
    try:
        if shown is not None:
            shown.objectName()
            shown.show()
            shown.raise_()
            shown.activateWindow()
            return
    except RuntimeError:
        pass
    try:
        backend = make_backend()
    except ImportError as exc:
        journal.write("Aero-L3: Metashape adapter missing: {!r}".format(exc))
        msapi.message_box(i18n.t("l3.ui.backend_missing", error=str(exc)))
        return
    except Exception as exc:
        journal.write("Aero-L3: cannot start: {!r}".format(exc))
        msapi.message_box(i18n.t("l3.ui.no_backend"))
        return
    theme.load_fonts(QtGui)
    _STATE["window"] = open_window(backend, parent, _STATE.get("window"))
    journal.write("Aero-L3 opened")


def uninstall():
    """Close the window (before the code is reloaded): a running job is stopped first."""
    shown = _STATE.get("window")
    if shown is None:
        return
    try:
        shown.cancel_task()
        shown.wait_idle(5000)
        shown.close()
        shown.deleteLater()
    except RuntimeError:
        pass
    _STATE["window"] = None


start = install
stop = uninstall
