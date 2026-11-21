"""Aero-PPK: the vectorised combination, interpolation, camera positions and profile check give what the one-photo-at-a-time code gave.

The old loops are kept here (and in aeroppk_old_combine.py) as the reference; the numbers must agree to 1e-9 (they agree exactly)."""
import types

import numpy as np
import pytest

from aerotools.tools.aeroppk import combine, geo, pipeline, rtklib

import aeroppk_old_combine as old


# -- data -----------------------------------------------------------------------------------------------------------------

N = 240


def _solution(module, name, km, seed, fixp, bump):
    rng = np.random.default_rng(seed)
    t = np.linspace(0, 1, N)
    lat0, lon0, h0 = 42.3 + 0.01 * t, 9.1 + 0.012 * t, 300 + 20 * np.sin(6 * t)
    n, e, u = rng.normal(0, 0.01, N), rng.normal(0, 0.01, N), rng.normal(0, 0.02, N)
    if bump:
        n[bump[0]:bump[1]] += 0.5                          # a few photos where this base is far off
    x, y, z = geo.llh_to_ecef(lat0, lon0, h0)
    d = geo.ned_to_ecef_delta_array(lat0, lon0, n, e, -u)
    lat, lon, h = geo.ecef_to_llh(x + d[0], y + d[1], z + d[2])
    q = np.where(rng.random(N) < fixp, 1, 2)
    q[10:14] = 0
    lat, lon, h = np.array(lat), np.array(lon), np.array(h)
    lat[q == 0] = lon[q == 0] = h[q == 0] = np.nan
    sd = np.full(N, 0.01)
    return module.Solution(name, km * 1000.0, lat, lon, h, q, sd, sd, sd * 2.0, np.full(N, 9.0), np.full(N, 12))


def solutions(module, count=3):
    plan = [("A", 3, 1, 0.9, None), ("B", 9, 2, 0.9, (40, 48)), ("C", 18, 3, 0.9, (100, 110))]
    return [_solution(module, *p) for p in plan[:count]]


def same(a, b, what=""):
    a, b = np.asarray(a, float), np.asarray(b, float)
    assert np.array_equal(np.isfinite(a), np.isfinite(b)), what
    np.testing.assert_allclose(a, b, rtol=0.0, atol=1e-9, equal_nan=True, err_msg=what)


def same_combined(new, ref):
    for field in ("lat", "lon", "h", "q", "sdn", "sde", "sdu", "used", "spread", "level"):
        same(getattr(new, field), getattr(ref, field), field)
    assert new.flags == ref.flags and new.used_names == ref.used_names
    assert new.suspect == ref.suspect and list(new.bases) == list(ref.bases)
    for name, stats in ref.bases.items():
        for key, value in stats.items():
            if isinstance(value, float):
                assert new.bases[name][key] == pytest.approx(value, abs=1e-9), (name, key)
            else:
                assert new.bases[name][key] == value, (name, key)


# -- combine -----------------------------------------------------------------------------------------------------------

def test_one_base_is_combined_as_before():
    same_combined(combine.combine(solutions(combine, 1)), old.combine(solutions(old, 1)))


# -- the geometry helpers ------------------------------------------------------------------------------------------------

def old_camera_positions(events, comb):
    count = len(events)
    lat, lon, h = (np.full(count, np.nan) for _ in range(3))
    for i, event in enumerate(events):
        if not np.isfinite(comb.lat[i]):
            continue
        x, y, z = geo.llh_to_ecef(comb.lat[i], comb.lon[i], comb.h[i])
        dx, dy, dz = geo.ned_to_ecef_delta(float(comb.lat[i]), float(comb.lon[i]), *event.offset_ned)
        la, lo, hh = geo.ecef_to_llh(x + dx, y + dy, z + dz)
        lat[i], lon[i], h[i] = float(la), float(lo), float(hh)
    return {"lat": lat, "lon": lon, "h": h}


def test_camera_positions_are_what_the_loop_gave():
    comb = combine.combine(solutions(combine, 1))
    events = [types.SimpleNamespace(offset_ned=(0.1 * (i % 5), -0.2, 0.3 + 0.001 * i)) for i in range(N)]
    new, ref = pipeline.camera_positions(events, comb), old_camera_positions(events, comb)
    assert np.isnan(new["lat"]).sum() == 4
    for key in ("lat", "lon", "h"):
        same(new[key], ref[key], key)
    nothing = types.SimpleNamespace(lat=np.full(3, np.nan), lon=np.full(3, np.nan), h=np.full(3, np.nan))
    assert np.isnan(pipeline.camera_positions([types.SimpleNamespace(offset_ned=(0, 0, 0))] * 3, nothing)["lat"]).all()
    assert pipeline.camera_positions([], nothing)["lat"].shape == (0,)


def old_interpolate(pos, times, max_gap):
    times = np.asarray(times, float)
    count = len(times)
    out = {"lat": np.full(count, np.nan), "lon": np.full(count, np.nan), "h": np.full(count, np.nan),
           "q": np.zeros(count, int), "ns": np.zeros(count, int), "sdn": np.full(count, np.nan),
           "sde": np.full(count, np.nan), "sdu": np.full(count, np.nan), "ratio": np.full(count, np.nan),
           "gap": np.full(count, np.nan)}
    x, y, z = geo.llh_to_ecef(pos.lat, pos.lon, pos.h)
    index = np.searchsorted(pos.t, times)
    for k in range(count):
        j = int(index[k])
        if j <= 0 or j >= len(pos):
            continue
        i = j - 1
        t0, t1 = pos.t[i], pos.t[j]
        if times[k] - t0 > max_gap or t1 - times[k] > max_gap:
            continue
        w = 0.0 if t1 == t0 else (times[k] - t0) / (t1 - t0)
        px, py, pz = (x[i] + w * (x[j] - x[i]), y[i] + w * (y[j] - y[i]), z[i] + w * (z[j] - z[i]))
        lat, lon, h = geo.ecef_to_llh(px, py, pz)
        out["lat"][k], out["lon"][k], out["h"][k] = float(lat), float(lon), float(h)
        out["q"][k] = int(max(pos.q[i], pos.q[j]))
        out["ns"][k] = int(min(pos.ns[i], pos.ns[j]))
        out["sdn"][k] = max(pos.sdn[i], pos.sdn[j])
        out["sde"][k] = max(pos.sde[i], pos.sde[j])
        out["sdu"][k] = max(pos.sdu[i], pos.sdu[j])
        out["ratio"][k] = min(pos.ratio[i], pos.ratio[j])
        out["gap"][k] = max(times[k] - t0, t1 - times[k])
    return out


def track(step, count, hole=None):
    t = np.arange(count) * step
    if hole:
        t = np.delete(t, np.arange(hole[0], hole[1]))
    n = len(t)
    rng = np.random.default_rng(3)
    rows = np.column_stack([t, 42.3 + 1e-7 * np.arange(n), 9.1 + 1.2e-7 * np.arange(n), 300 + np.sin(np.arange(n) / 20.0),
                            np.where(rng.random(n) < 0.8, 1, 2), rng.integers(8, 20, n), np.full(n, 0.01), np.full(n, 0.01),
                            np.full(n, 0.02), np.zeros(n), rng.uniform(1, 9, n)])
    return rtklib.PosEpoch(rows)


@pytest.mark.parametrize("step, hole", [(0.2, None), (0.2, (200, 260)), (30.0, None)])
def test_interpolation_is_what_the_loop_gave(step, hole):
    pos = track(step, 1500, hole)
    times = np.sort(np.random.default_rng(2).uniform(pos.t[0] + 1e-3, pos.t[-1] - 1e-3, 400))
    times = np.concatenate([[pos.t[0] - 5.0, pos.t[-1] + 5.0], times, [pos.t[5]]])           # outside the track, and on an epoch
    times = times[times != pos.t[0]]
    gap = max(1.0, 3.0 * step)
    new, ref = rtklib.interpolate(pos, times, max_gap=gap), old_interpolate(pos, times, gap)
    for key in ref:
        same(new[key], ref[key], key)
    if hole:
        assert (new["q"] == 0).any()                                       # the hole gives no position, as before


def test_an_event_on_the_first_or_last_epoch_is_interpolated():
    pos = track(1.0, 50)
    out = rtklib.interpolate(pos, [pos.t[0], pos.t[-1], pos.t[0] - 0.01, pos.t[-1] + 0.01], max_gap=3.0)
    assert out["q"][0] > 0 and out["q"][1] > 0 and out["q"][2] == 0 and out["q"][3] == 0
    assert out["lat"][0] == pytest.approx(pos.lat[0], abs=1e-12) and out["lat"][1] == pytest.approx(pos.lat[-1], abs=1e-12)
    assert out["gap"][0] == pytest.approx(1.0) and out["h"][1] == pytest.approx(pos.h[-1], abs=1e-9)


def test_a_trajectory_of_one_epoch_gives_nothing():
    out = rtklib.interpolate(track(1.0, 1), [0.0, 0.5], max_gap=3.0)
    assert (out["q"] == 0).all() and np.isnan(out["lat"]).all()


def test_profile_conflicts_are_what_the_loop_gave():
    pos = track(0.2, 800)
    times = np.sort(np.random.default_rng(4).uniform(1.0, pos.t[-2], 300))
    main = rtklib.interpolate(pos, times, max_gap=1.0)
    alt = {k: np.array(v, copy=True) for k, v in main.items()}
    alt["lat"] = alt["lat"] + np.where(np.arange(300) % 7 == 0, 1.2e-6, 2e-8)            # 13 cm for one photo in seven, 2 mm for the others
    run = types.SimpleNamespace(interp=main, alt=alt)
    comb = types.SimpleNamespace(q=np.ones(300, int))
    bad = pipeline.profile_conflicts([run], comb)
    ref = np.zeros(300, bool)
    for i in np.where((main["q"] == 1) & (alt["q"] == 1))[0]:
        n, e, d = geo.llh_difference_ned(main["lat"][i], main["lon"][i], main["h"][i], alt["lat"][i], alt["lon"][i], alt["h"][i])
        if np.hypot(n, e) > 0.05:
            ref[i] = True
    assert ref.any() and (~ref).any() and np.array_equal(bad, ref)
    assert not pipeline.profile_conflicts([types.SimpleNamespace(interp=main, alt=None)], comb).any()
