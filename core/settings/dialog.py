"""
core/settings/dialog.py
=======================
QDialog-based settings UI for Voice Commander.

Tab layout (left to right):
  - Commands    : Wake word field, command accordion rows
  - Overrides   : Vosk mishearing rewrite rules (defaults + user rules)
  - Displays    : monitor alias accordion rows
  - Model       : Vosk model path picker
  - Log         : live listener output with colour-coded categories
  - Options     : explanatory blurb

Save / Restore Defaults / Exit buttons pinned outside tabs at the bottom.
Restore Defaults is per-tab: dispatches to the active tab's reset handler
via `_tab_reset_map`, disabled with a tooltip on tabs without defaults
(Model, Log, Options).

Save behavior:
  - Writes through the symlink to the real file
  - Immediately calls commands.load_config() so changes are live without restart
  - Wake word and Vosk model changes still require a service restart
    (detector / model loaded at startup)
  - Dirty-tracked: starts disabled, enables on the first user-driven edit
    to any field, disables again after a successful save

Command name/slug rules:
  - User actions (launch_app, open_url, open_file, run_command): name is
    user-editable; slug auto-generated from name field on save. Deletable.
  - System actions (everything else): name and action dropdown both locked;
    no delete -- instead an enable/disable toggle. Options button kept so
    phrases stay editable.
  - Slot-pinned rows (set_volume, move_to_monitor): same locked header as
    system rows (name, Options, toggle); their phrases are read-only with a
    "cannot be edited" note in the body. A sub-flavor of system, not a
    separate tier.

Layout / sorting (applied on open, not on add):
  - Two sections: "User Commands" (top) and "System Commands" (below), each
    with its own header, divided by a separator line.
  - Newly added user rows -- prepended to the top of the User Commands
    section, stay there until the dialog reopens.
  - User commands -- A-Z by display name.
  - System + slot-pinned commands -- fixed order from _SYSTEM_COMMAND_ORDER in
    core/settings/helpers.py (NOT alphabetical); slot-pinned interleave per
    that order. The bottom-most system row has no separator line beneath it.

Action arg UI:
  - launch_app  -> searchable app picker popup (reads /usr/share/applications)
  - open_url    -> inline URL text field
  - open_file   -> file browser button + path label
  - run_command -> shell command text field
  - others      -> no arg UI (args managed directly in JSON)

Stash/restore:
  - CommandRow keeps a _stash dict keyed by action key.
  - When the user switches away from an action, current arg value is stashed.
  - When they switch back, the stashed value is restored.
  - Name is also stashed/restored when switching among user actions (the
    user-typed name persists across dropdown changes).
"""

import os
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QClipboard, QFont, QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QTabWidget, QVBoxLayout, QWidget,
)

from core.commands import (
    get_vosk_model_path, _default_commands,
    find_duplicate_phrases, _build_block_message, _build_invalid_chars_message,
)
from core.aliases import (
    PINNED_SLOT as _PINNED_SLOT,
    default_aliases as _default_aliases,
)
from core.actions.windows import get_connected_outputs
from core.env import GUI_ENV, blur_compositing_available
from core.run import run_capture
from core.settings.style import build_stylesheet
from core.settings.tabs.commands_tab import build as build_commands_tab
from core.settings.tabs.overrides_tab import build as build_overrides_tab
from core.settings.tabs.displays_tab import MonitorRow, build as build_displays_tab
from core.settings.tabs.model_tab import build as build_model_tab
from core.settings.tabs.log_tab import build as build_log_tab, _colorize_log_line
from core.settings.tabs.options_tab import build as build_options_tab
from core.settings.helpers import (
    _HIDDEN_COMMANDS,
    VOSK_MODELS_DIR,
    _load_config, _write_config,
)


# -- Main dialog --------------------------------------------------------------

class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Voice Commander")
        self.setWindowIcon(QIcon(os.path.join(
            os.path.expanduser("~/.local/share/voice-commander/icons"),
            "vc-sleeping.svg"  # the blue icon (vc-listening is green); brand icon
        )))
        self.setMinimumWidth(540)
        self.setMinimumHeight(400)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        from PySide6.QtCore import QTimer as _QTimer
        _QTimer.singleShot(0, lambda: self.resize(540, 720))

        # Only request a translucent window when the desktop will composite a
        # blur behind it -- otherwise the "frosted glass" becomes plain
        # see-through and looks broken. Spawned popups (AppPickerDialog) read
        # this flag too. See core.env.blur_compositing_available.
        self._translucent = blur_compositing_available()
        if self._translucent:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._config       = _load_config()
        self._monitors     = get_connected_outputs()
        self._monitor_rows: list[MonitorRow] = []

        # Snapshot restart-requiring values at dialog open for change detection.
        ww = self._config.get("wake_words")
        if isinstance(ww, list):
            self._orig_wake_words = ", ".join(ww)
        else:
            self._orig_wake_words = self._config.get("wake_word", "computer")
        try:
            self._orig_model_path = get_vosk_model_path()
        except ValueError:
            self._orig_model_path = self._config.get("vosk_model", "")

        self._orig_notifications = self._config.get("notifications", True)
        self._orig_match_threshold = self._config.get("match_threshold", 0.75)
        self._orig_autostart = self._autostart_is_enabled()

        self._build_ui()
        self._apply_stylesheet()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Single full-window backing panel. In the translucent build this is the
        # ONE surface that paints the frosted tint (rgba); every structural
        # surface above it (tabs, pane, scroll area, button bar, row bodies) is
        # transparent so the frost shows through uniformly -- no stacking (which
        # compounded to near-opaque) and no alpha-0 holes (the tab-bar gaps and
        # button bar that used to show raw wallpaper). The top-level QDialog's
        # own stylesheet background does NOT reliably paint under
        # WA_TranslucentBackground, which is exactly why a child panel is needed.
        # See core/settings/style.py.
        panel = QWidget()
        panel.setObjectName("frostPanel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(0)
        root.addWidget(panel)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_commands_tab(), "Commands")
        self._tabs.addTab(self._build_overrides_tab(), "Overrides")
        self._tabs.addTab(self._build_displays_tab(), "Displays")
        self._tabs.addTab(self._build_model_tab(), "Model")
        self._tabs.addTab(self._build_log_tab(), "Log")
        self._tabs.addTab(self._build_options_tab(), "Options")
        self._tabs.tabBar().setExpanding(True)
        self._tabs.tabBar().setMinimumWidth(540)
        panel_layout.addWidget(self._tabs)

        # Map tab title -> per-tab reset handler. Tabs absent from this map
        # (Model, Log) have no per-tab defaults and disable the
        # bottom-bar Restore Defaults button when selected. Keyed by tab
        # TITLE rather than index so reordering tabs doesn't silently rewire.
        self._tab_reset_map: dict = {
            "Commands":  (self._reset_commands,
                          "Restore the default set of commands (does not affect wake words, displays, overrides, or other settings)."),
            "Overrides": (self._reset_overrides,
                          "Remove all user-added override rules and re-enable every built-in default."),
            "Displays":  (self._reset_displays,
                          "Reset every monitor's aliases to the shipped defaults (Display 1, Display 2, ...)."),
            "Options":   (self._reset_options,
                          "Restore Options to defaults: notifications on, recognition strictness 0.75, and launch on login off."),
        }

        btn_bar = QWidget()
        btn_bar.setObjectName("btnBar")
        bl = QHBoxLayout(btn_bar)
        bl.setContentsMargins(16, 8, 16, 12)
        self._save_btn = QPushButton("Save")
        self._save_btn.setDefault(True)
        self._save_btn.clicked.connect(self._save)
        # Save starts disabled; flipped on by _mark_dirty when any field changes.
        self._save_btn.setEnabled(False)
        self._reset_btn = QPushButton("Restore Defaults")
        self._reset_btn.setObjectName("resetBtn")
        self._reset_btn.clicked.connect(self._reset_current_tab)
        self._close_btn = QPushButton("Exit")
        self._close_btn.clicked.connect(self.reject)
        bl.addStretch()
        bl.addWidget(self._save_btn)
        bl.addWidget(self._reset_btn)
        bl.addWidget(self._close_btn)
        bl.addStretch()
        panel_layout.addWidget(btn_bar)

        # Initial state of the Restore Defaults button matches the current tab,
        # and updates whenever the user switches tabs.
        self._tabs.currentChanged.connect(self._update_reset_button_for_tab)
        self._update_reset_button_for_tab(self._tabs.currentIndex())

        # -- Dirty-tracking wiring ------------------------------------------
        # All editable widgets across all tabs are connected here, AFTER they've
        # been built and seeded with initial values. This means the initial
        # setText / setPlainText calls in the build methods don't fire dirty.
        self._wake_edit.textChanged.connect(self._mark_dirty)
        self._commands_container.dirtied.connect(self._mark_dirty)
        self._overrides_container.dirtied.connect(self._mark_dirty)
        for mrow in self._monitor_rows:
            mrow.dirtied.connect(self._mark_dirty)
        self._notifications_toggle.toggled.connect(self._mark_dirty)
        self._strictness_slider.valueChanged.connect(self._mark_dirty)
        if self._orig_autostart is not None:
            self._autostart_toggle.toggled.connect(self._mark_dirty)
        # Model picker dirties via _browse_vosk_model -> _check_restart_needed,
        # which we extend below to also call _mark_dirty.

    def _make_scroll_tab(self) -> tuple[QWidget, QVBoxLayout]:
        tab = QWidget()
        tab.setObjectName("tabPage")
        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        tab_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(16, 16, 16, 8)
        cl.setSpacing(8)

        scroll.setWidget(content)
        tab_layout.addWidget(scroll)
        return tab, cl

    def _build_model_tab(self) -> QWidget:
        return build_model_tab(self)

    def _browse_vosk_model(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Select Vosk model directory",
            self._selected_model_path or VOSK_MODELS_DIR,
            QFileDialog.Option.ShowDirsOnly,
        )
        if not chosen:
            return

        if not os.path.isfile(os.path.join(chosen, "am", "final.mdl")):
            self._model_error_label.setText(
                f"That doesn't look like a Vosk model directory (missing am/final.mdl).\n"
                f"Please select the root folder of a downloaded Vosk model."
            )
            self._model_error_label.setVisible(True)
            return

        self._model_error_label.setVisible(False)
        self._selected_model_path = chosen
        self._model_path_label.setText(chosen)
        self._check_restart_needed()
        self._mark_dirty()
        print(f"[settings] Vosk model selected: {chosen}")

    def _build_options_tab(self) -> QWidget:
        return build_options_tab(self)

    def _build_commands_tab(self) -> QWidget:
        return build_commands_tab(self)

    def _build_overrides_tab(self) -> QWidget:
        return build_overrides_tab(self)

    def _build_displays_tab(self) -> QWidget:
        return build_displays_tab(self)

    def _build_log_tab(self) -> QWidget:
        return build_log_tab(self)

    def _poll_log(self) -> None:
        # Don't repaint while the user has text selected — setHtml() would
        # wipe the selection mid-copy. New lines accumulate in LOG_BUFFER
        # and surface on the next tick after deselection.
        if self._log_selection_active:
            return
        from core.log_buffer import LOG_BUFFER
        current = tuple(LOG_BUFFER)
        if current == self._last_log_snapshot:
            return
        self._last_log_snapshot = current
        html_lines = [_colorize_log_line(line) for line in current]
        self._log_view.setHtml("<br>".join(html_lines))
        self._log_view.verticalScrollBar().setValue(
            self._log_view.verticalScrollBar().maximum()
        )

    def _on_log_selection_changed(self) -> None:
        cursor = self._log_view.textCursor()
        if cursor.hasSelection():
            self._log_selection_active = True
            # QTextEdit returns paragraph separators (U+2029) instead of \n
            # in selectedText(); normalize so middle-click paste is sane.
            text = cursor.selectedText().replace("\u2029", "\n")
            QGuiApplication.clipboard().setText(text, QClipboard.Mode.Selection)
        else:
            self._log_selection_active = False

    def _clear_log(self) -> None:
        """Clear the log view and the underlying buffer."""
        from core.log_buffer import LOG_BUFFER
        LOG_BUFFER.clear()
        self._last_log_snapshot = ()
        self._log_view.clear()

    def show_log_tab(self) -> None:
        """Switch to the Log tab. Called by tray menu -> Log."""
        for i in range(self._tabs.count()):
            if self._tabs.tabText(i) == "Log":
                self._tabs.setCurrentIndex(i)
                break

    def _apply_stylesheet(self) -> None:
        self.setStyleSheet(build_stylesheet(self._translucent))

    def _mark_dirty(self, *_args) -> None:
        """Enable the Save button. Called by every dirty source -- wake edit,
        commands container, monitor rows, mic phrases, model picker.
        Idempotent: safe to call repeatedly."""
        if not self._save_btn.isEnabled():
            self._save_btn.setEnabled(True)

    def _mark_clean(self) -> None:
        """Disable the Save button. Called after a successful save."""
        self._save_btn.setEnabled(False)

    def _check_restart_needed(self, *_args) -> None:
        """Update save button label based on whether restart-requiring settings changed."""
        wake_changed = self._wake_edit.text().strip() != self._orig_wake_words
        model_changed = self._selected_model_path != self._orig_model_path
        if wake_changed or model_changed:
            self._save_btn.setText("Save && Exit")
        else:
            self._save_btn.setText("Save")

    def _show_duplicate_block(self, dupes: list[dict]) -> None:
        """Modal block shown when Save is refused due to duplicate phrases.

        Pure presentation -- the detection and wording live in core.commands
        (Qt-free, unit-tested). PlainText so guillemets / quotes / '<' in a
        user's phrase aren't parsed as HTML. Single dismiss button; the Save
        button stays enabled so the user can fix the dup and retry."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Can't save — duplicate phrase")
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(_build_block_message(dupes))
        ok = box.addButton("Got it", QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(ok)
        box.setEscapeButton(ok)
        box.exec()

    def _show_invalid_chars_block(self, offenders: list[dict]) -> None:
        """Modal shown when Save stripped non-speakable characters from phrases.

        Parallels _show_duplicate_block: the wording is built Qt-free in
        core.commands and only presented here. The phrase boxes have already
        been cleaned in place (CommandsContainer.sanitize_phrases), so this
        block returns the user to the dialog to eyeball the result and Save
        again -- the second Save finds them clean and proceeds. PlainText so
        stripped characters shown in the message aren't parsed as HTML."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Uh oh! That isn't gonna work.")
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(_build_invalid_chars_message(offenders))
        ok = box.addButton("Got it", QMessageBox.ButtonRole.AcceptRole)
        box.setDefaultButton(ok)
        box.setEscapeButton(ok)
        box.exec()

    def _autostart_is_enabled(self) -> bool | None:
        """Return True/False from `systemctl --user is-enabled` for the service,
        or None if systemctl or the unit isn't available (e.g. running from
        source). Read-only; called once at dialog open."""
        try:
            r = run_capture(
                ["systemctl", "--user", "is-enabled", "voice-commander.service"],
                timeout=5,
            )
        except Exception as e:
            print(f"[settings] autostart is-enabled check failed: {e}")
            return None
        out = (r.stdout or "").strip()
        if out == "enabled":
            return True
        if out == "disabled":
            return False
        return None  # not-found / static / unexpected -> treat as unavailable

    def _apply_autostart(self, enable: bool) -> bool:
        """`systemctl --user enable|disable` the service. Returns True on
        success. Captures + logs output (does not raise)."""
        verb = "enable" if enable else "disable"
        try:
            r = run_capture(
                ["systemctl", "--user", verb, "voice-commander.service"],
                timeout=10,
            )
        except Exception as e:
            print(f"[settings] autostart {verb} failed to run: {e}")
            return False
        if r.returncode != 0:
            print(f"[settings] autostart {verb} failed (rc={r.returncode}): "
                  f"{(r.stderr or '').strip()}")
            return False
        print(f"[settings] autostart: {verb}d voice-commander.service")
        return True

    def _save(self) -> None:
        old_wake_words  = self._config.get("wake_words", [self._config.get("wake_word", "computer")])

        # Strip characters that can't occur in a spoken phrase (braces,
        # punctuation, symbols) BEFORE anything else. This cleans the phrase
        # boxes in place; if it changed anything, block this save and show the
        # user what was removed. Mirrors the duplicate block below: returns
        # before any write, edits stay in the dialog, and the next Save finds
        # the boxes clean and proceeds. Runs first so the duplicate check and
        # the write both operate on already-sanitized phrases.
        invalid = self._commands_container.sanitize_phrases()
        if invalid:
            self._show_invalid_chars_block(invalid)
            return

        commands = self._commands_container.collect()

        # Hard guard: two commands cannot share an exact phrase. Only the first
        # in file order would ever fire; the rest are silently shadowed (and a
        # disabled command still wins the match -- see _score_segment). Block
        # the save and name the offenders. Returns BEFORE any write, so every
        # pending edit stays in the dialog for the user to fix and retry.
        dupes = find_duplicate_phrases(commands)
        if dupes:
            self._show_duplicate_block(dupes)
            return

        overrides = self._overrides_container.collect()
        disabled_default_overrides = self._overrides_container.collect_disabled_defaults()

        monitors: dict = dict(self._config.get("monitors", {}))
        for row in self._monitor_rows:
            name, aliases = row.collect()
            monitors[name] = aliases

        new_config = dict(self._config)
        raw_wake = self._wake_edit.text()
        wake_words = [w.strip().lower() for w in raw_wake.split(",") if w.strip()]
        if wake_words:
            new_config["wake_words"] = wake_words
            new_config.pop("wake_word", None)

        # Merge UI-collected commands with hidden commands from old config.
        # `commands` already includes _PINNED_SLOT (set_volume, move_to_monitor)
        # via CommandsContainer.collect(). `hidden` re-injects everything in
        # _HIDDEN_COMMANDS that exists on disk. As of 0.7.0 _HIDDEN_COMMANDS is
        # just the two slot-pinned names, which `commands` already supplies via
        # _PINNED_SLOT -- so this merge is now defensive (dedup drops the
        # duplicates). open_settings is collected as a normal system row.
        # Dedup by name with `commands` winning ties so the canonical pinned
        # versions don't get shadowed by stale duplicates from disk.
        hidden = [c for c in self._config.get("commands", [])
                  if c.get("name") in _HIDDEN_COMMANDS]
        seen: set[str] = set()
        deduped: list[dict] = []
        for c in list(commands) + hidden:
            name = c.get("name")
            if not name or name in seen:
                continue
            seen.add(name)
            deduped.append(c)
        new_config["commands"] = deduped
        # Always write overrides (even when empty) so deleting all user rules persists.
        new_config["overrides"] = overrides
        # Always write the disabled-default set too (even when empty) so
        # re-enabling a previously-disabled default persists. get_overrides()
        # filters DEFAULT_OVERRIDES by this list at runtime.
        new_config["disabled_default_overrides"] = disabled_default_overrides

        if monitors:
            new_config["monitors"] = monitors
        if self._selected_model_path:
            new_config["vosk_model"] = self._selected_model_path

        # Options tab: notifications + recognition strictness are config-backed.
        new_config["notifications"] = self._notifications_toggle.isChecked()
        new_config["match_threshold"] = round(self._strictness_slider.value() / 100.0, 2)

        try:
            _write_config(new_config)
        except Exception as e:
            print(f"[settings] Save failed: {e}")
            return

        # Refresh our in-memory snapshot so a second save in the same session
        # works correctly (hidden-command re-inject reads from self._config).
        self._config = new_config
        self._mark_clean()

        # Launch-on-login is external systemd state, not config -- apply it here,
        # only when its state is known and actually changed. The helper captures
        # and logs systemctl output.
        if self._orig_autostart is not None:
            desired = self._autostart_toggle.isChecked()
            if desired != self._orig_autostart and self._apply_autostart(desired):
                self._orig_autostart = desired

        new_wake_words = new_config.get("wake_words", [])
        needs_restart  = (sorted(old_wake_words) != sorted(new_wake_words) or
                          self._selected_model_path != self._orig_model_path)

        try:
            import core.commands as _cmds
            _cmds.load_config()
            print("[settings] Config reloaded into commands module.")
        except Exception as e:
            print(f"[settings] Post-save reload failed: {e}")

        if needs_restart:
            print("[settings] Restart-requiring setting changed -- restarting service.")
            try:
                result = run_capture(
                    ["systemctl", "--user", "restart", "voice-commander"],
                    timeout=10,
                )
                if result.returncode != 0:
                    raise RuntimeError(
                        f"systemctl exited {result.returncode}: {result.stderr.strip()}"
                    )
                print("[settings] Service restarted.")
            except Exception as e:
                print(f"[settings] Service restart failed: {e}")
            self.accept()

    def _confirm(self, title: str, body: str) -> bool:
        """Yes/No confirmation dialog (No is the default). True iff Yes."""
        reply = QMessageBox.question(
            self, title, body,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _reset_via(self, mutate, *, log_label: str, fail_verb: str, post=None) -> bool:
        """Load config, apply ``mutate(data)``, persist, and reload the runtime
        config -- the shared body of every per-tab Restore-Defaults handler.

        ``post`` (optional) runs inside the same try, after reload, for extra
        side effects (e.g. Options' launch-on-login). Returns True on success;
        on failure shows a warning and returns False so the caller bails before
        its success dialog.
        """
        try:
            data = _load_config()
            mutate(data)
            _write_config(data)
            import core.commands as _cmds
            _cmds.load_config()
            if post is not None:
                post()
            print(f"[settings] {log_label}")
            return True
        except Exception as e:
            print(f"[settings] Could not {fail_verb}: {e}")
            QMessageBox.warning(self, "Reset failed", f"Could not {fail_verb}:\n{e}")
            return False

    def _reset_commands(self) -> None:
        """
        Confirm with the user, then overwrite the commands list with defaults.
        Does not touch wake words, monitors, open mic phrases, overrides, or
        model settings. Writes immediately and reloads -- no save click required.

        Called from the per-tab 'Restore Defaults' button in the Commands tab
        header (not the bottom button bar -- that global button was removed in
        favour of scoped per-tab resets).
        """
        if not self._confirm(
            "Restore Defaults?",
            "This will replace all your custom commands with the defaults.\n\n"
            "Wake words, displays, open mic phrases, overrides, and model settings "
            "will NOT be affected.\n\n"
            "This cannot be undone. Continue?",
        ):
            return

        def mutate(data):
            data["commands"] = _default_commands() + [dict(p) for p in _PINNED_SLOT]

        if not self._reset_via(mutate, log_label="Commands reset to defaults.",
                               fail_verb="reset commands"):
            return

        QMessageBox.information(
            self,
            "Defaults restored",
            "Commands have been restored to defaults. The settings window will close; "
            "reopen it to see the new commands.",
        )
        self.accept()

    def _reset_options(self) -> None:
        """
        Confirm, then restore the Options tab to shipped defaults: notifications
        ON, recognition strictness 0.75, and launch-on-login OFF. Writes config
        and runs `systemctl --user disable` immediately (this reset is NOT
        Save-gated -- it writes and closes, like the other per-tab resets).
        """
        if not self._confirm(
            "Restore Default Options?",
            "This will turn notifications ON, reset recognition strictness to "
            "0.75, and turn OFF launch on login.\n\n"
            "This cannot be undone. Continue?",
        ):
            return

        def mutate(data):
            data["notifications"] = True
            data["match_threshold"] = 0.75

        # Launch-on-login default is OFF. It's external systemd state, so apply
        # it here rather than writing it to config.
        def post():
            if self._orig_autostart is not None:
                self._apply_autostart(False)

        if not self._reset_via(mutate, log_label="Options reset to defaults.",
                               fail_verb="reset options", post=post):
            return

        QMessageBox.information(
            self,
            "Defaults restored",
            "Options restored to defaults (notifications on, strictness 0.75, "
            "launch on login off). The settings window will close; reopen it to "
            "see the changes.",
        )
        self.accept()

    def _reset_overrides(self) -> None:
        """
        Confirm, then restore the Overrides tab to its shipped state: wipe all
        user-added overrides AND re-enable every built-in default (clear the
        disabled-default set). The defaults themselves are code-shipped
        (DEFAULT_OVERRIDES), so "restoring defaults" means an empty user list
        plus all defaults switched back on.
        """
        if not self._confirm(
            "Restore Default Overrides?",
            "This will remove every override you have added AND switch every "
            "built-in default back on.\n\n"
            "This cannot be undone. Continue?",
        ):
            return

        def mutate(data):
            data["overrides"] = []
            # Re-enable all defaults (empty disabled set).
            data["disabled_default_overrides"] = []

        if not self._reset_via(mutate, log_label="User overrides cleared.",
                               fail_verb="clear overrides"):
            return

        QMessageBox.information(
            self,
            "Defaults restored",
            "User overrides have been cleared and all built-in defaults "
            "re-enabled. The settings window will close; reopen it to see the "
            "updated list.",
        )
        self.accept()

    def _reset_displays(self) -> None:
        """
        Confirm, then reset every monitor's alias list to the shipped defaults
        from core.aliases._default_aliases (Display 1, Display 2, ...).

        Operates on the same monitor set the dialog was built with, so a user
        who has plugged in a new monitor after opening settings won't see it
        until the dialog is reopened -- consistent with the rest of the
        Displays tab.
        """
        if not self._confirm(
            "Restore Default Aliases?",
            "This will reset every monitor's alias list to the shipped defaults "
            "(Display 1, Display 2, ...).\n\n"
            "Wake words, commands, overrides, and other settings will NOT be affected.\n\n"
            "This cannot be undone. Continue?",
        ):
            return

        def mutate(data):
            # Rebuild the monitors block from the dialog's monitor list using
            # default aliases keyed by display-index order.
            new_monitors: dict = {}
            for i, m in enumerate(self._monitors):
                new_monitors[m["name"]] = _default_aliases(i + 1)
            data["monitors"] = new_monitors

        if not self._reset_via(mutate, log_label="Display aliases reset to defaults.",
                               fail_verb="reset displays"):
            return

        QMessageBox.information(
            self,
            "Defaults restored",
            "Display aliases have been restored to defaults. The settings window "
            "will close; reopen it to see the new aliases.",
        )
        self.accept()

    def _update_reset_button_for_tab(self, idx: int) -> None:
        """
        Keep the bottom-bar 'Restore Defaults' button in sync with the active
        tab. On tabs that have no defaults to restore (Model, Log, About),
        the button is disabled with a tooltip explaining why. On reset-capable
        tabs, the tooltip is the per-tab message from `_tab_reset_map`.
        """
        title = self._tabs.tabText(idx)
        entry = self._tab_reset_map.get(title)
        if entry is None:
            self._reset_btn.setEnabled(False)
            self._reset_btn.setToolTip(
                f"No defaults to restore on the {title} tab."
            )
        else:
            _handler, tooltip = entry
            self._reset_btn.setEnabled(True)
            self._reset_btn.setToolTip(tooltip)

    def _reset_current_tab(self) -> None:
        """
        Dispatch the bottom-bar 'Restore Defaults' click to the per-tab handler
        registered in `_tab_reset_map`. Safe to call even if the active tab has
        no handler -- it's a no-op (and the button should already be disabled
        in that case via `_update_reset_button_for_tab`).
        """
        title = self._tabs.tabText(self._tabs.currentIndex())
        entry = self._tab_reset_map.get(title)
        if entry is None:
            return
        handler, _tooltip = entry
        handler()
