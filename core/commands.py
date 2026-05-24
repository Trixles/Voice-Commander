"""
core/commands.py
================
Command registry, matching, argument extraction, and dispatch.

Two distinct match paths -- do NOT conflate them:

  Non-slot phrases  ("open reddit", "volume up")
      -> SequenceMatcher fuzzy match against n-gram windows of heard text.

  Slot-bearing phrases  ("open {app}", "set volume to {level}")
      -> Regex extraction of the slot region first, then fuzzy match of the
         extracted value against known_values (if fuzzy: true), then dispatch.
         If regex fails, this phrase is SKIPPED -- no fallthrough to fuzzy.

Command chaining:
  If the heard text contains " and ", try splitting into segments and
  matching each independently. Accept the split ONLY if every segment
  matches above threshold. If any fails, fall back to matching the
  unsplit text. Confirm-required commands cannot be chained.

Config is loaded from ~/.config/voice-commander/commands.json.
Hot-reload: mtime is checked every CHECK_MTIME_EVERY calls to try_match().
"""

import inspect
import json
import os
import re
import sys
import time
from datetime import datetime
from typing import Any

from core.actions import apps, system, windows
from core.aliases import PINNED_SLOT, default_aliases as _default_aliases
from core.desktop import extract_exec_token
from core.log_buffer import LOG_BUFFER
from core.matcher import (
    TAIL_THRESHOLD,
    _extract_slots,
    _has_slots,
    _match_non_slot,
    _resolve_slot,
    _similarity,
)
from core.notify import notify as _notify
from core.overrides import DEFAULT_OVERRIDES
from core.paths import CONFIG_PATH
from core.run import run_capture


# -- Default mic phrase lists ------------------------------------------------
# As of 0.8.0 the open/close mic phrases live in the command list as the
# `open_mic` / `close_mic` system commands (see _default_commands), edited on
# the Commands tab like any other system command. These constants are the
# default phrases those commands ship with, and the in-process fallback used by
# get_open_mic_phrases() / get_close_mic_phrases() when neither a command nor a
# legacy top-level key is present.
#
# "open mike" / "close mike" are intentionally absent: the shipped `mike -> mic`
# default override (core/overrides.py) rewrites them before matching.
DEFAULT_OPEN_MIC_PHRASES  = ["open mic", "open microphone"]
DEFAULT_CLOSE_MIC_PHRASES = ["close mic", "close microphone"]

# Mic toggles are listener state transitions, intercepted by substring match
# (_is_open_mic_command / _is_close_mic_command) BEFORE the fuzzy matcher runs.
# They are NOT dispatchable ACTION_REGISTRY actions. _score_segment skips any
# command with one of these action keys so a mic phrase can never be scored
# into a no-op dispatch or shadow a real fuzzy match.
_MIC_ACTIONS = {"open_mic", "close_mic"}


# -- Action registry ----------------------------------------------------------

ACTION_REGISTRY: dict[str, Any] = {
    "open_url":               apps.open_url,
    "launch_app":             apps.launch_app,
    "open_file":              apps.open_file,
    "volume_up":              system.volume_up,
    "volume_down":            system.volume_down,
    "set_volume":             system.set_volume,
    "mute":                   system.mute,
    "unmute":                 system.unmute,
    "media_pause":            system.media_pause,
    "media_resume":           system.media_resume,
    "shutdown":               system.shutdown,
    "restart":                system.restart,
    "logout":                 system.logout,
    "run_command":            system.run_command,
    "open_settings":          system.open_settings,
    "move_window_to_monitor": windows.move_window_to_monitor,
    "maximize_window":        windows.maximize_window,
    "move_window_left":       windows.move_window_left,
    "move_window_right":      windows.move_window_right,
    "close_window":           windows.close_window,
}

CHECK_MTIME_EVERY = 100
DEFAULT_THRESHOLD = 0.75
DEFAULT_COOLDOWN = 3

_commands: list[dict] = []
_config: dict = {}
_last_mtime: float = 0.0
_match_call_count: int = 0
_last_fired: dict[str, float] = {}


# -- Config loading -----------------------------------------------------------

_USER_APPS_DIR   = os.path.expanduser("~/.local/share/applications")
_SYSTEM_APPS_DIR = "/usr/share/applications"


def _parse_exec_from_desktop(desktop_path: str) -> str | None:
    """Read a .desktop file and return its executable token, or None.
    Thin wrapper around extract_exec_token() that handles the
    ConfigParser boilerplate."""
    try:
        import configparser
        cp = configparser.ConfigParser(interpolation=None)
        cp.read(desktop_path, encoding="utf-8")
        if "Desktop Entry" not in cp:
            return None
        return extract_exec_token(cp["Desktop Entry"].get("Exec", ""))
    except Exception:
        return None


def _detect_default_browser() -> str | None:
    """Detect the system default browser executable.
    Returns the executable string (e.g. 'waterfox', '/opt/waterfox/waterfox')
    or None if detection fails."""
    try:
        result = run_capture(
            ["xdg-settings", "get", "default-web-browser"],
            timeout=5,
        )
        desktop_name = result.stdout.strip()
        if not desktop_name:
            return None
        # Check user dir first (XDG priority), then system dir.
        for base in (_USER_APPS_DIR, _SYSTEM_APPS_DIR):
            path = os.path.join(base, desktop_name)
            if os.path.isfile(path):
                exe = _parse_exec_from_desktop(path)
                if exe:
                    print(f"[commands] Default browser detected: {exe} (from {path})", file=sys.stderr)
                    return exe
    except Exception as e:
        print(f"[commands] Default browser detection failed: {e}", file=sys.stderr)
    return None


_README_DEFAULT_PATH = os.path.expanduser("~/.local/share/voice-commander/README.md")


def _default_commands() -> list[dict]:
    """Generate the initial set of commands for a fresh install.

    Single source of truth for the default command set. Used by:
      - install.sh (via 'python -m core.commands --emit-defaults')
      - settings.py's Restore Defaults button

    Browser auto-detection: 'open_browser' and 'open_reddit' pre-populate the
    user's system default browser if it can be detected from xdg-settings.
    User can override in settings.
    """
    browser_exe = _detect_default_browser()

    commands: list[dict] = []

    # -- User-editable example commands ---------------------------------------
    browser_cmd = {
        "name": "open_browser",
        "display_name": "Open Browser",
        "phrases": ["open browser", "launch browser"],
        "action": "launch_app",
        "args": {},
    }
    if browser_exe:
        browser_cmd["args"]["app"] = browser_exe
    commands.append(browser_cmd)

    reddit_cmd = {
        "name": "open_reddit",
        "display_name": "Open Reddit",
        "phrases": ["open reddit", "open read it"],
        "action": "open_url",
        "args": {"url": "https://old.reddit.com"},
    }
    if browser_exe:
        reddit_cmd["args"]["browser"] = browser_exe
    commands.append(reddit_cmd)

    commands.append({
        "name": "open_readme",
        "display_name": "Open ReadMe",
        "phrases": ["open readme", "open read me"],
        "action": "open_file",
        "args": {"path": _README_DEFAULT_PATH},
    })

    # -- System actions -------------------------------------------------------
    commands.append({
        "name": "volume_up",
        "display_name": "Volume up",
        "phrases": ["volume up", "turn it up"],
        "action": "volume_up",
        "args": {},
    })
    commands.append({
        "name": "volume_down",
        "display_name": "Volume down",
        "phrases": ["volume down", "turn it down"],
        "action": "volume_down",
        "args": {},
    })
    commands.append({
        "name": "mute",
        "display_name": "Mute",
        "phrases": ["mute", "mute audio", "mute volume"],
        "action": "mute",
        "args": {},
    })
    commands.append({
        "name": "unmute",
        "display_name": "Unmute",
        "phrases": ["unmute", "unmute audio", "unmute volume"],
        "action": "unmute",
        "args": {},
    })
    commands.append({
        "name": "media_pause",
        "display_name": "Pause media",
        "phrases": ["pause", "pause media"],
        "action": "media_pause",
        "args": {},
    })
    commands.append({
        "name": "media_resume",
        "display_name": "Resume media",
        "phrases": ["resume", "play", "resume media"],
        "action": "media_resume",
        "args": {},
    })
    commands.append({
        "name": "move_window_left",
        "display_name": "Move window left",
        "phrases": ["move left", "move window left"],
        "action": "move_window_left",
        "args": {},
    })
    commands.append({
        "name": "move_window_right",
        "display_name": "Move window right",
        "phrases": ["move right", "move window right"],
        "action": "move_window_right",
        "args": {},
    })
    commands.append({
        "name": "maximize_window",
        "display_name": "Maximize window",
        "phrases": ["maximize window", "maximize this window"],
        "action": "maximize_window",
        "args": {},
    })
    commands.append({
        "name": "close_window",
        "display_name": "Close window",
        "phrases": ["close window", "close this window"],
        "action": "close_window",
        "args": {},
    })
    commands.append({
        # Open Mic mode (listen without a wake word). The phrase is intercepted
        # by the listener as a state transition, not dispatched as an action
        # (see _MIC_ACTIONS) -- but it lives here as a system command so its
        # phrases are editable on the Commands tab like every other one.
        "name": "open_mic",
        "display_name": "Open mic",
        "phrases": list(DEFAULT_OPEN_MIC_PHRASES),
        "action": "open_mic",
        "args": {},
    })
    commands.append({
        "name": "close_mic",
        "display_name": "Close mic",
        "phrases": list(DEFAULT_CLOSE_MIC_PHRASES),
        "action": "close_mic",
        "args": {},
    })
    commands.append({
        # Single phrase by design: "open settings" is deliberately NOT a
        # default so it stays free for the user's OS/system-settings command.
        "name": "open_settings",
        "display_name": "Open VC settings",
        "phrases": ["open voice commander settings"],
        "action": "open_settings",
        "args": {},
    })
    commands.append({
        "name": "shutdown",
        "display_name": "Shutdown",
        "phrases": ["shut down", "shutdown", "power off"],
        "action": "shutdown",
        "args": {},
        "confirm": True,
    })
    commands.append({
        "name": "restart",
        "display_name": "Restart",
        "phrases": ["restart", "reboot"],
        "action": "restart",
        "args": {},
        "confirm": True,
    })
    commands.append({
        "name": "logout",
        "display_name": "Logout",
        "phrases": ["log out", "logout"],
        "action": "logout",
        "args": {},
        "confirm": True,
    })

    return commands


def normalize_config(data: dict) -> dict:
    """Apply in-place forward-migrations to a freshly loaded config dict.

    Shared by both load paths (this module's load_config and the settings
    dialog's _load_config) so runtime and UI agree on the migrated shape.
    Idempotent.

    0.8.0 -- mic phrases moved out of the top-level `open_mic_phrases` /
    `close_mic_phrases` keys and into the command list as the `open_mic` /
    `close_mic` system commands. For pre-0.8.0 configs: fold each legacy key
    into a matching command (creating it from the legacy phrases if absent,
    else just dropping the now-stale key -- the command is authoritative).
    """
    commands = data.get("commands")
    if not isinstance(commands, list):
        return data
    present = {c.get("action") for c in commands if isinstance(c, dict)}
    for action, legacy_key, display, defaults in (
        ("open_mic", "open_mic_phrases", "Open mic", DEFAULT_OPEN_MIC_PHRASES),
        ("close_mic", "close_mic_phrases", "Close mic", DEFAULT_CLOSE_MIC_PHRASES),
    ):
        legacy = data.pop(legacy_key, None)
        if action in present:
            continue  # already a command; the stale legacy key is now dropped
        phrases = legacy if (isinstance(legacy, list) and legacy) else list(defaults)
        commands.append({
            "name": action,
            "display_name": display,
            "phrases": [str(p) for p in phrases],
            "action": action,
            "args": {},
        })
    return data


def load_config() -> None:
    global _commands, _config, _last_mtime
    with open(CONFIG_PATH, "r") as f:
        data = json.load(f)

    # First launch: populate default commands and write them to disk.
    if not data.get("commands"):
        print("[commands] Empty command list -- generating defaults.")
        data["commands"] = _default_commands()
        # Seed default wake words on fresh install if not already set.
        if "wake_words" not in data and "wake_word" not in data:
            data["wake_words"] = ["computer", "hey dude"]
        real_path = os.path.realpath(CONFIG_PATH)
        with open(real_path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"[commands] Defaults written to {real_path}")

    # Forward-migrations (mic phrases -> system commands, etc.). In-memory only;
    # the cleaned shape is persisted the next time the user saves from Settings.
    normalize_config(data)

    _config = data
    _commands = data.get("commands", [])

    _last_mtime = os.path.getmtime(CONFIG_PATH)
    print(f"[commands] Loaded {len(_commands)} command(s) from {CONFIG_PATH}")


def seed_monitor_defaults() -> None:
    """
    If the monitors block is empty but kscreen-doctor knows about connected
    outputs, populate it with default aliases and persist to disk.

    Self-healing: runs every startup. If a user clears their monitors block,
    it gets repopulated on next launch. If aliases are already set, no-op.

    Must be called AFTER load_config() AND windows.refresh_monitor_map().
    """
    global _config

    if _config.get("monitors"):
        return  # Already populated; respect the user's choices.

    outputs = windows.get_connected_outputs()
    if not outputs:
        return  # No monitors detected; nothing to seed.

    monitors: dict[str, list[str]] = {}
    for i, m in enumerate(outputs):
        monitors[m["name"]] = _default_aliases(i + 1)

    _config["monitors"] = monitors

    real_path = os.path.realpath(CONFIG_PATH)
    with open(real_path, "w") as f:
        json.dump(_config, f, indent=2)
    print(f"[commands] Seeded default monitor aliases: {monitors}")


def _check_reload(gui_env: dict = None) -> None:
    global _match_call_count
    _match_call_count += 1
    if _match_call_count < CHECK_MTIME_EVERY:
        return
    _match_call_count = 0
    try:
        current_mtime = os.path.getmtime(CONFIG_PATH)
        if current_mtime != _last_mtime:
            print("[commands] Config changed, reloading...")
            load_config()
            if gui_env:
                windows.refresh_monitor_map(gui_env)
    except OSError:
        pass


def get_wake_words() -> list[str]:
    if "wake_words" in _config:
        words = _config["wake_words"]
        if isinstance(words, list) and words:
            return [w.lower().strip() for w in words]
    word = _config.get("wake_word", "computer")
    return [word.lower().strip()]


def get_command_window() -> int:
    return _config.get("command_window", 5)


def _mic_phrases(action: str, legacy_key: str, defaults: list[str]) -> set[str]:
    """Resolve the active phrase set for a mic toggle, in priority order:

      1. The `open_mic`/`close_mic` system command in the list -- but ONLY if
         it's enabled. A disabled mic command returns an empty set, so the
         voice toggle stops working (tray left-click still toggles via the
         separate command-queue path).
      2. The legacy top-level `open_mic_phrases`/`close_mic_phrases` key, for
         configs written before 0.8.0 that haven't been migrated yet
         (see _normalize_config).
      3. The shipped defaults.
    """
    for cmd in _commands:
        if cmd.get("action") == action:
            if not cmd.get("enabled", True):
                return set()
            phrases = cmd.get("phrases") or []
            return {p.lower().strip() for p in phrases}
    legacy = _config.get(legacy_key)
    if isinstance(legacy, list) and legacy:
        return {p.lower().strip() for p in legacy}
    return {p.lower().strip() for p in defaults}


def get_open_mic_phrases() -> set[str]:
    return _mic_phrases("open_mic", "open_mic_phrases", DEFAULT_OPEN_MIC_PHRASES)


def get_close_mic_phrases() -> set[str]:
    return _mic_phrases("close_mic", "close_mic_phrases", DEFAULT_CLOSE_MIC_PHRASES)


def get_overrides() -> list[dict]:
    """Return the full ordered list of overrides to apply.

    USER rules come first, then the enabled built-in defaults. Overrides apply
    sequentially (each rewrite feeds the next), so the first rule to touch a
    span wins it -- user-first means a user rule beats a colliding default of
    the same pattern. This matches the Overrides tab's top-to-bottom layout
    (User section on top = runs first). A user with no overrides sees
    defaults-only behaviour, unchanged.

    A default can also be toggled off in Settings. Disabled defaults are
    stored by pattern under the "disabled_default_overrides" config key and
    filtered out here. The defaults themselves are never persisted (they live
    in DEFAULT_OVERRIDES); only the set of disabled patterns is stored, so the
    shipped default list can grow across versions without stale copies on
    disk."""
    user = _config.get("overrides", [])
    if not isinstance(user, list):
        user = []
    disabled = _config.get("disabled_default_overrides", [])
    if not isinstance(disabled, list):
        disabled = []
    disabled_set = set(disabled)
    defaults = [d for d in DEFAULT_OVERRIDES if d["pattern"] not in disabled_set]
    return user + defaults


_DEFAULT_VOSK_MODEL_DIR  = os.path.expanduser("~/.local/share/voice-commander/vosk-model/")
_DEFAULT_VOSK_MODEL_NAME = "vosk-model-small-en-us-0.15"


def get_vosk_model_path() -> str:
    """
    Return the absolute path to the Vosk model directory.

    Resolution order:
      1. 'vosk_model' key in commands.json -- can be an absolute path or a bare
         directory name (resolved relative to _DEFAULT_VOSK_MODEL_DIR).
      2. Hardcoded default: _DEFAULT_VOSK_MODEL_DIR / _DEFAULT_VOSK_MODEL_NAME.

    Raises ValueError if the resolved path does not look like a Vosk model
    (i.e. does not contain am/final.mdl).
    """
    raw = _config.get("vosk_model", "").strip()
    if raw:
        path = raw if os.path.isabs(raw) else os.path.join(_DEFAULT_VOSK_MODEL_DIR, raw)
    else:
        path = os.path.join(_DEFAULT_VOSK_MODEL_DIR, _DEFAULT_VOSK_MODEL_NAME)

    path = os.path.normpath(path)

    if not os.path.isdir(path):
        raise ValueError(f"Vosk model path does not exist: {path}")
    if not os.path.isfile(os.path.join(path, "am", "final.mdl")):
        raise ValueError(f"Not a valid Vosk model directory (missing am/final.mdl): {path}")

    return path


# -- "on [alias]" monitor placement detection --------------------------------

def _detect_on_monitor(heard: str) -> str | None:
    marker = " on "
    idx = heard.rfind(marker)
    if idx == -1:
        return None
    candidate = heard[idx + len(marker):].strip()
    if not candidate:
        return None
    monitors: dict[str, list[str]] = _config.get("monitors", {})
    if not monitors:
        return None
    best_score = 0.0
    best_output = None
    for output_name, aliases in monitors.items():
        for alias in aliases:
            score = _similarity(candidate, alias)
            if score > best_score:
                best_score = score
                best_output = output_name
    if best_output is not None and best_score >= DEFAULT_THRESHOLD:
        print(f"[commands] 'on' target: '{candidate}' -> '{best_output}' (score {best_score:.2f})")
        return best_output
    print(f"[commands] 'on' target: '{candidate}' -- no alias match above threshold (best {best_score:.2f})")
    return None


# -- Window class hint for queue tagging --------------------------------------

def _wm_class_hint(cmd: dict) -> str:
    """
    Extract a window-class hint from a command for tagging placer queue entries.

    Returns a lowercased executable basename (e.g. "waterfox-g", "dolphin") that
    the placer script substring-matches against `window.resourceClass` on
    `windowAdded`. Bidirectional substring matching handles cases like
    executable "waterfox-g" vs resourceClass "waterfox", or executable
    "dolphin" vs resourceClass "org.kde.dolphin".

    Returns "" for actions that don't spawn a window (the placer treats an
    empty tag as "matches anything", preserving head-of-queue behavior for
    untagged entries).
    """
    action = cmd.get("action", "")
    args = cmd.get("args", {})
    if action == "launch_app":
        raw = args.get("app", "")
    elif action == "open_url":
        # If no explicit browser is set, leave the tag empty -- the placer's
        # empty-tag-matches-anything rule handles it.
        raw = args.get("browser", "")
    else:
        return ""
    if not raw:
        return ""
    # "/opt/waterfox/waterfox-g --new-tab" -> "waterfox-g"
    return os.path.basename(raw.split()[0]).lower()


# -- Single-segment scoring ---------------------------------------------------

def _score_segment(
    heard: str,
) -> tuple[float, dict, dict, str | None] | tuple[str, dict] | None:
    """
    Score a single heard segment against all commands.

    Three possible return shapes:
      - (score, cmd, resolved_args, target_output): the best match, enabled
        and dispatchable.
      - ("disabled", cmd): the best match scored above threshold but its
        ``enabled`` flag is False. The caller is expected to notify the user
        and NOT dispatch. The sentinel is a 2-tuple whose first element is the
        literal string "disabled"; a normal match's first element is a float
        score, so callers can tell them apart with ``result[0] == "disabled"``.
      - None: nothing cleared the threshold.

    Scoring deliberately IGNORES ``enabled``: the best match is chosen purely
    on score, and only then is the flag checked. Doing it the other way --
    filtering disabled commands out of candidacy before scoring -- would let an
    enabled but lower-scoring command shadow a disabled higher-scoring one. The
    user would then get feedback for the wrong command, or none at all. The
    "disabled" notification must fire when the BEST match is the disabled one.

    Handles "on [alias]" detection and stripping per-segment.
    """
    target_output = _detect_on_monitor(heard)
    idx = heard.rfind(" on ")
    if idx != -1:
        heard = heard[:idx].strip()
        print(f"[commands] Stripped 'on' suffix; scoring against: '{heard}'")

    best_score: float = 0.0
    best_cmd: dict | None = None
    best_args: dict = {}

    for cmd in _commands:
        if cmd.get("action") in _MIC_ACTIONS:
            continue  # listener-handled state transition; see _MIC_ACTIONS
        threshold = cmd.get("threshold", DEFAULT_THRESHOLD)
        for phrase in cmd["phrases"]:
            if _has_slots(phrase):
                raw_slots = _extract_slots(heard, phrase)
                if raw_slots is None:
                    continue
                slot_defs = cmd.get("slots", {})
                resolved = {}
                slot_failed = False
                for slot_name, raw_value in raw_slots.items():
                    slot_def = slot_defs.get(slot_name, {})
                    try:
                        resolved[slot_name] = _resolve_slot(slot_name, raw_value, slot_def)
                    except ValueError as e:
                        print(f"[commands] Slot error: {e}")
                        slot_failed = True
                        break
                if slot_failed:
                    continue
                score = 1.0
                if score > best_score:
                    best_score = score
                    best_cmd   = cmd
                    best_args  = resolved
            else:
                score = _match_non_slot(heard, phrase)
                if score >= threshold and score > best_score:
                    best_score = score
                    best_cmd   = cmd
                    best_args  = {}

    if best_cmd is None:
        return None

    # Best match found on score alone (above). Now -- and only now -- consult
    # the enabled flag. Absent key means enabled (back-compat with pre-0.6.0
    # configs that never had the field).
    if not best_cmd.get("enabled", True):
        return ("disabled", best_cmd)

    return (best_score, best_cmd, best_args, target_output)


# -- Chain segment matching ---------------------------------------------------

def _match_chain_segments(
    segments: list[str],
) -> tuple[list, bool, bool]:
    """
    Score each segment of a candidate chain and gather matches.

    This is the false-positive defense for the deliberately-aggressive split
    pattern in try_match() (" and | an | in "). The pattern WILL produce
    false splits ("open mind and body" -> ["open mind", "body"]); a false
    split won't produce a match for every segment, so chain_ok comes back
    False and the caller falls through to single-match.

    Each entry in `matches` is one of two shapes:
      - (cmd, args, target): a normal, dispatchable segment.
      - ("disabled", cmd): a segment whose best match is disabled in Settings.
        It counts as "matched" for chain validity (so it does NOT flip
        chain_ok to False), but the caller notifies instead of dispatching.

    Returns (matches, chain_ok, chain_rejected):
      - chain_ok=True, chain_rejected=False: every segment matched (enabled or
        disabled). Chain is dispatchable; disabled entries notify-and-skip.
      - chain_ok=False, chain_rejected=False: at least one segment didn't
        score. Treat as a misparse and fall through to single-match.
      - chain_ok=False, chain_rejected=True: segments scored but a rule
        refused (confirm-required, cooldown). Caller should return False
        immediately -- the user clearly meant a chain; firing half of it
        via the single-match rescue would be worse than firing nothing.
        Disabled segments never trigger this: confirm/cooldown rules belong to
        enabled commands and take precedence (a disabled segment sitting next
        to a confirm-required one still aborts the chain).
    """
    matches: list = []
    for seg in segments:
        result = _score_segment(seg)
        if result is None:
            return matches, False, False
        if result[0] == "disabled":
            # Matched but disabled: ride along as a sentinel, do not abort.
            # Confirm/cooldown checks are skipped here -- a disabled command
            # never fires, so those rules are moot for it.
            matches.append(result)
            continue
        _score, cmd, args, target = result
        if cmd.get("confirm"):
            print(f"[commands] Chain aborted: '{cmd['name']}' requires confirmation")
            LOG_BUFFER.append(
                f"{datetime.now().strftime('%H:%M:%S')}  !! Chain aborted: '{cmd['name']}' requires confirmation"
            )
            return matches, False, True
        cooldown = cmd.get("cooldown", DEFAULT_COOLDOWN)
        if cooldown > 0:
            last = _last_fired.get(cmd["name"], 0.0)
            if time.time() - last < cooldown:
                print(f"[commands] Chain aborted: '{cmd['name']}' on cooldown")
                LOG_BUFFER.append(
                    f"{datetime.now().strftime('%H:%M:%S')}  !! Chain aborted: '{cmd['name']}' on cooldown"
                )
                return matches, False, True
        matches.append((cmd, args, target))
    return matches, True, False


# -- Dispatcher ---------------------------------------------------------------

def _get_current_volume(gui_env: dict) -> str:
    try:
        result = run_capture(
            ["pactl", "get-sink-volume", "@DEFAULT_SINK@"],
            env=gui_env, timeout=2,
        )
        for part in result.stdout.split("/"):
            part = part.strip()
            if part.endswith("%"):
                return part
    except Exception:
        pass
    return "?"


def _build_notification(action_name: str, cmd: dict, merged: dict, gui_env: dict) -> str:
    if action_name == "launch_app":
        display = merged.get("app", "app")
        return f"Opening {display}"
    elif action_name == "open_url":
        url = merged.get("url", "")
        domain = (
            url.removeprefix("https://")
               .removeprefix("http://")
               .removeprefix("www.")
               .split("/")[0]
        )
        return f"Opening {domain}"
    elif action_name == "open_file":
        return f"Opening {os.path.basename(merged.get('path', ''))}"
    elif action_name == "run_command":
        return "Running command."
    elif action_name == "set_volume":
        return f"Volume set to {merged.get('level', '')}%"
    elif action_name == "volume_up":
        return f"Volume up ({_get_current_volume(gui_env)})"
    elif action_name == "volume_down":
        return f"Volume down ({_get_current_volume(gui_env)})"
    elif action_name == "mute":
        return "Muted"
    elif action_name == "unmute":
        return "Unmuted"
    elif action_name == "media_pause":
        return "Media paused"
    elif action_name == "media_resume":
        return "Media resumed"
    elif action_name == "move_window_left":
        return "Window moved left"
    elif action_name == "move_window_right":
        return "Window moved right"
    elif action_name == "maximize_window":
        return "Window maximized"
    elif action_name == "close_window":
        return "Window closed"
    elif action_name == "move_window_to_monitor":
        return "Window moved"
    elif action_name == "open_settings":
        return "Opening settings"
    elif action_name == "shutdown":
        return "Shutting down"
    elif action_name == "restart":
        return "Restarting"
    elif action_name == "logout":
        return "Logging out"
    else:
        return "Command acknowledged"


def _required_args(fn) -> list[str]:
    """
    Return the list of required keyword args for an action function,
    excluding gui_env and context which the dispatcher always provides.
    A required arg is one with no default value.
    """
    sig = inspect.signature(fn)
    required = []
    for name, param in sig.parameters.items():
        if name in ("gui_env", "context"):
            continue
        if param.default is inspect.Parameter.empty:
            required.append(name)
    return required


def _dispatch(cmd: dict, resolved_args: dict, gui_env: dict, context) -> None:
    action_name = cmd["action"]
    fn = ACTION_REGISTRY.get(action_name)
    if fn is None:
        print(f"[commands] Unknown action '{action_name}' -- check ACTION_REGISTRY")
        return

    merged = {**cmd.get("args", {}), **resolved_args}

    # Guard: validate that all required args are present and non-empty before calling.
    # Prevents TypeError crashes when a command was saved with incomplete config
    # (e.g. launch_app with no app picked).
    missing = [
        name for name in _required_args(fn)
        if not merged.get(name)
    ]
    if missing:
        display = cmd.get("display_name") or cmd.get("name", action_name)
        msg = f"'{display}' command is missing required setting(s): {', '.join(missing)}"
        print(f"[commands] {msg}")
        _notify("Command not configured", msg, gui_env=gui_env)
        LOG_BUFFER.append(f"{datetime.now().strftime('%H:%M:%S')}  !! {msg}")
        return

    merged["gui_env"] = gui_env
    merged["context"] = context

    print(f"[commands] Dispatching '{cmd['name']}'")
    fn(**merged)

    summary = _build_notification(action_name, cmd, merged, gui_env)
    _last_fired[cmd["name"]] = time.time()
    _notify(summary, gui_env=gui_env)

    LOG_BUFFER.append(f"{datetime.now().strftime('%H:%M:%S')}  >> {summary}")

    if context:
        context.update(last_command_name=cmd["name"])


def _dispatch_with_monitor(cmd: dict, args: dict, target_output: str | None,
                           gui_env: dict, context) -> None:
    """
    Handle optional "on [alias]" monitor routing, then dispatch the command.
    The placer script's queue self-drains via splice; no clear needed.

    Queue entries are written as "output:wm_class_hint" so the placer can
    match arriving windows to the correct entry (see _wm_class_hint).
    """
    if target_output:
        entry = f"{target_output}:{_wm_class_hint(cmd)}"
        windows.write_next_screen(entry, gui_env)
        time.sleep(0.3)
        LOG_BUFFER.append(f"{datetime.now().strftime('%H:%M:%S')}  Target monitor: {target_output}")

    _dispatch(cmd, args, gui_env, context)


def _notify_disabled(cmd: dict, gui_env: dict) -> None:
    """Notify (and log) that a matched command is disabled in Settings, then
    return without dispatching. Shared by the single-match and chain paths in
    try_match. Kept short on purpose -- Plasma truncates notifications."""
    display = cmd.get("display_name") or cmd.get("name")
    msg = f"{display} is disabled in Settings"
    print(f"[commands] {msg}")
    LOG_BUFFER.append(f"{datetime.now().strftime('%H:%M:%S')}  !! {msg}")
    _notify(msg, gui_env=gui_env)


# -- Main entry point ---------------------------------------------------------

def try_match(heard: str, gui_env: dict, context) -> bool:
    """
    Attempt to match `heard` against every command in the registry.
    Returns True if a command fired, False otherwise.

    Supports command chaining: "open reddit and open youtube" splits on
    " and " and dispatches both if EVERY segment matches above threshold.
    If any segment fails, falls back to matching the unsplit text.
    Confirm-required commands cannot be chained.
    """
    _check_reload(gui_env)

    heard = heard.strip().lower()

    for wake_word in get_wake_words():
        if heard.startswith(wake_word):
            heard = heard[len(wake_word):].strip()
            break

    if not heard:
        return False

    # -- Try chained commands first -------------------------------------------
    # Split aggressively on " and ", " an ", or " in " -- the latter two are
    # common Vosk mishearings of "and" (the Blue Yeti reliably hears "and" as
    # "in"; cheaper mics do too). The pattern WILL produce false-positive
    # splits like "open mind and body" -> ["open mind", "body"]. The defense
    # is _match_chain_segments(), which requires every segment to score
    # above threshold -- a bogus split won't satisfy that, and we fall
    # through to single-match. Chained commands are a primary feature, so
    # the aggressive split is deliberate; the per-segment match requirement
    # is what makes it safe.
    #
    # Two failure modes, handled differently:
    #  - chain_ok=False, chain_rejected=False: segments didn't all match
    #    (likely a false-positive split). Fall through to single-match.
    #  - chain_rejected=True: segments matched but a rule refused the chain
    #    (confirm-required, cooldown, multi-URL cross-monitor). User intent
    #    was clear; firing a partial match would be worse than nothing.
    chain_pattern = re.compile(r" and | an | in ", re.IGNORECASE)
    if chain_pattern.search(heard):
        segments = [s.strip() for s in chain_pattern.split(heard) if s.strip()]
        if len(segments) >= 2:
            matches, chain_ok, chain_rejected = _match_chain_segments(segments)

            # Split matched segments into the dispatchable ones and the
            # disabled sentinels. All the target/URL routing below operates on
            # `active`; disabled segments only ever produce a "disabled in
            # Settings" notification (see the dispatch block). A disabled
            # segment counts as matched, so chain_ok stays True -- it just
            # doesn't fire. A normal entry is (cmd, args, target); a disabled
            # entry is ("disabled", cmd), so `m[0] != "disabled"` separates
            # them (a dict is never equal to that string).
            active = [m for m in matches if m[0] != "disabled"]
            disabled = [m[1] for m in matches if m[0] == "disabled"]

            # Trailing-target propagation. When only the LAST segment carries
            # an explicit "on {alias}" and every earlier segment is untargeted,
            # distribute the trailing target backward across all prior
            # segments. Matches English grammar: "open A and open B on
            # monitor one" reads as "open both on monitor one". Propagation
            # happens BEFORE the cross-monitor URL check so the check sees
            # the resolved targets. Applies to all action types, not just
            # open_url -- "launch dolphin and open reddit on monitor one"
            # naturally means both land on monitor one. Disabled segments are
            # excluded -- they carry no target and never dispatch.
            if chain_ok and len(active) >= 2:
                last_target = active[-1][2]
                earlier_targets = [t for _, _, t in active[:-1]]
                if last_target is not None and all(t is None for t in earlier_targets):
                    active = [
                        (cmd, args, last_target) for cmd, args, _ in active[:-1]
                    ] + [active[-1]]
                    print(f"[commands] Trailing target '{last_target}' propagated to {len(active) - 1} prior segment(s)")
                    LOG_BUFFER.append(f"{datetime.now().strftime('%H:%M:%S')}  Trailing target propagated: all segments -> {last_target}")

            # Two-or-more open_url commands targeting DIFFERENT monitors race in
            # ways we can't fix (the browser may reuse an existing window, open
            # a new one, or open new tabs depending on state). Same monitor --
            # including both untargeted, both explicit-same, or one of each --
            # is fine; the user is intentionally opening multiple tabs in one
            # place. A distinct-targets check (set length > 1) catches all the
            # ambiguous cases including "on mon1 and (no target)" since None
            # and "DP-2" are distinct values.
            if chain_ok and active:
                url_targets = [target for cmd, _, target in active if cmd["action"] == "open_url"]
                if len(url_targets) >= 2 and len(set(url_targets)) > 1:
                    print("[commands] Chain aborted: multiple open_url commands targeting different monitors")
                    LOG_BUFFER.append(f"{datetime.now().strftime('%H:%M:%S')}  !! Chain aborted: multiple URLs targeting different monitors")
                    chain_ok = False
                    chain_rejected = True

            # Rule-rejected chains return immediately. The user's intent was
            # two commands; we refused; firing one half via single-match
            # fallback would be worse than firing nothing.
            if chain_rejected:
                return False

            if chain_ok and matches:
                # Disabled segments matched but are disabled in Settings:
                # notify once each, never dispatch. They rode through chain
                # validation (so they don't fail the chain) but stop here.
                for cmd in disabled:
                    _notify_disabled(cmd, gui_env)

                if active:
                    print(f"[commands] Chained {len(active)} commands")
                    LOG_BUFFER.append(f"{datetime.now().strftime('%H:%M:%S')}  Chaining {len(active)} commands")

                    # Build a queue of "output:wm_class" entries so the KWin
                    # placer can match each arriving window to the correct
                    # entry by class -- not by head-of-queue order, which races
                    # with how fast each app maps its window (see _wm_class_hint).
                    entries = [
                        f"{target}:{_wm_class_hint(cmd)}"
                        for cmd, _, target in active if target
                    ]
                    if entries:
                        queue_str = ",".join(entries)
                        windows.write_next_screen(queue_str, gui_env)

                    for cmd, args, target in active:
                        if target:
                            LOG_BUFFER.append(f"{datetime.now().strftime('%H:%M:%S')}  Target monitor: {target}")
                        print(f"[commands] Best match: '{cmd['name']}'")
                        _dispatch(cmd, args, gui_env, context)

                return True
            # else: fall through to single-match below

    # -- Single command match -------------------------------------------------
    result = _score_segment(heard)
    if result is None:
        return False

    # Best match is disabled in Settings: notify and stop. Returning True (we
    # handled it) prevents any further fall-through.
    if result[0] == "disabled":
        _notify_disabled(result[1], gui_env)
        return True

    score, best_cmd, best_args, target_output = result
    print(f"[commands] Best match: '{best_cmd['name']}' (score {score:.2f})")

    # Cooldown check
    cooldown = best_cmd.get("cooldown", DEFAULT_COOLDOWN)
    if cooldown > 0:
        last = _last_fired.get(best_cmd["name"], 0.0)
        elapsed = time.time() - last
        if elapsed < cooldown:
            print(f"[commands] '{best_cmd['name']}' on cooldown ({elapsed:.1f}s / {cooldown}s elapsed), ignoring.")
            return False

    if best_cmd.get("confirm"):
        context.pending_confirm = best_cmd
        context.pending_args    = best_args
        return True

    _dispatch_with_monitor(best_cmd, best_args, target_output, gui_env, context)
    return True


def dispatch_confirmed(gui_env: dict, context) -> None:
    cmd  = context.pending_confirm
    args = context.pending_args
    context.pending_confirm = None
    context.pending_args    = None
    _dispatch(cmd, args, gui_env, context)


# -- CLI entrypoint -----------------------------------------------------------
# Used by install.sh to emit the default config without spinning up the full
# app. Invoked as:  python -m core.commands --emit-defaults
# Output is a complete commands.json document on stdout.

if __name__ == "__main__":

    if len(sys.argv) == 2 and sys.argv[1] == "--emit-defaults":
        config = {
            "command_window": 5,
            "vosk_model": _DEFAULT_VOSK_MODEL_NAME,
            # open_mic / close_mic ship inside _default_commands() now -- no
            # separate top-level open_mic_phrases / close_mic_phrases keys.
            "commands": _default_commands() + [dict(p) for p in PINNED_SLOT],
            "wake_words": ["computer", "hey dude"],
            "monitors": {},
            "overrides": [],
        }
        json.dump(config, sys.stdout, indent=2)
        sys.stdout.write("\n")
        sys.exit(0)

    sys.stderr.write("Usage: python -m core.commands --emit-defaults\n")
    sys.exit(2)
