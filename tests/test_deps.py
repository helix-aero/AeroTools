"""Dependencies: the checker, the pip command line, the process layer, the failure reasons, and the launch path of a tool whose package is
missing. The window itself is built with real Qt in tests_qt/run_qt_deps.py (run by test_deps_qt.py). Nothing here runs a real pip."""

import ast
import os
import sys
import types

import pytest

from aerotools import bootstrap, config, deps, i18n

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE = os.path.join(ROOT, "aerotools")


# --------------------------------------------------------------------------
# config is the single source of truth
# --------------------------------------------------------------------------

def test_every_required_module_has_an_entry_with_a_description():
    for entry in config.PACKS:
        for name in entry.get("requires_modules", []):
            declared = config.MODULES.get(name)
            assert declared, "{} needs {} but config.MODULES has no entry".format(entry["id"], name)
            assert declared["pip"] and declared["title"]
            assert declared["info"] in __import__("aerotools.lang.en", fromlist=["TEXTS"]).TEXTS


def test_numpy_is_pinned_to_what_python_39_can_take():
    assert config.MODULES["numpy"]["pip"] == "numpy>=1.21,<2.1"


def _third_party_imports(path):
    std = set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else None
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return {name for name in found if name not in ("aerotools", "Metashape", "PySide2", "PySide6", "shiboken2", "shiboken6")
            and (std is None or name not in std)}


# Imported by a tool but not installed by this window: they live in the separate AI Python (the detection engine installs its own),
# or only in a test fake. Everything else a tool imports must be declared in `requires_modules` of its pack.
NOT_OURS = {"cv2", "onnxruntime", "torch", "ultralytics", "PIL"}
EXTERNAL_PYTHON = {"aigcp_runner.py", "aigcp_train.py",          # scripts run by the AI Python as their own process, not by Metashape
                   "imageadjust.py"}                             # numpy only makes a display aid available; the tool works without


def test_the_declared_modules_cover_the_imports_of_every_tool():
    for entry in config.PACKS:
        declared = set(entry.get("requires_modules", []))
        for base_dir in entry["paths"]:
            folder = os.path.join(ROOT, base_dir.replace("/", os.sep))
            for base, _dirs, names in os.walk(folder):
                for name in names:
                    if name.endswith(".py") and name not in EXTERNAL_PYTHON:
                        imported = _third_party_imports(os.path.join(base, name)) - NOT_OURS
                        assert imported <= declared, "{}: {} imports {} without declaring it".format(
                            entry["id"], os.path.relpath(os.path.join(base, name), ROOT), sorted(imported - declared))


def test_module_entry_of_an_unknown_package_is_usable():
    entry = config.module_entry("no_such_package_xyz")
    assert entry["pip"] == "no_such_package_xyz" and entry["info"] == ""


# --------------------------------------------------------------------------
# The checker
# --------------------------------------------------------------------------

@pytest.fixture
def made_up(monkeypatch):
    """A tool that needs one package that exists (json) and one that does not, with a registry entry for it."""
    packs = [{"id": "demo", "paths": ["aerotools/tools/demo/"], "marker": "aerotools/tools/demo/tool.py", "requires": [],
              "requires_modules": ["json", "no_such_package_xyz"]}]
    monkeypatch.setattr(config, "PACKS", list(config.PACKS) + packs)
    monkeypatch.setattr(config, "TOOLS", list(config.TOOLS) + [{"id": "demo", "title": "Demo", "menu": "Demo"}])
    monkeypatch.setattr(config, "MODULES", dict(config.MODULES, no_such_package_xyz={"title": "Nope", "pip": "nope>=1", "info": "deps.numpy_info"}))
    return "demo"


def test_collect_lists_what_is_missing_and_what_is_there(made_up, monkeypatch):
    monkeypatch.setattr(deps, "pip_version", lambda: "22.2")
    rows = deps.collect([made_up])
    assert [row["id"] for row in rows] == ["pip", "json", "no_such_package_xyz"]
    assert [bool(row["version"]) for row in rows] == [True, True, False]
    assert rows[2]["users"] == ["Demo"] and rows[2]["title"] == "Nope"


def test_collect_only_missing_keeps_the_missing_ones(made_up, monkeypatch):
    monkeypatch.setattr(deps, "pip_version", lambda: "22.2")
    assert [row["id"] for row in deps.collect([made_up], only_missing=True)] == ["no_such_package_xyz"]


def test_a_missing_pip_is_a_row_when_something_needs_installing(made_up, monkeypatch):
    monkeypatch.setattr(deps, "pip_version", lambda: "")
    rows = deps.collect([made_up], only_missing=True)
    assert [row["id"] for row in rows] == ["pip", "no_such_package_xyz"]
    assert rows[0]["info"] == "deps.pip_info"


def test_no_pip_row_when_nothing_is_missing_and_pip_is_absent(monkeypatch):
    monkeypatch.setattr(deps, "pip_version", lambda: "")
    monkeypatch.setattr(config, "PACKS", [{"id": "ok", "paths": ["x/"], "marker": "x", "requires": [], "requires_modules": ["json"]}])
    assert deps.collect(["ok"]) == [{"id": "json", "title": "json", "info": "", "users": ["ok"], "version": deps.module_version("json")}]


def test_collect_without_packages_is_empty(monkeypatch):
    monkeypatch.setattr(config, "PACKS", [{"id": "plain", "paths": ["x/"], "marker": "x", "requires": []}])
    assert deps.collect(["plain"]) == []


def test_the_tools_a_tool_requires_count_and_a_module_is_listed_once(monkeypatch):
    monkeypatch.setattr(config, "PACKS", [
        {"id": "base", "paths": ["b/"], "marker": "b", "requires": [], "requires_modules": ["numpy"]},
        {"id": "top", "paths": ["t/"], "marker": "t", "requires": ["base"], "requires_modules": ["numpy"]}])
    assert deps.modules_needed(["top", "base"]) == {"numpy": ["top", "base"]}


def test_extra_modules_are_listed_even_when_no_pack_declares_them(monkeypatch):
    monkeypatch.setattr(config, "PACKS", [{"id": "plain", "paths": ["x/"], "marker": "x", "requires": []}])
    monkeypatch.setattr(deps, "pip_version", lambda: "22.2")
    rows = deps.collect(["plain"], only_missing=True, extra_modules=["no_such_package_xyz"])
    assert [row["id"] for row in rows] == ["no_such_package_xyz"]


def test_module_version_is_empty_for_a_missing_package_and_set_for_a_present_one():
    assert deps.module_version("no_such_package_xyz") == ""
    assert deps.module_version("json")                                  # stdlib: "?" or a version, never empty


def test_a_module_hidden_in_sys_modules_counts_as_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "numpy", None)
    assert deps.module_version("numpy") == ""


def test_a_package_that_is_found_but_does_not_load_counts_as_missing(monkeypatch, tmp_path):
    # Metashape 2.4.0 kept a numpy built for Python 3.9 in its folder: find_spec sees it, the import fails
    package = tmp_path / "aero_broken_pkg"
    package.mkdir()
    (package / "__init__.py").write_text("from . import _not_built_for_this_python\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setitem(config.MODULES, "aero_broken_pkg", {"title": "Broken", "pip": "aero_broken_pkg", "info": ""})
    assert config.module_available("aero_broken_pkg") is False
    assert not [key for key in sys.modules if key.startswith("aero_broken_pkg")]
    (package / "__init__.py").write_text("")                           # a good copy installed meanwhile loads in the same session
    import importlib
    importlib.invalidate_caches()
    assert config.module_available("aero_broken_pkg") is True
    sys.modules.pop("aero_broken_pkg", None)


def test_pip_is_looked_up_in_process(monkeypatch):
    monkeypatch.setitem(sys.modules, "pip", None)
    assert deps.pip_version() == ""


# --------------------------------------------------------------------------
# Where python is
# --------------------------------------------------------------------------

def test_python_is_looked_for_next_to_metashape_first(monkeypatch, tmp_path):
    shop = tmp_path / "Metashape"
    (shop / "python").mkdir(parents=True)
    exe = shop / "python" / "python.exe"
    exe.write_bytes(b"")
    (shop / "metashape.exe").write_bytes(b"")
    monkeypatch.setattr(sys, "executable", str(shop / "metashape.exe"))
    monkeypatch.setattr(deps.os, "name", "nt")
    found, tried = deps.find_python()
    assert found == os.path.normpath(str(exe))
    assert tried[0] == os.path.normpath(str(exe))


def test_metashape_exe_is_never_taken_for_python(monkeypatch, tmp_path):
    (tmp_path / "metashape.exe").write_bytes(b"")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "metashape.exe"))
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "nowhere"))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "nowhere"))
    monkeypatch.setattr(deps.os, "name", "nt")
    found, tried = deps.find_python()
    assert found == "" and tried and all(not t.lower().endswith("metashape.exe") for t in tried)


# --------------------------------------------------------------------------
# The command line
# --------------------------------------------------------------------------

def test_the_pip_command_installs_into_the_user_folder_with_the_pinned_range():
    args = deps.pip_command("C:/Metashape/python/python.exe", "numpy")
    assert args[:4] == ["C:/Metashape/python/python.exe", "-m", "pip", "install"]
    assert "--user" in args and "--target" not in args
    assert "--disable-pip-version-check" in args and "--no-input" in args
    assert args[-1] == "numpy>=1.21,<2.1"
    assert args[args.index("--timeout") + 1] == "30"


def test_over_a_broken_copy_pip_ignores_what_is_installed():
    assert "--ignore-installed" not in deps.pip_command("py", "numpy")
    args = deps.pip_command("py", "numpy", over_broken=True)
    assert "--user" in args and args[-2:] == ["--ignore-installed", "numpy>=1.21,<2.1"]


def test_install_one_installs_over_a_copy_that_does_not_load(monkeypatch):
    run = fake_run([(0, ["Successfully installed numpy-2.0.2"])])
    states = iter(["", "2.0.2", "2.0.2"])
    monkeypatch.setattr(deps, "run_command", run)
    monkeypatch.setattr(deps, "refresh_modules", lambda: None)
    monkeypatch.setattr(deps, "distribution_version", lambda name: "2.0.2")
    monkeypatch.setattr(deps, "state_of", lambda name: next(states))
    log = []
    assert deps.install_one("numpy", "py", log.append)["ok"] is True
    assert "--ignore-installed" in run.calls[0][0]
    assert any("does not load" in line for line in log)


def test_ensurepip_is_tried_twice_the_second_time_with_upgrade():
    first, second = deps.ensurepip_commands("py")
    assert first == ["py", "-m", "ensurepip", "--user"] and second == first + ["--upgrade"]


def test_command_text_quotes_a_path_with_spaces(monkeypatch):
    monkeypatch.setattr(deps.os, "name", "nt")
    assert deps.command_text(["C:\\Program Files\\Agisoft\\python.exe", "-m", "pip"]) == '"C:\\Program Files\\Agisoft\\python.exe" -m pip'


# --------------------------------------------------------------------------
# The process layer (real child processes, harmless ones; never pip)
# --------------------------------------------------------------------------

def test_run_command_reads_the_output_line_by_line():
    heard = []
    code, output = deps.run_command([sys.executable, "-c", "print('one'); print('two')"], heard.append)
    assert code == 0 and heard == ["one", "two"] and output == "one\ntwo"


def test_run_command_reports_the_exit_code():
    code, output = deps.run_command([sys.executable, "-c", "import sys; print('bad'); sys.exit(3)"])
    assert code == 3 and "bad" in output


def test_a_command_that_cannot_start_returns_minus_one():
    code, output = deps.run_command(["no_such_program_xyz"])
    assert code == -1 and "cannot start" in output


def test_a_command_is_stopped_on_request(monkeypatch):
    monkeypatch.setattr(deps, "CHECK_PAUSE_S", 0.05)
    code, _output = deps.run_command([sys.executable, "-c", "import time; time.sleep(30)"], cancelled=lambda: True)
    assert code == -2


def test_a_command_that_runs_too_long_is_killed(monkeypatch):
    monkeypatch.setattr(deps, "CHECK_PAUSE_S", 0.05)
    code, _output = deps.run_command([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.2)
    assert code == -3


def test_no_console_window_and_no_shell(monkeypatch):
    seen = {}

    class Proc(object):
        stdout = types.SimpleNamespace(readline=lambda: b"")

        def wait(self, timeout=None):
            return 0

    def popen(args, **kwargs):
        seen.update(kwargs, args=args)
        return Proc()

    monkeypatch.setattr(deps.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    assert deps.run_command(["x"], popen=popen)[0] == 0
    assert seen["creationflags"] == 0x08000000 and not seen.get("shell") and seen["stdin"] == deps.subprocess.DEVNULL


# --------------------------------------------------------------------------
# Why it failed
# --------------------------------------------------------------------------

@pytest.mark.parametrize("code, text, reason", [
    (-1, "", "start"), (-2, "", "stopped"), (-3, "", "timeout"),
    (1, "WARNING: Retrying (Retry(total=1)) after connection broken by 'NewConnectionError(Failed to establish a new connection: "
        "[Errno 11001] getaddrinfo failed)'\nERROR: No matching distribution found for numpy", "offline"),
    (1, "ProxyError('Cannot connect to proxy.', OSError('Tunnel connection failed: 407'))", "proxy"),
    (1, "SSLError(1, '[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed')", "ssl"),
    (1, "ERROR: Could not install packages due to an OSError: [WinError 5] Access is denied: 'C:\\\\x'", "permission"),
    (1, "OSError: [Errno 28] No space left on device", "disk"),
    (1, "C:\\python.exe: No module named ensurepip", "no_ensurepip"),
    (1, "C:\\python.exe: No module named pip", "no_pip"),
    (1, "ERROR: Could not find a version that satisfies the requirement numpy>=9\nERROR: No matching distribution found", "nomatch"),
    (1, "something odd", "generic"),
])
def test_a_failure_is_explained_in_one_short_reason(code, text, reason):
    assert deps.explain(code, text) == reason
    assert i18n.t("deps.err_" + reason) and len(i18n.t("deps.err_" + reason)) < 40


# --------------------------------------------------------------------------
# install_one with a fake process
# --------------------------------------------------------------------------

def fake_run(script):
    """A stand-in for deps.run_command: `script` is [(exit code, [lines])] answered one command at a time."""
    calls = []
    answers = list(script)

    def run(args, on_line=None, cancelled=None, timeout=None, popen=None):
        calls.append((list(args), timeout))
        code, lines = answers.pop(0)
        for line in lines:
            on_line(line)
        return code, "\n".join(lines)

    run.calls = calls
    return run


def test_install_one_runs_pip_and_reports_progress(monkeypatch):
    run = fake_run([(0, ["Collecting numpy<2.1,>=1.21", "Downloading numpy-2.0.2-cp39-cp39-win_amd64.whl (15.9 MB)",
                         "Installing collected packages: numpy", "Successfully installed numpy-2.0.2"])])
    monkeypatch.setattr(deps, "run_command", run)
    monkeypatch.setattr(deps, "refresh_modules", lambda: None)
    monkeypatch.setattr(deps, "state_of", lambda name: "2.0.2")
    log, steps = [], []
    result = deps.install_one("numpy", "py", log.append, lambda f, k, s: steps.append((f, k, s)))
    assert result == {"ok": True, "reason": "", "code": 0, "restart": False}
    assert run.calls[0][0][:4] == ["py", "-m", "pip", "install"] and run.calls[0][1] == deps.STEP_TIMEOUT_S
    assert (0.35, "download", "15.9 MB") in steps and steps[-1][0] == 1.0
    assert log[0].startswith("$ ") and "installed: numpy 2.0.2" in log


def test_progress_is_read_from_the_lines_pip_really_prints():
    assert deps.progress_of("Collecting numpy<2.1,>=1.21") == (0.15, "collect", "")
    assert deps.progress_of("  Using cached numpy-2.0.2-cp39-cp39-win_amd64.whl (15.9 MB)") == (0.35, "download", "15.9 MB")
    assert deps.progress_of("Installing collected packages: numpy") == (0.85, "install", "")
    assert deps.progress_of("WARNING: something") is None


def test_install_one_says_restart_when_the_package_is_still_not_importable(monkeypatch):
    monkeypatch.setattr(deps, "run_command", fake_run([(0, ["Successfully installed numpy-2.0.2"])]))
    monkeypatch.setattr(deps, "refresh_modules", lambda: None)
    monkeypatch.setattr(deps, "state_of", lambda name: "")
    assert deps.install_one("numpy", "py", lambda text: None)["restart"] is True


def test_install_one_fails_with_a_reason_from_the_output(monkeypatch):
    monkeypatch.setattr(deps, "run_command", fake_run([(1, ["Failed to establish a new connection", "ERROR: No matching distribution"])]))
    result = deps.install_one("numpy", "py", lambda text: None)
    assert result["ok"] is False and result["reason"] == "offline" and result["code"] == 1


def test_pip_is_bootstrapped_with_ensurepip_and_the_second_attempt_follows_the_first(monkeypatch):
    run = fake_run([(1, ["error"]), (0, ["Successfully installed pip-22.0.4"])])
    monkeypatch.setattr(deps, "run_command", run)
    monkeypatch.setattr(deps, "refresh_modules", lambda: None)
    monkeypatch.setattr(deps, "state_of", lambda name: "22.0.4")
    assert deps.install_one("pip", "py", lambda text: None)["ok"] is True
    assert [c[0] for c in run.calls] == deps.ensurepip_commands("py")
    assert run.calls[0][1] == deps.ENSUREPIP_TIMEOUT_S


def test_no_ensurepip_gives_the_short_reason_and_does_not_retry(monkeypatch):
    run = fake_run([(1, ["py: No module named ensurepip"]), (1, ["py: No module named ensurepip"])])
    monkeypatch.setattr(deps, "run_command", run)
    result = deps.install_one("pip", "py", lambda text: None)
    assert result["reason"] == "no_ensurepip" and len(run.calls) == 2           # the upgrade attempt is the last resort


def test_a_stopped_install_does_not_try_the_next_attempt(monkeypatch):
    run = fake_run([(-2, [])])
    monkeypatch.setattr(deps, "run_command", run)
    assert deps.install_one("pip", "py", lambda text: None)["reason"] == "stopped" and len(run.calls) == 1


def test_without_python_nothing_is_run(monkeypatch):
    monkeypatch.setattr(deps, "run_command", lambda *a, **k: pytest.fail("must not run"))
    assert deps.install_one("numpy", "", lambda text: None)["reason"] == "python"


def test_refresh_modules_adds_the_user_site_once_it_exists(monkeypatch, tmp_path):
    import site
    monkeypatch.setattr(site, "getusersitepackages", lambda: str(tmp_path))
    added = []
    monkeypatch.setattr(site, "addsitedir", added.append)
    monkeypatch.setattr(sys, "path", list(sys.path))
    deps.refresh_modules()
    assert added == [str(tmp_path)]


def test_the_user_folder_of_metashape_24_is_found_on_sys_path():
    base = os.path.join("C:" + os.sep, "Users", "me", "AppData", "Local", "Agisoft", "Metashape Pro", "user-packages-py312")
    path = [os.path.join("C:" + os.sep, "Metashape", "python", "Lib"), os.path.join(base, "Python312", "site-packages")]
    assert deps.metashape_user_base(path) == base
    assert deps.metashape_user_base([os.path.join("C:" + os.sep, "Users", "me", "AppData", "Roaming", "Python", "Python39",
                                                  "site-packages")]) == ""


def test_pip_writes_to_the_user_folder_of_metashape_24_when_there_is_one(monkeypatch):
    seen = {}

    class Proc(object):
        stdout = types.SimpleNamespace(readline=lambda: b"")

        def wait(self, timeout=None):
            return 0

    def popen(args, **kwargs):
        seen.update(kwargs)
        return Proc()

    monkeypatch.setattr(deps, "metashape_user_base", lambda path=None: "C:/ms/user-packages-py312")
    deps.run_command(["x"], popen=popen)
    assert seen["env"]["PYTHONUSERBASE"] == "C:/ms/user-packages-py312"
    monkeypatch.setattr(deps, "metashape_user_base", lambda path=None: "")
    monkeypatch.delenv("PYTHONUSERBASE", raising=False)
    deps.run_command(["x"], popen=popen)
    assert "PYTHONUSERBASE" not in seen["env"]


def test_refresh_modules_forgets_that_the_metashape_user_folder_was_missing(monkeypatch, tmp_path):
    folder = str(tmp_path / "user-packages-py312" / "Python312" / "site-packages")
    monkeypatch.setattr(sys, "path", [folder] + list(sys.path))
    monkeypatch.setitem(sys.path_importer_cache, folder, None)
    deps.refresh_modules()
    assert sys.path_importer_cache.get(folder, None) is None and folder not in sys.path_importer_cache


# --------------------------------------------------------------------------
# The tool-launch path
# --------------------------------------------------------------------------

@pytest.fixture
def boot(ms, clean_bootstrap_flags):
    saved = {k: v for k, v in sys.modules.items() if k.startswith("aerotools")}
    yield bootstrap
    package = saved["aerotools"]
    for key in [k for k in sys.modules if k.startswith("aerotools")]:
        del sys.modules[key]
        if key.count(".") == 1 and hasattr(package, key.split(".")[1]):
            delattr(package, key.split(".")[1])            # `from . import x` reads the package attribute, not sys.modules
    sys.modules.update(saved)
    for key, module in saved.items():
        if key.count(".") == 1:
            setattr(package, key.split(".")[1], module)


def fake_window(monkeypatch, result, opened):
    window = types.ModuleType("aerotools.deps_dialog")

    def show(parent=None, tool_id=None, modules=(), offer_start=True):
        opened.append((tool_id, list(modules)))
        return result

    window.show = show
    monkeypatch.setitem(sys.modules, "aerotools.deps_dialog", window)
    sys.modules["aerotools"].deps_dialog = window              # `from . import x` reads this attribute first (the `boot` fixture undoes it)


def test_a_tool_with_a_missing_package_opens_the_window_instead_of_failing(boot, ms, monkeypatch):
    import importlib
    monkeypatch.setattr(sys.modules["aerotools.config"], "missing_modules", lambda tool_id: ["numpy"])
    monkeypatch.setattr(sys.modules["aerotools.deps"], "module_available", lambda name: False)
    monkeypatch.setattr(sys.modules["aerotools.deps"], "refresh_modules", lambda: None)
    opened = []
    fake_window(monkeypatch, False, opened)
    monkeypatch.setattr(importlib, "import_module", lambda *a, **k: pytest.fail("the tool must not load"))
    assert boot.launch("fastgcp") is False
    assert opened == [("fastgcp", ["numpy"])] and ms.app.messages == []


def test_the_tool_starts_when_the_person_chooses_to_start_it_after_the_install(boot, ms, monkeypatch):
    import importlib
    cfg = sys.modules["aerotools.config"]
    answers = [["numpy"], []]                                     # missing before the window, nothing missing after it
    monkeypatch.setattr(cfg, "missing_modules", lambda tool_id: answers.pop(0) if answers else [])
    monkeypatch.setattr(sys.modules["aerotools.deps"], "module_available", lambda name: False)
    monkeypatch.setattr(sys.modules["aerotools.deps"], "refresh_modules", lambda: None)
    fake_window(monkeypatch, True, [])
    started = []
    monkeypatch.setattr(importlib, "import_module", lambda name, *a, **k: types.SimpleNamespace(install=lambda: started.append(name)))
    assert boot.launch("fastgcp") is True and started == ["aerotools.tools.fastgcp.tool"]


def test_a_package_installed_meanwhile_lets_the_tool_start_without_the_window(boot, ms, monkeypatch):
    import importlib
    cfg = sys.modules["aerotools.config"]
    monkeypatch.setattr(cfg, "missing_modules", lambda tool_id: ["numpy"])
    monkeypatch.setattr(sys.modules["aerotools.deps"], "module_available", lambda name: True)
    monkeypatch.setattr(sys.modules["aerotools.deps"], "refresh_modules", lambda: None)
    opened = []
    fake_window(monkeypatch, False, opened)
    monkeypatch.setattr(importlib, "import_module", lambda name, *a, **k: types.SimpleNamespace(install=lambda: None))
    assert boot.launch("fastgcp") is True and opened == []


def test_without_the_window_the_old_one_line_message_is_shown(boot, ms, monkeypatch):
    monkeypatch.setattr(sys.modules["aerotools.config"], "missing_modules", lambda tool_id: ["numpy"])
    monkeypatch.setattr(sys.modules["aerotools.deps"], "module_available", lambda name: False)
    window = types.ModuleType("aerotools.deps_dialog")

    def broken(*args, **kwargs):
        raise RuntimeError("no window")

    window.show = broken
    monkeypatch.setitem(sys.modules, "aerotools.deps_dialog", window)
    sys.modules["aerotools"].deps_dialog = window              # `from . import x` reads this attribute first (the `boot` fixture undoes it)
    assert boot.launch("fastgcp") is False
    assert "numpy" in ms.app.messages[0] and "Aero-GCP" in ms.app.messages[0]


def test_a_package_that_fails_to_import_and_is_known_opens_the_window_for_the_next_click(boot, ms, monkeypatch):
    import importlib

    def fails(name, *args, **kwargs):
        raise ModuleNotFoundError("No module named 'numpy'", name="numpy")

    monkeypatch.setattr(importlib, "import_module", fails)
    opened = []
    fake_window(monkeypatch, True, opened)
    monkeypatch.setattr(sys.modules["aerotools.deps"], "refresh_modules", lambda: None)
    assert boot.launch("fastgcp") is False
    assert opened == [("fastgcp", ["numpy"])]


def test_the_dependencies_menu_entry_sits_after_install_plugins(boot, ms):
    boot.install_menu()
    labels = [label for kind, label in ms.app.menu_order if kind == "item"]
    assert labels.index("Aero/Dependencies...") == labels.index("Aero/Install plugins...") + 1


def test_the_dependencies_menu_opens_the_window(boot, ms, monkeypatch):
    opened = []
    fake_window(monkeypatch, False, opened)
    monkeypatch.setattr(sys.modules["aerotools.deps"], "refresh_modules", lambda: None)
    boot.open_dependencies()
    assert opened == [(None, [])]


def test_the_dependencies_menu_falls_back_to_a_message(boot, ms, monkeypatch):
    window = types.ModuleType("aerotools.deps_dialog")

    def broken(*args, **kwargs):
        raise RuntimeError("no window")

    window.show = broken
    monkeypatch.setitem(sys.modules, "aerotools.deps_dialog", window)
    sys.modules["aerotools"].deps_dialog = window              # `from . import x` reads this attribute first (the `boot` fixture undoes it)
    boot.open_dependencies()
    assert "dependencies window did not open" in ms.app.messages[0] and "no window" in ms.app.messages[0]
