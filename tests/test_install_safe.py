"""install/install_core.ps1 and the manifest tool: staged copy, digests, swap, odd paths, zip viewer, deterministic manifest."""

import ast
import importlib.util
import json
import os
import shutil
import subprocess

import pytest

from aerotools import updater

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "install", "install_core.ps1")
TEMPLATE = os.path.join(ROOT, "install", "aerotools_launcher.py.tpl")
POWERSHELL = shutil.which("powershell")

needs_powershell = pytest.mark.skipif(os.name != "nt" or not POWERSHELL, reason="needs Windows PowerShell")


def load_maker():
    spec = importlib.util.spec_from_file_location("make_manifest", os.path.join(ROOT, "install", "make_manifest.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


maker = load_maker()


def run(root, local, *args):
    env = dict(os.environ, LOCALAPPDATA=str(local))
    return subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", SCRIPT, "-Root", str(root), "-Copy", *args],
                          capture_output=True, text=True, env=env, timeout=120)


def make_root(tmp_path, folder, files, absent=()):
    """A small installer folder whose manifest has the right digests. `files` {name: bytes}; `absent` are listed but not written."""
    root = tmp_path / folder
    (root / "aerotools").mkdir(parents=True)
    (root / "install").mkdir()
    shutil.copyfile(TEMPLATE, str(root / "install" / "aerotools_launcher.py.tpl"))
    files = dict(files)
    files.setdefault("aerotools/bootstrap.py", b"# x\n")
    listed = {}
    for name, data in files.items():
        listed[name] = {"sha256": updater.digest_bytes(data, name), "size": len(data)}
        if name not in absent:
            target = root.joinpath(*name.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    (root / "manifest.json").write_text(json.dumps({"files": listed, "packs": []}), encoding="utf-8")
    return root


def app_of(local):
    return local / "HELIX" / "AeroToolsApp"


# --------------------------------------------------------------------------
# The installer
# --------------------------------------------------------------------------

@needs_powershell
def test_every_missing_file_is_named_at_once_and_nothing_is_copied(tmp_path):
    root = make_root(tmp_path, "AeroTools-main", {"aerotools/a.py": b"1", "aerotools/b.py": b"2", "aerotools/c.py": b"3"},
                     absent=("aerotools/a.py", "aerotools/b.py"))
    result = run(root, tmp_path / "local")
    assert result.returncode == 1
    assert "2 files missing" in result.stdout and "aerotools/a.py" in result.stdout and "aerotools/b.py" in result.stdout
    assert not (app_of(tmp_path / "local") / "aerotools" / "c.py").exists()


@needs_powershell
def test_a_missing_guide_is_only_a_warning(tmp_path):
    root = make_root(tmp_path, "AeroTools-main", {"docs/OLD.md": b"# old\n", "aerotools/a.py": b"1"}, absent=("docs/OLD.md",))
    result = run(root, tmp_path / "local")
    assert result.returncode == 0, result.stdout
    assert "docs/OLD.md is not in this folder" in result.stdout
    assert (app_of(tmp_path / "local") / "aerotools" / "a.py").is_file()


@needs_powershell
def test_a_file_that_does_not_match_its_digest_stops_the_installation_before_anything_is_replaced(tmp_path):
    local = tmp_path / "local"
    assert run(make_root(tmp_path, "AeroTools-main", {"aerotools/a.py": b"print('old')\n"}), local).returncode == 0
    app = app_of(local)
    bad = make_root(tmp_path, "AeroTools-new", {"aerotools/a.py": b"print('new')\n", "aerotools/z.py": b"z\n"})
    (bad / "aerotools" / "z.py").write_bytes(b"tampered\n")
    result = run(bad, local)
    assert result.returncode == 1 and "aerotools/z.py does not match manifest.json" in result.stdout
    assert (app / "aerotools" / "a.py").read_bytes() == b"print('old')\n"           # the installed core is untouched
    assert not (app / "aerotools" / "z.py").exists()
    assert not os.path.exists(str(app) + ".new")                                    # the staging folder is gone


@needs_powershell
def test_crlf_files_pass_the_digest_check_and_are_copied_as_they_are(tmp_path):
    root = make_root(tmp_path, "AeroTools-main", {"install/x.bat": b"@echo off\n"})
    (root / "install" / "x.bat").write_bytes(b"@echo off\r\n")                       # the working copy has CRLF, the digest is of LF
    result = run(root, tmp_path / "local")
    assert result.returncode == 0, result.stdout
    assert (app_of(tmp_path / "local") / "install" / "x.bat").read_bytes() == b"@echo off\r\n"


@needs_powershell
def test_a_core_file_the_new_version_dropped_is_removed_and_kept_in_the_old_folder(tmp_path):
    local = tmp_path / "local"
    assert run(make_root(tmp_path, "AeroTools-v1", {"aerotools/keep.py": b"k", "aerotools/gone.py": b"g"}), local).returncode == 0
    assert (app_of(local) / "aerotools" / "gone.py").is_file()
    assert run(make_root(tmp_path, "AeroTools-v2", {"aerotools/keep.py": b"k2"}), local).returncode == 0
    assert not (app_of(local) / "aerotools" / "gone.py").exists()
    assert (app_of(local) / "aerotools" / "keep.py").read_bytes() == b"k2"
    assert (local / "HELIX" / "AeroToolsApp.old" / "aerotools" / "gone.py").is_file()


@needs_powershell
def test_the_installer_works_from_a_folder_with_spaces_and_accents(tmp_path):
    root = make_root(tmp_path, "Aero Tools \u00e9t\u00e9 (copie)", {"aerotools/a.py": b"1"})
    local = tmp_path / "loc\u00e0l dir"
    result = run(root, local)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (app_of(local) / "aerotools" / "a.py").is_file()
    launcher = local / "Agisoft" / "Metashape Pro" / "scripts" / "aerotools_launcher.py"
    ast.parse(launcher.read_text(encoding="utf-8"))
    assert str(app_of(local)) in launcher.read_text(encoding="utf-8")


@needs_powershell
def test_a_drive_root_gives_a_launcher_that_is_valid_python(tmp_path):
    """A raw string cannot end with a backslash: r"D:\\" is a SyntaxError. The installer writes r"D:" + "\\\\" instead."""
    root = make_root(tmp_path, "AeroTools-main", {"aerotools/a.py": b"1"})
    script = ("$t = 'X:\\'; $c = $t.TrimEnd([char]92); $l = 'r\"' + $c + '\"'; if ($c.Length -lt $t.Length) { $l += ' + \"' + [char]92 + [char]92 + '\"' }; "
              "$l")
    done = subprocess.run([POWERSHELL, "-NoProfile", "-Command", script], capture_output=True, text=True, timeout=60)
    literal = done.stdout.strip()
    assert literal == 'r"X:" + "\\\\"', literal
    source = open(TEMPLATE, encoding="utf-8").read().replace('r"@@AEROTOOLS_ROOT@@"', literal)
    tree = ast.parse(source)
    value = next(n.value for n in ast.walk(tree) if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "_aero_root")
    assert eval(compile(ast.Expression(value), "x", "eval")) == "X:\\"
    # the installer itself accepts a drive root as -AppDir (dry run: nothing is written)
    drive = os.path.splitdrive(str(tmp_path))[0] + "\\"
    assert run(root, tmp_path / "local", "-AppDir", drive, "-DryRun").returncode == 0


@needs_powershell
def test_running_from_inside_a_zip_viewer_says_to_extract_the_zip(tmp_path):
    lost = tmp_path / "Temp1_AeroTools-main.zip" / "AeroTools-main"
    (lost / "install").mkdir(parents=True)
    done = run(lost, tmp_path / "local")
    assert done.returncode == 1 and "inside a zip file" in done.stdout and "Extract the whole zip" in done.stdout


@pytest.mark.parametrize("name", ["CLICK HERE TO INSTALL.bat", "install/install_aerotools.bat"])
def test_the_launchers_explain_a_zip_that_was_not_extracted(name):
    with open(os.path.join(ROOT, *name.split("/")), encoding="ascii") as handle:
        text = handle.read()
    assert "if not exist" in text and "install_core.ps1" in text and "zip" in text.lower()
    assert '-Root "%AT_ROOT%"' not in text                                          # a trailing backslash would swallow the quote


def test_the_installer_scripts_are_ascii_with_crlf():
    for name in ("install_core.ps1", "install_all.ps1"):
        with open(os.path.join(ROOT, "install", name), "rb") as handle:
            data = handle.read()
        assert all(byte < 128 for byte in data), name
        assert b"\n" not in data.replace(b"\r\n", b""), name


# --------------------------------------------------------------------------
# The manifest tool
# --------------------------------------------------------------------------

def tiny_repo(tmp_path):
    root = tmp_path / "repo"
    for name, data in {"aerotools/__init__.py": '__version__ = "1.2.3"\n', "aerotools/a.py": "a = 1\n", "install/run.bat": "@echo off\r\n",
                       "docs/guide.md": "# guide\n", "docs/debug_adjust.py": "print('debug')\n", "README.md": "# r\n"}.items():
        path = root.joinpath(*name.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode("utf-8"))
    return root


def test_debug_scripts_of_the_docs_folder_are_not_published(tmp_path):
    root = tiny_repo(tmp_path)
    listed = maker.listing(str(root))
    assert "docs/guide.md" in listed and "docs/debug_adjust.py" not in listed
    assert not any(name.startswith("docs/") and name.endswith(".py") for name in maker.listing(ROOT))


def test_regenerating_an_unchanged_tree_gives_identical_bytes(tmp_path):
    root = tiny_repo(tmp_path)
    maker.write(str(root))
    first = (root / "manifest.json").read_bytes()
    maker.write(str(root))
    assert (root / "manifest.json").read_bytes() == first


def test_a_changed_file_gives_a_new_build_date_and_a_clean_check(tmp_path, monkeypatch):
    root = tiny_repo(tmp_path)
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")
    maker.write(str(root))
    before = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    (root / "aerotools" / "a.py").write_text("a = 2\n", encoding="utf-8")
    assert maker.stale(str(root)) == ["changed since the manifest was written: aerotools/a.py"]
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1800000000")
    maker.write(str(root))
    after = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert after["built"] != before["built"] and maker.stale(str(root)) == []


def test_the_check_names_added_and_removed_files(tmp_path):
    root = tiny_repo(tmp_path)
    maker.write(str(root))
    (root / "aerotools" / "new.py").write_text("n = 1\n", encoding="utf-8")
    (root / "docs" / "guide.md").unlink()
    problems = maker.stale(str(root))
    assert "on disk but not listed: aerotools/new.py" in problems
    assert "listed but not on disk (or not published any more): docs/guide.md" in problems


def test_every_file_the_installer_would_copy_exists_and_the_manifest_is_fresh():
    """The failure that broke installs: a manifest listing a file that is gone. The message says what to run."""
    problems = maker.stale(ROOT)
    assert problems == [], "run: python install/make_manifest.py   ({})".format("; ".join(problems[:6]))
