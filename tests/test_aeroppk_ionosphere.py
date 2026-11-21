"""The ionosphere header lines RTKLIB needs: detection, the stub file, where the values come from, the warning. No network."""

import datetime
import gzip
import types

from aerotools import i18n
from aerotools.tools.aeroppk import ionosphere, pipeline, rtklib

import test_aeroppk_engine_profiles as engine

END = " " * 60 + "END OF HEADER".ljust(20) + "\n"
V2_TOP = "     2.10           N: GPS NAV DATA                         RINEX VERSION / TYPE\n"
V2_ALPHA = "     .1770E-07   .2235E-07  -.1192E-06  -.5960E-07          ION ALPHA           \n"
V2_BETA = "     .1290E+06   .1638E+06  -.1966E+06  -.2621E+06          ION BETA            \n"
V3_TOP = "     3.04           N: GNSS NAV DATA    M: MIXED            RINEX VERSION / TYPE\n"
V3_LINES = ("GAL    1.4475E+02  5.0000E-01  1.1139E-02                   IONOSPHERIC CORR    \n"
            "GPSA   1.7695E-08  2.2352E-08 -1.1921E-07 -5.9605E-08       IONOSPHERIC CORR    \n"
            "GPSB   1.2902E+05  1.6384E+05 -1.9661E+05 -2.6214E+05       IONOSPHERIC CORR    \n")
BODY = " 1 26  7  3 10  0  0.0 0.232062302530D-03-0.977706804406D-11 0.000000000000D+00\n"
WINDOW = pipeline.Window(datetime.datetime(2026, 7, 3, 13, 33), datetime.datetime(2026, 7, 3, 13, 50))


def nav(tmp_path, name, *lines):
    path = tmp_path / name
    path.write_text("".join(lines), encoding="ascii")
    return str(path)


def plain(tmp_path, name="plain.26n"):
    return nav(tmp_path, name, V2_TOP, END, BODY)


def with_lines(tmp_path, name="lines.26n"):
    return nav(tmp_path, name, V2_TOP, V2_ALPHA, V2_BETA, END, BODY)


def test_a_rinex_2_header_is_read(tmp_path):
    alpha, beta = ionosphere.klobuchar(with_lines(tmp_path))
    assert alpha == (1.77e-08, 2.235e-08, -1.192e-07, -5.96e-08)
    assert beta == (1.29e+05, 1.638e+05, -1.966e+05, -2.621e+05)


def test_a_rinex_2_header_with_d_exponents_is_read(tmp_path):
    path = nav(tmp_path, "d.26n", V2_TOP, V2_ALPHA.replace("E", "D"), V2_BETA.replace("E", "D"), END)
    assert ionosphere.klobuchar(path)[0][0] == 1.77e-08


def test_a_rinex_3_header_is_read_among_the_other_constellations(tmp_path):
    alpha, beta = ionosphere.klobuchar(nav(tmp_path, "m.rnx", V3_TOP, V3_LINES, END, BODY))
    assert alpha == (1.7695e-08, 2.2352e-08, -1.1921e-07, -5.9605e-08)
    assert beta[0] == 1.2902e+05


def test_a_gzip_file_is_read(tmp_path):
    path = tmp_path / "BRDC_MN.rnx.gz"
    with gzip.open(str(path), "wb") as handle:
        handle.write((V3_TOP + V3_LINES + END).encode("ascii"))
    assert ionosphere.klobuchar(str(path)) is not None


def test_no_header_lines_means_none(tmp_path):
    assert ionosphere.klobuchar(plain(tmp_path)) is None
    assert ionosphere.klobuchar(str(tmp_path / "missing.26n")) is None
    assert not ionosphere.has_klobuchar([plain(tmp_path), ""])


def test_zero_alpha_and_beta_alone_do_not_count(tmp_path):
    zeros = "     .0000E+00   .0000E+00   .0000E+00   .0000E+00          ION ALPHA           \n"
    assert ionosphere.klobuchar(nav(tmp_path, "z.26n", V2_TOP, zeros, V2_BETA, END)) is None
    assert ionosphere.klobuchar(nav(tmp_path, "b.26n", V2_TOP, V2_BETA, END)) is None


def test_alpha_alone_counts(tmp_path):
    alpha, beta = ionosphere.klobuchar(nav(tmp_path, "a.26n", V2_TOP, V2_ALPHA, END))
    assert alpha[0] == 1.77e-08 and beta is None


def test_a_line_after_the_end_of_the_header_is_not_a_header_line(tmp_path):
    assert ionosphere.klobuchar(nav(tmp_path, "late.26n", V2_TOP, END, V2_ALPHA, BODY)) is None


def test_the_stub_is_a_header_only_file_that_reads_back(tmp_path):
    alpha, beta = ionosphere.klobuchar(with_lines(tmp_path))
    path = ionosphere.write_stub(str(tmp_path / "stub.nav"), alpha, beta)
    lines = open(path).read().splitlines()
    assert lines[0].split()[0] == "2.10"
    assert [ln[60:].strip() for ln in lines] == ["RINEX VERSION / TYPE", "ION ALPHA", "ION BETA", "END OF HEADER"]
    assert all(len(ln) == 80 for ln in lines[1:])
    back = ionosphere.klobuchar(path)
    assert all(abs(a - b) <= abs(b) * 1e-3 for a, b in zip(back[0] + back[1], alpha + beta))


def test_nothing_is_added_when_a_file_already_has_the_lines(tmp_path):
    fetched = []
    got = ionosphere.stub_for([plain(tmp_path), with_lines(tmp_path)], WINDOW, str(tmp_path / "work"), [], lambda day: fetched.append(day))
    assert got == (None, None) and not fetched
    assert not (tmp_path / "work").exists()


def test_the_other_bases_files_come_first(tmp_path):
    fetched = []
    got, warning = ionosphere.stub_for([plain(tmp_path)], WINDOW, str(tmp_path / "work"), [plain(tmp_path, "p2.26n"), with_lines(tmp_path)],
                                       lambda day: fetched.append(day), str(tmp_path / "nocache"))
    assert warning is None and ionosphere.klobuchar(got) is not None and not fetched
    assert got.endswith("ionosphere_2026184.nav")


def test_a_cached_file_of_the_day_comes_second(tmp_path):
    cache = tmp_path / "cache"
    (cache / "CAMP").mkdir(parents=True)
    (cache / "CAMP" / "camp185k.26n").write_text(V2_TOP + V2_ALPHA + V2_BETA + END, encoding="ascii")     # day 185: not the day of the flight
    (cache / "CAMP" / "camp184k.26n").write_text(V2_TOP + V2_ALPHA + V2_BETA + END, encoding="ascii")
    fetched = []
    got, warning = ionosphere.stub_for([plain(tmp_path)], WINDOW, str(tmp_path / "work"), [], lambda day: fetched.append(day), str(cache))
    assert warning is None and ionosphere.klobuchar(got) is not None and not fetched
    assert ionosphere.cached_navs(ionosphere.flight_days(WINDOW), str(cache)) == [str(cache / "CAMP" / "camp184k.26n")]


def test_the_broadcast_file_is_the_last_resort(tmp_path):
    brdc = nav(tmp_path, "brdc.rnx", V3_TOP, V3_LINES, END)
    asked = []
    got, warning = ionosphere.stub_for([plain(tmp_path)], WINDOW, str(tmp_path / "work"), [], lambda day: asked.append(day) or brdc,
                                       str(tmp_path / "nocache"))
    assert warning is None and asked == [datetime.datetime(2026, 7, 3)]
    assert ionosphere.klobuchar(got)[0][0] == 1.7695e-08


def test_a_stub_made_for_the_day_is_reused_by_the_next_base(tmp_path):
    work = str(tmp_path / "work")
    brdc = nav(tmp_path, "brdc.rnx", V3_TOP, V3_LINES, END)
    first, _w = ionosphere.stub_for([plain(tmp_path)], WINDOW, work, [], lambda day: brdc, str(tmp_path / "nocache"))
    again, warning = ionosphere.stub_for([plain(tmp_path)], WINDOW, work, [], None, str(tmp_path / "nocache"))
    assert again == first and warning is None


def test_nothing_found_gives_the_warning_and_no_file(tmp_path):
    work = tmp_path / "work"
    for fetch in (None, lambda day: None, lambda day: plain(tmp_path, "empty.rnx")):
        assert ionosphere.stub_for([plain(tmp_path)], WINDOW, str(work), [], fetch, str(tmp_path / "nocache")) == (None, "no_ionosphere")
    assert not work.exists()


def test_the_warning_has_a_sentence():
    text = i18n.t("aeroppk.warn.no_ionosphere", code="CAUR")
    assert text != "aeroppk.warn.no_ionosphere" and "CAUR" in text


def test_the_solver_gets_the_stub_last(monkeypatch, tmp_path):
    rig = engine._Rig(monkeypatch, {"GR": rtklib.Q_FIX, "G": rtklib.Q_FIX})
    seen = []
    original = rig._run
    monkeypatch.setattr(rtklib, "run_rnx2rtkp",
                        lambda rover, base, navs, opts, out, **k: seen.append(list(navs)) or original(rover, base, navs, opts, out, **k))
    events = engine._events()
    info = types.SimpleNamespace(systems="GR", antenna_hen=(0.0, 0.0, 0.0), antenna="ANT", first=None, last=None)
    spec = types.SimpleNamespace(station=types.SimpleNamespace(code="TLA2"), name="TLA2")
    base = pipeline.BaseData(spec, "base.obs", ["b.nav"], info, (4600000.0, 700000.0, 4350000.0))
    rover = types.SimpleNamespace(systems="GR", first=None, last=None)
    files = types.SimpleNamespace(obs="rover.obs", nav="drone.nav")
    pipeline.run_base(files, events, WINDOW, base, rtklib.Options(), rover, {"rnx2rtkp": "x"}, str(tmp_path), ion_nav="stub.nav")
    assert seen and all(navs == ["drone.nav", "b.nav", "stub.nav"] for navs in seen)
