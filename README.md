# Voice Commander

Offline voice control for KDE Plasma 6 on Wayland. Say a wake word,
say a command. Launch apps, open URLs, control volume and media,
fling windows between monitors, run arbitrary shell commands.

No cloud. No always-listening daemon phoning home. No telemetry.
Speech recognition runs locally via [Vosk][vosk]; the wake-word
detector ignores everything else until it hears its name.

Voice Commander runs as a `systemd --user` service that starts at
login, restarts on failure, and bounces cleanly with
`systemctl --user restart voice-commander`.

## Requirements

- **KDE Plasma 6** on a **Wayland session**
- **Python 3.11+**
- **Arch- or Debian-based distro** (Arch / CachyOS / Manjaro,
  Debian 13+ / Kubuntu 24.04+). Others probably work but aren't
  tested.
- A working microphone

The installer checks every prerequisite up front and prints the
exact `pacman` or `apt` command for anything missing &mdash; you'll
know what's broken before any state changes on your system.

## Install

```bash
git clone https://github.com/Trixles/Voice-Commander.git
cd Voice-Commander
./install.sh
```

What `install.sh` does:

1. Detects your distro and verifies every required binary is on `PATH`.
2. Copies the app to `~/.local/share/voice-commander/app/`.
3. Builds a Python venv at `~/.local/share/voice-commander/venv/`
   and installs `requirements.txt`.
4. Downloads the Vosk small-English model (~40 MB) to
   `~/.local/share/voice-commander/vosk-model/`.
5. Installs the KWin window-placer helper script.
6. Generates `~/.config/systemd/user/voice-commander.service`.
7. Drops a `voice-commander` launcher at `~/.local/bin/`.
8. Enables the service. If you're re-installing over a running
   instance, it restarts the service so you immediately get the new
   code.

When it finishes, a tray icon appears in your system tray. Say your
wake word (default: **"computer"**) followed by a command.

## First five minutes

The tray icon colour tells you what state the listener is in:

- **Blue** &mdash; sleeping, listening for the wake word
- **Green** &mdash; heard the wake word, listening for a command
- **Red** &mdash; open-mic mode (no wake word needed)
- **Amber** &mdash; mic isn't ready (USB unplug, suspend, etc.)

Right-click the tray icon for the settings dialog. Or say
"computer, open settings."

Try a few defaults to verify it's working:

- "computer, open browser"
- "computer, volume up" / "computer, volume down"
- "computer, pause" / "computer, resume" &mdash; works with any
  MPRIS-aware player (Spotify, browsers, mpv, VLC)
- "computer, open mic" &rarr; say commands without the wake word
  &rarr; "close mic" when done

## What ships out of the box

21 default commands out of the box (the last two are slot-phrase forms):

| Command          | Phrases                                | What it does                |
|------------------|----------------------------------------|-----------------------------|
| Open Browser     | "open browser", "launch browser"       | Launches your browser       |
| Open Reddit      | "open reddit"                          | Opens reddit.com            |
| Open ReadMe      | "open readme"                          | Opens this file             |
| Volume Up        | "volume up", "turn it up"              | +10% volume                 |
| Volume Down      | "volume down", "turn it down"          | &minus;10% volume           |
| Mute / Unmute    | "mute", "unmute"                       | Toggles the default sink    |
| Pause Media      | "pause", "pause media"                 | MPRIS pause                 |
| Resume Media     | "resume", "play", "resume media"       | MPRIS play                  |
| Move Window L/R  | "move left", "move right"              | Move to next monitor        |
| Maximize Window  | "maximize window"                      | Maximizes focused window    |
| Close Window     | "close window"                         | Closes focused window       |
| Open Mic         | "open mic", "open microphone"          | Enter open-mic mode         |
| Close Mic        | "close mic", "close microphone"        | Exit open-mic mode          |
| Open Settings    | "open settings"                        | Opens this dialog           |
| Shutdown         | "shut down", "shutdown", "power off"   | (confirmation required)     |
| Restart          | "restart", "reboot"                    | (confirmation required)     |
| Logout           | "log out", "logout"                    | (confirmation required)     |
| Set Volume       | "set volume to {level}"                | Slot &mdash; specific level |
| Move to Alias    | "move to {alias}"                      | Slot &mdash; named monitor  |

Wake words default to **"computer"** and **"hey dude"**. Add your
own in settings.

## The settings dialog

Right-click tray &rarr; Settings. Six tabs:

1. **Commands** &mdash; the main UI. Add, edit, delete commands. Each
   one has a display name, an action, one or more trigger phrases,
   and any action-specific arguments. The wake word field sits at the
   top of this tab.
2. **Overrides** &mdash; pre-match string rewrites that fix consistent
   Vosk mishearings. See "Fixing mishearings" below.
3. **Displays** &mdash; per-monitor aliases. So you can say "move to
   TV" instead of "move to HDMI-A-2." Friendly monitor names come
   from EDID where available (parsed via `edid-decode`), with the
   port name as fallback.
4. **Model** &mdash; path to the Vosk model. Leave the default unless
   you've downloaded a different one.
5. **Log** &mdash; live tail of the listener, colour-coded by
   category. Indispensable for figuring out what Vosk is *actually*
   hearing when a command doesn't fire.
6. **About** &mdash; quick reference for first-time users.

The phrases that enter and leave open-mic mode are now the **Open mic**
and **Close mic** system commands on the Commands tab (left-clicking the
tray icon toggles it too).

The bottom bar has **Save**, **Restore Defaults**, and **Exit**.
Save is dirty-tracked &mdash; it stays disabled until you've actually
changed something. Restore Defaults is per-tab; it resets only the
active tab and skips tabs that have no defaults (Model, Log, About).

Most edits hot-reload without a service restart. Two settings need a
restart to take effect: wake words and the Vosk model path (both are
loaded at startup). When those change, Voice Commander handles the
restart for you on Save &mdash; nothing for you to do.

## Wake words and open-mic mode

After Voice Commander hears a wake word, it listens for a command for
the next 5 seconds (configurable). If nothing matches, it goes back
to sleep.

For a hands-free session, say "computer, open mic" and Voice
Commander matches every command against your set until you say
"close mic." Useful when you're stringing many commands together
without wanting to repeat the wake word every time.

The wake word detector accepts multiple wake words simultaneously
&mdash; "computer" and "hey dude" both work out of the box.

## Slot phrases

Slots let one command match many inputs. The default set includes
two:

- **`set volume to {level}`** matches "set volume to fifty",
  "set it to twenty," and so on. The number-word in the slot is
  resolved to a percentage.
- **`move to {alias}`** matches the alias against your configured
  monitor aliases.

You can write your own slot phrases. Curly-brace tokens get
fuzzy-matched against the slot's value pool. Trailing slots use
greedy regex (eat to end-of-line); mid-phrase slots use lazy
matching (eat as little as possible).

## Command chaining

You can stack commands in one breath:

```
computer, open reddit and open youtube
```

The split is deliberately aggressive &mdash; Voice Commander
splits on `" and "`, `" an "`, and `" in "` &mdash; because Vosk
routinely transcribes "and" as one of those. The false-positive
defense is that *every* segment of a candidate chain has to match
an existing command. If even one segment doesn't, the whole chain
is rejected and the matcher falls through to normal (non-chained)
handling.

Chains also support a trailing target that distributes across all
segments:

```
computer, open reddit and open youtube on TV
```

&rarr; both URLs open on the monitor aliased to "TV." If different
segments name *different* monitors, the chain is rejected entirely
rather than fired with unpredictable results.

## Fixing Vosk mishearings (Overrides)

Vosk's small model is fast and offline, but it mishears predictably.
If it consistently transcribes "cause" when you say "close," you have
two ways to fix it:

1. **Add the mishearing as an alternate phrase** on the affected
   command (Settings &raquo; Commands &raquo; that command's Options).
   Right for one-off mishearings on a single command.
2. **Add an override rule** in Settings &raquo; Overrides. Right when
   the mishearing affects many commands at once &mdash; e.g. "cause"
   &rarr; "close" fires *before* any command sees the word, so it
   fixes every command that uses "close" in one shot.

Override rules are plain whole-word string rewrites. A rule for "in"
won't corrupt "open." Replacements can be empty for filler-word
deletion. Rules apply in list order; first match wins per overlapping
pattern.

Six defaults ship locked (visible but uneditable):

- "moved to" &rarr; "move to"
- "up and" / "hope in" / "oh been" &rarr; "open"
- "cause" &rarr; "close"
- "mike" &rarr; "mic"

Your own rules sit above the defaults and run first.

## Confirmation flow

Destructive commands (shutdown, restart, logout) don't fire on the
trigger phrase alone. Voice Commander prompts and waits for one of:

- **Confirm:** "confirm", "yes", "do it"
- **Cancel:** "cancel", "never mind", "abort"

After 5 seconds with no response, the command cancels itself.

## Multi-monitor

Monitor placement is handled in two layers, because moving an open
window and placing a new one need different mechanisms in Plasma:

1. **Moving an open window** uses KDE's built-in "Window to Screen N"
   shortcut. Voice Commander invokes the shortcut by *name* over
   `org.kde.kglobalaccel`. Each "Window to Screen N" shortcut you
   want to use needs an active keybinding in System Settings &raquo;
   Shortcuts &raquo; KWin. The key combo itself can be anything; only
   the name matters &mdash; but the binding has to exist.
2. **Opening a new window on a specific monitor** uses a KWin helper
   script (`vc-window-placer`) installed at
   `~/.local/share/kwin/scripts/`. Voice Commander writes the target
   output into `kwinrc` and reloads the script via D-Bus
   (`Scripting.unloadScript` then `loadScript`).

That reload dance is unusual but necessary: in Plasma 6,
`org.kde.KWin.reconfigure` only refreshes KWin's *own* configuration.
It does **not** re-execute user scripts. A full unload/load cycle is
the only way to make sure the placer sees the value you just wrote.

## ReSpeaker XVF3800 (optional)

If you're using a ReSpeaker XVF3800 mic array, Voice Commander will
drive its LED ring to reflect listener state &mdash; the colour
follows the tray-icon colour. The installer auto-detects the
`xvf_host` binary and enables LED support if present; absence is
fine and disables the feature silently.

## CLI

```bash
voice-commander --version    # or -V
voice-commander              # run in the foreground (rare; systemd handles this)
```

`--version` answers without loading Qt or the Vosk model, so it
returns instantly.

## Common operations

```bash
# Status
systemctl --user status voice-commander

# Follow logs
journalctl --user -u voice-commander -f

# Restart (e.g. after editing code or config that doesn't hot-reload)
systemctl --user restart voice-commander

# Stop temporarily
systemctl --user stop voice-commander

# Re-run installer (safe; preserves your commands.json)
./install.sh

# Uninstall (preserves config)
./uninstall.sh

# Uninstall everything including config
./uninstall.sh --purge
```

## Configuration file

User config lives at `~/.config/voice-commander/commands.json`. The
settings dialog is the supported way to edit it &mdash; direct edits
work, but they're unvalidated, so one bad comma and you're debugging
JSON.

The file is hot-reloaded on write, so most edits take effect without
a service restart.

## Security

The `run_command` action runs arbitrary shell commands. **Voice
commands you configure to use `run_command` have your full shell
privileges.** A voice command set up to run `rm -rf ~/` will in fact
`rm -rf ~/` if it fires.

This is deliberate. Voice Commander assumes you can be trusted with
your own system, the same way `cron` and your `~/.bashrc` do.
Treat `run_command` voice commands the same way you'd treat any
script you're about to execute &mdash; check the args, especially
when copying from other people's configurations.

## Known issues

- **Firefox-derivatives ignore monitor placement on launch.**
  Firefox, Waterfox, and LibreWolf restore their *own* remembered
  window geometry on startup, overriding KWin's placement
  instruction. Workaround: open the browser first, then say "move
  to {monitor}" to relocate the already-open window. Not specific to
  Voice Commander; affects any tool trying to place these browsers.

## Troubleshooting

**Tray icon doesn't appear.** Service probably isn't running. Check
`systemctl --user status voice-commander` and the last lines of
`journalctl --user -u voice-commander -n 50`.

**Wake word not being detected.** Open Settings &raquo; Log and watch
what Vosk transcribes when you say it. The small model can hear
"computer" as "compute her," "computers," "the computer," etc. Two
fixes: add the common mishearings as alternate wake words, or as
override rules that rewrite them back to "computer."

**"Move to monitor X" doesn't move anything.** System Settings &raquo;
Shortcuts &raquo; KWin needs an active keybinding for each "Window
to Screen N" shortcut you want to use. Voice Commander triggers the
shortcut by name, so the actual key combo can be anything &mdash; but
the binding has to be set.

**Newly-opened windows land on the wrong monitor.** Open the Log
tab and check the "Target monitor" line for that command &mdash;
that's the value Voice Commander resolved from your speech. If it's
wrong, the issue is on the recognition side (alias mishearing); add
an override or alternate phrase. If it's right but the window still
lands wrong, you may have hit the Firefox-derivative limitation
above.

**Mic stops working.** Voice Commander auto-detects mic changes and
restarts the audio pipeline. If the tray icon goes amber and stays
there, your default source isn't producing data. Check
`pactl get-default-source` and `pactl list sources short` to confirm
the source exists and is usable.

## Developing

```bash
# Run the test suite (Qt / Vosk / subprocess-free)
pytest -q
```

The tests live at `tests/test_matcher.py`. They hook the matcher
pipeline via `monkeypatch` and assert on what `_dispatch` would have
been called with, so chain logic, slot extraction, and tail-rescore
behaviour are observable without spinning up the GUI.

To test code changes against your running setup, edit the repo and
re-run `./install.sh`. The installer is idempotent, preserves your
`commands.json`, and restarts the running service so you get the new
code immediately. Editing the deployed copy directly under
`~/.local/share/voice-commander/app/` works too but it diverges from
the repo &mdash; not recommended.

Architecture and design invariants live in `ARCHITECTURE.md`.

## License

[MIT](LICENSE).

## Acknowledgements

- [Vosk][vosk] for offline speech recognition that actually works.
- [PySide6][pyside] for the Qt bindings.
- The KDE Plasma 6 team for the D-Bus and KWin scripting surfaces
  that make this kind of thing possible.

[vosk]:   https://alphacephei.com/vosk/
[pyside]: https://wiki.qt.io/Qt_for_Python
