"""
core/notify.py
==============
Single notify-send wrapper. Replaces the two former _notify() copies
in commands.py and listener.py.

Default timeout is 2000ms to match the documented 'standard duration'
constant (listener.NOTIFY_DURATION_MS). Callers that want longer
notifications (e.g. the confirm window in listener.py) pass an
explicit timeout_ms.
"""

import subprocess


def notify(summary: str, body: str = "", timeout_ms: int = 2000, gui_env: dict | None = None) -> None:
    """Send a desktop notification via notify-send.

    Fire-and-forget: returns immediately. notify-send is launched with
    Popen and not waited on. If notify-send fails or is missing, the
    error surfaces in journalctl but does not block the caller.
    """
    subprocess.Popen(
        ["notify-send", "--app-name=Voice Commander", f"--expire-time={timeout_ms}", summary, body],
        env=gui_env,
    )
