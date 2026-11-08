"""Fast Space: project measurement, locks, lightening and deletion (with a fake Metashape)."""

import os
import shutil

import pytest

from aerotools.tools.fastspace import actions, scanner


def make_project(root, name="p", dense=1000, depth=3000, tie=500, extra=0, chunks=1, with_psx=True, lock=False,
                 photos=0, mosaic=0):
    """A dummy project: p.psx and p.files/<chunk>/0/{dense_cloud, depth_maps.1, point_cloud, ...}.

    `photos`: bytes of orthophotos (images0.zip in orthomosaic); `mosaic`: bytes of the mosaic itself."""
    folder = os.path.join(str(root), name + "_dir")
    files = os.path.join(folder, name + ".files")
    os.makedirs(files)
    psx = os.path.join(folder, name + ".psx")
    if with_psx:
        with open(psx, "w") as handle:
            handle.write("<document/>")
    with open(os.path.join(files, "project.zip"), "wb") as handle:
        handle.write(b"z" * 10)
    for c in range(chunks):
        frame = os.path.join(files, str(c), "0")
        for part, size in (("dense_cloud", dense), ("depth_maps.1", depth), ("point_cloud", tie), ("elevation", extra)):
            if size:
                os.makedirs(os.path.join(frame, part))
                with open(os.path.join(frame, part, "data.zip"), "wb") as handle:
                    handle.write(b"x" * size)
        if photos or mosaic:
            ortho = os.path.join(frame, "orthomosaic")
            os.makedirs(ortho)
            for name_, size in (("images0.zip", photos), ("tile-1-1.tif", mosaic), ("orthomosaic.zip", 0)):
                if size or name_ == "orthomosaic.zip":
                    with open(os.path.join(ortho, name_), "wb") as handle:
                        handle.write(b"o" * size)
        with open(os.path.join(frame, "frame.zip"), "wb") as handle:
            handle.write(b"f" * 5)
    if lock:
        with open(os.path.join(files, "lock"), "wb") as handle:
            handle.write(b"lock")
    return psx


def test_part_names():
    assert scanner.part_of("depth_maps.12") == "depth_maps"
    assert scanner.part_of("dense_cloud") == "dense_cloud"
    assert scanner.part_of("tiled_model.1") == "tiled_model"


def test_measure_project_separates_dense_depth_and_the_rest(tmp_path):
    psx = make_project(tmp_path, dense=1000, depth=3000, tie=500, chunks=2)
    project = scanner.measure_project(psx)
    assert project.dense == 2000 and project.depth == 6000 and project.removable() == 8000
    assert project.parts["point_cloud"] == 1000
    assert project.total == project.psx_size + project.files_size
    assert project.total >= 8000 + 1000 + 10 + 10
    assert project.lock == scanner.FREE and not project.in_use


def test_orthophotos_are_counted_apart_from_the_orthomosaic(tmp_path):
    psx = make_project(tmp_path, dense=0, depth=0, tie=0, chunks=2, photos=5000, mosaic=300)
    project = scanner.measure_project(psx)
    assert project.ortho_photos == 10000 and project.parts["orthomosaic"] == 600
    assert project.size_of("ortho") == 10000 and project.removable(scanner.ITEMS) == 10000
    assert project.total >= 10600 and project.files_size >= 10600


def test_removable_adds_the_chosen_items_only(tmp_path):
    project = scanner.measure_project(make_project(tmp_path, dense=1000, depth=3000, photos=7000))
    assert project.removable() == 4000                                     # by default: dense + depth
    assert project.removable(("ortho",)) == 7000
    assert project.removable(scanner.ITEMS) == 11000
    assert project.removable(()) == 0


def test_scan_finds_projects_orphans_and_sorts_by_size(tmp_path):
    make_project(tmp_path, "small", dense=10, depth=10)
    make_project(tmp_path, "big", dense=5000, depth=5000)
    make_project(tmp_path, "orphan", with_psx=False)
    os.makedirs(os.path.join(str(tmp_path), "$RECYCLE.BIN", "x"))
    result = scanner.scan(str(tmp_path))
    names = [p.name for p in result["projects"]]
    assert names[0] == "big.psx" and "small.psx" in names
    orphans = [p for p in result["projects"] if p.orphan]
    assert len(orphans) == 1 and orphans[0].name == "orphan.files"
    assert not result["cancelled"]


def test_display_folder_gives_the_last_three_parents(tmp_path):
    psx = make_project(tmp_path)
    project = scanner.Project(r"D:\Projects\ClientA\2025\06-JUNE\Metashape\SiteA.psx")
    assert project.folders(3) == ["2025", "06-JUNE", "Metashape"]
    assert project.display_folder(2, sep="/") == "06-JUNE/Metashape"
    assert scanner.Project(r"D:\a\b.psx").folders(3) == ["D:\\", "a"]
    assert scanner.measure_project(psx).folders(1)


def test_gb_formatting():
    assert scanner.gb(2 * 1024 ** 3) == "2.00 GB"
    assert scanner.gb(50 * 1024 ** 2) == "50 MB"


@pytest.mark.skipif(os.name != "nt", reason="Windows exclusive lock")
def test_lock_states(tmp_path):
    import ctypes
    free = make_project(tmp_path, "free")
    stale = make_project(tmp_path, "stale", lock=True)
    held = make_project(tmp_path, "held", lock=True)
    assert scanner.measure_project(free).lock == scanner.FREE
    assert scanner.measure_project(stale).lock == scanner.STALE
    lock_path = os.path.splitext(held)[0] + ".files\\lock"
    GENERIC_RW, OPEN_EXISTING = 0xC0000000, 3
    ctypes.windll.kernel32.CreateFileW.restype = ctypes.c_void_p
    handle = ctypes.windll.kernel32.CreateFileW(lock_path, GENERIC_RW, 0, None, OPEN_EXISTING, 0, None)    # no sharing
    try:
        project = scanner.measure_project(held)
        assert project.lock == scanner.OPEN and project.in_use
        assert actions.refusal(project)
    finally:
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))


def test_reveal_selects_the_project_file_in_explorer(tmp_path, monkeypatch):
    project = tmp_path / "site one" / "a.psx"
    project.parent.mkdir()
    project.write_text("x")
    launched = []
    monkeypatch.setattr(actions.subprocess, "Popen", lambda command, *a, **k: launched.append(command))
    monkeypatch.setattr(actions.os, "name", "nt")
    assert actions.reveal(str(project)) is True
    assert launched == ['explorer /select,"{}"'.format(os.path.normpath(str(project)))]


def test_reveal_does_nothing_for_a_missing_path(tmp_path, monkeypatch):
    launched = []
    monkeypatch.setattr(actions.subprocess, "Popen", lambda command, *a, **k: launched.append(command))
    assert actions.reveal(str(tmp_path / "gone" / "a.psx")) is False
    assert launched == []


def test_confirmation_word():
    assert actions.confirmed("DELETE") and actions.confirmed(" delete ")
    assert not actions.confirmed("") and not actions.confirmed("del") and not actions.confirmed("DELETE IT")


def test_refusal_for_the_project_open_here(tmp_path, monkeypatch):
    project = scanner.measure_project(make_project(tmp_path))
    assert actions.refusal(project) == ""
    monkeypatch.setattr(actions, "current_document_path", lambda: project.path)
    assert "this Metashape" in actions.refusal(project)


# -- deletion ------------------------------------------------------------------------

def test_delete_permanent_removes_psx_and_files(tmp_path):
    psx = make_project(tmp_path)
    project = scanner.measure_project(psx)
    result = actions.delete_project(project, actions.PERMANENT)
    assert result["ok"] and result["freed"] == project.total and result["in_bin"] == 0
    assert not os.path.exists(psx) and not os.path.exists(project.files_dir)


def test_delete_recycle_uses_the_bin_and_keeps_nothing_behind(tmp_path, monkeypatch):
    sent = []

    def fake_recycle(paths):
        sent.extend(paths)
        for path in paths:
            shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
        return True, ""

    monkeypatch.setattr(actions, "recycle", fake_recycle)
    project = scanner.measure_project(make_project(tmp_path))
    result = actions.delete_project(project, actions.RECYCLE)
    assert result["ok"] and result["in_bin"] == project.total and result["freed"] == 0
    assert len(sent) == 2


def test_delete_refuses_an_open_project_and_removes_an_orphan(tmp_path, monkeypatch):
    project = scanner.measure_project(make_project(tmp_path))
    project.lock = scanner.OPEN
    result = actions.delete_project(project, actions.PERMANENT)
    assert not result["ok"] and os.path.exists(project.path)
    psx = make_project(tmp_path, "o", with_psx=False)
    orphan = scanner.measure_orphan(os.path.splitext(psx)[0] + ".files")
    assert actions.delete_project(orphan, actions.PERMANENT)["ok"] and not os.path.exists(orphan.files_dir)


def test_delete_keeps_every_file_next_to_the_project(tmp_path):
    psx = make_project(tmp_path)
    folder = os.path.dirname(psx)
    os.makedirs(os.path.join(folder, "Exports"))
    for rel in ("ortho.tif", "report.pdf", os.path.join("Exports", "dtm.tif")):
        with open(os.path.join(folder, rel), "wb") as handle:
            handle.write(b"d" * 100)
    project = scanner.measure_project(psx)
    result = actions.delete_project(project, actions.PERMANENT)
    assert result["ok"] and not os.path.exists(psx) and not os.path.exists(project.files_dir)
    assert sorted(os.listdir(folder)) == ["Exports", "ortho.tif", "report.pdf"]
    assert os.path.exists(os.path.join(folder, "Exports", "dtm.tif"))


def test_kept_counts_files_next_to_the_project_but_not_projects(tmp_path):
    psx = make_project(tmp_path, "a")
    folder = os.path.dirname(psx)
    other = os.path.join(folder, "b.files")
    os.makedirs(other)
    with open(os.path.join(folder, "b.psx"), "w") as handle:
        handle.write("<document/>")
    os.makedirs(os.path.join(folder, "Exports", "deep"))
    for rel, size in (("ortho.tif", 300), ("notes.txt", 20), (os.path.join("Exports", "dtm.tif"), 200),
                      (os.path.join("Exports", "deep", "ignored.tif"), 999)):
        with open(os.path.join(folder, rel), "wb") as handle:
            handle.write(b"d" * size)
    kept = scanner.measure_project(psx).kept
    assert kept.files == 3 and kept.size == 520 and kept.dirs == 2
    assert kept.exports and not kept.partial
    assert [name for _size, name in kept.biggest] == ["ortho.tif", "Exports/dtm.tif", "notes.txt"]


def test_at_risk_when_a_built_dem_has_no_export_next_to_it(tmp_path):
    risky = scanner.measure_project(make_project(tmp_path, "risky", extra=4000))
    assert risky.internal_products == 4000 and risky.at_risk
    psx = make_project(tmp_path, "safe", extra=4000)
    with open(os.path.join(os.path.dirname(psx), "dtm.TIF"), "wb") as handle:
        handle.write(b"d")
    safe = scanner.measure_project(psx)
    assert safe.kept.exports and not safe.at_risk
    plain = scanner.measure_project(make_project(tmp_path, "plain"))
    assert plain.internal_products == 0 and not plain.at_risk
    assert not scanner.measure_project(make_project(tmp_path, "orph", with_psx=False)).at_risk


def test_delete_impact_counts_a_shared_folder_once(tmp_path):
    first = make_project(tmp_path, "one")
    second = os.path.join(os.path.dirname(first), "two.psx")
    with open(second, "w") as handle:
        handle.write("<document/>")
    os.makedirs(os.path.join(os.path.dirname(first), "two.files"))
    with open(os.path.join(os.path.dirname(first), "ortho.tif"), "wb") as handle:
        handle.write(b"d" * 50)
    projects = [scanner.measure_project(first), scanner.measure_project(second)]
    files, size, risky = scanner.delete_impact(projects)
    assert files == 1 and size == 50 and risky == []


def test_delete_failure_is_reported_not_raised(tmp_path, monkeypatch):
    project = scanner.measure_project(make_project(tmp_path))

    def boom(path):
        raise PermissionError("locked")

    monkeypatch.setattr(actions, "remove_path", boom)
    result = actions.delete_project(project, actions.PERMANENT)
    assert not result["ok"] and "locked" in result["error"]


def test_recycle_of_nothing_is_ok_and_failure_is_reported(tmp_path):
    assert actions.recycle([str(tmp_path / "absent")]) == (True, "")


# -- lightening with a fake Metashape -------------------------------------------------

class FakeOrthomosaic(object):
    def __init__(self, files_dir, index):
        self._files, self._index, self.photos_removed = files_dir, index, False

    def removeOrthophotos(self):
        self.photos_removed = True


class FakeChunk(object):
    def __init__(self, label, files_dir, index):
        self.label, self._files, self._index = label, files_dir, index
        self.cameras, self.markers = [1, 2, 3], [1, 2]
        self.point_clouds = ["dense"]
        self.depth_maps_sets = ["depth"]
        self.orthomosaics = [FakeOrthomosaic(files_dir, index)]

    def remove(self, items):
        for item in items:
            if item in self.point_clouds:
                self.point_clouds.remove(item)
            if item in self.depth_maps_sets:
                self.depth_maps_sets.remove(item)


class FakeDocument(object):
    """A project that forgets its dense clouds and deletes its files on save, like Metashape."""

    def __init__(self, path, delete_on_save=True):
        self.path, self.delete_on_save, self.chunks = path, delete_on_save, []
        self.files = os.path.splitext(path)[0] + ".files"
        self.chunks = [FakeChunk("Chunk 1", self.files, 0)]
        self.had_dense = True

    def save(self):
        if self.delete_on_save:
            for chunk in os.listdir(self.files):
                frame = os.path.join(self.files, chunk, "0")
                if os.path.isdir(frame):
                    for part in os.listdir(frame):
                        kind = scanner.part_of(part)
                        if kind == "dense_cloud" and not self.chunks[0].point_clouds:
                            shutil.rmtree(os.path.join(frame, part))
                        elif kind == "depth_maps" and not self.chunks[0].depth_maps_sets:
                            shutil.rmtree(os.path.join(frame, part))
                        elif kind == "orthomosaic" and self.chunks[0].orthomosaics[0].photos_removed:
                            for name in os.listdir(os.path.join(frame, part)):
                                if scanner.ORTHO_IMAGES.match(name):
                                    os.remove(os.path.join(frame, part, name))
        self.saved = True


@pytest.fixture
def fake_metashape(monkeypatch):
    state = {"opened": [], "delete_on_save": True}
    memory = {}

    def fake_open(path, read_only):
        document = memory.get(path)
        if document is None:
            document = FakeDocument(path, state["delete_on_save"])
            memory[path] = document
        state["opened"].append(("ro" if read_only else "rw", path))
        return document

    monkeypatch.setattr(actions, "_open", fake_open)
    return state


def test_lighten_permanent_removes_dense_and_depth_and_verifies(tmp_path, fake_metashape):
    project = scanner.measure_project(make_project(tmp_path, dense=1000, depth=3000, tie=500))
    steps = []
    result = actions.lighten(project, actions.PERMANENT, progress=steps.append)
    assert result["ok"], result
    assert result["freed"] == 4000 and result["in_bin"] == 0
    assert project.removable() == 0 and project.parts["point_cloud"] == 500    # the rest is intact
    assert [mode for mode, _ in fake_metashape["opened"]] == ["ro", "rw", "ro"]
    assert steps[0] == "reading" and steps[-1] == "checking"


def test_lighten_ortho_only_keeps_the_dense_cloud_and_the_mosaic(tmp_path, fake_metashape):
    project = scanner.measure_project(make_project(tmp_path, dense=1000, depth=3000, photos=9000, mosaic=400))
    result = actions.lighten(project, actions.PERMANENT, items=("ortho",))
    assert result["ok"], result
    assert result["freed"] == 9000
    assert project.ortho_photos == 0 and project.parts["orthomosaic"] == 400
    assert project.dense == 1000 and project.depth == 3000                   # not chosen: untouched


def test_lighten_everything_in_one_pass(tmp_path, fake_metashape):
    project = scanner.measure_project(make_project(tmp_path, dense=1000, depth=3000, photos=9000))
    result = actions.lighten(project, actions.PERMANENT, items=scanner.ITEMS)
    assert result["ok"] and result["freed"] == 13000
    assert project.removable(scanner.ITEMS) == 0


def test_lighten_without_any_item_does_nothing(tmp_path, fake_metashape):
    project = scanner.measure_project(make_project(tmp_path, photos=100))
    result = actions.lighten(project, actions.PERMANENT, items=())
    assert not result["ok"] and fake_metashape["opened"] == []


def test_lighten_cleans_orthophotos_metashape_left_behind(tmp_path, fake_metashape):
    fake_metashape["delete_on_save"] = False
    project = scanner.measure_project(make_project(tmp_path, dense=0, depth=0, photos=5000, mosaic=100))
    assert [os.path.basename(p) for p in actions.removable_paths(project, ("ortho",))] == ["images0.zip"]
    result = actions.lighten(project, actions.PERMANENT, items=("ortho",))
    assert result["ok"] and result["freed"] == 5000 and actions.removable_paths(project, ("ortho",)) == []


def test_strip_chunk_only_touches_the_chosen_items():
    chunk = FakeChunk("C", "x", 0)
    assert actions.strip_chunk(chunk, ("ortho",)) == ["ortho"]
    assert chunk.point_clouds == ["dense"] and chunk.depth_maps_sets == ["depth"]
    assert chunk.orthomosaics[0].photos_removed
    assert actions.strip_chunk(chunk) == ["dense", "depth"]


def test_lighten_cleans_leftover_folders_when_metashape_keeps_them(tmp_path, fake_metashape):
    fake_metashape["delete_on_save"] = False
    project = scanner.measure_project(make_project(tmp_path, dense=1000, depth=3000))
    result = actions.lighten(project, actions.PERMANENT)
    assert result["ok"] and result["freed"] == 4000
    assert actions.removable_paths(project) == []


def test_lighten_has_no_recycle_mode_and_touches_nothing_then(tmp_path, fake_metashape):
    project = scanner.measure_project(make_project(tmp_path, dense=1000, depth=3000))
    result = actions.lighten(project, actions.RECYCLE)
    assert not result["ok"] and "no recycle bin" in result["error"]
    assert fake_metashape["opened"] == [] and project.removable() == 4000


def test_lighten_reports_an_api_failure_and_leaves_the_files(tmp_path, monkeypatch):
    project = scanner.measure_project(make_project(tmp_path, dense=1000, depth=3000))

    def broken(path, read_only):
        raise OSError("Can't open file dense_cloud.zip")

    monkeypatch.setattr(actions, "_open", broken)
    result = actions.lighten(project, actions.PERMANENT)
    assert not result["ok"] and "dense_cloud.zip" in result["error"]
    assert len(actions.removable_paths(project)) == 2


def test_lighten_refuses_open_projects_and_orphans(tmp_path, fake_metashape):
    held = scanner.measure_project(make_project(tmp_path, "held"))
    held.lock = scanner.OPEN
    assert not actions.lighten(held, actions.PERMANENT)["ok"] and fake_metashape["opened"] == []
    psx = make_project(tmp_path, "o", with_psx=False)
    orphan = scanner.measure_orphan(os.path.splitext(psx)[0] + ".files")
    assert "no .psx" in actions.lighten(orphan, actions.PERMANENT)["error"]


def test_lighten_removes_an_old_lock_before_opening_the_project(tmp_path, fake_metashape):
    project = scanner.measure_project(make_project(tmp_path, "stale", lock=True))
    lock = os.path.join(project.files_dir, "lock")
    assert project.lock == scanner.STALE and os.path.exists(lock)
    steps = []
    result = actions.lighten(project, actions.PERMANENT, progress=steps.append)
    assert result["ok"], result
    assert not os.path.exists(lock) and project.lock == scanner.FREE
    assert steps[0] == "stale lock" and fake_metashape["opened"][0][0] == "ro"


def test_lighten_explains_a_read_only_failure_with_the_lock_owner(tmp_path, monkeypatch):
    project = scanner.measure_project(make_project(tmp_path, "locked", lock=True))
    project.lock, project.lock_owner = scanner.FREE, "OTHER-PC/user"        # skip the lock removal

    def read_only(path, read_only):
        raise OSError("Document.save(): editing is disabled in read-only mode")

    monkeypatch.setattr(actions, "_open", read_only)
    error = actions.lighten(project, actions.PERMANENT)["error"]
    assert "OTHER-PC/user" in error and "editing is disabled" not in error


def test_lock_owner_is_the_first_line_of_the_lock_file(tmp_path):
    psx = make_project(tmp_path, "owned", lock=True)
    files = os.path.splitext(psx)[0] + ".files"
    with open(os.path.join(files, "lock"), "wb") as handle:
        handle.write(b"DESKTOP-ABC/user\nmore")
    assert scanner.lock_owner(files) == "DESKTOP-ABC/user"
    assert scanner.measure_project(psx).lock_owner == "DESKTOP-ABC/user"
    assert scanner.lock_owner(str(tmp_path)) == ""


def test_delete_can_bypass_an_open_lock_but_never_the_project_open_here(tmp_path, monkeypatch):
    project = scanner.measure_project(make_project(tmp_path, "busy"))
    project.lock = scanner.OPEN
    assert actions.refusal(project) and actions.delete_refusal(project) == ""
    assert not actions.delete_project(project, actions.PERMANENT)["ok"] and os.path.exists(project.path)
    monkeypatch.setattr(actions, "current_document_path", lambda: project.path)
    assert "this Metashape" in actions.delete_refusal(project)
    result = actions.delete_project(project, actions.PERMANENT, force=True)
    assert not result["ok"] and os.path.exists(project.path)                 # open here: never
    monkeypatch.setattr(actions, "current_document_path", lambda: "")
    assert actions.delete_project(project, actions.PERMANENT, force=True)["ok"]
    assert not os.path.exists(project.path)


def test_check_snapshots():
    before = [{"label": "C", "cameras": 3, "markers": 2, "dense": 1, "depth": 1}]
    assert actions.check_snapshots(before, [{"label": "C", "cameras": 3, "markers": 2, "dense": 0, "depth": 0}]) is None
    assert "markers" in actions.check_snapshots(before, [{"label": "C", "cameras": 3, "markers": 1, "dense": 0, "depth": 0}])
    assert "remain" in actions.check_snapshots(before, [{"label": "C", "cameras": 3, "markers": 2, "dense": 1, "depth": 0}])
    assert "chunks" in actions.check_snapshots(before, [])
    still = [{"label": "C", "cameras": 3, "markers": 2, "dense": 1, "depth": 1}]
    assert actions.check_snapshots(before, still, ("ortho",)) is None          # dense and depth not requested: not a problem


def test_log_is_written_as_csv(tmp_path, local_appdata):
    project = scanner.measure_project(make_project(tmp_path))
    actions.log_action("lighten", actions.RECYCLE, project, True, "", 123)
    actions.log_action("delete", actions.PERMANENT, project, False, "refuse", 0)
    text = open(actions.log_path(), encoding="utf-8-sig").read().splitlines()
    assert text[0].startswith("date;action") and len(text) == 3 and "refuse" in text[2]


def test_the_scanner_handles_a_missing_root(tmp_path):
    assert scanner.scan(str(tmp_path / "absent"))["projects"] == []
