# Development notes

## Look of the panel

One grey theme, the one of Metashape (`theme.py`: window #282828, base #404040, text #eeeeee), no light or dark switch. The header
holds the title, Undo, the language list (a flag in front of each language, painted in code) and the **settings** gear, which
opens the settings window (`settings_dialog.py`: open the review automatically, save marker picks, journal, and the AI models
block, see *AI models and retraining*). The very top is a banner: green strip, the gradient AERO text logo (`assets/logo/helix-aero-AERO-degrade-512px.png`, from the brand kit
validated on 31/10/2026, kept on the server in `1-HELIX AERO\Logo_Helix_Aero_2026-10-31`; the O and AERO are never combined) and a one-line presentation, all of it a link to helix.aero. In the header: Undo and Redo (Ctrl+Z, Ctrl+Y or
Ctrl+Shift+Z, `history.py` keeps a redo stack that a new pin clears), the language list showing only the flag, and the gear, all small.
Confirmation and message boxes are our own (`dialogs.py`, fixed width, text measured to fit) because a QMessageBox cut long text under
Metashape. After removing projections or validating, the marker table is read again at once and twice more (0.5 s and 2 s), since
Metashape recomputes marker positions a little later. At the bottom of the panel: *Tool made by helix.aero* (link) and a GitHub icon (link,
painted from an SVG path by `svgpath.py`). The marker list takes Shift+click and Ctrl+click (several markers at once); a right-click
offers *Remove projections* and *Remove markers* for the selection, after a confirmation. The active marker card is one line:
name (cut at 10 characters, then three dots), pointed count, photo count.

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
- No third-party dependency. Anything heavy (onnxruntime, numpy, opencv; torch and ultralytics for training only) stays out of Metashape's Python.
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
checkerboard (`damier`) and spray or chalk mark (`bombe`). Three drop-down lists choose the type, the scope (**Selected markers** or
**All markers**, meaning every marker that has a 3D position: no pin is needed when the marker has reference coordinates) and which photos to leave alone (**Ignore green + blue**,
green, blue or none). With **Selected markers**, Shift+click or Ctrl+click in the marker list selects several markers and the
detection covers all of them (one selected marker: the active one). Buttons: **Run detection**, **Clear proposals**, **Review**. The choices are saved per machine.

- The calculation never runs inside Metashape. `aigcp.py` writes a `work.json`, starts `aigcp_runner.py` in a separate Python
  (ONNX Runtime, numpy, opencv; no torch), and reads `hits.json` when it is done. The runner groups the markers by photo, decodes
  the next photos in background threads, and runs all the crops of a photo through the model in batches (about 2.7x faster than
  one crop at a time on the benchmark run, same results to 0.74 px). It falls back on ultralytics (torch) when there is no `.onnx`. The panel stays free; the tool's heartbeat (`tool._tick`)
  polls the job and places the results.
- The runner crops a square of `4 x S x pixels-per-metre` around the estimated position of the marker (S = 0.3 / 0.5 / 0.8 m,
  the target size is unknown), resizes it to 160 x 160, keeps the detection closest to the estimate, then re-centres on it.
  If nothing is found it also tries four crops shifted by 0.4 of the crop size, unless the marker's position is confirmed (see
  the guard below). This is the pipeline measured in `DATASET_GCP_IA\modeles\RAPPORT_ENTRAINEMENT_V2.md`.
- **Guard.** When a marker has at least 2 projections (placed by hand or computed by Metashape, AI proposals excluded),
  `msapi.marker_spread_m` measures how far they sit from the reprojection of the marker's 3D point. A detection further than
  `max(gate_min_m, gate_factor x spread)` metres from the expected position is rejected (roof, tree, a neighbouring object) and
  counted in the status line. Defaults: 0.30 m and 5. A marker with fewer projections has no guard but keeps the agreement check below.
- **Agreement between photos.** Each detection is a ray from the camera to the detected pixel (`msapi.pixel_ray`). True
  detections of one target meet at a single 3D point, a roof or a tree does not. `consensus.py` finds the point that gathers the most
  rays (RANSAC on pairs, then least squares) and every detection further than `consensus_m` (0.25 m) from it is rejected. If no
  point gathers at least 3 detections, all are rejected; with fewer than 3 usable rays nothing can be judged and all are kept.
  This works for a marker without any projection, where the guard above has nothing to compare with. Unvalidated proposals
  do move the marker's estimated position in Metashape, which is why a wrong one matters: validate them or clear them.
- **Progress bar.** The runner writes `progress.json` next to `hits.json`; the panel shows a bar under the buttons (moving back and
  forth while the model loads, then proportional to the photos done) and the count in the status line.
- **Already marked photos.** The *Ignore* list picks what the AI leaves alone: green (manual pins), blue (points computed by
  Metashape), both (default) or none. Choosing less lets the AI go over those photos, and **Clear proposals** then puts the previous
  point back (a green one comes back green). A proposal from an earlier run is always retried. A photo whose point changes while
  the calculation runs is left alone.
- A proposal is a projection with `pinned=False` (blue point), remembered by `msapi.mark_proposal`. It never replaces a manual
  pin and is not counted as a pointed photo. Clicking the target replaces it with a normal pin. **Clear proposals** removes the ones
  still unvalidated and puts back the blue point that was there before, if any. Metashape's own blue points are never touched.
- **Review panel.** When a detection ends with proposals, the Review panel opens by itself (setting *Open the review panel
  automatically*, checked by default; otherwise use **Review**). Markers on the left, one thumbnail per photo in the middle,
  cropped around the proposed point with a red cross on it and the confidence in the corner.
  - A click selects a thumbnail, another click unselects it: click every bad one, no Ctrl needed. **Exclude** (or right-click, or
    Delete) takes the selection out of the project at once and the previous point comes back; **Put back** undoes it. The buttons
    are grey when they have nothing to act on.
  - **Refine...** (or Enter, or a double-click) opens the selected thumbnails in ONE large window, one photo at a time: click the
    true centre (mouse wheel zooms, middle button pans), **Next** moves to the next photo still without a point, **Validate**
    puts the points down. A bar at the bottom counts the photos picked (`3/15`); with fewer than all of them the button reads
    *Validate and exit*, with all of them it turns green and reads *Validate*. **Cancel** puts nothing down. Each refined point
    becomes a manual pin (Ctrl+Z undoes it). A refined (or validated) thumbnail can be refined again.
  - Each thumbnail carries a small status tag in its top-right corner: a red cross (excluded), a green check (refined) or a
    hollow check (validated as is). The same word is in the tooltip.
  - **Right-hand panel.** *Zoom* (the short slider and the **+ / -** buttons) zooms *inside* the thumbnails (more or less context
    around the point, 50 to 400 %); **Shift + mouse wheel** does the same, **Ctrl + mouse wheel** makes the thumbnails themselves
    bigger or smaller. Under it, four viewing sliders: exposure, contrast, saturation and sharpness (`imageadjust.py`, numpy
    shipped with Metashape). They only change what the eye sees, in the Review panel AND in the Refine window (same sliders,
    same values, remembered between sessions); the photos, the crops and the points are never modified.
  - **Validate** turns every kept proposal into a manual pin, after a confirmation, with a small waiting window and a progress bar
    (the window is modal, so a second click cannot start a second run). When it ends, and after every exclusion or refinement, the
    marker table of the panel is read again (errors X/Y/Z and projection counts of every marker concerned).
  - Double-clicking a viewing slider (or its name or value) puts it back to 0.
  The runner writes one wide crop per detection (`crops/` of the run folder); the window cuts the thumbnails out of it.
- **PDF report.** After the usual pages, *Photos picked by the AI* shows, for every marker, one thumbnail per photo with the red
  cross and the confidence under it (refined photos say *manual*). Excluded photos are shown too, darkened with a large red cross
  and counted in the marker's header; they do not enter its statistics. Cleared proposals and refinements undone with Ctrl+Z
  are left out. Every page footer links to helix.aero (a PDF link annotation). The thumbnails come from the crops of the run when they still
  exist, else from the photo itself.
- **Feedback for retraining.** Every decision is appended to `<settings>/dataset/aigcp_feedback.jsonl` (format in `review.py`):
  `rejected` is a hard negative, `refined` a corrected position, `accepted` a confirmed one. Only the last event of each
  (run, marker, photo) counts. The file lives in the *storage location* chosen in the settings window, and nothing is written when
  *Use manual refinements to build the training dataset* is unchecked.
- Picking a marker in the panel also selects it in Metashape's Reference pane: Metashape recentres on its own selected marker
  when the photo changes (Page Up / Page Down).
- Weights: `models/damier.pt` and `models/bombe.pt` (v2, `poids_recommande_last.pt` of each `modeles\*_v2` folder) and their ONNX export
  `models/damier.onnx`, `models/bombe.onnx` (what the runner reads; export with `YOLO(...).export(format="onnx", imgsz=160, dynamic=True,
  simplify=True, opset=17)`; the training does it by itself). They are stored in git (the base model "Aero V1.0", released with the
  code); any other `*.pt` / `*.onnx` file stays git-ignored.
- Hidden settings, section `aigcp`: `engine` (auto, light, precise), `python` (path of the Python of the detection engine; default: `AIGCP_PYTHON`, then
  `~/AIGCP/_env/Scripts/python.exe`), `models_dir`, `conf_damier` (0.25), `conf_bombe` (0.30), `gate_min_m` (0.30),
  `gate_factor` (5), `consensus_m` (0.25), and the last `type`, `scope`, `ignore` and `review_thumb` (thumbnail size).
- Limits: the review keeps only the last detection; at most 200 photos per marker and 3000 per run, a marker needs a 3D position and an aligned photo. Measured by script
  on pins the model had never seen (60 per type, lot 2 validation, estimate shifted by 0 and 40 px): 98 to 100 % within 3 px.
  The guard is covered by tests but not measured on real roof false positives yet.

## Engines and installation

`engines.py` (no Qt) knows which engines the external Python has (one short subprocess, cached 20 s): `light` (onnxruntime, numpy,
opencv), `precise` (torch, ultralytics), `training` (precise + onnx). The setting `aigcp.engine` is `auto` (precise if present, else
light), `light` or `precise`; `work.json` gets `"engine": "onnx" | "torch"` and the runner never swaps a chosen engine silently.
`aigcp.check` fails with a clear text when the chosen engine is missing, and `aigcp.setup_target` says what the installer can add.
`install_ui.py` runs `install/install_all.ps1 -Yes -Engine ... [-Training] -PauseOnError` in a visible console and waits for it
(the "Install" buttons of the settings, the box after a failed detection, the retraining pre-check). The installer asks
the engine question itself when you double-click it.

## Updates

`aerotools/updater.py` (no Qt) and `update_dialog.py` (the window, menu Aero > Check for updates and Settings > Updates). The
repository publishes `manifest.json`: SHA-256 and size of every shipped file (code, models, installer, assets, docs; text files
are hashed with LF line endings). **Run `python install/make_manifest.py` before every push**: `tests/test_updater.py` fails when
the manifest is stale. Bump `aerotools.__version__` for a release. The update compares the disk to the remote manifest, downloads
the differences to `%LOCALAPPDATA%\HELIX\AeroTools\updates`, checks each file, backs up the old ones, replaces them, writes
`manifest.json` last, and puts everything back if a step fails. A folder with `.git` is never updated. Hidden settings, section
`update`: `channel` (`main` by default, `dev` to follow the test branch) and `base_url` (tests, mirrors; `file:///` works).
Test an update safely with two folders and `base_url` pointing at the first.

## Diagnostic of the marking

`diagnostic.py` (no Qt): cross-validation of each manual pick (3D position from the other rays, `consensus.triangulate`, gap in
pixels on the judged photo), peeling the worst pick until nothing exceeds 3 px so a bad pick does not accuse its neighbours;
threshold `max(3 px, 4 x median)`; suspect photos; markers with too few picks. `tests/test_diagnostic.py` checks it on synthetic
cameras where the shifted pick is known. Window: `diagnostic_dialog.py`, opened from the "Residual check" menu.

## AI models and retraining

The settings window (gear) has an **AI models** block: the checkbox above, the storage location (Browse...), the model in use
(*Aero V1.0* is ours and cannot be removed; *Local 1.1*, *Local 1.2*... are the ones trained on the user's data, never renamed),
*Add...* (disabled for now), *Manage...* (delete local models) and **Refine my model using my data** with **Revert to Aero V1.0**.

- `training.py` (no Qt) holds the registry (`<storage>/models/<id>/<type>.pt` + `meta.json`, numbers never reused thanks to
  `counter.json`), the active model (`aigcp.weights_path` follows it, a local model that lacks a type falls back on the original
  weights) and `Training`, which runs `aigcp_train.py` in the external Python (needs torch, ultralytics and onnx: `install/installer_entrainement.bat`;
  never inside Metashape). The window asks for confirmation first (graphics card used heavily, do not touch the computer, the result may be
  better on the user's targets and worse elsewhere, wrong annotations give a worse model, the original model is never modified).
- The training starts again from the original weights each time, with all the feedback. Per type: crops around the final point
  of every `refined` and `accepted` event (positives, 25 per target at most, refined first), crops at the AI position of every
  `rejected` event (negatives), a few random backgrounds; train/validation split by physical target; fine-tuning with the
  backbone frozen and a low learning rate; the result reports the validation mAP50 of the original and of the new model, and the
  window warns when the new one scores clearly lower. A type needs at least `MIN_POSITIVES` (30) positives.
- Honest limit: `accepted` events only confirm what the model already finds, so a first retraining on mostly validated points
  barely changes it (measured on 623 events of CITADELLE_Pompeani: mAP50 0.979 before and after). Corrections (`refined`) and
  rejections are what teach it something.

## Hidden settings

Written by hand in `settings.json`, section `fastgcp`. There is no switch for them in the panel.

- `zoom_fenetre_px` (default 400): image width, in pixels, shown around the marker when FastGCP zooms the photo view.
- `recalage_max_px` (default 0, off): maximum distance, in pixels, for snapping a click onto the centre of a 2x2 checker
  target (`tools/fastgcp/snap.py`). Off by default: on CITADELLE_Pompeani (93 manual pins, 11 snapped) the manual pins sat
  0.76 px from the reprojected 3D point on average, and the snapped ones 1.83 px. When on, the raw click is kept next to the
  snapped position in the training file (`x_click`, `y_click`).
