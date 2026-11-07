"""Aero-Facade UI: what the wizard knows and the rules that enable its buttons. No Qt, no Metashape: tested without a display.

`WizardState` holds the search outline, the facades (with their removed flags), the margins of each facade, the method, the pixel
size and the results of the long steps. The wizard windows read and write it; the rules (`can_run`, `can_next`...) are plain
functions of it.
"""

from ... import config
from .model import Availability, Margins, Method, active

SECTION = "aerofacade"
SEARCH, FACADES, MARGINS, METHOD, BUILD, PICTURES, EXPORT = range(7)
STEP_KEYS = ("search", "facades", "margins", "method", "build", "pictures", "export")
FORMATS = ("png", "jpg", "tif", "jp2", "ecw")
DEFAULT_PIXEL_CM = 3.0


class WizardState(object):
    def __init__(self):
        self.polygon = None                       # closed outline, [(x, y)] metres of the picture
        self.polygon_problem = "few"              # "" when the outline can be used
        self.detected_for = None                  # the outline (tuple) the current facades were detected for
        self.facades = []                         # every detected facade, removed ones included
        self.version = 0                          # grows each time a new detection replaces the facades
        self.default_margins = Margins()          # what a facade gets until the operator changes it
        self.margins = {}                         # letter -> Margins
        self.apply_all = True
        self.method = ""                          # key of a Method ("" until screen 4 chose)
        self.pixel_cm = DEFAULT_PIXEL_CM
        self.availability = Availability()
        self.dem_state = {}                       # letter -> (ok, message) of the last DEM build, or (True, "") when it existed
        self.ortho_state = {}                     # letter -> (ok, message)
        self.ortho_key = None                     # signature the orthophotos were built for
        self.skipped_build = False                # screen 5 was skipped because every DEM existed
        self.build_choice = None                  # letters ticked on screen 5 (the walls to build); None = every wall
        self.picture_choice = None                # letters ticked on screen 6 (the ones screen 7 exports); None = every picture that is built

    # -- the outline ------------------------------------------------------------------------------

    def set_polygon(self, polygon, problem=""):
        self.polygon = [(float(x), float(y)) for x, y in polygon] if polygon else None
        self.polygon_problem = problem if self.polygon else "few"

    def polygon_key(self):
        return tuple(self.polygon) if self.polygon else None

    def can_run(self):
        """Run is allowed only with a closed outline that is usable."""
        return bool(self.polygon) and not self.polygon_problem

    def detection_is_current(self):
        return bool(self.facades) and self.detected_for is not None and self.detected_for == self.polygon_key()

    # -- facades ----------------------------------------------------------------------------------

    def set_facades(self, facades, polygon_key=None):
        self.facades = list(facades)
        self.version += 1
        self.detected_for = polygon_key if polygon_key is not None else self.polygon_key()
        self.margins = {f.letter: self.default_margins.copy() for f in self.facades}
        self.invalidate_builds()

    def active(self):
        return active(self.facades)

    def set_wall_extent(self, letter, x0, y0, z0, length):
        """The operator moved an end of a wall: its new extent. Every surface and picture built so far is out of date."""
        facade = self.facade(letter)
        if facade is None:
            return False
        facade.set_extent(x0, y0, z0, length)
        self.invalidate_builds()
        return True

    def reset_walls(self, letters):
        """Back to the detected extent for these walls. Returns the letters that really changed."""
        changed = [f.letter for f in self.facades if f.letter in set(letters) and f.reset_extent()]
        if changed:
            self.invalidate_builds()
        return changed

    def letters(self):
        return [f.letter for f in self.active()]

    def facade(self, letter):
        for f in self.facades:
            if f.letter == letter:
                return f
        return None

    # -- margins ----------------------------------------------------------------------------------

    def margins_of(self, letter):
        if letter not in self.margins:
            self.margins[letter] = self.default_margins.copy()
        return self.margins[letter]

    def margins_map(self):
        """{letter: Margins copy} of the facades that are kept."""
        return {f.letter: self.margins_of(f.letter).copy() for f in self.active()}

    def apply_margin(self, letters, name, value):
        """Set one margin ("left", "right", "top", "bottom", "depth") on these facades (the link flags of each decide the twin side)."""
        for letter in letters:
            getattr(self.margins_of(letter), "set_" + name)(value)
        getattr(self.default_margins, "set_" + name)(value)
        self.invalidate_builds()

    def apply_link(self, letters, pair, linked):
        for letter in letters:
            getattr(self.margins_of(letter), "set_link_" + pair)(linked)
        getattr(self.default_margins, "set_link_" + pair)(linked)
        self.invalidate_builds()

    def set_all_margins(self, margins):
        """The same margins on every facade (the "apply to all" switch turned on)."""
        for f in self.facades:
            self.margins[f.letter] = margins.copy()
        self.default_margins = margins.copy()
        self.invalidate_builds()

    # -- results ----------------------------------------------------------------------------------

    def signature(self):
        """What the built DEMs and orthophotos depend on: the kept facades, their margins, the method and the pixel size."""
        rows = tuple((f.letter, self._extent_of(f), tuple(sorted(self.margins_of(f.letter).to_dict().items()))) for f in self.active())
        return (rows, self.method, round(self.pixel_cm, 4))

    def dem_signature(self):
        rows = tuple((f.letter, self._extent_of(f), tuple(sorted(self.margins_of(f.letter).to_dict().items()))) for f in self.active())
        return (rows, self.method)

    @staticmethod
    def _extent_of(facade):
        """Where the wall starts and how long it is (centimetres): a wall edited by hand is another wall for the builds."""
        return (round(facade.x0, 2), round(facade.y0, 2), round(facade.length, 2))

    def invalidate_builds(self):
        self.dem_state = {}
        self.ortho_state = {}
        self.ortho_key = None
        self.skipped_build = False
        self.picture_choice = None                # other walls or margins: new pictures, everything is ticked again
        self.build_choice = None

    def build_letters(self):
        """The walls to build: the kept walls that are ticked on screen 5 (all of them until the operator unticks one)."""
        return [f.letter for f in self.active() if self.build_choice is None or f.letter in self.build_choice]

    def set_build_choice(self, letters):
        """Tick exactly these walls on screen 5. Everything ticked is stored as 'no choice'."""
        wanted = set(letters)
        self.build_choice = None if wanted >= set(f.letter for f in self.active()) else wanted

    def dem_ok_letters(self):
        """Every kept wall whose surface is built (ticked or not)."""
        return [f.letter for f in self.active() if self.dem_state.get(f.letter, (False, ""))[0]]

    def build_ok_letters(self):
        """The ticked walls whose surface is built: the only ones the next screens work on."""
        wanted = set(self.build_letters())
        return [letter for letter in self.dem_ok_letters() if letter in wanted]

    def ortho_ok_letters(self):
        """The ticked walls whose orthophoto is built."""
        wanted = set(self.build_letters())
        return [f.letter for f in self.active() if f.letter in wanted and self.ortho_state.get(f.letter, (False, ""))[0]]

    def chosen_letters(self):
        """The pictures to export: the built ones that are ticked on screen 6 (all of them until the operator ticks)."""
        return [letter for letter in self.ortho_ok_letters() if self.picture_choice is None or letter in self.picture_choice]

    # -- rules ------------------------------------------------------------------------------------

    def can_next(self, step):
        if step == SEARCH:
            return self.detection_is_current()
        if step == FACADES:
            return bool(self.active())
        if step == MARGINS:
            return bool(self.active())
        if step == METHOD:
            return bool(self.method) and self.availability.ok(self.method) and bool(self.active())
        if step == BUILD:
            return bool(self.build_ok_letters())
        if step == PICTURES:
            return bool(self.chosen_letters())
        return False


# -- what is remembered between two sessions -----------------------------------------------------------

def load_prefs(state):
    """Put the last margins, method, pixel size and switches into `state`. A wrong stored value is ignored."""
    saved = config.section(SECTION)
    try:
        state.default_margins = Margins.from_dict(saved.get("margins"))
    except Exception:
        state.default_margins = Margins()
    method = saved.get("method")
    if method in [m.value for m in Method]:
        state.method = method
    try:
        pixel = float(saved.get("pixel_cm", DEFAULT_PIXEL_CM))
        if 0.1 <= pixel <= 100:
            state.pixel_cm = pixel
    except (TypeError, ValueError):
        pass
    state.apply_all = saved.get("apply_all") is not False
    return saved


def save_prefs(state, **extra):
    """Write the choices worth remembering. `extra` holds the export options (fmt, georef_txt, dxf)."""
    data = config.load_settings()
    section = data.get(SECTION)
    if not isinstance(section, dict):
        section = {}
        data[SECTION] = section
    section["margins"] = state.default_margins.to_dict()
    if state.method:
        section["method"] = state.method
    section["pixel_cm"] = state.pixel_cm
    section["apply_all"] = bool(state.apply_all)
    section.update(extra)
    return config.save_settings(data)
