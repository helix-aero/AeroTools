"""The list of the single base mode: what each row says about a station, without network and without Metashape.

    "C:\\Program Files\\Agisoft\\Metashape_v22\\python\\python.exe" -B tests_qt\\run_qt_aeroppk_baselist.py

Fills the station list (a `DataTable`) of the base step with a station with data, one with nothing that day, one whose files cannot be read,
one the servers could not be asked about, and one not asked yet, and checks the data column, the distance and whether the row can be chosen.
Prints PASS / FAIL lines.
"""

import os
import sys
import types

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import _isolation  # noqa: E402

GUARD = _isolation.Guard("aeroppk_baselist_")


def _prepare_qt_environment():
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

FAILURES = []


def check(name, condition, detail=""):
    print("{} {}{}".format("PASS" if condition else "FAIL", name, "  [{}]".format(detail) if detail else ""))
    if not condition:
        FAILURES.append(name)


class FakeOffer(object):
    def __init__(self, interval=None, error="", problem=""):
        self.interval, self.error, self.problem = interval, error, problem
        self.systems = "GR"

    def available(self):
        return self.interval is not None


def main():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    theme.load_fonts(QtGui)
    dlg = dialog.PpkDialog()
    net = dlg.screens[S.BASE].net
    station = types.SimpleNamespace(code="AJAC", name="Ajaccio", distance_m=1900.0)
    table = net.station_list.table

    def row(offer):
        """The list with one station and what the servers said about it; returns (data text, distance text, name text, tooltip, selectable)."""
        net.candidates = [station]
        net.stations = [station]
        net.offers = {} if offer is None else {station.code: offer}
        net._list_key = None
        net.refresh_list()
        item = table.item(0, 1)
        return (item.text(), table.item(0, 2).text(), table.item(0, 0).text(), table.item(0, 0).toolTip(),
                bool(table.item(0, 0).flags() & QtCore.Qt.ItemIsSelectable))

    with_data = row(FakeOffer(interval=1.0))
    check("a station with data shows its rate and distance", "1 Hz" in with_data[0] and "1.9 km" in with_data[1], str(with_data))
    check("and can be chosen", with_data[4])
    nothing = row(FakeOffer())
    check("a station with nothing that day says so", "no data that day" in nothing[0], str(nothing))
    check("and cannot be chosen", not nothing[4])
    check("its tooltip explains it", "flight day" in nothing[3], nothing[3])
    unusable = row(FakeOffer(problem="crinex"))
    check("a station whose files cannot be read says so", "not usable" in unusable[0], str(unusable))
    check("with the reason in its tooltip", "compressed RINEX" in unusable[3], unusable[3])
    unknown = row(FakeOffer(error="timeout"))
    check("a station the servers could not be asked about stays a choice", unknown[4] and "no data" not in unknown[0], str(unknown))
    waiting = row(None)
    check("a station not asked yet shows only its distance", waiting[0] == "" and waiting[1] == "1.9 km" and waiting[4], str(waiting))
    dlg.chunk_timer.stop()
    problem = GUARD.check()
    check("real settings untouched", problem is None, str(problem))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
