"""Aero-Facade FakeBackend: the protocol end to end on the synthetic building (no sleep)."""

import os
import struct

import pytest

np = pytest.importorskip("numpy")

from aerotools.tools.aerofacade import fake_backend as FB  # noqa: E402
from aerotools.tools.aerofacade.backend import Backend, Cancelled  # noqa: E402
from aerotools.tools.aerofacade.model import Availability, Margins, Method  # noqa: E402

AROUND_BUILDING = [(-5, -20), (75, -20), (75, 22), (-5, 22)]
AROUND_NEIGHBOUR = [(-40, 15), (-10, 15), (-10, 45), (-40, 45)]


@pytest.fixture(scope="module")
def fb():
    return FB.FakeBackend(delay=0)


@pytest.fixture(scope="module")
def found(fb):
    return fb.detect(AROUND_BUILDING, None, None)


def test_is_a_backend(fb):
    assert isinstance(fb, Backend)
    info = fb.project_info()
    assert set(info) == {"chunk", "site", "date", "outputs", "crs"}
    assert len(info["date"]) == 6


def test_availability_flags_and_reasons():
    a = Availability.from_dict(FB.FakeBackend(delay=0).availability())
    assert all(a.ok(m) for m in Method)
    a = Availability.from_dict(FB.FakeBackend(delay=0, dense_cloud=False, depth_maps=False).availability())
    assert a.ok("tie_points") and not a.ok("dense_cloud") and not a.ok("depth_maps")
    assert a.reason("dense_cloud") == "no dense cloud in this chunk"
    assert a.reason("tie_points") == ""


def test_ortho_preview_is_a_picture_with_a_metric_extent(fb):
    img, ext = fb.ortho_preview(800)
    assert img.dtype == np.uint8 and img.ndim == 3 and img.shape[2] == 3
    assert max(img.shape[:2]) == 800
    left, bottom, right, top = ext
    assert right > left and top > bottom
    # same pixel size in x and y: the picture is not stretched
    assert (right - left) / img.shape[1] == pytest.approx((top - bottom) / img.shape[0], rel=0.01)
    # the building lies inside the extent, in the middle
    assert left < 0 < 66 < right and bottom < 0 < 14 < top
    # a roof (terracotta: red > green > blue) is drawn at the middle of the building
    px = int((33 - left) / (right - left) * img.shape[1])
    py = int((top - 7) / (top - bottom) * img.shape[0])
    r, g, b = [int(v) for v in img[py, px]]
    assert r > g > b


def test_ortho_preview_without_orthomosaic_raises():
    with pytest.raises(RuntimeError, match="orthomosaic"):
        FB.FakeBackend(delay=0, ortho=False).ortho_preview()


def test_detect_finds_the_building_walls(found):
    assert [f.letter for f in found] == list("ABCDEF")
    lengths = sorted(round(f.length) for f in found)
    assert lengths[-1] == 66                         # the north wall, one plane
    assert lengths.count(22) == 3                    # the three south sections (the set-back is its own facade)
    assert lengths.count(14) == 2                    # the gables
    for f in found:
        assert f.height > 10 and f.points > 400
        assert 0.0 < f.score <= 1.0


def test_detect_orients_every_facade_to_the_outside(found):
    facing = sorted(f.facing for f in found)
    assert facing == sorted(["N", "S", "S", "S", "E", "W"])
    # the normal points away from the building centre
    for f in found:
        c = np.array(f.centre[:2]) - np.array(FB.CENTRE)
        assert c @ np.array(f.n[:2]) > 0


def test_detect_stays_inside_the_polygon(fb):
    # a polygon around the neighbour house finds only the house (four walls), none of the main building
    fs = fb.detect(AROUND_NEIGHBOUR, None, None)
    assert 2 <= len(fs) <= 4
    assert all(f.centre[0] < -10 for f in fs)


def test_detect_progress_is_monotonic_and_ends_at_one(fb):
    seen = []
    fb.detect(AROUND_BUILDING, lambda f, t: seen.append((f, t)), None)
    fr = [f for f, _t in seen]
    assert fr == sorted(fr) and fr[-1] == 1.0 and len(fr) >= 6
    assert all(isinstance(t, str) for _f, t in seen)


def test_detect_honours_cancel(fb):
    with pytest.raises(Cancelled):
        fb.detect(AROUND_BUILDING, None, lambda: True)
    state = {"n": 0}

    def late():
        state["n"] += 1
        return state["n"] > 8
    with pytest.raises(Cancelled):
        fb.detect(AROUND_BUILDING, None, late)


def test_dem_orthos_thumbnails_and_export_flow(tmp_path):
    fb = FB.FakeBackend(delay=0, outputs=str(tmp_path), site="CARRO", date="261106")
    fs = fb.detect(AROUND_BUILDING, None, None)
    m = Margins()
    assert fb.existing_dems(fs, m, Method.DEPTH_MAPS) == {f.letter: False for f in fs}
    # an orthophoto cannot be built before its DEM
    assert fb.build_orthos(fs[:1], m, "depth_maps", 0.03, None, None)["A"][0] is False
    res = fb.build_dems(fs, m, Method.DEPTH_MAPS, None, None)
    assert all(ok for ok, _msg in res.values()) and set(res) == {f.letter for f in fs}
    assert all(fb.existing_dems(fs, m, "depth_maps").values())
    assert not any(fb.existing_dems(fs, m, Method.TIE_POINTS).values())        # another method: not built
    m2 = m.copy()
    m2.set_left(3.0)
    assert not any(fb.existing_dems(fs, m2, "depth_maps").values())            # other margins: not built
    res = fb.build_orthos(fs, m, "depth_maps", 0.03, None, None)
    assert all(ok for ok, _msg in res.values())
    th = fb.thumbnail(fs[0])
    assert th.dtype == np.uint8 and th.shape[1] == 240 and th.shape[2] == 3
    out = fb.export(fs[:2], str(tmp_path / "Outputs"), "png", {"georef_txt": True, "dxf": True}, None, None)
    assert [ok for _p, ok, _m in out] == [True, True]
    names = sorted(os.listdir(str(tmp_path / "Outputs")))
    assert names == ["CARRO_261106_facade_A.dxf", "CARRO_261106_facade_A.png", "CARRO_261106_facade_A.txt",
                     "CARRO_261106_facade_B.dxf", "CARRO_261106_facade_B.png", "CARRO_261106_facade_B.txt"]
    with open(out[0][0], "rb") as fh:
        assert fh.read(8) == b"\x89PNG\r\n\x1a\n"
    txt = open(os.path.join(str(tmp_path / "Outputs"), "CARRO_261106_facade_A.txt")).read()
    assert "pixel size m" in txt and "wall length m" in txt


def test_failures_and_removed_facades(tmp_path):
    fb = FB.FakeBackend(delay=0, outputs=str(tmp_path), fail={"B": "No depth maps cover this facade"}, fail_ortho={"C": "Out of memory"})
    fs = fb.detect(AROUND_BUILDING, None, None)
    fs[3].removed = True                                                       # D is not processed
    res = fb.build_dems(fs, Margins(), "depth_maps", None, None)
    assert res["B"] == (False, "No depth maps cover this facade")
    assert "D" not in res and res["A"][0] is True
    res = fb.build_orthos(fs, Margins(), "depth_maps", 0.05, None, None)
    assert res["C"] == (False, "Out of memory")
    assert res["B"][0] is False                                                # no DEM was built for B
    assert res["A"][0] is True


def test_existing_shortcut_and_non_strict():
    fb = FB.FakeBackend(delay=0, existing=("A", "B"), strict=True)
    fs = fb.detect(AROUND_BUILDING, None, None)
    ex = fb.existing_dems(fs, Margins(), "dense_cloud")
    assert ex["A"] and ex["B"] and not ex["C"]
    assert fb.build_orthos(fs[:1], Margins(), "dense_cloud", 0.05, None, None)["A"][0] is True


def test_build_cancel_returns_what_is_done():
    fb = FB.FakeBackend(delay=0)
    fs = fb.detect(AROUND_BUILDING, None, None)
    calls = {"n": 0}

    def cancel_after_two():
        calls["n"] += 1
        return calls["n"] > 12
    res = fb.build_dems(fs, Margins(), "tie_points", None, cancel_after_two)
    assert 0 < len(res) < len(fs)


def test_thumbnail_before_build_raises():
    fb = FB.FakeBackend(delay=0)
    fs = fb.detect(AROUND_BUILDING, None, None)
    with pytest.raises(RuntimeError):
        fb.thumbnail(fs[0])


def test_export_unbuilt_and_failing(tmp_path):
    fb = FB.FakeBackend(delay=0, fail_export={"A": "Disk full"})
    fs = fb.detect(AROUND_BUILDING, None, None)
    out = fb.export(fs[:2], str(tmp_path / "o"), "png", {}, None, None)
    assert all(not ok for _p, ok, _m in out)
    fb.build_dems(fs[:2], Margins(), "tie_points", None, None)
    fb.build_orthos(fs[:2], Margins(), "tie_points", 0.05, None, None)
    out = fb.export(fs[:2], str(tmp_path / "o"), "png", {}, None, None)
    assert out[0][1:] == (False, "Disk full") and out[1][1] is True


def test_picture_margins_change_the_size():
    f = FB.FakeBackend(delay=0).detect(AROUND_BUILDING, None, None)[0]
    small, p1 = FB.render_facade(f, Margins(left=0, right=0, top=0, bottom=0), 0.2)
    big, p2 = FB.render_facade(f, Margins(left=5, right=5, top=3, bottom=3), 0.2)
    assert big.shape[1] > small.shape[1] and big.shape[0] > small.shape[0]
    assert p1 == pytest.approx(0.2)
    # the cap keeps files small: 3 cm on a 66 m wall would be 2200 px, it is limited to 1400
    huge, p3 = FB.render_facade(f, Margins(), 0.03)
    assert max(huge.shape[:2]) <= 1400 and p3 > 0.03


def test_image_writers(tmp_path):
    rgb = np.random.RandomState(0).randint(0, 255, (12, 20, 3)).astype(np.uint8)
    png, tif = str(tmp_path / "a.png"), str(tmp_path / "a.tif")
    FB.write_image(png, rgb, "png")
    FB.write_image(tif, rgb, "tif")
    data = open(png, "rb").read()
    w, h = struct.unpack(">II", data[16:24])
    assert (w, h) == (20, 12)
    t = open(tif, "rb").read()
    assert t[:4] == b"II*\x00"
    assert t[-rgb.size:] == rgb.tobytes()
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("Pillow missing")
    assert np.array_equal(np.asarray(Image.open(png).convert("RGB")), rgb)
    assert np.array_equal(np.asarray(Image.open(tif).convert("RGB")), rgb)
    FB.write_image(str(tmp_path / "a.jpg"), rgb, "jpg")
    assert os.path.getsize(str(tmp_path / "a.jpg")) > 100
    with pytest.raises(ValueError):
        FB.write_image(str(tmp_path / "a.bmp"), rgb, "bmp")


def test_scene_is_deterministic():
    a, b = FB.build_scene(3), FB.build_scene(3)
    assert np.array_equal(a["points"], b["points"])
    assert not np.array_equal(a["points"], FB.build_scene(4)["points"])
    assert len(a["points"]) > 20000
