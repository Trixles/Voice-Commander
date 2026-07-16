"""
core/wake.py
============
Wake word detector.

Whole-word check against the list of wake words on Vosk-transcribed text.
Matching is on word boundaries, NOT raw substrings: the wake word "computer"
wakes on "computer" but NOT on "computerized" (which merely embeds it).

Stdlib only (`re`) -- no PySide6, no Vosk. The module is pure logic with a
pure-logic test suite (tests/test_wake.py); keep it that way.
"""

import re


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
        # Stored as (word, pattern) pairs so `check` can exclude specific wake
        # words on demand (the Celery Man command mutes "computer" for a while).
        self._patterns: list[tuple[str, re.Pattern]] = []
        for w in self.wake_words:
            if not w:  # skip blank entries; an empty pattern would match all
                continue
            tokens = [re.escape(tok) for tok in w.split()]
            body = r"\s+".join(tokens)
            self._patterns.append((w, re.compile(r"(?<!\w)" + body + r"(?!\w)")))

    def check(self, text: str, exclude=()) -> bool:
        """True if any (non-excluded) wake word occurs as a whole word in text.

        `exclude` is an iterable of wake words to ignore for this call --
        normalized the same way as the stored words -- so a temporarily muted
        wake word doesn't count. An excluded word that isn't a configured wake
        word is simply a no-op.
        """
        t = text.lower()
        muted = {" ".join(w.lower().split()) for w in exclude}
        return any(p.search(t) for w, p in self._patterns if w not in muted)

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
        for _w, p in self._patterns:
            t = p.sub(" ", t)
        return not t.strip()
