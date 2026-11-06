# -*- coding: utf-8 -*-
"""Diagnostic of the image adjustments (exposure, contrast, saturation, sharpness) of the Review panel.

Paste into the Metashape console (View > Console), as a single line:
    exec(open(r"<AeroTools folder>\\docs\\debug_adjust.py", encoding="utf-8").read())
or, without a file, the `exec("...")` line given to you by the person who is helping you.

It changes nothing in the project. It prints the versions, says whether numpy is present, tests each adjustment
on a 64 x 64 pixel image and, if the Review panel is open, the state of its sliders.
"""
import sys
import traceback

print("=== AeroTools debug: image adjustments ===")
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
    print("numpy MISSING:", repr(exc), "  <- without numpy the sliders are disabled")

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
            verdict = "OK" if (a != b or name == "sharpness") else "NO EFFECT"
            print("  %-10s %-11s %s -> %s" % (name, verdict, b, a))
        except Exception:
            print("  %-10s ERROR" % name)
            traceback.print_exc()
except Exception:
    print("Cannot import aerotools.tools.fastgcp.imageadjust:")
    traceback.print_exc()

try:
    from aerotools.tools.fastgcp import session
    state = session.current()
    panel = getattr(state, "panel", None)
    dialog = getattr(panel, "_review_dialog", None) if panel is not None else None
    if dialog is None:
        print("Review panel: not open (open it with the Review button, then run this diagnostic again).")
    else:
        print("Review panel open. Adjustments:", dialog.adjust.values)
        print("  model subscribers:", len(dialog.adjust._subscribers), "| timer active:", dialog._refresh.isActive())
        for key, slider in dialog.panel._sliders.items():
            print("  slider %-10s enabled=%s value=%s" % (key, slider.isEnabled(), slider.value()))
        slider = dialog.panel._sliders["exposure"]
        if slider.isEnabled():
            original = slider.value()
            slider.setValue(40 if original != 40 else 30)
            QtWidgets.QApplication.processEvents()
            print("  after an exposure test: model =", dialog.adjust.values["exposure"])
            slider.setValue(original)                 # put the operator's setting back
            QtWidgets.QApplication.processEvents()
        else:
            print("  the exposure slider is DISABLED (numpy missing?)")
except Exception:
    print("Review panel state unreadable:")
    traceback.print_exc()
print("=== end of the diagnostic: copy everything above and send it ===")
