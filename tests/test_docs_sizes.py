"""The sizes the README gives for the install must match the manifest of the tree that holds the README.

The core is what `CLICK HERE TO INSTALL.bat` copies (every file no pack owns); the total is every file of the manifest. Both
channels state the same figures today (they hold the same core), so the check only runs on the filtered tree of a channel (the private repository holds the tools of
no channel in particular: the check is skipped there, and the release script runs it on each public tree).
"""
import json
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIB = 1024.0 * 1024.0

CORE_RE = re.compile(r"a few seconds, about (\d+) MB")
TOTAL_RE = re.compile(r"all the tools together take about (\d+) MB")


def read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as handle:
        return handle.read()


def manifest_sizes():
    manifest = json.loads(read("manifest.json"))
    prefixes = [prefix for pack in manifest["packs"] for prefix in pack["paths"]]
    total = core = 0
    for path, info in manifest["files"].items():
        size = info["size"]
        total += size
        if not any(path.startswith(prefix) for prefix in prefixes):
            core += size
    return manifest, core / MIB, total / MIB


def is_channel_tree(manifest):
    """True for the tree of a release channel (the stable and the dev channels hold three or four packs), False for the private repository."""
    return len(manifest["packs"]) in (3, 4)


def close(stated, real, floor):
    return abs(stated - real) <= max(floor, 0.15 * real)


def test_readme_install_sizes_match_the_manifest():
    manifest, core, total = manifest_sizes()
    if not is_channel_tree(manifest):
        pytest.skip("not the tree of a release channel")
    text = read("README.md")
    core_match = CORE_RE.search(text)
    total_match = TOTAL_RE.search(text)
    assert core_match, "README: the install size of the core is not stated as 'a few seconds, about N MB'"
    assert total_match, "README: the size of all the tools is not stated as 'all the tools together take about N MB'"
    stated_core = int(core_match.group(1))
    stated_total = int(total_match.group(1))
    assert close(stated_core, core, 1.0), "README says {} MB for the core, the manifest says {:.1f} MB".format(stated_core, core)
    assert close(stated_total, total, 3.0), "README says {} MB in all, the manifest says {:.1f} MB".format(stated_total, total)


def test_readme_sizes_are_stated_in_the_source_too():
    """In the private tree the sentences exist (so the filtered trees have them) and the old claim is gone."""
    text = read("README.md")
    assert CORE_RE.search(text) and TOTAL_RE.search(text)
    assert "nothing else (about 1 MB" not in text
