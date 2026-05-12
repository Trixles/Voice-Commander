# Voice Commander

A local, offline voice command system for KDE Plasma 6 on Wayland.

Voice Commander listens for a wake word, then matches what you say against
a configurable command set. Commands can launch applications, open URLs,
control system volume and media, manage windows across monitors, run
arbitrary shell commands, and more.

All speech recognition happens locally using [Vosk][vosk]. Nothing leaves
your machine.

## Features

- **Wake-word activation.** Say "computer" (or any configured word) and
  speak your command.
- **Configurable commands** via an in-app settings dialog. No JSON editing
  required.
- **Slot-based phrases.** `set volume to fifty`, `move to right monitor`
  &mdash; the variable parts are extracted and fuzzy-matched against known
  values.
- **Command chaining.** `open reddit and open youtube` dispatches both.
- **Confirmation flow** for destructive commands like shutdown, restart,
  and logout.
- **Open mic mode** for hands-free use without repeating the wake word.
- **Multi-monitor support.** "move to TV", "open reddit on left monitor".
  Aliases are configured per-monitor in the settings UI.
- **System tray integration** with state-coloured icons.
- **Optional ReSpeaker XVF3800 LED ring** support for visual state
  feedback on the mic itself.
- **Runs as a systemd user service.** Starts at login, restarts on
  failure, logs to journald.

## Requirements

- **KDE Plasma 6** on **Wayland**
- **Python 3.11+**
- An Arch-based or Debian-based distribution (Arch, CachyOS, Manjaro,
  Debian 13+, Kubuntu 24.04+). Other distros may work but are not tested.
- A working microphone

The installer will detect missing dependencies and tell you exactly what
to install for your distro.

## Installation

```fish
git clone https://github.com/Trixles/Voice-Commander.git
cd Voice-Commander
./install.sh
```

The installer will:

1. Check that all required system dependencies are present (refuses to
   continue if anything is missing &mdash; you'll get exact `pacman` or
   `apt` commands to copy-paste).
2. Copy the application to `~/.local/share/voice-commander/`.
3. Create a Python virtual environment and install Python dependencies.
4. Download the Vosk speech recognition model (~40 MB).
5. Install the KWin window-placer helper script.
6. Generate and install a systemd user service.
7. Install a `voice-commander` launcher into `~/.local/bin/`.
8. Enable and start the service.

After install, the tray icon appears and Voice Commander is listening.
Say your wake word (default: "computer") followed by a command.

## Usage

### Default commands

Out of the box, Voice Commander ships with examples for:

- Opening a browser, opening Reddit
- Volume up / down / set to specific level
- Pause / resume media (via MPRIS &mdash; works with Spotify, browsers,
  most players)
- Move window left / right monitor, maximize, close
- Move to a named monitor (`move to TV`, `move to monitor three`)
- Shut down, restart, log out (with confirmation)
- Open Voice Commander settings

Open the settings dialog (right-click the tray icon &raquo; Settings, or
say "computer, open settings") to add your own commands.

### Wake words and "open mic" mode

The default wake word is "computer." You can configure additional wake
words in settings.

To go hands-free, say "computer, open mic." Voice Commander will then
match commands without requiring the wake word until you say "close mic."

### Commanding-after-the-wake-word window

After hearing the wake word, Voice Commander listens for a command for
5 seconds (configurable). If nothing matches, it goes back to sleep.

### Confirmation flow

Destructive commands (shutdown, restart, logout) require a follow-up
"confirm" or "yes" within 5 seconds. Say "cancel" or "never mind" to
abort.

## Architecture

Voice Commander runs as two cooperating threads:

- **Listener thread:** captures audio via `pw-record`, runs it through
  Vosk's `KaldiRecognizer`, and matches the resulting text against the
  command set.
- **Qt main thread:** owns the system tray icon, settings dialog, and
  state queue.

A KWin script (`vc-window-placer`) handles the one window-management
trick that the rest of the system can't do via global shortcuts: routing
newly-opened windows to a specific monitor. Voice Commander signals it
by writing a key into `kwinrc` and calling KWin reconfigure.

The application runs under a `systemd --user` service so that Wayland
and D-Bus environment variables are reconstructed from `/run/user/<uid>/`
rather than inherited from a desktop session. This makes the service
robust to Plasma crashes / restarts and lets you `systemctl --user
restart voice-commander` to bounce it without logging out.

## Configuration

User configuration lives in `~/.config/voice-commander/commands.json`.

The settings dialog is the supported way to edit it. Direct edits are
allowed but unvalidated &mdash; you can break things with a typo. The
file is hot-reloaded; no service restart is needed for command edits.

### Adding a voice command

Right-click tray icon &raquo; Settings &raquo; Commands tab &raquo; Add.

For each command you configure:

- **Phrases** &mdash; what you say to trigger it. Multiple phrases per
  command are supported.
- **Action** &mdash; what it does: launch an app, open a URL, run a
  shell command, control volume, etc.
- **Args** &mdash; action-specific (the app to launch, the URL to open,
  the shell command to run).

### Adding a monitor alias

Settings &raquo; Displays tab. Aliases for each connected output let you
say "move to TV" instead of "move to HDMI-A-2."

## Security note

The `run_command` action runs arbitrary shell commands. Voice commands
you configure to use `run_command` are full shell access &mdash; treat
them with the same caution you'd give any script you're about to execute.
This is a deliberate design choice: users are trusted to configure their
own system.

## Common operations

```fish
# Check service status
systemctl --user status voice-commander

# Follow logs
journalctl --user -u voice-commander -f

# Restart after editing code or config
systemctl --user restart voice-commander

# Stop temporarily
systemctl --user stop voice-commander

# Re-run installer (safe; preserves your commands.json)
./install.sh

# Uninstall (preserves commands.json)
./uninstall.sh

# Uninstall everything including config
./uninstall.sh --purge
```

## Known issues

- **"Open X on Y" sometimes opens the window on the wrong monitor.**
  Issuing the same voice command twice can produce different monitor
  placements. The Python-side monitor resolution is consistent across
  attempts; the bug lives somewhere downstream in the KWin
  window-placer script. Under investigation.
- **Chained `"open X on Y and open Z on W"` is unreliable.** Likely
  related to the issue above. Chained commands without per-monitor
  targeting (`"open reddit and open youtube"`) work fine.
- **Firefox-derivative browsers ignore monitor placement on launch.**
  See the Troubleshooting section below for details and workaround.

## Troubleshooting

**Tray icon doesn't appear.** Make sure the service is running:
`systemctl --user status voice-commander`. Check the logs:
`journalctl --user -u voice-commander -n 50`.

**"Move to monitor X" doesn't work.** Each "Window to Screen N" shortcut
in KWin (System Settings &raquo; Shortcuts &raquo; KWin) needs an active
keybinding for Voice Commander to invoke it. The shortcut itself can be
any key combination &mdash; Voice Commander triggers it by name, not by
key.

**"Move to monitor X" doesn't move browsers to the right monitor.**
Firefox-derivatives (including Waterfox) restore their own remembered
window geometry on launch, which overrides any placement done by KWin.
This is a known limitation; it's not specific to Voice Commander.

**Wake word not detected.** Vosk's small model can mishear "computer."
Check the Log tab in settings to see what Vosk is transcribing. Add the
common mishearings as alternate wake words.

**Mic stops working.** Voice Commander auto-detects mic changes and
restarts the audio pipeline. If the tray icon goes amber (ERROR) and
stays there, check `pactl get-default-source` and that the source can
produce data.

## License

[MIT](LICENSE). See `LICENSE` for the full text.

## Acknowledgements

- [Vosk][vosk] for the offline speech recognition.
- [PySide6][pyside] for the Qt bindings.
- The KDE Plasma team for the rich shortcut and scripting surfaces that
  make all of this possible.

[vosk]:   https://alphacephei.com/vosk/
[pyside]: https://wiki.qt.io/Qt_for_Python
