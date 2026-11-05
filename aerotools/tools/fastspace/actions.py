"""Ce que Fast Space sait faire d'un projet : l'alleger (version douce) ou le supprimer (version dure).

Deux sorts possibles pour ce qu'on retire :
  RECYCLE    corbeille Windows : on peut tout restaurer tant qu'on ne la vide pas (disque local seulement ;
             sur un lecteur reseau Windows n'a pas de corbeille) ;
  PERMANENT  suppression definitive. L'interface demande alors de taper DELETE.

Alleger (`lighten`) retire, au choix, le nuage dense, les cartes de profondeur et les orthophotos (les images
redressees de l'ortho, `Orthomosaic.removeOrthophotos()`), en gardant l'alignement, les marqueurs, les MNT et les
mosaiques. Pour que le projet reste coherent (Metashape garde la liste de ses
nuages denses dans le fichier du projet), on passe par l'API de Metashape, qui efface lui-meme ses
fichiers a l'enregistrement (mesure sur une copie : 1967 Mo -> 1087 Mo, projet rouvert sans erreur).
L'allegement est donc toujours PERMANENT : il n'y a pas de corbeille possible. Essai fait : envoyer les
dossiers a la corbeille AVANT, puis laisser l'API nettoyer, echoue (`chunk.remove` relit le nuage dense et
leve « Can't open file dense_cloud.zip », le projet garde des nuages denses fantomes).

  1. lecture seule : on note les chunks, cameras, marqueurs ;
  2. l'API retire ce qui a ete choisi dans chaque chunk et enregistre ;
  3. les dossiers et fichiers qui resteraient sont supprimes ;
  4. verification : le projet se rouvre, memes cameras et marqueurs, plus rien de ce qui a ete choisi.

Supprimer le projet (`delete_project`) retire le .psx et son dossier .files, et rien d'autre : les fichiers
exportes a cote (ortho, MNT, nuages, rapports) ne sont pas touches.

Jamais sur un projet ouvert (fichier `lock` tenu par un Metashape) ni sur celui qui est ouvert ici.
Chaque action est ecrite dans un journal CSV (`<reglages>\\fastspace\\log.csv`).
"""

import csv
import ctypes
import datetime
import gc
import os
import shutil
import stat
import subprocess
import time

from ... import config, i18n, journal
from . import scanner

RECYCLE = "recycle"
PERMANENT = "permanent"
CONFIRM_WORD = "DELETE"
LOG_NAME = "log.csv"
RETRIES = 5


class SpaceError(Exception):
    """Echec lisible : le message s'affiche tel quel."""


# --------------------------------------------------------------------------
# Garde-fous
# --------------------------------------------------------------------------

def confirmed(text):
    """Le mot de confirmation a-t-il ete tape ? (sans tenir compte des majuscules ni des espaces)"""
    return str(text).strip().lower() == CONFIRM_WORD.lower()


def current_document_path():
    """Chemin du projet ouvert dans ce Metashape ("" si aucun)."""
    try:
        import Metashape
        return Metashape.app.document.path or ""
    except Exception:
        return ""


def same_path(a, b):
    return bool(a) and bool(b) and os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def refusal(project):
    """Raison pour laquelle on ne touche pas a ce projet ("" si on peut)."""
    if project.in_use:
        return "ouvert dans un Metashape (fichier lock tenu)"
    if not project.orphan and same_path(project.path, current_document_path()):
        return "ouvert dans ce Metashape"
    return ""


def delete_refusal(project):
    """Raison absolue de ne pas supprimer ("" si on peut) : le projet ouvert ici ne part jamais.

    Un verrou tenu par un autre Metashape se contourne (ecran « Confirm delete ») : c'est `refusal` qui le dit."""
    if not project.orphan and same_path(project.path, current_document_path()):
        return "ouvert dans ce Metashape"
    return ""


def reveal(path):
    """Ouvre l'Explorateur sur le projet (fichier selectionne) ou, a defaut, sur son dossier.

    Renvoie True si l'Explorateur a ete lance."""
    path = os.path.normpath(path)
    try:
        if os.name == "nt" and os.path.exists(path):
            subprocess.Popen('explorer /select,"{}"'.format(path))
            return True
        folder = path if os.path.isdir(path) else os.path.dirname(path)
        if os.name == "nt" and os.path.isdir(folder):
            os.startfile(folder)
            return True
    except OSError:
        pass
    return False


def is_network(path):
    if path.startswith("\\\\"):
        return True
    try:
        drive = os.path.splitdrive(os.path.abspath(path))[0] + "\\"
        return ctypes.windll.kernel32.GetDriveTypeW(drive) == 4          # DRIVE_REMOTE
    except Exception:
        return False


def recycle_available(path):
    """La corbeille existe-t-elle pour ce chemin ? (Windows, disque local)"""
    return os.name == "nt" and not is_network(path)


# --------------------------------------------------------------------------
# Corbeille et suppression
# --------------------------------------------------------------------------

def recycle(paths):
    """Envoie des fichiers ou dossiers a la corbeille Windows. Renvoie (ok, message).

    Si un element depasse la taille de la corbeille, Windows pose la question (on n'ecrase pas sa
    confirmation) : un refus donne ok=False et rien n'est supprime."""
    paths = [os.path.abspath(p) for p in paths if os.path.exists(p)]
    if not paths:
        return True, ""
    if os.name != "nt":
        return False, "corbeille indisponible hors Windows"
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", ctypes.c_void_p),
                    ("pTo", ctypes.c_void_p), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    FO_DELETE, FOF_ALLOWUNDO, FOF_NOERRORUI = 3, 0x0040, 0x0400
    buffer = ctypes.create_unicode_buffer("\0".join(paths) + "\0\0")
    operation = SHFILEOPSTRUCTW()
    operation.wFunc = FO_DELETE
    operation.pFrom = ctypes.addressof(buffer)
    operation.fFlags = FOF_ALLOWUNDO | FOF_NOERRORUI
    code = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
    if code != 0 or operation.fAnyOperationsAborted:
        return False, "corbeille refusee (code {})".format(code)
    leftover = [p for p in paths if os.path.exists(p)]
    if leftover:
        return False, "pas tout a la corbeille : {}".format(leftover[0])
    return True, ""


def _unlock(function, path, _info):
    os.chmod(path, stat.S_IWRITE)
    function(path)


def remove_path(path):
    """Supprime definitivement un fichier ou un dossier (leve OSError si impossible)."""
    if os.path.isdir(path) and not os.path.islink(path):
        for attempt in range(RETRIES):
            try:
                shutil.rmtree(path, onerror=_unlock)
                return
            except OSError:
                if attempt == RETRIES - 1:
                    raise
                time.sleep(0.5)
    elif os.path.exists(path):
        try:
            os.remove(path)
        except PermissionError:
            os.chmod(path, stat.S_IWRITE)
            os.remove(path)


# --------------------------------------------------------------------------
# Journal
# --------------------------------------------------------------------------

def log_path():
    return os.path.join(config.settings_dir(), "fastspace", LOG_NAME)


def log_action(action, mode, project, ok, message="", size=0):
    """Ajoute une ligne au journal CSV. Ne leve jamais."""
    try:
        path = log_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        new = not os.path.exists(path)
        with open(path, "a", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, delimiter=";")
            if new:
                writer.writerow(["date", "action", "mode", "projet", "octets", "ok", "message"])
            writer.writerow([datetime.datetime.now().isoformat(timespec="seconds"), action, mode, project.path,
                             int(size), "oui" if ok else "non", message])
    except Exception as exc:
        journal.write("Fast Space : journal non ecrit : {!r}".format(exc))


# --------------------------------------------------------------------------
# Allegement
# --------------------------------------------------------------------------

def removable_paths(project, items=scanner.DEFAULT_ITEMS):
    """Dossiers (dense_cloud*, depth_maps*) et fichiers (orthomosaic*/images*.zip) du projet qui font partie de `items`."""
    found = []
    try:
        chunks = list(os.scandir(project.files_dir))
    except OSError:
        return found
    for chunk in chunks:
        if not chunk.is_dir(follow_symlinks=False):
            continue
        for frame in scanner._scan(chunk.path):
            if not frame.is_dir(follow_symlinks=False):
                continue
            for item in scanner._scan(frame.path):
                if not item.is_dir(follow_symlinks=False):
                    continue
                part = scanner.part_of(item.name)
                if part == scanner.DENSE and "dense" in items or part == scanner.DEPTH and "depth" in items:
                    found.append(item.path)
                elif part == scanner.ORTHO and "ortho" in items:
                    found.extend(entry.path for entry in scanner._scan(item.path)
                                 if entry.is_file(follow_symlinks=False) and scanner.ORTHO_IMAGES.match(entry.name))
    return found


def _open(path, read_only):
    import Metashape
    document = Metashape.Document()
    document.open(path, read_only=read_only)
    return document


def _dense_list(chunk):
    for name in ("point_clouds", "dense_clouds"):
        items = getattr(chunk, name, None)
        if items is not None:
            return name, list(items)
    return None, []


def _depth_list(chunk):
    items = getattr(chunk, "depth_maps_sets", None)
    if items is not None:
        return list(items)
    single = getattr(chunk, "depth_maps", None)
    return [single] if single is not None else []


def snapshot(document):
    """Etat d'un projet pour la verification : une ligne par chunk."""
    rows = []
    for chunk in document.chunks:
        rows.append({"label": chunk.label, "cameras": len(chunk.cameras), "markers": len(chunk.markers),
                     "dense": len(_dense_list(chunk)[1]), "depth": len(_depth_list(chunk))})
    return rows


def _orthomosaics(chunk):
    items = getattr(chunk, "orthomosaics", None)
    if items is not None:
        return list(items)
    single = getattr(chunk, "orthomosaic", None)
    return [single] if single is not None else []


def strip_chunk(chunk, items=scanner.DEFAULT_ITEMS):
    """Retire d'un chunk ce que `items` demande (dense, depth, ortho). Renvoie ce qui a ete retire."""
    removed = []
    if "dense" in items:
        _name, dense = _dense_list(chunk)
        if dense:
            chunk.remove(dense)
            removed.append("dense")
    if "depth" in items:
        sets = getattr(chunk, "depth_maps_sets", None)
        if sets:
            chunk.remove(list(sets))
            removed.append("depth")
        elif sets is None and getattr(chunk, "depth_maps", None) is not None:
            chunk.depth_maps = None
            removed.append("depth")
    if "ortho" in items:
        for mosaic in _orthomosaics(chunk):
            mosaic.removeOrthophotos()
            removed.append("ortho")
    return removed


def check_snapshots(before, after, items=scanner.DEFAULT_ITEMS):
    """None si le projet est comme avant (sauf ce que `items` retire), sinon le texte du probleme."""
    if len(before) != len(after):
        return "nombre de chunks different ({} -> {})".format(len(before), len(after))
    for old, new in zip(before, after):
        for key in ("cameras", "markers"):
            if old[key] != new[key]:
                return "chunk {} : {} different ({} -> {})".format(old["label"], key, old[key], new[key])
        if ("dense" in items and new["dense"]) or ("depth" in items and new["depth"]):
            return "chunk {} : il reste du dense ou des cartes de profondeur".format(old["label"])
    return None


def lighten(project, mode, progress=None, items=scanner.DEFAULT_ITEMS):
    """Allege un projet : retire ce que `items` demande (dense, depth, ortho). Renvoie {ok, freed, in_bin, error, steps}.

    A lancer dans le fil principal de Metashape (l'API ne sait pas travailler ailleurs).
    `progress(texte)` annonce chaque etape."""
    items = tuple(item for item in scanner.ITEMS if item in items)
    progress = progress or (lambda text: None)
    result = {"ok": False, "freed": 0, "in_bin": 0, "error": "", "steps": []}

    def step(text):
        result["steps"].append(text)
        progress(text)

    reason = refusal(project)
    if reason:
        result["error"] = reason
        return result
    if project.orphan:
        result["error"] = "pas de .psx : rien a alleger (supprimer le dossier)"
        return result
    if mode != PERMANENT:
        result["error"] = "l'allegement passe par Metashape, qui efface lui-meme ses fichiers : pas de corbeille"
        return result
    if not items:
        result["error"] = "rien a retirer : aucune case cochee"
        return result
    before = project.removable(items)
    try:
        if project.lock == scanner.STALE:
            # Un verrou laisse par une session morte (souvent un autre PC) force Metashape a ouvrir en lecture seule.
            step("verrou perime")
            try:
                os.remove(os.path.join(project.files_dir, "lock"))
            except OSError as exc:
                raise SpaceError(i18n.t("space.err_lock_remove", error=exc))
            project.lock = scanner.FREE
        step("lecture")
        document = _open(project.path, True)
        snap_before = snapshot(document)
        document = None
        gc.collect()
        step("nettoyage du projet")
        document = _open(project.path, False)
        for chunk in document.chunks:
            strip_chunk(chunk, items)
        document.save()
        document = None
        gc.collect()
        left = removable_paths(project, items)
        if left:
            step("dossiers restants")
            for path in left:
                remove_path(path)
        step("verification")
        document = _open(project.path, True)
        problem = check_snapshots(snap_before, snapshot(document), items)
        document = None
        gc.collect()
        if problem:
            raise SpaceError("verification : " + problem)
        after = scanner.measure_project(project.path)
        result["freed"] = max(0, before - after.removable(items))
        result["ok"] = True
        project.parts, project.files_size = after.parts, after.files_size
    except Exception as exc:
        text = str(exc) if isinstance(exc, SpaceError) else repr(exc)
        if "read-only mode" in text:
            text = i18n.t("space.err_read_only", owner=project.lock_owner or scanner.lock_owner(project.files_dir) or "?")
        result["error"] = text
    return result


# --------------------------------------------------------------------------
# Suppression du projet
# --------------------------------------------------------------------------

def delete_project(project, mode, force=False):
    """Supprime le projet (.psx et .files seulement), ou le dossier orphelin. Renvoie {ok, freed, in_bin, error}.

    `force` : l'utilisateur a confirme malgre un verrou tenu par un autre Metashape ; seul le projet
    ouvert dans ce Metashape reste refuse."""
    result = {"ok": False, "freed": 0, "in_bin": 0, "error": ""}
    reason = delete_refusal(project) if force else refusal(project)
    if reason:
        result["error"] = reason
        return result
    size = project.total
    targets = [project.files_dir] if project.orphan else [project.path, project.files_dir]
    try:
        for target in targets:
            if len(os.path.abspath(target)) < 8:
                raise SpaceError("chemin trop court pour etre un projet : " + target)
        if mode == RECYCLE:
            ok, message = recycle(targets)
            if not ok:
                raise SpaceError(message)
            result["in_bin"] = size
        else:
            for target in targets:                          # le .psx d'abord : un reste = un orphelin visible
                remove_path(target)
            result["freed"] = size
        result["ok"] = True
    except Exception as exc:
        result["error"] = str(exc) if isinstance(exc, SpaceError) else repr(exc)
    return result
