# Changelog

All notable changes to Voice Commander will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.0] &mdash; 2026-05-23

### Added
- Per-command enable/disable toggle. Every system and slot-pinned
  command row on the Commands tab now carries a toggle switch
  (grey off / green on). A disabled command is still recognized by
  the matcher but, instead of running, pops a
  "&lt;Command&gt; is disabled in Settings" notification and skips
  dispatch. Disabled state is matched-but-not-dispatched on purpose:
  scoring ignores the flag, so an enabled lower-scoring command can't
  silently shadow a disabled higher-scoring one. The state persists
  in `commands.json` via a new `enabled` field &mdash; absent means
  enabled, so pre-0.6.0 configs keep working untouched.
- Single-instance guard. Launching `voice-commander` from a terminal
  while the systemd user service is already running now prints
  "Already running" and exits, instead of spawning a duplicate tray
  icon on top of the service. The lock (a per-UID `QLocalServer`) is
  acquired before the Vosk model loads, so the duplicate bails
  instantly.

### Changed
- Commands tab split into "User Commands" and "System Commands"
  sections, each with its own header. System commands render in a
  fixed order, and the bottom-most system row has no separator line
  beneath it.
- Slot-pinned commands (e.g. "Move window to monitor") are folded
  into the System Commands section as expandable, collapse-by-default
  rows with an Options button and read-only phrase bodies, rather
  than living in a separate always-at-bottom tier. The command-row
  layout is now two tiers (user / system) instead of three.
- In a chained command, a disabled segment notifies and is skipped
  but does **not** abort the chain &mdash; the remaining valid
  segments still fire. Confirm/cooldown abort rules still take
  precedence.
- Display label "Move to monitor" renamed to "Move window to monitor"
  for consistency with its action key; Mute/Unmute action labels
  added. The underlying slug (`move_to_monitor`) and action key
  (`move_window_to_monitor`) are unchanged.
- Save/Restore/Exit button bar gained a border to match the framed
  dialog.
- Tests: +9 matcher cases covering the disabled single-match, chain,
  and slot-pinned paths plus pre-0.6.0 back-compat synthesis, and a
  new headless `tests/test_settings_roundtrip.py` covering the
  `enabled` save/load round-trip. `pytest -q` = 27 passed.

## [0.5.0] &mdash; 2026-05-22

### Added
- `voice-commander --version` (also `-V`) CLI flag. Prints the version
  string and exits without loading Qt or the Vosk model, so it's
  instant. The version is also surfaced on the About tab of the
  settings dialog.
- Matcher unit test suite (`tests/test_matcher.py`) covering scoring,
  slot extraction, the tail-rescore guard, and chain dispatch. Qt /
  Vosk / subprocess-free; runs from the repo root with `pytest -q`
  in well under a second.

### Changed
- Internal refactor: matching pipeline lifted from `core/commands.py`
  into `core/matcher.py`. The pure scoring helpers
  (`_has_slots`, `_phrase_to_regex`, `_extract_slots`,
  `_resolve_slot`, `_match_non_slot`) and `TAIL_THRESHOLD` now live
  in the matcher module, which is registry-free and unit-testable
  in isolation. The orchestrator `_score_segment` stays in
  `commands.py` next to the command registry it walks.
- Internal refactor: all subprocess invocations now go through a new
  `core/run.py` wrapper module (`run_bg` for fire-and-forget,
  `run_capture` for run-and-wait). Two intentional exceptions stay
  raw with explanatory comments: the listener's streaming
  `pw-record` process and the standalone `core/notify.py` module.
- Internal refactor: settings UI split out of the single 2300-line
  `core/settings.py` into a `core/settings/` package &mdash; `dialog.py`,
  `helpers.py`, `style.py`, and one module per tab under
  `core/settings/tabs/`. No user-visible change.
- Internal refactor: small leaf modules (log buffer, desktop-app
  scanner, notification helper, path constants, matcher stub) lifted
  out of `core/commands.py` and `core/settings.py` into their own
  files. Default phrase lists centralized in `core/commands.py`. No
  user-visible change.
- Chain segment matching extracted into a named helper
  `_match_chain_segments()` and the chain-block comment in
  `try_match()` rewritten to spell out that the aggressive
  `" and | an | in "` split pattern is deliberate &mdash; the
  per-segment match requirement is the false-positive defense.
- Per-comparison matcher debug output (`'X' vs 'Y': 0.62 ...`) is
  now gated on the `VC_DEBUG_MATCHER=1` environment variable.
  Off by default; `journalctl -u voice-commander` is dramatically
  quieter. Set `Environment=VC_DEBUG_MATCHER=1` in the systemd unit
  to debug specific (mis)matches.
- `install.sh` now restarts the service when re-installed over a
  running instance. Previously the existing process would keep
  running on the old code until you manually restarted it &mdash;
  an easy footgun while iterating on the source.

### Fixed
- Latent `NameError` in chained-command dispatch: the per-segment
  "Target monitor" log call referenced an undefined `_log` instead
  of `LOG_BUFFER`. Would have raised the moment a chained command
  carried an `on {alias}` target. Now uses `LOG_BUFFER` like every
  other call in the file.
- Listener's command-queue drain caught `Exception` instead of
  `queue.Empty`, silently swallowing any real bug in the drain
  loop. Now tightened to `queue.Empty` only.
- `open_file` action's "file not found" notification went through
  a raw `notify-send` call that did not pass `gui_env`, almost
  certainly silently failing under the systemd user service.
  Now routes through `core.notify.notify()` like every other
  notification.
- App picker dialog (Settings &raquo; Commands &raquo; Launch app)
  rendered with an opaque corner background that didn't match the
  rest of the settings UI. Now translucent like its parent dialog.
- ReSpeaker XVF3800 LED ring could get stuck on the wrong color after
  the mic was unplugged and replugged &mdash; the device reboots into
  its own default LED state with no way to know Voice Commander's. The
  tray now re-asserts the current LED state every 3 seconds, so the
  ring recovers on its own. `xvf_host` discovery also falls back to
  common install paths (`~/.local/bin`, `/usr/local/bin`, `/usr/bin`)
  since the systemd user service doesn't inherit your shell `PATH`,
  and a 5ms delay between LED commands keeps the firmware from dropping
  a color change. When the required udev rule for the device is
  missing, `install.sh` now detects it and prints a copy-pasteable
  command to install it.

## [0.4.0] &mdash; 2026-05-16

### Added
- **Overrides:** user-editable Vosk mishearing rewrites applied before
  matching. Configure in Settings &raquo; Overrides. Six built-in
  defaults ship with the app (locked, undeletable): "moved to" &rarr;
  "move to"; "up and", "hope in", "oh been" &rarr; "open"; "cause"
  &rarr; "close"; "mike" &rarr; "mic". User rules support whole-word
  matching, first-match-wins ordering, and empty-replacement
  filler-word deletion.
- Restore Defaults button is now per-tab (Commands, Overrides,
  Displays, Open Mic). The bottom-bar button dispatches to whichever
  tab is active; disabled with explanation on tabs with no defaults
  (Model, Log, How to Use).
- Save button is now dirty-tracked: starts disabled, enables only when
  a setting has changed.

### Changed
- Vosk normalization layer split. Hardcoded mishearing rewrites moved
  out of `core/listener.py._normalize()` into the new override system.
  Only the "the " prefix hallucination filter remains in `_normalize()`
  as plumbing (microphone AGC artifact, not a transcription mistake).
- Settings dialog gains a 7th tab ("Overrides"); tab order is
  Commands, Overrides, Displays, Open Mic, Model, Log, How to Use.

## [0.3.0] &mdash; 2026-05-16

### Added
- Mute and unmute system commands (default phrases: "mute", "unmute",
  plus "audio"/"volume" variants). Uses `pactl set-sink-mute` on the
  default sink.
- Friendly monitor names in the Displays tab, parsed from EDID via
  `edid-decode`. Falls back to port name when no display product name
  is present in the descriptor.
- `ARCHITECTURE.md` &mdash; durable invariants extracted from
  `HANDOFF.md` and committed to the repo. Internal documentation.

### Changed
- Non-slot fuzzy matcher now applies a tail-rescore guard: when the
  heard text and a candidate phrase share their leading word
  (e.g. `open ...`), the remainder of each is scored separately and
  must clear a secondary threshold. Stops shared verb prefixes from
  carrying structurally weak matches above the main threshold.

## [0.2.0] &mdash; 2026-05-15

### Fixed
- KWin window-placer script is now reloaded via the `Scripting` D-Bus
  interface (`unloadScript` then `loadScript`) instead of
  `org.kde.KWin.reconfigure`. In current Plasma 6, `reconfigure` only
  refreshes KWin's own cached config and does not re-execute user
  scripts &mdash; the placer was reading a stale `nextScreen` value
  (or none at all), causing `open X on Y` to land windows on the
  wrong monitor.
- Chained `open X on Y and open Z on W` placement now uses
  `output:wm_class` queue entries instead of bare output names.
  Previously the placer matched windows in the order they mapped to
  the screen, which races with how fast each app spawns its window;
  class-tagging makes the match order-independent.
- URL chains targeting different monitors are now rejected at the
  parser level rather than fired with unpredictable results.
- Two small fixes to the service startup path.

### Changed
- Aliases (default monitor aliases, slot-pinned command definitions,
  number-word mappings) consolidated into `core/aliases.py`. Internal
  refactor; no user-visible behaviour change.

## [0.1.0] &mdash; 2026-05-10

Initial public release.

### Added
- Offline voice command recognition via Vosk (small English model).
- Wake-word activation with support for multiple wake words.
- State machine: SLEEPING, LISTENING, CONFIRMING, OPEN_MIC, ERROR.
- Configurable command system with fuzzy phrase matching.
- Slot-based parameterized phrases (e.g. `set volume to {level}`,
  `move to {monitor}`).
- Command chaining (e.g. `open reddit and open youtube`).
- Confirmation flow for destructive commands (shutdown, restart, logout).
- Open mic mode for hands-free use.
- Multi-monitor window management with user-configurable monitor aliases.
- KWin helper script (`vc-window-placer`) for routing newly-opened
  windows to specific monitors.
- Qt settings dialog with tabs for Commands, Displays, Open Mic, About,
  and Log.
- System tray icon with state-coloured indicators.
- Optional LED control for the ReSpeaker XVF3800 mic array.
- Hot-reload of `commands.json` without restart.
- Action types: launch_app, open_url, open_file, set_volume, volume_up /
  down, media_pause / resume, move_window_to_monitor, move_window_left /
  right, maximize_window, close_window, run_command, open_settings,
  shutdown, restart, logout.
- Installer (`install.sh`) with distro detection (Arch / Debian),
  prerequisite checking, Vosk model auto-download, venv creation, and
  systemd user service generation.
- Uninstaller (`uninstall.sh`) with optional `--purge` mode that also
  removes user config.

### Known issues
- **"Open X on Y" sometimes places the window on the wrong monitor.**
  Identical voice input can produce different monitor placements across
  attempts. The target monitor resolved by the Python side is consistent;
  the misplacement happens downstream in the KWin window-placer script.
  Root cause not yet identified &mdash; suspected race between the
  `kwinrc` write and KWin's window-added signal, or queue desync in the
  KWin script. Single-monitor moves of already-open windows are unaffected.
- **Chained `"open X on Y and open Z on W"` is unreliable.** Likely the
  same underlying issue as above. Chained commands without per-monitor
  targeting (e.g. `"open reddit and open youtube"`) work fine.
- **Firefox-derivative browsers ignore monitor placement on launch.**
  Waterfox, Firefox, and LibreWolf restore their own window geometry on
  startup, overriding KWin's placement. Not a Voice Commander bug; it
  affects any external tool trying to place these browsers. Workaround:
  open the browser first, then use `"move to {monitor}"` to relocate it.

[Unreleased]: https://github.com/Trixles/Voice-Commander/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/Trixles/Voice-Commander/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Trixles/Voice-Commander/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Trixles/Voice-Commander/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Trixles/Voice-Commander/releases/tag/v0.1.0
