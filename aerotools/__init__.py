"""AeroTools - HELIX AERO tool suite for Agisoft Metashape.

The "Aero" menu is installed when Metashape starts, by aerotools.bootstrap,
called from a tiny launcher installed on each workstation
(%LOCALAPPDATA%\\Agisoft\\Metashape Pro\\scripts\\aerotools_launcher.py).
All the rest of the code lives in the AeroTools folder the launcher points to.

Tools:
  - fastgcp : assisted manual picking of GCPs (the first tool).
  - aigcp    : automatic target detection (upcoming, see detection.py).
"""

__version__ = "0.5.0"
__all__ = ["bootstrap", "config", "i18n", "theme", "journal", "msapi"]
