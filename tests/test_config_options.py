"""
tests/test_config_options.py
============================
Pure tests for the 0.9.0 Options-tab config plumbing: the global
recognition-strictness getter (`get_match_threshold`), the notifications
blanket flag (`notifications_enabled`), and proof that the global threshold
actually gates non-slot matching in `_score_segment`. No Qt.

Each test sets the module globals it needs; an autouse fixture restores them
afterward so this file can't pollute the threshold/commands state that other
test files (e.g. test_matcher.py) rely on.

Run from the repo root:  pytest -q
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import core.commands as commands  # noqa: E402
from core.matcher import _match_non_slot  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_globals():
    orig_config, orig_commands = commands._config, commands._commands
    yield
    commands._config, commands._commands = orig_config, orig_commands


# -- get_match_threshold ------------------------------------------------------

def test_get_match_threshold_defaults_to_constant():
    commands._config = {}
    assert commands.get_match_threshold() == commands.DEFAULT_THRESHOLD == 0.75


def test_get_match_threshold_reads_config():
    commands._config = {"match_threshold": 0.42}
    assert commands.get_match_threshold() == 0.42


# -- notifications_enabled ----------------------------------------------------

def test_notifications_enabled_defaults_true():
    commands._config = {}
    assert commands.notifications_enabled() is True


def test_notifications_enabled_reads_config():
    commands._config = {"notifications": False}
    assert commands.notifications_enabled() is False


# -- get_auto_pause_media -----------------------------------------------------

def test_auto_pause_media_defaults_true():
    commands._config = {}
    assert commands.get_auto_pause_media() is True


def test_auto_pause_media_reads_config():
    commands._config = {"auto_pause_media": False}
    assert commands.get_auto_pause_media() is False


# -- global threshold gates matching ------------------------------------------

def test_global_threshold_gates_non_slot_matching():
    commands._commands = [{
        "name": "ff", "display_name": "Firefox", "action": "launch_app",
        "phrases": ["open firefox"], "args": {},
    }]
    heard = "open firefax"
    score = _match_non_slot(heard, "open firefox")
    # Sanity: a mid-range garble, so thresholds either side of it are meaningful.
    assert 0.6 < score < 0.97, f"expected a mid-range score, got {score}"

    # Threshold below the score -> the command matches (float score returned).
    commands._config = {"match_threshold": score - 0.05}
    res = commands._score_segment(heard)
    assert res is not None and isinstance(res[0], float)

    # Threshold above the score -> nothing clears it.
    commands._config = {"match_threshold": score + 0.05}
    assert commands._score_segment(heard) is None


def test_per_command_threshold_still_overrides_global():
    # A command with its own low threshold matches even when the global is high.
    commands._commands = [{
        "name": "ff", "display_name": "Firefox", "action": "launch_app",
        "phrases": ["open firefox"], "args": {}, "threshold": 0.5,
    }]
    commands._config = {"match_threshold": 0.99}
    res = commands._score_segment("open firefax")
    assert res is not None and isinstance(res[0], float)
