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
import subprocess
from difflib import SequenceMatcher


CONFIG_PATH = os.path.expanduser("~/.config/voice-commander/commands.json")

# Cached output-name -> KWin screen index mapping.
# Built by refresh_monitor_map() at startup and on config reload.
_output_to_screen: dict[str, int] = {}


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
        result = subprocess.run(
            ["/usr/bin/kscreen-doctor", "-o"],
            capture_output=True, text=True, timeout=5,
            env=gui_env,
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


def get_connected_outputs() -> list[dict]:
    """
    Return the current cached monitor map as a list of dicts for the settings UI.
    Returns [{"name": "DP-1", "index": 0}, ...] sorted by index.
    """
    return [
        {"name": name, "index": idx}
        for name, idx in sorted(_output_to_screen.items(), key=lambda x: x[1])
    ]


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
        subprocess.run(
            [
                "kwriteconfig6",
                "--file", os.path.expanduser("~/.config/kwinrc"),
                "--group", "Script-vc-window-placer",
                "--key", "nextScreen",
                output_name,
            ],
            env=gui_env, check=True,
            capture_output=True, text=True,
        )

        subprocess.run(
            [
                "dbus-send", "--session", "--print-reply",
                "--dest=org.kde.KWin", "/Scripting",
                "org.kde.kwin.Scripting.unloadScript",
                f"string:{placer_id}",
            ],
            env=gui_env,
        )
        subprocess.run(
            [
                "dbus-send", "--session", "--print-reply",
                "--dest=org.kde.KWin", "/Scripting",
                "org.kde.kwin.Scripting.loadScript",
                f"string:{placer_path}",
                f"string:{placer_id}",
            ],
            env=gui_env,
        )
        subprocess.run(
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


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


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

    subprocess.Popen(
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


def maximize_window(gui_env: dict, context=None) -> None:
    """Maximize the active window via the 'Window Maximize' KWin global shortcut."""
    subprocess.Popen(
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
    subprocess.Popen(
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
    subprocess.Popen(
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
    subprocess.Popen(
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
