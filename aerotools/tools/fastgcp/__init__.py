"""FastGCP - assisted GCP marking in the Metashape photo view.

Principle: one marker is "current"; a click in the photo view (or Shift+click)
places a projection for it, and the Photos pane filters itself to the photos that
see this marker, including those of a GCP that was never marked, computed from
its reference coordinates.

Modules:
  session.py    session state (current marker, options, Qt references)
  tool.py       install() / uninstall(), called by the Aero menu
  panel.py      the Qt dock
  marking.py    event filter and projection placement
  photos.py     Photos pane filter and move to the next photo
  dataset.py    recording of the markings for training (optional)
  detection.py  hook for the automatic detection
  aigcp.py      AI detection of checker / spray targets: marking proposals (outside Metashape)
  aigcp_runner.py  runner started in an external Python (onnxruntime; torch as a fallback)
"""
