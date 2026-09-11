"""
tests/test_celery_man.py
========================
The Celery Man system command: it opens a fixed video URL, unless that video
is ALREADY playing in a browser -- the clip's own audio says the command
phrase ("computer, load up celery man please"), so without a guard it would
echo-launch copies of itself. The guard asks playerctl (MPRIS) whether any
player is currently Playing the video, matched by video ID in the URL or by
title. No wake words are muted; the check runs only when celery_man fires and
fails OPEN (a broken/missing playerctl must never block the command).

See core/actions/apps.py (celery_man, _celery_man_is_playing) and
core/commands.py (default + registry).

Run from the repo root:  pytest -q
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import core.commands as commands  # noqa: E402
from core.actions import apps  # noqa: E402
from core.context import Context  # noqa: E402


def _playerctl_result(stdout: str, returncode: int = 0):
    """Fake CompletedProcess shaped like run_capture's return."""
    return subprocess.CompletedProcess(
        args=["playerctl"], returncode=returncode, stdout=stdout, stderr="",
    )


def _patch(monkeypatch, playerctl):
    """Wire a fake playerctl (callable or result) and an open_url recorder."""
    opened = {}
    monkeypatch.setattr(
        apps, "open_url",
        lambda url, gui_env, browser="", context=None: opened.update(url=url),
    )
    if callable(playerctl):
        monkeypatch.setattr(apps, "run_capture", playerctl)
    else:
        monkeypatch.setattr(apps, "run_capture", lambda *a, **kw: playerctl)
    return opened


def test_action_opens_url_when_not_playing(monkeypatch):
    """Video not playing: celery_man opens the URL (and returns non-False,
    so the dispatcher announces it normally)."""
    opened = _patch(monkeypatch, _playerctl_result("No players found", 1))
    result = apps.celery_man(gui_env={}, context=Context())

    assert opened["url"] == apps.CELERY_MAN_URL
    assert result is not False


def test_echo_block_returns_false_to_suppress_notification(monkeypatch):
    """A blocked echo must return False -- the dispatcher's cue to skip the
    'Loading up Celery Man' toast, cooldown stamp, and log line entirely."""
    line = f"firefox\tPlaying\t{apps.CELERY_MAN_URL}\tTim and Eric - Celery Man"
    _patch(monkeypatch, _playerctl_result(line))
    assert apps.celery_man(gui_env={}, context=Context()) is False


def test_echo_blocked_while_video_is_playing_by_url(monkeypatch):
    """The clip echoing its own launch phrase must not open a second copy."""
    line = f"firefox\tPlaying\t{apps.CELERY_MAN_URL}\tTim and Eric - Celery Man"
    opened = _patch(monkeypatch, _playerctl_result(line))
    apps.celery_man(gui_env={}, context=Context())
    assert opened == {}


def test_echo_blocked_by_title_when_no_url_exposed(monkeypatch):
    """Some browsers expose title but not URL over MPRIS -- title suffices."""
    line = "firefox\tPlaying\t\tTim and Eric - CELERY MAN (HD)"
    opened = _patch(monkeypatch, _playerctl_result(line))
    apps.celery_man(gui_env={}, context=Context())
    assert opened == {}


def test_echo_blocked_when_our_autopause_paused_the_video(monkeypatch):
    """Auto-pause-on-wake pauses the celery video on a phantom wake, but the
    echo utterance can still slip through before the pause lands. A player WE
    paused this window (context.auto_paused_players) must still count as an
    echo, or the guard fails open and relaunches over the paused video."""
    line = f"firefox\tPaused\t{apps.CELERY_MAN_URL}\tTim and Eric - Celery Man"
    opened = _patch(monkeypatch, _playerctl_result(line))
    ctx = Context()
    ctx.auto_paused_players = ["firefox"]
    apps.celery_man(gui_env={}, context=ctx)
    assert opened == {}


def test_fires_when_video_is_paused(monkeypatch):
    """Paused = no audio = no echo risk; a repeat command is genuinely the
    user -- UNLESS auto-pause paused it (see the test above). With no
    auto-paused players, a paused video means the user paused it themselves."""
    line = f"firefox\tPaused\t{apps.CELERY_MAN_URL}\tTim and Eric - Celery Man"
    opened = _patch(monkeypatch, _playerctl_result(line))
    apps.celery_man(gui_env={}, context=Context())
    assert opened["url"] == apps.CELERY_MAN_URL


def test_fire_marks_media_touched(monkeypatch):
    """A genuine fire flags the wake window so auto-pause won't resume the
    pre-wake music over the (autoplaying, load-delayed) celery video."""
    _patch(monkeypatch, _playerctl_result("No players found", 1))
    ctx = Context()
    apps.celery_man(gui_env={}, context=ctx)
    assert ctx.media_touched is True


def test_echo_block_leaves_media_touched_false(monkeypatch):
    """A blocked echo returns before the flag is set, so the window's close
    auto-resumes the very video the guard protected."""
    line = f"firefox\tPlaying\t{apps.CELERY_MAN_URL}\tTim and Eric - Celery Man"
    _patch(monkeypatch, _playerctl_result(line))
    ctx = Context()
    apps.celery_man(gui_env={}, context=ctx)
    assert ctx.media_touched is False


def test_fires_when_other_media_is_playing(monkeypatch):
    """Unrelated playing media must not block the command."""
    line = "spotify\tPlaying\thttps://www.youtube.com/watch?v=dQw4w9WgXcQ\tsome other video"
    opened = _patch(monkeypatch, _playerctl_result(line))
    apps.celery_man(gui_env={}, context=Context())
    assert opened["url"] == apps.CELERY_MAN_URL


def test_fails_open_when_playerctl_missing(monkeypatch):
    """A broken or absent playerctl must never block the command."""
    def boom(*a, **kw):
        raise FileNotFoundError("playerctl not on PATH")
    opened = _patch(monkeypatch, boom)
    apps.celery_man(gui_env={}, context=Context())
    assert opened["url"] == apps.CELERY_MAN_URL


def test_action_survives_missing_context(monkeypatch):
    """No context (shouldn't happen in practice) must not crash the action."""
    _patch(monkeypatch, _playerctl_result("No players found", 1))
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
