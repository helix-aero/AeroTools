"""Enveloppe des appels Metashape utilises par AeroTools.

Tout ce qui touche a l'API Metashape est ici : les outils n'appellent jamais
`Metashape.*` directement. Deux raisons : les API de vue photo ne sont pas
documentees et bougent d'une version a l'autre, et les references Python vers
des objets du document deviennent invalides (RuntimeError) des que l'on change
de chunk. Chaque fonction est donc defensive et renvoie une valeur neutre
plutot que de lever.

API Metashape mobilisees :
  Metashape.app.document / .photo_view / .photos_pane / .update()
  Metashape.app.addMenuItem(libelle, fonction, shortcut=...)
  chunk.cameras, chunk.markers, chunk.crs, chunk.transform
  marker.projections[camera] = Metashape.Marker.Projection(Vector, True)
  del marker.projections[camera], marker.position, marker.reference.location
  chunk.crs.project / unproject / localframe, chunk.transform.matrix.mulp
  camera.project(point), camera.transform, camera.sensor.width / .height
  photo_view.camera / .center / .scale / .width / .height / .open(camera)
  (center : pixel image au milieu de la vue ; scale : pixels ecran par pixel
  image. Semantique deduite de image_coords_at et d'anciens scripts, a
  confirmer avec diag_zoom.py)
"""

import math

import Metashape

from . import journal
from .qt import QtCore, QtWidgets

# Au-dela de cette distance, une photo n'est pas proposee pour un marqueur qui
# n'a pas encore de projection (sinon tout un vol « voit » le point).
MAX_FILTER_DIST_M = 400.0

# Onglets de la vue centrale qui ne sont pas des photos.
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
    """Version de Metashape, ou chaine vide si elle n'est pas accessible."""
    try:
        return str(Metashape.app.version)
    except Exception:
        return ""


def refresh():
    try:
        Metashape.app.update()
    except Exception:
        pass


# Le wrapper Python de la fenetre principale doit rester vivant : des qu'il est
# libere (variable locale d'une fonction terminee), PySide2 invalide les wrappers
# de tous ses enfants (« Internal C++ object already deleted » alors que les
# widgets existent toujours), et le clic dans la vue photo n'est plus reconnu.
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
# Marqueurs
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
    """Selectionne ce seul marqueur dans Metashape (panneau Reference). Ne leve jamais.

    Metashape se recentre sur SON marqueur selectionne a chaque changement de
    photo (Page precedente / suivante) : il doit etre le marqueur courant de
    FastGCP, sinon la vue part sur l'ancien. Renvoie True si la selection est faite.
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
    """Cameras ou ce marqueur a deja une projection.

    `without_proposals=True` ecarte les projections proposees par la detection
    IA de FastGCP (voir mark_proposal) et celles-la seulement : les autres
    projections du projet (posees a la main ou calculees par Metashape)
    restent.

    `marker.projections` ne s'itere pas de la meme facon selon la version :
    on essaie l'iteration directe, puis keys(), puis items().
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


# Projections posees par la detection IA et pas encore validees : (chunk, marqueur,
# photo). Metashape ne distingue pas une proposition d'un point bleu qu'il a
# lui-meme calcule : on s'en souvient ici, pour ne pas les compter comme pointees.
_PROPOSALS = set()


def _proposal_key(marker, camera):
    return (chunk_key(chunk()), marker_label(marker), camera_key(camera))


def mark_proposal(marker, camera):
    _PROPOSALS.add(_proposal_key(marker, camera))


def unmark_proposal(marker, camera):
    _PROPOSALS.discard(_proposal_key(marker, camera))


def is_proposal(marker, camera):
    """Cette projection est-elle une proposition de l'IA encore non validee ?

    Des que l'operateur la remplace par un pointage (pinned), elle n'en est plus une.
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
    """Nombre de photos avec une projection, propositions de l'IA exclues."""
    return len(projection_cameras(marker, without_proposals=True))


def marker_point(chk, marker):
    """Position du marqueur en coordonnees internes du chunk, ou None.

    Si le marqueur n'est pas encore triangule, on part de ses coordonnees de
    reference : c'est ce qui permet de filtrer les photos d'un GCP jamais
    pointe. Cette reference est exprimee dans le repere des marqueurs (`marker_crs`),
    qui peut differer de celui du chunk (ex. chunk en WGS 84, marqueurs en metres).
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
    """(x, y, z) d'un vecteur Metashape, ou None."""
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


# Une ligne de diagnostic par chunk et par cause (voir _log_once).
_DIAG_LOGGED = set()

# Ellipsoide GRS80 / WGS84, pour convertir des degres en metres locaux.
_ELL_A = 6378137.0
_ELL_E2 = 0.00669437999014


def _log_once(chk, kind, text):
    """Ecrit une seule fois par chunk (et par cause) une ligne dans le journal."""
    key = (chunk_key(chk), kind)
    if key in _DIAG_LOGGED:
        return
    _DIAG_LOGGED.add(key)
    try:
        journal.write("Erreurs du tableau : " + text)
    except Exception:
        pass


def _type_name(value):
    try:
        return type(value).__name__
    except Exception:
        return "?"


def crs_kind(crs):
    """"projected", "geographic", "geocentric" ou None si on ne sait pas.

    Lu dans le WKT (un CRS compose avec geoide contient PROJCS), puis dans
    la chaine proj4.
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
    """Erreur du marqueur en metres : position estimee moins position de reference.

    Renvoie {"x", "y", "z", "total"}, chaque valeur etant un flottant ou None
    (pas de coordonnee de reference, marqueur non estime, calcul impossible).
    Ne leve jamais : le panneau et le rapport PDF s'en servent tels quels.
    Quand un marqueur a une reference et une position mais que le calcul
    echoue, une ligne du journal (une par chunk) donne la cause exacte.

    Chemins essayes dans l'ordre, le premier resultat fini l'emporte :
      A. repere de coordonnees defini : les deux positions en geocentrique
         (estimee par la matrice du chunk, reference par crs.unproject), ecart
         tourne dans le repere local du point (crs.localframe) : est, nord,
         haut en metres, comme le tableau Reference (a la convergence des
         meridiens pres, negligeable au centimetre) ;
      B. CRS projete : crs.project(position estimee) moins la reference,
         deja en metres ;
      C. CRS geographique : ecart en degres converti en metres locaux ;
      sans systeme de coordonnees : difference dans le repere du chunk, mise
      a l'echelle metrique (chunk.transform.scale).
    """
    empty = {"x": None, "y": None, "z": None, "total": None}
    try:
        position = marker.position
        reference = marker.reference.location
    except Exception as exc:
        _log_once(chk, "lecture", "lecture du marqueur impossible : {!r}".format(exc))
        return empty
    if reference is None:
        return empty
    label = marker_label(marker)
    if position is None:
        _log_once(chk, "non_estime",
                  "{} a une reference mais pas de position estimee (marqueur non "
                  "triangule, normal tant qu'il n'est pas pointe)".format(label))
        return empty
    notes = []
    try:
        values = _error_components(chk, position, reference, notes)
    except Exception as exc:
        notes.append("exception inattendue {!r}".format(exc))
        values = None
    if not _finite(values):
        _log_once(chk, "echec", _diagnostic(chk, marker, position, reference, notes))
        return empty
    x, y, z = values
    return {"x": x, "y": y, "z": z, "total": math.sqrt(x * x + y * y + z * z)}


def _diagnostic(chk, marker, position, reference, notes):
    """Phrase unique qui dit pourquoi le calcul a echoue."""
    parts = ["calcul impossible pour {} (chunk {})".format(
        marker_label(marker), chunk_label(chk) or "?")]
    try:
        crs = getattr(chk, "marker_crs", None) or chk.crs
        parts.append("crs={} type={}".format(getattr(crs, "name", crs), crs_kind(crs)))
    except Exception as exc:
        parts.append("crs illisible {!r}".format(exc))
    try:
        enabled = marker.reference.enabled
    except Exception:
        enabled = "?"
    parts.append("reference.location={} enabled={}".format(
        _components(reference), enabled))
    parts.append("position={}".format(_components(position)))
    try:
        matrix = chk.transform.matrix
        parts.append("matrice={}".format(_type_name(matrix)))
        try:
            parts.append("mulp->{}".format(_type_name(matrix.mulp(position))))
        except Exception as exc:
            parts.append("mulp echoue {!r}".format(exc))
        try:
            parts.append("localframe->{}".format(
                _type_name(chk.crs.localframe(matrix.mulp(position)))))
        except Exception as exc:
            parts.append("localframe echoue {!r}".format(exc))
    except Exception as exc:
        parts.append("matrice illisible {!r}".format(exc))
    parts.extend(notes)
    return " ; ".join(parts)


def _delta_vector(values):
    return Metashape.Vector(list(values))


def _rotate(local, delta, notes):
    """Applique la matrice `local` a l'ecart `delta` (vecteur de 3). Plusieurs
    formes, selon ce que la matrice accepte (4x4 homogene, 3x3, lignes)."""
    attempts = (
        ("mulv", lambda: local.mulv(delta)),
        ("produit", lambda: local * delta),
        ("rotation", lambda: local.rotation() * delta),
    )
    for name, call in attempts:
        try:
            values = _components(call())
            if _finite(values):
                return values
        except Exception as exc:
            notes.append("localframe.{} echoue {!r}".format(name, exc))
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
        notes.append("localframe.lignes echoue {!r}".format(exc))
    return None


def _error_components(chk, position, reference, notes):
    ref = _components(reference)
    if ref is None:
        notes.append("reference illisible")
        return None
    # La reference est exprimee dans le repere des marqueurs, qui peut differer
    # de celui du chunk (ex. chunk en WGS 84, marqueurs en metres).
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
        # Repere local : la reference est dans le repere du chunk, en metres
        # apres la similitude du chunk.
        if matrix is not None:
            try:
                estimated = _components(matrix.mulp(position))
                if estimated is not None:
                    return tuple(e - r for e, r in zip(estimated, ref))
            except Exception as exc:
                notes.append("local : mulp echoue {!r}".format(exc))
        estimated = _components(position)
        if estimated is None:
            return None
        return tuple((e - r) * scale for e, r in zip(estimated, ref))

    if matrix is None:
        notes.append("chunk.transform.matrix est None")
        return None
    try:
        world = matrix.mulp(position)
    except Exception as exc:
        notes.append("matrice.mulp echoue {!r}".format(exc))
        return None
    world_c = _components(world)
    if world_c is None:
        notes.append("position du chunk illisible")
        return None
    kind = crs_kind(crs)

    # A. geocentrique tourne dans le repere local.
    try:
        ref_ecef = _components(crs.unproject(reference))
        if ref_ecef is None:
            raise ValueError("unproject sans resultat")
        delta = _delta_vector(w - r for w, r in zip(world_c, ref_ecef))
        local = crs.localframe(world)
        values = _rotate(local, delta, notes)
        if _finite(values):
            return values
    except Exception as exc:
        notes.append("chemin A echoue {!r}".format(exc))

    # B et C partent de la position estimee exprimee dans le systeme du CRS.
    try:
        estimated = _components(crs.project(world))
    except Exception as exc:
        notes.append("crs.project echoue {!r}".format(exc))
        return None
    if estimated is None:
        notes.append("crs.project sans resultat")
        return None
    looks_degrees = abs(ref[0]) <= 180.0 and abs(ref[1]) <= 90.0

    # B. CRS projete : l'ecart est deja en metres.
    if kind == "projected" or (kind is None and not looks_degrees):
        values = tuple(e - r for e, r in zip(estimated, ref))
        if _finite(values):
            return values
    # C. CRS geographique : degres vers metres locaux.
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
    notes.append("aucun chemin applicable (crs de type {})".format(kind))
    return None


def cameras_seeing_marker(chk, marker, max_dist_m=MAX_FILTER_DIST_M):
    """Photos a proposer pour ce marqueur, de la plus proche a la plus lointaine.

    D'abord les projections deja posees, puis les photos alignees d'ou le point
    tombe dans l'image, devant la camera et a moins de `max_dist_m`.
    Renvoie (cameras, diagnostic).
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
    diagnostic["alignees"] = sum(
        1 for cam in _cameras(chk) if _camera_transform(cam) is not None
    )
    diagnostic["proches"] = diagnostic["devant"] = diagnostic["dans_image"] = 0
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
            diagnostic["proches"] += 1
            local = cam.transform.inv().mulp(point)
            if local.z <= 0:
                continue
            diagnostic["devant"] += 1
            uv = cam.project(point)
            if uv is None:
                continue
            width, height = cam.sensor.width, cam.sensor.height
            if 0 <= uv.x < width and 0 <= uv.y < height:
                diagnostic["dans_image"] += 1
                extra.append((distance, cam))
        except Exception as exc:
            diagnostic["erreur"] = str(exc)
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
# Vue photo
# --------------------------------------------------------------------------

def photo_view_widgets():
    """Widgets Qt qui ont exactement la taille de la vue photo.

    Metashape n'expose pas le widget de la vue photo : on le retrouve par sa
    taille, que l'API donne (`photo_view.width` / `.height`), et on garde le
    plus profond dans l'arbre.
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
    """(texte de l'onglet central actif, clic dans un panneau dockable).

    Metashape 2.2 n'a pas de QMdiArea : la vue centrale est un QTabWidget dont
    les onglets sont Model / Ortho / <nom de photo>. On remonte du widget sous
    le curseur jusqu'a ce QTabWidget et on lit l'onglet actif.
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
        # Certains widgets de Metashape (la console Python) masquent `parent`
        # par un attribut qui n'est pas appelable : on s'arrete la, sans lever.
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
    # Un onglet qui porte exactement le nom d'une photo du chunk est une photo,
    # meme si ce nom contient un mot de la liste noire (« DEM_001 », « model »).
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
    # Onglet central non blackliste : on le considere comme une photo.
    return True


def cursor_over_photo_view(global_pos):
    """True seulement si l'onglet central sous le curseur est une photo."""
    title, _in_dock = central_tab_under(global_pos)
    if title is None:
        return False
    return _title_is_photo(title, chunk())


def describe_position(global_pos):
    """Phrase courte expliquant pourquoi un clic a ete ignore."""
    title, in_dock = central_tab_under(global_pos)
    if in_dock:
        return "panneau Metashape"
    if title:
        return "onglet « {} »".format(title)
    return "hors de la vue centrale"


# --------------------------------------------------------------------------
# Pose d'une projection
# --------------------------------------------------------------------------

def image_coords_at(global_pos):
    """Coordonnees image (pixels capteur) sous le curseur, ou (None, raison)."""
    view = photo_view()
    if view is None or view.camera is None:
        return None, "aucune photo ouverte"
    try:
        width, height, scale, center = view.width, view.height, view.scale, view.center
    except Exception:
        return None, "vue photo illisible"

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
        return None, "curseur hors de la vue photo"

    # Valable pour n'importe quel zoom : la vue est relue a chaque clic (centre
    # et echelle courants), rien n'est memorise ni suppose « ajuste a la fenetre ».
    try:
        scale = float(scale)
        center_x, center_y = float(center.x), float(center.y)
    except Exception:
        return None, "centre ou echelle de la vue illisibles"
    if not (scale > 0.0 and math.isfinite(scale)):
        return None, "echelle de la vue illisible"
    img_x = center_x + (local.x() - width / 2.0) / scale
    img_y = center_y + (local.y() - height / 2.0) / scale
    try:
        sensor_w = view.camera.sensor.width
        sensor_h = view.camera.sensor.height
    except Exception:
        return None, "capteur inconnu"
    if img_x < 0 or img_y < 0 or img_x > sensor_w or img_y > sensor_h:
        return None, "pixel hors image ({:.0f}, {:.0f})".format(img_x, img_y)
    return (img_x, img_y), None


def set_projection(marker, camera, img_x, img_y, pinned=True):
    """Pose la projection du marqueur sur cette camera. Renvoie (ok, message).

    `pinned=True` : pointage de l'operateur (pastille verte dans Metashape).
    `pinned=False` : projection proposee par une detection automatique (AIGCP),
    que Metashape affiche en bleu et qu'il peut reajuster.
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
    """Projection actuelle du marqueur sur cette photo : (x, y, pinned), ou None.

    Sert a memoriser l'etat d'avant pour pouvoir annuler. Ne leve jamais.
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
    """Supprime la projection du marqueur sur cette photo. Renvoie (ok, message).

    `update=False` : ne rafraichit pas Metashape (l'appelant le fait une fois pour toute une serie).
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
    """Supprime toutes les projections du marqueur (posees, bleues, propositions de l'IA).

    Renvoie le nombre de projections retirees. Le marqueur reste dans le chunk. Ne leve jamais.
    """
    removed = 0
    for camera in projection_cameras(marker):
        done, _message = remove_projection(marker, camera, update=False)
        removed += 1 if done else 0
    refresh()
    return removed


def remove_marker(chk, marker):
    """Supprime le marqueur du chunk. Renvoie (ok, message)."""
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
# Navigation entre photos
# --------------------------------------------------------------------------

def current_camera():
    """Camera ouverte dans la vue photo, ou None."""
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
    """Ouvre `camera` dans la vue photo. Renvoie (ok, message), ne leve jamais."""
    view = photo_view()
    if view is None:
        return False, "vue photo indisponible"
    try:
        view.open(camera)
        return True, camera_label(camera)
    except Exception as exc:
        return False, str(exc)


def open_next_camera():
    """Ouvre la camera suivante du chunk dans la vue photo. (ok, message)."""
    view = photo_view()
    chk = chunk()
    if view is None or chk is None or view.camera is None:
        return False, "aucune photo ouverte"
    cameras = _cameras(chk)
    try:
        index = cameras.index(view.camera)
    except ValueError:
        return False, "photo courante absente du chunk"
    if index + 1 >= len(cameras):
        return False, "derniere photo du chunk"
    try:
        view.open(cameras[index + 1])
        return True, camera_label(cameras[index + 1])
    except Exception as exc:
        return False, str(exc)


# --------------------------------------------------------------------------
# Zoom de la vue photo
# --------------------------------------------------------------------------
#
# Semantique supposee de la vue photo (voir image_coords_at, qui l'utilise deja
# et les anciens scripts de _sources_reference) :
#   photo_view.center : pixel de l'image (x, y) affiche au milieu de la vue ;
#   photo_view.scale  : facteur, pixels ecran par pixel image (16.74 = 1674 %).
# Rien de tout cela n'est documente par Agisoft : zoom_to_point relit la vue
# apres l'avoir modifiee et journalise une fois ce qu'il a lu (diag_zoom.py
# donne la verite a la main).

# Largeur d'image visible dans la vue, par defaut (pixels image).
ZOOM_DEFAULT_WINDOW_PX = 400.0
# Echelle quand la largeur de la vue est inconnue.
ZOOM_FALLBACK_SCALE = 4.0
# Echelle maximale du zoom automatique.
ZOOM_MAX_SCALE = 16.0
# Delai avant la premiere tentative, puis avant la seconde (ms) : l'ouverture
# d'une photo par photo_view.open() n'est pas toujours terminee au retour.
ZOOM_DELAYS_MS = (60, 150)
# Ecart tolere entre le centre demande et le centre relu (pixels image) et
# entre l'echelle demandee et l'echelle relue (relatif).
_ZOOM_CENTER_TOL_PX = 2.0
_ZOOM_SCALE_TOL = 0.05

# Vue deja zoomee par l'operateur (ou par Metashape, qui recentre lui-meme sur le
# marqueur en gardant l'echelle) : echelle courante > ajuster-a-la-fenetre x ce
# facteur. Le zoom automatique ne touche alors qu'au centre, jamais a l'echelle :
# ni double zoom, ni dezoom. Sur une photo encore « ajustee » (premiere photo),
# l'echelle automatique s'applique.
ZOOM_KEEP_FACTOR = 1.5

# La ligne detaillee zoom : centre=... n'est ecrite qu'une fois par session.
_ZOOM_LOGGED = [False]


def _schedule(delay_ms, function):
    """Appelle `function` apres `delay_ms` (la boucle Qt doit tourner)."""
    QtCore.QTimer.singleShot(int(delay_ms), function)


def _number(value):
    """Flottant fini, ou None."""
    try:
        number = float(value)
    except Exception:
        return None
    return number if math.isfinite(number) else None


def _xy(value):
    """(x, y) d'un Vector (ou d'une sequence), ou None."""
    try:
        return float(value.x), float(value.y)
    except Exception:
        try:
            return float(value[0]), float(value[1])
        except Exception:
            return None


def view_state(view=None):
    """Etat lu de la vue photo : dict camera / center / scale / width / height.

    Chaque attribut est lu seul ; une lecture qui echoue donne None. `center`
    est un couple (x, y) ou None, les autres valeurs des flottants ou None.
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
    """Echelle ajuster a la fenetre pour cette image, ou None."""
    image_w, image_h = camera_size(camera)
    if image_w <= 0 or image_h <= 0 or not width or not height:
        return None
    if width <= 0 or height <= 0:
        return None
    return min(float(width) / image_w, float(height) / image_h)


def zoom_scale(camera=None, window_px=None):
    """Echelle du zoom automatique : largeur de la vue / `window_px`.

    Bornee entre l'echelle qui ajuste l'image a la fenetre et ZOOM_MAX_SCALE.
    Largeur de vue inconnue : ZOOM_FALLBACK_SCALE.
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
    """Echelle au sol (pixels par metre) du marqueur sur cette photo, ou None.

    Meme convention que le jeu d'entrainement AIGCP : focale en pixels divisee
    par la distance camera-marqueur en metres. Ne leve jamais.
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
    """Metres par unite interne du chunk (1.0 si inconnu)."""
    try:
        return float(chk.transform.scale) or 1.0
    except Exception:
        return 1.0


def pixel_ray(camera, x, y):
    """Rayon qui part de la camera vers le pixel (x, y) de la photo.

    Renvoie (origine, direction) en coordonnees internes du chunk, ou None.
    Sert a verifier que des detections sur plusieurs photos se recoupent en un
    meme point 3D. Ne leve jamais.
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
    """Pixel (x, y) ou le point 3D (coordonnees internes du chunk) tombe sur cette photo, ou None."""
    try:
        return _xy(camera.project(Metashape.Vector(list(_components(point)))))
    except Exception:
        return None


def marker_estimate(chk, marker, camera):
    """Projection du point 3D du marqueur sur cette photo : (x, y) ou None.

    Contrairement a marker_anchor, ignore toute projection deja posee : c'est la
    ou le marqueur DEVRAIT etre, pas ou il est. Ne leve jamais.
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
    """Plus grande composante d'une precision (vecteur ou nombre), ou None."""
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
    """(utilise comme GCP, precision de reference en m) ; None pour ce qu'on ne sait pas lire.

    `reference.enabled` vrai : le marqueur sert de point d'appui (GCP) ; faux : point de
    controle. La precision est celle du marqueur, sinon celle du chunk (`marker_location_accuracy`,
    la valeur que le tableau Reference de Metashape affiche). Ne leve jamais.
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
    """Plus grand ecart, en metres au sol, entre une projection du marqueur et la
    reprojection de son point 3D ; None si on ne peut pas le dire.

    Toutes les projections comptent, posees a la main ou calculees par Metashape,
    sauf les propositions de l'IA : si elles retombent toutes pres de la
    reprojection du point 3D, la position du marqueur est connue a cet ecart pres
    sur les autres photos. None quand le marqueur a moins de 2 projections ou pas
    de position : on ne sait pas alors a quel point elle est fiable.
    Ne leve jamais.
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
    """Pixel de `camera` autour duquel zoomer pour ce marqueur.

    Renvoie ((x, y), source) avec source "pointee" (projection deja posee sur
    cette photo) ou "estimee" (projection de la position du marqueur, meme
    convention que cameras_seeing_marker), ou (None, raison) si on ne sait pas
    ou si le point tombe hors image. Ne leve jamais.
    """
    try:
        image_w, image_h = camera_size(camera)
        if image_w <= 0 or image_h <= 0:
            return None, "taille de l'image inconnue"
        x = y = None
        source = ""
        projection = get_projection(marker, camera)
        if projection is not None:
            x, y = projection[0], projection[1]
            source = "pointee"
        else:
            transform = _camera_transform(camera)
            if transform is None:
                return None, "photo non alignee"
            point = marker_point(chk, marker)
            if point is None:
                return None, "marqueur sans position ni projection"
            if transform.inv().mulp(point).z <= 0:
                return None, "le point est derriere la photo"
            pixel = _xy(camera.project(point))
            if pixel is None:
                return None, "projection impossible"
            x, y = pixel
            source = "estimee"
        if _number(x) is None or _number(y) is None:
            return None, "coordonnees illisibles"
        if x < 0 or y < 0 or x > image_w or y > image_h:
            return None, "{} hors image ({:.0f}, {:.0f})".format(source, x, y)
        return (float(x), float(y)), source
    except Exception as exc:
        return None, "calcul impossible ({!r})".format(exc)


def _close(read, wanted):
    return (read is not None
            and abs(read[0] - wanted[0]) <= _ZOOM_CENTER_TOL_PX
            and abs(read[1] - wanted[1]) <= _ZOOM_CENTER_TOL_PX)


def _apply_center(view, x, y):
    """Pose view.center, en essayant un Vector a 2 puis a 3 composantes.

    Renvoie (forme, centre_relu, erreur) : forme vaut "2", "3" ou None si
    aucune forme n'a ete acceptee. On passe a la forme suivante tant que le
    centre relu ne correspond pas a celui demande.
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
    """Une tentative de zoom. Renvoie (statut, detail).

    Statuts : "ok", "retry" (a refaire plus tard), "fail" (inutile de
    reessayer), "abandon" (photo jamais apparue apres la derniere tentative).
    """
    view = photo_view()
    if view is None:
        return "fail", "vue photo indisponible"
    opened = view_state(view)["camera"]
    wanted_key = camera_key(camera)
    if opened is None or wanted_key is None or camera_key(opened) != wanted_key:
        if last:
            return "abandon", "la photo {} n'est pas ouverte".format(camera_label(camera))
        return "retry", "photo pas encore ouverte"

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
        return "fail", "centre refuse ({!r})".format(error)
    if not keep:
        try:
            view.scale = target_scale
        except Exception as exc:
            return "fail", "echelle refusee ({!r})".format(exc)

    after = view_state(view)
    # Changer l'echelle peut deplacer le centre : on le repose une fois.
    if not _close(after["center"], (x, y)):
        form2, _read2, _error2 = _apply_center(view, x, y)
        form = form2 or form
        after = view_state(view)
    scale_ok = (after["scale"] is not None
                and abs(after["scale"] - target_scale) <= _ZOOM_SCALE_TOL * target_scale)
    center_ok = _close(after["center"], (x, y))

    detail = "centre=({:.1f}, {:.1f}) echelle={:.4f} lu=(centre {}, echelle {}) forme={} composantes".format(
        x, y, target_scale,
        "({:.1f}, {:.1f})".format(*after["center"]) if after["center"] else "illisible",
        "{:.4f}".format(after["scale"]) if after["scale"] is not None else "illisible",
        form)
    if keep:
        detail += " ; echelle de l'operateur gardee"
    if not (scale_ok and center_ok) and not last:
        return "retry", detail
    if not _ZOOM_LOGGED[0] or not (scale_ok and center_ok):
        _ZOOM_LOGGED[0] = True
        suffix = "" if (scale_ok and center_ok) else " ; lu different du demande"
        try:
            journal.write("zoom : " + detail + suffix)
        except Exception:
            pass
    return "ok", detail


def zoom_to_point(camera, x, y, scale=None, window_px=None, on_done=None, guard=None):
    """Centre la vue photo sur le pixel (x, y) de `camera` et fixe le zoom.

    L'application est differee (ZOOM_DELAYS_MS) : apres open_photo(), la vue
    n'est pas toujours prete. A chaque tentative on relit `photo_view.camera` ;
    si ce n'est pas `camera`, on recommence une fois, puis on abandonne. Le
    centre est pose en coordonnees image, puis l'echelle, puis tout est relu.

    `scale` : echelle imposee (pixels ecran par pixel image) ; sinon calculee
    par zoom_scale(camera, window_px). `guard` : fonction sans argument, le zoom
    est annule si elle renvoie False au moment d'appliquer (outil en pause).
    `on_done(ok)` est appele a la fin, appliquee ou abandonnee.

    Renvoie True si le zoom a ete programme, False sinon. Ne leve jamais.
    """
    try:
        px, py = _number(x), _number(y)
        if camera is None or px is None or py is None:
            journal.write("zoom : demande invalide (photo ou point manquant)")
            return False
        wanted = None
        if scale is not None:
            wanted = _number(scale)
            if wanted is None or wanted <= 0:
                journal.write("zoom : echelle invalide ({!r})".format(scale))
                return False
        if photo_view() is None:
            journal.write("zoom : vue photo indisponible")
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
                    journal.write("zoom : {} ({})".format(
                        "abandonne" if status == "abandon" else "impossible", detail))
                except Exception:
                    pass
            finish(status == "ok")

        _schedule(delays[0], lambda: attempt(0))
        return True
    except Exception as exc:
        try:
            journal.write("zoom : {!r}".format(exc))
        except Exception:
            pass
        return False


def send_page_down():
    """Envoie une touche Page suivante : suit l'ordre et le filtre du panneau Photos."""
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
# Panneau Photos
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
    """Ajoute une entree de menu. Renvoie True si Metashape l'a acceptee."""
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


def message_box(text):
    try:
        Metashape.app.messageBox(text)
    except Exception:
        print(text)


# --------------------------------------------------------------------------
# Controle des residus
# --------------------------------------------------------------------------

def worst_projection(marker):
    """(camera, ecart en pixels) de la projection posee a la main la plus
    eloignee de la reprojection du point 3D du marqueur, ou None.

    Sert a ouvrir d'un clic la photo qui explique un gros residu. Ne leve jamais.
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
