# Wake-word speaker verifier — design

**Date:** 2026-07-28 (session 34)
**Status:** implemented and deployed 2026-07-29 on branch
`wake-verifier`; recall confirmed live, suppression UNPROVEN — see
"Live results" at the end of this document before acting on it.
**Priority:** HANDOFF priority 1 — kill quiet-room false fires

## Problem

`computer_v2` @ 0.5 with the Silero speech gate blocks non-vocal noise
(19/19 music fires blocked across a 7h soak) but not *vocal* noise.
Measured 2026-07-28: coughs fired the audio wake engine at 0.567,
0.794, 0.998 and 0.893. The VAD gate cannot help — Silero correctly
rates a cough as voice activity, and whisper transcribed those segments
as "cough", meaning the gate had already passed them.

A cough is not Tyler, and it is not the wake word. openWakeWord's
custom verifier answers exactly that question, and one has already been
trained: `benchmark/verifier_clips/computer_v2_verifier.pkl`
(held-out recall 0.68–0.95, including one clip over music).

## How the openWakeWord verifier actually works

Read from the installed library
(`venv/lib/python3.14/site-packages/openwakeword/model.py:319-328`),
not from its docs, because the parameter name is misleading.

Per 80ms frame, the base model produces a score `s`. Then:

1. If `s >= custom_verifier_threshold`, the verifier runs and its
   output — the probability that this is the enrolled speaker saying
   the wake word — **replaces** `s`.
2. The engine's own `wake_threshold` comparison then decides whether to
   fire, against that possibly-replaced score.

So `custom_verifier_threshold` is **not** a reject threshold. It is the
gate above which the verifier gets a say. Its position relative to
`wake_threshold` determines everything:

| Gate vs `wake_threshold` | Effect |
|---|---|
| **equal** | Only frames that would have fired are re-scored. The verifier can only ever *remove* a fire. Pure veto. |
| **below** | Frames between the gate and the threshold are also re-scored, so a weak genuine wake can be *promoted* into firing. A recall knob — and unmeasured territory, since the verifier is ruling on frames the base model rejected. |
| **above** | Frames between `wake_threshold` and the gate fire **completely unverified**. The measured 0.567 cough lives in that window. |

## Decisions

1. **The verifier is user data, never shipped.** It is a voiceprint —
   useless to anyone but its subject, and actively harmful to another
   user, whom it would reject. It lives in `DATA_DIR/wakewords/`
   alongside the drop-in `.onnx` models. `install.sh` neither downloads
   nor copies it. Shipped default is **off**, so a fresh install
   behaves exactly as it does today.
2. **One config knob, gate derived.** `wake_verifier` only; the gate is
   set equal to `wake_threshold` in code. This makes the verifier a
   pure veto and makes the gate-above-threshold footgun structurally
   impossible. If the deferred music work later wants the recall knob,
   adding it is a small change — and by then there will be soak data to
   set it from instead of a guess.
3. **A failed verifier load must not degrade to text wake.** Losing the
   ~150ms ack is a bigger regression than the false fires the verifier
   was added to prevent, and a bad pickle is recoverable in seconds by
   retraining. openWakeWord keeps running unverified, loudly.
4. **The artifact and its training inputs get a durable home.** Today
   the `.pkl` and every training clip live only in `benchmark/`, which
   `.gitignore:44` excludes wholesale — one untracked directory on one
   machine, with no backup, holding both the model *and* the only means
   of rebuilding it.

## Design

### Config surface

One new key, `wake_verifier`: a filename stem, default `""` (off).

```python
def get_wake_verifier_path() -> str | None:
    """Speaker verifier for the audio wake engine: a second-stage
    classifier that answers "is this the enrolled speaker saying the
    wake word?" on frames the base model would fire on. Trained
    locally (benchmark/train_v2_verifier.py) — it is a voiceprint, so
    it is user data and ships with nothing. Empty = off."""
    name = str(_config.get("wake_verifier", "")).strip()
    if not name:
        return None
    return os.path.join(paths.DATA_DIR, "wakewords", f"{name}.pkl")
```

Mirrors `get_wake_model_path()`: resolves a stem against the same
directory, does not check existence (loading is the engine's job).
Added to the `--emit-defaults` block next to the other wake keys, with
value `""`.

### Engine wiring — `core/wakeword.py`

`_load_oww_model()` gains a `verifier_path` parameter:

```python
def _load_oww_model(model_path, vad_threshold=0.0, verifier_path=None,
                    threshold=0.5):
    from openwakeword.model import Model  # deferred import

    kwargs = {"wakeword_models": [model_path], "inference_framework": "onnx"}
    if vad_threshold > 0:
        kwargs["vad_threshold"] = vad_threshold
    if verifier_path:
        # Key MUST be the .onnx stem -- that is the name oww gives the
        # loaded model. Any other key and oww's constructor raises
        # ValueError, the factory below degrades to unverified, and the
        # wake word runs unprotected while the config says otherwise.
        stem = os.path.splitext(os.path.basename(model_path))[0]
        kwargs["custom_verifier_models"] = {stem: verifier_path}
        # Gate == fire threshold: only frames that would fire get a
        # second opinion, so the verifier can only veto, never create
        # a fire, and there is no unverified window above it.
        kwargs["custom_verifier_threshold"] = threshold
    return Model(**kwargs)
```

### Failure handling — the fallback lives in the factory

`core/listener.py:252-264` already catches *any* wake-engine
construction failure and degrades to text wake with an always-on toast.
Letting a bad pickle escape there would produce exactly the behavior
decision 3 rejects. So `make_wake_engine_factory()` handles it:

- Try to build the model with the verifier.
- On any exception, rebuild **without** it and set `verifier_error` (a
  message string) on the returned engine. `OpenWakeWordEngine` gains
  that attribute, defaulting to `None`.
- The listener reads `verifier_error` after constructing the engine and
  fires a notification via `_notify`, wording the fix: retrain the
  verifier. **`_notify`, not `_notify_general`** — the latter is gated
  by the Options-tab notifications toggle (`core/listener.py:44`), which
  would let "your wake word is unprotected" be silently suppressed. The
  existing wake-engine-failure toast three lines away uses bare
  `_notify` for the same reason.

This keeps `core/wakeword.py` a pure engine module with no notification
plumbing, and reserves the text-wake degrade for a genuinely dead
engine (missing dependency, missing `.onnx`).

### Logging

`last_score` becomes the verifier's probability whenever a verifier is
active, so a verified genuine wake logs ~0.68–0.95 where it previously
logged ~0.99. Unmarked, that reads in the journal as the wake model
getting worse. The tag goes on the **journal `print()` line that already
carries the score**:

```
[listener] Wake word detected (audio engine, verified score 0.820).
[listener] Wake word detected (audio engine, score 0.990).
```

It does **not** go into `log()`. The user-facing Settings log carries no
score by deliberate decision (commit 66b8214, "keep the wake score out
of the user-facing log") — it is journal-only diagnostics, and the
verified tag is more of the same.

### Artifact durability

- `benchmark/train_v2_verifier.py` writes its output to
  `DATA_DIR/wakewords/` (keeping the `benchmark/` copy for the
  offline evaluation table it prints).
- `benchmark/verifier_clips/` (1.7 MB — the complete retrain input)
  is added to the `~/Development/Backups/Voice-Commander/` rule.
  `benchmark/soak/` (6.1 MB of recorded evidence) stays out.
- `install.sh` needs no change: `install_wakeword_models()` already
  skips any file present in `DATA_DIR/wakewords/`, and never has
  reason to fetch a verifier.

### Out of scope

- **GUI.** The Models tab is HANDOFF priority 4; `wake_verifier`
  belongs in that batch with the other wake keys, not ahead of it.
- **Retraining.** This wires up the verifier that exists. A better
  model is priority 3, and stays deferred.

## Testing

Extends `tests/test_wakeword.py`, following its existing pattern of
monkeypatching `_load_oww_model` and asserting on captured kwargs:

1. `wake_verifier` unset → no verifier kwargs passed at all.
2. `wake_verifier` set → `custom_verifier_models` keyed by the `.onnx`
   stem, pointing at `DATA_DIR/wakewords/<stem>.pkl`.
3. Gate equals `wake_threshold`, including at a non-default threshold,
   so a future edit cannot silently open an unverified window.
4. `get_wake_verifier_path()` returns `None` for empty/whitespace and
   the resolved path otherwise.
5. Verifier load failure → factory returns a working unverified engine
   with `verifier_error` set, and does **not** raise (which would
   trigger the listener's text-wake degrade).
6. Full gauntlet stays green (213 at time of writing).

**None of this certifies the feature.** Per the standing methodology
law, offline scoring cannot certify wake behavior — only 3 of 8 live
fires reproduced when re-scored from their own saved audio. Acceptance
is a **live soak** (`benchmark/soak_wakeword.py`): quiet-room false
fires at or near zero, with genuine wakes still firing at conversational
distance. Unlike the music work, this soak only requires Tyler to be in
the room.

## ARCHITECTURE.md

The wake invariant ("Wake is acknowledged on partials…", line 892) gains
a verifier paragraph under its audio-wake-engine bullet, and three new
**Don't** entries:

- Don't set `custom_verifier_threshold` above `wake_threshold` — it
  creates a window of unverified fires.
- Don't key `custom_verifier_models` by anything but the `.onnx` stem —
  oww's constructor *raises* `ValueError` when a key matches no loaded
  base model (`openwakeword/model.py`, right after the verifier pickles
  are loaded). The factory catches that and degrades to an unverified
  engine, so the failure is loud but the wake word still ends up
  unprotected.
- Don't compare a verified score against an unverified one; they are
  different quantities from different models.

## Known-stale, not addressed here

`ARCHITECTURE.md:451` still describes the pre-2.0.0 fork namespace
(`voice-commander-whisper`, placer id `vcw-window-placer`,
`tests/test_fork_identity.py` — now `tests/test_install_identity.py`).
Unrelated to this work; flagged for separate cleanup.

## Live results — 2026-07-29

Deployed via `install.sh` (deployed `core/` byte-verified against the
repo), live config `wake_engine: openwakeword` +
`wake_verifier: computer_v2_verifier`, gate and Silero speech gate both
0.5. Verifier load confirmed positively, not merely by absence of error:
`OpenWakeWordEngine.verified is True`, `verifier_error is None`.

### Recall — CONFIRMED

Five genuine wakes, five fires, every journal line tagged: 0.745, 0.929,
0.903, 0.857, 0.816. All inside the 0.68–0.95 band the held-out clips
predicted. The verifier does not eat real wakes at normal speaking
distance, and a full command plus a chain segment dispatched correctly
through it.

### Suppression — NOT ESTABLISHED

Zero cough false-fires with the verifier on. That result cannot be
credited to the verifier, because the A/B says the condition did not
reproduce at all:

With `wake_verifier: ""` and everything else identical, **zero coughs
fired the wake engine.** Four `cough` transcripts and one `burps`
appeared in SLEEPING (whisper's transcription path, not the wake path)
and none woke it. Both fires during the A/B window were the operator
actually saying the wake word — `score 0.977` and `score 0.997`, each
followed by `Heard (listening): computer`, and neither carrying the
`verified` tag, which also confirms the A/B was genuinely in the off
state.

So the 2026-07-28 cough false-fires (0.567 / 0.794 / 0.998 / 0.893)
would not reproduce on demand. A deliberate test cough is not the same
acoustic event as the involuntary coughs that accumulated those scores
across a full day of real use. The only evidence favouring the verifier
remains the offline training result — it suppressed the held-out
recorded false-fires while keeping genuine wakes — and that is
file-scored, which the standing methodology law says cannot certify live
wake behavior.

### Consequence for how this gets certified

Deliberate provocation is not a valid test for this bug: you cannot
schedule an involuntary cough. Only accumulated real use can certify it.

Note the blind spot that makes even a long soak weak evidence: **a veto
logs nothing.** When the verifier suppresses a fire there is no trace,
so a clean soak shows an absence rather than a count of saves, and
"suppressed a 0.998 cough" is indistinguishable from "that cough scored
0.2 today". Making saves visible requires scoring base-and-verified in
parallel — soak-script work (`benchmark/soak_wakeword.py`), not app
work, since oww overwrites the base score in place.

The one signal that would falsify the approach and send us to the
retrain path (priority 3): a journal line carrying `verified` that came
from a cough.

### The first soak was lost — do not certify from the journal

The 2026-07-29 evening soak ran for 3h 05m (systemd's own accounting)
and produced **no usable evidence**: by the next day the user journal
retained only the final ~70 seconds before shutdown. Ten lines survived
from the entire day.

Cause, measured rather than assumed: the user journal caps around 37 MB
and rotates a 6.5 MB file roughly every 50 minutes under load. A Claude
Code session running a review fleet emitted ~96,000 of the next day's
116,000 user-journal lines — 83% — and evicted the soak window. Any
heavy `--user` logger will do the same; this is not specific to one
tool.

**Consequence: the journal is not an instrument.** It is fine for
watching behavior live, and useless as a record. Wake-score logging via
`journalctl` cannot certify anything that has to survive overnight.

Certify with `benchmark/soak_wakeword.py` instead. It writes
`soak/soak_log.txt` plus a 4-second WAV per fire, so its evidence is
file-based and immune to rotation — and it scores the SAME audio
through three streams at once (`v2` ungated control, `v2+vad`
production-before, `v2+vad+ver` the candidate). That last property also
answers the blind spot above: a cough that fires `v2+vad` but not
`v2+vad+ver` is the verifier's veto captured as a positive fact rather
than inferred from an absence. The off-then-on A/B run on 2026-07-29 was
the wrong method — it compared different moments in time instead of the
same audio, which is exactly why it could not distinguish "the verifier
suppressed it" from "the cough never scored high that time".

Note the script's gate ran at 0.3 until 2026-07-30, below production's
0.5. Soak logs older than that measured a config production never ran.
