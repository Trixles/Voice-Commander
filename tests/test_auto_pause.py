"""
tests/test_auto_pause.py
========================
Auto-pause-media-on-wake, tested as units: the media_control playerctl
helpers and the two listener hook functions (_auto_pause_on_wake /
_auto_resume_after_wake). The full wake->sleep integration path lives in
test_listener.py; here we pin the media_touched suppression and the
degrade/reset edges directly. No Qt, no real playerctl.

Run from the repo root:  pytest -q
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import core.commands as commands  # noqa: E402
import core.listener as listener  # noqa: E402
import core.media_control as media_control  # noqa: E402
from core.context import Context  # noqa: E402


def _result(stdout: str, returncode: int = 0):
    return subprocess.CompletedProcess(["playerctl"], returncode, stdout, "")


# -- media_control.list_playing_players --------------------------------------

def test_list_playing_returns_only_playing(monkeypatch):
    out = "spotify\tPlaying\nfirefox\tPaused\nmpv\tPlaying\n"
    monkeypatch.setattr(media_control, "run_capture", lambda *a, **k: _result(out))
    assert media_control.list_playing_players({}) == ["spotify", "mpv"]


def test_list_playing_empty_on_no_players(monkeypatch):
    monkeypatch.setattr(media_control, "run_capture",
                        lambda *a, **k: _result("No players found", 1))
    assert media_control.list_playing_players({}) == []


def test_list_playing_fails_safe_on_exception(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("playerctl not on PATH")
    monkeypatch.setattr(media_control, "run_capture", boom)
    assert media_control.list_playing_players({}) == []


# -- hook helpers: _auto_pause_on_wake ---------------------------------------

def _stub_media(monkeypatch, playing):
    """Stub the three media_control calls; return the pause/resume recorders."""
    paused: list = []
    resumed: list = []
    monkeypatch.setattr(media_control, "list_playing_players",
                        lambda env: list(playing))
    monkeypatch.setattr(media_control, "pause_players",
                        lambda names, env: paused.append(list(names)))
    monkeypatch.setattr(media_control, "resume_players",
                        lambda names, env: resumed.append(list(names)))
    return paused, resumed


def test_pause_records_players_and_resets_touched(monkeypatch):
    monkeypatch.setattr(commands, "get_auto_pause_media", lambda: True)
    paused, _ = _stub_media(monkeypatch, ["spotify"])
    ctx = Context()
    ctx.media_touched = True  # stale flag from a previous window
    listener._auto_pause_on_wake(ctx, {})
    assert paused == [["spotify"]]
    assert ctx.auto_paused_players == ["spotify"]
    assert ctx.media_touched is False  # fresh window opened


def test_pause_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(commands, "get_auto_pause_media", lambda: False)
    paused, _ = _stub_media(monkeypatch, ["spotify"])
    ctx = Context()
    listener._auto_pause_on_wake(ctx, {})
    assert paused == []
    assert ctx.auto_paused_players == []


def test_pause_nothing_playing_leaves_empty_snapshot(monkeypatch):
    monkeypatch.setattr(commands, "get_auto_pause_media", lambda: True)
    paused, _ = _stub_media(monkeypatch, [])
    ctx = Context()
    listener._auto_pause_on_wake(ctx, {})
    assert paused == []
    assert ctx.auto_paused_players == []


# -- hook helpers: _auto_resume_after_wake -----------------------------------

def test_resume_plays_exactly_the_paused_players(monkeypatch):
    _, resumed = _stub_media(monkeypatch, [])
    ctx = Context()
    ctx.auto_paused_players = ["spotify", "mpv"]
    listener._auto_resume_after_wake(ctx, {})
    assert resumed == [["spotify", "mpv"]]
    assert ctx.auto_paused_players == []


def test_resume_suppressed_when_media_touched(monkeypatch):
    _, resumed = _stub_media(monkeypatch, [])
    ctx = Context()
    ctx.auto_paused_players = ["spotify"]
    ctx.media_touched = True  # a media command ran this window
    listener._auto_resume_after_wake(ctx, {})
    assert resumed == []
    # Snapshot still cleared, so a stale pause can't leak into the next window.
    assert ctx.auto_paused_players == []


def test_resume_suppressed_when_new_media_playing(monkeypatch):
    """Predicate 2: media we didn't pause is playing at resume (e.g. a URL
    command opened an autoplaying page) -> don't stack the pre-wake audio."""
    # 'firefox' is playing now; we only paused 'spotify' -> firefox is new.
    _, resumed = _stub_media(monkeypatch, ["firefox"])
    ctx = Context()
    ctx.auto_paused_players = ["spotify"]
    listener._auto_resume_after_wake(ctx, {})
    assert resumed == []
    assert ctx.auto_paused_players == []


def test_resume_fires_when_only_our_own_player_reports_playing(monkeypatch):
    """A player in our snapshot showing as playing is not 'new' -- resume it
    (idempotent), don't treat our own paused player as foreign media."""
    _, resumed = _stub_media(monkeypatch, ["spotify"])
    ctx = Context()
    ctx.auto_paused_players = ["spotify"]
    listener._auto_resume_after_wake(ctx, {})
    assert resumed == [["spotify"]]


def test_resume_noop_when_nothing_was_paused(monkeypatch):
    _, resumed = _stub_media(monkeypatch, [])
    ctx = Context()  # auto_paused_players empty
    listener._auto_resume_after_wake(ctx, {})
    assert resumed == []


def test_resume_honors_in_flight_pause_even_if_toggle_now_off(monkeypatch):
    """The toggle gates whether we START pausing, not whether we honor a pause
    already taken -- flipping it off mid-window must not strand paused music."""
    monkeypatch.setattr(commands, "get_auto_pause_media", lambda: False)
    _, resumed = _stub_media(monkeypatch, [])
    ctx = Context()
    ctx.auto_paused_players = ["spotify"]
    listener._auto_resume_after_wake(ctx, {})
    assert resumed == [["spotify"]]
