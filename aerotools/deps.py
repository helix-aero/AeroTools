"""Python dependencies of the tools: what is missing, and how to install it into Metashape's own Python (no Qt in this module).

The list comes from config: every pack may declare `requires_modules` and every such name has an entry in `config.MODULES` (pip
requirement, title, description). pip itself is one more dependency, because installing anything else needs it.

Where Python is. Inside Metashape `sys.executable` is metashape.exe, not python, and a script cannot call pip in-process without
touching Metashape's own state. So the interpreter that ships with Metashape is run as a child process: `<Metashape folder>\\python\\python.exe`
on Windows (`<folder>/python/bin/python3.9` elsewhere). `find_python` looks next to metashape.exe and under `sys.prefix`, and returns
the places it looked at for the messages.

How it is installed. Metashape's folder is rarely writable without administrator rights, so everything goes to the user's site-packages
(`pip install --user`). Metashape 2.4 and later have their own user folder first on sys.path (`metashape_user_base`): pip is pointed at it,
so nothing lands in the user folder that a Python installed on the computer shares. That folder may not exist when Metashape starts: `refresh_modules` puts it on `sys.path` and clears the import caches,
so the package works in the running session and no restart is needed. If it still cannot be found afterwards, the row says so.

What this module never does: install anything by itself (the window asks), run a command without a timeout, or open a console window.
The process layer (`run_command`) takes `popen` as an argument so the tests never start a real pip.
"""

import importlib
import importlib.util
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time

from . import config

PIP = "pip"
PIP_INFO = "deps.pip_info"
PIP_TITLE = "pip"

STEP_TIMEOUT_S = 600                    # one pip install (a big wheel on a slow line)
ENSUREPIP_TIMEOUT_S = 180
CHECK_PAUSE_S = 0.2                     # how often a running command looks at the stop request and the clock

PIP_ARGS = ("-m", "pip", "install", "--user", "--disable-pip-version-check", "--no-input", "--no-warn-script-location",
            "--progress-bar", "off", "--timeout", "30", "--retries", "2")
ENSUREPIP_ARGS = ("-m", "ensurepip", "--user")
ENSUREPIP_UPGRADE_ARGS = ("-m", "ensurepip", "--user", "--upgrade")           # second attempt when the first one is refused


# --------------------------------------------------------------------------
# Where Python is
# --------------------------------------------------------------------------

def _interpreter_names():
    if os.name == "nt":
        return ["python.exe"]
    return ["python{}.{}".format(sys.version_info[0], sys.version_info[1]), "python3", "python"]


def candidates():
    """Paths where Metashape's python could be, best first (duplicates removed, nothing checked)."""
    names = _interpreter_names()
    folders = []
    exe_dir = os.path.dirname(os.path.abspath(sys.executable or ""))
    if exe_dir:
        folders += [os.path.join(exe_dir, "python"), os.path.join(exe_dir, "python", "bin"), os.path.join(exe_dir, "Python"),
                    os.path.normpath(os.path.join(exe_dir, "..", "Frameworks", "Python.framework", "Versions", "Current", "bin"))]
    for base in (getattr(sys, "base_prefix", ""), sys.prefix):
        if base:
            folders += [base, os.path.join(base, "bin"), os.path.join(base, "Scripts")]
    if exe_dir:
        folders.append(exe_dir)                                                  # last: a python.exe next to a python.exe
    found = []
    for folder in folders:
        for name in names:
            path = os.path.normpath(os.path.join(folder, name))
            if path not in found:
                found.append(path)
    return found


def find_python():
    """(path, tried): the python of this Metashape, or "" when none of the usual places has one (`tried` lists what was looked at)."""
    tried = candidates()
    for path in tried:
        if os.path.isfile(path) and not _is_metashape(path):
            return path, tried
    return "", tried


def _is_metashape(path):
    return os.path.basename(path).lower().startswith("metashape")


# --------------------------------------------------------------------------
# What is installed
# --------------------------------------------------------------------------

def metashape_user_base(path=None):
    """The user packages folder of Metashape 2.4 and later (`...\\Agisoft\\Metashape Pro\\user-packages-py312`), "" for older ones.
    Metashape puts its site-packages (`<folder>\\Python312\\site-packages`) first on sys.path, even before it exists; `pip install --user`
    writes there when PYTHONUSERBASE names the folder."""
    for entry in (sys.path if path is None else path):
        parts = os.path.normpath(str(entry)).split(os.sep)
        if not parts or parts[-1].lower() != "site-packages":
            continue
        for index, part in enumerate(parts[:-1]):
            if part.lower().startswith("user-packages-py"):
                return os.sep.join(parts[:index + 1])
    return ""


def refresh_modules():
    """Make what was just installed importable without restarting Metashape: the user site folder (it may not have existed when Python
    started, so it is not on sys.path yet), Metashape's own user folder (on sys.path from the start, but remembered as missing) and the
    import caches."""
    try:
        import site
        folder = site.getusersitepackages()
        if folder and os.path.isdir(folder) and folder not in sys.path:
            site.addsitedir(folder)
    except Exception:
        pass
    if metashape_user_base():
        for entry in sys.path:
            if metashape_user_base([entry]) and sys.path_importer_cache.get(entry, False) is None:
                del sys.path_importer_cache[entry]
    importlib.invalidate_caches()


def module_available(name):
    return config.module_available(name)


def distribution_version(name):
    """Version of an installed distribution as its metadata says, or "" (nothing is imported)."""
    try:
        from importlib import metadata
        return metadata.version(name) or ""
    except Exception:
        return ""


def module_version(name):
    """Version of a package that can be imported, "?" when it is there without a version, "" when it is missing."""
    if not module_available(name):
        return ""
    return distribution_version(name) or getattr(sys.modules.get(name), "__version__", "") or "?"


def pip_version():
    """Version of the pip of this Python, "" when it has none. Looked up in-process (Metashape's Python is the one pip would run in)."""
    return module_version(PIP)


def state_of(name):
    """Version of a dependency ("pip" or a package name), "" when missing."""
    return pip_version() if name == PIP else module_version(name)


# --------------------------------------------------------------------------
# What is needed
# --------------------------------------------------------------------------

def modules_needed(tool_ids):
    """{package: [tool ids that need it]} for these tools and the tools they require, in a stable order (the order of `tool_ids`)."""
    needed = {}
    for tool_id in tool_ids:
        for pack_id in config.pack_closure([tool_id]):
            for module in config.pack(pack_id).get("requires_modules", []):
                users = needed.setdefault(module, [])
                if tool_id not in users:
                    users.append(tool_id)
    return needed


def installed_tool_ids():
    """The tools installed on this computer, in menu order."""
    from . import updater
    return [entry["id"] for entry in config.TOOLS if updater.tool_installed(entry["id"])]


def _tool_titles(tool_ids):
    return [config.tool(t)["title"] if config.tool(t) else t for t in tool_ids]


def collect(tool_ids=None, only_missing=False, extra_modules=()):
    """The rows of the Dependencies window, pip first: [{"id", "title", "info", "users", "version"}], `version` "" for a missing one.

    tool_ids       the tools to look at (default: every installed tool)
    only_missing   keep only what is missing (the window a tool opens when it cannot start)
    extra_modules  packages to list besides the ones the tools declare (an import that failed in a tool)

    pip is listed when something is missing (it installs the rest) or when it is there (so the full list shows the state); a Python
    without pip and with nothing to install does not get a pip row."""
    ids = installed_tool_ids() if tool_ids is None else list(tool_ids)
    needed = modules_needed(ids)
    for name in extra_modules:
        needed.setdefault(name, [])
    rows = []
    for name, users in needed.items():
        entry = config.module_entry(name)
        rows.append({"id": name, "title": entry["title"], "info": entry["info"], "users": _tool_titles(users),
                     "version": module_version(name)})
    if not rows:
        return []
    some_missing = any(not row["version"] for row in rows)
    pip = pip_version()
    if some_missing or pip:
        rows.insert(0, {"id": PIP, "title": PIP_TITLE, "info": PIP_INFO, "users": [], "version": pip})
    if only_missing:
        rows = [row for row in rows if not row["version"]]
    return rows


# --------------------------------------------------------------------------
# What to run
# --------------------------------------------------------------------------

def pip_command(python, name, over_broken=False):
    """The command that installs one package (pip's own range from config.MODULES): the list of arguments, python first.
    `over_broken`: a copy that does not load is in the way (often in Metashape's folder, not writable), so pip must not take it as
    installed; the new copy goes to the user folder, which comes first on sys.path, and the old one is left where it is."""
    extra = ["--ignore-installed"] if over_broken else []
    return [python] + list(PIP_ARGS) + extra + [config.module_entry(name)["pip"]]


def broken_copy(name):
    """True when pip can see the package (its metadata is there) but it cannot be imported in this Metashape."""
    return name != PIP and bool(distribution_version(name)) and not state_of(name)


def ensurepip_commands(python):
    """The commands that bootstrap pip, one after the other until one works (alternatives, not steps)."""
    return [[python] + list(ENSUREPIP_ARGS), [python] + list(ENSUREPIP_UPGRADE_ARGS)]


def command_text(args):
    """The command line as one string to paste in a terminal (the program quoted when its path has spaces)."""
    if os.name == "nt":
        return subprocess.list2cmdline([str(a) for a in args])
    import shlex
    return " ".join(shlex.quote(str(a)) for a in args)


def run_command(args, on_line=None, cancelled=None, timeout=STEP_TIMEOUT_S, popen=subprocess.Popen):
    """Run a command to the end, feeding its output (stdout and stderr together) line by line to `on_line`.

    Returns (exit code, output text). Exit code -1 when it could not start, -2 when it was stopped (`cancelled()` became true), -3 when
    it ran longer than `timeout` seconds; the process is killed in the last two cases. Never raises. No console window on Windows. The
    reading is done in a helper thread so a silent process cannot hold up the stop request or the clock."""
    on_line = on_line or (lambda text: None)
    cancelled = cancelled or (lambda: False)
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PIP_DISABLE_PIP_VERSION_CHECK="1", PYTHONUTF8="1")
    if metashape_user_base():
        env["PYTHONUSERBASE"] = metashape_user_base()
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = popen([str(a) for a in args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                     creationflags=flags, env=env, cwd=tempfile.gettempdir())
    except Exception as exc:                          # missing file, no right to run it
        text = "cannot start: {!r}".format(exc)
        on_line(text)
        return -1, text
    lines = queue.Queue()

    def pump():
        try:
            for raw in iter(proc.stdout.readline, b""):
                lines.put(raw.decode("utf-8", "replace").rstrip("\r\n"))
        except Exception:
            pass
        lines.put(None)

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    out = []
    started = time.time()
    code = None
    ended = False
    while not ended:
        try:
            line = lines.get(timeout=CHECK_PAUSE_S)
        except queue.Empty:
            line = ""
            if cancelled():
                code = -2
            elif time.time() - started > timeout:
                code = -3
            if code is not None:
                _kill(proc)
                break
            continue
        if line is None:
            ended = True
        elif line:
            out.append(line)
            on_line(line)
    if code is None:
        try:
            code = proc.wait(timeout=30)
        except Exception:
            _kill(proc)
            code = -3
    return code, "\n".join(out)


def _kill(proc):
    try:
        proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=5)
    except Exception:
        pass


# --------------------------------------------------------------------------
# Why it failed
# --------------------------------------------------------------------------

# (i18n key suffix, words of pip's output), first match wins: the network comes before "no matching distribution" because pip says
# that too when it simply could not reach the index.
REASONS = (
    ("proxy", ("proxyerror", "proxy error", "tunnel connection failed")),
    ("ssl", ("sslerror", "certificate verify failed", "ssl: ")),
    ("offline", ("failed to establish a new connection", "getaddrinfo failed", "name or service not known", "network is unreachable",
                 "temporary failure in name resolution", "connection aborted", "connectionerror", "max retries exceeded",
                 "read timed out", "connect timeout", "connection timed out")),
    ("permission", ("permission denied", "errno 13", "access is denied", "winerror 5")),
    ("disk", ("no space left", "errno 28")),
    ("no_ensurepip", ("no module named ensurepip",)),
    ("no_pip", ("no module named pip",)),
    ("nomatch", ("no matching distribution", "could not find a version")),
)


def explain(code, output):
    """Short reason (i18n key suffix after "deps.err_") of a failed command, from its exit code and output."""
    if code == -1:
        return "start"
    if code == -2:
        return "stopped"
    if code == -3:
        return "timeout"
    text = (output or "").lower()
    for reason, words in REASONS:
        if any(word in text for word in words):
            return reason
    return "generic"


# --------------------------------------------------------------------------
# Installing
# --------------------------------------------------------------------------

DOWNLOAD_SIZE = re.compile(r"\(([\d.,]+\s*[kKMG]?B)\)")


def progress_of(line):
    """(fraction 0..1, i18n key suffix after "deps.step_", size text) read from one line of pip's output, or None for a line without news."""
    line = line.strip()                      # pip indents "Using cached" and "Downloading" under "Collecting"
    if line.startswith("Collecting"):
        return 0.15, "collect", ""
    if line.startswith(("Downloading", "Using cached")):
        found = DOWNLOAD_SIZE.search(line)
        return 0.35, "download", found.group(1) if found else ""
    if line.startswith("Installing collected packages"):
        return 0.85, "install", ""
    if line.startswith("Successfully installed"):
        return 1.0, "install", ""
    return None


def install_one(name, python, log, progress=None, cancelled=None, popen=subprocess.Popen):
    """Install pip ("pip") or a package into the user site of `python`, then make it importable here.

    log(text)               every line that is worth keeping for support: the command, pip's output, the verdict
    progress(fraction, key, size)   what a row shows while it works (key is the suffix after "deps.step_")
    Returns {"ok", "reason", "code", "restart"}: `reason` is the suffix after "deps.err_" when it failed; `restart` is true when the command
    worked but the package still cannot be imported in this session."""
    progress = progress or (lambda fraction, key, size: None)
    cancelled = cancelled or (lambda: False)
    if not python:
        log("no python found")
        return {"ok": False, "reason": "python", "code": -1, "restart": False}

    def heard(line):
        log(line)
        step = progress_of(line)
        if step:
            progress(*step)

    if name == PIP:
        attempts, timeout = ensurepip_commands(python), ENSUREPIP_TIMEOUT_S
        progress(0.1, "install", "")
    else:
        over_broken = broken_copy(name)
        if over_broken:
            log("a copy of {} {} is there but does not load: installing over it".format(name, distribution_version(name)))
        attempts, timeout = [pip_command(python, name, over_broken)], STEP_TIMEOUT_S
        progress(0.05, "collect", "")
    code, output = -1, ""
    for args in attempts:
        log("$ " + command_text(args))
        code, output = run_command(args, heard, cancelled, timeout, popen)
        if code == 0 or code in (-1, -2, -3):
            break
        log("exit code {}".format(code))
    if code != 0:
        reason = explain(code, output)
        log("failed: {}".format(reason))
        return {"ok": False, "reason": reason, "code": code, "restart": False}
    refresh_modules()
    if state_of(name):
        log("installed: {} {}".format(name, state_of(name)))
        progress(1.0, "install", "")
        return {"ok": True, "reason": "", "code": 0, "restart": False}
    log("the command worked but {} cannot be imported yet".format(name))
    return {"ok": True, "reason": "", "code": 0, "restart": True}
