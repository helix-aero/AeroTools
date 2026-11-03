# Development notes

## Layout

```
AeroTools/
  aerotools/
    bootstrap.py       Aero menu, tool loading, reload
    config.py          tool registry (TOOLS) and per-machine settings
    journal.py         in-memory journal and subscribers
    msapi.py           the only place that talks to the Metashape API
    qt.py              PySide2 / PySide6 compatibility
    theme.py           colours, fonts, stylesheets
    tools/
      fastgcp/         session, tool, panel, marking, photos, history, dataset, detection, report
  assets/fonts/
  install/             launcher template, installer, uninstaller
  tests/               pytest tests, no Metashape needed
  tests_qt/            Qt self-tests with real mouse events
```

## How the menu stays

The installer writes `aerotools_launcher.py` into Metashape's scripts folder, with the absolute path of this repository.
At each start Metashape runs it, and it adds the **Aero** menu. A tool module is imported only when its menu entry is clicked.
**Aero > Reload AeroTools** purges the `aerotools` modules and reopens the last tool, so you can edit code without restarting.

Per-machine settings (theme, language, options) live in `%LOCALAPPDATA%\HELIX\AeroTools\settings.json`.

## Adding a tool

1. Declare it in `config.TOOLS` (`id`, `menu`, `titre`, `sous_titre`, `raccourci`, `pret`). `sous_titre` is a text key defined in `aerotools/lang/*.py`.
2. Create `aerotools/tools/<id>/tool.py` with `install()` and `uninstall()`.
3. Restart Metashape or use Reload AeroTools. While `pret` is `False`, the menu entry says the tool is not available yet.

## Rules of the road

- Every Metashape call goes through `msapi.py`. Tools never import `Metashape` directly.
- Python 3.9 syntax, PySide2 first, PySide6 as well. Import Qt through `aerotools/qt.py`.
- No third-party dependency. Anything heavy (torch, ultralytics) stays out of Metashape's Python.
- The report and the panel only use Qt for drawing, so they work inside Metashape.

## Tests

```
python -m pytest tests -q
```

`tests/` uses fake `Metashape` and Qt modules, so it runs anywhere.
`tests_qt/run_qt_selftest.py` sends real mouse events through the real click filter. Run it with Metashape's own Python
(`python\python.exe` in the Metashape folder) and with a Python that has PySide6.

## Languages

The panel and the reports (PDF and CSV) come in French and English. Pick the language with the **FR | EN** switch in the
panel header: the whole panel is translated on the spot, and the choice is saved per machine (`ui.langue` in `settings.json`).
The report reads the language when you export, so the PDF and the CSV follow the switch (file name, number format,
CSV delimiter and decimal separator included: `;` and `,` in French, `,` and `.` in English).

On the very first start there is no saved choice: AeroTools uses Metashape's language, then the system language,
and takes French if that is French, English otherwise.

The **Aero** menu is built once at Metashape startup, in the saved language. A language change reaches it at the next
Metashape start (Reload AeroTools re-reads the code but cannot rename menu entries). Message boxes follow the switch at once.
The technical journal is always French, on purpose.

Code side:

- `aerotools/i18n.py` has no Qt dependency: `t(key, **fields)`, `lang()`, `set_lang(code)`, `subscribe(callback)`,
  `fmt_number()`, `fmt_date()`, `csv_delimiter()`. `t()` never raises: a missing text falls back to French, then to the key.
- Texts live in `aerotools/lang/<code>.py` (`TEXTES`: key to text, `FORMATS`: decimal separator, date pattern, CSV delimiter),
  loaded on demand. `fr.py` is the reference: `en.py` must have the same keys and the same `{fields}` (a test checks it).
- Call `i18n.t()` when you display something, never at import time, so a language change takes effect.
  A test scans the UI modules for visible text left in French.

To add a language (Spanish, say):

1. Copy `aerotools/lang/fr.py` to `aerotools/lang/es.py` and translate the values.
2. Add `("es", "Español")` to `LANGUES` in `aerotools/i18n.py`.
3. Run `python -m pytest tests -q`. The switch gets a new button by itself.

## Training data

When the "AI Training" switch is on, FastGCP appends one line per click (image path, clicked position, image size, marker)
to a file in a visible folder. No image is copied. The file format is documented in `aerotools/tools/fastgcp/dataset.py`.
