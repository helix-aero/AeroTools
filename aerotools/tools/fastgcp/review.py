"""Revue des pointes proposes par l'IA (sans Qt).

Apres une detection, chaque proposition posee dans le projet devient une entree de
revue : une vignette recadree autour du point, que l'operateur peut garder, retirer
ou affiner. La fenetre est dans review_dialog.py ; ici, l'etat et ce qu'on en fait.

Chaque decision est appliquee tout de suite au projet et inscrite, comme un
evenement, dans un fichier destine au futur reentrainement :

    <config.settings_dir()>/dataset/aigcp_feedback.jsonl

Une ligne par evenement (UTF-8, ajout en fin de fichier). Un script d'entrainement
garde le DERNIER evenement de chaque (run, marqueur, photo). Cles (schema 1) :
    schema, ts, run          version du format, date locale, lancement de la detection
    type                     "damier" ou "bombe" (les deux modeles ne se melangent jamais)
    chunk, marker, camera    ou
    image_path, image_w, image_h   la photo source (aucune image n'est copiee)
    ai_x, ai_y, conf         ce que l'IA avait propose (px photo, origine en haut a gauche)
    crop_px, px_per_m        taille de la fenetre du modele et echelle au sol
    action                   "rejected" : fausse detection (exemple negatif)
                             "restored" : l'operateur est revenu sur son retrait
                             "refined"  : position corrigee a la main (x, y)
                             "accepted" : proposition validee telle quelle (x, y = ai_x, ai_y)
    x, y                     position finale quand elle existe (refined, accepted)
    source                   "aigcp_review"
"""

import datetime
import json
import os

from ... import config, journal, msapi
from . import history

SCHEMA = 1
FILE_NAME = "aigcp_feedback.jsonl"
KEPT = "kept"
REMOVED = "removed"
REFINED = "refined"
ACCEPTED = "accepted"


def feedback_path():
    return os.path.join(config.settings_dir(), "dataset", FILE_NAME)


class Review(object):
    """Les propositions d'une detection : etat de chaque entree et regroupement par marqueur."""

    def __init__(self, kind, run_id, entries):
        self.kind = kind
        self.run_id = run_id
        self.entries = list(entries)

    def labels(self):
        """Etiquettes des marqueurs, dans l'ordre de la detection."""
        seen = []
        for entry in self.entries:
            if entry["label"] not in seen:
                seen.append(entry["label"])
        return seen

    def entries_for(self, label):
        return [entry for entry in self.entries if entry["label"] == label]

    def counts(self):
        out = {KEPT: 0, REMOVED: 0, REFINED: 0, ACCEPTED: 0}
        for entry in self.entries:
            out[entry["state"]] += 1
        return out


def make_entry(item, hit, previous, crops_dir):
    """Entree de revue d'une proposition posee. `hit["crop"]` vient du runner (peut manquer)."""
    crop = hit.get("crop") or {}

    def path(key):
        name = crop.get(key)
        return os.path.join(crops_dir, name) if name else None

    return {
        "marker": item["marker"], "label": item["label"],
        "camera": item["camera"], "camera_label": msapi.camera_label(item["camera"]),
        "image_path": item["image_path"],
        "ai_x": float(hit["x"]), "ai_y": float(hit["y"]),
        "x": float(hit["x"]), "y": float(hit["y"]),
        "conf": float(hit["conf"]), "crop_px": float(hit.get("crop_px") or 0),
        "px_per_m": float(item["px_per_m"]), "previous": previous,
        "big": path("big"),
        "win": float(crop.get("win") or 0), "x0": float(crop.get("x0") or 0),
        "y0": float(crop.get("y0") or 0),
        "state": KEPT,
    }


# --------------------------------------------------------------------------
# Passage entre la vignette et la photo
# --------------------------------------------------------------------------

def view_rect(entry, zoom=1.0):
    """Carre (gauche, haut, cote), en position photo, montre a ce zoom autour du point de l'IA.

    Au zoom 1 il fait la moitie de la fenetre enregistree par le runner ; zoom 0,5 montre
    toute la fenetre, zoom 4 un huitieme de son cote.
    """
    side = entry["win"] / 2.0 / zoom
    return entry["ai_x"] - side / 2.0, entry["ai_y"] - side / 2.0, side


def to_view(entry, x, y, size, zoom=1.0):
    """Position (x, y) de la photo, en pixels d'une vignette carree de `size` px au zoom donne."""
    left, top, side = view_rect(entry, zoom)
    scale = size / side
    return (x - left) * scale, (y - top) * scale


def to_source(entry, u, v, size, zoom=1.0):
    """Pixel (u, v) d'une vignette carree de `size` px au zoom donne, en position de la photo."""
    left, top, side = view_rect(entry, zoom)
    scale = side / float(size)
    return left + u * scale, top + v * scale


def source_rect(entry, zoom, image_width):
    """Le carre de view_rect, en pixels de l'image recadree enregistree (largeur `image_width`)."""
    left, top, side = view_rect(entry, zoom)
    scale = image_width / entry["win"]
    return (left - entry["x0"]) * scale, (top - entry["y0"]) * scale, side * scale


def has_crop(entry):
    return bool(entry.get("big") and entry["win"] > 0)


# --------------------------------------------------------------------------
# Retours pour l'entrainement
# --------------------------------------------------------------------------

def record(review, entry, action, x=None, y=None):
    """Ajoute un evenement au fichier de retours. Ne leve jamais."""
    try:
        width, height = msapi.camera_size(entry["camera"])
        row = {
            "schema": SCHEMA,
            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            "run": review.run_id, "type": review.kind,
            "chunk": msapi.chunk_label(msapi.chunk()),
            "marker": entry["label"], "camera": entry["camera_label"],
            "image_path": entry["image_path"], "image_w": width, "image_h": height,
            "ai_x": round(entry["ai_x"], 2), "ai_y": round(entry["ai_y"], 2),
            "conf": round(entry["conf"], 4),
            "crop_px": round(entry["crop_px"], 1), "px_per_m": round(entry["px_per_m"], 3),
            "action": action, "source": "aigcp_review",
        }
        if x is not None and y is not None:
            row["x"], row["y"] = round(float(x), 2), round(float(y), 2)
        os.makedirs(os.path.dirname(feedback_path()), exist_ok=True)
        with open(feedback_path(), "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row
    except Exception as exc:
        try:
            journal.write("revue IA : retour non enregistre ({!r})".format(exc))
        except Exception:
            pass
        return None


# --------------------------------------------------------------------------
# Decisions de l'operateur
# --------------------------------------------------------------------------

def _forget(state, entry):
    """Retire l'entree des propositions que « Effacer » doit defaire."""
    mine = (entry["label"], msapi.camera_key(entry["camera"]))
    state.aigcp_applied = [t for t in (getattr(state, "aigcp_applied", None) or [])
                           if (msapi.marker_label(t[0]), msapi.camera_key(t[1])) != mine]


def _own(entry):
    """True si la projection de la photo est encore la proposition de l'IA (non posee)."""
    current = msapi.get_projection(entry["marker"], entry["camera"])
    return current is not None and not current[2]


def remove(state, review, entry):
    """Retire la proposition du projet : le point d'avant revient. Renvoie True si fait."""
    if entry["state"] != KEPT:
        return False
    marker, camera = entry["marker"], entry["camera"]
    own = _own(entry)
    msapi.unmark_proposal(marker, camera)
    if own:
        previous = entry["previous"]
        if previous is not None:
            msapi.set_projection(marker, camera, previous[0], previous[1], previous[2])
        else:
            msapi.remove_projection(marker, camera)
    _forget(state, entry)
    entry["state"] = REMOVED
    record(review, entry, "rejected")
    return True


def restore(state, review, entry):
    """Remet une proposition retiree. Refuse si l'operateur a pointe la photo entre temps."""
    if entry["state"] != REMOVED:
        return False
    marker, camera = entry["marker"], entry["camera"]
    current = msapi.get_projection(marker, camera)
    if current is not None and current[2]:
        return False
    done, _message = msapi.set_projection(marker, camera, entry["ai_x"], entry["ai_y"], pinned=False)
    if not done:
        return False
    msapi.mark_proposal(marker, camera)
    entry["previous"] = current
    state.aigcp_applied = list(getattr(state, "aigcp_applied", None) or []) + [(marker, camera, current)]
    entry["state"] = KEPT
    record(review, entry, "restored")
    return True


def refine(state, review, entry, x, y):
    """Remplace la proposition par un pointage manuel en (x, y). Annulable par Ctrl+Z.

    Marche sur toute entree, y compris une deja affinee ou validee : on peut la reprendre.
    """
    marker, camera = entry["marker"], entry["camera"]
    before = msapi.get_projection(marker, camera)
    chunk = msapi.chunk()
    undo = history.push(marker, camera, before, chunk_key=msapi.chunk_key(chunk),
                        label=entry["label"], camera_name=entry["camera_label"])
    done, message = msapi.set_projection(marker, camera, x, y, pinned=True)
    if not done:
        history.discard(undo)
        journal.write("revue IA : {} sur {} : {}".format(entry["label"], entry["camera_label"], message))
        return False
    msapi.unmark_proposal(marker, camera)
    _forget(state, entry)
    entry["x"], entry["y"] = float(x), float(y)
    entry["state"] = REFINED
    record(review, entry, "refined", x, y)
    return True


def accept_kept(state, review):
    """Pose en pointage manuel toutes les propositions gardees. Renvoie leur nombre."""
    done_count = 0
    for entry in review.entries:
        if entry["state"] != KEPT:
            continue
        marker, camera = entry["marker"], entry["camera"]
        if not _own(entry):
            continue          # l'operateur a deja pointe cette photo
        done, _message = msapi.set_projection(marker, camera, entry["ai_x"], entry["ai_y"], pinned=True)
        if not done:
            continue
        msapi.unmark_proposal(marker, camera)
        _forget(state, entry)
        entry["state"] = ACCEPTED
        record(review, entry, "accepted", entry["ai_x"], entry["ai_y"])
        done_count += 1
    return done_count
