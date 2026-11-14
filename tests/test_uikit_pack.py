"""The UI kit is core code: every tool needs it, so no pack owns it, the manifest lists it, and it never imports a tool."""

import glob
import importlib.util
import os
import re

from aerotools import config, i18n

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIT = os.path.join(ROOT, "aerotools", "uikit")


def test_the_kit_belongs_to_the_core():
    for path in glob.glob(os.path.join(KIT, "*.py")):
        relative = "aerotools/uikit/" + os.path.basename(path)
        assert config.pack_of(relative) == config.CORE, relative


def test_the_manifest_lists_every_kit_file():
    spec = importlib.util.spec_from_file_location("make_manifest_for_uikit", os.path.join(ROOT, "install", "make_manifest.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    listed = set(module.listing(ROOT))
    for path in glob.glob(os.path.join(KIT, "*.py")):
        assert "aerotools/uikit/" + os.path.basename(path) in listed


def test_the_kit_never_imports_a_tool_and_names_none():
    for path in glob.glob(os.path.join(KIT, "*.py")):
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        text = re.sub(r"#\s*/?tool(:[\w,]+)?", "", text)           # the release markers of the tool icons name the packs
        assert not re.search(r"^\s*(from|import)\s+[\w.]*\btools\b", text, re.M), path
        for entry in config.TOOLS:
            assert entry["id"] not in text, (os.path.basename(path), entry["id"])


def test_every_text_key_the_kit_reads_exists_and_is_short():
    keys = set()
    for path in glob.glob(os.path.join(KIT, "*.py")):
        with open(path, encoding="utf-8") as handle:
            keys.update(re.findall(r"""i18n\.t\(\s*["']([a-z_.]+)["']""", handle.read()))
    assert keys
    for key in keys:
        assert i18n.has(key), key
        assert len(i18n.t(key)) <= 60, key


def test_the_shared_footer_keys_exist():
    assert [i18n.t(k) for k in ("ui.cancel", "ui.close", "ui.back", "ui.next", "ui.done", "ui.retry")] == [
        "Cancel", "Close", "Back", "Next", "Done", "Retry"]
    assert i18n.has("ui.info")
