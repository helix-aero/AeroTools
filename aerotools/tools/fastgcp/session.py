"""Etat de la session FastGCP.

Un seul objet partage par le panneau, le filtre de clics et le filtre photos.
Il vit dans ce module plutot que dans tool.py pour que personne n'ait a
importer tool.py (et donc panel.py) juste pour lire une option.

Les options sont relues au demarrage depuis les reglages du poste et
reecrites a chaque changement : chaque PC garde ses habitudes. Trois d'entre
elles (filtre Photos, suivi de la selection Reference, passage a la photo
suivante) sont fixes : voir FIXED.
"""

from ... import config
from . import history

SECTION = "fastgcp"

# Anciennes valeurs de l'option « apres pointage ». Le passage a la photo
# suivante est desormais fixe (photos.advance_after_place) ; les constantes
# restent definies pour les modules qui les importent encore.
AFTER_STAY = "stay"
AFTER_PAGEDOWN = "pagedown"
AFTER_API = "api"

AFTER_LABELS = (
    (AFTER_PAGEDOWN, "Suivante (liste)"),
    (AFTER_API, "Suivante (projet)"),
    (AFTER_STAY, "Rester"),
)

# Options qui ne sont plus des choix : valeur imposee a chaque chargement,
# toute ecriture est ignoree (un ancien settings.json ne les change pas).
FIXED = {
    "sync_selection": True,
    "auto_filter": True,
    "after_place": AFTER_API,
}

# Options supprimees : ni lues, ni ecrites.
RETIRED = ("click_only",)

DEFAULTS = dict(FIXED, collect_dataset=False, show_log=False)

# Reglage cache (aucune option dans l'interface) : largeur d'image, en pixels,
# visible autour du marqueur quand FastGCP zoome la vue photo. A ecrire a la
# main dans settings.json, section fastgcp : "zoom_fenetre_px": 400.
ZOOM_KEY = "zoom_fenetre_px"
ZOOM_KEY_ALIAS = "zoom_px"
ZOOM_DEFAULT_PX = 400
ZOOM_MIN_PX = 20
ZOOM_MAX_PX = 20000


def read_zoom_px(stored):
    """Largeur de fenetre du zoom, lue dans les reglages ; repli sur 400."""
    for key in (ZOOM_KEY, ZOOM_KEY_ALIAS):
        value = stored.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if ZOOM_MIN_PX <= value <= ZOOM_MAX_PX:
            return float(value)
    return float(ZOOM_DEFAULT_PX)


# Reglage cache : decalage maximal, en pixels, du recalage du clic sur le centre
# de la cible (voir snap.py). 0 (defaut) coupe le recalage. A ecrire a la main
# dans settings.json, section fastgcp : "recalage_max_px": 3.5.
#
# Coupe par defaut : mesure du 3 novembre 2026 sur CITADELLE_Pompeani (93
# pointages manuels, 11 recales). Par rapport a la reprojection du point 3D, les
# pointages a la main sont a 0,76 px en moyenne, apres recalage a 1,83 px. Dans
# une vue zoomee le clic est deja plus juste que le croisement de damier retrouve
# sur ces cibles.
SNAP_KEY = "recalage_max_px"
SNAP_DEFAULT_PX = 0.0
SNAP_MAX_PX = 20.0


def read_snap_px(stored):
    """Decalage maximal du recalage, lu dans les reglages ; repli sur 0 (coupe)."""
    value = stored.get(SNAP_KEY)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return SNAP_DEFAULT_PX
    if 0 <= value <= SNAP_MAX_PX:
        return float(value)
    return SNAP_DEFAULT_PX


class Session(object):
    def __init__(self):
        self.enabled = True
        self.active_marker = None
        self.panel = None
        self.click_filter = None
        self.timer = None
        self.shortcut = None
        self.undo_shortcut = None
        self.undo_listener = None
        self.last_photo_count = 0
        self.last_cameras = []
        self.last_selected_label = None
        self.last_chunk_key = None
        self.last_filter_key = None
        self.last_diagnostic = {}
        # (cle de la photo, etiquette du marqueur) deja zoomes, du plus ancien
        # au plus recent : voir photos.zoom_on_marker.
        self.zoomed = []
        # Detection IA (aigcp.py) : calcul en cours, propositions posees.
        self.aigcp_run = None
        self.aigcp_applied = []
        self.zoom_px = float(ZOOM_DEFAULT_PX)
        self.snap_px = SNAP_DEFAULT_PX
        self.options = dict(DEFAULTS)
        self.load_options()

    # -- options ---------------------------------------------------------

    def load_options(self):
        stored = config.section(SECTION)
        for key, default in DEFAULTS.items():
            value = stored.get(key, default)
            if isinstance(default, bool) and not isinstance(value, bool):
                value = default
            self.options[key] = value
        self.options.update(FIXED)
        self.zoom_px = read_zoom_px(stored)
        self.snap_px = read_snap_px(stored)

    def option(self, key):
        return self.options.get(key, DEFAULTS.get(key))

    def set_option(self, key, value):
        if key in FIXED or key in RETIRED:
            return
        if self.options.get(key) == value:
            return
        self.options[key] = value
        config.put(SECTION, key, value)

    # -- marqueur courant ------------------------------------------------

    def active_label(self):
        if self.active_marker is None:
            return None
        try:
            return self.active_marker.label
        except RuntimeError:
            # Reference devenue invalide (changement de chunk, marqueur efface).
            self.active_marker = None
            return None

    def forget_chunk(self):
        # Les references de l'historique meurent avec le chunk.
        history.clear()
        self.active_marker = None
        self.last_selected_label = None
        self.last_filter_key = None
        self.last_cameras = []
        self.zoomed = []
        self.aigcp_applied = []


_SESSION = None


def current():
    """Session FastGCP, creee au premier appel."""
    global _SESSION
    if _SESSION is None:
        _SESSION = Session()
    return _SESSION


def reset():
    global _SESSION
    _SESSION = None
