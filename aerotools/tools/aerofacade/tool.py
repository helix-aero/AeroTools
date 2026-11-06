"""Start and stop of Aero-Facade: the entry point called by the Aero menu (Aero > Aero-Facade).

`install()` / `uninstall()` are the names the launcher (`bootstrap.launch`) calls; `start()` / `stop()` are the same functions.
Inside Metashape the window talks to the project through `msfacade.MetashapeBackend` (the adapter module, imported only here); the
tests and the screenshots hand their own `Backend` (`FakeBackend`) to `open_window`.
"""

from ... import i18n, journal, msapi, theme
from ...qt import QtGui

_STATE = {"window": None}


def make_backend():
    """The Metashape `Backend`. Raises ImportError if the adapter is not installed, RuntimeError if there is no project.

    Threading model of the adapter: all its computing runs on the MAIN thread and blocks it; between two calls it calls
    `pump()`, which lets Qt work (the window refreshes itself with its timer). The window knows it from `backend.threaded = False`."""
    from . import msfacade                                   # the only Metashape import of the whole package: lazy, here
    from ...qt import QtWidgets
    backend = msfacade.MetashapeBackend(pump=QtWidgets.QApplication.processEvents)
    backend.threaded = False
    return backend


def open_window(backend, parent=None, existing=None):
    """Open (or bring to the front) the Aero-Facade window on `backend`. Returns the window, to be kept."""
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
        window = wizard.FacadeWizard(backend, parent)
        window.fit_to_fonts()
    window.show()
    window.raise_()
    window.activateWindow()
    return window


def install():
    """Open (or bring to the front) the Aero-Facade window on the Metashape project."""
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
        journal.write("Aero-Facade: Metashape adapter missing: {!r}".format(exc))
        msapi.message_box(i18n.t("facade.ui.backend_missing", error=str(exc)))
        return
    except Exception as exc:
        journal.write("Aero-Facade: no project: {!r}".format(exc))
        msapi.message_box(i18n.t("facade.ui.no_backend"))
        return
    theme.load_fonts(QtGui)
    _STATE["window"] = open_window(backend, parent, _STATE.get("window"))
    journal.write("Aero-Facade opened")


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
