"""Aero-PPK works on one chunk: the drop-down lines, the decision when the active chunk changes, the Metashape helpers, the backups per chunk
key, and what Apply / Undo / Use chunk photos do with the bound chunk. No Qt and no Metashape: a fake document of fake chunks.
(The window itself, with its header and its timer, is in tests_qt/run_qt_aeroppk_chunks.py.)"""
import datetime
import json
import os
import types

import pytest

from aerotools import i18n
from aerotools.tools.aeroppk import backup, chunks, frames
from aerotools.tools.aeroppk.screen_export import ExportScreen
from aerotools.tools.aeroppk.screen_drone import DroneScreen
from aerotools.tools.aeroppk.screen_files import FilesScreen

import test_aeroppk_core as core


# -- fakes ----------------------------------------------------------------------------------------------------------

class FakeChunk(object):
    def __init__(self, key, label, paths=()):
        self.key, self.label = key, label
        self.cameras = [core.FakeCamera(p, (8.0, 41.0, 100.0)) for p in paths]
        self.crs = object()


class FakeDoc(object):
    """A document whose `chunk` can be set like the one of Metashape. `accept=False`: the setter does nothing; `explode=True`: it raises."""

    def __init__(self, chunks_=(), path="", accept=True, explode=False):
        self.chunks = list(chunks_)
        self.path = path
        self.accept, self.explode = accept, explode
        self._active = self.chunks[0] if self.chunks else None

    @property
    def chunk(self):
        return self._active

    @chunk.setter
    def chunk(self, value):
        if self.explode:
            raise RuntimeError("no")
        if self.accept:
            self._active = value

    def remove(self, chunk):
        self.chunks.remove(chunk)
        if self._active is chunk:
            self._active = self.chunks[0] if self.chunks else None


@pytest.fixture
def api(ms, monkeypatch):
    msapi = core.fake_metashape(monkeypatch, ms)

    def put(doc):
        ms.app.document = doc
        return doc
    msapi.put = put
    return msapi


# -- the lines of the drop-down -------------------------------------------------------------------------------------

def test_entries_show_the_name_and_a_small_photo_count():
    rows = chunks.entries([(1, "Block A", 412), (2, "Block B", 0)])
    assert [r["text"] for r in rows] == ["Block A (412)", "Block B (0)"]
    assert [r["key"] for r in rows] == [1, 2] and rows[1]["photos"] == 0


def test_entries_tell_duplicate_labels_apart_and_name_the_unnamed():
    rows = chunks.entries([(1, "Site", 10), (2, "Site", 20), (3, "Other", 5), (4, "Site", 7), (5, "", 1), (6, None, 2)])
    assert [r["text"] for r in rows][:4] == ["Site (10)", "Site #2 (20)", "Other (5)", "Site #3 (7)"]
    assert rows[4]["label"] == rows[5]["label"] == i18n.t("aeroppk.chunk.unnamed")
    assert len(set(r["key"] for r in rows)) == 6


def test_entries_of_nothing():
    assert chunks.entries([]) == []


# -- what to do when the active chunk of Metashape is not the bound one -------------------------------------------------

def test_decide_nothing_when_the_bound_chunk_is_the_active_one():
    assert chunks.decide(1, 1, False, True, True) == chunks.NONE
    assert chunks.decide(None, None, False, False, False) == chunks.NONE


def test_decide_never_starts_again_while_a_job_runs():
    assert chunks.decide(1, 2, True, False, True) == chunks.NONE
    assert chunks.decide(1, 2, True, True, True, after_job=True) == chunks.NONE


def test_decide_starts_again_on_the_new_active_chunk():
    assert chunks.decide(1, 2, False, False, True) == chunks.REBIND
    assert chunks.decide(1, 2, False, True, True) == chunks.REBIND                  # results, but nobody asked to keep them
    assert chunks.decide(None, 2, False, False, False) == chunks.REBIND             # a chunk appeared (first chunk, document opened)
    assert chunks.decide(1, None, False, False, True) == chunks.REBIND              # no active chunk any more


def test_decide_keeps_the_results_of_a_job_that_just_ended_on_another_active_chunk():
    assert chunks.decide(1, 2, False, True, True, kept=None, after_job=True) == chunks.KEEP
    assert chunks.decide(1, 2, False, True, True, kept=2) == chunks.KEEP            # and the next looks do not drop them
    assert chunks.decide(1, 3, False, True, True, kept=2) == chunks.REBIND          # until the active chunk changes again
    assert chunks.decide(1, 2, False, False, True, after_job=True) == chunks.REBIND  # nothing to keep
    assert chunks.decide(1, 2, False, True, False, after_job=True) == chunks.REBIND  # the bound chunk is gone: nothing to apply to


# -- Metashape helpers -----------------------------------------------------------------------------------------------

def test_no_document(api):
    api.put(None)
    assert api.chunks() == [] and api.chunk() is None and api.chunk_by_key(1) is None
    assert api.set_active_chunk(FakeChunk(1, "A")) is False
    assert api.chunk_camera_count(None) == 0 and api.document_path() == ""


def test_document_without_chunk(api):
    api.put(FakeDoc([]))
    assert api.chunks() == [] and api.chunk() is None and api.chunk_by_key(1) is None
    assert api.set_active_chunk(None) is False


def test_one_chunk_with_and_without_cameras(api):
    full, empty = FakeChunk(1, "Full", ["D:/f/a.JPG", "D:/f/b.JPG"]), FakeChunk(2, "Empty")
    api.put(FakeDoc([full, empty]))
    assert [api.chunk_camera_count(c) for c in api.chunks()] == [2, 0]
    rows = chunks.entries([(api.chunk_key(c), api.chunk_label(c), api.chunk_camera_count(c)) for c in api.chunks()])
    assert [r["text"] for r in rows] == ["Full (2)", "Empty (0)"]


def test_chunks_are_found_by_key_even_with_the_same_label(api):
    a, b = FakeChunk(5, "Site"), FakeChunk(9, "Site")
    api.put(FakeDoc([a, b]))
    assert api.chunk_by_key(9) is b and api.chunk_by_key(5) is a and api.chunk_by_key(7) is None and api.chunk_by_key(None) is None


def test_set_active_chunk_switches_and_reports_the_truth(api):
    a, b = FakeChunk(1, "A"), FakeChunk(2, "B")
    doc = api.put(FakeDoc([a, b]))
    assert api.set_active_chunk(b) is True and doc.chunk is b and api.chunk() is b
    assert api.set_active_chunk(a) is True and doc.chunk is a
    doc.accept = False
    assert api.set_active_chunk(b) is False and doc.chunk is a                     # Metashape did not follow
    doc.explode = True
    assert api.set_active_chunk(b) is False                                          # an exception never gets out


def test_a_removed_chunk_is_not_found_any_more(api):
    a, b = FakeChunk(1, "A"), FakeChunk(2, "B")
    doc = api.put(FakeDoc([a, b]))
    doc.remove(a)
    assert api.chunk_by_key(1) is None and api.chunk() is b and [c.key for c in api.chunks()] == [2]
    doc.remove(b)
    assert api.chunk() is None and api.chunks() == []


# -- backups per chunk key ---------------------------------------------------------------------------------------------

def _entries():
    return [{"path": "a.jpg", "label": "a", "location": [1.0, 2.0, 3.0], "accuracy": [10, 10, 10], "enabled": True}]


def test_backups_of_two_chunks_with_the_same_label_do_not_mix(tmp_path):
    folder, project = str(tmp_path), "C:/x/Site.psx"
    when = datetime.datetime(2026, 1, 2, 3, 4, 5)
    p1 = backup.new_path(folder, project, "Block", when, chunk_key=1)
    p2 = backup.new_path(folder, project, "Block", when, chunk_key=2)
    assert p1 != p2
    backup.save(p1, _entries(), "WGS 84", chunk="Block", chunk_key=1)
    backup.save(p2, _entries(), "WGS 84", chunk="Block", chunk_key=2)
    assert backup.find(folder, project, "Block", chunk_key=1, legacy=False) == [p1]
    assert backup.find(folder, project, "Block", chunk_key=2, legacy=False) == [p2]
    data = json.load(open(p1, encoding="utf-8"))
    assert data["chunk_key"] == 1 and data["chunk"] == "Block" and data["entries"] == _entries()


def test_old_backups_known_by_label_only_are_found_when_the_label_is_unique(tmp_path):
    folder, project = str(tmp_path), "C:/x/Site.psx"
    old = backup.new_path(folder, project, "Block", datetime.datetime(2026, 1, 2, 3, 4, 5))
    new = backup.new_path(folder, project, "Block", datetime.datetime(2026, 1, 1, 3, 4, 5), chunk_key=3)
    backup.save(old, _entries(), "WGS 84")
    backup.save(new, _entries(), "WGS 84", chunk_key=3)
    assert backup.find(folder, project, "Block", chunk_key=3, legacy=True) == [new, old]          # oldest first, whatever the name
    assert backup.find(folder, project, "Block", chunk_key=3, legacy=False) == [new]
    assert backup.find(folder, project, "Block") == [old]                                        # without key: the old behaviour
    assert backup.find(folder, project, "Block", chunk_key=4, legacy=False) == []


def test_backup_find_in_a_missing_folder(tmp_path):
    assert backup.find(str(tmp_path / "nothing"), "", "A", chunk_key=1) == []


def test_unsaved_project_backups_have_a_neutral_name(tmp_path):
    path = backup.new_path(str(tmp_path), "", "", datetime.datetime(2026, 1, 2, 3, 4, 5), chunk_key=1)
    assert os.path.basename(path) == "project_chunk_k1_AeroPPK_backup_20260102_030405.json"


# -- Apply / Undo on the bound chunk -----------------------------------------------------------------------------------

class Wizard(object):
    def __init__(self, api_, key, answer=True, on_confirm=None):
        self.api, self.key, self.answer, self.on_confirm, self.asked = api_, key, answer, on_confirm, []

    def bound_chunk(self):
        return self.api.chunk_by_key(self.key)

    def bound_name(self):
        chk = self.bound_chunk()
        return self.api.chunk_label(chk) if chk is not None else ""

    def confirm(self, text, ok=None, danger=False):
        self.asked.append(text)
        if self.on_confirm:
            self.on_confirm()
        return self.answer


class Status(object):
    text = tip = ""

    def setText(self, text):
        self.text = text

    def setToolTip(self, text):
        self.tip = text


def screen_for(wizard, photos):
    """An export screen without Qt: the real `apply` / `restore` on an object that has what they use."""
    screen = types.SimpleNamespace(wizard=wizard, status=Status(), state=types.SimpleNamespace(results=[]))
    screen._items = lambda: ([{"path": p, "lat": 45.0, "lon": 5.0, "h": 95.0, "sd_h": 0.02, "sd_v": 0.03} for p in photos], 1)
    screen.say = lambda kind, text, title=None, details=None: (screen.status.setText(text), screen.status.setToolTip(details or ""))   # the banner
    screen._bound = types.MethodType(ExportScreen._bound, screen)
    screen._refuse = types.MethodType(ExportScreen._refuse, screen)
    screen._frame = lambda chk, advanced=False: frames.ApplyFrame("WGS 84", "EPSG::4326")       # the frame decision has its own tests
    screen._label_is_unique = ExportScreen._label_is_unique
    return screen


def two_chunks(api, tmp_path, labels=("Block", "Other")):
    a = FakeChunk(1, labels[0], ["D:/f/a1.JPG", "D:/f/a2.JPG"])
    b = FakeChunk(2, labels[1], ["D:/f/b1.JPG"])
    doc = api.put(FakeDoc([a, b], path=str(tmp_path / "Site.psx")))
    return doc, a, b


def test_apply_goes_to_the_bound_chunk_even_when_another_one_is_active(api, tmp_path):
    doc, a, b = two_chunks(api, tmp_path)
    doc.chunk = b                                                                   # the user moved on in Metashape
    screen = screen_for(Wizard(api, 1), ["D:/f/a1.JPG", "D:/f/a2.JPG"])
    ExportScreen.apply(screen)
    assert [c.reference.location for c in a.cameras] == [(1005.0, 45.0, 95.0)] * 2
    assert b.cameras[0].reference.location == (8.0, 41.0, 100.0)                    # the active chunk is untouched
    assert "Block" in screen.wizard.asked[0] and "Block" in screen.status.text and "2 photo" in screen.status.text
    backups = [n for n in os.listdir(str(tmp_path)) if n.endswith(".json")]
    assert len(backups) == 1 and "_k1_" in backups[0]


def test_apply_leaves_out_the_photos_that_are_not_in_the_chunk(api, tmp_path):
    doc, a, b = two_chunks(api, tmp_path)
    screen = screen_for(Wizard(api, 1), ["D:/f/a1.JPG", "D:/f/b1.JPG", "D:/f/zz.JPG"])
    ExportScreen.apply(screen)
    assert a.cameras[0].reference.location == (1005.0, 45.0, 95.0) and a.cameras[1].reference.location == (8.0, 41.0, 100.0)
    assert b.cameras[0].reference.location == (8.0, 41.0, 100.0)
    assert "2 photos are not in this chunk" in screen.status.text and "1 photo position" in screen.status.text
    assert "not found" not in screen.status.text


def test_apply_with_no_photo_in_the_chunk_changes_nothing(api, tmp_path):
    doc, a, b = two_chunks(api, tmp_path)
    screen = screen_for(Wizard(api, 1), ["D:/f/b1.JPG"])
    ExportScreen.apply(screen)
    assert screen.wizard.asked == [] and "None of these photos is in chunk Block" in screen.status.text
    assert a.cameras[0].reference.location == (8.0, 41.0, 100.0) and not os.listdir(str(tmp_path))


def test_apply_and_undo_refuse_when_the_bound_chunk_is_gone(api, tmp_path):
    doc, a, b = two_chunks(api, tmp_path)
    screen = screen_for(Wizard(api, 1), ["D:/f/a1.JPG"])
    doc.remove(a)
    ExportScreen.apply(screen)
    assert screen.status.text == i18n.t("aeroppk.chunk.gone") and screen.wizard.asked == []
    screen.status.text = ""
    ExportScreen.restore(screen)
    assert screen.status.text == i18n.t("aeroppk.chunk.gone") and b.cameras[0].reference.location == (8.0, 41.0, 100.0)


def test_apply_refuses_when_the_chunk_disappears_while_the_question_is_open(api, tmp_path):
    doc, a, b = two_chunks(api, tmp_path)
    screen = screen_for(Wizard(api, 1, on_confirm=lambda: doc.remove(a)), ["D:/f/a1.JPG"])
    ExportScreen.apply(screen)
    assert screen.status.text == i18n.t("aeroppk.chunk.gone") and not os.listdir(str(tmp_path))


def test_apply_declined_changes_nothing(api, tmp_path):
    doc, a, b = two_chunks(api, tmp_path)
    screen = screen_for(Wizard(api, 1, answer=False), ["D:/f/a1.JPG"])
    ExportScreen.apply(screen)
    assert a.cameras[0].reference.location == (8.0, 41.0, 100.0) and not os.listdir(str(tmp_path))


def test_undo_puts_back_the_bound_chunk_only_and_uses_its_own_backup(api, tmp_path):
    doc, a, b = two_chunks(api, tmp_path, labels=("Block", "Block"))               # duplicate labels
    screen_a = screen_for(Wizard(api, 1), ["D:/f/a1.JPG", "D:/f/a2.JPG"])
    screen_b = screen_for(Wizard(api, 2), ["D:/f/b1.JPG"])
    ExportScreen.apply(screen_a)
    ExportScreen.apply(screen_b)
    assert a.cameras[0].reference.location == (1005.0, 45.0, 95.0) and b.cameras[0].reference.location == (1005.0, 45.0, 95.0)
    doc.chunk = b                                                                   # B is active, the window is bound to A
    ExportScreen.restore(screen_a)
    assert a.cameras[0].reference.location == (8.0, 41.0, 100.0)
    assert b.cameras[0].reference.location == (1005.0, 45.0, 95.0)                  # B keeps its PPK positions
    assert "2 photo positions restored in Block" in screen_a.status.text


def test_undo_without_backup_for_this_chunk(api, tmp_path):
    doc, a, b = two_chunks(api, tmp_path)
    ExportScreen.apply(screen_for(Wizard(api, 1), ["D:/f/a1.JPG"]))
    screen = screen_for(Wizard(api, 2), ["D:/f/b1.JPG"])
    ExportScreen.restore(screen)
    assert screen.status.text == i18n.t("aeroppk.restore_none") and screen.wizard.asked == []


def test_apply_on_an_unsaved_project_still_works(api, tmp_path, monkeypatch):
    from aerotools.tools.aeroppk.stations import rgp
    monkeypatch.setattr(rgp, "cache_dir", lambda: str(tmp_path))
    a = FakeChunk(1, "Block", ["D:/f/a1.JPG"])
    api.put(FakeDoc([a], path=""))
    screen = screen_for(Wizard(api, 1), ["D:/f/a1.JPG"])
    ExportScreen.apply(screen)
    assert a.cameras[0].reference.location == (1005.0, 45.0, 95.0)
    assert [n for n in os.listdir(str(tmp_path)) if n.endswith(".json")][0].startswith("project_Block_k1_")


# -- Use chunk photos -------------------------------------------------------------------------------------------------

def rover_for(api, key, flights_=()):
    taken = []
    screen = types.SimpleNamespace(wizard=Wizard(api, key), state=types.SimpleNamespace(flights=list(flights_), all_flights=list(flights_)), _chunk_paths=None,
                                   set_folders=lambda folders: taken.append(list(folders)))
    screen.chunk_paths = types.MethodType(FilesScreen.chunk_paths, screen)
    screen.outside_count = types.MethodType(FilesScreen.outside_count, screen)
    return screen, taken


def test_use_chunk_photos_reads_only_the_bound_chunk(api, tmp_path):
    a = FakeChunk(1, "A", ["D:/one/a1.JPG", "D:/one/a2.JPG", "D:/two/a3.JPG"])
    b = FakeChunk(2, "B", ["D:/three/b1.JPG"])
    doc = api.put(FakeDoc([a, b]))
    doc.chunk = b
    screen, taken = rover_for(api, 1)
    DroneScreen.use_project(screen)
    assert taken == [["D:/one", "D:/two"]]
    screen, taken = rover_for(api, 2)
    DroneScreen.use_project(screen)
    assert taken == [["D:/three"]]


def test_use_chunk_photos_without_chunk_or_cameras_gives_no_folder(api):
    api.put(None)
    screen, taken = rover_for(api, None)
    DroneScreen.use_project(screen)
    assert taken == [[]]
    api.put(FakeDoc([FakeChunk(1, "Empty")]))
    screen, taken = rover_for(api, 1)
    DroneScreen.use_project(screen)
    assert taken == [[]]
    api.put(FakeDoc([]))
    screen, taken = rover_for(api, 1)                                               # the key of a chunk that is gone
    DroneScreen.use_project(screen)
    assert taken == [[]]


def flight(*photos):
    return {"files": types.SimpleNamespace(photos=list(photos))}


def test_photos_of_a_picked_folder_that_are_not_in_the_chunk_are_counted(api):
    a = FakeChunk(1, "A", ["D:/one/a1.JPG", "D:/one/a2.JPG"])
    api.put(FakeDoc([a]))
    screen, _ = rover_for(api, 1, [flight("d:\\one\\A1.jpg", "D:/one/a2.JPG", "D:/one/a3.JPG", "D:/one/a4.JPG")])
    assert screen.outside_count() == 2
    screen, _ = rover_for(api, None, [flight("D:/one/a3.JPG")])
    assert screen.outside_count() == 0                                              # no chunk: nothing to compare with
    api.put(FakeDoc([FakeChunk(1, "Empty")]))
    screen, _ = rover_for(api, 1, [flight("D:/x/1.JPG", "D:/x/2.JPG")])
    assert screen.outside_count() == 2
