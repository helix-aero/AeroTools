"""Detection engines installed in the AIGCP Python (no Qt, no Metashape).

Two engines, chosen by the operator (`engine` setting of the `aigcp` section):
  light    ONNX Runtime on the CPU: about 100 MB, fast, works on any PC;
  precise  torch + ultralytics: the original engine (about 3 GB), graphics card if present.
  auto     (default) the precise one if installed, otherwise the light one.
Both give the same positions to within a few hundredths of a pixel (measured on 569 targets).

The retraining tools (torch, ultralytics, onnx) are those of the precise engine plus the
onnx package: the installer sets them up for the user (`InstallJob`), in a console
window that shows the progress.

Nothing is imported here: the external Python is queried through a short subprocess.
"""

import os
import subprocess
import time

from ... import config

SECTION = "aigcp"
CHOICES = ("auto", "light", "precise")
DEFAULT = "auto"
LIGHT = ("onnxruntime", "cv2", "numpy")
PRECISE = ("torch", "ultralytics")
TRAINING = ("torch", "ultralytics", "onnx")
PROBE = ("import importlib.util as u; names = %r; "
         "print(','.join(n for n in names if u.find_spec(n) is not None))" % (sorted(set(LIGHT + TRAINING)),))
CACHE_SECONDS = 20.0

# Text keys (aerotools/lang): written out in full here so that the catalog stays checkable.
CHOICE_TEXT = {"auto": "settings.engine_auto", "light": "settings.engine_light", "precise": "settings.engine_precise"}
NAME_TEXT = {"light": "settings.engine_name_light", "precise": "settings.engine_name_precise",
             "training": "settings.engine_name_training"}
WHAT_TEXT = {"light": "engine.what_light", "precise": "engine.what_precise", "training": "engine.what_training"}
MISSING_TEXT = {"light": "aigcp.no_engine_light", "precise": "aigcp.no_engine_precise"}

_cache = {"key": None, "when": 0.0, "value": None}


def selected():
    value = config.get(SECTION, "engine")
    return value if value in CHOICES else DEFAULT


def set_selected(value):
    if value in CHOICES:
        config.put(SECTION, "engine", value)


def python_path():
    from . import aigcp
    return aigcp.python_path()


def default_python():
    sub = ("Scripts", "python.exe") if os.name == "nt" else ("bin", "python")
    return os.path.join(os.path.expanduser("~"), "AIGCP", "_env", *sub)


def can_install():
    """The installer can only fill the default environment (not a hand-picked Python)."""
    return (os.name == "nt" and os.path.isfile(script_path())
            and os.path.normcase(os.path.normpath(python_path())) == os.path.normcase(os.path.normpath(default_python())))


def script_path():
    return os.path.join(config.ROOT, "install", "install_all.ps1")


def status(python=None, refresh=False):
    """{"python", "light", "precise", "training"}: booleans. Cached for a few seconds.

    A Python that is missing or does not answer gives all False. Never raises."""
    python = python or python_path()
    now = time.time()
    if (not refresh and _cache["key"] == python and _cache["value"] is not None
            and now - _cache["when"] < CACHE_SECONDS):
        return dict(_cache["value"])
    result = {"python": False, "light": False, "precise": False, "training": False}
    if os.path.isfile(python):
        result["python"] = True
        try:
            done = subprocess.run([python, "-c", PROBE], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            found = set(done.stdout.decode("utf-8", "replace").strip().split(","))
            result["light"] = all(name in found for name in LIGHT)
            result["precise"] = all(name in found for name in PRECISE)
            result["training"] = all(name in found for name in TRAINING)
        except Exception:
            pass
    _cache.update(key=python, when=now, value=dict(result))
    return result


def forget():
    """Forgets the cache: to be called after an installation."""
    _cache.update(key=None, when=0.0, value=None)


def resolve(choice=None, state=None):
    """Engine actually used: "light" or "precise", or None if nothing needed is installed."""
    choice = choice or selected()
    state = state or status()
    if choice == "light":
        return "light" if state["light"] else None
    if choice == "precise":
        return "precise" if state["precise"] else None
    if state["precise"]:
        return "precise"
    return "light" if state["light"] else None


def missing(choice=None, state=None):
    """What must be installed for the choice to work: "light", "precise" or None (nothing to do)."""
    choice = choice or selected()
    state = state or status()
    if resolve(choice, state) is not None:
        return None
    return "precise" if choice == "precise" else "light"


def runner_engine(choice=None, state=None):
    """Value of the `engine` field of work.json: "onnx" or "torch" (the runner chooses by itself if unknown)."""
    which = resolve(choice, state)
    return {"light": "onnx", "precise": "torch"}.get(which, "auto")


class InstallJob(object):
    """Launches the installer in a visible console; `poll()` returns None while it runs."""

    def __init__(self, engine=None, training=False):
        self.engine = engine or "none"
        self.training = bool(training)
        self.process = None

    def command(self):
        cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script_path(),
               "-Root", config.ROOT, "-Yes", "-Engine", self.engine, "-PauseOnError"]
        if self.training:
            cmd.append("-Training")
        return cmd

    def start(self):
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        self.process = subprocess.Popen(self.command(), creationflags=flags, cwd=config.ROOT)
        return self

    def poll(self):
        """None while the installation is still running; otherwise {"ok": bool, "code": exit code}."""
        if self.process is None:
            return {"ok": False, "code": -1}
        code = self.process.poll()
        if code is None:
            return None
        forget()
        return {"ok": code == 0, "code": code}

    def cancel(self):
        try:
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
        except Exception:
            pass
        forget()


def describe(state=None):
    """Status lines for the interface: [(key, installed)] in the order light, precise, retraining."""
    state = state or status()
    return [("light", state["light"]), ("precise", state["precise"]), ("training", state["training"])]

