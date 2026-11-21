"""Aero-PPK engine: the choice between the constellation profiles of one base, with a fake solver (no RTKLIB, no file)."""
import datetime
import types

import pytest

from aerotools.tools.aeroppk import pipeline, rtklib
from aerotools.tools.aeroppk.drones.base import PhotoEvent

T0 = 1_400_000_000.0


def _events(n=20):
    events = [PhotoEvent(i + 1, T0 + 10.0 + i) for i in range(n)]
    for i, event in enumerate(events):
        event.photo_name = "IMG_%04d.JPG" % (i + 1)
    return events


def _pos(q, n=60):
    """A `.pos` of `n` epochs one second apart around the photos; `q` 0 = no epoch at all."""
    if q == 0:
        return rtklib.PosEpoch([])
    rows = [[T0 + k, 42.0 + 1e-6 * k, 9.0, 100.0, q, 12, 0.01, 0.01, 0.02, 0.0, 9.0] for k in range(n)]
    return rtklib.PosEpoch(rows)


class _Rig(object):
    """Runs `pipeline.run_base` with a fake rnx2rtkp: `outcome` maps the systems of a run to a quality, or to an error."""

    def __init__(self, monkeypatch, outcome):
        self.outcome, self.calls = outcome, []
        monkeypatch.setattr(rtklib, "run_rnx2rtkp", self._run)
        monkeypatch.setattr(rtklib, "parse_pos", lambda path: (self._read[path], []))
        self._read = {}

    def _run(self, rover, base, navs, opts, out_path, **kwargs):
        self.calls.append(opts.systems)
        result = self.outcome[opts.systems]
        if result == "error":
            raise rtklib.RtklibError("RTKLIB failed")
        self._read[out_path] = _pos(result)

    def go(self, tmp_path, base_systems="GR"):
        events = _events()
        info = types.SimpleNamespace(systems=base_systems, antenna_hen=(0.0, 0.0, 0.0), antenna="ANT", first=None, last=None)
        spec = types.SimpleNamespace(station=types.SimpleNamespace(code="TLA2"), name="TLA2")
        base = pipeline.BaseData(spec, "base.obs", [], info, (4600000.0, 700000.0, 4350000.0))
        window = pipeline.Window(datetime.datetime(2026, 5, 19, 11, 0), datetime.datetime(2026, 5, 19, 12, 0))
        rover = types.SimpleNamespace(systems="GR", first=None, last=None)
        files = types.SimpleNamespace(obs="rover.obs", nav="")
        return pipeline.run_base(files, events, window, base, rtklib.Options(), rover, {"rnx2rtkp": "x"}, str(tmp_path))


def test_a_profile_with_no_position_never_wins_over_one_that_has_some(monkeypatch, tmp_path):
    rig = _Rig(monkeypatch, {"GR": 0, "G": rtklib.Q_FLOAT})        # GPS + GLONASS: no epoch at all; GPS alone: float
    run = rig.go(tmp_path)
    assert run.profile == "gps" and run.systems == "G"
    assert run.solved_pct() == 100.0 and run.fix_pct() == 0.0
    assert "reduced_systems" in run.base.warnings


def test_the_richer_profile_still_wins_when_both_work(monkeypatch, tmp_path):
    rig = _Rig(monkeypatch, {"GR": rtklib.Q_FIX, "G": rtklib.Q_FIX})
    run = rig.go(tmp_path)
    assert run.systems == "GR"
    assert "reduced_systems" not in run.base.warnings


def test_more_fixed_photos_win_even_with_fewer_systems(monkeypatch, tmp_path):
    rig = _Rig(monkeypatch, {"GR": rtklib.Q_FLOAT, "G": rtklib.Q_FIX})
    run = rig.go(tmp_path)
    assert run.systems == "G" and run.fix_pct() == 100.0
    assert "reduced_systems" not in run.base.warnings              # the full profile did give positions: nothing to announce


def test_a_failing_profile_does_not_stop_the_base_when_another_one_works(monkeypatch, tmp_path):
    rig = _Rig(monkeypatch, {"GR": "error", "G": rtklib.Q_FIX})
    run = rig.go(tmp_path)
    assert run.systems == "G" and run.fix_pct() == 100.0
    assert "reduced_systems" in run.base.warnings


def test_the_error_stays_when_every_profile_fails(monkeypatch, tmp_path):
    rig = _Rig(monkeypatch, {"GR": "error", "G": "error"})
    with pytest.raises(pipeline.PipelineError):
        rig.go(tmp_path)


def test_nothing_to_announce_with_a_single_profile(monkeypatch, tmp_path):
    rig = _Rig(monkeypatch, {"G": 0})
    run = rig.go(tmp_path, base_systems="G")
    assert run.solved_pct() == 0.0 and run.base.warnings == []      # no other profile exists: it stays what it is


def test_the_warning_has_a_sentence_for_the_screen_and_the_report():
    from aerotools import i18n
    assert "TLA2" in i18n.t("aeroppk.warn.reduced_systems", code="TLA2")


def test_the_report_lists_the_note_for_the_flight():
    from aerotools.tools.aeroppk import reportdata
    from tests.aeroppk_synth import fake_result
    result = fake_result()
    assert reportdata._run_warnings(result) == []
    result.runs[1].base.warnings.append("reduced_systems")
    assert reportdata._run_warnings(result) == [("info", "aeroppk.warn.reduced_systems", {"code": result.runs[1].base.name})]


# -- exposures with a placeholder time in the events file (seen on a Mavic 3E flight of 2026-03-14) ------------------------

GOOD = "{i}\t558{i:03d}.500000\t[2409]\t    22,N\t    31,E\t    90,V\t41.92903331,Lat\t8.78249300,Lon\t106.330,Ellh\t0.011066, 0.009222, 0.021446\t50,Q"
BAD = "{i}\t-259200.000000\t[-522]\t     0,N\t     0,E\t     0,V\t41.92849363,Lat\t8.78410222,Lon\t107.184,Ellh\t0.010712, 0.008869, 0.020768\t50,Q"


def _flight_with_placeholder(tmp_path, bad_at=(4,)):
    from aerotools.tools.aeroppk import drones
    names = ["DJI_20260314120%03d_%04d_V.JPG" % (i, i) for i in range(1, 7)]
    for name in names:
        (tmp_path / name).write_bytes(b"x")
    lines = [(BAD if i in bad_at else GOOD).format(i=i) for i in range(1, 7)]
    (tmp_path / "DJI_202603141142_002_Timestamp.MRK").write_text("\n".join(lines) + "\n", encoding="utf-8")
    reader = drones.reader_by_id("dji_m3e")
    return reader, reader.scan(str(tmp_path))


def test_a_placeholder_time_is_named_and_the_pairing_stays_by_index(tmp_path):
    reader, files = _flight_with_placeholder(tmp_path)
    events, warnings = reader.read_events(files)
    assert [e.index for e in events] == [1, 2, 3, 5, 6]            # the exposure without a time is left out, the others keep their number
    assert all(e.photo_name.endswith("_%04d_V.JPG" % e.index) for e in events)    # and each keeps ITS photo (no shift after the gap)
    assert files.extra["no_time"] == [(4, "DJI_20260314120004_0004_V.JPG")]
    assert any("no valid time" in w for w in warnings)


def test_no_note_when_every_exposure_has_a_time(tmp_path):
    reader, files = _flight_with_placeholder(tmp_path, bad_at=())
    events, warnings = reader.read_events(files)
    assert len(events) == 6 and files.extra["no_time"] == [] and warnings == []


def test_the_screen_and_the_report_say_it(tmp_path):
    from aerotools import i18n
    from aerotools.tools.aeroppk import reportdata
    from tests.aeroppk_synth import fake_result
    reader, files = _flight_with_placeholder(tmp_path, bad_at=(2, 4))
    reader.read_events(files)
    result = fake_result()
    result.files = files
    assert ("info", "aeroppk.warn.no_time", {"code": 2}) in reportdata._run_warnings(result)
    assert "2 photos" in i18n.t("aeroppk.warn.no_time", code=2)
    assert "DJI_20260314120002_0002_V.JPG" in i18n.t("aeroppk.rover.no_time", n=2, names="DJI_20260314120002_0002_V.JPG, ...")
    result = pipeline.recombine(result)
    assert "no_time:2" in result.warnings
