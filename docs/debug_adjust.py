# -*- coding: utf-8 -*-
"""Diagnostic des reglages d'image (exposition, contraste, saturation, nettete) du Review panel.

A coller dans la console de Metashape (View > Console), sous la forme d'une seule ligne :
    exec(open(r"<dossier AeroTools>\\docs\\debug_adjust.py", encoding="utf-8").read())
ou, sans fichier, la ligne `exec("...")` que fournit la personne qui t'aide.

Il ne modifie rien dans le projet. Il affiche les versions, dit si numpy est la, teste chaque reglage
sur une image de 64 x 64 pixels et, si le Review panel est ouvert, l'etat de ses curseurs.
"""
import sys
import traceback

print("=== AeroTools debug : reglages d'image ===")
try:
    import Metashape
    print("Metashape", Metashape.app.version)
except Exception as exc:
    print("Metashape ?", exc)
print("Python", sys.version.split()[0])
try:
    from PySide2 import QtCore, QtGui, QtWidgets
    print("PySide2", QtCore.__version__, "- Qt", QtCore.qVersion())
except Exception:
    from PySide6 import QtCore, QtGui, QtWidgets
    print("PySide6", QtCore.__version__, "- Qt", QtCore.qVersion())
try:
    import numpy as np
    print("numpy", np.__version__)
except Exception as exc:
    print("numpy ABSENT :", repr(exc), "  <- sans numpy les curseurs sont desactives")

try:
    from aerotools.tools.fastgcp import imageadjust
    print("imageadjust.available() =", imageadjust.available())
    image = QtGui.QImage(64, 64, QtGui.QImage.Format_RGB32)
    image.fill(QtGui.QColor(120, 90, 60))
    for name in ("exposure", "contrast", "saturation", "sharpness"):
        try:
            out = imageadjust.apply_image(image, {name: 50})
            before = QtGui.QColor(image.pixel(10, 10))
            after = QtGui.QColor(out.pixel(10, 10))
            b = (before.red(), before.green(), before.blue())
            a = (after.red(), after.green(), after.blue())
            verdict = "OK" if (a != b or name == "sharpness") else "SANS EFFET"
            print("  %-10s %-11s %s -> %s" % (name, verdict, b, a))
        except Exception:
            print("  %-10s ERREUR" % name)
            traceback.print_exc()
except Exception:
    print("Impossible d'importer aerotools.tools.fastgcp.imageadjust :")
    traceback.print_exc()

try:
    from aerotools.tools.fastgcp import session
    state = session.current()
    panel = getattr(state, "panel", None)
    dialog = getattr(panel, "_review_dialog", None) if panel is not None else None
    if dialog is None:
        print("Review panel : pas ouvert (ouvre-le avec le bouton Review, puis relance ce diagnostic).")
    else:
        print("Review panel ouvert. Reglages :", dialog.adjust.values)
        print("  abonnes du modele :", len(dialog.adjust._subscribers), "| minuteur actif :", dialog._refresh.isActive())
        for key, slider in dialog.panel._sliders.items():
            print("  curseur %-10s actif=%s valeur=%s" % (key, slider.isEnabled(), slider.value()))
        slider = dialog.panel._sliders["exposure"]
        if slider.isEnabled():
            original = slider.value()
            slider.setValue(40 if original != 40 else 30)
            QtWidgets.QApplication.processEvents()
            print("  apres un essai d'exposition : modele =", dialog.adjust.values["exposure"])
            slider.setValue(original)                 # on remet le reglage de l'operateur
            QtWidgets.QApplication.processEvents()
        else:
            print("  le curseur d'exposition est DESACTIVE (numpy absent ?)")
except Exception:
    print("Etat du Review panel illisible :")
    traceback.print_exc()
print("=== fin du diagnostic : copie tout ce qui precede et envoie-le ===")
