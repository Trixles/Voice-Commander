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
        run_bg(
            ["notify-send", "--app-name=Voice Commander", "--expire-time=3000",
             "File not found", f"Path does not exist:\n{path}"],
            env=gui_env,
        )
        return
    run_bg(["xdg-open", path], env=gui_env, detach=True)
    if context:
        context.update(last_command_name="open_file")
