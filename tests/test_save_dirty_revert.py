"""
tests/test_save_dirty_revert.py
===============================
The Save button should track *actual* difference from the last-saved state,
not just "something was touched once." Editing a field enables Save; undoing
the edit (returning every field to its saved value) disables it again.

Builds the real SettingsDialog offscreen with its filesystem/systemd probes
monkeypatched: config comes from an in-memory dict, the autostart systemctl
check is skipped, and translucency detection is pinned off. Everything else
is the genuine dialog wiring.
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from core.commands import _default_commands  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _fixture_config() -> dict:
    """A complete, realistic config dict, built from the real default command
    set so the Commands tab renders every row flavor (user, system,
    slot-pinned)."""
    return {
        "wake_words": ["computer"],
        "commands": _default_commands(),
        "overrides": [],
        "disabled_default_overrides": [],
        "monitors": {},
        "notifications": True,
        "match_threshold": 0.75,
    }


@pytest.fixture()
def dialog(qapp, monkeypatch):
    import core.settings.dialog as D

    # Config from memory, not disk.
    monkeypatch.setattr(D, "_load_config", _fixture_config)
    # No systemctl probe: behaves like running from source (toggle hidden).
    monkeypatch.setattr(D.SettingsDialog, "_autostart_is_enabled",
                        lambda self: None)
    # Deterministic opaque build; skips kwinrc parsing.
    monkeypatch.setattr(D, "blur_compositing_available", lambda: False)

    dlg = D.SettingsDialog()
    yield dlg
    dlg.deleteLater()


def test_save_disables_again_when_wake_edit_reverted(dialog):
    assert not dialog._save_btn.isEnabled(), "Save must start disabled"

    original = dialog._wake_edit.text()
    dialog._wake_edit.setText(original + " x")
    assert dialog._save_btn.isEnabled(), "an edit must enable Save"

    dialog._wake_edit.setText(original)
    assert not dialog._save_btn.isEnabled(), \
        "reverting the only edit must disable Save again"


def test_save_disables_again_when_toggle_reverted(dialog):
    dialog._notifications_toggle.setChecked(False)
    assert dialog._save_btn.isEnabled()

    dialog._notifications_toggle.setChecked(True)
    assert not dialog._save_btn.isEnabled(), \
        "flipping a toggle back must disable Save again"
