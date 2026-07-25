"""
core/notify.py
==============
Single notify-send wrapper. Replaces the two former _notify() copies
in commands.py and listener.py.

The 3000ms default IS the standard duration -- this is its single
definition. Only callers whose toast must outlive it pass an explicit
timeout_ms, and both of them tie the value to a real window they
represent ("Listening..." to command_window, the confirm prompt to
CONFIRM_WINDOW) rather than picking a number.

Note that --expire-time is only a HINT under the freedesktop spec:
Plasma's notification service may override it or keep undismissed
toasts in its history regardless.
"""

import subprocess


def notify(summary: str, body: str = "", timeout_ms: int = 3000, gui_env: dict | None = None) -> None:
    """Send a desktop notification via notify-send.

    Fire-and-forget: returns immediately. notify-send is launched with
    Popen and not waited on. If notify-send fails or is missing, the
    error surfaces in journalctl but does not block the caller.
    """
    subprocess.Popen(
        ["notify-send", "--app-name=Voice Commander", f"--expire-time={timeout_ms}", summary, body],
        env=gui_env,
    )
