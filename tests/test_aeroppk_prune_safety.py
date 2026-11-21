"""The download cache is the only thing Aero-PPK ever prunes: never a folder the user (or a caller) points at."""
import os

import pytest

from aerotools.tools.aeroppk.stations import provider


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(provider, "cache_dir", lambda: str(tmp_path / "plugin" / "AeroPPK"))
    monkeypatch.setattr(provider, "_pruned", set())
    return tmp_path / "plugin" / "AeroPPK" / "rinex"


def fill(folder, count=3, size=1000):
    folder.mkdir(parents=True, exist_ok=True)
    for k in range(count):
        path = folder / ("f%d.gz" % k)
        path.write_bytes(b"x" * size)
        os.utime(str(path), (1000.0 + k, 1000.0 + k))


def test_a_users_folder_named_rinex_is_never_pruned(tmp_path, cache):
    mine = tmp_path / "mine" / "rinex"
    fill(mine / "a" / "b")
    (mine / provider.CACHE_MARKER).write_text("a marker copied by hand")
    provider._prune_once(str(mine / "a" / "b"))
    with pytest.raises(ValueError):
        provider.prune_cache(str(mine), max_bytes=0)
    assert len(list((mine / "a" / "b").iterdir())) == 3


def test_fetch_into_a_foreign_folder_does_not_prune_it(tmp_path, cache, monkeypatch):
    mine = tmp_path / "mine" / "rinex"
    fill(mine / "net" / "STA")
    called = []
    monkeypatch.setattr(provider, "prune_cache", lambda *a, **k: called.append(a))
    provider._prune_once(str(mine / "net" / "STA"))
    assert called == []


def test_the_own_cache_without_marker_is_refused(cache):
    fill(cache / "igs" / "ALIC")
    with pytest.raises(ValueError):
        provider.prune_cache(max_bytes=0)
    assert len(list((cache / "igs" / "ALIC").iterdir())) == 3


def test_the_own_cache_with_marker_is_pruned(cache):
    fill(cache / "igs" / "ALIC")
    assert provider.ensure_cache_marker()
    kept = provider.prune_cache(max_bytes=1500, now=1e9)
    assert kept == 1000 and (cache / provider.CACHE_MARKER).exists()


def test_prune_once_writes_the_marker_and_prunes(cache, monkeypatch):
    monkeypatch.setattr(provider, "CACHE_CAP_BYTES", 1500)
    fill(cache / "igs" / "ALIC")
    provider._prune_once(str(cache / "igs" / "ALIC"))
    assert (cache / provider.CACHE_MARKER).exists() and len(list((cache / "igs" / "ALIC").iterdir())) == 1


@pytest.mark.parametrize("which", ["home", "drive", "temp", "parent", "plugin"])
def test_roots_that_are_not_the_cache_are_refused(cache, tmp_path, which):
    provider.ensure_cache_marker()
    root = {"home": os.path.expanduser("~"), "drive": os.path.splitdrive(os.getcwd())[0] + os.sep,
            "temp": os.environ.get("TEMP") or str(tmp_path), "parent": str(cache.parent), "plugin": str(cache / "igs")}[which]
    with pytest.raises(ValueError):
        provider.prune_cache(root, max_bytes=0)


def test_a_junction_or_symlink_inside_the_cache_is_not_followed(cache, tmp_path):
    outside = tmp_path / "outside"
    fill(outside)
    provider.ensure_cache_marker()
    fill(cache / "igs")
    link = cache / "igs" / "out"
    try:
        os.symlink(str(outside), str(link), target_is_directory=True)
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("no symlink right")
    provider.prune_cache(max_bytes=0, now=1e9)
    assert len(list(outside.iterdir())) == 3
