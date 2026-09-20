# Voice Commander

Hands-free voice control for KDE Plasma 6 on Wayland — and every word of it
stays on your machine.

Say a wake word, speak a command: launch apps, open sites, throw windows onto
the monitor you name, ride volume and media, or run anything else you've
taught it. Speech recognition happens locally through
[whisper.cpp](https://github.com/ggml-org/whisper.cpp). No cloud, no accounts,
no API keys, no network dependency once installed.

![Voice Commander settings window](assets/screenshot.png)

## Highlights

- **Fully local.** Audio goes to a `whisper-server` process on localhost;
  transcripts go to a log you can watch in the settings window. Nothing is
  sent anywhere, ever.
- **Wake word without training.** Waking is a whole-word match on the live
  transcript — say "computer" and it's listening. Any phrase Whisper can hear
  works as a custom wake word, straight out of the box.
- **One breath or two.** "Computer" … "open reddit" works. So does
  "computer open reddit" in a single utterance.
- **Command chaining.** "Open dolphin and open kate on the left monitor"
  runs both — and puts both where you said.
- **Multi-monitor aware.** A bundled KWin script places windows by output.
  Monitors get friendly names read from their EDID, plus any aliases you
  like ("tv", "left monitor").
- **Media-friendly.** Whatever's playing auto-pauses the moment you wake it
  and resumes when the command window closes.
- **Voice-confirmed power commands.** Shutdown, restart, and logout make you
  say "confirm" before anything happens.
- **A real settings UI.** Tray icon plus a full settings window: edit
  commands and phrases, add wake words, alias monitors, correct chronic
  mishearings, watch the live transcript.

## How it works

PipeWire captures the mic → Silero VAD slices the stream into utterances (a
400 ms pause closes a segment) → each segment is transcribed by a local
`whisper-server` (whisper.cpp, `base.en` by default) → the transcript is
checked for wake words and matched against your commands with a tunable
similarity threshold. Matched commands dispatch through KWin scripting,
D-Bus, `playerctl`, `pactl`, and friends.

Voice Commander runs as a systemd user service with a tray icon.
`whisper-server` runs as a second user unit bound to the first — it starts
with the app, stops with the app, and can never outlive it.

## Requirements

- **KDE Plasma 6 on Wayland.** Window placement, power commands, and the
  tray are deliberately Plasma-specific.
- **PipeWire** for audio capture.
- **whisper.cpp** installed system-wide — the `whisper-server` binary
  (package `whisper-cpp` on Arch-family distros).
- **Python 3.11+** and **systemd** (user units).
- Assorted CLI tools (`playerctl`, `pactl`, `kscreen-doctor`,
  `notify-send`, `kwriteconfig6`, `dbus-send`, `xdg-open`, `curl`) — the
  installer checks every one and names the missing package, with Arch and
  Debian package names built in.

A GPU is optional but pleasant: with a Vulkan build of whisper.cpp, a spoken
command transcribes in tens of milliseconds. Whisper runs on whatever
backend your system's whisper.cpp package was built with.

## Install

```bash
git clone https://github.com/Trixles/Voice-Commander.git
cd Voice-Commander
./install.sh
```

The installer checks prerequisites, builds a private Python venv, downloads
the Whisper `base.en` model (~150 MB) and the Silero VAD model, installs the
KWin placer script, sets up both systemd user units, and starts the service.
Re-running it is safe — your config is always preserved.

Where things land:

| What | Where |
|---|---|
| App, venv, icons, models | `~/.local/share/voice-commander/` |
| Config (one JSON file) | `~/.config/voice-commander/commands.json` |
| KWin placer script | `~/.local/share/kwin/scripts/vc-window-placer/` |
| Launcher + app-menu entry | `~/.local/bin/voice-commander` |

Launch-on-login is **off by default** — flip it in Settings → Options.

## Talking to it

Say **"computer"** (fresh installs also answer to "hey dude" — both the
extra wake word and everything below are editable). The tray icon changes
and a listening window opens; five seconds of silence closes it again.

| Say | It does |
|---|---|
| "open browser" | launches your default browser (detected at install) |
| "volume up" / "turn it down" / "set volume to fifty" | volume |
| "mute" / "unmute" | mic-friendly silence |
| "pause" / "play" | media control |
| "move left" / "move right" | shove the active window between screens |
| "minimize / maximize / close window" | window management |
| "move to the tv" | send the active window to a monitor by alias |
| "open mic" | keep listening for commands until you say "close mic" |
| "open voice commander settings" | the settings window |
| "shut down" / "restart" / "log out" | power — asks you to say "confirm" |

If nothing matches, one toast tells you exactly what it heard — no silent
failures, no guessing.

### Your own commands

Add commands in the settings UI with any spoken phrases you want. Four
action types: **launch an app**, **open a URL**, **open a file**, or **run
a shell command**. Append a monitor when you speak and the window lands
there: *"computer open youtube on the tv."*

### Settings tour

- **Commands** — every command, phrase, and wake word; enable/disable
  per command.
- **Displays** — your monitors with their EDID names; assign the aliases
  you'll actually say.
- **Overrides** — rewrite rules for chronic mishearings ("open crome" →
  "open chrome"), applied before matching.
- **Model** — point at a different Whisper `.bin` model.
- **Options** — notifications, auto-pause media, match strictness,
  launch on login.
- **Log** — the live transcript, exactly as Whisper heard it.

Under it all is one JSON config file, hot-reloaded on change, safe to edit
by hand. A corrupt file is reported, never overwritten; upgrades merge new
built-in commands into existing configs without touching your edits.

## A note on the always-on mic

A text wake word means the mic is transcribed continuously while the
service runs — that's what makes wake-up instant and training-free. All of
it stays local: audio never leaves `localhost`, and the rolling transcript
lives only in the in-app log. Stop the service and the mic closes.

## Extras

- Optional LED state colors on the ReSpeaker XVF3800 4-mic array. The
  installer detects `xvf_host` if you have it and prints the udev
  one-liner the device needs; without the tool, LED control quietly
  stays off.

## Uninstall

```bash
./uninstall.sh          # keeps your config
./uninstall.sh --purge  # removes the config too
```

## History

2.0.0 is a ground-up rebuild on whisper.cpp. The original Vosk-based build
(v0.2.0–v1.0.1) is archived read-only at
[Voice-Commander-Vosk](https://github.com/Trixles/Voice-Commander-Vosk).

## License

[MIT](LICENSE)
