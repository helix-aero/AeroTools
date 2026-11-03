"""Pose d'une projection de marqueur par clic dans la vue photo.

Un filtre d'evenements est installe sur l'application Qt. Au clic declencheur
(clic gauche simple, ou Maj + clic gauche) il demande a `place_at` de poser la
projection du marqueur courant sous le curseur, en ecrasant celle que le
marqueur avait deja sur cette photo. L'evenement n'est consomme que si la
projection a ete posee : sinon Metashape garde la main (rotation, panoramique,
selection). Ctrl/Alt + clic, clic droit et molette ne sont jamais interceptes.

Le meme chemin sert au raccourci clavier (place_at_cursor).
"""

import time
import traceback

from ... import i18n, journal, msapi
from ...qt import QtCore, cursor_pos, event_global_pos, modifiers
from . import dataset, detection, history, photos

# True : un clic gauche simple pose le point, Maj + clic aussi. False : seul
# Maj + clic pose (ancien comportement).
PLAIN_CLICK_PLACES = True

# Deux appels du filtre pour le meme clic (l'evenement refuse remonte les
# parents, chacun repasse par le filtre) ne doivent journaliser qu'une fois.
_DEDUP_WINDOW_S = 0.25


# --------------------------------------------------------------------------
# Pose
# --------------------------------------------------------------------------

def _refuse(message):
    journal.write("Pointage ignore : " + message)
    return False


def place_at(state, global_pos):
    """Pose la projection du marqueur courant sous `global_pos`.

    Renvoie True seulement si la projection a ete posee. Chaque refus laisse
    une ligne courte dans le journal.
    """
    if not state.enabled:
        return _refuse("outil desactive")

    label = state.active_label()
    if label is None:
        return _refuse("aucun marqueur courant")
    marker = state.active_marker

    if not msapi.cursor_over_photo_view(global_pos):
        return _refuse("curseur hors d'une photo ({})".format(
            msapi.describe_position(global_pos)
        ))

    chunk = msapi.chunk()
    if chunk is None:
        return _refuse("aucun chunk actif")
    try:
        view = msapi.photo_view()
        camera = view.camera if view is not None else None
    except Exception:
        camera = None
    if camera is None:
        return _refuse("aucune photo ouverte")
    if not msapi.camera_in_chunk(chunk, camera):
        return _refuse("la photo ouverte n'appartient pas au chunk actif")

    coords, reason = msapi.image_coords_at(global_pos)
    if coords is None:
        return _refuse(reason or "position illisible")
    x, y = coords

    camera_name = msapi.camera_label(camera)

    # L'historique est ecrit AVANT la pose : il garde la projection d'avant.
    entry = history.push(
        marker, camera, msapi.get_projection(marker, camera),
        chunk_key=msapi.chunk_key(chunk), label=label, camera_name=camera_name,
    )
    ok, message = msapi.set_projection(marker, camera, x, y)
    if not ok:
        history.discard(entry)
        journal.write("Echec de la pose de {} : {}".format(label, message))
        return False

    journal.write("{} pose en ({:.0f}, {:.0f}) sur {}".format(
        label, x, y, camera_name
    ))

    # Suites du pointage : aucune ne doit remettre en cause la pose.
    try:
        width, height = msapi.camera_size(camera)
        record = {
            "chunk": msapi.chunk_label(chunk),
            "marker": label,
            "camera": camera_name,
            "image_path": msapi.camera_image_path(camera),
            "image_w": width,
            "image_h": height,
            "x": x,
            "y": y,
            "source": "manual",
        }
        written = dataset.record_pin(state, record)
        detection.notify_pin(written if written is not None else record)
    except Exception as exc:
        journal.write("suites du pointage : {!r}".format(exc))
    photos.advance_after_place(state)
    photos.panel_call(state, "update_active_card")
    photos.panel_call(state, "refresh_errors")
    return True


def place_at_cursor(state):
    """Raccourci Ctrl+Maj+M : pose sous le curseur."""
    if not state.enabled:
        return
    try:
        place_at(state, cursor_pos())
    except Exception as exc:
        journal.write("pointage au curseur : {!r}".format(exc))


# --------------------------------------------------------------------------
# Annulation
# --------------------------------------------------------------------------

def _undo_status(state, text):
    photos.panel_call(state, "set_status", text)


def undo_last(state):
    """Defait le dernier pointage pose par FastGCP.

    Restaure la projection que le marqueur avait avant sur cette photo, ou la
    supprime s'il n'en avait pas ; rouvre la photo, remet le marqueur comme
    marqueur courant et rafraichit le panneau. Renvoie True si quelque chose a
    ete defait. Ne leve jamais.
    """
    try:
        return _undo_last(state)
    except Exception as exc:
        try:
            journal.write("annulation : {!r}".format(exc))
        except Exception:
            pass
        return False


def _undo_last(state):
    if not state.enabled:
        return False
    chunk = msapi.chunk()
    key = msapi.chunk_key(chunk)

    entry = None
    while True:
        entry = history.pop()
        if entry is None:
            _undo_status(state, i18n.t("undo.nothing"))
            journal.write("Annuler : rien a annuler")
            return False
        if chunk is None or (entry.chunk_key is not None and entry.chunk_key != key):
            # Le chunk a change depuis : ces pointages ne sont plus atteignables.
            history.clear()
            journal.write("Annuler : historique d'un autre chunk, vide")
            return False
        if entry.is_valid() and msapi.camera_in_chunk(chunk, entry.camera):
            break
        journal.write("Annuler : entree ignoree (marqueur ou photo disparus)")

    marker, camera = entry.marker, entry.camera
    label = msapi.marker_label(marker) or entry.label
    camera_name = msapi.camera_label(camera) or entry.camera_name

    previous = entry.previous
    if previous is None:
        ok, message = msapi.remove_projection(marker, camera)
        what = "projection retiree"
    else:
        ok, message = msapi.set_projection(marker, camera, previous[0], previous[1], previous[2])
        what = "position precedente restauree"
    if not ok:
        journal.write("Echec de l'annulation de {} sur {} : {}".format(
            label, camera_name, message))
        _undo_status(state, i18n.t("undo.failed"))
        return False

    # Une photo suivante programmee par le pointage defait n'a plus lieu d'etre.
    photos.cancel_advance()
    state.active_marker = marker
    if getattr(state, "panel", None) is not None:
        photos.panel_call(state, "set_active_marker_by_label", label)
    already_open = msapi.camera_key(msapi.current_camera()) == msapi.camera_key(camera)
    msapi.open_photo(camera)
    # Photo rouverte par l'annulation : zoom sur le point defait. Photo deja a
    # l'ecran : on ne touche pas au zoom de l'operateur.
    photos.zoom_on_marker(state, marker, camera, force=not already_open)
    photos.panel_call(state, "update_active_card")
    photos.panel_call(state, "refresh_errors")
    _undo_status(state, i18n.t("undo.done", label=label))
    journal.write("Annule : {} sur {} ({})".format(label, camera_name, what))
    return True


# --------------------------------------------------------------------------
# Filtre d'evenements
# --------------------------------------------------------------------------

class ClickFilter(QtCore.QObject):
    """Intercepte le clic gauche declencheur et pose la projection."""

    def __init__(self, state, parent=None):
        super(ClickFilter, self).__init__(parent)
        self._state = state
        self._busy = False
        self._error_logged = False
        self._last_refused = None

    def _is_trigger(self, ev):
        """Clic gauche, sans Ctrl ni Alt, avec ou sans Maj selon PLAIN_CLICK_PLACES."""
        if ev.button() != QtCore.Qt.LeftButton:
            return False
        ctrl, shift, alt = modifiers(ev)
        if ctrl or alt:
            return False
        return shift or PLAIN_CLICK_PLACES

    def _already_refused(self, ev, pos):
        try:
            stamp = int(ev.timestamp())
        except Exception:
            stamp = 0
        key = (stamp, pos.x(), pos.y())
        now = time.time()
        last = self._last_refused
        if last is not None and last[0] == key and now - last[1] < _DEDUP_WINDOW_S:
            return True
        self._last_refused = (key, now)
        return False

    def eventFilter(self, obj, ev):
        try:
            if ev.type() != QtCore.QEvent.MouseButtonPress:
                return False
            state = self._state
            if not state.enabled or self._busy:
                return False
            if not self._is_trigger(ev):
                return False
            pos = event_global_pos(ev)
            # Clic hors vue photo : on ne dit rien et on laisse Metashape faire.
            if not msapi.cursor_over_photo_view(pos):
                return False
            if self._already_refused(ev, pos):
                return False
            self._busy = True
            try:
                return bool(place_at(state, pos))
            finally:
                self._busy = False
        except Exception as exc:
            self._busy = False
            if not self._error_logged:
                self._error_logged = True
                try:
                    journal.write("filtre de clics : {!r} | {}".format(exc, " / ".join(l.strip() for l in traceback.format_exc().strip().splitlines()[-4:])))
                except Exception:
                    pass
            return False


def install_filter(state):
    """Installe le filtre de clics sur l'application Qt."""
    remove_filter(state)
    app = QtCore.QCoreApplication.instance()
    if app is None:
        journal.write("filtre de clics : application Qt introuvable")
        return None
    click_filter = ClickFilter(state)
    app.installEventFilter(click_filter)
    state.click_filter = click_filter
    return click_filter


def remove_filter(state):
    """Retire le filtre de clics. Sans erreur s'il n'y en a pas."""
    click_filter = getattr(state, "click_filter", None)
    state.click_filter = None
    if click_filter is None:
        return
    try:
        app = QtCore.QCoreApplication.instance()
        if app is not None:
            app.removeEventFilter(click_filter)
    except RuntimeError:
        pass
    try:
        click_filter.deleteLater()
    except RuntimeError:
        pass
