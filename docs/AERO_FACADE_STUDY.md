# Aero-Facade: feasibility study

Status: study, 6 November 2026. Branch `aerofacade` (local, not pushed). Nothing here is wired into the Aero menu.

Idea (François): the user draws a polygon on the orthomosaic, the tool finds the walls inside it from the **tie points only**, lists them
with a small front view, and for the façades picked it builds the **DEM in the plane of the façade** (cropped, with a buffer), shows it,
then builds the **orthomosaic of the façade**.

## 1. Verdict

**GO, with changes.** Detection works and is fast; the Metashape side is much better than feared (a DEM *can* be built from the sparse
cloud, in an arbitrary plane, in 2 seconds). Two points stay open and must be checked in 5 minutes in a console before any UI work
(section 8): the orthomosaic in the façade plane through the Python API, and the depth crop of the DEM.

Changes to the original idea:

1. Do not rely on "DEM then ortho" alone: also offer the **flat plane** as the ortho surface (what façade orthos usually use), and keep
   the tie-point DEM as an option/preview (it shows protrusions, and it is the checking step the user asked for).
2. Work in a **work chunk** (a copy made with `chunk.copy`, 1.4 s without the dense cloud) so that `chunk.region`, the DEM and the ortho
   assets of the user's chunk are never touched; export files, then delete the copy.
3. Add a manual way to define a façade (two markers or two clicks) next to the automatic list: detection will always miss some walls
   (sparse or leaning) and the user must never be stuck.

## 2. What was measured, and on what

| Data | What it is | Used for |
|---|---|---|
| Synthetic scenes (`tests/facade_synth.py`) | 100 m to 300 m scenes: sloping terrain, 5 to 14 buildings (4 faces, flat or gable roof, 4.5 to 11 m), a 5 m curved wall (R 18 m), a 1.2 m fence, 6 to 60 trees (3 points/m3), outliers; scene rotated in plan and moved to projected-like coordinates; ground truth for every wall | precision / recall / errors, density and noise limits, runtime |
| **Real tie points of CITADELLE_Pompeani**, chunks Janvier_2026 (571 447 pts) and Mai_2026_29 (448 241 pts), read through the live Metashape console (read only) | real sparse clouds, no ground truth (judged on plan-view overlays and consistency) | real behaviour, real tie point density on façades, thinning |
| 3 dense photogrammetric clouds of buildings (`Robert/DRONE.laz`, `BIM/Bertin/BERTIN_top_CC42.laz`, `Forcioli/CARRO.laz`), randomly thinned to 600 k then 200 k ... 700 points | stand-in for tie points of different density; the reference is the detection on the 600 k cloud | how many points are enough |

No standalone `Metashape` Python module exists on this PC (and none was installed); everything Metashape-side was done in the open session
on a **copy** of the May chunk (`ZZ_FACADE_TEST_COPY`, still in the session, never saved: the project title shows `*`, do not save it).

## 3. A. Detection

Prototype: `aerotools/tools/facade/detect.py` (numpy only, ~490 lines) + `tests/facade_synth.py` + `tests/test_facade_detect.py` (5 tests,
4 s). Python 3.9 syntax checked with Metashape's own Python; numpy 2.0.2 imports in the live Metashape console (checked), scipy is not needed.

Principle: a wall is a thin vertical slab of points. A point is *elevated* if it stands > 0.4 m above the lowest point of its 3 m cell
(kills the terrain). Three detectors (and their combination) share one validation step (height >= 2 m, length >= 3 m, >= 25 points, band contrast >= 2.5 against the
flanking bands, no height/along-wall slope, >= 20 occupied 1 m x 1 m cells of the (along-wall, height) image: a wall fills a rectangle, a roof or a
ground strip one row; all are `Params` fields):

* `grid_hough`: plan-view grid (1 m) -> cells with a vertical extent >= 2 m -> Hough transform on the cell centres -> point-level refit.
* `ransac_line`: sequential RANSAC of vertical planes (lines in plan view) on elevated points.
* `ransac_plane`: RANSAC of near-vertical 3D planes (tilt <= 12 degrees), for leaning walls.
* `combined`: `grid_hough` first, then `ransac_plane` on the points left, second-pass walls kept only if stricter
  (contrast >= 4.5, rms <= 0.6 x band). **Recommendation: `grid_hough` always (0.1 to 5 s), the second pass only on request** (~30 s on a real cloud).

### Results on synthetic scenes (3 seeds, ~90 000 points, 4 cm noise, band 15 cm, unless said)

recall / precision are by wall length; "pos" = mean distance of the detected trace to the true wall (cm); "false" = detected walls that
match no true wall (trees, roofs, ground); the angle error is about 1 degree everywhere (0.4 to 1.4).

| scene | detector | recall | precision | pos cm | false walls | seconds |
|---|---|---|---|---|---|---|
| baseline (2 pts/m2 of façade, faces seen at 30-100 %) | grid_hough | 0.95 | 0.91 | 4.9 | 0.0 | 0.1 |
| | ransac_line | 0.82 | 0.81 | 4.5 | 1.0 | 2.0 |
| | ransac_plane | 0.79 | 0.89 | 4.2 | 0.0 | 1.2 |
| | combined | 0.95 | 0.91 | 4.9 | 0.0 | 1.3 |

Façade density needed (tie points per m2 of façade, recall by length):

| density | 0.1 | 0.25 | 0.5 | 1 | 5 |
|---|---|---|---|---|---|
| grid_hough | 0.03 | 0.31 | 0.81 | 0.98 | 1.00 |
| combined | 0.03 | 0.31 | 0.86 | 0.98 | 1.00 |
| ransac_line | 0.10 | 0.27 | 0.63 | 0.77 | 0.99 |
| ransac_plane | 0.03 | 0.08 | 0.55 | 0.74 | 0.92 |

Precision stays 0.9 to 1.0 at every density: the detector misses sparse walls, it does not invent them.

-> **at least 0.5 tie point per m2 of façade, 1 to be comfortable.** On the real clouds the detected façades hold a median 16 (January) and
5 (May) tie points/m2 (min 1.3 to 1.7, max 50 to 80), above the limit with margin. A lower-quality alignment would come closer to it (not measured).

Noise (density 2, 15 cm band): `grid_hough` recall 0.98 to 0.99 from 2 cm to 15 cm of noise, position error 5 to 9 cm; `ransac_line` falls to 0.77
at 15 cm noise unless the band is widened (0.84 with a 38 cm band). -> band as a setting, default 15 cm.

Leaning walls (one building, 8 m high; recall of its faces):

| tilt | grid_hough | ransac_plane | combined |
|---|---|---|---|
| 3 deg | 1.00 | 0.86 | 1.00 |
| 6 deg | **0.00** | 0.86 | **0.86** |
| 10 deg | **0.00** | 0.55 | **0.95** |

-> the plumb detector tolerates ~3 degrees; the second pass is needed above that and measures the tilt (`Wall.tilt`). The real lean of the
Citadelle façades was not measured; the choice between a vertical and a best-fit ortho plane is decision 4.

Curved wall (R 18 m, 5 m high, cut into 12 chords of 4.2 m): recall 0.80 at 2 points/m2, 0.93 at 5, **0.00 at 1** (the chords fall below the
minimum area). It comes out as a succession of short straight pieces: usable for a rotunda only if the user picks all pieces (Metashape
has a cylindrical projection, out of scope).

Vegetation, roofs, fence: false walls with the default thresholds (area >= 20 cells, length >= 3 m, contrast >= 2.5): 0 to 1 per 100 m scene,
never roofs of any slope, never the 1.2 m fence; dense foliage is the only thing that fools it. Contrast 4 or 6 would lose recall
(0.88 -> 0.66 -> 0.40 at 1 pt/m2) for nothing: keep 2.5. The first prototype thresholds (area >= 6, length >= 2 m) gave 2 to 4 false walls
per scene on the synthetic data and 35 to 112 "walls" on the real tie points, mostly short pieces in vegetation: **the area threshold
matters most on real data**. Sort the list by confidence (area x contrast) anyway.

Runtime and size (numpy, one core, this PC; `grid_hough` is the detector, `combined` adds the leaning-wall pass):

| cloud | points | grid_hough | combined | recall / precision |
|---|---|---|---|---|
| synthetic 100 m | 109 k | 0.1 s | 0.9 s | 0.99 / 0.94 |
| synthetic 200 m | 503 k | 0.7 s | 2.2 s | 0.84 / 0.97 |
| synthetic 300 m | 1.8 M | 1.8 s | 2.7 s | 0.87 / 0.95 |
| synthetic 300 m | 5 M | 3.6 s | 4.5 s | 0.97 / 0.98 |
| real, Janvier tie points (core 117 m x 107 m) | 556 k | 5.6 s | 39.8 s | no ground truth |
| real, Mai tie points (core 140 m x 135 m) | 436 k | 4.3 s | 33.7 s | no ground truth |

The second pass costs ~30 s on a real 500 k cloud (RANSAC on clutter): run it only when the user asks for leaning walls, or on the residual only.
Above 600 k points `detect()` thins the cloud by voxels (one point per 10 cm voxel, growing): dense areas lose points, a sparse wall does not (a
random thinning kills the walls: tested, recall 0.00 on the 1.8 M scene). The Hough runs on cell centres, so time hardly depends on the number
of points. **Polygon limit: none in practice** (a 150 m x 150 m polygon of tie points is a few seconds); reading the points from Metashape is the
real cost (section 4).

### Results on real data

Real tie points (no ground truth; plan-view overlays in the scratch folder, `tie_*_grid_hough.png`): January 13 façades (6 to 56 m long,
2.9 to 16.7 m high), May 18 (5 to 55 m, 2.9 to 16.9 m); they follow the building outlines and the long retaining/terrace walls on the overlay
and the main ones are stable from run to run. Thinning the real tie points at random and comparing with the full-density detection:
January keeps recall 0.71 to 0.85 and precision 0.64 to 0.86 down to 28 000 points; May recall 0.88 / 0.68 / 0.62 / 0.48 and precision 0.69 / 0.69 /
0.90 / 0.73 at 50 / 20 / 10 / 5 % of the points. The main façades survive, the marginal ones come and go.

Dense photogrammetric clouds (30 m x 26 m house, 26 m x 18 m building, 73 m x 22 m building) thinned to tie-point densities, `grid_hough`,
agreement with its own detection on the full cloud (this measures robustness to thinning, **not accuracy**):

| cloud | 20 000 pts (13 to 42 /m2) | 6 000 pts (4 to 12 /m2) | 2 000 pts (1.3 to 4 /m2) | 700 pts (0.4 to 1.5 /m2) |
|---|---|---|---|---|
| house 30 x 26 m | rec 0.92 / prec 0.85 | 0.61 / 0.95 | 0.55 / 0.92 | 0.40 / 1.00 |
| building 26 x 18 m | 0.96 / 0.92 | 0.85 / 0.69 | 0.78 / 0.65 | 0.64 / 0.82 |
| building 73 x 22 m | 0.94 / 0.89 | 0.88 / 0.91 | 0.68 / 1.00 | 0.52 / 1.00 |

-> a building is still recognised with ~2 000 points (1 to 4 points/m2 of plan area), and the precision stays high. Limitation seen: on the
**very dense** cloud of the 73 m building (60 000 to 200 000 points, 37 to 125 /m2) the plumb pass alone found 6 to 12 % of the walls
(0.67 to 0.72 with the second pass) because slopes and vegetation fill the Hough accumulator with false peaks; thin dense clouds
(voxel 20 to 30 cm) before detecting. This does not concern tie points, but it matters for the "dense cloud" option later.

### Honest limits

* No ground truth on real data yet: François should mark the façades he wants on one of his sites (two clicks each); 30 minutes give a real
  recall/precision number.
* Trees and dense hedges generate false façades; open-air scaffolding, balconies and shutters spread the points over 0.5 m: widen the band.
* Façade parts (a wall broken by a recess) are cut where the points leave a gap > 4 m (setting `gap`).
* Roof edges / parapets: found as walls when the parapet is >= 2 m (they are walls).

## 4. B. Metashape API (2.2.1)

Sources: the Python Reference 2.2.1 text already on this PC (`scratchpad\api221.txt`, 659 KB), the user manual text (`manual22.txt`), and
tests in the live session. **V** = verified in Metashape 2.2.1 in the Citadelle session; **U** = not verified.

| Question | Answer | |
|---|---|---|
| Read tie points | `chunk.tie_points.points`: `p.coord` is a 4-component Vector (homogeneous, w = 1 here), `p.valid`. 571 k points: 8.3 s including a per-point `crs.project`; 448 k: 6.0 s. In numpy with `transform.matrix` and `crs.localframe` it is the same loop minus the projection call. | V |
| Coordinates | Chunk units are **not metres**: `chunk.transform.scale` = 3.05 / 3.60 / 4.52 (Jan / May / Jul). `chunk.crs` is **geographic** (WGS 84) while the existing DEM and ortho use the projected compound CRS `RGF93 v1 / CC42 + RAC09`. Metric work: world = `transform.matrix @ chunk`, then `crs.localframe(world)` gives a local east/north/up frame (used by the prototype), or `projected_crs.project(world)`. | V |
| `OrthoProjection` types | Only `Planar` and `Cylindrical`. The usual map projection **is** a `Planar` projection with an identity matrix and a projected CRS (the existing DEM and ortho of the project report exactly that). A façade plane is a `Planar` projection with another matrix. | V |
| Meaning of `OrthoProjection.matrix` | 4 x 4, maps the coordinates of `proj.crs` **into the plane frame**: X, Y = image axes, Z = height above the plane. So `matrix = inverse([u v n \| o])` with `u` along the wall (to the viewer's right), `v` up, `n` toward the viewer (= toward the cameras), `o` the origin, all in `proj.crs` coordinates (E, N, H). Checked with `proj.transform`: o -> (0,0,0), o+5u -> (5,0,0), o+3v -> (0,3,0), o+2n -> (0,0,2). | V |
| DEM from the sparse cloud | `chunk.buildDem(source_data=Metashape.DataSource.TiePointsData, ...)` is allowed by the API and by the manual ("preliminary elevation data from tie points"). **It ran with a planar façade projection in 2.2 s** on 448 k tie points: `projection=proj`, `region=Metashape.BBox(Vector((-b,-b)), Vector((L+b,H+b)))` (**2D, in plane coordinates**; a 3D BBox raises `Invalid argument value: region`), `replace_asset=True`. Result 233 x 131 px, extent exactly the requested box. | V |
| DEM resolution | Requested 0.05 m, got **0.112 m**: Metashape imposes its own minimum (tie point spacing). Other values not tried. | V (one value), U (rule) |
| Depth of the DEM | The 2D region does not limit depth: heights ran from -9.7 m to +87.5 m (terrain and trees far in front of the plane). The DEM keeps the surface nearest to the viewer, so anything in front of the wall hides it. A depth crop is needed: the way is `chunk.region` rotated onto the façade box (centre, size, rotation in **chunk** units and axes). | U (script ready, section 8) |
| Ortho in the plane | Manual: planar ortho "providing that mesh is selected as the surface type". API: `buildOrthomosaic(surface_data=ModelData / ElevationData ...)`, `projection=`, `region=`, `resolution=`. Whether `ElevationData` (the planar DEM) is accepted as surface through the API is **not verified**. Fallbacks, in order: surface = planar tie-point DEM; surface = a small mesh built from the tie points inside the region (`buildModel(source_data=TiePointsData)` with `chunk.region` on the façade box, seconds); surface = flat plane (a DEM at constant 0). | **U** |
| Project must be saved? | Manual: ortho "only for projects saved in .PSX". The project here is read-only and DEM built fine in memory; the ortho test is pending. | U |
| Chunk copy | `chunk.copy(items=[TiePointsData], keypoints=False)`: 1.4 s for 278 cameras and 448 k tie points; the copy has no dense cloud, DEM or ortho. Ortho needs cameras only. | V |
| Aim the 3D view at the façade | `Metashape.app.model_view.viewpoint` (`coo`, `rot`, `mag`, `fov`) exists and `Application.captureView()` returns an image. No locked camera needed. | U (exists in the Reference) |
| Read the polygon | `chunk.shapes` / `Shape.geometry` / `Shape.selected` / `Shapes.crs` (Reference). The user draws with Metashape's own polygon tool on the Ortho view; the tool reads the selected shape. | U |
| Export | `exportRaster(source_data=ElevationData / OrthomosaicData, save_world=True, region=..., resolution_x/y=...)`; world file in plane coordinates. Georeferencing of a planar raster in a GeoTIFF: not checked. | U |

Consequence for the user's plan "DEM from tie points, then ortho": **feasible for the DEM**; for the ortho the safe design is "ortho on the
plane (flat) by default, on the DEM or on a local mesh as options", decided by the 5-minute check below.

Cost of the Metashape calls in the session: copy 1.4 s, DEM 2.2 s. Ortho timing is **not measured**; for a 30 m x 15 m façade at 3 cm
(1 000 x 500 px) expect seconds to a minute, dominated by loading the images that see the façade (estimate).

Pure-Python note: numpy 2.0.2 is importable in this Metashape (the project's other tools already depend on it, see the tutorial note).
Without numpy the detector is not usable (a pure-Python Hough on 100 k points would take minutes); the tool should show a clear message
like the Aero-GCP sliders do.

## 5. C. UI and UX

What the repo gives: a dark Qt look (`theme.py`, `ui.py`: `styled_root`, `dark_root`, `big_button`, `OptionCard`, `Mark`, `tile`), tool
entry points (`tool.py`: `install()` / `uninstall()` + `config.TOOLS`), Aero-Exports as the model of a one-window tool, the DEM relief
renderer of Aero-Exports (`fastexport/relief.py` on the `fastexport` branch: palette + Horn hillshade on a numpy array, reusable as is for
the DEM preview; add the colour scale and the buffer outline). **No polygon picker exists in the repo**, and Metashape does not let a plugin
install a mouse tool on the Ortho view (only the photo view has hooks in `msapi`). So:

* **The user draws the polygon with Metashape's own tool** (Ortho view, "Draw Polygon"), selects it, and the tool reads the selected shape.
  The button "Use the selected polygon" validates it. This is the same convention as the "fence = selected polygon" used elsewhere.
* The mini front view is a **Qt painter widget** (no matplotlib, no camera): the tie points of the façade projected on its plane,
  320 x 200 px, metre ticks, density colouring. It is cheaper and more reliable than a locked camera, and it works without the 3D view.
  Optional button "Show in 3D view" sets `model_view.viewpoint` to face the wall (U).
* The DEM preview reuses the relief renderer, shown in a `QLabel` pixmap.

Proposed wizard (English texts; one window, 5 steps, Back / Next):

1. **Area.** "Draw a polygon on the orthomosaic around the façades you need, select it, then click *Use selected polygon*."
   Source: (o) Tie points   ( ) Dense cloud *(not available yet, greyed)*. Shows "12 438 tie points in the area".
2. **Façades.** The detector runs (a few seconds, progress bar). Table: tick box, no., length (m), height (m), direction (compass), tilt,
   points, confidence (stars). Selecting a row shows its front view on the right and highlights it on the ortho (a temporary
   polyline shape). Buttons: *Flip side* (default: the side where most cameras are), *Add a façade by two markers*, *Merge pieces*.
3. **Frame.** Buffer around the façade (default 1.0 m: margin on the four sides and depth in front/behind), façade plane: *best fit* (default)
   / *vertical*, pixel size (default 3 cm, shows the image size in pixels and the file size).
4. **DEM preview.** *Build DEM* -> relief image with scale, depth range, coverage %, warnings ("coverage 41 %: the façade is poorly
   covered by tie points"). *Change the buffer* reruns in 2 s. *Next* only when the user clicks "This is good".
5. **Orthomosaic.** Surface: *Flat plane* (default) / *DEM from tie points* / *Local mesh*; blending; output folder (next to the project, `Outputs\Facades`),
   file names `SITE_DATE_F01_3cm_ortho.tif` (+ `.tfw`, JPG optional, DXF image for AutoCAD with the existing `dxfout` writer: the
   image is placed in a vertical plane there, which `IMAGE` entities can do only with a 3D UCS: check). Queue several façades, one
   progress line per façade. Nothing is added to the user's chunks.

Decisions the user must make: see section 9.

## 6. D. Cost, risk, relevance

Relevance: Metashape already does planar ortho (markers define the plane, region rotated by hand, a dozen clicks per façade, easy to get
wrong). What Aero-Facade adds: automatic façade finding from the data, the plane and region set exactly (no hand rotation), a buffer with
a **preview before the long job**, several façades in one go, naming/export. Value is real for building surveys and for the monthly
ortho chain; it is a niche tool, not a daily one.

Effort (working days, one developer, includes tests, English + French texts):

| Part | Days |
|---|---|
| 5-minute console checks (section 8), decide ortho path | 0.25 |
| `msapi`: tie points in a local frame, selected polygon, work-chunk copy, plane projection, region, DEM, ortho, export | 2 |
| Detector integration, front-view widget, confidence, camera side | 1.5 |
| Wizard window (5 steps), DEM preview, queue and export, i18n | 3 |
| Real-site validation (3 sites, ground truth by hand), tuning | 2 |
| **Smallest useful version** (steps 1, 2, 3 + build + export, no DEM preview polish, flat-plane ortho) | **5 to 6 d** |
| **Complete v1** | **9 to 10 d** |

Main risks:

1. Ortho in the façade plane through the API (surface choice, project-must-be-saved): unverified; three fallbacks exist; worst case a local mesh.
2. The depth of the DEM needs `chunk.region` of the work copy: unverified, but only the copy is touched.
3. Tie points are sparse and noisy on façades with windows, vegetation, scaffolding: the user must see and control (front view, band, manual add).
4. Georeferenced chunks in a geographic CRS need a projected CRS for the plane: take the project's DEM/ortho CRS, else UTM from the longitude.
5. Metashape freezes its window during `buildDem` / `buildOrthomosaic` (main thread): keep each call short, show "working" first, never run the Qt
   event loop inside Metashape's callbacks (the repaint crash fixed in Aero-Exports, commit e236cc7).
6. Large façades at fine pixels: size warning before the run.

What to build first: (1) the console checks, (2) `msapi` + a headless script that takes a wall (from the detector) and produces the DEM and
the ortho on a work copy, (3) the window. The detector is already done.

## 7. E. Aero-Plan / Aero-Draw (opinion only, no prototype)

From the same detector, wall traces are already polylines (`Wall.x0,y0,x1,y1`, height range). A DXF with a layer per type and a LINE or
LWPOLYLINE per trace is easy: `fastexport/dxfout.py` is **image-only** (hand-written R2000, IMAGE entities), so a polyline writer is a
new function, about 80 lines in the same style (no ezdxf, same approach), plus AutoCAD verification with `accoreconsole` like the image
DXF. What is hard is not the file but the **geometry**: closing rooms, snapping corners (intersect consecutive walls), merging the two faces of
one thick wall, handling the pieces cut by gaps. Tie points give wall traces at +-5 to 10 cm, good for "where are the façades", not for an
architectural plan; a dense cloud or a 2 m horizontal slice is the better source for that. Effort 4 to 6 days after Aero-Facade.

## 8. Not verified, and the 5-minute checks

Not verified (**U**): ortho in the plane through the API and with which surface; depth crop through `chunk.region`; DEM resolution rule;
planar GeoTIFF georeferencing; reading the selected shape; `model_view.viewpoint`; the DXF placement of a vertical image; recall/precision on
real façades (no ground truth); behaviour with a dense-cloud DEM (greyed out in the first version).

**Check 1: export the tie points of any chunk (3 lines, Metashape console):**

```python
import numpy as np
c = Metashape.app.document.chunk; V = Metashape.Vector; M = c.transform.matrix; LF = c.crs.localframe(M.mulp(c.region.center))
np.save(r"C:\temp\tie_enu.npy", np.array([list(LF.mulp(M.mulp(V((p.coord[0], p.coord[1], p.coord[2]))))) for p in c.tie_points.points if p.valid]))
```

(east, north, up in metres around the region centre; then `detect.detect(np.load(...))`.)

**Check 2: planar DEM and ortho on a work copy (nothing saved, place two markers `FA` and `FB` at the foot of the two ends of a façade):**

```python
import Metashape as M
V = M.Vector
c0 = M.app.document.chunk
crs = c0.elevation.projection.crs if c0.elevation else M.CoordinateSystem("EPSG::2154")   # any projected CRS in metres
T = c0.transform.matrix
mk = {m.label: m for m in c0.markers}
a, b = [crs.project(T.mulp(mk[k].position)) for k in ("FA", "FB")]
H, BUF, DEPTH = 10.0, 1.0, 2.0                       # façade height, buffer, depth in front/behind (m)
u = V((b.x - a.x, b.y - a.y, 0.0)); L = u.norm(); u = u / L
v = V((0.0, 0.0, 1.0)); n = V((u.y, -u.x, 0.0))       # n must point to the cameras: swap FA and FB otherwise
F = M.Matrix([[u.x, v.x, n.x, a.x], [u.y, v.y, n.y, a.y], [u.z, v.z, n.z, a.z], [0, 0, 0, 1]])
proj = M.OrthoProjection(); proj.type = M.OrthoProjection.Type.Planar; proj.crs = crs; proj.matrix = F.inv()
box = M.BBox(V((-BUF, -BUF)), V((L + BUF, H + BUF)))
c = c0.copy(items=[M.DataSource.TiePointsData], keypoints=False); c.label = "FACADE_CHECK"      # work copy, never save
# depth crop: chunk.region on the façade box (chunk units and axes)
Ti = T.inv(); ch = lambda p: Ti.mulp(crs.unproject(p))
o = ch(a); eu, ev, en = ch(a + u) - o, ch(a + v) - o, ch(a + n) - o; s = eu.norm()
reg = M.Region(); reg.center = ch(a + u * (L / 2) + v * (H / 2)); reg.size = V((L + 2 * BUF, H + 2 * BUF, 2 * DEPTH)) * s
reg.rot = M.Matrix([[eu.x / s, ev.x / s, en.x / s], [eu.y / s, ev.y / s, en.y / s], [eu.z / s, ev.z / s, en.z / s]]); c.region = reg
c.buildDem(source_data=M.DataSource.TiePointsData, interpolation=M.EnabledInterpolation, projection=proj, region=box, resolution=0.05, replace_asset=True)
e = c.elevation; print("DEM", e.width, e.height, e.resolution, e.min, e.max)           # expect max-min ~ buffer, not tens of metres
c.buildOrthomosaic(surface_data=M.DataSource.ElevationData, projection=proj, region=box, resolution=0.03, replace_asset=True)
o2 = c.orthomosaic; print("ORTHO", o2.width, o2.height, o2.resolution)
c.exportRaster(path=r"C:\temp\facade_ortho.tif", source_data=M.DataSource.OrthomosaicData, save_world=True, save_alpha=True)
```

What to look at: (a) DEM `max - min` of the order of the buffer (depth crop works); (b) the ortho builds from the DEM, otherwise change
`surface_data` to `ModelData` after `c.buildModel(source_data=M.DataSource.TiePointsData, surface_type=M.Arbitrary)`; (c) the TIFF shows
the façade upright and not mirrored (if mirrored, swap `FA` and `FB`). Delete the copy (`Metashape.app.document.remove(c)`) and do not save.
The same code, with the plane taken from a detected `Wall`, is what the tool will run. The ready-made probe scripts are in the scratch
folder (`ms_probe1.py`, `ms_probe2a.py`, `ms_probe2b.py`, `ms_probe2c.py`).

## 9. Decisions for the user

1. Go with the **smallest useful version first** (5 to 6 days) or the complete v1 (9 to 10)?
2. **Ortho surface by default**: flat plane (fast, usual for façades) or tie-point DEM? Decide after Check 2.
3. **Manual façade definition** (two markers) in v1, yes or no (recommended: yes).
4. **Plane**: vertical through the best fit (recommended: the ortho is always upright and measurable) or tilted best-fit plane for leaning walls?
5. **Work copy** of the chunk (recommended; the user's chunk is never changed) or in place?
6. Give a **real ground truth** (mark the façades on one site) to turn the synthetic and consistency numbers into real ones.
7. Outputs: TIFF + world file only, or also JPG, and a DXF (image in a vertical plane, to be checked in AutoCAD)?
8. Priority against Aero-Exports and AeroPPK.
9. Aero-Plan (section 7) afterwards, yes or no.

## 10. Where things are

* Worktree `C:\Users\LTB1\AeroTools_facade`, branch `aerofacade` (from `origin/dev` 87313dd), local commits only.
* Prototype: `aerotools/tools/facade/detect.py`; synthetic scene `tests/facade_synth.py`; tests `tests/test_facade_detect.py`.
* Scratch, evaluation scripts and results: `C:\Users\LTB1\AppData\Local\Temp\claude\C--Users-LTB1-Desktop\0e8331c3-9b57-48b0-82bc-f4616d429b51\scratchpad\facade_study\`
  (`eval_all.py`, `eval_results*.json`, `real_*`, `tie_*`, `ms_probe*.py`, `ms_probe*.json`, overlays `tie_*.png`).
