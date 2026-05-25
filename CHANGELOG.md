# Changelog

All notable changes to Voice Commander will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.9.0] &mdash; 2026-05-25

### Added
- **New "Options" tab** (renamed from "About"; the first-time-user About
  reference is now a subsection there) gathering three app settings:
  - **Launch on login** &mdash; opt-in autostart. Off by default; turn it on
    to have Voice Commander start with your session.
  - **Enable notifications** &mdash; a single on/off toggle for desktop
    notifications. The shutdown/restart/logout confirmation prompts always
    appear regardless, so a destructive command is never confirmed blind.
  - **Recognition strictness** &mdash; tune how closely speech must match a
    command phrase (the global fuzzy-match threshold; default 0.75), with a
    firm warning about the extremes and Restore Defaults to snap back.
- **Application-menu launcher** &mdash; the installer now drops a `.desktop`
  entry, so Voice Commander appears in your app menu (KDE Kickoff, etc.), not
  only as the `voice-commander` terminal command.
- **Duplicate-phrase guard** &mdash; Settings refuses to save when two
  commands share an exact trigger phrase, naming the conflict so you can fix
  it. (Only the first command in order would ever fire otherwise; a disabled
  command still counts, since it still wins the match.)

### Changed
- **Autostart is now opt-in.** The installer no longer enables the service to
  start at login &mdash; enable it yourself via Options &raquo; "Launch on
  login." (Re-installing still starts/restarts the service for the current
  session.)
- **Consistent fixed-width layout across tabs.** The wake-words field, user
  command rows, and Overrides rows now keep their size and center &mdash; like
  the system command rows &mdash; instead of stretching to fill a wide or
  maximized window. Section dividers are capped to the content width rather
  than spanning the whole window.
- The settings-window and application-menu icon is now the blue Voice
  Commander icon.

## [0.8.0] &mdash; 2026-05-24

### Added
- **"Open mic" and "Close mic" are now system commands** on the Commands
  tab, with editable phrases and an enable/disable toggle like every other
  system command. This replaces the dedicated "Open Mic" tab, whose only job
  was editing those two phrase lists. Toggling a mic command off stops the
  voice phrase (no "disabled" notification fires, since mic is matched in the
  listener rather than by the fuzzy matcher); left-clicking the tray icon
  still enters/exits open-mic mode regardless. Existing configs migrate
  automatically on first load &mdash; your custom mic phrases are preserved,
  and the old `open_mic_phrases` / `close_mic_phrases` keys are folded into
  the commands list.

### Changed
- **System and slot-pinned command rows dropped their redundant "action"
  column.** The action label only ever restated the locked name ("Close
  window" / "Close window"), so it's gone; the name, "Options" button, and
  enable toggle are now centered together as one group, with the per-row
  separator sized to that button cluster rather than spanning the full width.
  User command rows are unchanged.
- **The settings window is translucent only when a compositor blur is actually
  available.** Voice Commander now reads `kwinrc` and makes its window
  translucent only when KWin's Blur effect (or a fork such as Better Blur) is
  enabled; otherwise it paints as a solid opaque panel. Previously the window
  was always translucent and relied on blur to look like frosted glass, so on
  a desktop without blur it appeared see-through to the raw desktop.
- **The "How to Use" tab is now called "About."**
- The default "Open mic" / "Close mic" phrases dropped the "mike" spellings
  ("open mike" / "close mike"); the built-in "mike" &rarr; "mic" override
  already rewrites those.

### Removed
- The dedicated **"Open Mic" tab** &mdash; its phrase editing now lives on the
  Commands tab as the "Open mic" and "Close mic" system commands (above).

## [0.7.0] &mdash; 2026-05-23

### Added
- Per-override enable/disable toggle for the built-in defaults. Each
  shipped default on the Overrides tab now carries a toggle switch
  (grey off / green on), matching the system-command toggles. A
  disabled default is filtered out of the rewrite pipeline entirely,
  freeing both its before- and after-phrases for reuse. Disabled
  defaults persist by pattern in a new `disabled_default_overrides`
  key in `commands.json`; the default rules themselves are never
  written, so the shipped list can grow across versions without stale
  on-disk copies. Absent key = all defaults on, so pre-0.7.0 configs
  keep working untouched.
- "Open VC settings" is now a visible system command at the bottom of
  the System Commands list (it existed before but was hidden). Like
  other system commands its phrases are editable and it can be toggled
  on/off, but not deleted. Its only default phrase is "open voice
  commander settings" &mdash; "open settings" is intentionally left
  free for your OS settings command.

### Changed
- Overrides tab redesigned to mirror the Commands tab: split into
  "User Overrides" and "System Overrides" sections with their own
  headers and a divider line between them, a separator line between
  each row (none beneath the last), and a flush right-edge control
  column &mdash; the user-row delete button was widened to match the
  toggle width so the two columns line up.
- Override precedence is now **user-first**: user rules run before the
  built-in defaults, so a user override wins a same-word conflict with
  a default (matching the tab's top-to-bottom layout). Previously
  defaults ran first and could silently shadow a colliding user rule.
  Users with no custom overrides are unaffected.
- The Overrides tab's "Restore Defaults" now clears user rules **and**
  re-enables every built-in default (a full shipped-state restore),
  instead of only clearing user rules.
- The Commands-tab action dropdown no longer responds to the scroll
  wheel. Scrolling the page while the pointer is over a dropdown can
  no longer silently change a command's action &mdash; click to open
  the dropdown and pick.
- The Save/Restore/Exit button bar lost its top divider line so it
  blends into the framed dialog, mirroring how the tab row meets the
  body at the top.
- `ToggleSwitch` moved from the Commands tab module into
  `core/settings/helpers.py`, shared by the Commands and Overrides
  tabs.

### Fixed
- Log tab scrollbar now shows a visible, draggable handle instead of a
  hollow outline. The read-only log view's inline stylesheet used a
  bare declaration block, which bled its border onto the child
  scrollbar and suppressed the handle fill; scoping it to a
  `QTextEdit { ... }` selector restores the dialog-wide handle style.

### Tests
- New `tests/test_overrides_roundtrip.py`: the disabled-default
  save/load round-trip, user-first precedence (a user rule beats a
  colliding default), and the disable-frees-the-pattern scenario.
  `pytest -q` = 30 passed.

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

[Unreleased]: https://github.com/Trixles/Voice-Commander/compare/v0.8.0...HEAD
[0.8.0]: https://github.com/Trixles/Voice-Commander/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/Trixles/Voice-Commander/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/Trixles/Voice-Commander/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/Trixles/Voice-Commander/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Trixles/Voice-Commander/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Trixles/Voice-Commander/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Trixles/Voice-Commander/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Trixles/Voice-Commander/releases/tag/v0.1.0
