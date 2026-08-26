"""
tests/test_overrides_roundtrip.py
=================================
Phase 4 (0.7.0): verify the default-override enable/disable toggle round-trips
through the Overrides tab. Disabled defaults are stored by PATTERN under
commands.json["disabled_default_overrides"]; the default rules themselves are
never persisted. Flip toggles, collect_disabled_defaults(), serialize, reload
into a fresh container, confirm states survived; and confirm get_overrides()
filters disabled patterns out at assembly while leaving DEFAULT_OVERRIDES
untouched.

Like test_settings_roundtrip.py this touches Qt, so it forces the offscreen
platform and skips entirely if PySide6 isn't installed. Run from the repo root
with `pytest -q`.
"""

import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

import core.commands as commands  # noqa: E402
from core.overrides import DEFAULT_OVERRIDES  # noqa: E402
from core.settings.tabs.overrides_tab import OverridesContainer  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def synthetic_defaults():
    """The whisper build ships an EMPTY DEFAULT_OVERRIDES, but the
    enable/disable-a-shipped-default MECHANISM still needs coverage. Inject a
    synthetic default set IN PLACE and restore afterwards.

    In place matters: core.commands and the Overrides tab both did
    `from core.overrides import DEFAULT_OVERRIDES`, so they hold references to
    the SAME list object. Mutating that object's contents (`[:] = ...`) is
    visible everywhere; rebinding the name would not be. This tests the
    mechanism without depending on which rules actually ship.
    """
    import core.overrides as ov

    fixture = [
        {"pattern": "mike",    "replacement": "mic"},
        {"pattern": "cause",   "replacement": "close"},
        {"pattern": "hope in", "replacement": "open"},
    ]
    saved = list(ov.DEFAULT_OVERRIDES)
    ov.DEFAULT_OVERRIDES[:] = fixture
    try:
        yield fixture
    finally:
        ov.DEFAULT_OVERRIDES[:] = saved


def _toggle_of(container, pattern):
    for row in container._default_rows:
        if row._pattern_value == pattern:
            return row._enable_toggle
    raise AssertionError(f"no default row with pattern {pattern!r}")


def test_disabled_defaults_roundtrip(qapp, synthetic_defaults):
    user = [{"pattern": "reboot now", "replacement": "restart"}]
    c1 = OverridesContainer(user, disabled_default_patterns=[])

    # Everything on at first.
    assert c1.collect_disabled_defaults() == []

    # Disable two defaults.
    _toggle_of(c1, "mike").setChecked(False)
    _toggle_of(c1, "cause").setChecked(False)

    disabled = json.loads(json.dumps(c1.collect_disabled_defaults()))
    assert set(disabled) == {"mike", "cause"}
    # User rules collected separately, unaffected by the default toggles.
    assert c1.collect() == [{"pattern": "reboot now", "replacement": "restart"}]

    # Reload a fresh container from the persisted disabled set.
    c2 = OverridesContainer(user, disabled_default_patterns=disabled)
    assert _toggle_of(c2, "mike").isChecked() is False
    assert _toggle_of(c2, "cause").isChecked() is False
    assert _toggle_of(c2, "hope in").isChecked() is True   # untouched default

    # Re-enable one, recollect: only the still-off pattern remains.
    _toggle_of(c2, "cause").setChecked(True)
    assert c2.collect_disabled_defaults() == ["mike"]


def test_get_overrides_filters_disabled(qapp, synthetic_defaults):
    saved_config = commands._config
    try:
        commands._config = {
            "overrides": [{"pattern": "foo", "replacement": "bar"}],
            "disabled_default_overrides": ["mike"],
        }
        out = commands.get_overrides()
        pats = [o["pattern"] for o in out]
        assert "mike" not in pats                     # disabled default dropped
        assert "cause" in pats                        # other defaults remain
        assert pats[0] == "foo"                       # user rules come FIRST

        # Absent key => all defaults present (back-compat).
        commands._config = {}
        pats2 = [o["pattern"] for o in commands.get_overrides()]
        assert len(pats2) == len(DEFAULT_OVERRIDES)
        assert "mike" in pats2

        # DEFAULT_OVERRIDES itself is never mutated.
        assert any(o["pattern"] == "mike" for o in DEFAULT_OVERRIDES)
    finally:
        commands._config = saved_config


def test_user_override_wins_conflict_and_disable_frees_pattern(synthetic_defaults):
    """User-first precedence: a user rule beats a colliding enabled default.
    And disabling a default frees both its before- and after-phrases for
    reuse (the scenario Tyler asked us to cover)."""
    from core.overrides import apply_overrides

    saved_config = commands._config
    try:
        # 'mike'->'mic' ships enabled; user wants 'mike'->'microphone'. With
        # user-first ordering the user rule runs first and wins.
        commands._config = {"overrides": [{"pattern": "mike", "replacement": "microphone"}]}
        assert apply_overrides("mike", commands.get_overrides()) == "microphone"

        # Disable the 'mike' default with no user rule: 'mike' (and its old
        # replacement 'mic') now pass through untouched, free to reuse.
        commands._config = {"overrides": [], "disabled_default_overrides": ["mike"]}
        assert apply_overrides("mike check", commands.get_overrides()) == "mike check"
    finally:
        commands._config = saved_config
