"""Aero-PPK, the first steps and the start of the calculation: Drone, Files, Mode, then Compute. A synthetic job, no network, no Metashape.

    "C:\\Program Files\\Agisoft\\Metashape_v22\\python\\python.exe" -B tests_qt\\run_qt_aeroppk_steps.py [out folder]

Drone: the sentence that says what was found, the antenna row (the height is asked only when the antenna is not known), the state of a drone no
reader knows (its title, its two buttons, the link of the mail with the other address in copy). Files: one row per flight with a tick, a
flight left out by its tick (a real click) and by the right-click action, the last flight that cannot be left out, the count line, the footer
line ("472 photos in 2 flights"), a folder no reader understood (a note, never a row with a tick). Mode: the cards have the same height and
their lines sit level, the tag is inside the first card, a click picks the mode. Compute: the button of the Base step starts the
calculation (a made-up solver), the Result step shows its verdict; Cancel goes back to the Base step. Single base mode picks its station by
itself (1 s first, then the nearest, never against the user); Compute downloads first (stays on the Base step, goes on when a base is
ready, stays when the download fails or is cancelled, goes straight on when everything is there). No row about the EXIF anywhere. Prints
PASS / FAIL lines.
"""

import os
import sys
import time
import types
from urllib.parse import parse_qs, unquote, urlsplit

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.argv = [sys.argv[0]] + ([sys.argv[1]] if len(sys.argv) > 1 else [])
sys.path.insert(0, HERE)
import run_qt_aeroppk_hittest as H  # noqa: E402  (the same window, the same made-up servers, the same helpers)

from aerotools import theme  # noqa: E402
from aerotools.qt import QtCore, QtGui, QtWidgets  # noqa: E402
from aerotools.tools.aeroppk import basket as B, devices, drones, rtklib, runner, screen_drone, state as S  # noqa: E402
from aerotools.tools.aeroppk.stations import survey as survey_tools  # noqa: E402
from aerotools.uikit import shell  # noqa: E402

check, pump, shot = H.check, H.pump, H.shot
FLIGHT_A, FLIGHT_B = H.FLIGHTS[0][0], H.FLIGHTS[1][0]


def texts_of(root):
    """Every text a widget shows or explains: labels, buttons, toggles, tool tips (what a user could read on the screen)."""
    out = []
    for widget in root.findChildren(QtWidgets.QWidget):
        for getter in ("text", "toolTip"):
            value = getattr(widget, getter, None)
            if callable(value):
                try:
                    value = value()
                except Exception:
                    continue
                if isinstance(value, str) and value.strip():
                    out.append(value)
        if hasattr(widget, "fullText"):
            out.append(widget.fullText())
        if hasattr(widget, "text_tip"):
            try:
                out.append(widget.text_tip())
            except Exception:
                pass
    return out


def cell(table, row, column):
    """The text of a cell of a DataTable (the files table builds its cells with `uikit.cell`)."""
    value = table.row_data(row)[column]
    return value["text"] if isinstance(value, dict) else str(value)


def click_cell(app, table, row, column=0, right=False):
    """A real click (or right-click) on a cell of a DataTable, on its viewport."""
    view = table.table
    visual = table._visual(row)
    rect = view.visualRect(view.model().index(visual, column))
    point = rect.center()
    if right:
        glob = view.viewport().mapToGlobal(point)
        QtWidgets.QApplication.sendEvent(view.viewport(), QtGui.QContextMenuEvent(QtGui.QContextMenuEvent.Mouse, point, glob))
    else:
        H.mouse_click(view.viewport(), point)
    pump(app, 0.15)


class MenuSpy(object):
    """Replaces the menu's exec: records the entries and takes the first one when `pick` is set."""

    def __init__(self):
        self.seen, self.pick = [], True
        self.real = (QtWidgets.QMenu.exec_, QtWidgets.QMenu.exec)
        spy = self

        def run(menu, *_args):
            spy.seen.append([(a.text(), a.isEnabled()) for a in menu.actions()])
            first = menu.actions()[0] if menu.actions() else None
            if spy.pick and first is not None and first.isEnabled():
                first.trigger()
                return first
            return None
        QtWidgets.QMenu.exec_ = QtWidgets.QMenu.exec = run

    def restore(self):
        QtWidgets.QMenu.exec_, QtWidgets.QMenu.exec = self.real


class UrlSpy(object):
    """Replaces the opening of an address: records it."""

    def __init__(self):
        self.urls = []
        self.real = QtGui.QDesktopServices.openUrl
        QtGui.QDesktopServices.openUrl = lambda url: self.urls.append(url.toString(QtCore.QUrl.FullyEncoded)) or True

    def restore(self):
        QtGui.QDesktopServices.openUrl = self.real


# ---------------------------------------------------------------------------------------------------------------------

def drone_step(app, dlg):
    drone, state = dlg.screens[S.DRONE], dlg.state
    dlg.go(S.DRONE, force=True)
    pump(app, 0.3)
    check("drone: the sentence says the photos, the flights and the drone", drone.found.fullText() == "472 photos in 2 flights, taken with DJI Mavic 3 Enterprise.",
          drone.found.fullText())
    check("drone: the device is the Mavic 3 Enterprise and its antenna is known", drone.device.currentData() == drones.READERS[0].id
          and drone.antenna.fullText() == "Known" and drone.antenna.property("tone") == "success", drone.antenna.fullText())
    check("drone: the height of the antenna is not shown (hidden, not greyed) when the antenna is known", not drone.z_row.isVisible())
    check("drone: the buttons to change the photos are there", drone.project_button.isVisible() and drone.folder_button.isVisible())
    check("drone: no Type box and no output frame row", not any(t in ("Type", "PPK", "Output frame") for t in texts_of(drone)), str(texts_of(drone)[:8]))
    check("drone: Next is enabled and the footer is quiet", dlg.next_button.isEnabled() and dlg.status.fullText() == "472 photos in 2 flights",
          dlg.status.fullText())
    check("drone: there is no Back button on the first step", not dlg.back_button.isVisible())
    shot(app, dlg, "steps_1_drone")
    # a device whose antenna is not known: the height is asked, Next waits for it
    reader = drones.READERS[0]
    reader.antenna_known = False
    try:
        drone.refresh()
        pump(app, 0.2)
        check("drone: antenna not known: the height row appears and Next waits", drone.z_row.isVisible() and not dlg.next_button.isEnabled()
              and drone.antenna.property("tone") == "warning", drone.antenna.fullText())
        check("drone: the footer says what is missing", "antenna height" in dlg.status.fullText().lower(), dlg.status.fullText())
        drone.z_edit.setText("0.085")
        drone._on_z("0.085")
        check("drone: a typed height lets Next go on", dlg.next_button.isEnabled() and state.antenna_z == 0.085, str(state.antenna_z))
        drone.z_edit.setText("9")
        drone._on_z("9")
        check("drone: a height that makes no sense is refused", not dlg.next_button.isEnabled() and state.antenna_z is None, str(state.antenna_z))
    finally:
        reader.antenna_known = True
        state.antenna_z = None
        drone.z_edit.setText("")
        drone.refresh()
    # a drone no reader knows: the photos were found, nothing can be filled in
    urls = UrlSpy()
    saved = (state.all_flights, state.unread, state.device_id)
    try:
        state.flights, state.unread, state.device_id = [], [("C:/Flights/July/Unknown_drone_photos", 472)], devices.OTHER
        drone.refresh()
        pump(app, 0.3)
        check("unsupported: the title says the drone is not supported", drone.empty.isVisible() and drone.empty.title.text() == "This drone is not supported yet",
              drone.empty.title.text())
        check("unsupported: the card names the drones that work today", "DJI Mavic 3 Enterprise" in drone.empty.text.text(), drone.empty.text.text())
        buttons = [b for b in drone.help_row.findChildren(QtWidgets.QPushButton)]
        check("unsupported: two buttons, the user group and the email, both reachable", sorted(b.text() for b in buttons) == ["Open the user group", "Send an email"]
              and all(b.isVisible() and b.isEnabled() for b in buttons), str([b.text() for b in buttons]))
        check("unsupported: Next is off and the footer says why", not dlg.next_button.isEnabled() and dlg.status.fullText() == "This drone is not supported yet.",
              dlg.status.fullText())
        shot(app, dlg, "steps_2_drone_unsupported")
        next(b for b in buttons if b.text() == "Send an email").click()
        check("unsupported: the email button opens one mailto link", len(urls.urls) == 1 and urls.urls[0].startswith("mailto:"), str(urls.urls))
        url = urls.urls[0] if urls.urls else "mailto:"
        fields = parse_qs(urlsplit(url).query, keep_blank_values=True)
        check("unsupported: the mail goes to the support address with the other one in copy",
              url.startswith("mailto:info@helix.corsica?cc=info@helix.aero&") and fields.get("cc") == ["info@helix.aero"], url[:90])
        check("unsupported: subject and body are encoded and read back unchanged", fields.get("subject") == ["Aero-PPK: drone not supported"] and
              unquote(url.split("body=")[1]).startswith("Drone model:\nCamera or sensor:") and "\n" not in url and " " not in url, url[-80:])
        next(b for b in buttons if b.text() == "Open the user group").click()
        check("unsupported: the group button opens the user group", urls.urls[-1] == screen_drone.community_link(), str(urls.urls[-1]))
    finally:
        urls.restore()
        state.flights, state.unread, state.device_id = saved
        drone.refresh()
    # choosing a device by hand: the reader reads the folders again
    drone.device.setCurrentIndex(drone.device.findData(devices.OTHER))
    drone._on_device()
    check("drone: 'Other' says the drone is not supported, Next waits", drone.empty.isVisible() and not dlg.next_button.isEnabled())
    state.flights, state.device_id = H.realistic_flights(), drones.READERS[0].id     # (a device picked by hand reads the folders again: these are made up)
    drone.refresh()


def files_step(app, dlg, flights):
    files, state = dlg.screens[S.FILES], dlg.state
    table = files.table
    dlg.go(S.FILES, force=True)
    pump(app, 0.3)
    check("files: one row per flight, both ticked", table.row_count() == 2 and table.checked_rows() == [0, 1], str(table.checked_rows()))
    check("files: the rows say the name, the photos, the start and the status", [cell(table, i, 1) for i in (0, 1)] == [FLIGHT_A, FLIGHT_B]
          and [cell(table, i, 2) for i in (0, 1)] == ["225", "247"] and cell(table, 0, 3) == "2026-07-03 13:33:12" and cell(table, 0, 6) == "Ready",
          str([[cell(table, i, c) for c in range(7)] for i in (0, 1)]))
    check("files: the count line says everything will be computed", files.count.fullText() == "472 photos in 2 flights will be computed.", files.count.fullText())
    check("files: the three files of the flight are named", [r for r in files.flight_name.fullText().split()][-1] == FLIGHT_A, files.flight_name.fullText())
    check("files: Next is enabled and the footer says the job", dlg.next_button.isEnabled() and dlg.status.fullText() == "472 photos in 2 flights",
          dlg.status.fullText())
    shot(app, dlg, "steps_3_files")
    state.results = [object()]                                           # (results exist: leaving a flight out must drop them)
    # left out by its tick: a real click on the check box of the second flight
    click_cell(app, table, 1)
    pump(app, 0.2)
    check("files: a click on the tick leaves the flight out", state.skipped == {flights[1]["files"].folder} and [f["files"].name for f in state.flights] == [FLIGHT_A],
          str(state.skipped))
    check("files: the flight stays in the list, greyed, and says it is left out", table.row_count() == 2 and table.checked_rows() == [0]
          and cell(table, 1, 6) == "Left out" and table.row_data(1)[1]["tone"] == "faint", str(table.row_data(1)))
    check("files: the count line follows", files.count.fullText() == "225 photos in 1 of 2 flights will be computed.", files.count.fullText())
    check("files: the footer says the flights and the one left out", dlg.status.fullText() == "225 photos in 1 flight, 1 flight left out", dlg.status.fullText())
    check("files: the results of before are dropped", state.results == [])
    check("files: a note says that the photos of the flight left out keep their position", any("keep the drone" in t for t in texts_of(files)), "")
    shot(app, dlg, "steps_4_files_one_excluded")
    # the last flight cannot be left out: the tick goes back, the menu entry is off
    click_cell(app, table, 0)
    pump(app, 0.2)
    check("files: the last flight cannot be left out (its tick comes back)", state.skipped == {flights[1]["files"].folder} and table.checked_rows() == [0],
          str((state.skipped, table.checked_rows())))
    check("files: the state refuses too", state.skip_flight(flights[0]["files"].folder, True) is False and len(state.flights) == 1)
    spy = MenuSpy()
    try:
        spy.pick = False
        click_cell(app, table, 0, 1, right=True)
        check("files: the menu of the last flight offers 'Exclude flight', switched off", spy.seen and spy.seen[-1] == [("Exclude flight", False)], str(spy.seen))
        # put it back by the right-click action
        spy.pick = True
        click_cell(app, table, 1, 1, right=True)
        pump(app, 0.2)
        check("files: the menu of a flight left out offers 'Include flight'", spy.seen[-1] == [("Include flight", True)], str(spy.seen[-1]))
        check("files: the action puts it back", state.skipped == set() and table.checked_rows() == [0, 1] and len(state.flights) == 2, str(state.skipped))
        check("files: the count line is whole again", files.count.fullText() == "472 photos in 2 flights will be computed.", files.count.fullText())
        # and left out by the action
        spy.pick = True
        click_cell(app, table, 0, 1, right=True)
        pump(app, 0.2)
        check("files: the menu of an included flight offers 'Exclude flight', and the action leaves it out", spy.seen[-1] == [("Exclude flight", True)]
              and state.skipped == {flights[0]["files"].folder} and [f["files"].name for f in state.flights] == [FLIGHT_B], str((spy.seen[-1], state.skipped)))
    finally:
        spy.restore()
    files.set_included(flights[0]["files"].folder, True)
    check("files: back to both flights", len(state.flights) == 2 and table.checked_rows() == [0, 1])
    # a flight with a missing file: its status and the footer, Next waits; a flight left out does not count
    missing = H.realistic_flights({1: ("obs",)})
    state.flights = missing
    files.refresh()
    pump(app, 0.2)
    check("files: a missing file is named in the status", cell(table, 1, 6) == "Missing: rover observations", cell(table, 1, 6))
    check("files: a blocking gap stops Next and the footer says so", not dlg.next_button.isEnabled() and dlg.status.fullText() == "Point to the missing files",
          dlg.status.fullText())
    files.set_included(missing[1]["files"].folder, False)
    check("files: a flight left out does not hold Next back", dlg.next_button.isEnabled() and not state.blocking_gaps())
    # a folder no reader understood: a note under the table, never a row with a tick
    state.flights = H.realistic_flights()
    state.unread = [("C:/Flights/July/Misc_photos", 18)]
    files.refresh()
    pump(app, 0.2)
    notes = texts_of(files)
    check("files: a folder no reader understood is a note, not a row", table.row_count() == 2 and "1 folder was not read: Misc_photos" in notes, str(table.row_count()))
    check("files: so no tick, disabled or not, is drawn for it", table.checked_rows() == [0, 1] and all(table.is_row_enabled(r) for r in range(table.row_count())))
    shot(app, dlg, "steps_5_files_unread")
    state.unread = []
    files.refresh()
    # the flights stay as they were when the chunk is read again (a new list drops the exclusions)
    files.set_included(flights[1]["files"].folder, False)
    state.flights = H.realistic_flights()
    check("files: a new list of flights drops what was left out", state.skipped == set() and len(state.flights) == 2)


def mode_step(app, dlg):
    mode, state = dlg.screens[S.MODE], dlg.state
    dlg.go(S.MODE, force=True)
    pump(app, 0.3)
    cards = mode.cards
    expected = [S.MODE_SINGLE, S.MODE_OWN]
    check("mode: the cards are %s" % ", ".join(expected), list(cards) == expected, str(list(cards)))
    check("mode: the same height for every card", len(set(c.height() for c in cards.values())) == 1, str([c.height() for c in cards.values()]))
    tops = [c.mapTo(dlg, QtCore.QPoint(0, 0)).y() for c in cards.values()]
    check("mode: the cards start at the same height", len(set(tops)) == 1, str(tops))
    who = [w for w in mode.findChildren(QtWidgets.QLabel) if w.text() in ("For most jobs.", "For a job that needs a cross-check.", "For a site with its own base.")]
    check("mode: the 'who it is for' lines sit at the same height under the cards", len(who) == len(cards) and len(set(w.mapTo(dlg, QtCore.QPoint(0, 0)).y() for w in who)) == 1,
          str([w.mapTo(dlg, QtCore.QPoint(0, 0)).y() for w in who]))
    first = cards[S.MODE_SINGLE]
    check("mode: 'Recommended' is the tag of the first card, inside it", first.tag == "Recommended" and all(not c.tag for m, c in cards.items() if m != S.MODE_SINGLE)
          and not any(w.text() == "Recommended" for w in mode.findChildren(QtWidgets.QLabel)))
    check("mode: the public station is picked at first and Next is enabled", first.isChecked() and state.mode == S.MODE_SINGLE and dlg.next_button.isEnabled())
    for key, card in cards.items():
        centre = card.mapToGlobal(card.rect().center())
        found = H.widget_at(dlg, centre)
        H.mouse_click(found, found.mapFromGlobal(centre))
        pump(app, 0.15)
        check("mode: a click on the '%s' card picks it and tells the state" % key, state.mode == key and card.isChecked()
              and [m for m, c in cards.items() if c.isChecked()] == [key] and dlg.next_button.isEnabled(), state.mode)
    mode.select(S.MODE_SINGLE)
    check("mode: select() acts as a click", state.mode == S.MODE_SINGLE)
    check("mode: Back goes to the files and Next to the base", mode.prev_step() == S.FILES and mode.next_step() == S.BASE)
    shot(app, dlg, "steps_6_mode")
    base = dlg.screens[S.BASE]
    check("mode: the Base step has no switch of mode any more", not hasattr(base, "modes"))


def compute_step(app, dlg, flights):
    """The button of the Base step starts the calculation; the Result step shows what came back."""
    state, res, base = dlg.state, dlg.screens[S.RESULTS], dlg.screens[S.BASE]
    H.fill_basket(dlg, app, S.MODE_SINGLE, 1)
    for item in state.basket.items:
        item.base = types.SimpleNamespace(name=item.code)
    base.basket_changed()
    dlg.go(S.BASE, force=True)
    pump(app, 0.3)
    check("compute: the next button of the Base step reads Compute", dlg.next_button.text() == "Compute" and dlg.next_button.isEnabled(), dlg.next_button.text())
    check("compute: going back from the Base step goes to the Mode step", base.prev_step() == S.MODE and base.next_step() == S.RESULTS)
    # a made-up solver: the calculation runs for real in its worker thread, on made-up results
    calls = []
    stop = []

    def calculate(flights_, bases, site, programs, options, post, cancelled, *rest):
        calls.append([b.name for b in bases])
        post("stage", name="solve", code=bases[0].name)
        while not stop[0] and not cancelled():
            time.sleep(0.02)
        return {"results": H.scenario_results(flights_, "good", codes=("CRTE",)), "errors": []}
    real = (runner.calculate, rtklib.find_programs)
    runner.calculate = calculate
    rtklib.find_programs = lambda folder=None: {"rnx2rtkp": "rnx2rtkp.exe"}
    try:
        stop.append(False)
        dlg.next_button.click()
        pump(app, 0.5)
        check("compute: the click goes to the Result step and starts the calculation", dlg.current_step() == S.RESULTS and res.mode == "running" and dlg.busy()
              and len(calls) == 1, "%s %s" % (dlg.current_step(), res.mode))
        check("compute: the start is asked once", dlg.pending_start is False)
        check("compute: the steps of the calculation are listed, the footer shows Cancel and no Next", not dlg.next_button.isVisible() and res.busy())
        shot(app, dlg, "steps_7_running")
        stop[0] = True
        dlg.wait_idle(30)
        pump(app, 0.5)
        check("compute: when it ends the Result step shows the verdict", res.mode == "done" and dlg.current_step() == S.RESULTS and
              res.verdict.fullText().startswith("Your PPK is good."), res.verdict.fullText())
        check("compute: the base that was asked is the one that was computed", calls == [[state.basket.items[0].code]], str(calls))
        # Cancel: back to the Base step
        state.results = []
        res.mode = "idle"
        dlg.go(S.BASE, force=True)
        stop[:] = [False]
        dlg.next_button.click()
        pump(app, 0.4)
        check("compute: a second calculation starts", res.mode == "running" and dlg.busy(), res.mode)
        dlg.cancel_button.click()
        dlg.wait_idle(30)
        pump(app, 0.5)
        check("compute: Cancel stops it and goes back to the Base step", dlg.current_step() == S.BASE and res.mode == "idle" and not dlg.busy(),
              "%s %s" % (dlg.current_step(), res.mode))
        check("compute: the Compute button is there again", dlg.next_button.isVisible() and dlg.next_button.text() == "Compute")
    finally:
        stop[:] = [True]
        runner.calculate, rtklib.find_programs = real
        dlg.wait_idle(10)


def offer_of(station, interval):
    """What a server said about a station, made up: data every `interval` seconds, or nothing."""
    offer = survey_tools.Offer(station)
    if interval:
        offer.products = [types.SimpleNamespace(interval=interval, label="%g s, RINEX 3" % interval)]
    return offer


def auto_pick_step(app, dlg):
    """Single base mode picks the station by itself: 1 s first, then the nearest; a better answer replaces it; a click or a removal of the
    user stops it for the visit."""
    state, base = dlg.state, dlg.screens[S.BASE]
    net = base.net
    state.basket.clear()
    base.set_mode(S.MODE_SINGLE)
    net.ask = lambda station: None                                       # the answers of the servers are made up below
    one_s = state.one_s_only
    try:
        dlg.go(S.BASE, force=True)
        net.slider.setValue(B.slider_from_radius(200))
        pump(app, 0.5)
        net.offers, net.dropped, net.manual = {}, set(), set()
        net._asking.clear()
        net.auto_code, net.auto_stopped = "", False
        state.basket.clear()
        net._list_key = None
        base.basket_changed()
        pump(app, 0.2)
        cands = [s.code for s in net.candidates]
        check("auto pick: the site has stations around", len(cands) >= 4, str(len(cands)))
        c0, c1, c2, c3 = cands[:4]

        def answer(**intervals):
            for code, interval in intervals.items():
                net.offers[code] = offer_of(next(s for s in net.candidates if s.code == code), interval)
            net._offers_changed()
            pump(app, 0.1)

        check("auto pick: nothing is picked before the servers answered, and the footer says it looks",
              not state.basket.items and dlg.status.fullText() == "Looking for the nearest station" and not dlg.next_button.isEnabled(),
              dlg.status.fullText())
        check("auto pick: the empty basket says it picks", net.empty.isVisible() and net.empty.text() == "Picking the nearest station...", net.empty.text())
        state.one_s_only = False
        answer(**{c0: 30.0, c2: 1.0})
        check("auto pick: 1 s data comes before a nearer coarse station", state.basket.codes() == [c2], str(state.basket.codes()))
        check("auto pick: it is shown selected in the basket, the list and the map",
              net.selected == c2 and c2 in net._list_codes and net._list_codes.index(c2) in net.station_list.selected_rows()
              and any(s["code"] == c2 and s["selected"] for s in net.map.stations()),
              "%s %s" % (net.selected, net.station_list.selected_rows()))
        check("auto pick: it is not a choice of the user", c2 not in net.manual and net.auto_code == c2 and not net.auto_stopped)
        check("auto pick: Next is on (Compute downloads the data) and the footer no longer asks for a station",
              dlg.next_button.isEnabled() and dlg.next_button.text() == "Compute" and base.hint() == "", base.hint())
        answer(**{c1: 1.0})
        check("auto pick: a nearer 1 s answer replaces it", state.basket.codes() == [c1] and net.auto_code == c1, str(state.basket.codes()))
        answer(**{c3: 1.0})
        check("auto pick: a farther one does not", state.basket.codes() == [c1])
        # with only 1 s data wanted, a coarse pick gives way to the nearest 1 s station
        state.one_s_only = True
        answer(**{c1: 30.0})
        check("auto pick: with only 1 s data wanted the coarse one gives way to the nearest 1 s station", state.basket.codes() == [c2], str(state.basket.codes()))
        # a row that is downloading is never replaced
        state.basket.items[0].state = B.RUNNING
        answer(**{c0: 1.0})
        check("auto pick: a row that is downloading stays", state.basket.codes() == [c2])
        state.basket.items[0].state = B.PENDING
        # the user removes it
        net.remove(c2)
        pump(app, 0.1)
        check("auto pick: the user removes the station: the basket stays empty", not state.basket.items and net.auto_stopped, str(state.basket.codes()))
        answer(**{c1: 1.0})
        check("auto pick: nothing is picked again on this visit", not state.basket.items)
        # a new visit picks again, but not what was removed
        dlg.go(S.MODE, force=True)
        dlg.go(S.BASE, force=True)
        pump(app, 0.3)
        check("auto pick: a new visit picks again, not the station that was removed", state.basket.codes() == [c0] and c2 in net.dropped,
              str(state.basket.codes()))
        # the user clicks another one
        net.map.toggle_station(c2)
        pump(app, 0.2)
        check("auto pick: a click of the user replaces it and counts as a choice", state.basket.codes() == [c2] and c2 in net.manual and net.auto_stopped)
        answer(**{c1: 1.0})
        check("auto pick: a better answer does not take the user's choice away", state.basket.codes() == [c2], str(state.basket.codes()))
        shot(app, dlg, "steps_8_auto_pick")
    finally:
        state.one_s_only = one_s
        if "ask" in net.__dict__:
            del net.ask


def compute_downloads_step(app, dlg):
    """Compute downloads what is missing, stays on the Base step while it runs, then goes on by itself when a base is ready."""
    state, res, base = dlg.state, dlg.screens[S.RESULTS], dlg.screens[S.BASE]
    net = base.net
    if "download" in base.__dict__:
        del base.download                                                # the real one: the harness window stubs it
    gate = {"open": False, "fail": False}
    fetched, calls = [], []

    def fetch(station, window, hours, programs, work_root, **kwargs):
        fetched.append(station.code)
        cancelled = kwargs.get("cancelled")
        end = time.time() + 10
        while not gate["open"] and time.time() < end:
            if cancelled is not None and cancelled():
                raise runner.pipeline.Cancelled()
            time.sleep(0.02)
        if gate["fail"]:
            raise RuntimeError("No data from this station.")
        return H._fakes.fake_fetch(station)

    def calculate(flights_, bases, site, programs, options, post, cancelled, *rest):
        calls.append([b.name for b in bases])
        return {"results": H.scenario_results(flights_, "good", codes=("CRTE",)), "errors": []}

    def pending(done=False):
        """One station in the basket that is still to download (or already downloaded), on the Base step."""
        state.results = []
        res.mode, dlg.pending_start = "idle", False
        state.basket.clear()
        base.set_mode(S.MODE_SINGLE)
        net.ask = lambda station: None
        station = net.candidates[0]
        state.basket.add(station)
        item = state.basket.items[0]
        item.state, item.error = B.PENDING, ""
        item.info = {"rate": 1.0, "systems": "GR", "receiver": "LEICA GR50", "antenna": "LEIAR25.R3 LEIT", "product": "1 s, RINEX 3", "first": None, "last": None}
        if done:
            item.state, item.base = B.DONE, H._fakes.fake_fetch(station)
        net.selected = station.code
        net.auto_stopped = True
        dlg.go(S.BASE, force=True)
        pump(app, 0.3)
        base.basket_changed()
        return item

    real = (runner.fetch_base, runner.calculate, rtklib.find_programs)
    runner.fetch_base, runner.calculate = fetch, calculate
    rtklib.find_programs = lambda folder=None: {"rnx2rtkp": "rnx2rtkp.exe"}
    try:
        # 1. nothing downloaded: Compute downloads, waits on the Base step, then the calculation starts
        item = pending()
        check("compute: with a station to download Next is on and says Compute", dlg.next_button.isEnabled() and dlg.next_button.text() == "Compute")
        gate["open"] = False
        dlg.next_button.click()
        pump(app, 0.4)
        check("compute: the download starts and the window stays on the Base step", dlg.current_step() == S.BASE and item.state == B.RUNNING and dlg.busy()
              and fetched, "%s %s" % (dlg.current_step(), item.state))
        check("compute: while it runs Next and Back are off", not dlg.next_button.isEnabled() and not dlg.back_button.isEnabled())
        check("compute: nothing was calculated yet", calls == [] and dlg.pending_start is False)
        dlg.next_button.click()
        pump(app, 0.1)
        check("compute: a second click does nothing", dlg.current_step() == S.BASE and fetched.count(item.code) == 1)
        shot(app, dlg, "steps_9_compute_downloading")
        gate["open"] = True
        dlg.wait_idle(30)
        pump(app, 1.0)
        check("compute: when the base is ready the wizard goes on by itself", dlg.current_step() == S.RESULTS and item.state == B.DONE, "%s %s" % (dlg.current_step(), item.state))
        check("compute: the calculation started on the downloaded base", calls == [[item.code]] and res.mode == "done", "%s %s" % (calls, res.mode))
        # 2. the download fails: the window stays, the row says why, nothing starts
        del calls[:]
        del fetched[:]
        item = pending()
        gate["open"], gate["fail"] = True, True
        dlg.next_button.click()
        dlg.wait_idle(30)
        pump(app, 0.8)
        check("compute: a failed download keeps the window on the Base step", dlg.current_step() == S.BASE and not calls and dlg.pending_start is False,
              "%s %s" % (dlg.current_step(), calls))
        check("compute: the row says why", item.state == B.ERROR and "No data" in item.error, "%s %s" % (item.state, item.error))
        check("compute: Compute can be tried again", dlg.next_button.isEnabled() and not dlg.busy())
        # 3. the user cancels the download: nothing starts
        gate["open"], gate["fail"] = False, False
        item = pending()
        dlg.next_button.click()
        pump(app, 0.4)
        check("compute: the download runs again", item.state == B.RUNNING and dlg.busy())
        dlg.cancel_button.click()
        dlg.wait_idle(30)
        pump(app, 0.8)
        check("compute: Cancel stops the download and nothing starts", dlg.current_step() == S.BASE and not calls and not dlg.busy(), "%s %s" % (dlg.current_step(), calls))
        # 4. everything already downloaded: Compute goes straight on
        gate["open"] = True
        del fetched[:]
        item = pending(done=True)
        dlg.next_button.click()
        pump(app, 1.0)
        check("compute: with the base already downloaded nothing is fetched and the calculation starts",
              dlg.current_step() == S.RESULTS and not fetched and calls == [[item.code]], "%s %s %s" % (dlg.current_step(), fetched, calls))
        # 5. the window is closing while the download runs: the step does not change
        gate["open"] = False
        item = pending()
        dlg.next_button.click()
        pump(app, 0.3)
        dlg._closing = True
        gate["open"] = True
        dlg.wait_idle(30)
        pump(app, 0.5)
        dlg._closing = False
        check("compute: a window that is closing does not go on", dlg.current_step() == S.BASE, str(dlg.current_step()))
    finally:
        gate["open"] = True
        runner.fetch_base, runner.calculate, rtklib.find_programs = real
        if "ask" in net.__dict__:
            del net.ask
        dlg.wait_idle(10)


def no_exif_anywhere(app, dlg):
    """No row about the EXIF positions on any step, nor in the windows of the Result step."""
    found = []
    res = dlg.screens[S.RESULTS]
    results = H.scenario_results(H.realistic_flights(), "good")
    H.show_results(app, dlg, results)
    res.open_details()
    for step in S.STEPS:
        dlg.go(step, force=True)
        pump(app, 0.2)
        for text in texts_of(dlg.screens[step]):
            if "exif" in text.lower():
                found.append((step, text[:60]))
    for window in (res.details,):
        for text in texts_of(window):
            if "exif" in text.lower():
                found.append(("details", text[:60]))
    res.details.close()
    check("no row or text about the EXIF positions on any step or in the Details window", not found, str(found))


def main():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    theme.load_fonts(QtGui)
    H.runner.fetch_base = H._fakes.fake_fetch
    H.runner.survey_one = H._fakes.fake_survey
    H.orbits.level_of_days = lambda days: H.orbits.Answer(sure=False)
    real_area = shell.screen_area
    shell.screen_area = lambda widget=None: QtCore.QRect(0, 0, 1920, 1040)
    flights = H.realistic_flights()
    H.install_document(flights)
    dlg = H.make_dialog(app, flights=flights)
    try:
        drone_step(app, dlg)
        files_step(app, dlg, flights)
        mode_step(app, dlg)
        compute_step(app, dlg, flights)
        auto_pick_step(app, dlg)
        compute_downloads_step(app, dlg)
        no_exif_anywhere(app, dlg)
    finally:
        H.close_popups(app)
        dlg.reject = lambda: None
        dlg.chunk_timer.stop()
        QtWidgets.QWidget.hide(dlg)
        dlg.deleteLater()
        pump(app, 0.1)
        shell.screen_area = real_area
    problem = H.GUARD.check()
    check("real settings untouched", problem is None, str(problem))
    print("out:", H.OUT)
    return 1 if H.FAILURES else 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)
