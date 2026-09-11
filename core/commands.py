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
from typing import Any

from core.actions import apps, system, windows
from core.aliases import PINNED_SLOT, default_aliases as _default_aliases
from core.desktop import extract_exec_token
from core.log_buffer import log
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
import core.paths as paths
from core.paths import CONFIG_PATH
from core.run import run_capture
from core.wake import BAKED_IN_WAKE_WORDS, custom_wake_words


# -- Default mic phrase lists ------------------------------------------------
# As of 0.8.0 the open/close mic phrases live in the command list as the
# `open_mic` / `close_mic` system commands (see _default_commands), edited on
# the Commands tab like any other system command. These constants are the
# default phrases those commands ship with, and the in-process fallback used by
# get_open_mic_phrases() / get_close_mic_phrases() when no command is present.
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
    "celery_man":             apps.celery_man,
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
    "minimize_window":        windows.minimize_window,
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


_README_DEFAULT_PATH = os.path.join(paths.DATA_DIR, "README.md")


def _default_system_commands() -> list[dict]:
    """The built-in system commands (volume, media, window, power, mic, settings).

    Subprocess-free and deterministic -- no browser detection -- so it's cheap to
    call on every load_config() for the missing-command merge (see load_config).
    Single source of truth for the SYSTEM half of the default set; the user-action
    examples (browser/reddit/readme) live in _default_commands().

    Does NOT include the slot-pinned commands (move_to_monitor, set_volume): those
    live in core.aliases._PINNED_SLOT and self-heal via their own injection path,
    so re-adding them here would duplicate them.
    """
    commands: list[dict] = []
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
        "name": "minimize_window",
        "display_name": "Minimize window",
        "phrases": ["minimize window", "minimize this window"],
        "action": "minimize_window",
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
    # System commands live in their own subprocess-free helper so load_config()
    # can cheaply merge in any that a pre-existing config is missing.
    commands.extend(_default_system_commands())

    return commands


class ConfigError(Exception):
    """Raised when the config file exists but can't be parsed as JSON.

    Distinct from FileNotFoundError (no config yet -> caller seeds defaults):
    a ConfigError means there IS a file and it's corrupt, so we must NOT
    silently overwrite it -- that would destroy the user's custom commands.
    Callers surface it and stop rather than regenerate."""


def load_config() -> None:
    global _commands, _config, _last_mtime
    with open(CONFIG_PATH, "r") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            # Non-destructive: never rewrite a file we couldn't parse. Startup
            # turns this into a friendly exit (voice_commander.main); hot-reload
            # (_check_reload) catches it and keeps the last-good config running.
            raise ConfigError(
                f"{CONFIG_PATH} is not valid JSON ({e}). Fix the file or "
                f"delete it to regenerate defaults."
            ) from e

    # First launch: populate default commands and write them to disk.
    if not data.get("commands"):
        print("[commands] Empty command list -- generating defaults.")
        data["commands"] = _default_commands()
        # Seed default wake words on fresh install if not already set.
        if "wake_words" not in data and "wake_word" not in data:
            data["wake_words"] = ["hey dude"]
        real_path = os.path.realpath(CONFIG_PATH)
        with open(real_path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"[commands] Defaults written to {real_path}")
    else:
        # Self-healing migration: merge in any SYSTEM commands a pre-existing
        # config predates (e.g. minimize_window, added after the user's config
        # was first written). load_config() only seeds defaults when the list is
        # empty, so without this an existing user never gets newly-shipped system
        # commands -- and Restore Defaults would nuke their custom commands to do
        # it. We add only by NAME and only system commands: user-action examples
        # (browser/reddit/readme) are deletable and must STAY deleted, so they're
        # never re-added. Slot-pinned commands aren't in _default_system_commands
        # (they self-heal via _PINNED_SLOT), so they're untouched too. Toggle
        # state on existing rows is preserved -- we only append what's missing.
        existing_names = {c.get("name") for c in data["commands"]}
        missing = [c for c in _default_system_commands()
                   if c["name"] not in existing_names]
        if missing:
            data["commands"].extend(missing)
            real_path = os.path.realpath(CONFIG_PATH)
            with open(real_path, "w") as f:
                json.dump(data, f, indent=2)
            print(f"[commands] Merged {len(missing)} new system command(s) into "
                  f"config: {[c['name'] for c in missing]}")

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
    except ConfigError as e:
        # The user saved a syntactically broken config while we were running
        # (e.g. mid hand-edit). Keep the last-good config loaded and try again
        # on the next check -- do NOT let a parse error propagate into try_match
        # and kill the listener thread. Note we leave _last_mtime unchanged so
        # the reload retries once the file becomes valid again.
        print(f"[commands] Ignoring config reload: {e}")
    except OSError:
        pass


def get_wake_words() -> list[str]:
    """Effective wake words: the baked-in word(s) followed by the user's
    custom words. 'computer' is always present and cannot be removed, so the
    openWakeWord audio path always has its word; customs are additive. Config
    stores customs only, but custom_wake_words() still filters a stray
    'computer' for back-compat with configs written before this was
    structural; we then dedupe so a repeated custom can't double."""
    raw = _config.get("wake_words")
    if not (isinstance(raw, list) and raw):
        raw = [_config.get("wake_word", "computer")]

    result = list(BAKED_IN_WAKE_WORDS)
    seen = set(result)
    for w in custom_wake_words(raw):
        if w not in seen:
            result.append(w)
            seen.add(w)
    return result


def get_command_window() -> int:
    return _config.get("command_window", 5)


def get_match_threshold() -> float:
    """Global fuzzy-match threshold ("recognition strictness"), user-adjustable
    on the Options tab. Falls back to DEFAULT_THRESHOLD when unset. Per-command
    `threshold` keys still override this in _score_segment."""
    return _config.get("match_threshold", DEFAULT_THRESHOLD)


def notifications_enabled() -> bool:
    """Whether general desktop notifications are on (Options tab blanket toggle).
    The shutdown/restart/logout CONFIRMATION prompts ignore this and always fire
    -- that gating lives at the call sites in core/listener.py."""
    return _config.get("notifications", True)


def get_auto_pause_media() -> bool:
    """Whether to auto-pause playing media on wake and resume it when the wake
    window closes (Options tab toggle, default ON). Read live at wake time in
    core/listener.py's set_state hook."""
    return _config.get("auto_pause_media", True)


def _mic_phrases(action: str, defaults: list[str]) -> set[str]:
    """Resolve the active phrase set for a mic toggle, in priority order:

      1. The `open_mic`/`close_mic` system command in the list -- but ONLY if
         it's enabled. A disabled mic command returns an empty set, so the
         voice toggle stops working (tray left-click still toggles via the
         separate command-queue path).
      2. The shipped defaults.
    """
    for cmd in _commands:
        if cmd.get("action") == action:
            if not cmd.get("enabled", True):
                return set()
            phrases = cmd.get("phrases") or []
            return {p.lower().strip() for p in phrases}
    return {p.lower().strip() for p in defaults}


def get_open_mic_phrases() -> set[str]:
    return _mic_phrases("open_mic", DEFAULT_OPEN_MIC_PHRASES)


def get_close_mic_phrases() -> set[str]:
    return _mic_phrases("close_mic", DEFAULT_CLOSE_MIC_PHRASES)


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


# Model files (ggml weights for whisper-server, Silero VAD onnx) live
# under a shared models/ dir.
_MODELS_DIR = os.path.join(paths.DATA_DIR, "models/")


def get_whisper_server_port() -> int:
    return int(_config.get("whisper_server_port", 8910))


def get_whisper_vad_tail_ms() -> int:
    """Silence tail that closes a speech segment (see WhisperRecognizer)."""
    return int(_config.get("whisper_vad_tail_ms", 400))


def get_whisper_model_path() -> str:
    """Absolute path to the ggml weights whisper-server should load.

    'whisper_model' is either a size token ('base.en', 'small.en', ...) that
    derives MODELS_DIR/whisper/ggml-{token}.bin, OR a full filesystem path the
    user browsed to. A value containing a path separator is treated as a path;
    anything else is a token."""
    raw = str(_config.get("whisper_model", "base.en")).strip() or "base.en"
    if os.sep in raw:
        return raw
    return os.path.join(_MODELS_DIR, "whisper", f"ggml-{raw}.bin")


def get_vad_model_path() -> str:
    return os.path.join(_MODELS_DIR, "silero_vad.onnx")


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
    if best_output is not None and best_score >= get_match_threshold():
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


# -- Phrase character sanitizing (Save-time guard) ----------------------------

def sanitize_phrase_text(raw: str) -> tuple[str, str]:
    """Strip characters that can't occur in a spoken command from phrase text.

    Transcriptions reach matching as letters, digits and spaces only (the
    recognizer seam strips punctuation and symbols) -- so a phrase
    containing "{", "?", "@", etc. can
    never match anything a user says; it's a dead phrase. Worse, "{...}" makes
    the matcher treat the phrase as slot-bearing (see _score_segment), which
    once crashed the listener on an unbalanced slot name. We forbid the whole
    class at Save time rather than special-casing braces.

    Allowed: letters, digits, spaces, and commas (the phrase-list separator).
    Any other whitespace (tabs, newlines) collapses to a single space so
    pasted multi-line text doesn't fuse words. Returns
    ``(cleaned_text, removed_chars)`` where removed_chars is the sorted, unique
    run of disallowed characters that were dropped ("" if the text was clean).

    Pure and Qt-free so it lives here beside the other save-time phrase
    guards and is unit-tested without a settings dialog; the UI only presents.
    """
    removed: set[str] = set()
    out: list[str] = []
    for ch in raw:
        if ch.isalnum() or ch in ", ":
            out.append(ch)
        elif ch.isspace():
            out.append(" ")  # tab/newline -> space, not a dropped character
        else:
            removed.add(ch)
    cleaned = re.sub(r" {2,}", " ", "".join(out))
    return cleaned, "".join(sorted(removed))


def _build_invalid_chars_message(offenders: list[dict]) -> str:
    """User-facing text for the invalid-character Save block.

    ``offenders`` is a list of ``{"name": str, "cleaned": str, "removed": str}``
    (one per command whose phrases lost characters). Pure string assembly, no
    Qt -- unit-tested; the dialog only shows it. The per-command "…for the
    <name> command are now:" framing makes clear which command changed."""
    all_removed = sorted(set("".join(o["removed"] for o in offenders)))
    shown = " ".join(all_removed)
    lines = [
        "Voice phrases can only contain letters, numbers, and spaces, so the "
        "following characters have been removed:",
        "",
        shown,
        "",
    ]
    for o in offenders:
        lines.append(f"Your phrases for the {o['name']} command are now:")
        lines.append(o["cleaned"].strip() or "(no phrases left)")
        lines.append("")
    return "\n".join(lines).rstrip()


def dedupe_phrase_text(raw: str) -> tuple[str, list[str]]:
    """Drop phrases listed more than once within a single command's box.

    Comparison is case-insensitive and whitespace-trimmed (matching how the
    runtime matcher normalizes). The first spelling of each phrase is kept in
    order; later repeats are dropped. Blank entries (e.g. a trailing comma) are
    also discarded. Returns ``(cleaned_text, removed_phrases)`` where
    removed_phrases lists the dropped duplicates in the raw spelling seen ("[]"
    if nothing was duplicated).

    This is the WITHIN-command case; cross-command collisions are handled
    separately by find_cross_collision. Pure and Qt-free."""
    seen: set[str] = set()
    kept: list[str] = []
    removed: list[str] = []
    for part in raw.split(","):
        phrase = part.strip()
        if not phrase:
            continue
        key = phrase.lower()
        if key in seen:
            removed.append(phrase)
            continue
        seen.add(key)
        kept.append(phrase)
    return ", ".join(kept), removed


def _build_duplicate_removed_message(offenders: list[dict]) -> str:
    """User-facing text for the within-command duplicate-phrase Save block.

    ``offenders`` is a list of ``{"name": str, "cleaned": str, "removed": [..]}``
    (one per command that listed a phrase more than once). Parallels
    _build_invalid_chars_message. Pure/Qt-free -- unit-tested; dialog presents."""
    lines = [
        "Each phrase only needs to be listed once per command, so the "
        "duplicates have been removed.",
        "",
    ]
    for o in offenders:
        lines.append(f"Your phrases for the {o['name']} command are now:")
        lines.append(o["cleaned"].strip() or "(no phrases left)")
        lines.append("")
    return "\n".join(lines).rstrip()


def find_cross_collision(commands: list[dict]) -> dict | None:
    """Find the first phrase claimed by two different commands at once.

    ``commands`` is a list, in row/file order, of
    ``{"name", "enabled", "original": [phrases on disk], "current": [phrases
    now]}``. Only phrases in TWO OR MORE commands' *current* lists count, so a
    phrase moved from one command to another (dropped here, added there) is not
    a collision. Comparison is case-insensitive and trimmed; within-command
    repeats are ignored (they're handled by dedupe first).

    The keeper (owner) of a colliding phrase is the command that already had it
    on disk -- the established owner -- so a user can't knock a phrase off a
    built-in command just by duplicating it. If neither had it (two commands
    added the same new phrase), the first in order keeps it. Every OTHER holder
    is a loser; this returns the FIRST loser found, scanning in order:

        {"phrase", "owner_name", "owner_enabled", "loser_index",
         "loser_name", "loser_cleaned"}

    ``loser_index`` indexes ``commands``; ``loser_cleaned`` is that command's
    current phrases minus the colliding one, comma-joined -- the caller writes
    it back to the box. Returns None if there are no cross-command collisions.
    One per call: the caller blocks and the next Save surfaces the next.

    Pure and Qt-free -- unit-tested; the container only supplies row data and
    applies the mutation."""
    def _norm_unique(phrases: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for p in phrases:
            s = p.strip()
            k = s.lower()
            if s and k not in seen:
                seen.add(k)
                out.append(s)
        return out

    current = [_norm_unique(c["current"]) for c in commands]
    on_disk = [{p.strip().lower() for p in c.get("original", [])} for c in commands]

    holders: dict[str, list[int]] = {}
    for i, phrases in enumerate(current):
        for p in phrases:
            holders.setdefault(p.lower(), []).append(i)

    for i, phrases in enumerate(current):
        for p in phrases:
            key = p.lower()
            idxs = holders.get(key, [])
            if len(idxs) < 2:
                continue
            keeper = next((j for j in idxs if key in on_disk[j]), idxs[0])
            if keeper == i:
                continue  # this command legitimately owns the phrase
            remaining = [q for q in phrases if q.lower() != key]
            return {
                "phrase": p,
                "owner_name": commands[keeper]["name"],
                "owner_enabled": commands[keeper].get("enabled", True),
                "loser_index": i,
                "loser_name": commands[i]["name"],
                "loser_cleaned": ", ".join(remaining),
            }
    return None


def _build_cross_collision_message(info: dict) -> str:
    """User-facing text for the cross-command duplicate auto-fix.

    ``info`` is a find_cross_collision() result. The phrase has already been
    stripped from the loser's box, so this reports which command still owns it
    and what the loser's phrases are now. Pure/Qt-free -- unit-tested."""
    lines = [
        "Each phrase can only belong to a single command.",
        "",
        f'The phrase "{info["phrase"]}" is already being used on the '
        f'{info["owner_name"]} command. You must remove it from that command '
        f'before you can add it to this one.',
        "",
        f'Your phrases for the {info["loser_name"]} command are now:',
        info["loser_cleaned"].strip() or "(no phrases left)",
    ]
    if not info.get("owner_enabled", True):
        lines += [
            "",
            "Note: a disabled command still claims its phrases — disabling it "
            "doesn't free them up.",
        ]
    return "\n".join(lines)


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
        threshold = cmd.get("threshold", get_match_threshold())
        for phrase in cmd["phrases"]:
            # A phrase only takes the slot path if the command actually DECLARES
            # slots. _has_slots() just sees "{...}" in the text, and a successful
            # extraction hard-scores 1.0 below -- so a stray-braced phrase on a
            # command with no slot defs (e.g. a user who typed "open {x}") would
            # otherwise win every "open ..." utterance at max score and shadow
            # real commands. Gating on cmd["slots"] confines 1.0 to genuine slot
            # commands; braces without a slot def fall through to literal
            # matching, where they simply never match spoken text.
            if _has_slots(phrase) and cmd.get("slots"):
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

    Partial execution: a segment that scores below threshold no longer kills
    the whole chain. It rides along as a ("nomatch", seg) sentinel; the caller
    fires a "'<seg>' No match" toast for it while still dispatching the OTHER
    matched segments. So a single mishear ("open it on monitor one and open
    dolphin on monitor two") fires Dolphin and visibly flags the bad half.

    The false-split defense for the deliberately-aggressive split pattern in
    try_match() (" and | an | in ") moves to the return value. The pattern WILL
    produce false splits ("open mind and body" -> ["open mind", "body"]); when
    NOT ONE segment matches, chain_ok comes back False and the caller falls
    through to single-match instead of toasting once per bogus fragment.

    Each entry in `matches` is one of three shapes:
      - (cmd, args, target): a normal, dispatchable segment.
      - ("disabled", cmd): a segment whose best match is disabled in Settings.
        Counts as "matched" for chain validity; caller notifies, never fires.
      - ("nomatch", seg): a segment that scored below threshold. Caller toasts
        "'<seg>' No match" but still fires the rest. seg is raw segment text.

    Returns (matches, chain_ok, chain_rejected):
      - chain_ok=True, chain_rejected=False: at least one segment matched
        (enabled or disabled). Dispatchable; disabled entries notify-and-skip,
        nomatch entries notify-and-skip, the rest fire.
      - chain_ok=False, chain_rejected=False: NO segment matched. Treat as a
        misparse/false-split and fall through to single-match.
      - chain_ok=False, chain_rejected=True: a segment scored but a rule
        refused (confirm-required, cooldown). Caller returns False
        immediately -- the user clearly meant a chain containing a guarded
        command; firing the rest of it would be worse than refusing the whole
        thing. Rule rejections still abort wholesale even with partial
        execution on. Disabled/nomatch segments never trigger this.
    """
    matches: list = []
    for seg in segments:
        result = _score_segment(seg)
        if result is None:
            # Below threshold: record a no-match sentinel and keep going. The
            # caller toasts it but still fires the matched siblings (partial
            # execution). The all-miss case is caught at the return below.
            matches.append(("nomatch", seg))
            continue
        if result[0] == "disabled":
            # Matched but disabled: ride along as a sentinel, do not abort.
            # Confirm/cooldown checks are skipped here -- a disabled command
            # never fires, so those rules are moot for it.
            matches.append(result)
            continue
        _score, cmd, args, target = result
        if cmd.get("confirm"):
            print(f"[commands] Chain aborted: '{cmd['name']}' requires confirmation")
            log(f"!! Chain aborted: '{cmd['name']}' requires confirmation")
            return matches, False, True
        cooldown = cmd.get("cooldown", DEFAULT_COOLDOWN)
        if cooldown > 0:
            last = _last_fired.get(cmd["name"], 0.0)
            if time.time() - last < cooldown:
                print(f"[commands] Chain aborted: '{cmd['name']}' on cooldown")
                log(f"!! Chain aborted: '{cmd['name']}' on cooldown")
                return matches, False, True
        matches.append((cmd, args, target))
    # chain_ok = "at least one segment actually matched" (enabled or disabled).
    # If every segment came back nomatch, this was a misparse/false-split, not
    # a chain -- return False so the caller falls through to single-match
    # rather than firing nothing and toasting each bogus fragment.
    matched_any = any(m[0] != "nomatch" for m in matches)
    return matches, matched_any, False


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


def _url_domain(url: str) -> str:
    """Bare domain of a URL for notification text (strips scheme, www, path)."""
    return (
        url.removeprefix("https://")
           .removeprefix("http://")
           .removeprefix("www.")
           .split("/")[0]
    )


# Per-action notification summaries. Each entry is a callable
# (merged_args, gui_env) -> str; an action absent from the table falls back to
# a generic acknowledgement. A table (rather than an if-elif chain) keeps the
# action coverage scannable and gives any new action a safe default for free.
_NOTIFICATION_TEMPLATES = {
    "launch_app":   lambda m, env: f"Opening {m.get('app', 'app')}",
    "open_url":     lambda m, env: f"Opening {_url_domain(m.get('url', ''))}",
    "celery_man":   lambda m, env: "Loading up Celery Man",
    "open_file":    lambda m, env: f"Opening {os.path.basename(m.get('path', ''))}",
    "run_command":  lambda m, env: "Running command.",
    "set_volume":   lambda m, env: f"Volume set to {m.get('level', '')}%",
    "volume_up":    lambda m, env: f"Volume up ({_get_current_volume(env)})",
    "volume_down":  lambda m, env: f"Volume down ({_get_current_volume(env)})",
    "mute":         lambda m, env: "Muted",
    "unmute":       lambda m, env: "Unmuted",
    "media_pause":  lambda m, env: "Media paused",
    "media_resume": lambda m, env: "Media resumed",
    "move_window_left":       lambda m, env: "Window moved left",
    "move_window_right":      lambda m, env: "Window moved right",
    "minimize_window":        lambda m, env: "Window minimized",
    "maximize_window":        lambda m, env: "Window maximized",
    "close_window":           lambda m, env: "Window closed",
    "move_window_to_monitor": lambda m, env: "Window moved",
    "open_settings": lambda m, env: "Opening settings",
    "shutdown":      lambda m, env: "Shutting down",
    "restart":       lambda m, env: "Restarting",
    "logout":        lambda m, env: "Logging out",
}


def _build_notification(action_name: str, merged: dict, gui_env: dict) -> str:
    template = _NOTIFICATION_TEMPLATES.get(action_name)
    if template is None:
        return "Command acknowledged"
    return template(merged, gui_env)


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
        if notifications_enabled():
            _notify("Command not configured", msg, gui_env=gui_env)
        log(f"!! {msg}")
        return

    merged["gui_env"] = gui_env
    merged["context"] = context

    print(f"[commands] Dispatching '{cmd['name']}'")
    if fn(**merged) is False:
        # The action declined to run (e.g. Celery Man's echo guard) and has
        # already logged why. Suppress the fired-notification, cooldown stamp,
        # and ">>" log line -- nothing happened, so nothing gets announced.
        return

    summary = _build_notification(action_name, merged, gui_env)
    _last_fired[cmd["name"]] = time.time()
    if notifications_enabled():
        _notify(summary, gui_env=gui_env)

    log(f">> {summary}")

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
        log(f"Target monitor: {target_output}")

    _dispatch(cmd, args, gui_env, context)


def _notify_disabled(cmd: dict, gui_env: dict) -> None:
    """Notify (and log) that a matched command is disabled in Settings, then
    return without dispatching. Shared by the single-match and chain paths in
    try_match. Kept short on purpose -- Plasma truncates notifications."""
    display = cmd.get("display_name") or cmd.get("name")
    msg = f"{display} is disabled in Settings"
    print(f"[commands] {msg}")
    log(f"!! {msg}")
    if notifications_enabled():
        _notify(msg, gui_env=gui_env)


def _notify_nomatch(seg: str, gui_env: dict) -> None:
    """Notify (and log) that one segment of a chain matched no command, while
    its sibling segments still fire (partial execution). Mirrors the
    single-match "No match" toast so a misheard chain segment is visible
    without reading the log -- e.g. "open it on monitor one and open dolphin"
    fires Dolphin and toasts "'open it on monitor one' No match"."""
    print(f"[commands] Chain segment no match: '{seg}'")
    log(f"No match: '{seg}'")
    if notifications_enabled():
        _notify("No match", f"'{seg}'", gui_env=gui_env)


# -- Main entry point ---------------------------------------------------------

def try_match(heard: str, gui_env: dict, context) -> bool:
    """
    Attempt to match `heard` against every command in the registry.
    Returns True if a command fired, False otherwise.

    Supports command chaining: "open reddit and open youtube" splits on
    " and " and dispatches every segment that matches above threshold. A
    segment that doesn't match fires a "'<seg>' No match" notification while
    its siblings still fire (partial execution). If NO segment matches, falls
    back to matching the unsplit text. Confirm-required commands cannot be
    chained -- a guarded segment aborts the whole chain.
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
    # common mishearings of "and" (the Blue Yeti reliably hears "and" as
    # "in"; cheaper mics do too). The pattern WILL produce false-positive
    # splits like "open mind and body" -> ["open mind", "body"]. Partial
    # execution (in _match_chain_segments) fires every segment that matches and
    # toasts "'<seg>' No match" for those that don't -- so one misheard segment
    # no longer kills the whole chain. The false-split defense survives as the
    # all-miss guard: if NOT ONE segment matches, chain_ok is False and we fall
    # through to single-match instead of toasting per bogus fragment.
    #
    # Three outcomes, handled differently:
    #  - chain_ok=True: >=1 segment matched. Fire the matches; toast the
    #    disabled and nomatch segments.
    #  - chain_ok=False, chain_rejected=False: NO segment matched (likely a
    #    false-positive split). Fall through to single-match.
    #  - chain_rejected=True: a segment matched but a rule refused the chain
    #    (confirm-required, cooldown, multi-URL cross-monitor). User intent
    #    was clear; firing the rest would be worse than refusing the whole
    #    thing. Rule rejections abort wholesale even with partial execution.
    chain_pattern = re.compile(r" and | an | in ", re.IGNORECASE)
    if chain_pattern.search(heard):
        segments = [s.strip() for s in chain_pattern.split(heard) if s.strip()]
        if len(segments) >= 2:
            matches, chain_ok, chain_rejected = _match_chain_segments(segments)

            # Partition the segments three ways. All target/URL routing below
            # operates on `active` (the dispatchable ones). `disabled` segments
            # produce a "disabled in Settings" toast; `nomatch` segments produce
            # a "'<seg>' No match" toast (partial execution) -- neither fires.
            # Entry shapes: (cmd, args, target) | ("disabled", cmd) |
            # ("nomatch", seg). m[0] is a dict for a real match and a literal
            # string otherwise, so the membership tests separate them cleanly (a
            # dict never equals "disabled"/"nomatch").
            active   = [m for m in matches if m[0] not in ("disabled", "nomatch")]
            disabled = [m[1] for m in matches if m[0] == "disabled"]
            nomatch  = [m[1] for m in matches if m[0] == "nomatch"]

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
                    log(f"Trailing target propagated: all segments -> {last_target}")

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
                    log("!! Chain aborted: multiple URLs targeting different monitors")
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

                # Misheard/unmatched segments: toast each so the user sees which
                # part of the chain didn't fire, then carry on (partial exec).
                for seg in nomatch:
                    _notify_nomatch(seg, gui_env)

                if active:
                    print(f"[commands] Chained {len(active)} commands")
                    log(f"Chaining {len(active)} commands")

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
                            log(f"Target monitor: {target}")
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
            "match_threshold": DEFAULT_THRESHOLD,
            "notifications": True,
            "auto_pause_media": True,
            "whisper_model": "base.en",
            "whisper_server_port": 8910,
            "whisper_vad_tail_ms": 400,
            # open_mic / close_mic ship inside _default_commands() now -- no
            # separate top-level open_mic_phrases / close_mic_phrases keys.
            "commands": _default_commands() + [dict(p) for p in PINNED_SLOT],
            "wake_words": ["hey dude"],
            "monitors": {},
            "overrides": [],
        }
        json.dump(config, sys.stdout, indent=2)
        sys.stdout.write("\n")
        sys.exit(0)

    sys.stderr.write("Usage: python -m core.commands --emit-defaults\n")
    sys.exit(2)
