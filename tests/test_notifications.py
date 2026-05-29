"""
tests/test_notifications.py
===========================
Covers the per-action notification summary table (_build_notification /
_NOTIFICATION_TEMPLATES): a couple of representative templates render as
expected, the URL-domain helper strips scheme/www/path, and an action with
no entry falls back to the generic acknowledgement.

Pure logic -- no Qt, no Vosk, no subprocess. Run from the repo root:
  pytest -q
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.commands import _build_notification, _url_domain  # noqa: E402


def test_template_launch_app_uses_app_name():
    assert _build_notification("launch_app", {"app": "Firefox"}, {}) == "Opening Firefox"


def test_template_static_action():
    assert _build_notification("mute", {}, {}) == "Muted"


def test_template_open_url_shows_bare_domain():
    merged = {"url": "https://www.reddit.com/r/linux"}
    assert _build_notification("open_url", merged, {}) == "Opening reddit.com"


def test_unknown_action_falls_back_to_acknowledgement():
    assert _build_notification("not_a_real_action", {}, {}) == "Command acknowledged"


def test_url_domain_strips_scheme_www_and_path():
    assert _url_domain("https://www.example.com/a/b") == "example.com"
    assert _url_domain("http://example.org") == "example.org"
    assert _url_domain("example.net/page") == "example.net"
