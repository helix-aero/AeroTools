# Aero-Facade: product specification (v1, written 2026-11-06 from the product owner's brief)

Tool id `aerofacade`, package `aerotools/tools/aerofacade/`, menu entry **Aero > Aero-Facade**. UI texts in
`lang/en.py`. Audience: novice operators. Read first: `docs/AERO_FACADE_STUDY.md` (what the Metashape 2.2.1 API can and
cannot do, measured), `aerotools/tools/aerofacade/detect.py` (wall detector prototype), `docs/facade_console_build.py` (planar DEM + ortho
from tie points, verified in pieces), `aerotools/tools/aeroexports/` (window, theme, controls, config, journal patterns to reuse).

## What the tool does

Produces one ortho-facade picture per detected wall of a building, from a Metashape chunk (photogrammetry of a building). The user
never touches the Metashape GUI: one window, a wizard in 6 screens.

### Screen 1 — Search area
- Left/centre: a picture of the chunk's orthomosaic (our own Qt widget, from a small low-resolution export of the orthomosaic;
  pan + zoom with the wheel). Right: short instructions and the **Run facade detection** button, disabled at first.
- A polygon tool: the operator clicks points on the picture to draw the **search area** (a polygon; double click or click on the first
  point closes it; Esc cancels; the polygon can be redrawn; Delete clears). When a valid polygon exists, **Run** becomes enabled.
- The point is to limit the computation: only data inside this polygon is used (all heights).

### Screen 2 — Detected facades
- **Run** starts the detection in the background (the window stays alive, a progress indicator and a Cancel button show). The detector
  works on the tie points inside the polygon (fast, always available); see `detect.py`.
- Each detected facade is drawn on the ortho picture as a polyline in its own colour with its name **A, B, C, D…** (letters, then AA,
  AB… if more than 26). Hover / selection highlights it.
- Right panel: a **list** of the facades: colour swatch, name, **length (m)** and **height (m)** (and orientation as small grey text).
  Click selects, Shift-click and Ctrl-click multi-select, **right click > Remove** (and the Delete key) removes the selected ones from the
  list (and from the picture), **Undo** brings them back. Removed facades are not processed.
- A facade can be selected in the list to zoom on it on the picture.

### Screen 3 — Margins (the area around each plane)
For the selected facades (or all, with an **Apply to all** switch): how much to take around the detected plane, in metres:
- **Left** and **Right** with a small **link badge** between them (linked = changing one changes the other; click the badge to unlink);
- **Top** and **Bottom** (also with a link badge between them, linked by default);
- **Depth** (kept in front of / behind the wall plane, default 2.0 m; hint: more depth keeps balconies, less avoids trees).
- Default margins: 1.0 m left/right/top/bottom. A small schematic drawing of a facade with the margin area shaded shows the effect, live.
- Also the **pixel size** of the final orthophoto (default 3 cm; hint that smaller pixels are heavier).

### Screen 4 — Method for the DEM under each facade ("MNT" in the plane)
Radio choices, each with a one-line explanation. Availability is detected in the chunk; **an unavailable method is shown greyed**
with the reason ("no dense cloud in this chunk"). The best available one is preselected. Ranking by quality (best first; decided in
the spec, with the reasons shown to the operator as small grey text):
1. **Dense cloud** — the most complete and cleanest surface (filtered, dense). Best quality, and the fastest to use when it already
   exists. Needs the chunk to have a dense cloud; if it must be built it takes long (shown as a warning, not offered when absent).
2. **Depth maps** — very good detail, a good compromise: quality close to the dense cloud, available in most projects, and quick
   because only the area of the facade is processed. Default for most projects when there is no dense cloud.
3. **Tie points** — always available but sparse: poor result (holes, rough surface). Offered with the warning "good enough for a quick
   look, but expect a poor result".
Each option carries a time hint ("about N min for K facades": estimate from the area; honest "unknown" if not estimable). If the
selected method will take a long time, a gentle notice says so.
(The detection of screen 2 always uses the tie points. This screen only chooses how the surface is built for the ortho.)

### Screen 5 — Batch build of the DEMs
- If DEMs for the chosen facades/method **already exist in the chunk** (we tag the assets we create), skip straight to screen 6.
- Otherwise: a list of what will be computed, per facade; one button **Build**; then the batch runs **sequentially**, with a clear
  "Please do not touch Metashape, please wait" banner and a progress list (waiting / running / done / failed per facade) and a Cancel.
  It is fine that Metashape's own progress dialog appears. To keep it fast, every computation is restricted to a **generated bounding
  region around the facade** (plane + margins + depth), never the whole chunk.
- End: a success message with two buttons: **Exit** and **Continue to build the orthophotos**. Failures are listed per facade with
  a plain-English reason and a Retry.

### Screen 6 — Orthophotos and export
- Preview of each facade's orthophoto (small thumbnail) in a list; checkboxes to pick which to export; output folder (default:
  `Outputs` next to the project, like Aero-Exports; the folder is created); format choices (PNG / JPEG / TIFF + a georeferencing
  text file: origin, pixel size, plane frame, so the picture can be placed and measured; optional DXF with the picture inserted).
- **Export** writes one file per facade named `<site>_<date>_facade_<A>.<ext>`; a final success screen with **Open folder**.

## Architecture (three layers, so three people can work in parallel)

1. `model.py`, `geometry.py`, `methods.py`, `detect.py` — **pure Python (numpy at most), no Qt, no Metashape**: dataclasses
   (`Facade` with letter, colour, plane frame, length, height, `Margins`, `Method`), polygon-in-area filtering, naming, margin /
   plane-frame / bounding-region maths, method ranking and availability rules, time estimates. Fully unit-tested.
2. `msfacade.py` — the **Metashape adapter** (the only module that imports `Metashape`; same style as `aerotools/msapi.py`):
   reads data availability, ortho preview export, tie points inside a polygon, builds the per-facade DEM (three methods), builds the
   orthophoto in the facade plane, and **gets the picture out as a file** (the study found that `exportRaster` writes nothing for a
   vertical plane: a working export path must be found and *measured* — calibrated `ortho_view.captureView`, tiling of captures, or
   our own reprojection from the images; whatever is correct to the centimetre). It exposes a `Backend` class implementing the protocol below.
3. `wizard.py` + `screens_*.py` + `widgets.py` + `tool.py` — **Qt UI**. It talks only to a `Backend` object, so it runs with a
   `FakeBackend` (synthetic building) in tests and offscreen screenshots, and with `msfacade.MetashapeBackend` in Metashape.

### `Backend` protocol (frozen, change only by agreement in `docs/AERO_FACADE_SPEC.md`)
```python
class Backend:
    def project_info(self) -> dict            # {"chunk": label, "site": str, "date": "YYMMDD", "outputs": folder, "crs": str}
    def availability(self) -> dict            # {"tie_points": (ok, reason), "depth_maps": (ok, reason), "dense_cloud": (ok, reason)}
    def ortho_preview(self, max_px=1600)      # (RGB uint8 array HxWx3, extent (left, bottom, right, top) in `crs` metres) or raises
    def detect(self, polygon, progress, cancelled) -> list[Facade]   # polygon: [(x, y)] in the preview's crs; runs off the UI thread
    def existing_dems(self, facades, margins, method) -> dict        # letter -> True/False
    def build_dems(self, facades, margins, method, progress, cancelled) -> dict   # letter -> (ok, message); sequential
    def build_orthos(self, facades, margins, method, pixel_m, progress, cancelled) -> dict  # letter -> (ok, message)
    def thumbnail(self, facade) -> array      # small RGB picture of the built orthophoto
    def export(self, facades, folder, fmt, options, progress, cancelled) -> list[(path, ok, message)]
```
All long calls report `progress(fraction, text)` and honour `cancelled()`. Metashape calls that need the main thread are made by the
backend on the main thread (the UI calls `detect/build/export` from a worker thread or a timer-driven step runner: the adapter agent
decides which, documents it, and the UI agent follows; NO Qt widget is touched from inside a Metashape progress callback, ever: it
crashed Metashape before).

## Rules for everybody
- Never push, merge or open PRs (the owner decides later). Local commits on branch `aerofacade-ui` of `C:\Users\LTB1\AeroTools_test`,
  small commits, English titles, ending with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`; commit only your own files
  (`git add <paths>`; if git reports an index lock, wait a few seconds and retry).
- The owner's live Metashape imports this tree: write each file whole and atomically (Write tool, never Bash heredocs with
  backslashes), keep the tree importable (`python -m compileall aerotools`) and the suite green before every commit. The package
  `aerofacade` is only imported when the tool starts; the shared files (`config.py`, menu registration, `lang/en.py`,
  `manifest.json`) are touched in small, careful edits.
- Tests: `C:\Users\LTB1\AppData\Local\Temp\claude\C--Users-LTB1-Desktop\0e8331c3-9b57-48b0-82bc-f4616d429b51\scratchpad\venv\Scripts\python.exe -m pytest tests -q`
  (829 pass now); after adding files run `python install/make_manifest.py`. Qt harness pattern: `tests_qt/run_qt_aeroexports.py` +
  `tests_qt/_isolation.py` (never touch the real settings; PySide6 offscreen for tests, PySide2 in Metashape; Python 3.9 compatible code).
- UI: understandable by a 10-year-old, safe for novices, nothing clipped at any font scale (use `aeroexports/controls.py` helpers),
  English texts, the Aero theme (`aerotools/theme.py`, `aerotools/ui.py`).
- **Only the "Metashape adapter" agent drives Metashape's GUI** (computer-use). The test project is `carro_test_facade.psx` (CARRO,
  724 aligned photos, ~1.1 M tie points, one big white building in the middle), already open in the owner's Metashape: it is a
  throw-away test project, you may modify it, but never close Metashape. Work on chunk copies where it matters. Console technique:
  click the Metashape Python console input (bottom right), `exec(open(script).read())`, scripts write results to files. Never run
  `metashape.exe -r`. If a click is refused ("user interrupt"), wait 20 s and retry once.
- Keep `C:\Users\LTB1\AppData\Local\Temp\claude\C--Users-LTB1-Desktop\0e8331c3-9b57-48b0-82bc-f4616d429b51\scratchpad\facade_shots\`
  for screenshots (PNG) that the owner will see: name them `NN_what.png`; the UI agent produces offscreen renders of every screen at
  several font scales; the adapter agent keeps live screenshots of Metashape results.
- Final reports concise, measured vs not verified.


## UI notes (UI agent)

Files: `wizard.py` (window, step bar, Back / Next / Cancel, job runner), `screens_search.py` (screens 1 and 2), `screens_margins.py` (3 and 4),
`screens_build.py` (5 and 6), `widgets.py` (OrthoCanvas, FacadeList, BatchProgress, OrthoThumbnails), `widgets_margins.py` (NumberField, LinkBadge,
MarginSchematic, MarginEditor, MethodPicker), `state.py` (what the wizard knows and the rules that enable the buttons, pure), `tasks.py` (worker
thread, pure), `common.py`, `theme_facade.py`, `tool.py` (`install()` / `uninstall()` as called by `bootstrap`, `start` / `stop` are aliases).
Tests: `tests/test_aerofacade_ui_logic.py` (no display) and `python -B tests_qt/run_qt_aerofacade.py [--shots FOLDER]` (offscreen, FakeBackend, isolated settings).

Threading, as the wizard uses it: every backend call goes through `wizard.run_job`. If `backend.threaded` is absent or True (FakeBackend) the call runs on a
worker thread (`tasks.Task`); the backend only writes plain state through `progress(fraction, text)` and the window reads it with a 100 ms timer.
If `backend.threaded` is False (the Metashape adapter: `tool.make_backend` sets it and passes `pump=QApplication.processEvents`) the call runs on the
main thread and blocks; the timer fires only when the adapter calls `pump()` between Metashape calls, never inside a Metashape callback.
The long steps (DEMs, orthophotos) call the backend ONE FACADE AT A TIME (`build_dems([f], ...)`), so each wall has its own result row; export is one call.

Assumption to confirm: the wizard draws `Facade.polyline_xy()` on the orthomosaic preview WITHOUT converting, so the facade frame (local ENU) and the
preview `extent` / polygon (`crs`) must be the same plane frame. FakeBackend does that. The adapter converts inside if its two frames differ.

Texts: `facade.ui.*` (UI), the engine owns `facade.method.*`. The method cards use `methods.describe`; an unavailable method shows only its reason.
New texts from now on are written in English only (lang/en.py).


## Engine notes (engine agent)

Files: `model.py` (Facade, Margins, Method, Availability, Settings, `letter_name`, `margins_for`), `backend.py` (`Backend`, `Cancelled`, `Progress`),
`fake_backend.py` (FakeBackend), `geometry.py`, `methods.py`, `detect.py`. Tests: `tests/test_aerofacade_{model,fake,geometry,geometry_plane,methods,detect,engine_detect}.py`.

Contracts the other layers rely on (additions only, the protocol section above is unchanged):
- `margins` in `existing_dems / build_dems / build_orthos` is one `Margins` OR a dict `letter -> Margins` (`"*"` = default): always read it with `model.margins_for(margins, facade)`.
- Facade frame: origin = bottom-left corner of the wall seen from the front, `u` along the wall to the right, `v` up, `n` toward the cameras, `u x v = n`;
  `azimuth` = compass bearing of the direction the facade FACES (south front = 180). Local ENU metres, same frame as the preview `extent` in the fake.
- `geometry.bounding_region(facade, margins)` gives centre, size (along u, v, n: length + left + right, height + top + bottom, 2 x depth), `rot` (3 x 3 nested lists,
  columns u, v, n), `box2d` (xmin, ymin, xmax, ymax in plane coordinates, what a 2D region takes) and the 8 corners. The adapter multiplies by `chunk.transform.scale`.
- `detect.detect_in_polygon(points_enu, polygon, progress, cancelled, cameras=None)` is what a backend's `detect` calls (the Metashape adapter reads the tie points
  in ENU metres, passes the camera centres in ENU as `cameras`, which is only a tie-breaker for the side of the wall). It returns `Facade` objects named A, B, C by
  decreasing area. Progress texts are translated (`methods.stage_text`). ~3 to 5 s for 1.1 M tie points.
- `geometry.DEM_TIME` holds the time model of the three methods (tie points measured in the study, the other two first estimates): the adapter should update these
  numbers when it measures DEM times, `methods.time_hint` then follows.
- Texts: `facade.method.*`, `facade.margin.*`, `facade.detect.*` live in `lang/en.py`; the keys are listed in `methods.py`
  tables (`METHOD_KEYS`, `MARGIN_KEYS`, `DETECT_KEYS`, ...), because the catalog test needs each key to appear literally in the source.

### The detector, as tuned on CARRO (1.1 M real tie points) and on the synthetic scenes
The first prototype (Hough on cells with a 2 m vertical extent, strict two-sided contrast) gave ONE 75 m wall on CARRO: real tie points are far thicker than synthetic ones
(balconies and shutters spread the south front over 1 m, a stair box, noise below the roof). What changed:
1. **Vertical-occupancy test** instead of the vertical extent: a plan cell (0.5 m) is a wall cell when its column holds points in at least 8 different 0.5 m height bins.
   A wall fills a column, a roof or the ground fills 1 or 2 bins whatever the density, stray points under a roof cannot fill a column. (The extent test flagged the whole roof.)
2. **Thick facades**: the Hough peak is snapped to the densest plane within 1.8 m; the cells within 1.2 m of an accepted wall stop voting (relief cannot seed a wall);
   a wall that fills its rectangle (fill >= 0.5, >= 0.5 pt/m2, >= 40 m2) is accepted on its weaker flank only, and then must have nothing behind it (density behind <= 10 % of the band's:
   rejects trees and hedges, which have as many points behind as in front).
3. **Two passes** (`two_pass`, method "auto"): pass 1 as above; pass 2 = the original tolerant extent test on the points NOT within 1.7 m of a pass-1 wall (sparse walls, small houses).
4. **Clean-up**: duplicates and relief removed (`suppress`: same plane within 0.7 m, or a short / weak parallel piece within 1.8 m; longer comparable pieces at another depth stay as setbacks),
   a direction gate (`aligned_with_axes`: a wall dwarfed 5x by a neighbour within 30 m must be parallel / perpendicular to an anchor wall, or show contrast >= 12),
   `regularize` (walls within 4.5 degrees of the main axes are made exactly parallel / perpendicular, only when 85 % of their inliers stay inliers).
5. Height = 1 % / 99 % of the heights widened by 2 % (the top of a wall is sharp, the foot fuzzy); orientation = the side with ground-level points in front (the inside is never seen), cameras only as a tie-breaker.

Measured (this PC, numpy 2.5, one core):
- CARRO, tight polygon around the building (850 k points): **4 facades, no false wall**, 3.4 s. South 64.7 m x 22.7 m (z 63.6 to 86.3), north 66.9 m x 17.0 m, west 12.0 m x 24.2 m, east 12.5 m x 17.2 m;
  facing S / N / W / E. Plans (DXF elevations): south 65.96 m, north 66.46 m (detected -1.2 m and +0.4 m); the two gables are 15.7 m on the plans, which include balconies and the stair box
  (not comparable with the 12 m of the wall plane; the roof plan is 11.8 m deep). North and south planes are parallel to 0 degrees (regularised; 0.5 degree before) and **10.50 m apart**
  (the study: 10.5 m). The plans give elevations only, so no absolute plane position is compared. Heights are lower than the plans' 26 m (ground floor garages and the hillside: the visible wall
  stands 17 m on the north side), about 1.5 m under on the south foot.
- CARRO, a loose polygon (120 m x 85 m): the same 4 plus 2 small extra pieces (13 m x 8 m and 6 m x 7 m, other structures); the whole cloud: 3 to 4 extra small pieces, 3.9 s. None is a tree or a roof.
- Synthetic scenes (7 scenes of 100 m, 20 true walls each, density 1 to 5 pt/m2 of facade, faces seen at 30 to 100 %): recall by length 0.79 to 0.93 (mean 0.86), precision 0.95 to 1.0, 0 to 1 false wall
  per scene, 0.1 s. The original detector scored recall 0.91 and precision 0.98 on the same scenes: the new one gives up a little on sparse walls (below ~0.5 pt/m2) to work on real clouds.
- Not verified: other real sites (Citadelle tie points were not re-run), leaning walls (method "combined" is untouched and slow), curved walls.

Open points: the gables of CARRO come out 12 m, not 15.7 m; very low walls (2 to 4 m high, < 40 m2) are dropped by `min_area` in the thick path only; a wall seen through a 2 m alley may fail the "nothing behind it" test.


## Adapter notes (Metashape adapter agent, `msfacade.py`; live measurements on CARRO, Metashape 2.2.1, 2026-11-06)

### Export of a vertical-plane orthophoto: DECIDED, measured
Option (a) works and is the one in the code: **rotate the coordinate system of a throw-away work chunk so that the facade plane is the horizontal
plane, then use the ordinary horizontal `buildDem` / `buildOrthomosaic` / `exportRaster`.** `chunk.copy(items=[TiePointsData], keypoints=False)`
(1.6 s), `chunk.crs = CoordinateSystem('LOCAL_CS["AeroFacade plane",LOCAL_DATUM["Local Datum",0],UNIT["metre",1]]')`,
`chunk.transform.matrix = A * E` where `E` is chunk -> local ENU metres (`localframe * transform`) and `A` ENU -> plane frame (x along the wall to the
right, y up, z toward the viewer, origin at the bottom-left corner of the wall). After that no `OrthoProjection` is needed: the default horizontal
projection of the chunk is the facade plane. The user's chunk is never touched.

| What | Measured (CARRO south facade, 65.7 m x 21.6 m, margins 1 m, 3 cm) |
|---|---|
| `exportRaster` PNG / TIFF / JPEG of the rotated ortho | writes a file in 0.2 to 0.6 s (2255 x 786 px: 2.5 MB PNG, 5.4 MB TIFF, 0.5 MB JPEG) |
| pixel size and extent | exactly the requested ones (`ortho.resolution` 0.0300, box `-1.0 .. 80.87` for a requested `-1.0 .. 80.89`), GeoTIFF tags present (pixel scale 0.03, tie point = top-left corner in plane metres, local CS) |
| orientation | upright and not mirrored; the first test used a wall normal pointing into the building and still gave a clean picture (cameras of the far side projected through the slab): see `_orient` below |
| scale | the distance between the two vertical joints of the south facade is 23.01 m on our picture, 23.03 m on the reference `facade_sud.jpg` (6621 px, assumed 1.000 cm per pixel: its true pixel size is not known, so this is a consistency check, not an absolute accuracy); building ends fall on the picture where the detected wall ends (`s = 0` -> column 33, `s = L` -> column 2223) |
| time | copy 1.6 s, DEM from tie points 13.5 s, orthophoto 39 s (2255 x 786 px; 29 s for 2299 x 631), export < 1 s |
| (b) `ortho_view.captureView` | not used: bound to the screen size and to the GUI, framing unexplained in the study; (a) makes it unnecessary |
| (c) own reprojection | not needed |

Without a region `exportRaster` is still dangerous in a vertical projection; in the rotated chunk it is a normal horizontal export.
`exportRaster(..., clip_to_boundary=False, save_alpha=False, white_background=True)`: no boundary shape of the user's project applies to the work chunk.

### Threading model (what the UI must do)
* `MetashapeBackend.threaded = False`: **call every method on the main thread** (the wizard already does: `tool.make_backend`). Metashape processing
  (`buildDem`, `buildOrthomosaic`, `buildDepthMaps`, `exportRaster`, `chunk.copy`) blocks the main thread and shows Metashape's own progress dialog.
* Constructor: `MetashapeBackend(chunk=None, pump=QApplication.processEvents, max_cameras=250, depth_downscale=4, keep_work_chunks=False)`.
  `pump()` is called between Metashape calls and while the detector thread runs, never inside a Metashape callback.
* `detect()`: reads the tie points on the main thread (1.4 s for 1.1 M points: the study's 8 s was the per-point `crs.project`), then runs the numpy
  detector in a plain worker thread (it never touches Metashape) while the main thread loops on `join(0.05)` + `progress` + `pump`; the Cancel
  button therefore works during detection. 23.7 s end to end on CARRO (9 facades). Reading tie points from a non-main thread also works (3 s) but is not used.
* `progress(fraction, text)` is called from Metashape callbacks (0.2 s throttle): it must only store plain state. Cancel inside a Metashape job:
  the adapter raises `KeyboardInterrupt` in the callback when `cancelled()` is true (measured: aborts the job in about 0.3 s with no dialog) and turns it into
  `Cancelled`. Never raise an ordinary exception in a Metashape callback: Metashape opens a modal "Retry processing?" dialog (measured, 147 s lost until clicked).
  Metashape's own Cancel button (confirmation "Are you sure?") also ends as KeyboardInterrupt "Aborted by user" -> `Cancelled`.
* `cleanup()` removes the work chunks (`AeroFacade_*`) and the temp files; call it when the wizard closes (unless the user asked to keep them: `keep_work_chunks`).

### Frames and conventions
* **The preview frame IS the facade frame**: local east/north metres around the middle of the chunk region at the moment the backend is created
  (`backend.enu`, a 4 x 4 chunk -> ENU matrix, frozen for the whole session). `project_info()["crs"]` = "Local east/north (m)". The polygon, the preview extent and
  `Facade.polyline_xy()` all live in it, no conversion in the UI. (ENU y is north: checked against WGS84 latitude of the two long walls.)
* `ortho_preview()`: if the chunk has an orthomosaic it is exported small and resampled onto ENU axes (`_preview_from_ortho`, written, NOT tested live: CARRO has none);
  otherwise a quick orthomosaic is built in a work chunk from the tie-point surface and at most 120 near-vertical photos, at about 8 cm per pixel: **29 s** for
  131 m x 122 m on CARRO (1600 px, shows roofs, cars, trees well). Last resort: a tie-point density picture (instant). Tell the operator "about 30 seconds".
* `detect()` returns facades whose normal points to the side the photos stand on; `_orient` double-checks with the tie-point tracks (which cameras really see
  the wall, not the mean camera side: on CARRO the flight goes all around the building, 292 cameras on one side, 289 on the other) and turns a facade round if needed.
  Measured on CARRO: A (south, 65.7 x 21.6 m) 800 k vs 12 k matches on the right side, B (north, 67.0 x 17.0 m) 705 k vs 25 k, C and D the gables 11.5 / 12.0 m, 5 small walls.
* Work chunks: `AeroFacade_<site>_<letter>`, one per facade (a chunk has one transform, so one plane). Tags in `chunk.meta`: `aerofacade.sig` (hash of plane frame, margins,
  method, ENU frame: this is how `existing_dems` finds them), `aerofacade.stage` (`dem` / `ortho`), `aerofacade.pixel`, `aerofacade.letter`, `aerofacade.source`, `aerofacade.margins`.
  Changing margins or method gives another signature, hence a new work chunk. The cameras that do not look at the facade from its viewer side are disabled in the work chunk
  (at most 250 kept, ranked by visible fraction and frontalness): this is what keeps depth maps and orthophotos short.
* Names: `<site>_<date>_facade_<A>.<ext>` in `Outputs` next to the project (site from `aeroexports.naming.guess_site`, date = flight date from EXIF `DateTimeOriginal`), plus
  `<same name>.txt` (engine's `georef_lines` + exact extent and size of the picture).

### Methods, measured on CARRO (Metashape 2.2.1, RTX 4070, 724 photos of 5280 x 3956 px, no dense cloud and no depth maps in the project)
All numbers are one run each, margins 1 m, depth 2 m, orthophoto 3 cm per pixel. A = south facade 65.7 m x 21.6 m (2255 x 786 px), C = east gable 12.0 m x 17.1 m.

| Method | What it needs | DEM step | Orthophoto step | Look |
|---|---|---|---|---|
| tie points | nothing (always there) | 13.5 s, 7.0 cm per pixel (the spacing of the tie points: not settable) | 39 s (A) | clean walls, **wavy balconies and window reveals**, holes smeared: a quick look |
| depth maps, computed for the facade (not in the project) | aligned photos | **162 s** for A with 40 photos at `downscale=4` (2.0 cm per pixel), 51 s with `downscale=8` (3.9 cm per pixel); 204 s for C (40 photos, ds 4) | 33 s (A) | crisp, balconies and windows right; ds 8 is almost as good (a little rougher balconies) |
| depth maps already in the project | the chunk's own depth maps | measured through a chunk with 140 photos at ds 8: **53 s** for A (3.9 cm per pixel) | 40 s (A) | same as above |
| dense cloud | an existing dense cloud (2.8 M points in the test chunk, built for the test) | **2.0 s** for A (4.6 cm per pixel) | 25 s (A) | the cleanest balconies and the best edges |

* Depth maps cost about 4 s per photo at ds 4 and 1 s per photo at ds 8, whatever the facade: the adapter computes them for at most `depth_max_cameras` = 40 photos spread out in space
  (farthest-point sampling among the photos that look at the facade from its viewer side), not for the 250 photos kept enabled for the orthophoto. `depth_downscale` = 4 by default.
* Photos in a work chunk: only the ones that stand on the viewer's side of the plane, look toward it and see part of it stay enabled (at most 250). This is what keeps the orthophoto at 3 to 40 s;
  without it, an orthophoto of a 12 m wide gable took minutes. Do not rely on `camera.project` alone: it also answers for points BEHIND the camera.
* `buildDem(source_data=DepthMapsData)` is accepted by Metashape 2.2.1 although the reference says "point cloud or tie points" only. The DEM crop in depth is the work chunk's region (heights -2.00 .. +2.00 m for a 2 m depth).
* The dense cloud is copied into each work chunk with `chunk.copy(items=[..., PointCloudData])`: 2.8 M points were no problem (1.6 s); a 100 M point cloud was NOT tried (memory and copy time unknown).
* Failure modes met: a Python exception inside a Metashape callback opens a modal "Retry processing?" dialog; `chunk.copy` makes the copy the ACTIVE chunk (the adapter puts the user's chunk back);
  a wall normal that points away from the photos gives a picture made from the wrong side (see `_orient`).

### DXF with the picture
`export(..., options={"dxf": True, "dxf_epsg": 3942})` writes `<picture>.dxf` next to the picture: one IMAGE entity standing in the facade plane (insertion point = bottom-left corner of the picture,
pixel vectors along the wall and up), image path relative (same folder). `aeroexports/dxfout.py` got optional `insert_z` / `u_vec` / `v_vec` keys (flat images are written exactly as before).
Coordinates: `dxf_epsg` if given, else the chunk's own coordinate system when it is projected, else WGS 84 / UTM of the site. **Checked in AutoCAD 2023 (accoreconsole): the image reloads
("loaded and relinked"), `LIST` shows the insertion at Z 61.84 and the top at Z 87.31 (25.47 m = 849 px x 3 cm), width 67.43 m (= 2248 px x 3 cm), extrusion direction horizontal and pointing to the viewer.**

### Live run of the whole wizard in Metashape (CARRO, 2026-11-06, `tool.install()` from the console daemon, real mouse clicks)
Preview 30 s (work chunk built then removed) -> polygon drawn around the building (1828 m2) -> detection **11 s**, 4 facades (A south 58.7 x 22.8 m, B north 66.8 x 17.0 m, C east gable, D west gable;
A is shorter than the real 66 m because the polygon cut its east end: only data inside the polygon is used) -> C and D removed -> margins 1 m / depth 2 m / 3 cm -> method: depth maps (preselected) ->
**surfaces: A 120 s, B 98 s** (40 photos each, `downscale` 4) -> **orthophotos: A 31 s, B 26 s** -> export PNG + georeferencing text + DXF in 1.5 s -> `Outputs` next to the project.
About **4.5 minutes of computing** for two facades. Pictures: `facade_shots/31_*` to `38_*` (wizard screens) and `40_*`, `41_*` (outputs). B matches the reference `Face_nord.jpg`, A the reference `facade_sud.jpg`
(`27_compare_*`). The UI stayed alive (Metashape's own progress dialog shows on top, the wizard banner says "do not touch Metashape").
Thumbnails: `exportRaster(width=, height=)` does NOT scale, it cuts a window of that many native pixels (the first wizard thumbnails showed only the top-left corner): the adapter uses `resolution=` instead.
Reloading code in the console daemon: `from package import module` returns the stale module object after `del sys.modules[...]` unless the package itself is purged too (this wasted one test).

### Not verified
`_preview_from_ortho` was checked on a chunk whose orthomosaic is in EPSG:3942 (aligned with the built preview), not on a chunk with a projected CRS that is a compound one or on a very large ortho.
A dense cloud of tens of millions of points in `chunk.copy`; a chunk without georeference (`enu_matrix` falls back to the chunk axes, untested live); Cancel through the wizard's button during a Metashape job
(the KeyboardInterrupt path is measured with a callback, not through the Qt button); a project saved with work chunks inside (`keep_work_chunks`).
