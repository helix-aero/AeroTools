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

## AI detection (AIGCP)

The **AI detection** card, at the top of the panel, proposes a marker on the photos that should see it. Two models, never mixed:
checkerboard (`damier`) and spray or chalk mark (`bombe`). The card has a type switch, a scope switch (**Selected marker** or
**All**, meaning every marker that has at least 2 projections) and the **Run detection** and **Clear proposals** buttons.
The choices are saved per machine.

- The calculation never runs inside Metashape. `aigcp.py` writes a `work.json`, starts `aigcp_runner.py` in a separate Python
  that has torch and ultralytics, and reads `hits.json` when it is done. The panel stays free; the tool's heartbeat (`tool._tick`)
  polls the job and places the results.
- The runner crops a square of `4 x S x pixels-per-metre` around the estimated position of the marker (S = 0.3 / 0.5 / 0.8 m,
  the target size is unknown), resizes it to 160 x 160, keeps the detection closest to the estimate, then re-centres on it.
  If nothing is found it also tries four crops shifted by 0.4 of the crop size, unless the marker's position is confirmed (see
  the guard below). This is the pipeline measured in `DATASET_GCP_IA\modeles\RAPPORT_ENTRAINEMENT_V2.md`.
- **Guard.** When a marker has at least 2 projections (placed by hand or computed by Metashape, AI proposals excluded),
  `msapi.marker_spread_m` measures how far they sit from the reprojection of the marker's 3D point. A detection further than
  `max(gate_min_m, gate_factor x spread)` metres from the expected position is rejected (roof, tree, a neighbouring object) and
  counted in the status line. Defaults: 0.30 m and 5. A marker with fewer projections has no guard, and the **All** scope skips it.
- **Agreement between photos.** Each detection is a ray from the camera to the detected pixel (`msapi.pixel_ray`). True
  detections of one target meet at a single 3D point, a roof or a tree does not. `consensus.py` finds the point that gathers the most
  rays (RANSAC on pairs, then least squares) and every detection further than `consensus_m` (0.25 m) from it is rejected. If no
  point gathers at least 3 detections, all are rejected; with fewer than 3 usable rays nothing can be judged and all are kept.
  This works for a marker without any projection, where the guard above has nothing to compare with. Unvalidated proposals
  do move the marker's estimated position in Metashape, which is why a wrong one matters: validate them or clear them.
- **Progress bar.** The runner writes `progress.json` next to `hits.json`; the panel shows a bar under the buttons (moving back and
  forth while the model loads, then proportional to the photos done) and the count in the status line.
- **Already marked photos.** The settings card ("More") has two checkboxes, both checked by default: *AI: skip photos already
  marked (green)* and *AI: skip photos with a blue point*. Checked means the AI leaves those photos alone. Unchecking one lets the
  AI go over it, and **Clear proposals** then puts the previous point back (a green one comes back green). A proposal from an
  earlier run is always retried. A photo whose point changes while the calculation runs is left alone.
- A proposal is a projection with `pinned=False` (blue point), remembered by `msapi.mark_proposal`. It never replaces a manual
  pin and is not counted as a pointed photo. Clicking the target replaces it with a normal pin. **Clear proposals** removes the ones
  still unvalidated and puts back the blue point that was there before, if any. Metashape's own blue points are never touched.
- Picking a marker in the panel also selects it in Metashape's Reference pane: Metashape recentres on its own selected marker
  when the photo changes (Page Up / Page Down).
- Weights: `models/damier.pt` and `models/bombe.pt` (v2, `poids_recommande_last.pt` of each `modeles\*_v2` folder). `*.pt` is
  git-ignored: copy them by hand next to the code.
- Hidden settings, section `aigcp`: `python` (path of the Python with torch and ultralytics; default: `AIGCP_PYTHON`, then
  `~/AIGCP/_env/Scripts/python.exe`), `models_dir`, `conf_damier` (0.25), `conf_bombe` (0.30), `gate_min_m` (0.30),
  `gate_factor` (5), `consensus_m` (0.25), and the last `type` and `scope`.
- Limits: at most 200 photos per marker and 3000 per run, a marker needs a 3D position and an aligned photo. Measured by script
  on pins the model had never seen (60 per type, lot 2 validation, estimate shifted by 0 and 40 px): 98 to 100 % within 3 px.
  The guard is covered by tests but not measured on real roof false positives yet.

## Hidden settings

Written by hand in `settings.json`, section `fastgcp`. There is no switch for them in the panel.

- `zoom_fenetre_px` (default 400): image width, in pixels, shown around the marker when FastGCP zooms the photo view.
- `recalage_max_px` (default 0, off): maximum distance, in pixels, for snapping a click onto the centre of a 2x2 checker
  target (`tools/fastgcp/snap.py`). Off by default: on CITADELLE_Pompeani (93 manual pins, 11 snapped) the manual pins sat
  0.76 px from the reprojected 3D point on average, and the snapped ones 1.83 px. When on, the raw click is kept next to the
  snapped position in the training file (`x_click`, `y_click`).
