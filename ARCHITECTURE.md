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
- **Don't:** Rename it back.

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
  match.
- **Don't:** Make `to_dict()` return None for pinned rows.

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
     mishearing rewrites (Vosk heard X, user meant Y). Defaults from
     `DEFAULT_OVERRIDES` plus user-added rules from
     `commands.json["overrides"]`, in that order.
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

### Settings UI has three command-row tiers
- **What:** User actions (editable), system actions (locked
  name/dropdown, phrases editable), slot-pinned (fully read-only,
  always last). Full spec in `core/settings/dialog.py` module docstring.
- **Don't:** Add system actions to dropdown. Make system names
  editable. Add delete to system rows.

### Centering inside a settings row uses a structural anchor, not a computed offset
- **What:** When a row needs a centered element, split the row at the
  same x as an already-centered element in the parent layout via two
  equal-stretch sub-widgets — the boundary between them IS the center.
- **Why:** Calibrating a pixel/font-metric constant to land near the
  midpoint only works at one width and breaks the moment the parent
  resizes, the font changes, or DPI shifts.
- **Don't:** Compute centering offsets from `fontMetrics()`, widget
  widths, or hardcoded pixel values. If something else in the parent
  layout is centered (e.g. an `Qt.AlignmentFlag.AlignHCenter` button),
  anchor to that.

### `run_command` is a user-action, not a system action
- **What:** `_USER_ACTIONS` includes `run_command`; no default
  example ships. Users need custom shell commands with custom names.
- **Don't:** Move it back to system actions.

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

### Default mic phrases live in `commands.py`, not the settings UI or `listener.py`
- **What:** `DEFAULT_OPEN_MIC_PHRASES` / `DEFAULT_CLOSE_MIC_PHRASES`
  are module-level constants in `core/commands.py`.
  `_default_commands()` embeds them in `--emit-defaults` output.
  `core/settings/tabs/open_mic_tab.py` imports them as `_DEFAULT_*`
  aliases for the Restore Defaults buttons and for synthesizing missing
  keys on load. `listener.py` reads runtime values via
  `commands.get_open_mic_phrases()` / `get_close_mic_phrases()` —
  no hardcoded fallback in the listener.
- **Why:** Single source of truth. Pre-Tier-1, the settings UI had
  3-phrase defaults and `listener.py` had a 6-phrase fallback — they
  drifted, so a user's Restore Defaults produced different behavior
  than a fresh install.
- **Don't:** Add a fallback constant in `listener.py`. Move the
  canonical constants into the `core/settings/` package — that
  re-couples defaults to Qt and breaks
  `python -m core.commands --emit-defaults`, which `install.sh` runs
  without PySide6 available.

### Save button: dirty-tracked, not always-on
- **What:** Save starts disabled. A `dirtied` Qt Signal on
  `CommandRow`, `CommandsContainer`, and `MonitorRow` bubbles to
  `SettingsDialog._mark_dirty()`. `_mark_clean()` disables after
  successful save. "Save & Exit" label flip for restart-requiring
  changes only shows when value differs from dialog-open snapshot.
- **Why:** Spamming Save with no changes still rewrote the file
  (and a buggy save could destroy data).
- **Don't:** Strip dirty-signal plumbing. Make Save enabled-by-default.

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

### Chain abort rules return early; parse failures fall through
- **What:** `try_match` tracks `chain_ok` (segments parsed?) and
  `chain_rejected` (rule refused: confirm, cooldown, multi-URL
  cross-monitor). Rule rejections return False immediately; parse
  failures fall through to single-match as a rescue path.
- **Why:** Firing half a refused chain is worse than nothing. The
  rescue path is intentional for misparses only.
- **Don't:** Collapse the two flags. Let rule rejections fall through.

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
