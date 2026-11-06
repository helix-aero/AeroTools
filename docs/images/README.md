# Documentation screenshots

The images in this folder illustrate `docs/TUTORIAL.md` and `README.md`.
Eight come from Greenshot captures of 04/11/2026 ("Review panel" windows); the originals stay in
`Desktop\Screenshots_greenshot`, the files here are copies. Five others (Aero-GCP panel, table, AI card, Settings) were
taken on 04/11/2026 directly in a running Metashape, English interface, on the CITADELLE_Pompeani project.

## Already there

| File | What it shows |
|---|---|
| `review-panel-overview.png` | Review panel on PS_1: status tags (red cross, green checks), summary line, right-hand panel |
| `review-panel-markers.png` | List of all the markers on the left, 573 proposals kept |
| `review-panel-adjust-before.png` | Zoom 400 %, viewing sliders almost neutral |
| `review-panel-adjust-after.png` | Same thumbnails with exposure -60, contrast +85, saturation +69 |
| `review-panel-excluded.png` | Thumbnails at 131 % zoom on PS_6; the bottom line counts 559 kept, 10 excluded, 4 refined (no tag visible in the scrolled area) |
| `review-panel-context-menu.png` | Right-click menu: Exclude, Put back, Refine… |
| `review-panel-selected.png` | PS_2: one selected thumbnail (green outline, confidence 0.66), right-click menu open, Exclude and Refine… active, Put back greyed out |
| `review-panel-refined.png` | PS_11 at 400 %: first thumbnail refined (green cross, green check), the others keep the red cross; 572 kept, 1 refined |
| `panel-overview.png` | The whole Aero-GCP panel: header, Play/Pause button with "Tool active", AI DETECTION card, active marker PS_4, search, table, Residual check, Export, helix.aero credit. Used in `README.md` and `docs/TUTORIAL.md` |
| `marker-list.png` | The marker table: ID, Error X/Y/Z and Proj. columns, active marker PS_4 highlighted. It shows neither multi-selection nor the right-click menu. Used in `docs/TUTORIAL.md` |
| `ai-card.png` | The AI DETECTION card: Run (green), Clear proposals and Review greyed out, three lists (Spray, All markers, Ignore green + blue). Used in `docs/TUTORIAL.md` |
| `settings-window.png` | The Settings window: General (Open the review panel automatically, Save marker picks, Log) and AI models (storage folder, Aero V1.0, Refine my model using my data). It shows the local path `C:\Users\LTB1\AppData\Local\HELIX\AeroTools`. Used in `docs/TUTORIAL.md` |
| `settings-training-warning.png` | The "Refine the AI model" warning with its Yes and No buttons. Opened, then declined with No: no training was started. Used in `docs/TUTORIAL.md` |

> The eight `review-panel-*` captures were taken before the latest interface changes: the Review panel is unchanged, but the
> AI detection card and the main panel visible in some of the original captures no longer have the same layout. They are
> therefore only used for the Review panel; the main panel and the AI card come from the five more recent captures.

## Still to do

The `.md` files already reference them as comments `<!-- TODO screenshot: ... -->`. When a file exists, replace the
comment with `![text](images/name.png)`.

| Expected file | What it should show | Why not taken yet |
|---|---|---|
| `refine-window.png` | The Refine window: title "Refine marker … on …", red and green crosses, slider panel, `n/N picked` bar, Cancel / Next / Validate and exit buttons, and ideally a second capture with the green Validate button | Needs a real AI detection (Run) on the open project, not run so as not to modify the project |
| `report-ai-page.png` | A page of the PDF report "Photos picked by the AI" with at least one excluded photo (large red cross) | Needs a detection, then a report export |
| `validate-busy.png` | The small "Applying changes" window with its progress bar, during Validate | Needs validating AI picks, which modifies the project |

To add one: take the capture with Greenshot, copy it here under the expected name, then replace the comment in the
relevant `.md` files (`docs/TUTORIAL.md`).
