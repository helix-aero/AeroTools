"""Step 1 of the Aero-PPK wizard: the drone. It asks one thing, which drone took the photos, and says in a sentence what was found in
them. The photos come from the bound chunk (or a folder); the device is detected, and the height of the antenna is asked only when the
device does not tell it."""

from urllib.parse import quote

from ... import about_dialog, config, msapi, uikit
from ...qt import QtCore, QtGui, QtWidgets
from ...uikit import tokens
from . import devices, flights as flight_tools, state as S
from .ui_common import Screen, card, glyph, heading, narrow_labels, tr, value

SECTION = "aeroppk"
SUPPORT_EMAIL = "info@helix.corsica"       # the public contact of the project: where a user asks for a drone that is not supported


SUPPORT_EMAIL_CC = "info@helix.aero"       # in copy of that mail


def mailto_url(subject, body, address=SUPPORT_EMAIL, cc=SUPPORT_EMAIL_CC):
    """A mailto: link with the subject and the body encoded (a mail client reads %20 and %0A, not + and line breaks)."""
    return "mailto:%s?cc=%s&subject=%s&body=%s" % (address, cc, quote(subject, safe=""), quote(body, safe=""))


def community_link():
    """The address of the user group, the one the About window shows."""
    return dict(about_dialog.LINKS)["about.community"]


class DroneScreen(Screen):
    step = S.DRONE
    name_key = "aeroppk.step_drone"

    def __init__(self, wizard):
        super(DroneScreen, self).__init__(wizard)
        self.content.setSpacing(uikit.px(tokens.M))                    # (the fullest state, a drone no reader knows, fits a 768 px high screen under Qt 5 too)
        self.content.addWidget(uikit.label(tr("aeroppk.drone.question"), "strong", wrap=False))
        top = uikit.hbox(spacing=tokens.M)
        self.found = uikit.ElidedLabel("", "body", fit=True)           # what was found, in one sentence
        top.addWidget(self.found, 1)
        self.project_button = uikit.Button(tr("aeroppk.rover.use_project"), "secondary", handler=self.use_project)
        self.folder_button = uikit.Button(tr("aeroppk.rover.folder"), "secondary", handler=self.choose_folder)
        top.addWidget(self.project_button)
        top.addWidget(self.folder_button)
        top.addWidget(uikit.InfoButton(tr("aeroppk.rover.photos_tip")))
        self.content.addLayout(top)
        self.empty = uikit.EmptyState(tr("aeroppk.rover.empty"), tr("aeroppk.rover.empty_hint"))
        self.empty.set_card(True)
        self.content.addWidget(self.empty, 1)
        self.help_row = QtWidgets.QWidget()                            # how to ask for a drone that is not supported: shown under the empty card
        help_box = uikit.vbox(spacing=tokens.S, parent=self.help_row)
        help_line = uikit.hbox(spacing=tokens.S)
        help_box.addLayout(help_line)
        help_line.addStretch(1)
        help_line.addWidget(uikit.Button(tr("aeroppk.drone.group"), "primary", handler=self.open_group))
        help_line.addWidget(uikit.Button(tr("aeroppk.drone.mail"), "secondary", handler=self.send_mail))
        help_line.addStretch(1)
        self.content.addWidget(self.help_row)
        self.device_card = self._build_device()
        row = uikit.hbox()
        row.addWidget(self.device_card)
        row.addStretch(1)
        self.content.addLayout(row)
        self.content.addStretch(1)

    # -- construction -----------------------------------------------------------------------------

    def _build_device(self):
        """The one card: the device, its antenna and, only when the antenna is not known, its height above the camera."""
        frame, box = card()
        frame.setFixedWidth(uikit.px(tokens.POPOVER_W + 3 * tokens.XL))
        box.addLayout(heading(tr("aeroppk.rover.g_device")))
        form = uikit.FormGrid()
        self.device = uikit.ChoiceBox([(choice.id, choice.name or tr("aeroppk.rover.other")) for choice in devices.choices()])
        self.device.changed.connect(self._on_device)
        self.device_row = form.add_row(tr("aeroppk.rover.device"), self.device, tr("aeroppk.rover.device_tip"))
        self.antenna = value()
        antenna_cell = QtWidgets.QWidget()                               # the state is an icon + a colour + a word
        antenna_line = uikit.hbox(spacing=tokens.S, parent=antenna_cell)
        self.antenna_glyph = glyph("pending")
        antenna_line.addWidget(self.antenna_glyph)
        antenna_line.addWidget(self.antenna, 1)
        form.add_row(tr("aeroppk.rover.antenna"), antenna_cell)
        self.z_edit = uikit.TextField()
        self.z_edit.textEdited.connect(self._on_z)
        self.z_edit.setPlaceholderText(tr("aeroppk.rover.z_placeholder"))
        self.z_row = form.add_row(tr("aeroppk.rover.z"), self.z_edit, tr("aeroppk.rover.z_tip"))
        self.z_row.hide()                                                # shown only when the antenna is not known
        narrow_labels(form)
        box.addWidget(form)
        return frame

    # -- photos -----------------------------------------------------------------------------------

    def enter(self):
        if not self.state.all_flights and not self.state.unread:
            self.use_project()
        else:
            self.refresh()

    def use_project(self):
        """The photos of the bound chunk, and only those: their folders are the flights."""
        chk = self.wizard.bound_chunk()
        paths = [e["path"] for e in msapi.camera_reference_entries(chk)] if chk is not None else []
        self.set_folders(flight_tools.folders_of(paths))

    def choose_folder(self):
        folder = self.wizard.pick_folder(config.get(SECTION, "last_folder", ""))
        if not folder:
            return
        config.put(SECTION, "last_folder", folder)
        found, unread = flight_tools.scan_folders(flight_tools.expand_folder(folder))
        if not found and not unread:
            found, unread = flight_tools.scan_folders([folder])
        self._take(found, unread)

    def set_folders(self, folders):
        found, unread = flight_tools.scan_folders(folders)
        self._take(found, unread)

    def _take(self, found, unread):
        self.state.flights = found                                       # a new list of flights: nothing is excluded any more
        self.state.unread = unread
        self.state.antenna_z = None
        if found:
            self.state.device_id = devices.detect(found[0]["files"].photos, found[0]["files"].folder) or found[0]["reader"].id
        elif unread:
            self.state.device_id = devices.OTHER
        self.z_edit.setText("")
        self.wizard.rover_changed()
        self.refresh()

    def open_group(self):
        QtGui.QDesktopServices.openUrl(QtCore.QUrl(community_link()))

    def send_mail(self):
        QtGui.QDesktopServices.openUrl(QtCore.QUrl(mailto_url(tr("aeroppk.drone.mail_subject"), tr("aeroppk.drone.mail_body"))))

    # -- device and antenna -----------------------------------------------------------------------

    def _on_device(self, *_args):
        ident = self.device.currentData()
        self.state.device_id = ident
        reader_id = ident if ident != devices.OTHER else None
        if reader_id and (self.state.all_flights or self.state.unread):
            folders = [f["files"].folder for f in self.state.all_flights] + [u[0] for u in self.state.unread]
            left_out = set(self.state.skipped)
            found, unread = flight_tools.scan_folders(folders, reader_id)
            self.state.flights, self.state.unread = found, unread
            kept = set(f["files"].folder for f in found if f["files"].folder in left_out)     # the same folders stay excluded
            if len(kept) < len(found):                                   # one flight at least must count
                self.state.skipped = kept
        self.state.antenna_z = None
        self.z_edit.setText("")
        self.z_row.clear_error()
        self.wizard.rover_changed()
        self.refresh()

    def _on_z(self, text):
        self.state.antenna_z = devices.parse_antenna_z(text)
        bad = bool(text.strip()) and self.state.antenna_z is None
        self.z_row.set_error(tr("aeroppk.rover.z_bad") if bad else "")
        self.wizard.rover_changed()
        self._fill_device()
        self.wizard.refresh_buttons()

    # -- drawing ----------------------------------------------------------------------------------

    def refresh_skin(self):
        self.refresh()

    def refresh(self):
        has = bool(self.state.all_flights or self.state.unread)
        chk = self.wizard.bound_chunk()
        empty_chunk = chk is not None and not msapi.chunk_camera_count(chk)
        unsupported = self.state.unsupported()
        if unsupported:
            self.empty.set_state(tr("aeroppk.drone.unsupported"), tr("aeroppk.drone.unsupported_text")
                                 + "\n" + tr("aeroppk.drone.supported", names=", ".join(S.supported_names())))
        else:
            self.empty.set_state(tr("aeroppk.rover.empty_chunk" if empty_chunk else "aeroppk.rover.empty"),
                                 tr("aeroppk.rover.empty_chunk_hint" if empty_chunk else "aeroppk.rover.empty_hint"))
        self.empty.setVisible(not has or unsupported)
        self.empty.icon.setVisible(not unsupported)                      # room for the text and the buttons
        text = self.empty.text                                           # the card does not give a wrapped text its lines by itself
        text.setMaximumWidth(uikit.px(tokens.POPOVER_W * 2 if unsupported else 380))      # (wider: one line less, the page is not taller than the window under Qt 5)
        text.setMinimumHeight(text.heightForWidth(text.maximumWidth()) if unsupported else 0)
        self.help_row.setVisible(unsupported)
        self.device_card.setVisible(has)         # stays visible for an unsupported drone: a supported one that was not detected is picked here
        self.found.setFullText(self._sentence())
        self._fill_device()
        self.wizard.refresh_buttons()

    def _sentence(self):
        """What was found, in words: the photos, the flights and the drone."""
        flights = self.state.all_flights
        if not flights:
            count = sum(n for _folder, n in self.state.unread)
            return tr("aeroppk.drone.unread_only", n=count) if count else ""
        photos = sum(len(f["files"].photos) for f in flights)
        name = self.state.choice().name
        if name:
            return tr("aeroppk.drone.found", photos=photos, flights=len(flights), device=name)
        return tr("aeroppk.drone.found_other", photos=photos, flights=len(flights))

    def _fill_device(self):
        index = self.device.findData(self.state.device_id)
        self.device.blockSignals(True)
        self.device.setCurrentIndex(index if index >= 0 else self.device.count() - 1)
        self.device.blockSignals(False)
        mode = self.state.antenna_state()
        if mode == "known":
            text, tone = tr("aeroppk.rover.antenna_known"), "success"
        elif mode in ("needed", "manual"):
            text, tone = tr("aeroppk.drone.antenna_unknown"), "warning"
        else:
            text, tone = "-", None
        self.antenna.setFullText(text)
        uikit.set_tone(self.antenna, tone)
        self.antenna_glyph.set_kind({"success": "ok", "warning": "warn"}.get(tone, "pending"))
        self.antenna_glyph.setVisible(tone is not None)
        self.z_row.setVisible(mode in ("needed", "manual"))              # hidden, not greyed, when the antenna is known
        if self.device_row.info is not None:
            self.device_row.info.set_text(tr("aeroppk.rover.device_tip"))

    # -- footer -----------------------------------------------------------------------------------

    def show_back(self):
        return False

    def hint(self):
        if self.state.unsupported():
            return tr("aeroppk.drone.unsupported_hint")
        if not self.state.all_flights:
            return tr("aeroppk.rover.hint_flights")
        mode = self.state.antenna_state()
        if mode == "needed":
            return tr("aeroppk.rover.hint_antenna")
        return ""
