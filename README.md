<p align="center">
  <br>
  <a href="https://github.com/helix-aero/AeroTools/archive/refs/heads/main.zip"><img alt="Download latest version" src="https://img.shields.io/badge/%E2%AC%87%20Download-latest%20version-1eca59?style=for-the-badge&labelColor=06241a"></a>
</p>

<p align="center">
  <a href="https://helix.aero">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="assets/logo/helix-aero-AERO-blanc-512px.png">
      <img alt="HELIX AERO" src="assets/logo/helix-aero-AERO-degrade-512px.png" width="260">
    </picture>
  </a>
</p>

<h3 align="center">Complex flights made easy</h3>

<p align="center">
  Flight planning for aerial mapping, with drones and crewed aircraft.<br>
  <a href="https://helix.aero"><b>Discover HELIX Flight Planner</b></a>&nbsp;&nbsp;&nbsp;&middot;&nbsp;&nbsp;&nbsp;<a href="https://helix.aero"><b>Request a demo</b></a>
</p>

---

## About HELIX AERO

HELIX AERO is flight planning software for aerial mapping, built by surveyors and engineers in Corsica. We started it from our
own survey work: preparing missions, flying them, and checking the data they produce.

- **HELIX Flight Planner** is a desktop application that turns a polygon into an optimized LiDAR or photogrammetry mission,
  checked before the flight, for drones and crewed aircraft. It calculates on your computer's graphics card, works offline, and
  exports missions for DJI aircraft (MAVLink is coming). It is in early access.
- **HELIX FMS** guides the pilot of a crewed survey aircraft along the planned flight line, on an iPad. It is in beta.

AeroTools, below, is the part of our daily tooling that we share with the Metashape community.

[Discover HELIX Flight Planner →](https://helix.aero)

---

# AeroTools

**Mark ground control points in Agisoft Metashape in a fraction of the time, by hand or with an AI that proposes the marks.**

<p>
  <a href="LICENSE"><img alt="License: PolyForm Noncommercial 1.0.0" src="https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-blue"></a>&nbsp;&nbsp;&nbsp;
  <img alt="Metashape Pro 2.x" src="https://img.shields.io/badge/Metashape%20Pro-2.x-green">&nbsp;&nbsp;&nbsp;
  <img alt="Windows" src="https://img.shields.io/badge/platform-Windows-lightgrey">
</p>

[Tutorial](docs/TUTORIAL.md) · [Version française](README.fr.md) · [Development](docs/DEVELOPMENT.md)

![The FastGCP panel docked in Metashape: header, play/pause button, AI detection card, active marker, marker table, Residual check and Export buttons](docs/images/panel-overview.png)

We map with drones and mark a lot of ground control points (GCPs). Metashape does it, but the workflow is clumsy: you scroll
through hundreds of photos to find the ones that see a given marker, open them one by one, zoom in by hand, click, and start
again. So we wrote a tool for ourselves. It saved us enough time that we decided to share it.

**FastGCP** is the tool. It adds an **Aero** menu to Metashape and does two things in one panel: **manual marking** and
**AI detection** that proposes the marks for you to review.

![The FastGCP Review panel: one thumbnail per photo, each with the point proposed by the AI](docs/images/review-panel-overview.png)

## Features

- **Fast manual marking.** Pick a marker and the Photos pane filters itself to the photos that see it. Click on the target: the
  point lands exactly where you clicked, and the next photo opens on its own, zoomed on the marker. Ctrl+Z takes back the last
  point, Ctrl+Y brings it back.
- **Marker table.** X, Y and Z error and number of projections for every marker, sortable and searchable. Select several markers
  with Shift or Ctrl and remove their projections, or the markers themselves, with a right-click.
- **Residual check.** Lists the markers to watch: high residual, or fewer than 3 placed photos.
- **AI detection.** An AI proposes a point on every photo that should see the marker, and you review the proposals.
- **Review panel.** One thumbnail per photo. Exclude the bad proposals, refine the imprecise ones, validate the rest.
- **PDF report and CSV table.** One click from the **Export** button.
- English and French interface. Pause the tool and Metashape behaves as usual.

## Install

You need Metashape Pro 2.x on Windows. We use it daily with Metashape 2.2.

1. Click **Download latest version** at the top of this page (or clone this repository), and unzip it anywhere you like.
2. Double-click **`CLICK HERE TO INSTALL.bat`** (at the root of the unzipped folder). It adds the **Aero** menu to Metashape and offers to install the AI detection too.
3. Restart Metashape. The **Aero** menu is there.

The installer writes one small launcher into `%LOCALAPPDATA%\Agisoft\Metashape Pro\scripts\`. Nothing else is touched.
`install\desinstaller_aerotools.bat` removes it.

> [!NOTE]
> The AI detection needs a separate Python with `torch` and `ultralytics` (about 3 GB). The installer offers to set it up for
> you (it can also be run later: `install\installer_ia.bat`). The hand-marking tools work without it. See the
> [troubleshooting table](docs/TUTORIAL.md#9-troubleshooting).

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

![The AI detection card: Run, Clear proposals and Review buttons, target type, scope and photos to ignore](docs/images/ai-card.png)

- Two target types, each with its own model: **Checker** (2x2 checkerboard) and **Spray** (paint or chalk mark). They are never mixed.
- Scope: the selected markers, or all the markers that have a 3D position.
- The calculation runs in a separate process, so Metashape stays usable, and a **Cancel** button stops it.
- A proposal is not a placed point: it never replaces a point you placed. Two checks reject doubtful detections: one that is too
  far from where the marker's other projections say it should be, and detections that do not meet at a single 3D point.
- **Aero V1.0** is the model we ship and is always there. From the Settings window you can retrain a **Local** copy on your own
  decisions from the Review panel (exclusions, refinements, validations). The original model is never modified, and **Revert to
  Aero V1.0** brings it back at any time. Adding a model of your own (**Add…**) is not available yet.

![The Settings window: storage folder, model choice and the retraining button](docs/images/settings-window.png)

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
trained on.

If something breaks, open an issue and paste the lines from the Log (Settings window). It makes fixing much faster.

## Development

Issues, ideas and pull requests are welcome.

- Fork the repository, work on a branch, and open a pull request against `dev`.
- Code runs in Metashape's embedded Python 3.9 with PySide2, and also under PySide6. No extra dependency, please.
- Run the tests with `python -m pytest tests -q`. They do not need Metashape.
- By sending a pull request you agree that your contribution is released under the same license, and that HELIX AERO may also offer it under other terms, including commercial ones.
- More details in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## License

[PolyForm Noncommercial 1.0.0](LICENSE). Copyright 2026 HELIX AERO.

The code and the base AI model (`models/damier.pt`, `models/bombe.pt`, "Aero V1.0") are open to read, and free to use, modify and share for any non-commercial purpose: personal use, study,
research, teaching, hobby projects, non-profit organizations. Commercial use needs a separate license from us.
If you would like to use AeroTools in a business, get in touch. We are happy to talk.

---

<p align="center">
  <a href="https://helix.aero">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="assets/logo/helix-aero-AERO-blanc-512px.png">
      <img alt="HELIX AERO" src="assets/logo/helix-aero-AERO-degrade-512px.png" width="200">
    </picture>
  </a>
</p>

<h3 align="center">You mark the control points. Let HELIX plan the flight.</h3>

<p align="center">
  Bring a survey area to a video demo and look at the plan together, or try HELIX Flight Planner free for seven days.<br><br><br>
  <a href="https://helix.aero"><b>Discover HELIX Flight Planner</b></a>&nbsp;&nbsp;&nbsp;&middot;&nbsp;&nbsp;&nbsp;<a href="https://helix.aero"><b>Request a demo</b></a>
</p>
