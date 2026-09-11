"""
tests/test_mic_and_blur.py
==========================
0.8.0 coverage:

  1. The get_open_mic_phrases / get_close_mic_phrases resolution chain
     (command list -> shipped defaults), including the enable/disable
     behaviour.
  2. The matcher exclusion: open_mic / close_mic are listener state
     transitions, so _score_segment must never match them.
  3. blur_compositing_available: the kwinrc parse that decides whether the
     settings window goes translucent (blur present) or opaque (the safe
     default), including third-party blur forks.

Pure logic -- no Qt, no engine, no subprocess. Run from the repo root:
  pytest -q
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core import commands  # noqa: E402
from core.commands import (  # noqa: E402
    DEFAULT_OPEN_MIC_PHRASES,
    DEFAULT_CLOSE_MIC_PHRASES,
    _score_segment,
)
from core.env import blur_compositing_available  # noqa: E402


# -- Default mic phrase lists -------------------------------------------------

def test_default_mic_phrases_have_no_mike_variants():
    """'open mike'/'close mike' are covered by the mike->mic default override,
    so they must not ship in the phrase lists."""
    everything = list(DEFAULT_OPEN_MIC_PHRASES) + list(DEFAULT_CLOSE_MIC_PHRASES)
    assert not any("mike" in p for p in everything)


# -- get_open_mic_phrases / get_close_mic_phrases resolution chain ------------

def test_getter_reads_from_command_list(monkeypatch):
    monkeypatch.setattr(commands, "_commands", [
        {"name": "open_mic", "action": "open_mic", "phrases": ["Foo", " Bar "]},
    ])
    monkeypatch.setattr(commands, "_config", {})
    assert commands.get_open_mic_phrases() == {"foo", "bar"}


def test_getter_disabled_command_returns_empty_set(monkeypatch):
    monkeypatch.setattr(commands, "_commands", [
        {"name": "open_mic", "action": "open_mic",
         "phrases": ["open mic"], "enabled": False},
    ])
    monkeypatch.setattr(commands, "_config", {})
    assert commands.get_open_mic_phrases() == set()


def test_getter_falls_back_to_defaults(monkeypatch):
    monkeypatch.setattr(commands, "_commands", [])
    monkeypatch.setattr(commands, "_config", {})
    assert commands.get_open_mic_phrases() == {
        p.lower() for p in DEFAULT_OPEN_MIC_PHRASES
    }


# -- Matcher exclusion --------------------------------------------------------

def test_score_segment_never_matches_mic_command(monkeypatch):
    """A mic phrase must not be scored into a (no-op) dispatch."""
    monkeypatch.setattr(commands, "_commands", [
        {"name": "open_mic", "action": "open_mic", "phrases": ["open mic"]},
    ])
    monkeypatch.setattr(commands, "_config", {"monitors": {}})
    assert _score_segment("open mic") is None


def test_score_segment_mic_does_not_shadow_real_command(monkeypatch):
    """A real command still matches even when a mic command shares lexical
    space; the mic entry is simply absent from candidacy."""
    monkeypatch.setattr(commands, "_commands", [
        {"name": "open_mic", "action": "open_mic", "phrases": ["open mic"]},
        {"name": "mute", "action": "mute", "phrases": ["mute"]},
    ])
    monkeypatch.setattr(commands, "_config", {"monitors": {}})
    result = _score_segment("mute")
    assert result is not None and result[1]["action"] == "mute"


# -- blur_compositing_available: kwinrc parse ---------------------------------

def _write(tmp_path, text):
    p = tmp_path / "kwinrc"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_blur_missing_file_is_opaque():
    assert blur_compositing_available("/no/such/kwinrc") is False


def test_blur_stock_default_absent_counts_as_on(tmp_path):
    # KDE ships Blur enabled; an absent key means on.
    path = _write(tmp_path, "[Plugins]\nsomethingElseEnabled=true\n")
    assert blur_compositing_available(path) is True


def test_blur_no_plugins_section_counts_as_on(tmp_path):
    path = _write(tmp_path, "[Compositing]\nEnabled=true\n")
    assert blur_compositing_available(path) is True


def test_blur_explicitly_off_no_fork_is_opaque(tmp_path):
    path = _write(tmp_path, "[Plugins]\nblurEnabled=false\n")
    assert blur_compositing_available(path) is False


def test_blur_fork_plugin_on_counts(tmp_path):
    # Stock off but a third-party fork enabled (the real-world Better Blur case).
    path = _write(tmp_path, "[Plugins]\nblurEnabled=false\nbetter_blur_dxEnabled=true\n")
    assert blur_compositing_available(path) is True


def test_blur_immutability_marker_off_is_opaque(tmp_path):
    path = _write(tmp_path, "[Plugins]\nblurEnabled[$i]=false\n")
    assert blur_compositing_available(path) is False
