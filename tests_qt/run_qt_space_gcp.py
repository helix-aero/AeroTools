"""Qt self-test of two windows whose state handling was fixed: the Aero-Space batch and the Aero-GCP Adjust window.

Run (no pytest required):
    python -B tests_qt\\run_qt_space_gcp.py

Works with the Python embedded in Metashape (PySide2) as well as with a Python equipped with PySide6, on the offscreen platform.
A fake `Metashape` module is injected; the Metashape documents of Aero-Space are the doubles of tests\\test_fastspace.py.
Output: one PASS / FAIL line per case, non-zero exit code on any FAIL.
"""

import os
import sys
import tempfile
import time
import traceback
import types

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for folder in (ROOT, HERE, os.path.join(ROOT, "tests")):
    if folder not in sys.path:
        sys.path.insert(0, folder)
import _isolation  # noqa: E402
GUARD = _isolation.Guard("aerotools_qt_space_gcp_")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

fake = types.ModuleType("Metashape")
fake.app = types.SimpleNamespace(document=None, update=lambda: None)
fake.Vector = lambda *a: a
sys.modules["Metashape"] = fake

from aerotools import config, i18n  # noqa: E402
GUARD.pin(config)
from aerotools.qt import QtCore, QtGui, QtWidgets  # noqa: E402

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
RESULTS = []


def case(name):
    def wrap(function):
        try:
            function()
            RESULTS.append((name, None))
            print("PASS  " + name)
        except Exception:
            RESULTS.append((name, traceback.format_exc()))
            print("FAIL  " + name + "\n" + traceback.format_exc())
        return function
    return wrap


def pump(until, seconds=15.0):
    end = time.time() + seconds
    while not until() and time.time() < end:
        APP.processEvents()
        time.sleep(0.01)
    return until()


# --------------------------------------------------------------------------
# Aero-Space
# --------------------------------------------------------------------------

from aerotools.tools.fastspace import actions, dialog as space_dialog, scanner, tool as space_tool  # noqa: E402
from test_fastspace import FakeCloud, FakeDocument, make_project  # noqa: E402
REAL_CONFIRM = space_dialog.ConfirmDialog                                   # the batch cases swap it for a stub


class StubConfirm(object):
    """Stands for the modal confirmation window: nobody is there to click."""
    mode, items, allow_upgrade = actions.PERMANENT, ["dense", "depth"], False

    def __init__(self, *args, **kwargs):
        pass


def make_dialog(tmp, **projects):
    window = space_dialog.SpaceDialog()
    window.projects = [scanner.measure_project(make_project(tmp, name, **kw)) for name, kw in projects.items()]
    window._fill_table()
    return window


def check_all(window):
    window.table.blockSignals(True)
    for row in range(window.table.rowCount()):
        window.table.item(row, 0).setCheckState(QtCore.Qt.Checked)
    window.table.blockSignals(False)


def run_batch(window, kind, mode, items=("dense", "depth")):
    StubConfirm.mode, StubConfirm.items = mode, list(items)
    space_dialog.ConfirmDialog = StubConfirm
    space_dialog._exec = lambda dlg: None
    picked = window._checked()
    window._confirm_and_run(kind, picked, "text")
    return picked


def fake_documents(imported=(), empty=()):
    memory = {}

    def fake_open(path, read_only):
        document = memory.get(path)
        if document is None:
            document = FakeDocument(path, True)
            if os.path.basename(path) in imported:
                document.chunks[0].point_clouds = [FakeCloud("las", {"import_points": "las"})]
                document.chunks[0].depth_maps_sets = []
            if os.path.basename(path) in empty:
                document.chunks[0].point_clouds, document.chunks[0].depth_maps_sets = [], []
            memory[path] = document
        return document

    actions._open = fake_open


@case("Aero-Space: a Lighten batch runs project by project, keeps an imported cloud, skips a project with nothing to remove")
def _():
    tmp = tempfile.mkdtemp()
    fake_documents(imported=("keep.psx",), empty=("empty.psx",))
    window = make_dialog(tmp, plain=dict(dense=1000, depth=3000), keep=dict(dense=1000, depth=0), empty=dict(dense=0, depth=0))
    check_all(window)
    run_batch(window, "lighten", actions.PERMANENT)
    assert pump(lambda: window._batch is None), "the batch did not finish"
    status = window.status.fullText()
    assert "3 projects processed" in status, status
    assert "not built by Metashape" in status, status
    by_name = {p.name: p for p in window.projects}
    assert by_name["plain.psx"].removable() == 0
    assert by_name["keep.psx"].dense == 1000                         # the imported cloud and its folder are still there
    assert "had nothing to remove" in status, status
    for name in ("empty", "keep"):                                   # not saved, so no safety copy either
        assert not [n for n in os.listdir(os.path.dirname(by_name[name + ".psx"].path)) if actions.BACKUP_MARK in n], name
    window.close()


@case("Aero-Space: closing the window and Reload (uninstall) are refused while a batch runs")
def _():
    tmp = tempfile.mkdtemp()
    fake_documents()
    window = make_dialog(tmp, one=dict(dense=1000, depth=3000), two=dict(dense=1000, depth=3000))
    check_all(window)
    space_tool._STATE["dialog"] = window
    seen = {}

    def during():                                                    # lightening is a task of the window itself: ask from inside it
        window.close()
        seen["running"] = window._batch is not None
        seen["reload"] = space_tool.uninstall()

    QtCore.QTimer.singleShot(0, during)
    run_batch(window, "lighten", actions.PERMANENT)
    assert seen.get("running"), "the batch was not running when the window was closed (close() ended it?)"
    assert seen["reload"] is False and space_tool._STATE["dialog"] is window
    assert pump(lambda: window._batch is None)
    assert space_tool.uninstall() is True and space_tool._STATE["dialog"] is None


@case("Aero-Space: Cancel stops a Lighten batch before the next project and says how many were left")
def _():
    tmp = tempfile.mkdtemp()
    fake_documents()
    window = make_dialog(tmp, one=dict(dense=1000, depth=3000), two=dict(dense=1000, depth=3000))
    check_all(window)
    QtCore.QTimer.singleShot(0, window.cancel_task)                   # pressed while the first project is being lightened
    run_batch(window, "lighten", actions.PERMANENT)
    assert pump(lambda: window._batch is None)
    status = window.status.fullText()
    assert "1 project processed" in status and "Stopped: 1 project not processed" in status, status
    window.close()


@case("Aero-Space: a permanent delete batch removes the rows and the files, in a worker thread")
def _():
    tmp = tempfile.mkdtemp()
    window = make_dialog(tmp, one=dict(), two=dict())
    check_all(window)
    paths = [p.path for p in window._checked()]
    run_batch(window, "delete", actions.PERMANENT, items=())
    assert pump(lambda: window._batch is None)
    assert window.table.rowCount() == 0 and not any(os.path.exists(p) for p in paths)
    assert "2 projects processed" in window.status.fullText(), window.status.fullText()
    window.close()


@case("Aero-Space: a recycle delete that did not reach the bin is reported as such, not as recoverable")
def _():
    tmp = tempfile.mkdtemp()
    window = make_dialog(tmp, one=dict())
    check_all(window)
    import shutil

    def fake_shfileop(paths, hwnd=0):
        for path in paths:
            shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
        return 0, False, {}

    actions._shfileop = fake_shfileop
    run_batch(window, "delete", actions.RECYCLE, items=())
    assert pump(lambda: window._batch is None)
    text = window.status.fullText()
    assert "0.00 GB in the Recycle Bin" in text or "0 MB in the Recycle Bin" in text, text
    assert "could not be confirmed" in text, text
    window.close()


@case("Aero-Space: a scan stopped half way says the list is partial")
def _():
    window = space_dialog.SpaceDialog()
    window._scan_finished({"projects": [], "seconds": 1.0, "cancelled": True})
    assert "partial" in window.status.fullText()
    window.close()


@case("Aero-Space: the main window and the worst confirmation fit a 1366 x 768 screen, with no scroll bar of their own")
def _():
    space_dialog.ConfirmDialog = REAL_CONFIRM                        # an earlier case put a stub in its place
    tmp = tempfile.mkdtemp()
    room_w, room_h = 1366 - 48, 768 - 48                             # what the kit leaves around a window
    window = make_dialog(tmp, one=dict(dense=1000, depth=3000), two=dict(dense=1000, depth=3000))
    window.show()
    pump(lambda: False, 0.3)
    need = window.layout().totalMinimumSize()
    assert need.width() <= room_w and need.height() <= room_h, (need.width(), need.height())
    for bar in window.findChildren(QtWidgets.QScrollBar):
        assert not bar.isVisible() or bar.parent().parent() is window.table, "a scroll bar outside the table"
    choices = [(key, key, "hint", 1000, 2, key != "ortho") for key in scanner.ITEMS]
    plan = {"title": "Lighten 2 projects?", "remove": [], "kept": ["kept"], "kept_info": "i",
            "warnings": [("warning", "one line"), ("warning", "line one" + chr(10) + "line two")], "go": "Remove the files"}
    dialog = REAL_CONFIRM(window, "t", False, "space.mode_recycle_none", choices, 2, plan)
    dialog.show()
    pump(lambda: False, 0.3)
    need = dialog.layout().totalMinimumSize()
    assert need.width() <= room_w and need.height() <= room_h, ("confirmation", need.width(), need.height())
    dialog.close()
    window.close()


@case("Aero-Space: the confirmation names what goes and what stays, the red button waits for the word, Enter never confirms")
def _():
    space_dialog.ConfirmDialog = REAL_CONFIRM                        # an earlier case put a stub in its place
    tmp = tempfile.mkdtemp()
    window = make_dialog(tmp, one=dict(dense=1000, depth=3000))
    check_all(window)
    picked = window._checked()
    plan = {"title": "Delete 1 project?", "remove": ["1 project, 4 KB", "more"], "kept": ["kept line"],
            "warnings": [], "go": "Delete permanently"}
    dialog = REAL_CONFIRM(window, "t", False, "space.mode_recycle_na", None, 0, plan)
    dialog.show()
    pump(lambda: False, 0.2)
    assert dialog.next_button.role() == "danger" and not dialog.next_button.isEnabled(), "permanent must wait for the word"
    dialog.word.setText("delete")
    assert dialog.next_button.isEnabled()
    event = QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Return, QtCore.Qt.NoModifier)
    dialog.word.setFocus()
    dialog.keyPressEvent(event)
    assert dialog.mode is None, "Enter confirmed a deletion by itself"
    dialog.next_button.click()
    assert dialog.mode == actions.PERMANENT
    recycle = REAL_CONFIRM(window, "t", True, plan=plan)
    recycle.show()
    pump(lambda: False, 0.2)
    assert recycle.recycle.isChecked() and recycle.next_button.isEnabled()
    assert recycle.next_button.text() == i18n.t("space.go_recycle") and recycle.next_button.role() == "danger"
    recycle.close()
    dialog.close()
    window.close()


# --------------------------------------------------------------------------
# Aero-GCP Adjust window
# --------------------------------------------------------------------------

from aerotools.tools.fastgcp import gcp_select, gcp_select_dialog  # noqa: E402


def points(names=("A", "B", "C", "D", "E")):
    return [{"label": n, "marker": object(), "n": 5, "est": (float(i), 0.0, 0.0), "ref": (float(i), 0.0, 0.0)}
            for i, n in enumerate(names)]


@case("Aero-GCP Adjust: closing the window during the computation does not leave it stuck on reopening")
def _():
    gate = {"release": False, "calls": 0}

    def slow(pts, enabled, model, *a, **k):
        gate["calls"] += 1
        end = time.time() + 5
        while not gate["release"] and time.time() < end:
            time.sleep(0.01)
        return {"model": model, "count": len(pts), "usable": len(pts), "few": [], "suspects": [], "curve": [],
                "recommended": None, "current": (None, None), "reason": ""}

    real = gcp_select.analyze
    gcp_select.analyze = slow
    try:
        applied = []
        data = {"points": points(), "why": "", "ppk": False, "context": ("p.psx", 1), "enabled": {}}
        window = gcp_select_dialog.GcpSelectDialog(lambda: data, lambda *a: applied.append(a) or (0, ""))
        assert window._worker is not None
        window.close()                                        # closed while the worker computes
        assert window._worker is None
        gate["release"] = True
        window.show()
        window.refresh()                                      # what show() does on reopening
        assert window._worker is not None and not window._worker.abandoned
        assert pump(lambda: window._worker is None), "the reopened window never finished computing"
        assert not window.bar.isVisible()
        window.close()
    finally:
        gcp_select.analyze = real


@case("Aero-GCP Adjust: Apply passes the chunk context and the analysed labels")
def _():
    seen = []
    data = {"points": points() + [dict(points(("F",))[0], n=1)], "why": "", "ppk": False, "context": ("p.psx", 7), "enabled": {}}
    window = gcp_select_dialog.GcpSelectDialog(lambda: data, lambda *a: seen.append(a) or (1, ""))
    assert pump(lambda: window._worker is None)
    assert window._context == ("p.psx", 7) and window._analysed == frozenset("ABCDE")      # F has one photo: not analysed
    window.close()


# --------------------------------------------------------------------------
# Aero-GCP secondary windows (kit)
# --------------------------------------------------------------------------

from aerotools import uikit  # noqa: E402
from aerotools.tools.fastgcp import diagnostic_dialog, qc, qc_dialog, review, review_dialog, settings_dialog, widgets  # noqa: E402


def visible_primaries(window):
    """Primary-role buttons that are visible in a window (the kit rule: at most one)."""
    return [b for b in window.findChildren(uikit.Button) if b.role() == "primary" and b.isVisible()]


@case("Aero-GCP Residual check: one primary (Open), enabled only with a row chosen, the marker goes to the opener")
def _():
    rows = [{"label": "GCP%d" % i, "marker": object(), "proj": 4 - i, "x": 0.01 * i, "y": 0.0, "z": 0.02, "total": 0.03 * i}
            for i in range(1, 4)]
    items = qc.table(rows, {row["label"]: True for row in rows})
    opened = []
    window = qc_dialog.QcDialog(lambda: {"items": items, "groups": qc.group_rms(items), "limit": 0.05}, opened.append, lambda: None)
    window.show()
    pump(lambda: False, 0.2)
    assert window.table.row_count() == 3 and len(visible_primaries(window)) == 1
    assert window.cancel_button.text() == i18n.t("ui.close")
    assert not window.open_button.isEnabled()
    window.table.select_row(0)
    assert window.open_button.isEnabled()
    window.open_button.click()
    assert len(opened) == 1 and opened[0]["label"] in {row["label"] for row in rows}
    window.close()


@case("Aero-GCP diagnostic: findings listed with their kind, the photo button follows the choice, an error shows the banner with Retry")
def _():
    result = {"summary": {"observations": 5, "markers": 2, "checked": 2, "photos": 3, "rms_px": 0.5, "median_px": 0.4, "below_1px": 0.9},
              "projections": [{"label": "A", "camera_label": "C1", "px": 2.0, "camera": None}], "photos": [],
              "markers": [{"kind": "few", "label": "B", "n": 1}], "limit_px": 1.0}
    seen = []
    window = diagnostic_dialog.DiagnosticDialog(lambda: result, seen.append)
    window.show()
    assert window.table.row_count() == 2 and len(visible_primaries(window)) == 1
    assert not window.open_button.isEnabled()
    window.table.select_row(0)
    assert window.open_button.isEnabled()
    window.open_button.click()
    assert seen and seen[0]["label"] == "A"
    state = {"fail": True}

    def compute():
        if state["fail"]:
            raise RuntimeError("no chunk")
        return result

    window._compute = compute
    window.refresh()
    assert window.banner.isVisible() and "no chunk" in window.banner.message.text()
    state["fail"] = False
    window.retry = window.refresh
    window.refresh()
    assert not window.banner.isVisible()
    window.close()


@case("Aero-GCP settings: three pages without a scroll area, Close only, the options block is shown, a running training refuses to close")
def _():
    panel = types.SimpleNamespace(more=QtWidgets.QWidget(), _state=None)
    window = settings_dialog.SettingsDialog(panel)
    window.show()
    pump(lambda: False, 0.2)
    assert not window.findChildren(QtWidgets.QScrollArea)
    assert not visible_primaries(window) and window.cancel_button.text() == i18n.t("ui.close")
    keys = [key for key, _text in settings_dialog.PAGES]
    for index, key in enumerate(keys):
        window.tabs.set_value(key)
        window.tabs.changed.emit(key)
        assert window.pages.currentIndex() == index
    assert panel.more.parent() is not None and not panel.more.isHidden()
    window._job = types.SimpleNamespace(cancel=lambda: None)
    assert not window.can_close()
    window._job = None
    window.close()


@case("Fonts: the header Aa button, the Appearance tab of Aero-GCP Settings and the Install plugins row stay in sync; open windows are restyled; the choice is stored")
def _():
    """The font: the header "Aa" button cycles System, Brand, Large; a window opened before the switch is restyled; the Appearance tab of
    Aero-GCP Settings, the Install plugins row and the two header buttons stay in sync; the choice is stored in ui.font."""
    from aerotools.uikit import metrics, tokens
    from aerotools import uikit
    Qt = QtCore.Qt
    check = lambda ok, message: (_ for _ in ()).throw(AssertionError(message)) if not ok else None
    pump = lambda ms=30: globals()["pump"](lambda: False, ms / 1000.0)
    metrics.set_scale(1.0)
    from aerotools import plugins_dialog
    from aerotools.tools.fastgcp import settings_dialog
    previous_font, previous_skin = tokens.active_font(), tokens.active_skin()
    uikit.style.switch_skin("dark", save=False)
    uikit.style.switch_font("system", save=False)
    first = uikit.ToolWindow(None, title="First")
    label = uikit.label("Probe", "body")
    field = uikit.label("Probe", "field")
    holder = QtWidgets.QWidget()
    lay = uikit.vbox(parent=holder)
    lay.addWidget(label)
    lay.addWidget(field)
    first.set_content(holder)
    second = uikit.ToolWindow(None, title="Second")
    second.set_content(QtWidgets.QWidget())
    panel = types.SimpleNamespace(more=QtWidgets.QWidget(), _state=None)
    settings = settings_dialog.SettingsDialog(panel)
    plugins = plugins_dialog.PluginsDialog()
    for w in (first, second, settings, plugins):
        w.show()
    pump(150)
    keys = [key for key, _t in settings_dialog.PAGES]
    check(keys == ["ai", "train", "options", "appearance"] and settings.tabs.sizeHint().width() > 0, "Aero-GCP Settings has four tabs, the last is Appearance")
    button = first.header.font_button
    check(button.width() == metrics.px(28) and button.focusPolicy() != Qt.NoFocus and "System" in button.toolTip(), "the font button is 28 px, reachable by Tab, tooltip names the font")
    check(first.header.theme_button.x() < button.x(), "the font button sits right next to the theme button")
    body_px = lambda: "font-size: %dpx" % metrics.font_px("body")
    check("Segoe UI" in first.root.styleSheet() or "sans-serif" in first.root.styleSheet(), "system font: the sheet names Segoe UI")
    before = first.root.styleSheet()
    size_before = tokens.TYPE["body"]
    for expected in ("brand", "clear", "system"):
        button.click()
        pump(80)
        sheets = [w.root.styleSheet() for w in (first, second, settings, plugins)]
        want = tokens.resolve("dark", expected)
        check(tokens.active_font() == expected and all(sh == sheets[0] for sh in sheets) and ("font-size: %dpx" % want["TYPE"]["body"]) in sheets[0],
              "after a click the font is {} in every window opened before the switch".format(expected))
        check(config.get("ui", "font") == expected, "the choice is stored in ui.font ({})".format(expected))
        check(settings.font_choice.value() == expected and plugins.appearance.font.value() == expected, "the Settings tab and the Install plugins row follow the header button")
        check(first.header.font_button.toolTip() == second.header.font_button.toolTip() and uikit.controls.font_label(expected) in second.header.font_button.toolTip(), "the tooltip names the font")
    check(first.root.styleSheet() == before and tokens.TYPE["body"] == size_before, "back to system: the first sheet again (cached per skin, font and scale)")
    settings.tabs.set_value("appearance")
    settings.tabs.changed.emit("appearance")
    pump(60)
    settings.font_choice._pick(1)                                   # the Settings tab drives the others
    pump(80)
    check(tokens.active_font() == "brand" and plugins.appearance.font.value() == "brand" and config.get("ui", "font") == "brand", "the Settings tab switches the font everywhere")
    plugins.appearance.font._pick(2)
    pump(80)
    check(tokens.active_font() == "clear" and settings.font_choice.value() == "clear", "the Install plugins row switches the font everywhere")
    settings.theme_choice._pick(2)
    pump(80)
    check(tokens.active_skin() == "light" and plugins.appearance.theme.value() == "light" and tokens.active_font() == "clear"
          and first.header.theme_button.toolTip().count("Light") == 1, "the theme changes without touching the font, and the theme row follows")
    plugins.appearance.theme._pick(0)
    pump(60)
    check(tokens.active_skin() == "dark" and settings.theme_choice.value() == "dark", "the Install plugins theme row drives the Settings theme row")
    first.header.theme_button.click()
    pump(60)
    check(tokens.active_skin() == "medium" and settings.theme_choice.value() == "medium" and tokens.active_font() == "clear", "the theme button still cycles the theme only")
    check(all(not w.grab().toImage().isNull() for w in (first, settings, plugins)), "the windows paint after the switches")
    config.put("ui", "font", "nonsense")
    check(tokens.configured_font() == "system", "an unknown font in the settings means system")
    fresh = uikit.ToolWindow(None, title="Fresh")
    fresh.set_content(QtWidgets.QWidget())
    uikit.style.switch_font("brand")
    fresh.show()
    pump(60)
    check(tokens.resolve("medium", "brand")["FONT_FAMILY"] in fresh.root.styleSheet(), "a window opened afterwards gets the stored font")
    uikit.style.switch_font(previous_font, save=False)
    uikit.style.switch_skin(previous_skin, save=False)
    for w in (first, second, settings, plugins, fresh):
        w.close()


@case("Aero-GCP review: one primary (Validate), disabled until a point is kept; the progress window steps and closes")
def _():
    entries = [{"marker": None, "label": "A", "camera": None, "camera_label": "IMG_1", "image_path": "", "ai_x": 5.0, "ai_y": 5.0, "x": 5.0,
                "y": 5.0, "conf": 0.5, "crop_px": 0.0, "px_per_m": 1.0, "previous": None, "big": None, "win": 0.0, "x0": 0.0, "y0": 0.0,
                "state": review.KEPT}]
    rev = review.Review("checker", 1, entries)
    window = review_dialog.ReviewDialog(types.SimpleNamespace(), rev)
    window.show()
    pump(lambda: False, 0.2)
    assert len(visible_primaries(window)) == 1 and window.validate_button.isEnabled()
    entries[0]["state"] = review.REMOVED
    window._update_buttons()
    assert not window.validate_button.isEnabled()
    window.close()
    busy = widgets.BusyDialog("Working")
    busy.show()
    busy.step(1, 4)
    assert not visible_primaries(busy) and busy.row.bar.fraction() == 0.25
    busy.close()


failed = [name for name, error in RESULTS if error]
print("-" * 60)
print("Qt binding: {} ; {} case(s), {} failure(s)".format(QtWidgets.__name__.split(".")[0], len(RESULTS), len(failed)))
problem = GUARD.check()
if problem:
    print("FAIL  " + problem)
    failed.append(problem)
sys.exit(1 if failed else 0)
