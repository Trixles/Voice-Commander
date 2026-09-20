# Changelog

All notable changes to Voice Commander will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.1] &mdash; 2026-09-20

### Fixed
- **Watching Celery Man no longer trips a spurious wake.** The clip says
  "computer" a second time beyond its launch phrase ("Computer, do we
  have any new sequences&hellip;"), which opened a phantom command window
  and &mdash; with auto-pause on &mdash; paused the video mid-line. A wake
  carried only by a baked-in wake word is now suppressed while the video
  is actually playing: checked against MPRIS reality (never a timer),
  failing open if the probe breaks, and never gating custom wake words.
  The gate's playerctl snapshot is reused by auto-pause, so a wake still
  costs exactly one query.

## [2.0.0] &mdash; 2026-09-20

The whisper rebuild. 2.0.0 replaces the entire speech stack: recognition
moved from Vosk streaming to whisper.cpp, and waking moved to a text match
on the live transcript. The Vosk-era build (v0.2.0&ndash;v1.0.1) is archived
read-only as
[Voice-Commander-Vosk](https://github.com/Trixles/Voice-Commander-Vosk).

### Changed
- **Speech recognition runs on whisper.cpp.** Audio is segmented by Silero
  VAD (a 400 ms pause closes an utterance) and each segment is transcribed
  by a local `whisper-server`, shipped as a second systemd user unit bound
  to the app so it can never outlive it. New config keys: `whisper_model`
  (default `base.en`), `whisper_server_port`, `whisper_vad_tail_ms`.
  Requires the system `whisper-cpp` package.
- **Waking is a text match.** Wake words are matched whole-word against the
  live transcript and acknowledged on partials. "computer" is baked in
  structurally &mdash; always active, never stored in config &mdash; and
  `wake_words` now holds only your custom additions (fresh installs seed
  "hey dude").
- **The Model tab picks a Whisper model.** Browse to any ggml `.bin`
  (with a link to the Hugging Face model files), replacing the Vosk model
  machinery.
- **Shipped default overrides emptied.** The old rewrite rules corrected
  Vosk's specific mishearing profile; whisper starts from a clean slate and
  rules return only if real use earns them. The Overrides tab drops its
  System Overrides section accordingly.
- **Power commands exit through `org.kde.Shutdown`** &mdash; the same D-Bus
  path as KDE's own power controls, so shutdown, restart, and logout get a
  proper session-managed exit.

### Added
- **Auto-pause media on wake.** Whatever is playing pauses the moment a
  wake word lands and resumes when the command window closes
  (`auto_pause_media`, default on; it only resumes what it paused).
- **Open-folder commands.** The open-file action now accepts a directory,
  not just a file.

### Fixed
- The Save button correctly re-disables when edits are reverted back to
  the saved state.

### Removed
- **The Vosk speech backend.** whisper.cpp (via `whisper-server`) is now the
  only recognizer; the `recognizer_backend` and `vosk_model` config keys are
  gone, along with the installer's Vosk model download and the `vosk` Python
  dependency. Existing configs that still carry the old keys load fine —
  unknown keys are ignored, never pruned.
- **The openWakeWord audio wake engine**, including the speaker verifier,
  the wake-audio diagnostic dumps, the vendored `computer_v2` wake model,
  and the `wake_engine` / `wake_model` / `wake_threshold` /
  `wake_vad_threshold` / `wake_verifier` / `wake_audio_dump` /
  `wake_confirm` config keys. Text wake (transcript matching, acknowledged
  on partials) is the wake path. This also drops the openwakeword-only
  Python dependencies (tqdm, scipy, requests, scikit-learn) and the
  `unzip` install requirement.
- **Celery Man left the shipped defaults.** Existing configs keep the
  command and it still dispatches; fresh installs no longer include it.

## [1.0.1] &mdash; 2026-07-16

### Fixed
- **"Open X on {monitor}" intermittently landed the window on the wrong
  display.** The KWin placer issued the correct move every time, but KWin's
  `sendClientToScreen` silently does nothing when called while a window is
  still in its initial setup &mdash; so placement depended on a race against
  how fast the app mapped its window. The placer now checks whether the move
  actually took and, if not, re-asserts it on the window's next geometry
  commit (event-driven; no timers, no added latency, and it stands down the
  moment the window lands so it can never fight a manual drag). Affected all
  apps and chained commands; present since 0.1.0 as the "wrong monitor"
  known issue.

## [1.0.0] &mdash; 2026-07-15

First stable release. The command set, config format, and settings UI are
considered settled; changes from here follow semantic versioning off this
baseline.

### Added
- **Minimize Window system command** &mdash; a new `minimize_window` action
  (KWin "Window Minimize") wired into the action map, defaults, and
  notifications. The window-command group is ordered move &rarr; minimize
  &rarr; maximize &rarr; close.
- **Config self-heal.** Loading an existing `commands.json` now merges in any
  system commands shipped since it was written, matched by name, so upgrading
  gains new built-in commands without a destructive Restore Defaults. Your
  edits and disabled states are preserved.
- **"Celery Man" system command** &mdash; an easter-egg command (last in the
  System list) that opens a baked-in URL. Because the clip repeatedly says
  "computer," it selectively mutes the "computer" wake word for 100 seconds
  while it plays; every other wake word keeps working, and the mute is
  time-based so it restores itself.

### Changed
- **Responsive wake feedback.** The LED, tray, and "Listening…" acknowledgement
  now fire the moment the wake word lands in a Vosk partial (~1.5s earlier than
  the old end-of-utterance path), so the app feels like it's listening as you
  speak. The command window is now an inactivity timer that partials keep alive,
  so a long chained phrase can't fall asleep mid-sentence. A wake-word-only
  utterance keeps listening instead of toasting, and a total miss toasts exactly
  once (the old double "No match" is gone).
- **Whole-word wake matching.** Wake words now match on word boundaries, so
  "computer" wakes the app but "computerized" does not.
- **Cross-command duplicate phrases now auto-resolve instead of blocking the
  save.** When you assign a phrase already owned by another command, it's
  stripped from the newcomer and left with the established owner (with a notice),
  rather than refusing to save. Within-command duplicates and characters that
  can't be spoken (anything outside letters, digits, spaces, and commas) are
  likewise stripped on save with a warning naming each affected command.
  Slot-pinned `{alias}` rows are left untouched.
- **Frosted settings window.** The translucent build now paints a single frosted
  backing panel with every structural surface above it transparent, eliminating
  the compounded near-opaque stacking and the alpha-0 holes (tab-bar gaps, button
  bar) that previously showed raw wallpaper. Inactive tabs stay solid so the
  active tab is obvious. All save-blocking dialogs are now frosted panels rather
  than `QMessageBox`, which couldn't paint a background under the translucent
  window (fixing the see-through look and a text-clipping sizing bug). The opaque
  no-blur build is unchanged.
- **Settings-tab help text** rewritten across Commands, Overrides, Displays, Log,
  and Options for clarity, including a note on `and`-chaining and its known
  limitations in the User Commands blurb.

### Fixed
- **"Move to {monitor}" landed windows on the wrong display.** The move path
  resolved a monitor alias to a numeric KWin screen index, which matches neither
  KWin's own numbering nor survives a monitor hotplug. Both monitor-routing paths
  (arriving windows and the active window) now converge on output **names**
  through the `vc-window-placer`, which resolves the live screen by name.
- **Near-homophones fired the wrong single-word command** (e.g. "cause" &rarr;
  Media Pause). A one-letter difference still scores ~0.80 under the fuzzy
  matcher, clearing the 0.75 default. Single-token command phrases now hard-reject
  below a 0.85 floor; the effective bar is `max(your strictness setting, 0.85)`,
  so it never loosens below what you've chosen. Multi-word phrases are unaffected.
- **Three crash/robustness bugs found in a pre-1.0 edge-case audit:**
  - A slot name that isn't a valid regex group (containing a space, hyphen, dot,
    leading digit, etc.) no longer crashes the listener thread with a regex error
    &mdash; which killed voice control and re-crashed on restart since the phrase
    persists in config.
  - A stray-braced phrase (e.g. `open {x}`) on a command that declares no slots
    can no longer hard-score a perfect match and shadow every other "open …"
    command; it falls back to literal matching and simply misses.
  - A corrupt `commands.json` now raises a clean `ConfigError` and is never
    overwritten. Startup exits cleanly; a hot-reload keeps the last-good config
    instead of letting the parse error kill the listener.

### Internal
- Pre-1.0 cleanup pass (net &minus;102 LOC): de-duplicated the default-source
  helper into `core/run.py`, dropped the pre-0.8.0 mic-key migration shim,
  collapsed the notification builder into a dispatch table, and merged three
  byte-identical CONFIRMING-entry blocks in the listener. Behavior-preserving.
- Repo now tracks `CLAUDE.md` and `.claude/` per Anthropic's shared-config
  convention (`HANDOFF.md` and worktrees stay gitignored).
- Test suite grew to 120 passing; several new ARCHITECTURE.md invariants
  document the wake, slot, config-load, and monitor-routing behavior above.

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

[Unreleased]: https://github.com/Trixles/Voice-Commander/compare/v1.0.1...HEAD
[1.0.1]: https://github.com/Trixles/Voice-Commander/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/Trixles/Voice-Commander/compare/v0.9.0...v1.0.0
[0.9.0]: https://github.com/Trixles/Voice-Commander/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/Trixles/Voice-Commander/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/Trixles/Voice-Commander/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/Trixles/Voice-Commander/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/Trixles/Voice-Commander/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Trixles/Voice-Commander/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/Trixles/Voice-Commander/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Trixles/Voice-Commander/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Trixles/Voice-Commander/releases/tag/v0.1.0
