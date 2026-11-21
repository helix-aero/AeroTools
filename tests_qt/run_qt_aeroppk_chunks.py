"""The Aero-PPK window works on one chunk: header drop-down, switching, reset, lock while a job runs. A fake Metashape document, no network.

    "C:\\Program Files\\Agisoft\\Metashape_v22\\python\\python.exe" -B tests_qt\\run_qt_aeroppk_chunks.py [out folder]

A fake document of three chunks (two with the photos of a small synthetic flight each, one with the same label and no photo) is put in
`Metashape.app.document`; the window is opened on it. The script checks the drop-down of the header, the chunk picked in it (the fake
document must follow), the chunk changed in the fake document (found by the timer alone, and when the window is shown again), the reset of
the steps, flights and results, the lock while a job runs, a chunk removed while the window is open, and the cases without document or
without chunk. Screenshots of the header go to the out folder. Settings go to a temporary LOCALAPPDATA. Prints PASS / FAIL lines.
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

GUARD = _isolation.Guard("aeroppk_chunks_")
OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="aeroppk_chunks_out_")
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


class FakeCamera(object):
    def __init__(self, path):
        self.photo = types.SimpleNamespace(path=path)
        self.label = os.path.basename(path)
        self.reference = types.SimpleNamespace(location=None, location_accuracy=None, location_enabled=True)


class FakeChunk(object):
    def __init__(self, key, label, paths=()):
        self.key, self.label, self.cameras, self.crs = key, label, [FakeCamera(p) for p in paths], None


class FakeDoc(object):
    def __init__(self, chunks, path=""):
        self.chunks, self.path = list(chunks), path
        self._active = self.chunks[0] if self.chunks else None

    @property
    def chunk(self):
        return self._active

    @chunk.setter
    def chunk(self, value):
        self._active = value

    def remove(self, chunk):
        self.chunks.remove(chunk)
        if self._active is chunk:
            self._active = self.chunks[0] if self.chunks else None


meta = types.ModuleType("Metashape")
meta.app = types.SimpleNamespace(document=None, update=lambda: None)
meta.Vector = lambda *a: a
sys.modules["Metashape"] = meta

from aerotools import config, msapi, theme  # noqa: E402
from aerotools.qt import QtCore, QtGui, QtWidgets  # noqa: E402

GUARD.pin(config)
from aerotools.tools.aeroppk import dialog, state as S  # noqa: E402

FAILURES = []

MRK = ("1\t470091.021653\t[2420]\t     7,N\t   -33,E\t    88,V\t45.00000927,Lat\t5.00003899,Lon\t95.679,Ellh\t0.011931, 0.009100, 0.022696\t50,Q\n"
       "2\t470091.727536\t[2420]\t     8,N\t   -20,E\t    93,V\t45.00001193,Lat\t5.00003190,Lon\t95.669,Ellh\t0.011955, 0.009114, 0.022720\t50,Q\n"
       "3\t470092.427536\t[2420]\t     8,N\t   -20,E\t    93,V\t45.00001293,Lat\t5.00003290,Lon\t95.669,Ellh\t0.011955, 0.009114, 0.022720\t50,Q\n")


def check(name, condition, detail=""):
    print("{} {}{}".format("PASS" if condition else "FAIL", name, "  [{}]".format(detail) if detail else ""))
    if not condition:
        FAILURES.append(name)


def pump(app, seconds=0.3):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def shot(app, dlg, name, seconds=0.3):
    """The whole window and its header alone (the part that is under test)."""
    pump(app, seconds)
    path = os.path.join(OUT, name + ".png")
    dlg.grab().save(path)
    dlg.grab(QtCore.QRect(0, 0, dlg.width(), 64)).save(os.path.join(OUT, name + "_header.png"))
    print("shot", path)


def make_flight(base, name, start):
    """A folder in the format of a Mavic 3 Enterprise flight, empty photos, three trigger lines. Returns the photo paths."""
    folder = os.path.join(base, "DJI_20300101%04d_001_%s" % (start, name))
    os.makedirs(folder)
    with open(os.path.join(folder, "DJI_20300101%04d_001_%s_Timestamp.MRK" % (start, name)), "w") as handle:
        handle.write(MRK)
    paths = []
    for i in (1, 2, 3):
        path = os.path.join(folder, "DJI_20300101%06d_%04d_V.JPG" % ((start + i) * 100, i))   # 14 digits like the real names
        with open(path, "wb") as handle:
            handle.write(b"x")
        paths.append(path)
    return paths


def texts(combo):
    return [combo.itemText(i) for i in range(combo.count())]


def current_key(dlg):
    return dlg.chunk_combo.itemData(dlg.chunk_combo.currentIndex())


def flight_folders(dlg):
    return [os.path.basename(f["files"].folder) for f in dlg.state.flights]


def main():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    theme.load_fonts(QtGui)
    work = tempfile.mkdtemp(prefix="aeroppk_chunks_data_")
    a_paths, b_paths = make_flight(work, "SITEA", 1000), make_flight(work, "SITEB", 1100)
    a, b, c = FakeChunk(11, "Block A", a_paths), FakeChunk(12, "Block B", b_paths), FakeChunk(13, "Block A")
    doc = FakeDoc([a, b, c], path=os.path.join(work, "Demo project.psx"))
    meta.app.document = doc

    # ---- opened on the active chunk
    dlg = dialog.PpkDialog()
    dlg.show()
    pump(app, 0.5)
    check("the drop-down lists the three chunks with a small photo count", texts(dlg.chunk_combo) == ["Block A (3)", "Block B (3)", "Block A #2 (0)"],
          str(texts(dlg.chunk_combo)))
    check("the active chunk is selected and the window is bound to it", current_key(dlg) == 11 and dlg.bound_key == 11)
    check("the project is named in the header", "Demo project" in dlg.project_label.fullText(), dlg.project_label.fullText())
    check("only the photos of the chunk are read", flight_folders(dlg) == [os.path.basename(os.path.dirname(a_paths[0]))], str(flight_folders(dlg)))
    check("the drop-down is enabled", dlg.chunk_combo.isEnabled())
    files = dlg.screens[S.FILES]
    check("every photo of the chunk is in a listed flight: no note", files.uncovered_count() == 0)
    files.chunk_paths().add("c:/elsewhere/dji_20260101000000_0001_v.jpg")          # a photo of the chunk that no listed flight holds
    check("a chunk photo that no listed flight holds is counted", files.uncovered_count() == 1, str(files.uncovered_count()))
    files.chunk_paths().discard("c:/elsewhere/dji_20260101000000_0001_v.jpg")
    check("the drone step says what was found in the chunk", dlg.screens[S.DRONE].found.fullText() == "3 photos in 1 flight, taken with DJI Mavic 3 Enterprise.",
          dlg.screens[S.DRONE].found.fullText())
    dlg.go(S.MODE, force=True)
    check("the footer says the photos and the flights, with no frame and no dash", dlg.status.fullText() == "3 photos in 1 flight", dlg.status.fullText())
    dlg.go(S.DRONE, force=True)
    shot(app, dlg, "1_chunk_a")

    # ---- the user works a little, then picks another chunk in the drop-down
    old_state = dlg.state
    dlg.screens[S.BASE].net.map.set_tiles_enabled(False)
    dlg.go(S.BASE, force=True)
    dlg.state.results = [object()]
    dlg.stored.append("a stored solution")
    dlg.chunk_combo.setCurrentIndex(1)
    dlg.chunk_combo.activated.emit(1)
    pump(app, 0.5)
    check("picking a chunk makes it the active chunk of the document", doc.chunk is b)
    check("the window is bound to the picked chunk", dlg.bound_key == 12 and current_key(dlg) == 12)
    check("the steps start again", dlg.current_step() == S.DRONE and dlg.stack.currentIndex() == S.DRONE)
    check("the stored solutions are dropped with the chunk", dlg.stored == [])
    check("a fresh state: no results, not the old object", dlg.state is not old_state and dlg.state.results == [])
    check("the pages work on the new state", all(s.state is dlg.state for s in dlg.screens))
    check("the new chunk is read", flight_folders(dlg) == [os.path.basename(os.path.dirname(b_paths[0]))], str(flight_folders(dlg)))
    check("the step bar is back on the first step", dlg.steps.current == S.DRONE)
    shot(app, dlg, "2_chunk_b_picked")

    # ---- the active chunk changed in Metashape: the timer alone finds it
    doc.chunk = a
    pump(app, dialog.CHUNK_POLL_MS / 1000.0 + 1.0)
    check("the timer follows the active chunk of Metashape", dlg.bound_key == 11 and current_key(dlg) == 11 and
          flight_folders(dlg) == [os.path.basename(os.path.dirname(a_paths[0]))], "%s %s" % (dlg.bound_key, flight_folders(dlg)))
    shot(app, dlg, "3_chunk_a_by_metashape")

    # ---- a chunk without photo, same label as the first one
    doc.chunk = c
    pump(app, dialog.CHUNK_POLL_MS / 1000.0 + 1.0)
    check("a chunk with the same label is told apart by its key", dlg.bound_key == 13 and current_key(dlg) == 13 and
          dlg.chunk_combo.currentText() == "Block A #2 (0)", dlg.chunk_combo.currentText())
    drone = dlg.screens[S.DRONE]
    check("a chunk without photos: no flight and a clear line", dlg.state.flights == [] and drone.empty.isVisible() and
          "no photos" in drone.empty.title.text() and not drone.device_card.isVisible(), drone.empty.title.text())
    check("a chunk without photos: Next is off and the footer says what to do", not dlg.next_button.isEnabled() and dlg.status.fullText() != "",
          dlg.status.fullText())
    shot(app, dlg, "4_chunk_without_photos")

    # ---- the window is closed, the chunk changes, the window is shown again
    doc.chunk = b
    dlg.hide()
    pump(app, 0.2)
    doc.chunk = a
    dlg.present()                                        # (what opening the tool again does: the active chunk is read again)
    pump(app, 0.4)
    check("shown again: the active chunk is read", dlg.bound_key == 11 and current_key(dlg) == 11, str(dlg.bound_key))

    # ---- a job runs: the chunk cannot change
    done = []
    dlg.run_job(lambda post, cancelled: time.sleep(4.0), lambda *_a: None, lambda job: done.append(job.state), "test")
    pump(app, 0.3)
    check("while a job runs the drop-down is locked, with the reason", not dlg.chunk_combo.isEnabled() and "calculation is running" in dlg.chunk_combo.toolTip(),
          dlg.chunk_combo.toolTip())
    shot(app, dlg, "5_locked")
    doc.chunk = b
    pump(app, dialog.CHUNK_POLL_MS / 1000.0 + 0.6)
    check("a chunk change in Metashape during the job does not reset the window", dlg.bound_key == 11 and current_key(dlg) == 11 and
          flight_folders(dlg) == [os.path.basename(os.path.dirname(a_paths[0]))], str(dlg.bound_key))
    dlg.chunk_combo.activated.emit(0)
    check("a pick during the job is ignored", dlg.bound_key == 11)
    dlg.wait_idle(10)
    pump(app, 0.5)
    check("the job ended", done == ["done"], str(done))
    check("then the window follows Metashape (nothing to keep)", dlg.bound_key == 12 and current_key(dlg) == 12 and dlg.chunk_combo.isEnabled(), str(dlg.bound_key))

    # ---- a job that produces results, another chunk becomes active meanwhile: the results are kept and apply goes to their chunk
    def job_with_results(post, cancelled):
        time.sleep(1.0)

    def finished(job):
        dlg.state.results = [object()]
    dlg.run_job(job_with_results, lambda *_a: None, finished, "test")
    pump(app, 0.2)
    doc.chunk = a
    dlg.wait_idle(10)
    pump(app, dialog.CHUNK_POLL_MS / 1000.0 + 0.6)
    check("results of a job that ended on another active chunk are kept", dlg.bound_key == 12 and len(dlg.state.results) == 1 and current_key(dlg) == 12,
          str(dlg.bound_key))
    check("the bound chunk is still the one the results are for", dlg.bound_chunk() is b)
    shot(app, dlg, "6_results_kept_on_bound_chunk")
    doc.chunk = c                                                            # the active chunk changes again: the window follows
    pump(app, dialog.CHUNK_POLL_MS / 1000.0 + 0.6)
    check("a new change of the active chunk drops them", dlg.bound_key == 13 and dlg.state.results == [])

    # ---- the bound chunk is removed while a job runs, then the job ends
    doc.chunk = a
    pump(app, dialog.CHUNK_POLL_MS / 1000.0 + 0.6)
    dlg.run_job(lambda post, cancelled: time.sleep(4.0), lambda *_a: None, lambda job: None, "test")
    pump(app, 0.2)
    doc.remove(a)
    pump(app, dialog.CHUNK_POLL_MS / 1000.0 + 0.6)
    check("a removed bound chunk stays in the drop-down, marked removed, and is not found", dlg.bound_chunk() is None and
          any("(removed)" in t for t in texts(dlg.chunk_combo)) and "(removed)" in dlg.chunk_combo.currentText(), str(texts(dlg.chunk_combo)))
    shot(app, dlg, "7_chunk_removed_during_job")
    dlg.wait_idle(10)
    pump(app, 0.6)
    check("when the job ends the window moves to the active chunk", dlg.bound_key in (12, 13) and dlg.bound_chunk() is not None, str(dlg.bound_key))

    # ---- a chunk removed while the window is idle
    doc.chunk = b
    pump(app, dialog.CHUNK_POLL_MS / 1000.0 + 0.6)
    doc.remove(b)
    pump(app, dialog.CHUNK_POLL_MS / 1000.0 + 0.6)
    check("removing the active chunk: the window follows the new active one", dlg.bound_key == doc.chunk.key and len(texts(dlg.chunk_combo)) == len(doc.chunks),
          str(texts(dlg.chunk_combo)))
    doc.remove(c)
    pump(app, dialog.CHUNK_POLL_MS / 1000.0 + 0.6)
    check("a single chunk is listed", dlg.chunk_combo.count() == 1)
    doc.chunk = None
    for chunk in list(doc.chunks):
        doc.remove(chunk)
    pump(app, dialog.CHUNK_POLL_MS / 1000.0 + 0.6)
    check("no chunk left: the drop-down says so and is disabled", dlg.bound_key is None and texts(dlg.chunk_combo) == ["No chunk"] and
          not dlg.chunk_combo.isEnabled(), str(texts(dlg.chunk_combo)))
    check("no chunk: no flight, the window still works", dlg.state.flights == [] and dlg.current_step() == S.DRONE)
    shot(app, dlg, "8_no_chunk")
    d = FakeChunk(21, "Late chunk", a_paths)
    doc.chunks.append(d)
    doc.chunk = d
    pump(app, dialog.CHUNK_POLL_MS / 1000.0 + 0.6)
    check("a chunk that appears is taken", dlg.bound_key == 21 and len(dlg.state.flights) == 1)
    dlg.close()
    pump(app, 0.3)

    # ---- no document, unsaved project
    meta.app.document = None
    dlg = dialog.PpkDialog()
    dlg.show()
    pump(app, 0.5)
    check("no document: no chunk, drop-down disabled, project unsaved", dlg.bound_key is None and texts(dlg.chunk_combo) == ["No chunk"] and
          not dlg.chunk_combo.isEnabled() and "unsaved" in dlg.project_label.fullText(), dlg.project_label.fullText())
    shot(app, dlg, "9_no_document")
    meta.app.document = FakeDoc([FakeChunk(31, "Only chunk", a_paths)], path="")
    pump(app, dialog.CHUNK_POLL_MS / 1000.0 + 0.6)
    check("a document opened while the window is shown is taken (unsaved project)", dlg.bound_key == 31 and len(dlg.state.flights) == 1, str(dlg.bound_key))
    shot(app, dlg, "10_one_chunk_unsaved")
    dlg.close()
    pump(app, 0.3)
    problem = GUARD.check()
    check("real settings untouched", problem is None, str(problem))
    print("out:", OUT)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
