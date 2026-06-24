# Voice Commander — Architectural Invariants

Durable properties of the code that must hold across time. Each entry
gives the **What** (the rule), optionally a **Why** (the reasoning),
and a **Don't** (the failure mode to avoid).

**Read this file selectively.** Use `grep -n "^### " ARCHITECTURE.md`
to list invariants, then read only the section(s) relevant to the
code area you're changing. Don't read the whole file at session
start — that defeats the purpose of splitting it from `HANDOFF.md`.

**Updating:** when an invariant changes mid-session, update this
file immediately (not at end-of-session). Adding new invariants is
the same: write them when the decision crystallizes, not later. The
**Invariant index** in `HANDOFF.md` is the table-of-contents
companion — keep them in sync.


### Slot name for `move_to_monitor` is `{alias}`, not `{monitor}`
- **What:** Pinned `move_to_monitor` uses `fuzzy: false` and slot
  `{alias}`. Name must match in phrase and `"slots"`. UI calls them
  aliases everywhere; canonical def in `core/aliases.py`.
- **Three-way mismatch (deliberate):** the slug (`move_to_monitor`), the
  action key (`move_window_to_monitor`), and the 0.6.0 display label
  ("Move window to monitor") intentionally differ. The slug is grandfathered
  into users' `commands.json`; renaming it would break saved configs. The
  action key is the function name in `core.actions.windows`. The label was
  renamed in 0.6.0 for user-facing consistency with the action key. Canonical
  explanation lives in a code comment above the `_PINNED_SLOT` entry in
  `core/aliases.py`; unifying them requires a `load_config` migration first.
- **Don't:** Rename the slug back, or "fix" the mismatch without a migration.

### Slot-pinned commands and default aliases live in `core/aliases.py`
- **What:** `PINNED_SLOT`, `PINNED_SLOT_NAMES`, `NUMBER_WORDS`, and
  `default_aliases()` all live here. `commands.py` and the
  `core/settings/` package import from it; neither defines its own copy.
- **Don't:** Add Qt imports to `core/aliases.py` — `commands.py`
  must stay Qt-free so `install.sh`'s `python -m core.commands
  --emit-defaults` works without PySide6.

### Pinned slot commands must be written to `commands.json`
- **What:** `_PINNED_SLOT` entries (`set_volume`, `move_to_monitor`)
  are always appended by `CommandsContainer.collect()` on save and
  by `_reset_commands()` on Restore Defaults. `_score_segment` loops
  over `_commands` from the JSON — if not in the file, they don't
  match. `CommandRow.to_dict()` returns None for these rows (their
  canonical definition lives in `_PINNED_SLOT`, not the row), so
  `collect()` is the single place that serializes them. As of 0.6.0
  their `enabled` flag is read off the row in `collect()` via direct
  attribute access (`row._enable_toggle.isChecked()`) and merged onto
  the injected `_PINNED_SLOT` dict — `to_dict()` still returns None
  (Option B; a partial `to_dict` was the alternative, rejected to keep
  `to_dict`'s None contract intact).
- **Don't:** Make `collect()` skip the `_PINNED_SLOT` injection. Route
  slot-pinned `enabled` through `to_dict()` — it returns None by design;
  `collect()` owns slot-pinned serialization.

### Trailing slots use greedy regex; mid-phrase slots use lazy
- **What:** `_phrase_to_regex()` uses `.+` for trailing slots,
  `.+?` for mid-phrase. Lazy `.+?` stopped at internal word
  boundaries on trailing slots.
- **Don't:** Revert to unconditional `.+?`.

### Vosk normalization split: hallucination filter vs user overrides
- **What:** Two separate, sequential rewrites of Vosk transcripts
  before matching, in this order:
  1. `_normalize()` in `core/listener.py` — hallucination filter for
     mic AGC artifacts the user never said. Currently a single rule
     (strip leading `"the "`). Plumbing, not user-facing.
  2. `apply_overrides()` in `core/overrides.py` — user-configurable
     mishearing rewrites (Vosk heard X, user meant Y). `get_overrides()`
     in `commands.py` assembles the final list: **USER rules first**, then
     the enabled `DEFAULT_OVERRIDES` (minus any the user disabled — see
     "Default overrides toggle via a disabled-pattern list"). Rules apply
     sequentially, so user-first means a user rule wins a same-span conflict
     with a default, matching the Overrides tab's top-to-bottom layout.
- **Why:** Categorically different mechanisms. AGC hallucinations
  are noise artifacts; mishearings are recognition errors. Mixing
  them in one place either exposes plumbing to the user (confusing)
  or hides legitimate rewrites the user should see (opaque).
- **Don't:** Move `"the "` strip into `DEFAULT_OVERRIDES` —
  exposing it as a deletable-looking rule mismodels its mechanism.
  Move user-facing rewrites back into `_normalize()` — they belong
  in the user-data layer, not the plumbing layer.

### Precision-vs-recall split in the matching pipeline
- **What:** Overrides handle precision (deterministic, user-owned
  rewrites for consistent observable mishearings). Fuzzy matcher
  handles recall (forgives minor near-misses automatically).
  Together they form the matching pipeline; alone neither is
  sufficient.
- **Why:** A loose fuzzy matcher fires wrong commands on shared
  prefixes (the 0.3.0 "open like" vs "open plex" problem). A
  strict matcher rejects plausible Vosk garbles unless the user has
  a rewrite for them. The split lets the matcher stay conservative
  (TAIL_THRESHOLD guard, etc.) while overrides cover the gray zone
  the user notices and wants to fix.
- **Goal:** Both work invisibly for the median user. Overrides are
  an emergency valve, not an expectation. Default overrides ship
  to cover the mishearings every user will hit.
- **Don't:** Loosen the matcher on the theory that overrides will
  catch the fallout — that inverts the split. Add matcher-side
  workarounds for individual mishearing patterns — those are
  override material.

### Non-slot matcher applies a tail-rescore guard
- **What:** `_match_non_slot()` in `core/matcher.py` computes the full
  `SequenceMatcher.ratio()`, then — if heard and phrase share their
  leading token — computes a second ratio on just the rest and
  requires it to clear `TAIL_THRESHOLD` (0.60). Otherwise returns
  0.0 (rejected). Single-word phrases and phrases without a shared
  leading token skip the guard.
- **Why:** Shared verb prefixes ("open ", "launch ") carried
  structurally weak matches above the 0.75 threshold (e.g. "open
  like" scored 0.778 against "open plex"). Tail rescore checks the
  distinguishing portion separately.
- **Don't:** Lower `TAIL_THRESHOLD` below ~0.55 — the gap to
  semantically-unrelated tails closes fast there.

### Short single-token phrases require a higher match floor
- **What:** In `_match_non_slot()` (`core/matcher.py`), a phrase that is a
  single token must clear `SHORT_PHRASE_THRESHOLD` (0.85) or it is
  hard-rejected (returns 0.0). Multi-token phrases are unaffected — they
  take the normal / tail-rescore path.
- **Why:** `SequenceMatcher` scores short strings deceptively high: one
  swapped letter in a 5-char word still yields ~0.80, which clears the 0.75
  default and fires the wrong command. Canonical case: "cause" → "pause"
  (0.80). Bumping the GLOBAL threshold was rejected — it would have to
  exceed 0.80, penalising legitimate garbles of longer commands. The floor
  targets only the phrases that actually have the problem.
- **Effective bar:** applied as a hard reject inside the matcher, so the
  effective threshold for a single-token phrase is `max(global threshold,
  0.85)` — never looser than the user's strictness slider.
- **Don't:** Don't gate on the HEARD length — gate on the PHRASE being a
  single token (the phrase is the known/fixed side). Don't fold it into the
  global threshold.

### Settings UI has two command-row tiers
- **What:** User actions (editable name/dropdown, deletable, alphabetized)
  and system commands (locked name, editable phrases for most, read-only
  phrases for slot-pinned, an enable/disable toggle on all). The Commands tab
  presents these as two sections — "User Commands" and "System Commands" —
  each with its own header, divided by a separator line. System commands
  display in a hardcoded order from `_SYSTEM_COMMAND_ORDER` in
  `core/settings/helpers.py`. Slot-pinned rows are a sub-flavor of system:
  same header layout, same collapse-by-default behavior; the only difference
  is their body shows a read-only phrase with a "cannot be edited" note. The
  bottom-most system row has no separator line beneath it. Pre-0.6.0 this was
  three separate tiers. Full spec in `core/settings/dialog.py` module
  docstring. As of 0.7.0 the Overrides tab mirrors this two-section
  User/System layout — see "Default overrides toggle via a disabled-pattern
  list".
- **Header layout (0.8.0):** the two tiers have different header shapes.
  User rows are `[name (expanding)] [action dropdown] [Options] [delete]` —
  controls flush right. Locked rows (system + slot-pinned) are
  `[name (fixed width)] [Options] [toggle]` *centered* in the row by
  equal-stretch spacers on both ends. Locked rows have **no action label**:
  it only ever restated the name ("Close window" / "Close window"). The fixed
  name width (`_LOCKED_NAME_WIDTH` in `commands_tab.py`) keeps every locked
  row identical so the Options/toggle column stays vertically flush down the
  section — see "Centering inside a settings row uses a structural anchor".
  Each locked row's separator (`_bottom_rule`) is variable-width
  (`_apply_rule_width`): short and centered under the button cluster when
  collapsed, full width (matching the phrases body) when Options is expanded.
  User-row separators stay full width. **The collapsed width MUST be pinned
  with `setFixedWidth`, not `setMaximumWidth`:** an HLine's sizeHint width is
  -1, so under a cap-plus-stretch scheme the flanking stretch (1) spacers
  absorb all the slack and the rule collapses to width 0 — invisible on real
  Qt (it survived only in some offscreen renders). That was the 0.8.0
  "invisible separator" bug; a fixed width can't be starved.
- **Don't:** Add system actions to the dropdown. Make system names editable.
  Add delete to system rows (they get a toggle instead). Re-separate
  slot-pinned as its own tier — the structural distinction it once had (no
  Options button, permanently expanded, always at the bottom) was removed in
  0.6.0; slot-pinned now sort interleaved per `_SYSTEM_COMMAND_ORDER`, not at
  the bottom. Re-add the action label to locked rows. Size the collapsed
  locked-row separator with `setMaximumWidth` (it collapses to 0 — use
  `setFixedWidth`).

### Disabled commands are matched but not dispatched
- **What:** System and slot-pinned commands carry an `enabled` field in
  `commands.json` (`true`/`false`; absent = `true` for back-compat with
  pre-0.6.0 configs). `_score_segment` still SCORES disabled commands but,
  when the best match is disabled, returns a `("disabled", cmd)` sentinel
  (a 2-tuple; a normal match's first element is a float score, so callers
  disambiguate with `result[0] == "disabled"`). The dispatcher (`try_match`,
  both single-match and chain paths) sees the sentinel and fires a
  `"<Display> is disabled in Settings"` notification via `_notify_disabled`
  without calling the action. In a chain, disabled segments notify but do
  NOT abort the chain — other valid segments still dispatch. User-action
  commands (launch_app, open_url, open_file, run_command) do not carry
  `enabled`; their removal mechanism is the delete button.
- **Why:** Disabling needs to be a first-class user action with feedback.
  Filtering disabled commands out at load time was the simpler alternative
  but produced confusing chain behavior — a disabled segment would silently
  fail the chain match, fall through to single-match, and misfire or do
  nothing with no explanation.
- **Don't:** Filter disabled commands at load time. Add `enabled` to
  user-action rows. Abort a chain on a disabled segment. Check the `enabled`
  flag before scoring (best-match-then-check-enabled is deliberate, so an
  enabled lower-scorer can't shadow a disabled higher-scorer).

### Default overrides toggle via a disabled-pattern list, filtered at assembly
- **What:** The shipped `DEFAULT_OVERRIDES` (`core/overrides.py`) can each be
  toggled off in the Overrides tab. Disabled defaults are stored by **pattern
  string** under `commands.json["disabled_default_overrides"]` (a list);
  `get_overrides()` (`commands.py`) filters them out of `DEFAULT_OVERRIDES`,
  then appends the survivors AFTER the user rules (user-first — see "Vosk
  normalization split"). Absent/empty key = all defaults on (back-compat).
  The default rules themselves are NEVER persisted — only the set of disabled
  patterns.
- **Why a pattern list, not per-row `enabled` flags on disk:** defaults are
  append-only and never removed (`core/overrides.py`), so the shipped list
  grows across versions. Storing only the disabled patterns means a newly
  shipped default is enabled automatically and there are no stale on-disk
  copies of the default rules to drift from the code.
- **Why filter at assembly, NOT the commands' matched-but-not-dispatched
  sentinel:** overrides are pre-match text rewrites with no dispatch/chain
  stage, so dropping a disabled rule at `get_overrides()` time is correct and
  has none of the chain-misfire problems that made load-time filtering wrong
  for commands (contrast "Disabled commands are matched but not dispatched").
- **UI:** the Overrides tab mirrors the Commands two-section layout — "User
  Overrides" (editable, deletable, 44px red X) above "System Overrides"
  (locked pattern, 44px enable/disable `ToggleSwitch`). Each row owns its
  separator (`_bottom_rule`); the last default row's is hidden.
  `OverridesContainer.collect_disabled_defaults()` returns the disabled
  patterns; `_save()` always writes the key (so re-enabling persists);
  `_reset_overrides()` clears it (re-enable all). The Overrides tab's
  "Restore Defaults" wipes user rules AND re-enables all defaults.
- **Where `ToggleSwitch` lives:** `core/settings/helpers.py` (shared by the
  Commands and Overrides tabs), not `commands_tab.py`.
- **Don't:** Persist the default rules themselves. Add an `enabled` field to
  user override rows (their removal mechanism is the delete X). Filter
  disabled defaults anywhere but `get_overrides()`.

### Centering inside a settings row uses a structural anchor, not a computed offset
- **What:** When a row needs a centered element, split the row at the
  same x as an already-centered element in the parent layout via two
  equal-stretch sub-widgets — the boundary between them IS the center.
  Two current users: the Overrides tab's row arrow, and (0.8.0) the locked
  command rows, which flank the fixed-width `[name][Options][toggle]` trio
  with a leading and trailing `addStretch(1)` so the trio centers.
- **Why:** Calibrating a pixel/font-metric constant to land near the
  midpoint only works at one width and breaks the moment the parent
  resizes, the font changes, or DPI shifts.
- **Don't:** Compute centering offsets from `fontMetrics()`, widget
  widths, or hardcoded pixel values. If something else in the parent
  layout is centered (e.g. an `Qt.AlignmentFlag.AlignHCenter` button),
  anchor to that.

### Settings window translucency is conditional on detected KWin blur
- **What (0.8.0):** The window is translucent (so a compositor blur shows
  through as frosted glass) ONLY when `core.env.blur_compositing_available()`
  returns True; otherwise it's an opaque solid-dark panel. The dialog computes
  this once at construction (`self._translucent`), sets
  `WA_TranslucentBackground` only when true, and applies
  `core/settings/style.build_stylesheet(translucent)`.
  `AppPickerDialog` gates its own `WA_TranslucentBackground` on the same check.
- **Single frost panel (the layering rule):** the translucent tint is carried by
  exactly ONE surface — `QWidget#frostPanel`, a full-window backing widget that
  wraps the tabs + button bar (`dialog._build_ui`). In the translucent build
  `#frostPanel` fills with `rgba(30, 30, 46, 0.8)` (Catppuccin base at 80% alpha)
  and EVERY structural surface above it (dialog, tab pane, scroll area, tab bar,
  tabs, button bar, `cmdBody`/`aliasBody`) is `transparent`, so the one frost
  layer shows through uniformly. The opaque build gives those surfaces back the
  original layered dark palette (`_BASE`/`_BODY`). `build_stylesheet` swaps a
  per-surface `_THEME[translucent]` token dict into the template.
- **Why a panel, not the QDialog background:** under `WA_TranslucentBackground`
  the top-level QDialog's own stylesheet background does NOT reliably paint, so a
  child panel is required. Two bugs the panel fixes vs. tinting every surface
  directly: (1) nested tinted surfaces (dialog + pane + scroll area) STACK and
  compound to near-opaque; (2) surfaces with no fill of their own (tab-bar corner
  gaps, the button bar) become alpha-0 holes that show raw blurred wallpaper. The
  alpha on `_FROST` (`style.py`) is the "how frosted" lever.
- **Detection:** parse `~/.config/kwinrc`'s `[Plugins]` group (the file VC
  already writes via kwriteconfig6 — no new dependency). True iff stock
  `blurEnabled` is on — *absent counts as on*, since Plasma ships Blur enabled
  — OR any other `[Plugins]` key containing "blur" and ending "enabled" is
  true (catches forks like `better_blur_dxEnabled`). Missing file / read error
  → False.
- **Why:** an unconditionally-translucent window on a desktop with no blur is
  genuinely see-through to the raw desktop — it looks broken. Most users run
  no blur, so opaque is the safe default; translucency is opt-in by positive
  detection. Erring toward opaque is deliberate (a default-on stock-blur user
  who has the key absent still reads as on; the failure we avoid is a no-blur
  user getting a see-through window).
- **Don't:** Set `WA_TranslucentBackground` unconditionally. Tint the QDialog
  background and expect it to paint under translucency (use `#frostPanel`). Tint
  multiple stacked surfaces in the translucent build (they compound to opaque) or
  leave any structural surface with no fill (alpha-0 holes show raw wallpaper) —
  in the translucent build, frost ONLY `#frostPanel` and keep everything above it
  `transparent`. Assume the stock `blurEnabled` key is the only blur effect
  (third-party forks exist).

### `run_command` is a user-action, not a system action
- **What:** `_USER_ACTIONS` includes `run_command`; no default
  example ships. Users need custom shell commands with custom names.
- **Don't:** Move it back to system actions.

### Entry point enforces single-instance via a per-UID QLocalServer lock
- **What:** `voice_commander.py` `main()` creates the `QApplication`, then
  calls `_acquire_single_instance()` **before** loading config or the Vosk
  model. The lock is a Qt `QLocalServer` (Unix domain socket) named
  `voice-commander-{os.getuid()}`. A would-be second instance probes with a
  `QLocalSocket`; if the probe connects, an instance is already running, so
  the new process prints "Already running" and `sys.exit(0)`. If nobody
  answers, `removeServer()` clears any stale socket left by a crash, then
  `listen()` claims the name. The returned server is held in a local
  (`singleton`) for the process lifetime — dropping that ref frees the lock.
- **Why a per-UID name:** the `systemd --user` service and any terminal
  launch share a UID, so the name collides between them *by design*. That
  collision is exactly what stops a terminal start from spinning up a second
  listener + duplicate tray icon alongside the running service.
- **Why fail-open:** if `listen()` fails for an unexpected reason, the guard
  logs a warning and starts anyway. It's a footgun-killer, not a security
  control — never block a legitimate launch.
- **Why before the model load:** the probe needs a live `QApplication` for
  the event dispatcher, but running it first means a redundant launch exits
  in milliseconds instead of after a multi-second Vosk model load.
- **Don't:** Move the guard after the model load. Use a fixed (non-UID)
  socket name. Drop the `singleton` reference. Make a failed `listen()`
  abort startup.

### Install architecture: code lives in `~/.local/share/voice-commander/app/`
- **What:** `install.sh` copies repo source to the XDG data dir; the
  systemd service runs from there. User can clone anywhere, install,
  optionally delete the clone.
- **Don't:** Point the service at the repo checkout.

### ReSpeaker LED control depends on a udev rule the installer does NOT install
- **What:** `hardware/respeaker.py` shells out to `xvf_host` to drive the
  XVF3800 LED ring. Writing to the device's USB control endpoint requires
  user-level write permissions, granted by
  `/etc/udev/rules.d/99-respeaker.rules` with `MODE="0666"` for VID `2886`
  PID `001a`. Without the rule, `xvf_host` runs without errors but the LED
  silently fails to change.
- **Why installer doesn't install it:** Writing to `/etc/udev/rules.d/`
  needs sudo. `install.sh` is intentionally a userland install — adding a
  sudo prompt would change its contract. Instead, `check_respeaker_udev`
  in `install.sh` detects the missing rule and prints a clear copy-paste
  one-liner for the user. It also prints a confirmation message if the
  rule is already present, so the user knows LED control will work.
- **Why both PATH-baking AND fallback paths in `respeaker.py`:** The
  systemd user service does not inherit PATH from the user's interactive
  shell, so `~/.local/bin` may be missing. The service unit
  (`voice-commander.service.in`) bakes
  `Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin` to fix this
  for the standard case. The fallback path list in `_find_xvf_host()`
  (`~/.local/bin/xvf_host`, `/usr/local/bin/xvf_host`, `/usr/bin/xvf_host`)
  is belt-and-suspenders: it lets LED control work if a user installs
  `xvf_host` to a non-standard location AND has it on their interactive
  shell PATH but somehow not on the service's PATH (e.g. session env was
  cached before they updated their shell config).
- **Why 5ms `time.sleep` between `xvf_host` calls:** Seeed's reference
  Python example does the same. Without it, the XVF3800 firmware can
  drop the second or third command in a rapid back-to-back sequence.
  Costs ~15ms of Qt main-thread blocking per state change — imperceptible.
- **Don't:** Make the installer require sudo to install the rule. Move the
  delay out of `set_state` to "avoid blocking the main thread" without a
  measurement showing it's actually a problem. Drop the fallback path list
  on the assumption the service-unit PATH is sufficient — it isn't if
  `xvf_host` is somewhere unusual.

### Default config generated by Python, not hardcoded in shell
- **What:** `install.sh` runs `python -m core.commands
  --emit-defaults`. Single source of truth — same
  `_default_commands()` used by installer and Restore Defaults.
- **Don't:** Hardcode defaults in `install.sh`.

### Mic toggles are `open_mic` / `close_mic` system commands, not a separate tab
- **What (0.8.0):** Open/close mic phrases live in the command list as the
  `open_mic` / `close_mic` **system commands** (`_default_commands()`),
  editable on the Commands tab like any other system command. There is no
  longer an "Open Mic" tab. `DEFAULT_OPEN_MIC_PHRASES` /
  `DEFAULT_CLOSE_MIC_PHRASES` remain module-level constants in
  `core/commands.py` — they seed those commands and are the last-resort
  fallback. "open mike"/"close mike" are **not** in the defaults: the shipped
  `mike -> mic` override (`core/overrides.py`) covers them.
- **Dispatch:** mic toggling is a *listener state transition*, not an action.
  `_MIC_ACTIONS = {"open_mic","close_mic"}` is excluded from `_score_segment`
  so the fuzzy matcher never scores or dispatches it; the listener intercepts
  the phrases by substring (`_is_open_mic_command` / `_is_close_mic_command`),
  which read `commands.get_open_mic_phrases()` / `get_close_mic_phrases()`.
- **Phrase resolution (`_mic_phrases`):** command-in-list (only if `enabled`;
  a disabled mic command returns an empty set, so the voice toggle stops while
  the tray left-click still works) → shipped defaults. The pre-0.8.0 migration
  of top-level `open_mic_phrases` / `close_mic_phrases` keys was dropped in 1.0;
  fresh installs get the system commands from `_default_commands()`, so there is
  no remaining legacy-key path in either load path.
- **Why:** Single source of truth, and the tab was redundant — phrase editing
  already exists for every system command.
- **Don't:** Add a fallback constant in `listener.py`. Move the canonical
  constants into the `core/settings/` package — that re-couples defaults to Qt
  and breaks `python -m core.commands --emit-defaults`, which `install.sh`
  runs without PySide6. Add `open_mic`/`close_mic` to `ACTION_REGISTRY` or let
  them into `_score_segment` (they are state transitions, not actions).

### Save button: dirty-tracked, not always-on
- **What:** Save starts disabled. A `dirtied` Qt Signal on
  `CommandRow`, `CommandsContainer`, and `MonitorRow` bubbles to
  `SettingsDialog._mark_dirty()`. `_mark_clean()` disables after
  successful save. "Save & Exit" label flip for restart-requiring
  changes only shows when value differs from dialog-open snapshot.
- **Why:** Spamming Save with no changes still rewrote the file
  (and a buggy save could destroy data).
- **Don't:** Strip dirty-signal plumbing. Make Save enabled-by-default.

### Save is blocked on exact duplicate command phrases
- **What:** `SettingsDialog._save()` calls `find_duplicate_phrases(commands)`
  (core/commands.py) right after `collect()` and BEFORE any write. If two or
  more commands share an exact phrase (case-insensitive, trimmed), it shows
  `_show_duplicate_block` (a QMessageBox naming the offenders) and returns
  early -- nothing is written, every pending edit stays in the dialog for the
  user to fix and retry. Detection is EXACT-match only, EXCLUDES slot phrases
  (`_has_slots`), de-dupes per command, and INCLUDES disabled commands (a
  disabled command still wins the match and shadows the enabled twin -- see
  "Disabled commands are matched but not dispatched"). Detection and the
  message builder (`_build_block_message`) are pure and Qt-free, so they live
  in commands.py and are unit-tested without Qt; the dialog only presents.
- **Why:** Two commands can't usefully share a phrase -- only the first in
  file order ever fires (strict `>` tie-break in `_score_segment`); the rest
  are silently shadowed. There is no legitimate persistent duplicate, so the
  state is forbidden rather than merely warned. The shipped defaults are
  guarded by a test (no exact dups) because a default collision would lock
  EVERY user out of saving.
- **Don't:** Describe the block as catching ALL collisions -- it is
  exact-match only; fuzzy near-collisions ("volume up" vs "volume app") sail
  through by design. Filter disabled commands out of detection. Import the
  PySide6-bound display helpers from `core/settings/helpers.py` into
  commands.py (breaks `--emit-defaults`).

### `kwriteconfig6` calls use absolute path to kwinrc
- **What:** Pass `os.path.expanduser("~/.config/kwinrc")` to
  `--file`, not bare `"kwinrc"`. Removes path resolution as a
  variable in daemon-vs-shell debugging.
- **Don't:** Revert to bare `"kwinrc"`.

### Side-effecting subprocess calls capture stdout/stderr
- **What:** Calls whose side effect matters use
  `capture_output=True, text=True`. See "Don't trust `check=True`"
  in Working rules for the why.

### Placer script is reloaded via `Scripting` DBus, NOT `reconfigure`
- **What:** `write_next_screen` triggers placer reload via
  `org.kde.kwin.Scripting.unloadScript` → `loadScript` → `start`.
- **Why:** `reconfigure` does NOT re-execute user scripts in
  Plasma 6. `loadScript` reads kwinrc fresh. `unloadScript` is
  mandatory — without it, `windowAdded` handlers stack on every
  reload (each `loadScript` creates a fresh JS engine).
- **Don't:** Add `reconfigure` back. Drop `unloadScript`.

### Placer queue entries are `output:wm_class` pairs
- **What:** Python writes `kwinrc nextScreen` as comma-separated
  `output:tag` entries (e.g. `DP-2:waterfox-g,HDMI-A-1:dolphin`).
  Placer matches arriving windows by bidirectional substring
  against `window.resourceClass`. Empty tag matches anything.
  No match = ignore.
- **Why:** `windowAdded` fires in window-mapping order, not launch
  order — class-tagging is order-independent.
- **Don't:** Revert to bare-output queue entries. Drop the
  empty-tag-matches-anything escape hatch.

### `move_to_X` and `open_on_X` use different KWin APIs
- **What:** `move_window_to_monitor` resolves alias → output name →
  KWin screen *index*, fires `Window to Screen N` global shortcut.
  `open … on X` (placer path) writes the output *name* to `kwinrc`;
  KWin script matches by name → `sendClientToScreen`.
- **Don't:** Assume one working proves the other works. Test both
  when changing anything monitor-related.

### Chains partial-execute; rule rejections abort wholesale
- **What:** `_match_chain_segments` scores each segment into one of
  three shapes: a real match `(cmd, args, target)`, a disabled sentinel
  `("disabled", cmd)`, or a no-match sentinel `("nomatch", seg)`.
  `try_match` then tracks `chain_ok` and `chain_rejected`:
  - **Partial execution:** matched segments fire; `disabled` and
    `nomatch` segments only toast (`nomatch` -> `"<seg>" No match`). A
    single misheard segment no longer kills the whole chain.
  - `chain_ok` = "at least one segment matched" (enabled or disabled).
  - **All-miss** (`chain_ok=False`, not rejected): NO segment matched —
    treat as a false split and fall through to single-match. This is
    what still rescues `open mind and body` -> `["open mind","body"]`
    (zero matches, and crucially NO per-segment toast).
  - **Rule rejection** (`chain_rejected=True`): a segment matched but a
    rule refused (confirm-required, cooldown, multi-URL cross-monitor).
    Return False immediately and fire NOTHING — even already-matched
    segments. Rejections abort wholesale even with partial execution on.
- **Why:** A mishear shouldn't silently drop a valid sibling command
  (the demo bug), but a guarded command (confirm/cooldown) or an
  unfixable URL race must still veto the entire chain. The all-miss
  fall-through keeps the aggressive `" and | an | in "` split from
  toasting bogus fragments.
- **Don't:** Collapse the two flags. Let rule rejections fall through to
  single-match. Fire matched segments when `chain_rejected` is set.
  Toast per-segment on the all-miss path (it's a misparse, not a partial
  chain — let single-match emit one "No match").

### URL chains across different monitors are forbidden
- **What:** Chains with 2+ `open_url` segments whose targets are
  distinct are rejected. Same-monitor or both-untargeted is fine.
- **Why:** Browser may reuse a window, open a new one, or open new
  tabs depending on warm/cold state — unfixable race from our side.
- **Don't:** Reject same-monitor URL chains. Loosen this rule
  without redesigning `apps.open_url` to force-new-window.

### Trailing-target distribution across chained segments
- **What:** When a chain's last segment carries `on {alias}` and
  every earlier segment is untargeted, the trailing target
  distributes backward across all prior segments (English-grammar
  default). E.g. `open reddit and open youtube on monitor one`
  fires both URLs targeting monitor one. Applies to all action
  types, not just `open_url` — `launch dolphin and open reddit on
  monitor two` works the same way.
- **Where:** `try_match()` in `core/commands.py`, after segment
  match assembly and **before** the cross-monitor URL check, so
  the check sees resolved targets.
- **Guard:** Propagation only fires when earlier segments are all
  untargeted. Mixed chains like `open reddit on monitor one and
  open youtube on monitor two` are left alone and still trip the
  cross-monitor URL rule as designed.
- **Don't:** Fix unrelated chain bugs by loosening the
  cross-monitor URL rule — that rule is correct and load-bearing.

### Save path: dedup, config refresh, symlink-aware
- **What:** `_save()` merges UI commands (`collect()`, which already
  includes `_PINNED_SLOT`) with `_HIDDEN_COMMANDS` from old config,
  in that order, deduped by name. After write, refreshes
  `self._config`. `_write_config` resolves realpath for symlinks.
- **Don't:** Reorder to `hidden + commands`. Skip the config
  refresh. Bypass realpath resolution.

### `CommandsContainer.collect()` does NOT filter on `isVisible()`
- **What:** Iteration is over `self._rows`; `remove_row()` already
  prunes deleted rows.
- **Why:** Qt reports widgets on inactive tabs as invisible.
- **Don't:** Add a visibility filter.

### LOG_BUFFER quirks
- **What:** `LOG_BUFFER` lives in `core/log_buffer.py` — a Qt-free
  leaf module imported by `listener.py`, `commands.py`, and
  `core/settings/tabs/log_tab.py`. `_poll_log()` compares
  `tuple(LOG_BUFFER)` snapshots (deque at maxlen=200; cursor math
  breaks). Clear Log must clear `LOG_BUFFER` itself, not just the
  widget.
- **Don't:** Move `LOG_BUFFER` back into `listener.py` for
  "locality" — `commands.py` and the settings UI both import it, so
  the cycle the leaf module breaks returns immediately.

### Monitor friendly names parsed from EDID via `core/edid.py`
- **What:** `get_monitor_friendly_names()` runs `/usr/bin/edid-decode`
  against `/sys/class/drm/card*-*/edid` and regexes the
  `Display Product Name: '...'` line into `{port: name}`. Called
  from `refresh_monitor_map()`, cached in `_monitor_details`,
  exposed via `get_monitor_details()`. Display tab shows friendly
  name primary (bold), port secondary (small/dim). Missing name
  descriptor → "Unknown display"; empty friendly_name → port-only.
- **Don't:** Pre-check `os.path.getsize()` on sysfs — always
  reports 0, skips every monitor. Add a PNP-ID lookup table —
  Display Product Name shown verbatim per Tyler's call. Add Qt
  imports to `core/edid.py`.

### Recognition strictness is a config-driven global match threshold
- **What (0.9.0):** The fuzzy-match threshold `DEFAULT_THRESHOLD = 0.75`
  (commands.py) is exposed on the Options tab as "Recognition strictness"
  (a full 0.00–1.00 slider). `get_match_threshold()` returns
  `_config.get("match_threshold", DEFAULT_THRESHOLD)`. `_score_segment` uses
  `cmd.get("threshold", get_match_threshold())` so a per-command `threshold`
  still overrides the global; `_detect_on_monitor` compares against
  `get_match_threshold()` too. Config hot-reloads, so a change takes effect
  without a restart. Default config carries `match_threshold: 0.75`.
- **Why:** Users wanted to tune sensitivity. Full range + a firm in-UI warning
  (Tyler's call: trust the user, no clamp) beats a hidden/bounded knob.
- **Don't:** Re-hardcode `DEFAULT_THRESHOLD` at the call sites (read the
  getter). Clamp the slider to a "safe" sub-range. Drop the per-command override.

### Notifications: a blanket toggle gates general notifications; confirm prompts always fire
- **What (0.9.0):** Config `notifications` (bool, default true) →
  `notifications_enabled()`. `notify()` itself stays pure; gating is at the CALL
  SITES. General feedback (no-match, mic toggles, "Listening…", command
  results, `_notify_disabled`, file-not-found) is suppressed when off — in
  listener.py via the `_notify_general()` wrapper, in commands.py via inline
  `if notifications_enabled():`, in actions/apps.py via a function-local import
  (avoids the commands→apps cycle). The shutdown/restart/logout CONFIRMATION
  prompts (and their cancel/timeout notices) call `_notify` DIRECTLY and ALWAYS
  fire — user protection: never confirm a destructive action blind.
- **Why:** One simple user toggle (Tyler: "off means off, they'll figure it
  out") without nuking the safety-critical confirm UI.
- **Don't:** Gate the confirm-flow calls. Read config inside notify.py (keep it
  pure; gate at call sites). Re-introduce a per-notification granular UI.

### Launch-on-login is opt-in external systemd state (not config)
- **What (0.9.0):** Autostart = whether `voice-commander.service` is
  `systemctl --user enable`d. It is OPT-IN: install.sh's `start_service` starts
  (or restarts) the unit but does NOT `enable` it. The Options-tab "Launch on
  login" toggle reads real state via `_autostart_is_enabled()` (is-enabled →
  True/False/None) and APPLIES on Save via `_apply_autostart()` (enable/disable,
  output captured). None (no systemctl/unit, e.g. running from source) → toggle
  disabled with a note. Autostart is NEVER written to config — external system
  state, handled separately in `_save`.
- **Why:** Power users hate forced autostart; default-off respects that. On-Save
  keeps one mental model (consistent with every other control).
- **Don't:** `systemctl enable` in install.sh. Store autostart in commands.json.
  Apply it instantly on toggle (it waits for Save).

### Settings "Options" tab (renamed from "About" in 0.9.0)
- **What (0.9.0):** `core/settings/tabs/options_tab.py` (was about_tab.py) holds
  the Launch-on-login, Enable-notifications, and Recognition-strictness controls,
  then the old About blurb + version footer as a subsection. Registered in
  dialog.py as `_build_options_tab` / `addTab(..., "Options")` and in
  `_tab_reset_map` → `_reset_options`, which (like every per-tab reset) is
  IMMEDIATE: confirm → write defaults → reload → close. Reset defaults:
  notifications on, strictness 0.75, AND launch-on-login OFF (an immediate
  `systemctl disable`, since reset is not Save-gated). install.sh ships an
  app-menu `.desktop` (Icon = `vc-listening.svg`, the same icon the settings
  window already uses).
- **Why:** A real home for app-wide options; About demoted to a subsection
  (Tyler rewrites the blurb later).
- **Don't:** Assume Restore Defaults is Save-gated (it writes + closes
  immediately). Forget that the Options reset also disables autostart.

### Wake is acknowledged on partials; LISTENING is an inactivity window; no-match is one-shot
- **What:** The SLEEPING→LISTENING feedback (tray icon, mic LED, "Listening…"
  notification) tracks *speech*, not Vosk's end-of-utterance endpoint (~1.5s of
  silence). Three coupled rules in `run_listener` (`core/listener.py`):
  - **Wake on partials:** in SLEEPING, the first Vosk *partial* containing the
    wake word transitions to LISTENING and fires the acknowledgement
    immediately (via `set_state`, which drives the LED/tray off `state_queue`).
    Once it leaves SLEEPING the branch can't re-fire on later partials of the
    same utterance, so no guard flag is needed. The SLEEPING-*finalize* branch
    stays as the fallback for utterances Vosk finalizes with no useful partial.
  - **Inactivity window:** every non-empty partial in LISTENING resets
    `command_window_start`, so `command_window` counts silence-since-you-stopped,
    NOT wall-clock-since-wake. A long multi-segment chain can't fall asleep
    mid-utterance.
  - **Wake-only is swallowed; total-miss is one-shot:** a finalized utterance
    that is nothing but the wake word (`detector.is_wake_only`) is swallowed
    silently in LISTENING (keep listening). A total miss (`try_match` returns
    False — nothing matched at all) toasts "No match" ONCE and returns to
    SLEEPING immediately. A partial-good chain returns True from `try_match`
    (see "Chains partial-execute") and never reaches this path, so its
    per-segment toasts are unaffected.
- **Why:** Pre-fix, feedback waited for finalize, so "Listening…" arrived ~1.5s
  late or simultaneously with execution. And a no-match used to leave you in
  LISTENING, so the command-window-expiry path fired a SECOND "No match" — two
  toasts for one failed attempt. Re-waking is cheap now (acknowledged off a
  partial), so one-shot is the obvious behavior.
- **Don't:** Gate wake detection on finalized results only. Make the command
  window wall-clock again. Toast on a wake-only utterance. Leave LISTENING after
  a total miss (reintroduces the double "No match"). Assume a partial-good chain
  reaches the listener's no-match branch — it doesn't.

### Wake-word matching is whole-word, not substring
- **What:** `WakeWordDetector.check()` (`core/wake.py`) matches each wake word on
  word boundaries via a precompiled regex per word, `(?<!\w)…(?!\w)`, NOT a raw
  `w in text` substring. "computer" wakes on "computer" but NOT "computerized".
  Lookarounds (not `\b`) so a wake word flanked by punctuation or string-edge
  still matches (`\b` needs a word char on one side — breaks "c++"). `re.escape`
  makes user-config wake words literal; multi-word phrases ("hey computer") join
  tokens on `\s+`. `is_wake_only()` removes wake words using the same patterns.
- **Why:** Substring matching tripped the wake on any longer word embedding the
  wake word — a real misfire source.
- **Don't:** Revert to `w in t`. Use `\b` (breaks punctuation-flanked words).
  Add PySide6/Vosk deps — `core/wake.py` is stdlib-only (`re`) with a pure-logic
  test suite (`tests/test_wake.py`); keep it that way.
