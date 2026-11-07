# Aero-Facade: product specification (v1, written 2026-11-06 from the product owner's brief)

Tool id `aerofacade`, package `aerotools/tools/aerofacade/`, menu entry **Aero > Aero-Facade**. UI texts in
`lang/en.py`. Audience: novice operators. Read first: `docs/AERO_FACADE_STUDY.md` (what the Metashape 2.2.1 API can and
cannot do, measured), `aerotools/tools/aerofacade/detect.py` (wall detector prototype), `docs/facade_console_build.py` (planar DEM + ortho
from tie points, verified in pieces), `aerotools/tools/aeroexports/` (window, theme, controls, config, journal patterns to reuse).

## What the tool does

Produces one ortho-facade picture per detected wall of a building, from a Metashape chunk (photogrammetry of a building). The user
never touches the Metashape GUI: one window, a wizard in 7 screens (since 2026-11-07; it had 6, the last one was split).

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
- **Handles (since 2026-11-07).** The walls are drawn thin and semi-transparent (the selected one a little stronger) so the tie point density picture under them stays
  readable, with a round **handle at each end** of every wall. Dragging a handle moves that end ALONG the wall's own line only (the line, the orientation and the height never
  change): the wall is lengthened or shortened, and its length shows live next to it while the mouse is down. Minimum length 1 m (`geometry.MIN_WALL_LENGTH`), maximum 500 m, and the
  end stays inside the picture; Esc during a drag puts the wall back. Handles keep the same size on the screen at any zoom, are grabbed within 15 px (`HANDLE_HIT_PX`), turn white
  with a resize cursor when hovered, and come before the left-button pan (right / middle drag, wheel zoom and everything else are unchanged). A click on a handle selects its wall.
- The edited extent is the wall's extent from then on (`Facade.set_extent`; the detected one is kept in `Facade.detected`, `Facade.edited`): the list shows the new length
  ("edited"), `state.set_wall_extent` calls `invalidate_builds` (surfaces and pictures built so far are out of date), margins, picture size, bounding regions, the work chunk
  signature (`msfacade.signature`: origin and length), the georeferencing text and the drawing sheet all read the facade, so they follow. **Reset this wall** (list button) brings the
  detected extent back; running the detection again replaces the walls, and so the edits. Pure geometry: `geometry.axis_position` and `geometry.moved_end`.

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
- If DEMs for the chosen facades/method **already exist in the chunk** (we tag the assets we create), skip straight to screen 6 (orthophotos).
- Otherwise: a list of what will be computed, per facade; one button **Build**; then the batch runs **sequentially**, with a clear
  "Please do not touch Metashape, please wait" banner and a progress list (waiting / running / done / failed per facade) and a Cancel.
  It is fine that Metashape's own progress dialog appears. To keep it fast, every computation is restricted to a **generated bounding
  region around the facade** (plane + margins + depth), never the whole chunk.
- End: a success message with two buttons: **Exit** and **Continue to build the orthophotos**. Failures are listed per facade with
  a plain-English reason and a Retry.

### Screen 5 — Build: which walls (2026-11-07)
- Every wall of the list has a **check box** on its left (outside its row), all ticked at first. Only the ticked walls are built (`state.build_letters()`; the choice is
  `WizardState.build_choice`, `None` = all). The Build button reads "Build n of m wall(s)" when some walls are not ticked, a note says how many are left out, an unticked wall's
  row says "Not ticked", and with no wall ticked Build is off with a note. The boxes are locked while something is built; a retry and a second Build only do the ticked walls that
  are not built yet. `can_next(BUILD)` needs one built ticked wall.
- The next screens only see the ticked walls that are built (`build_ok_letters()`, and `ortho_ok_letters()` / `chosen_letters()` which respect the choice): the orthophotos are built for
  them, screen 6 lists them with a short note naming the walls left out, screen 7 exports from them. The "surfaces already exist" shortcut (screen 5 skipped) looks at the ticked walls
  (all of them by default). New walls, or other margins / method (`invalidate_builds`), tick everything again.

### Screen 6 — Orthophotos
- The orthophotos are built one wall after the other (a row per wall, as on screen 5), then a small picture of each is listed with a checkbox: tick the pictures to
  export. The ticks are kept in the shared `WizardState` (`picture_choice`, a set of letters; `None` = every built picture; `chosen_letters()`): screen 7 exports exactly
  these, and Back from screen 7 shows the same ticks. Rebuilding the same walls keeps the ticks; other walls or other margins (`invalidate_builds`) tick everything again.
- Each picture is a compact **card** (small picture, "Wall A", size; about 70 px high, so six fit one above the other in the usual window without a scroll bar), with its **check box
  on the left, outside the card**, so the box keeps its own background and stays visible when the card is hovered, ticked or not (a click on the card ticks the box too). A ticked
  card has a green border, an unticked one is dimmed.
- A normal **Next** button, enabled when at least one picture is ticked. Nothing about the export is on this screen, so it has no scroll bar.

### Screen 7 — Export settings (last step)
- Folder (default: `Outputs` next to the project; created), picture format (PNG / JPEG / TIFF / JPEG 2000 / ECW; a format whose program is missing is greyed with its reason),
  extra files (georeferencing text with origin, pixel size and plane frame; optional 3D DXF of each picture), the **Drawing sheet** group (see below), the example file names, the
  **Export** button with its progress box (Cancel), and the final success card (**Open folder**, **Change the settings**, **Close**).
- Two columns (folder, format, extra files | drawing sheet) and a bottom bar (names, Export), so it fits the usual 1280 x 800 window without a scroll bar, also at 125 % and 150 %
  text size and on a 1366 x 768 screen (checked by the Qt harness; the page is inside a scroll area that only shows a bar when the window is smaller than the content, e.g. at 200 %
  or with the missing-grid banner on the smallest screens). Back goes to screen 6.
- **Export** writes one file per facade named `<site>_<date>_facade_<A>.<ext>`.

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
    def height_context(self) -> dict           # {"epsg": int | None, "lonlat": (lon, lat) | None, "crs_name": str}: where the sheet's altitudes can be read (see Drawing sheet)
    def export(self, facades, folder, fmt, options, progress, cancelled) -> list[(path, ok, message)]   # fmt: png | jpg | tif | jp2 | ecw; options: see Drawing sheet
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
`screens_build.py` (5), `screens_export.py` (6 and 7), `widgets.py` (OrthoCanvas, FacadeList, BatchProgress, OrthoThumbnails), `widgets_margins.py` (NumberField, LinkBadge,
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

### Live check of the merged code (CARRO, 2026-11-06, real mouse in Metashape 2.2.1, pictures `facade_shots/50_live_en_*`)
Whole wizard again on the current code: polygon (wheel zoom, four clicks, double click), detection 11 s (A to D), right-click menu Remove / Undo, margins with the link badges (a comma is accepted as the decimal mark),
depth maps (A 113 s, B 98 s, C 56 s in the first run), then tie points (about 10 s per wall) in a second run with other margins and a 5 cm pixel, orthophotos (31 s / 27 s / 8 s at 3 cm), export PNG and JPEG with
their georeferencing text and DXF. The exported A and B match the references `facade_sud.jpg` and `Face_nord.jpg` (`50_live_en_20_*`, `21_*`). Re-running the wizard for the same walls, margins and method skips screen 5 and
reuses the orthophotos; other margins or another method build new surfaces.
Found and fixed during this run:
* the wheel zoom of the picture anchored on the corner of the picture when the mouse had not moved inside the window first (Qt's "under the mouse" anchor); the anchor is now computed by hand;
* after a long Metashape call (detection, build) Metashape's main window could end up above the wizard, which then looked gone: the wizard raises itself when a job ends (only when Metashape is the active application);
* Cancel in Metashape's own progress window ended the wall as "failed, no reason was given" and the batch went on with the next wall: it now cancels the batch, like the wizard's own Cancel button
  (which is out of reach while Metashape's modal progress window is open);
* the small pictures of the export screen kept the previous build when the same walls were rebuilt;
* the export format hints showed raw keys (`format_png_text`): the keys are now taken from the `FORMAT_KEYS` table, and a test checks every key the package asks for;
* the walls screen now frames the building (not the whole picture) and counts "3 of 4 walls kept" after a removal; the method screen counts only the surfaces still to build (no long-wait notice when they all exist).

### Not verified
`_preview_from_ortho` was checked on a chunk whose orthomosaic is in EPSG:3942 (aligned with the built preview), not on a chunk with a projected CRS that is a compound one or on a very large ortho.
A dense cloud of tens of millions of points in `chunk.copy`; a chunk without georeference (`enu_matrix` falls back to the chunk axes, untested live); Cancel through the wizard's own button during a Metashape job
(Metashape's modal progress window blocks it; the path was checked through the Cancel button of that window); a project saved with work chunks inside (`keep_work_chunks`).


## Drawing sheet (DXF / DWG with every picture), JPEG 2000 and ECW

Added on top of the export (screen 7). Pure logic: `sheet.py` (naming, order, layout, levels, entities; no Metashape, no Qt, no files), `sheet_labels.py`
(the few words written INTO the drawing, French and English, plain ASCII), `sheetout.py` (options, heights, pictures, files, and `run_export`, the whole
export sequence shared by both backends), `widgets_heights.py` (the height system picker). Tests: `tests/test_aerofacade_sheet.py`, the sheet cases of
`tests/test_aerofacade_msfacade.py`, and the `export sheet:` cases of `tests_qt/run_qt_aerofacade.py`.

### What the user gets
* New formats next to PNG / JPEG / TIFF: **JPEG 2000** (GDAL from QGIS, or Global Mapper) and **ECW** (Global Mapper only), converted from a GeoTIFF with the
  Aero-Exports tools (`external.to_jp2_gdal`, `to_jp2_gm`, `to_ecw`, found by `external.find_*`, saved paths of the Aero-Exports settings honoured). A format whose
  program is missing is greyed on the screen with its reason, and refused by the backend with the same reason (never skipped silently).
* A group **Drawing sheet**: one `<Site>_<date>_facades.dxf` (and `.dwg`, made by AutoCAD's `accoreconsole`; the DXF is kept) holding every ticked picture.
  The pictures are referenced by RELATIVE paths in `00_Assets\ortho\<Site>_<date>_facade_<L>.<ext>` (the `aeroexports.dxfout` convention). The loose
  pictures in the folder stay (option "Also save each picture on its own", on by default). The per-facade 3D DXF is a separate option, unchanged.
* After the export, AutoCAD (when found) reloads every image of the DXF (`external.check_dxf`): a picture it cannot load is listed on the last screen.

### The sheet
* Drawing units = metres, scale 1:1 (`$INSUNITS` 6). ONE row, left to right, the facades in the order of the compass bearing of the direction they face, in steps of
  5 degrees from the start of the north sector (N, NE, E, SE, S, SW, W, NW; walls facing the same way within 5 degrees keep the order of their letters). The first
  picture's left edge is at X = 0. A constant gap: `max(2 m, 5 % of the mean picture width)`. Pictures are as in the wizard (seen from outside, left to right).
* **Y is the height**: each picture stands with its bottom edge at its true height, at 1:1. Georeferencing in X/Y is not kept (the sheet is a plane drawing).
  Leaning walls are drawn upright (the picture is not squashed).
* Titles under each picture, `FACADE NORD` / `NORTH FACADE` ... with the cardinal point of the outward normal (`sheet.compass`, 45 degree sectors); two walls with the
  same name both get their letter (`FACADE SUD (A)`); a smaller line gives azimuth, size in metres and pixel size (two lines when the picture is narrow). Language:
  `options["sheet"]["titles"]` = `"fr"` (default) or `"en"`. The words are in `sheet_labels.py`.
* Dressing: thin level lines at round values (step 0.5 / 1 / 2 / 5 / 10 ... m chosen so that there are at most 20 lines), labelled at both ends (`+3.00` in relative
  mode, `NGF-IGN78 123.45` in altitude mode: the datum's short name), a graduated axis on the left, ticks on both edges of each frame, a thin frame around each picture, a
  note under the sheet (`Relative heights, zero = lowest point of the selected facades`, or `Altimetry: NGF-IGN78 / RAC09, geoid grid fr_ign_RAC09.tif (horizontal frame
  EPSG:32632)`). The level lines are cut around the pictures and the titles: they never run through a picture. Layers: `FACADE_LEVELS` (lines, ticks, axis and labels:
  freeze it to hide the dressing), `FACADE_FRAMES`, `FACADE_TITLES` (titles and notes), one image layer `FACADE_<L>` per picture (these names are English on purpose).
  Font: SHX `simplex` (the default `txt` draws the capital O as a square). The DWG is saved after a `ZOOM Extents`, so it opens on the whole sheet.

### Heights
* **Relative** (default): Y = height above the lowest bottom edge of the ticked pictures (common zero, relative heights between facades kept). The height of a
  picture's bottom edge is read in the local ENU frame of the backend (no coordinate system needed).
* **Altitudes** (`z`: true): Y = `H = h - N`. `h` is the height of the bottom edge in the chunk's horizontal output CRS, which is ELLIPSOIDAL (measured live, below);
  `N` is the undulation of the chosen vertical datum at the middle of the facade, one value per facade: read from the `Geoid` folder with PROJ as the authority and our own
  reading of the grid as a check (they must agree to 10 mm, the rule of `vertical.correct_dem`), or no `N` for `ellipsoid`. The systems offered are
  `vertical.compatible_verticals(epsg, lon, lat)`; the picker has the same states as the Aero-Exports window (missing / damaged grid: orange banner, verified download,
  Export held until it is there). A missing grid, a grid that does not cover the place, no PROJ, no coordinate system: a plain `SheetError` message, and NOTHING of the
  sheet is written (the loose pictures still are). The horizontal CRS is the chunk's own when it is projected with an EPSG code, else WGS 84 / UTM of the site (a geographic
  chunk, as CARRO's); a local (engineering) chunk has no altitudes.

### Backend protocol additions
```python
def height_context(self) -> dict    # {"epsg": int | None, "lonlat": (lon, lat) | None, "crs_name": str}; default: nothing known (relative heights only)
def export(self, facades, folder, fmt, options, progress, cancelled)
    # fmt: 'png' | 'jpg' | 'tif' | 'jp2' | 'ecw'
    # options: {'georef_txt': bool, 'dxf': bool (3D DXF of each picture), 'pictures': bool (default True; False only counts with a sheet),
    #           'tools': {'gm','acad','gdal'} (default: detected only when needed),
    #           'sheet': {'dxf': bool, 'dwg': bool, 'z': bool, 'vertical': id | None, 'titles': 'fr' | 'en'}}   # dwg implies dxf
    # returns [(path, ok, message)]; the sheet's .dxf / .dwg are in it; a problem with the sheet is an entry (path, False, reason)
```
Both backends call `sheetout.run_export(...)` with their `sheetout.Hooks` (`info`, `write_native`, `write_tif`, `sidecars`, `message`, `epsg`): `MetashapeBackend` (`msfacade._ExportHooks`,
`bottom_edge`, `height_context`) and `FakeBackend` (`fake_backend._FakeHooks`, a synthetic site in Ajaccio, RGF93 v1 / CC42, `epsg=None` for a project without CRS). Preferences saved
in the `aerofacade` section: `fmt`, `sheet`, `sheet_fmt`, `sheet_pictures`, `sheet_titles`, `sheet_z`, `sheet_vertical` (besides the existing ones).

### JPEG 2000 / ECW pictures
Metashape writes a GeoTIFF of the picture in its plane frame; the loose JPEG 2000 / ECW is converted from it as it is. The picture of the sheet is converted from a COPY whose
tiepoint (`geotiff.set_origin`, in place on the copy only) puts its top-left corner at its place of the sheet (X, Y = height): the file says where it sits on the drawing.
AutoCAD's IMAGE entity does not read the georeferencing, it uses the entity's own insertion point and pixel vectors.

### Checked
* Offline, with real tools on this PC (AutoCAD 2023 `accoreconsole`, QGIS 4.0.3 GDAL, Global Mapper 26): DXF reloads with all images (`check_dxf`), DWG written, plotted to PDF through
  `-PLOT` and rendered with Ghostscript; PNG, JPEG 2000 and ECW pictures all load and plot. Synthetic facades (the fake backend's), titles readable, levels cut around pictures,
  not mirrored.
* Live in Metashape 2.2.1 on CARRO (chunk CRS WGS 84, EPSG:4326, no vertical): `crs.project` of a 2D projected EPSG (32632, 3942) returns the SAME height as the geographic WGS 84
  height (64.2025 m in all four cases at a tie point): ellipsoidal. Altitudes of the ground at the five surveyed points of `CARRO.csv` (CC42, NGF) from the tie points: `h - N`
  with RAC09 differs from the survey by -5.7 cm on average (std 4.6 cm, worst -13.8 cm), with RAC23 by -3.6 cm (std 4.6 cm, worst -11.7 cm).
* Not verified: ECW / JPEG 2000 sheets from Metashape live (only offline with the fake backend), a chunk with a projected compound CRS live, a very large picture, DWG opened in
  AutoCAD's interface (only `accoreconsole`).
