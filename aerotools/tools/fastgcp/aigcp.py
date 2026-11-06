"""AIGCP in FastGCP: propose pins with the detection models.

The computation NEVER runs in Metashape's Python. FastGCP writes a
`work.json`, launches `aigcp_runner.py` in a separate Python (the onnxruntime one, without torch), then reads back `hits.json` (contract described in aigcp_runner.py). The
panel stays free meanwhile: `poll()` is called by the heartbeat of
tool.py and places the proposals when the computation is finished.

A proposal is a projection NOT placed by hand (`pinned=False`, Metashape's blue
point), remembered by msapi.mark_proposal. It never replaces an operator's
pin, does not count in the pinned photos
(msapi.projection_count) and disappears with one click ("Clear proposals",
which gives back the previous blue point if there was one) or is validated by clicking the
target, which replaces it with a normal pin.

Two models, never mixed: checker and spray. The operator chooses the type.
Scope: the active marker, or all markers that have a 3D position (triangulated, or given
by a GCP's reference coordinates): no pin is required.
Photos already marked: by default the AI touches neither the manual pins (green)
nor Metashape's blue points; the panel's "Ignore" choice (green, blue,
both, none) allows going over them again, and "Clear" then restores the previous state.

Geometric guard: when a marker has at least 2 projections (placed by
hand or computed by Metashape), their gap to the reprojection of its
3D point is measured: the expected position on the other photos is known to within that gap.
A detection that falls more than max(GATE_MIN_M, GATE_FACTOR x that gap)
metres from that position is a false detection (roof, tree, neighbouring
object): it is discarded, and the widened search around the estimate is
disabled for that marker.

Consistency between photos: the detections of one marker must intersect
at a single 3D point (see consensus.py). Those that fall more than
CONSENSUS_M metres from that point are discarded; with no common point, all of them are.
This also protects a marker with no projection at all, where the guard
above has nothing to compare.

Hidden settings (settings.json, section `aigcp`):
  python      path of AIGCP's Python (onnxruntime, numpy, opencv; torch only for training)
              (default: AIGCP_PYTHON variable, then ~/AIGCP/_env/Scripts/python.exe)
  models_dir  folder of the checker.pt and spray.pt weights (default: <repo>/models)
  conf_checker, conf_spray   confidence thresholds (defaults 0.25 and 0.30)
  gate_min_m (0.30), gate_factor (5)   guard; very large gate_min_m = disabled
  consensus_m (0.25)   tolerance of the consistency between photos; very large = disabled
  type, scope, ignore   last choices made in the panel
"""

import json
import math
import os
import shutil
import subprocess
import time

from ... import config, i18n, journal, msapi
from . import consensus, detection, engines, photos, review

SECTION = "aigcp"
TYPES = ("checker", "spray")
SCOPES = ("marker", "all")
# Already marked photos to respect: green (manual pin), blue (point computed by
# Metashape), both, or none.
IGNORES = ("both", "green", "blue", "none")
DEFAULT_TYPE = "checker"
DEFAULT_SCOPE = "marker"
DEFAULT_IGNORE = "both"
DEFAULT_CONF = {"checker": 0.25, "spray": 0.30}
SCALES_M = (0.3, 0.5, 0.8)
MAX_PHOTOS = 200
MAX_PHOTOS_ALL = 3000
# A proposal must stay close to the marker's estimate: at most this
# fraction of the crop size (the widened search goes up to 0.4).
MAX_SHIFT_FRACTION = 0.7
# Geometric guard: see the module header.
GATE_MIN_M = 0.30
GATE_FACTOR = 5.0
CONSENSUS_M = 0.25
RUNNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aigcp_runner.py")
KEEP_RUNS = 5


# --------------------------------------------------------------------------
# Installation: external Python, weights, settings
# --------------------------------------------------------------------------

def models_dir():
    value = config.get(SECTION, "models_dir")
    if isinstance(value, str) and value.strip():
        return value
    return os.path.join(config.ROOT, "models")


def weights_path(kind):
    """Weights of the active model for this type (the base one until another is chosen)."""
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
    from . import training
    saved_before = _number("conf_" + training.legacy_name(kind), DEFAULT_CONF[kind], 0.0, 1.0)    # key of an earlier version
    return _number("conf_" + kind, saved_before, 0.0, 1.0)


def gate_min_m():
    return _number("gate_min_m", GATE_MIN_M, 0.0, 1e9)


def gate_factor():
    return _number("gate_factor", GATE_FACTOR, 0.0, 1e6)


def consensus_m():
    return _number("consensus_m", CONSENSUS_M, 0.0, 1e9)


def selected_type():
    from . import training
    value = training.canonical_type(config.get(SECTION, "type"))
    return value if value in TYPES else DEFAULT_TYPE


def selected_scope():
    value = config.get(SECTION, "scope")
    return value if value in SCOPES else DEFAULT_SCOPE


def selected_ignore():
    value = config.get(SECTION, "ignore")
    return value if value in IGNORES else DEFAULT_IGNORE


def set_choice(key, value):
    """Remembers the type, scope or photos to ignore chosen in the panel."""
    allowed = {"type": TYPES, "scope": SCOPES, "ignore": IGNORES}
    if value in allowed.get(key, ()):
        config.put(SECTION, key, value)


def skip_flags(state=None):
    """(ignore green, ignore blue) according to the panel's "Ignore" choice."""
    choice = selected_ignore()
    return choice in ("both", "green"), choice in ("both", "blue")


def installer_path():
    """File to double-click to install the AI detection (Python + onnxruntime)."""
    return os.path.join(config.ROOT, "CLICK HERE TO INSTALL.bat")


def check(kind):
    """(True, "") if the detection of this type can run, otherwise (False, text)."""
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
    wanted = engines.missing()
    if wanted is not None:
        return False, i18n.t(engines.MISSING_TEXT[wanted])
    return True, ""


def setup_target():
    """What the installer can set up so that the detection works: "light", "precise", or None.

    None when the problem is not in the standard environment (hand-picked Python) or
    when no engine is missing (absent weights, for example)."""
    if not engines.can_install():
        return None
    if not os.path.isfile(python_path()):
        return "precise" if engines.selected() == "precise" else "light"
    return engines.missing()


# --------------------------------------------------------------------------
# Work files
# --------------------------------------------------------------------------

def runs_dir():
    return os.path.join(config.settings_dir(), "aigcp")


def new_run_dir():
    """Folder specific to this detection; keeps the last KEEP_RUNS."""
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
    """Content of work.json. `items`: dicts id, image_path, x_est, y_est, px_per_m, rescue.

    `crops_dir`: folder where the runner writes the review thumbnails (optional)."""
    work = {
        "version": 1,
        "type": kind,
        "weights": weights_path(kind),
        "conf": conf_for(kind) if conf is None else float(conf),
        "scales_m": list(SCALES_M),
        "device": "auto",
        "engine": engines.runner_engine(),
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
    """(list of hits, list of errors) read from hits.json; raises if unreadable."""
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or not isinstance(data.get("hits"), list):
        raise ValueError("unexpected hits.json")
    return data["hits"], [str(e) for e in data.get("errors", [])]


def child_env():
    """Environment of the external Python: without the variables of Metashape's."""
    env = dict(os.environ)
    for name in ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONUSERBASE"):
        env.pop(name, None)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def gap_m(hit, item):
    """Distance, in metres on the ground, between the detection and the expected position."""
    gap_px = math.hypot(float(hit["x"]) - item["x_est"], float(hit["y"]) - item["y_est"])
    return gap_px / float(item["px_per_m"])


def check_hit(hit, item, conf):
    """(True, "") if the detection is kept, otherwise (False, reason).

    Reasons: "absent" (nothing found), "conf" (not certain enough), "far" (outside the
    search area), "gate" (incompatible with the marker's 3D position).
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
# Background work
# --------------------------------------------------------------------------

class Job(object):
    """One launch of the runner. `poll()`: None while it runs, then (hits, errors)."""

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
        """(photos processed, photos in total) read from progress.json, or None."""
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
        """None if running; otherwise (hits, errors). Raises RuntimeError if the runner failed."""
        if self._process is None:
            raise RuntimeError("detection not started")
        code = self._process.poll()
        if code is None:
            return None
        self._close_log()
        if code != 0 or not os.path.isfile(self.hits_path):
            raise RuntimeError("runner stopped (code {}): {}".format(code, self.log_tail()))
        return read_hits(self.hits_path)

    def wait(self, timeout_s=600.0, step_s=0.2):
        end = time.time() + timeout_s
        while time.time() < end:
            result = self.poll()
            if result is not None:
                return result
            time.sleep(step_s)
        self.cancel()
        raise RuntimeError("timed out")


# --------------------------------------------------------------------------
# Photos to test
# --------------------------------------------------------------------------

def marker_gate_m(chk, marker):
    """Maximum distance (m) accepted between a detection and the expected position,
    or None if the marker's position is not confirmed enough (fewer than 2 projections)."""
    spread = msapi.marker_spread_m(chk, marker)
    if spread is None:
        return None
    return max(gate_min_m(), gate_factor() * spread)


def _already_marked(marker, camera, projection, skip_green, skip_blue):
    """Does the photo already carry a point we were asked to respect?

    Green: manual pin. Blue: point computed by Metashape. A proposal
    from a previous detection is neither: it is tried again.
    """
    if projection is None:
        return False
    if projection[2]:
        return skip_green
    if msapi.is_proposal(marker, camera):
        return False
    return skip_blue


def collect_items(chk, marker, index=0, limit=MAX_PHOTOS, skip_green=True, skip_blue=True):
    """Photos where the marker is expected and where the AI can propose: list of items.

    Each item: id, marker, label, camera, image_path, x_est, y_est, px_per_m,
    gate_m (None = no guard), rescue (widened search allowed), seen
    (the photo's projection at computation time: if it changes in the meantime,
    the proposal is not placed).
    Returns (items, number of photos discarded for lack of an estimate or scale).
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
        journal.write("AI: {}: {} already marked photo(s) ignored".format(label, respected))
    return items, skipped


def collect_all(chk, skip_green=True, skip_blue=True, labels=None):
    """Items of all the markers that have a 3D position (no pin is required).

    `labels` (optional) restricts the search to these markers: the panel's
    multiple selection.

    A marker without a pin relies on its reference coordinates: the widened
    search remains allowed and only the consistency between photos guards it. Returns
    (items, markers kept, markers ignored). The items of one photo are
    adjacent: the runner decodes it only once.
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
# Detector (contract of detection.py): blocking call
# --------------------------------------------------------------------------

class AigcpDetector(detection.Detector):
    """AIGCP detector for one target type. `propose` waits for the computation to finish."""

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
# Session state: one computation at a time
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
    """Does the review open by itself? Panel setting, ticked by default."""
    option = getattr(state, "option", None)
    return True if option is None else bool(option("ai_auto_review"))


def kind_label(kind):
    """Name of the target type (Spray, Checker): the product name."""
    return i18n.texts().get("aigcp.type_" + str(kind), str(kind))


def bar_text(state):
    """Text written IN the progress bar: type, photos processed. Empty without a computation."""
    run = getattr(state, "aigcp_run", None)
    if run is None:
        return ""
    if run.progress is None:
        return i18n.t("aigcp.bar_start", kind=kind_label(run.kind))
    return i18n.t("aigcp.bar", kind=kind_label(run.kind), done=run.progress[0], total=run.progress[1])


def progress_of(state):
    """(photos processed, total) of the running computation, or None (no computation, or not started yet)."""
    run = getattr(state, "aigcp_run", None)
    return None if run is None else run.progress


def start(state, kind=None, scope=None, labels=None):
    """Starts the detection on the active marker (scope "marker") or on all
    (scope "all"). Without arguments: the panel's remembered choices. Never raises.

    `labels`: markers selected in the panel (Shift+click). With scope "marker"
    and more than one marker, the detection covers all of them; otherwise the active marker."""
    try:
        return _start(state, kind or selected_type(), scope or selected_scope(), labels)
    except Exception as exc:
        _status(state, i18n.t("aigcp.failed"))
        journal.write("AI: cannot start: {!r}".format(exc))
        return False


def _start(state, kind, scope, labels=None):
    if busy(state):
        _status(state, i18n.t("aigcp.busy"))
        return False
    ok, message = check(kind)
    if not ok:
        _status(state, message)
        target = setup_target()
        if target is None:
            text = i18n.t("aigcp.setup_needed", detail=message, script=installer_path())
        else:
            text = i18n.t("aigcp.setup_offer", detail=message,
                          what=i18n.t(engines.WHAT_TEXT[target]))
        photos.panel_call(state, "show_ai_setup", text, target)
        return False
    chk = msapi.chunk()
    if chk is None:
        _status(state, i18n.t("aigcp.no_marker"))
        return False
    label = state.active_label()
    several = scope == "marker" and labels is not None and len(labels) > 1
    if scope == "all" or several:
        # Several markers chosen: same processing and same messages as "all".
        scope = "all"
        items, used, ignored = collect_all(chk, *skip_flags(state),
                                           labels=labels if several else None)
        if ignored:
            journal.write("AI: {} marker(s) ignored: no 3D position".format(ignored))
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
            journal.write("AI: {} photo(s) discarded (no estimate or scale)".format(skipped))
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
    """Called by the heartbeat: places the proposals when the computation is finished."""
    run = getattr(state, "aigcp_run", None)
    if run is None:
        return
    try:
        result = run.job.poll()
    except Exception as exc:
        state.aigcp_run = None
        _status(state, i18n.t("aigcp.failed"))
        journal.write("AI: {}".format(exc))
        _refresh_panel(state)
        return
    if result is None:
        seen = run.job.progress()
        if seen is not None and seen != run.progress:
            run.progress = seen
            photos.panel_call(state, "update_aigcp")      # the text is in the progress bar
        return
    state.aigcp_run = None
    try:
        _apply(state, run, result[0], result[1])
    except Exception as exc:
        _status(state, i18n.t("aigcp.failed"))
        journal.write("AI: cannot place the proposals: {!r}".format(exc))
    _refresh_panel(state)
    if getattr(state, "aigcp_review", None) is not None and _auto_review(state):
        photos.panel_call(state, "open_review")


def _apply(state, run, hits, errors):
    for line in errors[:5]:
        journal.write("AI: photo ignored: {}".format(line))
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
            journal.write("AI: {} discarded on {}: {:.1f} m from the expected position (limit {:.2f} m)".format(
                item["label"], msapi.camera_label(item["camera"]), gap_m(hit, item), item["gate_m"]))
    to_place = []
    for label, group in accepted.items():
        kept, dropped = _consistent(chk, group)
        for item, hit in dropped:
            gated += 1
            journal.write("AI: {} discarded on {}: no 3D point in common with the other photos".format(
                label, msapi.camera_label(item["camera"])))
        to_place.extend(kept)
    for item, hit in to_place:
        marker, camera = item["marker"], item["camera"]
        current = msapi.get_projection(marker, camera)
        if current != item["seen"]:
            continue          # the photo was pinned or modified during the computation
        previous = current    # previous point (blue, or green if it was gone over), given back on clearing
        done, message = msapi.set_projection(marker, camera, hit["x"], hit["y"], pinned=False)
        if not done:
            journal.write("AI: {} on {}: {}".format(item["label"], msapi.camera_label(camera), message))
            continue
        msapi.mark_proposal(marker, camera)
        placed.append((marker, camera, previous))
        entries.append(review.make_entry(item, hit, previous, crops_dir))
        with_proposal.add(item["label"])
        journal.write("AI: {} proposed at ({:.0f}, {:.0f}) on {} (confidence {:.2f})".format(
            item["label"], hit["x"], hit["y"], msapi.camera_label(camera), hit["conf"]))
    applied = getattr(state, "aigcp_applied", None)
    if applied is None:
        applied = state.aigcp_applied = []
    applied.extend(placed)
    # The review only covers the last detection; "Clear" always undoes everything.
    state.aigcp_review = (review.Review(run.kind, os.path.basename(run.job.run_dir), entries)
                          if entries else None)
    # Cumulative log of all the detections of the session: the PDF report uses it.
    fresh = set((e["label"], msapi.camera_key(e["camera"])) for e in entries)
    state.aigcp_log = [e for e in (getattr(state, "aigcp_log", None) or [])
                       if (e["label"], msapi.camera_key(e["camera"])) not in fresh] + entries
    _status(state, _summary(run, len(placed), len(run.items), gated, len(with_proposal)))


def _consistent(chk, group):
    """Splits the detections of a marker into (kept, discarded) according to their 3D intersection.

    `group`: list of (item, hit). With fewer than MIN_RAYS usable rays nothing
    can be judged: everything is kept. If no 3D point gathers at least MIN_RAYS
    detections, none is reliable: everything is discarded.
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
    """Forgets the proposals of these markers (deleted or emptied of their projections).

    Removes them from the proposals that "Clear" undoes, from the report log and from the
    current review; closes the review window if it has nothing left to show.
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
    """Clears the proposals that are still unvalidated. Returns their number."""
    removed = 0
    for marker, camera, previous in list(getattr(state, "aigcp_applied", None) or []):
        try:
            current = msapi.get_projection(marker, camera)
            msapi.unmark_proposal(marker, camera)
            if current is None or current[2]:
                continue      # validated or already cleared: leave it alone
            if previous is not None:
                done, _message = msapi.set_projection(
                    marker, camera, previous[0], previous[1], previous[2])
            else:
                done, _message = msapi.remove_projection(marker, camera)
            removed += 1 if done else 0
        except Exception:
            continue          # marker or photo gone with the chunk
    state.aigcp_applied = []
    state.aigcp_review = None
    state.aigcp_log = [e for e in (getattr(state, "aigcp_log", None) or [])
                       if e["state"] != review.KEPT]
    photos.panel_call(state, "close_review")
    _status(state, i18n.t("aigcp.cleared", n=removed))
    _refresh_panel(state)
    return removed


def abort(state):
    """Cancel button of the bar: stops the computation, nothing is placed. True if a computation was running."""
    if getattr(state, "aigcp_run", None) is None:
        return False
    cancel(state)
    _status(state, i18n.t("aigcp.cancelled"))
    _refresh_panel(state)
    return True


def cancel(state):
    """Stops a running computation (tool closing). Never raises."""
    run = getattr(state, "aigcp_run", None)
    state.aigcp_run = None
    if run is not None:
        try:
            run.job.cancel()
        except Exception:
            pass
