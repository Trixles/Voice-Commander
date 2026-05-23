"""
tests/test_settings_roundtrip.py
================================
Phase 4 (0.6.0): verify the per-command `enabled` flag round-trips through
the settings UI -- flip toggles, collect(), serialize to JSON, reload into a
fresh container, and confirm the toggle states survived. Also confirms the
on-disk shape: system + slot-pinned rows write `enabled` explicitly (even
when True); user-action rows never do.

Unlike test_matcher.py this touches Qt, so it forces the offscreen platform
and skips entirely if PySide6 isn't installed. Run from the repo root with
`pytest -q` (the offscreen platform is set here, no env var needed).
"""

import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from core.settings.tabs.commands_tab import CommandsContainer  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _toggle_of(container, name):
    for row in container._rows:
        if row._cmd.get("name") == name:
            return row._enable_toggle
    raise AssertionError(f"no row named {name!r} in container")


def _base_commands():
    """A pre-0.6.0-style command list: no `enabled` keys anywhere. set_volume
    and move_to_monitor are the slot-pinned pair (their canonical defs come
    from _PINNED_SLOT; these on-disk entries supply the saved enabled state)."""
    return [
        {"name": "open_reddit", "display_name": "Open Reddit",
         "phrases": ["open reddit"], "action": "open_url",
         "args": {"url": "https://reddit.com"}},
        {"name": "close_window", "display_name": "Close window",
         "phrases": ["close window"], "action": "close_window", "args": {}},
        {"name": "volume_down", "display_name": "Volume down",
         "phrases": ["volume down"], "action": "volume_down", "args": {}},
        {"name": "maximize_window", "display_name": "Maximize window",
         "phrases": ["maximize window"], "action": "maximize_window", "args": {}},
        {"name": "set_volume", "display_name": "Set Volume",
         "phrases": ["set volume to {level}"], "action": "set_volume",
         "args": {}, "slots": {"level": {"fuzzy": False}}},
        {"name": "move_to_monitor", "display_name": "Move to Alias",
         "phrases": ["move to {alias}"], "action": "move_window_to_monitor",
         "args": {}, "slots": {"alias": {"fuzzy": False}}},
    ]


def test_enabled_roundtrips_through_collect_and_reload(qapp):
    c1 = CommandsContainer(_base_commands())

    # Flip three OFF: one system, one slot-pinned, one more system.
    _toggle_of(c1, "volume_down").setChecked(False)
    _toggle_of(c1, "set_volume").setChecked(False)        # slot-pinned
    _toggle_of(c1, "close_window").setChecked(False)

    # Serialize exactly as a save would (collect() -> JSON on disk).
    saved = json.loads(json.dumps(c1.collect()))
    by_name = {c["name"]: c for c in saved}

    # The three flipped are False on disk.
    assert by_name["volume_down"]["enabled"] is False
    assert by_name["set_volume"]["enabled"] is False
    assert by_name["close_window"]["enabled"] is False
    # Other system / slot-pinned rows write enabled True explicitly.
    assert by_name["maximize_window"]["enabled"] is True
    assert by_name["move_to_monitor"]["enabled"] is True
    # User-action rows never carry `enabled`.
    assert "enabled" not in by_name["open_reddit"]

    # Reload into a fresh container; the three toggles must still be off.
    c2 = CommandsContainer(saved)
    assert _toggle_of(c2, "volume_down").isChecked() is False
    assert _toggle_of(c2, "set_volume").isChecked() is False
    assert _toggle_of(c2, "close_window").isChecked() is False
    assert _toggle_of(c2, "maximize_window").isChecked() is True
    assert _toggle_of(c2, "move_to_monitor").isChecked() is True

    # Flip them back ON, save again, all enabled True.
    _toggle_of(c2, "volume_down").setChecked(True)
    _toggle_of(c2, "set_volume").setChecked(True)
    _toggle_of(c2, "close_window").setChecked(True)
    recollected = {c["name"]: c for c in json.loads(json.dumps(c2.collect()))}
    assert recollected["volume_down"]["enabled"] is True
    assert recollected["set_volume"]["enabled"] is True
    assert recollected["close_window"]["enabled"] is True
