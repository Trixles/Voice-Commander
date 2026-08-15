"""
tests/test_wake.py
==================
Pure-logic tests for core/wake.py (WakeWordDetector). No Qt, no Vosk,
no subprocess.

`is_wake_only()` exists so the listener can tell, once it has entered
LISTENING early (off a partial wake), whether a finalized utterance was
*nothing but* the wake word -- in which case it must be swallowed
silently (keep listening) rather than toasting "No match". See the
2026-06-23 wake-feedback design.

Run from the repo root:  pytest -q
"""

import os
import sys

# Allow `pytest` to be invoked from the repo root without installing the
# package (same shim as tests/test_matcher.py).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.wake import WakeWordDetector  # noqa: E402


# -- check: whole-word wake match (NOT substring) -----------------------------
#
# The wake fires on the wake word as a *word*, never as a substring buried in
# a longer word. "computer" wakes; "computerized" does not. This is the whole
# point of word-boundary matching -- a bare substring check trips the wake on
# any longer word that happens to embed it.

def test_check_exact_wake_word_matches():
    d = WakeWordDetector(["computer"])
    assert d.check("computer") is True


def test_check_embedded_substring_does_not_match():
    """'computerized' embeds 'computer' but is a different word -> must NOT
    wake. Regression guard for the substring-matching bug."""
    d = WakeWordDetector(["computer"])
    assert d.check("computerized") is False


def test_check_wake_word_mid_sentence_matches():
    """The wake word as a whole word anywhere in the utterance still wakes."""
    d = WakeWordDetector(["computer"])
    assert d.check("please computer open reddit") is True


def test_check_multiword_wake_phrase_matches():
    d = WakeWordDetector(["hey computer"])
    assert d.check("hey computer open reddit") is True


def test_check_multiword_phrase_embedded_does_not_match():
    """The last token of a multi-word phrase must also respect word
    boundaries: 'hey computerized' is not 'hey computer'."""
    d = WakeWordDetector(["hey computer"])
    assert d.check("hey computerized") is False


def test_check_is_case_insensitive():
    """Vosk emits lowercase, but the detector must not depend on that."""
    d = WakeWordDetector(["Computer"])
    assert d.check("COMPUTER") is True


def test_check_tolerates_adjacent_punctuation():
    """Word boundaries fall at punctuation too, so a stray trailing mark
    (defensive -- Vosk normally emits none) doesn't hide the wake word."""
    d = WakeWordDetector(["computer"])
    assert d.check("computer.") is True


def test_check_regex_metacharacter_wake_word_is_literal():
    """A user-supplied wake word containing regex metacharacters must be
    matched literally, not interpreted as a pattern."""
    d = WakeWordDetector(["c++"])
    assert d.check("c++") is True
    assert d.check("computer") is False


# -- is_wake_only: True only when the text is the wake word and nothing else --

def test_bare_wake_word_is_wake_only():
    """The whole point: 'computer' alone, said then silence, must read as
    wake-only so the listener keeps listening instead of toasting No match."""
    d = WakeWordDetector(["computer"])
    assert d.is_wake_only("computer") is True


def test_wake_plus_command_is_not_wake_only():
    """A real command rides in with the wake word -> not wake-only; it must
    fall through to matching."""
    d = WakeWordDetector(["computer"])
    assert d.is_wake_only("computer open reddit") is False


def test_empty_text_is_not_wake_only():
    """No wake word present at all -> not wake-only (nothing to swallow)."""
    d = WakeWordDetector(["computer"])
    assert d.is_wake_only("") is False


def test_command_without_wake_word_is_not_wake_only():
    """Plain command text in LISTENING is a normal utterance, not a bare
    wake -> not wake-only, so existing match/no-match behaviour is unchanged."""
    d = WakeWordDetector(["computer"])
    assert d.is_wake_only("open reddit") is False


def test_wake_word_embedded_in_longer_word_is_not_wake_only():
    """'computerized' is not a whole-word wake match at all, so it is NOT
    wake-only -> falls through to a normal No match rather than being
    silently swallowed."""
    d = WakeWordDetector(["computer"])
    assert d.is_wake_only("computerized") is False


def test_multiword_wake_phrase_is_wake_only():
    """A multi-word wake phrase said alone is still wake-only."""
    d = WakeWordDetector(["hey computer"])
    assert d.is_wake_only("hey computer") is True


# -- fuzzy_check: text-confirmation of an audio wake --------------------------

def test_fuzzy_check_passes_exact_wake_word():
    d = WakeWordDetector(["computer"])
    assert d.fuzzy_check("computer") is True
    assert d.fuzzy_check("computer open firefox") is True


def test_fuzzy_check_passes_near_mishearing():
    # Whisper dropping a letter must still confirm a real wake.
    d = WakeWordDetector(["computer"])
    assert d.fuzzy_check("compute") is True      # ~0.93 ratio
    assert d.fuzzy_check("commuter") is True      # ~0.87 ratio


def test_fuzzy_check_rejects_a_wild_mangling():
    # "peter" for "computer" is far enough that at a sane threshold it reads
    # as "didn't say it" -- the accepted recall cost.
    d = WakeWordDetector(["computer"])
    assert d.fuzzy_check("peter", threshold=0.7) is False


def test_fuzzy_check_rejects_unrelated_text():
    d = WakeWordDetector(["computer"])
    assert d.fuzzy_check("okay") is False
    assert d.fuzzy_check("what is your problem") is False
    assert d.fuzzy_check("") is False


def test_fuzzy_check_threshold_zero_is_exact_only():
    # threshold 0 disables the fuzzy step: only exact whole-word matches pass.
    d = WakeWordDetector(["computer"])
    assert d.fuzzy_check("computer", threshold=0) is True
    assert d.fuzzy_check("compute", threshold=0) is False


def test_fuzzy_check_multiword_phrase():
    d = WakeWordDetector(["hey dude"])
    assert d.fuzzy_check("hey dude") is True
    assert d.fuzzy_check("hey dud") is True                # near mishearing
    assert d.fuzzy_check("random noise here") is False
