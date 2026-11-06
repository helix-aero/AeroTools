"""Small panel functions that do not need Qt."""

from aerotools import svgpath  # noqa: F401  (the module must import with the fake Qt)
from aerotools.tools.fastgcp import panel


def test_short_label_keeps_ten_characters_then_three_dots():
    assert panel.short_label("PS_11") == "PS_11"
    assert panel.short_label("0123456789") == "0123456789"
    assert panel.short_label("0123456789A") == "0123456789..."
    assert panel.short_label(None) == ""


def test_credit_points_at_the_helix_site_and_the_github_repository():
    assert panel.SITE_URL == "https://helix.aero" and panel.SITE_NAME == "helix.aero"
    assert panel.GITHUB_URL.startswith("https://github.com/helix-aero/")
