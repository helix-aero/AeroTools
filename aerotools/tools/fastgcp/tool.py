"""Demarrage et arret de FastGCP. C'est le point d'entree appele par le menu Aero.

`install()` est idempotent : relance depuis le menu = on ferme proprement
l'instance precedente (filtre d'evenements, timer, raccourci, dock) avant d'en
ouvrir une neuve. Sans cela, chaque relance empilerait un filtre de clics de
plus sur l'application Qt.
"""

from ... import i18n, journal, msapi, qt, theme
from ...qt import QShortcut, QtCore, QtGui, QtWidgets
from . import aigcp, history, marking, photos, session as session_mod

TICK_MS = 400
POLL_TICKS = 5                        # le tableau du panneau est compare a Metashape tous les 5 battements (2 s)
SLOW_POLL_S = 0.15                    # lecture plus lente que ca : on espace les comparaisons par 5
HOTKEY = "Ctrl+Shift+M"
UNDO_KEY = "Ctrl+Z"
REDO_KEYS = ("Ctrl+Y", "Ctrl+Shift+Z")


def install():
    """Ouvre (ou rouvre) FastGCP."""
    uninstall()

    state = session_mod.current()
    window = msapi.main_window()
    if window is None:
        msapi.message_box(i18n.t("tool.no_window"))
        return

    theme.load_fonts(QtGui)

    from .panel import FastGcpPanel

    panel = FastGcpPanel(state, window)
    state.panel = panel
    window.addDockWidget(QtCore.Qt.RightDockWidgetArea, panel)
    panel.show()
    panel.raise_()

    marking.install_filter(state)

    shortcut = QShortcut(QtGui.QKeySequence(HOTKEY), window)
    shortcut.setContext(QtCore.Qt.ApplicationShortcut)
    shortcut.activated.connect(lambda: marking.place_at_cursor(state))
    state.shortcut = shortcut

    _install_undo_shortcut(state, window)

    timer = QtCore.QTimer(window)
    timer.setInterval(TICK_MS)
    timer.timeout.connect(lambda: _tick(state))
    timer.start()
    state.timer = timer

    aigcp.set_choice("scope", aigcp.DEFAULT_SCOPE)      # on repart toujours sur « Selected markers »
    panel.update_aigcp()
    panel.refresh_markers()
    trigger = "clic" if marking.PLAIN_CLICK_PLACES else "Maj + clic"
    journal.write("FastGCP demarre : Qt {}, Metashape {} ; {} dans la vue photo, ou {}".format(
        qt.BINDING, msapi.app_version() or "version inconnue", trigger, HOTKEY
    ))


def uninstall():
    """Ferme FastGCP et libere tout ce qui tourne en fond."""
    state = session_mod.current()

    marking.remove_filter(state)
    aigcp.cancel(state)

    timer = getattr(state, "timer", None)
    if timer is not None:
        try:
            timer.stop()
            timer.deleteLater()
        except RuntimeError:
            pass
        state.timer = None

    shortcut = getattr(state, "shortcut", None)
    if shortcut is not None:
        try:
            shortcut.setEnabled(False)
            shortcut.deleteLater()
        except RuntimeError:
            pass
        state.shortcut = None

    listener = getattr(state, "undo_listener", None)
    if listener is not None:
        history.unsubscribe(listener)
        state.undo_listener = None

    undo_shortcut = getattr(state, "undo_shortcut", None)
    if undo_shortcut is not None:
        try:
            undo_shortcut.setEnabled(False)
            undo_shortcut.deleteLater()
        except RuntimeError:
            pass
        state.undo_shortcut = None

    for redo in getattr(state, "redo_shortcuts", None) or []:
        try:
            redo.setEnabled(False)
            redo.deleteLater()
        except RuntimeError:
            pass
    state.redo_shortcuts = []

    panel = getattr(state, "panel", None)
    if panel is not None:
        try:
            panel.detach()
        except Exception:
            pass
        try:
            panel.setParent(None)
            panel.deleteLater()
        except RuntimeError:
            pass
        state.panel = None

    state.forget_chunk()


def _install_undo_shortcut(state, window):
    """Ctrl+Z defait le dernier pointage, mais seulement quand il y en a un.

    Le raccourci n'est actif que si l'outil tourne et que l'historique n'est pas
    vide : sinon Ctrl+Z reste a Metashape. Les champs texte du panneau gardent
    le leur (Qt leur laisse la touche avant tout raccourci).
    """
    shortcut = QShortcut(QtGui.QKeySequence(UNDO_KEY), window)
    shortcut.setContext(QtCore.Qt.ApplicationShortcut)

    def fire(*_args):
        marking.undo_last(state)

    shortcut.activated.connect(fire)
    # Si Metashape avait deja un Ctrl+Z, Qt le signale comme ambigu : on agit
    # quand meme, puisque le raccourci n'est actif que lorsqu'il y a de quoi defaire.
    try:
        shortcut.activatedAmbiguously.connect(fire)
    except Exception:
        pass

    redo_shortcuts = []
    for key in REDO_KEYS:
        redo = QShortcut(QtGui.QKeySequence(key), window)
        redo.setContext(QtCore.Qt.ApplicationShortcut)
        redo.activated.connect(lambda *_args: marking.redo_last(state))
        redo_shortcuts.append(redo)

    def sync():
        try:
            shortcut.setEnabled(bool(state.enabled) and history.can_undo())
            for redo in redo_shortcuts:
                redo.setEnabled(bool(state.enabled) and history.can_redo())
        except RuntimeError:
            history.unsubscribe(sync)

    state.redo_shortcuts = redo_shortcuts
    state.undo_shortcut = shortcut
    state.undo_listener = sync
    history.subscribe(sync)
    sync()


def panel():
    return getattr(session_mod.current(), "panel", None)


# --------------------------------------------------------------------------
# Battement : suit l'etat de Metashape
# --------------------------------------------------------------------------

def _poll_table(state):
    """Le tableau du panneau suit Metashape sans qu'on clique : relu quand il a change ailleurs."""
    panel = state.panel
    if not hasattr(panel, "poll_rows"):
        return
    state.poll_count = getattr(state, "poll_count", 0) + 1
    every = POLL_TICKS * (5 if getattr(panel, "poll_cost", 0.0) > SLOW_POLL_S else 1)
    if state.poll_count % every == 0:
        panel.poll_rows()


def _tick(state):
    """Detecte un changement de chunk et un changement de selection.

    Metashape ne previent pas quand l'utilisateur clique un marqueur dans le
    panneau Reference ou change de chunk : on relit l'etat quatre fois par
    seconde, ce qui est insensible a l'usage et evite d'accrocher des signaux
    internes non documentes.
    """
    if state.panel is None:
        return
    aigcp.poll(state)
    _poll_table(state)
    chunk = msapi.chunk()
    key = msapi.chunk_key(chunk)

    if key != state.last_chunk_key:
        state.last_chunk_key = key
        state.forget_chunk()
        photos.panel_call(state, "close_review")
        try:
            # Le nom du chunk suit la progression dans le statut du panneau.
            state.panel.set_chunk_name(
                msapi.chunk_label(chunk) or i18n.t("tool.chunk_unnamed"))
            state.panel.refresh_markers()
        except Exception as exc:
            journal.write("relecture apres changement de chunk : {!r}".format(exc))
        return

    if chunk is None:
        return

    selected = msapi.selected_markers(chunk)
    if len(selected) != 1:
        return
    label = msapi.marker_label(selected[0])
    if not label or label == state.last_selected_label:
        return
    state.last_selected_label = label

    # Suivi de la selection Reference et filtre Photos : toujours actifs.
    try:
        state.panel.set_active_marker_by_label(label, from_sync=True)
    except Exception as exc:
        journal.write("synchronisation de la selection : {!r}".format(exc))
    photos.filter_for_marker(state, selected[0])
