"""Retraining of the AI model on the operator's feedback (no Qt, no Metashape).

Three things live here:

1. The storage settings: data folder (`storage_dir`), the "use my manual
   refinements" check box (`collect_enabled`), feedback file
   `<storage>/dataset/aigcp_feedback.jsonl` (written by review.py, read back here).
2. The model registry. The base model "Aero V1.0" is shipped with the tool
   (`models/checker.pt`, `models/spray.pt`); it is never modified nor deleted.
   The operator's models are stored in `<storage>/models/<id>/` (meta.json +
   one .pt file per type) and are called "Local 1.1", "Local 1.2"...: the
   number is not reused, the name is not changed. A local model may carry
   only one type: the other type falls back to the base weights.
3. The launch of the training (Training class). Like the detection, it runs
   in the external Python (the one that has torch and ultralytics), never in Metashape:
   `aigcp_train.py` reads `train.json`, writes `progress.json` then `result.json`.

Hidden settings (settings.json, section `aigcp`):
  storage_dir       data folder (default: the machine's settings folder)
  collect_feedback  true/false: record the review feedback (default true)
  model             identifier of the active model (default: the base one)

The retraining ALWAYS starts from the base weights and from ALL the recorded feedback
(not from the last local model): two trainings on the same data give the
same kind of model, and the errors of one trial do not accumulate.
"""

import datetime
import json
import os
import shutil
import subprocess
import time

from ... import config, journal

SECTION = "aigcp"
TYPES = ("checker", "spray")
# Names written by earlier versions (settings, feedback log, file names of the local models): still read.
LEGACY_TYPES = {"damier": "checker", "bombe": "spray"}
BASE_ID = "aero-1.0"
BASE_NAME = "Aero V1.0"
LOCAL_PREFIX = "local-1."
LOCAL_NAME = "Local 1.{}"
# Positives (refined or validated points) required, per type, to train that type.
MIN_POSITIVES = 30
DEFAULT_EPOCHS = 30
TRAIN_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aigcp_train.py")
LOCK_STALE_S = 12 * 3600
KEEP_RUNS = 3

FEEDBACK_FILE = "aigcp_feedback.jsonl"
POSITIVE_ACTIONS = ("refined", "accepted")


# --------------------------------------------------------------------------
# Type names
# --------------------------------------------------------------------------

def canonical_type(value):
    """Current name of a type; a name written by an earlier version is translated."""
    return LEGACY_TYPES.get(value, value)


def legacy_name(kind):
    """Name an earlier version used for this type (the type itself if it never had another)."""
    for old, new in LEGACY_TYPES.items():
        if new == kind:
            return old
    return kind


def weights_file(folder, kind, ext=".pt"):
    """Path of the `kind` file in `folder`; a file saved under the earlier name is used when the current one is missing."""
    path = os.path.join(folder, kind + ext)
    if not os.path.isfile(path):
        old = os.path.join(folder, legacy_name(kind) + ext)
        if os.path.isfile(old):
            return old
    return path


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

def storage_dir():
    value = config.get(SECTION, "storage_dir")
    if isinstance(value, str) and value.strip():
        return value
    return config.settings_dir()


def set_storage_dir(path):
    """Store the data folder (empty string: back to the default folder)."""
    return config.put(SECTION, "storage_dir", str(path or "").strip())


def collect_enabled():
    value = config.get(SECTION, "collect_feedback")
    return value if isinstance(value, bool) else True


def set_collect(enabled):
    return config.put(SECTION, "collect_feedback", bool(enabled))


def feedback_path():
    return os.path.join(storage_dir(), "dataset", FEEDBACK_FILE)


def models_root():
    return os.path.join(storage_dir(), "models")


def runs_root():
    return os.path.join(storage_dir(), "training")


def _subdirs(root, name):
    try:
        return [item for item in os.listdir(os.path.join(root, name))
                if os.path.isdir(os.path.join(root, name, item))]
    except OSError:
        return []


def storage_summary(root=None):
    """What a storage folder contains: annotations, local models, trials.

    {"annotations": lines of the feedback file, "models": local models,
     "detections": detection passes kept, "trainings": training trials}.
    """
    root = root or storage_dir()
    lines = 0
    try:
        with open(os.path.join(root, "dataset", FEEDBACK_FILE), "rb") as handle:
            lines = sum(1 for line in handle if line.strip())
    except OSError:
        pass
    return {"annotations": lines,
            "models": sum(1 for name in _subdirs(root, "models") if _number(name) is not None),
            "detections": len(_subdirs(root, "aigcp")),
            "trainings": len(_subdirs(root, "training"))}


def copy_storage(old, new):
    """Copy the annotations and the local models from `old` to `new` (nothing is deleted).

    Returns the list of the subfolders copied. A subfolder already present in `new`
    is never overwritten. Raises OSError if the copy fails.
    """
    copied = []
    if not old or not new or os.path.abspath(old) == os.path.abspath(new):
        return copied
    for name in ("dataset", "models"):
        source, target = os.path.join(old, name), os.path.join(new, name)
        if os.path.isdir(source) and not os.path.exists(target):
            shutil.copytree(source, target)
            copied.append(name)
    return copied


# --------------------------------------------------------------------------
# Recorded feedback
# --------------------------------------------------------------------------

def latest_events(path=None):
    """Last event of each (run, marker, photo), "restored" left out.

    Returns a list of dicts (file order). An unreadable line or one from another
    schema is ignored. "restored" cancels the earlier "rejected": no example is
    drawn from it.
    """
    last = {}
    order = []
    try:
        with open(path or feedback_path(), "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict) or row.get("schema") != 1:
                    continue
                if row.get("type") in LEGACY_TYPES:
                    row["type"] = LEGACY_TYPES[row["type"]]
                key = (row.get("run"), row.get("marker"), row.get("camera"))
                if key not in last:
                    order.append(key)
                last[key] = row
    except OSError:
        return []
    return [last[key] for key in order if last[key].get("action") != "restored"]


def _empty_counts():
    return {"samples": 0, "positives": 0, "negatives": 0, "refined": 0, "accepted": 0, "rejected": 0}


def feedback_counts(path=None):
    """{"checker": {...}, "spray": {...}}: usable examples per type according to the feedback."""
    out = {kind: _empty_counts() for kind in TYPES}
    for row in latest_events(path):
        counts = out.get(row.get("type"))
        action = row.get("action")
        if counts is None or action not in ("refined", "accepted", "rejected"):
            continue
        counts[action] += 1
        counts["samples"] += 1
        counts["negatives" if action == "rejected" else "positives"] += 1
    return out


# --------------------------------------------------------------------------
# Model registry
# --------------------------------------------------------------------------

def _base_dir():
    from . import aigcp
    return aigcp.models_dir()


def base_weights(kind):
    return weights_file(_base_dir(), kind)


def _number(model_id):
    """Number of a local identifier ("local-1.4" -> 4), or None."""
    if not isinstance(model_id, str) or not model_id.startswith(LOCAL_PREFIX):
        return None
    tail = model_id[len(LOCAL_PREFIX):]
    return int(tail) if tail.isdigit() else None


def _read_meta(folder):
    try:
        with open(os.path.join(folder, "meta.json"), "r", encoding="utf-8") as handle:
            meta = json.load(handle)
        return meta if isinstance(meta, dict) else None
    except Exception:
        return None


def list_models():
    """Available models: the base one first, then the local ones by increasing number.

    Each model: {id, name, created (ISO or None), base (bool), types (list of the
    types it carries), path (folder), metrics (dict, may be empty)}.
    """
    base_types = [kind for kind in TYPES if os.path.isfile(base_weights(kind))]
    out = [{"id": BASE_ID, "name": BASE_NAME, "created": None, "base": True,
            "types": base_types, "path": _base_dir(), "metrics": {}}]
    found = []
    try:
        names = os.listdir(models_root())
    except OSError:
        names = []
    for name in names:
        number = _number(name)
        folder = os.path.join(models_root(), name)
        if number is None or not os.path.isdir(folder):
            continue
        meta = _read_meta(folder) or {}
        types = [kind for kind in TYPES if os.path.isfile(weights_file(folder, kind))]
        if not types:
            continue
        found.append((number, {
            "id": name, "name": meta.get("name") or LOCAL_NAME.format(number),
            "created": meta.get("created"), "base": False, "types": types,
            "path": folder, "metrics": {canonical_type(k): v for k, v in (meta.get("types") or {}).items()}}))
    out.extend(model for _number_, model in sorted(found, key=lambda pair: pair[0]))
    return out


def get_model(model_id):
    for model in list_models():
        if model["id"] == model_id:
            return model
    return None


def active_model():
    """Identifier of the active model; the base one if the stored choice has disappeared."""
    wanted = config.get(SECTION, "model")
    if isinstance(wanted, str) and get_model(wanted) is not None:
        return wanted
    return BASE_ID


def set_active_model(model_id):
    """Choose the model used by the detection. Returns False if it does not exist."""
    if get_model(model_id) is None:
        return False
    return config.put(SECTION, "model", model_id)


def revert_to_base():
    return config.put(SECTION, "model", BASE_ID)


def weights_for(kind):
    """Weights to use for this type: those of the active model, otherwise the base ones."""
    model_id = active_model()
    if model_id != BASE_ID:
        path = weights_file(os.path.join(models_root(), model_id), kind)
        if os.path.isfile(path):
            return path
    return base_weights(kind)


def installer_path():
    """File to double-click to install the training tools (torch, ultralytics, onnx)."""
    return os.path.join(config.ROOT, "install", "install_training.bat")


def missing_tools(python=None):
    """Training modules missing from the external Python: [] if everything is there (quick read, without importing them).

    Lists ["python"] if this Python does not exist; ["?"] if the check itself fails.
    """
    if python is None:
        from . import aigcp
        python = aigcp.python_path()
    if not os.path.isfile(python):
        return ["python"]
    code = ("import importlib.util as u, sys; m = [n for n in ('torch', 'ultralytics', 'onnx') "
            "if u.find_spec(n) is None]; print(','.join(m)); sys.exit(1 if m else 0)")
    try:
        done = subprocess.run([python, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return ["?"]
    if done.returncode == 0:
        return []
    names = [name for name in done.stdout.decode("utf-8", "replace").strip().split(",") if name]
    return names or ["?"]


def delete_model(model_id):
    """Delete a local model. Refuses the base one (returns False). The number is not given back."""
    if model_id == BASE_ID or _number(model_id) is None:
        return False
    folder = os.path.join(models_root(), model_id)
    if not os.path.isdir(folder):
        return False
    shutil.rmtree(folder, ignore_errors=True)
    if os.path.isdir(folder):
        return False
    if config.get(SECTION, "model") == model_id:
        revert_to_base()
    return True


def _counter_path():
    return os.path.join(models_root(), "counter.json")


def _next_number():
    """Number of the next local model: never reused, even after a deletion."""
    highest = 0
    try:
        with open(_counter_path(), "r", encoding="utf-8") as handle:
            highest = int(json.load(handle).get("last", 0))
    except Exception:
        highest = 0
    for name in (os.listdir(models_root()) if os.path.isdir(models_root()) else []):
        number = _number(name)
        if number is not None:
            highest = max(highest, number)
    return highest + 1


def _store_counter(number):
    try:
        with open(_counter_path(), "w", encoding="utf-8") as handle:
            json.dump({"last": number}, handle)
    except OSError:
        pass


def register_model(weights, metrics=None, base_id=BASE_ID):
    """Store weights in a new local model. `weights`: {type: .pt path}.

    Returns the model record (as list_models). The files are copied: the
    source (working folder) can then be deleted.
    """
    os.makedirs(models_root(), exist_ok=True)
    number = _next_number()
    model_id = LOCAL_PREFIX + str(number)
    folder = os.path.join(models_root(), model_id)
    os.makedirs(folder)
    try:
        for kind, path in weights.items():
            shutil.copy2(path, os.path.join(folder, kind + ".pt"))
            onnx = os.path.splitext(path)[0] + ".onnx"       # export made by aigcp_train.py (may be missing)
            if os.path.isfile(onnx):
                shutil.copy2(onnx, os.path.join(folder, kind + ".onnx"))
        meta = {"id": model_id, "name": LOCAL_NAME.format(number), "base": base_id,
                "created": datetime.datetime.now().isoformat(timespec="seconds"),
                "types": metrics or {}}
        with open(os.path.join(folder, "meta.json"), "w", encoding="utf-8") as handle:
            json.dump(meta, handle, ensure_ascii=False, indent=1)
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    _store_counter(number)
    return get_model(model_id)


# --------------------------------------------------------------------------
# Training
# --------------------------------------------------------------------------

def _lock_path():
    return os.path.join(runs_root(), "training.lock")


def _pid_alive(pid):
    if not pid:
        return False
    if os.name == "nt":
        try:
            import ctypes
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))   # QUERY_LIMITED_INFORMATION
            if not handle:
                return False
            code = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            ctypes.windll.kernel32.CloseHandle(handle)
            return bool(ok) and code.value == 259          # STILL_ACTIVE
        except Exception:
            return True
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def is_training():
    """Is a training running (in this tool or another window)?"""
    try:
        with open(_lock_path(), "r", encoding="utf-8") as handle:
            lock = json.load(handle)
    except Exception:
        return False
    if time.time() - float(lock.get("ts", 0)) > LOCK_STALE_S:
        return False
    return _pid_alive(lock.get("pid"))


def _write_lock(pid):
    os.makedirs(runs_root(), exist_ok=True)
    with open(_lock_path(), "w", encoding="utf-8") as handle:
        json.dump({"pid": pid, "ts": time.time()}, handle)


def _drop_lock():
    try:
        os.remove(_lock_path())
    except OSError:
        pass


def eligible_kinds(kinds=None, min_positives=None, path=None):
    """Types that have enough positives to be trained, according to the feedback."""
    need = MIN_POSITIVES if min_positives is None else int(min_positives)
    counts = feedback_counts(path)
    return [kind for kind in (kinds or TYPES)
            if kind in counts and counts[kind]["positives"] >= need
            and os.path.isfile(base_weights(kind))]


def _kill_tree(process):
    """Stop the subprocess and its children (the ultralytics workers)."""
    if process is None or process.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=20)
        else:
            process.terminate()
    except Exception:
        pass
    try:
        process.wait(timeout=20)
    except Exception:
        try:
            process.kill()
        except OSError:
            pass


class Training(object):
    """One training: start(), then poll() until the result is received.

    poll() returns None while it runs, then a dict, always the same one:
        {"ok": bool, "message": str, "model_id": str or None, "name": str or None,
         "kinds_trained": [types], "metrics": {type: {...}}, "warnings": [str],
         "seconds": float, "device": str}
    A failure or a cancellation leaves no model (the working folder is deleted).
    `min_positives`, `python`, `script` are only used by the tests.
    """

    def __init__(self, kinds=None, epochs=None, min_positives=None, python=None,
                 script=None, replay_dir=None):
        self.kinds = list(kinds) if kinds else list(TYPES)
        self.epochs = int(epochs) if epochs else DEFAULT_EPOCHS
        self.min_positives = MIN_POSITIVES if min_positives is None else int(min_positives)
        self._python = python
        self._script = script or TRAIN_SCRIPT
        self._replay = replay_dir
        self.run_dir = None
        self.started = None
        self._process = None
        self._log = None
        self._result = None
        self._types = []

    # -- launch -----------------------------------------------------------

    def start(self):
        from . import aigcp
        try:
            self._start(aigcp)
        except Exception as exc:
            journal.write("Retraining: cannot start: {!r}".format(exc))
            self._finish(False, "failed", str(exc))
        return self

    def _start(self, aigcp):
        if is_training():
            return self._finish(False, "busy")
        python = self._python or aigcp.python_path()
        if not os.path.isfile(python):
            return self._finish(False, "no_python", python)
        if not os.path.isfile(self._script):
            return self._finish(False, "no_script", self._script)
        events = latest_events()
        self._types = eligible_kinds(self.kinds, self.min_positives)
        if not self._types:
            return self._finish(False, "no_data")
        self.run_dir = self._new_run_dir()
        self.started = time.time()
        events_path = os.path.join(self.run_dir, "events.jsonl")
        with open(events_path, "w", encoding="utf-8", newline="\n") as handle:
            for row in events:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        work = {
            "version": 1, "types": self._types, "events": events_path,
            "base_weights": {kind: base_weights(kind) for kind in self._types},
            "out_dir": os.path.join(self.run_dir, "out"), "work_dir": os.path.join(self.run_dir, "work"),
            "epochs": self.epochs, "device": "auto", "min_positives": self.min_positives,
            "replay_dir": self._replay,
        }
        with open(os.path.join(self.run_dir, "train.json"), "w", encoding="utf-8") as handle:
            json.dump(work, handle, ensure_ascii=False, indent=1)
        self._log = open(os.path.join(self.run_dir, "train.log"), "wb")
        self._process = subprocess.Popen(
            [python, "-I", self._script, "--work", os.path.join(self.run_dir, "train.json"),
             "--out", os.path.join(self.run_dir, "result.json")],
            stdout=self._log, stderr=subprocess.STDOUT, env=aigcp.child_env(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), cwd=self.run_dir)
        _write_lock(self._process.pid)

    @staticmethod
    def _new_run_dir():
        base = runs_root()
        os.makedirs(base, exist_ok=True)
        try:
            old = sorted(name for name in os.listdir(base) if os.path.isdir(os.path.join(base, name)))
            for name in old[:max(0, len(old) - (KEEP_RUNS - 1))]:
                shutil.rmtree(os.path.join(base, name), ignore_errors=True)
        except OSError:
            pass
        path, suffix = os.path.join(base, time.strftime("%Y%m%d_%H%M%S")), 0
        stamp = path
        while os.path.exists(path):
            suffix += 1
            path = "{}_{}".format(stamp, suffix)
        os.makedirs(path)
        return path

    # -- follow-up --------------------------------------------------------

    def running(self):
        return self._result is None and self._process is not None and self._process.poll() is None

    def progress(self):
        """(fraction 0..1 or None, short text) according to progress.json."""
        if self.run_dir is None:
            return None, ""
        try:
            with open(os.path.join(self.run_dir, "progress.json"), "r", encoding="utf-8") as handle:
                data = json.load(handle)
            total = max(1, int(data.get("kinds_total") or 1))
            index = int(data.get("kind_index") or 0)
            epochs = max(1, int(data.get("epochs") or 1))
            epoch = int(data.get("epoch") or 0)
            stage = data.get("stage") or "train"
            kind = data.get("type") or ""
            if stage == "train":
                return min(1.0, (index + epoch / float(epochs)) / total), \
                    "{} epoch {}/{}".format(kind, epoch, epochs)
            done = index + 1 if stage == "eval" else index
            return min(1.0, done / float(total)), "{} {}".format(kind, stage).strip()
        except Exception:
            return None, "starting"

    def poll(self):
        if self._result is not None:
            return self._result
        if self._process is None:
            return None
        code = self._process.poll()
        if code is None:
            return None
        try:
            self._collect(code)
        except Exception as exc:
            journal.write("Retraining: unreadable result: {!r}".format(exc))
            self._finish(False, "failed", self._log_tail())
        return self._result

    def _collect(self, code):
        if self._log is not None:
            self._log.close()
            self._log = None
        path = os.path.join(self.run_dir, "result.json")
        data = {}
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        if code != 0 or not data.get("ok"):
            return self._finish(False, "failed", data.get("message") or self._log_tail(),
                                seconds=data.get("seconds"), device=data.get("device"))
        weights = {}
        for kind, info in (data.get("types") or {}).items():
            if info.get("ok") and os.path.isfile(os.path.join(self.run_dir, "out", kind + ".pt")):
                weights[kind] = os.path.join(self.run_dir, "out", kind + ".pt")
        if not weights:
            return self._finish(False, "failed", data.get("message") or "no weights")
        model = register_model(weights, metrics=data["types"])
        warnings = [info["warning"] for info in data["types"].values() if info.get("warning")]
        self._finish(True, "done", model=model, kinds=sorted(weights), metrics=data["types"],
                     warnings=warnings, seconds=data.get("seconds"), device=data.get("device"))
        shutil.rmtree(self.run_dir, ignore_errors=True)

    def _log_tail(self, chars=400):
        try:
            with open(os.path.join(self.run_dir, "train.log"), "rb") as handle:
                return handle.read().decode("utf-8", "replace")[-chars:].strip()
        except Exception:
            return ""

    def cancel(self):
        """Stop the training, delete the working folder. No model is created."""
        if self._result is not None:
            return
        _kill_tree(self._process)
        if self._log is not None:
            try:
                self._log.close()
            except OSError:
                pass
            self._log = None
        self._finish(False, "cancelled")

    def _finish(self, ok, code, detail="", model=None, kinds=None, metrics=None, warnings=None,
                seconds=None, device=None):
        """Set the result, release the lock; on failure, delete the leftovers."""
        if self._process is not None:
            _drop_lock()
        if not ok and self.run_dir:
            keep = code == "failed"          # train.log is kept to understand what happened
            if not keep:
                shutil.rmtree(self.run_dir, ignore_errors=True)
            else:
                shutil.rmtree(os.path.join(self.run_dir, "out"), ignore_errors=True)
                shutil.rmtree(os.path.join(self.run_dir, "work"), ignore_errors=True)
        self._result = {
            "ok": ok, "code": code, "message": _MESSAGES[code].format(detail=detail), "model_id": model["id"] if model else None,
            "name": model["name"] if model else None, "kinds_trained": kinds or [],
            "metrics": metrics or {}, "warnings": warnings or [],
            "seconds": float(seconds if seconds is not None else
                             (time.time() - self.started if self.started else 0.0)),
            "device": device or "",
        }


# Fallback texts: the interface replaces them with its own texts according to `code`.
_MESSAGES = {
    "done": "Training finished",
    "busy": "A training is already running",
    "no_python": "Python with torch not found: {detail}",
    "no_script": "Training script not found: {detail}",
    "no_data": "Not enough annotated examples to train",
    "cancelled": "Training cancelled",
    "failed": "Training failed: {detail}",
}
