"""Pose le menu « Aero » dans Metashape et charge les outils a la demande.

Mecanisme du menu permanent
---------------------------
Au lancement, Metashape execute tous les fichiers .py presents dans
`%LOCALAPPDATA%\\Agisoft\\Metashape Pro\\scripts\\`. On y installe un lanceur
d'une dizaine de lignes (voir install/installer_aerotools.bat) qui ajoute la
racine AeroTools du serveur au sys.path puis appelle `install_menu()`. Le menu
Aero reapparait donc a chaque ouverture, sans rien lancer d'autre.

Chargement tardif
-----------------
`install_menu()` n'enregistre que des entrees de menu : aucun outil, aucun
widget Qt n'est construit au demarrage. Le code d'un outil n'est importe qu'au
premier clic sur son entree. Les rappels de menu passent tous par
`launch(tool_id)`, donc recharger le code ne demande pas de reenregistrer le
menu — ce que l'API Metashape ne sait pas defaire.

Robustesse
----------
Un serveur injoignable ou un outil casse ne doit jamais empecher Metashape de
demarrer : tout est sous try/except et se contente d'ecrire dans la console.
"""

import builtins
import importlib
import os
import sys

_MENU_FLAG = "_AEROTOOLS_MENU_INSTALLED"
_LAST_TOOL_FLAG = "_AEROTOOLS_LAST_TOOL"
_ROOT_FLAG = "_AEROTOOLS_ROOT"

MENU = "Aero"


# --------------------------------------------------------------------------
# Racine du depot
# --------------------------------------------------------------------------

def root():
    """Dossier qui contient le paquet « aerotools »."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    setattr(builtins, _ROOT_FLAG, here)
    return here


def ensure_on_path():
    here = root()
    if here not in sys.path:
        sys.path.insert(0, here)
    return here


# --------------------------------------------------------------------------
# Menu
# --------------------------------------------------------------------------

def install_menu(force=False):
    """Enregistre les entrees du menu Aero. Idempotent.

    Le drapeau vit sur `builtins` et non dans un module : il survit donc au
    purge de sys.modules fait par `reload_all()`, ce qui evite d'empiler des
    entrees de menu en double a chaque rechargement.
    """
    if getattr(builtins, _MENU_FLAG, False) and not force:
        return False
    ensure_on_path()
    try:
        from . import config, i18n, msapi
    except Exception as exc:
        print("[AeroTools] chargement impossible : {!r}".format(exc))
        return False

    # Le menu est pose une seule fois par session de Metashape, dans la langue du
    # reglage de ce moment : un changement de langue s'y applique au prochain
    # demarrage (l'API de Metashape ne sait pas retirer ni renommer une entree).
    added = 0
    for entry in config.TOOLS:
        name = entry["menu"]
        if not entry.get("pret", False):
            name = "{} {}".format(name, i18n.t("menu.soon"))
        label = "{}/{}".format(MENU, name)
        callback = _make_launcher(entry["id"])
        if msapi.add_menu_item(label, callback, entry.get("raccourci")):
            added += 1

    msapi.add_menu_item("{}/{}".format(MENU, i18n.t("menu.update")), update_plugins)
    msapi.add_menu_item("{}/{}".format(MENU, i18n.t("menu.reload")), reload_all)
    msapi.add_menu_item("{}/{}".format(MENU, i18n.t("menu.about")), about)

    setattr(builtins, _MENU_FLAG, True)
    print(
        "[AeroTools] menu « {} » installe ({} outil(s)) - racine {}".format(
            MENU, added, root()
        )
    )
    return True


def _make_launcher(tool_id):
    def _run():
        launch(tool_id)

    _run.__name__ = "aero_launch_" + tool_id
    return _run


# --------------------------------------------------------------------------
# Chargement des outils
# --------------------------------------------------------------------------

def launch(tool_id, reload=False):
    """Charge un outil et l'installe. Renvoie True si l'outil a demarre."""
    ensure_on_path()
    from . import config, i18n, journal, msapi

    entry = config.tool(tool_id)
    if entry is None:
        msapi.message_box(i18n.t("boot.unknown_tool", tool=tool_id))
        return False

    if not entry.get("pret", False):
        msapi.message_box(i18n.t("boot.not_ready", title=entry["titre"]))
        return False

    if reload:
        purge_modules()

    module_name = "aerotools.tools.{}.tool".format(tool_id)
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        journal.write("echec du chargement de {} : {!r}".format(tool_id, exc))
        msapi.message_box(
            i18n.t("boot.load_failed", tool=tool_id, error=repr(exc))
        )
        return False

    try:
        module.install()
    except Exception as exc:
        journal.write("echec du demarrage de {} : {!r}".format(tool_id, exc))
        msapi.message_box(
            i18n.t("boot.start_failed", tool=tool_id, error=repr(exc))
        )
        return False

    setattr(builtins, _LAST_TOOL_FLAG, tool_id)
    return True


def purge_modules():
    """Oublie tous les modules aerotools pour relire le code du serveur.

    Les outils ouverts sont fermes proprement avant le purge : sans cela,
    leurs filtres d'evenements Qt et leurs timers continueraient de tourner
    dans l'ancien code.
    """
    for name in sorted(sys.modules):
        if not name.startswith("aerotools"):
            continue
        module = sys.modules.get(name)
        closer = getattr(module, "uninstall", None)
        if callable(closer) and name.endswith(".tool"):
            try:
                closer()
            except Exception:
                pass
    for name in [n for n in sys.modules if n.startswith("aerotools")]:
        del sys.modules[name]


def reload_all():
    """Relit le code depuis le serveur et rouvre le dernier outil utilise."""
    last = getattr(builtins, _LAST_TOOL_FLAG, None)
    here = root()
    purge_modules()
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        module = importlib.import_module("aerotools.bootstrap")
    except Exception as exc:
        print("[AeroTools] rechargement impossible : {!r}".format(exc))
        return False
    print("[AeroTools] code recharge depuis {}".format(here))
    if last:
        return module.launch(last)
    return True


# --------------------------------------------------------------------------
# Mises a jour
# --------------------------------------------------------------------------

def update_plugins():
    """Menu Aero > Mises a jour : ouvre la fenetre, recharge le code si quelque chose a change."""
    ensure_on_path()
    from . import i18n, journal, msapi
    try:
        from . import update_dialog
        update_dialog.open_and_reload(msapi.main_window())
    except Exception as exc:
        journal.write("echec de la fenetre de mises a jour : {!r}".format(exc))
        msapi.message_box(i18n.t("boot.update_failed", error=repr(exc)))


# --------------------------------------------------------------------------
# A propos
# --------------------------------------------------------------------------

def about():
    ensure_on_path()
    from . import __version__, config, i18n, msapi

    soon = i18n.t("menu.soon")
    outils = "\n".join(
        "    {}  {}{}".format(
            entry["titre"],
            i18n.t(entry["sous_titre"]),
            "" if entry.get("pret") else "  " + soon,
        )
        for entry in config.TOOLS
    )
    msapi.message_box(
        i18n.t(
            "boot.about",
            version=__version__,
            vendor=config.VENDOR,
            tools=outils,
            root=root(),
            settings=config.settings_path(),
        )
    )
