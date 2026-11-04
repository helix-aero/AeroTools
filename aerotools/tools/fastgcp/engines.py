"""Moteurs de detection installes dans le Python d'AIGCP (sans Qt, sans Metashape).

Deux moteurs, au choix de l'operateur (reglage `engine` de la section `aigcp`) :
  light    ONNX Runtime sur processeur : environ 100 Mo, rapide, marche sur tout PC ;
  precise  torch + ultralytics : le moteur d'origine (environ 3 Go), carte graphique si presente.
  auto     (defaut) le precis s'il est installe, sinon le leger.
Les deux donnent les memes positions a quelques centiemes de pixel pres (mesure sur 569 cibles).

Les outils de reentrainement (torch, ultralytics, onnx) sont ceux du moteur precis plus le
paquet onnx : l'installeur les pose a la place de l'utilisateur (`InstallJob`), dans une
fenetre de console qui affiche la progression.

Rien n'est importe ici : on interroge le Python externe par un court sous-processus.
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

# Cles de texte (aerotools/lang) : ecrites en clair ici pour que les catalogues restent verifiables.
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
    """L'installeur ne sait remplir que l'environnement par defaut (pas un Python choisi a la main)."""
    return (os.name == "nt" and os.path.isfile(script_path())
            and os.path.normcase(os.path.normpath(python_path())) == os.path.normcase(os.path.normpath(default_python())))


def script_path():
    return os.path.join(config.ROOT, "install", "install_all.ps1")


def status(python=None, refresh=False):
    """{"python", "light", "precise", "training"} : booleens. Mis en cache quelques secondes.

    Un Python introuvable ou qui ne repond pas donne tout a False. Ne leve jamais."""
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
    """Oublie le cache : a appeler apres une installation."""
    _cache.update(key=None, when=0.0, value=None)


def resolve(choice=None, state=None):
    """Moteur reellement utilise : "light" ou "precise", ou None si rien de ce qu'il faut n'est installe."""
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
    """Ce qu'il faut installer pour que le choix marche : "light", "precise" ou None (rien a faire)."""
    choice = choice or selected()
    state = state or status()
    if resolve(choice, state) is not None:
        return None
    return "precise" if choice == "precise" else "light"


def runner_engine(choice=None, state=None):
    """Valeur du champ `engine` de work.json : "onnx" ou "torch" (le runner choisit seul si inconnu)."""
    which = resolve(choice, state)
    return {"light": "onnx", "precise": "torch"}.get(which, "auto")


class InstallJob(object):
    """Lance l'installeur dans une console visible ; `poll()` rend None tant qu'il tourne."""

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
        """None si l'installation tourne encore ; sinon {"ok": bool, "code": code de sortie}."""
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
    """Lignes d'etat pour l'interface : [(cle, installe)] dans l'ordre leger, precis, reentrainement."""
    state = state or status()
    return [("light", state["light"]), ("precise", state["precise"]), ("training", state["training"])]

