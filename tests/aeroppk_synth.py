"""A synthetic Aero-PPK flight result, shared by the tests and the Qt scripts (no file, no solver)."""
import types

import numpy as np

from aerotools.tools.aeroppk import geo, pipeline
from aerotools.tools.aeroppk.drones.base import PhotoEvent

N = 60


def planned_result(
        plan,
        n=N,
        km=None,
):
    """A FlightResult with one run per entry of `plan`: a dict with `north`, `east`, `up` (metres, a number or one value per photo: where
    the base solution is from the truth), `q` (1 fixed, 2 float, 0 none; a number or one value per photo) and `ratio`. The result is not
    combined yet."""
    km = km or [3.0 + 6.0 * k for k in range(len(plan))]
    t = np.linspace(0, 1, n)
    lat, lon, h = 42.3 + 0.001 * t, 9.1 + 0.001 * t, 300.0 + 0 * t
    events = [PhotoEvent(i + 1, 1000.0 + i) for i in range(n)]
    for i, e in enumerate(events):
        e.onboard, e.onboard_q = (float(lat[i]), float(lon[i]), float(h[i])), 50
        e.photo_name = "IMG_%04d.JPG" % (i + 1)
    result = pipeline.FlightResult(types.SimpleNamespace(name="FLIGHT_DEMO_001", folder="demo", photos=[]), events, None)
    x, y, z = geo.llh_to_ecef(lat, lon, h)
    full = lambda value: np.broadcast_to(np.asarray(value, float), (n,))
    for k, (spec, dist) in enumerate(zip(plan, km)):
        north, east, up = full(spec.get("north", 0.0)), full(spec.get("east", 0.0)), full(spec.get("up", 0.0))
        q = np.broadcast_to(np.asarray(spec.get("q", 1), int), (n,)).copy()
        la, lo, hh = [], [], []
        for i in range(n):
            dx, dy, dz = geo.ned_to_ecef_delta(lat[i], lon[i], north[i], east[i], -up[i])
            a, b, c = geo.ecef_to_llh(x[i] + dx, y[i] + dy, z[i] + dz)
            la.append(float(a)); lo.append(float(b)); hh.append(float(c))
        sd = full(spec.get("sd", 0.01))
        interp = {"lat": np.array(la), "lon": np.array(lo), "h": np.array(hh), "q": q, "sdn": sd.copy(), "sde": sd.copy(),
                  "sdu": 2.0 * sd, "ratio": full(spec.get("ratio", 9.0)).copy(), "ns": np.full(n, 12)}
        info = types.SimpleNamespace(interval=1.0, receiver="RX", antenna="ANT")
        product = types.SimpleNamespace(label="1 s", obs=[("u", "f.obs")])
        base = types.SimpleNamespace(name="B%d" % (k + 1), warnings=[], info=info, spec=types.SimpleNamespace(product=product))
        run = types.SimpleNamespace(base=base, interp=interp, baseline_m=dist * 1000.0, alt=None, systems="GR", profile="all",
                                    fix_pct=lambda q=q: 100.0 * float(np.mean(q == 1)))
        result.runs.append(run)
    return result


def fake_result(biases=((0.0, 0.0), (0.0, 0.0), (0.4, 0.0)), km=(3.0, 9.0, 18.0)):
    """A FlightResult with one run per base: synthetic trajectory, each base offset by `biases` (north, east metres)."""
    return planned_result([{"north": b[0], "east": b[1]} for b in biases], km=km)
