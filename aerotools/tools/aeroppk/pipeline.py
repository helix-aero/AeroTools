"""AeroPPK calculation chain, with no Qt and no Metashape (it runs in a worker thread, the window only shows its progress).

  flight files + photo events     (drones/)
  -> base station files           (stations/, rinexprep.py): download, unpack, merge, check the time coverage
  -> one RTKLIB run per base      (rtklib.py), positions of the photos by interpolation of the 5 Hz trajectory
  -> the error estimate           (combine.py)
  -> camera positions             lever arm added (antenna -> camera), quality, comparison with the drone's own position

The frame of the result is the frame of the base station coordinates (RGF93 v2b / ETRS89 for RGP). See docs/AEROPPK.md:
a rover position computed from a base inherits the frame of that base; it is NOT ITRF / WGS 84 at the date of the flight.
"""

import datetime
import os

import numpy as np

from . import antex, combine, geo, gnsstime, ionosphere, rinex, rinexprep, rtklib
from .stations import catalog, net, rgp


class PipelineError(Exception):
    """A failure with a sentence that can be shown to the user as it is."""


class Cancelled(Exception):
    pass


class Window(object):
    """Time window (GPS datetimes) the base files must cover."""

    def __init__(self, start, end):
        self.start, self.end = start, end

    def seconds(self):
        return (self.end - self.start).total_seconds()


def flight_window(files, events, margin=0.0):
    """The rover observation span, widened to the photo events when they stick out."""
    start = end = None
    if files.obs:
        try:
            info = rinex.read_obs_info(files.obs)
            start, end = info.first, info.last
        except rinex.RinexError:
            pass
    if events:
        e0 = gnsstime.from_seconds(min(e.time for e in events))
        e1 = gnsstime.from_seconds(max(e.time for e in events))
        start = e0 if start is None or e0 < start else start
        end = e1 if end is None or e1 > end else end
    if start is None:
        raise PipelineError("The flight has no usable time span.")
    pad = datetime.timedelta(seconds=margin)
    return Window(start - pad, end + pad)


class BaseSpec(object):
    """A base chosen by the user: the station and the product (file set) to use."""

    def __init__(self, station, product, distance_m):
        self.station, self.product, self.distance_m = station, product, distance_m

    @property
    def name(self):
        return self.station.code


class BaseData(object):
    def __init__(self, spec, obs, navs, info, xyz):
        self.spec, self.obs, self.navs, self.info, self.xyz = spec, obs, navs, info, xyz
        self.warnings = []
        self.antenna_delta = None         # (east, north, up) of the antenna over `xyz`; None = read it from the RINEX header

    @property
    def name(self):
        return self.spec.name


class BaseRun(object):
    """What one base gave: the trajectory, the photo positions, the numbers needed to judge it."""

    def __init__(self, base, profile, systems, options, pos, interp, seconds):
        self.base, self.profile, self.systems, self.options = base, profile, systems, options
        self.pos, self.interp, self.seconds = pos, interp, seconds
        self.pos_path = ""
        self.alt = None                   # the other constellation profile, for the cross-check
        self.alt_profile = None
        self.baseline_m = 0.0

    def fix_pct(self):
        q = self.interp["q"]
        return 100.0 * float(np.mean(q == rtklib.Q_FIX)) if len(q) else 0.0

    def solved_pct(self):
        """Share of the photos that have a position of any quality."""
        q = self.interp["q"]
        return 100.0 * float(np.mean(q > 0)) if len(q) else 0.0


class FlightResult(object):
    def __init__(self, files, events, window):
        self.files, self.events, self.window = files, events, window
        self.runs = []
        self.combined = None
        self.cam = None                   # dict of arrays: lat, lon, h of the camera
        self.check = {}
        self.warnings = []
        self.options = None
        self.profile_conflicts = 0


def _check_cancel(cancelled):
    if cancelled is not None and cancelled():
        raise Cancelled()


def prepare_base(spec, window, work_root, programs, progress=None, cancelled=None, opener=None):
    """Download (cached), unpack and merge the files of a base. Returns BaseData."""
    station, product = spec.station, spec.product
    crx = programs.get("crx2rnx")
    provider = catalog.provider_of(station) or rgp.PROVIDER
    folder = os.path.join(rgp.cache_dir(), "rinex", provider.id, station.code)
    if progress:
        progress("download", 0.0)
    try:
        obs_archives, nav_archives = provider.fetch(
            station, product, folder, progress=(lambda i, n, d, t: progress("download", (i + (d / float(t) if t else 0.0)) / n))
            if progress else None, cancelled=cancelled, opener=opener)
    except net.NetError as exc:
        if cancelled is not None and cancelled():
            raise Cancelled()
        raise PipelineError("Cannot download the data of station %s: %s" % (station.code, exc))
    _check_cancel(cancelled)
    work = os.path.join(work_root, station.code)
    try:
        obs, navs = rinexprep.prepare_archives(obs_archives, nav_archives, work, crx)
        info = rinex.read_obs_info(obs)
    except (rinexprep.PrepError, rinex.RinexError, OSError) as exc:
        raise PipelineError("The data of station %s cannot be used: %s" % (station.code, exc))
    except Exception as exc:                      # unpack.UnpackError and the like
        raise PipelineError("The data of station %s cannot be unpacked: %s" % (station.code, exc))
    if info.interval is None and product.interval:      # 15 minute pieces often have no INTERVAL line: the file set knows its rate
        info.interval = product.interval
    position = station.xyz_at(window.start)            # moved to the date of the flight for the networks whose frame drifts
    base = BaseData(spec, obs, navs, info, position)
    if info.first is None or info.last is None or info.first > window.start or info.last < window.end:
        base.warnings.append("coverage")
    if info.approx_xyz is not None:
        shift = float(np.linalg.norm(np.array(info.approx_xyz) - np.array(position)))
        if shift > 1.0:
            base.warnings.append("header_position")
    if info.interval and info.interval > 5.0:
        base.warnings.append("coarse_sampling")
    return base


PROFILES = {"all": None, "gps_glo": "GR", "gps": "G"}


def _systems_for(profile, common):
    wanted = PROFILES[profile]
    letters = "".join(s for s in "GRECJ" if s in common and (wanted is None or s in wanted))
    return letters


def run_base(files, events, window, base, options, rover_info, programs, work_dir, progress=None, cancelled=None, ion_nav=None):
    """Run RTKLIB with one base (two constellation profiles when the data allows) and keep the better one.

    `ion_nav` = a navigation file that only carries the ionosphere parameters (see `ionosphere.stub_for`), given to RTKLIB last."""
    exe = programs.get("rnx2rtkp")
    if not exe:
        raise PipelineError("The PPK engine (RTKLIB) is missing.")
    common = [s for s in "GRECJ" if s in rover_info.systems and s in base.info.systems]
    if "G" not in common:
        raise PipelineError("The drone and the station %s have no satellite system in common." % base.name)
    # constellation profiles to try: every common system, GPS + GLONASS, GPS alone (skipping duplicates). Measured: a base
    # receiver whose GLONASS data do not agree with the drone's (Trimble Alloy, 2026) makes any run with GLONASS fail
    # completely while GPS alone fixes 100 %; elsewhere (Leica GR50, 2023) adding Galileo raised the fixed share from 90 to 99 %.
    profiles, seen = [], set()
    for name in ("all", "gps_glo", "gps"):
        letters_for = _systems_for(name, common)
        if letters_for and letters_for not in seen:
            seen.add(letters_for)
            profiles.append(name)
    times = np.array([e.time for e in events], float)
    runs, failures = {}, []
    start = max(window.start, rover_info.first) if rover_info.first else window.start
    end = min(window.end, rover_info.last) if rover_info.last else window.end
    for k, profile in enumerate(profiles):
        _check_cancel(cancelled)
        letters = _systems_for(profile, common)
        opts = options.copy(systems=letters, base_xyz=base.xyz)
        if base.antenna_delta is not None:
            opts.base_antenna_delta = tuple(base.antenna_delta)
        else:
            opts.base_antenna_delta = (base.info.antenna_hen[1], base.info.antenna_hen[2], base.info.antenna_hen[0])
        own_base = getattr(getattr(base.spec, "station", None), "provider", "") == "own"
        if options.antex_file and not own_base:        # a typed position is already the phase centre: RTKLIB would add the offset again
            # the base antenna calibration is only applied when an ANTEX file really holds this antenna (the first file wins)
            path, known = antex.choose([options.antex_file] + list(options.antex_extra or []), base.info.antenna)
            opts.base_antenna = known or ""
            if path:
                opts.antex_file = path
            if known is None and "antenna_unknown" not in base.warnings:
                base.warnings.append("antenna_unknown")
        pos_path = os.path.join(work_dir, "%s_%s.pos" % (base.name, profile))
        navs = ([files.nav] if files.nav else []) + list(base.navs) + ([ion_nav] if ion_nav else [])
        try:
            rtklib.run_rnx2rtkp(files.obs, base.obs, navs, opts, pos_path, start=start, end=end, exe=exe,
                                progress=(lambda f, k=k: progress("solve", (k + f) / len(profiles))) if progress else None,
                                cancelled=cancelled, work_dir=work_dir)
            pos, _header = rtklib.parse_pos(pos_path)
        except rtklib.RtklibError as exc:
            if cancelled is not None and cancelled():
                raise Cancelled()
            failures.append(PipelineError("The PPK engine failed with station %s: %s" % (base.name, exc)))
            continue                      # another profile of the same base may still work
        step = float(np.median(np.diff(pos.t))) if len(pos) > 2 else 1.0
        interp = rtklib.interpolate(pos, times, max_gap=max(1.0, 3.0 * step))
        runs[profile] = BaseRun(base, profile, letters, opts, pos, interp, 0.0)
        runs[profile].pos_path = pos_path
    if not runs:
        raise failures[0]
    # the profile with more fixed photos wins; on equal fixed photos the one that has a position for more photos (a profile with
    # no solution at all never wins over one that has), then the one with more satellite systems; the runner-up is kept to
    # cross-check the winner
    ranked = sorted(runs.values(), key=lambda r: (-(round(r.fix_pct() / 1.0)), -round(r.solved_pct()), -len(r.systems)))
    best = ranked[0]
    for other in ranked[1:]:
        if other.fix_pct() > best.fix_pct() + 1.0:
            best = other
    others = [r for r in ranked if r is not best]
    first = runs.get(profiles[0])
    if best.profile != profiles[0] and (first is None or first.solved_pct() == 0.0) and best.solved_pct() > 0.0:
        # the full profile gave nothing (a base whose GLONASS data do not agree with the drone's): fewer systems were used instead
        if "reduced_systems" not in base.warnings:
            base.warnings.append("reduced_systems")
    if others:
        best.alt, best.alt_profile, best.alt_pos = others[0].interp, others[0].profile, others[0].pos
    base_lat, base_lon, base_h = geo.ecef_to_llh(*base.xyz)
    best.baseline_m = float(np.mean([geo.baseline_m(base_lat, base_lon, base_h, la, lo, hh)
                                     for la, lo, hh in zip(best.interp["lat"][::25], best.interp["lon"][::25], best.interp["h"][::25])
                                     if np.isfinite(la)] or [0.0]))
    return best


def camera_positions(events, comb):
    """Antenna positions of the combination + lever arm (antenna -> camera, north / east / down metres) -> camera lat, lon, h."""
    count = len(events)
    lat = np.full(count, np.nan)
    lon = np.full(count, np.nan)
    h = np.full(count, np.nan)
    clat, clon, ch = (np.asarray(a, float)[:count] for a in (comb.lat, comb.lon, comb.h))
    rows = np.nonzero(np.isfinite(clat))[0]
    if len(rows):
        offset = np.array([events[i].offset_ned for i in rows], float).reshape(-1, 3)
        x, y, z = geo.llh_to_ecef(clat[rows], clon[rows], ch[rows])
        dx, dy, dz = geo.ned_to_ecef_delta_array(clat[rows], clon[rows], offset[:, 0], offset[:, 1], offset[:, 2])
        lat[rows], lon[rows], h[rows] = geo.ecef_to_llh(x + dx, y + dy, z + dz)
    return {"lat": lat, "lon": lon, "h": h}


def compare_onboard(events, cam):
    """Compare PPK camera positions with the drone's own positions (north / east / up, metres).

    Only photos where the drone itself had an RTK fix (DJI status 50) are a fair reference: their statistics are returned
    separately from the gross check done on all photos (a median distance of more than 20 m means wrong files or wrong time).
    """
    out = {"rtk_photos": 0, "gross_median_m": None, "gross_error": False}
    rows = [(i, e) for i, e in enumerate(events) if e.onboard and np.isfinite(cam["lat"][i])]
    if not rows:
        return out
    idx = np.array([i for i, _ in rows])
    ref = np.array([e.onboard for _, e in rows])
    n, e_, d = geo.llh_difference_ned(ref[:, 0], ref[:, 1], ref[:, 2], cam["lat"][idx], cam["lon"][idx], cam["h"][idx])
    dist = np.sqrt(n ** 2 + e_ ** 2 + d ** 2)
    out["gross_median_m"] = float(np.median(dist))
    out["gross_error"] = bool(np.median(dist) > 20.0)
    fixed = np.array([events[i].onboard_q == 50 for i in idx])
    if fixed.sum() >= 3:
        out["rtk_photos"] = int(fixed.sum())
        for name, v in (("n", n), ("e", e_), ("u", -d)):
            vv = v[fixed]
            med = float(np.median(vv))
            out["bias_" + name] = med
            out["p95_" + name] = float(np.percentile(np.abs(vv - med), 95))
        out["rms_h"] = float(np.sqrt(np.mean((n[fixed] - out["bias_n"]) ** 2 + (e_[fixed] - out["bias_e"]) ** 2)))
    return out


def profile_conflicts(best_runs, comb, limit=0.05):
    """Photos where the two constellation profiles of the same base are both fixed and 5 cm apart (horizontal)."""
    bad = np.zeros(len(comb.q), bool)
    for run in best_runs:
        alt = run.alt
        if alt is None:
            continue
        both = np.nonzero((run.interp["q"] == rtklib.Q_FIX) & (alt["q"] == rtklib.Q_FIX))[0]
        if not len(both):
            continue
        n, e, _d = geo.llh_difference_ned_array(run.interp["lat"][both], run.interp["lon"][both], run.interp["h"][both],
                                                alt["lat"][both], alt["lon"][both], alt["h"][both])
        bad[both[np.hypot(n, e) > limit]] = True
    return bad


def _solutions(result, exclude=()):
    sols = []
    for run in result.runs:
        it = run.interp
        sols.append(combine.Solution(run.base.name, run.baseline_m, it["lat"], it["lon"], it["h"], it["q"], it["sdn"], it["sde"],
                                     it["sdu"], it["ratio"], it["ns"]))
    return sols


def recombine(
        result,
        policy=None,
):
    """Redo the combination and everything that follows it from the runs already made (no solver call, a fraction of a second).

    """
    events = result.events
    all_sols = _solutions(result)
    sols = list(all_sols)
    if not sols:
        raise PipelineError("No base is left to compute with.")
    comb = None
    if comb is None:
        comb = combine.combine(sols, policy)
    runs = list(result.runs)
    bad = profile_conflicts(runs, comb)
    result.profile_conflicts = int(bad.sum())
    for i in np.where(bad)[0]:
        comb.flags[i].append("profile_disagree")
        if comb.level[i] == combine.LEVEL_OK:
            comb.level[i] = combine.LEVEL_WARN
    result.combined = comb
    result.cam = camera_positions(events, comb)
    result.check = compare_onboard(events, result.cam)
    result.warnings = []
    if result.check.get("gross_error"):
        result.warnings.append("gross_error")
    if result.check.get("rtk_photos", 0) >= 3 and float(np.hypot(result.check["bias_n"], result.check["bias_e"])) > 0.3:
        result.warnings.append("onboard_offset")
    stations = [s for s in (getattr(run.base.spec, "station", None) for run in runs) if s is not None]
    if catalog.frames_mixed(stations):                 # results of bases in different frames were averaged as they are: say so
        result.warnings.append("mixed_frames:" + catalog.frames_text(stations, short=True))
    for run in result.runs:
        result.warnings.extend("%s:%s" % (w, run.base.name) for w in run.base.warnings)
    no_time = len((getattr(result.files, "extra", None) or {}).get("no_time") or ())
    if no_time:
        result.warnings.append("no_time:%d" % no_time)
    return result


def compute(
        files,
        events,
        window,
        bases,
        options=None,
        programs=None,
        work_dir=None,
        progress=None,
        cancelled=None,
        policy=None,
        on_run=None,
        ion_fetch=None,
):
    """Run the whole calculation for one flight. `bases` is a list of prepared BaseData. Returns FlightResult.

    `on_run(run)` is called after each base finished (the window lights up the photos with it). `ion_fetch(day)` gives the broadcast
    navigation file of a day (`ionosphere.fetch_broadcast`) for the ionosphere parameters when no navigation file has them; None = no network."""
    programs = programs or rtklib.find_programs()
    options = options or rtklib.Options()
    result = FlightResult(files, events, window)
    result.options = options
    if not bases:
        raise PipelineError("Choose at least one base station.")
    if not events:
        raise PipelineError("The flight has no photo events.")
    work_dir = work_dir or os.path.join(os.path.dirname(files.obs), "AeroPPK_work")
    os.makedirs(work_dir, exist_ok=True)
    rover_info = rinex.read_obs_info(files.obs)
    for k, base in enumerate(bases):
        _check_cancel(cancelled)
        # RTKLIB stays float for a whole flight when no navigation file of the run has the GPS ionosphere header lines
        own = [files.nav] if files.nav else []
        others = [p for b in bases if b is not base for p in b.navs]
        ion_nav, missing = ionosphere.stub_for(own + list(base.navs), window, work_dir, others, ion_fetch)
        if missing and missing not in base.warnings:
            base.warnings.append(missing)
        elif not missing and ionosphere.WARNING in base.warnings:        # found this time (the base object outlives one calculation)
            base.warnings.remove(ionosphere.WARNING)
        run = run_base(files, events, window, base, options, rover_info, programs, work_dir,
                       progress=(lambda step, f, k=k: progress(step, (k + f) / len(bases))) if progress else None,
                       cancelled=cancelled, ion_nav=ion_nav)
        result.runs.append(run)
        if on_run is not None:
            on_run(run)
    return recombine(
        result,
        policy,
    )
