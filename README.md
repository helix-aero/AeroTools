# AeroTools

Small scripts for Agisoft Metashape, written by [HELIX AERO](https://helix.aero) in Corsica.

[Version française](README.fr.md)

<!-- Screenshot of the FastGCP panel goes here: docs/images/fastgcp.png -->

## Why this exists

We map with drones and we mark a lot of ground control points. Metashape does it, but the workflow is clumsy:
you scroll through hundreds of photos to find the ones that see a given marker, open them one by one,
zoom in by hand, click, and start again.

So we wrote a script for ourselves. It saved us enough time that we decided to share it.
It is not a big piece of software. It replaces a few Metashape features that are not very ergonomic,
and it does that well enough that we use it on every project.

If you do photogrammetry, you may find it useful too.

## What is inside

AeroTools adds an **Aero** menu to Metashape. The tools load when you click them.

**FastGCP** is the first tool. It is a panel for marking GCPs quickly:

- Pick a marker and the Photos pane filters itself to the photos that see it.
- Click in the photo view to place or move the marker at the exact spot you clicked.
- The next photo that sees the marker opens on its own, zoomed on the area around the marker.
- Ctrl+Z (or the undo button) takes back the last point.
- A table lists every marker with its X, Y and Z error and its number of projections. Click a column header to sort.
- One click exports a PDF report (key figures, error charts, screenshots) or a CSV table.
- The panel comes in English and French, and in a light or dark theme.
- Pause the tool and Metashape behaves as usual.
- An optional "AI Training" switch saves where you clicked, so the data can train detection models later.

**AIGCP** is next. See the roadmap below.

## Install

You need Metashape Pro 2.x on Windows. We use it with Metashape 2.2 every day.

1. Download or clone this repository anywhere you like.
2. Double-click `install\installer_aerotools.bat`.
3. Restart Metashape. The **Aero** menu is there.

The installer writes one small launcher into `%LOCALAPPDATA%\Agisoft\Metashape Pro\scripts\`. Nothing else is touched.
`install\desinstaller_aerotools.bat` removes it. On macOS or Linux, the same launcher should work if you copy it to your
Metashape scripts folder yourself, but we have not tried.

## Use FastGCP

1. **Aero > FastGCP** (shortcut F8) opens the panel on the right.
2. Pick a marker in the list or in the Reference pane.
3. Click on the target in the photo view.
4. Repeat. The tool moves on to the next photo.

Mistakes happen. Ctrl+Z undoes the last point, and the Journal (gear button) says why a click was refused.

## Roadmap

- **AIGCP.** We are training detection weights for chalk marks, whatever the shape of the target.
  Proposed points will appear in blue so you can accept or correct them. We expect to publish this within a few weeks.
- Other small tools, as we need them on our own projects.

## Status

We use AeroTools on real projects, but only on a few setups so far.
Metashape's photo view API is thinly documented, so expect a few rough edges.
If something breaks, open an issue and paste the lines from the Journal. It makes fixing much faster.

## Contributing

Issues, ideas and pull requests are welcome.

- `main` and `dev` are protected. Fork the repository, work on a branch, and open a pull request against `dev`.
- Code runs in Metashape's embedded Python 3.9 with PySide2, and also under PySide6. No extra dependency, please.
- Run the tests with `python -m pytest tests -q`. They do not need Metashape.
- By sending a pull request you agree that your contribution is released under the AGPL-3.0.
- More details in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## License

GNU AGPL-3.0, see [LICENSE](LICENSE). Copyright 2026 HELIX AERO.

You are free to use and modify AeroTools, including at work. If you distribute a modified version, or let others use
it over a network, you must publish your changes under the same license. The simplest way is to send them back
here as a pull request. If that does not suit you, get in touch and we can talk about other terms.
