"""Diagnostic du pointage : quels pointages, quelles photos, quels marqueurs posent probleme.

Methode, pour chaque marqueur qui a au moins 3 pointages manuels (pastille verte) :
  1. on prend le rayon de chaque pointage (centre de la camera -> pixel pointe) ;
  2. pour un pointage donne, on triangule la position 3D du marqueur avec TOUS LES AUTRES
     rayons (« validation croisee » : le pointage juge n'influence pas son propre verdict) ;
  3. on projette ce point sur la photo jugee et on mesure l'ecart, en pixels, avec le pointage.
Un ecart de quelques pixels au plus est normal ; un grand ecart veut dire un pointage decale ou
une photo mal alignee. Avec seulement 2 pointages il n'y a rien pour recouper : le marqueur est
signale « a completer », pas juge.

On calcule aussi l'ecart classique « pointage - reprojection de la position de Metashape » (fit_px),
pour pouvoir le comparer au chiffre « Error (pix) » du panneau References de Metashape.

Un pointage tres decale tire la position calculee sans lui, et fait paraitre ses voisins fautifs. On
« pele » donc : on juge tout le monde, on met de cote le pire s'il depasse PEEL_PX, puis on recommence
sans lui, jusqu'a ce que plus rien ne depasse (ou qu'il ne reste que 3 pointages, qu'on ne peut plus
departager). Seul le vrai fautif est alors signale.

Seuil de signalement d'un pointage : max(FLOOR_PX, FACTOR x mediane des ecarts du projet) : un projet
globalement bruyant ne signale pas tout. Une photo est suspecte quand sa mediane (sur >= 3 marqueurs)
est nettement au-dessus de celle du projet.

`analyze` ne touche ni Metashape ni Qt ; `collect` lit le projet.
"""

import math

from ... import msapi
from . import consensus, qc

MIN_PINNED = 3
FLOOR_PX = 3.0
PEEL_PX = 3.0
FACTOR = 4.0
PHOTO_FLOOR_PX = 1.5
PHOTO_FACTOR = 2.0
PHOTO_MIN_MARKERS = 3


def median(values):
    values = sorted(values)
    if not values:
        return 0.0
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else 0.5 * (values[middle - 1] + values[middle])


def rms(values):
    values = list(values)
    return math.sqrt(sum(v * v for v in values) / len(values)) if values else 0.0


def leave_one_out_points(rays):
    """Pour chaque rayon, le point triangule avec tous les autres (None si impossible).

    `rays` : liste de (origine, direction) ou None. Il faut au moins 2 autres rayons utilisables et
    pas tous paralleles."""
    out = []
    for index in range(len(rays)):
        others = [ray for i, ray in enumerate(rays) if i != index and ray is not None]
        cleaned = [r for r in consensus._clean(others) if r is not None]
        out.append(consensus.triangulate(cleaned) if len(cleaned) >= 2 else None)
    return out


def cross_check(rays, gap_of, peel_px=PEEL_PX):
    """Ecart (px) de chaque pointage avec la position triangulee SANS lui, en pelant le pire a chaque tour.

    `rays` : (origine, direction) par pointage ; `gap_of(i, point)` : ecart en pixels entre le pointage i
    et la projection de `point` sur sa photo (ou None). Renvoie une liste (None si impossible a juger)."""
    result = [None] * len(rays)
    active = [i for i, ray in enumerate(rays) if ray is not None]
    while len(active) >= MIN_PINNED:
        points = leave_one_out_points([rays[i] for i in active])
        gaps = [gap_of(i, point) if point is not None else None for i, point in zip(active, points)]
        judged = [(gap, i) for gap, i in zip(gaps, active) if gap is not None]
        if not judged:
            break
        worst, culprit = max(judged)
        if worst <= peel_px or len(active) == MIN_PINNED:
            for gap, i in zip(gaps, active):
                result[i] = gap
            break
        result[culprit] = worst
        active.remove(culprit)
    return result


# --------------------------------------------------------------------------
# Lecture du projet
# --------------------------------------------------------------------------

def collect(chk):
    """Les pointages manuels de chaque marqueur du chunk, avec leurs ecarts en pixels.

    Renvoie une liste de {"label", "marker", "total_m"?, "pinned": n, "proposals": n,
    "observations": [{"camera", "camera_label", "x", "y", "fit_px", "loo_px"}]}. Ne leve jamais."""
    out = []
    for marker in msapi.markers(chk):
        label = msapi.marker_label(marker)
        if not label:
            continue
        try:
            out.append(_collect_marker(chk, marker, label))
        except Exception:
            continue
    return out


def _collect_marker(chk, marker, label):
    try:
        position = marker.position
    except Exception:
        position = None
    observations, rays, proposals = [], [], 0
    for camera in msapi.projection_cameras(marker):
        projection = msapi.get_projection(marker, camera)
        if projection is None:
            continue
        if not projection[2]:
            if msapi.is_proposal(marker, camera):
                proposals += 1
            continue
        fit = None
        if position is not None:
            fit = _gap(projection, msapi.project_point(camera, position))
        observations.append({"camera": camera, "camera_label": msapi.camera_label(camera),
                             "x": projection[0], "y": projection[1], "fit_px": fit, "loo_px": None})
        rays.append(msapi.pixel_ray(camera, projection[0], projection[1]))
    if len(observations) >= MIN_PINNED:
        def gap_of(index, point):
            obs = observations[index]
            return _gap((obs["x"], obs["y"]), msapi.project_point(obs["camera"], point))

        for obs, gap in zip(observations, cross_check(rays, gap_of)):
            obs["loo_px"] = gap
    return {"label": label, "marker": marker, "pinned": len(observations), "proposals": proposals,
            "observations": observations}


def _gap(a, b):
    if a is None or b is None:
        return None
    return math.hypot(a[0] - b[0], a[1] - b[1])


# --------------------------------------------------------------------------
# Analyse
# --------------------------------------------------------------------------

def analyze(markers, rows=None):
    """Resultat du diagnostic (dict) a partir de `collect` ; `rows` : lignes du tableau du panneau
    (pour l'erreur 3D totale par marqueur, voir qc.watchlist).

    Cles : summary, limit_px, projections (triees du pire au meilleur), photos, markers."""
    loo = [o["loo_px"] for m in markers for o in m["observations"] if o["loo_px"] is not None]
    fit = [o["fit_px"] for m in markers for o in m["observations"] if o["fit_px"] is not None]
    typical = median(loo)
    limit = max(FLOOR_PX, FACTOR * typical)
    projections = []
    by_camera = {}
    for marker in markers:
        for obs in marker["observations"]:
            if obs["loo_px"] is None:
                continue
            by_camera.setdefault(obs["camera_label"], {"camera": obs["camera"], "values": [], "markers": set()})
            by_camera[obs["camera_label"]]["values"].append(obs["loo_px"])
            by_camera[obs["camera_label"]]["markers"].add(marker["label"])
            if obs["loo_px"] > limit:
                projections.append({"label": marker["label"], "marker": marker["marker"],
                                    "camera": obs["camera"], "camera_label": obs["camera_label"],
                                    "px": obs["loo_px"], "fit_px": obs["fit_px"]})
    projections.sort(key=lambda item: -item["px"])
    photos = []
    for name, data in by_camera.items():
        if len(data["markers"]) < PHOTO_MIN_MARKERS:
            continue
        mid = median(data["values"])
        if mid > max(PHOTO_FLOOR_PX, PHOTO_FACTOR * typical):
            photos.append({"camera": data["camera"], "camera_label": name, "px": mid,
                           "n": len(data["markers"])})
    photos.sort(key=lambda item: -item["px"])

    flagged = []
    for marker in markers:
        if marker["pinned"] < MIN_PINNED:
            flagged.append({"label": marker["label"], "marker": marker["marker"],
                            "kind": "few" if marker["pinned"] < 2 else "unchecked", "n": marker["pinned"]})
        if marker["proposals"]:
            flagged.append({"label": marker["label"], "marker": marker["marker"],
                            "kind": "proposals", "n": marker["proposals"]})
    if rows:
        result = qc.watchlist(rows)
        for item in result["residual"]:
            flagged.append({"label": item["label"], "marker": None, "kind": "residual",
                            "n": item["proj"], "total": item["total"], "limit": result["limit"]})
    order = {"few": 0, "residual": 1, "unchecked": 2, "proposals": 3}
    flagged.sort(key=lambda item: (order[item["kind"]], item["label"]))

    summary = {
        "markers": len(markers),
        "checked": sum(1 for m in markers if m["pinned"] >= MIN_PINNED),
        "observations": sum(m["pinned"] for m in markers),
        "photos": len({o["camera_label"] for m in markers for o in m["observations"]}),
        "rms_px": rms(fit), "median_px": typical, "loo_rms_px": rms(loo),
        "below_1px": (sum(1 for v in loo if v <= 1.0) / float(len(loo))) if loo else None,
    }
    return {"summary": summary, "limit_px": limit, "projections": projections, "photos": photos,
            "markers": flagged}


def is_clean(result):
    return not (result["projections"] or result["photos"] or result["markers"])
