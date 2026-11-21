"""Aero-PPK, step 2: the two modes (single base, own base) and the choice of stations by radius. Pure logic, no Qt."""
import types

from aerotools.tools.aeroppk import basket, state, stationfilter


def station(code, km):
    return types.SimpleNamespace(code=code, distance_m=km * 1000.0, name=code)


class Offer(object):
    def __init__(self, code, km, interval, ok=True, error=""):
        self.station = station(code, km)
        self.interval, self.systems, self._ok, self.error = interval, ["G"], ok, error

    def available(self):
        return self._ok


def test_the_window_starts_in_single_base_mode():
    st = state.WizardState()
    assert st.mode == state.MODE_SINGLE and st.basket.limit == 1 and st.radius() == basket.DEFAULT_RADIUS


def test_the_prepared_base_of_the_own_base_mode():
    st = state.WizardState()
    st.set_mode(state.MODE_OWN)
    st.own_base = "own"
    assert st.prepared_bases() == ["own"]


def test_the_own_base_leaves_the_choice_alone():
    st = state.WizardState()
    st.basket.add(station("AAAA", 5))
    st.set_mode(state.MODE_OWN)
    assert st.basket.codes() == ["AAAA"]
    st.set_mode(state.MODE_SINGLE)
    assert st.basket.codes() == ["AAAA"]


def test_a_new_choice_replaces_the_station_of_the_basket():
    st = state.WizardState()
    st.basket.add(station("AAAA", 5))
    st.basket.add(station("BBBB", 6))
    assert st.basket.codes() == ["BBBB"]


def test_unknown_mode_is_ignored():
    st = state.WizardState()
    st.set_mode("nonsense")
    assert st.mode == state.MODE_SINGLE


def test_the_list_hides_the_stations_known_to_have_no_data_or_coarse_data():
    stations = [station(c, i + 1) for i, c in enumerate("ABCDE")]
    offers = {"A": Offer("A", 1, 30.0), "B": Offer("B", 2, 1.0), "C": Offer("C", 3, 1.0, ok=False),
              "D": Offer("D", 4, 1.0, ok=False, error="timeout")}
    assert [s.code for s in stationfilter.candidates(stations, offers)] == ["A", "B", "D", "E"]       # no data out, unreachable and unknown stay
    assert [s.code for s in stationfilter.candidates(stations, offers, one_s_only=True)] == ["B", "D", "E"]


def test_the_servers_are_not_asked_about_farther_stations_once_enough_one_second_stations_are_known():
    stations = [station("S%02d" % i, i + 1) for i in range(20)]
    offers = {}
    assert len(stationfilter.next_to_ask(stations, offers, True, 3)) == 20
    for s in stations[:3]:
        offers[s.code] = Offer(s.code, 0, 1.0)
    assert stationfilter.next_to_ask(stations, offers, True, 3) == []
    offers["S00"] = Offer("S00", 0, 30.0)                                  # one of them is coarse: ask on
    assert [s.code for s in stationfilter.next_to_ask(stations, offers, True, 3)] == ["S%02d" % i for i in range(3, 20)]
