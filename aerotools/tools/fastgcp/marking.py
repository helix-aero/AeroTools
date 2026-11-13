"""Placement of a marker projection by a click in the photo view.

An event filter is installed on the Qt application. On the triggering click
(plain left click, or Shift + left click) it asks `place_at` to place the
projection of the current marker under the cursor, overwriting the one the
marker already had on this photo. The event is only consumed if the
projection was placed: otherwise Metashape keeps control (rotation, panning,
selection). Ctrl/Alt + click, right click and wheel are never intercepted.

The same path serves the keyboard shortcut (place_at_cursor).
"""

import time
import traceback

from ... import i18n, journal, msapi
from ...qt import QtCore, cursor_pos, event_global_pos, modifiers
from . import dataset, detection, history, photos, snap

# True: a plain left click places the point, Shift + click too. False: only
# Shift + click places it (former behaviour).
PLAIN_CLICK_PLACES = True

# Two calls of the filter for the same click (the refused event goes up the
# parents, each one going through the filter again) must only log once.
_DEDUP_WINDOW_S = 0.25


# --------------------------------------------------------------------------
# Placement
# --------------------------------------------------------------------------

_REFUSAL_REPEAT_S = 5.0
_last_refusal = [None, 0.0]


def _refuse(message):
    """Say why a click was not used. The same reason repeated within a few seconds (plain clicks on a photo with no current
    marker, for example) is written once: the journal must not grow with every click."""
    now = time.time()
    if _last_refusal[0] != message or now - _last_refusal[1] > _REFUSAL_REPEAT_S:
        journal.write("Marking ignored: " + message)
    _last_refusal[0], _last_refusal[1] = message, now
    return False


def _snapped(state, camera, label, x, y):
    """Position to place: the click snapped onto the centre of the target if there is one
    very close, otherwise the click itself. Never raises."""
    try:
        limit = getattr(state, "snap_px", 0.0)
        if limit <= 0:
            return x, y
        result = snap.snap_click(camera, x, y, limit)
        if result[0] is None:
            # A single line per photo would not be more useful: the reason for the
            # missing snap helps to understand a badly recognised target.
            journal.write("{}: click kept as is ({})".format(label, result[1]))
            return x, y
        nx, ny, shift = result
        journal.write("{}: snapped by {:.1f} px onto the target centre".format(label, shift))
        return nx, ny
    except Exception as exc:
        try:
            journal.write("snapping: {!r}".format(exc))
        except Exception:
            pass
        return x, y


def place_at(state, global_pos, over_photo=False):
    """Place the projection of the current marker under `global_pos`.

    Returns True only if the projection was placed. Each refusal leaves
    a short line in the journal. `over_photo`: the caller (the click filter) has just checked that the cursor is over a photo.
    """
    if not state.enabled:
        return _refuse("tool disabled")

    label = state.active_label()
    if label is None:
        return _refuse("no current marker")
    marker = state.active_marker

    if not over_photo and not msapi.cursor_over_photo_view(global_pos):
        return _refuse("cursor outside a photo ({})".format(
            msapi.describe_position(global_pos)
        ))

    chunk = msapi.chunk()
    if chunk is None:
        return _refuse("no active chunk")
    try:
        view = msapi.photo_view()
        camera = view.camera if view is not None else None
    except Exception:
        camera = None
    if camera is None:
        return _refuse("no photo open")
    if not msapi.camera_in_chunk(chunk, camera):
        return _refuse("the open photo does not belong to the active chunk")

    coords, reason = msapi.image_coords_at(global_pos)
    if coords is None:
        return _refuse(reason or "unreadable position")
    click_x, click_y = coords
    x, y = _snapped(state, camera, label, click_x, click_y)

    camera_name = msapi.camera_label(camera)

    # The history is written BEFORE the placement: it keeps the previous projection.
    entry = history.push(
        marker, camera, msapi.get_projection(marker, camera),
        chunk_key=msapi.chunk_key(chunk), label=label, camera_name=camera_name,
    )
    ok, message = msapi.set_projection(marker, camera, x, y)
    if not ok:
        history.discard(entry)
        journal.write("Failed to place {}: {}".format(label, message))
        return False

    journal.write("{} placed at ({:.0f}, {:.0f}) on {}".format(
        label, x, y, camera_name
    ))

    # Follow-ups of the marking: none of them may call the placement into question.
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
            "x_click": click_x,
            "y_click": click_y,
            "source": "manual",
        }
        written = dataset.record_pin(state, record)
        detection.notify_pin(written if written is not None else record)
    except Exception as exc:
        journal.write("marking follow-ups: {!r}".format(exc))
    photos.advance_after_place(state)
    photos.panel_call(state, "update_active_card")
    photos.panel_call(state, "refresh_errors")
    return True


def place_at_cursor(state):
    """Ctrl+Shift+M shortcut: place under the cursor."""
    if not state.enabled:
        return
    try:
        place_at(state, cursor_pos())
    except Exception as exc:
        journal.write("marking at the cursor: {!r}".format(exc))


# --------------------------------------------------------------------------
# Undo
# --------------------------------------------------------------------------

def _undo_status(state, text):
    photos.panel_call(state, "set_status", text)


def undo_last(state):
    """Undo the last marking placed by FastGCP.

    Restores the projection the marker had before on this photo, or removes it
    if it had none; reopens the photo, makes the marker the current
    marker again and refreshes the panel. Returns True if something was
    undone. Never raises.
    """
    try:
        return _undo_last(state)
    except Exception as exc:
        try:
            journal.write("undo: {!r}".format(exc))
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
            journal.write("Undo: nothing to undo")
            return False
        if chunk is None or (entry.chunk_key is not None and entry.chunk_key != key):
            # The chunk has changed since: these markings can no longer be reached.
            history.clear()
            journal.write("Undo: history of another chunk, emptied")
            return False
        if entry.is_valid() and msapi.camera_in_chunk(chunk, entry.camera):
            break
        journal.write("Undo: entry ignored (marker or photo gone)")

    marker, camera = entry.marker, entry.camera
    label = msapi.marker_label(marker) or entry.label
    camera_name = msapi.camera_label(camera) or entry.camera_name

    after = msapi.get_projection(marker, camera)          # what Redo will put back
    previous = entry.previous
    if previous is None:
        ok, message = msapi.remove_projection(marker, camera)
        what = "projection removed"
    else:
        ok, message = msapi.set_projection(marker, camera, previous[0], previous[1], previous[2])
        what = "previous position restored"
    if not ok:
        journal.write("Failed to undo {} on {}: {}".format(
            label, camera_name, message))
        _undo_status(state, i18n.t("undo.failed"))
        return False

    # A next photo scheduled by the undone marking no longer applies.
    history.push_redo(marker, camera, after, chunk_key=entry.chunk_key, label=label, camera_name=camera_name)
    _show_result(state, marker, camera, label)
    _undo_status(state, i18n.t("undo.done", label=label))
    journal.write("Undone: {} on {} ({})".format(label, camera_name, what))
    return True


def _show_result(state, marker, camera, label):
    """After Undo or Redo: photo reopened, zoom on the point, panel reread."""
    photos.cancel_advance()
    state.active_marker = marker
    if getattr(state, "panel", None) is not None:
        photos.panel_call(state, "set_active_marker_by_label", label)
    already_open = msapi.camera_key(msapi.current_camera()) == msapi.camera_key(camera)
    msapi.open_photo(camera)
    # Photo reopened: zoom on the point. Photo already on screen: the operator's
    # zoom is left alone.
    photos.zoom_on_marker(state, marker, camera, force=not already_open)
    photos.panel_call(state, "update_active_card")
    photos.panel_call(state, "refresh_errors")


def redo_last(state):
    """Place the last marking undone by Undo again. True if done. Never raises."""
    try:
        return _redo_last(state)
    except Exception as exc:
        try:
            journal.write("redo: {!r}".format(exc))
        except Exception:
            pass
        return False


def _redo_last(state):
    if not state.enabled:
        return False
    chunk = msapi.chunk()
    key = msapi.chunk_key(chunk)
    while True:
        entry = history.pop_redo()
        if entry is None:
            _undo_status(state, i18n.t("redo.nothing"))
            return False
        if chunk is None or (entry.chunk_key is not None and entry.chunk_key != key):
            history.clear()
            return False
        if entry.is_valid() and msapi.camera_in_chunk(chunk, entry.camera):
            break
    marker, camera = entry.marker, entry.camera
    label = msapi.marker_label(marker) or entry.label
    camera_name = msapi.camera_label(camera) or entry.camera_name
    before = msapi.get_projection(marker, camera)
    undo = history.push(marker, camera, before, chunk_key=entry.chunk_key, label=label,
                        camera_name=camera_name, keep_redo=True)
    after = entry.previous
    if after is None:
        ok, message = msapi.remove_projection(marker, camera)
    else:
        ok, message = msapi.set_projection(marker, camera, after[0], after[1], after[2])
    if not ok:
        history.discard(undo)
        journal.write("Failed to redo {} on {}: {}".format(label, camera_name, message))
        _undo_status(state, i18n.t("redo.failed"))
        return False
    _show_result(state, marker, camera, label)
    _undo_status(state, i18n.t("redo.done", label=label))
    journal.write("Redone: {} on {}".format(label, camera_name))
    return True


# --------------------------------------------------------------------------
# Event filter
# --------------------------------------------------------------------------

class ClickFilter(QtCore.QObject):
    """Intercepts the triggering left click and places the projection."""

    def __init__(self, state, parent=None):
        super(ClickFilter, self).__init__(parent)
        self._state = state
        self._busy = False
        self._error_logged = False
        self._last_refused = None

    def _is_trigger(self, ev):
        """Left click, without Ctrl or Alt, with or without Shift depending on PLAIN_CLICK_PLACES."""
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
            # Click outside the photo view: say nothing and let Metashape act.
            if not msapi.cursor_over_photo_view(pos):
                return False
            if self._already_refused(ev, pos):
                return False
            self._busy = True
            try:
                return bool(place_at(state, pos, over_photo=True))
            finally:
                self._busy = False
        except Exception as exc:
            self._busy = False
            if not self._error_logged:
                self._error_logged = True
                try:
                    journal.write("click filter: {!r} | {}".format(exc, " / ".join(l.strip() for l in traceback.format_exc().strip().splitlines()[-4:])))
                except Exception:
                    pass
            return False


def install_filter(state):
    """Install the click filter on the Qt application."""
    remove_filter(state)
    app = QtCore.QCoreApplication.instance()
    if app is None:
        journal.write("click filter: Qt application not found")
        return None
    click_filter = ClickFilter(state)
    app.installEventFilter(click_filter)
    state.click_filter = click_filter
    return click_filter


def remove_filter(state):
    """Remove the click filter. No error if there is none."""
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
