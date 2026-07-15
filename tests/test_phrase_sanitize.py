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
    _build_invalid_chars_message,
)


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


# -- _build_invalid_chars_message ---------------------------------------------

def test_message_names_each_command_and_lists_all_removed_chars():
    msg = _build_invalid_chars_message([
        {"name": "Launch Zoom", "cleaned": "open zoom", "removed": "{}"},
        {"name": "Play Music", "cleaned": "play music", "removed": "!"},
    ])
    assert "removed these characters: ! { }" in msg
    assert "«Launch Zoom» — your phrases for this command are now:" in msg
    assert "open zoom" in msg
    assert "«Play Music» — your phrases for this command are now:" in msg
    assert "play music" in msg


def test_message_shows_placeholder_when_nothing_survives():
    msg = _build_invalid_chars_message([
        {"name": "Broken", "cleaned": "", "removed": "{}"},
    ])
    assert "(no phrases left)" in msg
