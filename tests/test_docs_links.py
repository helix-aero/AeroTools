"""Links of the documentation: a link to a file or a heading that does not exist fails here.

The release script runs this test on the filtered tree of every channel, so a page or an image that a channel does not ship
cannot stay linked from a page that it does ship.

Screenshots that are still to be taken are linked as `<name>_placeholder.png`. They are not broken links, so the normal run
accepts them. Set `AERO_DOCS_STRICT=1` (before a release) to make the run fail while any placeholder is still linked.
"""

import os
import re
import urllib.parse

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MD_LINK = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
HTML_REF = re.compile(r"""(?:src|href|srcset)\s*=\s*["']([^"']+)["']""")
FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def _documents():
    found = []
    for name in ("README.md", "CONTRIBUTING.md"):
        if os.path.isfile(os.path.join(ROOT, name)):
            found.append(name)
    for folder, _dirs, files in os.walk(os.path.join(ROOT, "docs")):
        for name in files:
            if name.endswith(".md"):
                found.append(os.path.relpath(os.path.join(folder, name), ROOT).replace(os.sep, "/"))
    return sorted(found)


def _read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as handle:
        return handle.read()


def _lines_outside_code(text):
    inside = False
    for number, line in enumerate(text.split("\n"), 1):
        if FENCE.match(line):
            inside = not inside
            continue
        if not inside:
            yield number, line


def _slug(title):
    title = re.sub(r"<[^>]+>", "", title)                       # inline html (a comment, a tag)
    title = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", title)    # a link keeps its text
    title = title.replace("`", "").replace("*", "").strip().lower()
    title = re.sub(r"[^\w\- ]", "", title)
    return title.replace(" ", "-")


def anchors(path):
    """The heading anchors of a Markdown file, the way GitHub makes them (-1, -2 for repeated headings)."""
    seen, found = {}, set()
    for _number, line in _lines_outside_code(_read(path)):
        match = HEADING.match(line)
        if match:
            slug = _slug(match.group(2))
            count = seen.get(slug, 0)
            seen[slug] = count + 1
            found.add(slug if count == 0 else "{}-{}".format(slug, count))
    return found


def references(path):
    """[(line number, target)] of the relative links and images of one file."""
    out = []
    for number, line in _lines_outside_code(_read(path)):
        targets = MD_LINK.findall(line) + HTML_REF.findall(line)
        for target in targets:
            if re.match(r"^(?:[a-z][a-z0-9+.\-]*:|//)", target, re.IGNORECASE):
                continue                                         # http:, https:, mailto:, data:
            out.append((number, target))
    return out


def _resolve(path, target):
    """(file path relative to the root, anchor or None) of a link found in `path`."""
    target, _sep, anchor = target.partition("#")
    target = urllib.parse.unquote(target)
    if not target:
        return path, anchor or None
    folder = os.path.dirname(path)
    full = os.path.normpath(os.path.join(folder, target)).replace(os.sep, "/")
    return full, anchor or None


DOCUMENTS = _documents()


@pytest.mark.parametrize("path", DOCUMENTS)
def test_every_link_of_a_document_points_to_a_file_and_a_heading_that_exist(path):
    broken = []
    for number, target in references(path):
        full, anchor = _resolve(path, target)
        if full.startswith(".."):
            broken.append("{}:{}: {} leaves the repository".format(path, number, target))
            continue
        if not os.path.exists(os.path.join(ROOT, full)):
            broken.append("{}:{}: {} (no file {})".format(path, number, target, full))
            continue
        if anchor and full.endswith(".md") and anchor.lower() not in anchors(full):
            broken.append("{}:{}: {} (no heading #{} in {})".format(path, number, target, anchor, full))
    assert not broken, "\n".join(broken)


def test_the_slug_of_a_heading_follows_github():
    assert _slug("1. Open the tool") == "1-open-the-tool"
    assert _slug("8. Adjust: choose the control markers") == "8-adjust-choose-the-control-markers"
    assert _slug("Troubleshooting") == "troubleshooting"
    assert _slug("What `Train` does") == "what-train-does"


def test_a_missing_file_and_a_missing_heading_are_reported(tmp_path, monkeypatch):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("# Title\n\n[ok](b.md#other)\n[no file](c.md)\n[no heading](b.md#nope)\n![x](img/x.png)\n",
                                            encoding="utf-8")
    (tmp_path / "docs" / "b.md").write_text("## Other\n", encoding="utf-8")
    import sys
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "ROOT", str(tmp_path))
    bad = []
    for number, target in references("docs/a.md"):
        full, anchor = _resolve("docs/a.md", target)
        if not os.path.exists(os.path.join(module.ROOT, full)) or \
                (anchor and full.endswith(".md") and anchor not in anchors(full)):
            bad.append(target)
    assert bad == ["c.md", "b.md#nope", "img/x.png"]


@pytest.mark.skipif(not os.environ.get("AERO_DOCS_STRICT"), reason="set AERO_DOCS_STRICT=1 before a release")
def test_no_screenshot_is_still_a_placeholder():
    left = ["{}:{}: {}".format(path, number, target)
            for path in DOCUMENTS for number, target in references(path) if "_placeholder." in target]
    assert not left, "screenshots still to take:\n" + "\n".join(left)
