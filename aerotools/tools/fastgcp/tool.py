"""Start and stop of FastGCP. This is the entry point called by the Aero menu.

`install()` is idempotent: relaunching from the menu closes the previous
instance cleanly (event filter, timer, shortcut, dock) before opening a new
one. Without this, each relaunch would stack one more click filter on the
Qt application.
"""

from ... import i18n, journal, msapi, qt, theme
from ...qt import QShortcut, QtCore, QtGui, QtWidgets
from . import aigcp, history, marking, photos, session as session_mod

TICK_MS = 400
POLL_TICKS = 5                        # the panel table is compared with Metashape every 5 ticks (2 s)
SLOW_POLL_S = 0.15                    # a read slower than this: the comparisons are spaced out by 5
HOTKEY = "Ctrl+Shift+M"
UNDO_KEY = "Ctrl+Z"
REDO_KEYS = ("Ctrl+Y", "Ctrl+Shift+Z")


def install():
    """Open (or reopen) FastGCP."""
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

    aigcp.set_choice("scope", aigcp.DEFAULT_SCOPE)      # always start again from "Selected markers"
    panel.update_aigcp()
    panel.refresh_markers()
    trigger = "click" if marking.PLAIN_CLICK_PLACES else "Shift + click"
    journal.write("FastGCP started: Qt {}, Metashape {}; {} in the photo view, or {}".format(
        qt.BINDING, msapi.app_version() or "unknown version", trigger, HOTKEY
    ))


def uninstall():
    """Close FastGCP and release everything that runs in the background."""
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
    """Ctrl+Z undoes the last marking, but only when there is one.

    The shortcut is only active if the tool is running and the history is not
    empty: otherwise Ctrl+Z stays with Metashape. The text fields of the panel keep
    their own (Qt gives them the key before any shortcut).
    """
    shortcut = QShortcut(QtGui.QKeySequence(UNDO_KEY), window)
    shortcut.setContext(QtCore.Qt.ApplicationShortcut)

    def fire(*_args):
        marking.undo_last(state)

    shortcut.activated.connect(fire)
    # If Metashape already had a Ctrl+Z, Qt reports it as ambiguous: we act
    # anyway, since the shortcut is only active when there is something to undo.
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
# Tick: follows the state of Metashape
# --------------------------------------------------------------------------

def _poll_table(state):
    """The panel table follows Metashape without any click: reread when it changed elsewhere."""
    panel = state.panel
    if not hasattr(panel, "poll_rows"):
        return
    state.poll_count = getattr(state, "poll_count", 0) + 1
    every = POLL_TICKS * (5 if getattr(panel, "poll_cost", 0.0) > SLOW_POLL_S else 1)
    if state.poll_count % every == 0:
        panel.poll_rows()


def _tick(state):
    """Detect a chunk change and a selection change.

    Metashape does not notify when the user clicks a marker in the
    Reference pane or changes chunk: the state is reread four times per
    second, which is unnoticeable in use and avoids hooking undocumented
    internal signals.
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
            # The chunk name follows the progress in the panel status.
            state.panel.set_chunk_name(
                msapi.chunk_label(chunk) or i18n.t("tool.chunk_unnamed"))
            state.panel.refresh_markers()
        except Exception as exc:
            journal.write("reread after chunk change: {!r}".format(exc))
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

    # Following of the Reference selection and Photos filter: always on.
    try:
        state.panel.set_active_marker_by_label(label, from_sync=True)
    except Exception as exc:
        journal.write("selection synchronisation: {!r}".format(exc))
    photos.filter_for_marker(state, selected[0])
