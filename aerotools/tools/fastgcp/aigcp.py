"""AIGCP dans FastGCP : proposer des pointages avec les modeles de detection.

Le calcul ne tourne JAMAIS dans le Python de Metashape. FastGCP ecrit un
`work.json`, lance `aigcp_runner.py` dans un Python a part (celui qui a torch et
ultralytics), puis relit `hits.json` (contrat decrit dans aigcp_runner.py). Le
panneau reste libre pendant ce temps : `poll()` est appele par le battement de
tool.py et pose les propositions quand le calcul est fini.

Une proposition est une projection NON posee a la main (`pinned=False`, point
bleu de Metashape). Elle ne remplace jamais un pointage de l'operateur, ne
compte pas dans les photos pointees (msapi.projection_count) et disparait d'un
clic (« Effacer les propositions ») ou se valide en cliquant la cible, ce qui la
remplace par un pointage normal.

Deux modeles, jamais melanges : damier et bombe. L'operateur choisit le type.

Reglages caches (settings.json, section `aigcp`) :
  python      chemin du Python qui a torch + ultralytics
              (defaut : variable AIGCP_PYTHON, puis ~/AIGCP/_env/Scripts/python.exe)
  models_dir  dossier des poids damier.pt et bombe.pt (defaut : <depot>/models)
  conf_damier, conf_bombe   seuils de confiance (defauts 0,25 et 0,30)
"""

import json
import math
import os
import shutil
import subprocess
import time

from ... import config, i18n, journal, msapi
from . import detection, photos

SECTION = "aigcp"
TYPES = ("damier", "bombe")
DEFAULT_CONF = {"damier": 0.25, "bombe": 0.30}
SCALES_M = (0.3, 0.5, 0.8)
MAX_PHOTOS = 200
# Une proposition doit rester pres de l'estimation du marqueur : au plus cette
# fraction de la taille du recadrage (la recherche elargie va jusqu'a 0,4).
MAX_SHIFT_FRACTION = 0.7
RUNNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aigcp_runner.py")
KEEP_RUNS = 5


# --------------------------------------------------------------------------
# Installation : Python externe, poids
# --------------------------------------------------------------------------

def models_dir():
    value = config.get(SECTION, "models_dir")
    if isinstance(value, str) and value.strip():
        return value
    return os.path.join(config.ROOT, "models")


def weights_path(kind):
    return os.path.join(models_dir(), kind + ".pt")


def python_path():
    value = config.get(SECTION, "python")
    if isinstance(value, str) and value.strip():
        return value
    value = os.environ.get("AIGCP_PYTHON")
    if value:
        return value
    sub = ("Scripts", "python.exe") if os.name == "nt" else ("bin", "python")
    return os.path.join(os.path.expanduser("~"), "AIGCP", "_env", *sub)


def conf_for(kind):
    value = config.get(SECTION, "conf_" + kind)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return DEFAULT_CONF[kind]
    return float(value) if 0.0 < value < 1.0 else DEFAULT_CONF[kind]


def check(kind):
    """(True, "") si la detection de ce type peut tourner, sinon (False, texte)."""
    if kind not in TYPES:
        return False, i18n.t("aigcp.bad_type", kind=kind)
    python = python_path()
    if not os.path.isfile(python):
        return False, i18n.t("aigcp.no_python", path=python)
    weights = weights_path(kind)
    if not os.path.isfile(weights):
        return False, i18n.t("aigcp.no_weights", kind=kind, path=weights)
    if not os.path.isfile(RUNNER):
        return False, i18n.t("aigcp.no_runner", path=RUNNER)
    return True, ""


# --------------------------------------------------------------------------
# Fichiers de travail
# --------------------------------------------------------------------------

def runs_dir():
    return os.path.join(config.settings_dir(), "aigcp")


def new_run_dir():
    """Dossier propre a cette detection ; garde les KEEP_RUNS derniers."""
    base = runs_dir()
    os.makedirs(base, exist_ok=True)
    try:
        old = sorted(name for name in os.listdir(base)
                     if os.path.isdir(os.path.join(base, name)))
        for name in old[:max(0, len(old) - (KEEP_RUNS - 1))]:
            shutil.rmtree(os.path.join(base, name), ignore_errors=True)
    except OSError:
        pass
    stamp = os.path.join(base, time.strftime("%Y%m%d_%H%M%S"))
    path, suffix = stamp, 0
    while os.path.exists(path):
        suffix += 1
        path = "{}_{}".format(stamp, suffix)
    os.makedirs(path)
    return path


def build_work(kind, items, conf=None):
    """Contenu de work.json. `items` : dicts id, image_path, x_est, y_est, px_per_m."""
    return {
        "version": 1,
        "type": kind,
        "weights": weights_path(kind),
        "conf": conf_for(kind) if conf is None else float(conf),
        "scales_m": list(SCALES_M),
        "device": "auto",
        "items": [
            {"id": it["id"], "image_path": it["image_path"],
             "x_est": float(it["x_est"]), "y_est": float(it["y_est"]),
             "px_per_m": float(it["px_per_m"])}
            for it in items
        ],
    }


def read_hits(path):
    """(liste de hits, liste d'erreurs) lue dans hits.json ; leve si illisible."""
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or not isinstance(data.get("hits"), list):
        raise ValueError("hits.json inattendu")
    return data["hits"], [str(e) for e in data.get("errors", [])]


def child_env():
    """Environnement du Python externe : sans les variables de celui de Metashape."""
    env = dict(os.environ)
    for name in ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONUSERBASE"):
        env.pop(name, None)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def accept(hit, item, conf):
    """Une detection est retenue si elle est trouvee, assez sure et pres de l'estimation."""
    try:
        if not hit.get("found") or float(hit["conf"]) < conf:
            return False
        crop = float(hit.get("crop_px") or 0)
        gap = math.hypot(float(hit["x"]) - item["x_est"], float(hit["y"]) - item["y_est"])
        return crop > 0 and gap <= MAX_SHIFT_FRACTION * crop
    except (KeyError, TypeError, ValueError):
        return False


# --------------------------------------------------------------------------
# Travail en arriere-plan
# --------------------------------------------------------------------------

class Job(object):
    """Un lancement du runner. `poll()` : None tant que ca tourne, puis (hits, erreurs)."""

    def __init__(self, kind, work, run_dir, python=None):
        self.kind = kind
        self.run_dir = run_dir
        self.work_path = os.path.join(run_dir, "work.json")
        self.hits_path = os.path.join(run_dir, "hits.json")
        self.log_path = os.path.join(run_dir, "runner.log")
        with open(self.work_path, "w", encoding="utf-8") as handle:
            json.dump(work, handle, ensure_ascii=False)
        self._python = python or python_path()
        self._process = None
        self._log = None
        self.started = time.time()

    def start(self):
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._log = open(self.log_path, "wb")
        self._process = subprocess.Popen(
            [self._python, "-I", RUNNER, "--work", self.work_path, "--out", self.hits_path],
            stdout=self._log, stderr=subprocess.STDOUT, env=child_env(),
            creationflags=flags, cwd=self.run_dir)
        return self

    def running(self):
        return self._process is not None and self._process.poll() is None

    def cancel(self):
        if self._process is not None and self._process.poll() is None:
            try:
                self._process.terminate()
            except OSError:
                pass
        self._close_log()

    def _close_log(self):
        if self._log is not None:
            try:
                self._log.close()
            except OSError:
                pass
            self._log = None

    def log_tail(self, chars=400):
        try:
            with open(self.log_path, "rb") as handle:
                return handle.read().decode("utf-8", "replace")[-chars:].strip()
        except OSError:
            return ""

    def poll(self):
        """None si en cours ; sinon (hits, erreurs). Leve RuntimeError si le runner a echoue."""
        if self._process is None:
            raise RuntimeError("detection non lancee")
        code = self._process.poll()
        if code is None:
            return None
        self._close_log()
        if code != 0 or not os.path.isfile(self.hits_path):
            raise RuntimeError("runner arrete (code {}) : {}".format(code, self.log_tail()))
        return read_hits(self.hits_path)

    def wait(self, timeout_s=600.0, step_s=0.2):
        end = time.time() + timeout_s
        while time.time() < end:
            result = self.poll()
            if result is not None:
                return result
            time.sleep(step_s)
        self.cancel()
        raise RuntimeError("delai depasse")


# --------------------------------------------------------------------------
# Photos a tester pour un marqueur
# --------------------------------------------------------------------------

def collect_items(chk, marker):
    """Photos sans pointage manuel ou le marqueur est attendu : liste d'items.

    Chaque item : id (cle de la photo), camera, image_path, x_est, y_est, px_per_m.
    Renvoie (items, nombre de photos ecartees faute d'estimation ou d'echelle).
    """
    cameras, _diag = msapi.cameras_seeing_marker(chk, marker)
    items, skipped = [], 0
    for camera in cameras:
        projection = msapi.get_projection(marker, camera)
        if projection is not None and projection[2]:
            continue
        estimate = msapi.marker_estimate(chk, marker, camera)
        ppm = msapi.marker_ground_scale(chk, marker, camera)
        path = msapi.camera_image_path(camera)
        if estimate is None or ppm is None or not path:
            skipped += 1
            continue
        items.append({"id": str(msapi.camera_key(camera)), "camera": camera,
                      "image_path": path, "x_est": estimate[0], "y_est": estimate[1],
                      "px_per_m": ppm})
        if len(items) >= MAX_PHOTOS:
            break
    return items, skipped


# --------------------------------------------------------------------------
# Detecteur (contrat de detection.py) : appel bloquant
# --------------------------------------------------------------------------

class AigcpDetector(detection.Detector):
    """Detecteur AIGCP d'un type de cible. `propose` attend la fin du calcul."""

    def __init__(self, kind):
        self.kind = kind
        self.name = "aigcp-" + kind

    def status(self):
        return check(self.kind)

    def propose(self, chunk, marker, cameras=None):
        ok, message = check(self.kind)
        if not ok:
            raise RuntimeError(message)
        items, _skipped = collect_items(chunk, marker)
        if cameras is not None:
            wanted = set(msapi.camera_key(cam) for cam in cameras)
            items = [it for it in items if msapi.camera_key(it["camera"]) in wanted]
        if not items:
            return []
        conf = conf_for(self.kind)
        job = Job(self.kind, build_work(self.kind, items, conf), new_run_dir()).start()
        hits, _errors = job.wait()
        by_id = {it["id"]: it for it in items}
        label = msapi.marker_label(marker)
        out = []
        for hit in hits:
            item = by_id.get(str(hit.get("id")))
            if item is not None and accept(hit, item, conf):
                out.append(detection.Proposal(label, msapi.camera_key(item["camera"]),
                                              hit["x"], hit["y"], hit["conf"], self.name))
        return out


# --------------------------------------------------------------------------
# Etat de la session : un seul calcul a la fois
# --------------------------------------------------------------------------

class _Running(object):
    def __init__(self, job, kind, marker, label, chunk_key, items):
        self.job, self.kind, self.marker, self.label = job, kind, marker, label
        self.chunk_key, self.items = chunk_key, items


def _status(state, text):
    photos.panel_call(state, "set_status", text)
    journal.write(text)


def busy(state):
    return getattr(state, "aigcp_run", None) is not None


def start(state, kind):
    """Lance la detection de `kind` sur les photos du marqueur courant. Ne leve jamais."""
    try:
        return _start(state, kind)
    except Exception as exc:
        _status(state, i18n.t("aigcp.failed"))
        journal.write("IA : lancement impossible : {!r}".format(exc))
        return False


def _start(state, kind):
    if busy(state):
        _status(state, i18n.t("aigcp.busy"))
        return False
    ok, message = check(kind)
    if not ok:
        _status(state, message)
        return False
    chk = msapi.chunk()
    label = state.active_label()
    marker = state.active_marker
    if chk is None or label is None or marker is None:
        _status(state, i18n.t("aigcp.no_marker"))
        return False
    items, skipped = collect_items(chk, marker)
    if skipped:
        journal.write("IA : {} photo(s) ecartee(s) (pas d'estimation ou d'echelle)".format(skipped))
    if not items:
        _status(state, i18n.t("aigcp.no_photos", label=label))
        return False
    job = Job(kind, build_work(kind, items), new_run_dir()).start()
    state.aigcp_run = _Running(job, kind, marker, label, msapi.chunk_key(chk), items)
    _status(state, i18n.t("aigcp.running", kind=kind, n=len(items), label=label))
    return True


def poll(state):
    """Appele par le battement : pose les propositions quand le calcul est fini."""
    run = getattr(state, "aigcp_run", None)
    if run is None:
        return
    try:
        result = run.job.poll()
    except Exception as exc:
        state.aigcp_run = None
        _status(state, i18n.t("aigcp.failed"))
        journal.write("IA : {}".format(exc))
        return
    if result is None:
        return
    state.aigcp_run = None
    try:
        _apply(state, run, result[0], result[1])
    except Exception as exc:
        _status(state, i18n.t("aigcp.failed"))
        journal.write("IA : pose des propositions impossible : {!r}".format(exc))


def _apply(state, run, hits, errors):
    for line in errors[:5]:
        journal.write("IA : photo ignoree : {}".format(line))
    chk = msapi.chunk()
    if chk is None or msapi.chunk_key(chk) != run.chunk_key:
        _status(state, i18n.t("aigcp.chunk_changed"))
        return
    conf = conf_for(run.kind)
    by_id = {it["id"]: it for it in run.items}
    placed = []
    for hit in hits:
        item = by_id.get(str(hit.get("id")))
        if item is None or not accept(hit, item, conf):
            continue
        camera = item["camera"]
        current = msapi.get_projection(run.marker, camera)
        if current is not None and current[2]:
            continue          # l'operateur a pointe cette photo entre temps
        ok, message = msapi.set_projection(run.marker, camera, hit["x"], hit["y"], pinned=False)
        if not ok:
            journal.write("IA : {} sur {} : {}".format(run.label, msapi.camera_label(camera), message))
            continue
        placed.append((run.marker, camera))
        journal.write("IA : {} propose en ({:.0f}, {:.0f}) sur {} (confiance {:.2f})".format(
            run.label, hit["x"], hit["y"], msapi.camera_label(camera), hit["conf"]))
    applied = getattr(state, "aigcp_applied", None)
    if applied is None:
        applied = state.aigcp_applied = []
    applied.extend(placed)
    if placed:
        _status(state, i18n.t("aigcp.done", n=len(placed), m=len(run.items), label=run.label))
    else:
        _status(state, i18n.t("aigcp.none", m=len(run.items), label=run.label))
    photos.panel_call(state, "update_active_card")
    photos.panel_call(state, "refresh_errors")


def clear(state):
    """Efface les propositions encore non validees. Renvoie leur nombre."""
    removed = 0
    for marker, camera in list(getattr(state, "aigcp_applied", None) or []):
        try:
            current = msapi.get_projection(marker, camera)
            if current is None or current[2]:
                continue      # validee ou deja effacee : on n'y touche pas
            ok, _message = msapi.remove_projection(marker, camera)
            removed += 1 if ok else 0
        except Exception:
            continue          # marqueur ou photo disparus avec le chunk
    state.aigcp_applied = []
    _status(state, i18n.t("aigcp.cleared", n=removed))
    photos.panel_call(state, "update_active_card")
    photos.panel_call(state, "refresh_errors")
    return removed


def cancel(state):
    """Arrete un calcul en cours (fermeture de l'outil). Ne leve jamais."""
    run = getattr(state, "aigcp_run", None)
    state.aigcp_run = None
    if run is not None:
        try:
            run.job.cancel()
        except Exception:
            pass
