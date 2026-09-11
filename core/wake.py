"""
core/wake.py
============
Wake word detector.

Whole-word check against the list of wake words on transcribed text.
Matching is on word boundaries, NOT raw substrings: the wake word "computer"
wakes on "computer" but NOT on "computerized" (which merely embeds it).

Stdlib only (`re`, `difflib`) -- no PySide6, no engine deps. The module is pure
logic with a pure-logic test suite (tests/test_wake.py); keep it that way.
"""

import re
from difflib import SequenceMatcher

# The wake word the shipped openWakeWord model (computer_v2) detects. It is
# structural, not user data: it is always active (audio + text paths) and is
# prepended to the user's custom wake words. Kept here beside the detector so
# the module stays pure stdlib and every detector consumer sees one source.
BAKED_IN_WAKE_WORDS = ["computer"]


def custom_wake_words(words: list[str]) -> list[str]:
    """The user's custom wake words: `words` lowercased/stripped with any
    baked-in word removed, order preserved. One source of truth for what the
    Commands-tab field shows, what a save stores, and what get_wake_words
    appends after the baked-in word."""
    baked = set(BAKED_IN_WAKE_WORDS)
    out = []
    for w in words:
        s = (w or "").strip().lower()
        if s and s not in baked:
            out.append(s)
    return out


class WakeWordDetector:
    def __init__(self, wake_words: list[str]):
        # Normalize each wake word to lowercase with internal whitespace
        # collapsed, so "Hey  Computer" stores as "hey computer".
        self.wake_words = [" ".join(w.lower().split()) for w in wake_words]

        # Precompile one whole-word pattern per wake word. Matching is done
        # once per audio partial, so compiling up front avoids re-parsing the
        # regex on every call.
        #
        #   (?<!\w) ... (?!\w) -- the wake word must not be flanked by a word
        #                 character, so it stands on its own rather than sitting
        #                 inside a longer word ("computerized" does NOT trip
        #                 "computer"). We use lookarounds instead of \b because
        #                 \b needs a word char on one side: a wake word that
        #                 starts or ends in punctuation ("c++") has none there,
        #                 and \b would wrongly reject it. Lookarounds only care
        #                 that no *word* char abuts the match, so punctuation
        #                 and end-of-string both count as a clean boundary.
        #   re.escape  -- wake words come from user config; escape so a word
        #                 like "c++" is matched literally, not as a pattern.
        #   \s+ join   -- a multi-word phrase ("hey computer") tolerates any run
        #                 of whitespace between its tokens.
        self._patterns: list[re.Pattern] = []
        for w in self.wake_words:
            if not w:  # skip blank entries; an empty pattern would match all
                continue
            tokens = [re.escape(tok) for tok in w.split()]
            body = r"\s+".join(tokens)
            self._patterns.append(re.compile(r"(?<!\w)" + body + r"(?!\w)"))

    def check(self, text: str) -> bool:
        """True if any wake word occurs as a whole word in text."""
        t = text.lower()
        return any(p.search(t) for p in self._patterns)

    def fuzzy_check(self, text: str, threshold: float = 0.7) -> bool:
        """True if `text` contains a wake word within fuzzy tolerance.

        An exact whole-word match (see `check`) always passes. Otherwise a
        same-length token window is slid across the text and accepted if any
        window is at least `threshold` similar to a wake word (difflib
        ratio, 0-1). This is the text-confirmation of an audio wake: whisper
        near-mishearings ("compute", "commuter") still confirm, while a wild
        mangling ("peter" for "computer", ~0.6) stays below a sane threshold
        and is treated as "didn't actually say it". `threshold <= 0`
        disables the fuzzy step, leaving only the exact check."""
        if self.check(text):
            return True
        if threshold <= 0:
            return False
        tokens = text.lower().split()
        for w in self.wake_words:
            n = len(w.split())
            for i in range(len(tokens) - n + 1):
                window = " ".join(tokens[i:i + n])
                if SequenceMatcher(None, window, w).ratio() >= threshold:
                    return True
        return False

    def is_wake_only(self, text: str) -> bool:
        """True iff `text` contains a wake word and nothing else of substance.

        Used by the listener: once it has entered LISTENING early off a
        partial wake, a finalized utterance that is *just* the wake word
        ("computer", then silence) must be swallowed silently -- keep
        listening for the real command -- instead of toasting "No match".

        Requires a wake word to actually be present (so empty / wake-free
        text is never "wake-only"), then removes every whole-word wake
        occurrence and checks that only whitespace remains. Removal uses the
        same word-boundary patterns as `check`, so "computerized" -- which is
        not a whole-word match at all -- never reaches the removal step and is
        NOT wake-only.
        """
        t = text.lower().strip()
        if not self.check(t):
            return False
        for p in self._patterns:
            t = p.sub(" ", t)
        return not t.strip()
