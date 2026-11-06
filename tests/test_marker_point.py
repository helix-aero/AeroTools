"""Position of a marker that was never marked: it starts from its reference, in the markers frame."""

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
        return ("internal", point)


def _chunk(marker_crs):
    return SimpleNamespace(crs=_Crs("chunk"), marker_crs=marker_crs, transform=SimpleNamespace(matrix=_Matrix()))


def _marker(position=None):
    return SimpleNamespace(position=position, reference=SimpleNamespace(location=(1.0, 2.0, 3.0)))


def test_an_unmarked_marker_is_placed_with_the_marker_crs():
    point = msapi.marker_point(_chunk(_Crs("markers")), _marker())
    assert point == ("internal", ("ecef", "markers", (1.0, 2.0, 3.0)))


def test_without_a_marker_crs_the_chunk_crs_is_used():
    point = msapi.marker_point(_chunk(None), _marker())
    assert point == ("internal", ("ecef", "chunk", (1.0, 2.0, 3.0)))


def test_a_triangulated_marker_keeps_its_own_position():
    assert msapi.marker_point(_chunk(_Crs("markers")), _marker(position="P")) == "P"
