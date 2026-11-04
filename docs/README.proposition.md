<!--
Proposal for the repository README (English). To use it, copy this file to the repository root as README.md.
The links and image paths are written for the root of the repository (docs/..., LICENSE, README.fr.md): they work once the file is there.
Nothing here changes the real README.md, README.fr.md or LICENSE.
-->

<h1 align="center">AeroTools</h1>

<p align="center">
  <b>Mark ground control points in Agisoft Metashape in a fraction of the time, by hand or with an AI that proposes the marks.</b>
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: PolyForm Noncommercial 1.0.0" src="https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-blue"></a>
  <img alt="Metashape Pro 2.x" src="https://img.shields.io/badge/Metashape%20Pro-2.x-green">
  <img alt="Windows" src="https://img.shields.io/badge/platform-Windows-lightgrey">
</p>

<p align="center">
  <a href="docs/TUTORIAL.md">Tutorial</a> ·
  <a href="README.fr.md">Version française</a> ·
  <a href="docs/DEVELOPMENT.md">Development</a>
</p>

![The FastGCP Review panel: one thumbnail per photo, each with the point proposed by the AI](docs/images/review-panel-overview.png)
<!-- TODO screenshot: docs/images/panel-overview.png (the FastGCP panel docked in Metashape) -->

AeroTools is a small suite of tools for Agisoft Metashape, written by [HELIX AERO](https://helix.aero) in Corsica. We map with
drones and mark a lot of ground control points (GCPs). Metashape does it, but the workflow is clumsy: you scroll through hundreds
of photos to find the ones that see a given marker, open them one by one, zoom in by hand, click, and start again. So we wrote
a tool for ourselves. It saved us enough time that we decided to share it.

**FastGCP** is the first tool. It adds an **Aero** menu to Metashape.

## Features

- **Fast manual marking.** Pick a marker and the Photos pane filters itself to the photos that see it. Click on the target: the
  point lands exactly where you clicked, and the next photo opens on its own, zoomed on the marker. Ctrl+Z takes back the last
  point.
- **Marker table.** X, Y and Z error and number of projections for every marker, sortable and searchable. Select several markers
  with Shift or Ctrl and remove their projections, or the markers themselves, with a right-click.
- **Residual check.** Lists the markers to watch: high residual, or fewer than 3 placed photos.
- **AI detection.** An AI proposes a point on every photo that should see the marker, and you review the proposals.
- **Review panel.** One thumbnail per photo. Exclude the bad proposals, refine the imprecise ones, validate the rest.
- **PDF report and CSV table.** One click from the **Export** button.
- English and French interface, light and dark theme. Pause the tool and Metashape behaves as usual.

## Install

You need Metashape Pro 2.x on Windows. We use it daily with Metashape 2.2.

1. Download or clone this repository anywhere you like.
2. Double-click `install\installer_aerotools.bat`.
3. Restart Metashape. The **Aero** menu is there.

The installer writes one small launcher into `%LOCALAPPDATA%\Agisoft\Metashape Pro\scripts\`. Nothing else is touched.
`install\desinstaller_aerotools.bat` removes it.

> [!NOTE]
> The AI detection needs two things the installer does not provide: a separate Python with `torch` and `ultralytics`, and the
> weight files `models\damier.pt` and `models\bombe.pt` (`*.pt` files are not stored in git). The hand-marking tools work without
> them. See the [troubleshooting table](docs/TUTORIAL.md#9-troubleshooting).

## Quick start

1. **Aero > FastGCP** (F8) opens the panel on the right of Metashape.
2. Pick a marker in the panel or in the Reference pane, then click on the target in the photo view. Repeat: the next photo opens by itself.
3. Or let the AI do the first pass: choose the target type and the scope, then press **Run**. The Review panel opens when the
   detection ends.
4. In the Review panel, click the bad thumbnails and press **Exclude**, or **Refine…** to place the point yourself, then press
   **Validate**.

![A thumbnail selected in the Review panel: Exclude and Refine… are active](docs/images/review-panel-selected.png)

After **Refine…**, the thumbnail shows your green cross and a green check:

![A refined thumbnail in the Review panel, with a green cross and a green check](docs/images/review-panel-refined.png)

Exposure, contrast, saturation and sharpness sliders help your eye in the Review panel. They never modify your photos.

| Before | After |
|---|---|
| ![Thumbnails with no adjustment](docs/images/review-panel-adjust-before.png) | ![The same thumbnails with exposure -60, contrast +85 and saturation +69](docs/images/review-panel-adjust-after.png) |

The [tutorial](docs/TUTORIAL.md) goes through every button.

## AI detection

- Two target types, each with its own model: **Checker** (2x2 checkerboard) and **Spray** (paint or chalk mark). They are never mixed.
- Scope: the selected markers, or all the markers that have a 3D position.
- The calculation runs in a separate process, so Metashape stays usable.
- A proposal is not a placed point: it never replaces a point you placed. Two checks reject doubtful detections: one that is too
  far from where the marker's other projections say it should be, and detections that do not meet at a single 3D point.
- **Aero V1.0** is the model we ship and is always there. From the Settings window you can retrain a **Local** copy on your own
  decisions from the Review panel (exclusions, refinements, validations). The original model is never modified, and **Revert to
  Aero V1.0** brings it back at any time. Adding a model of your own (**Add…**) is not available yet.

> [!IMPORTANT]
> Retraining is an experiment, not an upgrade. In our one test (spray type, 798 positive and 208 negative examples, 75 seconds
> on an RTX 4070), the new model scored mAP50 0.9762 against 0.9784 for Aero V1.0 on the targets kept aside: no measurable
> difference. We do not promise that it improves detection on your data. Compare, and revert if it does not do better.

## PDF report

**Export > PDF report** writes a report of the current chunk: key figures, error charts, a table of the markers and screenshots.
After an AI detection, it adds a section called *Photos picked by the AI*: one block per marker with the pointing gap and the AI
confidence, one thumbnail per photo, and the excluded photos too, darkened with a large red cross and left out of the
statistics. **Export > CSV table** writes the marker table.

See [an example report](docs/exemple_rapport_gcp.pdf) (in French, without the AI section).

## Status

We use AeroTools on real projects, but only on a few setups so far. Metashape's photo view API is thinly documented, so expect a
few rough edges. The AI detection is recent: we measured it on our own projects, and it works best on the target types it was
trained on. The **Aero** menu still lists a separate AIGCP entry marked "coming soon": the detection itself already lives in the
FastGCP panel.

If something breaks, open an issue and paste the lines from the Log (Settings window). It makes fixing much faster.

## Development

Issues, ideas and pull requests are welcome.

- `main` and `dev` are protected. Fork the repository, work on a branch, and open a pull request against `dev`.
- Code runs in Metashape's embedded Python 3.9 with PySide2, and also under PySide6. No extra dependency, please.
- Run the tests with `python -m pytest tests -q`. They do not need Metashape.
- By sending a pull request you agree that your contribution is released under the same license, and that HELIX AERO may also offer it under other terms, including commercial ones.
- More details in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## License

[PolyForm Noncommercial 1.0.0](LICENSE). Copyright 2026 HELIX AERO. Free to use, modify and share for non-commercial purposes;
commercial use needs a separate license from us.

Made by [HELIX AERO](https://helix.aero).
