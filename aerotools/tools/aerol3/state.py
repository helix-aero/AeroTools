"""Aero-L3 UI: what the wizard knows and the rules that enable its buttons. No Qt, no Metashape: tested without a display."""

from ... import config
from . import checks
from . import plan as plan_module

SECTION = "aerol3"
PHOTOS, CHECKS, PROJECT, RUN = range(4)
STEP_KEYS = ("photos", "checks", "project", "run")


# Options that the plan and the checks never read: changing one of them leaves the analysis as it is (the others redo it).
NO_REPLAN = frozenset(("chunk_name", "frame", "calibration_fixed", "quick_test", "quick_test_photos", "pixel_cm", "ortho_epsg", "steps"))


class WizardState(object):
    def __init__(self):
        self.root = ""                       # the folder chosen on the Photos screen
        self.scan = None                     # scan.ScanResult
        self.selection = {}                  # {flight key: ticked}
        self.options = plan_module.Options()
        self.normalise_steps()
        self.analysis = None                 # checks.Analysis of the ticked flights
        self.result = None                   # backend.RunResult
        self.auto_name = ""                  # the chunk name proposed last (a name the operator typed is kept)

    # -- the scan ---------------------------------------------------------------------------------

    def set_scan(self, scan):
        self.scan = scan
        self.result = None
        self.selection = checks.default_selection(scan.flights) if scan is not None and scan.flights else {}
        self.refresh_analysis()

    def has_photos(self):
        return bool(self.scan is not None and self.scan.records)

    def selected_flights(self):
        if self.scan is None:
            return []
        return [f for f in self.scan.flights if self.selection.get(f.key)]

    def set_selected(self, key, ticked):
        self.selection[key] = bool(ticked)
        self.refresh_analysis()

    # -- the analysis -----------------------------------------------------------------------------

    def refresh_analysis(self):
        flights = self.selected_flights()
        self.analysis = checks.analyse(flights, self.options) if flights else None
        if self.analysis is not None:
            proposed = checks.default_chunk_name(self.analysis.summary)
            if not self.options.chunk_name or self.options.chunk_name == self.auto_name:
                self.options.chunk_name = proposed
            self.auto_name = proposed
        return self.analysis

    def set_option(self, key, value):
        """Changes one option; the analysis is made again (the plan depends on them)."""
        setattr(self.options, key, value)
        if key in NO_REPLAN and self.analysis is not None:
            return self.analysis
        return self.refresh_analysis()

    # -- the buttons ------------------------------------------------------------------------------

    def can_next(self, step):
        if step == PHOTOS:
            return self.has_photos() and self.analysis is not None
        if step == CHECKS:
            return self.analysis is not None and self.analysis.ok
        if step == PROJECT:
            return self.analysis is not None and self.analysis.ok and bool(self.options.chunk_name.strip())
        return False

    def normalise_steps(self):
        """The repair of flipped cameras follows the alignment (it has no tick of its own on screen)."""
        steps = self.options.steps
        steps["repair"] = bool(steps.get("align"))

    def set_step(self, key, ticked):
        """Ticks or unticks one step of the chain. Align carries the repair of flipped cameras with it, and what comes after it needs it."""
        steps = self.options.steps
        steps[key] = bool(ticked)
        if key == "align":
            steps["repair"] = bool(ticked)
            if not ticked:
                for later in ("optimize", "dem", "ortho"):
                    steps[later] = False
        elif ticked:
            steps["align"] = True
            steps["repair"] = True

    def steps_to_run(self):
        """Keys of the steps after the creation that are ticked, in order."""
        keys = []
        if self.options.quick_test:
            keys.append("quick_test")
        for key in plan_module.STEP_KEYS:
            if self.options.steps.get(key):
                keys.append(key)
        # steps that need the alignment come after it: ticking one of them alone is meaningless, so the alignment is added
        if any(k in keys for k in ("repair", "optimize", "dem", "ortho")) and "align" not in keys:
            keys.append("align")
        order = ("quick_test", "align", "repair", "optimize", "dem", "ortho")
        return [k for k in order if k in keys]


def load_prefs(state):
    data = config.section(SECTION)
    state.root = data.get("last_folder", "") if isinstance(data.get("last_folder", ""), str) else ""
    saved = data.get("options")
    if isinstance(saved, dict):
        options = plan_module.Options.from_dict(saved)
        options.chunk_name = ""             # the name belongs to a site, not to the machine
        state.options = options
        state.normalise_steps()


def save_prefs(state):
    settings = config.load_settings()
    section = dict(settings.get(SECTION, {})) if isinstance(settings.get(SECTION), dict) else {}
    section["last_folder"] = state.root
    saved = state.options.as_dict()
    saved.pop("chunk_name", None)
    section["options"] = saved
    settings[SECTION] = section
    config.save_settings(settings)
