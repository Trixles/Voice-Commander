"""
Tests for the placer failure armor (born from the s30 live-test bug).

The placement chain fails SILENT by design of its parts: kwriteconfig6
succeeds, every dbus-send returns a clean reply, and KWin can still
discard the script without executing a line (e.g. plugin not enabled in
[Plugins]). Symptom: windows quietly land on the focused screen --
Tyler's single most hated bug class.

Armor:
  1. Sentinel -- after start(), _signal_placer asks KWin isScriptLoaded;
     false -> loud journal line + always-on toast. No more gaslighting.
  2. dbus-send return codes are checked (they never were).
  3. clear_placer_queue() -- wipes queue keys at app startup so a stale
     queue can never outlive the session that wrote it (the login-zombie
     fix; also exercises the whole chain at boot, so the sentinel fires
     on day-one install problems).

All subprocess traffic is faked through run_capture/notify recorders.
"""

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import core.actions.windows as windows  # noqa: E402


class FakeBus:
    """Scriptable stand-in for run_capture in windows.py."""

    def __init__(self, script_loaded="true", fail_cmd=None):
        self.calls = []
        self.script_loaded = script_loaded
        self.fail_cmd = fail_cmd  # substring: any argv containing it exits 1

    def __call__(self, argv, env=None, **kwargs):
        self.calls.append(argv)
        joined = " ".join(argv)
        if self.fail_cmd and self.fail_cmd in joined:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="boom")
        stdout = ""
        if "isScriptLoaded" in joined:
            stdout = f"method return ...\n   boolean {self.script_loaded}"
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")


@pytest.fixture
def notifications(monkeypatch):
    sent = []
    monkeypatch.setattr(windows, "notify", lambda *a, **k: sent.append(a))
    return sent


def test_healthy_chain_verifies_and_stays_quiet(monkeypatch, notifications, capsys):
    bus = FakeBus(script_loaded="true")
    monkeypatch.setattr(windows, "run_capture", bus)
    windows._signal_placer("nextScreen", "DP-1:kate", gui_env={})
    # The sentinel actually ran...
    assert any("isScriptLoaded" in " ".join(c) for c in bus.calls)
    # ...and a healthy chain toasts nothing and logs no failure.
    assert notifications == []
    assert "failed" not in capsys.readouterr().out.lower()


def test_script_dead_after_start_toasts_and_logs(monkeypatch, notifications, capsys):
    # THE s30 bug: every dbus reply clean, script silently discarded.
    bus = FakeBus(script_loaded="false")
    monkeypatch.setattr(windows, "run_capture", bus)
    windows._signal_placer("nextScreen", "DP-1:kate", gui_env={})
    assert len(notifications) == 1
    assert "placement" in notifications[0][0].lower()
    assert "_signal_placer failed" in capsys.readouterr().out


def test_dbus_send_failure_is_no_longer_swallowed(monkeypatch, notifications, capsys):
    # Before the armor, dbus-send exit codes were never checked.
    bus = FakeBus(fail_cmd="loadScript")
    monkeypatch.setattr(windows, "run_capture", bus)
    windows._signal_placer("nextScreen", "DP-1:kate", gui_env={})
    assert len(notifications) == 1
    assert "_signal_placer failed" in capsys.readouterr().out


def test_clear_placer_queue_wipes_both_keys_and_reloads(monkeypatch, notifications):
    bus = FakeBus(script_loaded="true")
    monkeypatch.setattr(windows, "run_capture", bus)
    windows.clear_placer_queue(gui_env={})
    writes = [c for c in bus.calls if c[0] == "kwriteconfig6"]
    # Both queue keys written empty: a stale queue must not survive into
    # a new app session (the login-zombie bug).
    assert len(writes) == 2
    assert all(c[-1] == "" for c in writes)
    keys = {c[c.index("--key") + 1] for c in writes}
    assert keys == {"nextScreen", "moveActive"}
    # And the script is re-executed so the running instance drops its
    # in-memory copy too.
    assert any("loadScript" in " ".join(c) for c in bus.calls)
    assert notifications == []  # healthy chain, no toast
