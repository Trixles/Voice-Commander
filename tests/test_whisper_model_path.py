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
