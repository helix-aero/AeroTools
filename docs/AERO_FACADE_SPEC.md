# Aero-Facade: product specification (v1, written 2026-11-06 from François's brief)

Tool id `aerofacade`, package `aerotools/tools/aerofacade/`, menu entry **Aero > Aero-Facade**. UI texts English first, French in
`lang/fr.py` (same keys). Audience: novice operators. Read first: `docs/AERO_FACADE_STUDY.md` (what the Metashape 2.2.1 API can and
cannot do, measured), `aerotools/tools/aerofacade/detect.py` (wall detector prototype), `docs/facade_console_build.py` (planar DEM + ortho
from tie points, verified in pieces), `aerotools/tools/aeroexports/` (window, theme, controls, config, journal patterns to reuse).

## What the tool does

Produces one ortho-façade picture per detected wall of a building, from a Metashape chunk (photogrammetry of a building). The user
never touches the Metashape GUI: one window, a wizard in 6 screens.

### Screen 1 — Search area
- Left/centre: a picture of the chunk's orthomosaic (our own Qt widget, from a small low-resolution export of the orthomosaic;
  pan + zoom with the wheel). Right: short instructions and the **Run façade detection** button, disabled at first.
- A polygon tool: the operator clicks points on the picture to draw the **search area** (a polygon; double click or click on the first
  point closes it; Esc cancels; the polygon can be redrawn; Delete clears). When a valid polygon exists, **Run** becomes enabled.
- The point is to limit the computation: only data inside this polygon is used (all heights).

### Screen 2 — Detected façades
- **Run** starts the detection in the background (the window stays alive, a progress indicator and a Cancel button show). The detector
  works on the tie points inside the polygon (fast, always available); see `detect.py`.
- Each detected façade is drawn on the ortho picture as a polyline in its own colour with its name **A, B, C, D…** (letters, then AA,
  AB… if more than 26). Hover / selection highlights it.
- Right panel: a **list** of the façades: colour swatch, name, **length (m)** and **height (m)** (and orientation as small grey text).
  Click selects, Shift-click and Ctrl-click multi-select, **right click > Remove** (and the Delete key) removes the selected ones from the
  list (and from the picture), **Undo** brings them back. Removed façades are not processed.
- A façade can be selected in the list to zoom on it on the picture.

### Screen 3 — Margins (the area around each plane)
For the selected façades (or all, with an **Apply to all** switch): how much to take around the detected plane, in metres:
- **Left** and **Right** with a small **link badge** between them (linked = changing one changes the other; click the badge to unlink);
- **Top** and **Bottom** (also with a link badge between them, linked by default);
- **Depth** (kept in front of / behind the wall plane, default 2.0 m; hint: more depth keeps balconies, less avoids trees).
- Default margins: 1.0 m left/right/top/bottom. A small schematic drawing of a façade with the margin area shaded shows the effect, live.
- Also the **pixel size** of the final orthophoto (default 3 cm; hint that smaller pixels are heavier).

### Screen 4 — Method for the DEM under each façade ("MNT" in the plane)
Radio choices, each with a one-line explanation. Availability is detected in the chunk; **an unavailable method is shown greyed**
with the reason ("no dense cloud in this chunk"). The best available one is preselected. Ranking by quality (best first; decided in
the spec, with the reasons shown to the operator as small grey text):
1. **Dense cloud** — the most complete and cleanest surface (filtered, dense). Best quality, and the fastest to use when it already
   exists. Needs the chunk to have a dense cloud; if it must be built it takes long (shown as a warning, not offered when absent).
2. **Depth maps** — very good detail, a good compromise: quality close to the dense cloud, available in most projects, and quick
   because only the area of the façade is processed. Default for most projects when there is no dense cloud.
3. **Tie points** — always available but sparse: poor result (holes, rough surface). Offered with the warning "good enough for a quick
   look, but expect a poor result".
Each option carries a time hint ("about N min for K façades": estimate from the area; honest "unknown" if not estimable). If the
selected method will take a long time, a gentle notice says so.
(The detection of screen 2 always uses the tie points. This screen only chooses how the surface is built for the ortho.)

### Screen 5 — Batch build of the DEMs
- If DEMs for the chosen façades/method **already exist in the chunk** (we tag the assets we create), skip straight to screen 6.
- Otherwise: a list of what will be computed, per façade; one button **Build**; then the batch runs **sequentially**, with a clear
  "Please do not touch Metashape, please wait" banner and a progress list (waiting / running / done / failed per façade) and a Cancel.
  It is fine that Metashape's own progress dialog appears. To keep it fast, every computation is restricted to a **generated bounding
  region around the façade** (plane + margins + depth), never the whole chunk.
- End: a success message with two buttons: **Exit** and **Continue to build the orthophotos**. Failures are listed per façade with
  a plain-English reason and a Retry.

### Screen 6 — Orthophotos and export
- Preview of each façade's orthophoto (small thumbnail) in a list; checkboxes to pick which to export; output folder (default:
  `Outputs` next to the project, like Aero-Exports; the folder is created); format choices (PNG / JPEG / TIFF + a georeferencing
  text file: origin, pixel size, plane frame, so the picture can be placed and measured; optional DXF with the picture inserted).
- **Export** writes one file per façade named `<site>_<date>_facade_<A>.<ext>`; a final success screen with **Open folder**.

## Architecture (three layers, so three people can work in parallel)

1. `model.py`, `geometry.py`, `methods.py`, `detect.py` — **pure Python (numpy at most), no Qt, no Metashape**: dataclasses
   (`Facade` with letter, colour, plane frame, length, height, `Margins`, `Method`), polygon-in-area filtering, naming, margin /
   plane-frame / bounding-region maths, method ranking and availability rules, time estimates. Fully unit-tested.
2. `msfacade.py` — the **Metashape adapter** (the only module that imports `Metashape`; same style as `aerotools/msapi.py`):
   reads data availability, ortho preview export, tie points inside a polygon, builds the per-façade DEM (three methods), builds the
   orthophoto in the façade plane, and **gets the picture out as a file** (the study found that `exportRaster` writes nothing for a
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
- Never push, merge or open PRs (François decides later). Local commits on branch `aerofacade-ui` of `C:\Users\LTB1\AeroTools_test`,
  small commits, English titles, ending with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`; commit only your own files
  (`git add <paths>`; if git reports an index lock, wait a few seconds and retry).
- François's live Metashape imports this tree: write each file whole and atomically (Write tool, never Bash heredocs with
  backslashes), keep the tree importable (`python -m compileall aerotools`) and the suite green before every commit. The package
  `aerofacade` is only imported when the tool starts; the shared files (`config.py`, menu registration, `lang/en.py`, `lang/fr.py`,
  `manifest.json`) are touched in small, careful edits.
- Tests: `C:\Users\LTB1\AppData\Local\Temp\claude\C--Users-LTB1-Desktop\0e8331c3-9b57-48b0-82bc-f4616d429b51\scratchpad\venv\Scripts\python.exe -m pytest tests -q`
  (829 pass now); after adding files run `python install/make_manifest.py`. Qt harness pattern: `tests_qt/run_qt_aeroexports.py` +
  `tests_qt/_isolation.py` (never touch the real settings; PySide6 offscreen for tests, PySide2 in Metashape; Python 3.9 compatible code).
- UI: understandable by a 10-year-old, safe for novices, nothing clipped at any font scale (use `aeroexports/controls.py` helpers),
  English + French texts, the Aero theme (`aerotools/theme.py`, `aerotools/ui.py`).
- **Only the "Metashape adapter" agent drives Metashape's GUI** (computer-use). The test project is `carro_test_facade.psx` (CARRO,
  724 aligned photos, ~1.1 M tie points, one big white building in the middle), already open in François's Metashape: it is a
  throw-away test project, you may modify it, but never close Metashape. Work on chunk copies where it matters. Console technique:
  click the Metashape Python console input (bottom right), `exec(open(script).read())`, scripts write results to files. Never run
  `metashape.exe -r`. If a click is refused ("user interrupt"), wait 20 s and retry once.
- Keep `C:\Users\LTB1\AppData\Local\Temp\claude\C--Users-LTB1-Desktop\0e8331c3-9b57-48b0-82bc-f4616d429b51\scratchpad\facade_shots\`
  for screenshots (PNG) that François will see: name them `NN_what.png`; the UI agent produces offscreen renders of every screen at
  several font scales; the adapter agent keeps live screenshots of Metashape results.
- Final reports in French, concise, measured vs not verified.
