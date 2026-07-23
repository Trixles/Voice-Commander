"""
core/actions/windows.py
=======================
KWin window management actions.

move_window_to_monitor resolves a raw alias string (e.g. "monkey") against
the monitors block in commands.json to an OUTPUT NAME, then signals the
vc-window-placer to move the active window there. The placer calls KWin's
`sendClientToScreen` against the live, name-keyed screen list -- the SAME
name-based mechanism the "open X on [alias]" path uses.

Alias resolution (move path):
  1. Load the monitors block from commands.json.
  2. Fuzzy-match the raw alias against every alias in every output's list.
  3. Signal the placer with the winning output NAME (_signal_placer
     "moveActive"); the placer resolves the screen by name on reload.

No KWin screen index is involved in the move path. This is deliberate:
kscreen-doctor's output numbers are neither KWin screen indices nor stable
across hotplugs, so the old index-based "Window to Screen N" shortcut sent
windows to the wrong display. Routing by name survives both KWin's screen
numbering and monitor hotplugs.

The _output_to_screen index map (built by refresh_monitor_map() at startup
and on every config hot-reload) is retained only for the settings UI's
output list -- runtime move/open routing no longer depends on it.

close_window, minimize_window, maximize_window, and the left/right moves use
invokeShortcut via kglobalaccel -- a separate, simpler by-name shortcut path
that has no screen-index dependency.
"""

import json
import os
import re

import core.paths as paths
from core.edid import get_monitor_friendly_names
from core.matcher import _similarity
from core.notify import notify
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


# The placer reads its signals from this kwinrc group. Two keys it watches:
#   nextScreen -- queue outputs for ARRIVING windows ("open X on [alias]")
#   moveActive -- move the CURRENTLY ACTIVE window now ("move to [alias]")
_PLACER_GROUP = f"Script-{paths.PLACER_ID}"
_PLACER_KEYS = ("nextScreen", "moveActive")


def _signal_placer(active_key: str, value: str, gui_env: dict) -> None:
    """
    Write `active_key=value` into kwinrc's placer group, clear the OTHER placer
    key, then force KWin to re-execute the placer script via unloadScript /
    loadScript / start on the Scripting DBus interface.

    Clearing the complementary key keeps exactly ONE signal live per reload: a
    stale `nextScreen` entry must never hijack a later-opened window, and a
    stale `moveActive` must never re-fling the active window on the next
    placement reload.

    KWin's `org.kde.KWin.reconfigure` does NOT re-execute user scripts -- only
    the Scripting interface does. `loadScript` reads kwinrc fresh each call, so
    no reconfigure is needed. `unloadScript` first stops the windowAdded handler
    from stacking across calls.
    """
    placer_id = paths.PLACER_ID
    placer_path = os.path.join(paths.PLACER_DIR, "contents/code/main.js")
    kwinrc = os.path.expanduser("~/.config/kwinrc")

    try:
        for key in _PLACER_KEYS:
            result = run_capture(
                [
                    "kwriteconfig6", "--file", kwinrc,
                    "--group", _PLACER_GROUP,
                    "--key", key,
                    value if key == active_key else "",
                ],
                env=gui_env,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"kwriteconfig6 exited {result.returncode}: {result.stderr.strip()}"
                )

        for tail in (
            ("org.kde.kwin.Scripting.unloadScript", f"string:{placer_id}"),
            ("org.kde.kwin.Scripting.loadScript",
             f"string:{placer_path}", f"string:{placer_id}"),
            ("org.kde.kwin.Scripting.start",),
        ):
            result = run_capture(
                ["dbus-send", "--session", "--print-reply",
                 "--dest=org.kde.KWin", "/Scripting", *tail],
                env=gui_env,
            )
            # unloadScript legitimately returns "boolean false" when nothing
            # was loaded -- only the transport failing is an error here.
            if result.returncode != 0:
                raise RuntimeError(
                    f"dbus-send {tail[0].rsplit('.', 1)[-1]} exited "
                    f"{result.returncode}: {result.stderr.strip()}"
                )

        # Sentinel: a clean loadScript id + clean start() reply does NOT
        # mean the script ran. KWin silently discards scripts whose plugin
        # Id matches an installed-but-not-enabled package (see the
        # "[Plugins]" invariant, found in the s30 live test), and a script
        # that throws at execution dies just as quietly. isScriptLoaded is
        # the only truth: false here = placement is DEAD and the next
        # window will land on the focused screen. Fail LOUD.
        check = run_capture(
            ["dbus-send", "--session", "--print-reply",
             "--dest=org.kde.KWin", "/Scripting",
             "org.kde.kwin.Scripting.isScriptLoaded", f"string:{placer_id}"],
            env=gui_env,
        )
        if check.returncode != 0 or "boolean true" not in check.stdout:
            raise RuntimeError(
                f"placer script is NOT running after start() "
                f"(isScriptLoaded: rc={check.returncode}, "
                f"reply={check.stdout.strip()!r}). Windows will not be "
                f"placed. Is '{placer_id}Enabled' true in kwinrc [Plugins]?"
            )

        print(f"[windows] Placer signal: {active_key}={value!r}")
    except Exception as e:
        print(f"[windows] _signal_placer failed: {e}")
        # Deliberately NOT gated on the notifications toggle: a dead placer
        # silently mis-places every window (Tyler's most-hated bug class).
        # Same always-fire rule as the destructive-command confirm prompts.
        notify("Window placement failed",
               "The KWin placer script is not running -- check the journal.",
               gui_env=gui_env)


def write_next_screen(output_name: str, gui_env: dict) -> None:
    """Queue `output_name` for the next ARRIVING normal window ("open X on
    [alias]"). Thin wrapper over _signal_placer; pass "" to clear the queue."""
    _signal_placer("nextScreen", output_name, gui_env)


def clear_placer_queue(gui_env: dict) -> None:
    """Wipe both placer keys and re-execute the script with the empty queue.

    Called once at app startup: kwinrc queue entries persist across
    sessions, and KWin auto-loads enabled script plugins at login -- so a
    stale queue from a past session sits armed and yanks unrelated windows
    (the "misplacing AGAIN" login-zombie). Clearing at startup guarantees a
    queue can never outlive the app session that wrote it. Bonus: this
    exercises the full placement chain at boot, so the _signal_placer
    sentinel surfaces a dead placer immediately, not on the first command.
    """
    _signal_placer("nextScreen", "", gui_env)


def _resolve_output_alias(raw: str) -> str:
    """
    Fuzzy-match `raw` (what Vosk heard) against all aliases in the monitors
    block of commands.json and return the best-scoring OUTPUT NAME.

    Name-only by design: it deliberately does NOT consult the cached
    `_output_to_screen` index map. The move path hands this name to KWin, which
    resolves the live screen by name -- so a stale or reshuffled index can't
    send the window astray (that was the "move to [alias]" bug).

    Raises ValueError if there is no monitors block or nothing scores >= 0.5.
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

    print(f"[windows] '{raw}' -> '{best_output}' (score {best_score:.2f})")
    return best_output


def move_window_to_monitor(alias: str, gui_env: dict, context=None) -> None:
    """
    Move the active window to the monitor whose alias matches `alias`.

    Resolves the alias to an OUTPUT NAME, then signals the vc-window-placer to
    move the active window there. The placer calls `sendClientToScreen` against
    KWin's live, name-keyed screen list -- the same name-based mechanism the
    "on [alias]" path uses. No KWin screen index is involved, so it survives
    both KWin's screen numbering AND monitor hotplugs (the old index path did
    not -- kscreen-doctor output numbers are neither KWin indices nor stable).
    """
    try:
        output_name = _resolve_output_alias(alias)
    except ValueError as e:
        print(f"[windows] move_window_to_monitor failed: {e}")
        return

    _signal_placer("moveActive", output_name, gui_env)

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
