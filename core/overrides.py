"""
core/overrides.py
=================
User-editable Vosk mishearing overrides.

Overrides are pre-match string rewrites applied to transcribed text BEFORE
the matcher sees it. They fill the precision niche in the matching pipeline:

    Overrides (precision)  -- user-controlled, deterministic rewrites for
                              consistent, observable Vosk mishearings.
    Fuzzy matcher (recall) -- forgives minor near-misses automatically.

The matcher is intentionally conservative; overrides are how the user (and
shipped defaults) take responsibility for the gray zone.

Hard rule: NO Qt imports here. Mirrors core/aliases.py -- this module must
be importable from core/commands.py and the install-time CLI.

Schema (in commands.json under top-level "overrides" key):
    [
      {"pattern": "hope in", "replacement": "open"},
      {"pattern": "cause",   "replacement": "close"},
      ...
    ]

Patterns and replacements are plain lowercase strings. Whole-word matching
is enforced via regex \\b boundaries under the hood -- users type plain
strings, never see regex. Replacement may be empty (for filler-word deletion).
Rules apply in list order; first-match-wins per overlapping pattern.

The "the " prefix hallucination filter is NOT an override -- it stays in
core/listener.py._normalize() as plumbing, because its mechanism (mic AGC
artifact) is categorically different from "Vosk heard X, user meant Y".
"""

import re


# -- Factory defaults --------------------------------------------------------
# Shipped with every install. Greyed-out, locked, undeletable in the UI.
# Match the historical hardcoded rules in core/listener.py._normalize() that
# pre-date the user-facing override system. "the " prefix strip is excluded
# (lives in listener.py as a hallucination filter, not a mishearing fix).
#
# Adding a new default here: append to the list. Removing one would be a
# breaking change for users who depend on it -- prefer leaving stale defaults
# in place over removing them.

DEFAULT_OVERRIDES: list[dict] = [
    {"pattern": "moved to", "replacement": "move to"},
    {"pattern": "up and",   "replacement": "open"},
    {"pattern": "hope in",  "replacement": "open"},
    {"pattern": "oh been",  "replacement": "open"},
    {"pattern": "cause",    "replacement": "close"},
    {"pattern": "mike",     "replacement": "microphone"},
]

# Pattern strings of defaults, for "is this a default?" checks in the UI.
DEFAULT_PATTERNS: set[str] = {o["pattern"] for o in DEFAULT_OVERRIDES}


# -- Compiled regex cache ----------------------------------------------------
# Keyed by pattern string. Word boundaries are added at compile time so the
# user can type "hope in" and we treat it as r"\bhope in\b". re.escape()
# handles any incidental regex metacharacters in user input -- they're typing
# plain strings, the regex layer is internal plumbing.

_pattern_cache: dict[str, re.Pattern] = {}


def _compile(pattern: str) -> re.Pattern:
    if pattern not in _pattern_cache:
        _pattern_cache[pattern] = re.compile(r"\b" + re.escape(pattern) + r"\b")
    return _pattern_cache[pattern]


# -- Sanitization ------------------------------------------------------------

def sanitize(pattern: str, replacement: str) -> tuple[str, str]:
    """Normalize a user-entered override row.

    - Lowercase both fields (Vosk emits lowercase; matching is case-blind).
    - Collapse runs of whitespace to single spaces and trim ends.
    - Empty replacement is allowed (filler-word deletion).
    - Empty pattern is invalid -- caller should reject the row before save.
    """
    pattern = " ".join(pattern.lower().split())
    replacement = " ".join(replacement.lower().split())
    return pattern, replacement


def is_valid(pattern: str, replacement: str) -> bool:
    """A row is valid iff the pattern is non-empty after sanitization.
    Replacement is allowed to be empty."""
    p, _ = sanitize(pattern, replacement)
    return bool(p)


# -- Apply -------------------------------------------------------------------

def apply_overrides(text: str, overrides: list[dict]) -> str:
    """Apply override rules in order to `text`, returning the rewritten text.

    Whole-word matching only -- a rule with pattern "in" will NOT corrupt
    "open" or "spin". Rules apply sequentially; the output of rule N is the
    input to rule N+1, matching the historical behaviour of _normalize()'s
    chained .replace() calls.

    Malformed rows (missing keys, non-string values) are skipped silently.
    This keeps a broken commands.json edit from killing the listener loop.
    """
    any_applied = False
    for row in overrides:
        if not isinstance(row, dict):
            continue
        pattern = row.get("pattern")
        replacement = row.get("replacement")
        if not isinstance(pattern, str) or not isinstance(replacement, str):
            continue
        if not pattern:
            continue
        new_text = _compile(pattern).sub(replacement, text)
        if new_text != text:
            any_applied = True
            text = new_text
    # Collapse whitespace runs introduced by empty-replacement deletions.
    # Cheap, only when we actually rewrote something.
    if any_applied:
        text = " ".join(text.split())
    return text
