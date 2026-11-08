# AeroTools - launcher written by install_aerotools.bat. Do not edit by hand.
# Metashape runs this file at every start. The path below is written at install
# time (__file__ is not reliable in this context).
import os
import sys

try:
    _aero_root = r"@@AEROTOOLS_ROOT@@"
    if not os.path.isdir(os.path.join(_aero_root, "aerotools")):
        print("[AeroTools] folder not found (moved or deleted?): " + _aero_root)
    else:
        if _aero_root not in sys.path:
            sys.path.insert(0, _aero_root)
        from aerotools import bootstrap
        bootstrap.install_menu()
except Exception as _aero_exc:
    print("[AeroTools] startup failed: {!r}".format(_aero_exc))
