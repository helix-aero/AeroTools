"""Step 2 of the Aero-PPK wizard: the base. Two modes under one segmented control: "Single base" (one station of a public network, the
default) and "Your own base" (RINEX files of your own receiver and where it stood). The network page: the settings in one strip on top,
the stations on the left, the map and the time axis in the middle, the basket on the right."""

import os

from ... import config, uikit
from ...qt import QtCore, QtGui, QtWidgets
from ...uikit import tokens
from . import antex, basket as B, flights as flight_tools, frames, gnsstime, ownbase, runner, state as S, stationfilter, timeline
from .mapview import MapView
from .stations import catalog, orbits, rgp, survey as survey_tools
from .ui_common import Screen, clear_layout, fmt_time, glyph, heading, one_line, tr
from .ui_views import TimelineView
from .widgets import NetChip, NetworkFilter, OrbitMark, PickRow, qcolor

SECTION = "aeroppk"
SHORT = {"G": "GPS", "R": "GLO", "E": "GAL", "C": "BDS", "J": "QZS", "I": "NAV", "S": "SBS"}
MAX_LIST = 60                                   # stations placed on the map at once (nearest first)
LIST_ROWS = 8                                   # stations listed in single base mode (the list scrolls inside itself)
ASK_AT_ONCE = 4                                 # stations the servers are asked about at the same time
Qt = QtCore.Qt


def systems_text(letters):
    return " ".join(SHORT.get(c, c) for c in (letters or ""))


def utc(moment):
    return gnsstime.gps_to_utc(moment) if moment is not None else None


def utc_span(window):
    """(start, end) UTC of a pipeline.Window."""
    return utc(window.start), utc(window.end)


def flight_days(window):
    """The UTC dates a pipeline.Window touches (two when the flight crosses midnight)."""
    return orbits.days_between(*utc_span(window))


def orbit_tip(answer):
    """The words under the satellite: what exists, from whom, how late, and that the solver does not use it."""
    return "\n".join(tr(key, **fields) for key, fields in orbits.words(answer))


def _transparent(area):
    """A scroll area that shows what is behind it (the kit's cards), not the palette's base colour."""
    area.viewport().setAutoFillBackground(False)
    area.viewport().setBackgroundRole(QtGui.QPalette.NoRole)
    area.widget().setAutoFillBackground(False)
    area.widget().setBackgroundRole(QtGui.QPalette.NoRole)


def _setting(caption, widget, info=None):
    """One item of the settings strip: a muted caption, the control, an optional "i"."""
    box = uikit.hbox(spacing=tokens.S)
    box.addWidget(uikit.label(caption, "small", "muted", wrap=False))
    box.addWidget(widget)
    if info is not None:
        box.addWidget(info)
    return box


# ---------------------------------------------------------------------------------------------------------------------
# Basket row
# ---------------------------------------------------------------------------------------------------------------------

class BasketRow(PickRow):
    """A thin row: mark (spinner while it downloads, tick when done, cross when it failed), the name, and under it in small type the data
    rate, the satellite systems and the distance. Click: show it on the time axis. Right click: take it out."""

    picked = QtCore.Signal(str)
    removed = QtCore.Signal(str)

    def __init__(
            self,
            item,
            selected=False,
    ):
        super(BasketRow, self).__init__(selected)
        self.code = item.code
        row = uikit.hbox((tokens.S, tokens.XS, tokens.XS, tokens.XS), tokens.S, parent=self)
        texts = uikit.vbox()
        name = item.station.name or ""
        top = uikit.hbox(spacing=tokens.S)
        self.title = uikit.ElidedLabel("%s  %s" % (item.code, name) if name else item.code, "strong")      # the station is what the user chose: semibold
        self._plain(self.title)
        top.addWidget(self.title, 1)
        kind = {B.RUNNING: "running", B.DONE: "ok", B.ERROR: "stop"}.get(item.state)
        self.glyph = glyph(kind or "pending")
        self.glyph.setVisible(kind is not None)
        if item.state == B.ERROR and item.error:
            self.glyph.setToolTip(item.error)
        top.addWidget(self.glyph)
        texts.addLayout(top)
        info = item.info or {}
        network = item.network
        bits = []
        if info.get("rate"):
            bits.append(B.rate_text(info.get("rate")))
        bits.append("%s km" % ("%.1f" % (item.distance_m / 1000.0)))
        if info.get("systems"):                                      # last: when the row is narrow it is the list of systems that is cut
            bits.append(systems_text(info["systems"]))
        low = uikit.hbox(spacing=tokens.S)
        self.sub = uikit.ElidedLabel(" · ".join(b for b in bits if b), "small", "muted")
        self._plain(self.sub)
        low.addWidget(self.sub, 1)
        if network is not None:
            low.addWidget(NetChip(network.short, network.color))
        texts.addLayout(low)
        row.addLayout(texts, 1)
        detail = [item.station.name or item.code]
        if network is not None:
            detail.append(tr("aeroppk.base.net_tip", network=network.name, frame=network.frame_label))
        if info.get("receiver"):
            detail.append("%s / %s" % (info.get("receiver"), info.get("antenna") or "-"))
        if info.get("product"):
            detail.append(info["product"])
        first, last = info.get("first"), info.get("last")
        if first and last:
            detail.append("%s - %s UTC" % (fmt_time(utc(first), False), fmt_time(utc(last), False)))
        tip_key = "aeroppk.base.row_tip_one"
        self.setToolTip("\n".join(detail + [tr(tip_key)]))

    @staticmethod
    def _plain(text):
        """A line of the row is no text field: a click on it is a click on the row, and the tooltip is the row's."""
        text.setTextInteractionFlags(Qt.NoTextInteraction)
        text.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        text.setToolTip("")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.picked.emit(self.code)
        super(BasketRow, self).mousePressEvent(event)

    def contextMenuEvent(self, event):
        menu = QtWidgets.QMenu(self)
        action = menu.addAction(tr("aeroppk.base.remove"))
        chosen = menu.exec_(event.globalPos()) if hasattr(menu, "exec_") else menu.exec(event.globalPos())
        if chosen is action:
            self.removed.emit(self.code)


# ---------------------------------------------------------------------------------------------------------------------
# The network mode: single base
# ---------------------------------------------------------------------------------------------------------------------

class NetPage(QtWidgets.QWidget):
    def __init__(self, screen):
        super(NetPage, self).__init__()
        self.screen = screen
        self.wizard = screen.wizard
        self.state = screen.state
        self.stations = []                 # stations placed on the map (nearest first)
        self.candidates = []               # the stations inside the radius, of the networks that are on (nearest first)
        self.offers = {}                   # code -> survey.Offer: what the servers said for this flight window
        self._offer_key = None
        self._stamp = object()             # changes when what the servers said is no longer valid
        self._checking_shown = False
        self._asking = set()               # codes the servers are being asked about
        self._list_key = None
        self._list_codes = []              # the station of each row of the list
        self._silent = False
        self.manual = set()                # stations the user put in the basket by hand
        self.dropped = set()               # stations the user took out of the automatic choice
        self.auto_code = ""                # single base: the station Aero-PPK picked by itself (the user did not choose it)
        self.auto_stopped = False          # the user chose or removed a station on this visit: no more automatic choice
        self.selected = ""
        self.window = None                 # pipeline.Window of the flights, GPS time
        self.site = None
        outer = uikit.vbox(spacing=tokens.M, parent=self)
        outer.addLayout(self._build_settings())
        body = uikit.hbox(spacing=tokens.L)
        body.addWidget(self._build_left())
        body.addLayout(self._build_middle(), 1)
        body.addWidget(self._build_basket())
        outer.addLayout(body, 1)
        self._update_radius_label()

    # -- construction -----------------------------------------------------------------------------

    def _build_settings(self):
        """The settings in one strip: networks, 1 s only, the data around the flight, the search radius."""
        strip = uikit.hbox(spacing=tokens.L)                            # four settings in one strip: it must fit a 1366 px screen with a side taskbar
        self.netfilter = NetworkFilter(catalog.PROVIDERS)
        self.netfilter.changed.connect(self._on_networks)
        strip.addLayout(_setting(tr("aeroppk.base.networks"), self.netfilter, uikit.InfoButton(self._networks_tip())))
        self.one_s = uikit.Toggle(tr("aeroppk.base.one_s"), self.state.one_s_only)
        self.one_s.toggled.connect(self._on_one_s)
        row = uikit.hbox(spacing=tokens.S)
        row.addWidget(self.one_s)
        row.addWidget(uikit.InfoButton(tr("aeroppk.base.one_s_tip")))
        strip.addLayout(row)
        self.margin = uikit.Segmented([(str(h), tr("aeroppk.base.margin_h", h=h)) for h in B.MARGINS_H], str(self.state.margin_h))
        self.margin.changed.connect(self._on_margin)
        strip.addLayout(_setting(tr("aeroppk.base.margin"), self.margin, uikit.InfoButton(tr("aeroppk.base.margin_tip"))))
        self.slider = QtWidgets.QSlider(Qt.Horizontal)
        self.slider.setObjectName("aeroSlider")
        self.slider.setRange(0, len(B.RADIUS_STEPS) - 1)
        self.slider.setFixedWidth(uikit.px(tokens.FIELD_W))
        self.slider.setValue(B.slider_from_radius(self.state.radius()))
        self.slider.valueChanged.connect(self._on_radius)
        self.radius_label = uikit.label("", "body", wrap=False)
        self.radius_label.setMinimumWidth(uikit.px(tokens.XXL + tokens.S))
        self.radius_info = uikit.InfoButton(tr("aeroppk.base.radius_tip_single"))
        radius = uikit.hbox(spacing=tokens.S)
        radius.addWidget(uikit.label(tr("aeroppk.base.radius"), "small", "muted", wrap=False))
        radius.addWidget(self.slider)
        radius.addWidget(self.radius_label)
        radius.addWidget(self.radius_info)
        strip.addLayout(radius)
        strip.addStretch(1)
        return strip

    @staticmethod
    def _networks_tip():
        return tr("aeroppk.base.networks_tip") + "\n" + tr("aeroppk.base.networks_frame_tip")

    def _build_left(self):
        """The column left of the map: the stations around."""
        column = QtWidgets.QWidget()
        column.setFixedWidth(uikit.px(tokens.POPOVER_W + tokens.XXL + tokens.S))
        box = uikit.vbox(spacing=tokens.S, parent=column)
        # single base: the stations around, one choice
        self.list_box = QtWidgets.QWidget()
        lbox = uikit.vbox(spacing=tokens.S, parent=self.list_box)
        head = uikit.hbox(spacing=tokens.S)
        head.addWidget(uikit.label(tr("aeroppk.base.stations"), "strong", wrap=False))
        head.addStretch(1)
        head.addWidget(uikit.label(tr("aeroppk.base.nearest"), "small", "muted", wrap=False))
        lbox.addLayout(head)
        self.station_list = uikit.DataTable([uikit.Column("station", tr("aeroppk.base.c_station"), stretch=True),
                                             uikit.Column("data", tr("aeroppk.base.c_data")),                     # (as wide as its widest text: "no data that day")
                                             uikit.Column("km", tr("aeroppk.base.c_km"), align="right")],
                                            sortable=False)
        one_line(self.station_list)
        self.station_list.selectionChanged.connect(self._on_list_selected)
        lbox.addWidget(self.station_list, 1)
        self.list_note = uikit.label("", "small", "muted", wrap=False)
        lbox.addWidget(self.list_note)
        box.addWidget(self.list_box, 1)
        return column

    def _build_middle(self):
        mid = uikit.vbox(spacing=tokens.M)
        self.map = MapView(cache_dir=os.path.join(rgp.cache_dir(), "tiles"))
        self.map.setMinimumHeight(uikit.px(tokens.CONTROL_H * 3))
        self.map.stationClicked.connect(self._on_map_click)
        mid.addWidget(self.map, 1)
        self.axis = TimelineView()
        self.axis.rowClicked.connect(self.select)
        self.axis_area = QtWidgets.QScrollArea()                       # many bases: the axis scrolls inside itself, the map keeps its room
        self.axis_area.setWidgetResizable(True)
        self.axis_area.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.axis_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.axis_area.setWidget(self.axis)
        _transparent(self.axis_area)
        mid.addWidget(self.axis_area)
        return mid

    def _build_basket(self):
        frame = QtWidgets.QFrame()
        frame.setProperty("ui", "card")
        frame.setAttribute(Qt.WA_StyledBackground, True)
        frame.setFixedWidth(uikit.px(tokens.POPOVER_W - tokens.L))
        box = uikit.vbox((tokens.M,) * 4, tokens.S, parent=frame)
        self.basket_title = heading(tr("aeroppk.base.basket"), tr("aeroppk.base.basket_tip_single"))
        self.basket_info = self.basket_title.itemAt(1).widget()
        self.orbit_mark = OrbitMark()
        self.orbit_mark.setVisible(False)
        self.basket_title.addWidget(self.orbit_mark)
        box.addLayout(self.basket_title)
        self.orbit_days = None
        self.rows_box = uikit.vbox(spacing=tokens.XS)
        rows = QtWidgets.QWidget()
        rows.setLayout(self.rows_box)
        self.rows_area = QtWidgets.QScrollArea()                       # a long basket scrolls inside itself, the window does not grow
        self.rows_area.setWidgetResizable(True)
        self.rows_area.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.rows_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.rows_area.setMinimumHeight(uikit.px(tokens.CONTROL_H * 2))
        self.rows_area.setWidget(rows)
        _transparent(self.rows_area)
        self.empty = uikit.label(tr("aeroppk.base.basket_empty"), "body", "muted", wrap=False)
        box.addWidget(self.empty)
        box.addWidget(self.rows_area, 1)
        self.download_button = uikit.Button(tr("aeroppk.base.download"), "secondary", handler=self.screen.download)
        box.addWidget(self.download_button)
        status = uikit.hbox(spacing=tokens.S)
        self.status = uikit.ElidedLabel("", "small", "muted")
        status.addWidget(self.status, 1)
        self.frame_glyph = glyph("warn")                                # a warning is colour + icon + words
        self.frame_glyph.hide()
        status.addWidget(self.frame_glyph)
        self.frame_line = uikit.ElidedLabel("", "small", "muted", fit=True)
        status.addWidget(self.frame_line)
        self.frame_info = uikit.InfoButton(tr("aeroppk.base.frame_tip"))
        status.addWidget(self.frame_info)
        box.addLayout(status)
        return frame

    # -- data -------------------------------------------------------------------------------------

    def start(self):
        """Called when the step is shown: the site, the flight window, the stations around."""
        flights = self.state.flights
        self.site = flight_tools.site_of(flights)
        self.window = runner.union_window(flights, 60.0)
        key = (self.window.start, self.window.end, self.state.margin_h) if self.window is not None else None
        self.auto_stopped = False                                        # a new visit: the automatic choice may work again
        if key != self._offer_key:                                       # another flight or margin: what the servers said is stale
            self._offer_key = key
            self.offers = {}
            self._asking = set()
            self._stamp = object()
        self.fill_stations()
        self.begin()
        self.check_orbits()

    def apply_mode(self):
        """What the mode changes on the page: the radius (nothing is asked of a server)."""
        self.slider.blockSignals(True)
        self.slider.setValue(B.slider_from_radius(self.state.radius()))
        self.slider.blockSignals(False)
        self._update_radius_label()
        self.manual, self.dropped, self.selected = set(), set(), ""
        self.auto_code, self.auto_stopped = "", False


    def on_mode(self):
        """The mode changed (the basket was emptied by the state when it had to be)."""
        self.apply_mode()
        self._list_key = None
        self.fill_stations()
        self.begin()
        self.map.fit()

    def begin(self):
        """Ask the servers about the stations that may be a choice."""
        self._ask_more()
        self._offers_changed()

    def check_orbits(self):
        """The satellite of the basket: which orbits exist for the flight days. Asked in the background, silent when offline."""
        days = flight_days(self.window) if self.window is not None else None
        self.orbit_days = days
        self.orbit_mark.setVisible(days is not None)
        if days is None:
            return
        self.orbit_mark.set_state(orbits.BROADCAST, orbit_tip(None))

        def work(post, cancelled):
            return orbits.level_of_days(days)

        def done(job):
            if days != self.orbit_days:                                  # the flights changed while the servers were asked
                return
            answer = job.result if job.state == "done" and job.result is not None else orbits.Answer(sure=False)
            self.orbit_mark.set_state(answer.level, orbit_tip(answer))
        self.wizard.run_side(work, done)

    def fill_stations(self):
        self.stations = []
        self.candidates = []
        if self.site is None:
            self.map.set_stations([])
            return
        day = self.window.start.date() if self.window else None
        radius = self.state.radius()
        counts = catalog.counts(self.site[0], self.site[1], radius, day)
        enabled = stationfilter.enabled_networks(self.state.networks, counts)
        self.netfilter.set_state(counts, enabled, self.state.networks is None)
        self.candidates = catalog.nearby(catalog.load(enabled), self.site[0], self.site[1], radius, day)[:MAX_LIST]
        self.stations = list(self.candidates)
        for item in self.state.basket.items:                      # a chosen station stays on the map even outside the radius
            if all(s.code != item.code for s in self.stations):
                self.stations.append(item.station)
        self.map.set_site(self.site[0], self.site[1], self.site[2])
        self.map.set_radii_km([radius])
        self.push_map()

    def push_map(self):
        out = []
        for station in self.stations:
            item = self.state.basket.get(station.code)
            info = item.info if item else {}
            rate = info.get("rate")
            offer = self.offers.get(station.code)
            if not rate and offer is not None and offer.available():
                rate = offer.interval
            mark = "na" if not rate else ("ok" if rate <= 1.0 else "warn")
            network = catalog.provider_of(station)
            lines = []
            if network is not None:
                lines.append("%s · %s" % (network.short, network.frame_short))
            if rate:
                lines.append("%s · %s" % (B.rate_text(rate), systems_text(info.get("systems") or (offer.systems if offer is not None else ""))))
            elif offer is not None and not offer.available() and not offer.error:
                lines.append(tr("aeroppk.base.no_data_short"))
            order = self.state.basket.codes().index(station.code) + 1 if item else 0
            out.append({"code": station.code, "name": station.name, "lat": station.lat, "lon": station.lon,
                        "distance_m": station.distance_m, "selected": item is not None, "state": mark, "tooltip": lines,
                        "order": order, "net": network.short if network else "", "net_color": network.color if network else ""})
        self.map.set_stations(out)

    # -- what the servers say ---------------------------------------------------------------------

    def _wanted(self):
        return LIST_ROWS


    def finding(self):
        """Single base mode: the basket is empty and the servers are still being asked, so a station will be picked by itself."""
        return (self.state.mode == S.MODE_SINGLE and self.window is not None and not self.state.basket.items and not self.auto_stopped
                and bool(self._asking or stationfilter.next_to_ask(self.candidates, self.offers, self.state.one_s_only, self._wanted())))

    def _ask_more(self):
        """Start asking about the next stations (a few at a time, nearest first). In single base mode only as many as the list needs."""
        if self.window is None or self.state.mode == S.MODE_OWN:
            return
        todo = stationfilter.next_to_ask(self.candidates, self.offers, self.state.one_s_only, self._wanted())
        for station in todo:
            if len(self._asking) >= ASK_AT_ONCE:
                break
            if station.code not in self._asking:
                self.ask(station)

    def ask(self, station):
        """Ask what a station offers for the flight (a few small requests, in the background)."""
        window, hours, stamp = self.window, self.state.margin_h, self._stamp
        if window is None or station.code in self._asking or station.code in self.offers:
            return
        self._asking.add(station.code)

        def work(post, cancelled):
            return runner.survey_one(station, window, hours, post, cancelled)

        def done(job):
            if stamp is not self._stamp:                                 # the flight or the margin changed meanwhile
                return
            self._asking.discard(station.code)
            offer = job.result if job.state == "done" and job.result is not None else None
            if offer is None:
                offer = survey_tools.Offer(station)
                offer.error = job.error or "failed"
            self.offers[station.code] = offer
            self._ask_more()
            self._offers_changed()
        self.wizard.run_side(work, done)

    def _apply_offer(self, item):
        """What the servers said about a station, on its basket row."""
        offer = self.offers.get(item.code)
        if offer is None:
            return
        if item.base is None:
            item.info = runner.info_of(None, offer) or item.info
        if not offer.available() and not offer.error and item.state == B.PENDING:
            item.state = B.ERROR
            item.error = tr("aeroppk.base.err_" + offer.problem) if offer.problem else tr("aeroppk.base.no_data")

    def _offers_changed(self):
        self._auto_pick()
        for item in self.state.basket.items:
            self._apply_offer(item)
        self.refresh_list()
        self.screen.basket_changed()

    def _auto_pick(self):
        """Single base mode: the best station with data (1 s first, then the nearest) goes in the basket by itself. It is not a choice of
        the user: a better answer from the servers replaces it, until the user chooses or removes a station, or it is downloaded."""
        if self.state.mode != S.MODE_SINGLE or self.auto_stopped:
            return
        items = self.state.basket.items
        if items and (items[0].code != self.auto_code or items[0].state != B.PENDING):
            return
        offers = [self.offers[s.code] for s in self.candidates if s.code in self.offers and s.code not in self.dropped]
        best = stationfilter.auto_pick(offers, 1, self.state.one_s_only)
        station = next((s for s in self.stations if best and s.code == best[0]), None)
        if station is None or (items and items[0].code == station.code):
            return
        if self.state.basket.add(station):
            self.auto_code = station.code
            self.selected = station.code
            self._list_key = None


    # -- the list of single base mode -------------------------------------------------------------

    def refresh_skin(self):
        """The skin changed: the list is filled again (its rows carry the colours of the skin)."""
        if self._list_codes:
            self.refresh_list()

    def refresh_list(self):
        """The nearest stations that may be a choice, with the chosen one marked. Rebuilt only when it changed."""
        if self.state.mode != S.MODE_SINGLE:
            return
        shown = stationfilter.candidates(self.candidates, self.offers, self.state.one_s_only)[:LIST_ROWS]
        chosen = self.state.basket.codes()[0] if self.state.basket.items else ""
        if chosen and all(s.code != chosen for s in shown):
            shown = [self.state.basket.items[0].station] + shown[:LIST_ROWS - 1]
        if len(shown) < LIST_ROWS:                                 # room left: the nearest stations known to have nothing that day, greyed
            empty = [s for s in self.candidates if s.code in self.offers and not self.offers[s.code].available()
                     and not self.offers[s.code].error and all(s.code != t.code for t in shown)]
            shown = shown + empty[:LIST_ROWS - len(shown)]
        key = (tuple(s.code for s in shown), chosen, tuple((s.code, (self.offers[s.code].interval if s.code in self.offers else 0)) for s in shown))
        if key == self._list_key:
            return
        self._list_key = key
        rows, disabled = [], {}
        for number, station in enumerate(shown):
            offer = self.offers.get(station.code)
            rate = B.rate_text(offer.interval) if offer is not None and offer.available() else ""
            nothing = offer is not None and not offer.available() and not offer.error       # asked, and nothing usable that day
            why = ""
            if nothing:
                rate = tr("aeroppk.base.unusable_short" if offer.problem else "aeroppk.base.no_data_short")
                why = tr("aeroppk.base.err_" + offer.problem) if offer.problem else tr("aeroppk.base.no_data")
                disabled[number] = why
            name = ("%s  %s" % (station.code, station.name)) if station.name else station.code
            rows.append([name, rate, "%.1f km" % ((station.distance_m or 0.0) / 1000.0)])
        self._list_codes = [s.code for s in shown]
        self._silent = True
        try:
            self.station_list.set_rows(rows, disabled=list(disabled))            # a row with nothing usable is greyed by the kit
            table = self.station_list.table
            for number in range(len(shown)):
                for column in range(table.columnCount()):
                    item = table.item(number, column)
                    if item is None:
                        continue
                    if number in disabled:
                        item.setToolTip(disabled[number])
                    elif column == 0:
                        item.setToolTip(shown[number].name or shown[number].code)
            if chosen in self._list_codes:
                self.station_list.select_row(self._list_codes.index(chosen))
        finally:
            self._silent = False
        total = len(self.candidates)
        asking = len(self._asking)
        if not self.candidates:
            self.list_note.setText(tr("aeroppk.base.none_inside", km=self.state.radius()))
        elif asking and len(shown) < LIST_ROWS:
            self.list_note.setText(tr("aeroppk.base.checking", done=len(self.offers), total=total))
        else:
            self.list_note.setText("")
        self.list_note.setVisible(bool(self.list_note.text()))

    def _on_list_selected(self):
        if self._silent:
            return
        rows = self.station_list.selected_rows()
        if rows and 0 <= rows[0] < len(self._list_codes):
            self._on_list_pick(self._list_codes[rows[0]])

    def _on_list_pick(self, code):
        station = next((s for s in self.stations if s.code == code), None)
        if station is None or self.state.basket.get(code) is not None:
            return
        if self._put(station):
            self.screen.basket_changed()
            self.refresh_list()

    def _put(self, station):
        """The user chose a station (map or list): it goes in the basket, and the servers are asked about it if they were not."""
        added = self.state.basket.add(station)
        if added:
            self.auto_stopped = True
            self.selected = station.code
            self.manual.add(station.code)
            self.dropped.discard(station.code)
            if self.state.mode == S.MODE_SINGLE:
                self.manual = set([station.code])
            if station.code not in self.offers:
                self.ask(station)
            else:                                          # the servers already answered: the row shows it at once
                self._apply_offer(self.state.basket.get(station.code))
        return added

    # -- the basket -------------------------------------------------------------------------------

    def _on_map_click(self, code):
        station = next((s for s in self.stations if s.code == code), None)
        if station is None:
            return
        if self.state.basket.get(code) is not None:
            self.remove(code)
            return
        self._put(station)
        self._list_key = None
        self.screen.basket_changed()
        self.refresh_list()

    def remove(self, code):
        self.state.basket.remove(code)
        self.auto_stopped = True
        self.manual.discard(code)
        self.dropped.add(code)
        if self.selected == code:
            self.selected = ""
        self._list_key = None
        self.screen.basket_changed()
        self.refresh_list()

    def select(self, code):
        if self.state.basket.get(code) is not None:
            self.selected = code
            self.refresh()


    def refresh(self):
        clear_layout(self.rows_box)
        items = self.state.basket.items
        extra = lambda item: {}
        for item in items:
            row = BasketRow(item, item.code == self.selected, **extra(item))
            row.picked.connect(self.select)
            row.removed.connect(self.remove)
            row.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)
            self.rows_box.addWidget(row, 0, Qt.AlignTop)                 # a single row keeps its own height instead of filling the card
        if self.state.mode == S.MODE_SINGLE:
            self.empty.setText(tr("aeroppk.base.basket_finding" if self.finding() else "aeroppk.base.basket_empty"))
        self.empty.setVisible(not items)
        mixed = self.state.basket.frames_mixed()
        frames = self.state.basket.frames_text(short=True) if items else ""
        self.frame_line.setFullText(tr("aeroppk.base.frame_line", frames=frames) if frames else "")
        uikit.set_tone(self.frame_line, "warning" if mixed else "muted")
        self.frame_glyph.setVisible(bool(mixed and frames))
        self.frame_info.setVisible(bool(frames))
        busy = self.screen.busy()
        searching = False
        self.download_button.setEnabled(bool(self.state.basket.todo()) and not busy and not searching and self.window is not None)
        self.download_button.setText(tr("aeroppk.base.downloading") if busy else tr("aeroppk.base.download"))
        self._fill_axis()
        self.push_map()

    def _fill_axis(self):
        if self.window is None:
            self.axis.set_rows([], self.state.margin_h)
            self._size_axis()
            return
        span = utc_span(self.window)
        rows = []
        for item in self.state.basket.items:
            info = item.info or {}
            first = utc(info.get("first")) if item.state == B.DONE else None
            last = utc(info.get("last")) if item.state == B.DONE else None
            rows.append((item.code, first, last))
        self.axis.set_rows(timeline.build_rows(span, self.state.margin_h, rows, tr("aeroppk.base.flight")), self.state.margin_h)
        self.axis.set_selected(self.selected)
        self._size_axis()

    AXIS_ROWS = 4                                                      # rows of the time axis shown at once

    def _size_axis(self):
        """The axis area is as high as the axis, up to `AXIS_ROWS` rows (then it scrolls)."""
        rows = len(self.axis.rows())
        full = self.axis.sizeHint().height()
        height = full if rows <= self.AXIS_ROWS else self.axis._row_h() * self.AXIS_ROWS + (full - self.axis._row_h() * rows)
        self.axis_area.setFixedHeight(height + 2)

    # -- settings ---------------------------------------------------------------------------------

    def _update_radius_label(self):
        self.radius_label.setText("%d km" % B.radius_from_slider(self.slider.value()))

    def _on_radius(self, _value):
        km = B.radius_from_slider(self.slider.value())
        self.state.radius_km = km
        config.put(SECTION, "radius_km", km)
        self._update_radius_label()
        self._fresh_choice()
        self.map.fit()


    def _on_networks(self):
        """The user ticked or unticked a network (or asked for the automatic choice)."""
        self.state.networks = self.netfilter.chosen()
        config.put(SECTION, "networks", self.state.networks)
        self._fresh_choice()
        self.map.fit()

    def _on_one_s(self, checked):
        self.state.one_s_only = bool(checked)
        config.put(SECTION, "one_s_only", bool(checked))
        self._fresh_choice()


    def _fresh_choice(self):
        """The radius, the networks or the 1 s filter changed: the automatic choice starts again (what was taken out comes back)."""
        self.dropped = set()
        self.auto_stopped = False
        self._list_key = None
        self.fill_stations()
        self.begin()

    def _on_margin(self, key):
        self.state.margin_h = int(key)
        config.put(SECTION, "margin_h", self.state.margin_h)
        if self.state.basket.done():
            self.state.basket.clear_results()
            self.status.setFullText(tr("aeroppk.base.margin_reset"))
        self._offer_key = None                                           # the servers are asked again for the wider window
        self.start()
        self.screen.basket_changed()


# Your own base
# ---------------------------------------------------------------------------------------------------------------------

class DropArea(QtWidgets.QFrame):
    """A card that takes files dropped on it."""

    filesDropped = QtCore.Signal(list)

    def __init__(self):
        super(DropArea, self).__init__()
        self.setProperty("ui", "card")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.filesDropped.emit(paths)
            event.acceptProposedAction()


class OwnPage(QtWidgets.QWidget):
    def __init__(self, screen):
        super(OwnPage, self).__init__()
        self.screen = screen
        self.wizard = screen.wizard
        self.state = screen.state
        self.paths = []
        self.prefilled = False
        self.header_xyz = None
        self._exclusive = False
        outer = uikit.hbox(spacing=tokens.XL, parent=self)
        # left: the files
        left = uikit.vbox(spacing=tokens.M)
        left.addLayout(heading(tr("aeroppk.own.files"), tr("aeroppk.own.files_tip")))
        self.drop = DropArea()
        self.drop.filesDropped.connect(self.add_paths)
        box = uikit.vbox((tokens.M,) * 4, tokens.S, parent=self.drop)
        self.hint = uikit.label(tr("aeroppk.own.drop"), "body", "muted")
        box.addWidget(self.hint)
        self.slots = QtWidgets.QGridLayout()
        self.slots.setHorizontalSpacing(uikit.px(tokens.S))
        self.slots.setVerticalSpacing(uikit.px(tokens.XS))
        box.addLayout(self.slots)
        row = uikit.hbox(spacing=tokens.S)
        row.addWidget(uikit.Button(tr("aeroppk.own.browse"), "secondary", handler=self.browse))
        row.addWidget(uikit.Button(tr("aeroppk.own.clear"), "quiet", handler=self.clear))
        row.addStretch(1)
        box.addLayout(row)
        left.addWidget(self.drop)
        self.findings = uikit.vbox(spacing=tokens.XS)
        left.addLayout(self.findings)
        self.axis = TimelineView()
        left.addWidget(self.axis)
        left.addStretch(1)
        outer.addLayout(left, 1)
        # right: where is the base
        right = uikit.vbox(spacing=tokens.S)
        right.addLayout(heading(tr("aeroppk.own.where"), tr("aeroppk.own.where_tip")))
        self.warn = uikit.Banner("warning", tr("aeroppk.own.approx"), closable=False)
        self.warn.hide()
        right.addWidget(self.warn)
        self.lat, self.lon, self.height = uikit.TextField(), uikit.TextField(), uikit.TextField()
        self.g_pos = uikit.Disclosure(tr("aeroppk.own.g_position"), open=True)
        form = uikit.FormGrid()
        form.add_row(tr("aeroppk.own.lat"), self.lat, tr("aeroppk.own.pos_tip"), "lat")
        form.add_row(tr("aeroppk.own.lon"), self.lon, tr("aeroppk.own.pos_tip"), "lon")
        form.add_row(tr("aeroppk.own.h"), self.height, tr("aeroppk.own.h_tip"), "h")
        self.g_pos.add(form)
        right.addWidget(self.g_pos)
        self.g_mark = uikit.Disclosure(tr("aeroppk.own.g_marker"))
        self.mlat, self.mlon, self.mh = uikit.TextField(), uikit.TextField(), uikit.TextField()
        form = uikit.FormGrid()
        form.add_row(tr("aeroppk.own.lat"), self.mlat, tr("aeroppk.own.marker_tip"), "mlat")
        form.add_row(tr("aeroppk.own.lon"), self.mlon, tr("aeroppk.own.marker_tip"), "mlon")
        form.add_row(tr("aeroppk.own.h"), self.mh, tr("aeroppk.own.h_tip"), "mh")
        self.pole_edit = uikit.TextField("%.3f" % ownbase.DEFAULT_POLE, "2.000")
        form.add_row(tr("aeroppk.own.pole"), self.pole_edit, tr("aeroppk.own.pole_tip"), "pole")
        self.pc = uikit.TextField("", "0.000")
        form.add_row(tr("aeroppk.own.pc"), self.pc, tr("aeroppk.own.pc_tip"), "pc")
        self.pc_note = uikit.label("", "small", "muted")
        self.pc_note.hide()                                                # (it takes room only when it has something to say)
        form.add_wide(self.pc_note)
        form.layout().setSpacing(uikit.px(tokens.S))                    # five rows: tighter, so the step fits a 768 px screen with a message shown
        self.g_mark.add(form)
        right.addWidget(self.g_mark)
        # the frame is asked whichever way the position is given: it is required to write the positions into the project
        frame_form = uikit.FormGrid()
        self.frame_box = uikit.ChoiceBox([("", tr("aeroppk.own.frame_unset"))] + [(key, entry[0]) for key, entry in frames.OWN_FRAMES.items()])
        self.frame_box.set_value(getattr(self.state, "own_frame", "") or "")
        self.frame_box.currentIndexChanged.connect(self._frame_changed)
        self.frame_row = frame_form.add_row(tr("aeroppk.own.frame_label"), self.frame_box, tr("aeroppk.own.frame_pick_tip"), "frame")
        left.insertWidget(left.count() - 1, frame_form)                 # under the files: the right column keeps room for the position fields
        self._show_frame_error()
        # one group opens while the other closes. The other is closed as the mouse goes down (before the group opens): showing a group
        # makes the window measure its content at once, with both open it would be taller than the screen for a moment.
        self.g_pos.head.pressed.connect(lambda: self._exclusive_open(self.g_mark, not self.g_pos.is_open()))
        self.g_mark.head.pressed.connect(lambda: self._exclusive_open(self.g_pos, not self.g_mark.is_open()))
        self.g_pos.toggled.connect(lambda on: self._exclusive_open(self.g_mark, on))
        self.g_mark.toggled.connect(lambda on: self._exclusive_open(self.g_pos, on))
        self.g_pos.toggled.connect(lambda *_a: self.update_state())
        self.g_mark.toggled.connect(lambda *_a: self.update_state())
        for edit in (self.lat, self.lon, self.height, self.mlat, self.mlon, self.mh, self.pole_edit, self.pc):
            edit.textEdited.connect(self._edited)
        self.result = uikit.vbox(spacing=tokens.XS)
        right.addLayout(self.result)
        right.addStretch(1)
        side = QtWidgets.QWidget()
        side.setLayout(right)
        side.setFixedWidth(uikit.px(tokens.POPOVER_W + 4 * tokens.XL))
        outer.addWidget(side)
        self.set_slots()

    # -- files ------------------------------------------------------------------------------------

    def browse(self):
        paths = self.wizard.pick_files(tr("aeroppk.own.browse_title"), "RINEX (*);;*.*", config.get(SECTION, "last_base_folder", ""))
        if paths:
            config.put(SECTION, "last_base_folder", os.path.dirname(paths[0]))
            self.add_paths(paths)

    def clear(self):
        self.paths = []
        self.state.own = None
        self.state.own_position = None
        self.set_slots()
        self.render_findings()
        self.screen.own_changed()

    def add_paths(self, paths):
        for path in paths:
            if os.path.isdir(path):
                continue
            if path not in self.paths:
                self.paths.append(path)
        self.recognise()

    def recognise(self):
        window = runner.union_window(self.state.flights, 0.0)
        flight = (window.start, window.end) if window else None
        rover_systems, rover_nav = None, False
        if self.state.flights:
            f = self.state.flights[0]
            rover_nav = f["files"].nav is not None
            try:
                from . import rinex
                rover_systems = rinex.read_obs_info(f["files"].obs).systems if f["files"].obs else None
            except Exception:
                rover_systems = None
        from . import rtklib
        crx = rtklib.find_programs(config.get(SECTION, "rtklib_dir", None)).get("crx2rnx")
        work = os.path.join(rgp.cache_dir(), "work", "own_base")
        paths = list(self.paths)

        def work_fn(post, cancelled):
            return ownbase.recognise(paths, work, crx, flight, rover_systems, rover_nav)
        self.screen.recognise_job(work_fn)

    def taken(self, rec):
        """The recognition came back (called by the screen)."""
        self.state.own = rec
        info = rec.obs_info
        if info is not None and info.approx_xyz is not None and not self._any_typed():
            self.header_xyz = info.approx_xyz
            lat, lon, h = ownbase.marker_from_header(info.approx_xyz)
            self.lat.setText("%.8f" % lat)
            self.lon.setText("%.8f" % lon)
            self.height.setText("%.3f" % h)
            self.mlat.setText("%.8f" % lat)
            self.mlon.setText("%.8f" % lon)
            self.mh.setText("%.3f" % h)
            self.prefilled = True
        if info is not None:
            self._prefill_antenna(info)
        self.set_slots()
        self.render_findings()
        self.update_state()
        self.screen.own_changed()

    def _prefill_antenna(self, info):
        height = info.antenna_hen[0] if info.antenna_hen else 0.0
        if height and 0.0 < height <= ownbase.MAX_POLE and not self._any_typed():
            self.pole_edit.setText("%.3f" % height)                       # the antenna height written in the file
        if not self.pc.text().strip():
            up = antex.phase_centre_up(antex.cache_path(), info.antenna) if info.antenna else None
            if up is not None:
                self.pc.setText("%.3f" % up)
                self.pc_note.setText(tr("aeroppk.own.pc_from_antex", antenna=info.antenna))
            else:
                self.pc_note.setText(tr("aeroppk.own.pc_ask", antenna=info.antenna or "?"))
            self.pc_note.setVisible(True)

    def set_slots(self):
        clear_layout(self.slots)
        rec = self.state.own
        names = rec.names if rec else {"obs": [], "nav": []}
        brand = rec.brand if rec else ""
        for index, (key, text_key) in enumerate((("obs", "aeroppk.own.obs"), ("nav", "aeroppk.own.nav"))):
            found = names.get(key) or []
            mark = glyph("ok" if found else ("stop" if key == "obs" else "warn")) if rec else glyph("pending")
            self.slots.addWidget(mark, index, 0)
            self.slots.addWidget(uikit.label(tr(text_key), "body", "muted", wrap=False), index, 1)
            text = ", ".join(found) if found else ("-" if not rec else tr("aeroppk.own.missing"))
            value = uikit.ElidedLabel(text, "body", "success" if found else "muted")
            self.slots.addWidget(value, index, 2)
        self.slots.setColumnStretch(2, 1)
        if rec and rec.obs_info is not None:
            info = rec.obs_info
            bits = [b for b in (brand, info.receiver, info.antenna) if b]
            line = "  ·  ".join([" ".join(bits)] if bits else [])
            self.hint.setText(line or tr("aeroppk.own.drop"))
        else:
            self.hint.setText(tr("aeroppk.own.drop"))

    def render_findings(self):
        clear_layout(self.findings)
        rec = self.state.own
        rows = []
        if rec is not None:
            for issue in rec.issues:
                rows.append(("stop" if issue.level == "stop" else "warn", tr("aeroppk.own.issue." + issue.key, **issue.fields),
                             tr("aeroppk.own.cons." + issue.key, **issue.fields)))
            info = rec.obs_info
            if info is not None and info.first and info.last:
                rows.append(("ok", tr("aeroppk.own.summary", rate=B.rate_text(info.interval) or "-", systems=systems_text("".join(info.systems)),
                                      start=fmt_time(utc(info.first), False), end=fmt_time(utc(info.last), False)), ""))
        for kind, text, why in rows:
            holder = QtWidgets.QWidget()
            h = uikit.hbox(spacing=tokens.S, parent=holder)
            h.addWidget(glyph(kind), 0, Qt.AlignTop)
            col = uikit.vbox()
            col.addWidget(uikit.label(text, "body", "danger" if kind == "stop" else None))
            if why:
                col.addWidget(uikit.label(why, "small", "muted"))
            h.addLayout(col, 1)
            self.findings.addWidget(holder)
        window = runner.union_window(self.state.flights, 60.0)
        if rec is not None and rec.obs_info is not None and window is not None and rec.obs_info.first and rec.obs_info.last:
            span = utc_span(window)
            self.axis.set_rows(timeline.build_rows(span, 0, [("BASE", utc(rec.obs_info.first), utc(rec.obs_info.last))], tr("aeroppk.base.flight")), 0)
            self.axis.setVisible(True)
        else:
            self.axis.setVisible(False)

    # -- position ---------------------------------------------------------------------------------

    def _exclusive_open(self, other, on):
        """The two ways to give the position open one at a time (a group may also be closed)."""
        if on and not self._exclusive and other.is_open():
            self._exclusive = True
            try:
                other.set_open(False)
            finally:
                self._exclusive = False

    def _frame_changed(self, *_args):
        self.state.own_frame = self.frame_box.currentData() or ""
        self._show_frame_error()
        self.screen.own_changed()

    def _show_frame_error(self):
        """The frame is required: until it is chosen the field says so under itself (Apply refuses to write positions without it)."""
        self.frame_row.set_error("" if self.frame_box.currentData() else tr("aeroppk.own.frame_required"))

    def _any_typed(self):
        return any(e.text().strip() for e in (self.lat, self.lon, self.height, self.mlat, self.mlon, self.mh)) and not self.prefilled

    def _edited(self, *_args):
        self.prefilled = False
        self.update_state()

    def active(self):
        return "marker" if self.g_mark.is_open() else "position"

    def current_position(self):
        """(Position or None, [Check])."""
        site = None
        site_info = flight_tools.site_of(self.state.flights)
        if site_info:
            site = site_info[:2]
        if self.active() == "position":
            lat, lon, h = ownbase.parse_angle(self.lat.text()), ownbase.parse_angle(self.lon.text()), ownbase.parse_number(self.height.text())
            checks = ownbase.check_position(lat, lon, h, site, self.header_xyz)
            if any(c.level == "stop" for c in checks):
                return None, checks
            return ownbase.position_from_phase_centre(lat, lon, h), checks
        lat, lon, h = ownbase.parse_angle(self.mlat.text()), ownbase.parse_angle(self.mlon.text()), ownbase.parse_number(self.mh.text())
        pole, pc = ownbase.parse_number(self.pole_edit.text()), ownbase.parse_number(self.pc.text())
        if None in (lat, lon, h) or not ownbase.heights_valid(pole, pc):
            return None, [ownbase.Check("incomplete", "stop")]
        marker_checks = ownbase.check_position(lat, lon, h, site, self.header_xyz)
        if any(c.level == "stop" for c in marker_checks):
            return None, marker_checks
        position = ownbase.position_from_marker(lat, lon, h, pole, pc)
        return position, marker_checks

    def update_state(self):
        position, checks = self.current_position()
        self.state.own_position = position
        clear_layout(self.result)
        if position is not None:
            text = tr("aeroppk.own.used", lat="%.8f" % position.lat, lon="%.8f" % position.lon, h="%.3f" % position.h)
            holder = uikit.hbox(spacing=tokens.S)
            holder.addWidget(glyph("ok"), 0, Qt.AlignTop)
            holder.addWidget(uikit.label(text, "body"), 1)
            self.result.addLayout(holder)
        for check in checks:
            if check.key == "incomplete":
                continue
            holder = uikit.hbox(spacing=tokens.S)
            holder.addWidget(glyph("stop" if check.level == "stop" else "warn"), 0, Qt.AlignTop)
            holder.addWidget(uikit.label(tr("aeroppk.own.check." + check.key, **check.fields), "body", "danger" if check.level == "stop" else "warning"), 1)
            self.result.addLayout(holder)
        approx = self.prefilled and self.header_xyz is not None
        self.warn.setVisible(approx)
        self.screen.own_changed()


# ---------------------------------------------------------------------------------------------------------------------
# The screen
# ---------------------------------------------------------------------------------------------------------------------

class BaseScreen(Screen):
    step = S.BASE
    name_key = "aeroppk.step_base"

    def __init__(self, wizard):
        super(BaseScreen, self).__init__(wizard)
        self.state.radius_km = int(config.get(SECTION, "radius_km", B.DEFAULT_RADIUS))
        self.state.margin_h = int(config.get(SECTION, "margin_h", B.DEFAULT_MARGIN_H))
        if self.state.margin_h not in B.MARGINS_H:
            self.state.margin_h = B.DEFAULT_MARGIN_H
        self.state.one_s_only = bool(config.get(SECTION, "one_s_only", True))
        saved = config.get(SECTION, "networks", None)
        self.state.networks = [n for n in saved if n in catalog.BY_ID] if isinstance(saved, list) else None
        self.content.setSpacing(uikit.px(tokens.M))
        self.stack = QtWidgets.QStackedWidget()
        self.net = NetPage(self)
        self.own = OwnPage(self)
        self.stack.addWidget(self.net)
        self.stack.addWidget(self.own)
        self.stack.setCurrentIndex(0)
        self.content.addWidget(self.stack, 1)
        self.net.apply_mode()
        self._shown_mode = self.state.mode
        self._go_on = False                # set while the step changes after a download that Compute started
        self._compute_asked = False        # a download started by Compute is running: the step changes when it ends well

    def set_mode(self, mode):
        """Show the page of a mode (the Mode step chose it; the tests call this too)."""
        self.state.set_mode(mode)
        self._shown_mode = self.state.mode
        own = mode == S.MODE_OWN
        self.stack.setCurrentIndex(1 if own else 0)
        if not own:
            self.net.on_mode()
            self.net.refresh()
        self.wizard.refresh_buttons()

    def enter(self):
        if self.state.mode != self._shown_mode:                          # the Mode step changed it since the page was last shown
            self.set_mode(self.state.mode)
        self.net.start()
        if self.wizard.rover_dirty:
            self.wizard.rover_dirty = False
            if self.own.paths:
                self.own.recognise()
        self.own.render_findings()
        self.own.update_state()
        self.wizard.refresh_buttons()

    def busy(self):
        return self.state.basket.busy()

    def next_text(self):
        return tr("aeroppk.base.compute")

    def can_leave(self, forward=True):
        if forward and self.state.mode != S.MODE_OWN and not self._go_on and self.state.basket.todo():
            self.download(go_on=True)                                      # Compute fetches what is missing first, then the step changes by itself
            return False
        self._go_on = False
        if forward:
            self.wizard.pending_start = True                               # the Result step starts the calculation when it appears
        return True

    # -- jobs (started through the window) --------------------------------------------------------

    def download(self, go_on=False):
        """Fetch every row that is not done yet, one after the other, in one job. With `go_on` (Compute), the Result step follows
        when at least one base is ready at the end."""
        window = self.net.window
        todo = self.state.basket.todo()
        if window is None or not todo or self.busy():
            return
        self._compute_asked = go_on
        from . import rtklib
        programs = rtklib.find_programs(config.get(SECTION, "rtklib_dir", None))
        hours = self.state.margin_h
        work_root = os.path.join(rgp.cache_dir(), "work")
        stations = [i.station for i in todo]
        surveyed = dict((code, list(offer.products)) for code, offer in self.offers.items() if offer.available())   # what the survey found
        for item in todo:
            item.state, item.error = B.RUNNING, ""

        def work(post, cancelled):
            for station in stations:
                if cancelled():
                    break
                post("item", code=station.code, state="running")
                try:
                    base = runner.fetch_base(station, window, hours, programs, work_root, post=None, cancelled=cancelled,
                                             products=surveyed.get(station.code))
                    post("base", code=station.code, base=base)
                except Exception as exc:
                    post("item", code=station.code, state="error", error=str(exc))
            return bool(cancelled())                                       # True: the user stopped it, Compute does not go on

        def message(kind, fields):
            item = self.state.basket.get(fields.get("code", ""))
            if item is None:
                return
            if kind == "base":
                item.base, item.state, item.error = fields["base"], B.DONE, ""
                item.info = runner.info_of(item.base) or item.info
            elif kind == "item" and fields.get("state") == "error":
                item.state, item.error = B.ERROR, fields.get("error", "")
            self.basket_changed()

        def done(job):
            for item in self.state.basket.items:
                if item.state == B.RUNNING:
                    item.state = B.PENDING
            self.net.status.setFullText(tr("aeroppk.base.done_n", n=len(self.state.basket.done())) if self.state.basket.done() else "")
            self.basket_changed()
            go = self._compute_asked and job.state == "done" and not job.result and bool(self.state.basket.done())
            self._compute_asked = False
            if go and not getattr(self.wizard, "_closing", False) and self.wizard.current_screen() is self:
                self._go_on = True
                try:
                    self.wizard.next()
                finally:
                    self._go_on = False
        if not self.wizard.run_job(work, message, done, "download", text=tr("aeroppk.base.downloading"), indeterminate=True):
            self._compute_asked = False
            for item in todo:
                item.state = B.PENDING if not item.error else B.ERROR
        self.basket_changed()

    @property
    def offers(self):
        return self.net.offers

    def recognise_job(self, fn):
        def done(job):
            if job.state == "done" and job.result is not None:
                self.own.taken(job.result)
            elif job.state == "failed":
                rec = ownbase.Recognition()
                rec.issues.append(ownbase.Issue("obs_unusable", "stop", why=job.error))
                self.own.taken(rec)
        self.wizard.run_side(fn, done)

    def basket_changed(self):
        self.net.refresh()
        self.wizard.refresh_buttons()

    def own_changed(self):
        self.wizard.refresh_buttons()

    # -- footer -----------------------------------------------------------------------------------

    def hint(self):
        if self.state.mode == S.MODE_OWN:
            rec = self.state.own
            if rec is None:
                return tr("aeroppk.base.hint_own_files")
            if not rec.ok():
                return tr("aeroppk.base.hint_own_fix")
            if self.state.own_position is None:
                return tr("aeroppk.base.hint_own_position")
            return ""
        if not self.state.basket.items:
            if self.net.finding():
                return tr("aeroppk.base.hint_finding")
            hint_key = "aeroppk.base.hint_pick"
            return tr(hint_key)
        return ""
