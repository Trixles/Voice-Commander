# Design: timely wake-word acknowledgement (B2 + B1)

**Date:** 2026-06-23
**Status:** approved (design); not yet implemented
**Status note:** design approved, not yet implemented — this doc is a plan,
not a record of shipped behavior.

## Problem

The "I'm awake" feedback — tray icon, mic LED, and the "Listening..."
notification — is gated on Vosk *finalizing* the utterance, not on first
hearing the wake word. Concretely, wake detection + `set_state` + the
notification all live in the SLEEPING branch (`core/listener.py`), which is
only reached after `rec.AcceptWaveform(data)` returns `True` — i.e. after
Vosk has endpointed the whole utterance.

Observed symptoms (hardware, 2026-06-23):
- "computer open reddit" (one breath) → notification + command execution
  fire together, at the end. The acknowledgement is pointless after the fact.
- "computer" then silence → ~1.5s before the notification / tray / LED react.
  That 1.5s **is** Vosk's endpoint-silence delay.
- It is *hearing* immediately (partials stream live) but not *reacting* until
  finalize.

Two distinct bugs inside the one report:
- **B2 (latency, primary):** feedback is late because it waits for finalize.
- **B1 (redundant "Listening..."):** the notification at the SLEEPING-finalize
  branch fires unconditionally before `try_match`, so a bundled command shows
  "Listening..." even though it already executed.

## Decision

**Approach B — transition to LISTENING early, on the partial.** Chosen over
Approach A (decouple a visual-only signal from state) because B keeps a single
source of truth for "are we listening?" (the state), and reuses the
already-hardware-verified LISTENING keepalive. `set_state(LISTENING)` is what
lights the tray + LED (`core/tray.py` maps `State.LISTENING` → listening icon
and drives the LED off the same `state_queue`).

**Bundled-command feel:** always acknowledge. The wake word lights it up every
time (consistent with universal voice-assistant expectations), then the command
executes.

## Changes

All in `core/listener.py`, plus one helper in `core/wake.py`.

### 1. Early wake acknowledgement on partials (the fix)

The partial block (runs when `not rec.AcceptWaveform(data)`) currently does
work only `if context.state == State.LISTENING` (the inactivity keepalive). Add
a SLEEPING case: when a partial contains the wake word, perform exactly the
SLEEPING-finalize "go to LISTENING" path, ~1.5s earlier:
- log "Wake word detected"
- `_notify_general("Listening...", ...)`
- `set_state(State.LISTENING)`  ← lights tray + LED
- `command_window = commands.get_command_window()`
- `command_window_start = now`

No "already-woke" flag is needed: once we leave SLEEPING, the SLEEPING case
cannot re-fire on later partials of the same utterance. Subsequent partials hit
the existing LISTENING keepalive, which holds the window while the user finishes
talking. At finalize we are already in LISTENING; the existing LISTENING branch
matches the full text (the matcher already tolerates the wake-word prefix —
verified by "computer open dolphin and open readme" firing from SLEEPING).

### 2. Bare-wake suppression (the required guard)

Because we now enter LISTENING *before* finalize, saying the wake word alone
("computer" + silence) finalizes as just `"computer"`, which would hit the
LISTENING no-match path and wrongly toast "No match." Guard: in the LISTENING
branch, if the finalized text is nothing but wake word(s), swallow it silently —
stay in LISTENING, wait for the real command; do not match, do not toast.

Add `WakeWordDetector.is_wake_only(text) -> bool` in `core/wake.py`: remove all
wake-word occurrences from the text and return whether the remainder is empty/
whitespace. Pure function → unit-testable. Keeps `core/wake.py` dependency-free.

This also improves the pre-existing case where a user re-says the wake word
while already LISTENING: now silently ignored instead of toasting "No match."

### 3. SLEEPING finalize branch stays (fallback)

Unchanged. Remains the path for utterances so short Vosk emits no useful partial
before finalizing. Everywhere else the early path supersedes it, which resolves
**B1** for the common case without extra code: "Listening..." now fires *before*
a bundled command runs. The rare no-partial fallback keeps the old
notify-then-execute timing; accepted (YAGNI — no early signal is possible when
the only event is the finalize).

## Data flow

```
SLEEPING ──partial has wake?──▶ set_state(LISTENING) + "Listening..." + window starts  ← early, lights LED
   │                                      │
   │ (no partial caught it — rare)        ▼ (same utterance keeps streaming)
   ▼                              finalize ──▶ LISTENING branch:
SLEEPING finalize branch                       • is_wake_only(text) → swallow, keep listening
(fallback, unchanged)                          • real command       → match/execute → SLEEPING
```

## Edge cases

- **False-positive partial** (partial flashes the wake word, final does not):
  spurious listening flash + a window that times out or no-matches. Rare; same
  risk class as today's false wakes. Accepted.
- **Wake-only re-utterance while already LISTENING:** now silently ignored
  instead of toasting "No match." Strictly an improvement.
- **OPEN_MIC / CONFIRMING:** untouched. The change is SLEEPING-partial +
  LISTENING-finalize only.

## Testing

- **Unit:** `is_wake_only()` — returns `True` for wake-only text; `False` for
  wake+command, and `False` for a wake word embedded in a longer word
  (`is_wake_only("computerized")` is `False`, remainder `"ized"`). Note this is
  separate from wake *detection*: `detector.check("computerized")` is still
  `True` (substring match) and still wakes the system — that pre-existing quirk
  is out of scope here.
- **Hardware:** the loop timing is not pytest-able (live audio + Vosk). The
  three observed scenarios become the acceptance check:
  1. "computer" alone → LED/tray/notification react immediately (no ~1.5s lag),
     then it waits for the command without toasting "No match."
  2. "computer open reddit" bundled → acknowledgement flashes immediately, then
     reddit opens.
  3. Long slow chain still fires (no regression to bug (a)).

## Docs to update (same change set)

- `core/listener.py` state-machine docstring + the now-false comment ("SLEEPING
  ... arrives as one utterance").
- **New `ARCHITECTURE.md` invariant:** the command window is an inactivity timer
  driven by partials; wake acknowledgement fires on partials in SLEEPING; bare-
  wake utterances are swallowed in LISTENING. (The s16 keepalive never got an
  invariant; this is overdue.)
