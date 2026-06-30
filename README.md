# Voice Commander

![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)
![Platform: KDE Plasma 6](https://img.shields.io/badge/platform-KDE%20Plasma%206%20%2F%20Wayland-1d99f3.svg)
![Speech: Vosk (offline)](https://img.shields.io/badge/speech-Vosk%20(offline)-success.svg)

> Talk to your desktop. Voice Commander turns KDE Plasma into something you
> can boss around hands-free — no cloud, no subscription, no smart speaker
> listening in. Just your voice, a tray icon, and KWin doing what it's told.

---

## Table of Contents

- [What is Voice Commander?](#what-is-voice-commander)
- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [How It Works](#how-it-works)
- [The Settings Window](#the-settings-window)
  - [Commands](#commands)
  - [Overrides](#overrides)
  - [Displays](#displays)
  - [Model](#model)
  - [Log](#log)
  - [Options](#options)
- [Confirming Destructive Commands](#confirming-destructive-commands)
- [Open Mic Mode](#open-mic-mode)
- [Optional Hardware: ReSpeaker LED Ring](#optional-hardware-respeaker-led-ring)
- [Updating](#updating)
- [Uninstalling](#uninstalling)
- [Troubleshooting](#troubleshooting)
- [Known Limitations](#known-limitations)
- [For Developers](#for-developers)
- [License](#license)

---

## What is Voice Commander?

Voice Commander is a lightweight, fully offline voice-command daemon for
KDE Plasma 6 on Wayland. It sits quietly in your system tray, listens for a
wake word, and when it hears one, listens briefly for a command. Say
something it recognizes — launch an app, open a URL, nudge the volume, throw
the active window onto another monitor, even shut the machine down (with a
confirmation, because it's not a maniac) — and it does the thing.

> **Privacy, by construction, not by policy.** Speech recognition runs
> entirely on your machine via [Vosk](https://alphacephei.com/vosk/). Nothing
> you say is ever sent anywhere. The only network call Voice Commander itself
> ever makes is the one-time speech model download during installation —
> after that, it works completely offline (commands that explicitly open a
> URL still need a connection to reach that URL, obviously).

It's built for people who want a voice assistant that behaves like a Unix
tool: predictable, inspectable, fully configurable in a text file if you want
to go that route, and not trying to sell you anything.

## Features

- **Fully offline speech recognition** — powered by [Vosk](https://alphacephei.com/vosk/),
  no API keys, no accounts, no telemetry.
- **Custom wake words** — one or several, your call.
- **Forgiving recognition** — a fuzzy matcher tolerates minor mishearings out
  of the box, and a user-editable override system fixes the words Vosk
  reliably mangles for *your* voice and mic.
- **Command chaining** — "open reddit and open youtube" fires both. If one
  half is misheard, the other still fires, and you're told which part
  didn't land.
- **Multi-monitor aware** — name your displays, then target any command at
  one ("open dolphin on monitor two") or relocate the active window on
  demand ("move to monitor two").
- **Confirmation on destructive commands** — shutdown, restart, and logout
  all require a verbal "confirm" before they execute.
- **Open Mic mode** — drop the wake-word requirement entirely when you want
  to fire off several commands back to back.
- **Fully configurable command set** — launch applications, open URLs or
  files, or run arbitrary shell commands, all editable from a native Qt
  settings window. No hand-editing JSON required (but you can, if you'd
  rather).
- **System tray with live state feedback** — the icon (and, if you own one,
  a ReSpeaker LED ring) changes color depending on whether Voice Commander
  is sleeping, listening, in Open Mic mode, or hit an error.
- **Hot-reloading config** — edit your commands and save; no restart needed.
- **Lightweight** — the default small Vosk model uses practically no system
  resources.

## Requirements

| | |
|---|---|
| **Desktop** | KDE Plasma 6 on Wayland (the installer gates on `kwriteconfig6`, which ships with Plasma 6) |
| **Init** | `systemd --user` (the app runs as a user service) |
| **Python** | 3.11+ |
| **Distro** | Auto-detected dependency installation on Arch-likes (CachyOS, Manjaro, etc.) and Debian-likes (Debian, Ubuntu, Kubuntu). Other distros: install the binaries below yourself, then run the installer. |
| **Microphone** | Any device PipeWire can see as a default source |

The installer checks for these system binaries and tells you exactly what to
install if any are missing: `pactl`, `pw-record`, `playerctl`,
`kscreen-doctor`, `notify-send`, `kwriteconfig6`, `dbus-send`, `xdg-open`,
`curl`, `unzip`, `systemctl`.

Python dependencies (`vosk`, `PySide6`) are installed automatically into a
dedicated virtual environment — you don't need to install them yourself.

## Installation

```bash
git clone https://github.com/Trixles/Voice-Commander.git
cd Voice-Commander
./install.sh
```

The installer is entirely userland (no `sudo` required) and:

1. Checks prerequisites and offers copy-paste install commands for anything missing.
2. Copies the application into `~/.local/share/voice-commander/app/` and
   creates a Python virtual environment alongside it.
3. Downloads the small Vosk speech model (~40 MB) if it isn't already present.
4. Installs the `vc-window-placer` KWin script, which powers multi-monitor
   window routing.
5. Generates a default `commands.json` on first install (existing configs
   are never overwritten on re-install).
6. Installs and starts a `systemd --user` service, plus an app-menu launcher
   and a `voice-commander` terminal command.

Re-running `./install.sh` any time (to update, for example) is always safe —
it preserves your configuration and restarts the service to pick up new code.

**Launch on login is off by default.** Turn it on from Settings → Options if
you want Voice Commander to start automatically with your session.

**Have a ReSpeaker XVF3800 mic array?** The installer auto-detects it and
enables LED ring support. If the required udev rule isn't installed yet,
it prints a one-line `sudo` command to add it — see
[Optional Hardware](#optional-hardware-respeaker-led-ring).

## Quick Start

After installing, look for the Voice Commander icon in your system tray.
Right-click it for Settings, Log, or Quit. Left-click toggles
[Open Mic mode](#open-mic-mode).

The default wake words are **"computer"** and **"hey dude."** Try:

```
"Computer, open browser."
"Computer, open reddit."
"Computer, volume up."
"Computer, open browser on monitor two."
"Computer, shut down."
  → "Shutdown command received. Say 'confirm', 'yes', or 'do it' to proceed."
"Confirm."
```

Three example commands ship out of the box so there's something to say on
first run — **Open Browser**, **Open Reddit** (the *old* design, obviously —
see [Commands](#commands) if you'd like to fight about it), and
**Open ReadMe** (opens this file). All three are ordinary user commands:
rename them, repoint them, or delete them entirely from the Commands tab.

## How It Works

Voice Commander runs a small state machine, driven entirely by what it hears:

1. **Sleeping.** It's listening for a wake word, and nothing else. The
   moment it hears one — even mid-sentence, off a live partial transcript,
   so you don't have to wait for a pause — it wakes up immediately.
2. **Listening.** A short command window opens (5 seconds by default, and it
   resets every time you're still talking, so a long multi-part command
   never gets cut off mid-sentence). Say a command.
3. **Matching.** What was heard is first run through the
   [override list](#overrides) — deterministic find-and-replace fixes for
   words your mic/voice reliably confuses Vosk on — and *then* fuzzy-matched
   against every command phrase you've configured. A close-but-imperfect
   match still fires; an unrelated phrase doesn't, even if it happens to
   share a word with a real command.
4. **Dispatch.** A match runs the corresponding action and shows a brief
   notification confirming what happened. No match gets a "No match" toast,
   and Voice Commander goes straight back to sleep — no second failed
   attempt, no hanging around waiting.

You can chain multiple commands in one breath — *"open reddit and open
youtube"* fires both. If part of a chain is misheard, the rest still fires;
you'll just get a heads-up about the part that didn't.

Every recognized phrase, match, and dispatch is recorded in the
[Log tab](#log), so you can always see exactly what Voice Commander heard —
which, with Vosk, is sometimes unintentionally hilarious.

## The Settings Window

Right-click the tray icon and choose **Settings** (or say *"open voice
commander settings"*). Everything is editable here — nothing requires
hand-editing config files, though `~/.config/voice-commander/commands.json`
is plain JSON if you'd rather.

| Tab | What it's for |
|---|---|
| [Commands](#commands) | Wake words, plus every command — yours and the built-ins. |
| [Overrides](#overrides) | Fixes for words Vosk consistently mishears. |
| [Displays](#displays) | Name your monitors so you can target them by voice. |
| [Model](#model) | Choose which Vosk speech model to use. |
| [Log](#log) | Live transcript of everything heard and matched. |
| [Options](#options) | Launch on login, notifications, recognition strictness. |

### Commands

This is the heart of the app, split into two sections:

**User Commands** are yours. Click **Add Command**, give it a name, pick an
action, and set the phrase(s) that trigger it. Four action types are
available:

| Action | Does |
|---|---|
| **Launch App** | Runs an installed application, picked from a searchable app list. |
| **Open URL** | Opens a web address, optionally in a specific browser. |
| **Open File** | Opens any file, script, or `.desktop` entry — anything `xdg-open` understands. |
| **Run Command** | Runs an arbitrary shell command. Yes, you can wire up literally anything from here. |

Commands can be executed on a specific display by appending *"on
{alias}"* to the command phrase — e.g. *"open dolphin on monitor 3."*
Monitor aliases are set on the [Displays](#displays) tab.

**System Commands** are the built-ins: mic toggling, window management,
volume, media playback, and power controls. Their names and actions are
fixed, but every phrase is editable, and each one can be toggled on or off
individually:

| Category | Commands |
|---|---|
| Mic | Open mic, Close mic |
| Window | Move left, move right, minimize, maximize, close, move to *{alias}* |
| Volume | Volume up, volume down, mute, unmute, set volume to *{level}* |
| Media | Pause, resume |
| Power | Shut down, restart, log out *(all require [confirmation](#confirming-destructive-commands))* |
| Settings | Open Voice Commander settings |

A disabled command isn't deleted — it's still recognized, but instead of
firing, you get a quick "disabled in Settings" notification, so you always
know *why* nothing happened instead of just getting silence.

### Overrides

Vosk is good, not psychic. Certain words get misheard the same way, every
time, for a given mic and voice — Overrides are how you fix that for good,
as a simple "Vosk heard X, I meant Y" rewrite applied *before* matching even
starts.

Built-in overrides cover common mishearings relevant to Voice Commander.
They can be toggled on or off, but not edited or deleted. Shipped defaults
include things like correcting "cause" to "close" and "mike" to "mic."

Add your own under **User Overrides**: a pattern (what Vosk tends to hear)
and a replacement (what you meant). Matching is whole-word, so a rule for
"in" won't corrupt "open" or "spin." If you notice a word getting
consistently misheard, check the [Log tab](#log) to see exactly what Vosk
transcribed, then add an override for it.

### Displays

Connected displays are detected automatically. Click the **Aliases** button
on a display to give it one or more voice-friendly names (defaults like
"monitor one," "monitor two" are seeded automatically).

Once a display has an alias, you can:

- **Target a command at it**, by appending *"on {alias}"* to any command
  phrase — *"open dolphin on monitor three."*
- **Relocate the active window to it**, by saying *"move to {alias}."*

Other window controls live here too — *"move left,"* *"move right,"*
*"minimize window,"* *"maximize window,"* and *"close window"* — though
those are edited as ordinary [System Commands](#commands), not on this tab.

### Model

Voice interpretation is powered by [Vosk](https://alphacephei.com/vosk/).
The default small English model is recommended — it's not as accurate as
the larger models, but it's generally good enough and uses practically zero
system resources. If you want more accuracy and don't mind the extra disk
and memory footprint, download a
[larger model](https://alphacephei.com/vosk/models) and point this tab at
it. Changing models requires a service restart, which the settings window
will tell you about when you save.

### Log

Tracks wake word and command activation, and shows exactly what is heard by
the interpreter (which is sometimes unintentionally hilarious). If a certain
word or phrase is being consistently misheard, you can create an
[override](#overrides) for it.

### Options

Three app-wide settings, plus an About blurb:

- **Launch on login** — off by default. Toggle on to have Voice Commander
  start automatically with your session.
- **Enable notifications** — a blanket on/off switch for desktop
  notifications. Confirmation prompts for shutdown, restart, and logout
  *always* appear regardless of this setting — you should never be able to
  confirm a destructive action blind.
- **Recognition strictness** — how closely what you say has to match a
  command phrase to fire it. Defaults to a sane middle ground; the slider
  goes all the way to either extreme if you want to live dangerously, with
  a clear warning attached. Lower is looser (more misfires); higher is
  stricter (commands may stop registering).

## Confirming Destructive Commands

Shutdown, restart, and logout all require a spoken confirmation before they
execute — Voice Commander will never power off your machine because it
misheard something. Trigger one of these and you'll get a notification with
a five-second window:

- Say **"confirm," "yes,"** or **"do it"** to proceed.
- Say **"cancel," "never mind,"** or **"abort"** to back out.
- Say nothing, and it cancels itself automatically when the window expires.

This prompt always fires, even with notifications turned off in
[Options](#options) — there's no setting that lets you confirm a destructive
command blind.

## Open Mic Mode

Normally you need to say a wake word before every command. Open Mic mode
skips that — useful when you're about to issue several commands in a row and
don't want to repeat "computer" each time.

Toggle it by left-clicking the tray icon, or with the voice commands *"open
mic"* / *"close mic"* (phrases editable under [System Commands](#commands)).
The tray icon and notifications make it obvious when Open Mic is active, so
you won't forget it's on.

## Optional Hardware: ReSpeaker LED Ring

If you have a Seeed Studio ReSpeaker XVF3800 USB mic array, Voice Commander
will drive its LED ring to match the tray icon's state — sleeping, listening,
open mic, and error each get their own color. This is entirely optional and
auto-detected; without the hardware, it's simply a no-op.

The installer detects `xvf_host` automatically. Controlling the device's LED
requires a udev rule granting write access, which the installer can't install
itself (that would require `sudo`, and the installer is intentionally
userland-only). If the rule is missing, `install.sh` prints the exact
copy-paste command to add it. You can run it any time — install continues
fine without it, and LED control kicks in as soon as the rule's in place and
the device is replugged.

## Updating

```bash
cd Voice-Commander
git pull
./install.sh
```

Re-running the installer updates the application code and restarts the
service. Your `commands.json` is never touched.

## Uninstalling

```bash
./uninstall.sh
```

Removes the systemd service, launcher, app-menu entry, application code, and
the KWin placer script. Your `commands.json` is preserved by default. To
remove it too:

```bash
./uninstall.sh --purge
```

## Troubleshooting

**No tray icon / service won't start**
```bash
systemctl --user status voice-commander
journalctl --user -u voice-commander -f
```

**Wake word not triggering, or commands misfiring** — Open the
[Log tab](#log) and watch what's actually being transcribed while you talk.
If a specific word is consistently wrong, add an [override](#overrides) for
it. If recognition feels generally too loose or too strict, adjust
**Recognition strictness** in [Options](#options).

**Commands route to the wrong monitor, or don't move at all** — Confirm the
monitor has an alias set on the [Displays](#displays) tab, and that you're
on KDE Plasma 6 (multi-monitor routing depends on the `vc-window-placer`
KWin script, which only installs on Plasma 6).

**`voice-commander` command not found in a terminal** — `~/.local/bin` isn't
on your `PATH`. Add it in your shell config (`~/.bashrc`, `~/.zshrc`, etc.).

**ReSpeaker LED isn't changing** — See
[Optional Hardware](#optional-hardware-respeaker-led-ring); you likely need
the udev rule.

## Known Limitations

- **Chained commands that open URLs on two *different* monitors are
  rejected**, rather than risk firing wrong. The browser may reuse an
  existing window, open a new one, or open new tabs depending on its
  current state — there's no reliable way to predict which, so this case is
  refused outright rather than silently doing the wrong thing. Chaining
  multiple URLs to the *same* monitor (or leaving them untargeted) works
  fine.
- **Command phrases can't start with "the."** A leading "the " is silently
  stripped before matching, because some mics with onboard noise processing
  (the ReSpeaker XVF3800 included) occasionally hallucinate it from
  background noise.

## For Developers

This README covers using Voice Commander. If you're looking at the code:

- **`ARCHITECTURE.md`** documents the durable design invariants — the *why*
  behind non-obvious decisions in the matching pipeline, the settings UI,
  and the KWin integration.
- **`CHANGELOG.md`** has the full version history.

Contributions, issues, and forks are welcome under the license below.

## License

[MIT](LICENSE) — do what you want with it.
