"""Faux modules Metashape et PySide2 pour tester la logique pure d'AeroTools."""

import builtins
import os
import sys
import tempfile
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _real_settings():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "HELIX", "AeroTools", "settings.json")


def _snapshot(path):
    import hashlib
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest(), os.path.getmtime(path)
    except OSError:
        return None


# The machine's real settings are read now, before anything can write to them. From here to the end of the run LOCALAPPDATA points
# to a throw-away folder (code that runs at import or collection time, or from a test without the fixtures below, is covered too),
# and the session fails if the real file changed.
_REAL_SETTINGS = _real_settings()
_REAL_BEFORE = _snapshot(_REAL_SETTINGS)
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="aerotools_pytest_")


@pytest.fixture(autouse=True, scope="session")
def _real_settings_untouched():
    yield
    assert _snapshot(_REAL_SETTINGS) == _REAL_BEFORE, "a test wrote the real settings file " + _REAL_SETTINGS


@pytest.fixture(autouse=True)
def _journal_log_in_tmp(tmp_path_factory, monkeypatch):
    """Les tests n'ecrivent jamais dans le vrai aerotools.log du poste."""
    from aerotools import journal
    monkeypatch.setattr(journal, "log_path", lambda: str(tmp_path_factory.getbasetemp() / "aerotools.log"))


# --------------------------------------------------------------------------
# Faux Metashape
# --------------------------------------------------------------------------

class Vector(object):
    def __init__(self, *args):
        values = args[0] if len(args) == 1 else args
        self.x, self.y, self.z = (list(values) + [0.0, 0.0, 0.0])[:3]

    def __sub__(self, other):
        return Vector(self.x - other.x, self.y - other.y, self.z - other.z)

    def __add__(self, other):
        return Vector(self.x + other.x, self.y + other.y, self.z + other.z)

    def __mul__(self, factor):
        return Vector(self.x * factor, self.y * factor, self.z * factor)

    def __getitem__(self, index):
        return (self.x, self.y, self.z)[index]

    def norm(self):
        return (self.x ** 2 + self.y ** 2 + self.z ** 2) ** 0.5


class _Ref(object):
    location = None


class Marker(object):
    class Projection(object):
        def __init__(self, coord=None, pinned=False):
            self.coord = coord
            self.pinned = pinned

    def __init__(self, label="pt", position=None, projections=None):
        self.label = label
        self.position = position
        self.projections = dict(projections or {})
        self.reference = _Ref()
        self.selected = False


class _CamTransform(object):
    """Camera regardant vers +Z (sign=1) ou -Z (sign=-1) depuis `origin`."""

    def __init__(self, origin, sign):
        self.origin, self.sign = origin, sign

    def inv(self):
        return self

    def mulp(self, point):
        return Vector(point.x - self.origin.x, point.y - self.origin.y,
                      self.sign * (point.z - self.origin.z))


class _Sensor(object):
    def __init__(self, width, height):
        self.width, self.height = width, height


class Camera(object):
    FOCAL = 1000.0

    def __init__(self, key, center, sign=1, aligned=True, enabled=True,
                 size=(4000, 3000)):
        self.key = key
        self.label = "IMG_{:04d}".format(key)
        self.center = center
        self.enabled = enabled
        self.sensor = _Sensor(*size)
        self.transform = _CamTransform(center, sign) if aligned else None

    def project(self, point):
        local = self.transform.mulp(point)
        if local.z == 0:
            return None
        return Vector(self.sensor.width / 2.0 + self.FOCAL * local.x / local.z,
                      self.sensor.height / 2.0 + self.FOCAL * local.y / local.z, 0)


class _ChunkTransform(object):
    scale = 1.0
    matrix = None


class Chunk(object):
    def __init__(self, cameras=(), markers=()):
        self.cameras = list(cameras)
        self.markers = list(markers)
        self.transform = _ChunkTransform()
        self.crs = None
        self.key = 1
        self.label = "Chunk 1"

    def remove(self, items):
        gone = items if isinstance(items, (list, tuple)) else [items]
        self.markers = [m for m in self.markers if m not in gone]


class FakeApp(object):
    def __init__(self):
        self.menu_items = []
        self.menu_order = []            # entrees et traits dans l'ordre : ("item", label) ou ("separator", menu)
        self.messages = []
        self.document = types.SimpleNamespace(chunk=None)
        self.photo_view = None

    def addMenuItem(self, label, callback, shortcut=None):
        self.menu_items.append((label, callback, shortcut))
        self.menu_order.append(("item", label))

    def addMenuSeparator(self, menu):
        self.menu_order.append(("separator", menu))

    def messageBox(self, text):
        self.messages.append(text)

    def update(self):
        pass


def _install_fake_metashape():
    mod = types.ModuleType("Metashape")
    mod.Vector = Vector
    mod.Camera = Camera
    mod.Marker = Marker
    mod.Chunk = Chunk
    mod.app = FakeApp()
    sys.modules["Metashape"] = mod
    return mod


# --------------------------------------------------------------------------
# Faux PySide2 (juste de quoi importer aerotools.qt et aerotools.msapi)
# --------------------------------------------------------------------------

class _Anything(object):
    def __init__(self, *args, **kwargs):
        pass

    def __getattr__(self, name):
        return _Anything()

    def __call__(self, *args, **kwargs):
        return _Anything()

    def __add__(self, other):
        return _Anything()

    __radd__ = __or__ = __ror__ = __add__


class QApplication(_Anything):
    @staticmethod
    def instance():
        return None

    @staticmethod
    def widgetAt(pos):
        return None

    @staticmethod
    def postEvent(target, event):
        pass


class _FakeQtModule(types.ModuleType):
    """Module Qt factice : tout nom inconnu devient une classe vide, ce qui
    suffit pour importer panel.py (ses classes heritent de widgets Qt)."""

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        made = type(name, (_Anything,), {})
        setattr(self, name, made)
        return made


def _install_fake_pyside2():
    pkg = types.ModuleType("PySide2")
    pkg.__path__ = []
    core = _FakeQtModule("PySide2.QtCore")
    gui = _FakeQtModule("PySide2.QtGui")
    widgets = _FakeQtModule("PySide2.QtWidgets")
    core.Qt = _Anything()
    core.QEvent = _Anything()
    core.QTimer = _Anything
    for name in ("QKeySequence", "QKeyEvent", "QCursor"):
        setattr(gui, name, _Anything)
    widgets.QApplication = QApplication
    for name in ("QMainWindow", "QWidget", "QDockWidget", "QTabWidget", "QShortcut"):
        setattr(widgets, name, type(name, (_Anything,), {}))
    pkg.QtCore, pkg.QtGui, pkg.QtWidgets = core, gui, widgets
    sys.modules.update({"PySide2": pkg, "PySide2.QtCore": core,
                        "PySide2.QtGui": gui, "PySide2.QtWidgets": widgets})


_install_fake_pyside2()
_install_fake_metashape()


@pytest.fixture
def ms():
    """Faux module Metashape remis a neuf pour chaque test."""
    mod = _install_fake_metashape()
    # msapi garde une reference au module : on la met a jour s'il est deja charge.
    if "aerotools.msapi" in sys.modules:
        sys.modules["aerotools.msapi"].Metashape = mod
    return mod


@pytest.fixture
def local_appdata(tmp_path, monkeypatch):
    """Redirige les reglages du poste vers un dossier temporaire."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def french_by_default(tmp_path, monkeypatch):
    """Chaque test part en francais, avec des reglages de poste jetables.

    Les textes attendus par les tests sont ceux du catalogue francais, quelle
    que soit la langue du poste qui lance pytest. La langue est aussi ecrite
    dans les reglages temporaires : un module i18n recharge en cours de test
    (apres un purge de sys.modules) retrouve le francais.
    """
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "poste"))
    from aerotools import i18n

    i18n.reset()
    i18n.set_lang("fr")
    yield
    i18n.reset()


@pytest.fixture
def clean_bootstrap_flags():
    flags = ("_AEROTOOLS_MENU_INSTALLED", "_AEROTOOLS_LAST_TOOL", "_AEROTOOLS_ROOT")
    for flag in flags:
        if hasattr(builtins, flag):
            delattr(builtins, flag)
    yield
    for flag in flags:
        if hasattr(builtins, flag):
            delattr(builtins, flag)
