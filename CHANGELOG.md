# Changelog

All notable changes to Voice Commander will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/Trixles/Voice-Commander/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Trixles/Voice-Commander/releases/tag/v0.1.0
