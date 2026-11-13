"""Qt self-test and gallery of the UI kit: every component in every state, rendered offscreen at 100 % and 150 %, plus behaviour checks.

    python -B tests_qt\\run_qt_uikit.py                      (PySide6 here, also meant for Metashape's PySide2)
    python -B tests_qt\\run_qt_uikit.py --shots DIR          where the PNG renders go (default: next to this file, in a temp folder)
    python -B tests_qt\\run_qt_uikit.py --no-shots           behaviour checks only

Exit code 0 when every check passed. No Metashape needed.
"""

import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import _isolation  # noqa: E402
GUARD = _isolation.Guard("aerotools_qt_uikit_")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from aerotools import config, journal  # noqa: E402
GUARD.pin(config)
from aerotools.qt import BINDING, QtCore, QtGui, QtWidgets  # noqa: E402
from aerotools import uikit  # noqa: E402
from aerotools.uikit import metrics, tokens  # noqa: E402

if "--skin" in sys.argv:                                # --skin light: render and check with that skin
    tokens.use(sys.argv[sys.argv.index("--skin") + 1])

journal.print = lambda *args, **kwargs: None
APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
Qt = QtCore.Qt
QtTest = __import__(BINDING + ".QtTest", fromlist=["QTest"])
FAILURES = []
SHOTS = None if "--no-shots" in sys.argv else (sys.argv[sys.argv.index("--shots") + 1] if "--shots" in sys.argv else
                                               os.path.join(tempfile.gettempdir(), "uikit_gallery"))
LONG = "A very long label that goes on and on well past the width any reasonable control column can offer to it"
LONG_PATH = r"D:\Projects\Corsica\2026\Mission_07_Calvi_Balagne\Photos\Flight_03_afternoon\RAW\DJI_0001_to_DJI_0842_selected"


def check(condition, message):
    print(("ok   " if condition else "FAIL ") + message)
    if not condition:
        FAILURES.append(message)


def pump(ms=30):
    end = time.time() + ms / 1000.0
    while time.time() < end:
        APP.processEvents()
        time.sleep(0.005)


def until(condition, seconds=3.0):
    end = time.time() + seconds
    while time.time() < end:
        APP.processEvents()
        if condition():
            return True
        time.sleep(0.005)
    return bool(condition())


def shot(widget, name, scale):
    if not SHOTS:
        return
    os.makedirs(SHOTS, exist_ok=True)
    pump(60)
    path = os.path.join(SHOTS, "{}_{}.png".format(name, int(round(scale * 100))))
    widget.grab().save(path)
    print("     wrote " + path)


def shown(widget, width=None, height=None):
    widget.show()
    if width:
        widget.resize(width, height or widget.height())
    lay = widget.layout()
    if lay is not None and not isinstance(widget, (QtWidgets.QDialog, QtWidgets.QMainWindow)) and lay.hasHeightForWidth():
        widget.resize(widget.width(), max(widget.height(), lay.totalHeightForWidth(widget.width())))
    pump(80)
    return widget


def key(widget, k, modifier=Qt.NoModifier):
    QtTest.QTest.keyClick(widget, k, modifier)
    pump(10)


def cut_off(window):
    """Visible widgets that stick out of the window, and labels / buttons whose text does not fit (not elided on purpose)."""
    pump(80)
    problems = []
    area = window.rect()
    for child in window.findChildren(QtWidgets.QWidget):
        if not child.isVisible() or child.window() is not window:
            continue
        top_left = child.mapTo(window, QtCore.QPoint(0, 0))
        rect = QtCore.QRect(top_left, child.size())
        if not area.adjusted(-1, -1, 1, 1).contains(rect) and not isinstance(child, QtWidgets.QAbstractScrollArea):
            if child.parentWidget() is not None and not isinstance(child.parentWidget(), (QtWidgets.QScrollArea,)):
                problems.append("outside the window: {} {}".format(type(child).__name__, child.objectName()))
        if isinstance(child, uikit.ElidedLabel):
            continue
        if isinstance(child, QtWidgets.QPushButton) and child.text():
            need = child.fontMetrics().horizontalAdvance(child.text())
            if need > child.width():
                problems.append("button text clipped: " + child.text())
        elif isinstance(child, QtWidgets.QLabel) and child.text() and not child.pixmap():
            if child.wordWrap():
                if child.heightForWidth(child.width()) > child.height() + 1:
                    problems.append("wrapped label clipped: " + child.text()[:30])
            elif child.fontMetrics().horizontalAdvance(child.text()) > child.width() + 1 and "<" not in child.text():
                problems.append("label text clipped: " + child.text()[:30])
    return problems


# --------------------------------------------------------------------------------------------------
# Gallery pages
# --------------------------------------------------------------------------------------------------

def page(width=None):
    """A bare kit-styled surface to lay components on."""
    root = QtWidgets.QWidget()
    root.setAttribute(Qt.WA_StyledBackground, True)
    uikit.style.apply(root)
    lay = uikit.vbox((16, 16, 16, 16), 24, parent=root)
    if width:
        root.setFixedWidth(metrics.px(width))
    return root, lay


def row_of(*widgets, spacing=8):
    holder = QtWidgets.QWidget()
    lay = uikit.hbox(spacing=spacing, parent=holder)
    for w in widgets:
        lay.addWidget(w)
    lay.addStretch(1)
    return holder


def caption(text):
    return uikit.label(text, "caption", wrap=False)


def build_controls_page():
    root, lay = page(760)
    lay.addWidget(caption("TYPE SCALE"))
    for kind in ("display", "title", "lead", "body", "small", "caption"):
        lay.addWidget(uikit.label("{}: HELIX AERO - the quick brown fox 0123456789".format(kind), kind, wrap=False))
    tones = [uikit.label(t, "body", t, wrap=False) for t in ("muted", "faint", "accent", "success", "warning", "danger", "info")]
    lay.addWidget(row_of(*tones, spacing=16))

    lay.addWidget(caption("BUTTONS: PRIMARY  SECONDARY  QUIET  DANGER  (NORMAL / FOCUS RING / DISABLED / WITH ICON)"))
    for role in ("primary", "secondary", "quiet", "danger"):
        normal = uikit.Button("Export", role)
        ring = uikit.Button("Focused", role)
        ring.force_ring = True
        off = uikit.Button("Disabled", role)
        off.setEnabled(False)
        icon = uikit.Button("Open folder", role, icon="folder")
        longer = uikit.Button("A button with a long caption", role)
        lay.addWidget(row_of(normal, ring, off, icon, longer))
    icon_row = [uikit.IconButton(n, n) for n in ("cross", "search", "refresh", "plus")]
    icon_row[1].force_ring = True
    icon_row[3].setEnabled(False)
    lay.addWidget(row_of(*icon_row))

    lay.addWidget(caption("FORM: FIELDS, NUMBER, CHOICE, SEGMENTED, TOGGLE, PATH, INFO"))
    form = uikit.FormGrid()
    form.add_row("Name", uikit.TextField("Calvi flight 3", "Name of the mission"), info="Shown in the report header.")
    focused = uikit.TextField("Focused field")
    focused.force_ring = True
    form.add_row("Focused", focused)
    bad = uikit.TextField("not-a-number")
    form.add_row("Invalid", bad).set_error("This value is not valid.")
    off = uikit.TextField("Disabled")
    off.setEnabled(False)
    form.add_row("Disabled", off)
    form.add_row("Pixel size", uikit.NumberField(0.05, 0.001, 10, 3, 0.01, "m"), info="Ground size of one pixel of the orthophoto.")
    num = uikit.NumberField(12.5, 0, 10, 1, 1, "deg")
    num.setText("99")
    num.commit()
    form.add_row("Out of range", num)
    form.add_row("Format", uikit.ChoiceBox([("tif", "GeoTIFF"), ("jp2", "JPEG 2000 with a rather long description"), ("ecw", "ECW")], parent=None))
    form.add_row("Mode", uikit.Segmented([("a", "Contours"), ("b", "Points"), ("c", "Both")], "b"))
    toggle_on, toggle_off, toggle_dis = uikit.Toggle("Clip to boundary", True), uikit.Toggle("Keep tiles", False), uikit.Toggle("Locked", True)
    toggle_dis.setEnabled(False)
    form.add_row("Options", row_of(toggle_on, toggle_off, toggle_dis, spacing=16))
    form.add_row("Folder", uikit.PathField("dir", LONG_PATH))
    form.add_row("Empty path", uikit.PathField("dir", "", "Choose where to save"))
    form.add_row(LONG, uikit.TextField("Long label wraps in its column"))
    lay.addWidget(form)
    info = uikit.InfoButton("A short explanation behind the small i. It wraps at a comfortable width and never leaves the screen.")
    ring = uikit.InfoButton("x")
    ring.force_ring = True
    lay.addWidget(row_of(info, ring))
    lay.addStretch(1)
    return root


def build_blocks_page():
    root, lay = page(760)
    section = uikit.Section("Section heading", "The explanation of this whole block lives behind the i.")
    section.add(uikit.label("Body text of the section, in the normal weight and the main text colour.", "body"))
    closed = uikit.Disclosure("Advanced options")
    closed.add(uikit.label("Hidden until opened", "body"))
    opened = uikit.Disclosure("Advanced options (open)", open=True)
    opened.add(uikit.label("This content takes no room while the disclosure is closed.", "body"))
    section.add(closed)
    section.add(opened)
    lay.addWidget(section)

    for kind, text in (("info", "Photos are read from the folder you chose."), ("success", "42 photos were added to the chunk."),
                       ("warning", "3 photos have no position and were skipped."), ("danger", "The export stopped: the disk is full.")):
        lay.addWidget(uikit.Banner(kind, text, title=kind.capitalize() if kind == "danger" else "", closable=True))
    lay.addWidget(uikit.Banner("danger", LONG + ". " + LONG, "Could not read the project", ("Retry", lambda: None),
                               "Traceback (most recent call last):\n  File x.py, line 3\nValueError: bad value"))
    lay.addWidget(uikit.Banner("warning", "Two scales differ by more than expected.", action=("Fix", lambda: None), closable=False))

    rows = uikit.vbox(spacing=12)
    running = uikit.ProgressRow(cancellable=True)
    running.set_progress(0.42, "Reading photo 126 of 300: " + LONG_PATH)
    indeterminate = uikit.ProgressRow()
    indeterminate.set_indeterminate("Waiting for the server")
    done = uikit.ProgressRow()
    done.set_state("done", "Finished")
    failed = uikit.ProgressRow()
    failed.set_progress(0.3, "Stopped")
    failed.set_state("error")
    for r in (running, indeterminate, done, failed):
        rows.addWidget(r)
    lay.addLayout(rows)
    # A narrow dock shows the cancel as a small cross; it must be reachable and must send the signal.
    cross = uikit.ProgressRow(cancellable=True, icon_cancel=True)
    cross.set_indeterminate("AI (Spray) - starting...")
    sent = []
    cross.cancelRequested.connect(lambda: sent.append(1))
    cross.cancel_button.click()
    check(isinstance(cross.cancel_button, uikit.IconButton) and sent == [1], "icon cancel of a progress row sends cancelRequested")
    hidden = uikit.ProgressRow(icon_cancel=True)
    check(hidden.cancel_button.isHidden(), "icon cancel is hidden when the row is not cancellable")
    steps = uikit.StepProgress([("a", "Read the photos"), ("b", "Match the pairs"), ("c", "Build the dense cloud"), ("d", "Export"),
                                ("e", "Clean up the temporary files in a folder with a very long name " * 2)])
    for k, state, detail in (("a", "done", "300 photos"), ("b", "running", "42 %"), ("c", "pending", ""), ("d", "error", "disk full"),
                             ("e", "skipped", "")):
        steps.set_state(k, state, detail)
    lay.addWidget(steps)

    cols = [uikit.Column("name", "Name", stretch=True), uikit.Column("size", "Size (GB)", "right", 110, kind="number"),
            uikit.Column("date", "Date", width=110)]
    table = uikit.DataTable(cols, "extended")
    table.set_rows([[("Calvi flight 3", "calvi"), ("12.4", 12.4), "2026-10-01"], [("Bonifacio cliffs " + LONG, "b"), ("3.2", 3.2), "2026-09-12"],
                    [("Ajaccio port", "a"), ("101.0", 101.0), "2026-11-02"], [("Porto", "p"), ("0.8", 0.8), "2026-08-30"]])
    table.select_row(1)
    table.sort_by("size", True)
    table.setFixedHeight(metrics.px(200))
    empty = uikit.DataTable(cols, empty_title="No projects found", empty_text="Choose a folder and scan it to list its projects.")
    empty.set_empty("No projects found", "Choose a folder and scan it to list its projects.", ("Scan a folder", lambda: None))
    loading = uikit.DataTable(cols)
    loading.set_loading("Scanning the drive")
    error = uikit.DataTable(cols)
    error.set_error("The drive could not be read", "Check that it is connected, then try again.", ("Retry", lambda: None))
    lay.addWidget(table)
    small = uikit.hbox(spacing=12)
    for t in (empty, loading, error):
        t.setFixedHeight(metrics.px(230))
        small.addWidget(t)
    lay.addLayout(small)
    lay.addStretch(1)
    return root


class Photos(uikit.StepScreen):
    name_key = ""
    title = "Photos"

    def __init__(self, shell):
        super(Photos, self).__init__(shell)
        section = uikit.Section("Where are the photos", "Pick the folder that holds the flight photos. Subfolders are included.")
        form = section.form()
        self.path = uikit.PathField("dir", LONG_PATH)
        form.add_row("Folder", self.path)
        self.mode = uikit.Segmented([("all", "All photos"), ("new", "New only")])
        form.add_row("Take", self.mode)
        self.content.addWidget(section)
        more = uikit.Disclosure("Advanced")
        more_form = uikit.FormGrid()
        more_form.add_row("Skip files smaller than", uikit.NumberField(10, 0, 1000, 0, 5, "kB"))
        more.add(more_form)
        self.content.addWidget(more)
        self.content.addStretch(1)
        self.ok = True

    def next_enabled(self):
        return self.ok

    def hint(self):
        return "842 photos found"


class Checks(uikit.StepScreen):
    title = "Checks"

    def __init__(self, shell):
        super(Checks, self).__init__(shell)
        self.content.addWidget(uikit.EmptyState("Nothing to check yet", "Run the scan on the previous step first."))


class Run(uikit.StepScreen):
    title = "Run with a long step name"

    def __init__(self, shell):
        super(Run, self).__init__(shell)
        self.content.addWidget(uikit.label("Ready to run.", "body"))
        self.content.addStretch(1)

    def next_text(self):
        return "Create the orthophoto"


def build_wizard(parent=None):
    window = uikit.ToolWindow(parent, title="Aero Demo", subtitle="Project: " + LONG_PATH, info="This tool does one thing, in three steps.",
                              wizard=True, design_size=(900, 560))
    window.add_screen(Photos(window))
    window.add_screen(Checks(window))
    window.add_screen(Run(window))
    return window


def build_single(parent=None):
    window = uikit.ToolWindow(parent, title="Aero Single", subtitle="Chunk 1", info="One screen, one primary.")
    body = QtWidgets.QWidget()
    lay = uikit.vbox(spacing=24, parent=body)
    sec = uikit.Section("Output")
    form = sec.form()
    form.add_row("Pixel size", uikit.NumberField(0.05, 0.001, 10, 3, 0.01, "m"), info="Ground size of one pixel.")
    form.add_row("Format", uikit.ChoiceBox([("tif", "GeoTIFF"), ("jp2", "JPEG 2000")]))
    form.add_row("Folder", uikit.PathField("dir", ""))
    lay.addWidget(sec)
    lay.addStretch(1)
    window.set_content(body)
    window.set_primary("Export", lambda: None)
    return window


# --------------------------------------------------------------------------------------------------
# Renders
# --------------------------------------------------------------------------------------------------

def render_all(scale):
    metrics.set_scale(scale)
    page_controls = shown(build_controls_page())
    shot(page_controls, "controls", scale)
    page_blocks = shown(build_blocks_page())
    shot(page_blocks, "blocks", scale)

    wizard = shown(build_wizard())
    shot(wizard, "wizard_step1", scale)
    check(not cut_off(wizard), "wizard step 1 at {}%: nothing clipped ({})".format(int(scale * 100), cut_off(wizard)[:3]))
    wizard.go(1)
    wizard.show_banner("warning", "3 photos have no position and were skipped.", action=("Show them", lambda: None))
    shot(wizard, "wizard_step2_banner", scale)
    wizard.go(2)
    wizard.show_error(RuntimeError("The drive was removed"), retry=lambda: None, trace="Traceback...\nOSError: [Errno 5]")
    shot(wizard, "wizard_step3_error", scale)
    check(not cut_off(wizard), "wizard step 3 at {}%: nothing clipped ({})".format(int(scale * 100), cut_off(wizard)[:3]))
    wizard.clear_banner()
    wizard.run(lambda progress: [progress.step(i, 10, "Step {} of 10: {}".format(i + 1, LONG_PATH)) or time.sleep(0.01) for i in range(5)] and
               time.sleep(0.5), name="demo")
    pump(120)
    shot(wizard, "wizard_busy", scale)
    wizard.cancel_task()
    until(lambda: not wizard.is_busy())
    wizard.close()

    single = shown(build_single())
    shot(single, "single", scale)
    check(not cut_off(single), "single window at {}%: nothing clipped".format(int(scale * 100)))
    single.close()

    dock_host = QtWidgets.QMainWindow()
    dock_host.resize(metrics.px(900), metrics.px(640))
    dock = uikit.DockShell("Aero Panel", "A dock panel in the same language.")
    for text in ("Compare", "Detect"):
        dock.body.addWidget(uikit.Button(text, "secondary"))
    dock.body.addWidget(uikit.Toggle("Auto review", True))
    dock.body.addWidget(uikit.EmptyState("No markers yet", "Place a marker in the photo view."))
    dock.set_status("0 markers")
    dock.show_banner("info", "Select a photo to start.")
    footer = dock.add_footer()
    footer.set_primary("Export")
    dock_host.addDockWidget(Qt.RightDockWidgetArea, dock)
    shown(dock_host)
    shot(dock_host, "dock", scale)
    dock_host.close()

    box_confirm = uikit.MessageBox(None, "Delete", "This removes the project and its 12 files. It cannot be undone.",
                                   [("Cancel", False, "secondary"), ("Delete project", True, "danger")], "warning", "Delete this project?")
    box_confirm.show()
    pump(80)
    shot(box_confirm, "message_confirm", scale)
    box_confirm.close()

    info = uikit.InfoButton("A short explanation behind the small i. It wraps at a comfortable width and never leaves the screen.")
    holder = shown(row_of(info))
    holder.move(40, 40)
    info.show_tip()
    pump(80)
    pop = uikit.controls.Popover.shared()
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        pop.grab().save(os.path.join(SHOTS, "popover_{}.png".format(int(scale * 100))))
    check(pop.isVisible() and pop.owner is info, "info popover shows at {}%".format(int(scale * 100)))
    info.hide_tip()
    holder.close()
    page_controls.close()
    page_blocks.close()


# --------------------------------------------------------------------------------------------------
# Behaviour
# --------------------------------------------------------------------------------------------------

def behaviours():
    metrics.set_scale(1.0)
    wizard = shown(build_wizard())

    check(all(hasattr(wizard, n) for n in ("next_button", "back_button", "status", "stack")), "ToolWindow exposes next_button, back_button, status, stack")
    check(wizard.stack.currentIndex() == 0 and wizard.steps.state_of(0) == "current", "wizard starts on step 1")
    check(not wizard.back_button.isVisible(), "no Back on the first step")
    order = [w for w in (wizard.cancel_button, wizard.back_button, wizard.next_button)]
    xs = [w.mapTo(wizard, QtCore.QPoint(0, 0)).x() for w in order]
    wizard.go(1)
    xs = [w.mapTo(wizard, QtCore.QPoint(0, 0)).x() for w in order]
    check(xs[0] < xs[1] < xs[2], "footer order: Cancel, Back, primary left to right")
    check(wizard.next_button.mapTo(wizard, QtCore.QPoint(wizard.next_button.width(), 0)).x() >= max(
        w.mapTo(wizard, QtCore.QPoint(w.width(), 0)).x() for w in (wizard.cancel_button, wizard.back_button)), "primary is the right-most button")
    check(wizard.steps.state_of(0) == "done" and wizard.steps.state_of(1) == "current", "step bar marks done and current")
    wizard.go(0)

    # Enter presses the primary; a page that is not ready disables it
    wizard.screens[0].ok = False
    wizard.screens[0].changed.emit()
    check(not wizard.next_button.isEnabled(), "primary disabled while the page is not ready")
    key(wizard, Qt.Key_Return)
    check(wizard.stack.currentIndex() == 0, "Enter does nothing when the primary is disabled")
    wizard.screens[0].ok = True
    wizard.screens[0].changed.emit()
    key(wizard, Qt.Key_Return)
    check(wizard.stack.currentIndex() == 1, "Enter triggers the primary (Next)")
    key(wizard, Qt.Key_Return)
    check(wizard.stack.currentIndex() == 2 and wizard.next_button.text() == "Create the orthophoto", "primary text comes from the page")

    # tab order is visual
    chain = uikit.shell.focus_chain(wizard.root)
    ys = [w.mapTo(wizard, QtCore.QPoint(0, 0)).y() for w in chain]
    check(ys == sorted(ys) or all(ys[i] <= ys[i + 1] + 40 for i in range(len(ys) - 1)), "focus chain follows the visual order")
    check(chain[-1] is wizard.next_button, "the primary is the last stop of the chain")

    # busy: Esc cancels the task, the window refuses to close
    finished = {}
    wizard.run(lambda progress: [(progress(i / 50.0, "working"), time.sleep(0.01)) for i in range(50)], name="busy",
               on_cancel=lambda: finished.setdefault("cancelled", True), on_done=lambda r: finished.setdefault("done", True))
    pump(50)
    check(wizard.is_busy() and not wizard.next_button.isEnabled() and not wizard.back_button.isEnabled(), "busy disables Back and the primary")
    wizard.close()
    check(wizard.isVisible(), "close is refused while busy")
    key(wizard, Qt.Key_Escape)
    check(until(lambda: "cancelled" in finished), "Esc while busy cancels the task")
    check(not wizard.is_busy() and wizard.isVisible(), "after the cancel the window is idle and still open")
    check(wizard.footer.progress.state() == "cancelled", "progress row says cancelled")

    # error path ends in a banner with Retry; Retry runs it again
    calls = []

    def flaky(progress):
        calls.append(1)
        if len(calls) == 1:
            raise ValueError("Boom: the server said no")
        return "fine"
    results = []
    wizard.run(flaky, name="flaky", on_done=results.append)
    check(until(lambda: wizard.banner.isVisible()), "an error shows the banner")
    check(wizard.banner.kind() == "danger" and "Boom" in wizard.banner.message.text(), "banner is danger and carries the plain message")
    check(wizard.banner.action_button.isVisible() and wizard.banner.details_button.isVisible(), "banner offers Retry and Details")
    wizard.banner.action_button.click()
    check(until(lambda: results == ["fine"]), "Retry runs the task again")

    # cooperative mode runs on the UI thread, keeps Qt alive and can be cancelled
    seen = {"thread": None}

    def coop(progress):
        seen["thread"] = QtCore.QThread.currentThread()
        for i in range(30):
            progress(i / 30.0, "metashape call {}".format(i))
            if i == 5:
                QtTest.QTest.mouseClick(wizard.cancel_button, Qt.LeftButton)
        return "unreachable"
    flags = {}
    wizard.run(coop, mode="cooperative", on_cancel=lambda: flags.setdefault("cancelled", True))
    check(seen["thread"] is APP.thread(), "cooperative mode runs on the UI thread")
    check(flags.get("cancelled") and not wizard.is_busy(), "cooperative mode: a click on Cancel stops it")
    runner = uikit.TaskRunner(mode="threaded")
    thread_ids = []
    runner.start(lambda progress: thread_ids.append(QtCore.QThread.currentThread()) or 5)
    check(until(lambda: not runner.is_running()) and runner.result == 5, "threaded mode returns its result")
    check(thread_ids and thread_ids[0] is not APP.thread(), "threaded mode runs off the UI thread")

    wizard.go(0)
    wizard.close()

    # single instance
    class Demo(uikit.ToolWindow):
        TITLE = "Demo"
    Demo.forget()
    first = Demo.open(None)
    second = Demo.open(None)
    check(first is second, "open() returns the same window")
    first.close()
    pump(20)
    third = Demo.open(None)
    check(third is first and third.isVisible(), "open() shows the closed window again")
    third.close()
    Demo.forget()

    # fields
    number = uikit.NumberField(5, 0, 10, 2, 0.5, "m")
    got = []
    number.valueChanged.connect(got.append)
    errors = []
    number.errorChanged.connect(errors.append)
    shown(number)
    number.setFocus()
    number.setText("7,25")
    number.commit()
    check(got == [7.25] and number.value() == 7.25 and number.text() == "7.25", "NumberField accepts a comma and normalises the text")
    number.setText("11")
    number.commit()
    check(number.value() == 7.25 and errors and "0.00" in errors[-1], "NumberField keeps the last valid value and names the range")
    check(number.property("invalid") is True, "NumberField shows the error edge")
    number.setText("abc")
    check(bool(number.error_text()), "NumberField flags text that is not a number as you type")
    number.set_value(5)
    key(number, Qt.Key_Up)
    check(number.value() == 5.5, "Up steps the NumberField")
    number.close()

    segmented = uikit.Segmented([("a", "One"), ("b", "Two"), ("c", "Three")])
    picked = []
    segmented.changed.connect(picked.append)
    shown(segmented)
    segmented.setFocus(Qt.TabFocusReason)
    key(segmented, Qt.Key_Right)
    key(segmented, Qt.Key_Right)
    key(segmented, Qt.Key_Right)
    check(picked == ["b", "c"] and segmented.value() == "c", "Segmented: arrows move the choice and stop at the end")
    segmented.close()

    toggle = uikit.Toggle("Clip", False)
    shown(toggle)
    toggle.setFocus(Qt.TabFocusReason)
    key(toggle, Qt.Key_Space)
    check(toggle.isChecked(), "Toggle: Space switches it")
    toggle.close()

    choice = uikit.ChoiceBox([("a", "Alpha"), ("b", "Beta")])
    check(choice.focusPolicy() == Qt.StrongFocus and choice.value() == "a", "ChoiceBox is reachable by Tab and returns keys")
    choice.set_value("b")
    check(choice.value() == "b", "ChoiceBox.set_value")

    form = uikit.FormGrid()
    row = form.add_row("Name", uikit.TextField())
    shown(form)
    row.set_error("Required")
    check(row.error.isVisible() and row.control.property("invalid") is True, "FormGrid row shows an inline error and the error edge")
    row.clear_error()
    check(not row.error.isVisible() and row.control.property("invalid") is False, "FormGrid row clears its error")
    form.close()

    table = uikit.DataTable([uikit.Column("n", "Name", stretch=True), uikit.Column("s", "Size", "right", 90, kind="number")])
    table.set_empty("Nothing here", "Scan first.")
    shown(table)
    check(table.state() == "empty", "DataTable shows its empty state with no rows")
    table.set_rows([[("b", "b"), ("10", 10)], [("a", "a"), ("9", 9)], [("c", "c"), ("100", 100)]])
    check(table.state() == "table" and table.row_count() == 3, "DataTable shows rows")
    table.sort_by("s", False)
    check([table.table.item(r, 1).text() for r in range(3)] == ["9", "10", "100"], "DataTable sorts numbers as numbers")
    table.table.selectRow(0)
    check(table.selected_rows() == [1], "DataTable maps a selected row back to its source row")
    table.set_loading("Scanning")
    check(table.state() == "empty", "DataTable loading state")
    table.close()

    pf = uikit.PathField("dir", LONG_PATH)
    shown(pf, 320, 40)
    check(pf.text.text() != LONG_PATH and "..." in pf.text.text() or "\u2026" in pf.text.text(), "PathField cuts a long path in the middle")
    pf.pick = lambda: r"C:\x"
    pf.browse.click()
    check(pf.path() == r"C:\x", "PathField takes the picked path")
    pf.close()

    # the "i": hover/click shows the popover, Esc hides it
    info = uikit.InfoButton("Some help")
    shown(info)
    info.show_tip()
    check(info.tip_visible(), "InfoButton.show_tip shows the popover")
    info.setFocus(Qt.TabFocusReason)
    key(info, Qt.Key_Escape)
    check(not info.tip_visible(), "Esc hides the popover")
    info.close()

    # tooltips of plain widgets use the kit style (a QToolTip rule in the root sheet)
    check("QToolTip" in uikit.style.stylesheet(), "the root sheet styles QToolTip")

    # long strings in the footer and header do not widen the window
    long_window = uikit.ToolWindow(None, title=LONG, subtitle=LONG_PATH, wizard=False)
    long_window.set_content(QtWidgets.QWidget())
    long_window.set_status(LONG * 3)
    shown(long_window)
    area = QtWidgets.QApplication.primaryScreen().availableGeometry()
    check(long_window.width() <= area.width(), "long title and status do not widen the window past the screen")
    long_window.close()


def contrast_and_tokens():
    bad = [(n, round(tokens.contrast(f, b), 2)) for n, f, b in tokens.TEXT_PAIRS if tokens.contrast(f, b) < 4.5]
    check(not bad, "every text pair meets WCAG AA 4.5:1 {}".format(bad))
    for name in tokens.skin_names():
        check(not tokens.contrast_failures(name), "skin {}: every pair meets WCAG AA ({})".format(name, tokens.contrast_failures(name)))


def kit_requests():
    """Behaviour of the components added for the tools (wrapping, run chaining, table ticks, slider, cards...)."""
    metrics.set_scale(1.0)
    long_text = "This is a long message that has to wrap over several lines inside the banner of a window. " * 3
    window = uikit.ToolWindow(None, title="Height", wizard=False)
    body = QtWidgets.QWidget()
    uikit.vbox(parent=body).addWidget(uikit.label("content"))
    window.set_content(body)
    window.show()
    pump(120)
    h0 = window.height()
    window.show_banner("danger", long_text)
    pump(150)
    h1 = window.height()
    need = window.banner.height() + metrics.px(tokens.L)
    check(h1 > h0 and abs((h1 - h0) - need) <= metrics.px(4), "banner with wrapped text grows the window by its own height ({} -> {}, banner {})".format(h0, h1, need))
    check(window.banner.minimumSizeHint().height() < 2 * metrics.px(tokens.CONTROL_H) + metrics.px(20), "banner minimum is about one line")
    window.clear_banner()
    pump(150)
    check(abs(window.height() - h0) <= metrics.px(4), "the window shrinks back when the banner goes ({} vs {})".format(window.height(), h0))
    check(not cut_off(window), "nothing clipped after the banner cycle")
    window.close()

    es = uikit.EmptyState("Nothing here", "A long explanation of why this list is empty and what to do about it, wrapped. " * 2)
    es.resize(metrics.px(300), 10)
    es.show()
    pump(60)
    check(es.layout().hasHeightForWidth() and es.heightForWidth(metrics.px(300)) > metrics.px(100), "empty state wraps (height for width {})".format(es.heightForWidth(metrics.px(300))))
    es.close()

    table = uikit.DataTable([uikit.Column("a", "A"), uikit.Column("b", "B")], empty_title="No rows", empty_text="Long text for the empty table that must wrap. " * 3)
    table.resize(metrics.px(260), metrics.px(130))
    table.show()
    pump(80)
    check(table.minimumHeight() >= table.empty.heightForWidth(table.width()) - 2, "table empty state is never clipped (min {} for {})".format(table.minimumHeight(), table.empty.heightForWidth(table.width())))
    table.close()

    win = uikit.ToolWindow(None, title="Run")
    win.set_content(QtWidgets.QWidget())
    win.show()
    order = []

    def second_done(result):
        order.append("second")

    def first_done(result):
        order.append("first")
        win.run(lambda progress: time.sleep(0.15) or "b", on_done=second_done)
    win.run(lambda progress: "a", on_done=first_done)
    check(until(lambda: "first" in order and win.is_busy(), 2), "the second job is running after on_done started it")
    pump(40)
    check(win.is_busy(), "the second job was not ended by the first one's end")
    check(until(lambda: "second" in order and not win.is_busy(), 3), "the second job ends by itself")
    ran = []
    win.run(lambda progress: [time.sleep(0.01) or progress.check() for _ in range(30)] and ran.append(1), cancellable=False, on_done=lambda r: order.append("fixed"))
    pump(30)
    key(win, Qt.Key_Escape)
    check(until(lambda: "fixed" in order, 3) and ran, "Esc does not cancel a task started with cancellable=False")
    win.close()

    cols = [uikit.Column("on", "", kind="check"), uikit.Column("st", "State", kind="status"), uikit.Column("name", "Name", stretch=True),
            uikit.Column("v", "Value", align="right", kind="number")]
    t = uikit.DataTable(cols, selection="extended")
    t.set_rows([["", "ok", "alpha", ("1.50", 1.5)], ["", "warn", uikit.cell("beta", tone="danger", bold=True), ("0.20", 0.2)], ["", "error", "gamma", ("9", 9)]],
               checked=[0], disabled=[2])
    shown(t, 420, 200)
    check(t.checked_rows() == [0], "checked rows are reported")
    t.set_checked(1, True)
    t.set_checked(2, True)
    check(t.checked_rows() == [0, 1], "a disabled row cannot be ticked")
    check(not t.is_row_enabled(2) and t.is_row_enabled(0), "disabled rows are reported")
    t.check_all(False)
    check(t.checked_rows() == [], "check_all(False) unticks")
    t.sort_by("v", True)
    check(t.row_data(0)[2] == "alpha" or t.row_data(0)[3] == ("1.50", 1.5), "sorting keeps source rows")
    shot(t, "table_features", 1.0)
    t.close()

    sl = uikit.Slider(50, 0, 100, 0, 5, "%", typed=True)
    shown(sl, 360)
    sl.set_value(72, emit=True)
    check(sl.value() == 70 and sl.number.value() == 70, "slider snaps to its step and its typed box follows")
    key(sl.track, Qt.Key_Right, Qt.ShiftModifier)
    check(sl.value() == 100, "Shift moves a slider ten steps")
    sl.close()
    nf = shown(uikit.NumberField(1.0, 0, 1000, 2, 0.5, "m"))
    nf.setFocus()
    key(nf, Qt.Key_Up, Qt.ShiftModifier)
    check(nf.value() == 6.0, "Shift + Up on a number field adds ten steps")
    nf.setText("1.23456")
    nf.commit()
    check(nf.value() == 1.23, "a typed value is rounded to the declared decimals")
    nf.close()
    a, b = uikit.Disclosure("A"), uikit.Disclosure("B")
    uikit.DisclosureGroup(a, b)
    a.set_open(True)
    b.set_open(True)
    check(b.is_open() and not a.is_open(), "opening a disclosure of a group closes its sibling")
    chip = uikit.Chip("Rocks", True)
    check(chip.isChecked(), "chip is checkable")
    ms = uikit.MultiSelect([("a", "Alpha"), ("b", "Beta"), ("c", "Gamma")], "Layers", ["b"])
    ms.menu().actions()[2].setChecked(True)
    check(ms.selected() == ["b", "c"] and "(2)" in ms.summary(), "multi-select reports its ticks")
    group = uikit.ChoiceGroup()
    c1, c2 = uikit.ChoiceCard("One", "First", "1"), uikit.ChoiceCard("Two", "Second", "2")
    group.add(c1)
    group.add(c2)
    c2.click()
    check(group.value() == "2" and not c1.isChecked(), "choice cards are exclusive")
    kv = uikit.KeyValue([("File", "D:/a/very/long/path/that/will/break/anywhere_in_a_card.tif")])
    shown(kv, 220)
    check(kv.value_of("File").endswith("card.tif") and kv.rows[0][1].heightForWidth(150) > kv.rows[0][1].fontMetrics().lineSpacing(), "a long path breaks inside a KeyValue")
    kv.close()
    first = uikit.present(uikit.ToolWindow)
    check(uikit.present(uikit.ToolWindow) is first, "present() keeps one instance per class")
    first.close()
    uikit.ToolWindow.forget()


def theme_button():
    """One click on the header's round button cycles dark, medium, light; every open window and dock follows and the choice is stored."""
    metrics.set_scale(1.0)
    previous = tokens.active_skin()
    tokens.use("dark")
    first = uikit.ToolWindow(None, title="First")
    first.set_content(QtWidgets.QWidget())
    second = uikit.ToolWindow(None, title="Second", wizard=False)
    second.set_content(QtWidgets.QWidget())
    dock = uikit.DockShell("Panel")
    for w in (first, second, dock):
        w.show()
    pump(100)
    button = first.header.theme_button
    check(button.width() == metrics.px(28) and button.focusPolicy() != Qt.NoFocus, "the theme button is 28 px and reachable by Tab")
    for expected in ("medium", "light", "dark"):
        button.click()
        pump(60)
        bg = tokens.resolve(expected)["BG"]
        sheets = [w.root.styleSheet() for w in (first, second)] + [dock.root.styleSheet()]
        check(tokens.active_skin() == expected and all(bg in sh for sh in sheets), "after a click the skin is {} in every open window and dock".format(expected))
        check(config.get("ui", "skin") == expected, "the choice is stored in ui.skin ({})".format(expected))
        shown = {"medium": "Metashape"}.get(expected, expected.title())
        check(second.header.theme_button.toolTip() and shown in second.header.theme_button.toolTip(), "the tooltip names the state ({})".format(shown))
    image = first.grab().toImage()
    check(not image.isNull(), "the window paints after the switches")
    config.put("ui", "skin", "nonsense")
    check(tokens.configured_skin() == "dark", "an unknown value in the settings means dark")
    uikit.style.switch_skin("light")
    pump(60)
    fresh = uikit.ToolWindow(None, title="Fresh")
    fresh.set_content(QtWidgets.QWidget())
    fresh.show()
    pump(60)
    check(tokens.resolve("light")["BG"] in fresh.root.styleSheet(), "a window opened afterwards gets the stored skin")
    uikit.style.switch_skin(previous, save=False)
    for w in (first, second, dock, fresh):
        w.close()


def stylesheet_cache():
    metrics.set_scale(1.0)
    a = uikit.style.stylesheet()
    check(uikit.style.stylesheet() is a, "the style sheet is built once and cached")
    metrics.set_scale(1.5)
    b = uikit.style.stylesheet()
    check(b is not a and uikit.style.stylesheet() is b, "a second scale builds a second sheet, also cached")
    metrics.set_scale(1.0)


def main():
    start = time.time()
    contrast_and_tokens()
    stylesheet_cache()
    behaviours()
    kit_requests()
    theme_button()
    for scale in ((1.0,) if "--only100" in sys.argv else (1.0, 1.25, 1.5)):
        if scale == 1.25 and SHOTS:
            pass
        render_all(scale) if (SHOTS or scale != 1.25) else None
    metrics.set_scale(None)
    problem = GUARD.check()
    check(problem is None, "the real settings file was not touched" + ("" if problem is None else ": " + problem))
    print("{} checks failed in {:.1f}s".format(len(FAILURES), time.time() - start))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    for top in APP.topLevelWidgets():                  # Qt5 on Windows can crash while the process unloads with live widgets
        top.close()
        top.deleteLater()
    APP.processEvents(QtCore.QEventLoop.AllEvents, 50)
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
    os._exit(code)
