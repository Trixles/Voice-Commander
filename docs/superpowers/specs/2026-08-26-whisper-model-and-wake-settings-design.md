# Whisper-era Model tab + baked-in wake word — design

**Date:** 2026-08-26
**Branch:** `wake-verifier`
**Status:** design approved, pending spec review

## Problem

Two settings surfaces still carry Vosk-era assumptions that are wrong under
the whisper build:

1. **Model tab** is a Vosk-model-*directory* picker ("Voice interpretation is
   powered by Vosk", Browse-to-a-folder, alphacephei download link). Whisper
   models are single `ggml-*.bin` *files* from a different source, so the tab's
   copy, its picker, and its link are all wrong.
2. **Commands tab wake word** treats `"computer"` as an ordinary, editable,
   removable entry in the wake-words list. But the shipped openWakeWord model
   (`computer_v2`) only detects `"computer"` — it *is* the fast audio-ack path.
   A user who edits `"computer"` out of the list silently loses that path and
   falls back to slower text-only wake, with no warning. `"computer"` is
   structural, not user data.

Both are small, both flow from the same whisper reality, so they ship as one
change.

## Non-goals

- No in-app model downloader. Users fetch `.bin` files themselves (link + a
  Browse picker), same shape as the old Vosk tab.
- No whisper-model dropdown / size enumeration. Browse-to-file only.
- No openWakeWord knobs exposed anywhere (thresholds, model selection). The
  wake engine is "one setup, just works."
- No chip/tag wake-word widget. The comma-separated field stays.

## Part A — Model tab (`core/settings/tabs/model_tab.py`)

The tab becomes purely about the **whisper speech model**.

### Behavior

- **Browse-to-`.bin`**: a path label + Browse button, mirroring the current
  Vosk tab's shape, but the file dialog filters to `*.bin` and selects a
  **file**, not a directory.
- The chosen absolute path is stored in the `whisper_model` config key.
- **`core/commands.py::get_whisper_model_path()` becomes path-or-token:**
  - If `whisper_model` contains a path separator, treat it as a filesystem
    path and return it as-is.
  - Otherwise treat it as a size token and derive
    `MODELS_DIR/whisper/ggml-{token}.bin` (current behavior).
  - This keeps the shipped default `whisper_model = "base.en"` working and lets
    a browsed path override it. `--emit-defaults` still emits the token.
- **Download blurb**: link to `https://huggingface.co/ggerganov/whisper.cpp`
  (the `ggml-*.bin` files), replacing the alphacephei/vosk link. Show the
  runtime-computed absolute path where a `.bin` can be dropped
  (`MODELS_DIR/whisper/`).
- **All "Vosk" copy replaced** with whisper phrasing.
- **Missing-model error label** stays as a fallback for the nuked-folder case
  (a model is force-installed, so this should not normally occur).

### Apply / restart

Changing the model marks the dialog dirty (reuse the existing restart-needed
tracking). On save, the save path calls `core/whisper_server.py::ensure_server()`,
which already rewrites `whisper-server.env` and restarts *only* the whisper unit
when the model path changed — no new restart logic. Calling it on save (rather
than waiting for the listener's next segment) makes the swap immediate.

### Cleanup pulled in

- `SettingsDialog._browse_vosk_model` → `_browse_whisper_model` (file picker,
  not directory).
- The save path that reads `_selected_model_path`, plus `_model_path_label` /
  `_model_error_label`, reworked for the file/whisper model.
- Tab reads `get_whisper_model_path()` instead of `get_vosk_model_path()`.

## Part B — Commands tab: baked-in `"computer"`

### Data model — `"computer"` is structural, not user data

- Define `BAKED_IN_WAKE_WORDS = ["computer"]` next to the wake detector, tied
  to the shipped openWakeWord model. (The word the vendored `computer_v2` model
  detects.)
- The `WakeWordDetector` is always constructed from
  **`BAKED_IN_WAKE_WORDS + custom_wake_words`** at the call site (keep
  `core/wake.py` pure — inject at assembly, e.g. a
  `commands.get_effective_wake_words()` helper or at the listener's detector
  build). Dedupe so a stray stored `"computer"` doesn't double.
- The `wake_words` config key stores **only the custom words**.
  `--emit-defaults` ships `wake_words = ["hey dude"]` (a removable example so
  users discover the feature exists).

### UI (`core/settings/tabs/commands_tab.py`)

- **New blurb:**
  > Say "computer" to wake Voice Commander from sleep to listen for commands.
  > You can also add custom wake words below, but they will be slightly slower
  > to activate than using "computer". Separate multiple words or phrases with
  > commas.
- **Locked `"computer"` prefix (Option A — seamless):** a container styled as a
  single input (bg, border, radius) holds a greyed, non-interactive
  `"computer"` segment fused flush-left of a **borderless** `QLineEdit` that
  holds only the custom words. Together they read as one field where the start
  is permanently greyed. `QLineEdit` has no locked-prefix support, so the
  prefix must be a separate widget — never a keystroke-intercepted prefix.
  - Comma is dynamic: field non-empty → label shows `"computer,"`; field empty
    → `"computer"` (no dangling comma), matching the old placeholder ghost but
    now permanent and real.
- The field shows/edits customs only. On load, `"computer"` is filtered out of
  the editable text. On save, the field's contents are stored as `wake_words`;
  `"computer"` is stripped if a user typed it (avoid a dupe).

### Migration (self-healing, no explicit step)

Existing configs (incl. the live one) have `wake_words = ["computer", "hey
dude"]`:

- On load, the editable field shows `"hey dude"` (computer filtered from
  display).
- The detector injects + dedupes, so wake behavior is unchanged.
- On the next Settings save, `"computer"` naturally drops from stored config.

### Footgun resolved

`"computer"` is always present and unremovable, so the openWakeWord audio path
always has its word; custom words are purely additive. The previously-flagged
"change the wake word, silently lose the fast ack" trap is gone by
construction.

## Testing

- `get_whisper_model_path()`: token → derived path; absolute existing path →
  returned as-is; default `"base.en"` → `ggml-base.en.bin`.
- Effective wake-words assembly: baked-in `"computer"` always present;
  `BAKED_IN + custom` order; dedupe when a stored config still lists
  `"computer"`; empty custom list still yields a working `"computer"`.
- Wake config roundtrip: `wake_words` stores customs only; the field's text
  maps to customs; `"computer"` filtered on display and stripped on save.
- `core/wake.py` stays pure and its existing `tests/test_wake.py` unaffected
  (injection happens at the call site, not in the detector).

## Out of scope / deferred

- Vosk backend scrub (priority 2) — this design leaves `recognizer_backend`
  untouched; it just stops the Model tab from *mentioning* Vosk.
- Any warning when a custom wake word won't get the fast audio path (a
  docs/Commands-tab concern, not this change).
