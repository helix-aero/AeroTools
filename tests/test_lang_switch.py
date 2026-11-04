"""Le selecteur FR | EN du panneau : une etape en erreur ne le rend pas muet."""

from aerotools import i18n
from aerotools.tools.fastgcp import panel as panel_mod

Panel = panel_mod.FastGcpPanel


class _Combo(object):
    """Juste ce que _refresh_lang_buttons touche dans la liste des langues."""

    def __init__(self):
        self.codes = ["fr", "en"]
        self.index = 0

    def findData(self, code):
        return self.codes.index(code) if code in self.codes else -1

    def currentIndex(self):
        return self.index

    def setCurrentIndex(self, index):
        self.index = index

    def blockSignals(self, _blocked):
        pass


class _Stub(object):
    """Juste ce que retranslate et _on_lang_clicked touchent."""

    retranslate = Panel.retranslate
    _on_lang_clicked = Panel._on_lang_clicked
    _refresh_lang_buttons = Panel._refresh_lang_buttons
    _retranslate_eyebrow = Panel._retranslate_eyebrow
    _retranslate_search = Panel._retranslate_search
    _retranslate_grid = Panel._retranslate_grid
    _retranslate_checks = Panel._retranslate_checks
    _retranslate_buttons = Panel._retranslate_buttons
    _retranslate_export = Panel._retranslate_export
    _retranslate_aigcp = Panel._retranslate_aigcp
    _retranslate_credit = Panel._retranslate_credit

    def __init__(self, broken=()):
        self.broken = set(broken)
        self.calls = []
        self._lang_combo = _Combo()
        self._lang_cb = self.retranslate
        self._running = True
        self.destroyed = False

    def objectName(self):
        if self.destroyed:
            raise RuntimeError("Internal C++ object already deleted")
        return "stub"

    def _step(self, name):
        self.calls.append(name)
        if name in self.broken:
            raise RuntimeError("objet perime : " + name)

    def _apply_toggle(self, running):
        self._step("etat")

    def refresh_markers(self):
        self._step("marqueurs")

    def update_active_card(self):
        self._step("carte")


for _name in ("_retranslate_eyebrow", "_retranslate_search", "_retranslate_grid",
              "_retranslate_checks", "_retranslate_buttons", "_retranslate_export",
              "_retranslate_aigcp", "_retranslate_credit"):
    setattr(_Stub, _name, (lambda n: lambda self: self._step(n))(_name))


def _active(stub):
    return [stub._lang_combo.codes[stub._lang_combo.index]]


def test_highlight_follows_even_when_a_step_raises(monkeypatch):
    monkeypatch.setattr(panel_mod, "_repolish", lambda widget: None)
    i18n.set_lang("en")
    stub = _Stub(broken={"_retranslate_eyebrow"})
    stub.retranslate()
    assert _active(stub) == ["en"]
    # Les etapes apres celle qui echoue ont quand meme tourne.
    assert "_retranslate_grid" in stub.calls and "carte" in stub.calls


def test_runtime_error_in_a_step_does_not_unsubscribe(monkeypatch):
    monkeypatch.setattr(panel_mod, "_repolish", lambda widget: None)
    stub = _Stub(broken={"marqueurs"})
    i18n.subscribe(stub._lang_cb)
    i18n.set_lang("en")
    i18n.set_lang("fr")
    assert stub._lang_cb in i18n._subscribers
    assert _active(stub) == ["fr"]
    assert stub.calls.count("carte") == 2
    i18n.unsubscribe(stub._lang_cb)


def test_destroyed_panel_is_unsubscribed(monkeypatch):
    monkeypatch.setattr(panel_mod, "_repolish", lambda widget: None)
    stub = _Stub()
    i18n.subscribe(stub._lang_cb)
    stub.destroyed = True
    stub.retranslate()
    assert stub._lang_cb not in i18n._subscribers
    assert stub.calls == []


def test_click_resubscribes_a_lost_subscription(monkeypatch):
    monkeypatch.setattr(panel_mod, "_repolish", lambda widget: None)
    i18n.set_lang("fr")
    stub = _Stub()
    assert stub._lang_cb not in i18n._subscribers
    stub._on_lang_clicked("en")
    assert i18n.lang() == "en"
    assert _active(stub) == ["en"]
    assert "carte" in stub.calls
    i18n.unsubscribe(stub._lang_cb)


def test_click_on_current_language_resyncs_the_panel(monkeypatch):
    monkeypatch.setattr(panel_mod, "_repolish", lambda widget: None)
    i18n.set_lang("fr")
    stub = _Stub()
    stub._lang_combo.index = 1  # liste restee sur l'autre langue
    stub._on_lang_clicked("fr")
    assert _active(stub) == ["fr"]
    assert "_retranslate_eyebrow" in stub.calls
    i18n.unsubscribe(stub._lang_cb)
