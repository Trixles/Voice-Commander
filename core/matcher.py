"""
core/matcher.py
===============
Fuzzy-matching primitives shared between commands.py and
actions/windows.py.

Today this file holds only `_similarity` -- a thin SequenceMatcher
wrapper. Tier 4 of the refactor plan will move the rest of the
matcher logic here (_score_segment, _match_non_slot, _phrase_to_regex,
_extract_slots, _resolve_slot, TAIL_THRESHOLD) so the matching
pipeline can be unit-tested in isolation. Until then this is just a
leaf module to kill the duplicated _similarity definitions in
commands.py and actions/windows.py.
"""

from difflib import SequenceMatcher


def _similarity(a: str, b: str) -> float:
    """Return SequenceMatcher.ratio() of a and b, case-insensitive.

    Leading underscore kept so that when Tier 4 lifts the rest of the
    matcher in here, the public/private split inside the module is
    consistent. Callers that already used _similarity from commands.py
    or actions/windows.py keep the same name.
    """
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()
