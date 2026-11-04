"""Reentrainement du modele IA sur les retours de l'operateur (sans Qt, sans Metashape).

Trois choses vivent ici :

1. Les reglages de stockage : dossier des donnees (`storage_dir`), case « utiliser
   mes affinages manuels » (`collect_enabled`), fichier de retours
   `<storage>/dataset/aigcp_feedback.jsonl` (ecrit par review.py, relu ici).
2. Le registre des modeles. Le modele de base « Aero V1.0 » est livre avec l'outil
   (`models/damier.pt`, `models/bombe.pt`), on ne le modifie ni ne le supprime jamais.
   Les modeles de l'operateur sont ranges dans `<storage>/models/<id>/` (meta.json +
   un fichier .pt par type) et s'appellent « Local 1.1 », « Local 1.2 »... : le
   numero ne se reutilise pas, le nom ne se change pas. Un modele local peut ne
   porter qu'un seul type : l'autre type retombe sur les poids de base.
3. Le lancement de l'entrainement (classe Training). Comme la detection, il tourne
   dans le Python externe (celui qui a torch et ultralytics), jamais dans Metashape :
   `aigcp_train.py` lit `train.json`, ecrit `progress.json` puis `result.json`.

Reglages caches (settings.json, section `aigcp`) :
  storage_dir       dossier des donnees (defaut : le dossier des reglages du poste)
  collect_feedback  true/false : enregistrer les retours de la revue (defaut true)
  model             identifiant du modele actif (defaut : celui de base)

Le reentrainement part TOUJOURS des poids de base et de TOUS les retours enregistres
(pas du dernier modele local) : deux entrainements sur les memes donnees donnent le
meme genre de modele, et les erreurs d'un essai ne se cumulent pas.
"""

import datetime
import json
import os
import shutil
import subprocess
import time

from ... import config, journal

SECTION = "aigcp"
TYPES = ("damier", "bombe")
BASE_ID = "aero-1.0"
BASE_NAME = "Aero V1.0"
LOCAL_PREFIX = "local-1."
LOCAL_NAME = "Local 1.{}"
# Positifs (points affines ou valides) a avoir, par type, pour entrainer ce type.
MIN_POSITIVES = 30
DEFAULT_EPOCHS = 30
TRAIN_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aigcp_train.py")
LOCK_STALE_S = 12 * 3600
KEEP_RUNS = 3

FEEDBACK_FILE = "aigcp_feedback.jsonl"
POSITIVE_ACTIONS = ("refined", "accepted")


# --------------------------------------------------------------------------
# Reglages
# --------------------------------------------------------------------------

def storage_dir():
    value = config.get(SECTION, "storage_dir")
    if isinstance(value, str) and value.strip():
        return value
    return config.settings_dir()


def set_storage_dir(path):
    """Memorise le dossier des donnees (chaine vide : retour au dossier par defaut)."""
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
    """Ce que contient un dossier de stockage : annotations, modeles locaux, essais.

    {"annotations": lignes du fichier de retours, "models": modeles locaux,
     "detections": passes de detection gardees, "trainings": essais d'entrainement}.
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
    """Copie les annotations et les modeles locaux de `old` vers `new` (rien n'est efface).

    Renvoie la liste des sous-dossiers copies. Un sous-dossier deja present dans `new`
    n'est jamais ecrase. Leve OSError si la copie echoue.
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
# Retours enregistres
# --------------------------------------------------------------------------

def latest_events(path=None):
    """Dernier evenement de chaque (run, marqueur, photo), « restored » ecarte.

    Renvoie une liste de dicts (ordre du fichier). Une ligne illisible ou d'un autre
    schema est ignoree. « restored » annule le « rejected » d'avant : on n'en tire
    aucun exemple.
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
    """{"damier": {...}, "bombe": {...}} : exemples utilisables par type d'apres les retours."""
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
# Registre des modeles
# --------------------------------------------------------------------------

def _base_dir():
    from . import aigcp
    return aigcp.models_dir()


def base_weights(kind):
    return os.path.join(_base_dir(), kind + ".pt")


def _number(model_id):
    """Numero d'un identifiant local (« local-1.4 » -> 4), ou None."""
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
    """Modeles disponibles : celui de base d'abord, puis les locaux par numero croissant.

    Chaque modele : {id, name, created (ISO ou None), base (bool), types (liste des
    types qu'il porte), path (dossier), metrics (dict, peut etre vide)}.
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
        types = [kind for kind in TYPES if os.path.isfile(os.path.join(folder, kind + ".pt"))]
        if not types:
            continue
        found.append((number, {
            "id": name, "name": meta.get("name") or LOCAL_NAME.format(number),
            "created": meta.get("created"), "base": False, "types": types,
            "path": folder, "metrics": meta.get("types") or {}}))
    out.extend(model for _number_, model in sorted(found, key=lambda pair: pair[0]))
    return out


def get_model(model_id):
    for model in list_models():
        if model["id"] == model_id:
            return model
    return None


def active_model():
    """Identifiant du modele actif ; celui de base si le choix memorise a disparu."""
    wanted = config.get(SECTION, "model")
    if isinstance(wanted, str) and get_model(wanted) is not None:
        return wanted
    return BASE_ID


def set_active_model(model_id):
    """Choisit le modele utilise par la detection. Renvoie False s'il n'existe pas."""
    if get_model(model_id) is None:
        return False
    return config.put(SECTION, "model", model_id)


def revert_to_base():
    return config.put(SECTION, "model", BASE_ID)


def weights_for(kind):
    """Poids a utiliser pour ce type : ceux du modele actif, sinon ceux de base."""
    model_id = active_model()
    if model_id != BASE_ID:
        path = os.path.join(models_root(), model_id, kind + ".pt")
        if os.path.isfile(path):
            return path
    return base_weights(kind)


def installer_path():
    """Fichier a double-cliquer pour installer les outils d'entrainement (torch, ultralytics, onnx)."""
    return os.path.join(config.ROOT, "install", "installer_entrainement.bat")


def missing_tools(python=None):
    """Modules d'entrainement absents du Python externe : [] si tout y est (lecture rapide, sans les importer).

    Liste ["python"] si ce Python n'existe pas ; ["?"] si la verification elle-meme echoue.
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
    """Supprime un modele local. Refuse celui de base (renvoie False). Le numero n'est pas rendu."""
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
    """Numero du prochain modele local : jamais reutilise, meme apres suppression."""
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
    """Range des poids dans un nouveau modele local. `weights` : {type: chemin .pt}.

    Renvoie la fiche du modele (comme list_models). Les fichiers sont copies : la
    source (dossier de travail) peut ensuite etre supprimee.
    """
    os.makedirs(models_root(), exist_ok=True)
    number = _next_number()
    model_id = LOCAL_PREFIX + str(number)
    folder = os.path.join(models_root(), model_id)
    os.makedirs(folder)
    try:
        for kind, path in weights.items():
            shutil.copy2(path, os.path.join(folder, kind + ".pt"))
            onnx = os.path.splitext(path)[0] + ".onnx"       # export fait par aigcp_train.py (peut manquer)
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
# Entrainement
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
    """Un entrainement tourne-t-il (dans cet outil ou une autre fenetre) ?"""
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
    """Types qui ont assez de positifs pour etre entraines, d'apres les retours."""
    need = MIN_POSITIVES if min_positives is None else int(min_positives)
    counts = feedback_counts(path)
    return [kind for kind in (kinds or TYPES)
            if kind in counts and counts[kind]["positives"] >= need
            and os.path.isfile(base_weights(kind))]


def _kill_tree(process):
    """Arrete le sous-processus et ses enfants (les workers d'ultralytics)."""
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
    """Un entrainement : start(), puis poll() jusqu'a recevoir le resultat.

    poll() renvoie None tant que ca tourne, puis un dict, toujours le meme :
        {"ok": bool, "message": str, "model_id": str ou None, "name": str ou None,
         "kinds_trained": [types], "metrics": {type: {...}}, "warnings": [str],
         "seconds": float, "device": str}
    Un echec ou une annulation ne laisse aucun modele (le dossier de travail est efface).
    `min_positives`, `python`, `script` ne servent qu'aux tests.
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

    # -- lancement --------------------------------------------------------

    def start(self):
        from . import aigcp
        try:
            self._start(aigcp)
        except Exception as exc:
            journal.write("Reentrainement : lancement impossible : {!r}".format(exc))
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

    # -- suivi ------------------------------------------------------------

    def running(self):
        return self._result is None and self._process is not None and self._process.poll() is None

    def progress(self):
        """(fraction 0..1 ou None, texte court) d'apres progress.json."""
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
            journal.write("Reentrainement : resultat illisible : {!r}".format(exc))
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
        """Arrete l'entrainement, efface le dossier de travail. Aucun modele n'est cree."""
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
        """Fixe le resultat, rend le verrou ; hors succes, efface les restes."""
        if self._process is not None:
            _drop_lock()
        if not ok and self.run_dir:
            keep = code == "failed"          # on garde train.log pour comprendre
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


# Textes de secours (anglais) : l'interface les remplace par ses propres traductions
# d'apres `code`.
_MESSAGES = {
    "done": "Training finished",
    "busy": "A training is already running",
    "no_python": "Python with torch not found: {detail}",
    "no_script": "Training script not found: {detail}",
    "no_data": "Not enough annotated examples to train",
    "cancelled": "Training cancelled",
    "failed": "Training failed: {detail}",
}
