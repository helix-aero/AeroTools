"""Point d'accroche de la detection automatique des cibles (AIGCP).

Ce module ne contient aucune IA : il fixe les types par lesquels un detecteur
se branche. Le detecteur AIGCP (damier, bombe) est dans aigcp.py ; le panneau le
pilote directement (bouton « Detection IA »), de facon asynchrone.

Contrat fichier d'AIGCP (voir aigcp.py et aigcp_runner.py) :
  - FastGCP ecrit un `work.json` : poids du modele, seuil de confiance, et une
    liste d'items {id, image_path, x_est, y_est, px_per_m}.
  - Un executable separe, dans un environnement Python externe, lit ce fichier
    et ecrit `hits.json` : liste de {id, found, x, y, conf, crop_px}.
  - torch, ultralytics et CUDA ne sont JAMAIS importes dans le Python de
    Metashape : tout le calcul passe par cet executable et ces deux fichiers.
  - Une projection posee par l'IA l'est avec `pinned=False`, une projection
    manuelle avec `pinned=True` (msapi.set_projection).

Un detecteur se branche ainsi :
    from aerotools.tools.fastgcp import detection
    detection.register(MonDetecteur())
"""

import collections

from ... import journal

# Une proposition de pointage faite par un detecteur.
#   marker_label : etiquette du marqueur concerne
#   camera_key   : cle (camera.key) de la photo
#   x, y         : pixels capteur
#   confidence   : 0.0 a 1.0
#   source       : nom du detecteur qui l'a produite
Proposal = collections.namedtuple(
    "Proposal", ["marker_label", "camera_key", "x", "y", "confidence", "source"]
)


class Detector(object):
    """Base d'un detecteur de cibles. Ne fait rien par defaut."""

    name = "detecteur"

    def status(self):
        """(ok, message) : le detecteur est-il pret a servir ?"""
        return True, ""

    def propose(self, chunk, marker, cameras):
        """Liste de `Proposal` pour ce marqueur sur ces photos (vide ici)."""
        return []

    def on_pin_placed(self, record):
        """Appele apres chaque pointage manuel. `record` : dict, voir dataset.py."""
        return None


class NullDetector(Detector):
    """Detecteur par defaut : aucun, tant qu'AIGCP n'est pas installe."""

    name = "aucun"

    def status(self):
        return False, "Aucun detecteur installe (AIGCP a venir)"


_NULL = NullDetector()
_active = _NULL


def register(detector):
    """Installe `detector` comme detecteur courant. `None` revient au NullDetector."""
    global _active
    _active = detector if detector is not None else _NULL
    return _active


def active():
    """Detecteur courant (NullDetector si aucun n'est enregistre)."""
    return _active


def notify_pin(record):
    """Previent le detecteur courant d'un pointage manuel. Ne leve jamais."""
    try:
        active().on_pin_placed(record)
    except Exception as exc:
        journal.write("detecteur : {!r}".format(exc))
