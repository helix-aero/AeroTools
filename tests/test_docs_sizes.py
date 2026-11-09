"""The sizes the README gives for the install must match the manifest of the tree that holds the README.

The core is what `CLICK HERE TO INSTALL.bat` copies (every file no pack owns); the total is every file of the manifest. Each
channel has its own figures, so the check only runs on the filtered tree of a channel (the private repository holds the tools of
no channel in particular: the check is skipped there, and the release script runs it on each public tree).
"""
import json
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIB = 1024.0 * 1024.0

CORE_RE = re.compile(r"about (\d+) MB on the stable version, about (\d+) MB on the dev version")
TOTAL_RE = re.compile(r"about (\d+) MB \(stable version\) or about (\d+) MB \(dev version\)")


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


def channel_index(manifest):
    """0 for the stable channel (one pack), 1 for the dev channel (three packs), None for the private repository (all the packs)."""
    count = len(manifest["packs"])
    return {1: 0, 3: 1}.get(count)


def close(stated, real, floor):
    return abs(stated - real) <= max(floor, 0.15 * real)


def test_readme_install_sizes_match_the_manifest():
    manifest, core, total = manifest_sizes()
    index = channel_index(manifest)
    if index is None:
        pytest.skip("not the tree of a release channel")
    text = read("README.md")
    core_match = CORE_RE.search(text)
    total_match = TOTAL_RE.search(text)
    assert core_match, "README: the install size of the core is not stated as 'about N MB on the stable version, about N MB on the dev version'"
    assert total_match, "README: the size of all the tools is not stated as 'about N MB (stable version) or about N MB (dev version)'"
    stated_core = int(core_match.group(index + 1))
    stated_total = int(total_match.group(index + 1))
    assert close(stated_core, core, 1.0), "README says {} MB for the core, the manifest says {:.1f} MB".format(stated_core, core)
    assert close(stated_total, total, 3.0), "README says {} MB in all, the manifest says {:.1f} MB".format(stated_total, total)


def test_readme_sizes_are_stated_in_the_source_too():
    """In the private tree the sentences exist (so the filtered trees have them) and the old claim is gone."""
    text = read("README.md")
    assert CORE_RE.search(text) and TOTAL_RE.search(text)
    assert "nothing else (about 1 MB" not in text
