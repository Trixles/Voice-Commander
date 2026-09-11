"""
core/actions/system.py
======================
Actions for system-level controls: volume, media, power, and settings UI.

Volume is controlled via pactl (PipeWire exposes a PulseAudio-compatible
interface). Media play/pause uses playerctl, which talks to MPRIS-compatible
players (Plex, YouTube in browser, etc.) over D-Bus.

Power commands (shutdown, restart, logout) go through KDE's session
manager (org.kde.Shutdown on the session bus) rather than systemctl.
This is the same path the physical power button takes: the theme's
logout sound plays, and apps get a clean session-managed exit.
`systemctl poweroff` would skip both.
"""

from core.run import run_bg, run_capture


# -- Volume word-to-int -------------------------------------------------------
# Vosk transcribes speech as text, so "fifty" arrives as the string "fifty".
# We only need multiples of 5 up to 100.

_WORD_TO_INT: dict[str, int] = {
    "five": 5, "ten": 10, "fifteen": 15, "twenty": 20,
    "twenty five": 25, "thirty": 30, "thirty five": 35,
    "forty": 40, "forty five": 45, "fifty": 50,
    "fifty five": 55, "sixty": 60, "sixty five": 65,
    "seventy": 70, "seventy five": 75, "eighty": 80,
    "eighty five": 85, "ninety": 90, "ninety five": 95,
    "one hundred": 100, "hundred": 100,
}


def _parse_level(raw: str) -> int:
    """
    Convert a raw slot string to an integer volume level.
    Accepts digits ("50") or spoken words ("fifty").
    Raises ValueError if the input can't be resolved.
    """
    raw = raw.strip().lower()
    if raw.isdigit():
        return int(raw)
    if raw in _WORD_TO_INT:
        return _WORD_TO_INT[raw]
    raise ValueError(f"Cannot parse volume level from '{raw}'")


# -- Volume -------------------------------------------------------------------

def volume_up(gui_env: dict, percent: int = 10, context=None) -> None:
    """Raise default sink volume by `percent`%."""
    run_capture(
        ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"+{percent}%"],
        env=gui_env,
    )


def volume_down(gui_env: dict, percent: int = 10, context=None) -> None:
    """Lower default sink volume by `percent`%."""
    run_capture(
        ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"-{percent}%"],
        env=gui_env,
    )


def set_volume(level: str, gui_env: dict, context=None) -> None:
    """
    Set default sink volume to an absolute level (0-100).
    `level` is the raw string extracted from speech -- digits or words.
    """
    try:
        value = _parse_level(level)
    except ValueError as e:
        print(f"[system] set_volume: {e}")
        return
    value = max(0, min(100, value))
    run_capture(
        ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{value}%"],
        env=gui_env,
    )


def mute(gui_env: dict, context=None) -> None:
    """Mute the default sink unconditionally."""
    run_capture(
        ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1"],
        env=gui_env,
    )


def unmute(gui_env: dict, context=None) -> None:
    """Unmute the default sink unconditionally."""
    run_capture(
        ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"],
        env=gui_env,
    )


# -- Media --------------------------------------------------------------------

def media_pause(gui_env: dict, context=None) -> None:
    """Pause the active MPRIS media player."""
    run_bg(["playerctl", "pause"], env=gui_env)
    # The user is managing playback this wake window -> don't let auto-pause
    # resume on the way out (they asked for pause; keep it paused).
    if context:
        context.update(media_touched=True)


def media_resume(gui_env: dict, context=None) -> None:
    """Resume the active MPRIS media player."""
    run_bg(["playerctl", "play"], env=gui_env)
    # Same reasoning: a spoken resume already restarted playback, so the
    # window's auto-resume must not second-guess it.
    if context:
        context.update(media_touched=True)


# -- Power --------------------------------------------------------------------

def _kde_session_exit(method: str, gui_env: dict) -> None:
    """
    Invoke one of org.kde.Shutdown's session-exit methods (logout,
    logoutAndReboot, logoutAndShutdown). All power commands funnel
    through here so they behave identically to the power button.
    """
    run_bg(
        ["dbus-send", "--session", "--print-reply",
         "--dest=org.kde.Shutdown", "/Shutdown",
         f"org.kde.Shutdown.{method}"],
        env=gui_env,
    )


def shutdown(gui_env: dict, context=None) -> None:
    """Shut down via KDE session manager (plays logout sound, clean exit)."""
    _kde_session_exit("logoutAndShutdown", gui_env)


def restart(gui_env: dict, context=None) -> None:
    """Reboot via KDE session manager (plays logout sound, clean exit)."""
    _kde_session_exit("logoutAndReboot", gui_env)


def logout(gui_env: dict, context=None) -> None:
    """Log out of the current KDE Plasma 6 session."""
    _kde_session_exit("logout", gui_env)


# -- Shell command ------------------------------------------------------------

def run_command(command: str, gui_env: dict, context=None) -> None:
    """
    Execute an arbitrary shell command string.
    The command string is passed directly to the shell -- the user is
    responsible for what they configure here.
    Non-blocking: fire-and-forget via Popen.
    """
    if not command or not command.strip():
        print("[system] run_command: empty command, skipping")
        return
    run_bg(command, shell=True, env=gui_env, detach=True)


# -- Settings UI --------------------------------------------------------------
# The tray registers a callback at startup via register_open_settings().
# open_settings() is a normal action function; it just delegates to that
# callback. No-ops silently if the tray hasn't registered yet (shouldn't
# happen in practice, but fail-safe beats crash).

_open_settings_callback = None


def register_open_settings(fn) -> None:
    """Called by VoiceCommanderTray.__init__ to wire in the settings opener."""
    global _open_settings_callback
    _open_settings_callback = fn


def open_settings(gui_env: dict, context=None) -> None:
    """Open the Voice Commander settings dialog."""
    if _open_settings_callback is None:
        print("[system] open_settings: no callback registered, ignoring")
        return
    _open_settings_callback()
