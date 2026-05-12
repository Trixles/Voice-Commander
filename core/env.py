"""
core/env.py
===========
Shared GUI environment dict.

Built once at import time. Passed to every subprocess so Wayland/D-Bus work
correctly even when launched from a systemd user service with no active
desktop session.

Lives in its own module to avoid a circular import between voice_commander.py
(entry point) and core/settings.py (which needs GUI_ENV for kscreen-doctor
calls and similar).
"""

import os


def _find_wayland_display() -> str:
    uid = os.getuid()
    for i in range(4):
        if os.path.exists(f"/run/user/{uid}/wayland-{i}"):
            return f"wayland-{i}"
    return "wayland-0"


def _find_dbus_address() -> str:
    uid = os.getuid()
    path = f"/run/user/{uid}/bus"
    if os.path.exists(path):
        return f"unix:path={path}"
    return os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")


GUI_ENV: dict = {
    **os.environ,
    "DISPLAY": ":0",
    "WAYLAND_DISPLAY": _find_wayland_display(),
    "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}",
    "DBUS_SESSION_BUS_ADDRESS": _find_dbus_address(),
}
