"""What the Aero-PPK wizard knows and when each step may be left. Pure logic, no Qt (tested without a display)."""

from . import basket, devices, outputs

DRONE, FILES, MODE, BASE, RESULTS, EXPORT = 0, 1, 2, 3, 4, 5
STEPS = (DRONE, FILES, MODE, BASE, RESULTS, EXPORT)
MODE_SINGLE, MODE_OWN = "single", "own"                            # the two ways to give the base of the Base step
NET_MODES = (MODE_SINGLE,)                                         # the one that takes a station of the networks
CORRECTION = "ppk"                         # the only correction type today


def supported_names():
    """The drones Aero-PPK reads today (the names of the readers of `drones/`)."""
    return [choice.name for choice in devices.choices() if choice.reader is not None]


class WizardState(object):
    def __init__(self):
        self.all_flights = []              # every flight read (dicts: reader, files, events, needs, warnings), the excluded ones too
        self.unread = []                   # (folder, photo count) of the folders no reader understood
        self.skipped = set()               # folders of the flights the user left out: listed, never computed, exported or written
        self.device_id = ""                # id of a reader of `drones/`, or `devices.OTHER`
        self.antenna_z = None              # metres, typed by the user when the antenna of the device is not known
        self.mode = MODE_SINGLE            # which mode of the Mode step feeds the calculation
        self.basket = basket.Basket(limit=1)
        self.radius_km = basket.DEFAULT_RADIUS                 # the stations around are listed and shown on the map
        self.one_s_only = True             # only stations that record every second (when they are known to)
        self.networks = None               # network ids ticked by the user in the network filter; None = every network around the site
        self.margin_h = basket.DEFAULT_MARGIN_H
        self.own = None                    # ownbase.Recognition
        self.own_position = None           # ownbase.Position, None while it is not valid
        self.own_frame = ""                # key of `frames.OWN_FRAMES`: the frame the user says the typed base position is in ("" = not said)
        self.own_base = None               # pipeline.BaseData made from the two above when the calculation starts
        self.outputs = outputs.OutputOptions()
        self.results = []

    # -- drone and files ------------------------------------------------------------------------

    @property
    def flights(self):
        """The flights that count: every flight read minus the excluded ones. Everything that computes, exports or writes reads this."""
        return [f for f in self.all_flights if not self.is_skipped(f)]

    @flights.setter
    def flights(self, value):
        """A new list of flights replaces the old one, and nothing is excluded any more."""
        self.all_flights = list(value)
        self.skipped = set()

    def is_skipped(self, flight):
        return flight["files"].folder in self.skipped

    def skip_flight(self, folder, on=True):
        """Leave a flight out (or put it back). The last included flight cannot be left out. True when something changed."""
        known = any(f["files"].folder == folder for f in self.all_flights)
        if not known:
            return False
        if on:
            if folder in self.skipped or len([f for f in self.flights if f["files"].folder != folder]) == 0:
                return False
            self.skipped.add(folder)
            return True
        if folder not in self.skipped:
            return False
        self.skipped.discard(folder)
        return True

    def blocking_gaps(self):
        """List of (flight name, need key) that stop the calculation."""
        out = []
        for flight in self.flights:
            for need in flight["needs"]:
                if need.level == "blocking" and not need.present:
                    out.append((flight["files"].name, need.key))
        return out

    def choice(self):
        return devices.choice_by_id(self.device_id)

    def antenna_state(self):
        """"known", "manual" (typed and valid), "needed" (not known and nothing valid typed) or "none" (no reader for this device)."""
        choice = self.choice()
        if choice.reader is None:
            return "none"
        if choice.antenna_known:
            return "known"
        return "manual" if self.antenna_z is not None else "needed"

    def unsupported(self):
        """Photos were read but no reader of `drones/` knows this device: nothing to fill in, the user is told to ask for the drone."""
        return self.antenna_state() == "none" and bool(self.all_flights or self.unread)

    def rover_ready(self):
        return bool(self.flights) and not self.blocking_gaps() and self.antenna_state() in ("known", "manual")

    def drone_ready(self):
        """A flight was read and the device and its antenna are fine (the gaps of the files are the Files step's business)."""
        return bool(self.all_flights) and self.antenna_state() in ("known", "manual")

    # -- mode and base --------------------------------------------------------------------------------

    def set_mode(self, mode):
        """Single base or own base."""
        if mode not in (MODE_SINGLE, MODE_OWN):
            return
        self.mode = mode


    def radius(self):
        return self.radius_km


    def own_ready(self):
        return self.own is not None and self.own.ok() and self.own_position is not None

    def base_ready(self):
        if self.mode == MODE_OWN:
            return self.own_ready()
        return self.basket.can_continue()

    # -- steps ----------------------------------------------------------------------------------

    def can_next(self, step):
        if step == DRONE:
            return self.drone_ready()
        if step == FILES:
            return self.rover_ready()
        if step == MODE:
            return True
        if step == BASE:
            return self.base_ready()
        if step == RESULTS:
            return bool(self.results)
        return False                                   # EXPORT: its screen has its own `next_enabled`

    def prepared_bases(self):
        """The base objects the calculation takes, nearest first for the known stations."""
        if self.mode == MODE_OWN:
            return [self.own_base] if self.own_base is not None else []
        return [item.base for item in self.basket.used()]
