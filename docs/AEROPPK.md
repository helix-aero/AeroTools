# Aero-PPK

Aero-PPK (menu **Aero > Aero-PPK**, folder `aerotools/tools/aeroppk/`) replaces the position of every photo of a DJI Mavic 3
Enterprise flight by a post-processed (PPK) position computed from a public base station or from the user's own base, tells how good the
result is, writes the positions into the Metashape chunk (with an Undo) and writes a PDF report. The user page is
[docs/tools/aero-ppk.md](tools/aero-ppk.md). Everything below that says "measured" was measured on real files on 06/11/2026 (numbers in the
benchmark tables); what is not measured is said so.

## Architecture

```
drones/        one reader per drone family (base.py = the contract, dji_m3e.py = today's only reader)
stations/      one module per network (rgp.py, epn.py, igs.py, noaa.py, ga.py: metadata, file names, station snapshot `*_stations.json`),
               provider.py (what they share: `Product`, probe, download), archive.py (long RINEX 3 names, short RINEX 2 daily names),
               catalog.py (the networks, the merged station list, frames), station.py (a station and its motion), sources.py and
               build_catalogs.py (how the snapshots are made), net.py (polite HTTP), inspect.py (look inside a file with a 32 KB range
               read), survey.py (what each station offers for a flight), orbits.py (which precise orbits exist for a flight day)
gnsstime.py    GPS week / second, leap seconds        geo.py       ellipsoid, local north-east-down, distances
frames.py      ETRS89 <-> ITRF through the embedded PROJ       rinex.py, unpack.py, rinexprep.py   headers, .Z / .gz / Hatanaka, merging
rtklib.py      RTKLIB-EX as a sub-process (config file, .pos parser, interpolation at the photo times)
pipeline.py    the chain: files -> base -> RTKLIB run (several profiles) -> camera positions -> checks
combine.py     the error estimate of each photo, quality flags
reportdata.py, report.py   the dictionary and the PDF / CSV   mapview.py, mapmath.py   the map
state.py, basket.py, ownbase.py, devices.py, flights.py, outputs.py, summary.py, timeline.py, runner.py   the rules of the wizard (no Qt)
solutions.py   the stored solutions of the window and the comparison of two of them (memory only, no Qt)
ionosphere.py  the ionosphere parameters RTKLIB needs, and the stub navigation file that carries them when a file has none
dialog.py      the window shell and the six steps, built on the UI kit (`aerotools/uikit`, see docs/UIKIT.md)
screen_drone.py, screen_files.py, screen_mode.py, screen_base.py, screen_results.py, screen_export.py   one module per step
screen_details.py, screen_compare.py   the Details and Compare windows opened from the Result step
ui_common.py, ui_views.py, widgets.py   what the screens share; the few widgets the kit does not have
tool.py        entry point of the menu                 backup.py + msapi.py   camera position replacement and its undo
```

Rules kept: no Qt and no Metashape below the `screen_*.py` files, `ui_common.py`, `ui_views.py`, `dialog.py`, `mapview.py` and `report.py` (the calculation runs in a worker thread); every
Metashape call is in `msapi.py`; RTKLIB, crx2rnx and PROJ run as separate processes; nothing is hard coded to a machine (programs
found in `aerotools/vendor/rtklib`, then the setting `aeroppk.rtklib_dir`, then the PATH; downloads cached in
`%LOCALAPPDATA%\HELIX\AeroTools\AeroPPK`).

### Adding a drone

Write a class with the methods of `DroneReader` (`drones/base.py`): `accepts(folder)`, `scan(folder)` -> `FlightFiles`,
`needs(files)` -> list of `Need` (what is present / missing and whether it blocks), `read_events(files)` -> list of `PhotoEvent`
(time in GPS seconds, lever arm antenna -> camera in north / east / down metres, the drone's own position for comparison).
Register it in `drones/__init__.py`, add text keys `aeroppk.need.<key>` / `aeroppk.why.<key>` for new file kinds, and tests with
real file excerpts. Nothing after the reader knows which drone it is.

## The window (wizard)

Six steps on the kit's window shell (`uikit.ToolWindow`): header (tool mark, title, the project, the chunk), step bar, the step, a message
banner, and the footer (Cancel / Close on the left, Back and the one primary button on the right: Next, Compute, Continue or Export).
`dialog.py` owns the shell and the shared services (jobs on a worker thread, confirmations, the bound chunk); each step is a `Screen` in its
own `screen_*.py` module and reads and writes one `state.WizardState`. Dark theme by default (Medium and Light too), no scroll bar (a table or
a list may scroll inside itself). The window opens at 1180 x 780 (design pixels), or at what the usable area of the screen (taskbar excluded)
allows, centred on the screen that holds the Metashape main window; it fits a 1366 x 768 laptop, it can be made bigger and never smaller than
its content needs (`tests_qt/run_qt_aeroppk_window.py` with made-up screens). Next stays grey until the step is valid, and the line next to
it says why. Every message ends in the banner of the window (the one place errors go, with Retry where it makes sense); a long task shows
its progress in the footer and Esc or Cancel stops it.

The window works on ONE chunk, the active chunk of the Metashape document, found again by its key (a key never changes, a label may, two
chunks may share one). The header holds the title with the project name under it and, at its right, "Active chunk:" and a small drop-down of the chunks of the project, each with its number of photos (the steps are in a row under the header); the chunk it
works on is selected. Picking another one makes it the active chunk of Metashape (`msapi.set_active_chunk`) and the window starts again:
a new state, the first step, the photos of the new chunk. If the active chunk changes in Metashape itself, the window finds out when it is
shown or activated and with a light timer (1.5 s, only while it is shown) and does the same. While a calculation or a download runs nothing
starts again: the drop-down is grey ("A calculation is running") and still names the chunk the work is for. When a job ends on a chunk that
is no longer the active one, the results on screen are kept (they belong to the chunk they were made for) until the active chunk changes
again. **Apply** and **Undo** act on that bound chunk, looked up by key at the click, and say so when it is gone from the project; the
backup of the positions is made per chunk key (older backups, known by the label, are read when no other chunk has that label). **Use chunk
photos** reads the photos of the bound chunk only. A flight folder picked by hand may hold photos that are not in the chunk: they are listed,
counted in a line under the list ("n photos are not in this chunk. They are not changed.") and left out of Apply. No document, no chunk,
one chunk, a chunk without photos, a chunk removed while the window is open, duplicate labels and an unsaved project are handled
(`tests/test_aeroppk_chunks.py`, `tests_qt/run_qt_aeroppk_chunks.py` with a fake document).

1. **Drone** (`screen_drone.py`). One question: which drone took the photos. The photos come from the bound chunk (**Use chunk photos**) or
   from a folder (**Folder...**); `flights.scan_folders` finds the flights and the reader that understands each one, `devices.detect` names the
   device from the EXIF model. A sentence says what was found. The **Device** card holds a drop-down (the readers of `drones/`, plus "Other")
   and the antenna: "Known" with a tick, or, when the reader says `antenna_known = False`, a field for the height of the antenna above the
   camera (`devices.parse_antenna_z`). **Only the vertical part is offered**: the horizontal part of the lever arm has to be rotated with the
   heading of each photo, which the tool does not have, and on a mapping drone it is a few centimetres at most. The M3E reader knows its
   antenna (the vector is in the events file), so nothing is asked for it. A drone that no reader accepts (`state.unsupported()`) gets a card
   with the message, the list of the supported drones, **Open the user group** (the link of the About window) and **Send an email** (a
   `mailto:` with the subject and a body to fill in, `screen_drone.mailto_url`); Next stays grey.
2. **Files** (`screen_files.py`). One row per flight in a `uikit.DataTable`: a tick, the name, the number of photos, the start (UTC), the
   duration and a status (*Ready*, *Missing: ...*, *Left out*). A flight that is unticked, or excluded from its right-click menu, stays in the
   list, greyed, and is never computed, exported or written (`WizardState.skip_flight`, the folders of the left-out flights are in
   `state.skipped`; `state.all_flights` is every flight read, `state.flights` the ones that count). At least one flight stays. A line says how
   many photos will be computed. Notes in orange: photos that are not in the bound chunk (listed, never changed), photos of the chunk that no
   flight covers, folders no reader understood, photos of a left-out flight (they keep the drone's own position), photos without a valid
   exposure time, a number of exposures that differs from the number of photos. A card shows the three files of the chosen flight (trigger
   file, rover observations, rover orbits); a missing one has a Browse button and an "i" that says what it is for (`aeroppk.why.*`).
3. **Mode** (`screen_mode.py`). Two cards in a `uikit.ChoiceGroup`: **A public station** (`state.MODE_SINGLE`, tagged "Recommended") and
   **My own base** (`state.MODE_OWN`). The choice decides which page the Base step shows (`state.set_mode`).
4. **Base** (`screen_base.py`). With a public station: the settings in one strip on top (networks, "1 s data only", the data window of 1 or 2
   hours around the flight, the search radius, 5 to 300 km, 50 by default), the stations in a list on the left (the nearest stations first),
   a map and a time axis in the middle, and the basket (**Bases**) on the right. Aero-PPK picks the nearest station that has data for the day
   by itself (`basket_finding`); a click on the map or in the list replaces it (the basket holds one station). A marker is coloured by its
   network (the state of the data is a small dot on it), a row carries the tag of its network, and its tooltip the frame, the receiver, the
   antenna and the files. A row shows the data rate, the constellations and the distance (read from the first bytes of the station file, no
   download). The **Networks** drop-down ticks the networks whose stations are shown (see "Base stations: the networks"): by default those
   that have a station inside the radius, with the number of their stations and their frame in the list; what the user ticks is kept in the
   setting `aeroppk.networks`. The frame of the station is under the basket. A **time axis** under the map shows the flight, the download
   window and the coverage of the station. The orbit satellite at the top of the basket is described in "Precise orbits". **Compute** (the
   primary button of the step) downloads the files that are missing (spinner, then a tick, or a cross with the reason; the files cover the
   flight plus 1 h or 2 h before and after it, the narrow window is tried when the server does not have all of that yet), then the Result
   step starts the calculation by itself (`wizard.pending_start`).
   *Your own base*: drop or browse the RINEX files (any order, any name, compressed or not, several observation files are merged); the
   brand of the receiver is recognised from the header; what is missing is listed with what it may cause (`ownbase.recognise`). Where the base
   is: **Base position** (WGS 84 latitude, longitude, ellipsoidal height of the antenna PHASE CENTRE, used as it is) or **Marker position**
   (WGS 84 latitude, longitude and ellipsoidal height of the ground point, pole height (a free number, 2.0 m proposed), and phase centre above
   the antenna base; both heights are asked and they add up: the phase centre is the marker lifted along the vertical by the sum). Only one of
   the two is open at a time. The **frame** of the position (ETRS89, NAD83(2011), WGS 84 / ITRF) is asked under both, as a required field that
   says so under itself until it is chosen: without it Replace writes nothing in the Advanced mode (the calculation does not need it). The
   position of the file header prefills both, with a visible warning that it is approximate. The phase centre height above the antenna base
   is prefilled from the IGS antenna file when it is already in the cache, otherwise it is asked. The solver then gets the phase centre with no
   antenna offset (`BaseData.antenna_delta = (0, 0, 0)`). Checked on a real flight: the same station files used as "own base" with the
   station coordinates gave camera positions within 0.5 cm (median) of the run through the network path.
5. **Result** (`screen_results.py`). While it runs (`runner.py`, a worker thread): the real steps with a progress bar and a plan of the
   photos that light up as the solver advances. Then one page: a sentence with the verdict (*Your PPK is good*, *Mostly good*, *This result is
   not reliable*; `summary.verdict`: good when no photo needs a look, not reliable when more than a quarter of the photos are), a thin bar cut
   in four and four counters: **Reliable** (a fixed position and no flag), **To check** (level warn), **Not reliable** (level bad), **No
   position** (`summary.level_counts`, the levels come from `combine.py`). Under them the base and its distance, the typical precision (the
   median over the photos that have a position, an "i" explains it), and one line of advice. The precision is the filter's own standard
   deviation per photo, a floor for the noise of a fixed solution and a term of about 1.2 mm per km of baseline (the numbers come from
   `combine.Policy`): an internal consistency figure, not a check against ground truth. It does not depend on the drone's own positions.
   Four buttons: **Details...** (`screen_details.py`: the figures, the spread of the precision, the bases and the facts of the selected one,
   the plan coloured by level, the processing settings that were used, the comparison with the drone's own position as information),
   **Try another base** (back to the Base step, the result stays until the next calculation), **Store this solution** (`solutions.py`: a
   snapshot of the camera positions in memory, keyed by photo path, named after the bases, the mode and the time) and **Compare...**
   (`screen_compare.py`: the stored solutions and the current one, the horizontal and vertical differences over the photos they share, the
   number of photos that differ by more than 5 cm, and one sentence). Stored solutions live in the window and are gone when it closes. The
   limits of a good figure are constants (`summary.GOOD_FIX_PCT`, `summary.GOOD_ERROR_M`), not settings. A result that is not good asks once
   (`aeroppk.result.bad_ask`) before the user goes on.
6. **Export** (`screen_export.py`). Two cards. **Files**: the PDF report, the CSV of the photo positions, the KML and a text table, each a
   toggle that shows its file name (`outputs.base_file_name`: date, kind, project) and, once written, a tick and the name. The folder is
   `Outputs/PPK` next to the project (`Outputs` is the suite's folder, found with the helper of Aero-Exports; next to the photos when the
   project is not saved), a file that exists is never overwritten (`_v2`). The PDF asks once whether to include the table of all photos (a
   long report). **Metashape**: *Replace the photo positions in chunk ...*, ticked by default. The positions that the export would write
   (`ExportScreen._items`; a photo with no position is left out) go into the bound chunk after a backup (`backup.py`, per chunk key), and
   **Undo** puts the backup back. The EXIF rewrite (`aeroppk.out.exif*` in lang/en.py) was taken off the screen on purpose: it cannot be
   undone and the positions of Metashape are the safe way to use the result. The primary button does everything that is ticked, files first,
   then the positions. A result that is not good asks once before anything is written (`aeroppk.export.weak_ask`) unless the Result step
   already asked.

### What Replace writes

`frames.simple_frame` is the rule of the Simple mode, the only one the user meets first: the positions go into a chunk in WGS 84 as they
are, in the frame of the base, as a DJI RTK drone does. A chunk in any other system gets the question "Continue in Advanced?"
(`aeroppk.apply_advanced_ask`); in Advanced, `frames.source_frame` declares the frame of the base (`NETWORK_FRAMES`, or the frame the user
chose for an own base) and Metashape converts the positions to the system of the chunk, which can fail where its height system needs a
geoid grid it does not have (the message says so and nothing is written for those photos). A chunk with no coordinate system, or a family
of frames Metashape cannot reach without an epoch, is refused with the reason (`frame.problem`). Every Metashape call is in `msapi.py`.

## What the files of the Mavic 3 Enterprise are (checked on 30 real flights, 2023 to 2026)

| file | content |
|---|---|
| `<folder>_Timestamp.MRK` | one line per photo: number, GPS second of week, `[GPS week]`, `N,E,V` offsets in mm, lat, lon, ellipsoidal height, three sigmas, status (16 single, 50 RTK fix) |
| `<folder>_PPKOBS.obs` | RINEX 3.05 observations of the drone (5 Hz, GPS + GLONASS + Galileo + BeiDou; header padded to 16 KB, position 0 0 0) |
| `<folder>_PPKNAV.nav` | RINEX 3 broadcast ephemerides |
| `<folder>_PPKRAW.bin` | raw RTCM 3 log; `convbin -r rtcm3` rebuilds an `.obs` and `.nav` from it (1773 epochs on the flight tried; not used to solve) |

* **The MRK time is GPS time, not UTC.** Measured: the 214 events of a 2023 flight match the REDtoolbox event times to 1 ms, and
  fall inside the RINEX window written in GPS time. With 18 s of leap seconds, UTC would put every photo 180 m away at 10 m/s.
* **Lever arm.** Measured on 280 photos of an RTK fixed flight: camera = antenna position + (N, E, V) with V positive **down**.
  The scatter of PPK minus the drone's position drops to 2 to 6 mm with `+offset` (2.6 cm horizontal without, 5 cm with the
  opposite sign). So the MRK lat / lon / height are the **camera** positions, not the antenna's.
* Photo names may carry a waypoint tag after the camera letter (`DJI_<time>_<n>_V_point12.JPG`, mission planners do this): the reader accepts it.
* RTK status 16 (single) flights have a 1 to 2 m onboard position: only status 50 photos are used as a comparison.

## Coordinate frames (the classic trap)

* RGP station coordinates are **RGF93 v2b** (an ETRS89 realisation), in decimal-looking angles `DDD.MMSSsssss` in the RGP csv (read as
  decimal degrees, a point lands 13 km away: `geo.packed_dms_to_deg`). The RINEX header position of an RGP file equals the published
  coordinates (checked on one station).
* A rover position computed from a base inherits the frame of the base. AeroPPK therefore returns positions in **RGF93 / ETRS89
  (the frame of the bases)** and says so in the window and the report. That is what a project in RGF93 / Lambert 93 needs, and
  Metashape converts them to the project system (`EPSG::4258` as the source in `msapi.set_camera_positions`).
* ITRF / WGS 84 of the day differs from ETRS89 by a lot: measured with the embedded PROJ 9.8.1 on one station, ETRS89 -> ITRF2014 moves
  the point by (-0.475, +0.656, +0.401) m, **0.90 m** in 3D at 2023.96 and 0.98 m at 2026.85 (`frames.py`, round trip under 0.2 mm).
  `frames.convert_xyz` is the tool for a base given in ITRF (a PPP result, an IGS station) before it is mixed with RGP stations.
  The window offers stations of several networks, each in the frame of its network (table in "Base stations: the networks"). Nothing is
  converted: the result is in the frame of the base. RGF93 v2b and ETRF2000 are both ETRS89 and agree to a few centimetres (measured 5, 7
  and 16 mm on the three stations that RGP and EPN both list); IGS20 of the IGS and of Geoscience Australia is one group.
* Measured consequence: on the 9 RTK fixed flights of 16/12/2023 taken together and on 4 of the 6 flights of set B (a second set: one site, three days of 2026, all with the drone's RTK fixed), the drone's own RTK position and
  the PPK (RGP coordinates) agree within 1 to 2 cm horizontally, so the network RTK of those days was already in RGF93, not in "WGS 84 at the date". The two July 2026 flights are
  1.95 m away (N +1.71 m, E +0.93 m) with a scatter of 1.5 cm, while two independent stations agree: the drone's real-time
  reference was wrong that day (cause not known to us), the PPK is the one to use. The window prints this warning (`onboard_offset`).
* Heights are ellipsoidal (as in the MRK). No geoid is applied: Metashape takes the project's own height system.

## Base stations: the networks

A network is a `stations.provider.Provider`: metadata, a station snapshot, and `products()`, the file sets that could cover a flight, best
first (the server decides with `probe`). The basket, the map, the download and the solver do not know which network a station comes from.
Station lists are snapshots made from the networks' own coordinate solutions (`stations/build_catalogs.py`, run by hand), stored next to the
code with their date, source and licence. Only networks whose RINEX files can be downloaded **without an account, a payment or a key**
are offered. Every file below was downloaded and read for real on 08/11/2026 (`tests_qt/run_qt_aeroppk_networks_live.py`, not run by the
test suite).

| network | server (anonymous) | frame of the positions | stations in the snapshot | licence |
|---|---|---|---|---|
| **RGP** (IGN, France) | `https://rgpdata.ign.fr/pub` | RGF93 v2b (ETRS89), fixed | 781 (57 overseas are not offered) | Licence Ouverte Etalab 2.0 |
| **EPN** (EUREF, Europe) | mirror of the BKG, `https://igs.bkg.bund.de/root_ftp/EUREF` | ETRF2000, moved to the date with the EPN velocity | 240 (the stations carried by that mirror) | CC BY 4.0 unless the operator states another open licence |
| **IGS** (worldwide) | mirror of the BKG, `https://igs.bkg.bund.de/root_ftp/IGS` | IGS20 (ITRF2020), moved to the date with the IGc20 velocity | 184 (reference frame stations with an open solution and a daily file) | open, credit the IGS and the operators |
| **NOAA CORS** (USA) | AWS open data bucket `https://noaa-cors-pds.s3.amazonaws.com` | NAD 83 (2011), epoch 2010.0, used as published (the frame is fixed to the plate) | 1709 | free of charge, credit NOAA / NGS |
| **Geoscience Australia** (AUSCORS, ARGN) | `https://ga-gnss-data-rinex-v1.s3.amazonaws.com/public` | IGS20 (ITRF2020), moved to the date with a velocity | 716 | CC BY 3.0 Australia |

Where the coordinates come from (the networks' own public files, nothing else):

* **RGP**: `coordRGP.php?t=csv` (angles written DDD.MMSSsss, see above).
* **EPN**: the EUREF reference frame solution, `EUR0OPSSNX_..._SOL_ETRF2000.SSC` of the latest release (C2415), positions at 2020.0 and
  velocities in ETRF2000, plus its `_short.SSC` for the stations of less than 3 years (position only). The last solution of a station is
  used when it ends within half a year of the end of the file. Names from the EPN header SINEX.
* **IGS**: `IGc20.ssc` (positions and velocities at 2020.0 in IGS20) with `soln_IGc20.snx` to take the solution that has no end date.
  Stations whose solution ended after the release (a recent antenna change) are left out: their coordinates would be wrong.
* **NOAA CORS**: one page per station (`coord/coord_20/ssss_20.coord.txt`), the first NAD 83 (2011) position (the antenna reference
  point).
* **Geoscience Australia**: the APREF weekly solutions `AUS24407.SNX` (week 2440) and `AUS23367.SNX` (week 2336): positions in IGS20 at
  the epoch of the week 2440; the velocity is the difference of the two weeks over 2.1 years, kept when its horizontal part is within
  12 mm/yr of the median of the 8 nearest stations (an antenna change makes a jump), else replaced by that median, and the vertical part is
  always the median of the neighbours. The 16 stations that are also IGS reference stations take the IGS solution instead. **Not GDA2020**:
  Aero-PPK converts nothing, so the Australian legal datum is not produced; the user converts the positions with the official
  transformation. Measured on 4 stations that are in both solutions, the weekly solution with the neighbour velocity was 1.5 to 5.7 cm
  from the IGS one (ALIC 2.2, CEDU 3.2, COCO 5.7, DARW 1.5 cm): AUSCORS positions are good to a few centimetres, not to a millimetre.

Cross-check of the whole chain with the position written in the RINEX header of the downloaded file (header positions are approximate,
decimetre level, and in the frame of the data centre's own habit): for the EPN station AUBG the header is 0.84 m from the ETRF2000 snapshot
and 0.13 m from it once moved to ITRF2020 by the embedded PROJ; for the IGS station ZIM2 the header is 0.98 m from the IGS20 snapshot and
0.10 m from it once moved to ETRS89. Both are what the 0.9 m frame difference of 2026 predicts, to the precision of a header position.

**Files tried, best first** (`probe` asks the server with HEAD requests, the first set that exists wins, the nearest 1 Hz first):

| network | 1 s | 30 s | navigation | sampling of the station |
|---|---|---|---|---|
| RGP | RINEX 3 15 min, RINEX 2 hourly | RINEX 3 hourly and daily, RINEX 2 daily | per station and constellation | in the name |
| EPN | `highrate/YYYY/DDD/<hour letter>/`, 15 min files, RINEX 3 | `obs/YYYY/DDD/` daily | station mixed file, else per constellation, else the worldwide `BRDC00IGS` file | in the name |
| IGS | same layout under `IGS/` | same | same | in the name |
| NOAA CORS | none | `rinex/YYYY/DDD/ssss/ssssDDD0.YYd.gz`, one RINEX 2 file a day | `brdcDDD0.YYn.gz` (GPS) and `.YYg.gz` (GLONASS) of the day, no Galileo | **only the header says** (30 s, 15 s, 5 s or 1 s): the survey reads it |
| Geoscience Australia | `highrate/YYYY/DDD/HH/`, 15 min | `hourly/YYYY/DDD/HH/`, `daily/YYYY/DDD/` | per station `..._01D_MN` or per constellation, else `BRDC00IGS` | in the name |

The long names carry a source letter, `R` (receiver files) or `S` (files cut from a stream); it is not the same for every station, the
snapshot keeps the one seen on the day it was made and the other one is tried too. Retention measured with one station per network (a 20
minute window, which products exist): RGP 1 Hz at every age tried up to 800 days; GA 1 Hz up to 800 days; EPN and IGS 1 Hz up to at least
120 days and daily files only at 400 days (the `highrate` folders of the BKG seem to keep the current year: the 2025 and 2024 ones answered 404); NOAA CORS daily files at every age. A
flight of today has the 1 Hz files within hours on the BKG mirror and GA, and the daily files only the next day or later.

**What the solver needed, found by running it**

* 15 minute RINEX 3 pieces of one station are not always the same: at 09:00 UTC the receiver of one EPN station wrote the GPS codes in
  another order. `rinexprep.merge_obs` refused it (it still refuses RINEX 2 pieces that disagree). RINEX 3 pieces are now rewritten on
  the union of the codes (first order first, a missing value is blank), the header lists them, and events are copied as they are. Checked
  on 11 real pieces (9,900 epochs, the receiver changed order at 09:00): `convbin` read all 9,900 epochs.
* 15 minute pieces have no `INTERVAL` line: the rate of the file set is used.
* **Compact RINEX 3.1** (and RINEX 4.02 observations, written by the newest receivers; seen on the IGS station MAL2) is refused by the
  embedded `crx2rnx` (it reads up to Compact RINEX 3.0). The survey reads the first line of the file and does not offer such a station
  (the row says so); `unpack` gives a sentence if one gets through. A decoder of 3.1 is not written and no other `crx2rnx` is downloaded.
  Of 36 files sampled at random on the three RINEX 3 networks all were 3.0.
* Positions move with the date: `BaseData.xyz` is `Station.xyz_at(start of the flight)`, so a velocity matters only where the frame
  drifts (IGS20, EPN, GA); NAD 83 (2011) and RGF93 v2b are used as published.
* A station that two networks offer (the same code less than 300 m apart) is listed once, from the first network in the order RGP, EPN,
  NOAA CORS, GA, IGS; a different station with the same code is renamed `CODE~network`. The counts in the drop-down are of that merged
  list, so IGS counts 0 where all its stations are also EPN ones.

**Live check, 08/11/2026** (sample sites chosen by the script, the nearest station of the snapshot that has data, a 25 minute flight
window with the 1 h margin on each side, the files downloaded, unpacked with the embedded `crx2rnx`, prepared by `rinex` / `rinexprep`, and
read by the embedded `convbin`):

| network | station (distance to the sample site) | file set used | rate | systems | download and unpack | header position against the snapshot | `convbin` |
|---|---|---|---|---|---|---|---|
| RGP | RNNE (34 km) | RINEX 2 hourly, 4 files, 8 navigation files | 1 s | GPS GLONASS | 5 s | 0.00 m | 14,400 epochs read |
| EPN | AUBG (62 km) | RINEX 3 of 15 min, 11 files merged, `BRDC00IGS` | 1 s | GPS GLONASS Galileo BeiDou | 20 s | 0.84 m (0.13 m after the frame move) | 9,900 epochs read |
| IGS | ZIM2 (228 km) | RINEX 3 of 15 min, 11 files merged, `BRDC00IGS` | 1 s | GPS GLONASS Galileo BeiDou | 14 s | 0.98 m (0.10 m after the frame move) | 9,900 epochs read |
| NOAA CORS | P037 (65 km) | RINEX 2 daily (`.26d.gz`), GPS and GLONASS navigation | 30 s (header) | GPS GLONASS Galileo | 3 s | 0.00 m | 2,880 epochs read |
| Geoscience Australia | UNW2 (4 km) | RINEX 3.04 daily, navigation per constellation | 30 s | GPS GLONASS Galileo BeiDou QZSS | 35 s | 0.39 m | 2,880 epochs read |

The header positions of RGP and NOAA CORS files are the official coordinates (0.00 m: the snapshot of those two networks is the position
the files carry); the others are approximate (see above). Every line passed all the checks of the script: survey, files present, RINEX read
by `rinex`, the data cover the flight window, a navigation file with GPS ephemerides, `convbin` reads the file.

**Not offered**

* **CDDIS** (the main IGS data centre): `https://cddis.nasa.gov/archive/gnss/data/daily/` answers HTTP 401, it needs an Earthdata login.
* **igs.ign.fr** (the IGN mirror of the IGS): ports 80 and 443 did not answer from the machine of this work (timeout after 21 s); not used.
  The BKG mirror gives the same files.
* **Canada, NRCan CACS** (`cacsa.nrcan.gc.ca/gps/data/gpsdata/YYDDD/YYd/`, Open Government Licence - Canada): the RINEX 3 daily files
  download (HTTP 200, 2.6 MB for ACHT). Not offered because the station coordinates published are a list with 4 decimals of degree
  (10 m) and old site logs with centimetre values in an "ITRF" of no stated epoch: not a position good enough for a base, and the frame is
  not written down. A coordinate solution (NRCan publishes one in its own tools, not as a file this work found) would add it.
* **New Zealand, GeoNet** (`data.geonet.org.nz/gnss/rinex/...`, daily `.rnx.gz`, `rinex1Hz`, `rinexhourly`): the list and a file answer. Not
  offered for the same reason as Canada (site logs and metadata only), and because the New Zealand datum needs its deformation model.
  The licence text was not checked.
* **Spain, IGN ERGNSS** (`datos-geodesia.ign.es/ERGNSS/`, daily 30 s and hourly 1 s RINEX 3, an ACOR daily file answers HTTP 200): the
  coordinates are in PDF sheets and approximate site logs in an unstated ITRF. The Spanish stations that are in the EPN are offered
  through it (26 in the snapshot).
* **Italy RING (INGV)**: `ftp://webring.gm.ingv.it` timed out from here and the web site was not explored further; the Italian EPN
  stations are offered through the EPN (50 in the snapshot). **Switzerland (swisstopo)**: only the landing page of the open data portal
  was looked at; ZIM2 and the other Swiss IGS / EPN stations are offered through the IGS. **Portugal, Belgium, Japan, Finland, Norway,
  Sweden**: not tested (Finland, Norway and Sweden hosts did not answer or did not resolve from here; no claim is made about Japan's
  terms).

### RGP (IGN, France)

Anonymous HTTPS on `https://rgpdata.ign.fr/pub`, Licence Ouverte. Measured: 781 stations in the csv (57 overseas ones are not offered:
other frames). RINEX 3 at 1 Hz exists only for the last days (found 7 days back, gone at 26 days); RINEX 2 at 1 Hz hourly exists for
years (2022 to 2026 found); RINEX 3 at 30 s hourly and daily for every day tried; RINEX 2 at 30 s daily only. The planner tries,
best first: 1 s RINEX 3, 1 s RINEX 2, 30 s RINEX 3 hourly, 30 s RINEX 3 daily, 30 s RINEX 2 daily. Stations without data that day are
shown as such (one station had none on two of the days tested, found by the probe). The constellations and the receiver of a station are
read from the first 32 KB of its file (HTTP Range), not from a download. Orbits come from the broadcast files of the base and of the drone.
Hatanaka files go through `crx2rnx`, `.Z` files through a pure Python decoder (checked: a 17 MB hourly file decoded and passed to
crx2rnx; the unit test uses a real 4.7 KB file).

## Precise orbits

The solver uses the **broadcast orbits** only (`pos1-sateph = brdc`). What is built is the look at the free servers that tells, for the
flight day, whether precise orbits (SP3) exist: `stations/orbits.py`, and a small satellite at the top right of the basket in the Base
step: white = broadcast only, blue = rapid published, green = final published. Its tooltip names the type, the source and the usual delay,
and says that the solution still uses the broadcast orbits. Nothing is downloaded and nothing in the solver reads the answer
(`orbits.precise_available(days)` is the hook for the day it does). The checks were made on 08/11/2026 around 13:30 UTC, from one machine,
with plain `HEAD` requests, no account, and the first lines of a file of each source read to confirm it is an SP3 file (`#cP`/`#dP`/`#dV`,
frame IGS20, 5 or 15 minute epochs, 1.5 to 2.7 MB unpacked for the 5 minute files).

**Open, anonymous HTTPS, answer a `HEAD`** (the last four rows give the delay seen as the `Last-Modified` time of the files, counted from
the end of the product day, so on one day only):

| source | directory (GPS week `WWWW`) | rapid | final | delay seen |
|---|---|---|---|---|
| ESA / ESOC | `navigation-office.esa.int/products/gnss-products/WWWW/` | `ESA0OPSRAP_YYYYDDD0000_01D_05M_ORB.SP3.gz` (0.76 MB) | `ESA0OPSFIN_..._01D_05M_ORB.SP3.gz` (0.9 MB) | rapid 1.8 h, final 3.65 days (the same for four days tested) |
| CODE (AIUB) | `www.aiub.unibe.ch/download/CODE/` (the old `ftp.aiub.unibe.ch` does not resolve any more, also from a public DNS) | `COD0OPSRAP_YYYYDDD0000_01D_05M_ORB.SP3` (1.5 MB, not zipped) in the top directory, kept about a week (found 5 days back, gone at 8) | `CODE/YYYY/COD0OPSFIN_..._01D_05M_ORB.SP3.gz` (0.65 MB); before 27/11/2022 `CODE/YYYY/CODWWWWD.EPH.Z` (130 KB) | rapid 7 to 14 h, final 4 to 12 days (published by the week) |
| GFZ | `isdc-data.gfz.de/gnss/products/{rapid,final}/wWWWW/` | `GFZ0OPSRAP_..._01D_05M_ORB.SP3.gz` (0.67 MB) | `GFZ0OPSFIN_..._01D_15M_ORB.SP3.gz` (0.65 MB) | rapid 9 h, final 4 to 11 days (published by the week) |
| IGS, BKG mirror | `igs.bkg.bund.de/root_ftp/IGS/products/WWWW/` | `IGS0OPSRAP_..._01D_15M_ORB.SP3.gz` (0.1 MB) | `IGS0OPSFIN_..._01D_15M_ORB.SP3.gz`, **but only up to GPS week 2431** | rapid 17 h |

* Clocks are next to the orbits with the same names (`..._30S_CLK.CLK.gz`, `..._05M_CLK.CLK.gz` at BKG); the probe looks at the orbit file only.
* Ultra-rapid exists on the same servers (BKG `IGS0OPSULT_YYYYDDDHH00_02D_15M_ORB.SP3.gz` every 6 hours, the 06:00 file there at 09:00;
  ESA `ESA0OPSULT_...`; CODE `COD0OPSULT_...` and `COD0OPSULT.SP3`): `HEAD` answered 200 for one file of each. Not used.
* Dates checked, all with a plain `HEAD`: rapid for yesterday and 2 days back (ESA, CODE, GFZ, BKG; BKG not for yesterday yet), 5 days back (ESA,
  CODE, BKG; GFZ had no file for that day), 8, 15 and 21 days back (ESA, GFZ, BKG; CODE has dropped its rapid file by then). GFZ keeps its rapid files for years (found for 2021-06, 2022-06, 2022-12, 2024-06) and BKG
  back to 2022-12; ESA's 2024 rapid file has a 15 minute step in its name, which the probe does not try, because by then a final file exists.
  Final: 3 weeks back (ESA, CODE, GFZ), 2024-06 and 2022-12 (ESA, CODE, GFZ, BKG); with the GPS week names `CODWWWWD.EPH.Z` for 2015,
  2017, 2019, 2020, 2021, 2022-06 and 2022-11-26 (the last day before the long names; `CODWWWWD.EPH_M` answers 404 on the new host for
  every date), `esaWWWWD.sp3.Z` for 2022-06 and 2022-11-26 only (404 for 2019 to 2021), GFZ long names for 2021 and 2022-06 (404 for 2019
  and 2020).
* The IGS final combination is **not** on the BKG mirror for GPS weeks 2432 to 2441 (the directory lists rapid and ultra-rapid only), and
  `igsWWWWD.sp3.Z` / `igrWWWWD.sp3.Z` are not there at all (404 for 2021, 2022, 2024). The IGS final of week 2441 appeared on 05/11/2026
  (12 to 18 days after its days) on the sources below that cannot be probed with a `HEAD`, so the green state may come from ESA, CODE or GFZ
  days before the IGS final exists. An ESA or CODE final is a final orbit of an analysis centre, not the IGS combination.

**Not used, and why**

* **CDDIS** (`cddis.nasa.gov/archive/gnss/products/WWWW/`): the directory answers 401. A `HEAD` of a file answers 200 with
  `application/x-gzip`, but the `GET` goes to the Earthdata login page (an HTML page, status 200). A `HEAD` probe on CDDIS would say "there"
  for a file nobody can fetch without an account, so it is excluded, and the probe also refuses a 200 that is HTML or smaller than 20 KB.
* **IGN** `igs.ign.fr`: HTTPS times out again. Anonymous FTP works (`ftp://igs.ign.fr/pub/igs/products/WWWW/`) and holds
  IGS rapid and final (`IGS0OPSFIN`, one file per day, day 291 on 07/11/2026), the products of CODE, ESA, GFZ and others, and the GPS week
  names `igrWWWWD.sp3.Z`, `igsWWWWD.sp3.Z` (size answered for 2015-03-07 and 2021-06-12; not for 2024).
* **Wuhan** `igs.gnsswhu.cn`: HTTP port 80 and 443 refuse the connection. Anonymous FTP works (`/pub/gps/products/WWWW/`) with `IGS0OPSFIN`
  (week 2441 complete on 05/11/2026 19:29 UTC), `IGS0OPSRAP`, the Wuhan MGEX products, and `igsWWWWD.sp3.Z` for 2015 and 2021.
* **GFZ FTP** `ftp.gfz-potsdam.de/pub/GNSS/products/{final,rapid}/wWWWW/` is anonymous too and holds the same files as the HTTPS site.
* **JPL** (`sideshow.jpl.nasa.gov`): the TLS certificate was not accepted from this machine; not explored. **NRCan** (`cacsa.nrcan.gc.ca/gps/
  products/{final,rapid}/WWWW/`, Open Government Licence - Canada): the listing answers, the week 2441 final directory held a `glonass/`
  folder only; not explored. **KASI** (`nfs.kasi.re.kr`): FTP refused, HTTPS timed out.
* FTP is left out of the probe because `HEAD` is the check that is the same for every source and cheap; adding the IGN and Wuhan FTP
  (`SIZE`) would make the IGS final visible earlier than ESA, CODE or GFZ never will, at the price of a second protocol.

**How the probe decides** (`stations/orbits.py`, never raises, all failures give broadcast):

* a day that is not over has no rapid orbit: nothing is asked; a flight that crosses midnight takes the lower level of its two days;
* from the fourth day on the final files are asked first (ESA, CODE, GFZ, the IGS at BKG; the GPS week names for days before 27/11/2022),
  then the rapid ones (ESA, CODE, GFZ, IGS at BKG), one `HEAD` each, 6 s timeout, 25 s for the whole day; the first file that answers 200
  as a data file ends the search; a host that fails to answer is not asked again for that day;
* 404 and 410 are "not there"; 401, 403 and 5xx are "unknown": the answer is kept 2 minutes only. A final answer is kept for the session,
  a rapid or broadcast one 30 minutes (a rapid orbit becomes final within days); the cache is shared by threads;
* the satellite is white with the tooltip "Broadcast orbits only." when nothing could be reached.

Not verified: that the SP3 and clock files of these sources are accepted by the solver (they are not used), the delays on other days (one
day of observation, not statistics; the weekly batches of CODE and GFZ make them vary), the length of the CODE rapid window (three
probes), the behaviour behind a company proxy or a firewall that blocks one of the four hosts (the other three are asked, the answer is
then "unknown"), and the satellite inside a running Metashape (colours under its theme).

## PPK engine: the measurements

Engine = **RTKLIB-EX 2.5.1** (rtklibexplorer "demo5" fork), BSD 2-clause (`aerotools/vendor/rtklib/LICENSE.txt`), `rnx2rtkp.exe` 1.4 MB.
Compared on the 9 RTK fixed M3E flights of 16/12/2023 (3894 photos, base 0.4 km away, 1 s RINEX 2 with GPS, GLONASS, Galileo),
against the drone's own RTK position (lever arm applied; "bad" = a fixed photo more than 10 cm horizontally or 15 cm vertically off
the drone; robust sigma = 1.48 x MAD of the fixed photos):

| run | fixed photos | bad fixes | sigma N / E / U (cm) | note |
|---|---|---|---|---|
| EX 2.5.1, GPS + GLONASS, dynamics off | 89.7 % | 47 | 1.1 / 0.8 / 1.6 | |
| **EX 2.5.1, + Galileo (all common systems)** | **98.8 %** | **15** | 0.7 / 0.4 / 1.0 | best on this set |
| EX 2.5.1, GPS only | 95.0 % | 108 | 0.7 / 0.5 / 1.2 | |
| EX 2.5.1, GPS + GLONASS, **vehicle dynamics on** (the f9p example config) | 69.8 % | 0 | 0.8 / 0.4 / 0.7 | rejected: far fewer fixes |
| continuous ambiguity resolution | 70.9 % | 625 | 2.4 / 2.0 / 3.7 | rejected |
| forward only | 95.9 % | 417 | 1.2 / 0.9 / 2.0 | rejected |
| ratio threshold 2 / 5 | 66.7 % / 94.9 % | 124 / 212 | | default 3 kept |
| elevation mask 10 / 20 deg | 97.6 % / 91.6 % | 28 / 17 | | default 15 kept |
| EX 2.5.0 (REDtoolbox CLI), demo5 b34f.1 (REDtoolbox GUI) | 89.7 % / 89.8 % | 47 / 53 | same | same family: same result as 2.5.1 |

* **Not measured: vanilla RTKLIB 2.4.3** (no binary on the machine, and downloading third party executables was not allowed for this
  work). The recommendation of EX / demo5 rests on the table above and on the maintained status (release 22/06/2026), not on a
  head to head against 2.4.3. No other open source PPK engine was tried (none found that does this job with RINEX input).
* The same drone file against the **REDtoolbox** result of 14/12/2023 (214 photos, no onboard RTK: status 16, GPS only, RINEX header
  position of the base): for the 206 photos both call fixed, median differences 2 mm N, 2 mm E, 9 mm U, 95 % under 10 / 8 / 38 mm. The
  differences are in the first 40 s after take-off (we say float where REDtoolbox says fix) and on 3 photos at 0.78 m, one of the two is
  wrong, not decided without a truth.
* **A wrong integer fix exists and is the real danger**: on one flight of set B (31 photos) a station at 20 km gave 100 % fix with
  a position 1.5 m N, 1.2 m E away from the 4 km station and from the drone; on flight 1 of the same day it was right. Only the
  comparison between bases (or with the drone's RTK) shows it. In the Result step, **Store this solution** and **Compare...** put two
  bases side by side on the same photos.
* **Constellations: data dependent, so every flight is run with several profiles** (all common systems, GPS + GLONASS, GPS alone) and the
  best fixed share wins, the runner-up cross-checks it. Measured: with a Trimble Alloy base (2026) every run that includes
  GLONASS fails completely (no usable solution) while GPS alone fixes 100 %; with a Leica GR50 base (2023) adding Galileo
  raised the fixed share from 90 % to 99 %. Galileo made one 2023 flight slightly worse (178 against 211 fixed photos of 214).
* **Sampling of the base**: on the 6 flights of set B 30 s data gave 82.7 % and 87.9 % fixed photos on two flights where 1 Hz gave
  100 %, and one flight had 3.6 m 95 % error on photos called fixed with the 30 s file. 30 s files work (a RINEX 3 hourly 30 s file of
  a 2023 base gave 100 % fix on the 2023 flight) but are flagged "coarse" in the window and the report.
* Run time: 6 to 20 s per flight (5 minutes to 14 minutes of flight, three profiles), plus the downloads (0.5 to 1.5 MB per file).

## Absolute accuracy

**Ground control points were NOT used.** A photo position can only be checked against ground points after a photogrammetric adjustment
(that needs the model, outside this tool). What is established is internal consistency (bases against bases), agreement with the drone's own
RTK, and agreement with REDtoolbox on the 2023 flight. The absolute accuracy of the positions is not independently verified.

## Optional base antenna correction (ANTEX)

The antenna model of the base is written in the RINEX header (`ANT # / TYPE`). `Options.antex_file` (module `antex.py`) gives RTKLIB the IGS
calibration file `igs20.atx` (free, https://files.igs.org/pub/station/general/igs20.atx, 61.6 MB, downloaded once into the cache on demand, never
shipped) and the receiver antenna correction is switched on for the base (`pos1-posopt2`, `file-rcvantfile`, `ant2-anttype`). A base antenna that
is not in the file (checked by `antex.lookup`) is left uncorrected with the warning `antenna_unknown`. The antenna of the drone is not in the IGS
file (no calibration is published for it), so only the base side is corrected. It is **off by default**, switched on by the hidden setting
`aeroppk.base_antenna_correction`. A second file can be read next to it, the NGS `ngs20.atx`
(https://www.ngs.noaa.gov/ANTCAL/, 70 MB, 1167 receiver antenna types, downloaded the same way into the cache on demand by the hidden setting
`aeroppk.base_antenna_extra` = `ngs20`): for each base the first file that holds the antenna wins (`antex.choose`), `igs20.atx` first.
Checked: the file downloads (`LoadFile?file=ngs20.atx`, HTTP 200) and is read by the existing reader; `TRM59800.00 SCIT`, `TRM115000.00`,
`LEIAR25.R3 LEIT` and `SEPCHOKE_B3E6` are in it. The effect on a flight was not measured.

Measured on 11 flights of two sites (1 Hz bases 1 to 35 km away, Trimble Zephyr type antennas `TRM55971.00` and `TRM115000.00`, one Septentrio
`TWIVC6050 SCIS`), same bases and settings with and without the correction:

* the camera heights move by **+5.4 to +5.8 cm** (5 to 95 % per flight: +4.1 to +7.0 cm); horizontal change under 0.3 cm; the two Zephyr models give the same shift (+5.3 to +6.6 cm);
  the Septentrio base gives +12.4 cm; far bases with a partly wrong fix move by +7 to +65 cm (noise, not signal);
* the scatter against the drone's own RTK does not change (95 % vertical 1.1 to 4.9 cm both ways) and the 2 to 3.5 cm height difference between
  two bases of different antennas stays the same, so the correction does not reconcile bases with each other;
* the height bias against the drone's own RTK moves with it: flights of one day that sat at -6.7 and -7.7 cm go to -0.7 and -2.3 cm,
  but flights of two other days that sat at -1.3 to +2.1 cm go to +4.4 to +7.7 cm, and a third site goes from -3.7..-1.8 to +1.6..+3.8 cm. The drone's
  real-time reference is not a constant truth, so these data do not say which one is right. Applying the satellite antenna model as well
  changed nothing at the 1 mm level on the flights tried (expected for short baselines; whether RTKLIB really read the satellite entries was not checked).

The height difference of -6 to -8 cm that the plain run shows against the drone on some days (and on the 2023 flights, not re-run with the
correction) is the same size as this correction (+5.6 cm) and vanishes on the day tested, but the correction makes other days worse. It is not
explained as a general bias and not a reason to turn the option on by default.

## Map

No QtWebEngine in Metashape's Python, so the map is painted with QPainter (`mapview.py`): OpenStreetMap raster tiles with a disk cache (7 days),
at most 2 connections, no prefetch, an identifying User-Agent and the attribution, switchable to a plain background when offline; the site,
photo footprint, stations (green: 1 s data, amber: coarser, grey: unknown or none), numbered choices and distance circles (10, 20, 30 km by default,
editable). Tiles were fetched for real in the window test; the OSM policy was followed by construction, not audited.

## Report

PDF in the style of the Aero-GCP report (`report.py`): cover with a verdict, an orange box saying the station data come from an external public
source (name, licence, files, sampling, constellations, distance), files checklist, quality figures, comparison with the drone, per-photo
table, method and limits (frame, time, engine). CSV with one row per photo.
Texts come from the catalog (`lang/en.py`, keys `aeroppk.report.*`).

## What was verified, and what was not

Verified: the whole chain on real files through the real window in Metashape's own Python (not Metashape itself): survey of the RGP server,
automatic choice of the station, downloads, unpacking, three RTKLIB runs per flight, the camera positions, the comparison with the drone,
the PDF and the CSV; the six steps with made-up servers and a fake document (`tests_qt/run_qt_aeroppk_steps.py`,
`tests_qt/run_qt_aeroppk_results.py`, `tests/test_aeroppk_wizard_logic.py`, `tests/test_aeroppk_drone_screen.py`, `tests/test_aeroppk_modes.py`,
`tests/test_aeroppk_solutions.py`); the replacement of the photo positions and its Undo against a fake chunk; 1500+ unit tests; Python 3.9
syntax of every file.
Verified for the networks beyond RGP (08/11/2026): the live check above for each of the five networks (survey, download, unpack, prepare,
`convbin`), the readers of the coordinate files on excerpts of the real files (unit tests), the window with several networks in Metashape's
Python without Metashape (`tests_qt/run_qt_aeroppk_baselist.py`: filter, markers, rows, frame warning, screenshot).
Verified for the orbit satellite: the real servers (08/11/2026, see Precise orbits), unit tests with a fake server (`tests/test_aeroppk_orbits.py`), and the three
colours, the tooltips, the offline state, a flight over midnight and a late answer in the window (`tests_qt/run_qt_aeroppk_orbits.py`, fake servers).
Verified for the reach of the mouse (`tests_qt/run_qt_aeroppk_hittest.py`, Metashape's Python, made-up servers): in every step, with the
public networks ticked and with one or eight stations listed, a real mouse event is sent to the centre of every visible control of every step and must be
received by it; the window must not change size under a click, must fit the screen, and no control may be squeezed under its least size.
The failure it found in an earlier layout: a right panel that grew with each row, so the least height of the page passed the height of the
window and the buttons of the next steps sat under the bottom of the screen. Fixed by a basket that does not grow the page and a least size
of the window that is read after the layout is invalidated and never exceeds the design size.
Not verified for the networks beyond RGP: a PPK solution from an EPN, IGS, NOAA CORS or GA base (the files are read by `convbin`, not run
through `rnx2rtkp` against a flight), the accuracy of the snapshot positions against a ground truth (only the cross-checks above), the
1 Hz bases of RINEX 3 in the solver (found, downloaded, merged, not solved).
Not verified: the window inside a running Metashape (colours and fonts under the real theme, the Aero menu entry), the replacement of camera
positions in a real project (the Metashape calls follow the 2.2.1 reference and are tested against a fake API only), `convbin` results used to
solve, absolute accuracy against GCPs, other drones, other countries, Windows antivirus reaction to the embedded programs.
