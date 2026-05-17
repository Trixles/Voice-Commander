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
import subprocess
import sys
import time
from datetime import datetime
from typing import Any

from core.actions import apps, system, windows
from core.aliases import PINNED_SLOT, default_aliases as _default_aliases
from core.desktop import extract_exec_token
from core.log_buffer import LOG_BUFFER
from core.matcher import _similarity
from core.notify import notify as _notify
from core.overrides import DEFAULT_OVERRIDES
from core.paths import CONFIG_PATH


# -- Default mic phrase lists ------------------------------------------------
# Shipped in commands.json on first install (via --emit-defaults below) and
# referenced by the Open Mic tab's 'Restore Defaults' button. Also used as
# the in-process fallback by get_open_mic_phrases() / get_close_mic_phrases()
# if commands.json is missing these top-level fields entirely.
DEFAULT_OPEN_MIC_PHRASES  = ["open mic", "open mike", "open microphone"]
DEFAULT_CLOSE_MIC_PHRASES = ["close mic", "close mike", "close microphone"]


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
# Tail-rescore threshold for non-slot fuzzy matches whose leading token is
# shared with the phrase (e.g. "open ..." vs "open ..."). The full-string
# ratio rewards the shared prefix structurally, so we require the *rest* of
# the strings to clear a lower-but-meaningful bar. Calibrated to reject
# "open like" vs "open plex" (tail score 0.50) while accepting plausible
# Vosk garbles like "open plix" vs "open plex" (0.75) and "open dolfen"
# vs "open dolphin" (0.62). Don't lower below ~0.55 -- the gap to
# semantically-unrelated tails narrows fast there.
TAIL_THRESHOLD = 0.60
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
        result = subprocess.run(
            ["xdg-settings", "get", "default-web-browser"],
            capture_output=True, text=True, timeout=5,
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
        "name": "open_settings",
        "display_name": "Open Settings",
        "phrases": ["open settings", "voice commander settings", "open voice commander settings"],
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

def load_config() -> None:
    global _commands, _config, _last_mtime
    with open(CONFIG_PATH, "r") as f:
        data = json.load(f)
    _config = data
    _commands = data.get("commands", [])

    # First launch: populate default commands and write them to disk.
    if not _commands:
        print("[commands] Empty command list -- generating defaults.")
        _commands = _default_commands()
        data["commands"] = _commands
        # Seed default wake words on fresh install if not already set.
        if "wake_words" not in data and "wake_word" not in data:
            data["wake_words"] = ["computer", "hey dude"]
        real_path = os.path.realpath(CONFIG_PATH)
        with open(real_path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"[commands] Defaults written to {real_path}")

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


def get_open_mic_phrases() -> set[str]:
    phrases = _config.get("open_mic_phrases")
    if isinstance(phrases, list) and phrases:
        return {p.lower().strip() for p in phrases}
    return {p.lower().strip() for p in DEFAULT_OPEN_MIC_PHRASES}


def get_close_mic_phrases() -> set[str]:
    phrases = _config.get("close_mic_phrases")
    if isinstance(phrases, list) and phrases:
        return {p.lower().strip() for p in phrases}
    return {p.lower().strip() for p in DEFAULT_CLOSE_MIC_PHRASES}


def get_overrides() -> list[dict]:
    """Return the full ordered list of overrides to apply.
    Defaults come first; user-added rules from commands.json follow.
    Users cannot disable a default, but can shadow one by adding a
    user rule with the same pattern (later rules apply to text already
    rewritten by earlier rules)."""
    user = _config.get("overrides", [])
    if not isinstance(user, list):
        user = []
    return list(DEFAULT_OVERRIDES) + user


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


# -- Slot-bearing phrase handling ---------------------------------------------

def _has_slots(phrase: str) -> bool:
    return "{" in phrase and "}" in phrase


def _phrase_to_regex(phrase: str) -> re.Pattern:
    parts = re.split(r"(\{[^}]+\})", phrase)
    pattern_parts = []
    for i, part in enumerate(parts):
        if part.startswith("{") and part.endswith("}"):
            slot_name = part[1:-1]
            # Greedy when the slot is the last token (nothing meaningful after it).
            # Lazy when there's literal text after the slot that the regex must match.
            remaining = "".join(parts[i + 1:]).strip()
            quantifier = ".+" if not remaining else ".+?"
            pattern_parts.append(f"(?P<{slot_name}>{quantifier})")
        else:
            pattern_parts.append(re.escape(part))
    return re.compile(r"(?:^|\b)" + "".join(pattern_parts) + r"(?:\b|$)", re.IGNORECASE)


def _extract_slots(heard: str, phrase: str) -> dict[str, str] | None:
    pattern = _phrase_to_regex(phrase)
    match = pattern.search(heard)
    if not match:
        return None
    return {k: v.strip() for k, v in match.groupdict().items()}


def _resolve_slot(slot_name: str, raw_value: str, slot_def: dict) -> str:
    if not slot_def.get("fuzzy", False):
        return raw_value
    known = slot_def.get("known_values")
    if not known:
        raise ValueError(f"Slot '{slot_name}' has fuzzy=true but no known_values")
    best_match = max(known, key=lambda k: _similarity(raw_value, k))
    best_score = _similarity(raw_value, best_match)
    if best_score >= 0.5:
        print(f"  Slot '{slot_name}': '{raw_value}' -> '{best_match}' (score {best_score:.2f})")
        return best_match
    print(f"  Slot '{slot_name}': '{raw_value}' fuzzy match failed (best '{best_match}' @ {best_score:.2f})")
    return raw_value


# -- Non-slot phrase matching -------------------------------------------------

def _match_non_slot(heard: str, phrase: str) -> float:
    """
    Score a non-slot phrase against heard text.

    Returns SequenceMatcher.ratio() of the full strings, with one guard:
    if heard and phrase share their leading token (the "open"/"launch"/etc.
    verb prefix that aliases many commands), the *tail* of both strings is
    scored separately and must clear TAIL_THRESHOLD. This stops the shared
    verb from carrying a match where the actual content words are unrelated
    (e.g. "open like" vs "open plex" -- "open " contributes most of the
    ratio, but "like" vs "plex" scores 0.50 on its own).

    Single-word phrases and phrases without a shared leading token skip the
    guard entirely and fall back to the plain overall ratio.
    """
    overall = _similarity(heard, phrase)

    h_tokens = heard.split()
    p_tokens = phrase.split()
    if (len(h_tokens) >= 2 and len(p_tokens) >= 2
            and h_tokens[0].lower() == p_tokens[0].lower()):
        h_tail = " ".join(h_tokens[1:])
        p_tail = " ".join(p_tokens[1:])
        tail = _similarity(h_tail, p_tail)
        if tail < TAIL_THRESHOLD:
            print(f"  '{heard}' vs '{phrase}': {overall:.2f} (tail {tail:.2f} < {TAIL_THRESHOLD} -- rejected)")
            return 0.0
        print(f"  '{heard}' vs '{phrase}': {overall:.2f} (tail {tail:.2f})")
        return overall

    print(f"  '{heard}' vs '{phrase}': {overall:.2f}")
    return overall


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

def _score_segment(heard: str) -> tuple[float, dict, dict, str | None] | None:
    """
    Score a single heard segment against all commands.
    Returns (score, cmd, resolved_args, target_output) for the best match,
    or None if nothing clears the threshold.

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

    return (best_score, best_cmd, best_args, target_output)


# -- Dispatcher ---------------------------------------------------------------

def _get_current_volume(gui_env: dict) -> str:
    try:
        result = subprocess.run(
            ["pactl", "get-sink-volume", "@DEFAULT_SINK@"],
            capture_output=True, text=True, timeout=2, env=gui_env,
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
    # Split on " and ", " an ", or " in " (common Vosk mishearings of "and").
    # Only accept the split if every segment matches a command.
    #
    # Two failure modes, handled differently:
    #  - chain_ok=False: segments didn't all match (e.g. "open mind and body"
    #    isn't really a chain). Fall through to single-match.
    #  - chain_rejected=True: segments matched but a rule refused the chain
    #    (confirm-required, cooldown, multi-URL cross-monitor). User intent
    #    was clear; firing a partial match would be worse than nothing.
    chain_pattern = re.compile(r" and | an | in ", re.IGNORECASE)
    if chain_pattern.search(heard):
        segments = [s.strip() for s in chain_pattern.split(heard) if s.strip()]
        if len(segments) >= 2:
            matches = []
            chain_ok = True
            chain_rejected = False
            for seg in segments:
                result = _score_segment(seg)
                if result is None:
                    chain_ok = False
                    break
                score, cmd, args, target = result
                # Don't allow confirm-required commands in chains
                if cmd.get("confirm"):
                    print(f"[commands] Chain aborted: '{cmd['name']}' requires confirmation")
                    LOG_BUFFER.append(f"{datetime.now().strftime('%H:%M:%S')}  !! Chain aborted: '{cmd['name']}' requires confirmation")
                    chain_ok = False
                    chain_rejected = True
                    break
                # Cooldown check per segment
                cooldown = cmd.get("cooldown", DEFAULT_COOLDOWN)
                if cooldown > 0:
                    last = _last_fired.get(cmd["name"], 0.0)
                    if time.time() - last < cooldown:
                        print(f"[commands] Chain aborted: '{cmd['name']}' on cooldown")
                        LOG_BUFFER.append(f"{datetime.now().strftime('%H:%M:%S')}  !! Chain aborted: '{cmd['name']}' on cooldown")
                        chain_ok = False
                        chain_rejected = True
                        break
                matches.append((cmd, args, target))

            # Trailing-target propagation. When only the LAST segment carries
            # an explicit "on {alias}" and every earlier segment is untargeted,
            # distribute the trailing target backward across all prior
            # segments. Matches English grammar: "open A and open B on
            # monitor one" reads as "open both on monitor one". Propagation
            # happens BEFORE the cross-monitor URL check so the check sees
            # the resolved targets. Applies to all action types, not just
            # open_url -- "launch dolphin and open reddit on monitor one"
            # naturally means both land on monitor one.
            if chain_ok and len(matches) >= 2:
                last_target = matches[-1][2]
                earlier_targets = [t for _, _, t in matches[:-1]]
                if last_target is not None and all(t is None for t in earlier_targets):
                    matches = [
                        (cmd, args, last_target) for cmd, args, _ in matches[:-1]
                    ] + [matches[-1]]
                    print(f"[commands] Trailing target '{last_target}' propagated to {len(matches) - 1} prior segment(s)")
                    LOG_BUFFER.append(f"{datetime.now().strftime('%H:%M:%S')}  Trailing target propagated: all segments -> {last_target}")

            # Two-or-more open_url commands targeting DIFFERENT monitors race in
            # ways we can't fix (the browser may reuse an existing window, open
            # a new one, or open new tabs depending on state). Same monitor --
            # including both untargeted, both explicit-same, or one of each --
            # is fine; the user is intentionally opening multiple tabs in one
            # place. A distinct-targets check (set length > 1) catches all the
            # ambiguous cases including "on mon1 and (no target)" since None
            # and "DP-2" are distinct values.
            if chain_ok and matches:
                url_targets = [target for cmd, _, target in matches if cmd["action"] == "open_url"]
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
                print(f"[commands] Chained {len(matches)} commands")
                LOG_BUFFER.append(f"{datetime.now().strftime('%H:%M:%S')}  Chaining {len(matches)} commands")

                # Build a queue of "output:wm_class" entries so the KWin
                # placer can match each arriving window to the correct
                # entry by class -- not by head-of-queue order, which races
                # with how fast each app maps its window (see _wm_class_hint).
                entries = [
                    f"{target}:{_wm_class_hint(cmd)}"
                    for cmd, _, target in matches if target
                ]
                if entries:
                    queue_str = ",".join(entries)
                    windows.write_next_screen(queue_str, gui_env)

                for cmd, args, target in matches:
                    if target:
                        _log.append(f"{datetime.now().strftime('%H:%M:%S')}  Target monitor: {target}")
                    print(f"[commands] Best match: '{cmd['name']}'")
                    _dispatch(cmd, args, gui_env, context)

                return True
            # else: fall through to single-match below

    # -- Single command match -------------------------------------------------
    result = _score_segment(heard)
    if result is None:
        return False

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
            "commands": _default_commands() + [dict(p) for p in PINNED_SLOT],
            "wake_words": ["computer", "hey dude"],
            "monitors": {},
            "open_mic_phrases": list(DEFAULT_OPEN_MIC_PHRASES),
            "close_mic_phrases": list(DEFAULT_CLOSE_MIC_PHRASES),
            "overrides": [],
        }
        json.dump(config, sys.stdout, indent=2)
        sys.stdout.write("\n")
        sys.exit(0)

    sys.stderr.write("Usage: python -m core.commands --emit-defaults\n")
    sys.exit(2)
