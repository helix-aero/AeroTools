"""Import de GCP depuis un fichier texte (CSV, TXT, ...).

Boite de dialogue : on choisit le fichier, les colonnes sont attribuees toutes
seules (nom, X, Y, Z : voir gcp_table.py) et restent modifiables, on choisit le
systeme de coordonnees du fichier, un apercu dit ce qui va etre cree ou remplace.
La lecture et la detection sont dans gcp_table.py ; la creation des marqueurs
dans msapi.import_gcps. Ce module ne fait que l'interface.

Regles : aucun appel direct a Metashape (tout passe par `msapi`), aucun texte
visible en dur (tout passe par `i18n.t`).
"""

import os

from ... import i18n, journal, msapi, theme
from ...qt import QtCore, QtWidgets
from . import gcp_table

PREVIEW_ROWS = 8
FILE_EXTENSIONS = ("csv", "txt", "xyz", "pts", "asc", "dat")

# (cle de texte, separateur ; None = automatique)
DELIMITER_CHOICES = (
    ("imp.delim.auto", None),
    ("imp.delim.semicolon", ";"),
    ("imp.delim.tab", "\t"),
    ("imp.delim.comma", ","),
    ("imp.delim.space", " "),
)

# Motifs de rejet de gcp_table.build_records : code -> cle de texte.
REASON_KEYS = {
    "x": "imp.reason.x",
    "y": "imp.reason.y",
    "z": "imp.reason.z",
    "name": "imp.reason.name",
    "columns": "imp.reason.columns",
}

WARN_CRS_MISMATCH = "imp.warn_crs_mismatch"

# Systeme propose quand le fichier est en degres et que le chunk n'est pas en degres.
WGS84_CODE = "EPSG::4326"

FIELD_LABELS = (
    ("name", "imp.field.name"),
    ("x", "imp.field.x"),
    ("y", "imp.field.y"),
    ("z", "imp.field.z"),
)


def _exec(dialog):
    runner = getattr(dialog, "exec_", None) or getattr(dialog, "exec")
    return runner()


def file_filter():
    """Filtre de la boite « ouvrir » : fichiers texte, puis tous les fichiers."""
    patterns = " ".join("*." + ext for ext in FILE_EXTENSIONS)
    return "{} ({});;{} (*)".format(
        i18n.t("imp.filter_text"), patterns, i18n.t("imp.filter_all"))


def reject_text(reason):
    """Phrase courte pour un code de rejet de gcp_table.build_records."""
    return i18n.t(REASON_KEYS.get(str(reason), "imp.reason.columns"))


def plan(records, existing_labels):
    """(nouveaux, mis a jour) pour ces points face aux marqueurs du chunk."""
    existing = set(existing_labels)
    updated = sum(1 for record in records if record["label"] in existing)
    return len(records) - updated, updated


def should_suggest_wgs84(records, chunk_kind):
    """Vrai si le fichier est en degres alors que les marqueurs du chunk n'ont pas
    de systeme geographique : WGS 84 est alors la proposition par defaut."""
    return gcp_table.looks_geographic(records) and chunk_kind != "geographic"


def crs_warning(records, crs):
    """Code d'avertissement si les valeurs ne vont pas avec le systeme, sinon None."""
    if not records or crs is None:
        return None
    kind = msapi.crs_kind(crs)
    geographic = gcp_table.looks_geographic(records)
    if kind == "projected" and geographic:
        return WARN_CRS_MISMATCH
    if kind == "geographic" and not geographic:
        return WARN_CRS_MISMATCH
    return None


class GcpImportDialog(QtWidgets.QDialog):
    """Choix du fichier, des colonnes et du systeme de coordonnees."""

    def __init__(self, chunk, parent=None):
        super(GcpImportDialog, self).__init__(parent)
        self._chunk = chunk
        self._path = ""
        self._text = ""
        self._table = None
        self._crs = None
        self._auto_crs = None
        self._records = []
        self._rejected = []
        self._header_forced = None
        self._combos = {}
        self._existing = [msapi.marker_label(m) for m in msapi.markers(chunk)]
        self._chunk_crs = msapi.marker_crs(chunk)

        self.setObjectName("aeroPanel")
        self.setStyleSheet(theme.stylesheet())
        self.setMinimumWidth(680)
        self._build()
        self._retranslate()
        self._refresh()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(theme.PAD_L, theme.PAD_L, theme.PAD_L, theme.PAD_L)
        layout.setSpacing(theme.PAD)

        row = QtWidgets.QHBoxLayout()
        self._file_button = QtWidgets.QPushButton()
        self._file_button.clicked.connect(self._choose_file)
        self._file_label = QtWidgets.QLabel()
        row.addWidget(self._file_button, 0)
        row.addWidget(self._file_label, 1)
        layout.addLayout(row)

        options = QtWidgets.QHBoxLayout()
        self._delimiter_label = QtWidgets.QLabel()
        self._delimiter = QtWidgets.QComboBox()
        self._delimiter.currentIndexChanged.connect(self._on_options)
        self._header = QtWidgets.QCheckBox()
        self._header.toggled.connect(self._on_header_toggled)
        options.addWidget(self._delimiter_label, 0)
        options.addWidget(self._delimiter, 0)
        options.addSpacing(theme.PAD_L)
        options.addWidget(self._header, 1)
        layout.addLayout(options)

        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(theme.PAD)
        grid.setVerticalSpacing(theme.PAD_S)
        self._field_labels = {}
        for index, (field, _key) in enumerate(FIELD_LABELS):
            label = QtWidgets.QLabel()
            combo = QtWidgets.QComboBox()
            combo.currentIndexChanged.connect(self._on_assignment)
            self._field_labels[field] = label
            self._combos[field] = combo
            grid.addWidget(label, index // 2, (index % 2) * 2)
            grid.addWidget(combo, index // 2, (index % 2) * 2 + 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        layout.addLayout(grid)

        self._preview = QtWidgets.QTableWidget()
        self._preview.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._preview.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self._preview.setMinimumHeight(170)
        layout.addWidget(self._preview, 1)

        crs_row = QtWidgets.QHBoxLayout()
        self._crs_label = QtWidgets.QLabel()
        self._crs_button = QtWidgets.QPushButton()
        self._crs_button.clicked.connect(self._choose_crs)
        crs_row.addWidget(self._crs_label, 1)
        crs_row.addWidget(self._crs_button, 0)
        layout.addLayout(crs_row)
        self._chunk_crs_label = QtWidgets.QLabel()
        layout.addWidget(self._chunk_crs_label)

        self._summary = QtWidgets.QLabel()
        self._summary.setWordWrap(True)
        layout.addWidget(self._summary)

        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        self._cancel = QtWidgets.QPushButton()
        self._cancel.clicked.connect(self.reject)
        self._ok = QtWidgets.QPushButton()
        self._ok.clicked.connect(self.accept)
        self._ok.setEnabled(False)
        buttons.addWidget(self._cancel)
        buttons.addWidget(self._ok)
        layout.addLayout(buttons)

    def _retranslate(self):
        self.setWindowTitle(i18n.t("imp.title"))
        self._file_button.setText(i18n.t("imp.choose_file"))
        self._delimiter_label.setText(i18n.t("imp.delimiter"))
        self._header.setText(i18n.t("imp.header"))
        self._crs_button.setText(i18n.t("imp.crs_choose"))
        self._cancel.setText(i18n.t("imp.cancel"))
        self._ok.setText(i18n.t("imp.ok"))
        for field, key in FIELD_LABELS:
            self._field_labels[field].setText(i18n.t(key))
        self._delimiter.blockSignals(True)
        self._delimiter.clear()
        for key, _value in DELIMITER_CHOICES:
            self._delimiter.addItem(i18n.t(key))
        self._delimiter.blockSignals(False)
        chunk_name = msapi.crs_name(self._chunk_crs)
        if chunk_name:
            self._chunk_crs_label.setText(i18n.t("imp.chunk_crs", name=chunk_name))
        else:
            self._chunk_crs_label.setText(i18n.t("imp.chunk_crs_none"))

    # ------------------------------------------------------------------
    # Fichier et lecture
    # ------------------------------------------------------------------

    def _choose_file(self, _checked=False):
        start = os.path.dirname(self._path) if self._path else ""
        result = QtWidgets.QFileDialog.getOpenFileName(
            self, i18n.t("imp.dlg_open"), start, file_filter())
        path = result[0] if isinstance(result, (tuple, list)) else result
        if path:
            self.load(str(path))

    def load(self, path):
        """Lit le fichier et propose les colonnes. Renvoie True si la lecture a reussi."""
        try:
            self._text = gcp_table.read_file(path)
        except Exception as exc:
            self._path = path
            self._text = ""
            self._table = None
            self._file_label.setText(i18n.t("imp.read_failed", error=str(exc)))
            self._refresh()
            return False
        self._path = path
        self._file_label.setText(os.path.basename(path))
        self._header_forced = None
        self._parse(reset_columns=True)
        return True

    def _delimiter_value(self):
        index = self._delimiter.currentIndex()
        if 0 <= index < len(DELIMITER_CHOICES):
            return DELIMITER_CHOICES[index][1]
        return None

    def _parse(self, reset_columns):
        self._table = gcp_table.parse_text(
            self._text, self._delimiter_value(), self._header_forced)
        self._header.blockSignals(True)
        self._header.setChecked(self._table.has_header)
        self._header.blockSignals(False)
        if reset_columns:
            self._fill_combos(gcp_table.guess_columns(self._table))
        self._refresh()

    def _fill_combos(self, mapping):
        choices = gcp_table.column_choices(self._table) if self._table else []
        for field, combo in self._combos.items():
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(i18n.t("imp.none"))
            for text in choices:
                combo.addItem(text)
            column = mapping.get(field)
            combo.setCurrentIndex(0 if column is None else column + 1)
            combo.blockSignals(False)

    def _mapping(self):
        mapping = {}
        for field, combo in self._combos.items():
            index = combo.currentIndex()
            mapping[field] = index - 1 if index > 0 else None
        return mapping

    def _on_options(self, _index=0):
        if self._text:
            self._parse(reset_columns=True)

    def _on_header_toggled(self, checked):
        self._header_forced = bool(checked)
        if self._text:
            self._parse(reset_columns=True)

    def _on_assignment(self, _index=0):
        self._refresh()

    # ------------------------------------------------------------------
    # Systeme de coordonnees
    # ------------------------------------------------------------------

    def _choose_crs(self, _checked=False):
        chosen = msapi.choose_crs(i18n.t("imp.crs_caption"), self._crs or self._chunk_crs)
        if chosen is not None:
            self._crs = chosen
            self._refresh()

    def source_crs(self):
        """Systeme du fichier : celui choisi, sinon WGS 84 propose pour des degres,
        sinon celui des marqueurs du chunk."""
        return self._crs or self._auto_crs or self._chunk_crs

    # ------------------------------------------------------------------
    # Apercu et resume
    # ------------------------------------------------------------------

    def records(self):
        return list(self._records)

    def _refresh(self):
        self._refresh_preview()
        if self._table is None:
            self._records, self._rejected = [], []
        else:
            self._records, self._rejected = gcp_table.build_records(
                self._table, self._mapping())
        self._auto_crs = None
        if self._crs is None:
            kind = msapi.crs_kind(self._chunk_crs) if self._chunk_crs is not None else None
            if should_suggest_wgs84(self._records, kind):
                self._auto_crs = msapi.find_crs(WGS84_CODE)
        crs = self._crs
        if crs is not None:
            self._crs_label.setText(i18n.t("imp.crs", name=msapi.crs_name(crs)))
        elif self._auto_crs is not None:
            self._crs_label.setText(i18n.t(
                "imp.crs", name=i18n.t("imp.crs_auto", name=msapi.crs_name(self._auto_crs))))
        elif self._chunk_crs is not None:
            self._crs_label.setText(i18n.t("imp.crs", name=i18n.t("imp.crs_same")))
        else:
            self._crs_label.setText(i18n.t("imp.crs", name=i18n.t("imp.crs_unset")))

        lines = []
        if self._records:
            new, updated = plan(self._records, self._existing)
            lines.append(i18n.t("imp.summary", n=len(self._records), new=new, upd=updated))
            if updated:
                lines.append(i18n.t("imp.warn_overwrite", n=updated))
            if self._mapping().get("z") is None:
                lines.append(i18n.t("imp.warn_no_z"))
            source = self.source_crs()
            if source is not None and self._chunk_crs is not None \
                    and not msapi.same_crs(source, self._chunk_crs):
                lines.append(i18n.t("imp.warn_converted"))
            warning = crs_warning(self._records, source)
            if warning:
                lines.append(i18n.t(warning))
        if self._rejected:
            number, reason = self._rejected[0]
            lines.append(i18n.t("imp.rejected", n=len(self._rejected),
                                line=number, reason=reject_text(reason)))
        needs_crs = self.source_crs() is None
        if needs_crs:
            lines.append(i18n.t("imp.warn_no_crs"))
        self._summary.setText("\n".join(lines))
        self._ok.setEnabled(bool(self._records) and not needs_crs)

    def _refresh_preview(self):
        table = self._table
        self._preview.clear()
        if table is None:
            self._preview.setRowCount(0)
            self._preview.setColumnCount(0)
            return
        rows = table.body[:PREVIEW_ROWS]
        self._preview.setRowCount(len(rows))
        self._preview.setColumnCount(table.width)
        titles = table.header if table.has_header else []
        self._preview.setHorizontalHeaderLabels(
            [titles[c] if c < len(titles) and titles[c] else str(c + 1)
             for c in range(table.width)])
        for row, (_number, cells) in enumerate(rows):
            for column in range(table.width):
                text = cells[column] if column < len(cells) else ""
                self._preview.setItem(row, column, QtWidgets.QTableWidgetItem(text))

    def run(self):
        """Affiche la boite. Renvoie True si l'utilisateur a valide."""
        return bool(_exec(self))


def run(state, panel=None):
    """Point d'entree du bouton « Importer des GCP » du panneau. Ne leve jamais."""
    try:
        chunk = msapi.chunk()
        if chunk is None:
            return _say(panel, i18n.t("dlg.no_chunk"))
        parent = panel if panel is not None else msapi.main_window()
        dialog = GcpImportDialog(chunk, parent)
        if not dialog.run():
            return False
        records = dialog.records()
        result = msapi.import_gcps(chunk, records, dialog.source_crs())
        for label, reason in result["errors"]:
            journal.write("Import GCP : {} en erreur : {}".format(label or "-", reason))
        journal.write("Import GCP : {} cree(s), {} mis a jour, {} en erreur{}{}".format(
            result["created"], result["updated"], len(result["errors"]),
            ", conversion de systeme" if result["converted"] else "",
            ", systeme des marqueurs defini" if result["crs_set"] else ""))
        text = i18n.t("imp.done", new=result["created"], upd=result["updated"])
        if result["errors"]:
            text += i18n.t("imp.done_errors", n=len(result["errors"]))
        _say(panel, text)
        refresh = getattr(panel, "refresh_markers", None)
        if refresh is not None:
            refresh()
        return True
    except Exception as exc:
        journal.write("Import GCP impossible : {!r}".format(exc))
        _say(panel, i18n.t("imp.failed"))
        return False


def _say(panel, text):
    setter = getattr(panel, "set_status", None)
    if setter is not None:
        setter(text)
    return False
