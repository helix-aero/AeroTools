"""Everything the PPK report and the CSV need, as plain Python data (no Qt, no Metashape).

`build(...)` turns the results of the calculation into one dictionary. The PDF writer (report.py) and the CSV writer only read
this dictionary, so they can be tested without running a calculation: see `sample()` for a complete small example.

Layout of the dictionary (texts are keys, never sentences: the writers translate them with i18n):

  data["title"]         project name shown on the cover
  data["generated"]     datetime of the calculation
  data["tool"]          {"name": "Aero-PPK", "version": "0.2.0"}
  data["engine"]        {"name": "RTKLIB-EX", "version": "2.5.1", "licence": "BSD 2-clause", "options": [(label key, value text)]}
  data["frame"]         {"label": "RGF93 v2b (ETRS89)", "note": "frame" }      frame of every position of the report
  data["source"]        {"name": "RGP (IGN)", "licence": "...", "url": "..."}  where the base station data come from
  data["flights"]       list of flight dictionaries (below)
  data["totals"]        {"photos": n, "fix": n, "float": n, "none": n, "fix_pct": x, "warn": n, "bad": n}
  data["warnings"]      list of (level, text key, fields dict)   level in "info", "warning", "error"

  flight:
    name, drone, photos, events, start_utc, end_utc, duration_s, folder
    files:    list of {"key", "present", "level", "detail"}              the checklist (aeroppk.need.<key>)
    bases:    list of {"code", "name", "distance_km", "baseline_km", "interval_s", "systems", "product", "files", "fix_pct",
                       "float", "used", "dropped", "bias_n", "bias_e", "bias_u", "rms_h", "suspect", "lat", "lon", "h"}
    quality:  combine.summary() of the flight plus "sd_h_median" etc.
    onboard:  pipeline.compare_onboard() result
    photos:   number of images found in the folder; photos_rows: list of {"index", "name", "time_utc", "lat", "lon", "h", "sdn", "sde", "sdu", "q", "level", "flags", "used",
                       "spread", "dn", "de", "du", "onboard_q"}   (dn/de/du: PPK minus the drone's own position, metres, or None)
    track:    list of (lat, lon, q) decimated trajectory points, for the map
    center:   (lat, lon) mean photo position
    warnings: list of (level, text key, fields)
"""

import datetime

import numpy as np

from . import combine, geo, gnsstime
from .stations import catalog


def _utc(seconds):
    return gnsstime.gps_to_utc(gnsstime.from_seconds(seconds))


def _engine_version(runs):
    for run in runs:
        try:
            with open(run.pos_path, "r", encoding="ascii", errors="replace") as handle:
                for line in handle:
                    if line.startswith("% program"):
                        return line.split(":", 1)[1].strip()
                    if not line.startswith("%"):
                        break
        except (OSError, AttributeError):
            continue
    return ""


def _run_warnings(result):
    """Notes of the flight worth a line in the report: a base that gave no position with every satellite system, exposures with no valid time."""
    out = [("info", "aeroppk.warn.reduced_systems", {"code": run.base.name}) for run in result.runs
           if "reduced_systems" in getattr(run.base, "warnings", [])]
    no_time = (getattr(result.files, "extra", None) or {}).get("no_time") or []
    if no_time:
        out.append(("info", "aeroppk.warn.no_time", {"code": len(no_time)}))
    return out


def flight_dict(
        result,
        drone_name="",
):
    files, events, comb = result.files, result.events, result.combined
    cam = result.cam
    photos = []
    onboard_delta = [None] * len(events)
    for i, event in enumerate(events):
        if event.onboard and np.isfinite(cam["lat"][i]):
            n, e, d = geo.llh_difference_ned(event.onboard[0], event.onboard[1], event.onboard[2], cam["lat"][i], cam["lon"][i],
                                             cam["h"][i])
            onboard_delta[i] = (float(n), float(e), float(-d))
    for i, event in enumerate(events):
        delta = onboard_delta[i]
        photos.append({
            "index": event.index, "name": event.photo_name or "", "time_utc": _utc(event.time),
            "lat": float(cam["lat"][i]), "lon": float(cam["lon"][i]), "h": float(cam["h"][i]),
            "sdn": float(comb.sdn[i]), "sde": float(comb.sde[i]), "sdu": float(comb.sdu[i]), "q": int(comb.q[i]),
            "level": int(comb.level[i]), "flags": list(comb.flags[i]), "used": list(comb.used_names[i]),
            "spread": float(comb.spread[i]) if np.isfinite(comb.spread[i]) else None,
            "dn": delta[0] if delta else None, "de": delta[1] if delta else None, "du": delta[2] if delta else None,
            "onboard_q": event.onboard_q})
    bases = []
    for run in result.runs:
        stats = comb.bases.get(run.base.name, {})
        station = run.base.spec.station
        info = run.base.info
        bases.append({
            "code": station.code, "name": station.name, "distance_km": run.base.spec.distance_m / 1000.0,
            "baseline_km": run.baseline_m / 1000.0, "interval_s": info.interval, "systems": "".join(info.systems),
            "systems_used": run.systems, "product": run.base.spec.product.label,
            "files": [name for _url, name in run.base.spec.product.obs], "fix_pct": run.fix_pct(),
            "float": stats.get("float", 0), "used": stats.get("used", 0), "dropped": stats.get("dropped", 0),
            "bias_n": stats.get("bias_n"), "bias_e": stats.get("bias_e"), "bias_u": stats.get("bias_u"),
            "rms_h": stats.get("rms_h"), "suspect": bool(stats.get("suspect")), "lat": station.lat, "lon": station.lon,
            "h": station.h, "receiver": info.receiver, "antenna": info.antenna, "profile": run.profile,
            "alt_profile": run.alt_profile, "network": catalog.short_of(station), "frame": catalog.frame_of(station)[1]})
    valid = np.isfinite(cam["lat"])
    step = max(1, len(events) // 400)
    track = []
    for run in result.runs[:1]:
        pos = run.pos
        stride = max(1, len(pos) // 600)
        track = [(float(pos.lat[k]), float(pos.lon[k]), int(pos.q[k])) for k in range(0, len(pos), stride)]
    center = (float(np.nanmean(cam["lat"])), float(np.nanmean(cam["lon"]))) if valid.any() else None
    times = [e.time for e in events]
    quality = combine.summary(comb)
    return {
        "name": files.name, "drone": drone_name, "folder": files.folder, "photos": len(files.photos), "events": len(events),
        "start_utc": _utc(min(times)) if times else None, "end_utc": _utc(max(times)) if times else None,
        "duration_s": (max(times) - min(times)) if times else 0.0, "files": [], "bases": bases, "quality": quality,
        "onboard": dict(result.check), "photos_rows": photos, "track": track, "center": center, "warnings": _run_warnings(result),
        "profile_conflicts": result.profile_conflicts, "step": step,
    }


def _frame_warnings(flight_results):
    """The report lists once that bases in different frames were averaged (see `pipeline.recombine`)."""
    for result in flight_results:
        for entry in result.warnings:
            name, _sep, frames = str(entry).partition(":")
            if name == "mixed_frames":
                return [("warning", "aeroppk.warn.mixed_frames", {"code": frames})]
    return []


def build(
        title,
        flight_results,
        options,
        source,
        frame_label,
        tool_version,
        drone_name="",
        needs=None,
        now=None,
):
    """Assemble the report dictionary from the FlightResult list. `needs` maps a flight folder to its list of Need."""
    now = now or datetime.datetime.now()
    flights = []
    for result in flight_results:
        f = flight_dict(
            result,
            drone_name,
        )
        for need in (needs or {}).get(result.files.folder, []):
            f["files"].append({"key": need.key, "present": need.present, "level": need.level, "detail": need.detail})
        flights.append(f)
    totals = {"photos": 0, "fix": 0, "float": 0, "none": 0, "warn": 0, "bad": 0}
    for f in flights:
        q = f["quality"]
        for key in totals:
            totals[key] += q.get(key, 0)
    totals["fix_pct"] = 100.0 * totals["fix"] / totals["photos"] if totals["photos"] else 0.0
    version = _engine_version(flight_results[0].runs) if flight_results else ""
    opts = [("frequency", options.frequency), ("systems", options.systems), ("elevation_mask", "%g deg" % options.elevation_mask),
            ("ambiguity", options.ambiguity), ("ratio", "%g" % options.ratio), ("dynamics", "off" if not options.dynamics else "on"),
            ("solution_type", options.solution_type), ("iono", options.iono), ("tropo", options.trop)]
    return {"title": title, "generated": now, "tool": {"name": "Aero-PPK", "version": tool_version},
            "engine": {"name": "RTKLIB-EX (demo5)", "version": version, "licence": "BSD 2-clause", "options": opts},
            "frame": {"label": frame_label}, "source": source, "flights": flights, "totals": totals,
            "warnings": _frame_warnings(flight_results)}


def sample():
    """A small complete dictionary for tests and for designing the report: two bases, one suspect, a few photos."""
    now = datetime.datetime(2026, 11, 6, 10, 0, 0)
    rows = []
    for i in range(12):
        rows.append({"index": i + 1, "name": "IMG_%04d.JPG" % (i + 1), "time_utc": now + datetime.timedelta(seconds=i),
                     "lat": 45.30 + i * 1e-5, "lon": 5.15 + i * 1e-5, "h": 500.0 + 0.01 * i, "sdn": 0.012, "sde": 0.011,
                     "sdu": 0.02 + (0.2 if i == 5 else 0.0), "q": 2 if i == 5 else 1, "level": 1 if i == 5 else 0,
                     "flags": ["float"] if i == 5 else [], "used": ["STNA", "STNB"], "spread": 0.02,
                     "dn": 0.012, "de": -0.008, "du": -0.05, "onboard_q": 50})
    base = {"code": "STNA", "name": "Station A, IGN", "distance_km": 0.5, "baseline_km": 0.6, "interval_s": 1.0, "systems": "GRES",
            "systems_used": "GRE", "product": "1 s, RINEX 2, hourly files", "files": ["stna350i.23d.Z"], "fix_pct": 99.0, "float": 1,
            "used": 11, "dropped": 0, "bias_n": 0.0, "bias_e": 0.0, "bias_u": 0.0, "rms_h": 0.001, "suspect": False,
            "lat": 45.2988, "lon": 5.1528, "h": 499.4, "receiver": "LEICA GR50", "antenna": "TRM57971.00", "profile": "all",
            "alt_profile": "gps_glo"}
    other = dict(base, code="STNB", name="Station B", distance_km=21.2, baseline_km=21.3, systems="GR", systems_used="GR",
                 fix_pct=18.0, suspect=True, bias_n=-0.026, bias_e=0.0, bias_u=0.048, rms_h=0.027, lat=45.1, lon=5.4)
    flight = {"name": "FLIGHT_DEMO_001", "drone": "DJI Mavic 3 Enterprise", "folder": "demo", "photos": 12, "events": 12,
              "start_utc": now, "end_utc": now + datetime.timedelta(seconds=11), "duration_s": 11.0,
              "files": [{"key": "photos", "present": True, "level": "blocking", "detail": "12"},
                        {"key": "events", "present": True, "level": "blocking", "detail": "x_Timestamp.MRK"},
                        {"key": "rover_obs", "present": True, "level": "blocking", "detail": "x_PPKOBS.obs"},
                        {"key": "rover_nav", "present": False, "level": "advised", "detail": ""},
                        {"key": "raw", "present": True, "level": "optional", "detail": "x_PPKRAW.bin"}],
              "bases": [base, other], "quality": {"photos": 12, "solved": 12, "fix": 11, "float": 1, "none": 0, "fix_pct": 91.7,
                                                  "ok": 11, "warn": 1, "bad": 0, "suspect_bases": ["STNB"], "sd_h_median": 0.016,
                                                  "sd_h_max": 0.02, "sd_u_median": 0.02, "sd_u_max": 0.25, "multi_pct": 50.0,
                                                  "spread_median": 0.02, "spread_max": 0.04},
              "onboard": {"rtk_photos": 12, "gross_median_m": 0.06, "gross_error": False, "bias_n": 0.012, "bias_e": -0.008,
                          "bias_u": -0.05, "p95_n": 0.01, "p95_e": 0.004, "p95_u": 0.01, "rms_h": 0.005},
              "photos_rows": rows, "track": [(45.30 + k * 1e-5, 5.15 + k * 1e-5, 1) for k in range(12)],
              "center": (45.3, 5.15), "warnings": [("warning", "aeroppk.warn.suspect_base", {"code": "STNB"})],
              "profile_conflicts": 0, "step": 1}
    return {"title": "Demo project", "generated": now, "tool": {"name": "Aero-PPK", "version": "0.2.0"},
            "engine": {"name": "RTKLIB-EX (demo5)", "version": "RTKLIB ver.EX 2.5.1", "licence": "BSD 2-clause",
                       "options": [("frequency", "l1+l2"), ("systems", "GREC"), ("ratio", "3")]},
            "frame": {"label": "RGF93 v2b (ETRS89)"},
            "source": {"name": "RGP (IGN)", "licence": "Licence Ouverte Etalab 2.0", "url": "https://rgpdata.ign.fr"},
            "flights": [flight], "totals": {"photos": 12, "fix": 11, "float": 1, "none": 0, "fix_pct": 91.7, "warn": 1, "bad": 0},
            "warnings": [("warning", "aeroppk.warn.suspect_base", {"code": "STNB"})]}
