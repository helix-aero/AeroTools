import math
import types

import pytest

from aerotools import msapi


@pytest.fixture(autouse=True)
def _bind(ms):
    msapi.Metashape = ms
    msapi._DIAG_LOGGED.clear()


class Shift(object):
    """Fake chunk matrix: offset (and scaling) of a point."""

    def __init__(self, ms, dx=0.0, dy=0.0, dz=0.0, scale=1.0):
        self.ms, self.d, self.scale = ms, (dx, dy, dz), scale

    def mulp(self, point):
        return self.ms.Vector(point.x * self.scale + self.d[0],
                              point.y * self.scale + self.d[1],
                              point.z * self.scale + self.d[2])


class Rot90(object):
    """Fake local frame: rotates the vectors by 90 degrees around Z."""

    def __init__(self, ms):
        self.ms = ms

    def mulv(self, vec):
        return self.ms.Vector(-vec.y, vec.x, vec.z)


class Crs(object):
    name = "fake crs"

    def __init__(self, ms, projected=True, local=True):
        self.ms, self.projected = ms, projected
        if not local:
            self.localframe = None

    def unproject(self, location):
        return location

    def project(self, point):
        return point

    def localframe(self, point):
        return Rot90(self.ms)


def _chunk(ms, crs=None, matrix=None, scale=1.0):
    chunk = ms.Chunk([], [])
    chunk.crs = crs
    chunk.transform = types.SimpleNamespace(matrix=matrix, scale=scale)
    return chunk


def _marker(ms, position, reference):
    marker = ms.Marker("GCP1", position=position)
    marker.reference.location = reference
    return marker


EMPTY = {"x": None, "y": None, "z": None, "total": None}


def test_chunk_without_crs_uses_chunk_frame_times_scale(ms):
    chunk = _chunk(ms, crs=None, matrix=None, scale=2.0)
    marker = _marker(ms, ms.Vector(1, 2, 3), ms.Vector(0, 0, 0))
    errors = msapi.marker_errors(chunk, marker)
    assert (errors["x"], errors["y"], errors["z"]) == (2.0, 4.0, 6.0)
    assert errors["total"] == pytest.approx(math.sqrt(4 + 16 + 36))


def test_chunk_without_crs_but_with_matrix_uses_world_coordinates(ms):
    chunk = _chunk(ms, crs=None, matrix=Shift(ms, 10, 20, 30, scale=2.0))
    marker = _marker(ms, ms.Vector(1, 1, 1), ms.Vector(12, 22, 31))
    errors = msapi.marker_errors(chunk, marker)
    assert (errors["x"], errors["y"], errors["z"]) == (0.0, 0.0, 1.0)


def test_with_crs_the_difference_goes_through_the_local_frame(ms):
    # estimated (100.5, 200, 50.25), reference (100, 200, 50): difference (0.5, 0, 0.25)
    # then 90 degree rotation of the fake local frame: (0, 0.5, 0.25).
    chunk = _chunk(ms, crs=Crs(ms), matrix=Shift(ms))
    marker = _marker(ms, ms.Vector(100.5, 200, 50.25), ms.Vector(100, 200, 50))
    errors = msapi.marker_errors(chunk, marker)
    assert errors["x"] == pytest.approx(0.0)
    assert errors["y"] == pytest.approx(0.5)
    assert errors["z"] == pytest.approx(0.25)
    assert errors["total"] == pytest.approx(math.sqrt(0.5 ** 2 + 0.25 ** 2))


def test_without_local_frame_falls_back_to_the_plain_difference(ms):
    chunk = _chunk(ms, crs=Crs(ms, local=False), matrix=Shift(ms))
    marker = _marker(ms, ms.Vector(500000.012, 4600000.0, 12.0),
                     ms.Vector(500000.0, 4600000.0, 12.0))
    errors = msapi.marker_errors(chunk, marker)
    assert errors["x"] == pytest.approx(0.012)
    assert errors["y"] == pytest.approx(0.0)
    assert errors["z"] == pytest.approx(0.0)


def test_degrees_are_converted_to_local_metres(ms):
    # Path C: 0.0001 degree of longitude at 42 degrees north = about 8.27 m.
    chunk = _chunk(ms, crs=Crs(ms, local=False), matrix=Shift(ms))
    marker = _marker(ms, ms.Vector(9.0001, 42.0, 10.0), ms.Vector(9.0, 42.0, 10.0))
    errors = msapi.marker_errors(chunk, marker)
    assert errors["x"] == pytest.approx(8.27, abs=0.02)
    assert errors["y"] == pytest.approx(0.0, abs=1e-6)
    assert errors["z"] == pytest.approx(0.0)
    north = _marker(ms, ms.Vector(9.0, 42.0001, 10.0), ms.Vector(9.0, 42.0, 10.0))
    assert msapi.marker_errors(chunk, north)["y"] == pytest.approx(11.1, abs=0.1)


def test_crs_kind_is_read_from_the_wkt_then_proj4(ms):
    def crs(wkt="", proj4=""):
        return types.SimpleNamespace(wkt=wkt, proj4=proj4)

    assert msapi.crs_kind(crs('PROJCS["RGF93 v1 / Lambert-93",GEOGCS["x"]]')) == "projected"
    assert msapi.crs_kind(crs('COMPD_CS["a",PROJCS["b",GEOGCS["c"]],VERT_CS["d"]]')) == "projected"
    assert msapi.crs_kind(crs('GEOGCS["WGS 84"]')) == "geographic"
    assert msapi.crs_kind(crs('GEOCCS["WGS 84"]')) == "geocentric"
    assert msapi.crs_kind(crs(proj4="+proj=latlong +ellps=WGS84")) == "geographic"
    assert msapi.crs_kind(crs(proj4="+proj=lcc +lat_1=49")) == "projected"
    assert msapi.crs_kind(object()) is None


def test_projected_crs_wins_over_degrees_heuristic(ms):
    # A projected local frame with small coordinates: the WKT decides (path B).
    class Local(Crs):
        wkt = 'PROJCS["site"]'

    chunk = _chunk(ms, crs=Local(ms, local=False), matrix=Shift(ms))
    marker = _marker(ms, ms.Vector(100.5, 50.0, 10.0), ms.Vector(100.0, 50.0, 10.0))
    assert msapi.marker_errors(chunk, marker)["x"] == pytest.approx(0.5)


def test_local_frame_matrix_variants(ms):
    # mulv rejects the vector: the matrix * vector product takes over.
    class OnlyProduct(object):
        def __init__(self, ms):
            self.ms = ms

        def mulv(self, vec):
            raise ValueError("vector length does not match matrix row size minus 1")

        def __mul__(self, vec):
            return self.ms.Vector(-vec.y, vec.x, vec.z)

    class Crs2(Crs):
        def localframe(self, point):
            return OnlyProduct(self.ms)

    chunk = _chunk(ms, crs=Crs2(ms), matrix=Shift(ms))
    marker = _marker(ms, ms.Vector(100.5, 200, 50.25), ms.Vector(100, 200, 50))
    errors = msapi.marker_errors(chunk, marker)
    assert errors["y"] == pytest.approx(0.5) and errors["z"] == pytest.approx(0.25)

    # Neither mulv nor product nor rotation: the rows are applied one by one.
    class Rows(object):
        data = [[0, -1, 0, 0], [1, 0, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]

        def row(self, index):
            return self.data[index]

    class Crs3(Crs):
        def localframe(self, point):
            return Rows()

    chunk = _chunk(ms, crs=Crs3(ms), matrix=Shift(ms))
    errors = msapi.marker_errors(chunk, marker)
    assert errors["y"] == pytest.approx(0.5) and errors["z"] == pytest.approx(0.25)


def test_a_failing_computation_is_explained_once_per_chunk(ms):
    from aerotools import journal

    class Boom(Crs):
        def unproject(self, location):
            raise RuntimeError("Vertical datum out of range")

        def project(self, point):
            raise RuntimeError("project too")

    msapi._DIAG_LOGGED.clear()
    chunk = _chunk(ms, crs=Boom(ms), matrix=Shift(ms))
    chunk.crs.name = "RGF93 + IGN69"
    marker = _marker(ms, ms.Vector(1, 1, 1), ms.Vector(0, 0, 0))
    before = len(journal.lines())
    for _ in range(3):
        assert msapi.marker_errors(chunk, marker) == EMPTY
    added = [line for line in journal.lines()[before:] if "Table errors" in line]
    assert len(added) == 1
    line = added[0]
    assert "Vertical datum out of range" in line and "crs.project failed" in line
    assert "GCP1" in line and "enabled" in line and "reference.location" in line
    assert "mulp->Vector" in line and "localframe->Rot90" in line
    # Another chunk gets its own line.
    other = _chunk(ms, crs=Boom(ms), matrix=Shift(ms))
    other.key = 2
    msapi.marker_errors(other, marker)
    assert len([l for l in journal.lines()[before:] if "Table errors" in l]) == 2


def test_geocentric_or_unknown_crs_is_reported_not_guessed(ms):
    from aerotools import journal

    class Geoc(Crs):
        wkt = 'GEOCCS["WGS 84"]'

    msapi._DIAG_LOGGED.clear()
    chunk = _chunk(ms, crs=Geoc(ms, local=False), matrix=Shift(ms))
    marker = _marker(ms, ms.Vector(1, 1, 1), ms.Vector(0, 0, 0))
    before = len(journal.lines())
    assert msapi.marker_errors(chunk, marker) == EMPTY
    assert any("geocentric" in l for l in journal.lines()[before:])


def test_missing_reference_is_silent_and_unestimated_is_explained_once(ms):
    from aerotools import journal

    msapi._DIAG_LOGGED.clear()
    chunk = _chunk(ms, crs=Crs(ms), matrix=Shift(ms))
    before = len(journal.lines())
    msapi.marker_errors(chunk, _marker(ms, ms.Vector(1, 1, 1), None))
    assert len(journal.lines()) == before
    for _ in range(2):
        msapi.marker_errors(chunk, _marker(ms, None, ms.Vector(1, 1, 1)))
    assert len([l for l in journal.lines()[before:] if "no estimated position" in l]) == 1


def test_no_reference_or_not_estimated_gives_none(ms):
    chunk = _chunk(ms, crs=Crs(ms), matrix=Shift(ms))
    assert msapi.marker_errors(chunk, _marker(ms, ms.Vector(1, 1, 1), None)) == EMPTY
    assert msapi.marker_errors(chunk, _marker(ms, None, ms.Vector(1, 1, 1))) == EMPTY


def test_never_raises(ms):
    assert msapi.marker_errors(None, None) == EMPTY
    assert msapi.marker_errors(object(), object()) == EMPTY

    class Boom(object):
        def mulp(self, point):
            raise RuntimeError("broken matrix")

    chunk = _chunk(ms, crs=Crs(ms), matrix=Boom())
    marker = _marker(ms, ms.Vector(1, 1, 1), ms.Vector(0, 0, 0))
    assert msapi.marker_errors(chunk, marker) == EMPTY

    class DeadMarker(object):
        @property
        def position(self):
            raise RuntimeError("object destroyed")

    assert msapi.marker_errors(_chunk(ms), DeadMarker()) == EMPTY


def test_result_has_exactly_the_four_keys(ms):
    chunk = _chunk(ms, crs=None, matrix=None)
    marker = _marker(ms, ms.Vector(1, 0, 0), ms.Vector(0, 0, 0))
    assert set(msapi.marker_errors(chunk, marker)) == {"x", "y", "z", "total"}
