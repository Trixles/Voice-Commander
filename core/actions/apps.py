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
        # Deliberately does NOT set media_touched: most URLs (a webpage, a
        # search) start no audio, and flagging them would strand the pre-wake
        # music paused for nothing. Whether resuming would stack over media
        # this URL started is decided by REALITY at resume time (the listener
        # re-checks what's actually playing), not pessimistically here. An
        # action that KNOWS it autoplays (celery_man) sets the flag itself.
        context.update(last_command_name="open_url")


# Baked-in, deliberately not user-editable (see the Celery Man system command).
# Update here if the video ever moves -- it's been up since 2013, so: unlikely.
CELERY_MAN_URL = "https://www.youtube.com/watch?v=maAFcEU6atk"
# The YouTube video ID -- the invariant part of the URL. MPRIS players may
# append params (t=, list=) to what they report, so match on this, not the
# full URL.
_CELERY_MAN_VIDEO_ID = CELERY_MAN_URL.split("v=")[-1]


def _row_is_celery_echo(name: str, status: str, url: str, title: str,
                        paused_by_us: frozenset | set = frozenset()) -> bool:
    """One MPRIS metadata row: is this the Celery Man video making noise?

    Pure logic shared by the command echo guard below and the listener's
    wake gate (core/listener.py) -- both consume the same
    name/status/url/title row shape, so the video-matching rules live once.
    Playing counts; Paused counts only if WE paused it this wake window
    (see _celery_man_is_playing's docstring for why)."""
    is_celery = _CELERY_MAN_VIDEO_ID in url or "celery man" in title.lower()
    if not is_celery:
        return False
    s = status.strip()
    return s == "Playing" or (s == "Paused" and name.strip() in paused_by_us)


def _celery_man_is_playing(gui_env: dict, context=None) -> bool:
    """
    Return True if the Celery Man video is currently PLAYING in any MPRIS
    media player (browsers expose playing tabs over MPRIS; playerctl is
    already a hard dependency -- it drives the pause/resume commands).

    Matched by video ID in the reported URL, falling back to the title for
    browsers that don't expose xesam:url. Paused doesn't count: no audio
    means no echo, so a repeat command is genuinely the user -- EXCEPT a
    player auto-pause paused THIS wake window (context.auto_paused_players).
    That video is really playing, just suppressed by auto-pause-on-wake; the
    echo utterance can still slip in before the pause lands, so treat our own
    paused celery player as an echo or the guard fails open mid-echo (the
    exact hole the auto-pause feature would otherwise punch).

    Fails OPEN: if playerctl is missing, errors, or reports no players, the
    command fires normally -- a broken check must never block the command.
    """
    try:
        result = run_capture(
            ["playerctl", "-a", "metadata", "--format",
             "{{playerName}}\t{{status}}\t{{xesam:url}}\t{{xesam:title}}"],
            env=gui_env, timeout=3,
        )
    except Exception as e:
        print(f"[apps] celery_man playing-check failed ({e}); firing anyway")
        return False
    if result.returncode != 0:  # typically "No players found"
        return False
    paused_by_us = set(getattr(context, "auto_paused_players", None) or [])
    for line in result.stdout.splitlines():
        name, status, url, title = (line.split("\t", 3) + ["", "", "", ""])[:4]
        if _row_is_celery_echo(name, status, url, title, paused_by_us):
            return True
    return False


def celery_man(gui_env: dict, context=None):
    """Load up Celery Man.

    Echo guard: the clip's own audio says "computer, load up celery man
    please" -- without a guard it would launch copies of itself. If the video
    is already playing (per _celery_man_is_playing), the command that matched
    is the video echoing, so decline by returning False -- the dispatcher then
    suppresses the notification and log line too, so an echo is fully silent.
    Every other command stays fully usable while the video plays, and the
    guard ends the instant the video does. (The clip also says "computer"
    beyond its launch phrase; the WAKE side of that echo is gated in
    core/listener.py::_wake_is_celery_echo -- see the ARCHITECTURE invariant.)

    A genuine fire marks the wake window media_touched so auto-pause won't
    resume the pre-wake music on top of the video -- and it must be set HERE,
    not in open_url, because the celery clip autoplays with load latency: the
    wake window can close before the video starts, so the listener's
    "is anything new playing?" resume check would miss it and stack the old
    music over it. The echo-block path returns BEFORE this, so a blocked echo
    leaves media_touched False and auto-resumes the very video it guarded.
    """
    if _celery_man_is_playing(gui_env, context):
        print("[apps] celery_man: video already playing -- echo blocked")
        return False
    if context:
        context.update(media_touched=True)
    open_url(CELERY_MAN_URL, gui_env=gui_env, context=context)


def launch_app(app: str, gui_env: dict, context=None) -> None:
    """Launch an application by executable name."""
    run_bg([app], env=gui_env, detach=True)
    if context:
        context.update(last_app=app, last_command_name="launch_app")


def open_file(path: str, gui_env: dict, context=None) -> None:
    """
    Open an arbitrary file, folder, or executable via xdg-open.
    Works for .desktop files, scripts, binaries, documents, and
    directories (xdg-open hands a folder to the file manager) --
    anything xdg-open knows how to handle. The user picks the path via
    the settings UI, which offers a file chooser and a folder chooser
    writing to this same arg; we just run it.
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
            notify("Not found", f"Path does not exist:\n{path}",
                   gui_env=gui_env)
        return
    run_bg(["xdg-open", path], env=gui_env, detach=True)
    if context:
        context.update(last_command_name="open_file")
