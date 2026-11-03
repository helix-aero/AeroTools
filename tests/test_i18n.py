"""Langue d'AeroTools : repli, formats, langue par defaut, persistance, abonnes.

Le fichier conftest remet le francais avant chaque test et redirige les
reglages du poste vers un dossier temporaire.
"""

import datetime
import json
import os
import sys
import types

import pytest

from aerotools import config, i18n


# --------------------------------------------------------------------------
# Doubles
# --------------------------------------------------------------------------

@pytest.fixture
def fake_catalogs(monkeypatch):
    """Catalogues minuscules a la place des vrais : on maitrise les repli."""
    catalogs = {
        "fr": ({"a": "Bonjour {nom}", "seul_fr": "Seulement francais", "x": "X {n}"},
               {"decimal": ",", "date": "%d/%m/%Y", "csv_delimiter": ";"}),
        "en": ({"a": "Hello {nom}", "x": "X {n}"},
               {"decimal": ".", "date": "%Y-%m-%d", "csv_delimiter": ","}),
    }
    monkeypatch.setattr(i18n, "_catalogs", catalogs)
    return catalogs


def detect_with(monkeypatch, metashape=None, system=None):
    """Simule la langue de Metashape et celle du systeme (None = indisponible)."""
    def probe(value):
        def read():
            if value is None:
                raise RuntimeError("pas d'information")
            return value
        return read

    monkeypatch.setattr(i18n, "_metashape_language", probe(metashape))
    monkeypatch.setattr(i18n, "_system_language", probe(system))


# --------------------------------------------------------------------------
# Langues proposees
# --------------------------------------------------------------------------

def test_languages_list_is_ordered_with_native_names():
    assert i18n.LANGUES[0] == ("fr", "Français")
    assert i18n.LANGUES[1] == ("en", "English")
    assert i18n.codes() == ["fr", "en"]
    assert i18n.native_name("en") == "English"
    assert i18n.native_name("zz") == "zz"


def test_set_lang_rejects_unknown_codes_and_keeps_the_current_one():
    assert i18n.set_lang("fr") is True
    for bad in ("es", "", None, "xx_YY", 42):
        assert i18n.set_lang(bad) is False
    assert i18n.lang() == "fr"


def test_set_lang_accepts_locale_style_codes():
    assert i18n.set_lang("EN_us") is True
    assert i18n.lang() == "en"
    assert i18n.set_lang(" fr-CA ") is True
    assert i18n.lang() == "fr"


# --------------------------------------------------------------------------
# Langue par defaut
# --------------------------------------------------------------------------

def _forget_choice():
    """Retire le choix du poste : on repart comme au premier lancement."""
    path = config.settings_path()
    if os.path.exists(path):
        os.remove(path)
    i18n.reset()


def test_default_is_french_when_the_system_is_french(monkeypatch):
    _forget_choice()
    detect_with(monkeypatch, metashape=None, system="fr_FR")
    assert i18n.lang() == "fr"


def test_default_is_english_for_any_other_system_language(monkeypatch):
    for system in ("en_US", "de_DE", "es_ES", "ja_JP"):
        _forget_choice()
        detect_with(monkeypatch, metashape=None, system=system)
        assert i18n.lang() == "en", system


def test_default_is_english_when_nothing_is_known(monkeypatch):
    _forget_choice()
    detect_with(monkeypatch, metashape=None, system=None)
    assert i18n.lang() == "en"


def test_metashape_language_wins_over_the_system(monkeypatch):
    _forget_choice()
    detect_with(monkeypatch, metashape="en", system="fr_FR")
    assert i18n.lang() == "en"
    _forget_choice()
    detect_with(monkeypatch, metashape="fr", system="en_US")
    assert i18n.lang() == "fr"


def test_metashape_may_give_a_full_language_name(monkeypatch):
    _forget_choice()
    detect_with(monkeypatch, metashape="Français", system="en_US")
    assert i18n.lang() == "fr"
    _forget_choice()
    detect_with(monkeypatch, metashape="English", system="fr_FR")
    assert i18n.lang() == "en"


def test_saved_choice_wins_over_detection(monkeypatch):
    detect_with(monkeypatch, metashape="fr", system="fr_FR")
    config.put("ui", "langue", "en")
    i18n.reset()
    assert i18n.lang() == "en"


def test_unknown_saved_choice_falls_back_to_detection(monkeypatch):
    detect_with(monkeypatch, metashape=None, system="fr_FR")
    config.put("ui", "langue", "klingon")
    i18n.reset()
    assert i18n.lang() == "fr"


def test_unreadable_settings_do_not_break_the_default(monkeypatch):
    os.makedirs(config.settings_dir(), exist_ok=True)
    with open(config.settings_path(), "w", encoding="utf-8") as handle:
        handle.write("{pas du json")
    detect_with(monkeypatch, metashape=None, system="en_GB")
    i18n.reset()
    assert i18n.lang() == "en"


def test_real_detection_never_raises():
    # Sans les doubles : Metashape factice sans reglages, locale du poste.
    assert i18n.detect_default() in i18n.codes()


# --------------------------------------------------------------------------
# Persistance
# --------------------------------------------------------------------------

def test_set_lang_is_saved_in_the_workstation_settings():
    i18n.set_lang("en")
    assert config.get("ui", "langue") == "en"
    with open(config.settings_path(), encoding="utf-8") as handle:
        assert json.load(handle)["ui"] == {"langue": "en"}
    i18n.reset()
    assert i18n.lang() == "en"


def test_set_lang_without_persistence_leaves_the_settings_alone():
    i18n.set_lang("en", persist=False)
    assert i18n.lang() == "en"
    assert config.get("ui", "langue") == "fr"


def test_set_lang_keeps_other_settings():
    config.put("theme", "mode", "dark")
    i18n.set_lang("en")
    assert config.get("theme", "mode") == "dark"


def test_set_lang_survives_a_settings_write_failure(monkeypatch):
    monkeypatch.setattr(config, "put", lambda *a, **k: (_ for _ in ()).throw(OSError("disque")))
    assert i18n.set_lang("en") is True
    assert i18n.lang() == "en"


# --------------------------------------------------------------------------
# t() : repli et robustesse
# --------------------------------------------------------------------------

def test_t_returns_the_text_of_the_current_language():
    assert i18n.t("panel.export") == "Exporter"
    i18n.set_lang("en")
    assert i18n.t("panel.export") == "Export"


def test_t_falls_back_to_french_then_to_the_key(fake_catalogs):
    i18n.set_lang("en")
    assert i18n.t("a", nom="Lea") == "Hello Lea"
    assert i18n.t("seul_fr") == "Seulement francais"
    assert i18n.t("cle.inconnue") == "cle.inconnue"


def test_t_fills_fields():
    i18n.set_lang("en")
    assert i18n.t("undo.done", label="GCP3") == "Undone: GCP3"
    i18n.set_lang("fr")
    assert i18n.t("undo.done", label="GCP3") == "Annulé : GCP3"


def test_t_never_fails_with_incomplete_or_wrong_fields(fake_catalogs):
    assert i18n.t("a") == "Bonjour {nom}"
    assert i18n.t("a", autre=1) == "Bonjour {nom}"
    assert i18n.t("x", n=None) == "X None"
    assert i18n.t("cle.inconnue", n=3) == "cle.inconnue"


def test_t_survives_a_broken_format_string(fake_catalogs):
    fake_catalogs["fr"][0]["cassee"] = "Valeur {0} et {n:d} et {obj.attr}"
    assert i18n.t("cassee", n="pas un entier") == "Valeur {0} et {n:d} et {obj.attr}"
    fake_catalogs["fr"][0]["accolade"] = "Texte { seul"
    assert i18n.t("accolade", n=1) == "Texte { seul"


def test_t_with_a_non_string_key_does_not_raise():
    assert i18n.t(42) == "42"
    assert i18n.t(None) == "None"


def test_t_survives_a_missing_catalog_module(monkeypatch):
    monkeypatch.setattr(i18n, "_catalogs", {})
    monkeypatch.setattr(i18n, "LANGUES", [("fr", "Français"), ("zz", "Zzz")])
    i18n.set_lang("zz", persist=False)
    # Le module aerotools.lang.zz n'existe pas : tout repose sur le francais.
    assert i18n.t("panel.export") == "Exporter"
    assert i18n.t("rien.du.tout") == "rien.du.tout"
    assert i18n.decimal_separator() == ","


def test_has_tells_whether_a_key_exists():
    assert i18n.has("panel.export")
    assert not i18n.has("panel.nexiste.pas")
    assert i18n.has("panel.export", "en")


# --------------------------------------------------------------------------
# Chargement a la demande et extensibilite
# --------------------------------------------------------------------------

def test_catalogs_load_lazily(monkeypatch):
    monkeypatch.setattr(i18n, "_catalogs", {})
    i18n.set_lang("fr", persist=False)
    assert i18n.t("panel.export") == "Exporter"
    assert set(i18n._catalogs) == {"fr"}
    i18n.set_lang("en", persist=False)
    i18n.t("panel.export")
    assert set(i18n._catalogs) == {"fr", "en"}


def test_a_new_language_is_one_file_and_one_line(monkeypatch):
    module = types.ModuleType("aerotools.lang.es")
    module.TEXTES = {"panel.export": "Exportar"}
    module.FORMATS = {"decimal": ",", "date": "%d.%m.%Y", "csv_delimiter": ";"}
    monkeypatch.setitem(sys.modules, "aerotools.lang.es", module)
    monkeypatch.setattr(i18n, "LANGUES", i18n.LANGUES + [("es", "Español")])
    monkeypatch.setattr(i18n, "_catalogs", {})
    assert i18n.set_lang("es", persist=False) is True
    assert i18n.t("panel.export") == "Exportar"
    # Texte absent de l'espagnol : repli sur le francais.
    assert i18n.t("panel.search") == "Rechercher"
    assert i18n.fmt_date(datetime.date(2026, 11, 3)) == "03.11.2026"
    assert i18n.codes() == ["fr", "en", "es"]


def test_a_spanish_system_picks_spanish_once_it_is_listed(monkeypatch):
    monkeypatch.setattr(i18n, "LANGUES", i18n.LANGUES + [("es", "Español")])
    _forget_choice()
    detect_with(monkeypatch, metashape=None, system="es_ES")
    assert i18n.lang() == "es"


# --------------------------------------------------------------------------
# Formats
# --------------------------------------------------------------------------

def test_fmt_number_french_comma_english_point():
    assert i18n.fmt_number(0.12345) == "0,123"
    assert i18n.fmt_number(1234567.891, 2) == "1234567,89"
    i18n.set_lang("en")
    assert i18n.fmt_number(0.12345) == "0.123"
    assert i18n.fmt_number(1234567.891, 2) == "1234567.89"


def test_fmt_number_has_no_thousands_separator_and_no_negative_zero():
    assert i18n.fmt_number(1000000) == "1000000,000"
    assert i18n.fmt_number(-0.0004) == "0,000"
    assert i18n.fmt_number(-0.0456) == "-0,046"
    i18n.set_lang("en")
    assert i18n.fmt_number(-0.0004) == "0.000"
    assert i18n.fmt_number(-12.5, 1) == "-12.5"


def test_fmt_number_decimals_and_integers():
    assert i18n.fmt_number(12, 0) == "12"
    assert i18n.fmt_number(2.5, 0) in ("2", "3")
    assert i18n.fmt_number(4.56, 1) == "4,6"
    assert i18n.fmt_number("3.5", 1) == "3,5"


def test_fmt_number_gives_an_empty_text_when_unknown():
    for value in (None, float("nan"), float("inf"), float("-inf"), "abc", [], object()):
        assert i18n.fmt_number(value) == ""


def test_fmt_date_by_language():
    day = datetime.date(2026, 11, 3)
    assert i18n.fmt_date(day) == "03/11/2026"
    assert i18n.fmt_date(datetime.datetime(2026, 11, 3, 17, 5)) == "03/11/2026"
    i18n.set_lang("en")
    assert i18n.fmt_date(day) == "2026-11-03"
    assert i18n.fmt_date(datetime.datetime(2026, 11, 3, 17, 5)) == "2026-11-03"


def test_fmt_date_leaves_anything_else_alone():
    assert i18n.fmt_date("hier") == "hier"
    assert i18n.fmt_date(None) == "None"


def test_csv_delimiter_by_language():
    assert i18n.csv_delimiter() == ";"
    i18n.set_lang("en")
    assert i18n.csv_delimiter() == ","


# --------------------------------------------------------------------------
# Abonnes
# --------------------------------------------------------------------------

def test_subscribers_are_told_when_the_language_changes():
    seen = []
    callback = lambda: seen.append(i18n.lang())
    i18n.subscribe(callback)
    i18n.subscribe(callback)
    try:
        i18n.set_lang("en")
        i18n.set_lang("fr")
        assert seen == ["en", "fr"]
    finally:
        i18n.unsubscribe(callback)
    i18n.set_lang("en")
    assert seen == ["en", "fr"]


def test_subscribers_are_not_told_when_nothing_changes():
    seen = []
    callback = lambda: seen.append(1)
    i18n.subscribe(callback)
    try:
        i18n.set_lang("fr")
        i18n.set_lang("fr")
        i18n.set_lang("es")
        assert seen == []
    finally:
        i18n.unsubscribe(callback)


def test_a_failing_subscriber_does_not_block_the_others():
    seen = []

    def broken():
        raise ValueError("panne")

    def fine():
        seen.append(i18n.lang())

    i18n.subscribe(broken)
    i18n.subscribe(fine)
    try:
        i18n.set_lang("en")
        assert seen == ["en"]
    finally:
        i18n.unsubscribe(broken)
        i18n.unsubscribe(fine)


def test_a_destroyed_qt_object_is_dropped_from_the_subscribers():
    calls = []

    def dead():
        calls.append(1)
        raise RuntimeError("Internal C++ object already deleted")

    i18n.subscribe(dead)
    i18n.set_lang("en")
    i18n.set_lang("fr")
    assert calls == [1]
    assert dead not in i18n._subscribers


def test_unsubscribe_unknown_callback_is_harmless():
    i18n.unsubscribe(lambda: None)
