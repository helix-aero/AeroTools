# Development notes

## Look of the panel

One grey theme, the one of Metashape (`theme.py`: window #282828, base #404040, text #eeeeee), no light or dark switch. The header
holds the title, Undo and the **settings** gear, which
opens the settings window (`settings_dialog.py`: open the review automatically, save marker picks, journal, and the AI models
block, see *AI models and retraining*). The very top is a banner: green strip, the gradient AERO text logo (`assets/logo/helix-aero-AERO-degrade-512px.png`, from the brand kit
validated on 31/10/2026; the O and AERO are never combined) and a one-line presentation, all of it a link to helix.aero. In the header: Undo and Redo (Ctrl+Z, Ctrl+Y or
Ctrl+Shift+Z, `history.py` keeps a redo stack that a new pin clears), and the gear, all small.
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
      fastgcp/         Aero-GCP: session, tool, panel, marking, photos, history, dataset, detection, report
      fastspace/       Aero-Space: scan, lighten or delete old projects
      aeroexports/     Aero-Exports: ortho and DEM exports, AutoCAD drawing, colours
      aerol3/          Aero-L3
  assets/fonts/
  install/             launcher template, installer, uninstaller
  tests/               pytest tests, no Metashape needed
  tests_qt/            Qt self-tests with real mouse events
```

## How the menu stays

The installer writes `aerotools_launcher.py` into Metashape's scripts folder, with the absolute path of this repository.
At each start Metashape runs it, and it adds the **Aero** menu. A tool module is imported only when its menu entry is clicked.
**Aero > Reload AeroTools** purges the `aerotools` modules and reopens the last tool, so you can edit code without restarting.

Per-machine settings live in `%LOCALAPPDATA%\HELIX\AeroTools\settings.json`.

## Adding a tool

1. Declare it in `config.TOOLS` (`id`, `menu`, `title`, `subtitle`, `shortcut`, `ready`, `reopen`). `subtitle` is a text key defined in `aerotools/lang/en.py`.
2. Create `aerotools/tools/<id>/tool.py` with `install()` and `uninstall()`.
3. Declare its pack in `config.PACKS`: `id` (same as the tool), `paths` (the folders it owns: its package, plus any model or library only it uses), `marker`
   (its `tool.py`), `requires` (the other packs it imports code from). Everything not owned by a pack is the core, always installed.
   `tests/test_packs.py` fails if the tool imports a tool it does not require, or if the core imports a tool. Put code shared by several tools in the core
   (`aerotools/controls.py`, `aerotools/dialogs.py`, `ui.py`, `pdfkit.py`), not in another tool.
   Other core files of this kind: `projtool.py`, `outputdir.py` and a shared library (`aerotools/vendor/proj` is core, the PROJ catalogue files
   `vendor/proj/data/` belong to the tool that reads them).
   `tests/test_tool_independence.py` fails if a tool whose `requires` is empty imports another tool.
4. Run `python install/make_manifest.py`. Users get the tool from **Aero > Install plugins...**; a development copy (`.git`) has every tool.
5. Restart Metashape or use Reload AeroTools. While `ready` is `False`, the menu entry says the tool is not available yet.
6. Document it: one page `docs/tools/<tool-name>.md` (what it does, numbered steps, what each option does, troubleshooting), its screenshots in
   `docs/images/<tool-name>/`, and one row in the tools table of `README.md`. `docs/tools/aero-gcp.md` is the model to copy. Screenshots show
   the window only (no title bar, no project path, no client or server name). `tests/test_docs_links.py` fails on a link to a file or a heading that does not exist.

A tool that needs a choice before it installs (Aero-GCP: which AI engine) adds a small options window in `plugins_dialog.py` and, if something has to run once the
files are there, a `setup` module in its pack (loaded by name: the core never imports a tool).

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
`tests_qt/run_qt_aeroexports.py` does the same for Aero-Exports (both windows, DEM card, pixel field, layout at several font scales, coordinate trap); it needs numpy for the preview cases.

## Texts and formats

Every visible text (panel, menu, message boxes, PDF report, CSV table) lives in one catalog, `aerotools/lang/en.py`: `TEXTS` (key
to text) and `FORMATS` (decimal separator, date pattern, CSV delimiter). Numbers use a decimal point, dates are written
YYYY-MM-DD and the CSV delimiter is a comma.

- `aerotools/i18n.py` has no Qt dependency: `t(key, **fields)`, `has(key)`, `fmt_number()`, `fmt_date()`, `csv_delimiter()`.
  `t()` never raises: a key missing from the catalog gives the key itself, and a `{field}` that is not given is left as written.
- Call `i18n.t()` when you display something, never at import time.
- To add a text, add its key to `TEXTS` in `aerotools/lang/en.py` and use it with `i18n.t("key", name=value)`.

## Training data

When the "AI Training" switch is on, Aero-GCP appends one line per click (image path, clicked position, image size, marker)
to a file in a visible folder. No image is copied. The file format is documented in `aerotools/tools/fastgcp/dataset.py`.

## AI detection (AIGCP)

The **AI detection** card, at the top of the panel, proposes a marker on the photos that should see it. Two models, never mixed:
checkerboard (`checker`) and spray or chalk mark (`spray`). Three drop-down lists choose the type, the scope (**Selected markers** or
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
  the guard below). This is the pipeline measured on the training set.
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
- Weights: `models/checker.pt` and `models/spray.pt` (v2: the last weights of each v2 training run) and their ONNX export
  `models/checker.onnx`, `models/spray.onnx` (what the runner reads; export with `YOLO(...).export(format="onnx", imgsz=160, dynamic=True,
  simplify=True, opset=17)`; the training does it by itself). They are stored in git (the base model "Aero V1.0", released with the
  code); any other `*.pt` / `*.onnx` file stays git-ignored.
- Hidden settings, section `aigcp`: `engine` (auto, light, precise), `python` (path of the Python of the detection engine; default: `AIGCP_PYTHON`, then
  `~/AIGCP/_env/Scripts/python.exe`), `models_dir`, `conf_checker` (0.25), `conf_spray` (0.30), `gate_min_m` (0.30),
  `gate_factor` (5), `consensus_m` (0.25), and the last `type`, `scope`, `ignore` and `review_thumb` (thumbnail size).
- Limits: the review keeps only the last detection; at most 200 photos per marker and 3000 per run, a marker needs a 3D position and an aligned photo. Measured by script
  on pins the model had never seen (60 per type, lot 2 validation, estimate shifted by 0 and 40 px): 98 to 100 % within 3 px.
  The guard is covered by tests but not measured on real roof false positives yet.

## Aero-Exports

Aero-Exports was first called "Fast Export" (package `fastexport`). The package, the tool id and the settings section are now all
`aeroexports`. Migration: `config.load_settings` copies an old `fastexport` section of `settings.json` to `aeroexports` when that
one does not exist yet (the old section is left in place, so nothing is lost; test in `tests/test_config.py`).

`aerotools/tools/aeroexports/`: `naming.py` (short names, `Outputs` folder, versions; ported from the research prototype,
31 tests), `plan.py` (presets and the list of files to write, pure), `dialog.py` (the window), `tool.py`. The Metashape side is
`msapi.raster_info` and `msapi.export_raster` (`Chunk.exportRaster`). Measured on Metashape 2.2.1 on a real project:
- `resolution=0.5` is meters on the ground and gave a 0.49996 pixel in the projection (CC42); `resolution_x/y=0.5` plus a
  `region` snapped outward to multiples of the pixel gives exactly 0.5 and an aligned origin, for the ortho and the DEM
  (same grid, so they line up). A pixel size left untouched exports on Metashape's own grid (no region, no resampling).
- Compression: LZW keeps the alpha band, `TiffCompressionJPEG` writes YCbCr JPEG (no alpha, so the quick view uses a white
  background), the DEM is Float32 + Deflate with nodata -32767. A JPEG export writes a `.jgw` and a `.prj` (compound CRS with the geoid).
- A region with no data writes no file (matters for tiling).
- The GeoTIFF carries the horizontal CRS only, the geoid is in the `.prj` of the JPEG.
Formats and external tools (`pipeline.py`, `external.py`, `dxfout.py`, `plan.py`; nothing is hard-coded, programs are found in the
registry and the usual folders, or pointed at by the user):
- ECW: Global Mapper script `EXPORT_RASTER ... TYPE=ECW TARGET_COMPRESSION=10` (works from a script with the user's licence, Global Mapper 26;
  GDAL cannot write ECW without a Hexagon OEM key).
- JPEG 2000: QGIS GDAL `JP2OpenJPEG QUALITY=5`, else Global Mapper `TYPE=JPEG2000`, else Metashape's own `ImageFormatJP2`.
  Measured on the ortho of a real project (RGB, 48.6 MB raw): ECW 10:1 = 4.6 MB, 36.8 dB; ECW 20:1 = 2.8 MB, 34.5 dB; JP2 GDAL q5 = 5.2 MB, 42.4 dB;
  JP2 GDAL q3 = 3.3 MB, 38.4 dB; JP2 Global Mapper 20:1 = 4.4 MB, 41.4 dB; Metashape JP2 = 13 MB, 55.2 dB. PSNR favours JPEG 2000 at equal
  size; visual comparison is left to the user. GDAL cannot decode ECW (no plugin): ECW was decoded back with Global Mapper to measure it.
- DXF: written by hand (R2000, no library). AutoCAD wants plot style objects, ByBlock/ByLayer linetypes and the empty VPORT/VIEW/UCS/DIMSTYLE
  tables. Checked with `accoreconsole.exe /i TOPO.dxf /s script` (`-IMAGE R *` then `-IMAGE ?`): 0 audit errors, JPEG 2000 and ECW both
  "loaded and relinked" through relative paths; ezdxf `recover` reads it with no error. Not checked: the AutoCAD window itself.
- DWG: `_SAVEAS 2018` in accoreconsole from the DXF.
- DEM in a drawing: GeoTIFF float kept in `00_Assets/DTM` plus a colour image for the layer, made in-house by `relief.py` (numpy, no GDAL):
  Metashape's default DEM palette (read from the legend of the Ortho tab: blue low, red high, linear between the DEM min and max) times a
  hillshade (Horn, light from the north-west at 45 degrees, strength 0.8; checked against `gdaldem hillshade`: mean difference 1.4/255), white
  where there is no data. The nodata value is read from the file.
- Clip (option "Crop white background", on by default): each IMAGE entity gets a polygonal clip boundary (group 71 = 2, vertices in pixels, closed,
  display flags 7, clipping on) computed by `footprint.py` from a coarse mask (alpha of a small uncompressed RGBA export of the ortho by Metashape,
  about 800 px; valid pixels of the DEM), outline = first to last data pixel of each row, simplified, grown by one mask pixel so no data is cut.
  The image files are not changed. AutoCAD 2023 (accoreconsole) loads the DXF with the clip without error; not checked: the clip drawn in the window.
  A failed mask export only adds a warning.
- The native ortho export is cropped to the data (a few thousand pixels a side on a large ortho) and not to the orthomosaic box: the placement in the DXF is read from
  the GeoTIFF itself (`geotiff.py`). When the grid is imposed (other CRS, tiles) the native pixel is rounded to 0.1 mm.
- Coordinate systems and heights, worldwide (`projdb.py`, `vertical.py`, `tiffio.py`, `crs.py`, and `aerotools/projtool.py` in the core, which the other tools use too; engine in `aerotools/vendor/proj`):
  Metashape only reprojects XY (`exportRaster` to a plain `EPSG::n`, heights come out ellipsoidal in that system's frame). The altitude is
  computed by PROJ 9.8.1 (embedded `cs2cs` + `cct`, MIT, copied from the QGIS 4.0.3 build; no pyproj, no dependency on Metashape's Python):
  `H = h - N` with the grid imposed as `+inv +proj=vgridshift +multiplier=1`, applied to the DEM tile by tile (own TIFF reader/rewriter,
  Deflate output, georeferencing tags copied). Our own bilinear reader recomputes the same lattice and the export stops if PROJ and our
  reader differ by more than 1 cm; 5 random pixels are also recomputed without the lattice (5 mm).
  Why PROJ imposed and not automatic: PROJ's automatic choice for RGF93 v1 in Corsica is RAC23, not RAC09 (23 cm on one point).
  Why not Metashape: measured on 2.2.1, it picks the grid by datum name (RAC09 and RAC23 give the same value), ignores RAC23 with RGF93 v2b
  (heights stay ellipsoidal, no error) and refuses EGM96 with a French system.
  Catalogue: `proj.db` (EPSG, read-only through sqlite3) for the systems and their frames; `vendor/proj/data/files.geojson` (PROJ-data
  index: url, size, SHA-256, extent, source and target CRS of the 160 vertical grids) for the heights; `grid_ops.json` = what `projinfo`
  says about each grid (7 are depth surfaces: PROJ flips the vertical axis, never offered). Grids live in `%LOCALAPPDATA%/HELIX/AeroTools/Geoid`
  (nothing is read from Metashape's folders), verified by SHA-256, downloaded from cdn.proj.org only.
  Pairing (`projdb.verticals_for`): a grid is offered when its source frame is the datum of the chosen XY system, or an equivalent
  geocentric modern realization (ETRS89, WGS 84, ITRF, RGF93, GDA2020, NAD83(2011)...) when no exact grid gives the same height system
  (RAC09 stays with RGF93 v1, RAC23 with v2b). A system on a local datum (Bessel, Clarke, NTF, CH1903+...) only gets the ellipsoidal height:
  subtracting an ETRS89 grid from Bessel heights would be wrong by metres (a 3D datum transformation through PROJ would lift that).
  Checks: 111 grids under 1.5 MB downloaded and compared with PROJ: our reader differs by less than 0.001 mm; RAC09 matches Metashape's
  own heights to 0.3 mm on a real project; GTG registration (PixelIsPoint, tie point = node) measured against PROJ (0.03 mm).

### DEM look and preview window

`look.py` (the setting), `palettes.json` (palettes as data), `preview.py` (model of a small DEM, numpy only), `previewwin.py` (the window).
- A `Look` is `palette, shading (none | sun | lights4 | slope), azimuth, altitude, strength, stretch (auto | full | custom), low, high`. It is saved
  in `settings.json` (section `aeroexports`, key `dem_look`), and `relief.render` paints the DXF/DWG DEM image with that same look: what the
  preview shows is what lands in AutoCAD. The default look is the one the export always had (Metashape palette, sun from 315 degrees at 45,
  strength 0.8, full range), except along the data border (see below).
- Palettes: the package file `palettes.json` (metashape, relief, rainbow, gray, viridis, terrain, hot), plus an optional `palettes.json` in the
  settings folder (same format, same id replaces). A broken palette is skipped, never fatal. The Metashape ramp was read from the legend of
  Metashape's Ortho tab; `Elevation.palette` exists in the API (2.2.1) and could be used to check it, see the open points below.
- The preview works on a small DEM (about 1000 px on the long side): the DEM already exported next to the project when it is not older than
  the `.psx`, otherwise `preview.fetch_small` asks Metashape for a low-resolution export (no progress callback: a Qt call inside Metashape's
  export crashed it). `reduce` averages the valid pixels of each k x k square tile by tile. Legend labels come from `Elevation.min/.max`
  (`msapi.dem_range`) when the small DEM stays within 1 % of them, else from the small DEM.
- Repainting a 1000 x 1000 DEM takes 6 to 120 ms (measured on three real DEMs: sun 33 to 87 ms, four lights 54 to 122 ms, palette only 13 to
  33 ms); one 35 ms timer keeps only the latest request, no thread. Without numpy the button is greyed out with an explanation.
- A coloured DEM picture can also be written next to a JPEG 2000 or ECW export (`plan.PICTURE_PRESETS`, option on the DEM card).
- No-data pixels are filled from their data neighbours before the slope is computed (`relief.fill_for_shading`). Filling them with the lowest
  height drew a dark line along every border of the data (visible on a real DEM); only the pixels touching no data changed.
- Tests: `tests/test_aeroexports_look*.py`, `test_aeroexports_preview.py`; `python -B tests_qt\run_qt_aeroexports.py` runs the window, the DEM
  card and the pixel field with real Qt events, without Metashape (14 cases, PySide6 here, also meant for Metashape's PySide2).

### Window layout (two panels), coordinate trap, shared controls

`controls.py` holds what both windows share (no numpy: it loads and is tested anywhere).
- **Two panels** (`dialog.py`): left = what to export (Ortho and DEM cards, DEM look line) and the coordinates; right = format, image type,
  options, file names, then the result line and the buttons, which stay in view under the right panel. Each panel is a `controls.FitScroll`:
  it reports its content's size as its minimum, so the window's minimum (`controls.fit_window`: `SetMinimumSize` on the layout, measured at
  `showEvent` and again once the first layout is done) always follows the fonts and the Windows display scaling. A panel only scrolls when the
  screen is too small (the cap per panel comes from the screen), and then the bars are wide and easy to see. Long texts (file names, folder,
  result) are `controls.WrapText` (wrap anywhere, selectable, never widens the window); combos use `AdjustToMinimumContentsLength`.
  Real Metashape at 125-150 % scaling clipped the first version: the harness now checks every widget stays inside its panel (see below).
- **Look window** (`previewwin.py`): the picture and its legend on the left, the settings on the right (short sliders). `controls.EditableValue`
  is the number next to a slider: double-click it (or Tab to it, Enter / F2) to type an exact number, Enter applies, Esc cancels, a number out of range
  is refused with a red frame and the range in the tooltip (a compass wraps: 360 is 0). `controls.reset_button` puts one control back to its default
  (greyed when it already is); "Back to the Metashape look" resets all.
- **Raster in degrees** (`dialog._sync_auto_crs`): Metashape answers "Empty raster" to a pixel in cm on a geographic raster, and a drawing needs metres.
  When a ticked raster is geographic (`info["geographic_ortho" / "geographic_dem"]`, per raster since an ortho in WGS 84 can sit next to a DEM
  in CC42) and the user has not chosen a system, "Export in" is preselected: the DEM's own projected system, else `projdb.best_projected`
  (first catalogue system for the place that is not in feet / swapped axes). A short orange notice says why, or (user picked "Same as the project")
  what to pick. Unticking the raster in degrees gives back "Same as the project". Exporting a drawing, tiles or a pixel in cm from a raster in
  degrees without a projected system is refused in words before any file is written (`_degrees_blocks`).
- The error line names the file that really failed: `pipeline.Context.failed` is set before each file (and for TOPO.dxf / .dwg).
- Tests: `python -B tests_qt\run_qt_aeroexports.py` (41 cases) includes the layout at x1, x1.25, x1.5 and x2 font scale for both windows
  (`rescale` grows every font, `cut_off` lists any widget past its panel's right edge or the window's) and a small 1024 px screen.
  Offscreen Qt has no real DPI scaling, so this is an approximation of 125-200 %; the real check is Metashape on a scaled display.

### Always the right chunk

The window is not modal, so the active chunk can change in Metashape while it is open. Rule: everything the window does belongs to ONE
chunk, the one named on the first line of the left panel ("Chunk: <label>", an `ElidedLabel`: a long name is cut with "..." and the
full name is the tooltip).
- `refresh()` pins the chunk with what it reads from it: `self._chunk`, `self._pin = (project path, chunk key)`, `self._info`
  (`msapi.raster_info(chunk)`, boxes, systems, dates). The preview (`fetch_small`, `dem_range`), the export (`pipeline.Context.chunk`,
  the clip mask) and the job names all use that chunk and that info, never a fresh `msapi.chunk()`. `refresh()` also drops the preview
  and the planned jobs; the preview signature contains the chunk key.
- `_follow_chunk()` compares the pin with the current project and active chunk. It runs when the window becomes active again
  (`changeEvent`), before the look window opens (`_preview_model`) and before an export (`_on_export`). If they differ it calls
  `refresh()` and writes "The active chunk changed: now working on <label>" on the status line, and the action stops there: nothing is
  previewed or exported on a chunk the user has not seen. It does nothing during an export (the pinned chunk is exported to the end,
  whatever is active meanwhile).
- The journal says which chunk is exported ("Aero-Exports: export of chunk <label>") and when the window follows another one.
- A DEM already exported next to the project is reused for the preview only if `preview.belongs_to` agrees: same size in pixels, same
  pixel and same extent (two pixels) as the pinned chunk's DEM, besides the file being newer than the project. If it does not, a small
  export is asked from Metashape instead. The pipeline deletes a file about to be rewritten before exporting it (the user agreed to
  overwrite), so a file left by an earlier export is never taken for the new one when Metashape writes nothing (empty tile).
- Tests: `tests/test_aeroexports_preview.py` (`belongs_to`), `tests/test_aeroexports_cad.py` (stale files), and the Qt harness
  (two fake chunks with different boxes, dates and systems: switch between refresh and export, switch during an export, preview, the
  header at x1 to x2 font scales). Not covered offscreen: the real activation event (an offscreen window is never active; the harness calls
  `_follow_chunk()` as `changeEvent` does) and Metashape's real `Chunk.key`.

### Which raster, and the boundary clip

Measured live on Metashape 2.2.1 (three real chunks of one project), with read-only console probes:
- A chunk can hold several DEMs and orthomosaics. `chunk.elevation` / `chunk.orthomosaic` are the default ones (the non-italic entries of the
  Workspace pane; a double-click makes another one the default), `chunk.elevations` / `chunk.orthomosaics` list all, and `exportRaster`
  exports the default one. The window therefore targets the default raster only (no choice list) and says which it is under each card:
  "Default DEM: 5200 x 6400 px, 1.25 cm/px." plus "(1 of 4 DEMs: double-click another one in the Workspace pane to make it the default)".
  A default DEM more than 10 times coarser than the default ortho (`COARSE_RATIO`; a chunk whose default DEM was 50 cm/px next to a 0.8 cm ortho)
  gets an orange line "The default DEM is coarse (50 cm/px)".
- `Chunk.exportRaster(clip_to_boundary=True)` is Metashape's default: the export is cut to the chunk's shapes of boundary type OuterBoundary or
  InnerBoundary. One outer boundary left 19.9 % valid pixels in a DEM instead of 51.9 % without the clip; shapes of type NoBoundary cut nothing;
  no shape, no difference. Nothing in the file says it happened, so `msapi.export_raster(..., clip_to_boundary=)` now passes the value on every
  call, never the implicit default.
- The setting: a tick box "Clip to the chunk's boundary shapes", shown only when `msapi.boundary_count(chunk)` finds boundary shapes (a guarded loop,
  done at `refresh()` and when a click needs the exact count). Off by default (the export covers the whole default DEM, as seen in Metashape; ticking it cuts to the shapes), saved as `clip_to_boundary_shapes` in the settings. The same value
  (`ExportDialog._boundary_clip()`) goes to the small DEM of the preview (`preview.fetch_small(clip=)`), to every raster export and to the mask
  export that draws the "Crop white background" polygon (`pipeline.Context.clip_boundary`). The look window says when its picture is clipped.
  A DEM already exported next to the project is reused for the preview only when the chunk has no boundary shape (`boundaries == 0`): with shapes,
  or when the count could not be read, the file may have been written with the other setting, so a small export is made.

### The window follows Metashape by itself

A `QTimer` (`WATCH_MS` = 400 ms) calls `_watch_tick`: only while the window is visible, no export runs and none of our windows (look window,
"already exists" question) is open on top. It compares `msapi.raster_signature(active chunk)` (chunk key, default DEM and ortho as key / size / resolution,
number of shapes: nothing heavy, no loop over the shapes, no export) with the one taken at `refresh()`. A change calls `refresh()` and writes one line:
"The active chunk changed: now working on <name>", "The default DEM changed", "The default orthomosaic changed" or "The boundary shapes of the chunk
changed". An error while reading Metashape is journalled, never raised. A click on Export or on the look button also checks (with the exact boundary
count) and stops after the refresh, as before. The activation event is still handled but nothing relies on it.

### Journal lines for diagnosis

`ExportDialog._describe()`: chunk label and key, default DEM and ortho used (key, label, size, pixel), how many of each the chunk holds, number of boundary
shapes, `clip_to_boundary`. It is written when the window follows a change, before a preview ("preview of ...; DEM footprint: 51.9 % of the pixels have data",
the valid fraction of the small DEM) and before an export ("export of ..."); the pipeline adds "DEM footprint: x % of the pixels have data
(clip_to_boundary=...)" when it draws the clip of the DEM image. So a wrong crop can be diagnosed from `aerotools.log` alone.
Tests: `tests/test_aeroexports_plan.py` (flag, default rasters, signature), `test_aeroexports_clip_pipeline.py` (flag on every export, including the
mask), `test_aeroexports_preview.py`, and the Qt harness (tick box, flags on preview / export / journal, raster lines, real timer, no refresh during an
export, layout at x1 to x2). Only a real Metashape shows whether `boundary_type` strings, `Chunk.key` and the timer behave as in the fakes.

### AutoCAD speed: what was measured

AutoCAD cannot be driven by hand here, so the images were compared through `accoreconsole` (AutoCAD 2023): a DXF with one IMAGE, then
`-PLOT` to PDF, either of the whole image ("extents", zoomed out) or of a 20 x 15 m window at full resolution. This is a proxy: it exercises
AutoCAD's own image reader and resolution selection, not the interactive display cache. Script (one blank line after the first `Y` keeps the
layout name; the output path must not go through a shell that eats backslashes):

```
-PLOT / Y / (blank) / DWG To PDF.pc3 / ISO A1 (841.00 x 594.00 MM) / M / L / N / E (or W, then two corners) / F / C / Y / . / Y / A / out.pdf / N / Y
```

One large ortho (about 70 Mpx) and the same picture enlarged three times on each side (about 640 Mpx), JPEG-compressed in each format,
seconds for the whole plot (AutoCAD start included, about 0.2 to 1.3 s):

| 643 Mpx image                                     | whole image | 20 x 15 m window | size   |
|---------------------------------------------------|-------------|------------------|--------|
| ECW 10:1 (Global Mapper)                          | 3.5         | 1.8              | 13 MB  |
| JPEG 2000, GDAL q5, 1024 tiles (what we write)    | 12.4        | 3.7              | 32 MB  |
| JPEG 2000, 4096 tiles                             | 11.4        | 3.1              | 36 MB  |
| JPEG 2000 written by Global Mapper                | 10.4        | 3.5              | 11 MB  |
| JPEG 2000, no GML box                             | 11.5        | 3.5              | 32 MB  |
| JPEG 2000, RPCL + tile-parts + PLT/TLM            | 20.6        | 5.8              | 32 MB  |
| JPEG 2000, 256 pixel tiles                        | over 300    | not run          | 31 MB  |
| TIFF tiled 256, JPEG, with internal overviews     | 11.6        | 6.1              | 54 MB  |
| TIFF tiled 256, JPEG, without overviews           | 11.5        | 6.9              | 35 MB  |

On the 71 Mpx image: ECW 1.9 / 2.0 s, JPEG 2000 4.1 / 2.2 s, TIFF 1.4 to 1.7 / 2.3 s (whole / window).

- ECW is the only format whose cost does not grow with the size of the image: 3.5x faster than our JPEG 2000 zoomed out and 2x in a window.
- TIFF internal overviews are ignored by AutoCAD (11.6 s with, 11.5 s without); tiled TIFF is no better than JPEG 2000. So no TIFF option.
- JPEG 2000 parameters give 10 to 15 % at best; small tiles are catastrophic (256 px: no answer after 300 s). Never write small JP2 tiles.
- A polygon clip costs 0.6 to 0.7 s on the 643 Mpx JPEG 2000 and 0.1 s on the ECW. An open clip polygon makes AutoCAD reject the whole DXF
  ("Xdata wasn't read", drawing discarded): `dxfout.closed_polygon` now closes any polygon.
- What is not known: how it feels in the AutoCAD window (pan and zoom with the display cache), and what Global Mapper's own DXF contains.
  The lag is not claimed to be fixed; ECW is the best candidate.

5-minute protocol, in AutoCAD, on one real project (needs Global Mapper for ECW):
1. Export the same project three times into three folders with the CAD preset: Images = ECW, Images = JPEG 2000, and (for reference) your
   Global Mapper DXF of the same ortho.
2. Open each `TOPO.dxf`, same machine, same AutoCAD window size. Type `ZOOM` `E` and note how long the picture takes to settle.
3. Zoom to a street-sized window (about 100 m), then pan with the middle button for ten seconds: note if the picture trails the mouse.
4. Type `REGEN` and count seconds. Optionally `-IMAGE` `?` shows the format and path of each image.
5. Report for each file: zoom extents (s), regen (s), pan (smooth / trailing). If ECW is smooth and JPEG 2000 trails, make ECW the default.

### Open points (not verified)

- The preview window was run offscreen with PySide6 only; not yet opened inside Metashape.
- The time of the low-resolution DEM export by Metashape is not measured (the preview itself takes 6 to 120 ms once the DEM is read).
- The Metashape palette ramp was not compared with Metashape's own colouring. It can be: `chunk.exportRaster(source_data=ElevationData,
  raster_transform=RasterTransformPalette)` should give Metashape's coloured DEM, and `chunk.elevation.palette` its exact stops.
- The white-border mask was checked on a real Metashape RGBA export (202 x 241 px, tiled, LZW, alpha 0 / 255; `tests/data/`), but the real
  small mask export (`MASK_SPEC`, uncompressed strips) was not run in Metashape this time.

## Aero-Space

`aerotools/tools/fastspace/`: `scanner.py` (no Qt, no Metashape) finds `.psx` and orphan `.files` folders and measures them
(`<name>.files/<chunk>/<frame>/<part>[.N]/`: `dense_cloud`, `depth_maps`, `point_cloud` = tie points, `elevation`, `orthomosaic`...;
a scan of tens of projects, hundreds of gigabytes in all, takes about 1 s once the file system is cached). An open project is detected by its
`.files/lock`: Metashape keeps it open, so opening it for writing fails (PermissionError); one we can open is a stale lock.
The `orthomosaic[.N]` folder holds the orthophotos (`images*.zip`, the rectified photos: about 90 % of the orthomosaic data
in a scan of tens of projects) next to the small mosaic tiles; the scanner counts them apart (`ortho_photos`), and `Orthomosaic.removeOrthophotos()`
removes them (verified on a copy: space freed, mosaic and DEM intact, project re-opens). `lighten(project, mode, items=...)` takes any of
`dense`, `depth`, `ortho`; the window's Lighten screen lets the user tick them and shows the space each one frees.
`actions.py`: `lighten` goes through the Metashape API so the project stays consistent (`Document.open`, `chunk.remove` of the
dense clouds (`chunk.point_clouds`) and the depth map sets (`chunk.depth_maps_sets`), `save`, then a read-only re-open that checks
cameras and markers did not change). Measured on a copy: Metashape erases its own files on `save` (about 2 GB to about 1 GB). So lightening is
always permanent. Sending the folders to the bin first does not work: `chunk.remove` then raises `Can't open file dense_cloud.zip`
and the project keeps ghost dense clouds. A possible later step: hard-link the folders and copy the small metadata zips to a
side folder before the API call, then send that folder to the bin. `delete_project` only ever removes the `.psx` and its `.files`, so exported files next to the project are never touched
(`test_delete_keeps_every_file_next_to_the_project`). `scanner.measure_kept` counts those files for the **Kept** column (same
folder plus one level of sub-folders, at most 5000 entries, cached per folder because several projects can share one),
and `Project.at_risk` flags a project that holds a built DEM or ortho (`elevation`, `orthomosaic`) with no exported raster or
cloud (`EXPORT_EXTS`) found there. `delete_project` can use the bin (`SHFileOperationW` with
`FOF_ALLOWUNDO`; Windows asks itself when something is too big for the bin). `delete_project` removes the `.psx` first, then the
`.files` folder (a failure leaves a visible orphan). Permanent deletion needs the typed word `DELETE`. `lighten` runs in the main
thread (the API does not work elsewhere); scanning and deleting run in a worker. Actions are logged to
`%LOCALAPPDATA%\HELIX\AeroTools\fastspace\log.csv`. Tests use a fake Metashape document (`tests/test_fastspace.py`); the
Metashape API part was checked on a copy in the Metashape console; the window itself should be tried on a copy before real work.

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
`update`: `channel` (`main` by default, `dev` to follow the dev branch; the updates window has a Stable branch / Dev branch selector that writes it; dev is always stable plus the newest changes, and a change reaches `main` only once tested on dev) and `base_url` (tests, mirrors; `file:///` works).
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
  weights) and `Training`, which runs `aigcp_train.py` in the external Python (needs torch, ultralytics and onnx: `install/install_training.bat`;
  never inside Metashape). The window asks for confirmation first (graphics card used heavily, do not touch the computer, the result may be
  better on the user's targets and worse elsewhere, wrong annotations give a worse model, the original model is never modified).
- The training starts again from the original weights each time, with all the feedback. Per type: crops around the final point
  of every `refined` and `accepted` event (positives, 25 per target at most, refined first), crops at the AI position of every
  `rejected` event (negatives), a few random backgrounds; train/validation split by physical target; fine-tuning with the
  backbone frozen and a low learning rate; the result reports the validation mAP50 of the original and of the new model, and the
  window warns when the new one scores clearly lower. A type needs at least `MIN_POSITIVES` (30) positives.
- Honest limit: `accepted` events only confirm what the model already finds, so a first retraining on mostly validated points
  barely changes it (measured on several hundred events of a test project: mAP50 the same before and after). Corrections (`refined`) and
  rejections are what teach it something.

## Hidden settings

Written by hand in `settings.json`, section `fastgcp`. There is no switch for them in the panel.

- `zoom_window_px` (default 400): image width, in pixels, shown around the marker when Aero-GCP zooms the photo view.
- `snap_max_px` (default 0, off): maximum distance, in pixels, for snapping a click onto the centre of a 2x2 checker
  target (`tools/fastgcp/snap.py`). Off by default: on a test project the manual pins sat
  under 1 px from the reprojected 3D point on average, and the snapped ones about 2 px. When on, the raw click is kept next to the
  snapped position in the training file (`x_click`, `y_click`).
