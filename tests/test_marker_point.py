"""Position d'un marqueur jamais pointe : elle part de sa reference, dans le repere des marqueurs."""

from types import SimpleNamespace

from aerotools import msapi


class _Crs(object):
    def __init__(self, name):
        self.name = name

    def unproject(self, location):
        return ("ecef", self.name, tuple(location))


class _Matrix(object):
    def inv(self):
        return self

    def mulp(self, point):
        return ("interne", point)


def _chunk(marker_crs):
    return SimpleNamespace(crs=_Crs("chunk"), marker_crs=marker_crs, transform=SimpleNamespace(matrix=_Matrix()))


def _marker(position=None):
    return SimpleNamespace(position=position, reference=SimpleNamespace(location=(1.0, 2.0, 3.0)))


def test_an_unpointed_marker_is_placed_with_the_marker_crs():
    point = msapi.marker_point(_chunk(_Crs("marqueurs")), _marker())
    assert point == ("interne", ("ecef", "marqueurs", (1.0, 2.0, 3.0)))


def test_without_a_marker_crs_the_chunk_crs_is_used():
    point = msapi.marker_point(_chunk(None), _marker())
    assert point == ("interne", ("ecef", "chunk", (1.0, 2.0, 3.0)))


def test_a_triangulated_marker_keeps_its_own_position():
    assert msapi.marker_point(_chunk(_Crs("marqueurs")), _marker(position="P")) == "P"
