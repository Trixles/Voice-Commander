"""
core/context.py
===============
Shared state object and State enum.

The Context instance is created once in the entry point and passed into
action functions that need to read or update it. The state machine in
listener.py owns state transitions; context just holds the current value.
"""

from enum import Enum


class State(Enum):
    SLEEPING    = "sleeping"    # waiting for wake word
    LISTENING   = "listening"   # wake word heard, waiting for command
    CONFIRMING  = "confirming"  # dangerous command matched, awaiting "confirm"
    OPEN_MIC    = "open_mic"    # always processing commands, no wake word needed
    ERROR       = "error"       # mic dead or unrecoverable problem


class Context:
    def __init__(self):
        self.state: State = State.SLEEPING
        self.last_app: str | None = None
        self.last_window_title: str | None = None
        self.last_command_name: str | None = None
        self.last_monitor: int | None = None
        self.pending_confirm: dict | None = None   # command dict awaiting confirmation
        self.pending_args: dict | None = None      # resolved args for pending command
        # Wake-word suppression: while now < wake_suppress_until, the listener
        # ignores wake_suppress_word in the SLEEPING state. Set by the Celery
        # Man command (its video says "computer" and would trip the wake word).
        self.wake_suppress_word: str | None = None
        self.wake_suppress_until: float = 0.0

    def update(self, **kwargs):
        for k, v in kwargs.items():
            if not hasattr(self, k):
                raise AttributeError(f"Context has no attribute '{k}'")
            setattr(self, k, v)
