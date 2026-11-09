# Aero-GCP screenshots

Images of `README.md` and `docs/tools/aero-gcp.md`. Taken in a running Metashape (English interface, Aero-GCP panel).

**Rules.** Show the window only: no Metashape title bar, no project path, no client or server name, no personal data. Crop
tight. The Aero menu lists every installed tool, so crop it to the Aero-GCP line.

**How they were taken.** Metashape 2.2.1 on a sandbox project (a copy of a chunk, nothing saved), English interface, the grey theme of the
panel (there is no light theme). Each image is a crop of the screen at its native size, so the window title bar and the Workspace pane
are never in them. The storage folder path in the two Settings images is blurred because it holds a user name. The Aero menu image is
enlarged 3 times.

**Missing image.** `tests/test_docs_links.py` fails on a link to a file that does not exist. A screenshot still to take can be linked as
`NN-name_placeholder.png` (a grey card): the normal run accepts it, and `AERO_DOCS_STRICT=1 python -m pytest tests/test_docs_links.py`
fails while one is still linked.

| File | What it shows |
|---|---|
| `01-aero-menu` | The Aero menu open, cropped to the **Aero-GCP** entry (F8) |
| `02-panel-overview` | The whole panel: header, switch, AI detection card, active marker, marker table, Residual check, Adjust, Export |
| `03-tool-switch` | The switch in its two states, **Tool active** and **Tool inactive** |
| `04-photo-marking` | The photo view zoomed on a target, with the point just placed |
| `05-marker-table` | The active marker card, the Search box and the table with Error X, Y, Z and Proj. |
| `06-marker-menu` | Right-click on selected markers: Remove projections, Remove markers |
| `07-ai-card` | The AI detection card: Run, Clear proposals, Review and the three lists |
| `08-ai-target-type` | The target type list open: Checker, Spray |
| `09-ai-scope` | The scope list open: Selected markers, All markers |
| `10-ai-ignore` | The Ignore list open: the four choices |
| `11-ai-running` | The progress bar and Cancel during a detection |
| `12-photo-proposal` | A photo with the point proposed by the AI on a target |
| `13-review-panel` | The Review panel: markers, thumbnails, selected photo, sliders |
| `14-refine-window` | The Refine window with the red proposal and a green pick |
| `15-marker-check` | The Marker check window opened by Residual check |
| `16-diagnostic` | The Marking diagnostic window |
| `17-adjust-window` | The Adjust window: Now table, Options table, recommended row |
| `18-adjust-mode` | The Adjust model list open: Full frame, Shift only |
| `19-export-menu` | The Export menu: PDF report, CSV table |
| `20-settings-top` | Settings: Detection, Model, Data |
| `21-settings-bottom` | Settings: Training, Options |
| `review-panel-*` | Review panel details (taken 04/11/2026, the Review panel has not changed since) |
| `settings-training-warning` | The warning shown before training |
