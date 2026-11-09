"""Screenshots live in one folder per tool (docs/images/<tool>/), taken from the current interface.

Before the tools got their own pages the images sat loose in docs/images/ and some showed the former name of the panel.
No picture may come back there, and every picture that a page shows must exist.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGES = os.path.join(ROOT, "docs", "images")
PICTURE = re.compile(r"\.(png|jpe?g|gif|svg|webp)$", re.IGNORECASE)


def test_no_picture_sits_loose_in_docs_images():
    loose = [name for name in os.listdir(IMAGES) if PICTURE.search(name)]
    assert not loose, "move to docs/images/<tool>/ (and retake it): {}".format(loose)


def test_every_picture_is_in_a_tool_folder_named_like_a_page():
    pages = {name[:-3] for name in os.listdir(os.path.join(ROOT, "docs", "tools")) if name.endswith(".md")}
    folders = [name for name in os.listdir(IMAGES) if os.path.isdir(os.path.join(IMAGES, name))]
    assert folders and set(folders) <= pages, "folders without a page in docs/tools: {}".format(sorted(set(folders) - pages))
