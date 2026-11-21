"""Aero-PPK, Drone step: the link of the mail that asks for a drone that is not supported. Pure functions, no Qt window."""
from urllib.parse import parse_qs, unquote, urlsplit

from aerotools.tools.aeroppk import screen_drone


def test_the_mail_goes_to_the_support_address_with_the_other_one_in_copy():
    url = screen_drone.mailto_url("Aero-PPK: drone not supported", "Drone model:\nCamera or sensor:\n\nI can send a sample flight (photos and GNSS files).")
    assert url.startswith("mailto:info@helix.corsica?cc=info@helix.aero&")
    assert screen_drone.SUPPORT_EMAIL == "info@helix.corsica" and screen_drone.SUPPORT_EMAIL_CC == "info@helix.aero"


def test_the_subject_and_the_body_are_encoded_and_read_back_unchanged():
    subject, body = "Aero-PPK: drone not supported", "Drone model:\nCamera or sensor:\n\nI can send a sample flight (photos & GNSS files)."
    url = screen_drone.mailto_url(subject, body)
    assert "\n" not in url and " " not in url and "+" not in url and "&body=" in url
    assert "%0A" in url and "%20" in url and "%26" in url
    fields = parse_qs(urlsplit(url).query, keep_blank_values=True)
    assert fields["subject"] == [subject] and fields["body"] == [body] and fields["cc"] == ["info@helix.aero"]
    assert unquote(url.split("body=")[1]) == body


def test_the_user_group_is_the_one_of_the_about_window():
    assert screen_drone.community_link().startswith("https://www.facebook.com/groups/")
