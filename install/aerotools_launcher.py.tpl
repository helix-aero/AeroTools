# AeroTools - lanceur pose par installer_aerotools.bat. Ne pas modifier a la main.
# Metashape execute ce fichier a chaque demarrage. Le chemin ci-dessous est ecrit
# a l'installation (__file__ n'est pas fiable dans ce contexte).
import os
import sys

try:
    _aero_root = r"@@AEROTOOLS_ROOT@@"
    if not os.path.isdir(os.path.join(_aero_root, "aerotools")):
        print("[AeroTools] dossier introuvable (serveur injoignable ?) : " + _aero_root)
    else:
        if _aero_root not in sys.path:
            sys.path.insert(0, _aero_root)
        from aerotools import bootstrap
        bootstrap.install_menu()
except Exception as _aero_exc:
    print("[AeroTools] demarrage impossible : {!r}".format(_aero_exc))
