"""Pinning diagnostic: cross-validation finds the offset pin (synthetic cameras)."""

import math

import pytest

from aerotools import msapi
from aerotools.tools.fastgcp import diagnostic

F, CX, CY = 2000.0, 2000.0, 1500.0


def unit(v):
    n = math.sqrt(sum(c * c for c in v))
    return tuple(c / n for c in v)


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


class Camera(object):
    """Distortion-free camera, above the ground, looking at the point (0, 0, 0)."""

    def __init__(self, name, center):
        self.name, self.center = name, center
        self.axis = unit(tuple(-c for c in center))
        self.right = unit(cross(self.axis, (0, 0, 1)))
        self.up = cross(self.axis, self.right)

    def project(self, point):
        delta = tuple(p - c for p, c in zip(point, self.center))
        depth = dot(delta, self.axis)
        return CX + F * dot(delta, self.right) / depth, CY + F * dot(delta, self.up) / depth

    def ray(self, x, y):
        direction = tuple(self.axis[i] + (x - CX) / F * self.right[i] + (y - CY) / F * self.up[i] for i in range(3))
        return self.center, direction


CAMERAS = [Camera("IMG_%d" % i, (40 * math.cos(a), 40 * math.sin(a), 60 + 5 * i))
           for i, a in enumerate([0.0, 1.3, 2.6, 3.9, 5.2])]
POINT = (1.0, 2.0, 0.0)


class Marker(object):
    def __init__(self, label, picks, position=POINT):
        self.label, self.picks, self.position = label, picks, position


@pytest.fixture
def fake_project(monkeypatch):
    by_camera = {c.name: c for c in CAMERAS}
    monkeypatch.setattr(msapi, "markers", lambda chk: chk)
    monkeypatch.setattr(msapi, "marker_label", lambda m: m.label)
    monkeypatch.setattr(msapi, "projection_cameras", lambda m: [by_camera[n] for n in m.picks])
    monkeypatch.setattr(msapi, "get_projection", lambda m, cam: m.picks[cam.name])
    monkeypatch.setattr(msapi, "is_proposal", lambda m, cam: False)
    monkeypatch.setattr(msapi, "camera_label", lambda cam: cam.name)
    monkeypatch.setattr(msapi, "project_point", lambda cam, p: cam.project(tuple(p)))
    monkeypatch.setattr(msapi, "pixel_ray", lambda cam, x, y: cam.ray(x, y))


def picks(shift=None, point=POINT, cameras=CAMERAS):
    out = {}
    for cam in cameras:
        x, y = cam.project(point)
        if shift and cam.name == shift[0]:
            x, y = x + shift[1], y + shift[2]
        out[cam.name] = (x, y, True)
    return out


def test_leave_one_out_gives_the_true_point_for_perfect_picks():
    rays = [cam.ray(*cam.project(POINT)) for cam in CAMERAS]
    for point in diagnostic.leave_one_out_points(rays):
        assert point == pytest.approx(POINT, abs=1e-6)
    assert diagnostic.leave_one_out_points(rays[:2]) == [None, None]            # nothing to cross-check against


def test_perfect_picks_give_a_clean_diagnostic(fake_project):
    result = diagnostic.analyze(diagnostic.collect(
        [Marker("A", picks()), Marker("B", picks(point=(-3, 1, 0)), position=(-3, 1, 0))]))
    assert diagnostic.is_clean(result)
    assert result["summary"]["rms_px"] < 1e-6 and result["summary"]["checked"] == 2


def test_a_shifted_pick_is_found_and_is_the_worst(fake_project):
    good = [Marker("M%d" % i, picks(point=(i, -i, 0)), position=(i, -i, 0)) for i in range(6)]
    bad = Marker("BAD", picks(shift=("IMG_2", 25.0, -10.0)))
    result = diagnostic.analyze(diagnostic.collect(good + [bad]))
    first = result["projections"][0]
    assert (first["label"], first["camera_label"]) == ("BAD", "IMG_2")
    # Cross-validation finds the offset: sqrt(25^2 + 10^2) = 26.9 px.
    assert first["px"] == pytest.approx(math.hypot(25, 10), rel=0.01)
    assert [p["label"] for p in result["projections"]] == ["BAD"]            # the good pins of the same marker are not accused


def test_a_badly_aligned_photo_is_flagged_over_several_markers(fake_project):
    markers = [Marker("M%d" % i, picks(shift=("IMG_1", 6.0, 6.0), point=(i, 2 - i, 0))) for i in range(5)]
    result = diagnostic.analyze(diagnostic.collect(markers))
    assert [p["camera_label"] for p in result["photos"]] == ["IMG_1"]


def test_few_picks_and_pending_proposals_are_reported(fake_project, monkeypatch):
    one = Marker("ONE", {"IMG_0": picks()["IMG_0"]})
    two = Marker("TWO", {name: picks()[name] for name in ("IMG_0", "IMG_1")})
    unvalidated = Marker("AI", dict(picks(), IMG_4=(10.0, 10.0, False)))
    monkeypatch.setattr(msapi, "is_proposal", lambda m, cam: m.label == "AI" and cam.name == "IMG_4")
    result = diagnostic.analyze(diagnostic.collect([one, two, unvalidated]))
    kinds = {(item["label"], item["kind"]) for item in result["markers"]}
    assert kinds == {("ONE", "few"), ("TWO", "unchecked"), ("AI", "proposals")}


def test_a_noisy_project_does_not_flag_everything(fake_project):
    # Noise of 2 to 3 px everywhere: the threshold follows the project's median, but never goes below 3 px.
    noisy = []
    for i in range(6):
        p = picks(point=(i, 1, 0))
        p["IMG_%d" % (i % 5)] = (p["IMG_%d" % (i % 5)][0] + 2.5, p["IMG_%d" % (i % 5)][1], True)
        noisy.append(Marker("N%d" % i, p))
    result = diagnostic.analyze(diagnostic.collect(noisy))
    assert result["projections"] == []


def test_the_medians_and_rms_helpers():
    assert diagnostic.median([3, 1, 2]) == 2 and diagnostic.median([1, 2, 3, 4]) == 2.5 and diagnostic.median([]) == 0.0
    assert diagnostic.rms([3, 4]) == pytest.approx(math.sqrt(12.5)) and diagnostic.rms([]) == 0.0


def test_the_review_lists_the_weakest_markers_and_photos_first():
    from aerotools.tools.fastgcp import review
    entries = [{"label": "A", "conf": 0.9}, {"label": "B", "conf": 0.4}, {"label": "A", "conf": 0.8},
               {"label": "C", "conf": 0.6}, {"label": "B", "conf": 0.95}]
    rev = review.Review("checker", "r", entries)
    assert rev.labels() == ["B", "C", "A"]
    assert [e["conf"] for e in rev.entries_for("B")] == [0.4, 0.95]
    assert rev.weakest("A") == 0.8 and rev.weakest("none") == 1.0
