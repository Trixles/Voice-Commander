"""
Regression test for the "move to {alias}" monitor bug.

The bug: move_window_to_monitor resolved an alias to a numeric KWin screen
index (via kscreen-doctor output numbers) and fired the "Window to Screen N"
shortcut. That index neither matches KWin's own screen numbering nor stays
stable across monitor hotplugs, so windows landed on the wrong monitor.

The fix routes by OUTPUT NAME through the vc-window-placer (same mechanism the
working "on [alias]" path uses): signal the placer to move the ACTIVE window to
the named output, and let KWin resolve the live screen by name. No screen
index, no stale cache.
"""

import json

from core.actions import windows


def _fake_subprocess(calls):
    """Return (fake_capture, fake_bg) that record their argv into `calls`."""
    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_capture(cmd, **kw):
        calls.append(cmd)
        return _Result()

    def fake_bg(cmd, **kw):
        calls.append(cmd)

    return fake_capture, fake_bg


def test_move_to_monitor_routes_by_output_name_not_screen_index(tmp_path, monkeypatch):
    # Monitors block: the alias "monitor three" maps to output HDMI-A-1.
    cfg = tmp_path / "commands.json"
    cfg.write_text(json.dumps({
        "monitors": {
            "HDMI-A-1": ["monitor three"],
            "HDMI-A-2": ["monitor two", "tv"],
        }
    }))
    monkeypatch.setattr(windows, "CONFIG_PATH", str(cfg))

    # Populate the (now-irrelevant) index cache so the OLD code path would
    # resolve and fire "Window to Screen 1" -- proving the RED captures the
    # real bug. The fixed code must ignore this and route by name instead.
    monkeypatch.setattr(windows, "_output_to_screen", {"HDMI-A-1": 1, "HDMI-A-2": 0})

    calls = []
    fake_capture, fake_bg = _fake_subprocess(calls)
    monkeypatch.setattr(windows, "run_capture", fake_capture)
    monkeypatch.setattr(windows, "run_bg", fake_bg)

    windows.move_window_to_monitor("monitor three", {}, None)

    flat = [" ".join(c) for c in calls]

    # Must signal the placer to move the active window to the output NAME.
    assert any("moveActive" in s and "HDMI-A-1" in s for s in flat), \
        f"expected a placer moveActive=HDMI-A-1 signal; got {flat}"

    # Must NOT use the broken numeric "Window to Screen N" shortcut.
    assert not any("Window to Screen" in s for s in flat), \
        f"should not fire the screen-index shortcut; got {flat}"
