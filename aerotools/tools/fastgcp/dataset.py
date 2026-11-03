"""Enregistrement optionnel des pointages manuels (jeu d'apprentissage AIGCP).

Actif seulement si l'option `collect_dataset` de la session est cochee. Chaque
pointage manuel reussi ajoute une ligne JSON au fichier :

    <config.settings_dir()>/dataset/pins.jsonl

Format : un objet JSON par ligne, UTF-8, ajout en fin de fichier. On ne copie
aucune image et on ne recadre rien : on garde des references vers les photos.

Cles d'une ligne (schema 1) :
    schema      int    version du format (1)
    ts          str    date locale ISO 8601, a la seconde
    chunk       str    etiquette du chunk
    marker      str    etiquette du marqueur (GCP)
    camera      str    etiquette de la photo
    image_path  str    chemin de l'image source tel que Metashape le connait
    image_w     int    largeur du capteur en pixels (0 si inconnue)
    image_h     int    hauteur du capteur en pixels (0 si inconnue)
    x, y        float  position pointee, en pixels, origine en haut a gauche
    source      str    "manual" pour un pointage a la souris

Un script d'entrainement relit le fichier ligne par ligne (json.loads), ignore
les lignes dont `schema` est inconnu, et rouvre l'image via `image_path`.
"""

import datetime
import json
import os

from ... import config, journal

SCHEMA = 1
FILE_NAME = "pins.jsonl"


def dataset_dir():
    return os.path.join(config.settings_dir(), "dataset")


def dataset_path():
    return os.path.join(dataset_dir(), FILE_NAME)


def count():
    """Nombre de pointages enregistres (0 si le fichier n'existe pas)."""
    try:
        total = 0
        with open(dataset_path(), "rb") as handle:
            for line in handle:
                if line.strip():
                    total += 1
        return total
    except Exception:
        return 0


def record_pin(state, record):
    """Ajoute un pointage au jeu d'apprentissage.

    `record` : dict avec chunk, marker, camera, image_path, image_w, image_h,
    x, y et eventuellement source. Renvoie le dict ecrit (avec schema et ts),
    ou None si l'option est coupee ou si l'ecriture echoue. Ne leve jamais.
    """
    try:
        if not state.option("collect_dataset"):
            return None
        row = {
            "schema": SCHEMA,
            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            "chunk": record.get("chunk", ""),
            "marker": record.get("marker", ""),
            "camera": record.get("camera", ""),
            "image_path": record.get("image_path", ""),
            "image_w": int(record.get("image_w", 0) or 0),
            "image_h": int(record.get("image_h", 0) or 0),
            "x": round(float(record["x"]), 2),
            "y": round(float(record["y"]), 2),
            "source": record.get("source", "manual"),
        }
        os.makedirs(dataset_dir(), exist_ok=True)
        with open(dataset_path(), "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row
    except Exception as exc:
        try:
            journal.write("jeu d'apprentissage : ecriture impossible ({!r})".format(exc))
        except Exception:
            pass
        return None
