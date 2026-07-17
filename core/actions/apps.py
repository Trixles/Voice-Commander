"""
core/actions/apps.py
====================
Actions for launching applications and opening URLs.

All functions receive GUI_ENV from the caller (via the dispatcher) so that
subprocesses inherit the correct Wayland/D-Bus environment even when running
under a systemd user service with no active desktop session.
"""

import os
import threading
import time

from core.notify import notify
from core.run import run_bg, run_capture


def _raise_browser(gui_env: dict) -> None:
    """Wait briefly for the browser to open the tab, then raise it via kglobalaccel."""
    time.sleep(0.5)
    run_capture(
        [
            "dbus-send", "--session", "--print-reply",
            "--dest=org.kde.kglobalaccel",
            "/component/kwin",
            "org.kde.kglobalaccel.Component.invokeShortcut",
            "string:Activate Window Demanding Attention",
        ],
        env=gui_env,
    )


def open_url(url: str, gui_env: dict, browser: str = "", context=None) -> None:
    """
    Open a URL in a browser, then raise the window.

    If `browser` is set (from the command's args in commands.json), use that
    executable directly. Otherwise fall back to xdg-open, which opens the
    URL in the system default browser.
    """
    if browser:
        run_bg([browser, url], env=gui_env, detach=True)
    else:
        run_bg(["xdg-open", url], env=gui_env, detach=True)
    threading.Thread(target=_raise_browser, args=(gui_env,), daemon=True).start()
    if context:
        context.update(last_command_name="open_url")


# Baked-in, deliberately not user-editable (see the Celery Man system command).
# Update here if the video ever moves -- it's been up since 2013, so: unlikely.
CELERY_MAN_URL = "https://www.youtube.com/watch?v=maAFcEU6atk"
# The YouTube video ID -- the invariant part of the URL. MPRIS players may
# append params (t=, list=) to what they report, so match on this, not the
# full URL.
_CELERY_MAN_VIDEO_ID = CELERY_MAN_URL.split("v=")[-1]


def _celery_man_is_playing(gui_env: dict) -> bool:
    """
    Return True if the Celery Man video is currently PLAYING in any MPRIS
    media player (browsers expose playing tabs over MPRIS; playerctl is
    already a hard dependency -- it drives the pause/resume commands).

    Matched by video ID in the reported URL, falling back to the title for
    browsers that don't expose xesam:url. Paused doesn't count: no audio
    means no echo, so a repeat command is genuinely the user.

    Fails OPEN: if playerctl is missing, errors, or reports no players, the
    command fires normally -- a broken check must never block the command.
    """
    try:
        result = run_capture(
            ["playerctl", "-a", "metadata", "--format",
             "{{status}}\t{{xesam:url}}\t{{xesam:title}}"],
            env=gui_env, timeout=3,
        )
    except Exception as e:
        print(f"[apps] celery_man playing-check failed ({e}); firing anyway")
        return False
    if result.returncode != 0:  # typically "No players found"
        return False
    for line in result.stdout.splitlines():
        status, _, rest = line.partition("\t")
        if status.strip() != "Playing":
            continue
        url, _, title = rest.partition("\t")
        if _CELERY_MAN_VIDEO_ID in url or "celery man" in title.lower():
            return True
    return False


def celery_man(gui_env: dict, context=None):
    """Load up Celery Man.

    Echo guard: the clip's own audio says "computer, load up celery man
    please" -- without a guard it would launch copies of itself. If the video
    is already playing (per _celery_man_is_playing), the command that matched
    is the video echoing, so decline by returning False -- the dispatcher then
    suppresses the notification and log line too, so an echo is fully silent.
    No wake words are muted; every other command stays fully usable while the
    video plays, and the guard ends the instant the video does.
    """
    if _celery_man_is_playing(gui_env):
        print("[apps] celery_man: video already playing -- echo blocked")
        return False
    open_url(CELERY_MAN_URL, gui_env=gui_env, context=context)


def launch_app(app: str, gui_env: dict, context=None) -> None:
    """Launch an application by executable name."""
    run_bg([app], env=gui_env, detach=True)
    if context:
        context.update(last_app=app, last_command_name="launch_app")


def open_file(path: str, gui_env: dict, context=None) -> None:
    """
    Open an arbitrary file or executable via xdg-open.
    Works for .desktop files, scripts, binaries, documents -- anything
    xdg-open knows how to handle. The user picks the path via the
    settings UI file browser; we just run it.
    """
    if not path or not path.strip():
        print("[apps] open_file: empty path, skipping")
        return
    if not os.path.exists(path):
        print(f"[apps] open_file: path does not exist: {path}")
        # Local import: core.commands imports this module, so a top-level
        # import would be circular. notifications_enabled() reads the Options
        # toggle; this error toast is general feedback and respects it.
        from core.commands import notifications_enabled
        if notifications_enabled():
            notify("File not found", f"Path does not exist:\n{path}",
                   timeout_ms=3000, gui_env=gui_env)
        return
    run_bg(["xdg-open", path], env=gui_env, detach=True)
    if context:
        context.update(last_command_name="open_file")
