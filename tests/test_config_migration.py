"""
tests/test_config_migration.py
==============================
Tests for load_config()'s self-healing migration: a pre-existing config that
predates a newly-shipped system command (e.g. minimize_window) should gain it
on load WITHOUT disturbing the user's custom commands or toggle states.

This is the path that lets an existing install pick up new system commands.
load_config() only seeds defaults when the command list is empty (first launch),
so without the merge an existing user would never get them -- and the Commands
tab's Restore Defaults would wipe their custom commands to do it.

Each test points commands.CONFIG_PATH at a temp file; an autouse fixture
restores CONFIG_PATH and the module globals afterward so this file can't pollute
state other test files rely on. No Qt.

Run from the repo root:  pytest -q
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import core.commands as commands  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_globals():
    orig = (commands.CONFIG_PATH, commands._config,
            commands._commands, commands._last_mtime)
    yield
    (commands.CONFIG_PATH, commands._config,
     commands._commands, commands._last_mtime) = orig


def _write_cfg(tmp_path, cfg) -> str:
    """Write cfg to a temp commands.json and point the loader at it."""
    p = tmp_path / "commands.json"
    p.write_text(json.dumps(cfg))
    commands.CONFIG_PATH = str(p)
    return str(p)


def _load_back(path) -> dict:
    return json.loads(open(path).read())


def test_missing_system_command_is_merged(tmp_path):
    """A config lacking minimize_window gains it on load, persisted to disk."""
    path = _write_cfg(tmp_path, {
        "commands": [
            {"name": "close_window", "display_name": "Close window",
             "phrases": ["close window"], "action": "close_window", "args": {}},
        ],
    })
    commands.load_config()
    names = [c["name"] for c in _load_back(path)["commands"]]
    assert "minimize_window" in names


def test_custom_user_commands_preserved(tmp_path):
    """User-action commands survive the merge, and deleted user DEFAULTS
    (open_reddit etc.) are NOT resurrected -- only system commands are added."""
    path = _write_cfg(tmp_path, {
        "commands": [
            {"name": "open_dolphin", "display_name": "Open Dolphin",
             "phrases": ["open dolphin"], "action": "launch_app",
             "args": {"app": "dolphin"}},
        ],
    })
    commands.load_config()
    names = [c["name"] for c in _load_back(path)["commands"]]
    assert "open_dolphin" in names          # custom command kept
    assert "minimize_window" in names        # system command added
    assert "open_reddit" not in names        # deleted user default stays gone


def test_existing_toggle_state_preserved(tmp_path):
    """A system command the user disabled keeps enabled=False -- the merge only
    appends missing names, it never rewrites rows already present."""
    path = _write_cfg(tmp_path, {
        "commands": [
            {"name": "maximize_window", "display_name": "Maximize window",
             "phrases": ["maximize window"], "action": "maximize_window",
             "args": {}, "enabled": False},
        ],
    })
    commands.load_config()
    by_name = {c["name"]: c for c in _load_back(path)["commands"]}
    assert by_name["maximize_window"]["enabled"] is False


def test_no_duplicates_and_idempotent(tmp_path):
    """Re-loading adds nothing the second time, and never duplicates a command
    that was already present."""
    path = _write_cfg(tmp_path, {
        "commands": [
            {"name": "close_window", "display_name": "Close window",
             "phrases": ["close window"], "action": "close_window", "args": {}},
        ],
    })
    commands.load_config()
    first = [c["name"] for c in _load_back(path)["commands"]]
    commands.load_config()
    second = [c["name"] for c in _load_back(path)["commands"]]
    assert first == second                   # idempotent
    assert second.count("close_window") == 1  # no duplicate of the pre-existing


def test_slot_pinned_not_merged(tmp_path):
    """Slot-pinned commands self-heal via _PINNED_SLOT, so the system-command
    merge must NOT add them (that would duplicate them at save time)."""
    path = _write_cfg(tmp_path, {
        "commands": [
            {"name": "close_window", "action": "close_window",
             "phrases": ["close window"], "args": {}},
        ],
    })
    commands.load_config()
    names = [c["name"] for c in _load_back(path)["commands"]]
    assert "move_to_monitor" not in names
    assert "set_volume" not in names


def test_empty_config_still_seeds_full_defaults(tmp_path):
    """First-launch path is unaffected: an empty command list still gets the
    complete default set written (the migration branch is the else case)."""
    path = _write_cfg(tmp_path, {"commands": []})
    commands.load_config()
    names = [c["name"] for c in _load_back(path)["commands"]]
    assert "open_browser" in names           # user defaults seeded
    assert "minimize_window" in names         # system defaults seeded


# -- Corrupt-config resilience (regression guards) ----------------------------

def test_corrupt_json_raises_configerror_not_raw_decodeerror(tmp_path):
    """A truncated/corrupt commands.json must surface as a friendly ConfigError
    (which startup turns into a clean exit), NOT a bare JSONDecodeError. The
    file is never rewritten -- overwriting would destroy the user's commands."""
    p = tmp_path / "commands.json"
    p.write_text('{"commands": [ {"name": "x",')  # truncated mid-JSON
    commands.CONFIG_PATH = str(p)
    with pytest.raises(commands.ConfigError):
        commands.load_config()
    # Untouched on disk -- non-destructive.
    assert p.read_text() == '{"commands": [ {"name": "x",'


def test_check_reload_swallows_corrupt_config(tmp_path, monkeypatch):
    """A config corrupted WHILE running must not propagate out of _check_reload
    -- that exception would ride up through try_match and kill the listener
    thread. The last-good in-memory config is kept until the file is valid."""
    p = tmp_path / "commands.json"
    p.write_text('{"commands": [ broken')
    commands.CONFIG_PATH = str(p)
    monkeypatch.setattr(commands, "_config", {"sentinel": True})
    monkeypatch.setattr(commands, "_last_mtime", 0.0)  # force mtime mismatch
    monkeypatch.setattr(commands, "_match_call_count",
                        commands.CHECK_MTIME_EVERY - 1)
    commands._check_reload()  # must NOT raise
    assert commands._config == {"sentinel": True}  # last-good preserved
