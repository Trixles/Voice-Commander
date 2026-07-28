# Wake-Word Speaker Verifier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Suppress quiet-room false wake fires (coughs at 0.567–0.998) by
running openWakeWord's trained speaker verifier on any frame that would
otherwise fire.

**Architecture:** openWakeWord already supports a second-stage verifier
natively — a frame scoring above `custom_verifier_threshold` gets
re-scored by the verifier, whose output *replaces* the base score. So
this is config plumbing, not new inference. One config key
(`wake_verifier`, default off) resolves a `.pkl` in
`DATA_DIR/wakewords/`; the verifier gate is set equal to
`wake_threshold` in code so the verifier can only ever veto a fire,
never create one. A verifier that fails to load leaves openWakeWord
running unverified with a loud toast — it must NOT fall back to text
wake.

**Tech Stack:** Python 3.14, openwakeword 0.6.0 (onnx path), numpy,
scikit-learn (transitively, for the pickled verifier), pytest.

**Spec:** `docs/superpowers/specs/2026-07-28-wake-verifier-design.md` —
read it first, especially the table explaining why the gate's position
relative to `wake_threshold` is the whole ballgame.

## Global Constraints

- **Gauntlet green before every commit:**
  `python -m pytest -q && python -m compileall -q core && python -m core.commands --emit-defaults >/dev/null 2>/dev/null && echo "✅ green"`
  Baseline is 213 passing tests. Never pipe `--emit-defaults` with `2>&1`.
- **No new dependencies.** scikit-learn is already in `requirements.txt`.
- **`core/commands.py` must stay importable without PySide6** — the
  `--emit-defaults` path in `install.sh` runs with no Qt installed. Add
  no Qt imports; `os` and `core.paths` are already imported there.
- **Never write to `~/.local/share/voice-commander/` directly.** All
  deploys go through `./install.sh`, and only after Tyler says yes in
  the conversation. No task in this plan deploys.
- **Commit style:** conventional commits (`feat:`, `fix:`, `docs:`,
  `test:`, `refactor:`). Commit after each task. **Do not push** — Tyler
  approves pushes separately.
- **The gate must never exceed `wake_threshold`.** Above it, frames
  between the two fire completely unverified — the measured 0.567 cough
  lives in that window.

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `core/commands.py` | Config accessors + shipped defaults | Add `get_wake_verifier_path()`; add `wake_verifier` to `--emit-defaults` |
| `core/wakeword.py` | Audio wake engine behind a seam | Pass verifier kwargs to oww; degrade gracefully; expose `verified` / `verifier_error` |
| `core/listener.py` | State machine | Toast on `verifier_error`; tag verified scores in the journal line |
| `tests/test_wakeword.py` | Engine + config unit tests | New tests (Tasks 1–3) |
| `tests/test_listener.py` | State-machine tests | New tests (Task 4) |
| `benchmark/train_v2_verifier.py` | Trains the verifier | Write output to `DATA_DIR/wakewords/` |
| `ARCHITECTURE.md` | Durable invariants | Extend the wake invariant |

---

### Task 1: `wake_verifier` config key

**Files:**
- Modify: `core/commands.py` (add accessor after `get_wake_vad_threshold()`, ~line 635; add default in the `--emit-defaults` block, ~line 1458)
- Test: `tests/test_wakeword.py`

**Interfaces:**
- Consumes: `core.paths.DATA_DIR` (already imported in `core/commands.py` as `paths`)
- Produces: `commands.get_wake_verifier_path() -> str | None` — absolute
  path to `DATA_DIR/wakewords/<stem>.pkl`, or `None` when the key is
  empty/whitespace. Tasks 2 and 3 call this.

- [ ] **Step 1: Write the failing tests**

Append to the "Config keys" section of `tests/test_wakeword.py` (after
`test_wake_model_path_and_threshold`):

```python
def test_wake_verifier_defaults_to_off():
    # Shipped default is no verifier: a fresh install behaves exactly as
    # it did before this feature existed.
    commands._config = {}
    assert commands.get_wake_verifier_path() is None


def test_wake_verifier_blank_string_is_off():
    commands._config = {"wake_verifier": "   "}
    assert commands.get_wake_verifier_path() is None


def test_wake_verifier_path_resolves_stem_to_pkl():
    commands._config = {"wake_verifier": "computer_v2_verifier"}
    assert commands.get_wake_verifier_path() == os.path.join(
        paths.DATA_DIR, "wakewords", "computer_v2_verifier.pkl"
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_wakeword.py -q -k wake_verifier`
Expected: FAIL — `AttributeError: module 'core.commands' has no attribute 'get_wake_verifier_path'`

- [ ] **Step 3: Add the accessor**

In `core/commands.py`, immediately after `get_wake_vad_threshold()`:

```python
def get_wake_verifier_path() -> str | None:
    """Speaker verifier for the audio wake engine: a second-stage
    classifier that re-scores any frame the base model would fire on,
    answering "is this the enrolled speaker saying the wake word?".

    It is a VOICEPRINT -- trained locally per user
    (benchmark/train_v2_verifier.py), so it is user data and ships with
    nothing. Empty (the shipped default) = off. Same drop-in directory
    as the .onnx wake models; 'wake_verifier' is the file stem."""
    name = str(_config.get("wake_verifier", "")).strip()
    if not name:
        return None
    return os.path.join(paths.DATA_DIR, "wakewords", f"{name}.pkl")
```

- [ ] **Step 4: Add the shipped default**

In the `--emit-defaults` config dict, directly after the
`"wake_vad_threshold": 0.5,` line:

```python
            "wake_verifier": "",
```

- [ ] **Step 5: Run the gauntlet**

Run: `python -m pytest -q && python -m compileall -q core && python -m core.commands --emit-defaults >/dev/null 2>/dev/null && echo "✅ green"`
Expected: PASS, 216 tests, `✅ green`

- [ ] **Step 6: Verify the key actually reaches the emitted JSON**

Run: `python -m core.commands --emit-defaults 2>/dev/null | jq '.wake_verifier'`
Expected: `""` (empty string, not `null` — `null` means the key is missing)

- [ ] **Step 7: Commit**

```bash
git add core/commands.py tests/test_wakeword.py
git commit -m "feat: add wake_verifier config key (default off)"
```

---

### Task 2: Pass the verifier into openWakeWord

**Files:**
- Modify: `core/wakeword.py` (add `import os`; extend `_load_oww_model()`; extend `make_wake_engine_factory()`)
- Test: `tests/test_wakeword.py`

**Interfaces:**
- Consumes: `commands.get_wake_verifier_path()` from Task 1
- Produces: `_load_oww_model(model_path, vad_threshold=0.0, verifier_path=None, threshold=0.5)` — Task 3 monkeypatches this exact signature.

**Critical detail:** the `custom_verifier_models` dict key MUST be the
`.onnx` file stem (`"computer_v2"`), because that is the name oww gives
the loaded model. Any other key and oww logs a warning, ignores the
verifier entirely, and the wake word runs unprotected while the config
claims otherwise — a silent failure, which is why Step 1 tests the key.

- [ ] **Step 1: Write the failing tests**

Append to the "Factory" section of `tests/test_wakeword.py`:

```python
def test_no_verifier_kwargs_when_unset(monkeypatch):
    commands._config = {
        "wake_engine": "openwakeword",
        "wake_model": "computer_v2",
    }
    built = {}

    def fake_load(model_path, vad_threshold=0.0, verifier_path=None,
                  threshold=0.5):
        built["verifier_path"] = verifier_path
        return FakeOwwModel(scores=[0.0])

    monkeypatch.setattr(wakeword, "_load_oww_model", fake_load)
    wakeword.make_wake_engine_factory()()
    assert built["verifier_path"] is None


def test_verifier_path_and_gate_are_passed(monkeypatch):
    commands._config = {
        "wake_engine": "openwakeword",
        "wake_model": "computer_v2",
        "wake_threshold": 0.5,
        "wake_verifier": "computer_v2_verifier",
    }
    built = {}

    def fake_load(model_path, vad_threshold=0.0, verifier_path=None,
                  threshold=0.5):
        built["verifier_path"] = verifier_path
        built["threshold"] = threshold
        return FakeOwwModel(scores=[0.0])

    monkeypatch.setattr(wakeword, "_load_oww_model", fake_load)
    eng = wakeword.make_wake_engine_factory()()
    assert built["verifier_path"].endswith("wakewords/computer_v2_verifier.pkl")
    # The gate equals the fire threshold: only frames that WOULD fire get
    # a second opinion, so the verifier can only veto, never promote.
    assert built["threshold"] == 0.5
    assert eng.verified is True


def test_gate_tracks_a_non_default_threshold(monkeypatch):
    # Guards the footgun: a gate ABOVE wake_threshold silently creates a
    # window of unverified fires. It must follow the threshold, always.
    commands._config = {
        "wake_engine": "openwakeword",
        "wake_model": "computer_v2",
        "wake_threshold": 0.75,
        "wake_verifier": "computer_v2_verifier",
    }
    built = {}

    def fake_load(model_path, vad_threshold=0.0, verifier_path=None,
                  threshold=0.5):
        built["threshold"] = threshold
        return FakeOwwModel(scores=[0.0])

    monkeypatch.setattr(wakeword, "_load_oww_model", fake_load)
    wakeword.make_wake_engine_factory()()
    assert built["threshold"] == 0.75


def test_verifier_dict_is_keyed_by_onnx_stem(monkeypatch):
    # oww keys custom_verifier_models by the BASE MODEL's name. A
    # mismatch is ignored with only a warning -- unprotected while the
    # config says otherwise.
    captured = {}

    class FakeModel:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    # `from openwakeword.model import Model` resolves through sys.modules,
    # so a stub there intercepts the deferred import without the real
    # package being installed. `sys` is already imported at the top of
    # this test file.
    monkeypatch.setitem(
        sys.modules, "openwakeword.model", type("m", (), {"Model": FakeModel})
    )
    wakeword._load_oww_model(
        "/data/wakewords/computer_v2.onnx",
        vad_threshold=0.5,
        verifier_path="/data/wakewords/computer_v2_verifier.pkl",
        threshold=0.5,
    )
    assert captured["custom_verifier_models"] == {
        "computer_v2": "/data/wakewords/computer_v2_verifier.pkl"
    }
    assert captured["custom_verifier_threshold"] == 0.5
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_wakeword.py -q -k "verifier or gate"`
Expected: FAIL — `_load_oww_model()` got an unexpected keyword argument `verifier_path`

- [ ] **Step 3: Extend `_load_oww_model`**

Add `import os` at the top of `core/wakeword.py` (above `import core.commands as commands`), then replace `_load_oww_model` with:

```python
def _load_oww_model(model_path: str, vad_threshold: float = 0.0,
                    verifier_path: str | None = None,
                    threshold: float = 0.5):
    """Build the openwakeword Model (separated for test injection).

    vad_threshold > 0 turns on oww's bundled Silero speech gate: a
    frame's score is forced to 0 unless the VAD saw speech just before
    it. Its onnxruntime session is explicitly 1-thread upstream, so it
    can't reintroduce the s33 spin-pool CPU burn.

    verifier_path adds the second-stage speaker verifier. Read
    openwakeword/model.py:319-328 before touching this: its
    `custom_verifier_threshold` is NOT a reject threshold -- it is the
    base score above which the verifier runs and REPLACES the score.
    We pin it to our own fire threshold so the verifier only ever sees
    frames that would have fired: it can veto, never promote, and there
    is no unverified window above it."""
    from openwakeword.model import Model  # deferred import

    kwargs = {"wakeword_models": [model_path], "inference_framework": "onnx"}
    if vad_threshold > 0:
        kwargs["vad_threshold"] = vad_threshold
    if verifier_path:
        # Key MUST be the .onnx stem -- that's the name oww gives the
        # loaded base model. Mismatch = oww warns and silently ignores
        # the verifier, leaving the wake word unprotected.
        stem = os.path.splitext(os.path.basename(model_path))[0]
        kwargs["custom_verifier_models"] = {stem: verifier_path}
        kwargs["custom_verifier_threshold"] = threshold
    return Model(**kwargs)
```

- [ ] **Step 4: Record whether a verifier is active on the engine**

In `OpenWakeWordEngine.__init__`, extend the signature and add one
attribute (keep everything else as-is):

```python
    def __init__(self, model, threshold: float, verified: bool = False):
        import numpy as np

        self._np = np
        self._model = model
        self._threshold = threshold
        self._buffer = np.array([], dtype=np.int16)
        # Score of the frame that fired, for the log line. Debugging the
        # s33 false fires was blind without it: "wake detected" alone
        # can't distinguish a confident hit from a threshold squeaker.
        self.last_score = 0.0
        # True when a verifier is active, which means last_score is the
        # VERIFIER's probability (~0.68-0.95 on genuine wakes), not the
        # base model's (~0.99). Different quantities from different
        # models -- the journal line must say which one it printed.
        self.verified = verified
        # Set by the factory when a CONFIGURED verifier failed to load.
        self.verifier_error: str | None = None
```

- [ ] **Step 5: Wire the factory**

Replace `make_wake_engine_factory()`'s body with:

```python
    if commands.get_wake_engine() != "openwakeword":
        return None

    model_path = commands.get_wake_model_path()
    threshold = commands.get_wake_threshold()
    vad_threshold = commands.get_wake_vad_threshold()
    verifier_path = commands.get_wake_verifier_path()

    def factory():
        return OpenWakeWordEngine(
            model=_load_oww_model(model_path, vad_threshold,
                                  verifier_path, threshold),
            threshold=threshold,
            verified=bool(verifier_path),
        )

    return factory
```

- [ ] **Step 6: Run the gauntlet**

Run: `python -m pytest -q && python -m compileall -q core && python -m core.commands --emit-defaults >/dev/null 2>/dev/null && echo "✅ green"`
Expected: PASS, 220 tests, `✅ green`

- [ ] **Step 7: Commit**

```bash
git add core/wakeword.py tests/test_wakeword.py
git commit -m "feat: run the speaker verifier on frames that would fire

Gate is pinned to wake_threshold so the verifier can only veto a fire,
never create one, and no unverified window can open above it."
```

---

### Task 3: A failed verifier load keeps openWakeWord alive

**Files:**
- Modify: `core/wakeword.py` (`make_wake_engine_factory()` only)
- Test: `tests/test_wakeword.py`

**Interfaces:**
- Produces: `engine.verifier_error: str | None` — set to a message when a
  configured verifier could not be loaded. Task 4's listener reads it.

**Why this shape:** `core/listener.py:258-265` already catches *any*
wake-engine construction failure and degrades to **text wake**. Letting
a bad pickle escape would mean losing the ~150ms ack over a 50KB file —
the regression Tyler explicitly rejected. So the factory swallows
verifier-specific failure, rebuilds without it, and reports. A dead
*base* model still escapes and still degrades to text wake, which is
correct: there is no wake engine at all in that case.

- [ ] **Step 1: Write the failing tests**

Append to the "Factory" section of `tests/test_wakeword.py`:

```python
def test_verifier_load_failure_yields_unverified_engine(monkeypatch):
    # A missing/corrupt pickle must NOT take the wake engine down: that
    # would trip the listener's text-wake degrade and cost the ~150ms
    # ack, which is a worse regression than the false fires.
    commands._config = {
        "wake_engine": "openwakeword",
        "wake_model": "computer_v2",
        "wake_verifier": "computer_v2_verifier",
    }
    calls = []

    def fake_load(model_path, vad_threshold=0.0, verifier_path=None,
                  threshold=0.5):
        calls.append(verifier_path)
        if verifier_path:
            raise FileNotFoundError(verifier_path)
        return FakeOwwModel(scores=[0.0])

    monkeypatch.setattr(wakeword, "_load_oww_model", fake_load)
    eng = wakeword.make_wake_engine_factory()()

    assert eng.verifier_error is not None
    assert "computer_v2_verifier" in eng.verifier_error
    assert eng.verified is False           # don't claim protection we lost
    assert calls[-1] is None               # retried WITHOUT the verifier
    assert eng.feed(b"\x00" * 2560) is False   # engine is alive and usable


def test_base_model_failure_still_propagates(monkeypatch):
    # No verifier involved -- a dead base model has no working engine to
    # fall back to, so it must reach the listener's text-wake degrade.
    commands._config = {
        "wake_engine": "openwakeword",
        "wake_model": "computer_v2",
    }

    def fake_load(model_path, vad_threshold=0.0, verifier_path=None,
                  threshold=0.5):
        raise OSError("no such model")

    monkeypatch.setattr(wakeword, "_load_oww_model", fake_load)
    with pytest.raises(OSError):
        wakeword.make_wake_engine_factory()()


def test_healthy_verifier_leaves_no_error(monkeypatch):
    commands._config = {
        "wake_engine": "openwakeword",
        "wake_model": "computer_v2",
        "wake_verifier": "computer_v2_verifier",
    }
    monkeypatch.setattr(
        wakeword, "_load_oww_model",
        lambda *a, **k: FakeOwwModel(scores=[0.0]),
    )
    eng = wakeword.make_wake_engine_factory()()
    assert eng.verifier_error is None
    assert eng.verified is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_wakeword.py -q -k "failure or healthy_verifier"`
Expected: FAIL — `test_verifier_load_failure_yields_unverified_engine` raises `FileNotFoundError` instead of returning an engine

- [ ] **Step 3: Add the fallback to the factory**

Replace the `factory()` function written in Task 2 Step 5 with:

```python
    def factory():
        model = None
        verifier_error = None
        if verifier_path:
            try:
                model = _load_oww_model(model_path, vad_threshold,
                                        verifier_path, threshold)
            except Exception as e:
                # A dead verifier costs us false-fire protection. A dead
                # wake ENGINE costs us the fast ack entirely, so never
                # trade the second for the first -- rebuild unverified
                # and let the listener shout about it.
                verifier_error = f"{os.path.basename(verifier_path)}: {e}"
                print(f"[wakeword] Verifier failed to load, running "
                      f"UNVERIFIED: {e}")
        if model is None:
            # No verifier configured, or the verified build just failed.
            # If THIS raises, the base model is genuinely dead and the
            # listener's text-wake degrade is the right answer.
            model = _load_oww_model(model_path, vad_threshold)
        engine = OpenWakeWordEngine(
            model=model,
            threshold=threshold,
            verified=bool(verifier_path) and verifier_error is None,
        )
        engine.verifier_error = verifier_error
        return engine
```

- [ ] **Step 4: Run the gauntlet**

Run: `python -m pytest -q && python -m compileall -q core && python -m core.commands --emit-defaults >/dev/null 2>/dev/null && echo "✅ green"`
Expected: PASS, 223 tests, `✅ green`

- [ ] **Step 5: Commit**

```bash
git add core/wakeword.py tests/test_wakeword.py
git commit -m "fix: a broken verifier degrades to unverified, not to text wake

Losing the ~150ms audio ack over a 50KB pickle is a worse regression
than the false fires the verifier prevents. A dead base model still
propagates to the listener's text-wake fallback."
```

---

### Task 4: Listener surfaces the failure and tags verified scores

**Files:**
- Modify: `core/listener.py:257-265` (engine construction) and `core/listener.py:359-373` (audio wake branch)
- Test: `tests/test_listener.py`

**Interfaces:**
- Consumes: `engine.verifier_error` and `engine.verified` from Tasks 2–3

**Two rules to preserve:**
1. Use `_notify`, **not** `_notify_general`. `_notify_general` is
   suppressed by the Options-tab notifications toggle; "your wake word
   is unprotected" must not be silenceable. This matches the existing
   wake-engine-failure toast three lines above.
2. Do **not** add the score to `log()`. The user-facing Settings log
   deliberately omits it (commit 66b8214) — the score is journal-only
   diagnostics.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_listener.py`, after the existing wake-engine tests:

```python
class FakeWakeEngine:
    """Seam-conformant audio wake engine: feed(chunk) -> fired?."""

    def __init__(self, fire_on=None, verified=False, verifier_error=None):
        self.calls = 0
        self.fire_on = fire_on
        self.last_score = 0.82
        self.verified = verified
        self.verifier_error = verifier_error

    def feed(self, data):
        fired = self.calls == self.fire_on
        self.calls += 1
        return fired


def test_verifier_load_failure_toasts_loudly(monkeypatch):
    # Unprotected wake must be impossible to miss -- and impossible to
    # silence via the notifications toggle (_notify, not _notify_general).
    eng = FakeWakeEngine(verifier_error="computer_v2_verifier.pkl: boom")
    r = run_machine(
        [RecognizerEvent("partial", ""), RecognizerEvent("partial", "")],
        monkeypatch,
        wake_engine=eng,
    )
    assert any("Wake verifier failed" in n[0] for n in r["notifications"])


def test_healthy_verifier_does_not_toast(monkeypatch):
    eng = FakeWakeEngine(verified=True)
    r = run_machine(
        [RecognizerEvent("partial", ""), RecognizerEvent("partial", "")],
        monkeypatch,
        wake_engine=eng,
    )
    assert not any("verifier" in n[0].lower() for n in r["notifications"])


def test_verified_audio_wake_still_reaches_listening(monkeypatch):
    # The verifier changes what last_score MEANS, not the wake path.
    eng = FakeWakeEngine(fire_on=0, verified=True)
    r = run_machine(
        [RecognizerEvent("partial", ""), RecognizerEvent("partial", "")],
        monkeypatch,
        wake_engine=eng,
    )
    assert State.LISTENING in r["states"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_listener.py -q -k "verifier or verified"`
Expected: FAIL — `test_verifier_load_failure_toasts_loudly` finds no matching notification

- [ ] **Step 3: Toast on a failed verifier**

In `core/listener.py`, extend the engine-construction block (currently
lines 257-265) by adding an `else` clause to the existing `try`:

```python
    wake_engine = None
    if wake_engine_factory is not None:
        try:
            wake_engine = wake_engine_factory()
        except Exception as e:
            print(f"[listener] Wake engine failed, falling back to text wake: {e}")
            _notify("Wake engine failed",
                    "Falling back to transcription-based wake -- check the journal.",
                    gui_env=gui_env)
        else:
            # The engine is alive but a CONFIGURED verifier didn't load,
            # so the wake word is running without its false-fire filter.
            # _notify, not _notify_general: this must not be silenceable
            # by the notifications toggle.
            if getattr(wake_engine, "verifier_error", None):
                print("[listener] Wake verifier failed to load, wake word is "
                      f"UNVERIFIED: {wake_engine.verifier_error}")
                _notify("Wake verifier failed",
                        "Wake word is running unverified -- retrain the verifier.",
                        gui_env=gui_env)
```

- [ ] **Step 4: Tag the journal score**

In the audio wake branch, replace the score/print lines (currently 367-368):

```python
                # Score goes to the JOURNAL only -- a false fire and a genuine
                # wake look identical without it (s33 spent a day blind), but
                # it's diagnostic noise in the user-facing Settings log, which
                # stays identical to the other two wake paths below.
                # "verified" marks a VERIFIER probability (~0.68-0.95 on a
                # genuine wake), not a base-model score (~0.99) -- never
                # compare the two across that boundary.
                score = getattr(wake_engine, "last_score", 0.0)
                tag = " verified" if getattr(wake_engine, "verified", False) else ""
                print(f"[listener] Wake word detected (audio engine,{tag} score {score:.3f}).")
```

- [ ] **Step 5: Run the gauntlet**

Run: `python -m pytest -q && python -m compileall -q core && python -m core.commands --emit-defaults >/dev/null 2>/dev/null && echo "✅ green"`
Expected: PASS, 226 tests, `✅ green`

- [ ] **Step 6: Commit**

```bash
git add core/listener.py tests/test_listener.py
git commit -m "feat: shout when the wake verifier fails; tag verified scores

A verified genuine wake logs ~0.68-0.95 where an unverified one logs
~0.99. Unmarked, that reads in the journal as the wake model getting
worse."
```

---

### Task 5: Give the verifier and its training clips a durable home

**Files:**
- Modify: `benchmark/train_v2_verifier.py:28-30`

**Why:** the `.pkl` and every training clip currently live only in
`benchmark/`, which `.gitignore:44` excludes wholesale — one untracked
directory on one machine holding both the model and the only means of
rebuilding it.

- [ ] **Step 1: Write the verifier to the install's wakewords dir**

Replace lines 28-30 of `benchmark/train_v2_verifier.py`:

```python
BENCH = os.path.dirname(os.path.abspath(__file__))
# core/paths.py is the single source of install identity and is
# stdlib-only, so importing it here costs nothing and can't drift.
sys.path.insert(0, os.path.dirname(BENCH))
import core.paths as paths  # noqa: E402

V2 = f"{BENCH}/wakewords/computer_v2.onnx"
# Deploy target: the drop-in dir the app reads, alongside the .onnx
# models. install.sh skips files already present here, so a reinstall
# will not clobber it. The benchmark copy stays for the offline
# evaluation table this script prints.
OUT = os.path.join(paths.DATA_DIR, "wakewords", "computer_v2_verifier.pkl")
os.makedirs(os.path.dirname(OUT), exist_ok=True)
```

- [ ] **Step 2: Verify the script still parses and resolves the path**

Run: `python -c "import ast,sys; ast.parse(open('benchmark/train_v2_verifier.py').read()); print('parses')"`
Expected: `parses`

Run: `python -c "import core.paths as p, os; print(os.path.join(p.DATA_DIR,'wakewords','computer_v2_verifier.pkl'))"`
Expected: `/home/trixles/.local/share/voice-commander/wakewords/computer_v2_verifier.pkl`

(Do not run the training script itself here — it needs the benchmark
venv and its clips. Tyler runs it during the live-soak step.)

- [ ] **Step 3: Back up the training inputs (1.7 MB)**

```bash
cp -a benchmark/verifier_clips ~/Development/Backups/Voice-Commander/verifier_clips
```

Verify: `du -sh ~/Development/Backups/Voice-Commander/verifier_clips`
Expected: `1.7M` — matches the source. `benchmark/soak/` (6.1 MB of
recorded evidence) stays out by decision.

- [ ] **Step 4: Commit**

```bash
git add benchmark/train_v2_verifier.py
git commit -m "chore: train the verifier straight into the install's wakewords dir

The .pkl and its training clips lived only in gitignored benchmark/ --
one untracked directory holding both the model and the only means of
rebuilding it."
```

---

### Task 6: Document the invariant

**Files:**
- Modify: `ARCHITECTURE.md` (the "Wake is acknowledged on partials…" invariant, line 892 — extend the "Audio wake engine (s31)" bullet and the "Don't" list)
- Modify: `HANDOFF.md` (Current state — note the feature is built, awaiting live soak)

- [ ] **Step 1: Add the verifier paragraph**

Append to the **Audio wake engine (s31)** bullet, before the `- **Why:**`
line:

```markdown
  - **Speaker verifier (s34):** config `wake_verifier` (file stem in
    `DATA_DIR/wakewords/`, `.pkl`, empty = off, shipped off) adds
    openWakeWord's second-stage verifier: a frame scoring at or above
    `custom_verifier_threshold` is re-scored by the verifier, whose
    output REPLACES the base score. VC pins that gate to
    `wake_threshold`, so the verifier only ever sees frames that would
    have fired -- it can veto, never promote. It exists to kill VOCAL
    false fires the Silero gate cannot touch (coughs at 0.567-0.998,
    measured 2026-07-28; Silero correctly rates a cough as speech). The
    `.pkl` is a VOICEPRINT: user data, trained per-user by
    `benchmark/train_v2_verifier.py`, shipped with nothing. A verifier
    that fails to load leaves oww running UNVERIFIED with an always-on
    toast -- it does NOT degrade to text wake, because losing the fast
    ack is the worse regression.
```

- [ ] **Step 2: Add the three "Don't" entries**

Append to that invariant's `- **Don't:**` paragraph:

```markdown
  Set `custom_verifier_threshold` above `wake_threshold` (opens a window
  of unverified fires -- the measured 0.567 cough lives there). Key
  `custom_verifier_models` by anything but the `.onnx` stem (oww ignores
  a mismatch with only a warning, leaving the wake word unprotected
  while the config claims otherwise). Compare a verified score against
  an unverified one -- different quantities from different models. Let a
  failed verifier load reach the listener's text-wake degrade. Commit a
  `.pkl` to the repo.
```

- [ ] **Step 3: Sync HANDOFF.md**

Change priority 1's heading from `IN PROGRESS (s34)` to
`CODE COMPLETE (s34) — AWAITING LIVE SOAK`, and add this to **Current
state**:

```markdown
Wake speaker verifier is built and committed but NOT yet certified:
set `wake_verifier: "computer_v2_verifier"` after a deploy, then run a
quiet-room soak. Journal lines must read "verified score" — if they
don't, the verifier isn't loaded. Plan and spec:
`docs/superpowers/plans/2026-07-28-wake-verifier.md`.
```

Keep the file at ≤200 lines — retire the now-redundant detail from
priority 1's body, since the spec and plan hold it.

- [ ] **Step 4: Verify the invariant index still lists cleanly**

Run: `grep -c "^### " ARCHITECTURE.md`
Expected: unchanged from before this task (the verifier extends an
existing invariant; it does not add a new `###` heading)

- [ ] **Step 5: Commit**

```bash
git add ARCHITECTURE.md
git commit -m "docs: record the wake-verifier invariant and its footguns"
```

---

## Acceptance

Code-complete when Tasks 1–6 are committed and the gauntlet is green at
226 tests.

**The feature is NOT certified by any of that.** Per the standing
methodology law, offline scoring cannot certify wake behavior — only 3
of 8 live fires reproduced when re-scored from their own saved audio.
Acceptance is a **live soak** (`benchmark/soak_wakeword.py`), run by
Tyler after an explicitly approved `./install.sh` deploy:

1. Set `wake_verifier: "computer_v2_verifier"` in the live config.
2. Confirm the journal now prints `verified` on wake lines — if it
   doesn't, the verifier is not actually loaded.
3. Quiet-room false fires at or near zero, coughs included.
4. Genuine wakes still fire at normal conversational distance.

If recall drops at distance, the first knob to reach for is **not** the
gate — it is retraining with more positives, or the deferred second knob
from the spec.
