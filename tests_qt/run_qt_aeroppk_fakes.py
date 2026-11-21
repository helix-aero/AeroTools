"""What the Aero-PPK Qt scripts put in place of the servers: made-up answers about a station and made-up downloaded bases."""
import types

from aerotools.tools.aeroppk.stations import survey as survey_tools


def fake_survey(station, window, hours, post=None, cancelled=None, opener=None):
    """What a server would say about a station, made up: every second for the stations of the odd codes, every 30 s for the others, none for one."""
    offer = survey_tools.Offer(station)
    number = sum(ord(c) for c in station.code)
    if number % 11 == 0:
        return offer                                                     # no data that day
    interval = 1.0 if number % 3 else 30.0
    offer.products = [types.SimpleNamespace(interval=interval, label="%g s, RINEX 3" % interval)]
    offer.info = {"systems": ["G", "R"], "receiver": "LEICA GR50", "antenna": "LEIAR25.R3 LEIT"}
    return offer


def fake_fetch(station, *args, **kwargs):
    """A downloaded base, made up (the Download all button works for real in the harness, on nothing)."""
    info = types.SimpleNamespace(interval=1.0, systems=["G", "R"], receiver="LEICA GR50", antenna="LEIAR25.R3 LEIT", first=None, last=None)
    product = types.SimpleNamespace(interval=1.0, label="1 s, RINEX 3")
    return types.SimpleNamespace(info=info, name=station.code, spec=types.SimpleNamespace(product=product, station=station))
