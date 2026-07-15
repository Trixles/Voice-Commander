"""
core/matcher.py
===============
Fuzzy-matching primitives for the command pipeline.

This module owns the pure scoring/extraction logic: given a heard
phrase and a command phrase (possibly slot-bearing), how well do they
match, and what slot values come out? It has no knowledge of the
command registry or alias machinery -- that orchestration stays in
`core/commands.py` next to the data it walks (`_score_segment`).

Keeping this module Qt-free and registry-free lets the matching
pipeline be unit-tested in isolation.
"""

import os
import re
from difflib import SequenceMatcher


# Per-comparison debug output is gated on VC_DEBUG_MATCHER=1. Off by
# default since _match_non_slot prints once per (heard, phrase) pair --
# noisy in journalctl. Flip it on when debugging why a command did or
# didn't match (in a systemd unit, add Environment=VC_DEBUG_MATCHER=1).
_DEBUG = os.environ.get("VC_DEBUG_MATCHER") == "1"


# Tail-rescore threshold for non-slot fuzzy matches whose leading token is
# shared with the phrase (e.g. "open ..." vs "open ..."). The full-string
# ratio rewards the shared prefix structurally, so we require the *rest* of
# the strings to clear a lower-but-meaningful bar. Calibrated to reject
# "open like" vs "open plex" (tail score 0.50) while accepting plausible
# Vosk garbles like "open plix" vs "open plex" (0.75) and "open dolfen"
# vs "open dolphin" (0.62). Don't lower below ~0.55 -- the gap to
# semantically-unrelated tails narrows fast there.
TAIL_THRESHOLD = 0.60


# Minimum match floor for single-token command phrases. Short phrases score
# deceptively high under SequenceMatcher: one swapped letter in a 5-char word
# still yields ~0.80 ("cause" vs "pause"), which clears the 0.75 default and
# fires the wrong command. Requiring a higher floor for single-token phrases is
# the single-token analogue of TAIL_THRESHOLD. Applied as a hard reject inside
# _match_non_slot, so the effective bar for a short phrase is max(global
# threshold, this) -- never looser than the user's strictness setting.
SHORT_PHRASE_THRESHOLD = 0.85


def _similarity(a: str, b: str) -> float:
    """Return SequenceMatcher.ratio() of a and b, case-insensitive."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


# -- Slot-bearing phrase handling ---------------------------------------------

def _has_slots(phrase: str) -> bool:
    return "{" in phrase and "}" in phrase


def _phrase_to_regex(phrase: str) -> re.Pattern:
    parts = re.split(r"(\{[^}]+\})", phrase)
    pattern_parts = []
    for i, part in enumerate(parts):
        if part.startswith("{") and part.endswith("}"):
            slot_name = part[1:-1]
            # Greedy when the slot is the last token (nothing meaningful after it).
            # Lazy when there's literal text after the slot that the regex must match.
            remaining = "".join(parts[i + 1:]).strip()
            quantifier = ".+" if not remaining else ".+?"
            pattern_parts.append(f"(?P<{slot_name}>{quantifier})")
        else:
            pattern_parts.append(re.escape(part))
    return re.compile(r"(?:^|\b)" + "".join(pattern_parts) + r"(?:\b|$)", re.IGNORECASE)


def _extract_slots(heard: str, phrase: str) -> dict[str, str] | None:
    # A slot name that isn't a valid Python group identifier (spaces, hyphens,
    # dots, a leading digit, empty {}, or a duplicate name) makes
    # _phrase_to_regex raise re.error. That must never propagate: this runs on
    # the listener thread for every utterance, so an unguarded raise would kill
    # voice control until the offending phrase is removed from commands.json.
    # The settings UI strips these characters on save, but a hand-edited config
    # can still carry them -- treat an uncompilable slot phrase as "no match".
    try:
        pattern = _phrase_to_regex(phrase)
    except re.error as e:
        print(f"[matcher] Skipping uncompilable slot phrase {phrase!r}: {e}")
        return None
    match = pattern.search(heard)
    if not match:
        return None
    return {k: v.strip() for k, v in match.groupdict().items()}


def _resolve_slot(slot_name: str, raw_value: str, slot_def: dict) -> str:
    if not slot_def.get("fuzzy", False):
        return raw_value
    known = slot_def.get("known_values")
    if not known:
        raise ValueError(f"Slot '{slot_name}' has fuzzy=true but no known_values")
    best_match = max(known, key=lambda k: _similarity(raw_value, k))
    best_score = _similarity(raw_value, best_match)
    if best_score >= 0.5:
        print(f"  Slot '{slot_name}': '{raw_value}' -> '{best_match}' (score {best_score:.2f})")
        return best_match
    print(f"  Slot '{slot_name}': '{raw_value}' fuzzy match failed (best '{best_match}' @ {best_score:.2f})")
    return raw_value


# -- Non-slot phrase matching -------------------------------------------------

def _match_non_slot(heard: str, phrase: str) -> float:
    """
    Score a non-slot phrase against heard text.

    Returns SequenceMatcher.ratio() of the full strings, with one guard:
    if heard and phrase share their leading token (the "open"/"launch"/etc.
    verb prefix that aliases many commands), the *tail* of both strings is
    scored separately and must clear TAIL_THRESHOLD. This stops the shared
    verb from carrying a match where the actual content words are unrelated
    (e.g. "open like" vs "open plex" -- "open " contributes most of the
    ratio, but "like" vs "plex" scores 0.50 on its own).

    Single-word phrases and phrases without a shared leading token skip the
    guard entirely and fall back to the plain overall ratio.
    """
    overall = _similarity(heard, phrase)

    h_tokens = heard.split()
    p_tokens = phrase.split()

    # Short single-token phrases ("pause", "mute", "play") demand a higher floor:
    # a one-letter substitution still scores ~0.80, clearing the 0.75 default
    # ("cause" -> "pause"). Reject anything under SHORT_PHRASE_THRESHOLD. This is
    # the single-token analogue of the tail-rescore guard below.
    if len(p_tokens) == 1:
        if overall < SHORT_PHRASE_THRESHOLD:
            if _DEBUG:
                print(f"  '{heard}' vs '{phrase}': {overall:.2f} "
                      f"(single-token < {SHORT_PHRASE_THRESHOLD} -- rejected)")
            return 0.0
        if _DEBUG:
            print(f"  '{heard}' vs '{phrase}': {overall:.2f} "
                  f"(single-token, cleared {SHORT_PHRASE_THRESHOLD})")
        return overall

    if (len(h_tokens) >= 2 and len(p_tokens) >= 2
            and h_tokens[0].lower() == p_tokens[0].lower()):
        h_tail = " ".join(h_tokens[1:])
        p_tail = " ".join(p_tokens[1:])
        tail = _similarity(h_tail, p_tail)
        if tail < TAIL_THRESHOLD:
            if _DEBUG:
                print(f"  '{heard}' vs '{phrase}': {overall:.2f} (tail {tail:.2f} < {TAIL_THRESHOLD} -- rejected)")
            return 0.0
        if _DEBUG:
            print(f"  '{heard}' vs '{phrase}': {overall:.2f} (tail {tail:.2f})")
        return overall

    if _DEBUG:
        print(f"  '{heard}' vs '{phrase}': {overall:.2f}")
    return overall
