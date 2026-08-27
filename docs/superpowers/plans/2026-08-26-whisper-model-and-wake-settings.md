# Whisper Model tab + baked-in wake word — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the Vosk-era Model tab to a whisper `.bin` picker, and make `"computer"` a baked-in, unremovable wake word so custom words are purely additive.

**Architecture:** Two independent surfaces. (A) `get_whisper_model_path()` gains path-or-token behavior; the Model tab browses to a `.bin` file. (B) `get_wake_words()` prepends a structural `BAKED_IN_WAKE_WORDS = ["computer"]`; config stores only custom words; the Commands tab renders `"computer"` as a locked greyed prefix.

**Tech Stack:** Python 3.14, PySide6 (Qt6), pytest. Whisper via whisper.cpp `ggml-*.bin` weights served by a systemd user unit.

## Global Constraints

- `core/wake.py` stays pure stdlib (`re`, `difflib` only) — no PySide6, no imports from `core.commands`. `BAKED_IN_WAKE_WORDS` is plain data and belongs here.
- `core/commands.py` must stay importable without PySide6 (it runs under `--emit-defaults` in `install.sh`). Importing `core.wake` is safe (pure stdlib); never import a Qt module into it.
- Tests must stay green. Baseline is 257 passing (`pytest -q`).
- Exact Commands-tab blurb, verbatim: `Say "computer" to wake Voice Commander from sleep to listen for commands. You can also add custom wake words below, but they will be slightly slower to activate than using "computer". Separate multiple words or phrases with commas.`
- Copy is sentence case, de-Vosked. Model download link is `https://huggingface.co/ggerganov/whisper.cpp`.
- Do NOT touch `recognizer_backend` or `get_vosk_model_path()` (Vosk scrub is a separate, deferred change). This plan only stops the Model tab from *using/mentioning* Vosk.
- Deploy only via `./install.sh` after an explicit go-ahead; this plan does not deploy.

## File Structure

- `core/commands.py` — modify `get_whisper_model_path()` (path-or-token); modify `get_wake_words()` (inject baked-in); modify `--emit-defaults` block (customs-only default).
- `core/wake.py` — add `BAKED_IN_WAKE_WORDS` constant. Detector class unchanged.
- `core/settings/tabs/model_tab.py` — rewrite build() for whisper (`.bin` file, de-Vosked copy, HF link).
- `core/settings/tabs/commands_tab.py` — rewrite the Wake Words section (blurb + locked greyed `"computer"` prefix fused to a borderless custom-words field).
- `core/settings/dialog.py` — `_browse_vosk_model` → `_browse_whisper_model` (file, not dir); `__init__` originals; `_save` writes `whisper_model` + calls `ensure_server()`, stores customs-only `wake_words`.
- Tests: `tests/test_whisper_model_path.py`, `tests/test_wake_baked_in.py` (new — the only new test files; Tasks 4–5 add no unit tests, see Testability note).

**Testability note:** the project's Qt tests exercise *containers and pure helpers* directly (see `tests/test_settings_roundtrip.py`, `tests/test_overrides_roundtrip.py`) — `SettingsDialog` takes no `config` arg and its `_save` is not unit-tested. So this plan pushes every bug-prone decision into pure functions in `core/wake.py` / `core/commands.py` and full-TDDs those (Tasks 1–3, plus the `custom_wake_words` helper). The Qt wiring in Tasks 4–5 (which just calls those tested helpers) is verified by: the suite still importing/passing, plus a `Manual verify` deploy-and-eyeball step. Do NOT invent a `SettingsDialog(config=...)` constructor or a `_collect_new_config` seam — neither exists.

---

### Task 1: `get_whisper_model_path()` — accept a path or a size token

**Files:**
- Modify: `core/commands.py:593-597`
- Test: `tests/test_whisper_model_path.py` (create)

**Interfaces:**
- Produces: `get_whisper_model_path() -> str` — returns the config value as-is when it contains a path separator, else derives `MODELS_DIR/whisper/ggml-{token}.bin`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_whisper_model_path.py
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import core.commands as commands


def _with_config(cfg):
    saved = commands._config
    commands._config = cfg
    return saved


def test_token_derives_ggml_path():
    saved = _with_config({"whisper_model": "base.en"})
    try:
        p = commands.get_whisper_model_path()
        assert p.endswith(os.path.join("whisper", "ggml-base.en.bin"))
        assert os.sep in p
    finally:
        commands._config = saved


def test_absolute_path_returned_as_is():
    custom = os.path.join(os.sep, "srv", "models", "ggml-small.en.bin")
    saved = _with_config({"whisper_model": custom})
    try:
        assert commands.get_whisper_model_path() == custom
    finally:
        commands._config = saved


def test_missing_key_defaults_to_base_en():
    saved = _with_config({})
    try:
        assert commands.get_whisper_model_path().endswith("ggml-base.en.bin")
    finally:
        commands._config = saved
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_whisper_model_path.py -v`
Expected: FAIL — `test_absolute_path_returned_as_is` fails (current code derives `ggml-{path}.bin` from the whole path).

- [ ] **Step 3: Write minimal implementation**

Replace `core/commands.py:593-597` with:

```python
def get_whisper_model_path() -> str:
    """Absolute path to the ggml weights whisper-server should load.

    'whisper_model' is either a size token ('base.en', 'small.en', ...) that
    derives MODELS_DIR/whisper/ggml-{token}.bin, OR a full filesystem path the
    user browsed to. A value containing a path separator is treated as a path;
    anything else is a token."""
    raw = str(_config.get("whisper_model", "base.en")).strip() or "base.en"
    if os.sep in raw:
        return raw
    return os.path.join(_MODELS_DIR, "whisper", f"ggml-{raw}.bin")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_whisper_model_path.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add core/commands.py tests/test_whisper_model_path.py
git commit -m "feat: get_whisper_model_path accepts a browsed .bin path or a size token"
```

---

### Task 2: Baked-in `"computer"` wake word

**Files:**
- Modify: `core/wake.py` (add constant near top, after imports)
- Modify: `core/commands.py:486-492` (`get_wake_words`)
- Test: `tests/test_wake_baked_in.py` (create)

**Interfaces:**
- Produces: `core.wake.BAKED_IN_WAKE_WORDS: list[str]` == `["computer"]`.
- Produces: `core.wake.custom_wake_words(words: list[str]) -> list[str]` — the input list lowercased/stripped with any baked-in word removed, order preserved. Shared by the UI (display), the dialog save, and `get_wake_words`.
- Produces: `commands.get_wake_words() -> list[str]` — always `["computer"] + custom_wake_words(stored)`, deduped case-insensitively, `"computer"` first. Stored comes from `_config["wake_words"]` (or legacy `wake_word`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wake_baked_in.py
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import core.commands as commands
from core.wake import BAKED_IN_WAKE_WORDS


def _with_config(cfg):
    saved = commands._config
    commands._config = cfg
    return saved


def test_baked_in_constant():
    assert BAKED_IN_WAKE_WORDS == ["computer"]


def test_custom_wake_words_strips_baked_in():
    from core.wake import custom_wake_words
    assert custom_wake_words(["computer", "hey dude"]) == ["hey dude"]
    assert custom_wake_words(["Computer", "JARVIS", "hey dude"]) == ["jarvis", "hey dude"]
    assert custom_wake_words(["computer"]) == []
    assert custom_wake_words([]) == []


def test_empty_config_yields_just_computer():
    saved = _with_config({})
    try:
        assert commands.get_wake_words() == ["computer"]
    finally:
        commands._config = saved


def test_customs_are_appended_after_computer():
    saved = _with_config({"wake_words": ["hey dude"]})
    try:
        assert commands.get_wake_words() == ["computer", "hey dude"]
    finally:
        commands._config = saved


def test_stored_computer_is_deduped_case_insensitively():
    saved = _with_config({"wake_words": ["Computer", "HEY DUDE"]})
    try:
        assert commands.get_wake_words() == ["computer", "hey dude"]
    finally:
        commands._config = saved
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_wake_baked_in.py -v`
Expected: FAIL — `ImportError` on `BAKED_IN_WAKE_WORDS` / order and dedupe assertions fail.

- [ ] **Step 3: Write minimal implementation**

In `core/wake.py`, after the `import` lines (after line 15), add:

```python
# The wake word the shipped openWakeWord model (computer_v2) detects. It is
# structural, not user data: it is always active (audio + text paths) and is
# prepended to the user's custom wake words. Kept here beside the detector so
# the module stays pure stdlib and every detector consumer sees one source.
BAKED_IN_WAKE_WORDS = ["computer"]


def custom_wake_words(words: list[str]) -> list[str]:
    """The user's custom wake words: `words` lowercased/stripped with any
    baked-in word removed, order preserved. One source of truth for what the
    Commands-tab field shows, what a save stores, and what get_wake_words
    appends after the baked-in word."""
    baked = set(BAKED_IN_WAKE_WORDS)
    out = []
    for w in words:
        s = (w or "").strip().lower()
        if s and s not in baked:
            out.append(s)
    return out
```

Replace `core/commands.py:486-492` (`get_wake_words`) with:

```python
def get_wake_words() -> list[str]:
    """Effective wake words: the baked-in word(s) followed by the user's
    custom words. 'computer' is always present and cannot be removed, so the
    openWakeWord audio path always has its word; customs are additive. Config
    stores customs only, but custom_wake_words() still filters a stray
    'computer' for back-compat with configs written before this was
    structural; we then dedupe so a repeated custom can't double."""
    raw = _config.get("wake_words")
    if not (isinstance(raw, list) and raw):
        raw = [_config.get("wake_word", "computer")]

    result = list(BAKED_IN_WAKE_WORDS)
    seen = set(result)
    for w in custom_wake_words(raw):
        if w not in seen:
            result.append(w)
            seen.add(w)
    return result
```

Add the import near the other `from core.*` imports at the top of `core/commands.py`:

```python
from core.wake import BAKED_IN_WAKE_WORDS, custom_wake_words
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_wake_baked_in.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Verify emit-defaults still works without Qt**

Run: `python -m core.commands --emit-defaults > /dev/null && echo OK`
Expected: `OK` (proves the new `core.wake` import didn't drag in PySide6).

- [ ] **Step 6: Commit**

```bash
git add core/wake.py core/commands.py tests/test_wake_baked_in.py
git commit -m "feat: bake in 'computer' as a structural, unremovable wake word"
```

---

### Task 3: Ship `wake_words = ["hey dude"]` as the customs-only default

**Files:**
- Modify: `core/commands.py:1506`
- Test: `tests/test_wake_baked_in.py` (extend)

**Interfaces:**
- Consumes: `get_wake_words()` from Task 2.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_wake_baked_in.py`:

```python
import json
import subprocess


def test_emit_defaults_ships_customs_only():
    out = subprocess.run(
        [sys.executable, "-m", "core.commands", "--emit-defaults"],
        capture_output=True, text=True, check=True,
        cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "..")),
    ).stdout
    cfg = json.loads(out)
    assert cfg["wake_words"] == ["hey dude"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_wake_baked_in.py::test_emit_defaults_ships_customs_only -v`
Expected: FAIL — default is still `["computer", "hey dude"]`.

- [ ] **Step 3: Write minimal implementation**

Change `core/commands.py:1506` from:

```python
            "wake_words": ["computer", "hey dude"],
```

to:

```python
            "wake_words": ["hey dude"],
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_wake_baked_in.py -v`
Expected: PASS (5 tests). `get_wake_words()` on this default still yields `["computer", "hey dude"]`.

- [ ] **Step 5: Commit**

```bash
git add core/commands.py tests/test_wake_baked_in.py
git commit -m "feat: ship wake_words default as customs-only (hey dude)"
```

---

### Task 4: Model tab → whisper `.bin` picker

**Files:**
- Modify: `core/settings/tabs/model_tab.py` (rewrite `build`)
- Modify: `core/settings/dialog.py` — `_browse_vosk_model` → `_browse_whisper_model` (`262-291`); `__init__` original path (`131-137`); `_save` model key (`606-607`) + `ensure_server()`; imports.

**Interfaces:**
- Consumes: `get_whisper_model_path()` (Task 1 — already unit-tested).
- Produces: `_save` writes `new_config["whisper_model"] = <path>` (not `vosk_model`) when a model was browsed, and calls `whisper_server.ensure_server()`.

**No new unit test:** the path-or-token logic is covered by Task 1; the model getter/dialog wiring is Qt-bound and not unit-tested in this codebase (mirrors how `tests/test_settings_roundtrip.py` tests containers, not `SettingsDialog._save`). Guard = suite still green (imports/syntax) + the manual-verify step.

- [ ] **Step 1: Rewrite `core/settings/tabs/model_tab.py::build`**

Replace the tab body so it reads (de-Vosked copy, HF link, file path display):

```python
    tab, cl = dialog._make_scroll_tab()
    cl.addWidget(_section_label("Model"))

    model_blurb = QLabel(
        "Speech is transcribed by Whisper. Choose which model file to use below."
    )
    model_blurb.setWordWrap(True)
    model_blurb.setStyleSheet("color: #a6adc8; font-size: 9pt; padding: 0 4px 4px 4px;")
    cl.addWidget(model_blurb)

    cl.addWidget(_h_rule())

    path_row = QWidget()
    pr = QHBoxLayout(path_row)
    pr.setContentsMargins(4, 0, 4, 0)
    pr.setSpacing(8)

    dialog._model_path_label = QLabel()
    dialog._model_path_label.setWordWrap(True)
    dialog._model_path_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    dialog._model_path_label.setStyleSheet(
        "background-color: #282839; color: #cdd6f4; font-size: 9pt; "
        "border: 1px solid #45475a; border-radius: 4px; padding: 4px 6px;"
    )

    current_path = get_whisper_model_path()
    dialog._model_path_label.setText(current_path or "(using default)")
    dialog._selected_model_path = current_path

    browse_btn = QPushButton("Browse...")
    browse_btn.setFixedWidth(90)
    browse_btn.clicked.connect(dialog._browse_whisper_model)
    pr.addWidget(dialog._model_path_label)
    pr.addWidget(browse_btn)
    cl.addWidget(path_row)

    dialog._model_error_label = QLabel()
    dialog._model_error_label.setWordWrap(True)
    dialog._model_error_label.setStyleSheet(
        "color: #f38ba8; font-size: 9pt; font-style: italic; "
        "background-color: #2a1a1a; border: 1px solid #6c1a1a; "
        "border-radius: 4px; padding: 4px 6px;"
    )
    dialog._model_error_label.setVisible(False)
    cl.addWidget(dialog._model_error_label)

    cl.addSpacing(12)

    dl_blurb = QLabel(
        'Download models:<br><a href="https://huggingface.co/ggerganov/whisper.cpp" '
        'style="color: #89b4fa;">huggingface.co/ggerganov/whisper.cpp</a>'
    )
    dl_blurb.setWordWrap(True)
    dl_blurb.setStyleSheet("color: #a6adc8; font-size: 9pt; padding: 0 4px;")
    dl_blurb.setOpenExternalLinks(True)
    cl.addWidget(dl_blurb)

    cl.addStretch()
    return tab
```

Change the import at the top of `model_tab.py` from `get_vosk_model_path` to `get_whisper_model_path`, and update the module docstring to whisper (drop the `try/except ValueError` around the old getter — `get_whisper_model_path()` does not raise).

- [ ] **Step 2: Rewrite the dialog browse method + save**

In `core/settings/dialog.py`, replace `_browse_vosk_model` (262-291) with:

```python
    def _browse_whisper_model(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "Select a Whisper model file",
            os.path.dirname(self._selected_model_path) if self._selected_model_path else "",
            "Whisper models (*.bin)",
        )
        if not chosen:
            return
        if not (os.path.isfile(chosen) and chosen.endswith(".bin")):
            self._model_error_label.setText(
                "That doesn't look like a Whisper model. Pick a ggml-*.bin file."
            )
            self._model_error_label.setVisible(True)
            return
        self._model_error_label.setVisible(False)
        self._selected_model_path = chosen
        self._model_path_label.setText(chosen)
        self._check_restart_needed()
        self._mark_dirty()
        print(f"[settings] Whisper model selected: {chosen}")
```

In `__init__` (131-137), change the original model path to whisper:

```python
        self._orig_model_path = get_whisper_model_path()
```

and update the import on line 76 to `get_whisper_model_path` (keep `get_vosk_model_path` importable elsewhere if still referenced; if this was its only user, drop it from the import list).

In `_save`, replace the model-write block (606-607):

```python
        if self._selected_model_path:
            new_config["whisper_model"] = self._selected_model_path
```

and after `new_config` is assembled and before/around the existing restart handling, ensure the whisper unit reloads:

```python
        import core.whisper_server as whisper_server
        whisper_server.ensure_server()
```

(Place this so it runs whenever the dialog saves; `ensure_server` is a no-op restart when the env file is unchanged, so calling it unconditionally on save is safe.)

- [ ] **Step 3: Run the full suite**

Run: `pytest -q`
Expected: all green (was 257; now higher with the Task 1–3 tests). This proves the model_tab/dialog rewrite imports and parses cleanly.

- [ ] **Step 4: Manual verify (layout + copy)**

Ask Tyler for a deploy go-ahead, then `./install.sh && systemctl --user restart voice-commander.service`. Open Settings → Model tab. Confirm: whisper copy (no "Vosk"), the current `.bin` path shows, Browse opens a *file* dialog filtered to `*.bin`, the HF link is correct, and picking a `.bin` swaps the model (whisper unit restarts).

- [ ] **Step 5: Commit**

```bash
git add core/settings/tabs/model_tab.py core/settings/dialog.py
git commit -m "feat: Model tab picks a Whisper .bin file (de-Vosked, HF link)"
```

---

### Task 5: Commands tab — locked `"computer"` prefix + customs-only save

**Files:**
- Modify: `core/settings/tabs/commands_tab.py:1058-1085` (Wake Words section)
- Modify: `core/settings/dialog.py` — `__init__` `_orig_wake_words` (131-135); `_save` wake parse (572-574); `_check_restart_needed` (389)

**Interfaces:**
- Consumes: `core.wake.custom_wake_words()` and `BAKED_IN_WAKE_WORDS` (Task 2 — `custom_wake_words` is already unit-tested).
- Produces: `_save` stores `new_config["wake_words"]` = `custom_wake_words(field split on commas)`, including `[]` when the field is empty. `_wake_edit` displays `custom_wake_words(stored)` (no `"computer"`).

**No new unit test:** the strip/filter decision lives entirely in `custom_wake_words`, TDD'd in Task 2. This task is Qt wiring that calls it; guard = suite still green + manual verify.

- [ ] **Step 1: Rewrite the Wake Words UI section**

In `core/settings/tabs/commands_tab.py`, replace the blurb text (1059-1062) with the exact verbatim blurb from Global Constraints, then replace the `_wake_edit` row (1067-1085) so the field shows customs only behind a locked greyed `"computer"` prefix fused into one input-styled container:

```python
    wake_row = QWidget()
    wr = QHBoxLayout(wake_row)
    wr.setContentsMargins(4, 0, 4, 0)
    wr.setSpacing(8)
    wr.addStretch(1)

    # One container styled as an input; a greyed, non-interactive "computer"
    # segment is fused to the left of a borderless line edit that holds ONLY
    # the custom words. QLineEdit has no locked-prefix support, so the prefix
    # is a separate label -- never a keystroke-intercepted prefix.
    fused = QFrame()
    fused.setFixedWidth(_USER_ROW_CONTENT_W)
    fused.setStyleSheet(
        "QFrame { background-color: #282839; border: 1px solid #45475a; "
        "border-radius: 4px; }"
    )
    fl = QHBoxLayout(fused)
    fl.setContentsMargins(8, 0, 8, 0)
    fl.setSpacing(0)

    prefix = QLabel("computer")
    prefix.setStyleSheet("color: #6c7086; border: none; background: transparent;")

    dialog._wake_edit = QLineEdit()
    dialog._wake_edit.setStyleSheet("border: none; background: transparent; color: #cdd6f4;")
    dialog._wake_edit.setPlaceholderText("add custom words, comma-separated")

    # Show customs only (custom_wake_words filters the baked-in word out).
    stored = dialog._config.get("wake_words")
    if not (isinstance(stored, list) and stored):
        stored = [dialog._config.get("wake_word", "")]
    dialog._wake_edit.setText(", ".join(custom_wake_words(stored)))

    def _sync_prefix_comma(text):
        prefix.setText("computer," if text.strip() else "computer")
    _sync_prefix_comma(dialog._wake_edit.text())
    dialog._wake_edit.textChanged.connect(_sync_prefix_comma)
    dialog._wake_edit.textChanged.connect(dialog._check_restart_needed)

    fl.addWidget(prefix)
    fl.addWidget(dialog._wake_edit, stretch=1)
    wr.addWidget(fused)
    wr.addStretch(1)
    cl.addWidget(wake_row)
```

Add `QFrame` to the `PySide6.QtWidgets` import in `commands_tab.py`, and `from core.wake import custom_wake_words`.

- [ ] **Step 2: Update the dialog originals, save, and restart check**

In `core/settings/dialog.py::__init__` (131-135), make `_orig_wake_words` the customs-only string so `_check_restart_needed` compares against what the field shows:

```python
        ww = self._config.get("wake_words")
        if isinstance(ww, list) and ww:
            self._orig_wake_words = ", ".join(custom_wake_words(ww))
        else:
            self._orig_wake_words = ""
```

Add `from core.wake import custom_wake_words` to `dialog.py`.

In `_save` (572-574), store customs only (`custom_wake_words` strips `"computer"` and lowercases), and always set the key (empty list is valid):

```python
        new_config["wake_words"] = custom_wake_words(raw_wake.split(","))
```

The `_check_restart_needed` comparison on line 389 already compares `self._wake_edit.text().strip()` to `self._orig_wake_words`; both are now customs-only strings, so it is correct unchanged.

- [ ] **Step 3: Run the full suite**

Run: `pytest -q`
Expected: all green — proves the commands_tab/dialog rewrite imports and parses cleanly, and Task 2's `custom_wake_words` tests still pass.

- [ ] **Step 4: Manual verify (layout)**

With Tyler's go-ahead, deploy and open Settings → Commands. Confirm: the new blurb, a greyed `computer` locked at the left of the field, custom words editable after it, the comma appears only when customs are present, and clearing the field leaves `computer` alone. Saying just `"computer"` still wakes; saying `"hey dude"` still wakes.

- [ ] **Step 5: Commit**

```bash
git add core/settings/tabs/commands_tab.py core/settings/dialog.py
git commit -m "feat: lock 'computer' as a greyed prefix; wake field edits customs only"
```

---

## Post-implementation

- [ ] Update `HANDOFF.md` Current state: Model tab is whisper `.bin` picker; `"computer"` baked-in; `wake_words` default is `["hey dude"]`; note deploy status.
- [ ] Offer Tyler a squash/merge decision for `wake-verifier` per the separate KDE-2.0 milestone (not part of this plan).
