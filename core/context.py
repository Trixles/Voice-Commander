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
        # Auto-pause-on-wake bookkeeping (see core/media_control.py and the
        # set_state hook in core/listener.py):
        #   auto_paused_players -- names of MPRIS players THIS wake window
        #     paused; resumed by name when the window closes. Also read by the
        #     Celery Man echo guard so a video WE paused still counts as an echo.
        #   media_touched -- set by media actions (media_pause/resume, open_url,
        #     and thus celery_man) during a wake window; suppresses auto-resume
        #     so a command that managed media isn't fought on the way out.
        self.auto_paused_players: list[str] = []
        self.media_touched: bool = False
        #   pending_media_snapshot -- (name, status, url, title) rows the
        #     Celery Man wake gate already fetched while deciding whether to
        #     wake; _auto_pause_on_wake consumes-and-clears it so a wake
        #     costs one playerctl query total, not two.
        self.pending_media_snapshot: list[tuple] | None = None

    def update(self, **kwargs):
        for k, v in kwargs.items():
            if not hasattr(self, k):
                raise AttributeError(f"Context has no attribute '{k}'")
            setattr(self, k, v)
