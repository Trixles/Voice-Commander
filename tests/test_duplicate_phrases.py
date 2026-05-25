"""
tests/test_duplicate_phrases.py
===============================
Pure-logic tests for the duplicate-phrase guard in core/commands.py:
``find_duplicate_phrases`` (detection) and ``_build_block_message`` (the
user-facing Save-block text). No Qt, no Vosk, no subprocess -- just call the
functions and assert on what they return.

Behaviour under test mirrors the runtime matcher: exact match (case-insensitive,
trimmed), slot phrases excluded, disabled commands INCLUDED (they still win the
match), per-command de-dupe, first-appearance order.

Run from the repo root:  pytest -q
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.commands import (  # noqa: E402
    _build_block_message,
    _default_commands,
    find_duplicate_phrases,
)
from core.aliases import PINNED_SLOT  # noqa: E402


def _cmd(name, phrases, enabled=None, display_name=None):
    d = {"name": name, "display_name": display_name or name, "phrases": phrases}
    if enabled is not None:
        d["enabled"] = enabled
    return d


# -- find_duplicate_phrases ---------------------------------------------------

def test_no_collisions_returns_empty():
    cmds = [_cmd("a", ["open reddit"]), _cmd("b", ["close window"])]
    assert find_duplicate_phrases(cmds) == []


def test_two_command_exact_dup():
    cmds = [_cmd("a", ["volume up"], display_name="A"),
            _cmd("b", ["volume up"], display_name="B")]
    res = find_duplicate_phrases(cmds)
    assert len(res) == 1
    assert res[0]["phrase"] == "volume up"
    assert [m["name"] for m in res[0]["commands"]] == ["A", "B"]


def test_case_and_whitespace_insensitive_keeps_first_spelling():
    cmds = [_cmd("a", ["Volume Up"]), _cmd("b", ["  volume up  "])]
    res = find_duplicate_phrases(cmds)
    assert len(res) == 1
    # The displayed phrase is the FIRST raw spelling seen (stripped).
    assert res[0]["phrase"] == "Volume Up"


def test_disabled_command_counted():
    # A disabled command still wins the match, so it must still be flagged.
    cmds = [_cmd("a", ["volume up"], enabled=True, display_name="A"),
            _cmd("b", ["volume up"], enabled=False, display_name="B")]
    res = find_duplicate_phrases(cmds)
    assert len(res) == 1
    members = {m["name"]: m["enabled"] for m in res[0]["commands"]}
    assert members == {"A": True, "B": False}


def test_absent_enabled_means_enabled():
    cmds = [_cmd("a", ["volume up"]), _cmd("b", ["volume up"])]
    res = find_duplicate_phrases(cmds)
    assert all(m["enabled"] is True for m in res[0]["commands"])


def test_three_commands_one_phrase():
    cmds = [_cmd("a", ["next"], display_name="A"),
            _cmd("b", ["next"], display_name="B"),
            _cmd("c", ["next"], display_name="C")]
    res = find_duplicate_phrases(cmds)
    assert len(res) == 1
    assert [m["name"] for m in res[0]["commands"]] == ["A", "B", "C"]


def test_multiple_distinct_colliding_phrases_in_appearance_order():
    cmds = [_cmd("a", ["next", "play"]),
            _cmd("b", ["next"]),
            _cmd("c", ["play"])]
    res = find_duplicate_phrases(cmds)
    assert [r["phrase"] for r in res] == ["next", "play"]


def test_blank_phrase_ignored():
    cmds = [_cmd("a", ["", "   "]), _cmd("b", ["", "\t"])]
    assert find_duplicate_phrases(cmds) == []


def test_same_phrase_twice_in_one_command_is_not_a_collision():
    cmds = [_cmd("a", ["next", "next"])]
    assert find_duplicate_phrases(cmds) == []


def test_slot_phrases_excluded():
    # Two slot templates that share text must NOT be flagged (slots never match
    # by exact string equality at runtime).
    cmds = [_cmd("a", ["move to {alias}"]), _cmd("b", ["move to {alias}"])]
    assert find_duplicate_phrases(cmds) == []
    # A slot phrase and a plain phrase that overlap also don't collide.
    cmds2 = [_cmd("a", ["set volume to {level}"]), _cmd("b", ["set volume to {level}"])]
    assert find_duplicate_phrases(cmds2) == []


def test_winner_order_is_collected_order_regardless_of_enabled():
    # First in the list is commands[0] even if it is the disabled one.
    cmds = [_cmd("first", ["go"], enabled=False, display_name="First"),
            _cmd("second", ["go"], enabled=True, display_name="Second")]
    res = find_duplicate_phrases(cmds)
    assert res[0]["commands"][0]["name"] == "First"


def test_shipped_defaults_have_no_duplicate_phrases():
    """Critical guard for the hard block: if the shipped default command set
    ever contained an exact dup, EVERY user would be locked out of saving until
    they hand-fixed a collision they never created. Mirrors the --emit-defaults
    assembly in core/commands.py's __main__."""
    defaults = _default_commands() + [dict(p) for p in PINNED_SLOT]
    assert find_duplicate_phrases(defaults) == []


# -- _build_block_message -----------------------------------------------------

def test_block_message_single_enabled():
    res = find_duplicate_phrases([_cmd("a", ["volume up"], display_name="Turn Up Volume"),
                                  _cmd("b", ["volume up"], display_name="My Custom Thing")])
    msg = _build_block_message(res)
    assert "Can't save" in msg
    assert '"volume up"' in msg
    assert "«Turn Up Volume»" in msg
    assert "«My Custom Thing»" in msg
    assert "Remove it from one" in msg
    assert "(disabled)" not in msg


def test_block_message_single_disabled_adds_note():
    res = find_duplicate_phrases([_cmd("a", ["volume up"], enabled=False, display_name="Turn Up Volume"),
                                  _cmd("b", ["volume up"], display_name="My Custom Thing")])
    msg = _build_block_message(res)
    assert "«Turn Up Volume» (disabled)" in msg
    assert "still claims its phrases" in msg


def test_block_message_multiple_bullets_with_cap():
    # 8 colliding phrases -> 6 bullets shown + "…and 2 more."
    a = _cmd("a", [f"phrase{i}" for i in range(8)], display_name="A")
    b = _cmd("b", [f"phrase{i}" for i in range(8)], display_name="B")
    res = find_duplicate_phrases([a, b])
    assert len(res) == 8
    msg = _build_block_message(res)
    assert msg.count("•") == 6
    assert "…and 2 more." in msg
    assert "Fix them, then save:" in msg
