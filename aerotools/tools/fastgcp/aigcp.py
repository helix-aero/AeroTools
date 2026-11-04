"""AIGCP dans FastGCP : proposer des pointages avec les modeles de detection.

Le calcul ne tourne JAMAIS dans le Python de Metashape. FastGCP ecrit un
`work.json`, lance `aigcp_runner.py` dans un Python a part (celui qui a torch et
ultralytics), puis relit `hits.json` (contrat decrit dans aigcp_runner.py). Le
panneau reste libre pendant ce temps : `poll()` est appele par le battement de
tool.py et pose les propositions quand le calcul est fini.

Une proposition est une projection NON posee a la main (`pinned=False`, point
bleu de Metashape), memorisee par msapi.mark_proposal. Elle ne remplace jamais
un pointage de l'operateur, ne compte pas dans les photos pointees
(msapi.projection_count) et disparait d'un clic (« Effacer les propositions »,
qui rend le point bleu d'avant s'il y en avait un) ou se valide en cliquant la
cible, ce qui la remplace par un pointage normal.

Deux modeles, jamais melanges : damier et bombe. L'operateur choisit le type.
Portee : le marqueur actif, ou tous les marqueurs qui ont une position 3D (triangulee, ou donnee
par les coordonnees de reference d'un GCP) : aucun pointage n'est exige.
Photos deja marquees : par defaut l'IA ne touche ni aux pointages manuels (vert)
ni aux points bleus de Metashape ; le choix « Ignorer » du panneau (vert, bleu,
les deux, aucun) permet de repasser dessus, et « Effacer » rend alors l'etat d'avant.

Garde-fou geometrique : quand un marqueur a au moins 2 projections (posees a la
main ou calculees par Metashape), on mesure leur ecart a la reprojection de son
point 3D : la position attendue sur les autres photos est connue a cet ecart
pres. Une detection qui tombe a plus de max(GATE_MIN_M, GATE_FACTOR x cet ecart)
metres de cette position est une fausse detection (toiture, arbre, objet
voisin) : elle est ecartee, et la recherche elargie autour de l'estimation est
coupee pour ce marqueur.

Coherence entre photos : les detections d'un meme marqueur doivent se recouper
en un meme point 3D (voir consensus.py). Celles qui tombent a plus de
CONSENSUS_M metres de ce point sont ecartees ; sans point commun, tout l'est.
Cela protege aussi un marqueur sans aucune projection, ou le garde-fou
ci-dessus n'a rien a comparer.

Reglages caches (settings.json, section `aigcp`) :
  python      chemin du Python qui a torch + ultralytics
              (defaut : variable AIGCP_PYTHON, puis ~/AIGCP/_env/Scripts/python.exe)
  models_dir  dossier des poids damier.pt et bombe.pt (defaut : <depot>/models)
  conf_damier, conf_bombe   seuils de confiance (defauts 0,25 et 0,30)
  gate_min_m (0,30), gate_factor (5)   garde-fou ; gate_min_m tres grand = coupe
  consensus_m (0,25)   tolerance de la coherence entre photos ; tres grand = coupe
  type, scope, ignore   derniers choix du panneau
"""

import json
import math
import os
import shutil
import subprocess
import time

from ... import config, i18n, journal, msapi
from . import consensus, detection, photos, review

SECTION = "aigcp"
TYPES = ("damier", "bombe")
SCOPES = ("marker", "all")
# Photos deja marquees a respecter : vert (pointage manuel), bleu (point calcule par
# Metashape), les deux, ou aucune.
IGNORES = ("both", "green", "blue", "none")
DEFAULT_TYPE = "damier"
DEFAULT_SCOPE = "marker"
DEFAULT_IGNORE = "both"
DEFAULT_CONF = {"damier": 0.25, "bombe": 0.30}
SCALES_M = (0.3, 0.5, 0.8)
MAX_PHOTOS = 200
MAX_PHOTOS_ALL = 3000
# Une proposition doit rester pres de l'estimation du marqueur : au plus cette
# fraction de la taille du recadrage (la recherche elargie va jusqu'a 0,4).
MAX_SHIFT_FRACTION = 0.7
# Garde-fou geometrique : voir l'en-tete du module.
GATE_MIN_M = 0.30
GATE_FACTOR = 5.0
CONSENSUS_M = 0.25
RUNNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aigcp_runner.py")
KEEP_RUNS = 5


# --------------------------------------------------------------------------
# Installation : Python externe, poids, reglages
# --------------------------------------------------------------------------

def models_dir():
    value = config.get(SECTION, "models_dir")
    if isinstance(value, str) and value.strip():
        return value
    return os.path.join(config.ROOT, "models")


def weights_path(kind):
    """Poids du modele actif pour ce type (celui de base tant qu'on n'en a pas choisi d'autre)."""
    from . import training
    return training.weights_for(kind)


def python_path():
    value = config.get(SECTION, "python")
    if isinstance(value, str) and value.strip():
        return value
    value = os.environ.get("AIGCP_PYTHON")
    if value:
        return value
    sub = ("Scripts", "python.exe") if os.name == "nt" else ("bin", "python")
    return os.path.join(os.path.expanduser("~"), "AIGCP", "_env", *sub)


def _number(key, default, low, high):
    value = config.get(SECTION, key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value) if low < value < high else default


def conf_for(kind):
    return _number("conf_" + kind, DEFAULT_CONF[kind], 0.0, 1.0)


def gate_min_m():
    return _number("gate_min_m", GATE_MIN_M, 0.0, 1e9)


def gate_factor():
    return _number("gate_factor", GATE_FACTOR, 0.0, 1e6)


def consensus_m():
    return _number("consensus_m", CONSENSUS_M, 0.0, 1e9)


def selected_type():
    value = config.get(SECTION, "type")
    return value if value in TYPES else DEFAULT_TYPE


def selected_scope():
    value = config.get(SECTION, "scope")
    return value if value in SCOPES else DEFAULT_SCOPE


def selected_ignore():
    value = config.get(SECTION, "ignore")
    return value if value in IGNORES else DEFAULT_IGNORE


def set_choice(key, value):
    """Memorise le type, la portee ou les photos a ignorer choisis dans le panneau."""
    allowed = {"type": TYPES, "scope": SCOPES, "ignore": IGNORES}
    if value in allowed.get(key, ()):
        config.put(SECTION, key, value)


def skip_flags(state=None):
    """(ignorer le vert, ignorer le bleu) d'apres le choix « Ignorer » du panneau."""
    choice = selected_ignore()
    return choice in ("both", "green"), choice in ("both", "blue")


def installer_path():
    """Fichier a double-cliquer pour installer la detection IA (Python + torch + ultralytics)."""
    return os.path.join(config.ROOT, "install", "installer_ia.bat")


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


def build_work(kind, items, conf=None, crops_dir=None):
    """Contenu de work.json. `items` : dicts id, image_path, x_est, y_est, px_per_m, rescue.

    `crops_dir` : dossier ou le runner ecrit les vignettes de la revue (facultatif)."""
    work = {
        "version": 1,
        "type": kind,
        "weights": weights_path(kind),
        "conf": conf_for(kind) if conf is None else float(conf),
        "scales_m": list(SCALES_M),
        "device": "auto",
        "items": [
            {"id": it["id"], "image_path": it["image_path"],
             "x_est": float(it["x_est"]), "y_est": float(it["y_est"]),
             "px_per_m": float(it["px_per_m"]), "rescue": bool(it.get("rescue", True))}
            for it in items
        ],
    }
    if crops_dir:
        work["crops_dir"] = crops_dir
    return work


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


def gap_m(hit, item):
    """Distance, en metres au sol, entre la detection et la position attendue."""
    gap_px = math.hypot(float(hit["x"]) - item["x_est"], float(hit["y"]) - item["y_est"])
    return gap_px / float(item["px_per_m"])


def check_hit(hit, item, conf):
    """(True, "") si la detection est retenue, sinon (False, raison).

    Raisons : "absent" (rien trouve), "conf" (pas assez sure), "far" (hors de la
    zone de recherche), "gate" (incompatible avec la position 3D du marqueur).
    """
    try:
        if not hit.get("found"):
            return False, "absent"
        if float(hit["conf"]) < conf:
            return False, "conf"
        crop = float(hit.get("crop_px") or 0)
        gap_px = math.hypot(float(hit["x"]) - item["x_est"], float(hit["y"]) - item["y_est"])
        if not crop > 0 or gap_px > MAX_SHIFT_FRACTION * crop:
            return False, "far"
        limit = item.get("gate_m")
        if limit is not None and gap_m(hit, item) > limit:
            return False, "gate"
        return True, ""
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return False, "absent"


def accept(hit, item, conf):
    return check_hit(hit, item, conf)[0]


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
        self.progress_path = os.path.join(run_dir, "progress.json")
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

    def progress(self):
        """(photos traitees, photos au total) lu dans progress.json, ou None."""
        try:
            with open(self.progress_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return int(data["done"]), int(data["total"])
        except Exception:
            return None

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
# Photos a tester
# --------------------------------------------------------------------------

def marker_gate_m(chk, marker):
    """Distance maximale (m) acceptee entre une detection et la position attendue,
    ou None si la position du marqueur n'est pas assez confirmee (moins de 2 projections)."""
    spread = msapi.marker_spread_m(chk, marker)
    if spread is None:
        return None
    return max(gate_min_m(), gate_factor() * spread)


def _already_marked(marker, camera, projection, skip_green, skip_blue):
    """La photo porte-t-elle deja un point qu'on a demande de respecter ?

    Vert : pointage manuel. Bleu : point calcule par Metashape. Une proposition
    d'une detection precedente n'est ni l'un ni l'autre : on la retente.
    """
    if projection is None:
        return False
    if projection[2]:
        return skip_green
    if msapi.is_proposal(marker, camera):
        return False
    return skip_blue


def collect_items(chk, marker, index=0, limit=MAX_PHOTOS, skip_green=True, skip_blue=True):
    """Photos ou le marqueur est attendu et ou l'IA peut proposer : liste d'items.

    Chaque item : id, marker, label, camera, image_path, x_est, y_est, px_per_m,
    gate_m (None = pas de garde-fou), rescue (recherche elargie permise), seen
    (projection de la photo au moment du calcul : si elle change entre-temps,
    la proposition n'est pas posee).
    Renvoie (items, nombre de photos ecartees faute d'estimation ou d'echelle).
    """
    cameras, _diag = msapi.cameras_seeing_marker(chk, marker)
    gate = marker_gate_m(chk, marker)
    label = msapi.marker_label(marker)
    items, skipped, respected = [], 0, 0
    for camera in cameras:
        projection = msapi.get_projection(marker, camera)
        if _already_marked(marker, camera, projection, skip_green, skip_blue):
            respected += 1
            continue
        estimate = msapi.marker_estimate(chk, marker, camera)
        ppm = msapi.marker_ground_scale(chk, marker, camera)
        path = msapi.camera_image_path(camera)
        if estimate is None or ppm is None or not path:
            skipped += 1
            continue
        items.append({"id": "{}:{}".format(index, msapi.camera_key(camera)),
                      "marker": marker, "label": label, "camera": camera,
                      "image_path": path, "x_est": estimate[0], "y_est": estimate[1],
                      "px_per_m": ppm, "gate_m": gate, "rescue": gate is None,
                      "seen": projection})
        if len(items) >= limit:
            break
    if respected:
        journal.write("IA : {} : {} photo(s) deja marquee(s) ignoree(s)".format(label, respected))
    return items, skipped


def collect_all(chk, skip_green=True, skip_blue=True, labels=None):
    """Items de tous les marqueurs qui ont une position 3D (aucun pointage n'est exige).

    `labels` (facultatif) restreint la recherche a ces marqueurs : la selection
    multiple du panneau.

    Un marqueur sans pointage s'appuie sur ses coordonnees de reference : la recherche
    elargie reste permise et seule la coherence entre photos le garde. Renvoie
    (items, marqueurs retenus, marqueurs ignores). Les items d'une meme photo sont
    voisins : le runner ne la decode qu'une fois.
    """
    items, used, ignored = [], 0, 0
    wanted = None if labels is None else set(labels)
    for index, marker in enumerate(msapi.markers(chk)):
        if len(items) >= MAX_PHOTOS_ALL:
            break
        if wanted is not None and msapi.marker_label(marker) not in wanted:
            continue
        if msapi.marker_point(chk, marker) is None:
            ignored += 1
            continue
        found, _skipped = collect_items(chk, marker, index, MAX_PHOTOS_ALL - len(items),
                                        skip_green, skip_blue)
        if found:
            used += 1
            items.extend(found)
    items.sort(key=lambda it: it["image_path"])
    return items, used, ignored


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
    def __init__(self, job, kind, scope, label, chunk_key, items, markers):
        self.job, self.kind, self.scope, self.label = job, kind, scope, label
        self.chunk_key, self.items, self.markers = chunk_key, items, markers
        self.progress = None


def _status(state, text):
    photos.panel_call(state, "set_status", text)
    journal.write(text)


def _refresh_panel(state):
    photos.panel_call(state, "update_aigcp")
    photos.panel_call(state, "update_active_card")
    photos.panel_call(state, "refresh_rows", None)


def busy(state):
    return getattr(state, "aigcp_run", None) is not None


def _auto_review(state):
    """La revue s'ouvre-t-elle toute seule ? Reglage du panneau, coche par defaut."""
    option = getattr(state, "option", None)
    return True if option is None else bool(option("ai_auto_review"))


def kind_label(kind):
    """Nom du type de cible, toujours en anglais (Spray, Checker) : c'est le nom du produit."""
    return i18n.texts("en").get("aigcp.type_" + str(kind), str(kind))


def bar_text(state):
    """Texte ecrit DANS la barre de progression : type, photos traitees. Vide sans calcul."""
    run = getattr(state, "aigcp_run", None)
    if run is None:
        return ""
    if run.progress is None:
        return i18n.t("aigcp.bar_start", kind=kind_label(run.kind))
    return i18n.t("aigcp.bar", kind=kind_label(run.kind), done=run.progress[0], total=run.progress[1])


def progress_of(state):
    """(photos traitees, total) du calcul en cours, ou None (pas de calcul, ou pas encore demarre)."""
    run = getattr(state, "aigcp_run", None)
    return None if run is None else run.progress


def start(state, kind=None, scope=None, labels=None):
    """Lance la detection sur le marqueur actif (scope "marker") ou sur tous
    (scope "all"). Sans argument : les choix memorises du panneau. Ne leve jamais.

    `labels` : marqueurs selectionnes dans le panneau (Maj+clic). Avec le scope "marker"
    et plus d'un marqueur, la detection porte sur eux tous ; sinon sur le marqueur actif."""
    try:
        return _start(state, kind or selected_type(), scope or selected_scope(), labels)
    except Exception as exc:
        _status(state, i18n.t("aigcp.failed"))
        journal.write("IA : lancement impossible : {!r}".format(exc))
        return False


def _start(state, kind, scope, labels=None):
    if busy(state):
        _status(state, i18n.t("aigcp.busy"))
        return False
    ok, message = check(kind)
    if not ok:
        _status(state, message)
        photos.panel_call(state, "show_ai_setup", i18n.t(
            "aigcp.setup_needed", detail=message, script=installer_path()))
        return False
    chk = msapi.chunk()
    if chk is None:
        _status(state, i18n.t("aigcp.no_marker"))
        return False
    label = state.active_label()
    several = scope == "marker" and labels is not None and len(labels) > 1
    if scope == "all" or several:
        # Plusieurs marqueurs choisis : meme traitement et memes messages que « tous ».
        scope = "all"
        items, used, ignored = collect_all(chk, *skip_flags(state),
                                           labels=labels if several else None)
        if ignored:
            journal.write("IA : {} marqueur(s) ignore(s) : sans position 3D".format(ignored))
        if not items:
            _status(state, i18n.t("aigcp.no_markers"))
            return False
        text = i18n.t("aigcp.running_all", kind=kind_label(kind), n=len(items), k=used)
        shown = None
    else:
        marker = state.active_marker
        if label is None or marker is None:
            _status(state, i18n.t("aigcp.no_marker"))
            return False
        items, skipped = collect_items(chk, marker, skip_green=skip_flags(state)[0],
                                       skip_blue=skip_flags(state)[1])
        if skipped:
            journal.write("IA : {} photo(s) ecartee(s) (pas d'estimation ou d'echelle)".format(skipped))
        if not items:
            _status(state, i18n.t("aigcp.no_photos", label=label))
            return False
        used = 1
        text = i18n.t("aigcp.running", kind=kind_label(kind), n=len(items), label=label)
        shown = label
    run_dir = new_run_dir()
    job = Job(kind, build_work(kind, items, crops_dir=os.path.join(run_dir, "crops")), run_dir).start()
    state.aigcp_run = _Running(job, kind, scope, shown, msapi.chunk_key(chk), items, used)
    _status(state, text)
    _refresh_panel(state)
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
        _refresh_panel(state)
        return
    if result is None:
        seen = run.job.progress()
        if seen is not None and seen != run.progress:
            run.progress = seen
            photos.panel_call(state, "update_aigcp")      # le texte est dans la barre de progression
        return
    state.aigcp_run = None
    try:
        _apply(state, run, result[0], result[1])
    except Exception as exc:
        _status(state, i18n.t("aigcp.failed"))
        journal.write("IA : pose des propositions impossible : {!r}".format(exc))
    _refresh_panel(state)
    if getattr(state, "aigcp_review", None) is not None and _auto_review(state):
        photos.panel_call(state, "open_review")


def _apply(state, run, hits, errors):
    for line in errors[:5]:
        journal.write("IA : photo ignoree : {}".format(line))
    chk = msapi.chunk()
    if chk is None or msapi.chunk_key(chk) != run.chunk_key:
        _status(state, i18n.t("aigcp.chunk_changed"))
        return
    conf = conf_for(run.kind)
    by_id = {it["id"]: it for it in run.items}
    placed, gated, with_proposal, entries = [], 0, set(), []
    crops_dir = os.path.join(run.job.run_dir, "crops")
    accepted = {}
    for hit in hits:
        item = by_id.get(str(hit.get("id")))
        if item is None:
            continue
        ok, reason = check_hit(hit, item, conf)
        if ok:
            accepted.setdefault(item["label"], []).append((item, hit))
        elif reason == "gate":
            gated += 1
            journal.write("IA : {} ecarte sur {} : a {:.1f} m de la position attendue (limite {:.2f} m)".format(
                item["label"], msapi.camera_label(item["camera"]), gap_m(hit, item), item["gate_m"]))
    to_place = []
    for label, group in accepted.items():
        kept, dropped = _consistent(chk, group)
        for item, hit in dropped:
            gated += 1
            journal.write("IA : {} ecarte sur {} : pas de point 3D commun avec les autres photos".format(
                label, msapi.camera_label(item["camera"])))
        to_place.extend(kept)
    for item, hit in to_place:
        marker, camera = item["marker"], item["camera"]
        current = msapi.get_projection(marker, camera)
        if current != item["seen"]:
            continue          # la photo a ete pointee ou modifiee pendant le calcul
        previous = current    # point d'avant (bleu, ou vert si on a repasse dessus), rendu a l'effacement
        done, message = msapi.set_projection(marker, camera, hit["x"], hit["y"], pinned=False)
        if not done:
            journal.write("IA : {} sur {} : {}".format(item["label"], msapi.camera_label(camera), message))
            continue
        msapi.mark_proposal(marker, camera)
        placed.append((marker, camera, previous))
        entries.append(review.make_entry(item, hit, previous, crops_dir))
        with_proposal.add(item["label"])
        journal.write("IA : {} propose en ({:.0f}, {:.0f}) sur {} (confiance {:.2f})".format(
            item["label"], hit["x"], hit["y"], msapi.camera_label(camera), hit["conf"]))
    applied = getattr(state, "aigcp_applied", None)
    if applied is None:
        applied = state.aigcp_applied = []
    applied.extend(placed)
    # La revue ne porte que sur la derniere detection ; « Effacer » defait toujours tout.
    state.aigcp_review = (review.Review(run.kind, os.path.basename(run.job.run_dir), entries)
                          if entries else None)
    # Journal cumule de toutes les detections de la session : le rapport PDF s'en sert.
    fresh = set((e["label"], msapi.camera_key(e["camera"])) for e in entries)
    state.aigcp_log = [e for e in (getattr(state, "aigcp_log", None) or [])
                       if (e["label"], msapi.camera_key(e["camera"])) not in fresh] + entries
    _status(state, _summary(run, len(placed), len(run.items), gated, len(with_proposal)))


def _consistent(chk, group):
    """Separe les detections d'un marqueur en (retenues, ecartees) selon leur recoupement 3D.

    `group` : liste de (item, hit). Avec moins de MIN_RAYS rayons utilisables on ne
    peut pas juger : tout est garde. Si aucun point 3D ne reunit au moins MIN_RAYS
    detections, aucune n'est fiable : tout est ecarte.
    """
    rays = [msapi.pixel_ray(item["camera"], hit["x"], hit["y"]) for item, hit in group]
    flags, _point = consensus.consensus(rays, consensus_m() / msapi.chunk_scale(chk))
    if flags is None:
        return list(group), []
    kept = [pair for pair, ok in zip(group, flags) if ok]
    if len(kept) < consensus.MIN_RAYS:
        return [], list(group)
    return kept, [pair for pair, ok in zip(group, flags) if not ok]


def _summary(run, placed, tested, gated, markers):
    if run.scope == "all":
        if placed:
            return i18n.t("aigcp.done_all", n=placed, k=markers, m=tested, g=gated)
        return i18n.t("aigcp.none_all", m=tested, k=run.markers, g=gated)
    if placed and gated:
        return i18n.t("aigcp.done_gate", n=placed, m=tested, label=run.label, g=gated)
    if placed:
        return i18n.t("aigcp.done", n=placed, m=tested, label=run.label)
    if gated:
        return i18n.t("aigcp.none_gate", label=run.label, g=gated)
    return i18n.t("aigcp.none", m=tested, label=run.label)


def forget_markers(state, labels):
    """Oublie les propositions de ces marqueurs (supprimes ou vides de leurs projections).

    Les retire des propositions que « Effacer » defait, du journal du rapport et de la
    revue en cours ; ferme la fenetre de revue si elle n'a plus rien a montrer.
    """
    gone = set(labels)
    state.aigcp_applied = [t for t in (getattr(state, "aigcp_applied", None) or [])
                           if _label_of(t[0]) not in gone]
    state.aigcp_log = [e for e in (getattr(state, "aigcp_log", None) or [])
                       if e["label"] not in gone]
    current = getattr(state, "aigcp_review", None)
    if current is not None:
        current.entries = [e for e in current.entries if e["label"] not in gone]
        if not current.entries:
            state.aigcp_review = None
            photos.panel_call(state, "close_review")
    photos.panel_call(state, "update_aigcp")


def _label_of(marker):
    try:
        return msapi.marker_label(marker)
    except Exception:
        return None


def clear(state):
    """Efface les propositions encore non validees. Renvoie leur nombre."""
    removed = 0
    for marker, camera, previous in list(getattr(state, "aigcp_applied", None) or []):
        try:
            current = msapi.get_projection(marker, camera)
            msapi.unmark_proposal(marker, camera)
            if current is None or current[2]:
                continue      # validee ou deja effacee : on n'y touche pas
            if previous is not None:
                done, _message = msapi.set_projection(
                    marker, camera, previous[0], previous[1], previous[2])
            else:
                done, _message = msapi.remove_projection(marker, camera)
            removed += 1 if done else 0
        except Exception:
            continue          # marqueur ou photo disparus avec le chunk
    state.aigcp_applied = []
    state.aigcp_review = None
    state.aigcp_log = [e for e in (getattr(state, "aigcp_log", None) or [])
                       if e["state"] != review.KEPT]
    photos.panel_call(state, "close_review")
    _status(state, i18n.t("aigcp.cleared", n=removed))
    _refresh_panel(state)
    return removed


def abort(state):
    """Bouton Annuler de la barre : arrete le calcul, rien n'est pose. True si un calcul tournait."""
    if getattr(state, "aigcp_run", None) is None:
        return False
    cancel(state)
    _status(state, i18n.t("aigcp.cancelled"))
    _refresh_panel(state)
    return True


def cancel(state):
    """Arrete un calcul en cours (fermeture de l'outil). Ne leve jamais."""
    run = getattr(state, "aigcp_run", None)
    state.aigcp_run = None
    if run is not None:
        try:
            run.job.cancel()
        except Exception:
            pass
