"""Aero-Facade UI: the height system picker of the drawing sheet (altitudes).

The same rules as the Aero-Exports window, on the same functions: the systems offered are `vertical.compatible_verticals` for the project's
horizontal CRS at its place, the state of the chosen grid in our `Geoid` folder is `crs.check_geoid_presence`, the download is
`crs.download_geoid` (HTTPS to the PROJ CDN only, size and SHA-256 checked before the file is put in place), with the same texts
(`export.geoid_*`). A missing or damaged grid shows the orange banner and blocks the export until it is downloaded (or another system is
picked). Nothing here touches Metashape.
"""

import threading

from ... import i18n, journal, theme, ui
from ...qt import QtCore, QtWidgets
from ..aeroexports import crs, vertical
from .common import tr


def size_text(nbytes):
    for unit, step in (("GB", 1024.0 ** 3), ("MB", 1024.0 ** 2)):
        if nbytes >= step:
            return "{:.1f} {}".format(nbytes / step, unit)
    return "{:.0f} KB".format(nbytes / 1024.0)


class HeightPicker(QtWidgets.QWidget):
    """A drop-down of the height systems that go with the project's CRS, and the banner of a grid that must be downloaded."""

    changed = QtCore.Signal()

    def __init__(self, parent=None, folder=None):
        super(HeightPicker, self).__init__(parent)
        self._folder = folder                              # tests: another Geoid folder (default: ours)
        self._epsg = None
        self._lonlat = (None, None)
        self._missing = None                               # id of the chosen grid when it is missing or damaged
        self._state = ""
        self._busy = False
        col = QtWidgets.QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(theme.PAD_S)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(theme.PAD)
        row.addWidget(ui.label(tr("export.vertical"), "aeroMeta", wrap=False))
        self.box = ui.combo()
        self.box.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.box.setMinimumContentsLength(12)
        self.box.activated.connect(self._picked)
        row.addWidget(self.box, 1)
        col.addLayout(row)
        self.card = QtWidgets.QFrame()
        self.card.setObjectName("aeroOption")
        self.card.setProperty("on", True)
        self.card.setProperty("danger", True)
        inner = QtWidgets.QVBoxLayout(self.card)
        inner.setContentsMargins(14, 10, 14, 10)
        self.text = ui.label("")
        inner.addWidget(self.text)
        self.button = ui.button("export.geoid_download", self.download, "aeroRun", width=130)
        inner.addWidget(self.button, 0, QtCore.Qt.AlignLeft)
        self.bar = QtWidgets.QProgressBar()
        self.bar.setObjectName("aeroProgress")
        self.bar.setTextVisible(False)
        self.bar.setVisible(False)
        inner.addWidget(self.bar)
        self.card.setVisible(False)
        col.addWidget(self.card)
        self.note = ui.label("", "aeroMeta")
        self.note.setVisible(False)
        col.addWidget(self.note)

    # -- what is offered ---------------------------------------------------------------------------

    def set_context(self, epsg, lonlat, keep=None):
        """Fill the list for the horizontal CRS `epsg` at (lon, lat). `keep`: the id to select again if it is still offered."""
        self._epsg = int(epsg) if epsg else None
        self._lonlat = tuple(lonlat) if lonlat else (None, None)
        wanted = keep if keep is not None else self.selected()
        self.box.blockSignals(True)
        self.box.clear()
        if self._epsg:
            lon, lat = self._lonlat
            for spec in vertical.compatible_verticals(self._epsg, lon, lat, folder=self._folder or crs.own_dir()):
                self.box.addItem(crs.label(spec), spec["id"])
        index = self.box.findData(wanted) if wanted else -1
        self.box.setCurrentIndex(index if index >= 0 else 0)
        self.box.blockSignals(False)
        self.box.setEnabled(bool(self._epsg) and self.isEnabled())
        self.refresh()

    def selected(self):
        """Id of the chosen height system, or None when nothing is offered."""
        value = self.box.currentData()
        return value if value else None

    def select(self, vertical_id):
        index = self.box.findData(vertical_id)
        if index >= 0:
            self.box.setCurrentIndex(index)
            self.refresh()

    def blocking(self):
        """True while the chosen grid is missing or damaged (or being downloaded): the export must wait."""
        return bool(self._missing) or self._busy

    def count(self):
        return self.box.count()

    def _picked(self, *_args):
        self.refresh()                                      # refresh tells the window (`changed`)

    # -- the grid ---------------------------------------------------------------------------------------

    def refresh(self):
        """State of the chosen grid in our folder: missing or damaged = orange banner and a download button; not covering = a note."""
        if self._busy:
            return
        choice = self.selected()
        self._missing, self._state = None, ""
        text, note = "", ""
        if choice and choice != "ellipsoid":
            lon, lat = self._lonlat
            found = crs.check_geoid_presence(choice, self._folder, lon, lat)
            spec = found["spec"]
            self._state = found["status"]
            if found["status"] in ("missing", "stale"):
                self._missing = choice
                key = "export.geoid_missing" if found["status"] == "missing" else "export.geoid_stale"
                text = i18n.t(key, name=crs.label(spec), size=size_text(spec.get("size", 0)))
            elif found["status"] == "not_covering":
                note = tr("facade.ui.height_not_covering")
        elif choice == "ellipsoid":
            note = tr("facade.ui.height_ellipsoid_note")
        self.text.setText(text)
        self.button.setEnabled(bool(self._missing) and bool(crs.vertical_by_id(self._missing).get("url")))
        self.bar.setVisible(False)
        self.card.setVisible(bool(text))
        self.note.setText(note)
        self.note.setVisible(bool(note))
        self.changed.emit()

    def download(self):
        """Download the chosen grid (verified), with the window alive meanwhile."""
        vid = self._missing
        if not vid or self._busy:
            return
        self._busy = True
        self.button.setEnabled(False)
        total = crs.vertical_by_id(vid).get("size", 0) or 1
        self.bar.setRange(0, total)
        self.bar.setValue(0)
        self.bar.setVisible(True)
        state = {"done": 0, "error": ""}
        stale = self._state == "stale"
        folder = self._folder or crs.own_dir()

        def work():
            try:
                path = crs.download_geoid(vid, folder, overwrite=stale, progress=lambda got, _total: state.update(done=got))
                journal.write("Aero-Facade: geoid downloaded: {}".format(path))
            except FileExistsError:
                pass
            except Exception as exc:
                state["error"] = str(exc) or repr(exc)
                journal.write("Aero-Facade: geoid {}: {}".format(vid, state["error"]))

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        self.text.setText(i18n.t("export.geoid_progress", name=crs.label(crs.vertical_by_id(vid)), index=1, total=1))
        while thread.is_alive():
            self.bar.setValue(min(state["done"], total))
            QtWidgets.QApplication.processEvents()
            thread.join(0.1)
        self._busy = False
        self.refresh()
        if state["error"]:
            self.text.setText(i18n.t("export.geoid_failed", error=state["error"]))
            self.card.setVisible(True)
            self.button.setEnabled(True)

    def setEnabled(self, on):
        super(HeightPicker, self).setEnabled(on)
        self.box.setEnabled(bool(on) and bool(self._epsg))
