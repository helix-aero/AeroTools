import json
import os

import pytest

from aerotools import config
from aerotools.tools.fastgcp import session as session_mod


@pytest.fixture(autouse=True)
def _fresh(local_appdata):
    session_mod.reset()
    yield
    session_mod.reset()


def _write_stored(data):
    os.makedirs(config.settings_dir(), exist_ok=True)
    with open(config.settings_path(), "w", encoding="utf-8") as handle:
        json.dump({"fastgcp": data}, handle)


def test_defaults():
    state = session_mod.Session()
    assert state.options == session_mod.DEFAULTS
    assert state.option("after_place") == session_mod.AFTER_API
    assert state.option("auto_filter") is True
    assert state.option("sync_selection") is True
    assert state.option("inconnue") is None


def test_fixed_options_override_stored_settings():
    _write_stored({
        "auto_filter": False,
        "sync_selection": False,
        "after_place": session_mod.AFTER_STAY,
        "click_only": True,
        "collect_dataset": True,
    })
    state = session_mod.Session()
    assert state.option("auto_filter") is True
    assert state.option("sync_selection") is True
    assert state.option("after_place") == session_mod.AFTER_API
    assert state.option("click_only") is None
    assert state.option("collect_dataset") is True


def test_fixed_and_retired_options_ignore_writes():
    state = session_mod.Session()
    state.set_option("auto_filter", False)
    state.set_option("sync_selection", False)
    state.set_option("after_place", session_mod.AFTER_STAY)
    state.set_option("click_only", True)
    assert state.option("auto_filter") is True
    assert state.option("sync_selection") is True
    assert state.option("after_place") == session_mod.AFTER_API
    assert not os.path.exists(config.settings_path())


def test_after_constants_are_kept_for_the_panel():
    assert dict(session_mod.AFTER_LABELS)
    assert {session_mod.AFTER_STAY, session_mod.AFTER_PAGEDOWN,
            session_mod.AFTER_API} <= set(dict(session_mod.AFTER_LABELS))


def test_non_bool_value_for_bool_option_falls_back():
    _write_stored({"collect_dataset": "oui", "show_log": True})
    state = session_mod.Session()
    assert state.option("collect_dataset") is False
    assert state.option("show_log") is True


def test_set_option_persists():
    state = session_mod.Session()
    state.set_option("collect_dataset", True)
    state.set_option("show_log", True)
    again = session_mod.Session()
    assert again.option("collect_dataset") is True
    assert again.option("show_log") is True


def test_set_option_same_value_does_not_write():
    state = session_mod.Session()
    state.set_option("collect_dataset", False)
    assert not os.path.exists(config.settings_path())


def test_current_is_singleton_until_reset():
    first = session_mod.current()
    assert session_mod.current() is first
    session_mod.reset()
    assert session_mod.current() is not first


def test_corrupted_section_does_not_break_session():
    os.makedirs(config.settings_dir(), exist_ok=True)
    with open(config.settings_path(), "w", encoding="utf-8") as handle:
        json.dump({"fastgcp": "casse"}, handle)
    assert session_mod.Session().options == session_mod.DEFAULTS


def test_forget_chunk_clears_camera_list():
    state = session_mod.Session()
    state.last_cameras = [object()]
    state.forget_chunk()
    assert state.last_cameras == []


def test_active_label_handles_dead_reference():
    state = session_mod.Session()
    assert state.active_label() is None

    class Dead(object):
        @property
        def label(self):
            raise RuntimeError("objet detruit")

    state.active_marker = Dead()
    assert state.active_label() is None
    assert state.active_marker is None
