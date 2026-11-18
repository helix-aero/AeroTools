"""Aero-Exports window on the real Windows window system: no "Unable to set geometry" warning in the console.

Metashape prints Qt's warnings in its console. While a user dragged the edge of the Aero-Exports window, Windows refused every height
between the least size the window declared and what its wrapped texts really needed ("QWindowsWindow::setGeometry: Unable to set geometry
1000x925 ... Resulting geometry: 1000x967"), and a window could be taller than the screen. The offscreen platform never says so, so this
harness uses the `windows` platform (it opens real windows for a few seconds) and is skipped anywhere else.

    python -B tests_qt\\run_qt_window_geometry.py

Checks, on the tallest states of the window (geoid warning, coordinates open, result banner, a list of planned exports):
  - dragging the bottom edge up at several widths, and widening, makes no warning;
  - content changes (a banner, the warning appearing and going, a section opening) make no warning;
  - the least size the window declares fits the screen.

And on a generic `ToolWindow` whose content is taller than any screen (a page that cannot shrink, wrapped texts, a banner, a page change),
at 100 % and 150 % and on simulated work areas of 1920x1040 and 1366x728:
  - no refused geometry, the declared least size never exceeds the work area, the window never grows past it by itself,
  - the window drawn by Qt and the native window have the same size (what is drawn is what answers the clicks).
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
if os.name != "nt":
    print("SKIP the real Windows window system is not available here")
    sys.exit(0)
import _isolation  # noqa: E402
GUARD = _isolation.Guard("aerotools_qt_geom_")
os.environ["QT_QPA_PLATFORM"] = "windows"

FAKE = types.ModuleType("Metashape")
FAKE.app = types.SimpleNamespace(version="2.2.1-selftest", document=types.SimpleNamespace(chunk=None),
                                 addMenuItem=lambda *a, **k: None, messageBox=lambda text: None, update=lambda: None)
sys.modules["Metashape"] = FAKE

from aerotools import config, journal, msapi  # noqa: E402
GUARD.pin(config)
from aerotools.qt import QtCore, QtWidgets  # noqa: E402
from aerotools.tools.aeroexports import dialog  # noqa: E402

journal.print = lambda *args, **kwargs: None
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
WARNINGS = []


def handler(mode, context, message):
    if "Unable to set geometry" in message or "setGeometry" in message:
        WARNINGS.append(message)


QtCore.qInstallMessageHandler(handler)
LONG = ("The grid for RGF93 v1 / Lambert-93 + NGF-IGN69 height (RAF20) is not on this computer (25 MB to download). "
        "AeroTools needs it to convert heights.")


def pump(milliseconds=60):
    end = time.time() + milliseconds / 1000.0
    while time.time() < end:
        APP.processEvents()
        time.sleep(0.004)


def native_size(window):
    """(width, height) of the client area of the native window, from the window system itself."""
    import ctypes
    from ctypes import wintypes
    rect = wintypes.RECT()
    ctypes.windll.user32.GetClientRect(wintypes.HWND(int(window.winId())), ctypes.byref(rect))
    return rect.right - rect.left, rect.bottom - rect.top


def generic_cases():
    """A `ToolWindow` with content that is taller than the screen, put through the changes that used to push it past the work area."""
    from aerotools.uikit import metrics, shell
    problems = []
    real = shell.screen_area

    class Page(QtWidgets.QWidget):
        def __init__(self, height):
            super(Page, self).__init__()
            lay = QtWidgets.QVBoxLayout(self)
            self.text = QtWidgets.QLabel("A paragraph of wrapped text that needs more height when the window is narrow. " * 6)
            self.text.setWordWrap(True)                      # a text that wraps: the layout then has a height for width
            lay.addWidget(self.text)
            self.block = QtWidgets.QWidget()
            self.block.setMinimumHeight(height)
            lay.addWidget(self.block, 1)

    class Window(shell.ToolWindow):
        DESIGN_SIZE = (1320, 860)

    try:
        for scale in (1.0, 1.5):
            metrics.set_scale(scale)
            for size in ((1920, 1040), (1366, 728)):
                area = QtCore.QRect(0, 0, size[0], size[1])
                shell.screen_area = lambda widget=None, a=area: a
                tag = "{}% {}x{}".format(int(scale * 100), size[0], size[1])
                window = Window(title="Geometry", wizard=True)
                first = Page(300)
                window.stack.addWidget(first)
                window.show()
                pump(250)
                del WARNINGS[:]

                def check(label):
                    pump(200)
                    room = (area.width(), area.height())
                    if WARNINGS:
                        problems.append("{} {}: {}".format(tag, label, WARNINGS[0][:160]))
                        del WARNINGS[:]
                    if window.minimumWidth() > room[0] or window.minimumHeight() > room[1]:
                        problems.append("{} {}: the least size {}x{} is larger than the work area".format(
                            tag, label, window.minimumWidth(), window.minimumHeight()))
                    if window.width() > room[0] or window.height() > room[1]:
                        problems.append("{} {}: the window {}x{} is larger than the work area".format(
                            tag, label, window.width(), window.height()))
                    qt_size, native = (window.width(), window.height()), native_size(window)
                    if qt_size != native:
                        problems.append("{} {}: Qt thinks {} but the native window is {}".format(tag, label, qt_size, native))

                check("start")
                before = (window.width(), window.height())
                first.block.setMinimumHeight(1100)            # the content grows past any screen (a table, a history, a long list)
                first.updateGeometry()
                check("content taller than the screen")
                window.show_banner("success", "2 files written to " + "C:\\" + "A_very_long_folder_name_" * 8 + "\file.tif")
                check("a banner")
                second = Page(1400)
                window.stack.addWidget(second)
                window.stack.setCurrentWidget(second)
                check("another page, taller still")
                window.stack.setCurrentWidget(first)
                window.clear_banner()
                check("back")
                first.block.setMinimumHeight(200)
                first.updateGeometry()
                second.block.setMinimumHeight(200)
                check("content shorter")
                for height in range(window.height() + 20, window.minimumHeight() - 40, -23):      # a drag on the bottom edge
                    window.resize(window.width(), height)
                    pump(10)
                check("dragging the bottom edge")
                if before != (window.width(), window.height()) and window.width() > area.width():
                    problems.append("{}: the window changed size past the work area".format(tag))
                window.close()
                pump(60)
    finally:
        shell.screen_area = real
        metrics.set_scale(None)
    return problems


def main():
    path = os.path.join(tempfile.mkdtemp(prefix="aerotools_geom_"), "SITE.psx")
    open(path, "w").write("x")
    chunk = object()
    msapi.document_path = lambda: path
    msapi.chunk = lambda: chunk
    msapi.boundary_count = lambda c: 1
    msapi.raster_info = lambda c=None: {
        "ortho": (0.05, 4000, 3000), "dem": (0.2, 1000, 800), "box_ortho": (0, 0, 200, 150), "box_dem": (0, 0, 200, 160),
        "crs": "RGF93 v1 / Lambert-93", "dates": [], "crs_object": None, "center": None,
        "ortho_default": {"key": 1, "label": "o", "width": 4000, "height": 3000, "resolution": 0.05},
        "dem_default": {"key": 2, "label": "d", "width": 1000, "height": 800, "resolution": 0.2}, "n_dems": 3, "n_orthos": 2,
        "boundaries": 1}
    box = dialog.ExportDialog(None)
    box.show()
    box.refresh()
    box.site.setText("SITE")
    pump(300)
    area = APP.primaryScreen().availableGeometry()
    problems = []

    def settle(label):
        pump(120)
        if WARNINGS:
            problems.append("{}: {}".format(label, WARNINGS[0][:200]))
            del WARNINGS[:]

    # content changes: each one asks the window for another height
    box.geoid_card.show_message("warning", LONG, None, ("Download", lambda: None), None, True)
    settle("the warning appears")
    box.coords.set_open(True)
    settle("coordinates open")
    box.show_banner("success", "3 exports done · 7 files, 1.2 GB")
    settle("a result banner")
    box.show_banner("danger", "SITE_A_300112_14mm_dtm.tif: " + "X" * 90)
    settle("a long unbreakable error")
    box.clear_banner()
    box.geoid_card.hide()
    settle("both go away")
    box.geoid_card.show_message("warning", LONG, None, ("Download", lambda: None), None, True)
    box.coords.set_open(True)
    settle("the tallest state")
    # the user drags the bottom edge up at several widths, then widens it
    for width in (box.width(), 900, 1100, box.minimumWidth() + 20):
        box.resize(width, box.height())
        settle("width {}".format(width))
        for height in range(box.height() + 30, box.minimumHeight() - 60, -11):
            box.resize(width, height)
            pump(15)
        settle("dragging up at {}".format(width))
    fits = box.minimumHeight() <= area.height() and box.minimumWidth() <= area.width()
    if not fits:
        problems.append("the least size {}x{} does not fit the screen {}x{}".format(box.minimumWidth(), box.minimumHeight(),
                                                                                   area.width(), area.height()))
    native = native_size(box)
    if (box.width(), box.height()) != native:
        problems.append("Aero-Exports: Qt thinks {} but the native window is {}".format((box.width(), box.height()), native))
    box.close()
    return problems + generic_cases()


if __name__ == "__main__":
    found = main()
    for line in found:
        print("FAIL " + line)
    print("PASS no refused geometry on the real window system" if not found else "{} problem(s)".format(len(found)))
    sys.stdout.flush()
    os._exit(1 if found else 0)
