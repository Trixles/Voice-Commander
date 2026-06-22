"""
core/actions/windows.py
=======================
KWin window management actions.

move_window_to_monitor resolves a raw alias string (e.g. "monkey") against
the monitors block in commands.json, maps it to a KWin screen index, and
invokes the "Window to Screen N" shortcut via kglobalaccel.

Alias resolution:
  1. Load the monitors block from commands.json.
  2. Fuzzy-match the raw alias against every alias in every output's list.
  3. Map the winning output name to its KWin screen index via the cached
     _output_to_screen map (built from kscreen-doctor at startup).
  4. Fire invokeShortcut.

The output-to-screen mapping is built once at startup by refresh_monitor_map()
and refreshed on every config hot-reload. This avoids calling kscreen-doctor
on every monitor-move command.

close_window uses invokeShortcut "Window Close" via kglobalaccel -- same
pattern as the screen-move shortcuts.
"""

import json
import os
import re

from core.edid import get_monitor_friendly_names
from core.matcher import _similarity
from core.paths import CONFIG_PATH
from core.run import run_bg, run_capture


# Cached output-name -> KWin screen index mapping.
# Built by refresh_monitor_map() at startup and on config reload.
_output_to_screen: dict[str, int] = {}

# Cached output-name -> friendly label (EDID Display Product Name).
# Built alongside _output_to_screen so both refresh in lockstep. Used only
# by the settings UI; runtime alias resolution uses output names directly.
_monitor_details: dict[str, str] = {}


def refresh_monitor_map(gui_env: dict) -> None:
    """
    Query kscreen-doctor for connected outputs and build the
    output-name -> KWin-screen-index mapping.

    kscreen-doctor uses 1-based output numbering; KWin screen indices are
    0-based (subtract 1). ANSI color codes are stripped since kscreen-doctor
    emits them even when piped.

    Called once at startup and again on every config hot-reload.
    """
    global _output_to_screen

    try:
        result = run_capture(
            ["/usr/bin/kscreen-doctor", "-o"],
            env=gui_env, timeout=5,
        )
    except Exception as e:
        print(f"[windows] kscreen-doctor failed: {e}")
        return

    _ansi = re.compile(r"\x1b\[[0-9;]*m")
    new_map: dict[str, int] = {}
    current: dict | None = None

    for raw_line in result.stdout.splitlines():
        line = _ansi.sub("", raw_line)
        m = re.match(r"^\s*Output:\s+(\d+)\s+(\S+)", line)
        if m:
            if current and current.get("connected"):
                new_map[current["name"]] = current["number"] - 1
            current = {"number": int(m.group(1)), "name": m.group(2), "connected": False}
        elif current and re.match(r"^\s+connected\s*$", line):
            current["connected"] = True

    if current and current.get("connected"):
        new_map[current["name"]] = current["number"] - 1

    _output_to_screen = new_map
    print(f"[windows] Monitor map: {_output_to_screen}")

    # Friendly names from EDID. Isolated try/except: if edid-decode is missing
    # or /sys/class/drm is unreadable, the alias map stays valid and we just
    # don't show friendly names. UI falls back to port names alone.
    global _monitor_details
    try:
        _monitor_details = get_monitor_friendly_names()
        print(f"[windows] Monitor details: {_monitor_details}")
    except Exception as e:
        print(f"[windows] get_monitor_friendly_names failed: {e}")
        _monitor_details = {}


def get_connected_outputs() -> list[dict]:
    """
    Return the current cached monitor map as a list of dicts for the settings UI.
    Returns [{"name": "DP-1", "index": 0}, ...] sorted by index.
    """
    return [
        {"name": name, "index": idx}
        for name, idx in sorted(_output_to_screen.items(), key=lambda x: x[1])
    ]


def get_monitor_details() -> dict[str, str]:
    """
    Return {output_name: friendly_label} from EDID, populated by the most
    recent refresh_monitor_map() call. Used by the Displays settings tab
    to render a human-readable label alongside the port name.

    Returns an empty dict if EDID parsing failed or refresh_monitor_map()
    hasn't run yet. Callers should fall back to port names alone.
    """
    return dict(_monitor_details)


def write_next_screen(output_name: str, gui_env: dict) -> None:
    """
    Signal the vc-window-placer KWin script to move the next new normal window
    to `output_name`. Writes via kwriteconfig6 into kwinrc, then forces KWin
    to re-execute the placer script via unloadScript / loadScript / start on
    the Scripting DBus interface.

    KWin's `org.kde.KWin.reconfigure` does NOT re-execute user scripts -- only
    the Scripting interface does. `loadScript` reads kwinrc fresh on each
    invocation, so no reconfigure is needed between the kwriteconfig6 write
    and the reload. `unloadScript` first prevents the script's windowAdded
    handler from stacking across calls.

    Pass empty string to clear the signal after dispatch.
    """
    placer_id = "vc-window-placer"
    placer_path = os.path.expanduser(
        "~/.local/share/kwin/scripts/vc-window-placer/contents/code/main.js"
    )

    try:
        result = run_capture(
            [
                "kwriteconfig6",
                "--file", os.path.expanduser("~/.config/kwinrc"),
                "--group", "Script-vc-window-placer",
                "--key", "nextScreen",
                output_name,
            ],
            env=gui_env,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"kwriteconfig6 exited {result.returncode}: {result.stderr.strip()}"
            )

        run_capture(
            [
                "dbus-send", "--session", "--print-reply",
                "--dest=org.kde.KWin", "/Scripting",
                "org.kde.kwin.Scripting.unloadScript",
                f"string:{placer_id}",
            ],
            env=gui_env,
        )
        run_capture(
            [
                "dbus-send", "--session", "--print-reply",
                "--dest=org.kde.KWin", "/Scripting",
                "org.kde.kwin.Scripting.loadScript",
                f"string:{placer_path}",
                f"string:{placer_id}",
            ],
            env=gui_env,
        )
        run_capture(
            [
                "dbus-send", "--session", "--print-reply",
                "--dest=org.kde.KWin", "/Scripting",
                "org.kde.kwin.Scripting.start",
            ],
            env=gui_env,
        )

        if output_name:
            print(f"[windows] Set next-screen signal: {output_name}")
        else:
            print("[windows] Cleared next-screen signal")
    except Exception as e:
        print(f"[windows] write_next_screen failed: {e}")


def _resolve_monitor(raw: str) -> tuple[str, int]:
    """
    Fuzzy-match `raw` (what Vosk heard) against all aliases in the monitors
    block of commands.json. Returns (output_name, kwin_screen_index).

    Raises ValueError if no monitors block exists, no alias scores >= 0.5,
    or the output has no entry in the cached monitor map.
    """
    with open(CONFIG_PATH, "r") as f:
        config = json.load(f)

    monitors: dict[str, list[str]] = config.get("monitors", {})
    if not monitors:
        raise ValueError("No monitors block found in commands.json")

    best_score = 0.0
    best_output = None

    for output_name, aliases in monitors.items():
        for alias in aliases:
            score = _similarity(raw, alias)
            if score > best_score:
                best_score = score
                best_output = output_name

    if best_output is None or best_score < 0.5:
        raise ValueError(
            f"No monitor alias matched '{raw}' (best score {best_score:.2f})"
        )

    screen_index = _output_to_screen.get(best_output)
    if screen_index is None:
        raise ValueError(
            f"Output '{best_output}' not found in monitor map. "
            f"Known outputs: {list(_output_to_screen.keys())}"
        )

    print(f"[windows] '{raw}' -> '{best_output}' (score {best_score:.2f}) -> Screen {screen_index}")
    return best_output, screen_index


def move_window_to_monitor(alias: str, gui_env: dict, context=None) -> None:
    """
    Move the active window to the monitor matching `alias` (raw alias from Vosk).
    Resolves alias -> output name -> KWin screen index, then invokes the
    'Window to Screen N' shortcut via kglobalaccel.
    """
    try:
        output_name, screen_index = _resolve_monitor(alias)
    except ValueError as e:
        print(f"[windows] move_window_to_monitor failed: {e}")
        return

    shortcut_name = f"Window to Screen {screen_index}"

    run_bg(
        [
            "dbus-send", "--session", "--print-reply",
            "--dest=org.kde.kglobalaccel",
            "/component/kwin",
            "org.kde.kglobalaccel.Component.invokeShortcut",
            f"string:{shortcut_name}",
        ],
        env=gui_env,
    )

    if context:
        context.update(last_command_name="move_window_to_monitor", last_monitor=output_name)


def minimize_window(gui_env: dict, context=None) -> None:
    """Minimize the active window via the 'Window Minimize' KWin global shortcut.

    Same kglobalaccel-by-name pattern as maximize/close: we fire the named KWin
    action rather than a key combo, so it works regardless of which (if any)
    keyboard shortcut the user has bound to it.
    """
    run_bg(
        [
            "dbus-send", "--session", "--print-reply",
            "--dest=org.kde.kglobalaccel",
            "/component/kwin",
            "org.kde.kglobalaccel.Component.invokeShortcut",
            "string:Window Minimize",
        ],
        env=gui_env,
    )
    if context:
        context.update(last_command_name="minimize_window")


def maximize_window(gui_env: dict, context=None) -> None:
    """Maximize the active window via the 'Window Maximize' KWin global shortcut."""
    run_bg(
        [
            "dbus-send", "--session", "--print-reply",
            "--dest=org.kde.kglobalaccel",
            "/component/kwin",
            "org.kde.kglobalaccel.Component.invokeShortcut",
            "string:Window Maximize",
        ],
        env=gui_env,
    )
    if context:
        context.update(last_command_name="maximize_window")


def move_window_left(gui_env: dict, context=None) -> None:
    """
    Move the active window one monitor to the left via the
    'Window One Screen to the Left' KWin global shortcut.
    """
    run_bg(
        [
            "dbus-send", "--session", "--print-reply",
            "--dest=org.kde.kglobalaccel",
            "/component/kwin",
            "org.kde.kglobalaccel.Component.invokeShortcut",
            "string:Window One Screen to the Left",
        ],
        env=gui_env,
    )
    if context:
        context.update(last_command_name="move_window_left")


def move_window_right(gui_env: dict, context=None) -> None:
    """
    Move the active window one monitor to the right via the
    'Window One Screen to the Right' KWin global shortcut.
    """
    run_bg(
        [
            "dbus-send", "--session", "--print-reply",
            "--dest=org.kde.kglobalaccel",
            "/component/kwin",
            "org.kde.kglobalaccel.Component.invokeShortcut",
            "string:Window One Screen to the Right",
        ],
        env=gui_env,
    )
    if context:
        context.update(last_command_name="move_window_right")


def close_window(gui_env: dict, context=None) -> None:
    """
    Close the active window by invoking the 'Window Close' KWin global shortcut
    via kglobalaccel.
    """
    run_bg(
        [
            "dbus-send", "--session", "--print-reply",
            "--dest=org.kde.kglobalaccel",
            "/component/kwin",
            "org.kde.kglobalaccel.Component.invokeShortcut",
            "string:Window Close",
        ],
        env=gui_env,
    )

    if context:
        context.update(last_command_name="close_window")
