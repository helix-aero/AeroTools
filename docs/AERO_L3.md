# Aero-L3

**Aero > Aero-L3** turns the photos of a DJI Zenmuse L3 into a Metashape chunk that is ready to align: the right two sensors, a camera for
every photo, positions with their accuracy, and a starting orientation that keeps the pictures from coming out flipped or turned. The LiDAR
point clouds are out of scope: this tool only prepares the photos.

Package `aerotools/tools/aerol3/`: a pure core (no Metashape, no Qt), one Metashape adapter (`msl3.py`, the only module that imports
`Metashape`) and the Qt wizard. It was written from the files of a real project (a 44 km coastal corridor, 15 022 L3 photos, 14 332 of them aligned in
Metashape 2.2.1); every number below was measured on it, not read in a manual.

## The wizard

1. **L3 folder.** Pick a flight folder, a folder with several flights, or the whole mission. The header of every photo is read (about 1 000
   photos a second, the pictures are not opened), plus the `*_Timestamp.MRK` of each flight. Photos sorted in `LV` / `RV` folders work too.
2. **Flights.** One row per flight (photos, shots, RTK fixed share, lens tilt, files). Flights of three shots or fewer and flights looking
   mostly sideways (calibration flights) start unticked.
3. **Checks**, before anything is created. A red *STOP* blocks the project; an orange *CHECK* is shown and does not block.
4. **Project.** Name of the chunk (`L3_<site>_<YYMMDD>`), frame of the positions, accuracies, height shift, calibration fixed or free, photos
   without partner, and the steps to run afterwards.
5. **Create.** Progress bar, Cancel (it stops the whole batch), then one line per step and what to do next. The tool never saves the project.

The minimum is *Create* alone: a chunk ready for *Workflow > Align Photos*. Optional steps, in this order: a quick test alignment (on a copy of
the chunk, deleted afterwards), the alignment, the repair of flipped cameras, the camera optimization, a DEM from depth maps, the orthophoto.

## What the L3 writes, and what the tool does with it

* **Two lenses.** Every shot gives `..._LV.JPG` (`ImageSource=LEFT_CAMERA`) and `..._RV.JPG` (`RIGHT_CAMERA`), 6144 x 4096, EXIF orientation 1,
  two different serial numbers and two different calibrations. The lenses are exactly 45 degrees apart around the vertical axis of the
  picture (7 510 pairs: 44.92 to 45.08), each 22.5 degrees off the nadir, left lens looking left of the flight, right lens looking right.
* **Sensors.** Metashape's multi-camera layout: groups of two photos, the left lens first. The first photo of a group makes the master
  sensor, so the left lens is the master and the right lens the slave, with a starting rotation of +45 degrees about the picture's vertical
  axis. A lone photo would take the master's calibration, so photos without partner are left out unless you ask for them, and then they get a
  sensor of their own.
* **Calibration.** `DewarpData` = `date;fx,fy,cx,cy,k1,k2,p1,p2,k3`. Metashape's `p1` and `p2` are the 8th and 7th numbers (DJI's `p1` and
  `p2` are swapped). The calibration is a starting value and stays adjustable: on a 44 km corridor the DJI values alone were not enough.
  With `DewarpFlag=1` (picture already undistorted) the camera is a pinhole with `CalibratedFocalLength`. Metashape's `load_xmp_calibration`,
  `load_xmp_orientation`, `load_reference`, `load_xmp_accuracy` and `load_xmp_antenna` are all switched off: the tool writes its own values.
* **Positions.** `GpsLatitude`, `GpsLongitude`, `AbsoluteAltitude` (WGS 84 ellipsoidal height, `AltitudeType=RtkAlt`). Accuracy by `RtkFlag`:
  50 (fixed) 0.05 m, 34 (float) 0.25 m, other 5 m. `RtkStd*` is 1 to 5 mm and is not believed. The reference system is WGS 84 (or ETRS89).
* **Time.** `UTCAtExposure` of the XMP is **GPS time**, not UTC (18 s ahead), the same as the MRK second of week.

### The orientation, and why no picture comes out flipped

The XMP holds three sets of angles. The *gimbal* angles (`GimbalPitch=-90`, `GimbalRoll=+180` in a mapping flight) describe the base, not the
lenses; read as camera angles they lose the 22.5 degree tilt of each lens. The *camera* angles (`CameraYaw/Pitch/Roll`) are the right ones:
read as DJI north-east-down angles (yaw about Z, pitch about Y, roll about X, body axes forward-right-down, forward = optical axis) they
match the **adjusted** poses of the 14 210 aligned cameras of the reference project to 1.6 degrees (left lens, 3.1 at most) and 2.0 degrees
(right lens, 3.3 at most). The same camera is written with opposite-looking angles from one flight direction to the other, which is a good way
to mislead a generic importer. The gimbal angles plus a tilt of -22.5 / +22.5 degrees about the picture's vertical axis give the same
orientation to 0.01 degree on all 15 022 photos and are the fallback and the cross-check.

The flips seen on the reference project came from an alignment with no constraint on the rotations: a badly connected strip could settle in a
mirrored pose (optical axis more than 40 degrees off the vertical, instead of 22.5). Nothing was flipped in the files. So:

1. Every photo that is not 6144 x 4096 or carries an EXIF orientation other than 1 is left out with a message (Metashape would turn it and
   the calibration would no longer fit), and every camera is set to `orientation = 1`.
2. Every camera gets its starting orientation (accuracy 10 degrees by default, the real gap is 3.3 at most): a flipped solution is far outside it.
3. Before creating: the top of the picture must point along the direction of travel, the left lens must lean left and the right lens right,
   the lenses must be 45 degrees apart. Each failure is a message with the file names.
4. After creating, the angles written into Metashape are read back and compared with the plan.
5. After the alignment, every aligned camera is compared with its starting orientation. More than 20 degrees away (`geomcheck.FLIP_DEG`) is a
   flip: the camera and its partner are reset and aligned again, two passes at most; what stays flipped is reported, not hidden.
6. The *quick test alignment* aligns a few dozen consecutive shots on a copy of the chunk and reports the median gap per lens: a rig rotation
   with the wrong sign would show as a right lens 90 degrees off.

## Settings worth knowing

* **Height shift.** On the reference project the camera heights came out 0.27 m too low against the control points; the cause was not found
  (the MRK offsets average 0.35 m in V, which is the right order but unproven, and their horizontal part is already in the photo positions).
  The tool applies nothing by itself. Place your control points after the alignment and enter the shift if they ask for one.
* **Frame.** The real-time RTK of the drone may be in the frame of the network that corrected it (ETRS89 / RGF93 for the French RGP), up to
  about 1 m from the WGS 84 of the day. Check on control points.
* **DEM and orthophoto.** The DEM and the ortho are built in the WGS 84 UTM zone of the site unless the hidden option `ortho_epsg` says
  otherwise. They are offered for convenience only: on the reference project the surface came from the LiDAR, and the ortho was built without seamline
  refinement (about 40 hours on 14 000 photos).

## Limits

* Photos only (no point cloud). One chunk holds one L3 (one pair of serial numbers and calibrations); photos of another unit block the project.
* PPK: the MRK holds the exposure time in GPS time, as the Mavic 3 files of AeroPPK do, so a PPK of the L3 positions is possible later. Version 1
  takes the RTK positions of the XMP. The L3 does not write observation files next to the photos.
* Not measured: other DJI payloads, a second L3 unit, the real Metashape behaviour of a few API calls. The adapter is tested against a fake Metashape; a real project is the final check.

## Tests

```
python -m pytest -q tests\test_aerol3_core.py tests\test_aerol3_msl3.py tests\test_aerol3_state.py
python -B tests_qt\run_qt_aerol3.py [--shots DIR]
```

`tests/aerol3_synth.py` writes tiny synthetic L3 photos (header only, invented coordinates and calibration, the real structure of the files).
