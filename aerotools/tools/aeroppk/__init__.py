"""Aero-PPK: post-processed kinematic (PPK) positions of drone photos from public base stations.

  drones/       drone readers (one per family: DJI Mavic 3 Enterprise today; add yours there)
  stations/     base station networks (RGP, EPN, IGS, NOAA CORS, Geoscience Australia): catalogue, availability, download
  rtklib.py     the PPK engine (RTKLIB-EX as a sub-process)
  pipeline.py   the calculation chain, combine.py the error estimate of each photo
  dialog.py, mapview.py, report.py   the window, the map, the PDF and CSV report
Documentation: docs/AEROPPK.md
"""
