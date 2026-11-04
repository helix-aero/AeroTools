"""AeroTools - suite d'outils HELIX AERO pour Agisoft Metashape.

Le menu « Aero » est pose au demarrage de Metashape par aerotools.bootstrap,
appele depuis un lanceur minuscule installe sur chaque poste
(%LOCALAPPDATA%\\Agisoft\\Metashape Pro\\scripts\\aerotools_launcher.py).
Tout le reste du code vit sur le serveur, partage par les deux postes.

Outils :
  - fastgcp : pointage manuel assiste des GCP (le premier outil).
  - aigcp    : detection automatique des cibles (a venir, voir detection.py).
"""

__version__ = "0.2.0"
__all__ = ["bootstrap", "config", "i18n", "theme", "journal", "msapi"]
