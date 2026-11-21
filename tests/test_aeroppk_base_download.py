"""Aero-PPK, Base step: the Download button path. `BaseScreen.download` once read an attribute that did not exist; it now asks the page
for `offers` (the surveys of the stations) and hands the products to the job. The real click is in tests_qt/run_qt_aeroppk_networks.py."""

import types

import pytest

screen_base = pytest.importorskip("aerotools.tools.aeroppk.screen_base")
from aerotools.tools.aeroppk import basket as B, runner  # noqa: E402


def test_download_forwards_the_survey_of_each_station_to_the_job(monkeypatch, tmp_path):
    station = types.SimpleNamespace(code="RIXH")
    item = types.SimpleNamespace(station=station, state=B.PENDING, error="")
    offer = types.SimpleNamespace(products=["P1 s"], available=lambda: True)
    asked, jobs = [], []
    monkeypatch.setattr(runner, "fetch_base", lambda st, window, hours, programs, root, **kw: asked.append((st.code, kw["products"])) or "base")
    monkeypatch.setattr(runner, "info_of", lambda base: None)
    page = types.SimpleNamespace(window="W", offers={"RIXH": offer}, status=types.SimpleNamespace(setFullText=lambda text: None))
    screen = types.SimpleNamespace(
        net=page, offers=page.offers, busy=lambda: False,
        state=types.SimpleNamespace(basket=types.SimpleNamespace(todo=lambda: [item], done=lambda: [], items=[item], get=lambda code: item),
                                    margin_h=1),
        wizard=types.SimpleNamespace(run_job=lambda work, message, done, name, **kw: jobs.append((work, message, done, name)) or True),
        basket_changed=lambda: None)
    screen_base.BaseScreen.download(screen)
    assert len(jobs) == 1 and jobs[0][3] == "download" and item.state == B.RUNNING        # the job was started, the row shows it
    posted = []
    jobs[0][0](lambda kind, **fields: posted.append((kind, fields)), lambda: False)       # run the job body with a fake mailbox
    assert asked == [("RIXH", ["P1 s"])]                                                  # what the survey found reached the download
    assert [kind for kind, _f in posted] == ["item", "base"]


def test_download_does_nothing_without_a_flight_window_or_bases(monkeypatch):
    jobs = []
    page = types.SimpleNamespace(window=None, offers={})
    screen = types.SimpleNamespace(net=page, offers={}, busy=lambda: False,
                                   state=types.SimpleNamespace(basket=types.SimpleNamespace(todo=lambda: [object()]), margin_h=1),
                                   wizard=types.SimpleNamespace(run_job=lambda *a, **k: jobs.append(a)))
    screen_base.BaseScreen.download(screen)
    assert jobs == []
