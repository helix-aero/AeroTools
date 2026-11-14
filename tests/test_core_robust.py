"""Core robustness: modules a tool needs, one menu layout at start, settings cache, projtool without numpy, uninstall details, journal."""

import builtins
import importlib.util
import json
import os
import subprocess
import sys

import pytest

from aerotools import config, journal, uninstall

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --------------------------------------------------------------------------
# Python packages a tool needs
# --------------------------------------------------------------------------

def made_up_packs(monkeypatch, **modules):
    """Packs with `requires_modules`: {id: (requires, [modules])}."""
    packs = [{"id": pid, "paths": ["aerotools/tools/{}/".format(pid)], "marker": "aerotools/tools/{}/tool.py".format(pid),
              "requires": needs, "requires_modules": mods} for pid, (needs, mods) in modules.items()]
    monkeypatch.setattr(config, "PACKS", list(config.PACKS) + packs)


def test_missing_modules_lists_only_what_is_absent(monkeypatch):
    made_up_packs(monkeypatch, madeup=([], ["json", "no_such_package_xyz"]))
    assert config.missing_modules("madeup") == ["no_such_package_xyz"]
    assert config.missing_modules("no-such-pack") == []


def test_the_modules_of_the_required_packs_count_too(monkeypatch):
    made_up_packs(monkeypatch, base=([], ["no_such_package_xyz"]), top=(["base"], ["json"]))
    assert config.missing_modules("top") == ["no_such_package_xyz"]


def test_a_module_blocked_in_sys_modules_is_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "numpy", None)                      # how a test (or an import hook) says "not available"
    assert config.module_available("numpy") is False
    assert config.module_available("json") is True


def test_every_declaration_is_a_list_of_names():
    for entry in config.PACKS:
        declared = entry.get("requires_modules", [])
        assert isinstance(declared, list) and all(isinstance(name, str) and name.isidentifier() for name in declared), entry["id"]


@pytest.fixture
def boot(ms, clean_bootstrap_flags):
    from aerotools import bootstrap
    yield bootstrap
    if hasattr(builtins, "_AEROTOOLS_MENU_RETRIES"):
        delattr(builtins, "_AEROTOOLS_MENU_RETRIES")


def test_a_tool_that_needs_a_missing_package_says_so_and_imports_nothing(boot, ms, monkeypatch):
    import importlib
    cfg = sys.modules["aerotools.config"]
    monkeypatch.setattr(cfg, "missing_modules", lambda tool_id: ["numpy"])

    def forbidden(name, *args, **kwargs):
        raise AssertionError("the tool must not be imported: " + name)

    monkeypatch.setattr(importlib, "import_module", forbidden)
    assert boot.launch("fastgcp") is False
    text = ms.app.messages[0]
    assert "numpy" in text and "Aero-GCP" in text and "ModuleNotFoundError" not in text and "Traceback" not in text


def test_a_package_missing_when_the_tool_loads_gets_the_same_short_message(boot, ms, monkeypatch):
    import importlib

    def fails(name, *args, **kwargs):
        raise ModuleNotFoundError("No module named 'scipy'", name="scipy")

    monkeypatch.setattr(importlib, "import_module", fails)
    assert boot.launch("fastgcp") is False
    assert "scipy" in ms.app.messages[0] and "ModuleNotFoundError" not in ms.app.messages[0]


def test_a_package_missing_when_the_tool_starts_gets_the_same_short_message(boot, ms, monkeypatch):
    import importlib

    class Tool(object):
        @staticmethod
        def install():
            import no_such_package_xyz  # noqa: F401

    monkeypatch.setattr(importlib, "import_module", lambda name, *a, **k: Tool)
    assert boot.launch("fastgcp") is False
    assert "no_such_package_xyz" in ms.app.messages[0] and "ModuleNotFoundError" not in ms.app.messages[0]


def test_a_bug_of_ours_stays_visible(boot, ms, monkeypatch):
    """An ImportError about our own code is not a missing package: the raw error is shown (and journalled)."""
    import importlib

    def fails(name, *args, **kwargs):
        raise ImportError("cannot import name 'x' from 'aerotools.controls'", name="aerotools.controls")

    monkeypatch.setattr(importlib, "import_module", fails)
    assert boot.launch("fastgcp") is False
    assert "could not load" in ms.app.messages[0] and "cannot import name" in ms.app.messages[0]


# --------------------------------------------------------------------------
# The menu is laid out once at start
# --------------------------------------------------------------------------

def count_layouts(monkeypatch):
    msapi = sys.modules["aerotools.msapi"]
    calls = {"arrange": 0, "logo": 0}

    def arrange(title, groups):
        calls["arrange"] += 1
        return True

    def logo(*args, **kwargs):
        calls["logo"] += 1
        return True

    monkeypatch.setattr(msapi, "arrange_menu", arrange)
    monkeypatch.setattr(msapi, "add_logo_button", logo)
    return calls


def test_the_menu_is_arranged_once_however_many_tools_are_installed(boot, ms, monkeypatch):
    import aerotools.msapi  # noqa: F401
    calls = count_layouts(monkeypatch)
    assert boot.install_menu() is True
    assert len(config.TOOLS) >= 3
    assert calls == {"arrange": 1, "logo": 1}


def test_a_tool_installed_later_still_arranges_the_menu(boot, ms, monkeypatch):
    import aerotools.msapi  # noqa: F401
    cfg = sys.modules["aerotools.config"]
    updater = sys.modules["aerotools.updater"] if "aerotools.updater" in sys.modules else __import__("aerotools.updater").updater
    calls = count_layouts(monkeypatch)
    monkeypatch.setattr(updater, "tool_installed", lambda tool_id: tool_id != cfg.TOOLS[-1]["id"])
    boot.install_menu()
    before = dict(calls)
    monkeypatch.setattr(updater, "tool_installed", lambda tool_id: True)
    assert boot.add_tool_menu(cfg.TOOLS[-1]["id"]) is True
    assert calls["arrange"] == before["arrange"] + 1


def test_a_menu_that_could_not_be_arranged_is_tried_again_a_few_times(boot, ms, monkeypatch):
    import aerotools.msapi  # noqa: F401
    msapi = sys.modules["aerotools.msapi"]
    monkeypatch.setattr(msapi, "arrange_menu", lambda title, groups: False)         # no main window yet
    monkeypatch.setattr(msapi, "add_logo_button", lambda *a, **k: False)
    scheduled = []
    qt = sys.modules["aerotools.qt"]
    monkeypatch.setattr(qt.QtCore, "QTimer", type("T", (), {"singleShot": staticmethod(lambda ms_, fn: scheduled.append((ms_, fn)))}), raising=False)
    boot.arrange_menu()
    assert scheduled and scheduled[0][0] == boot.ARRANGE_RETRY_MS and scheduled[0][1] == boot._retry_now
    for _ in range(boot.ARRANGE_RETRIES + 3):
        boot.arrange_menu()
    assert len(scheduled) == boot.ARRANGE_RETRIES                                   # bounded


def test_a_menu_that_was_arranged_is_not_retried(boot, ms, monkeypatch):
    import aerotools.msapi  # noqa: F401
    count_layouts(monkeypatch)
    scheduled = []
    qt = sys.modules["aerotools.qt"]
    monkeypatch.setattr(qt.QtCore, "QTimer", type("T", (), {"singleShot": staticmethod(lambda *a: scheduled.append(a))}), raising=False)
    boot.arrange_menu()
    assert scheduled == []


def test_an_update_cut_short_is_undone_when_the_menu_starts(boot, ms, monkeypatch):
    updater = __import__("aerotools.updater").updater
    seen = []
    monkeypatch.setattr(updater, "recover", lambda plugin: seen.append(plugin["id"]) or 0)
    boot.install_menu()
    assert seen == [plugin["id"] for plugin in updater.plugins()]


def test_a_failing_recovery_never_stops_the_menu(boot, ms, monkeypatch):
    updater = __import__("aerotools.updater").updater

    def broken(plugin):
        raise RuntimeError("journal unreadable")

    monkeypatch.setattr(updater, "recover", broken)
    assert boot.install_menu() is True


def test_release_files_closes_the_proj_database(boot, monkeypatch):
    from aerotools import projtool

    class Connection(object):
        closed = False

        def close(self):
            self.closed = True

    connection = Connection()
    monkeypatch.setitem(projtool._db_cache, "somewhere/proj.db", connection)
    boot.release_files()
    assert connection.closed and "somewhere/proj.db" not in projtool._db_cache


# --------------------------------------------------------------------------
# projtool without numpy
# --------------------------------------------------------------------------

def load_projtool_without_numpy(monkeypatch):
    monkeypatch.setitem(sys.modules, "numpy", None)
    spec = importlib.util.spec_from_file_location("projtool_nonumpy", os.path.join(ROOT, "aerotools", "projtool.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_projtool_loads_on_a_metashape_without_numpy(monkeypatch):
    module = load_projtool_without_numpy(monkeypatch)                       # raised ModuleNotFoundError at the import before
    assert module.datum_family("ETRS89") == "global"


def test_a_conversion_without_numpy_raises_a_readable_error_not_an_import_error(monkeypatch):
    module = load_projtool_without_numpy(monkeypatch)
    with pytest.raises(module.ProjError, match="numpy"):
        module.Proj(runner=lambda *a: "").lonlat(2154, [1.0], [2.0])
    with pytest.raises(module.ProjError, match="numpy"):
        module.Proj(runner=lambda *a: "").vertical_shift("grid.tif", [1.0], [2.0])


def test_the_conversions_still_work_with_numpy():
    np = pytest.importorskip("numpy")
    from aerotools import projtool

    def runner(exe, args, text):
        return "".join("0 0 -48.250 0\n" for _ in text.splitlines())

    shift = projtool.Proj(runner=runner).vertical_shift("fr_ign_RAF20.tif", [8.7], [41.9])
    assert np.allclose(shift, [-48.25])


@pytest.mark.parametrize("name", ["my grid.tif", "sub/grid.tif", "sub\\grid.tif", "a;b.tif", "a\tb.tif"])
def test_a_grid_name_that_could_split_the_proj_string_is_refused(name):
    pytest.importorskip("numpy")
    from aerotools import projtool
    with pytest.raises(projtool.ProjError, match="bare name"):
        projtool.Proj(runner=lambda *a: "").vertical_shift(name, [1.0], [2.0])


def test_close_db_closes_and_forgets_every_connection(monkeypatch):
    from aerotools import projtool
    closed = []

    class Connection(object):
        def __init__(self, name):
            self.name = name

        def close(self):
            closed.append(self.name)

    monkeypatch.setattr(projtool, "_db_cache", {"a": Connection("a"), "b": Connection("b")})
    projtool.close_db()
    assert sorted(closed) == ["a", "b"] and projtool._db_cache == {}


# --------------------------------------------------------------------------
# Settings: one parse per change, a migration that never loses a write
# --------------------------------------------------------------------------

def count_reads(monkeypatch):
    reads = []
    real = config._read_settings

    def counting(tries=1):
        reads.append(tries)
        return real(tries)

    monkeypatch.setattr(config, "_read_settings", counting)
    return reads


def age(path, seconds=100):
    stamp = os.path.getmtime(path) - seconds
    os.utime(path, (stamp, stamp))


def test_settings_are_parsed_once_until_the_file_changes(local_appdata, monkeypatch):
    config.put("a", "x", 1)
    age(config.settings_path())
    reads = count_reads(monkeypatch)
    for _ in range(20):
        assert config.get("a", "x") == 1
    assert len(reads) == 1
    config.put("a", "x", 2)                                                  # a put reads once more (under its lock) and writes
    age(config.settings_path())
    assert config.get("a", "x") == 2 and config.get("a", "x") == 2


def test_a_change_made_by_someone_else_is_seen(local_appdata):
    config.put("a", "x", 1)
    age(config.settings_path())
    assert config.get("a", "x") == 1
    with open(config.settings_path(), "w", encoding="utf-8") as handle:
        json.dump({"a": {"x": 99}}, handle)
    assert config.get("a", "x") == 99


def test_two_quick_writes_of_the_same_size_are_both_seen(local_appdata):
    """The file is written again within the same clock tick, with the same size: the cache must not serve the first content."""
    path = config.settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    for value in (1, 2, 3, 4):
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"a": {"x": value}}, handle)
        assert config.get("a", "x") == value


def test_the_cache_hands_out_copies(local_appdata):
    config.put("a", "x", [1, 2])
    age(config.settings_path())
    first = config.section("a")
    first["x"].append(3)
    first["y"] = 1
    assert config.section("a") == {"x": [1, 2]}


def test_a_missing_or_unreadable_file_is_never_cached_as_empty(local_appdata):
    assert config.get("a", "x") is None
    config.put("a", "x", 5)
    assert config.get("a", "x") == 5


def test_the_legacy_migration_does_not_overwrite_a_write_that_came_in_between(local_appdata, monkeypatch):
    path = config.settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"fastexport": {"a": 1}, "late": {"kept": True}}, handle)         # "late" was put by someone after our first read
    real = config._read_settings
    calls = []

    def stale_first(tries=1):
        calls.append(tries)
        if len(calls) == 1:
            return {"fastexport": {"a": 1}}, "ok"                                    # what we read before the other writer's put
        return real(tries)

    monkeypatch.setattr(config, "_read_settings", stale_first)
    data = config.load_settings()
    assert data["aeroexports"] == {"a": 1} and data["late"] == {"kept": True}
    with open(path, encoding="utf-8") as handle:
        on_disk = json.load(handle)
    assert on_disk["late"] == {"kept": True} and on_disk["aeroexports"] == {"a": 1} and on_disk["fastexport"] == {"a": 1}


# --------------------------------------------------------------------------
# Uninstall
# --------------------------------------------------------------------------

@pytest.fixture
def home(tmp_path, monkeypatch):
    local, profile = tmp_path / "local", tmp_path / "profile"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("USERPROFILE", str(profile))
    monkeypatch.setenv("HOME", str(profile))
    scripts = local / "Agisoft" / "Metashape Pro" / "scripts"
    scripts.mkdir(parents=True)
    return tmp_path


def write_launcher(home, root_literal):
    scripts = home / "local" / "Agisoft" / "Metashape Pro" / "scripts"
    template = open(os.path.join(ROOT, "install", "aerotools_launcher.py.tpl"), encoding="utf-8").read()
    (scripts / "aerotools_launcher.py").write_text(template.replace('r"@@AEROTOOLS_ROOT@@"', root_literal), encoding="utf-8")


def test_the_folder_the_launcher_points_to_is_read_from_it(home):
    write_launcher(home, 'r"D:\\Tools\\AeroToolsApp"')
    assert uninstall.launcher_root() == "D:\\Tools\\AeroToolsApp"
    write_launcher(home, 'r"D:" + "\\\\"')
    assert uninstall.launcher_root() == "D:\\"


def test_no_launcher_or_a_foreign_one_gives_no_folder(home):
    assert uninstall.launcher_root() == ""
    (home / "local" / "Agisoft" / "Metashape Pro" / "scripts" / "aerotools_launcher.py").write_text("print('mine')")
    assert uninstall.launcher_root() == ""


def test_the_code_folder_the_launcher_points_to_is_removed_when_it_is_the_installers(home):
    custom = home / "elsewhere" / "AeroToolsApp"
    (custom / "aerotools").mkdir(parents=True)
    (custom / "aerotools" / "bootstrap.py").write_text("# core")
    write_launcher(home, 'r"{}"'.format(custom))
    assert os.path.normcase(str(custom)) in [os.path.normcase(p) for p in uninstall.code_folders()]
    result = uninstall.remove(["code"])
    assert result["errors"] == {} and not custom.exists()


def test_a_development_copy_the_launcher_points_to_is_never_listed(home):
    dev = home / "work" / "AeroTools_dev"
    (dev / "aerotools").mkdir(parents=True)
    write_launcher(home, 'r"{}"'.format(dev))
    assert [os.path.normcase(p) for p in uninstall.code_folders()] == [os.path.normcase(config.app_dir())]
    uninstall.remove(["code"])
    assert dev.exists()


def test_read_only_files_do_not_stop_the_removal(home):
    app = home / "local" / "HELIX" / "AeroToolsApp" / "aerotools"
    app.mkdir(parents=True)
    locked = app / "locked.py"
    locked.write_text("x")
    os.chmod(str(locked), 0o444)
    result = uninstall.remove(["code"])
    assert result["errors"] == {} and not (home / "local" / "HELIX" / "AeroToolsApp").exists()


@pytest.mark.skipif(os.name != "nt", reason="junctions are a Windows thing")
def test_a_junction_inside_the_code_folder_is_unlinked_not_followed(home):
    app = home / "local" / "HELIX" / "AeroToolsApp"
    app.mkdir(parents=True)
    precious = home / "precious"
    precious.mkdir()
    (precious / "keep.txt").write_text("keep me")
    done = subprocess.run(["cmd", "/c", "mklink", "/J", str(app / "link"), str(precious)], capture_output=True, text=True)
    if done.returncode != 0:
        pytest.skip("cannot create a junction here: " + done.stderr)
    result = uninstall.remove(["code"])
    assert result["errors"] == {} and not app.exists()
    assert (precious / "keep.txt").read_text() == "keep me"


# --------------------------------------------------------------------------
# Journal
# --------------------------------------------------------------------------

@pytest.fixture
def log(tmp_path, monkeypatch):
    path = tmp_path / "logs" / "aerotools.log"
    monkeypatch.setattr(journal, "log_path", lambda: str(path))
    monkeypatch.setattr(journal, "_file", {"path": None, "size": 0})
    return path


def test_lines_are_appended_in_order_and_the_folder_is_created(log):
    for number in range(3):
        journal.write("line {}".format(number))
    text = log.read_text(encoding="utf-8").splitlines()
    assert [row.split("] ", 1)[1] for row in text] == ["line 0", "line 1", "line 2"]


def test_the_size_is_followed_without_asking_the_disk_each_time(log, monkeypatch):
    journal.write("first")
    asked = []
    real = os.path.getsize
    monkeypatch.setattr(journal.os.path, "getsize", lambda p: asked.append(p) or real(p))
    monkeypatch.setattr(journal.os, "makedirs", lambda *a, **k: asked.append("makedirs"))
    for number in range(50):
        journal.write("more {}".format(number))
    assert asked == []


def test_the_old_file_is_kept_when_the_log_gets_big(log, monkeypatch):
    monkeypatch.setattr(journal, "LOG_MAX_BYTES", 200)
    for number in range(30):
        journal.write("a line of some length {}".format(number))
    assert os.path.isfile(str(log) + ".old") and log.stat().st_size < 400


def test_the_log_comes_back_after_its_folder_was_deleted(log):
    journal.write("before")
    for name in os.listdir(str(log.parent)):
        os.remove(str(log.parent / name))
    os.rmdir(str(log.parent))                                             # an uninstall (or a cleanup) removed the folder
    journal.write("after")
    assert "after" in log.read_text(encoding="utf-8")


def test_a_metashape_without_a_console_does_not_stop_the_journal(log, monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    line = journal.write("no console")
    assert line.endswith("no console") and "no console" in log.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# AI proposals survive a reload of the code and are keyed by marker key
# --------------------------------------------------------------------------

@pytest.fixture
def proposals_world(ms):
    import types
    from aerotools import msapi
    msapi.Metashape = ms
    msapi._PROPOSALS.clear()
    vec, cam_class = ms.Vector, ms.Camera
    cams = [cam_class(1, vec(0, 0, -10)), cam_class(2, vec(0, 0, -20))]
    for cam in cams:
        cam.sensor.calibration = types.SimpleNamespace(f=1000.0)
    pinned = ms.Marker.Projection(vec(2000, 1500, 0), True)
    blue = ms.Marker.Projection(vec(1990, 1490, 0), False)
    marker = ms.Marker("GCP1", position=vec(0, 0, 0), projections={cams[0]: pinned, cams[1]: blue})
    marker.key = 5
    ms.app.document.chunk = ms.Chunk(cams, [marker])
    yield msapi, marker, cams, ms
    msapi._PROPOSALS.clear()


def test_a_pending_proposal_is_still_one_after_the_code_is_reloaded(proposals_world):
    msapi, marker, cams, ms = proposals_world
    msapi.mark_proposal(marker, cams[1])
    assert msapi.projection_count(marker) == 1
    before = msapi._PROPOSALS
    importlib.reload(msapi)                                              # what Reload AeroTools and every update do
    msapi.Metashape = ms
    assert msapi._PROPOSALS is before and len(msapi._PROPOSALS) == 1
    assert msapi.is_proposal(marker, cams[1]) is True
    assert msapi.projection_count(marker) == 1                           # not counted as placed


def test_a_renamed_marker_keeps_its_proposals(proposals_world):
    msapi, marker, cams, _ms = proposals_world
    msapi.mark_proposal(marker, cams[1])
    marker.label = "renamed by the operator"
    assert msapi.is_proposal(marker, cams[1]) is True


def test_two_markers_with_the_same_label_do_not_share_proposals(proposals_world):
    msapi, marker, cams, ms = proposals_world
    twin = ms.Marker("GCP1", position=ms.Vector(0, 0, 0),
                     projections={cams[1]: ms.Marker.Projection(ms.Vector(1, 2, 0), False)})
    twin.key = 6
    msapi.mark_proposal(marker, cams[1])
    assert msapi.is_proposal(marker, cams[1]) is True
    assert msapi.is_proposal(twin, cams[1]) is False


def test_a_marker_without_a_key_falls_back_on_its_label(proposals_world):
    msapi, marker, cams, _ms = proposals_world
    del marker.key
    msapi.mark_proposal(marker, cams[1])
    assert msapi.is_proposal(marker, cams[1]) is True


# --------------------------------------------------------------------------
# msapi: the photo view widgets are found once per size, EXIF dates read once, menu fallback, probe
# --------------------------------------------------------------------------

class FakeWidget(object):
    def __init__(self, width, height, visible=True, parent=None):
        self.w, self.h, self.visible, self._parent, self.dead = width, height, visible, parent, False

    def isVisible(self):
        if self.dead:
            raise RuntimeError("Internal C++ object already deleted")
        return self.visible

    def width(self):
        return self.w

    def height(self):
        return self.h

    def parent(self):
        return self._parent


class FakeWindow(object):
    def __init__(self, widgets):
        self.widgets, self.scans = widgets, 0

    def findChildren(self, *_args):
        self.scans += 1
        return list(self.widgets)

    def objectName(self):
        return "main"


@pytest.fixture
def photo_world(monkeypatch, ms):
    import types
    import aerotools.msapi as msapi
    root = FakeWidget(1400, 900)
    pane = FakeWidget(800, 600, parent=root)
    inner = FakeWidget(800, 600, parent=pane)                 # deeper: kept first
    other = FakeWidget(300, 200, parent=root)
    window = FakeWindow([root, pane, inner, other])
    view = types.SimpleNamespace(camera=object(), width=800, height=600)
    monkeypatch.setattr(msapi, "photo_view", lambda: view)
    monkeypatch.setattr(msapi, "main_window", lambda: window)
    monkeypatch.setattr(msapi, "_PHOTO_WIDGETS", {"size": None, "window": None, "widgets": []})
    return msapi, window, view, pane, inner


def test_the_photo_view_is_found_by_walking_the_window_once(photo_world):
    msapi, window, _view, pane, inner = photo_world
    assert msapi.photo_view_widgets() == [inner, pane]
    for _ in range(50):                                        # fifty clicks
        assert msapi.photo_view_widgets() == [inner, pane]
    assert window.scans == 1


def test_the_window_is_walked_again_when_the_view_is_resized(photo_world):
    msapi, window, view, pane, inner = photo_world
    msapi.photo_view_widgets()
    view.width, view.height = 700, 500
    pane.w, pane.h, inner.w, inner.h = 700, 500, 700, 500
    assert msapi.photo_view_widgets() == [inner, pane] and window.scans == 2


@pytest.mark.parametrize("change", ["hidden", "resized", "deleted"])
def test_a_widget_that_is_gone_or_changed_triggers_a_new_walk(photo_world, change):
    msapi, window, _view, pane, inner = photo_world
    msapi.photo_view_widgets()
    if change == "hidden":
        inner.visible = False
    elif change == "resized":
        inner.w = 500
    else:
        inner.dead = True
    found = msapi.photo_view_widgets()
    assert window.scans == 2 and pane in found and inner not in found


def test_no_photo_open_gives_nothing_without_walking(photo_world):
    msapi, window, view, _pane, _inner = photo_world
    view.camera = None
    assert msapi.photo_view_widgets() == [] and window.scans == 0


def test_exif_dates_are_read_once_per_photo(ms):
    import types
    import aerotools.msapi as msapi
    msapi._EXIF_DATES.clear()
    reads = []

    class Meta(dict):
        def __getitem__(self, key):
            reads.append(key)
            return "2026:11:12 10:20:30"

    cam = ms.Camera(7, ms.Vector(0, 0, 0))
    cam.photo = types.SimpleNamespace(meta=Meta())
    chunk = ms.Chunk([cam], [])
    for _ in range(10):
        assert msapi.exif_date(chunk, cam) == "2026:11:12 10:20:30"
    assert len(reads) == 1


def test_a_photo_that_cannot_be_read_is_tried_again_later(ms):
    import types
    import aerotools.msapi as msapi
    msapi._EXIF_DATES.clear()
    state = {"ok": False}

    class Meta(object):
        def __getitem__(self, key):
            if not state["ok"]:
                raise OSError("share unavailable")
            return "2026:11:12 10:20:30"

    cam = ms.Camera(8, ms.Vector(0, 0, 0))
    cam.photo = types.SimpleNamespace(meta=Meta())
    chunk = ms.Chunk([cam], [])
    assert msapi.exif_date(chunk, cam) == ""
    state["ok"] = True
    assert msapi.exif_date(chunk, cam) == "2026:11:12 10:20:30"


def test_a_refused_shortcut_adds_the_entry_without_it_and_says_so(ms, monkeypatch):
    import aerotools.msapi as msapi
    msapi.Metashape = ms
    journal.clear()
    added = []

    def add(label, callback, shortcut=None):
        if shortcut:
            raise RuntimeError("shortcut already used")
        added.append(label)

    monkeypatch.setattr(ms.app, "addMenuItem", add)
    assert msapi.add_menu_item("Aero/Tool", lambda: None, "F8") is True
    assert added == ["Aero/Tool"]
    assert any("shortcut F8 refused" in line for line in journal.lines())


def test_a_menu_entry_that_exists_is_not_added_twice(ms, monkeypatch):
    import aerotools.msapi as msapi
    msapi.Metashape = ms
    calls = []

    def add(label, callback, shortcut=None):
        calls.append(label)
        raise RuntimeError("raised after the entry was created")

    monkeypatch.setattr(ms.app, "addMenuItem", add)
    monkeypatch.setattr(msapi, "_menu_has", lambda label: True)
    assert msapi.add_menu_item("Aero/Tool", lambda: None, "F8") is True
    assert calls == []                                # already there: Metashape is not asked at all


def test_the_probe_names_what_the_interface_lacks(ms, monkeypatch):
    import aerotools.msapi as msapi
    msapi.Metashape = ms
    journal.clear()
    monkeypatch.delattr(type(ms.app), "photos_pane", raising=False)
    missing = msapi.probe_capabilities()
    assert "photos_pane" in missing
    assert any("missing" in line and "photos_pane" in line for line in journal.lines())
