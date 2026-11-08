"""Photos pane filter and move to the next photo.

When a marker becomes current, the Photos pane is filtered to the photos
that see it: those where it already has a projection, then those from which its point
falls inside the image (see msapi.cameras_seeing_marker). After each successful
marking, the next photo of this list that does not yet have a projection of the
marker is opened automatically.

Zoom (msapi.zoom_to_point): when FastGCP opens a photo for a marker
(move to the next photo, undo of a marking), or when the current marker
changes while a photo is already open, the view is centred on the
marker (placed projection, otherwise estimated projection) with about 400 px
of image visible (hidden setting fastgcp.zoom_window_px). If the view is already
zoomed (by the operator, or by Metashape which recentres on the marker by itself
while keeping the scale), only the centre is set again: the operator's scale is
kept, no double zoom and no zoom out; the automatic scale only applies
to a photo still fitted to the window (first photo). A (photo,
marker) pair is only recentred once unless explicitly requested.
Nothing is done when the tool is paused.
"""

from ... import journal, msapi
from ...qt import QtCore

# Delay before the next photo: lets Metashape finish redrawing.
ADVANCE_DELAY_MS = 300

# Number of the last scheduled move. Undoing a marking increments this number:
# the move still pending recognises itself as stale and does nothing.
_ADVANCE_TOKEN = [0]

# Number of already zoomed (photo, marker) pairs kept in memory.
ZOOM_MEMORY = 80


def panel_call(state, method, *args):
    """Call `state.panel.<method>(*args)` if there is a panel. Swallows everything."""
    try:
        panel = getattr(state, "panel", None)
        if panel is None:
            return
        getattr(panel, method)(*args)
    except Exception as exc:
        try:
            journal.write("panel.{}: {!r}".format(method, exc))
        except Exception:
            pass


def explain_diagnostic(diag):
    """Readable sentence for a diagnostic from msapi.cameras_seeing_marker."""
    if not diag.get("point"):
        return "marker without coordinates or projection, nothing to compute"
    if not diag.get("aligned"):
        return "no aligned photo in this chunk"
    if not diag.get("near"):
        return "no aligned photo within {:.0f} m of the point".format(
            msapi.MAX_FILTER_DIST_M
        )
    if not diag.get("in_front"):
        return "{} nearby photo(s) but none looks toward the point".format(
            diag.get("near", 0)
        )
    if not diag.get("in_image"):
        return "{} photo(s) facing the point but it falls outside the image".format(
            diag.get("in_front", 0)
        )
    return "no photo kept"


def _zoom_memory(state):
    """List of the (photo key, label) pairs already zoomed in the session."""
    memory = getattr(state, "zoomed", None)
    if memory is None:
        memory = []
        try:
            state.zoomed = memory
        except Exception:
            pass
    return memory


def zoom_on_marker(state, marker, camera=None, force=False):
    """Zoom the photo view around the marker on `camera` (open photo by default).

    Does nothing if the tool is paused, if the photo is not open in the
    view, or if this (photo, marker) pair has already been zoomed (the operator may have
    adjusted the zoom since) unless `force`. No open photo or point outside the
    image: the view stays untouched and a journal line says so. Returns True
    if a zoom was scheduled. Never raises.
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
            journal.write("{}: no zoom on {} ({})".format(
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
            journal.write("zoom on the marker: {!r}".format(exc))
        except Exception:
            pass
        return False


def filter_for_marker(state, marker, force=False):
    """Filter the Photos pane to the photos that see `marker`."""
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
                journal.write("{}: {} photo(s)".format(label, len(cameras)))
            else:
                journal.write(
                    "{}: Photos filter refused ({})".format(label, message)
                )
        else:
            msapi.reset_photo_filter()
            journal.write(
                "{}: no photo, {}".format(label, explain_diagnostic(diagnostic))
            )
            if diagnostic.get("error"):
                journal.write("{}: computation error ({})".format(
                    label, diagnostic["error"]
                ))
        panel_call(state, "update_active_card")
        _show_marker(state, chunk, marker, cameras)
    except Exception as exc:
        try:
            journal.write("photos filter: {!r}".format(exc))
        except Exception:
            pass


def _show_marker(state, chunk, marker, cameras):
    """Show the chosen marker: zoom on the open photo, otherwise open the best one.

    A click on a marker is always a zoom request, even if this (photo, marker)
    pair has already been zoomed: the operator is asking for it again.
    If the point does not fall inside the open photo, the first photo of the
    list without a projection of the marker is opened (failing that, the first of the list).
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
        journal.write("{}: photo opened {}".format(msapi.marker_label(marker), message))
        zoom_on_marker(state, marker, target, force=True)
    else:
        journal.write("{}: opening of the photo refused ({})".format(
            msapi.marker_label(marker), message))


def reset_filter(state):
    """Remove the Photos pane filter."""
    try:
        msapi.reset_photo_filter()
        state.last_filter_key = None
        state.last_photo_count = 0
        state.last_cameras = []
        journal.write("Photos filter removed")
    except Exception as exc:
        try:
            journal.write("filter removal: {!r}".format(exc))
        except Exception:
            pass


def _keys(cameras):
    return [msapi.camera_key(cam) for cam in cameras]


def _recompute_candidates(state, chunk, marker):
    """Recompute the ordered list of the marker's photos and store it.

    The Photos pane filter is only touched if the list changed: after
    a placement the marker may have a triangulated position, so other
    photos see it.
    """
    cameras, diagnostic = msapi.cameras_seeing_marker(chunk, marker)
    previous = _keys(state.last_cameras)
    state.last_cameras = list(cameras)
    state.last_diagnostic = diagnostic
    state.last_photo_count = len(cameras)
    if cameras and _keys(cameras) != previous:
        ok, message = msapi.set_photo_filter(cameras)
        if not ok:
            journal.write("Photos filter refused ({})".format(message))
    return cameras


def next_unpinned(cameras, current_key, pinned_keys):
    """First photo of `cameras` after the current photo without a projection.

    Returns (camera or None, photos_without_projection_remain).
    If the current photo is not in the list, start from the beginning.
    """
    keys = _keys(cameras)
    start = keys.index(current_key) + 1 if current_key in keys else 0
    remaining = [cam for cam, key in zip(cameras, keys) if key not in pinned_keys]
    for cam, key in zip(cameras[start:], keys[start:]):
        if key not in pinned_keys:
            return cam, True
    return None, bool(remaining)


def cancel_advance():
    """Cancel the move to the next photo still pending, if there is one."""
    _ADVANCE_TOKEN[0] += 1


def _same_marker(state, marker):
    """Is the current marker still `marker`?

    The labels are compared: the synchronisation with the Reference pane
    rereads the marker and creates a new Python object for it, so `is` would fail
    every other time and the move to the next photo would stop silently.
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


# New attempts when the photo view does not yet say which photo it shows.
ADVANCE_RETRIES = 3
ADVANCE_RETRY_MS = 200


def _advance(state, marker, origin_key, token=None, attempt=0):
    try:
        if token is not None and token != _ADVANCE_TOKEN[0]:
            # Replaced by a more recent marking or cancelled: normal case, no noise.
            return
        if not state.enabled:
            return
        if not _same_marker(state, marker):
            journal.write("next photo abandoned: the current marker changed")
            return
        current = msapi.current_camera()
        if current is None:
            if attempt < ADVANCE_RETRIES:
                QtCore.QTimer.singleShot(
                    ADVANCE_RETRY_MS,
                    lambda: _advance(state, marker, origin_key, token, attempt + 1),
                )
            else:
                journal.write("next photo abandoned: the photo view does not respond")
            return
        if msapi.camera_key(current) != origin_key:
            # The operator already changed photo: we do not force their hand.
            journal.write("next photo abandoned: you changed photo")
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
                journal.write("{}: no more next photo without a projection".format(label))
            else:
                journal.write("{}: marker marked on all photos".format(label))
        else:
            ok, message = msapi.open_photo(target)
            if ok:
                journal.write("{}: next photo {}".format(label, message))
                # Photo just opened: the operator cannot have zoomed it.
                zoom_on_marker(state, marker, target, force=True)
            else:
                journal.write("{}: next photo refused ({})".format(label, message))
        state.last_photo_count = len(state.last_cameras)
        panel_call(state, "update_active_card")
    except Exception as exc:
        try:
            journal.write("next photo: {!r}".format(exc))
        except Exception:
            pass


def advance_after_place(state):
    """After a successful placement, open the next photo without a projection of the marker.

    No option: it is always active. The opening goes through the photo view
    API (msapi.open_photo), not through a key press.
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
            journal.write("advance after marking: {!r}".format(exc))
        except Exception:
            pass
