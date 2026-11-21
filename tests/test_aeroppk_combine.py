"""AeroPPK: the error estimate and the quality flags of the solution of a base station (synthetic data with a known truth)."""
import numpy as np
import pytest

from aerotools.tools.aeroppk import combine, geo

RNG = np.random.RandomState(7)
N = 120


def truth():
    t = np.linspace(0, 1, N)
    return 42.30 + 0.01 * t, 9.15 + 0.01 * np.sin(3 * t), 500.0 + 20.0 * np.sin(5 * t)


def make(name, baseline, bias=(0.0, 0.0, 0.0), noise=(0.006, 0.006, 0.012), q=None, sd=(0.01, 0.01, 0.02), outliers=None):
    lat, lon, h = truth()
    n_off = RNG.normal(0, noise[0], N) + bias[0]
    e_off = RNG.normal(0, noise[1], N) + bias[1]
    u_off = RNG.normal(0, noise[2], N) + bias[2]
    if outliers:
        for index, (dn, de, du) in outliers.items():
            n_off[index] += dn
            e_off[index] += de
            u_off[index] += du
    x, y, z = geo.llh_to_ecef(lat, lon, h)
    la, lo, hh = [], [], []
    for i in range(N):
        dx, dy, dz = geo.ned_to_ecef_delta(lat[i], lon[i], n_off[i], e_off[i], -u_off[i])
        a, b, c = geo.ecef_to_llh(x[i] + dx, y[i] + dy, z[i] + dz)
        la.append(float(a)); lo.append(float(b)); hh.append(float(c))
    qq = np.ones(N, int) if q is None else q
    return combine.Solution(name, baseline, la, lo, hh, qq, np.full(N, sd[0]), np.full(N, sd[1]), np.full(N, sd[2]))


def error_m(result):
    lat, lon, h = truth()
    n, e, d = geo.llh_difference_ned(lat, lon, h, result.lat, result.lon, result.h)
    return n, e, -d


def test_float_solutions_are_down_weighted_and_flagged():
    q = np.ones(N, int)
    q[20:30] = combine.FLOAT
    a = make("A", 3000.0, q=q)
    a.sdn[20:30], a.sde[20:30], a.sdu[20:30] = 0.2, 0.2, 0.4          # a float solution reports large deviations
    res = combine.combine([a])
    assert (res.q[20:30] == combine.FLOAT).all() and combine.F_FLOAT in res.flags[25]
    assert res.sdn[25] > 5 * res.sdn[5]
    assert res.level[25] != combine.LEVEL_OK


def test_nothing_usable_is_reported_not_invented():
    q = np.full(N, combine.NONE)
    q[0] = combine.FIX
    res = combine.combine([make("A", 3000.0, q=q)])
    assert res.q[1] == combine.NONE and combine.F_NO_SOLUTION in res.flags[1] and np.isnan(res.lat[1])
    assert res.q[0] == combine.FIX
    assert combine.summary(res)["none"] == N - 1


def test_summary_figures_of_one_base():
    res = combine.combine([make("A", 3000.0)])
    s = combine.summary(res)
    assert s["photos"] == N and s["fix"] == N and s["fix_pct"] == pytest.approx(100.0)
