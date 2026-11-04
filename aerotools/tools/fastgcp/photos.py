"""Filtre du panneau Photos et passage a la photo suivante.

Quand un marqueur devient courant, le panneau Photos est filtre sur les photos
qui le voient : celles ou il a deja une projection, puis celles d'ou son point
tombe dans l'image (voir msapi.cameras_seeing_marker). Apres chaque pointage
reussi, on ouvre tout seul la photo suivante de cette liste qui n'a pas encore
de projection du marqueur.

Zoom (msapi.zoom_to_point) : quand FastGCP ouvre une photo pour un marqueur
(passage a la photo suivante, annulation d'un pointage), ou quand le marqueur
courant change alors qu'une photo est deja ouverte, la vue est centree sur le
marqueur (projection posee, sinon projection estimee) avec environ 400 px
d'image visibles (reglage cache fastgcp.zoom_fenetre_px). Si la vue est deja
zoomee (par l'operateur, ou par Metashape qui recentre lui-meme sur le marqueur
en gardant l'echelle), seul le centre est repose : l'echelle de l'operateur est
conservee, ni double zoom ni dezoom ; l'echelle automatique ne s'applique que
sur une photo encore ajustee a la fenetre (premiere photo). Un couple (photo,
marqueur) n'est recentre qu'une fois hors demande explicite.
Rien n'est fait quand l'outil est en pause.
"""

from ... import journal, msapi
from ...qt import QtCore

# Delai avant la photo suivante : laisse Metashape finir de redessiner.
ADVANCE_DELAY_MS = 300

# Numero du dernier passage programme. Annuler un pointage incremente ce numero :
# le passage encore en attente se reconnait perime et ne fait rien.
_ADVANCE_TOKEN = [0]

# Nombre de couples (photo, marqueur) deja zoomes que l'on garde en memoire.
ZOOM_MEMORY = 80


def panel_call(state, method, *args):
    """Appelle `state.panel.<method>(*args)` s'il y a un panneau. Avale tout."""
    try:
        panel = getattr(state, "panel", None)
        if panel is None:
            return
        getattr(panel, method)(*args)
    except Exception as exc:
        try:
            journal.write("panneau.{} : {!r}".format(method, exc))
        except Exception:
            pass


def explain_diagnostic(diag):
    """Phrase lisible pour un diagnostic de msapi.cameras_seeing_marker."""
    if not diag.get("point"):
        return "marqueur sans coordonnees ni projection, rien a calculer"
    if not diag.get("alignees"):
        return "aucune photo alignee dans ce chunk"
    if not diag.get("proches"):
        return "aucune photo alignee a moins de {:.0f} m du point".format(
            msapi.MAX_FILTER_DIST_M
        )
    if not diag.get("devant"):
        return "{} photo(s) proche(s) mais aucune ne regarde vers le point".format(
            diag.get("proches", 0)
        )
    if not diag.get("dans_image"):
        return "{} photo(s) devant le point mais il tombe hors image".format(
            diag.get("devant", 0)
        )
    return "aucune photo retenue"


def _zoom_memory(state):
    """Liste des couples (cle de photo, etiquette) deja zoomes dans la session."""
    memory = getattr(state, "zoomed", None)
    if memory is None:
        memory = []
        try:
            state.zoomed = memory
        except Exception:
            pass
    return memory


def zoom_on_marker(state, marker, camera=None, force=False):
    """Zoome la vue photo autour du marqueur sur `camera` (photo ouverte par defaut).

    Ne fait rien si l'outil est en pause, si la photo n'est pas ouverte dans la
    vue, ou si ce couple (photo, marqueur) a deja ete zoome (l'operateur a pu
    ajuster le zoom depuis) sauf `force`. Aucune photo ouverte ou point hors
    image : la vue reste intacte et une ligne du journal le dit. Renvoie True
    si un zoom a ete programme. Ne leve jamais.
    """
    try:
        if not getattr(state, "enabled", False) or marker is None:
            return False
        if camera is None:
            camera = msapi.current_camera()
        if camera is None:
            return False
        label = msapi.marker_label(marker)
        camera_key = msapi.camera_key(camera)
        if not label or camera_key is None:
            return False
        key = (camera_key, label)
        memory = _zoom_memory(state)
        if not force and key in memory:
            return False
        anchor, how = msapi.marker_anchor(msapi.chunk(), marker, camera)
        if anchor is None:
            journal.write("{} : pas de zoom sur {} ({})".format(
                label, msapi.camera_label(camera), how))
            return False
        window = getattr(state, "zoom_px", None)

        def done(ok):
            if not ok and key in memory:
                memory.remove(key)

        if key in memory:
            memory.remove(key)
        memory.append(key)
        del memory[:-ZOOM_MEMORY]
        scheduled = msapi.zoom_to_point(
            camera, anchor[0], anchor[1], window_px=window, on_done=done,
            guard=lambda: bool(getattr(state, "enabled", False)),
        )
        if not scheduled:
            done(False)
            return False
        return True
    except Exception as exc:
        try:
            journal.write("zoom sur le marqueur : {!r}".format(exc))
        except Exception:
            pass
        return False


def filter_for_marker(state, marker, force=False):
    """Filtre le panneau Photos sur les photos qui voient `marker`."""
    try:
        chunk = msapi.chunk()
        if chunk is None or marker is None:
            return
        label = msapi.marker_label(marker)
        if not label:
            return
        key = (msapi.chunk_key(chunk), label)
        if not force and key == state.last_filter_key:
            return
        state.last_filter_key = key

        cameras, diagnostic = msapi.cameras_seeing_marker(chunk, marker)
        state.last_diagnostic = diagnostic
        state.last_photo_count = len(cameras)
        state.last_cameras = list(cameras)

        if cameras:
            ok, message = msapi.set_photo_filter(cameras)
            if ok:
                journal.write("{} : {} photo(s)".format(label, len(cameras)))
            else:
                journal.write(
                    "{} : filtre Photos refuse ({})".format(label, message)
                )
        else:
            msapi.reset_photo_filter()
            journal.write(
                "{} : aucune photo, {}".format(label, explain_diagnostic(diagnostic))
            )
            if diagnostic.get("erreur"):
                journal.write("{} : erreur de calcul ({})".format(
                    label, diagnostic["erreur"]
                ))
        panel_call(state, "update_active_card")
        _show_marker(state, chunk, marker, cameras)
    except Exception as exc:
        try:
            journal.write("filtre photos : {!r}".format(exc))
        except Exception:
            pass


def _show_marker(state, chunk, marker, cameras):
    """Montre le marqueur choisi : zoom sur la photo ouverte, sinon ouvre la meilleure.

    Un clic sur un marqueur est toujours une demande de zoom, meme si ce couple
    (photo, marqueur) a deja ete zoome : c'est l'operateur qui le redemande.
    Si le point ne tombe pas dans la photo ouverte, on ouvre la premiere photo
    de la liste sans projection du marqueur (a defaut la premiere de la liste).
    """
    if not getattr(state, "enabled", False):
        return
    current = msapi.current_camera()
    anchor = None
    if current is not None:
        anchor, _how = msapi.marker_anchor(chunk, marker, current)
    if anchor is not None:
        zoom_on_marker(state, marker, current, force=True)
        return
    if not cameras:
        return
    pinned = set(msapi.camera_key(cam) for cam in msapi.projection_cameras(marker, without_proposals=True))
    target = next((cam for cam in cameras if msapi.camera_key(cam) not in pinned), cameras[0])
    ok, message = msapi.open_photo(target)
    if ok:
        journal.write("{} : photo ouverte {}".format(msapi.marker_label(marker), message))
        zoom_on_marker(state, marker, target, force=True)
    else:
        journal.write("{} : ouverture de la photo refusee ({})".format(
            msapi.marker_label(marker), message))


def reset_filter(state):
    """Retire le filtre du panneau Photos."""
    try:
        msapi.reset_photo_filter()
        state.last_filter_key = None
        state.last_photo_count = 0
        state.last_cameras = []
        journal.write("filtre Photos retire")
    except Exception as exc:
        try:
            journal.write("retrait du filtre : {!r}".format(exc))
        except Exception:
            pass


def _keys(cameras):
    return [msapi.camera_key(cam) for cam in cameras]


def _recompute_candidates(state, chunk, marker):
    """Recalcule la liste ordonnee des photos du marqueur et la memorise.

    Le filtre du panneau Photos n'est touche que si la liste a change : apres
    une pose le marqueur peut avoir une position triangulee, donc d'autres
    photos le voient.
    """
    cameras, diagnostic = msapi.cameras_seeing_marker(chunk, marker)
    previous = _keys(state.last_cameras)
    state.last_cameras = list(cameras)
    state.last_diagnostic = diagnostic
    state.last_photo_count = len(cameras)
    if cameras and _keys(cameras) != previous:
        ok, message = msapi.set_photo_filter(cameras)
        if not ok:
            journal.write("filtre Photos refuse ({})".format(message))
    return cameras


def next_unpinned(cameras, current_key, pinned_keys):
    """Premiere photo de `cameras` apres la photo courante sans projection.

    Renvoie (camera ou None, il_reste_des_photos_sans_projection).
    Si la photo courante n'est pas dans la liste, on part du debut.
    """
    keys = _keys(cameras)
    start = keys.index(current_key) + 1 if current_key in keys else 0
    remaining = [cam for cam, key in zip(cameras, keys) if key not in pinned_keys]
    for cam, key in zip(cameras[start:], keys[start:]):
        if key not in pinned_keys:
            return cam, True
    return None, bool(remaining)


def cancel_advance():
    """Annule le passage a la photo suivante encore en attente, s'il y en a un."""
    _ADVANCE_TOKEN[0] += 1


def _same_marker(state, marker):
    """Le marqueur courant est-il toujours `marker` ?

    On compare les etiquettes : la synchronisation avec le panneau Reference
    relit le marqueur et en cree un nouvel objet Python, donc `is` echouerait
    une fois sur deux et le passage a la photo suivante s'arreterait sans bruit.
    """
    try:
        active = state.active_marker
        if active is None:
            return False
        if active is marker:
            return True
        return msapi.marker_label(active) == msapi.marker_label(marker)
    except Exception:
        return False


# Nouveaux essais quand la vue photo ne dit pas encore quelle photo elle montre.
ADVANCE_RETRIES = 3
ADVANCE_RETRY_MS = 200


def _advance(state, marker, origin_key, token=None, attempt=0):
    try:
        if token is not None and token != _ADVANCE_TOKEN[0]:
            # Remplace par un pointage plus recent ou annule : cas normal, pas de bruit.
            return
        if not state.enabled:
            return
        if not _same_marker(state, marker):
            journal.write("photo suivante abandonnee : le marqueur courant a change")
            return
        current = msapi.current_camera()
        if current is None:
            if attempt < ADVANCE_RETRIES:
                QtCore.QTimer.singleShot(
                    ADVANCE_RETRY_MS,
                    lambda: _advance(state, marker, origin_key, token, attempt + 1),
                )
            else:
                journal.write("photo suivante abandonnee : la vue photo ne repond pas")
            return
        if msapi.camera_key(current) != origin_key:
            # L'operateur a deja change de photo : on ne lui force pas la main.
            journal.write("photo suivante abandonnee : vous avez change de photo")
            return
        chunk = msapi.chunk()
        if chunk is None:
            return
        label = msapi.marker_label(marker)
        cameras = _recompute_candidates(state, chunk, marker)
        pinned = set(msapi.camera_key(cam) for cam in msapi.projection_cameras(marker, without_proposals=True))
        target, remaining = next_unpinned(cameras, origin_key, pinned)
        if target is None:
            if remaining:
                journal.write("{} : plus de photo suivante sans projection".format(label))
            else:
                journal.write("{} : marqueur pointe sur toutes les photos".format(label))
        else:
            ok, message = msapi.open_photo(target)
            if ok:
                journal.write("{} : photo suivante {}".format(label, message))
                # Photo qu'on vient d'ouvrir : l'operateur n'a pas pu la zoomer.
                zoom_on_marker(state, marker, target, force=True)
            else:
                journal.write("{} : photo suivante refusee ({})".format(label, message))
        state.last_photo_count = len(state.last_cameras)
        panel_call(state, "update_active_card")
    except Exception as exc:
        try:
            journal.write("photo suivante : {!r}".format(exc))
        except Exception:
            pass


def advance_after_place(state):
    """Apres une pose reussie, ouvre la photo suivante sans projection du marqueur.

    Pas d'option : c'est toujours actif. L'ouverture passe par l'API de la vue
    photo (msapi.open_photo), pas par un envoi de touche.
    """
    try:
        marker = state.active_marker
        origin = msapi.current_camera()
        if marker is None or origin is None:
            return
        origin_key = msapi.camera_key(origin)
        _ADVANCE_TOKEN[0] += 1
        token = _ADVANCE_TOKEN[0]
        QtCore.QTimer.singleShot(
            ADVANCE_DELAY_MS, lambda: _advance(state, marker, origin_key, token)
        )
    except Exception as exc:
        try:
            journal.write("avance apres pointage : {!r}".format(exc))
        except Exception:
            pass
