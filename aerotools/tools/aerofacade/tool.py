"""Demarrage et arret de Aero-Facade : le point d'entree appele par le menu Aero (Aero > Aero-Facade).

`install()` / `uninstall()` sont les noms que le lanceur (`bootstrap.launch`) appelle ; `start()` / `stop()` sont les memes fonctions.
Dans Metashape la fenetre parle au projet par `msfacade.MetashapeBackend` (module de l'adaptateur, importe seulement ici) ; les tests
et les captures d'ecran donnent leur propre `Backend` (`FakeBackend`) a `open_window`.
"""

from ... import i18n, journal, msapi, theme
from ...qt import QtGui

_STATE = {"window": None}


def make_backend():
    """Le `Backend` de Metashape. Leve ImportError si l'adaptateur n'est pas installe, RuntimeError s'il n'y a pas de projet."""
    from . import msfacade                                   # le seul import de Metashape de tout le paquet : paresseux, ici
    return msfacade.MetashapeBackend()


def open_window(backend, parent=None, existing=None):
    """Ouvre (ou ramene devant) la fenetre Aero-Facade sur `backend`. Renvoie la fenetre, a garder."""
    from . import wizard
    window = existing
    try:
        if window is not None:
            window.objectName()
            if window._lang != i18n.lang() or window.backend is not backend:
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
    """Ouvre (ou ramene devant) la fenetre Aero-Facade sur le projet de Metashape."""
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
            if shown._lang == i18n.lang():
                return
    except RuntimeError:
        pass
    try:
        backend = make_backend()
    except ImportError as exc:
        journal.write("Aero-Facade : adaptateur Metashape absent : {!r}".format(exc))
        msapi.message_box(i18n.t("facade.ui.backend_missing", error=str(exc)))
        return
    except Exception as exc:
        journal.write("Aero-Facade : pas de projet : {!r}".format(exc))
        msapi.message_box(i18n.t("facade.ui.no_backend"))
        return
    theme.load_fonts(QtGui)
    _STATE["window"] = open_window(backend, parent, _STATE.get("window"))
    journal.write("Aero-Facade ouvert")


def uninstall():
    """Ferme la fenetre (avant un rechargement du code) : un calcul en cours est d'abord arrete."""
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
