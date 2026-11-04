import pytest

from aerotools import msapi


@pytest.fixture(autouse=True)
def _bind(ms):
    msapi.Metashape = ms


@pytest.fixture
def world(ms):
    V, C = ms.Vector, ms.Camera
    point = V(0, 0, 0)
    far_projected = C(1, V(0, 0, -100))      # a deja une projection, la plus lointaine
    near = C(2, V(0, 0, -10))
    mid = C(3, V(0, 0, -30))
    behind = C(4, V(0, 0, 10))               # regarde +Z, le point est derriere
    off_image = C(5, V(50, 0, -10))          # devant, proche, mais hors image
    too_far = C(6, V(0, 0, -900))            # au-dela de 400 m
    not_aligned = C(7, V(0, 0, -5), aligned=False)
    disabled = C(8, V(0, 0, -6), enabled=False)
    cams = [far_projected, near, mid, behind, off_image, too_far, not_aligned, disabled]
    marker = ms.Marker("GCP1", position=point, projections={far_projected: object()})
    chunk = ms.Chunk(cams, [marker])
    ms.app.document.chunk = chunk
    return chunk, marker, cams


def test_projections_first_then_nearest_photos(world):
    chunk, marker, cams = world
    result, diag = msapi.cameras_seeing_marker(chunk, marker)
    assert [c.key for c in result] == [1, 2, 3]
    assert diag["projections"] == 1
    assert diag["dans_image"] == 2


def test_camera_behind_and_outside_image_excluded(world):
    chunk, marker, _ = world
    result, diag = msapi.cameras_seeing_marker(chunk, marker)
    keys = [c.key for c in result]
    assert 4 not in keys      # derriere
    assert 5 not in keys      # hors image
    assert 6 not in keys      # trop loin
    assert 7 not in keys      # non alignee
    assert 8 not in keys      # desactivee
    assert diag["devant"] > diag["dans_image"]


def test_distance_limit_scales_with_chunk_scale(world):
    chunk, marker, _ = world
    chunk.transform.scale = 10.0      # 1 unite interne = 10 m : limite 40 unites
    result, _ = msapi.cameras_seeing_marker(chunk, marker)
    assert [c.key for c in result] == [1, 2, 3]
    chunk.transform.scale = 100.0     # limite 4 unites : plus aucune photo proche
    result, _ = msapi.cameras_seeing_marker(chunk, marker)
    assert [c.key for c in result] == [1]


def test_marker_without_position_returns_only_projections(ms):
    cam = ms.Camera(1, ms.Vector(0, 0, -10))
    marker = ms.Marker("X", position=None, projections={cam: object()})
    chunk = ms.Chunk([cam], [marker])
    ms.app.document.chunk = chunk
    result, diag = msapi.cameras_seeing_marker(chunk, marker)
    assert result == [cam]
    assert diag["point"] is False


def test_no_projection_no_position(ms):
    marker = ms.Marker("X")
    result, diag = msapi.cameras_seeing_marker(ms.Chunk([], [marker]), marker)
    assert result == [] and diag["point"] is False


def test_add_menu_item_falls_back_without_shortcut(ms):
    calls = []

    def picky(label, callback, **kwargs):
        if "shortcut" in kwargs:
            raise TypeError("pas de raccourci")
        calls.append(label)

    ms.app.addMenuItem = picky
    assert msapi.add_menu_item("Aero/X", lambda: None, "F8") is True
    assert calls == ["Aero/X"]


def test_message_box_falls_back_to_print(ms, capsys):
    def boom(_text):
        raise RuntimeError

    ms.app.messageBox = boom
    msapi.message_box("bonjour")
    assert "bonjour" in capsys.readouterr().out


def test_open_photo_reports_success_and_failure(ms):
    opened = []
    ms.app.photo_view = type("View", (), {"open": lambda self, cam: opened.append(cam)})()
    assert msapi.open_photo("cam") == (True, "")
    assert opened == ["cam"]

    def refuse(self, cam):
        raise RuntimeError("refus")

    ms.app.photo_view = type("View", (), {"open": refuse})()
    ok, message = msapi.open_photo("cam")
    assert ok is False and "refus" in message

    ms.app.photo_view = None
    assert msapi.open_photo("cam")[0] is False


def test_app_version_is_empty_when_unavailable(ms):
    assert msapi.app_version() == ""
    ms.app.version = "2.2.1"
    assert msapi.app_version() == "2.2.1"


def test_photo_tab_named_like_a_blacklisted_word_is_still_a_photo(ms):
    camera = ms.Camera(1, ms.Vector(0, 0, 0))
    camera.label = "DEM_0001"
    chunk = ms.Chunk([camera], [])
    assert msapi._title_is_photo("DEM_0001.JPG", chunk) is True
    assert msapi._title_is_photo("DEM", chunk) is False
    assert msapi._title_is_photo("Model", chunk) is False


def test_central_tab_under_survives_a_widget_whose_parent_is_not_callable(monkeypatch):
    """La console Python de Metashape masque `parent` : pas d'exception."""
    from aerotools import msapi

    class Odd(object):
        parent = None

    monkeypatch.setattr(msapi.QtWidgets.QApplication, "widgetAt",
                        staticmethod(lambda pos: Odd()))
    assert msapi.central_tab_under(object()) == (None, False)


def test_main_window_wrapper_is_kept_alive_and_renewed_when_dead(monkeypatch):
    """Sans reference durable, PySide2 invalide les wrappers des enfants."""
    class Window(object):
        dead = False

        def objectName(self):
            if self.dead:
                raise RuntimeError("Internal C++ object already deleted")
            return ""

    first, second = Window(), Window()
    queue = [first, second]

    class Instance(object):
        def topLevelWidgets(self):
            return [queue[0]]

    monkeypatch.setattr(msapi.QtWidgets, "QMainWindow", Window)
    monkeypatch.setattr(msapi.QtWidgets.QApplication, "instance",
                        staticmethod(lambda: Instance()))
    monkeypatch.setattr(msapi, "_MAIN_WINDOW", None)
    assert msapi.main_window() is first
    queue.pop(0)
    assert msapi.main_window() is first
    first.dead = True
    assert msapi.main_window() is second


def test_select_marker_makes_it_the_only_selected_one(ms):
    first, second, third = ms.Marker("A"), ms.Marker("B"), ms.Marker("C")
    chunk = ms.Chunk([], [first, second, third])
    first.selected = True
    assert msapi.select_marker(chunk, third) is True
    assert [m.label for m in msapi.selected_markers(chunk)] == ["C"]
    assert msapi.select_marker(chunk, third) is True
    assert [m.label for m in msapi.selected_markers(chunk)] == ["C"]
