"""
tests/test_celery_man.py
========================
The Celery Man system command: it opens a fixed video URL and mutes the
"computer" wake word for a stretch afterwards (the video says "computer"
several times and would otherwise trip the wake word). See core/actions/apps.py
(celery_man), core/commands.py (default + registry), core/wake.py (exclude).

Run from the repo root:  pytest -q
"""

import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import core.commands as commands  # noqa: E402
from core.actions import apps  # noqa: E402
from core.context import Context  # noqa: E402


def test_action_opens_url_and_mutes_computer(monkeypatch):
    """celery_man opens the baked-in URL and sets a ~100s 'computer' mute."""
    opened = {}
    monkeypatch.setattr(
        apps, "open_url",
        lambda url, gui_env, browser="", context=None: opened.update(url=url),
    )
    ctx = Context()
    before = time.time()
    apps.celery_man(gui_env={}, context=ctx)
    after = time.time()

    assert opened["url"] == apps.CELERY_MAN_URL
    assert ctx.wake_suppress_word == "computer"
    mute = apps.CELERY_MAN_WAKE_MUTE_SECONDS
    assert before + mute <= ctx.wake_suppress_until <= after + mute


def test_action_survives_missing_context(monkeypatch):
    """No context (shouldn't happen in practice) must not crash the action."""
    monkeypatch.setattr(
        apps, "open_url",
        lambda url, gui_env, browser="", context=None: None,
    )
    apps.celery_man(gui_env={}, context=None)  # no raise


def test_command_registered_in_action_registry():
    assert commands.ACTION_REGISTRY.get("celery_man") is apps.celery_man


def test_default_system_command_present_and_shaped():
    cmd = next(c for c in commands._default_system_commands()
               if c["name"] == "celery_man")
    assert cmd["action"] == "celery_man"
    assert cmd["phrases"] == ["load up celery man"]
    assert cmd["display_name"] == "Celery Man"
    assert cmd.get("args", {}) == {}  # URL is baked into the action, not args


def test_is_last_in_system_command_order():
    import pytest
    pytest.importorskip("PySide6")
    from core.settings.helpers import _SYSTEM_COMMAND_ORDER
    assert _SYSTEM_COMMAND_ORDER[-1] == "celery_man"
