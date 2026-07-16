"""
tests/test_phrase_sanitize.py
=============================
Pure-logic tests for the Save-time phrase character guard in core/commands.py:
`sanitize_phrase_text` (what survives) and `_build_invalid_chars_message` (the
modal wording). No Qt -- the settings dialog only presents these results.

Voice phrases can only contain letters, numbers, spaces, and commas (the
phrase-list separator); everything else can't be spoken, so it's a dead phrase
and is stripped when the user saves. Braces additionally used to crash the
matcher, which is why the whole class is forbidden. See ARCHITECTURE
'Save strips non-speakable characters from phrases'.

Run from the repo root:  pytest -q
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.commands import (  # noqa: E402
    sanitize_phrase_text,
    dedupe_phrase_text,
    find_cross_collision,
    _build_invalid_chars_message,
    _build_duplicate_removed_message,
    _build_cross_collision_message,
)


def _cmd(name, current, original=(), enabled=True):
    return {"name": name, "enabled": enabled,
            "original": list(original), "current": list(current)}


# -- sanitize_phrase_text -----------------------------------------------------

def test_strips_braces_and_punctuation():
    clean, removed = sanitize_phrase_text("open zoom on {my main}!")
    assert clean == "open zoom on my main"
    assert removed == "!{}"  # sorted, unique


def test_keeps_letters_digits_spaces_and_commas():
    clean, removed = sanitize_phrase_text("open dolphin, play song 3")
    assert clean == "open dolphin, play song 3"
    assert removed == ""


def test_whitespace_collapses_and_is_not_reported_as_removed():
    # Tabs/newlines become a single space so pasted multi-line text doesn't
    # fuse words -- but they aren't "removed characters" the user must see.
    clean, removed = sanitize_phrase_text("hello\tthere\n\nfriend")
    assert clean == "hello there friend"
    assert removed == ""


def test_phrase_of_only_junk_becomes_empty():
    clean, removed = sanitize_phrase_text("{}")
    assert clean == ""
    assert removed == "{}"


def test_removed_chars_are_deduped_and_sorted():
    _, removed = sanitize_phrase_text("a@@@b###c@")
    assert removed == "#@"


# -- dedupe_phrase_text -------------------------------------------------------

def test_removes_within_command_duplicates_keeping_first():
    clean, removed = dedupe_phrase_text("open dolphin, launch dolphin, open dolphin")
    assert clean == "open dolphin, launch dolphin"
    assert removed == ["open dolphin"]


def test_dedupe_is_case_and_whitespace_insensitive():
    clean, removed = dedupe_phrase_text("Open Dolphin,  open dolphin ")
    assert clean == "Open Dolphin"       # first spelling kept
    assert removed == ["open dolphin"]


def test_dedupe_drops_blank_entries_and_reports_nothing_when_unique():
    clean, removed = dedupe_phrase_text("open dolphin,, launch dolphin")
    assert clean == "open dolphin, launch dolphin"
    assert removed == []


# -- _build_invalid_chars_message ---------------------------------------------

def test_message_names_each_command_and_lists_all_removed_chars():
    msg = _build_invalid_chars_message([
        {"name": "Launch Zoom", "cleaned": "open zoom", "removed": "{}"},
        {"name": "Play Music", "cleaned": "play music", "removed": "!"},
    ])
    assert "have been removed:" in msg
    assert "! { }" in msg
    assert "Your phrases for the Launch Zoom command are now:" in msg
    assert "open zoom" in msg
    assert "Your phrases for the Play Music command are now:" in msg
    assert "play music" in msg


def test_message_shows_placeholder_when_nothing_survives():
    msg = _build_invalid_chars_message([
        {"name": "Broken", "cleaned": "", "removed": "{}"},
    ])
    assert "(no phrases left)" in msg


# -- _build_duplicate_removed_message -----------------------------------------

def test_duplicate_message_names_command_and_shows_result():
    msg = _build_duplicate_removed_message([
        {"name": "Open Dolphin", "cleaned": "open dolphin, launch dolphin",
         "removed": ["open dolphin"]},
    ])
    assert "duplicates have been removed" in msg
    assert "Your phrases for the Open Dolphin command are now:" in msg
    assert "open dolphin, launch dolphin" in msg


# -- find_cross_collision -----------------------------------------------------

def test_strips_taken_phrase_from_command_adding_it():
    info = find_cross_collision([
        _cmd("Launch Files", ["open dolphin"], original=["open dolphin"]),
        _cmd("Open Dolphin", ["open dolphin", "launch dolphin"]),
    ])
    assert info["phrase"] == "open dolphin"
    assert info["owner_name"] == "Launch Files"   # established owner keeps it
    assert info["loser_name"] == "Open Dolphin"
    assert info["loser_index"] == 1
    assert info["loser_cleaned"] == "launch dolphin"   # taken phrase gone


def test_moved_phrase_is_not_a_collision():
    # A dropped "open dolphin" and B added it -> only B holds it now.
    info = find_cross_collision([
        _cmd("A", ["launch app"], original=["open dolphin"]),
        _cmd("B", ["open dolphin"]),
    ])
    assert info is None


def test_established_owner_keeps_phrase_regardless_of_order():
    # The user command (index 0) can't strip a phrase off a built-in command
    # (index 1) that already owns it, even though it's scanned first.
    info = find_cross_collision([
        _cmd("My Command", ["shut down"]),
        _cmd("Shut Down", ["shut down"], original=["shut down"]),
    ])
    assert info["owner_name"] == "Shut Down"
    assert info["loser_name"] == "My Command"
    assert info["loser_index"] == 0
    assert info["loser_cleaned"] == ""   # nothing left


def test_two_new_commands_first_in_order_keeps_phrase():
    info = find_cross_collision([
        _cmd("First", ["play music"]),
        _cmd("Second", ["play music"]),
    ])
    assert info["owner_name"] == "First"
    assert info["loser_name"] == "Second"


def test_no_cross_collision_returns_none():
    info = find_cross_collision([
        _cmd("A", ["open dolphin"]),
        _cmd("B", ["launch zoom"]),
    ])
    assert info is None


# -- _build_cross_collision_message -------------------------------------------

def test_cross_message_names_owner_and_shows_result_no_note_when_enabled():
    msg = _build_cross_collision_message({
        "phrase": "open dolphin", "owner_name": "Launch Files",
        "owner_enabled": True, "loser_index": 1, "loser_name": "Open Dolphin",
        "loser_cleaned": "launch dolphin",
    })
    assert "already being used on the Launch Files command" in msg
    assert "Your phrases for the Open Dolphin command are now:" in msg
    assert "launch dolphin" in msg
    assert "disabling it" not in msg


def test_cross_message_adds_disabled_note_when_owner_disabled():
    msg = _build_cross_collision_message({
        "phrase": "x", "owner_name": "Owner", "owner_enabled": False,
        "loser_index": 1, "loser_name": "Loser", "loser_cleaned": "y",
    })
    assert "disabling it doesn't free them up" in msg
