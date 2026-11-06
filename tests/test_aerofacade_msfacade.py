"""Tests of the Aero-Facade Metashape adapter with a small fake Metashape (no Metashape, no Qt, no display)."""

import json
import math
import os
import types

import numpy as np
import pytest

from aerotools.tools.aerofacade import msfacade
from aerotools.tools.aerofacade.backend import Cancelled
from aerotools.tools.aerofacade.model import Facade, Margins, Method


# --------------------------------------------------------------------------------------------------
# A fake Metashape, just rich enough for the adapter
# --------------------------------------------------------------------------------------------------

class Vector(object):
    def __init__(self, values=(0, 0, 0)):
        self.v = np.array([float(x) for x in values])

    def __getitem__(self, i):
        return self.v[i]

    def __iter__(self):
        return iter(self.v.tolist())

    def __len__(self):
        return len(self.v)

    @property
    def x(self):
        return self.v[0]

    @property
    def y(self):
        return self.v[1]

    @property
    def z(self):
        return self.v[2]

    def __sub__(self, o):
        return Vector(self.v - o.v)

    def __add__(self, o):
        return Vector(self.v + o.v)

    def __mul__(self, o):
        return float(self.v @ o.v) if isinstance(o, Vector) else Vector(self.v * o)

    def norm(self):
        return float(np.linalg.norm(self.v))


class Matrix(object):
    def __init__(self, rows):
        self.a = np.array(rows, dtype=float)

    def __getitem__(self, ij):
        return self.a[ij]

    def __mul__(self, o):
        return Matrix(self.a @ o.a)

    def inv(self):
        return Matrix(np.linalg.inv(self.a))

    def mulp(self, p):
        q = self.a @ np.array(list(p) + [1.0])
        return Vector(q[:3])

    def mulv(self, p):
        return Vector(self.a[:3, :3] @ np.array(list(p)))


class BBox(object):
    def __init__(self, lo, hi):
        self.min, self.max = lo, hi


class Region(object):
    center = size = rot = None


class CoordinateSystem(object):
    def __init__(self, wkt=""):
        self.wkt = wkt

    def localframe(self, p):
        return Matrix(np.eye(4))

    def project(self, p):
        return Vector(p)


class Sensor(object):
    width, height = 4000, 3000


class Camera(object):
    def __init__(self, key, centre, forward):
        self.key, self.label, self.enabled = key, "IMG_%d" % key, True
        self.sensor = Sensor()
        f = np.array(forward, float)
        f /= np.linalg.norm(f)
        right = np.cross(f, [0, 0, 1.0])
        right /= np.linalg.norm(right)
        down = np.cross(f, right)
        self.transform = Matrix(np.vstack([np.column_stack([right, down, f, centre]), [0, 0, 0, 1]]))
        self.center = Vector(centre)

    def project(self, P):
        q = self.transform.inv().mulp(P)
        if q.z == 0:
            return None
        return Vector((2000 + 1500 * q.x / q.z, 1500 + 1500 * q.y / q.z, 0))


class Point(object):
    def __init__(self, xyz, track):
        self.coord, self.valid, self.track_id = Vector(xyz), True, track


class TiePoints(object):
    def __init__(self, pts, cams):
        self.points = pts
        self.projections = {}


class FakeChunk(object):
    def __init__(self, key, label, cams, points, scale=5.0):
        self.key, self.label = key, label
        self.cameras = cams
        self.meta = {}
        self.crs = CoordinateSystem("fake geographic")
        self.transform = types.SimpleNamespace(matrix=Matrix(np.diag([scale, scale, scale, 1.0])), scale=scale)
        self.region = Region()
        self.region.center, self.region.size = Vector((0, 0, 0)), Vector((100, 100, 100))
        self.tie_points = TiePoints(points, cams)
        self.depth_maps, self.point_cloud, self.elevation, self.orthomosaic = None, None, None, None
        self.calls = []
        self.fail = None
        self.export_text = "image"

    def copy(self, items=None, keypoints=True):
        c = FakeChunk(self.key + 100 + len(self.calls), self.label + " copy", [Camera(k.key, list(k.center), list(k.transform.mulv((0, 0, 1)))) for k in self.cameras],
                      self.tie_points.points, self.transform.scale)
        c.parent, c.copied_items = self, items
        c.calls = []
        self.calls.append(("copy", items))
        self.doc.chunks.append(c)
        c.doc = self.doc
        return c

    def _run(self, name, progress, **kwargs):
        self.calls.append((name, kwargs))
        if self.fail == name:
            raise RuntimeError("Empty raster")
        if progress:
            for pct in (0, 50, 100):
                progress(pct)

    def buildDem(self, source_data=None, interpolation=None, region=None, replace_asset=False, progress=None, **kw):
        self._run("buildDem", progress, source_data=source_data, region=region)
        self.elevation = types.SimpleNamespace(width=100, height=50, resolution=0.07, min=-2.0, max=2.0)

    def buildDepthMaps(self, progress=None, **kw):
        self._run("buildDepthMaps", progress, **kw)
        self.depth_maps = {c: 1 for c in kw["cameras"]}

    def buildOrthomosaic(self, region=None, resolution=0.03, progress=None, **kw):
        self._run("buildOrthomosaic", progress, region=region)
        w = int(round((region.max[0] - region.min[0]) / resolution))
        h = int(round((region.max[1] - region.min[1]) / resolution))
        self.orthomosaic = types.SimpleNamespace(width=w, height=h, resolution=resolution, left=region.min[0], bottom=region.min[1],
                                                 right=region.max[0], top=region.max[1])

    def exportRaster(self, path, progress=None, **kw):
        self._run("exportRaster", progress, path=path)
        with open(path, "wb") as handle:
            handle.write(b"fake image")


class FakeDoc(object):
    def __init__(self, chunks, path):
        self.chunks, self.path = chunks, path
        for c in chunks:
            c.doc = self
        self.chunk = chunks[0]

    def remove(self, c):
        self.chunks.remove(c)

    def findChunk(self, key):
        for c in self.chunks:
            if c.key == key:
                return c
        return None


def make_fake_metashape(doc):
    ns = types.SimpleNamespace
    mod = types.ModuleType("Metashape")
    mod.Vector, mod.Matrix, mod.BBox, mod.Region, mod.CoordinateSystem = Vector, Matrix, BBox, Region, CoordinateSystem
    mod.DataSource = ns(TiePointsData="tie", DepthMapsData="depth", PointCloudData="cloud", ElevationData="dem", OrthomosaicData="ortho")
    mod.EnabledInterpolation, mod.MosaicBlending, mod.MildFiltering = "enabled", "mosaic", "mild"
    mod.ImageFormatPNG, mod.ImageFormatJPEG, mod.ImageFormatTIFF = "png", "jpeg", "tiff"
    mod.ImageCompression = type("ImageCompression", (), {})
    mod.app = ns(document=doc)
    return mod


def wall_points(n=4000, seed=1):
    """A 20 m x 8 m wall at y = 0 (x 0..20, z 0..8) with a bit of ground in front, in ENU metres."""
    rng = np.random.RandomState(seed)
    wall = np.column_stack([rng.uniform(0, 20, n), rng.normal(0, 0.03, n), rng.uniform(0, 8, n)])
    ground = np.column_stack([rng.uniform(-5, 25, n // 2), rng.uniform(-12, 0, n // 2), rng.normal(0, 0.05, n // 2)])
    return np.vstack([wall, ground])


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A fake chunk (ENU = chunk coordinates x 5), 12 cameras in front of the wall (y < 0), the wall's tie points, a project path."""
    P = wall_points()
    pts = [Point(p / 5.0, i) for i, p in enumerate(P)]
    cams = []
    for k in range(12):
        c = np.array([k * 2.0 - 2.0, -25.0 - (k % 3), 4.0]) / 5.0
        cams.append(Camera(k, list(c), [0.0, 1.0, 0.0]))
    chunk = FakeChunk(1, "Chunk 1", cams, pts)
    chunk.tie_points.projections = {cam: [types.SimpleNamespace(track_id=i) for i in range(0, 4000, 2)] for cam in cams}
    doc = FakeDoc([chunk], str(tmp_path / "SITE_test.psx"))
    mod = make_fake_metashape(doc)
    monkeypatch.setattr(msfacade, "Metashape", mod)
    return types.SimpleNamespace(chunk=chunk, doc=doc, mod=mod, tmp=tmp_path, points=P)


def facade_a(**kw):
    return Facade.from_frame("A", 0, (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), 20.0, 8.0, n=(0.0, -1.0), **kw)


# --------------------------------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------------------------------

def test_plane_matrix_puts_the_wall_corner_at_the_origin_and_the_axes_on_x_y_z():
    f = Facade.from_frame("A", 0, (10.0, 4.0, 2.0), (0.6, 0.8, 0.0), 12.0, 5.0)
    A = msfacade.plane_matrix(f)
    p = lambda xyz: (A @ np.array(list(xyz) + [1.0]))[:3]
    assert np.allclose(p(f.origin), 0, atol=1e-9)
    assert np.allclose(p(f.point_at(12.0, 0.0)), [12, 0, 0], atol=1e-9)
    assert np.allclose(p(f.point_at(0.0, 5.0)), [0, 5, 0], atol=1e-9)
    assert np.allclose(p(f.point_at(0.0, 0.0, 2.0)), [0, 0, 2], atol=1e-9)
    assert np.linalg.det(A[:3, :3]) == pytest.approx(1.0)


def test_plane_box_grows_the_wall_by_the_margins():
    f = facade_a()
    m = Margins(left=1, right=2, top=3, bottom=0.5, link_lr=False, link_tb=False)
    assert msfacade.plane_box(f, m) == (-1.0, -0.5, 22.0, 11.0)


def test_region_in_chunk_is_in_chunk_units_and_inverts_to_plane_metres():
    f = facade_a()
    m = Margins(left=1, right=1, top=1, bottom=1, depth=2)
    N = msfacade.plane_matrix(f) @ np.diag([5.0, 5.0, 5.0, 1.0])          # chunk units are 5 m
    centre, size, rot = msfacade.region_in_chunk(N, f, m)
    assert np.allclose(size, np.array([22.0, 10.0, 4.0]) / 5.0)
    assert np.allclose(N @ np.append(centre, 1.0), [10.0, 4.0, 0.0, 1.0])  # middle of the picture area, on the plane
    assert np.allclose(rot @ rot.T, np.eye(3), atol=1e-12)


def test_signature_changes_with_margins_method_and_frame_not_with_noise():
    f, enu = facade_a(), np.eye(4)
    base = msfacade.signature(f, Margins(), Method.TIE_POINTS, enu)
    assert base == msfacade.signature(f, Margins(), "tie_points", enu)
    assert base != msfacade.signature(f, Margins(depth=3.0), Method.TIE_POINTS, enu)
    assert base != msfacade.signature(f, Margins(), Method.DEPTH_MAPS, enu)
    shifted = np.eye(4)
    shifted[0, 3] = 10.0
    assert base != msfacade.signature(f, Margins(), Method.TIE_POINTS, shifted)
    g = facade_a()
    g.length += 0.001                                                      # a millimetre is the same facade
    assert base == msfacade.signature(g, Margins(), Method.TIE_POINTS, enu)


def test_work_label_names_the_site_and_the_letter():
    assert msfacade.work_label("CARRO", "B") == "AeroFacade_CARRO_B"


def test_rank_cameras_keeps_the_viewer_side_and_the_best_placed_first():
    centres = np.array([[0, 0, 20], [5, 0, 20], [0, 0, -20], [0, 0, 30], [0, 0, 40.0], [9, 0, 20]])
    forwards = np.array([[0, 0, -1], [0.6, 0, -0.8], [0, 0, 1], [1, 0, 0], [0, 0, -1.0], [0, 0, -1.0]])
    visible = np.array([1.0, 0.5, 1.0, 1.0, 0.0, 0.8])
    keep = msfacade.rank_cameras(centres, forwards, visible, centres[:, 2], max_cameras=10)
    assert list(keep) == [0, 1, 5]               # behind the plane, looking along the wall, or seeing nothing: out
    assert list(msfacade.rank_cameras(centres, forwards, visible, centres[:, 2], max_cameras=1)) == [0]


def test_rank_cameras_falls_back_to_every_seeing_camera_when_nobody_is_on_the_viewer_side():
    centres = np.array([[0, 0, -20], [3, 0, -20], [6, 0, -20.0]])
    forwards = np.array([[0, 0, 1.0]] * 3)
    keep = msfacade.rank_cameras(centres, forwards, np.array([1.0, 1.0, 0.5]), centres[:, 2])
    assert list(keep) == [0, 1, 2]


def test_spread_cameras_picks_far_apart_cameras():
    c = np.array([[x, 0, 0] for x in range(100)], float)
    pick = msfacade.spread_cameras(c, 4)
    assert len(pick) == 4 and 0 in pick or 99 in pick
    assert min(b - a for a, b in zip(pick, pick[1:])) > 20
    assert msfacade.spread_cameras(c[:3], 10) == [0, 1, 2]


def test_explain_gives_plain_english():
    assert "nothing to draw" in msfacade.explain(RuntimeError("Empty raster"))
    assert "memory" in msfacade.explain(MemoryError("out of memory"))
    assert msfacade.explain(RuntimeError("weird")) == "weird"


def test_meter_maps_percent_to_the_job_fraction_and_cancels_with_keyboard_interrupt():
    from aerotools.tools.aerofacade.backend import Progress
    seen = []
    flag = {"stop": False}
    prog = Progress(lambda f, t: seen.append((round(f, 3), t)), lambda: flag["stop"], 0.5, 1.0)
    meter = msfacade._Meter(prog, 0.2, 0.8, "working")
    meter(50.0)
    assert seen[-1] == (0.5 + 0.5 * 0.5, "working")
    flag["stop"] = True
    with pytest.raises(KeyboardInterrupt):
        meter(60.0)


def test_sample_points_cover_the_corners():
    pts = msfacade.sample_points((0, 0, 10, 4), 3)
    assert len(pts) == 9 and (0, 0) in pts and (10, 4) in pts and (5, 2) in pts


# --------------------------------------------------------------------------------------------------
# The backend on the fake chunk
# --------------------------------------------------------------------------------------------------

def test_project_info_and_availability(world):
    be = msfacade.MetashapeBackend(world.chunk)
    info = be.project_info()
    assert info["chunk"] == "Chunk 1" and info["outputs"].endswith("Outputs") and len(info["date"]) == 6
    assert be.threaded is False
    av = be.availability()
    assert av["tie_points"] == (True, "") and av["dense_cloud"][0] is False and "dense cloud" in av["dense_cloud"][1]
    assert av["depth_maps"][0] is True                       # computable: aligned photos
    world.chunk.point_cloud = types.SimpleNamespace(point_count=5)
    world.chunk.tie_points = TiePoints([], [])
    av = be.availability()
    assert av["dense_cloud"] == (True, "") and av["tie_points"][0] is False and "align" in av["tie_points"][1]


def test_read_points_converts_chunk_to_enu_metres(world):
    be = msfacade.MetashapeBackend(world.chunk)
    pts, cams = be.read_points()
    assert pts.shape == (6000, 3) and np.allclose(pts, world.points, atol=1e-9)
    assert cams.shape == (12, 3) and cams[:, 1].max() < -24


def test_detect_finds_the_wall_with_the_viewer_on_the_cameras_side(world):
    be = msfacade.MetashapeBackend(world.chunk, pump=lambda: None)
    seen = []
    facades = be.detect([(-8, -15), (28, -15), (28, 8), (-8, 8)], lambda f, t: seen.append(f), lambda: False)
    assert len(facades) >= 1
    f = max(facades, key=lambda x: x.length)
    assert f.length == pytest.approx(20.0, abs=1.5) and f.height == pytest.approx(8.0, abs=1.0)
    assert f.ny < -0.9                                      # the cameras are at y < 0: the picture is taken from there
    assert seen[0] == 0.0 and seen[-1] == 1.0


def test_detect_turns_a_wrongly_oriented_facade_round(world):
    be = msfacade.MetashapeBackend(world.chunk, pump=lambda: None)
    be.read_points()
    f = facade_a()
    f.__dict__.update(x0=20.0, y0=0.0, ux=-1.0, uy=0.0, nx=0.0, ny=1.0, azimuth=0.0)      # normal toward +y: away from the cameras
    be._orient([f], msfacade.Progress())
    assert f.ny == pytest.approx(-1.0) and f.x0 == pytest.approx(0.0, abs=1e-9) and f.ux == pytest.approx(1.0)
    assert f.azimuth == pytest.approx(180.0)


def test_detect_cancel(world):
    be = msfacade.MetashapeBackend(world.chunk, pump=lambda: None)
    with pytest.raises(Cancelled):
        be.detect([(-8, -15), (28, -15), (28, 8), (-8, 8)], lambda f, t: None, lambda: True)


def test_build_dems_makes_a_tagged_rotated_work_chunk_and_reuses_it(world):
    be = msfacade.MetashapeBackend(world.chunk)
    f, m = facade_a(), Margins()
    assert be.existing_dems([f], m, Method.TIE_POINTS) == {"A": False}
    done = []
    res = be.build_dems([f], m, Method.TIE_POINTS, lambda fr, t: done.append((fr, t)), lambda: False)
    assert res["A"][0] is True
    work = [c for c in world.doc.chunks if c is not world.chunk][0]
    assert work.label == "AeroFacade_SITE_A" and work.meta[msfacade.META_STAGE] == "dem"
    assert work.meta["aerofacade.source"] == str(world.chunk.key)
    assert work.crs.wkt.startswith("LOCAL_CS") and np.allclose(work.transform.matrix.a[:3, 3], [0, 0, 0] + np.array([0, 0, 0]), atol=1e3)
    n_dem = sum(1 for c in work.calls if c[0] == "buildDem")
    assert n_dem == 1 and done[-1][0] == 1.0
    box = [c for c in work.calls if c[0] == "buildDem"][0][1]["region"]
    assert (box.min[0], box.min[1], box.max[0], box.max[1]) == (-1.0, -1.0, 21.0, 9.0)
    assert world.chunk.elevation is None and world.chunk.orthomosaic is None and not world.chunk.calls[1:]      # the user's chunk: only the copy call
    assert be.existing_dems([f], m, Method.TIE_POINTS) == {"A": True}
    assert be.existing_dems([f], Margins(depth=3.0), Method.TIE_POINTS) == {"A": False}
    assert be.build_dems([f], m, Method.TIE_POINTS, None, lambda: False)["A"][1] == "Already built."
    assert sum(1 for c in work.calls if c[0] == "buildDem") == 1


def test_work_chunk_is_rotated_so_the_wall_is_horizontal(world):
    be = msfacade.MetashapeBackend(world.chunk)
    f = facade_a()
    be.build_dems([f], Margins(), Method.TIE_POINTS, None, lambda: False)
    work = [c for c in world.doc.chunks if c is not world.chunk][0]
    N = work.transform.matrix.a
    corner = N @ np.array([20.0 / 5, 0, 0, 1])               # the far end of the wall in chunk units
    assert np.allclose(corner[:3], [20, 0, 0], atol=1e-9)
    top = N @ np.array([0, 0, 8.0 / 5, 1])
    assert np.allclose(top[:3], [0, 8, 0], atol=1e-9)
    assert np.allclose(work.region.size.v, np.array([22, 10, 4]) / 5.0)


def test_depth_maps_are_computed_for_a_spread_subset_of_the_cameras(world):
    be = msfacade.MetashapeBackend(world.chunk, depth_max_cameras=5)
    res = be.build_dems([facade_a()], Margins(), Method.DEPTH_MAPS, None, lambda: False)
    assert res["A"][0] is True
    work = [c for c in world.doc.chunks if c is not world.chunk][0]
    dm = [c for c in work.calls if c[0] == "buildDepthMaps"][0][1]
    assert len(dm["cameras"]) == 5 and dm["downscale"] == 4
    assert [c for c in work.calls if c[0] == "buildDem"][0][1]["source_data"] == "depth"


def test_a_failure_returns_a_reason_and_leaves_no_work_chunk_behind(world):
    be = msfacade.MetashapeBackend(world.chunk)
    orig = FakeChunk.copy

    def failing_copy(self, items=None, keypoints=True):
        c = orig(self, items, keypoints)
        c.fail = "buildDem"
        return c
    FakeChunk.copy = failing_copy
    try:
        res = be.build_dems([facade_a()], Margins(), Method.TIE_POINTS, None, lambda: False)
    finally:
        FakeChunk.copy = orig
    ok, message = res["A"]
    assert ok is False and "nothing to draw" in message
    assert world.doc.chunks == [world.chunk]


def test_too_few_tie_points_on_the_facade_is_refused_before_metashape_is_called(world):
    be = msfacade.MetashapeBackend(world.chunk)
    f = facade_a()
    f.x0 = 500.0                                              # nowhere near the points
    ok, message = be.build_dems([f], Margins(), Method.TIE_POINTS, None, lambda: False)["A"]
    assert ok is False and "Too few tie points" in message and world.doc.chunks == [world.chunk]


def test_a_cancel_between_facades_returns_what_was_done(world):
    be = msfacade.MetashapeBackend(world.chunk)
    a, b = facade_a(), facade_a()
    b.letter = "B"
    state = {"stop": False}

    def progress(fraction, text):
        if "A: surface ready" in text:
            state["stop"] = True                              # the operator presses Cancel just as A finishes
    res = be.build_dems([a, b], Margins(), Method.TIE_POINTS, progress, lambda: state["stop"])
    assert "A" in res and res["A"][0] is True and "B" not in res


def test_removed_facades_are_ignored(world):
    be = msfacade.MetashapeBackend(world.chunk)
    f = facade_a()
    f.removed = True
    assert be.build_dems([f], Margins(), Method.TIE_POINTS, None, lambda: False) == {}


def test_orthos_and_export_write_named_files_with_a_georeferencing_text(world, tmp_path):
    be = msfacade.MetashapeBackend(world.chunk)
    f, m = facade_a(), Margins()
    res = be.build_orthos([f], m, Method.TIE_POINTS, 0.03, None, lambda: False)
    assert res["A"][0] is True and "733 x 333 px" in res["A"][1]
    work = [c for c in world.doc.chunks if c is not world.chunk][0]
    assert work.meta[msfacade.META_STAGE] == "ortho" and float(work.meta[msfacade.META_PIXEL]) == 0.03
    folder = str(tmp_path / "out")
    out = be.export([f], folder, "png", {"georef_txt": True}, None, lambda: False)
    path, ok, message = out[0]
    name = os.path.basename(path)
    assert ok and name.startswith("SITE_") and name.endswith("_facade_A.png") and os.path.isfile(path)
    txt = open(os.path.splitext(path)[0] + ".txt", encoding="utf-8").read()
    assert "pixel size m: 0.03000" in txt and "exact picture extent" in txt and "facade: A" in txt
    out = be.export([f], str(tmp_path / "out2"), "jpg", {"georef_txt": False}, None, lambda: False)
    assert out[0][0].endswith("_facade_A.jpg") and not os.path.isfile(os.path.splitext(out[0][0])[0] + ".txt")
    again = be.build_orthos([f], m, Method.TIE_POINTS, 0.03, None, lambda: False)
    assert again["A"][1] == "Already built." and sum(1 for c in work.calls if c[0] == "buildOrthomosaic") == 1


def test_a_picture_that_would_be_huge_is_refused(world):
    be = msfacade.MetashapeBackend(world.chunk)
    res = be.build_orthos([facade_a()], Margins(), Method.TIE_POINTS, 0.0005, None, lambda: False)   # 44000 x 20000 px
    ok, message = res["A"]
    assert ok is False and "bigger pixel size" in message


def test_export_of_a_facade_without_orthophoto_says_so(world, tmp_path):
    be = msfacade.MetashapeBackend(world.chunk)
    out = be.export([facade_a()], str(tmp_path / "o"), "png", {}, None, lambda: False)
    assert out[0][1] is False and "not built yet" in out[0][2]


def test_cleanup_removes_the_work_chunks_and_temp_files(world):
    be = msfacade.MetashapeBackend(world.chunk)
    be.build_dems([facade_a()], Margins(), Method.TIE_POINTS, None, lambda: False)
    tmp = be.tmp()
    assert os.path.isdir(tmp) and len(world.doc.chunks) == 2
    be.cleanup()
    assert world.doc.chunks == [world.chunk] and not os.path.isdir(tmp)


def test_keep_work_chunks_option_keeps_them(world):
    be = msfacade.MetashapeBackend(world.chunk, keep_work_chunks=True)
    be.build_dems([facade_a()], Margins(), Method.TIE_POINTS, None, lambda: False)
    be.cleanup()
    assert len(world.doc.chunks) == 2


def test_a_backend_opened_on_a_work_chunk_goes_back_to_its_source(world):
    be = msfacade.MetashapeBackend(world.chunk)
    be.build_dems([facade_a()], Margins(), Method.TIE_POINTS, None, lambda: False)
    work = [c for c in world.doc.chunks if c is not world.chunk][0]
    again = msfacade.MetashapeBackend(work)
    assert again._src() is world.chunk


def test_dxf_places_the_picture_in_its_vertical_plane(world, tmp_path):
    be = msfacade.MetashapeBackend(world.chunk)
    f, m = facade_a(), Margins()
    be.build_orthos([f], m, Method.TIE_POINTS, 0.03, None, lambda: False)
    out = be.export([f], str(tmp_path / "dxf"), "png", {"georef_txt": False, "dxf": True, "dxf_epsg": 3942}, None, lambda: False)
    dxf = os.path.splitext(out[0][0])[0] + ".dxf"
    lines = open(dxf, encoding="ascii").read().splitlines()
    i = lines.index("AcDbRasterImage", lines.index("ENTITIES"))
    code = {}
    j = i + 1
    while lines[j] != "340":
        code[lines[j]] = float(lines[j + 1]) if lines[j] in ("10", "20", "30", "11", "21", "31", "12", "22", "32", "13", "23") else lines[j + 1]
        j += 2
    assert (code["10"], code["20"], code["30"]) == pytest.approx((-1.0, 0.0, -1.0))         # bottom-left corner of the picture, in the plane y = 0
    assert (code["11"], code["21"], code["31"]) == pytest.approx((0.03, 0.0, 0.0))          # one pixel to the right = 3 cm along the wall
    assert (code["12"], code["22"], code["32"]) == pytest.approx((0.0, 0.0, 0.03))          # one pixel up = 3 cm up (vertical)
    assert (code["13"], code["23"]) == (733.0, 333.0)
    assert os.path.basename(out[0][0]) in lines


def test_the_flat_dxf_writer_is_unchanged_without_3d_keys():
    from aerotools.tools.aeroexports import dxfout
    text = dxfout.build_dxf([{"layer": "L", "file": "a.tif", "size_px": (10, 20), "insert": (5.0, 6.0), "pixel": (0.5, 0.25)}])
    lines = text.splitlines()
    i = lines.index("AcDbRasterImage", lines.index("ENTITIES"))
    assert lines[i + 3:i + 15] == ["10", "5.0", "20", "6.0", "30", "0.0", "11", "0.5", "21", "0.0", "31", "0.0"]


# --------------------------------------------------------------------------------------------------
# The drawing sheet
# --------------------------------------------------------------------------------------------------

def facade_b(**kw):
    return Facade.from_frame("B", 1, (0.0, 0.0, 3.0), (1.0, 0.0, 0.0), 20.0, 8.0, n=(0.0, -1.0), **kw)


class OffsetCrs(object):
    """A coordinate system that adds a fixed offset (east, north, ellipsoidal height) to the geocentric point: what `project` does, minus the geodesy."""
    wkt = "PROJCS[fake]"

    def __init__(self, offset=(2175840.0, 1206740.0, 120.0)):
        self.offset = np.array(offset)

    def project(self, p):
        return Vector(np.array(list(p)) + self.offset)


def built(world, be, facades):
    for f in facades:
        assert be.build_orthos([f], Margins(), Method.TIE_POINTS, 0.03, None, lambda: False)[f.letter][0] is True


def test_the_sheet_of_two_pictures_is_written_with_relative_heights(world, tmp_path):
    from aerotools.tools.aerofacade import sheetout
    be = msfacade.MetashapeBackend(world.chunk)
    a, b = facade_a(), facade_b()
    built(world, be, [a, b])
    folder = str(tmp_path / "out")
    out = be.export([a, b], folder, "png", {"georef_txt": False, "tools": {}, "sheet": {"dxf": True}}, None, lambda: False)
    names = [os.path.basename(p) for p, ok, m in out if ok]
    assert names == ["SITE_%s_facade_A.png" % be.project_info()["date"], "SITE_%s_facade_B.png" % be.project_info()["date"],
                     "SITE_%s_facades.dxf" % be.project_info()["date"]]
    assets = os.listdir(os.path.join(folder, "00_Assets", "ortho"))
    assert len(assets) == 2 and all(n.endswith(".png") for n in assets)
    text = open(os.path.join(folder, names[-1]), newline="").read()
    assert "FACADE SUD (A)" in text and "FACADE SUD (B)" in text                    # two south walls: the letter tells them apart
    p = [(a_.strip(), b_) for a_, b_ in zip(text.split("\r\n")[0::2], text.split("\r\n")[1::2])]
    images = [i for i, pair in enumerate(p) if pair == ("0", "IMAGE")]
    y_of = [float(dict(p[i:i + 30])["20"]) for i in images]
    assert y_of == pytest.approx([0.0, 3.0])                                      # B stands 3 m higher than A, A is the common zero


def test_the_sheet_alone_when_the_loose_pictures_are_not_wanted(world, tmp_path):
    be = msfacade.MetashapeBackend(world.chunk)
    a = facade_a()
    built(world, be, [a])
    folder = str(tmp_path / "only")
    out = be.export([a], folder, "png", {"georef_txt": True, "pictures": False, "tools": {}, "sheet": {"dxf": True}}, None, lambda: False)
    assert sorted(os.listdir(folder)) == ["00_Assets", [n for n in os.listdir(folder) if n.endswith(".dxf")][0]]
    assert [ok for _p, ok, _m in out] == [True]


def test_altitude_goes_through_the_chunk_transform_into_the_height_crs(world, tmp_path, monkeypatch):
    be = msfacade.MetashapeBackend(world.chunk)
    a, b = facade_a(), facade_b()
    built(world, be, [a, b])
    monkeypatch.setattr(be, "_height_crs", lambda: (3942, OffsetCrs(), "fake"))
    work = be._work_for(b)
    h, x, y = be.bottom_edge(b, work, True)
    assert (x, y) == pytest.approx((2175840.0 + 10.0, 1206740.0 + 0.0))              # middle of the picture along the wall: east 10 m
    assert h == pytest.approx(120.0 + 3.0 - 1.0)                                      # picture bottom = wall foot (3 m) - bottom margin (1 m), plus the CRS height offset
    h0, x0, y0 = be.bottom_edge(b, work, False)
    assert (h0, x0, y0) == pytest.approx((2.0, 0.0, 0.0))
    folder = str(tmp_path / "z")
    out = be.export([a, b], folder, "png", {"georef_txt": False, "tools": {}, "sheet": {"dxf": True, "z": True, "vertical": "ellipsoid"}}, None,
                    lambda: False)
    assert all(ok for _p, ok, _m in out)
    dxf = [p for p, ok, m in out if p.endswith(".dxf")][0]
    text = open(dxf, newline="").read()
    assert "Y = altitude (m)" in text and "ellipsoidal heights" in text and "EPSG:3942" in text


def test_a_missing_geoid_grid_stops_the_sheet_but_not_the_pictures(world, tmp_path, monkeypatch):
    from aerotools.tools.aeroexports import crs as exports_crs
    monkeypatch.setattr(exports_crs, "own_dir", lambda: str(tmp_path / "no_geoid"))
    be = msfacade.MetashapeBackend(world.chunk)
    a = facade_a()
    built(world, be, [a])
    monkeypatch.setattr(be, "_height_crs", lambda: (3942, OffsetCrs(), "fake"))
    folder = str(tmp_path / "nz")
    out = be.export([a], folder, "png", {"georef_txt": False, "tools": {}, "sheet": {"dxf": True, "z": True, "vertical": "fr_ign_RAC09"}}, None,
                    lambda: False)
    assert [ok for _p, ok, _m in out].count(True) == 1
    bad = [m for _p, ok, m in out if not ok]
    assert len(bad) == 1 and "not on this computer" in bad[0]
    assert not [n for n in os.listdir(folder) if n.endswith((".dxf", ".dwg"))] and not os.path.isdir(os.path.join(folder, "00_Assets"))


def test_a_project_without_a_coordinate_system_offers_no_altitudes(world):
    be = msfacade.MetashapeBackend(world.chunk)
    world.chunk.crs = None
    assert be.height_context() == {"epsg": None, "lonlat": None, "crs_name": ""}
    world.chunk.crs = types.SimpleNamespace(wkt='LOCAL_CS["x"]')
    assert be.height_context()["epsg"] is None
    world.chunk.crs = CoordinateSystem("fake geographic")
    assert be.height_context()["epsg"] == msfacade.utm_epsg(0.0, 0.0) == 32631


def test_utm_zone_of_a_position():
    assert msfacade.utm_epsg(9.0, 42.0) == 32632
    assert msfacade.utm_epsg(-3.7, 40.4) == 32630
    assert msfacade.utm_epsg(151.2, -33.9) == 32756


def test_ecw_without_global_mapper_is_refused_with_a_reason_and_writes_nothing(world, tmp_path):
    be = msfacade.MetashapeBackend(world.chunk)
    a = facade_a()
    built(world, be, [a])
    folder = str(tmp_path / "ecw")
    out = be.export([a], folder, "ecw", {"tools": {}}, None, lambda: False)
    assert len(out) == 1 and out[0][1] is False and "Global Mapper" in out[0][2] and not os.path.isdir(folder)
