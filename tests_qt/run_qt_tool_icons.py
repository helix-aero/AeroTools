"""Tool icons: each icon draws something at 16 / 24 / 48 px, none look alike; renders the sheet on the three skins.

    python -B tests_qt\run_qt_tool_icons.py [--sheet PNG]
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import _isolation  # noqa: E402
GUARD = _isolation.Guard("aerotools_qt_toolicons_")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from aerotools import config  # noqa: E402
GUARD.pin(config)
from aerotools.qt import QtCore, QtGui, QtWidgets  # noqa: E402
from aerotools.uikit import icons, metrics, tokens  # noqa: E402

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
FAILURES = []


def check(ok, text):
    print(("ok   " if ok else "FAIL ") + text)
    if not ok:
        FAILURES.append(text)


def ink(image):
    return sum(1 for y in range(image.height()) for x in range(image.width()) if QtGui.QColor.fromRgba(image.pixel(x, y)).alpha() > 40)


def run_checks():
    metrics.set_scale(1.0)
    check(icons.TOOLS[-1] == "suite" and len(set(icons.TOOLS)) == len(icons.TOOLS) >= 4, "one icon per tool, suite last")
    for name in icons.TOOL_NAMES + icons.GLYPH_NAMES:
        check(name in icons.NAMES, name + " is listed")
        for size in (16, 24, 48):
            img = icons.pixmap(name, "#202020", size, dpr=1.0).toImage()
            check(ink(img) > size, "%s draws at %d px" % (name, size))
    sigs = {}
    for key in icons.TOOLS:
        for prefix in ("glyph_", "tool_"):
            img = icons.pixmap(prefix + key, "#202020", 48, dpr=1.0).toImage()
            sigs[prefix + key] = bytes(img.constBits())
    check(len(set(sigs.values())) == len(sigs), "no two icons are alike")


def sheet(path):
    cell = 120
    names = icons.TOOLS
    skins = ("dark", "medium", "light")
    width, height = cell * len(names) + 20, 140 * len(skins)
    out = QtGui.QImage(width, height, QtGui.QImage.Format_ARGB32)
    p = QtGui.QPainter(out)
    for si, skin in enumerate(skins):
        tokens.use(skin)
        top = si * 140
        p.fillRect(0, top, width, 140, QtGui.QColor(tokens.BG))
        for i, key in enumerate(names):
            x = 10 + i * cell
            # 16 / 24 px tiles and glyphs, then 48 px
            for j, (size, y) in enumerate(((16, 12), (24, 40), (48, 76))):
                p.drawPixmap(x + 4, top + y, icons.pixmap("tool_" + key, tokens.TEXT, size, dpr=1.0))
                p.drawPixmap(x + 4 + (size if size < 48 else 52) + 6, top + y, icons.pixmap("glyph_" + key, tokens.TEXT, size, dpr=1.0))
    p.end()
    tokens.use("dark")
    out.save(path)
    print("sheet", path)


if __name__ == "__main__":
    run_checks()
    if "--sheet" in sys.argv:
        sheet(sys.argv[sys.argv.index("--sheet") + 1])
    print("%d checks failed" % len(FAILURES))
    GUARD.finish() if hasattr(GUARD, "finish") else None
    sys.exit(1 if FAILURES else 0)
