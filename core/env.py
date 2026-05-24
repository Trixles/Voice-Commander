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


_KWINRC_PATH = os.path.expanduser("~/.config/kwinrc")


def blur_compositing_available(kwinrc_path: str | None = None) -> bool:
    """Whether the settings window should use a translucent background.

    Voice Commander's window is only meant to be see-through when the desktop
    will composite a blur behind it (the frosted-glass look). On a desktop with
    no blur, a translucent window just shows the raw desktop through the gaps --
    which reads as broken. So we go translucent ONLY on positive confirmation of
    a KWin blur effect, and stay opaque otherwise (the safe default for the
    majority who run no blur). Detection is by parsing kwinrc's `[Plugins]`
    group -- the same file VC already writes via kwriteconfig6 -- so it needs no
    extra dependency and no session env vars that might be missing in the
    user-service context.

    Returns True iff:
      - the stock `blurEnabled` effect is on -- and an ABSENT key counts as on,
        since Plasma ships the Blur effect enabled by default; OR
      - any other `[Plugins]` key whose name contains "blur" and ends in
        "enabled" is true -- this catches third-party forks that replace the
        stock effect (e.g. `better_blur_dxEnabled`, `forceblurEnabled`).

    Missing kwinrc (not a KWin session) or any read error -> False (opaque).
    """
    path = kwinrc_path if kwinrc_path is not None else _KWINRC_PATH
    if not os.path.exists(path):
        return False

    in_plugins = False
    stock_explicit: bool | None = None   # None = key absent (Plasma default = on)
    fork_on = False
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if line.startswith("[") and line.endswith("]"):
                    in_plugins = (line == "[Plugins]")
                    continue
                if not in_plugins or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                # Strip KDE immutability/locale markers like 'key[$i]'.
                base = key.split("[", 1)[0].strip().lower()
                val = val.strip().lower()
                if "blur" not in base or not base.endswith("enabled"):
                    continue
                if base == "blurenabled":
                    stock_explicit = (val == "true")
                elif val == "true":
                    fork_on = True
    except OSError:
        return False

    stock_on = True if stock_explicit is None else stock_explicit
    return stock_on or fork_on
