"""
tests/test_matcher.py
=====================
Pure-logic tests for the matcher pipeline in core/commands.py and the
override pre-processor in core/overrides.py. No Qt, no Vosk, no
subprocess: just call the functions and assert on what they return.

This is Tier 3 of the refactor plan in REVIEW.md. Two purposes:
  1. Lock in current behaviour so the Tier 4 commands.py split is safe.
  2. Catch the tail-threshold / chain-rule edge cases before users do.

Run from the repo root:  pytest -q
"""

import json
import os
import sys

import pytest

# Allow `pytest` to be invoked from the repo root without installing the
# package. The repo isn't pip-installable yet -- if/when it gets a
# pyproject.toml this shim can go.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core import commands  # noqa: E402
from core.commands import (  # noqa: E402
    _extract_slots,
    _match_chain_segments,
    _match_non_slot,
    _score_segment,
    try_match,
)
from core.overrides import apply_overrides  # noqa: E402


# -- _match_non_slot: tail-rescore guard --------------------------------------

def test_tail_rescore_rejects_open_like_vs_open_plex():
    """The exact case TAIL_THRESHOLD is calibrated for: shared 'open'
    verb, unrelated tails. Should hard-zero instead of returning the
    inflated overall ratio."""
    assert _match_non_slot("open like", "open plex") == 0.0


def test_tail_rescore_accepts_plausible_vosk_garble():
    """'open plix' vs 'open plex' is a 1-char swap in the tail --
    Vosk-realistic. Tail ratio ~0.75 clears TAIL_THRESHOLD."""
    score = _match_non_slot("open plix", "open plex")
    assert score >= 0.75


def test_tail_rescore_accepts_dolphin_garble():
    """Borderline-but-legitimate case called out in the TAIL_THRESHOLD
    docstring -- tail ratio ~0.62 should clear the 0.60 bar."""
    assert _match_non_slot("open dolfen", "open dolphin") > 0.0


def test_tail_rescore_skipped_when_leading_token_differs():
    """If the verbs don't match, the tail guard doesn't apply --
    fall back to the overall ratio."""
    assert _match_non_slot("play music", "open music") > 0.0


def test_tail_rescore_skipped_for_single_word_phrases():
    """Single-word phrases (e.g. 'mute', 'shutdown') have no tail
    to rescore, so the guard is a no-op."""
    assert _match_non_slot("mute", "mute") == pytest.approx(1.0)


# -- _extract_slots: greedy vs lazy regex -------------------------------------

def test_extract_slot_trailing_is_greedy():
    """Trailing slot (nothing after it) captures everything after the
    literal prefix -- needed so 'move window to monitor one' yields
    'monitor one', not just 'monitor'."""
    assert _extract_slots(
        "move window to monitor one", "move window to {alias}"
    ) == {"alias": "monitor one"}


def test_extract_slot_midphrase_is_lazy():
    """Mid-phrase slot is lazy so the literal text after it can anchor
    -- 'set volume to {level} percent' should pull just the number."""
    assert _extract_slots(
        "set volume to 50 percent", "set volume to {level} percent"
    ) == {"level": "50"}


def test_extract_slot_no_match_returns_none():
    """Mismatched literal text returns None, not an empty dict --
    the matcher uses this to skip slot-bearing phrases that don't fit."""
    assert _extract_slots("open reddit", "move window to {alias}") is None


# -- try_match: chain dispatch ------------------------------------------------

class _Ctx:
    """Minimal context stand-in. _dispatch is mocked, so try_match
    never reads from this -- it just needs an object to pass through."""
    pending_confirm = None
    pending_args = None


@pytest.fixture
def matcher_env(monkeypatch):
    """Inject a known command set + monitor map into commands.py's
    module globals, stub out the side-effecting helpers (_dispatch,
    _check_reload, windows.write_next_screen), and capture what would
    have been dispatched so tests can assert on it."""
    test_commands = [
        {"name": "open_reddit",   "phrases": ["open reddit"],
         "action": "open_url",   "args": {"url": "https://reddit.com"},
         "cooldown": 0},
        {"name": "open_youtube",  "phrases": ["open youtube"],
         "action": "open_url",   "args": {"url": "https://youtube.com"},
         "cooldown": 0},
        {"name": "launch_dolphin", "phrases": ["launch dolphin"],
         "action": "launch_app", "args": {"app": "dolphin"},
         "cooldown": 0},
        {"name": "shutdown",      "phrases": ["shut down"],
         "action": "shutdown",   "args": {}, "confirm": True,
         "cooldown": 0},
    ]
    monitors = {"DP-1": ["primary"], "DP-2": ["secondary"]}

    monkeypatch.setattr(commands, "_commands", test_commands)
    monkeypatch.setattr(commands, "_config", {"monitors": monitors})
    monkeypatch.setattr(commands, "_last_fired", {})
    monkeypatch.setattr(commands, "_check_reload", lambda gui_env=None: None)

    dispatched: list[tuple[str, dict]] = []
    queued: list[str] = []

    def fake_dispatch(cmd, args, gui_env, context):
        dispatched.append((cmd["name"], args))

    def fake_dispatch_with_monitor(cmd, args, target, gui_env, context):
        dispatched.append((cmd["name"], args, target))

    monkeypatch.setattr(commands, "_dispatch", fake_dispatch)
    monkeypatch.setattr(
        commands, "_dispatch_with_monitor", fake_dispatch_with_monitor
    )
    monkeypatch.setattr(
        commands.windows, "write_next_screen",
        lambda entry, gui_env: queued.append(entry),
    )

    return dispatched, queued


def test_chain_success_both_dispatched(matcher_env):
    dispatched, _ = matcher_env
    assert try_match("open reddit and open youtube", {}, _Ctx()) is True
    assert [d[0] for d in dispatched] == ["open_reddit", "open_youtube"]


def test_chain_partial_failure_falls_through_to_single(matcher_env):
    """One segment doesn't match a command -- chain is rejected (not
    rule-rejected), so the unsplit text gets matched as a single
    command. Here the full string doesn't match anything either, so
    nothing fires."""
    dispatched, _ = matcher_env
    assert try_match("open reddit and floogleblork", {}, _Ctx()) is False
    assert dispatched == []


def test_chain_rule_rejection_confirm_blocks_dispatch(matcher_env):
    """A chain containing a confirm-required command must fire NEITHER
    half -- returning False with no dispatch is the safety guarantee.
    No fall-through to single-match either."""
    dispatched, _ = matcher_env
    assert try_match("shut down and open reddit", {}, _Ctx()) is False
    assert dispatched == []


def test_chain_trailing_target_propagates(matcher_env):
    """'on primary' on the LAST segment only -- the earlier segment
    inherits it. Without propagation the cross-monitor URL guard would
    fire (DP-1 vs None are distinct targets), so the fact that both
    commands dispatch and the placer queue has DP-1 twice IS the
    propagation proof."""
    dispatched, queued = matcher_env
    assert try_match(
        "open reddit and open youtube on primary", {}, _Ctx()
    ) is True
    assert [d[0] for d in dispatched] == ["open_reddit", "open_youtube"]
    assert len(queued) == 1
    assert queued[0].count("DP-1") == 2


def test_chain_cross_monitor_urls_refused(matcher_env):
    """Two open_url commands with EXPLICIT different monitors race in
    ways we can't fix -- chain must be rejected, nothing dispatched,
    no queue write."""
    dispatched, queued = matcher_env
    assert try_match(
        "open reddit on primary and open youtube on secondary", {}, _Ctx()
    ) is False
    assert dispatched == []
    assert queued == []


# -- overrides: order-sensitive pre-match rewrites ----------------------------

def test_overrides_applied_in_order():
    """Rules apply sequentially -- the output of rule N feeds rule
    N+1. So foo -> bar -> baz."""
    overrides = [
        {"pattern": "foo", "replacement": "bar"},
        {"pattern": "bar", "replacement": "baz"},
    ]
    assert apply_overrides("foo", overrides) == "baz"


def test_overrides_whole_word_boundary():
    """Pattern 'in' must NOT corrupt 'open' or 'spin' -- whole-word
    matching is the whole point of the \\b boundaries."""
    overrides = [{"pattern": "in", "replacement": "out"}]
    assert apply_overrides("open spin pin", overrides) == "open spin pin"


def test_overrides_empty_replacement_deletes_and_collapses():
    """Empty replacement = filler-word deletion. Whitespace runs
    introduced by the deletion get collapsed."""
    overrides = [{"pattern": "please", "replacement": ""}]
    assert apply_overrides(
        "open reddit please now", overrides
    ) == "open reddit now"


def test_overrides_malformed_rows_skipped_silently():
    """Bad rows in the middle of the list shouldn't kill the pipeline
    -- malformed entries are skipped, good ones still apply."""
    overrides = [
        {"pattern": "foo", "replacement": "bar"},
        {"not": "a row"},
        "garbage",
        {"pattern": "bar", "replacement": "baz"},
    ]
    assert apply_overrides("foo", overrides) == "baz"


# -- enable/disable: matched-but-skipped semantics (0.6.0) --------------------
#
# A command with "enabled": False is still SCORED by the matcher (so the best
# match can be identified) but is not dispatched. _score_segment returns a
# ("disabled", cmd) sentinel; try_match notifies and skips. Absent "enabled"
# key means enabled (back-compat with pre-0.6.0 configs).


@pytest.fixture
def dispatch_capture(monkeypatch):
    """Mock the side-effecting tail of try_match so the disabled-path tests
    can assert on what dispatched and what got notified. Each test sets
    commands._commands itself. Mirrors `matcher_env` above but also captures
    _notify (the disabled path's only output) and lets the test own the
    command set."""
    monkeypatch.setattr(commands, "_config", {"monitors": {"DP-1": ["primary"]}})
    monkeypatch.setattr(commands, "_last_fired", {})
    monkeypatch.setattr(commands, "_check_reload", lambda gui_env=None: None)

    dispatched: list = []
    queued: list = []
    notified: list = []

    monkeypatch.setattr(
        commands, "_dispatch",
        lambda cmd, args, gui_env, context: dispatched.append((cmd["name"], args)),
    )
    monkeypatch.setattr(
        commands, "_dispatch_with_monitor",
        lambda cmd, args, target, gui_env, context: dispatched.append(
            (cmd["name"], args, target)
        ),
    )
    monkeypatch.setattr(
        commands, "_notify",
        lambda summary, *a, **k: notified.append(summary),
    )
    monkeypatch.setattr(
        commands.windows, "write_next_screen",
        lambda entry, gui_env: queued.append(entry),
    )
    return dispatched, queued, notified


def test_score_segment_returns_disabled_sentinel_when_command_disabled(monkeypatch):
    """A single disabled command, heard string matches its phrase -> the
    ("disabled", cmd) sentinel, not a normal (score, cmd, args, target)."""
    monkeypatch.setattr(commands, "_config", {"monitors": {}})
    monkeypatch.setattr(commands, "_commands", [
        {"name": "volume_down", "display_name": "Volume down",
         "phrases": ["volume down"], "action": "volume_down",
         "args": {}, "enabled": False, "cooldown": 0},
    ])
    result = _score_segment("volume down")
    assert result is not None
    assert result[0] == "disabled"
    assert result[1]["name"] == "volume_down"


def test_score_segment_enabled_match_wins_when_it_outscores_disabled(monkeypatch):
    """Best-on-score-then-check-enabled: the enabled command scores 1.0
    (exact), the disabled one ~0.89 (one-char tail garble). Enabled wins
    and a normal match tuple comes back."""
    monkeypatch.setattr(commands, "_config", {"monitors": {}})
    monkeypatch.setattr(commands, "_commands", [
        {"name": "open_plex_enabled", "phrases": ["open plex"],
         "action": "launch_app", "args": {"app": "plex"}, "cooldown": 0},
        {"name": "open_plix_disabled", "phrases": ["open plix"],
         "action": "launch_app", "args": {"app": "plix"},
         "enabled": False, "cooldown": 0},
    ])
    result = _score_segment("open plex")
    assert result[0] != "disabled"
    _score, cmd, _args, _target = result
    assert cmd["name"] == "open_plex_enabled"


def test_score_segment_disabled_sentinel_when_disabled_outscores_enabled(monkeypatch):
    """Same setup, flipped: the DISABLED command is the exact (1.0) match and
    the enabled one is the garble (~0.89). Because scoring ignores enabled,
    the disabled command is the best match -> sentinel. This is the case the
    'best wins, then check enabled' ordering exists for: an enabled lower
    scorer must NOT shadow the disabled higher scorer."""
    monkeypatch.setattr(commands, "_config", {"monitors": {}})
    monkeypatch.setattr(commands, "_commands", [
        {"name": "open_plix_enabled", "phrases": ["open plix"],
         "action": "launch_app", "args": {"app": "plix"}, "cooldown": 0},
        {"name": "open_plex_disabled", "phrases": ["open plex"],
         "action": "launch_app", "args": {"app": "plex"},
         "enabled": False, "cooldown": 0},
    ])
    result = _score_segment("open plex")
    assert result[0] == "disabled"
    assert result[1]["name"] == "open_plex_disabled"


def test_chain_with_one_disabled_segment_others_fire(monkeypatch):
    """_match_chain_segments on ["open reddit", "volume down"] where
    volume_down is disabled: chain_ok stays True, the disabled segment rides
    along as a sentinel, the enabled one is a normal entry."""
    monkeypatch.setattr(commands, "_config", {"monitors": {}})
    monkeypatch.setattr(commands, "_last_fired", {})
    monkeypatch.setattr(commands, "_commands", [
        {"name": "open_reddit", "phrases": ["open reddit"],
         "action": "open_url", "args": {"url": "https://reddit.com"},
         "cooldown": 0},
        {"name": "volume_down", "display_name": "Volume down",
         "phrases": ["volume down"], "action": "volume_down",
         "args": {}, "enabled": False, "cooldown": 0},
    ])
    matches, chain_ok, chain_rejected = _match_chain_segments(
        ["open reddit", "volume down"]
    )
    assert chain_ok is True
    assert chain_rejected is False
    assert len(matches) == 2
    # Entry 0 is (cmd, args, target); entry 1 is ("disabled", cmd).
    assert matches[0][0]["name"] == "open_reddit"
    assert matches[1][0] == "disabled"
    assert matches[1][1]["name"] == "volume_down"


def test_chain_all_disabled_segments_notify_none_dispatch(dispatch_capture, monkeypatch):
    """Every segment disabled: chain_ok=True, nothing dispatches, one
    notification per segment. Asserted via behaviour (try_match), not by
    poking at the matches list."""
    dispatched, _queued, notified = dispatch_capture
    monkeypatch.setattr(commands, "_commands", [
        {"name": "open_reddit", "display_name": "Open reddit",
         "phrases": ["open reddit"], "action": "open_url",
         "args": {"url": "https://reddit.com"}, "enabled": False,
         "cooldown": 0},
        {"name": "volume_down", "display_name": "Volume down",
         "phrases": ["volume down"], "action": "volume_down",
         "args": {}, "enabled": False, "cooldown": 0},
    ])
    assert try_match("open reddit and volume down", {}, _Ctx()) is True
    assert dispatched == []
    assert len(notified) == 2
    assert any("Open reddit is disabled in Settings" in n for n in notified)
    assert any("Volume down is disabled in Settings" in n for n in notified)


def test_disabled_does_not_override_confirm_abort(monkeypatch):
    """A confirm-required segment must abort the chain even when a disabled
    segment is processed first. The disabled sentinel does NOT short-circuit
    the loop before the confirm rule is reached -> chain_rejected=True."""
    monkeypatch.setattr(commands, "_config", {"monitors": {}})
    monkeypatch.setattr(commands, "_last_fired", {})
    monkeypatch.setattr(commands, "_commands", [
        {"name": "volume_down", "display_name": "Volume down",
         "phrases": ["volume down"], "action": "volume_down",
         "args": {}, "enabled": False, "cooldown": 0},
        {"name": "shutdown", "phrases": ["shut down"], "action": "shutdown",
         "args": {}, "confirm": True, "cooldown": 0},
    ])
    matches, chain_ok, chain_rejected = _match_chain_segments(
        ["volume down", "shut down"]
    )
    assert chain_rejected is True
    assert chain_ok is False


def test_load_config_synthesizes_enabled_true_for_existing_configs(tmp_path, monkeypatch):
    """A pre-0.6.0 commands.json has no `enabled` field on its system
    commands. Loading it must NOT treat them as disabled -- absent key means
    enabled. We rely on cmd.get("enabled", True) everywhere rather than
    literally writing the field on load, so this is a behaviour check: the
    command produces a normal match, not the disabled sentinel."""
    config_file = tmp_path / "commands.json"
    config_file.write_text(json.dumps({
        "commands": [
            {"name": "volume_down", "display_name": "Volume down",
             "phrases": ["volume down"], "action": "volume_down",
             "args": {}, "cooldown": 0},
        ],
    }))

    # Anchor the globals load_config() reassigns so they restore at teardown.
    monkeypatch.setattr(commands, "_commands", commands._commands)
    monkeypatch.setattr(commands, "_config", commands._config)
    monkeypatch.setattr(commands, "_last_mtime", commands._last_mtime)
    monkeypatch.setattr(commands, "CONFIG_PATH", str(config_file))

    commands.load_config()

    result = _score_segment("volume down")
    assert result is not None
    assert result[0] != "disabled"
    _score, cmd, _args, _target = result
    assert cmd["name"] == "volume_down"


def test_slot_pinned_disabled_notifies_no_dispatch(dispatch_capture, monkeypatch):
    """A disabled slot-pinned command (set_volume): heard 'set volume to
    fifty' matches the slot phrase, the single-match path returns the
    disabled sentinel -> notify, no dispatch."""
    dispatched, _queued, notified = dispatch_capture
    monkeypatch.setattr(commands, "_commands", [
        {"name": "set_volume", "display_name": "Set volume",
         "phrases": ["set volume to {level}"], "action": "set_volume",
         "args": {}, "slots": {"level": {"fuzzy": False}},
         "enabled": False, "cooldown": 0},
    ])
    assert try_match("set volume to fifty", {}, _Ctx()) is True
    assert dispatched == []
    assert len(notified) == 1
    assert "Set volume is disabled in Settings" in notified[0]


def test_apply_overrides_runs_regardless_of_enabled():
    """Overrides are a pre-match text rewrite in core/overrides.py; the
    function has no command/enabled parameter at all. This locks in the
    architectural split: a rewrite toward a (potentially disabled) command's
    phrase still happens -- the matcher decides what to do with the result
    afterwards. enabled lives entirely downstream of overrides."""
    overrides = [{"pattern": "vol down", "replacement": "volume down"}]
    assert apply_overrides("vol down", overrides) == "volume down"
