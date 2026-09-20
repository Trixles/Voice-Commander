"""
core/media_control.py
=====================
Per-player MPRIS pause/resume helpers for the auto-pause-on-wake feature.

Why per-player (``playerctl -p <name>``) instead of the bare
``playerctl pause`` the manual media commands use: auto-pause must resume
EXACTLY the players it paused, and touch nothing else. Bare playerctl acts
on the "first player on the bus", so it could pause one player and resume a
different one -- fine for a user-driven one-shot, wrong for a restore. We
snapshot the names that were Playing, pause those, and later play those same
names back.

playerctl is already a hard dependency (it drives media_pause/media_resume
and the Celery Man echo guard), so this adds no new requirement.

All functions FAIL SAFE: a missing/erroring playerctl yields an empty
playing-list (so nothing is paused, so nothing is later resumed) and never
raises into the listener loop -- a broken media probe must never take down
the wake path.
"""

from core.run import run_bg, run_capture

# One query returns name + status for every MPRIS player. Matches the
# invocation style of the Celery Man echo guard in core/actions/apps.py.
_LIST_ARGS = [
    "playerctl", "-a", "metadata",
    "--format", "{{playerName}}\t{{status}}",
]

# Full-row variant: adds url + title so ONE query can answer both the Celery
# Man wake gate ("is the video playing?") and auto-pause ("who is Playing?").
_SNAPSHOT_ARGS = [
    "playerctl", "-a", "metadata",
    "--format", "{{playerName}}\t{{status}}\t{{xesam:url}}\t{{xesam:title}}",
]


def metadata_snapshot(gui_env: dict) -> list[tuple[str, str, str, str]] | None:
    """(name, status, url, title) for every MPRIS player.

    Distinguishes two degrades the callers treat differently:
      []   -- playerctl answered "no players" (non-zero exit). A real,
              empty answer: the gate passes, auto-pause pauses nothing.
      None -- the probe itself failed (playerctl missing, timeout). The
              wake gate fails OPEN and auto-pause falls back to its own
              list_playing_players query.
    """
    try:
        result = run_capture(_SNAPSHOT_ARGS, env=gui_env, timeout=2)
    except Exception as e:
        print(f"[media_control] metadata snapshot failed ({e})")
        return None
    if result.returncode != 0:  # typically "No players found"
        return []
    rows: list[tuple[str, str, str, str]] = []
    for line in result.stdout.splitlines():
        name, status, url, title = (line.split("\t", 3) + ["", "", "", ""])[:4]
        if name.strip():
            rows.append((name.strip(), status.strip(), url, title))
    return rows


def list_playing_players(gui_env: dict) -> list[str]:
    """Names of every MPRIS player currently reporting status 'Playing'.

    Returns [] on any failure (playerctl missing, no players, non-zero
    exit) -- an empty snapshot means auto-pause simply does nothing, which
    is the safe degrade.
    """
    try:
        result = run_capture(_LIST_ARGS, env=gui_env, timeout=2)
    except Exception as e:
        # Timeout or playerctl-not-found: pause nothing rather than blocking
        # the wake path or crashing it.
        print(f"[media_control] playing-check failed ({e}); pausing nothing")
        return []
    if result.returncode != 0:  # typically "No players found"
        return []
    names: list[str] = []
    for line in result.stdout.splitlines():
        name, _, status = line.partition("\t")
        if status.strip() == "Playing" and name.strip():
            names.append(name.strip())
    return names


def pause_players(names: list[str], gui_env: dict) -> None:
    """Pause each named player. Fire-and-forget; a name that has since
    vanished just makes playerctl no-op."""
    for name in names:
        run_bg(["playerctl", "-p", name, "pause"], env=gui_env)


def resume_players(names: list[str], gui_env: dict) -> None:
    """Resume (play) each named player -- the exact set pause_players was
    given, so players we never touched stay untouched."""
    for name in names:
        run_bg(["playerctl", "-p", name, "play"], env=gui_env)
