"""Aero-PPK window on the kit's window shell: its size and place on screens of different sizes. Fake screens.

    "C:\\Program Files\\Agisoft\\Metashape_v22\\python\\python.exe" -B tests_qt\\run_qt_aeroppk_window.py [out folder]

The usable area the shell sees (`uikit.shell.screen_area`) is replaced by made-up ones: a 1366 x 768 laptop with the taskbar at the bottom, on
the left and on top, a full HD screen, and a second screen to the right of the first. For each: the window opens at its design size, or at what
the screen allows (never less than its content needs), centred, with its whole frame inside the usable area; it stays inside when the biggest
message (an error banner with a Retry button) is shown on every step; it can be made bigger, not smaller than its content; opening it again raises
the same window. Screenshots go to the out folder. Settings go to a temporary LOCALAPPDATA. Prints PASS / FAIL lines.
"""

import os
import sys
import tempfile
import time
import types

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import _isolation  # noqa: E402

GUARD = _isolation.Guard("aeroppk_window_")
OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="aeroppk_window_out_")
os.makedirs(OUT, exist_ok=True)


def _prepare_qt_environment():
    """Metashape DLLs when running inside its Python; the offscreen platform only when its plugin exists (else Qt would hang)."""
    metashape_dir = os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))
    offscreen = True
    if os.path.exists(os.path.join(metashape_dir, "Qt5Core.dll")):
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(metashape_dir)
        os.environ.setdefault("QT_PLUGIN_PATH", os.path.join(metashape_dir, "plugins"))
        offscreen = os.path.exists(os.path.join(metashape_dir, "plugins", "platforms", "qoffscreen.dll"))
    if offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"

    if os.environ.get("QT_QPA_PLATFORM") == "offscreen" and os.path.isdir(r"C:\Windows\Fonts"):
        os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")      # (the offscreen platform finds no font by itself: text would have no size)

_prepare_qt_environment()
meta = types.ModuleType("Metashape")
meta.app = types.SimpleNamespace(document=None, update=lambda: None)
meta.Vector = lambda *a: a
sys.modules["Metashape"] = meta

from aerotools import config, theme  # noqa: E402
from aerotools.qt import QtCore, QtGui, QtWidgets  # noqa: E402

GUARD.pin(config)
from aerotools.tools.aeroppk import dialog, state as S  # noqa: E402
from aerotools.uikit import metrics, shell, tokens  # noqa: E402

FAILURES = []
FAKE = {"area": (0, 0, 1920, 1040)}


def check(name, condition, detail=""):
    print("{} {}{}".format("PASS" if condition else "FAIL", name, "  [{}]".format(detail) if detail else ""))
    if not condition:
        FAILURES.append(name)


def pump(app, seconds=0.2):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def shot(app, widget, name):
    pump(app, 0.3)
    path = os.path.join(OUT, name + ".png")
    widget.grab().save(path)
    print("shot", path)


def frame(dlg):
    g = dlg.frameGeometry()
    return (g.x(), g.y(), g.width(), g.height())


def inside(rect, area):
    return area[0] <= rect[0] and rect[0] + rect[2] <= area[0] + area[2] and area[1] <= rect[1] and rect[1] + rect[3] <= area[1] + area[3]


def scenario(app, name, area):
    """One screen: open, look at size and place, show the biggest message on every step, resize."""
    FAKE["area"] = area
    dlg = dialog.PpkDialog()
    dlg.reject = lambda: None
    dlg.show()
    pump(app, 0.5)
    try:
        need = dlg.layout().totalMinimumSize()
        room = (area[2] - metrics.px(tokens.SCREEN_MARGIN), area[3] - metrics.px(tokens.SCREEN_MARGIN))
        expected = (min(max(metrics.px(dialog.DESIGN[0]), need.width()), room[0]), min(max(metrics.px(dialog.DESIGN[1]), need.height()), room[1]))
        check("%s: the size is the design size or what the screen allows (never less than the content needs)" % name,
              (dlg.width(), dlg.height()) == expected, "%dx%d, expected %dx%d" % (dlg.width(), dlg.height(), expected[0], expected[1]))
        check("%s: the content needs no more than the screen gives" % name, need.width() <= room[0] and need.height() <= room[1],
              "%dx%d for %dx%d" % (need.width(), need.height(), room[0], room[1]))
        f = frame(dlg)
        check("%s: the whole frame is inside the usable area" % name, inside(f, area), "%s in %s" % (f, area))
        gap_left, gap_right = f[0] - area[0], area[0] + area[2] - (f[0] + f[2])
        gap_top, gap_bottom = f[1] - area[1], area[1] + area[3] - (f[1] + f[3])
        check("%s: it is centred" % name, abs(gap_left - gap_right) <= 8 and abs(gap_top - gap_bottom) <= 8,           # (the shell centres the content, the frame adds a few pixels)
              "left %d right %d top %d bottom %d" % (gap_left, gap_right, gap_top, gap_bottom))
        shot(app, dlg, name.replace(" ", "_") + "_opened")
        # the biggest message of the kit (an error with a title, a Retry and details) on every step: the window stays inside the area
        for step in S.STEPS:
            dlg.go(step, force=True)
            dlg.show_error(RuntimeError("Something failed"), retry=lambda: None, title="That did not work", trace="Traceback\n  line")
            pump(app, 0.3)
            f = frame(dlg)
            check("%s: with an error banner on step %d the window is inside the usable area" % (name, step + 1), inside(f, area), "%s in %s" % (f, area))
            dlg.clear_banner()
        shot(app, dlg, name.replace(" ", "_") + "_banner")
        # a user may make it bigger, never smaller than its content
        before = dlg.size()
        dlg.resize(dlg.width() + 100, dlg.height() + 40)
        pump(app, 0.2)
        check("%s: it can be made bigger" % name, dlg.width() > before.width() and dlg.height() > before.height(), "%dx%d" % (dlg.width(), dlg.height()))
        dlg.resize(300, 200)
        pump(app, 0.2)
        minimum = dlg.layout().totalMinimumSize()
        check("%s: it cannot be made smaller than its content" % name, dlg.width() >= minimum.width() and dlg.height() >= minimum.height(),
              "%dx%d for %dx%d" % (dlg.width(), dlg.height(), minimum.width(), minimum.height()))
    finally:
        dlg.chunk_timer.stop()
        QtWidgets.QWidget.hide(dlg)
        dlg.deleteLater()
        pump(app, 0.2)


def main():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    theme.load_fonts(QtGui)
    real = shell.screen_area
    shell.screen_area = lambda widget=None: QtCore.QRect(*FAKE["area"])
    scenario(app, "full HD, taskbar at the bottom", (0, 0, 1920, 1040))
    scenario(app, "laptop 1366x768, taskbar at the bottom", (0, 0, 1366, 728))
    scenario(app, "laptop 1366x768, taskbar on the left", (48, 0, 1318, 768))
    scenario(app, "laptop 1366x768, taskbar on top", (0, 40, 1366, 728))
    scenario(app, "second screen, right of the first", (1366, 0, 1366, 728))
    # one window: opening it again raises the same one
    shell.screen_area = real
    first = dialog.PpkDialog.open(None)
    pump(app, 0.3)
    second = dialog.PpkDialog.open(None)
    check("opening it again raises the same window", first is second and first.isVisible())
    first.chunk_timer.stop()
    QtWidgets.QWidget.hide(first)
    dialog.PpkDialog.forget()
    first.deleteLater()
    pump(app, 0.2)
    problem = GUARD.check()
    check("real settings untouched", problem is None, str(problem))
    print("out:", OUT)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
