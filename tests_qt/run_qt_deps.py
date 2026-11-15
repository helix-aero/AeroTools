"""Dependencies window: real Qt offscreen, on the three skins. Never runs pip (the install step is replaced by a fake that moves the state).

    python -B tests_qt\run_qt_deps.py [--shots DIR]

Checks: a tool that cannot start lists only the missing rows (pip first when it is missing), each with its description, the "i" that names
the tools, the state and an Install button; "Install all" runs them in order and the progress shows on the row; when everything is there the
primary button offers to start the tool; a failure shows the short reason on its row, "Copy log" puts the log on the clipboard and Install
all tries again; a failed pip stops the rest with its own reason; Stop and Close during an install stop the running command; the menu
window lists every dependency with its state and has no primary button when nothing is missing; more rows than the cap say "and N more";
fixed size and no scroll bar everywhere. Screenshots with --shots (missing, installing, all installed, failed).
"""

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import _isolation  # noqa: E402
GUARD = _isolation.Guard("aerotools_qt_deps_")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")   # the offscreen platform finds no font by itself

import types  # noqa: E402
FAKE = types.ModuleType("Metashape")
FAKE.app = types.SimpleNamespace(version="2.2.1", messageBox=print)
sys.modules["Metashape"] = FAKE
from aerotools import config, deps, journal  # noqa: E402
GUARD.pin(config)
from aerotools.qt import QtCore, QtWidgets  # noqa: E402
from aerotools import deps_dialog, i18n, uikit  # noqa: E402
from aerotools.uikit import metrics, tokens  # noqa: E402

journal.print = lambda *args, **kwargs: None
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
FAILURES = []
SHOTS = sys.argv[sys.argv.index("--shots") + 1] if "--shots" in sys.argv else None
SKINS = ("dark", "medium", "light")
TOOL = "aeroexports"                                  # needs numpy (config.PACKS)
PYTHON = r"C:\Metashape\python\python.exe"


def t(key, **kwargs):
    return i18n.t(key, **kwargs)


def check(ok, text):
    print(("ok   " if ok else "FAIL ") + text)
    if not ok:
        FAILURES.append(text)


def pump(ms=40):
    end = QtCore.QDeadlineTimer(ms)
    while not end.hasExpired():
        APP.processEvents()


def wait_for(condition, seconds=8.0):
    end = time.time() + seconds
    while time.time() < end:
        pump(20)
        if condition():
            return True
    return False


def shot(widget, name, skin):
    if not SHOTS:
        return
    os.makedirs(SHOTS, exist_ok=True)
    pump()
    path = os.path.join(SHOTS, "{}_{}.png".format(name, skin))
    widget.grab().save(path)
    print("     wrote " + path)


def labels(widget):
    return [w.text() for w in widget.findChildren(QtWidgets.QLabel)] + [w.fullText() for w in widget.findChildren(uikit.ElidedLabel)]


def buttons(widget):
    return [b.text() for b in widget.findChildren(QtWidgets.QPushButton) if b.isVisible()]


def no_scroll(widget):
    return not widget.findChildren(QtWidgets.QScrollArea) and not widget.findChildren(QtWidgets.QScrollBar)


# -- the fake world: what is installed, and an install that moves it ------------------------------------

WORLD = {"pip": "", "numpy": "", "fail": {}, "gate": None, "calls": []}


def reset_world(pip="", numpy="", fail=None):
    WORLD.update(pip=pip, numpy=numpy, fail=dict(fail or {}), gate=None, calls=[])


def fake_install(name, python, log, progress=None, cancelled=None, popen=None):
    """Stands in for deps.install_one: walks the progress steps, then installs or fails as WORLD says."""
    WORLD["calls"].append(name)
    log("$ fake install " + name)
    progress(0.15, "collect", "")
    time.sleep(0.15)
    progress(0.35, "download", "15.9 MB")
    gate = WORLD["gate"] if name != "pip" else None
    while gate is not None and not gate.is_set():                    # a test holds the install here to look at it
        if cancelled():
            log("stopped")
            return {"ok": False, "reason": "stopped", "code": -2, "restart": False}
        time.sleep(0.02)
    reason = WORLD["fail"].get(name)
    if reason:
        log("failed: " + reason)
        return {"ok": False, "reason": reason, "code": 1, "restart": False}
    progress(0.85, "install", "")
    WORLD[name] = "1.0"
    return {"ok": True, "reason": "", "code": 0, "restart": False}


deps.install_one = fake_install
deps.find_python = lambda: (PYTHON, [PYTHON])
deps.pip_version = lambda: WORLD["pip"]
deps.module_version = lambda name: WORLD.get(name, "")
deps.state_of = lambda name: WORLD["pip"] if name == "pip" else WORLD.get(name, "")


def make(tool_id=TOOL, **kwargs):
    dialog = deps_dialog.DependenciesDialog(None, tool_id, **kwargs)
    dialog.show()
    pump()
    return dialog


def statuses(dialog):
    return {name: row.status.fullText() for name, row in dialog.rows.items()}


# -- checks ---------------------------------------------------------------------------------------------

def missing_checks(skin):
    import threading
    reset_world()
    dialog = make()
    check(list(dialog.rows) == ["pip", "numpy"], skin + ": a tool with nothing installed lists pip first, then NumPy")
    check(set(statuses(dialog).values()) == {t("deps.status_missing")}, skin + ": both rows say Missing")
    text = labels(dialog)
    check(t("deps.pip_info") in text and t("deps.numpy_info") in text, skin + ": each row has its one-line description")
    tips = [b.toolTip() for b in dialog.findChildren(uikit.InfoButton)] + [getattr(b, "_text", "") for b in dialog.findChildren(uikit.InfoButton)]
    check(any("Aero-Exports" in tip for tip in tips), skin + ": the 'i' of NumPy names the tool that needs it")
    caps = buttons(dialog)
    check(caps.count(t("deps.install")) == 2 and t("deps.install_all") in caps and t("ui.close") in caps, skin + ": Install x2, Install all, Close")
    check(not dialog.copy_button.isVisible(), skin + ": no Copy log before anything ran")
    check(dialog.minimumSize() == dialog.maximumSize(), skin + ": fixed size")
    check(no_scroll(dialog), skin + ": no scroll bar")
    check(dialog.layout().totalMinimumSize().height() <= dialog.height() + 1, skin + ": everything fits the window")
    check(dialog.next_button.isEnabled() and dialog.next_button.text() == t("deps.install_all"), skin + ": Install all is the primary button")
    shot(dialog, "missing", skin)

    WORLD["gate"] = threading.Event()
    dialog.next_button.click()
    check(wait_for(lambda: dialog.state["numpy"]["status"] == deps_dialog.INSTALLING and dialog.state["pip"]["status"] == deps_dialog.DONE),
          skin + ": Install all: pip first, then NumPy")
    check(WORLD["calls"][:2] == ["pip", "numpy"], skin + ": installed in that order")
    wait_for(lambda: dialog.state["numpy"]["step"] == "download")
    pump(250)
    check(statuses(dialog)["numpy"] == t("deps.step_download_size", size="15.9 MB"), skin + ": the row shows the step and the size")
    check(dialog.rows["numpy"].bar.isVisible() and 0.3 < dialog.rows["numpy"].bar.fraction() < 0.4, skin + ": a progress bar on the row")
    check(statuses(dialog)["pip"] == t("deps.status_done"), skin + ": pip is Installed")
    check(not dialog.next_button.isEnabled() and dialog.cancel_button.text() == t("deps.stop"), skin + ": while it works: Install all is off, Close says Stop")
    check(not any(b.isVisible() for b in (r.button for r in dialog.rows.values())), skin + ": no row button while installing")
    shot(dialog, "installing", skin)
    WORLD["gate"].set()
    check(wait_for(lambda: not dialog.installing()), skin + ": the install ends")
    pump(150)
    check(set(statuses(dialog).values()) == {t("deps.status_done")}, skin + ": both rows say Installed")
    check(dialog.next_button.isVisible() and dialog.next_button.text() == t("deps.start", title="Aero-Exports"), skin + ": primary offers to start the tool")
    check(t("deps.all_done") in labels(dialog.footer), skin + ": the footer says everything is installed")
    check(dialog.cancel_button.text() == t("ui.close"), skin + ": Close is back")
    check(dialog.copy_button.isVisible(), skin + ": Copy log is there once something ran")
    shot(dialog, "installed", skin)
    dialog.next_button.click()
    check(dialog.start_requested is True and not dialog.isVisible(), skin + ": Start closes the window and asks the caller to start the tool")


def failure_checks(skin):
    reset_world(fail={"numpy": "offline"}, pip="22.2")
    dialog = make()
    check(list(dialog.rows) == ["numpy"], skin + ": pip present: only NumPy is listed")
    dialog.next_button.click()
    check(wait_for(lambda: not dialog.installing()), skin + ": the failed install ends")
    pump(150)
    check(statuses(dialog)["numpy"] == t("deps.status_failed", reason=t("deps.err_offline")), skin + ": the row says why: no internet")
    check(dialog.rows["numpy"].button.isVisible() and dialog.next_button.isEnabled(), skin + ": Install and Install all are still there to try again")
    check(not dialog.next_button.text() == t("deps.start", title="Aero-Exports"), skin + ": no Start while something failed")
    check(t("deps.some_failed") in labels(dialog.footer), skin + ": the footer says some could not be installed")
    check(dialog.copy_button.isVisible(), skin + ": Copy log is offered")
    clip = dialog.copy_log()
    check(QtWidgets.QApplication.clipboard().text() == clip and "$ fake install numpy" in clip and "failed: offline" in clip
          and PYTHON in clip and "OS:" in clip, skin + ": the log has the system, python.exe and the commands")
    shot(dialog, "failed", skin)
    WORLD["fail"] = {}
    dialog.rows["numpy"].button.click()
    check(wait_for(lambda: not dialog.installing()), skin + ": trying again ends")
    pump(150)
    check(statuses(dialog)["numpy"] == t("deps.status_done") and dialog.next_button.text() == t("deps.start", title="Aero-Exports"),
          skin + ": the second try works, Start is offered")
    dialog.reject()

    reset_world(fail={"pip": "no_ensurepip"})
    dialog = make()
    dialog.next_button.click()
    check(wait_for(lambda: not dialog.installing()), skin + ": a pip that cannot be installed ends the run")
    pump(150)
    check(statuses(dialog)["pip"] == t("deps.status_failed", reason=t("deps.err_no_ensurepip")), skin + ": pip: the short reason")
    check(statuses(dialog)["numpy"] == t("deps.status_failed", reason=t("deps.err_needs_pip")) and WORLD["calls"] == ["pip"],
          skin + ": NumPy is not tried without pip")
    dialog.reject()


def single_row_checks(skin):
    reset_world()
    dialog = make()
    dialog.rows["numpy"].button.click()
    check(wait_for(lambda: not dialog.installing()), skin + ": Install on the NumPy row ends")
    check(WORLD["calls"] == ["pip", "numpy"], skin + ": NumPy's own button installs pip first")
    dialog.reject()


def stop_checks(skin):
    import threading
    reset_world(pip="22.2")
    WORLD["gate"] = threading.Event()
    dialog = make()
    dialog.next_button.click()
    wait_for(lambda: dialog.state["numpy"]["status"] == deps_dialog.INSTALLING)
    dialog.cancel_button.click()                                      # Stop
    check(wait_for(lambda: not dialog.installing()), skin + ": Stop ends the install")
    pump(150)
    check(statuses(dialog)["numpy"] == t("deps.status_failed", reason=t("deps.err_stopped")), skin + ": the row says stopped")
    check(dialog.isVisible(), skin + ": Stop does not close the window")
    dialog.reject()
    check(not dialog.isVisible(), skin + ": Close closes it")

    reset_world(pip="22.2")
    WORLD["gate"] = threading.Event()
    dialog = make()
    dialog.next_button.click()
    wait_for(lambda: dialog.state["numpy"]["status"] == deps_dialog.INSTALLING)
    dialog.close()                                                    # the cross asks the install to stop, the window stays
    check(wait_for(lambda: not dialog.installing()), skin + ": the cross stops a running install")
    dialog.reject()


def menu_checks(skin):
    reset_world(pip="22.2", numpy="2.0.2")
    deps.installed_tool_ids = lambda: [TOOL, "fastgcp"]
    dialog = make(None)
    check(list(dialog.rows) == ["pip", "numpy"], skin + ": menu window: pip and NumPy for the installed tools")
    check(set(statuses(dialog).values()) == {t("deps.status_present", version="22.2"), t("deps.status_present", version="2.0.2")},
          skin + ": menu window: each with its version")
    check(not dialog.next_button.isVisible() and not any(r.button.isVisible() for r in dialog.rows.values()), skin + ": nothing to install: no buttons")
    check(buttons(dialog) == [t("ui.close")], skin + ": only Close")
    shot(dialog, "all_installed", skin)
    dialog.reject()

    reset_world(pip="22.2", numpy="")
    dialog = make(None)
    check(statuses(dialog)["numpy"] == t("deps.status_missing") and dialog.next_button.isVisible(), skin + ": menu window: a missing one is installable")
    dialog.next_button.click()
    check(wait_for(lambda: not dialog.installing()), skin + ": menu window installs")
    pump(150)
    check(not dialog.next_button.isVisible() and t("deps.all_done") in labels(dialog.footer), skin + ": menu window: done, no Start button (no tool asked)")
    dialog.reject()

    deps.installed_tool_ids = lambda: ["fastgcp"]
    dialog = make(None)
    check(t("deps.empty") in labels(dialog) and not dialog.rows, skin + ": no installed tool needs a package: says so")
    shot(dialog, "none_needed", skin)
    dialog.reject()


def cap_checks(skin):
    reset_world(pip="22.2")
    many = {"id": "x", "title": "x", "info": "", "users": [], "version": ""}
    deps.collect = lambda *a, **k: [dict(many, id="m%d" % i, title="Package %d" % i) for i in range(8)]
    dialog = make(None)
    check(len(dialog.rows) == deps_dialog.MAX_ROWS and t("deps.more", count=8 - deps_dialog.MAX_ROWS) in labels(dialog),
          skin + ": more rows than the cap: 'and 3 more'")
    check(no_scroll(dialog) and dialog.minimumSize() == dialog.maximumSize(), skin + ": still fixed size, no scroll bar")
    check(len(dialog.todo()) == 8, skin + ": Install all still covers the hidden rows")
    dialog.reject()
    import importlib
    importlib.reload(deps)
    deps.install_one = fake_install
    deps.find_python = lambda: (PYTHON, [PYTHON])
    deps.pip_version = lambda: WORLD["pip"]
    deps.module_version = lambda name: WORLD.get(name, "")
    deps.state_of = lambda name: WORLD["pip"] if name == "pip" else WORLD.get(name, "")


def no_python_checks(skin):
    reset_world(pip="22.2")
    saved = deps.find_python
    deps.find_python = lambda: ("", [r"C:\Metashape\python\python.exe"])
    dialog = make()
    check(not dialog.next_button.isEnabled() and t("deps.python_missing") in labels(dialog.footer), skin + ": no python.exe: Install all is off and the footer says why")
    shot(dialog, "no_python", skin)
    dialog.reject()
    deps.find_python = saved


def main():
    metrics.set_scale(1.0)
    for skin in SKINS:
        tokens.use(skin)
        missing_checks(skin)
        failure_checks(skin)
        single_row_checks(skin)
        stop_checks(skin)
        menu_checks(skin)
        no_python_checks(skin)
    tokens.use("dark")
    cap_checks("dark")
    print("%d checks failed" % len(FAILURES))
    problem = GUARD.check()
    if problem:
        print("FAIL " + problem)
        FAILURES.append(problem)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
