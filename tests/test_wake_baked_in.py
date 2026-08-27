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
