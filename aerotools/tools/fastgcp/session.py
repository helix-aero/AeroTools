"""FastGCP session state.

A single object shared by the panel, the click filter and the photos filter.
It lives in this module rather than in tool.py so that nobody has to
import tool.py (and therefore panel.py) just to read an option.

The options are read back at startup from the machine's settings and
written again at each change: each PC keeps its habits. Three of
them (Photos filter, following of the Reference selection, move to the next
photo) are fixed: see FIXED.
"""

from ... import config
from . import history

SECTION = "fastgcp"

# Former values of the "after marking" option. The move to the next photo
# is now fixed (photos.advance_after_place); the constants stay defined
# for the modules that still import them.
AFTER_STAY = "stay"
AFTER_PAGEDOWN = "pagedown"
AFTER_API = "api"

AFTER_LABELS = (
    (AFTER_PAGEDOWN, "Next (list)"),
    (AFTER_API, "Next (project)"),
    (AFTER_STAY, "Stay"),
)

# Options that are no longer choices: value imposed at each load,
# any write is ignored (an old settings.json does not change them).
FIXED = {
    "sync_selection": True,
    "auto_filter": True,
    "after_place": AFTER_API,
}

# Removed options: neither read nor written.
RETIRED = ("click_only",)

# ai_auto_review: the review window opens by itself at the end of an AI detection.
DEFAULTS = dict(FIXED, collect_dataset=False, show_log=False, ai_auto_review=True)

# Hidden setting (no option in the interface): image width, in pixels,
# visible around the marker when FastGCP zooms the photo view. To be written by
# hand in settings.json, section fastgcp: "zoom_window_px": 400.
ZOOM_KEY = "zoom_window_px"
ZOOM_LEGACY_KEYS = ("zoom_fenetre_px", "zoom_px")      # older spellings of the same setting, still read
ZOOM_DEFAULT_PX = 400
ZOOM_MIN_PX = 20
ZOOM_MAX_PX = 20000


def read_zoom_px(stored):
    """Zoom window width, read from the settings; falls back to 400."""
    for key in (ZOOM_KEY,) + ZOOM_LEGACY_KEYS:
        value = stored.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if ZOOM_MIN_PX <= value <= ZOOM_MAX_PX:
            return float(value)
    return float(ZOOM_DEFAULT_PX)


# Hidden setting: maximum offset, in pixels, of the snapping of the click onto the
# centre of the target (see snap.py). 0 (default) turns the snapping off. To be written
# by hand in settings.json, section fastgcp: "snap_max_px": 3.5.
#
# Off by default: measured on 3 November 2026 on CITADELLE_Pompeani (93
# manual markings, 11 snapped). Against the reprojection of the 3D point, the
# manual markings are at 0.76 px on average, after snapping at 1.83 px. In
# a zoomed view the click is already more accurate than the checker crossing found
# on these targets.
SNAP_KEY = "snap_max_px"
SNAP_LEGACY_KEYS = ("recalage_max_px",)                # older spelling of the same setting, still read
SNAP_DEFAULT_PX = 0.0
SNAP_MAX_PX = 20.0


def read_snap_px(stored):
    """Maximum snapping offset, read from the settings; falls back to 0 (off)."""
    for key in (SNAP_KEY,) + SNAP_LEGACY_KEYS:
        value = stored.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
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
        # (photo key, marker label) already zoomed, from the oldest
        # to the most recent: see photos.zoom_on_marker.
        self.zoomed = []
        # AI detection (aigcp.py): computation in progress, proposals placed.
        self.aigcp_run = None
        self.aigcp_applied = []
        self.aigcp_review = None
        self.aigcp_log = []
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

    # -- current marker --------------------------------------------------

    def active_label(self):
        if self.active_marker is None:
            return None
        try:
            return self.active_marker.label
        except RuntimeError:
            # Reference became invalid (chunk change, marker deleted).
            self.active_marker = None
            return None

    def forget_chunk(self):
        # The history references die with the chunk.
        history.clear()
        self.active_marker = None
        self.last_selected_label = None
        self.last_filter_key = None
        self.last_cameras = []
        self.zoomed = []
        self.aigcp_applied = []
        self.aigcp_review = None
        self.aigcp_log = []


_SESSION = None


def current():
    """FastGCP session, created on the first call."""
    global _SESSION
    if _SESSION is None:
        _SESSION = Session()
    return _SESSION


def reset():
    global _SESSION
    _SESSION = None
