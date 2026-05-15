"""
core/aliases.py
===============
Shared constants and helpers for monitor aliases and slot-pinned commands.

This module exists to eliminate the "keep in sync" coupling that previously
duplicated PINNED_SLOT, default_aliases(), and NUMBER_WORDS across
core/commands.py and core/settings.py.

Hard rule: NO Qt imports here. core/commands.py imports this module, and
core/commands.py must remain Qt-free so it can be invoked via
'python -m core.commands --emit-defaults' from install.sh without dragging
PySide6 into the install path.
"""


# -- Monitor alias defaults ---------------------------------------------------

NUMBER_WORDS = [
    "one", "two", "three", "four", "five",
    "six", "seven", "eight", "nine", "ten",
]


def default_aliases(index_1based: int) -> list[str]:
    """Generate default aliases for a monitor at 1-based index.
    Returns e.g. ["monitor two", "monitor to"] for index 2.
    Vosk outputs phonetic text only, so numeric aliases are useless."""
    if index_1based <= len(NUMBER_WORDS):
        word = NUMBER_WORDS[index_1based - 1]
        aliases = [f"monitor {word}"]
        # Common Vosk mishearing: "two" -> "to"
        if word == "two":
            aliases.append("monitor to")
        return aliases
    return [f"monitor {index_1based}"]


# -- Slot-pinned commands -----------------------------------------------------
# Permanently expanded, fully read-only in the UI, always at the bottom of the
# commands list. Written to commands.json by both install.sh (via
# 'python -m core.commands --emit-defaults') and the Restore Defaults button.
#
# Invariant: slot name in the phrase MUST match the slot name in 'slots'.
# 'move_to_monitor' uses {alias} (NOT {monitor}) -- the UI calls them aliases
# everywhere, and renaming back would split the system into inconsistent halves.

PINNED_SLOT: list[dict] = [
    {
        "name": "set_volume",
        "display_name": "Set Volume",
        "phrases": ["set volume to {level}", "set it to {level}"],
        "action": "set_volume",
        "args": {},
        "slots": {"level": {"fuzzy": False}},
    },
    {
        "name": "move_to_monitor",
        "display_name": "Move to Alias",
        "phrases": ["move to {alias}"],
        "action": "move_window_to_monitor",
        "args": {},
        "slots": {"alias": {"fuzzy": False}},
    },
]

PINNED_SLOT_NAMES: set[str] = {p["name"] for p in PINNED_SLOT}
