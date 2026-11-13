"""Aero-Space: imported clouds are kept, nothing-to-remove never saves, recycle is verified, one kept-scan per folder."""

import os
import shutil
import threading

import pytest

from aerotools.tools.fastspace import actions, scanner, tool
from test_fastspace import FakeChunk, FakeCloud, fake_metashape, make_project   # noqa: F401  (fixture)


def project_of(tmp_path, name="p", **kw):
    return scanner.measure_project(make_project(tmp_path, name, **kw))


IMPORTED = {"import_points/format": "las"}


# -- P1-1: only a cloud that is provably built is removed -----------------------------------------------------------

def test_an_imported_cloud_is_never_removed():
    chunk = FakeChunk("C", "x", 0)
    chunk.point_clouds = [FakeCloud("built"), FakeCloud("las", IMPORTED), FakeCloud("unknown", {})]
    assert actions.strip_chunk(chunk, ("dense",)) == ["dense"]
    assert [c.name for c in chunk.point_clouds] == ["las", "unknown"]          # when in doubt: kept


def test_is_rebuildable_needs_a_build_key_and_no_import_key():
    assert actions.is_rebuildable(FakeCloud("a", {"build_depth/downscale": 4}))
    assert not actions.is_rebuildable(FakeCloud("b", {}))
    assert not actions.is_rebuildable(FakeCloud("c", {"build_x": 1, "import_y": 2}))
    assert not actions.is_rebuildable(object())                                 # no meta at all


def test_lighten_keeps_the_imported_cloud_and_its_folder(tmp_path, fake_metashape):
    project = scanner.measure_project(make_project(tmp_path, dense=1000, depth=3000))
    chunk_state = {}
    real_open = actions._open

    def open_with_import(path, read_only):
        document = real_open(path, read_only)
        if not chunk_state:
            document.chunks[0].point_clouds = [FakeCloud("las", IMPORTED)]
            chunk_state["ok"] = True
        return document

    actions._open = open_with_import
    try:
        result = actions.lighten(project, actions.PERMANENT)
    finally:
        actions._open = real_open
    assert result["ok"], result
    assert result["kept_clouds"] == 1
    assert project.dense == 1000 and project.depth == 0                        # the cloud folder stays, depth maps go
    assert any("dense_cloud" in p for p in actions.removable_paths(project, ("dense",)))


def test_lighten_with_only_imported_clouds_and_no_depth_touches_nothing(tmp_path, fake_metashape):
    project = scanner.measure_project(make_project(tmp_path, dense=1000, depth=0))
    real_open = actions._open

    def open_imported(path, read_only):
        document = real_open(path, read_only)
        document.chunks[0].point_clouds = [FakeCloud("las", IMPORTED)]
        document.chunks[0].depth_maps_sets = []
        return document

    actions._open = open_imported
    try:
        before = open(project.path).read()
        result = actions.lighten(project, actions.PERMANENT)
    finally:
        actions._open = real_open
    assert result["ok"] and result["nothing"] and result["kept_clouds"] == 1
    assert [mode for mode, _ in fake_metashape["opened"]] == ["ro"]
    assert result["backup"] == "" and open(project.path).read() == before
    assert project.dense == 1000


# -- P1-2: nothing to remove = no safety copy, no save -------------------------------------------------------------

def test_a_project_with_nothing_to_remove_is_not_saved_or_copied(tmp_path, fake_metashape):
    project = scanner.measure_project(make_project(tmp_path, dense=0, depth=0))
    real_open = actions._open

    def open_empty(path, read_only):
        document = real_open(path, read_only)
        document.chunks[0].point_clouds, document.chunks[0].depth_maps_sets = [], []
        return document

    actions._open = open_empty
    try:
        result = actions.lighten(project, actions.PERMANENT, items=scanner.ITEMS)
    finally:
        actions._open = real_open
    assert result["ok"] and result["nothing"] and result["freed"] == 0
    assert [mode for mode, _ in fake_metashape["opened"]] == ["ro"]            # never opened for writing
    assert not [n for n in os.listdir(os.path.dirname(project.path)) if actions.BACKUP_MARK in n]


def _write_version(project, text):
    with open(project.path, "w") as handle:
        handle.write('<?xml version="1.0"?><document version="{}"><chunks/></document>'.format(text))


def test_a_project_of_an_older_metashape_is_not_saved_unless_allowed(tmp_path, monkeypatch, fake_metashape):
    project = scanner.measure_project(make_project(tmp_path, dense=1000, depth=3000))
    _write_version(project, "2.0.4")
    monkeypatch.setattr(actions, "running_version", lambda: (2, 2))
    result = actions.lighten(project, actions.PERMANENT)
    assert not result["ok"] and "upgrade" in result["error"] and "2.0" in result["error"]
    assert [mode for mode, _ in fake_metashape["opened"]] == ["ro"] and result["backup"] == ""
    assert len(actions.removable_paths(project)) == 2
    result = actions.lighten(project, actions.PERMANENT, allow_upgrade=True)
    assert result["ok"], result


def test_the_same_or_a_newer_project_is_saved_without_asking(tmp_path, monkeypatch, fake_metashape):
    project = scanner.measure_project(make_project(tmp_path, dense=1000, depth=3000))
    _write_version(project, "2.2.1")
    monkeypatch.setattr(actions, "running_version", lambda: (2, 2))
    assert actions.lighten(project, actions.PERMANENT)["ok"]


def test_project_version_reads_the_psx_header(tmp_path):
    project = project_of(tmp_path)
    _write_version(project, "1.8.5")
    assert actions.project_version(project.path) == (1, 8)
    assert actions.project_version(str(tmp_path / "absent.psx")) is None


# -- P1-3: recycle mode checks the bin --------------------------------------------------------------------------------

@pytest.mark.skipif(os.name != "nt", reason="the recycle bin is a Windows thing")
class TestRecycleVerification(object):
    def _remove(self, paths):
        for path in paths:
            shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)

    def test_everything_mapped_into_the_bin_is_ok(self, tmp_path, monkeypatch):
        target = tmp_path / "a"
        target.mkdir()

        def fake(paths, hwnd=0):
            self._remove(paths)
            return 0, False, {paths[0]: "C:\\$Recycle.Bin\\S-1\\$R123"}

        monkeypatch.setattr(actions, "_shfileop", fake)
        assert actions.recycle([str(target)]) == (True, "")

    def test_gone_without_a_mapping_into_the_bin_is_reported_as_not_in_the_bin(self, tmp_path, monkeypatch):
        target = tmp_path / "a"
        target.mkdir()

        def fake(paths, hwnd=0):                                  # the user said "delete permanently" in the Windows prompt
            self._remove(paths)
            return 0, False, {}

        monkeypatch.setattr(actions, "_shfileop", fake)
        ok, message = actions.recycle([str(target)])
        assert ok and message.startswith(actions.NOT_IN_BIN)

    def test_gone_with_no_proof_at_all_is_reported_as_not_in_the_bin(self, tmp_path, monkeypatch):
        target = tmp_path / "a"
        target.mkdir()

        def fake(paths, hwnd=0):
            self._remove(paths)
            return 0, False, None

        monkeypatch.setattr(actions, "_shfileop", fake)
        assert actions.recycle([str(target)])[1].startswith(actions.NOT_IN_BIN)

    def test_a_refusal_deletes_nothing(self, tmp_path, monkeypatch):
        target = tmp_path / "a"
        target.mkdir()
        monkeypatch.setattr(actions, "_shfileop", lambda paths, hwnd=0: (0, True, None))
        ok, _message = actions.recycle([str(target)])
        assert not ok and target.exists()

    def test_delete_project_does_not_claim_the_bin_when_it_was_not(self, tmp_path, monkeypatch):
        project = project_of(tmp_path)

        def fake(paths, hwnd=0):
            self._remove(paths)
            return 0, False, {}

        monkeypatch.setattr(actions, "_shfileop", fake)
        result = actions.delete_project(project, actions.RECYCLE)
        assert result["ok"] and result["in_bin"] == 0 and result["freed"] == project.total
        assert result["note"].startswith(actions.NOT_IN_BIN)

    def test_delete_project_counts_a_verified_bin(self, tmp_path, monkeypatch):
        project = project_of(tmp_path)

        def fake(paths, hwnd=0):
            self._remove(paths)
            return 0, False, {p: "D:\\$RECYCLE.BIN\\S-1\\$R" + str(i) for i, p in enumerate(paths)}

        monkeypatch.setattr(actions, "_shfileop", fake)
        result = actions.delete_project(project, actions.RECYCLE)
        assert result["ok"] and result["in_bin"] == project.total and result["note"] == ""


def test_an_unknown_mode_never_deletes(tmp_path):
    project = project_of(tmp_path)
    result = actions.delete_project(project, "recicle")
    assert not result["ok"] and "unknown" in result["error"]
    assert os.path.exists(project.path) and os.path.isdir(project.files_dir)


def test_the_delete_worker_gets_the_open_project_from_the_ui_thread(tmp_path, monkeypatch):
    project = project_of(tmp_path)
    monkeypatch.setattr(actions, "current_document_path", lambda: pytest.fail("read in the worker"))
    assert not actions.delete_project(project, actions.PERMANENT, here=project.path)["ok"]
    assert os.path.exists(project.path)
    assert actions.delete_project(project, actions.PERMANENT, here="")["ok"]


def test_a_backup_folder_of_the_same_second_is_not_wiped_by_a_failed_copy(tmp_path):
    import datetime
    project = project_of(tmp_path)
    now = datetime.datetime(2026, 11, 12, 9, 0, 0)
    first = actions.safety_copy(project, now=now)
    with pytest.raises(actions.SpaceError):
        actions.safety_copy(project, now=now)                                  # same name: makedirs fails
    assert os.path.isfile(os.path.join(first, "RESTORE.txt"))


# -- P1-4: Reload during a batch -----------------------------------------------------------------------------------

class FakeDialog(object):
    def __init__(self, running):
        self.running, self.closed, self.deleted = running, False, False

    def batch_running(self):
        return self.running

    def close(self):
        self.closed = True

    def deleteLater(self):
        self.deleted = True


def test_uninstall_leaves_a_running_batch_alone():
    dialog = FakeDialog(True)
    tool._STATE["dialog"] = dialog
    assert tool.uninstall() is False
    assert not dialog.closed and not dialog.deleted and tool._STATE["dialog"] is dialog
    dialog.running = False
    assert tool.uninstall() is True
    assert dialog.closed and dialog.deleted and tool._STATE["dialog"] is None


# -- items 5, 6: the kept-scan and the export warning --------------------------------------------------------------

def test_threads_asking_for_the_same_folder_measure_it_once(tmp_path, monkeypatch):
    folder = str(tmp_path)
    calls = []
    release = threading.Event()
    real = scanner._measure_kept

    def slow(path):
        calls.append(path)
        release.wait(0.2)
        return real(path)

    monkeypatch.setattr(scanner, "_measure_kept", slow)
    scanner._KEPT_CACHE.clear()
    results = []
    threads = [threading.Thread(target=lambda: results.append(scanner.measure_kept(folder))) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(calls) == 1 and len({id(r) for r in results}) == 1


def test_delete_impact_counts_a_folder_once_even_with_separate_kept_objects(tmp_path):
    first = project_of(tmp_path, "one")
    second_psx = os.path.join(os.path.dirname(first.path), "two.psx")
    open(second_psx, "w").write("<document/>")
    second = scanner.Project(second_psx)
    second.kept = scanner.Kept()
    second.kept.files, second.kept.size = 7, 70
    first.kept = scanner.Kept()
    first.kept.files, first.kept.size = 7, 70                                  # equal but distinct objects: the old id() key missed it
    files, size, _risky = scanner.delete_impact([first, second])
    assert (files, size) == (7, 70)


def test_an_export_of_another_project_does_not_hide_the_warning(tmp_path):
    a = make_project(tmp_path, "siteA", extra=4000)
    folder = os.path.dirname(a)
    for name in ("siteB.psx", "siteB_ortho.tif"):
        open(os.path.join(folder, name), "w").write("x")
    os.makedirs(os.path.join(folder, "siteB.files"))
    project_a = scanner.measure_project(a)
    assert project_a.kept.exports and project_a.at_risk                        # the only export is B's
    open(os.path.join(folder, "siteA_dtm.tif"), "w").write("x")
    scanner._KEPT_CACHE.clear()
    assert not scanner.measure_project(a).at_risk


def test_a_single_project_folder_keeps_the_old_rule(tmp_path):
    psx = make_project(tmp_path, "solo", extra=4000)
    open(os.path.join(os.path.dirname(psx), "dtm.tif"), "w").write("x")
    assert not scanner.measure_project(psx).at_risk
