"""The small question asked before the PDF report is written: is the table of every photo (part 2) in it? Qt only.

The switch is on when the project has 500 photos or fewer, and off above that (`outputs.default_include_table`). When it is on for a long
project a plain grey line says how many pages that makes, with an "i" that explains why.
"""

from ... import uikit
from ...qt import QtWidgets
from ...uikit import tokens
from . import outputs, report
from .ui_common import tr


class ExportPrompt(uikit.ToolWindow):
    def __init__(self, parent, photos):
        super(ExportPrompt, self).__init__(parent, title=tr("aeroppk.export.title"), modal=True, mark="")
        self.photos = int(photos)
        body = QtWidgets.QWidget()
        lay = uikit.vbox(spacing=tokens.M, parent=body)
        self.check = uikit.Toggle(tr("aeroppk.export.table"), outputs.default_include_table(self.photos))
        self.check.toggled.connect(self._refresh)
        lay.addWidget(self.check)
        row = uikit.hbox(spacing=tokens.S)
        self.warning = uikit.label("", "small", "muted", wrap=False)
        row.addWidget(self.warning)
        self.tip = uikit.InfoButton(tr("aeroppk.export.pages_tip"))
        row.addWidget(self.tip)
        row.addStretch(1)
        lay.addLayout(row)
        self.set_content(body)
        self.set_primary(tr("aeroppk.res.export"), self.accept)
        self._refresh()

    def _refresh(self, *_args):
        text = self.warning_text()
        self.warning.setText(text)
        self.warning.setVisible(bool(text))
        self.tip.setVisible(bool(text))

    def warning_text(self):
        """The grey line about the length of the report: only when the table is ticked and the project is long."""
        if self.check.isChecked() and outputs.table_warning(self.photos):
            return tr("aeroppk.export.pages", pages=report.photo_table_pages(self.photos))
        return ""

    def include(self):
        return self.check.isChecked()

    def ask(self):
        """Show the box. True when the user chose Export."""
        return bool((getattr(self, "exec_", None) or getattr(self, "exec"))())
