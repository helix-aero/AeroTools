"""Wrapper around the Metashape calls used by AeroTools.

Everything that touches the Metashape API is here: the tools never call
`Metashape.*` directly. Two reasons: the photo view APIs are not
documented and move from one version to the next, and Python references to
document objects become invalid (RuntimeError) as soon as the chunk
changes. Each function is therefore defensive and returns a neutral value
rather than raising.

Metashape APIs used:
  Metashape.app.document / .photo_view / .photos_pane / .update()
  Metashape.app.addMenuItem(label, function, shortcut=...)
  chunk.cameras, chunk.markers, chunk.crs, chunk.transform
  marker.projections[camera] = Metashape.Marker.Projection(Vector, True)
  del marker.projections[camera], marker.position, marker.reference.location
  chunk.crs.project / unproject / localframe, chunk.transform.matrix.mulp
  camera.project(point), camera.transform, camera.sensor.width / .height
  photo_view.camera / .center / .scale / .width / .height / .open(camera)
  (center: image pixel at the middle of the view; scale: screen pixels per image
  pixel. Semantics inferred from image_coords_at and from older scripts, to be
  confirmed with diag_zoom.py)
"""

import math

import Metashape

from . import journal
from .qt import QtCore, QtWidgets

# Beyond this distance, a photo is not offered for a marker that has no
# projection yet (otherwise a whole flight "sees" the point).
MAX_FILTER_DIST_M = 400.0

# Tabs of the central view that are not photos.
NON_PHOTO_KEYWORDS = (
    "model", "ortho", "dem", "point cloud", "dense cloud", "depth map",
    "tiled", "console", "reference", "workspace", "animation", "report",
)


# --------------------------------------------------------------------------
# Document
# --------------------------------------------------------------------------

def document():
    try:
        return Metashape.app.document
    except Exception:
        return None


def chunk():
    doc = document()
    try:
        return doc.chunk if doc is not None else None
    except Exception:
        return None


def chunk_key(chk):
    try:
        return chk.key if chk is not None else None
    except Exception:
        return None


def chunk_label(chk):
    try:
        return chk.label if chk is not None else ""
    except Exception:
        return ""


def photo_view():
    try:
        return Metashape.app.photo_view
    except Exception:
        return None


def app_version():
    """Metashape version, or an empty string if it is not accessible."""
    try:
        return str(Metashape.app.version)
    except Exception:
        return ""


def refresh():
    try:
        Metashape.app.update()
    except Exception:
        pass


# The Python wrapper of the main window must stay alive: as soon as it is
# released (local variable of a finished function), PySide2 invalidates the wrappers
# of all its children ("Internal C++ object already deleted" while the
# widgets still exist), and the click in the photo view is no longer recognized.
_MAIN_WINDOW = None


def main_window():
    global _MAIN_WINDOW
    if _MAIN_WINDOW is not None:
        try:
            _MAIN_WINDOW.objectName()
            return _MAIN_WINDOW
        except RuntimeError:
            _MAIN_WINDOW = None
    instance = QtWidgets.QApplication.instance()
    if instance is None:
        return None
    for widget in instance.topLevelWidgets():
        if isinstance(widget, QtWidgets.QMainWindow):
            _MAIN_WINDOW = widget
            return widget
    return None


# --------------------------------------------------------------------------
# Markers
# --------------------------------------------------------------------------

def markers(chk):
    try:
        return list(chk.markers) if chk is not None else []
    except Exception:
        return []


def selected_markers(chk):
    out = []
    for marker in markers(chk):
        try:
            if marker.selected:
                out.append(marker)
        except RuntimeError:
            continue
    return out


def select_marker(chk, marker):
    """Select only this marker in Metashape (Reference pane). Never raises.

    Metashape recenters on ITS selected marker at each photo change
    (Page up / down): it must be FastGCP's current marker, otherwise the view
    goes to the old one. Returns True if the selection is done.
    """
    try:
        for other in markers(chk):
            try:
                wanted = other == marker
                if bool(other.selected) != wanted:
                    other.selected = wanted
            except RuntimeError:
                continue
        return True
    except Exception:
        return False


def marker_label(marker):
    try:
        return marker.label
    except Exception:
        return ""


def projection_cameras(marker, without_proposals=False):
    """Cameras where this marker already has a projection.

    `without_proposals=True` leaves out the projections proposed by FastGCP's
    AI detection (see mark_proposal) and only those: the other
    projections of the project (placed by hand or computed by Metashape)
    stay.

    `marker.projections` is not iterated the same way depending on the version:
    try direct iteration, then keys(), then items().
    """
    try:
        projections = marker.projections
    except Exception:
        return []
    raw = None
    for how in ("iter", "keys", "items"):
        try:
            if how == "iter":
                raw = [cam for cam in projections]
            elif how == "keys":
                raw = list(projections.keys())
            else:
                raw = [cam for cam, _proj in projections.items()]
            break
        except Exception:
            continue
    if not raw:
        return []
    chk = chunk()
    cameras = []
    for cam in raw:
        try:
            if isinstance(cam, int) and chk is not None:
                cam = chk.cameras[cam]
            if cam is not None:
                cameras.append(cam)
        except Exception:
            continue
    if without_proposals:
        cameras = [cam for cam in cameras if not is_proposal(marker, cam)]
    return cameras


# Projections placed by the AI detection and not yet validated: (chunk, marker,
# photo). Metashape does not tell a proposal from a blue point it computed
# itself: it is remembered here, so as not to count them as placed.
_PROPOSALS = set()


def _proposal_key(marker, camera):
    return (chunk_key(chunk()), marker_label(marker), camera_key(camera))


def mark_proposal(marker, camera):
    _PROPOSALS.add(_proposal_key(marker, camera))


def unmark_proposal(marker, camera):
    _PROPOSALS.discard(_proposal_key(marker, camera))


def is_proposal(marker, camera):
    """Is this projection an AI proposal that is not yet validated?

    As soon as the operator replaces it with a pick (pinned), it no longer is one.
    """
    if not _PROPOSALS:
        return False
    key = _proposal_key(marker, camera)
    if key not in _PROPOSALS:
        return False
    projection = get_projection(marker, camera)
    if projection is None or projection[2]:
        _PROPOSALS.discard(key)
        return False
    return True


def projection_count(marker):
    """Number of photos with a projection, AI proposals excluded."""
    return len(projection_cameras(marker, without_proposals=True))


def marker_point(chk, marker):
    """Position of the marker in the chunk's internal coordinates, or None.

    If the marker is not triangulated yet, start from its reference
    coordinates: this is what makes it possible to filter the photos of a GCP never
    placed. This reference is expressed in the markers' coordinate system (`marker_crs`),
    which may differ from the chunk's (e.g. chunk in WGS 84, markers in meters).
    """
    try:
        if marker.position is not None:
            return marker.position
    except Exception:
        pass
    try:
        location = marker.reference.location
        crs = getattr(chk, "marker_crs", None) or chk.crs
        if location is None or crs is None or chk.transform.matrix is None:
            return None
        ecef = crs.unproject(location)
        return chk.transform.matrix.inv().mulp(ecef)
    except Exception:
        return None


def _components(vector):
    """(x, y, z) of a Metashape vector, or None."""
    try:
        return float(vector.x), float(vector.y), float(vector.z)
    except Exception:
        try:
            return float(vector[0]), float(vector[1]), float(vector[2])
        except Exception:
            return None


def _finite(values):
    return values is not None and all(
        isinstance(v, float) and math.isfinite(v) for v in values
    )


# One diagnostic line per chunk and per cause (see _log_once).
_DIAG_LOGGED = set()

# GRS80 / WGS84 ellipsoid, to convert degrees to local meters.
_ELL_A = 6378137.0
_ELL_E2 = 0.00669437999014


def _log_once(chk, kind, text):
    """Write a line to the journal only once per chunk (and per cause)."""
    key = (chunk_key(chk), kind)
    if key in _DIAG_LOGGED:
        return
    _DIAG_LOGGED.add(key)
    try:
        journal.write("Table errors: " + text)
    except Exception:
        pass


def _type_name(value):
    try:
        return type(value).__name__
    except Exception:
        return "?"


def crs_kind(crs):
    """"projected", "geographic", "geocentric" or None if unknown.

    Read from the WKT (a compound CRS with a geoid contains PROJCS), then from
    the proj4 string.
    """
    try:
        wkt = str(crs.wkt).upper()
    except Exception:
        wkt = ""
    if "PROJCS" in wkt or "PROJCRS" in wkt:
        return "projected"
    if "GEOGCS" in wkt or "GEOGCRS" in wkt:
        return "geographic"
    if "GEOCCS" in wkt or "GEODCRS" in wkt:
        return "geocentric"
    try:
        proj4 = str(crs.proj4).lower()
    except Exception:
        proj4 = ""
    if "+proj=longlat" in proj4 or "+proj=latlong" in proj4:
        return "geographic"
    if "+proj=geocent" in proj4:
        return "geocentric"
    if "+proj=" in proj4:
        return "projected"
    return None


def marker_errors(chk, marker):
    """Marker error in meters: estimated position minus reference position.

    Returns {"x", "y", "z", "total"}, each value being a float or None
    (no reference coordinate, marker not estimated, computation impossible).
    Never raises: the panel and the PDF report use it as is.
    When a marker has a reference and a position but the computation
    fails, a journal line (one per chunk) gives the exact cause.

    Paths tried in order, the first finite result wins:
      A. coordinate system defined: both positions in geocentric
         (estimated through the chunk matrix, reference through crs.unproject), difference
         rotated into the point's local frame (crs.localframe): east, north,
         up in meters, like the Reference table (up to the meridian
         convergence, negligible at the centimeter level);
      B. projected CRS: crs.project(estimated position) minus the reference,
         already in meters;
      C. geographic CRS: difference in degrees converted to local meters;
      without a coordinate system: difference in the chunk frame, scaled
      to metric (chunk.transform.scale).
    """
    empty = {"x": None, "y": None, "z": None, "total": None}
    try:
        position = marker.position
        reference = marker.reference.location
    except Exception as exc:
        _log_once(chk, "read", "cannot read the marker: {!r}".format(exc))
        return empty
    if reference is None:
        return empty
    label = marker_label(marker)
    if position is None:
        _log_once(chk, "not_estimated",
                  "{} has a reference but no estimated position (marker not "
                  "triangulated, normal until it is placed)".format(label))
        return empty
    notes = []
    try:
        values = _error_components(chk, position, reference, notes)
    except Exception as exc:
        notes.append("unexpected exception {!r}".format(exc))
        values = None
    if not _finite(values):
        _log_once(chk, "failure", _diagnostic(chk, marker, position, reference, notes))
        return empty
    x, y, z = values
    return {"x": x, "y": y, "z": z, "total": math.sqrt(x * x + y * y + z * z)}


def marker_enabled(marker):
    """True if the marker is used as a control point (box ticked in Reference), None if unreadable."""
    try:
        return bool(marker.reference.enabled)
    except Exception:
        return None


def set_marker_enabled(marker, value):
    """Tick or untick the marker in Reference. Returns True if done."""
    try:
        marker.reference.enabled = bool(value)
        return True
    except Exception:
        return False


def cameras_georeferenced(chk):
    """(cameras whose reference position is used, aligned cameras): PPK / RTK if most of them are."""
    used = total = 0
    for cam in _cameras(chk):
        if _camera_transform(cam) is None:
            continue
        total += 1
        try:
            if cam.reference.enabled and cam.reference.location is not None:
                used += 1
        except Exception:
            continue
    return used, total


def marker_frame_points(chk):
    """Markers with an estimated position AND a reference, in the same local frame in meters.

    Returns ([{label, marker, n, est, ref}], why). `est`: estimated position (with the chunk
    matrix), `ref`: reference position, both as east / north / up around the same point
    (path A of `marker_errors`, with the geoid of the coordinate system); without a coordinate
    system, the chunk frame in meters. `why` is only filled when the list is empty."""
    try:
        crs = getattr(chk, "marker_crs", None) or chk.crs
        matrix = chk.transform.matrix
    except Exception as exc:
        return [], "cannot read the chunk: {!r}".format(exc)
    items = []
    for marker in markers(chk):
        try:
            position, reference = marker.position, marker.reference.location
        except Exception:
            continue
        if position is not None and reference is not None:
            items.append((marker, position, reference))
    if not items:
        return [], "no marker with an estimated position and a reference"
    if matrix is None:
        return [], "the chunk has no transformation matrix"
    try:
        scale = float(chk.transform.scale or 1.0)
    except Exception:
        scale = 1.0

    def local_path_a():
        origin = crs.unproject(items[0][2])
        local = crs.localframe(origin)
        return (lambda position, reference: (_components(local.mulp(matrix.mulp(position))),
                                              _components(local.mulp(crs.unproject(reference)))))

    def projected_path():
        return (lambda position, reference: (_components(crs.project(matrix.mulp(position))), _components(reference)))

    def chunk_path():
        return (lambda position, reference: (_components(matrix.mulp(position)), _components(reference)))

    attempts = [("local frame", local_path_a), ("projected frame", projected_path)] if crs is not None else []
    attempts.append(("chunk frame", chunk_path))
    notes = []
    for name, build in attempts:
        try:
            convert = build()
            points = []
            for marker, position, reference in items:
                est, ref = convert(position, reference)
                if not (_finite(est) and _finite(ref)):
                    raise ValueError("non-finite value")
                points.append({"label": marker_label(marker), "marker": marker, "n": projection_count(marker),
                               "est": est, "ref": ref})
            return points, ""
        except Exception as exc:
            notes.append("{}: {!r}".format(name, exc))
    return [], "; ".join(notes)


def _diagnostic(chk, marker, position, reference, notes):
    """Single sentence saying why the computation failed."""
    parts = ["computation impossible for {} (chunk {})".format(
        marker_label(marker), chunk_label(chk) or "?")]
    try:
        crs = getattr(chk, "marker_crs", None) or chk.crs
        parts.append("crs={} type={}".format(getattr(crs, "name", crs), crs_kind(crs)))
    except Exception as exc:
        parts.append("crs unreadable {!r}".format(exc))
    try:
        enabled = marker.reference.enabled
    except Exception:
        enabled = "?"
    parts.append("reference.location={} enabled={}".format(
        _components(reference), enabled))
    parts.append("position={}".format(_components(position)))
    try:
        matrix = chk.transform.matrix
        parts.append("matrix={}".format(_type_name(matrix)))
        try:
            parts.append("mulp->{}".format(_type_name(matrix.mulp(position))))
        except Exception as exc:
            parts.append("mulp failed {!r}".format(exc))
        try:
            parts.append("localframe->{}".format(
                _type_name(chk.crs.localframe(matrix.mulp(position)))))
        except Exception as exc:
            parts.append("localframe failed {!r}".format(exc))
    except Exception as exc:
        parts.append("matrix unreadable {!r}".format(exc))
    parts.extend(notes)
    return "; ".join(parts)


def _delta_vector(values):
    return Metashape.Vector(list(values))


def _rotate(local, delta, notes):
    """Apply the `local` matrix to the offset `delta` (3-vector). Several
    forms, depending on what the matrix accepts (4x4 homogeneous, 3x3, rows)."""
    attempts = (
        ("mulv", lambda: local.mulv(delta)),
        ("product", lambda: local * delta),
        ("rotation", lambda: local.rotation() * delta),
    )
    for name, call in attempts:
        try:
            values = _components(call())
            if _finite(values):
                return values
        except Exception as exc:
            notes.append("localframe.{} failed {!r}".format(name, exc))
    try:
        d = _components(delta)
        rows = []
        for index in range(3):
            row = local.row(index)
            rows.append(sum(float(row[k]) * d[k] for k in range(3)))
        values = tuple(rows)
        if _finite(values):
            return values
    except Exception as exc:
        notes.append("localframe.rows failed {!r}".format(exc))
    return None


def _error_components(chk, position, reference, notes):
    ref = _components(reference)
    if ref is None:
        notes.append("reference unreadable")
        return None
    # The reference is expressed in the markers' coordinate system, which may differ
    # from the chunk's (e.g. chunk in WGS 84, markers in meters).
    try:
        crs = getattr(chk, "marker_crs", None) or chk.crs
    except Exception:
        crs = None
    try:
        matrix = chk.transform.matrix
    except Exception:
        matrix = None
    try:
        scale = float(chk.transform.scale or 1.0)
    except Exception:
        scale = 1.0

    if crs is None:
        # Local frame: the reference is in the chunk frame, in meters
        # after the chunk similarity.
        if matrix is not None:
            try:
                estimated = _components(matrix.mulp(position))
                if estimated is not None:
                    return tuple(e - r for e, r in zip(estimated, ref))
            except Exception as exc:
                notes.append("local: mulp failed {!r}".format(exc))
        estimated = _components(position)
        if estimated is None:
            return None
        return tuple((e - r) * scale for e, r in zip(estimated, ref))

    if matrix is None:
        notes.append("chunk.transform.matrix is None")
        return None
    try:
        world = matrix.mulp(position)
    except Exception as exc:
        notes.append("matrix.mulp failed {!r}".format(exc))
        return None
    world_c = _components(world)
    if world_c is None:
        notes.append("chunk position unreadable")
        return None
    kind = crs_kind(crs)

    # A. geocentric rotated into the local frame.
    try:
        ref_ecef = _components(crs.unproject(reference))
        if ref_ecef is None:
            raise ValueError("unproject returned nothing")
        delta = _delta_vector(w - r for w, r in zip(world_c, ref_ecef))
        local = crs.localframe(world)
        values = _rotate(local, delta, notes)
        if _finite(values):
            return values
    except Exception as exc:
        notes.append("path A failed {!r}".format(exc))

    # B and C start from the estimated position expressed in the CRS system.
    try:
        estimated = _components(crs.project(world))
    except Exception as exc:
        notes.append("crs.project failed {!r}".format(exc))
        return None
    if estimated is None:
        notes.append("crs.project returned nothing")
        return None
    looks_degrees = abs(ref[0]) <= 180.0 and abs(ref[1]) <= 90.0

    # B. Projected CRS: the difference is already in meters.
    if kind == "projected" or (kind is None and not looks_degrees):
        values = tuple(e - r for e, r in zip(estimated, ref))
        if _finite(values):
            return values
    # C. Geographic CRS: degrees to local meters.
    if kind == "geographic" or (kind is None and looks_degrees):
        lat = math.radians(ref[1])
        sin2 = math.sin(lat) ** 2
        root = math.sqrt(1.0 - _ELL_E2 * sin2)
        prime = _ELL_A / root
        meridian = _ELL_A * (1.0 - _ELL_E2) / (root ** 3)
        height = ref[2]
        east = math.radians(estimated[0] - ref[0]) * (prime + height) * math.cos(lat)
        north = math.radians(estimated[1] - ref[1]) * (meridian + height)
        values = (east, north, estimated[2] - ref[2])
        if _finite(values):
            return values
    notes.append("no applicable path (crs of type {})".format(kind))
    return None


def cameras_seeing_marker(chk, marker, max_dist_m=MAX_FILTER_DIST_M):
    """Photos to offer for this marker, from the nearest to the farthest.

    First the projections already placed, then the aligned photos where the point
    falls inside the image, in front of the camera and within `max_dist_m`.
    Returns (cameras, diagnostic).
    """
    diagnostic = {}
    cameras = projection_cameras(marker)
    seen = set()
    for cam in cameras:
        try:
            seen.add(cam.key)
        except Exception:
            continue
    diagnostic["projections"] = len(cameras)

    point = marker_point(chk, marker)
    diagnostic["point"] = point is not None
    diagnostic["aligned"] = sum(
        1 for cam in _cameras(chk) if _camera_transform(cam) is not None
    )
    diagnostic["near"] = diagnostic["in_front"] = diagnostic["in_image"] = 0
    if point is None:
        return cameras, diagnostic

    scale = 1.0
    try:
        scale = chk.transform.scale or 1.0
    except Exception:
        pass
    limit = max_dist_m / scale

    extra = []
    for cam in _cameras(chk):
        try:
            if cam.key in seen or cam.transform is None or not cam.enabled:
                continue
            distance = (cam.center - point).norm()
            if distance > limit:
                continue
            diagnostic["near"] += 1
            local = cam.transform.inv().mulp(point)
            if local.z <= 0:
                continue
            diagnostic["in_front"] += 1
            uv = cam.project(point)
            if uv is None:
                continue
            width, height = cam.sensor.width, cam.sensor.height
            if 0 <= uv.x < width and 0 <= uv.y < height:
                diagnostic["in_image"] += 1
                extra.append((distance, cam))
        except Exception as exc:
            diagnostic["error"] = str(exc)
            continue
    extra.sort(key=lambda item: item[0])
    cameras.extend(cam for _distance, cam in extra)
    return cameras, diagnostic


def _cameras(chk):
    try:
        return list(chk.cameras) if chk is not None else []
    except Exception:
        return []


def _camera_transform(cam):
    try:
        return cam.transform
    except Exception:
        return None


# --------------------------------------------------------------------------
# Photo view
# --------------------------------------------------------------------------

def photo_view_widgets():
    """Qt widgets that have exactly the size of the photo view.

    Metashape does not expose the photo view widget: it is found by its
    size, which the API gives (`photo_view.width` / `.height`), and the
    deepest one in the tree is kept.
    """
    view = photo_view()
    if view is None:
        return []
    try:
        if view.camera is None:
            return []
        width, height = view.width, view.height
    except Exception:
        return []
    window = main_window()
    if window is None:
        return []
    try:
        children = list(window.findChildren(QtWidgets.QWidget))
    except RuntimeError:
        return []
    candidates = []
    for widget in children:
        try:
            if not widget.isVisible():
                continue
            if abs(widget.width() - width) > 2 or abs(widget.height() - height) > 2:
                continue
            depth = 0
            node = widget
            while node is not None:
                depth += 1
                node = node.parent()
            candidates.append((depth, widget))
        except RuntimeError:
            continue
    candidates.sort(key=lambda item: -item[0])
    return [widget for _depth, widget in candidates]


def central_tab_under(global_pos):
    """(text of the active central tab, click inside a dockable pane).

    Metashape 2.2 has no QMdiArea: the central view is a QTabWidget whose
    tabs are Model / Ortho / <photo name>. Walk up from the widget under
    the cursor to that QTabWidget and read the active tab.
    """
    widget = QtWidgets.QApplication.widgetAt(global_pos)
    if widget is None:
        return None, False
    node = widget
    tabs = None
    in_dock = False
    depth = 0
    while node is not None and depth < 60:
        if isinstance(node, QtWidgets.QDockWidget):
            in_dock = True
            break
        if tabs is None and isinstance(node, QtWidgets.QTabWidget):
            tabs = node
        # Some Metashape widgets (the Python console) shadow `parent`
        # with an attribute that is not callable: stop there, without raising.
        try:
            getter = node.parent
            node = getter() if callable(getter) else None
        except RuntimeError:
            node = None
        depth += 1
    if in_dock or tabs is None:
        return None, in_dock
    try:
        return tabs.tabText(tabs.currentIndex()), False
    except RuntimeError:
        return None, False


def _title_is_photo(title, chk):
    if not title:
        return False
    text = title.strip()
    stem = text.rsplit(".", 1)[0]
    # A tab that carries exactly the name of a photo of the chunk is a photo,
    # even if that name contains a word of the blacklist ("DEM_001", "model").
    if chk is not None:
        for cam in _cameras(chk):
            try:
                label = cam.label
            except Exception:
                continue
            if label == text or label == stem:
                return True
    lowered = text.lower()
    for keyword in NON_PHOTO_KEYWORDS:
        if keyword in lowered:
            return False
    # Central tab that is not blacklisted: considered a photo.
    return True


def cursor_over_photo_view(global_pos):
    """True only if the central tab under the cursor is a photo."""
    title, _in_dock = central_tab_under(global_pos)
    if title is None:
        return False
    return _title_is_photo(title, chunk())


def describe_position(global_pos):
    """Short sentence explaining why a click was ignored."""
    title, in_dock = central_tab_under(global_pos)
    if in_dock:
        return "Metashape pane"
    if title:
        return "tab \"{}\"".format(title)
    return "outside the central view"


# --------------------------------------------------------------------------
# Placing a projection
# --------------------------------------------------------------------------

def image_coords_at(global_pos):
    """Image coordinates (sensor pixels) under the cursor, or (None, reason)."""
    view = photo_view()
    if view is None or view.camera is None:
        return None, "no photo open"
    try:
        width, height, scale, center = view.width, view.height, view.scale, view.center
    except Exception:
        return None, "photo view unreadable"

    target = None
    local = None
    for candidate in photo_view_widgets():
        try:
            point = candidate.mapFromGlobal(global_pos)
            if 0 <= point.x() <= width and 0 <= point.y() <= height:
                target = candidate
                local = point
                break
        except RuntimeError:
            continue
    if target is None:
        return None, "cursor outside the photo view"

    # Valid for any zoom: the view is re-read at each click (current center
    # and scale), nothing is remembered or assumed "fitted to the window".
    try:
        scale = float(scale)
        center_x, center_y = float(center.x), float(center.y)
    except Exception:
        return None, "view center or scale unreadable"
    if not (scale > 0.0 and math.isfinite(scale)):
        return None, "view scale unreadable"
    img_x = center_x + (local.x() - width / 2.0) / scale
    img_y = center_y + (local.y() - height / 2.0) / scale
    try:
        sensor_w = view.camera.sensor.width
        sensor_h = view.camera.sensor.height
    except Exception:
        return None, "unknown sensor"
    if img_x < 0 or img_y < 0 or img_x > sensor_w or img_y > sensor_h:
        return None, "pixel outside the image ({:.0f}, {:.0f})".format(img_x, img_y)
    return (img_x, img_y), None


def set_projection(marker, camera, img_x, img_y, pinned=True):
    """Place the marker's projection on this camera. Returns (ok, message).

    `pinned=True`: operator pick (green flag in Metashape).
    `pinned=False`: projection proposed by an automatic detection (AIGCP),
    which Metashape shows in blue and may readjust.
    """
    try:
        projection = Metashape.Marker.Projection(
            Metashape.Vector([img_x, img_y]), bool(pinned)
        )
        marker.projections[camera] = projection
        refresh()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def get_projection(marker, camera):
    """Current projection of the marker on this photo: (x, y, pinned), or None.

    Used to remember the previous state so that it can be undone. Never raises.
    """
    try:
        projection = marker.projections[camera]
    except Exception:
        return None
    if projection is None:
        return None
    try:
        coord = projection.coord
        try:
            x, y = float(coord.x), float(coord.y)
        except AttributeError:
            x, y = float(coord[0]), float(coord[1])
        return (x, y, bool(projection.pinned))
    except Exception:
        return None


def remove_projection(marker, camera, update=True):
    """Remove the marker's projection on this photo. Returns (ok, message).

    `update=False`: does not refresh Metashape (the caller does it once for a whole series).
    """
    try:
        try:
            del marker.projections[camera]
        except (TypeError, AttributeError, NotImplementedError):
            marker.projections[camera] = None
        if update:
            refresh()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def remove_all_projections(marker):
    """Remove all the marker's projections (placed, blue, AI proposals).

    Returns the number of projections removed. The marker stays in the chunk. Never raises.
    """
    removed = 0
    for camera in projection_cameras(marker):
        done, _message = remove_projection(marker, camera, update=False)
        removed += 1 if done else 0
    refresh()
    return removed


def remove_marker(chk, marker):
    """Remove the marker from the chunk. Returns (ok, message)."""
    try:
        try:
            chk.remove([marker])
        except TypeError:
            chk.remove(marker)
        refresh()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def camera_in_chunk(chk, camera):
    try:
        return camera.key in {cam.key for cam in _cameras(chk)}
    except Exception:
        return False


def camera_label(camera):
    try:
        return camera.label
    except Exception:
        return ""


def camera_image_path(camera):
    try:
        return camera.photo.path or ""
    except Exception:
        return ""


def camera_size(camera):
    try:
        return int(camera.sensor.width), int(camera.sensor.height)
    except Exception:
        return 0, 0


# --------------------------------------------------------------------------
# Navigation between photos
# --------------------------------------------------------------------------

def current_camera():
    """Camera open in the photo view, or None."""
    view = photo_view()
    try:
        return view.camera if view is not None else None
    except Exception:
        return None


def camera_key(camera):
    try:
        return camera.key
    except Exception:
        return None


def open_photo(camera):
    """Open `camera` in the photo view. Returns (ok, message), never raises."""
    view = photo_view()
    if view is None:
        return False, "photo view unavailable"
    try:
        view.open(camera)
        return True, camera_label(camera)
    except Exception as exc:
        return False, str(exc)


def open_next_camera():
    """Open the next camera of the chunk in the photo view. (ok, message)."""
    view = photo_view()
    chk = chunk()
    if view is None or chk is None or view.camera is None:
        return False, "no photo open"
    cameras = _cameras(chk)
    try:
        index = cameras.index(view.camera)
    except ValueError:
        return False, "current photo not in the chunk"
    if index + 1 >= len(cameras):
        return False, "last photo of the chunk"
    try:
        view.open(cameras[index + 1])
        return True, camera_label(cameras[index + 1])
    except Exception as exc:
        return False, str(exc)


# --------------------------------------------------------------------------
# Photo view zoom
# --------------------------------------------------------------------------
#
# Assumed semantics of the photo view (see image_coords_at, which already uses it,
# and the older scripts of _sources_reference):
#   photo_view.center: image pixel (x, y) displayed at the middle of the view;
#   photo_view.scale : factor, screen pixels per image pixel (16.74 = 1674 %).
# None of this is documented by Agisoft: zoom_to_point re-reads the view
# after changing it and journals once what it read (diag_zoom.py
# gives the truth by hand).

# Image width visible in the view, by default (image pixels).
ZOOM_DEFAULT_WINDOW_PX = 400.0
# Scale when the view width is unknown.
ZOOM_FALLBACK_SCALE = 4.0
# Maximum scale of the automatic zoom.
ZOOM_MAX_SCALE = 16.0
# Delay before the first attempt, then before the second (ms): opening
# a photo with photo_view.open() is not always finished on return.
ZOOM_DELAYS_MS = (60, 150)
# Tolerated difference between the requested center and the re-read center (image pixels) and
# between the requested scale and the re-read scale (relative).
_ZOOM_CENTER_TOL_PX = 2.0
_ZOOM_SCALE_TOL = 0.05

# View already zoomed by the operator (or by Metashape, which recenters on the
# marker by itself while keeping the scale): current scale > fit-to-window x this
# factor. The automatic zoom then only touches the center, never the scale:
# no double zoom, no zoom out. On a photo still "fitted" (first photo),
# the automatic scale applies.
ZOOM_KEEP_FACTOR = 1.5

# The detailed line zoom: center=... is only written once per session.
_ZOOM_LOGGED = [False]


def _schedule(delay_ms, function):
    """Call `function` after `delay_ms` (the Qt loop must be running)."""
    QtCore.QTimer.singleShot(int(delay_ms), function)


def _number(value):
    """Finite float, or None."""
    try:
        number = float(value)
    except Exception:
        return None
    return number if math.isfinite(number) else None


def _xy(value):
    """(x, y) of a Vector (or of a sequence), or None."""
    try:
        return float(value.x), float(value.y)
    except Exception:
        try:
            return float(value[0]), float(value[1])
        except Exception:
            return None


def view_state(view=None):
    """State read from the photo view: dict camera / center / scale / width / height.

    Each attribute is read on its own; a failed read gives None. `center`
    is an (x, y) pair or None, the other values floats or None.
    """
    if view is None:
        view = photo_view()
    state = {"camera": None, "center": None, "scale": None, "width": None,
             "height": None}
    if view is None:
        return state
    try:
        state["camera"] = view.camera
    except Exception:
        pass
    try:
        state["center"] = _xy(view.center)
    except Exception:
        pass
    for name in ("scale", "width", "height"):
        try:
            state[name] = _number(getattr(view, name))
        except Exception:
            pass
    return state


def fit_scale(camera, width, height):
    """Fit-to-window scale for this image, or None."""
    image_w, image_h = camera_size(camera)
    if image_w <= 0 or image_h <= 0 or not width or not height:
        return None
    if width <= 0 or height <= 0:
        return None
    return min(float(width) / image_w, float(height) / image_h)


def zoom_scale(camera=None, window_px=None):
    """Automatic zoom scale: view width / `window_px`.

    Bounded between the scale that fits the image to the window and ZOOM_MAX_SCALE.
    Unknown view width: ZOOM_FALLBACK_SCALE.
    """
    state = view_state()
    width, height = state["width"], state["height"]
    window = _number(window_px)
    if window is None or window <= 0:
        window = ZOOM_DEFAULT_WINDOW_PX
    if width and width > 0:
        scale = width / window
    else:
        scale = ZOOM_FALLBACK_SCALE
    if camera is not None:
        fit = fit_scale(camera, width, height)
        if fit:
            scale = max(scale, fit)
    return min(scale, ZOOM_MAX_SCALE)


def marker_ground_scale(chk, marker, camera):
    """Ground scale (pixels per meter) of the marker on this photo, or None.

    Same convention as the AIGCP training set: focal length in pixels divided
    by the camera-marker distance in meters. Never raises.
    """
    try:
        point = marker_point(chk, marker)
        if point is None or camera.transform is None:
            return None
        scale = chk.transform.scale or 1.0
        focal = float(camera.sensor.calibration.f)
        distance = (camera.center - point).norm() * scale
        if focal <= 0 or not distance > 0:
            return None
        value = focal / distance
        return value if math.isfinite(value) else None
    except Exception:
        return None


def chunk_scale(chk):
    """Meters per internal unit of the chunk (1.0 if unknown)."""
    try:
        return float(chk.transform.scale) or 1.0
    except Exception:
        return 1.0


def pixel_ray(camera, x, y):
    """Ray from the camera towards the pixel (x, y) of the photo.

    Returns (origin, direction) in the chunk's internal coordinates, or None.
    Used to check that detections on several photos intersect at the
    same 3D point. Never raises.
    """
    try:
        direction = camera.sensor.calibration.unproject(Metashape.Vector([x, y]))
        world = camera.transform.mulv(direction)
        origin = _components(camera.center)
        vector = _components(world)
        if origin is None or vector is None or not _finite(origin + vector):
            return None
        return origin, vector
    except Exception:
        return None


def project_point(camera, point):
    """Pixel (x, y) where the 3D point (chunk internal coordinates) falls on this photo, or None."""
    try:
        return _xy(camera.project(Metashape.Vector(list(_components(point)))))
    except Exception:
        return None


def marker_estimate(chk, marker, camera):
    """Projection of the marker's 3D point on this photo: (x, y) or None.

    Unlike marker_anchor, ignores any projection already placed: this is
    where the marker SHOULD be, not where it is. Never raises.
    """
    try:
        image_w, image_h = camera_size(camera)
        transform = _camera_transform(camera)
        point = marker_point(chk, marker)
        if image_w <= 0 or transform is None or point is None:
            return None
        if transform.inv().mulp(point).z <= 0:
            return None
        pixel = _xy(camera.project(point))
        if pixel is None or _number(pixel[0]) is None or _number(pixel[1]) is None:
            return None
        if pixel[0] < 0 or pixel[1] < 0 or pixel[0] > image_w or pixel[1] > image_h:
            return None
        return float(pixel[0]), float(pixel[1])
    except Exception:
        return None


def _accuracy_m(value):
    """Largest component of an accuracy (vector or number), or None."""
    if value is None:
        return None
    try:
        values = _components(value)
        if values is None:
            values = (float(value),) * 3
        return max(values) if _finite(values) else None
    except Exception:
        return None


def marker_reference_info(marker, chk=None):
    """(used as GCP, reference accuracy in m); None for what cannot be read.

    `reference.enabled` true: the marker is a ground control point (GCP); false: check
    point. The accuracy is the marker's, otherwise the chunk's (`marker_location_accuracy`,
    the value that Metashape's Reference table shows). Never raises.
    """
    try:
        reference = marker.reference
    except Exception:
        return None, None
    try:
        used = bool(reference.enabled)
    except Exception:
        used = None
    accuracy = None
    try:
        accuracy = _accuracy_m(reference.accuracy)
    except Exception:
        accuracy = None
    if accuracy is None and chk is not None:
        try:
            accuracy = _accuracy_m(chk.marker_location_accuracy)
        except Exception:
            accuracy = None
    return used, accuracy


def marker_spread_m(chk, marker):
    """Largest offset, in ground meters, between a projection of the marker and the
    reprojection of its 3D point; None if it cannot be told.

    All projections count, placed by hand or computed by Metashape,
    except AI proposals: if they all fall close to the
    reprojection of the 3D point, the marker position is known to within that offset
    on the other photos. None when the marker has fewer than 2 projections or no
    position: it is then unknown how reliable it is.
    Never raises.
    """
    try:
        if marker.position is None:
            return None
        worst, count = 0.0, 0
        for camera in projection_cameras(marker, without_proposals=True):
            projection = get_projection(marker, camera)
            if projection is None:
                continue
            estimate = marker_estimate(chk, marker, camera)
            ppm = marker_ground_scale(chk, marker, camera)
            if estimate is None or ppm is None:
                continue
            count += 1
            worst = max(worst, math.hypot(projection[0] - estimate[0],
                                          projection[1] - estimate[1]) / ppm)
        return worst if count >= 2 else None
    except Exception:
        return None


def marker_anchor(chk, marker, camera):
    """Pixel of `camera` around which to zoom for this marker.

    Returns ((x, y), source) with source "placed" (projection already placed on
    this photo) or "estimated" (projection of the marker position, same
    convention as cameras_seeing_marker), or (None, reason) if unknown
    or if the point falls outside the image. Never raises.
    """
    try:
        image_w, image_h = camera_size(camera)
        if image_w <= 0 or image_h <= 0:
            return None, "unknown image size"
        x = y = None
        source = ""
        projection = get_projection(marker, camera)
        if projection is not None:
            x, y = projection[0], projection[1]
            source = "placed"
        else:
            transform = _camera_transform(camera)
            if transform is None:
                return None, "photo not aligned"
            point = marker_point(chk, marker)
            if point is None:
                return None, "marker with no position or projection"
            if transform.inv().mulp(point).z <= 0:
                return None, "the point is behind the photo"
            pixel = _xy(camera.project(point))
            if pixel is None:
                return None, "projection impossible"
            x, y = pixel
            source = "estimated"
        if _number(x) is None or _number(y) is None:
            return None, "unreadable coordinates"
        if x < 0 or y < 0 or x > image_w or y > image_h:
            return None, "{} outside the image ({:.0f}, {:.0f})".format(source, x, y)
        return (float(x), float(y)), source
    except Exception as exc:
        return None, "computation impossible ({!r})".format(exc)


def _close(read, wanted):
    return (read is not None
            and abs(read[0] - wanted[0]) <= _ZOOM_CENTER_TOL_PX
            and abs(read[1] - wanted[1]) <= _ZOOM_CENTER_TOL_PX)


def _apply_center(view, x, y):
    """Set view.center, trying a 2-component then a 3-component Vector.

    Returns (form, center_read, error): form is "2", "3" or None if
    no form was accepted. Move on to the next form as long as the
    center read back does not match the requested one.
    """
    form = None
    read = None
    error = None
    for name, values in (("2", [x, y]), ("3", [x, y, 0.0])):
        try:
            view.center = Metashape.Vector(values)
        except Exception as exc:
            error = exc
            continue
        form = name
        try:
            read = _xy(view.center)
        except Exception:
            read = None
        if _close(read, (x, y)):
            break
    return form, read, error


def _zoom_attempt(camera, x, y, scale, window_px, last):
    """One zoom attempt. Returns (status, detail).

    Statuses: "ok", "retry" (to redo later), "fail" (no point in
    retrying), "abandon" (photo never appeared after the last attempt).
    """
    view = photo_view()
    if view is None:
        return "fail", "photo view unavailable"
    opened = view_state(view)["camera"]
    wanted_key = camera_key(camera)
    if opened is None or wanted_key is None or camera_key(opened) != wanted_key:
        if last:
            return "abandon", "photo {} is not open".format(camera_label(camera))
        return "retry", "photo not open yet"

    keep = False
    if scale is not None:
        target_scale = scale
    else:
        before = view_state(view)
        fit = fit_scale(camera, before["width"], before["height"])
        if (before["scale"] is not None and fit is not None
                and before["scale"] > fit * ZOOM_KEEP_FACTOR):
            keep = True
            target_scale = before["scale"]
        else:
            target_scale = zoom_scale(camera, window_px)
    form, read_center, error = _apply_center(view, x, y)
    if form is None:
        return "fail", "center refused ({!r})".format(error)
    if not keep:
        try:
            view.scale = target_scale
        except Exception as exc:
            return "fail", "scale refused ({!r})".format(exc)

    after = view_state(view)
    # Changing the scale can move the center: set it once more.
    if not _close(after["center"], (x, y)):
        form2, _read2, _error2 = _apply_center(view, x, y)
        form = form2 or form
        after = view_state(view)
    scale_ok = (after["scale"] is not None
                and abs(after["scale"] - target_scale) <= _ZOOM_SCALE_TOL * target_scale)
    center_ok = _close(after["center"], (x, y))

    detail = "center=({:.1f}, {:.1f}) scale={:.4f} read=(center {}, scale {}) form={} components".format(
        x, y, target_scale,
        "({:.1f}, {:.1f})".format(*after["center"]) if after["center"] else "unreadable",
        "{:.4f}".format(after["scale"]) if after["scale"] is not None else "unreadable",
        form)
    if keep:
        detail += "; operator scale kept"
    if not (scale_ok and center_ok) and not last:
        return "retry", detail
    if not _ZOOM_LOGGED[0] or not (scale_ok and center_ok):
        _ZOOM_LOGGED[0] = True
        suffix = "" if (scale_ok and center_ok) else "; read differs from requested"
        try:
            journal.write("zoom: " + detail + suffix)
        except Exception:
            pass
    return "ok", detail


def zoom_to_point(camera, x, y, scale=None, window_px=None, on_done=None, guard=None):
    """Center the photo view on the pixel (x, y) of `camera` and set the zoom.

    The application is deferred (ZOOM_DELAYS_MS): after open_photo(), the view
    is not always ready. At each attempt `photo_view.camera` is re-read;
    if it is not `camera`, retry once, then give up. The
    center is set in image coordinates, then the scale, then everything is re-read.

    `scale`: imposed scale (screen pixels per image pixel); otherwise computed
    by zoom_scale(camera, window_px). `guard`: no-argument function, the zoom
    is cancelled if it returns False at the time of applying (tool paused).
    `on_done(ok)` is called at the end, applied or abandoned.

    Returns True if the zoom was scheduled, False otherwise. Never raises.
    """
    try:
        px, py = _number(x), _number(y)
        if camera is None or px is None or py is None:
            journal.write("zoom: invalid request (photo or point missing)")
            return False
        wanted = None
        if scale is not None:
            wanted = _number(scale)
            if wanted is None or wanted <= 0:
                journal.write("zoom: invalid scale ({!r})".format(scale))
                return False
        if photo_view() is None:
            journal.write("zoom: photo view unavailable")
            return False
        delays = tuple(ZOOM_DELAYS_MS)

        def finish(ok):
            if on_done is None:
                return
            try:
                on_done(ok)
            except Exception:
                pass

        def attempt(index):
            try:
                if guard is not None and not guard():
                    finish(False)
                    return
                last = index >= len(delays) - 1
                status, detail = _zoom_attempt(camera, px, py, wanted, window_px, last)
            except Exception as exc:
                status, detail = "fail", repr(exc)
            if status == "retry":
                try:
                    _schedule(delays[index + 1], lambda: attempt(index + 1))
                    return
                except Exception as exc:
                    status, detail = "fail", repr(exc)
            if status != "ok":
                try:
                    journal.write("zoom: {} ({})".format(
                        "abandoned" if status == "abandon" else "impossible", detail))
                except Exception:
                    pass
            finish(status == "ok")

        _schedule(delays[0], lambda: attempt(0))
        return True
    except Exception as exc:
        try:
            journal.write("zoom: {!r}".format(exc))
        except Exception:
            pass
        return False


def send_page_down():
    """Send a Page Down key: follows the order and the filter of the Photos pane."""
    window = main_window()
    instance = QtWidgets.QApplication.instance()
    if window is None or instance is None:
        return False
    target = None
    try:
        focused = instance.focusWidget()
        if focused is not None:
            focused.objectName()
            target = focused
    except RuntimeError:
        target = None
    if target is None:
        target = window
    from .qt import QtGui

    press = QtGui.QKeyEvent(
        QtCore.QEvent.KeyPress, QtCore.Qt.Key_PageDown, QtCore.Qt.NoModifier
    )
    release = QtGui.QKeyEvent(
        QtCore.QEvent.KeyRelease, QtCore.Qt.Key_PageDown, QtCore.Qt.NoModifier
    )
    QtWidgets.QApplication.postEvent(target, press)
    QtWidgets.QApplication.postEvent(target, release)
    return True


# --------------------------------------------------------------------------
# Photos pane
# --------------------------------------------------------------------------

def set_photo_filter(cameras):
    try:
        Metashape.app.photos_pane.setFilter(cameras)
        return True, ""
    except Exception as exc:
        return False, str(exc)


def reset_photo_filter():
    try:
        Metashape.app.photos_pane.resetFilter()
        return True, ""
    except Exception as exc:
        return False, str(exc)


# --------------------------------------------------------------------------
# Menu
# --------------------------------------------------------------------------

def add_menu_item(label, callback, shortcut=None):
    """Add a menu entry. Returns True if Metashape accepted it."""
    try:
        if shortcut:
            Metashape.app.addMenuItem(label, callback, shortcut=shortcut)
        else:
            Metashape.app.addMenuItem(label, callback)
        return True
    except Exception:
        try:
            Metashape.app.addMenuItem(label, callback)
            return True
        except Exception:
            return False


def add_menu_separator(menu):
    """Add a separator line at the end of the menu `menu`. Returns True if Metashape accepted it."""
    try:
        Metashape.app.addMenuSeparator(menu)
        return True
    except Exception:
        return False


def message_box(text):
    try:
        Metashape.app.messageBox(text)
    except Exception:
        print(text)


# --------------------------------------------------------------------------
# Residual check
# --------------------------------------------------------------------------

def worst_projection(marker):
    """(camera, offset in pixels) of the hand-placed projection farthest
    from the reprojection of the marker's 3D point, or None.

    Used to open in one click the photo that explains a large residual. Never raises.
    """
    try:
        position = marker.position
    except Exception:
        return None
    if position is None:
        return None
    worst = None
    for camera in projection_cameras(marker):
        projection = get_projection(marker, camera)
        if projection is None or not projection[2]:
            continue
        try:
            reprojected = _xy(camera.project(position))
        except Exception:
            continue
        if reprojected is None:
            continue
        gap = math.hypot(projection[0] - reprojected[0], projection[1] - reprojected[1])
        if worst is None or gap > worst[1]:
            worst = (camera, gap)
    return worst
