"""Aero-Space: the lock is read again before a destructive action, an unreadable lock is never 'free', lighten keeps a safety copy."""

import datetime
import errno
import os

import pytest

from aerotools.tools.fastspace import actions, scanner
from test_fastspace import fake_metashape, make_project   # noqa: F401  (fixture)


def project_of(tmp_path, name="p", **kw):
    return scanner.measure_project(make_project(tmp_path, name, **kw))


def raising(error):
    def fail(*args, **kwargs):
        raise error
    return fail


# -- C2: the state noted by the scan can be hours old -----------------------------------------------------------

def test_a_project_opened_since_the_scan_is_not_deleted(tmp_path, monkeypatch):
    monkeypatch.setattr(scanner, "lock_state", lambda files_dir: scanner.OPEN)       # a colleague opens it at 11:00
    for force in (False, True):                                  # a blanket "force" never covers a project that was not confirmed
        project = scanner.Project(make_project(tmp_path, "p%s" % force))
        project.lock = scanner.FREE                              # the scan saw it free
        result = actions.delete_project(project, actions.PERMANENT, force=force)
        assert not result["ok"] and "since the scan" in result["error"]
        assert os.path.exists(project.path) and os.path.isdir(project.files_dir)
        assert project.in_use                                    # and the table now knows


def test_a_project_the_user_confirmed_as_open_is_deleted_if_it_still_is(tmp_path, monkeypatch):
    project = project_of(tmp_path, lock=True)
    project.lock = scanner.OPEN
    monkeypatch.setattr(scanner, "lock_state", lambda files_dir: scanner.OPEN)
    assert not actions.delete_project(project, actions.PERMANENT)["ok"]
    assert actions.delete_project(project, actions.PERMANENT, force=True)["ok"]
    assert not os.path.exists(project.path)


def test_lighten_reads_the_lock_again_too(tmp_path, monkeypatch, fake_metashape):
    project = project_of(tmp_path)
    monkeypatch.setattr(scanner, "lock_state", lambda files_dir: scanner.OPEN)
    result = actions.lighten(project, actions.PERMANENT)
    assert not result["ok"] and "since the scan" in result["error"]
    assert fake_metashape["opened"] == [] and len(actions.removable_paths(project)) == 2


@pytest.mark.skipif(os.name != "nt", reason="Windows exclusive lock")
def test_a_real_lock_taken_after_the_scan_stops_the_deletion(tmp_path):
    import ctypes
    psx = make_project(tmp_path, "held")
    project = scanner.measure_project(psx)
    assert project.lock == scanner.FREE
    lock_path = os.path.join(project.files_dir, "lock")
    with open(lock_path, "wb") as handle:
        handle.write(b"OTHER-PC/user")
    ctypes.windll.kernel32.CreateFileW.restype = ctypes.c_void_p
    handle = ctypes.windll.kernel32.CreateFileW(lock_path, 0xC0000000, 0, None, 3, 0, None)       # open without sharing: Metashape's way
    try:
        result = actions.delete_project(project, actions.PERMANENT, force=True)
        assert not result["ok"] and os.path.exists(psx)
    finally:
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))


# -- I-space-1: an I/O error is not 'free' --------------------------------------------------------------------

def test_lock_state_says_free_only_when_there_is_no_lock(tmp_path):
    folder = tmp_path / "p.files"
    assert scanner.lock_state(str(folder)) == scanner.FREE                    # no folder: nothing to protect
    folder.mkdir()
    assert scanner.lock_state(str(folder)) == scanner.FREE
    (folder / "lock").write_bytes(b"x")
    assert scanner.lock_state(str(folder)) == scanner.STALE


@pytest.mark.parametrize("failing", ["stat", "open"])
def test_an_unexpected_io_error_on_the_lock_is_unknown_never_free(tmp_path, monkeypatch, failing):
    folder = tmp_path / "p.files"
    folder.mkdir()
    (folder / "lock").write_bytes(b"x")
    error = OSError(errno.EIO, "network error")
    if failing == "stat":
        real_stat = os.stat

        def stat(path, *args, **kwargs):
            if str(path).endswith("lock"):
                raise error
            return real_stat(path, *args, **kwargs)

        monkeypatch.setattr(scanner.os, "stat", stat)
    else:
        real_open = open

        def broken(path, *args, **kwargs):
            if str(path).endswith("lock"):
                raise error
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr("builtins.open", broken)
    assert scanner.lock_state(str(folder)) == scanner.UNKNOWN


def test_an_unknown_lock_counts_as_open_and_blocks_both_actions(tmp_path, monkeypatch, fake_metashape):
    project = project_of(tmp_path)
    monkeypatch.setattr(scanner, "lock_state", lambda files_dir: scanner.UNKNOWN)
    assert not actions.delete_project(project, actions.PERMANENT)["ok"] and "cannot be read" in actions.refusal(project)
    project = project_of(tmp_path, "q")
    assert not actions.lighten(project, actions.PERMANENT)["ok"]
    assert os.path.exists(project.path) and fake_metashape["opened"] == []


# -- I-space-2: a safety copy before the .psx is opened for writing -------------------------------------------

def test_lighten_copies_the_project_description_before_opening_it_for_writing(tmp_path, monkeypatch):
    project = project_of(tmp_path, dense=1000, depth=3000, chunks=2)
    seen = []

    from test_fastspace import FakeChunk

    class Doc(object):
        chunks = [FakeChunk("C", "x", 0)]

        def save(self):
            pass

    def fake_open(path, read_only):
        if not read_only:
            seen.append(sorted(n for n in os.listdir(os.path.dirname(path)) if actions.BACKUP_MARK in n))
        return Doc()

    monkeypatch.setattr(actions, "_open", fake_open)
    monkeypatch.setattr(actions, "check_snapshots", lambda *args, **kwargs: None)
    monkeypatch.setattr(actions, "snapshot", lambda document: [])
    result = actions.lighten(project, actions.PERMANENT)
    assert result["ok"], result
    assert len(seen[0]) == 1                                    # the copy existed when the project was opened for writing
    root = result["backup"]
    assert os.path.dirname(root) == os.path.dirname(project.path) and actions.BACKUP_MARK in os.path.basename(root)
    found = sorted(os.path.relpath(os.path.join(base, name), root).replace(os.sep, "/")
                   for base, _dirs, names in os.walk(root) for name in names)
    assert found == ["RESTORE.txt", "description_files/0/0/frame.zip", "description_files/1/0/frame.zip", "description_files/project.zip",
                     "p.psx.bak"]
    with open(os.path.join(root, "p.psx.bak")) as handle:
        assert handle.read() == "<document/>"
    assert not [name for name in found if "dense_cloud" in name or "depth_maps" in name]       # the heavy folders are not copied


def test_the_copy_is_not_taken_for_a_project_by_the_scan(tmp_path, fake_metashape):
    project = project_of(tmp_path, dense=1000, depth=3000)
    assert actions.lighten(project, actions.PERMANENT)["ok"]
    found = scanner.scan(str(tmp_path))["projects"]
    assert [p.name for p in found] == ["p.psx"]


def test_a_failed_copy_stops_lighten_before_anything_is_written(tmp_path, monkeypatch, fake_metashape):
    project = project_of(tmp_path, dense=1000, depth=3000)
    monkeypatch.setattr(actions.shutil, "copy2", raising(OSError(errno.ENOSPC, "disk full")))
    result = actions.lighten(project, actions.PERMANENT)
    assert not result["ok"] and "safety copy" in result["error"] and "disk full" in result["error"]
    assert [mode for mode, _path in fake_metashape["opened"]] == ["ro"]           # never opened for writing
    assert len(actions.removable_paths(project)) == 2                            # nothing removed
    assert not [n for n in os.listdir(os.path.dirname(project.path)) if actions.BACKUP_MARK in n]   # no half copy left


def test_two_lightenings_make_two_copies(tmp_path):
    project = project_of(tmp_path)
    first = actions.safety_copy(project, now=datetime.datetime(2026, 11, 8, 10, 0, 0))
    second = actions.safety_copy(project, now=datetime.datetime(2026, 11, 8, 10, 0, 1))
    assert first != second and os.path.isdir(first) and os.path.isdir(second)
